"""Typed Ryozan communication delivery into Ark AInbox and connected Packs.

Net-new justification: BTTCO already owns Courier messages, channel delivery,
and provider receipts, while Ark owns AInbox and cross-Pack intake. This module
adds only the authenticated adapter between those authorities and never creates
a second inbox, conversation store, or outbound transport.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.database import async_session
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.message import Message
from app.models.pack_bridge_outbox import PackBridgeOutbox

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bttco.pack_bridge")
PACK_BRIDGE_MAX_ATTEMPTS = 5
PACK_BRIDGE_STALE_MINUTES = 10
LEARNING_CAPTURE_LOOKBACK_HOURS = 24


@dataclass(frozen=True)
class PackBridgeReceipt:
    status: str
    outbox_id: str | None = None
    source_id: str | None = None
    idempotency_key: str | None = None
    attempts: int | None = None
    retryable: bool | None = None
    destination: str | None = None
    status_code: int | None = None
    item_id: str | None = None
    created: bool | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def build_pack_capture(
    *,
    source_id: str,
    kind: str,
    channel: str,
    actor: str,
    summary: str,
    idempotency_key: str,
    metadata: dict[str, Any],
    content_ref: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sourceId": source_id[:160],
        "kind": kind,
        "channel": channel[:120],
        "actor": actor[:160],
        "summary": summary.strip()[:2048] or "Ryozan communication event",
        "idempotencyKey": idempotency_key[:256],
        "metadata": metadata,
    }
    if content_ref:
        payload["contentRef"] = content_ref[:2048]
    return payload


def _learning_group_ref(value: object) -> str:
    """Return a stable pseudonym without copying a client identifier to Ark."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]


def build_courier_learning_capture(message: Message) -> dict[str, Any]:
    """Project a Courier event as reference-only, review-gated learning input."""
    return build_pack_capture(
        source_id="bttco:apex:learning",
        kind="agent",
        channel="courier",
        actor="bttco",
        summary="Courier interaction is available for operator learning review.",
        idempotency_key=f"bttco-learning:courier:{message.id}",
        content_ref=f"bttco://courier/{message.conversation_id}/messages/{message.id}",
        metadata={
            "contractVersion": "bttco-learning-capture/v1",
            "sourceKind": "courier_message",
            "sourceRecordRef": f"bttco://courier/messages/{message.id}",
            "subjectGroupHash": _learning_group_ref(message.conversation_id),
            "messageType": message.message_type,
            "capturedAt": message.created_at.isoformat(),
            "trainingUse": "needs_review",
            "trainingUseApproved": False,
            "consentState": "unverified",
            "retentionClass": "bttco-source-controlled",
            "packTargets": ["ainbox", "apex"],
        },
    )


def build_booking_learning_capture(event: BookingLifecycleEvent) -> dict[str, Any]:
    """Project a booking outcome without client contact data or mutable details."""
    group_owner = event.booking_id or event.hold_token or event.id
    return build_pack_capture(
        source_id="bttco:apex:learning",
        kind="agent",
        channel="booking-outcome",
        actor="bttco",
        summary="Booking outcome is available for operator learning review.",
        idempotency_key=f"bttco-learning:booking:{event.id}",
        content_ref=f"bttco://bookings/lifecycle/{event.id}",
        metadata={
            "contractVersion": "bttco-learning-capture/v1",
            "sourceKind": "booking_lifecycle",
            "sourceRecordRef": f"bttco://bookings/lifecycle/{event.id}",
            "subjectGroupHash": _learning_group_ref(group_owner),
            "outcomeLabel": event.event_type,
            "source": event.source,
            "capturedAt": event.created_at.isoformat(),
            "trainingUse": "needs_review",
            "trainingUseApproved": False,
            "consentState": "unverified",
            "retentionClass": "bttco-source-controlled",
            "packTargets": ["ainbox", "apex"],
        },
    )


async def queue_recent_learning_captures_once(limit: int = 100) -> int:
    """Queue recent source references for AInbox review, never direct training.

    Calls and SMS already enter the Pack bridge at their signed provider
    boundaries. This reconciler covers ordinary Courier and booking outcomes
    while preserving BTTCO as the raw-content authority.
    """
    observed_after = datetime.now(UTC) - timedelta(hours=LEARNING_CAPTURE_LOOKBACK_HOURS)
    async with async_session() as session:
        messages = list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.created_at >= observed_after)
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        remaining = max(0, limit - len(messages))
        events = list(
            (
                await session.execute(
                    select(BookingLifecycleEvent)
                    .where(BookingLifecycleEvent.created_at >= observed_after)
                    .order_by(BookingLifecycleEvent.created_at.desc(), BookingLifecycleEvent.id.desc())
                    .limit(remaining)
                )
            )
            .scalars()
            .all()
        )
        candidates: list[tuple[dict[str, Any], UUID | None]] = [
            (build_courier_learning_capture(message), message.id) for message in messages
        ] + [(build_booking_learning_capture(event), None) for event in events]
        if not candidates:
            return 0
        keys = [str(payload["idempotencyKey"]) for payload, _ in candidates]
        existing = set(
            (
                await session.execute(
                    select(PackBridgeOutbox.idempotency_key).where(PackBridgeOutbox.idempotency_key.in_(keys))
                )
            )
            .scalars()
            .all()
        )
        queued = 0
        for payload, courier_message_id in candidates:
            if payload["idempotencyKey"] in existing:
                continue
            await queue_pack_capture(
                session,
                payload,
                courier_message_id=courier_message_id,
            )
            queued += 1
        await session.commit()
        return queued


