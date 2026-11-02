"""Signed ElevenLabs phone events for Bella and Gala.

Net-new justification: voice numbers and provider agents already exist, but no
owned endpoint persisted their outcomes into Courier, Huddle, CRM, or AInbox.
This adapter verifies ElevenLabs HMAC, reuses those existing stores, and emits a
typed Pack capture without taking ownership from Twilio or ElevenLabs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.api.v1.endpoints.sms import _resolve_courier_owner, _resolve_sms_conversation, _resolve_sms_sender
from app.core.config import get_settings
from app.core.database import get_db
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.crm_pipeline_task import CrmPipelineTask
from app.models.message import Message
from app.models.notification import Notification
from app.models.user import User  # noqa: TC001
from app.services.pack_bridge import build_pack_capture, process_pack_bridge_outbox_once, queue_pack_capture
from app.services.sms import normalise_phone, placeholder_contact_email

router = APIRouter(prefix="/phone-agents", tags=["phone-agents"])

_SIGNATURE_TOLERANCE_SECONDS = 30 * 60
_URGENT_PATTERN = re.compile(r"\b(urgent|emergency|infection|bleeding|refund|chargeback|angry|complaint)\b", re.I)


class TakeMessageRequest(BaseModel):
    agent: Literal["bella", "gala"]
    conversation_id: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=4000)
    caller_phone: str | None = Field(default=None, max_length=40)
    caller_name: str | None = Field(default=None, max_length=160)
    urgency: Literal["low", "normal", "high", "urgent"] = "normal"
    callback_requested: bool = True
    booking_reference: str | None = Field(default=None, max_length=40)


def verify_elevenlabs_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
    *,
    now: int | None = None,
) -> bool:
    if not signature_header or not secret:
        return False
    try:
        values = dict(part.strip().split("=", 1) for part in signature_header.split(","))
        timestamp = values["t"]
        supplied = values["v0"]
        observed_at = int(timestamp)
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else int(time.time())) - observed_at) > _SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_phone(data: dict[str, Any]) -> str | None:
    initiation = _record(data.get("conversation_initiation_client_data"))
    dynamic = _record(initiation.get("dynamic_variables"))
    metadata = _record(data.get("metadata"))
    phone_call = _record(metadata.get("phone_call"))
    provider_body = _record(metadata.get("body"))
    candidates = (
        dynamic.get("system__caller_id"),
        dynamic.get("caller_id"),
        dynamic.get("caller_phone"),
        phone_call.get("external_number"),
        phone_call.get("from_number"),
        provider_body.get("From"),
        provider_body.get("Caller"),
        metadata.get("from_number"),
    )
    for candidate in candidates:
        normalized = normalise_phone(str(candidate)) if candidate else None
        if normalized:
            return normalized
    return None


def _transcript_text(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for turn in data.get("transcript") or []:
        if not isinstance(turn, dict):
            continue
        message = str(turn.get("message") or "").strip()
        if message:
            role = "Caller" if turn.get("role") == "user" else "Agent"
            lines.append(f"{role}: {message}")
    return "\n".join(lines)[:16000]


def _event_summary(event_type: str, data: dict[str, Any], lane: str) -> str:
    analysis = _record(data.get("analysis"))
    summary = str(analysis.get("transcript_summary") or "").strip()
    if summary:
        return summary[:2048]
    if event_type == "call_initiation_failure":
        reason = str(data.get("failure_reason") or "unknown")
        return f"{lane.title()} call could not start: {reason}."
    transcript = _transcript_text(data)
    return transcript[:2048] if transcript else f"{lane.title()} call completed without a transcript."


def _agent_lane(agent_id: str) -> str | None:
    settings = get_settings()
    if agent_id and agent_id == settings.ELEVENLABS_RYOZAN_AGENT_ID:
        return "bella"
    if agent_id and agent_id == settings.ELEVENLABS_JUSTIN_AGENT_ID:
        return "gala"
    return None


def _tool_authorized(request: Request, secret: str) -> bool:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return bool(token and secret and hmac.compare_digest(token, secret))


async def _resolve_booking(db: AsyncSession, sender: User, phone: str | None) -> Booking | None:
    booking = await db.scalar(
        select(Booking)
        .where(Booking.client_id == sender.id)
        .order_by(desc(Booking.updated_at), desc(Booking.created_at))
        .limit(1)
    )
    if booking or not phone:
        return booking
    guest_rows = (
        await db.execute(select(Booking).where(Booking.guest_phone.is_not(None)).order_by(desc(Booking.created_at)))
    ).scalars()
    return next((row for row in guest_rows if normalise_phone(row.guest_phone) == phone), None)


async def _resolve_voice_sender(
    db: AsyncSession,
    *,
    phone: str | None,
    conversation_id: str,
) -> User:
    if phone:
        return await _resolve_sms_sender(db, phone)
    opaque_id = hashlib.sha256(conversation_id.encode()).hexdigest()[:24]
    email = placeholder_contact_email(f"voice-{opaque_id}")
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        return existing
    sender = User(
        email=email,
        name="Unknown phone caller",
        role="collector",
        password_hash="voice-inbound-placeholder",
    )
    db.add(sender)
    await db.flush()
    return sender


def _capture_for_event(
    *,
    event_type: str,
    data: dict[str, Any],
    lane: str,
    summary: str,
    phone: str | None,
    local: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversation_id = str(data.get("conversation_id") or "unknown")
    analysis = _record(data.get("analysis"))
    metadata = _record(data.get("metadata"))
    transcript = _transcript_text(data)
    targets = ["ainbox", "courier", "mercury", "flow"]
    if lane == "bella":
        targets = ["ainbox", "courier", "huddle", "flow"]
    return build_pack_capture(
        source_id=f"bttco:phone:{lane}",
        kind="courier" if lane == "bella" else "agent",
        channel="voice",
        actor=lane,
        summary=summary,
        idempotency_key=f"elevenlabs:{event_type}:{conversation_id}",
        content_ref=(
            f"bttco://courier/{local['conversation_id']}"
            if local and local.get("conversation_id")
            else f"elevenlabs://conversation/{conversation_id}"
        ),
        metadata={
            "eventType": event_type,
            "provider": "elevenlabs",
            "agentId": data.get("agent_id"),
            "agentLane": lane,
            "conversationId": conversation_id,
            "callerPhone": phone,
            "callStatus": data.get("status"),
            "callSuccessful": analysis.get("call_successful"),
            "callDurationSeconds": metadata.get("call_duration_secs"),
            "transcript": transcript,
            "packTargets": targets,
            "localReceipts": local or {},
        },
    )


async def _process_bella_event(
    db: AsyncSession,
    *,
    event_type: str,
    data: dict[str, Any],
    summary: str,
    phone: str | None,
) -> dict[str, Any]:
    conversation_id = str(data.get("conversation_id") or "")
    provider_event_id = f"elevenlabs:{event_type}:{conversation_id}"[:255]
    existing = await db.scalar(
        select(BookingLifecycleEvent).where(BookingLifecycleEvent.provider_event_id == provider_event_id)
    )
    if existing:
        return {
            "duplicate": True,
            "event_id": str(existing.id),
            "conversation_id": _record(existing.details).get("courier_conversation_id"),
            "booking_id": str(existing.booking_id) if existing.booking_id else None,
        }

    owner = await _resolve_courier_owner(db)
    sender = await _resolve_voice_sender(db, phone=phone, conversation_id=conversation_id)
    courier = await _resolve_sms_conversation(db, owner, sender, phone or conversation_id, "voice")
    booking = await _resolve_booking(db, sender, phone)
    transcript = _transcript_text(data)
    analysis = _record(data.get("analysis"))
    metadata = _record(data.get("metadata"))
    now = datetime.now(UTC)
    message = Message(
        conversation_id=courier.id,
        sender_id=sender.id,
        content=f"{summary}\n\n{transcript}".strip()[:20000],
        message_type="voice",
        metadata_json=json.dumps(
            {
                "channel": "voice",
                "provider": "elevenlabs",
                "direction": "inbound",
                "agent": "bella",
                "agent_id": data.get("agent_id"),
                "elevenlabs_conversation_id": conversation_id,
                "caller_phone": phone,
                "call_status": data.get("status"),
                "call_successful": analysis.get("call_successful"),
                "call_duration_seconds": metadata.get("call_duration_secs"),
                "received_at": now.isoformat(),
            }
        ),
        created_at=now,
        updated_at=now,
    )
    db.add(message)
    courier.updated_at = now
    await db.flush()

    urgent = bool(_URGENT_PATTERN.search(f"{summary}\n{transcript}"))
    task = CrmPipelineTask(
        title=f"Review Bella phone call from {phone or 'unknown caller'}",
        body=summary[:4000],
        status="open",
        priority="high" if urgent or event_type == "call_initiation_failure" else "normal",
        booking_id=booking.id if booking else None,
        assignee_id=booking.artist_id if booking else owner.id,
    )
    db.add(task)
    notification = Notification(
        user_id=owner.id,
        title="New Bella phone call",
        body=summary[:500],
        notification_type="message",
        action_url="/dashboard/courier",
    )
    db.add(notification)
    await db.flush()

    local = {
        "courier_conversation_id": str(courier.id),
        "courier_message_id": str(message.id),
        "booking_id": str(booking.id) if booking else None,
        "crm_task_id": str(task.id),
        "notification_id": str(notification.id),
    }
    lifecycle = BookingLifecycleEvent(
        booking_id=booking.id if booking else None,
        artist_id=booking.artist_id if booking else None,
        actor_id=None,
        event_type=(
            "phone_call_failed"
            if event_type == "call_initiation_failure"
            else "phone_message_captured"
            if event_type == "agent_tool_take_message"
            else "phone_call_completed"
        ),
        source="elevenlabs",
        provider_event_id=provider_event_id,
        reference_code=booking.reference_code if booking else None,
        summary=summary,
        details={
            **local,
            "caller_phone": phone,
            "agent": "bella",
            "event_type": event_type,
            "call_successful": analysis.get("call_successful"),
        },
    )
    db.add(lifecycle)
    await db.flush()
    local["event_id"] = str(lifecycle.id)
    return local


@router.post("/tools/take-message")
async def take_phone_message(
    body: TakeMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Persist a live Bella or Gala message and return a receipt to the agent."""
    settings = get_settings()
    if not settings.ELEVENLABS_TOOL_SECRET:
        raise HTTPException(status_code=503, detail="ElevenLabs phone tools are not configured")
    if not _tool_authorized(request, settings.ELEVENLABS_TOOL_SECRET):
        raise HTTPException(status_code=401, detail="Invalid phone tool credential")

    phone = normalise_phone(body.caller_phone) if body.caller_phone else None
    if body.caller_phone and not phone:
        raise HTTPException(status_code=400, detail="caller_phone is invalid")
    labels = []
    if body.urgency in {"high", "urgent"}:
        labels.append("URGENT")
    if body.caller_name:
        labels.append(f"Caller: {body.caller_name.strip()}")
    if body.booking_reference:
        labels.append(f"Booking: {body.booking_reference.strip()}")
    if body.callback_requested:
        labels.append("Callback requested")
    summary = ". ".join([*labels, body.message.strip()])[:2048]
    data: dict[str, Any] = {
        "agent_id": (
            settings.ELEVENLABS_RYOZAN_AGENT_ID if body.agent == "bella" else settings.ELEVENLABS_JUSTIN_AGENT_ID
        ),
        "conversation_id": body.conversation_id,
        "status": "in_progress",
        "transcript": [{"role": "user", "message": body.message.strip()}],
        "analysis": {"transcript_summary": summary, "call_successful": "unknown"},
        "conversation_initiation_client_data": {"dynamic_variables": {"system__caller_id": phone} if phone else {}},
    }
    local: dict[str, Any] | None = None
    if body.agent == "bella":
        local = await _process_bella_event(
            db,
            event_type="agent_tool_take_message",
            data=data,
            summary=summary,
            phone=phone,
        )
    capture = _capture_for_event(
        event_type="agent_tool_take_message",
        data=data,
        lane=body.agent,
        summary=summary,
        phone=phone,
        local=local,
    )
    capture["metadata"].update(
        {
            "urgency": body.urgency,
            "callbackRequested": body.callback_requested,
            "bookingReference": body.booking_reference,
        }
    )
    courier_message_id = UUID(local["courier_message_id"]) if local and local.get("courier_message_id") else None
    outbox = await queue_pack_capture(db, capture, courier_message_id=courier_message_id)
    await db.commit()
    bridge = await process_pack_bridge_outbox_once(outbox_id=outbox.id)
    bridge_payload = (
        bridge.to_dict() if bridge else {"status": "queued", "outbox_id": str(outbox.id), "retryable": True}
    )
    routed = bridge_payload.get("status") == "delivered"
    return {
        "success": routed,
        "accepted": True,
        "message": "Message captured and routed for follow-up."
        if routed
        else "Message captured; Pack routing is pending retry.",
        "agent": body.agent,
        "conversation_id": body.conversation_id,
        "local": local,
        "pack_bridge": bridge_payload,
    }


