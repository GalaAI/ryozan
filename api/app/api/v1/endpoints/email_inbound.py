"""
Resend inbound email webhook -- receives email.received events and routes
incoming mail via DB-driven routing table (ryozan_email_routes).

Reserved addresses (info, bookings, admin ...) and the catch-all go to the
owner inbox: settings.owner_inbox_email (SHOP_OWNER_EMAIL, else SHOP_EMAIL).
With neither set, mail is archived to the app inbox and never forwarded.
Artist routes are created dynamically via the email connect flow.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from html import escape
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.user_lookup import find_user_by_email
from app.models.email_route import EmailRoute
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.user import User
from app.services.conversation_threads import conversation_reply_to, find_direct_conversation
from app.services.email import format_identity, sender_identity
from app.services.resend_webhook import verify_resend_webhook

logger = logging.getLogger("ryozan.email_inbound")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Reserved local parts routed to the owner inbox. INBOUND_OWNER_ALIASES adds more.
OWNER_LOCAL_PARTS = frozenset({"info", "bookings", "admin", "noreply", "support"})


def _is_owner_address(local_part: str) -> bool:
    """Reserved address or configured owner alias -> owner inbox."""
    return local_part in OWNER_LOCAL_PARTS or local_part in get_settings().inbound_owner_aliases


async def _owner_route(db: AsyncSession, route_handle: str | None) -> "InboundRoute":
    """The owner inbox: forward to owner_inbox_email, archive to that user.

    The archive goes to the account with the owner address, else the oldest
    founder, so mail stays visible in the app even with no address configured.
    A blank destination means the message is archived and never forwarded.
    """
    destination = get_settings().owner_inbox_email
    owner_id = None
    if destination:
        owner_id = (await db.execute(select(User.id).where(User.email == destination))).scalar_one_or_none()
    if owner_id is None:
        owner_id = (
            await db.execute(select(User.id).where(User.role == "founder").order_by(User.created_at).limit(1))
        ).scalar_one_or_none()
    return InboundRoute(destination, owner_id, route_handle)


async def _retrieve_received_body(data: dict, api_key: str) -> dict:
    """Hydrate metadata-only Resend events before routing or acknowledging them.

    https://resend.com/docs/api-reference/emails/retrieve-received-email
    The existing signed webhook remains the intake owner. A failed provider read
    is retryable and must never become a successful 'no body' acknowledgement.
    """
    if not data.get("email_id") and (data.get("html") or data.get("text")):
        return data
    try:
        email_id = str(UUID(str(data.get("email_id", ""))))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Received email ID is missing or invalid") from exc

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.resend.com/emails/receiving/{email_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            response.raise_for_status()
            received = response.json()
        if not isinstance(received, dict) or received.get("id") != email_id:
            raise ValueError("Received email identity did not match")
        for key in ("text", "html"):
            if received.get(key) is not None and not isinstance(received[key], str):
                raise ValueError("Received email body has an invalid type")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Resend inbound body retrieval failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Received email body is temporarily unavailable") from exc

    hydrated = {
        **data,
        "html": received.get("html"),
        "text": received.get("text"),
        "headers": received.get("headers") or {},
        "authentication": received.get("authentication"),
    }
    attachments = received.get("attachments") or data.get("attachments") or []
    # Do not fetch arbitrary attachment URLs inside a webhook. Preserve an
    # explicit notice instead of silently dropping an attachment-only message.
    if attachments:
        notice = (
            f"\n\n[This email has {len(attachments)} attachment(s). View the original email in Resend for the files.]"
        )
        if hydrated.get("text"):
            hydrated["text"] += notice
        if hydrated.get("html"):
            hydrated["html"] += f"<p>{notice.strip()}</p>"
        if not hydrated.get("text") and not hydrated.get("html"):
            hydrated["text"] = notice.strip()
    if not hydrated.get("text") and not hydrated.get("html"):
        hydrated["text"] = "[This email has no text body.]"
    return hydrated


def _extract_local_part(address: str) -> str:
    """Return the lowercase local part from a raw or display-form email."""
    raw = _extract_email_address(address)
    return raw.split("@", 1)[0].lower()


def _extract_email_address(address: str) -> str:
    """Return a normalized email address from a raw or display-form value."""
    _name, parsed = parseaddr(address.strip())
    return (parsed or address.strip()).lower()


@dataclass(frozen=True)
class InboundRoute:
    destination: str
    owner_user_id: UUID | None
    route_handle: str | None
    conversation_id: UUID | None = None
    sender_user_id: UUID | None = None


STAFF_ROLES = ("founder", "admin", "assistant", "artist")
FORWARD_HEADER = "X-BTTCO-Courier-Forward"


def _sender_address(value: str | list[str]) -> str:
    if isinstance(value, list):
        if len(value) != 1:
            raise HTTPException(status_code=400, detail="Expected one email sender")
        value = value[0]
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="Invalid email sender")
    addresses = getaddresses([value])
    if len(addresses) != 1 or any(c in value for c in "\r\n"):
        raise HTTPException(status_code=400, detail="Expected one email sender")
    address = addresses[0][1].strip().lower()
    if address.count("@") != 1 or any(c in address for c in "\r\n,; "):
        raise HTTPException(status_code=400, detail="Invalid email sender")
    return address


def _is_automatic_email(data: dict, *, conversation_reply: bool = True) -> bool:
    headers = data.get("headers") or {}
    if not isinstance(headers, dict):
        raise HTTPException(status_code=400, detail="Invalid email headers")
    headers = {str(k).lower(): str(v).strip().lower() for k, v in headers.items()}
    return FORWARD_HEADER.lower() in headers or (conversation_reply and headers.get("auto-submitted", "no") != "no")


async def _resolve_conversation_reply(
    local_part: str,
    db: AsyncSession,
    sender_address: str,
) -> InboundRoute:
    """Route a recognized participant to the opposite side of this exact thread.

    General inquiries can have several staff members. Preserve their historical
    priority for collector replies; never guess between multiple collectors.
    """
    try:
        conversation_id = UUID(local_part.removeprefix("convo-"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid conversation reply address") from exc
    result = await db.execute(
        select(User.email, User.id, User.role, ConversationParticipant.role)
        .join(ConversationParticipant, ConversationParticipant.user_id == User.id)
        .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
        .where(Conversation.id == conversation_id)
    )
    people = list(result.all())
    senders = [p for p in people if p[0] and p[0].strip().lower() == sender_address]
    if len(senders) != 1:
        raise HTTPException(status_code=403, detail="Sender is not a unique conversation participant")
    sender = senders[0]
    collectors = [p for p in people if p[2] == "collector"]
    staff = [p for p in people if p[2] in STAFF_ROLES]
    if len(collectors) != 1 or not staff or sender[2] not in (*STAFF_ROLES, "collector"):
        raise HTTPException(status_code=409, detail="Conversation reply recipient is ambiguous")
    if sender[2] in STAFF_ROLES:
        recipient = collectors[0]
    else:
        recipient = min(staff, key=lambda p: (STAFF_ROLES.index(p[2]), p[3] != "owner", str(p[1])))
    try:
        destination = _sender_address(recipient[0] or "")
    except HTTPException as exc:
        raise HTTPException(status_code=409, detail="Conversation recipient email is invalid") from exc
    if recipient[1] == sender[1] or destination == sender_address:
        raise HTTPException(status_code=409, detail="Conversation reply would forward to its sender")
    if sum(bool(p[0]) and p[0].strip().lower() == destination for p in people) != 1:
        raise HTTPException(status_code=409, detail="Conversation reply recipient is ambiguous")
    return InboundRoute(destination, recipient[1], local_part, conversation_id, sender[1])


async def _forward_email(
    original: dict,
    forward_to: str,
    api_key: str,
    conversation_id: UUID | None = None,
) -> None:
    """Forward received email to the destination address via Resend."""
    subject = original.get("subject") or "(no subject)"
    from_addr = original.get("from") or "unknown@unknown.com"
    if isinstance(from_addr, list):
        from_addr = from_addr[0] if from_addr else "unknown@unknown.com"
    html_body = original.get("html") or escape(original.get("text") or "").replace("\n", "<br>")
    if conversation_id and _sender_address(from_addr) == _sender_address(forward_to):
        raise HTTPException(status_code=409, detail="Email would forward to its sender")

    forward_html = f"""
    <div style="padding:12px;margin-bottom:16px;background:#f5f5f5;border-left:4px solid #d4a853;font-family:sans-serif;font-size:14px;">
        <strong>Forwarded email</strong><br>
        From: {escape(from_addr)}<br>
        Subject: {escape(subject)}
    </div>
    <div>{html_body}</div>
    """

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": format_identity(*sender_identity("resend", get_settings())),
                "to": [forward_to],
                "reply_to": conversation_reply_to(conversation_id) or from_addr,
                "headers": {FORWARD_HEADER: "1", "Auto-Submitted": "auto-generated"},
                "subject": f"[Ryozan] {subject}",
                "html": forward_html,
                "tags": [{"name": "type", "value": "inbound-forward"}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"Forwarded email from {from_addr} -> {forward_to}")


async def _resolve_destination(
    to_addresses: list[str] | str,
    db: AsyncSession,
    from_address: str | list[str],
) -> InboundRoute:
    """
    Resolve forwarding destination. Priority:
      1. Courier conversation reply address
      2. DB route (artist-claimed, verified handles)
      3. Reserved addresses and owner aliases -> owner inbox
      4. Catch-all -> owner inbox
    """
    addresses = [to_addresses] if isinstance(to_addresses, str) else to_addresses
    reply_addresses = {_extract_email_address(a) for a in addresses if _extract_local_part(a).startswith("convo-")}
    if reply_addresses:
        if len(reply_addresses) != 1:
            raise HTTPException(status_code=400, detail="Expected one conversation reply address")
        address = reply_addresses.pop()
        local_part, _, domain = address.partition("@")
        reply_domain = get_settings().COURIER_REPLY_DOMAIN
        if not reply_domain or domain != reply_domain:
            raise HTTPException(status_code=400, detail="Unsupported conversation reply domain")
        return await _resolve_conversation_reply(local_part, db, _sender_address(from_address))
    for addr in addresses:
        local_part = _extract_local_part(addr)

        # Check DB first (dynamic artist routes)
        result = await db.execute(
            select(EmailRoute).where(
                EmailRoute.handle == local_part,
                EmailRoute.verified,
            )
        )
        route = result.scalar_one_or_none()
        if route:
            return InboundRoute(route.forward_to, route.user_id, local_part)

        if _is_owner_address(local_part):
            return await _owner_route(db, local_part)

    return await _owner_route(db, None)


async def _archive_email_to_courier(
    data: dict,
    owner_user_id: UUID | None,
    route_handle: str | None,
    db: AsyncSession,
    conversation_id: UUID | None = None,
    resolved_sender_id: UUID | None = None,
) -> str | None:
    """Create a reference Courier thread/message for inbound email."""
    if not owner_user_id:
        return None

    from_addr = data.get("from") or "Unknown sender"
    if isinstance(from_addr, list):
        from_addr = from_addr[0] if from_addr else "Unknown sender"
    from_email = _extract_email_address(from_addr)
    subject = data.get("subject") or "(no subject)"
    body = data.get("text") or data.get("html") or ""
    preview_body = body.strip()
    if len(preview_body) > 4000:
        preview_body = f"{preview_body[:4000]}\n\n[Email clipped for Courier preview]"

    conv = None
    if conversation_id:
        conv_result = await db.execute(
            select(Conversation)
            .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
            .where(
                and_(
                    Conversation.id == conversation_id,
                    ConversationParticipant.user_id == owner_user_id,
                )
            )
            .limit(1)
        )
        conv = conv_result.scalar_one_or_none()
        if not conv:
            raise HTTPException(status_code=403, detail="Conversation recipient is no longer a participant")

    if not conv:
        # Match on WHO replied, not on what a thread happens to be called.
        #
        # This previously matched `Conversation.name == from_addr`, so a reply
        # only rejoined its thread if that thread was literally named after the
        # sender's email address. Any thread named after the person, a booking,
        # or anything else missed, and the reply started a new thread beside the
        # real conversation. Names are display text and change; identity does
        # not.
        sender = await find_user_by_email(db, from_email)
        if sender is not None:
            conv = await find_direct_conversation(db, {owner_user_id, sender.id})
        if not conv:
            conv = Conversation(
                name=from_addr,
                is_group=False,
                created_by=owner_user_id,
            )
            db.add(conv)
            await db.flush()
            db.add(ConversationParticipant(conversation_id=conv.id, user_id=owner_user_id, role="owner"))

    if conversation_id:
        # Recheck the resolved sender's membership; never add a participant via
        # a guessed UUID or a global email lookup that may match another user.
        participant_result = await db.execute(
            select(ConversationParticipant.id)
            .join(User, User.id == ConversationParticipant.user_id)
            .where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == resolved_sender_id,
                func.lower(func.trim(User.email)) == from_email,
            )
        )
        if not resolved_sender_id or not participant_result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Conversation sender is no longer a participant")
        inbound_sender_id = sender_user_id = resolved_sender_id
    else:
        sender_user_id = owner_user_id
        inbound_sender = await find_user_by_email(db, from_email)
        inbound_sender_id = inbound_sender.id if inbound_sender else None
        if inbound_sender_id:
            sender_user_id = inbound_sender_id
            participant_result = await db.execute(
                select(ConversationParticipant.id).where(
                    ConversationParticipant.conversation_id == conv.id,
                    ConversationParticipant.user_id == inbound_sender_id,
                )
            )
            if not participant_result.scalar_one_or_none():
                db.add(ConversationParticipant(conversation_id=conv.id, user_id=inbound_sender_id, role="member"))

    metadata = {
        "channel": "email",
        "direction": "inbound",
        "from": from_addr,
        "from_email": from_email,
        "to": data.get("to", []),
        "subject": subject,
        "route_handle": route_handle,
        "archived_for_protection": True,
        "threaded_from_reply_to": conversation_id is not None,
        "threaded_conversation_id": str(conversation_id) if conversation_id else None,
        "sender_resolved_from_email": inbound_sender_id is not None,
    }
    import json as _json

    db.add(
        Message(
            conversation_id=conv.id,
            sender_id=sender_user_id,
            content=f"Subject: {subject}\n\n{preview_body}",
            message_type="email",
            metadata_json=_json.dumps(metadata),
        )
    )
    conv.updated_at = datetime.now(UTC)
    await db.flush()
    return str(conv.id)


@router.post("/email-inbound")
async def handle_inbound_email(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Resend webhook handler for email.received events.

    Queries DB for dynamic artist routes, then sends reserved addresses and the
    catch-all to the owner inbox.

    The Svix signature is required outside local, development, and test
    environments and is verified before any database or forwarding work runs.
    """
    settings = get_settings()
    body = await request.body()

    verify_resend_webhook(
        body, request.headers, settings.RESEND_INBOUND_WEBHOOK_SECRET, settings, label="Inbound email webhook"
    )

    if not settings.RESEND_API_KEY:
        raise HTTPException(status_code=500, detail="RESEND_API_KEY not configured")

    import json as _json

    try:
        payload = _json.loads(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    event_type = payload.get("type")

    if event_type != "email.received":
        return {"status": "ignored", "event": event_type}

    data = payload.get("data", {})
    to_addresses = data.get("to", [])
    from_addr = data.get("from", "unknown")
    subject = data.get("subject", "(no subject)")

    logger.info(f"Inbound email: {from_addr} -> {to_addresses} | {subject}")

    data = await _retrieve_received_body(data, settings.RESEND_API_KEY)

    addresses = [to_addresses] if isinstance(to_addresses, str) else to_addresses
    conversation_reply = any(_extract_local_part(a).startswith("convo-") for a in addresses)
    if _is_automatic_email(data, conversation_reply=conversation_reply):
        logger.info("Ignored automatic or forwarded inbound email (conversation_reply=%s)", conversation_reply)
        return {"status": "ignored", "reason": "automatic_or_forwarded_email"}
    if conversation_reply:
        # Use the receiving server's computed result, never an Authentication-
        # Results header supplied by the sender. A signed webhook alone only
        # authenticates Resend, not the person in the From header.
        authentication = data.get("authentication")
        if not isinstance(authentication, dict) or authentication.get("dmarc") != "pass":
            raise HTTPException(status_code=403, detail="Conversation email sender authentication is required")
    route = await _resolve_destination(to_addresses, db, from_addr)
    if route.conversation_id and _sender_address(route.destination) == _sender_address(from_addr):
        raise HTTPException(status_code=409, detail="Email would forward to its sender")
    courier_conversation_id = await _archive_email_to_courier(
        data,
        route.owner_user_id,
        route.route_handle,
        db,
        route.conversation_id,
        route.sender_user_id,
    )
    if not route.destination:
        logger.warning(
            "Inbound email archived to the app inbox only: set SHOP_OWNER_EMAIL or SHOP_EMAIL to forward owner mail"
        )
        return {
            "status": "archived",
            "reason": "no_owner_email_configured",
            "courier_conversation_id": courier_conversation_id,
        }
    if not sender_identity("resend", settings)[1]:
        logger.warning("Inbound email archived without forwarding: set RESEND_FROM_EMAIL or SHOP_EMAIL")
        return {
            "status": "archived",
            "reason": "no_sender_configured",
            "courier_conversation_id": courier_conversation_id,
        }
    if not route.conversation_id:
        raw_sender = from_addr[0] if isinstance(from_addr, list) and from_addr else from_addr
        if isinstance(raw_sender, str) and _extract_email_address(raw_sender) == _extract_email_address(
            route.destination
        ):
            logger.info("Archived reserved-inbox self-forward without sending it back")
            return {
                "status": "archived",
                "reason": "self_forward_skipped",
                "courier_conversation_id": courier_conversation_id,
            }
    try:
        await _forward_email(data, route.destination, settings.RESEND_API_KEY, route.conversation_id)
    except httpx.HTTPError as exc:
        await db.rollback()
        logger.warning("Inbound email forwarding failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Email forwarding is temporarily unavailable") from exc

    return {"status": "forwarded", "to": route.destination, "courier_conversation_id": courier_conversation_id}