async def _post_pack_capture(payload: dict[str, Any]) -> PackBridgeReceipt:
    """Deliver one typed capture to Ark. Missing config is an explicit receipt."""
    settings = get_settings()
    url = settings.ARK_PACK_BRIDGE_URL.strip()
    token = settings.ARK_PACK_BRIDGE_KEY.strip()
    if not url or not token:
        return PackBridgeReceipt(status="disabled", error="pack_bridge_not_configured")
    if settings.APP_ENV.lower() == "production" and not url.lower().startswith("https://"):
        return PackBridgeReceipt(status="failed", destination=url, error="https_required")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        response_data = response.json() if response.content else {}
        if response.status_code >= 300:
            return PackBridgeReceipt(
                status="failed",
                destination=url,
                status_code=response.status_code,
                error=str(response_data.get("error") or "ark_pack_bridge_rejected")[:240],
            )
        item = response_data.get("item") if isinstance(response_data, dict) else None
        return PackBridgeReceipt(
            status="delivered",
            destination=url,
            status_code=response.status_code,
            item_id=str(item.get("id")) if isinstance(item, dict) and item.get("id") else None,
            created=bool(response_data.get("created")) if isinstance(response_data, dict) else None,
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Ark Pack bridge delivery failed: %s", type(exc).__name__)
        return PackBridgeReceipt(
            status="failed",
            destination=url,
            error=type(exc).__name__,
        )


def _receipt_from_outbox(row: PackBridgeOutbox) -> PackBridgeReceipt:
    retryable = row.status in {"queued", "processing", "disabled"} or (
        row.status == "failed" and row.attempts < PACK_BRIDGE_MAX_ATTEMPTS
    )
    return PackBridgeReceipt(
        status=row.status,
        outbox_id=str(row.id),
        source_id=row.source_id,
        idempotency_key=row.idempotency_key,
        attempts=row.attempts,
        retryable=retryable,
        destination=row.destination,
        status_code=row.status_code,
        item_id=row.item_id,
        created=row.created,
        error=row.last_error,
    )


async def queue_pack_capture(
    db: AsyncSession,
    payload: dict[str, Any],
    *,
    courier_message_id: UUID | None = None,
) -> PackBridgeOutbox:
    """Persist one idempotent outbound Pack bridge intent in the caller transaction."""
    source_id = str(payload.get("sourceId") or "").strip()[:160]
    idempotency_key = str(payload.get("idempotencyKey") or "").strip()[:256]
    if not source_id or not idempotency_key:
        raise ValueError("Pack capture requires sourceId and idempotencyKey")
    row_id = uuid.uuid4()
    now = datetime.now(UTC)
    inserted_id = await db.scalar(
        insert(PackBridgeOutbox)
        .values(
            id=row_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
            payload=payload,
            courier_message_id=courier_message_id,
            status="queued",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[PackBridgeOutbox.idempotency_key])
        .returning(PackBridgeOutbox.id)
    )
    if inserted_id is not None:
        row = await db.get(PackBridgeOutbox, inserted_id)
    else:
        row = await db.scalar(select(PackBridgeOutbox).where(PackBridgeOutbox.idempotency_key == idempotency_key))
    if row is None:
        raise RuntimeError("Pack capture idempotency row was not visible after conflict")
    if courier_message_id and row.courier_message_id is None:
        row.courier_message_id = courier_message_id
    return row


async def process_pack_bridge_outbox_once(
    limit: int = 20,
    *,
    outbox_id: UUID | None = None,
) -> PackBridgeReceipt | None:
    """Claim durable Pack intents, persist typed receipts, and schedule retries."""
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=PACK_BRIDGE_STALE_MINUTES)
    async with async_session() as session:
        query = select(PackBridgeOutbox).where(
            or_(
                and_(PackBridgeOutbox.status == "queued", PackBridgeOutbox.next_attempt_at <= now),
                and_(PackBridgeOutbox.status == "disabled", PackBridgeOutbox.next_attempt_at <= now),
                and_(
                    PackBridgeOutbox.status == "failed",
                    PackBridgeOutbox.attempts < PACK_BRIDGE_MAX_ATTEMPTS,
                    PackBridgeOutbox.next_attempt_at <= now,
                ),
                and_(
                    PackBridgeOutbox.status == "processing",
                    PackBridgeOutbox.attempts < PACK_BRIDGE_MAX_ATTEMPTS,
                    PackBridgeOutbox.updated_at <= stale_before,
                ),
            )
        )
        if outbox_id is not None:
            query = query.where(PackBridgeOutbox.id == outbox_id)
        rows = list(
            (
                await session.execute(
                    query.order_by(PackBridgeOutbox.created_at).with_for_update(skip_locked=True).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[tuple[UUID, dict[str, Any]]] = []
        for row in rows:
            if row.status == "processing" and row.updated_at > stale_before:
                continue
            if row.next_attempt_at > now:
                continue
            if row.status == "failed" and row.attempts >= PACK_BRIDGE_MAX_ATTEMPTS:
                continue
            row.status = "processing"
            row.attempts += 1
            row.updated_at = now
            row.last_error = None
            claimed.append((row.id, dict(row.payload)))
        await session.commit()

        last_receipt: PackBridgeReceipt | None = None
        for claimed_id, payload in claimed:
            try:
                provider_receipt = await _post_pack_capture(payload)
            except Exception as exc:  # noqa: BLE001 - durable row records the failure
                logger.exception("Ark Pack bridge attempt failed for outbox %s", claimed_id)
                provider_receipt = PackBridgeReceipt(status="failed", error=str(exc)[:1000])
            row = await session.get(
                PackBridgeOutbox,
                claimed_id,
                with_for_update=True,
                populate_existing=True,
            )
            if not row or row.status != "processing":
                continue
            finished_at = datetime.now(UTC)
            row.status = provider_receipt.status
            row.destination = provider_receipt.destination
            row.status_code = provider_receipt.status_code
            row.item_id = provider_receipt.item_id
            row.created = provider_receipt.created
            row.last_error = provider_receipt.error
            row.updated_at = finished_at
            if row.status in {"failed", "disabled"}:
                row.next_attempt_at = finished_at + timedelta(minutes=min(2**row.attempts, 60))
            await session.commit()
            last_receipt = _receipt_from_outbox(row)

        if outbox_id is not None and last_receipt is None:
            row = await session.get(PackBridgeOutbox, outbox_id, populate_existing=True)
            return _receipt_from_outbox(row) if row else None
        return last_receipt


async def requeue_terminal_learning_captures(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> list[UUID]:
    """Requeue only the known Apex source-registration failure class.

    This is an operator recovery seam for rows that exhausted retries while Ark
    rejected an unregistered source. It cannot requeue unrelated Pack traffic
    or silently broaden the retry policy.
    """
    row_ids = list(
        (
            await db.execute(
                select(PackBridgeOutbox.id)
                .where(
                    PackBridgeOutbox.source_id == "bttco:apex:learning",
                    PackBridgeOutbox.status == "failed",
                    PackBridgeOutbox.attempts >= PACK_BRIDGE_MAX_ATTEMPTS,
                    PackBridgeOutbox.status_code == 500,
                    PackBridgeOutbox.last_error == "bttco_pack_bridge_persist_failed",
                )
                .order_by(PackBridgeOutbox.created_at)
                .with_for_update(skip_locked=True)
                .limit(max(1, min(limit, 500)))
            )
        )
        .scalars()
        .all()
    )
    if not row_ids:
        return []
    now = datetime.now(UTC)
    await db.execute(
        update(PackBridgeOutbox)
        .where(PackBridgeOutbox.id.in_(row_ids))
        .values(
            status="queued",
            attempts=0,
            status_code=None,
            last_error=None,
            next_attempt_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    return row_ids


async def deliver_pack_capture(payload: dict[str, Any]) -> PackBridgeReceipt:
    """Compatibility entrypoint that now persists intent before attempting delivery."""
    async with async_session() as session:
        row = await queue_pack_capture(session, payload)
        await session.commit()
        outbox_id = row.id
    receipt = await process_pack_bridge_outbox_once(outbox_id=outbox_id)
    if receipt is None:
        return PackBridgeReceipt(status="queued", outbox_id=str(outbox_id), retryable=True)
    return receipt


async def deliver_pack_capture_background(payload: dict[str, Any]) -> None:
    receipt = await deliver_pack_capture(payload)
    logger.info(
        "Ark Pack bridge delivery status=%s source=%s idempotency=%s outbox=%s",
        receipt.status,
        payload.get("sourceId"),
        payload.get("idempotencyKey"),
        receipt.outbox_id,
    )