@router.post("/webhooks/elevenlabs")
async def elevenlabs_post_call_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    settings = get_settings()
    if not settings.ELEVENLABS_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="ElevenLabs webhook verification is not configured")
    raw_body = await request.body()
    if not verify_elevenlabs_signature(
        raw_body,
        request.headers.get("elevenlabs-signature"),
        settings.ELEVENLABS_WEBHOOK_SECRET,
    ):
        raise HTTPException(status_code=401, detail="Invalid ElevenLabs signature")
    try:
        payload = json.loads(raw_body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    event_type = str(payload.get("type") or "")
    if event_type not in {"post_call_transcription", "call_initiation_failure"}:
        return {"status": "ignored", "event_type": event_type}
    data = _record(payload.get("data"))
    conversation_id = str(data.get("conversation_id") or "").strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")
    lane = _agent_lane(str(data.get("agent_id") or ""))
    if not lane:
        return {"status": "ignored", "reason": "agent_not_managed"}

    phone = _extract_phone(data)
    summary = _event_summary(event_type, data, lane)
    local: dict[str, Any] | None = None
    if lane == "bella":
        local = await _process_bella_event(
            db,
            event_type=event_type,
            data=data,
            summary=summary,
            phone=phone,
        )
    capture = _capture_for_event(
        event_type=event_type,
        data=data,
        lane=lane,
        summary=summary,
        phone=phone,
        local=local,
    )
    courier_message_id = UUID(local["courier_message_id"]) if local and local.get("courier_message_id") else None
    outbox = await queue_pack_capture(db, capture, courier_message_id=courier_message_id)
    await db.commit()
    bridge = await process_pack_bridge_outbox_once(outbox_id=outbox.id)
    bridge_payload = (
        bridge.to_dict() if bridge else {"status": "queued", "outbox_id": str(outbox.id), "retryable": True}
    )
    return {
        "status": "received" if bridge_payload.get("status") == "delivered" else "accepted",
        "agent": lane,
        "conversation_id": conversation_id,
        "local": local,
        "pack_bridge": bridge_payload,
    }
