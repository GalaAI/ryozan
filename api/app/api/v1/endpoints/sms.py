"""SMS operations endpoints — log viewer, manual send, opt-in toggle, Twilio webhook.

Split from `sms_templates.py` so the webhook can live without auth and the
log viewer can have its own simple list/filter contract.
"""

import hashlib
import hmac
import json
import logging
import re
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.v1.endpoints.conversations import _queue_conversation_notifications
from app.constants.status import BookingStatus
from app.core.config import get_settings
from app.core.database import async_session, get_db
from app.core.routes import ROUTES
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking import Booking
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.notification import Notification as NotificationModel
from app.models.sms_message import SmsMessage
from app.models.user import User
from app.services.email import should_notify
from app.services.pack_bridge import build_pack_capture, queue_pack_capture
from app.services.push import send_push_notification
from app.services.sms import (
    SmsLaneConfigurationError,
    normalise_phone,
    placeholder_contact_email,
    project_sms_delivery_status,
    resolve_sms_lane_by_number,
    send_sms,
    send_twilio_whatsapp_text,
    set_opt_in,
    set_opt_out,
    sms_help_reply,
)

logger = logging.getLogger("ryozan.sms")
router = APIRouter(prefix="/sms", tags=["sms"])


# ── Helpers ──────────────────────────────────────────


def _is_admin(user: User) -> bool:
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK.get("admin", 3)


def _is_staff(user: User) -> bool:
    """Artist, admin, or founder."""
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK.get("artist", 2)


def _require_admin(user: User) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role or higher required")


def _require_staff(user: User) -> None:
    if not _is_staff(user):
        raise HTTPException(status_code=403, detail="Staff role or higher required")


STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_KEYWORDS = {"START", "YES", "UNSTOP"}
HELP_KEYWORDS = {"HELP", "INFO"}

# Only the shop line carries booking conversations. Gala and ryozan-collector replies
# keep their own lane threads so outbound lane inheritance never shifts a
# booking thread onto a personal number.
BOOKING_THREAD_LANE_ID = "bella"


def _sms_lane_auto_reply_enabled(lane_id: str, *, is_whatsapp: bool, settings: Any) -> bool:
    if lane_id != "bella":
        return False
    return settings.BELLA_WHATSAPP_AUTO_REPLY_ENABLED if is_whatsapp else settings.BELLA_SMS_AUTO_REPLY_ENABLED


# ── Schemas ──────────────────────────────────────────


class SmsLogRow(BaseModel):
    id: UUID
    direction: str
    to_phone: str
    from_phone: str
    body: str
    status: str
    error_code: str | None
    error_message: str | None
    twilio_sid: str | None
    template_slug: str | None
    num_segments: int | None
    created_at: datetime
    delivered_at: datetime | None

    model_config = {"from_attributes": True}


class ManualSendRequest(BaseModel):
    to: str
    body: str
    is_transactional: bool = True


class OptInToggleRequest(BaseModel):
    phone: str
    opt_in: bool


# ── List / search log ──────────────────────────────────────────


@router.get("/log", response_model=list[SmsLogRow])
async def list_messages(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
    direction: str | None = None,
    status: str | None = None,
    phone: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    """Staff+: list SMS messages.

    * Admin+ sees every message.
    * Staff (artist) sees only messages they sent (outbound) or inbound
      replies from a phone matching one of their conversation participants.
      Simpler v1: just filter by ``sender_id`` for outbound. Inbound from any
      phone they've ever texted is included by matching the phone set.
    """
    _require_staff(current_user)
    limit = max(1, min(500, limit))
    query = select(SmsMessage).order_by(desc(SmsMessage.created_at))
    if direction:
        query = query.where(SmsMessage.direction == direction)
    if status:
        query = query.where(SmsMessage.status == status)
    if phone:
        e164 = normalise_phone(phone) or phone
        query = query.where((SmsMessage.to_phone == e164) | (SmsMessage.from_phone == e164))

    if not _is_admin(current_user):
        # Find every phone this user has texted (their outbound to_phones),
        # then surface their outbound + inbound from those phones.
        outbound_phones_q = select(SmsMessage.to_phone).where(SmsMessage.sender_id == current_user.id)
        outbound_phones = {row[0] for row in (await db.execute(outbound_phones_q)).all()}
        query = query.where(
            (SmsMessage.sender_id == current_user.id)
            | ((SmsMessage.direction == "inbound") & SmsMessage.from_phone.in_(outbound_phones or {""}))
        )

    query = query.limit(limit).offset(offset)
    rows = (await db.execute(query)).scalars().all()
    return rows


@router.get("/stats")
async def sms_stats(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Staff+: counts for the SMS dashboard tile.

    Admin+ sees shop-wide totals plus opt-in/out counts. Staff sees their
    personal sent count (opt-in/out tiles are hidden for them in the UI).
    """
    _require_staff(current_user)
    base = select(func.count()).select_from(SmsMessage)
    if not _is_admin(current_user):
        base = base.where(SmsMessage.sender_id == current_user.id)

    total = await db.scalar(base)
    sent = await db.scalar(base.where(SmsMessage.status.in_(["sent", "delivered", "queued", "sending"])))
    delivered = await db.scalar(base.where(SmsMessage.status == "delivered"))
    failed = await db.scalar(base.where(SmsMessage.status == "failed"))

    if _is_admin(current_user):
        inbound = await db.scalar(select(func.count()).select_from(SmsMessage).where(SmsMessage.direction == "inbound"))
        opted_in = await db.scalar(select(func.count()).select_from(User).where(User.sms_opt_in.is_(True)))
        opted_out = await db.scalar(select(func.count()).select_from(User).where(User.sms_opt_out_at.is_not(None)))
    else:
        inbound = 0
        opted_in = 0
        opted_out = 0

    return {
        "total": total or 0,
        "sent": sent or 0,
        "delivered": delivered or 0,
        "failed": failed or 0,
        "inbound": inbound or 0,
        "opted_in_users": opted_in or 0,
        "opted_out_users": opted_out or 0,
        "scope": "shop" if _is_admin(current_user) else "personal",
    }


# ── Manual send (admin) ──────────────────────────────────────────


async def _staff_can_text(db: AsyncSession, sender: User, phone_e164: str) -> bool:
    """Return True if `sender` (non-admin staff) is allowed to text this number.

    Allowed when at least one `ryozan_users` row with that phone shares a
    Courier conversation with `sender`. Implemented as: find users with this
    phone, then check whether any participate alongside `sender` in a
    conversation.
    """
    from app.models.message import ConversationParticipant

    users_with_phone_q = select(User.id, User.phone).where(User.phone.is_not(None))
    user_ids: set[UUID] = set()
    for user_id, phone in (await db.execute(users_with_phone_q)).all():
        # The phone column isn't normalised in storage; check on read.
        if phone and normalise_phone(phone) == phone_e164:
            user_ids.add(user_id)
    if not user_ids:
        return False

    # Sender's conversation IDs
    sender_convos_q = select(ConversationParticipant.conversation_id).where(
        ConversationParticipant.user_id == sender.id
    )
    sender_convos = {row[0] for row in (await db.execute(sender_convos_q)).all()}
    if not sender_convos:
        return False

    # Conversation IDs that include any of the phone-owners
    shared_q = select(ConversationParticipant.conversation_id).where(
        ConversationParticipant.user_id.in_(user_ids),
        ConversationParticipant.conversation_id.in_(sender_convos),
    )
    return (await db.execute(shared_q)).first() is not None


@router.post("/send")
async def send_manual(
    body: ManualSendRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Staff+: send a raw SMS to one number.

    * Admin+ can text any number.
    * Staff (artist) can only text numbers that belong to a user they share
      a conversation with — keeps an artist from texting another artist's
      client. Opt-in gates still apply at the SMS service level.
    """
    _require_staff(current_user)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Body cannot be empty")

    e164 = normalise_phone(body.to)
    if not _is_admin(current_user) and (not e164 or not await _staff_can_text(db, current_user, e164)):
        raise HTTPException(
            status_code=403,
            detail="You can only text numbers belonging to a client you already have a Courier conversation with",
        )

    log = await send_sms(
        db=db,
        to=body.to,
        body=body.body,
        sender_id=current_user.id,
        is_transactional=body.is_transactional,
    )
    return {
        "status": log.status,
        "twilio_sid": log.twilio_sid,
        "error_code": log.error_code,
        "error_message": log.error_message,
        "segments": log.num_segments,
    }


# ── Opt-in toggle (admin tool for fixing wonky states) ──────────────────


@router.post("/opt-in-toggle")
async def opt_in_toggle(
    body: OptInToggleRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Admin+: manually flip a phone's opt-in state. Used to honour written
    consent on paper waivers or to clear an erroneous opt-out."""
    _require_admin(current_user)
    e164 = normalise_phone(body.phone)
    if not e164:
        raise HTTPException(status_code=400, detail="Invalid phone")
    if body.opt_in:
        affected = await set_opt_in(db, e164)
    else:
        affected = await set_opt_out(db, e164)
    return {"phone": e164, "users_updated": affected, "opt_in": body.opt_in}


# ── Twilio webhook (inbound + status callback) ──────────────────────────


def _validate_twilio_signature(request: Request, body_form: dict[str, Any]) -> bool:
    """Validate the X-Twilio-Signature header.

    Twilio signs: hmac_sha1(auth_token, full_url + sorted_form_params_concatenated)
    base64-encoded. We compare with the X-Twilio-Signature header.
    """
    settings = get_settings()
    from app.services.shop_texts import webhook_tokens

    token = settings.TWILIO_WEBHOOK_AUTH_TOKEN or settings.TWILIO_AUTH_TOKEN
    # The shop's own line signs with its own account's token.
    tokens = [t for t in (token, *webhook_tokens()) if t]
    if not tokens:
        is_local_dev = settings.DEBUG or settings.APP_ENV.lower() in {"development", "dev", "local", "test"}
        if is_local_dev:
            logger.warning("twilio.webhook.no_token_configured — skipping signature check in local/dev")
            return True
        logger.error("twilio.webhook.no_token_configured — rejecting webhook in non-dev environment")
        return False
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        return False
    public_base = (settings.API_PUBLIC_BASE_URL or "").rstrip("/")
    if public_base:
        url = f"{public_base}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
    else:
        url = str(request.url)
    sorted_keys = sorted(body_form.keys())
    payload = url + "".join(f"{k}{body_form[k]}" for k in sorted_keys)
    return any(
        hmac.compare_digest(
            b64encode(hmac.new(t.encode(), payload.encode(), hashlib.sha1).digest()).decode(), signature
        )
        for t in tokens
    )


def _twiml_response(message: str | None = None) -> Response:
    """Build a minimal TwiML response. Empty `<Response/>` means do nothing
    (Twilio's Advanced Opt-Out / our own send handles the reply)."""
    if message:
        # XML-escape the body
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
    else:
        xml = '<?xml version="1.0" encoding="UTF-8"?><Response/>'
    return Response(content=xml, media_type="application/xml")


async def _resolve_courier_owner(db: AsyncSession) -> User:
    result = await db.execute(
        select(User)
        .where(User.role.in_(("founder", "admin")))
        .order_by(desc(User.role == "founder"), User.created_at.asc())
        .limit(1)
    )
    owner = result.scalar_one_or_none()
    if owner:
        return owner
    fallback = await db.scalar(select(User).order_by(User.created_at.asc()).limit(1))
    if not fallback:
        raise HTTPException(status_code=500, detail="No Courier owner available for inbound SMS")
    return fallback


async def _resolve_sms_sender(db: AsyncSession, phone_e164: str) -> User:
    users = await db.execute(select(User).where(User.phone.is_not(None)))
    for user in users.scalars():
        if normalise_phone(user.phone) == phone_e164:
            return user

    email = placeholder_contact_email(f"sms-{phone_e164.lstrip('+')}")
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        return existing

    user = User(
        email=email,
        name=phone_e164,
        role="collector",
        phone=phone_e164,
        password_hash="sms-inbound-placeholder",
    )
    db.add(user)
    await db.flush()
    return user


async def _resolve_sms_conversation(
    db: AsyncSession,
    owner: User,
    sender: User,
    phone_e164: str,
    channel: str = "sms",
    lane_id: str = "bella",
    lane_label: str = "Bella",
) -> Conversation:
    channel_label = {"whatsapp": "WhatsApp", "voice": "Voice"}.get(channel, "SMS")
    conversation_name = f"{channel_label} - {lane_label}: {phone_e164}"
    existing = await db.execute(
        select(Conversation)
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .where(
            Conversation.name == conversation_name,
            ConversationParticipant.user_id == owner.id,
        )
        .limit(1)
    )
    conversation = existing.scalar_one_or_none()
    if conversation:
        return conversation

    conversation = Conversation(
        name=conversation_name,
        is_group=False,
        created_by=owner.id,
    )
    db.add(conversation)
    await db.flush()
    db.add(ConversationParticipant(conversation_id=conversation.id, user_id=owner.id, role="owner"))
    if sender.id != owner.id:
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=sender.id, role="member"))
    logger.info("Created %s Courier thread for lane=%s", channel_label, lane_id)
    return conversation


# Collector replies on the shop (Bella) line belong to the booking thread they
# are answering, not a founder-only lane thread. Completed bookings stay
# eligible for a short aftercare window.
_BOOKING_THREAD_OPEN_STATUSES = (BookingStatus.PENDING, BookingStatus.QUOTED, BookingStatus.CONFIRMED)
_BOOKING_THREAD_COMPLETED_WINDOW = timedelta(days=45)
_BOOKING_THREAD_CANDIDATE_LIMIT = 25
_STAFF_ROLES = tuple(role for role, rank in ROLE_RANK.items() if rank >= ROLE_RANK.get("assistant", 1))
# Mirrors bookings._require_booking_management_access: assistants and admin+.
_SHOP_MANAGER_ROLES = tuple(
    role for role, rank in ROLE_RANK.items() if role == "assistant" or rank >= ROLE_RANK.get("admin", 3)
)


# Staff messages a collector actually saw. Internal rows (audit records from
# archive or redact) must not make an older thread look freshest.
_RANKING_MESSAGE_TYPES = (
    "text",
    "email",
    "sms",
    "whatsapp",
    "voice",
    "slot_proposal",
    "quote_proposal",
    "booky_confirmation",
    "booking_approval",
)


def _phone_match_sql(column: Any, phone_e164: str) -> Any | None:
    """SQL test that a stored phone is this caller.

    North American callers (+1) match stored numbers with or without the
    leading 1. Every other caller must match the full number with its country
    code, so an international number never matches a US suffix.
    """
    digits = re.sub(r"\D", "", phone_e164)
    stored = func.regexp_replace(column, r"\D", "", "g")
    if phone_e164.startswith("+1") and len(digits) == 11:
        return or_(stored == digits[1:], stored == digits)
    if len(digits) < 8:
        return None
    return stored == digits


async def _candidate_bookings_for_sms(db: AsyncSession, sender: User, phone_e164: str) -> list[Booking]:
    """Open (or recently completed) bookings that belong to this phone, newest first."""
    guest_match = _phone_match_sql(Booking.guest_phone, phone_e164)
    if guest_match is None:
        return []
    client = aliased(User)
    # The aftercare window runs from the appointment, which edits do not move.
    completed_since = (datetime.now(UTC) - _BOOKING_THREAD_COMPLETED_WINDOW).date()
    return list(
        (
            await db.execute(
                select(Booking)
                .outerjoin(client, client.id == Booking.client_id)
                .where(
                    or_(
                        Booking.status.in_(_BOOKING_THREAD_OPEN_STATUSES),
                        and_(
                            Booking.status == BookingStatus.COMPLETED,
                            Booking.scheduled_date.is_not(None),
                            Booking.scheduled_date >= completed_since,
                        ),
                    ),
                    or_(
                        Booking.client_id == sender.id,
                        guest_match,
                        _phone_match_sql(client.phone, phone_e164),
                    ),
                )
                .order_by(Booking.created_at.desc())
                .limit(_BOOKING_THREAD_CANDIDATE_LIMIT)
            )
        )
        .scalars()
        .all()
    )


async def _find_booking_for_sms(db: AsyncSession, sender: User, phone_e164: str) -> Booking | None:
    """Pick the booking an inbound shop-line SMS is most likely answering.

    The thread that last reached this collector wins: the latest staff Courier
    message or the latest outbound SMS to this phone (receipts, confirmations,
    message alerts), whichever is newer. With no such contact on any candidate,
    the newest booking wins.
    """
    candidates = await _candidate_bookings_for_sms(db, sender, phone_e164)
    if not candidates:
        return None

    conversation_ids = [booking.conversation_id for booking in candidates if booking.conversation_id]
    last_touch: dict[UUID, datetime] = {}
    if conversation_ids:
        staff_rows = await db.execute(
            select(Message.conversation_id, func.max(Message.created_at))
            .join(User, User.id == Message.sender_id)
            .where(
                Message.conversation_id.in_(conversation_ids),
                User.role.in_(_STAFF_ROLES),
                Message.message_type.in_(_RANKING_MESSAGE_TYPES),
            )
            .group_by(Message.conversation_id)
        )
        sms_rows = await db.execute(
            select(SmsMessage.conversation_id, func.max(SmsMessage.created_at))
            .where(
                SmsMessage.conversation_id.in_(conversation_ids),
                SmsMessage.direction == "outbound",
                SmsMessage.to_phone == phone_e164,
            )
            .group_by(SmsMessage.conversation_id)
        )
        for conversation_id, touched_at in [*staff_rows.all(), *sms_rows.all()]:
            if touched_at and (conversation_id not in last_touch or touched_at > last_touch[conversation_id]):
                last_touch[conversation_id] = touched_at

    touched = [booking for booking in candidates if booking.conversation_id in last_touch]
    if touched:
        return max(touched, key=lambda booking: last_touch[booking.conversation_id])
    return candidates[0]


async def _ensure_participant(db: AsyncSession, conversation_id: UUID, user_id: UUID, role: str) -> None:
    existing = await db.scalar(
        select(ConversationParticipant.id).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
        )
    )
    if not existing:
        db.add(ConversationParticipant(conversation_id=conversation_id, user_id=user_id, role=role))


async def _booking_thread(db: AsyncSession, booking: Booking, collector: User) -> Conversation:
    """The booking's own Courier thread, created when the booking has none.

    Never reuses another artist-client conversation: a repeat collector has one
    thread per booking, and borrowing one would merge appointments.
    """
    if booking.conversation_id:
        existing = await db.get(Conversation, booking.conversation_id)
        if existing:
            return existing
    conversation = Conversation(
        name=collector.name or booking.guest_name or "Client",
        is_group=False,
        created_by=booking.artist_id,
    )
    db.add(conversation)
    await db.flush()
    booking.conversation_id = conversation.id
    return conversation


async def _resolve_booking_sms_conversation(
    db: AsyncSession,
    sender: User,
    phone_e164: str,
) -> tuple[Conversation, User, Booking] | None:
    """Return the booking thread, the collector identity to post as, and the booking."""
    booking = await _find_booking_for_sms(db, sender, phone_e164)
    if booking is None:
        return None
    collector = sender
    if booking.client_id and booking.client_id != sender.id:
        collector = await db.get(User, booking.client_id) or sender
    conversation = await _booking_thread(db, booking, collector)
    await _ensure_participant(db, conversation.id, booking.artist_id, "owner")
    await _ensure_participant(db, conversation.id, collector.id, "member")
    return conversation, collector, booking


async def _notify_inbound_sms_recipients(
    db: AsyncSession,
    *,
    conversation: Conversation,
    message: Message,
    sender: User,
    booking: Booking,
) -> None:
    """Alert the booking's artist and shop managers the same way a portal message does.

    Other artists left on the thread (for example after a reassignment) are not
    alerted: they can no longer manage the booking.

    In-app, push and email follow each recipient's preferences. SMS alerts go
    to staff only, through the existing Courier outbox, so a collector never
    receives another collector's text.
    """
    participant_ids = list(
        (
            await db.execute(
                select(ConversationParticipant.user_id)
                .join(User, User.id == ConversationParticipant.user_id)
                .where(
                    ConversationParticipant.conversation_id == conversation.id,
                    ConversationParticipant.user_id != sender.id,
                    or_(
                        ConversationParticipant.user_id == booking.artist_id,
                        User.role.in_(_SHOP_MANAGER_ROLES),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    if not participant_ids:
        return
    sender_first = (sender.name or "A collector").split()[0]
    preview = message.content[:80] + ("..." if len(message.content) > 80 else "")
    action_url = ROUTES.COURIER_CONVO.format(id=conversation.id)
    for recipient_id in participant_ids:
        db.add(
            NotificationModel(
                user_id=recipient_id,
                title=f"Text from {sender_first}",
                body=preview,
                notification_type="message",
                action_url=action_url,
            )
        )
        if await should_notify(db, recipient_id, "push", "messages"):
            await send_push_notification(
                db,
                recipient_id,
                title=f"Text from {sender_first}",
                body=preview,
                url=action_url,
            )
    await _queue_conversation_notifications(
        db,
        message=message,
        sender=sender,
        conversation=conversation,
        recipient_ids=participant_ids,
        allow_sms_channel_message=True,
    )


async def _archive_sms_to_courier(
    db: AsyncSession,
    *,
    log: SmsMessage,
    phone_e164: str,
    to_phone: str,
    body_text: str,
    message_sid: str | None,
    num_media: int,
    channel: str = "sms",
    lane_id: str = "bella",
    lane_label: str = "Bella",
    assigned_agent_id: str = "bella",
    assigned_agent_label: str = "Bella",
    reply_mode: str = "gated_auto_reply",
    voice_status: str = "not_configured",
) -> tuple[Conversation, User, User, Message]:
    owner = await _resolve_courier_owner(db)
    sender = await _resolve_sms_sender(db, phone_e164)
    booking_route = None
    if channel == "sms" and lane_id == BOOKING_THREAD_LANE_ID:
        booking_route = await _resolve_booking_sms_conversation(db, sender, phone_e164)
    if booking_route:
        conversation, sender, routed_booking = booking_route
    else:
        routed_booking = None
        conversation = await _resolve_sms_conversation(
            db,
            owner,
            sender,
            phone_e164,
            channel,
            lane_id=lane_id,
            lane_label=lane_label,
        )
    now = datetime.now(UTC)
    message = Message(
        conversation_id=conversation.id,
        sender_id=sender.id,
        content=body_text + (f" [+{num_media} media]" if num_media else ""),
        message_type=channel,
        metadata_json=json.dumps(
            {
                "channel": channel,
                "provider": "twilio_whatsapp" if channel == "whatsapp" else "twilio",
                "direction": "inbound",
                "lane_id": lane_id,
                "lane_label": lane_label,
                "assigned_agent_id": assigned_agent_id,
                "assigned_agent_label": assigned_agent_label,
                "assignment_status": "assigned",
                "reply_mode": reply_mode,
                "voice_status": voice_status,
                "from": phone_e164,
                "to": to_phone,
                "twilio_sid": message_sid,
                "sms_log_id": str(log.id),
                "received_at": now.isoformat(),
                "routed_booking_id": str(routed_booking.id) if routed_booking else None,
            }
        ),
        created_at=now,
        updated_at=now,
    )
    db.add(message)
    await db.flush()
    conversation.updated_at = now
    log.conversation_id = conversation.id
    log.user_id = sender.id
    if routed_booking:
        await _notify_inbound_sms_recipients(
            db, conversation=conversation, message=message, sender=sender, booking=routed_booking
        )
    return conversation, owner, sender, message


def _is_booking_routed(courier_message: Any) -> bool:
    try:
        metadata = json.loads(getattr(courier_message, "metadata_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(metadata, dict) and bool(metadata.get("routed_booking_id"))


def _clean_bella_reply(text: str) -> str:
    normalized = text.replace("\u2013", "-").replace("\u2014", "-")
    without_emoji = re.sub(r"[\U0001F300-\U0001FAFF\u2700-\u27BF]+", "", normalized)
    return re.sub(r"\s+", " ", without_emoji).strip()[:600]


async def _bella_channel_reply(
    *,
    conversation_id: UUID,
    owner_id: UUID,
    recipient_id: UUID,
    recipient_phone: str,
    channel: str,
    lane_id: str = "bella",
) -> None:
    """Generate one disclosed Bella reply and deliver it through Twilio."""
    settings = get_settings()
    enabled = (
        settings.BELLA_WHATSAPP_AUTO_REPLY_ENABLED if channel == "whatsapp" else settings.BELLA_SMS_AUTO_REPLY_ENABLED
    )
    if not enabled:
        return
    if not settings.ARK_MODEL_GATEWAY_KEY:
        logger.warning("Bella WhatsApp reply skipped: ARK_MODEL_GATEWAY_KEY not set")
        return

    try:
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(Message, User.name)
                    .join(User, Message.sender_id == User.id)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(desc(Message.created_at))
                    .limit(20)
                )
            ).all()
            if not rows:
                return

            transcript_lines: list[str] = []
            has_bella_reply = False
            for message, sender_name in reversed(rows):
                metadata: dict[str, Any] = {}
                if message.metadata_json:
                    try:
                        metadata = json.loads(message.metadata_json)
                    except (TypeError, ValueError):
                        metadata = {}
                if metadata.get("bella_generated"):
                    has_bella_reply = True
                role = "Shop" if metadata.get("direction") == "outbound" else sender_name
                transcript_lines.append(f"{role}: {message.content}")

            disclosure = (
                "This is your first reply in the thread. Start by saying you are Ensei, the AI assistant "
                f"for {settings.STUDIO_NAME}. "
                if not has_bella_reply
                else "You have already disclosed that you are Ensei, the studio's AI assistant."
            )
            system_prompt = (
                f"You are Ensei, the AI assistant for {settings.STUDIO_NAME}. "
                f"{disclosure} Be warm, concise, and conversational. Ask only one question at a time. "
                "Help with tattoo inquiries, booking intake, appointment support, and general shop questions. "
                "Collect only what is needed: name, tattoo idea, placement, approximate size, style, preferred "
                "artist, and availability. Never claim an appointment is booked, a price is final, or an artist "
                "has approved a design. Do not invent hours, policies, availability, or artist preferences. "
                "For medical, payment, complaint, or urgent issues, say a shop team member will follow up. "
                "Never use emojis. Keep the reply to 1-3 short sentences."
            )

            from app.services.ai_routing import call_ark_gateway_chat_completion
            from app.services.api_budget import log_usage

            response = await call_ark_gateway_chat_completion(
                settings=settings,
                primary_model=settings.AI_PREMIUM_PRIMARY_MODEL,
                fallback_models=[settings.AI_PREMIUM_FALLBACK_MODEL, settings.AI_PAID_FALLBACK_MODEL],
                max_tokens=220,
                tools=[],
                system_prompt=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": "Conversation so far:\n"
                        + "\n".join(transcript_lines)
                        + f"\n\nWrite Ensei's next {channel.upper()} reply.",
                    }
                ],
            )
            choice = ((response.get("choices") or [{}])[0]).get("message") or {}
            reply_text = _clean_bella_reply(choice.get("content") or "")
            if not reply_text:
                logger.warning("Bella %s returned empty content for conversation %s", channel, conversation_id)
                return
            if not has_bella_reply and ("ensei" not in reply_text.lower() or "ai" not in reply_text.lower()):
                reply_text = f"Hi, I'm Ensei, the AI assistant for {settings.STUDIO_NAME}. " + reply_text

            if channel == "whatsapp":
                delivery_log = await send_twilio_whatsapp_text(
                    db=db,
                    to=recipient_phone,
                    body=reply_text,
                    conversation_id=conversation_id,
                    user_id=recipient_id,
                    sender_id=owner_id,
                )
                provider = "twilio_whatsapp"
            else:
                delivery_log = await send_sms(
                    db=db,
                    to=recipient_phone,
                    body=reply_text,
                    conversation_id=conversation_id,
                    user_id=recipient_id,
                    sender_id=owner_id,
                    is_transactional=True,
                    lane_id=lane_id,
                )
                provider = "twilio"
            now = datetime.now(UTC)
            db.add(
                Message(
                    conversation_id=conversation_id,
                    sender_id=owner_id,
                    content=reply_text,
                    message_type=channel,
                    metadata_json=json.dumps(
                        {
                            "channel": channel,
                            "provider": provider,
                            "direction": "outbound",
                            "bella_generated": True,
                            "lane_id": lane_id,
                            "assigned_agent_id": "bella",
                            "assigned_agent_label": "Bella",
                            "assignment_status": "assigned",
                            "reply_mode": "gated_auto_reply",
                            "ai_disclosed": True,
                            "to": recipient_phone,
                            "sms_log_id": str(delivery_log.id),
                            "twilio_sid": delivery_log.twilio_sid,
                            "delivery_status": delivery_log.status,
                            "error_code": delivery_log.error_code,
                            "sent_at": now.isoformat(),
                        }
                    ),
                    created_at=now,
                    updated_at=now,
                )
            )
            conversation = await db.get(Conversation, conversation_id)
            if conversation:
                conversation.updated_at = now
            usage = response.get("usage") or {}
            await log_usage(
                db,
                f"conversations/bella-{channel}",
                int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                owner_id,
            )
            await db.commit()
    except Exception:
        logger.exception("Bella %s reply failed for conversation %s", channel, conversation_id)


async def _bella_whatsapp_reply(
    *,
    conversation_id: UUID,
    owner_id: UUID,
    recipient_id: UUID,
    recipient_phone: str,
) -> None:
    """Compatibility wrapper for the established WhatsApp release contract."""
    await _bella_channel_reply(
        conversation_id=conversation_id,
        owner_id=owner_id,
        recipient_id=recipient_id,
        recipient_phone=recipient_phone,
        channel="whatsapp",
        lane_id="bella",
    )


@router.post("/webhook/inbound")
async def twilio_inbound_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Receive inbound SMS or WhatsApp from Twilio and archive it in Courier."""
    form = await request.form()
    body_form = {k: str(v) for k, v in form.items()}

    if not _validate_twilio_signature(request, body_form):
        logger.warning("twilio.inbound.bad_signature from=%s", body_form.get("From"))
        raise HTTPException(status_code=403, detail="Invalid signature")

    from_phone = body_form.get("From", "")
    to_phone = body_form.get("To", "")
    is_whatsapp = from_phone.lower().startswith("whatsapp:") or to_phone.lower().startswith("whatsapp:")
    channel = "whatsapp" if is_whatsapp else "sms"
    body_text = body_form.get("Body", "").strip()
    sid = body_form.get("MessageSid")
    num_media = int(body_form.get("NumMedia", "0") or 0)
    e164 = normalise_phone(from_phone)

    if sid:
        duplicate = await db.scalar(select(SmsMessage.id).where(SmsMessage.twilio_sid == sid))
        if duplicate:
            logger.info("twilio.inbound.duplicate sid=%s from=%s", sid, from_phone)
            return _twiml_response()

    lane_id = "bella"
    lane_label = "Bella"
    assigned_agent_id = "bella"
    assigned_agent_label = "Bella"
    reply_mode = "gated_auto_reply"
    voice_status = "not_configured"
    if not is_whatsapp:
        try:
            lane = resolve_sms_lane_by_number(to_phone)
        except SmsLaneConfigurationError as exc:
            unclassified_body = body_text[:500]
            if num_media:
                unclassified_body = f"{unclassified_body} [+{num_media} media]"
            unclassified_log = SmsMessage(
                direction="inbound",
                to_phone=normalise_phone(to_phone) or "(unclassified)",
                from_phone=e164 or from_phone,
                body=unclassified_body[:600],
                status="unclassified",
                error_code=exc.code,
                error_message="Inbound receiving number did not resolve to one configured SMS lane",
                twilio_sid=sid,
            )
            db.add(unclassified_log)
            await db.flush()
            logger.warning("twilio.inbound.sender_lane_unclassified sid=%s code=%s", sid, exc.code)
            return _twiml_response()
        lane_id = lane.id
        lane_label = lane.label
        assigned_agent_id = lane.assigned_agent_id
        assigned_agent_label = lane.assigned_agent_label
        reply_mode = lane.reply_mode
        voice_status = lane.voice_status

    # Log the inbound message first.
    log = SmsMessage(
        direction="inbound",
        to_phone=normalise_phone(to_phone) or to_phone,
        from_phone=e164 or from_phone,
        body=body_text + (f" [+{num_media} media]" if num_media else ""),
        status="received",
        twilio_sid=sid,
    )
    db.add(log)
    await db.flush()

    # Keyword routing.
    keyword = re.split(r"\s+", body_text, maxsplit=1)[0].upper() if body_text else ""
    reply = None

    if keyword in STOP_KEYWORDS:
        if e164:
            await set_opt_out(db, e164)
            # Twilio Advanced Opt-Out already sends the standard reply, but we
            # log it explicitly. Reply with empty TwiML to avoid double-send.
            log.template_slug = "stop_confirmation"
            logger.info("twilio.inbound.stop from=%s", e164)
        else:
            logger.warning("twilio.inbound.stop.invalid_phone raw_from=%r", from_phone)
    elif keyword in START_KEYWORDS:
        if e164:
            await set_opt_in(db, e164)
            reply = (
                f"{get_settings().STUDIO_NAME}: You're re-subscribed to booking and appointment "
                "messages. Reply HELP for help, STOP to cancel."
            )
            log.template_slug = "start_confirmation"
            logger.info("twilio.inbound.start from=%s", e164)
        else:
            logger.warning("twilio.inbound.start.invalid_phone raw_from=%r", from_phone)
    elif keyword in HELP_KEYWORDS:
        reply = sms_help_reply()
        log.template_slug = "help_response"
        logger.info("twilio.inbound.help from=%s", e164)
    else:
        # Archive the conversation in Courier. WhatsApp can optionally schedule
        # Bella after this inbound row is committed; SMS remains staff-handled.
        if e164:
            conversation, owner, sender, courier_message = await _archive_sms_to_courier(
                db,
                log=log,
                phone_e164=e164,
                to_phone=normalise_phone(to_phone) or to_phone,
                body_text=body_text,
                message_sid=sid,
                num_media=num_media,
                channel=channel,
                lane_id=lane_id,
                lane_label=lane_label,
                assigned_agent_id=assigned_agent_id,
                assigned_agent_label=assigned_agent_label,
                reply_mode=reply_mode,
                voice_status=voice_status,
            )
            capture = build_pack_capture(
                source_id=f"bttco:phone:twilio:{lane_id}",
                kind="courier",
                channel=channel,
                actor=assigned_agent_id,
                summary=body_text or f"Inbound {channel} message with {num_media} media item(s).",
                idempotency_key=f"twilio:{channel}:{sid or log.id}",
                content_ref=f"bttco://courier/{conversation.id}",
                metadata={
                    "provider": "twilio",
                    "laneId": lane_id,
                    "laneLabel": lane_label,
                    "assignedAgentId": assigned_agent_id,
                    "assignedAgentLabel": assigned_agent_label,
                    "assignmentStatus": "assigned",
                    "replyMode": reply_mode,
                    "voiceStatus": voice_status,
                    "eventType": "message.received",
                    "messageSid": sid,
                    "from": e164,
                    "to": normalise_phone(to_phone) or to_phone,
                    "courierConversationId": str(conversation.id),
                    "smsLogId": str(log.id),
                    "packTargets": ["ainbox", "courier", "huddle", "flow"],
                },
            )
            await queue_pack_capture(
                db,
                capture,
                courier_message_id=courier_message.id,
            )
            settings = get_settings()
            # A reply routed into a booking thread belongs to the artist, and a
            # staff member texting the shop line is not a client: Bella answers
            # neither.
            sender_is_staff = ROLE_RANK.get(getattr(sender, "role", None), 0) >= ROLE_RANK.get("assistant", 1)
            if (
                not _is_booking_routed(courier_message)
                and not sender_is_staff
                and _sms_lane_auto_reply_enabled(lane_id, is_whatsapp=is_whatsapp, settings=settings)
            ):
                await db.commit()
                background_tasks.add_task(
                    _bella_channel_reply,
                    conversation_id=conversation.id,
                    owner_id=owner.id,
                    recipient_id=sender.id,
                    recipient_phone=e164,
                    channel=channel,
                    lane_id=lane_id,
                )
        logger.info("twilio.inbound.message from=%s body=%r", e164 or from_phone, body_text[:80])

    await db.flush()
    return _twiml_response(reply)


@router.post("/webhook/status")
async def twilio_status_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Receive delivery callbacks and project them to the owning Courier Message."""
    form = await request.form()
    body_form = {k: str(v) for k, v in form.items()}
    if not _validate_twilio_signature(request, body_form):
        raise HTTPException(status_code=403, detail="Invalid signature")
    sid = body_form.get("MessageSid")
    status = body_form.get("MessageStatus") or body_form.get("SmsStatus")
    if not sid or not status:
        return Response(status_code=204)
    result = await db.execute(select(SmsMessage).where(SmsMessage.twilio_sid == sid))
    msg = result.scalar_one_or_none()
    if msg:
        msg.status = status
        if status == "delivered":
            msg.delivered_at = datetime.now(UTC)
        elif status in ("failed", "undelivered"):
            msg.error_code = body_form.get("ErrorCode") or msg.error_code
        if msg.courier_message_id:
            courier_message = await db.get(Message, msg.courier_message_id, with_for_update=True)
            if courier_message:
                try:
                    metadata = json.loads(courier_message.metadata_json or "{}")
                except (TypeError, json.JSONDecodeError):
                    metadata = {}
                projected_status = project_sms_delivery_status(status)
                delivery = metadata.get("notification_delivery")
                if isinstance(delivery, dict):
                    intents = delivery.get("intents", [])
                    for intent in intents:
                        if isinstance(intent, dict) and intent.get("sms_log_id") == str(msg.id):
                            intent["status"] = projected_status
                            intent["provider_status"] = status
                            intent["updated_at"] = datetime.now(UTC).isoformat()
                            if projected_status == "failed":
                                intent["last_error"] = msg.error_code or status
                    statuses = {str(item.get("status") or "queued") for item in intents if isinstance(item, dict)}
                    metadata["delivery_status"] = (
                        "failed"
                        if "failed" in statuses
                        else "queued"
                        if "queued" in statuses
                        else "sending"
                        if "sending" in statuses
                        else "delivered"
                        if statuses and statuses <= {"delivered", "opened", "clicked"}
                        else "sent"
                    )
                    delivery["updated_at"] = datetime.now(UTC).isoformat()
                else:
                    metadata["delivery_status"] = projected_status
                    metadata["provider_status"] = status
                    if projected_status == "failed":
                        metadata["error_code"] = msg.error_code
                courier_message.metadata_json = json.dumps(metadata)
                courier_message.updated_at = datetime.now(UTC)
        await db.flush()
    return Response(status_code=204)
