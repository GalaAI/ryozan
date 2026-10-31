"""
Conversation + messaging endpoints.

- Create 1:1 or group conversations
- List my conversations
- Send messages
- Get message history
- Mark as read
"""

import asyncio
import json
import logging
import re
import time as _time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import async_session, get_db
from app.core.routes import ROUTES
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking import Booking
from app.models.email_route import EmailRoute
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.notification import Notification as NotificationModel
from app.models.sms_message import SmsMessage
from app.models.user import User
from app.schemas.message import (
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
    ParticipantResponse,
)
from app.services import artifacts
from app.services.conversation_threads import conversation_reply_to
from app.services.courier_channels import CourierRouteContext, build_channel_status
from app.services.email import FROM_SHOP, send_email, should_notify
from app.services.push import send_push_notification
from app.services.reward_engine import reward_user_by_id
from app.services.sms import (
    SmsLaneConfigurationError,
    mask_sms_sender,
    normalise_phone,
    project_sms_delivery_status,
    resolve_sms_lane_by_id,
    resolve_sms_lane_by_number,
    send_from_template,
    send_sms,
    send_twilio_whatsapp_text,
)

logger = logging.getLogger("ryozan.courier")

UNICODE_DASHES = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
    }
)
EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002700-\U000027bf]+",
    flags=re.UNICODE,
)


def _clean_ai_draft(text: str) -> str:
    draft = text.translate(UNICODE_DASHES)
    draft = EMOJI_RE.sub("", draft)
    draft = " ".join(draft.split()).strip()
    if len(draft) >= 2 and draft[0] == draft[-1] and draft[0] in {'"', "'"}:
        draft = draft[1:-1].strip()
    return draft


router = APIRouter(prefix="/conversations", tags=["conversations"])


class ChannelSendRequest(BaseModel):
    channel: str = Field(pattern="^(courier|sms|whatsapp)$")
    content: str = Field(min_length=1, max_length=1600)
    to: str | None = None
    is_transactional: bool = True


# Helpers


async def _verify_participant(
    conversation_id: UUID,
    user_id: UUID,
    db: AsyncSession,
    proxy_for: UUID | None = None,
) -> ConversationParticipant:
    """Ensure user (or their proxy target) is a member of the conversation."""
    ids = [user_id] + ([proxy_for] if proxy_for else [])
    result = await db.execute(
        select(ConversationParticipant)
        .where(
            and_(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id.in_(ids),
            )
        )
        .limit(1)
    )
    participant = result.scalar_one_or_none()
    if not participant:
        raise HTTPException(status_code=403, detail="Not a participant in this conversation")
    return participant


def _proxy_target(current_user: User) -> UUID | None:
    """The user this account acts as in Courier, when STAFF_PROXY_ENABLED is on.

    With the setting off a stored proxy_for is ignored and every account acts
    only as itself.
    """
    if not get_settings().STAFF_PROXY_ENABLED:
        return None
    return current_user.proxy_for


async def _resolve_effective_user(current_user: User, db: AsyncSession) -> User:
    """Return the user a proxy operator acts as in Courier."""
    proxy_for = _proxy_target(current_user)
    if not proxy_for:
        return current_user
    proxy_user_result = await db.execute(select(User).where(User.id == proxy_for))
    return proxy_user_result.scalar_one_or_none() or current_user


def _visible_user_ids(current_user: User) -> list[UUID]:
    """Return IDs the current session may act as or view in Courier."""
    proxy_for = _proxy_target(current_user)
    return [current_user.id] + ([proxy_for] if proxy_for else [])


async def _require_conversation_creation_access(
    body: ConversationCreate,
    effective_user: User,
    db: AsyncSession,
) -> None:
    """Restrict artist and collector-created threads to their booking counterpart."""
    if effective_user.role in {"assistant", "admin", "founder"}:
        return

    other_ids = set(body.participant_ids) - {effective_user.id}
    if body.is_group or len(other_ids) != 1:
        raise HTTPException(
            status_code=403,
            detail="Artists and collectors can only start one-to-one booking conversations",
        )

    other_id = next(iter(other_ids))
    if effective_user.role == "artist":
        relationship = await db.execute(
            select(Booking.id)
            .where(
                Booking.artist_id == effective_user.id,
                Booking.client_id == other_id,
            )
            .limit(1)
        )
    elif effective_user.role == "collector":
        relationship = await db.execute(
            select(Booking.id)
            .where(
                Booking.client_id == effective_user.id,
                Booking.artist_id == other_id,
            )
            .limit(1)
        )
    else:
        raise HTTPException(status_code=403, detail="Not authorized to start this conversation")

    if relationship.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=403,
            detail="Conversation participant is not linked to this account by a booking",
        )


def _merge_metadata(existing: str | None, updates: dict) -> str:
    """Merge JSON metadata while tolerating legacy non-JSON values."""
    try:
        data = json.loads(existing) if existing else {}
    except json.JSONDecodeError:
        data = {"legacy_metadata": existing}
    if not isinstance(data, dict):
        data = {"legacy_metadata": data}
    data.update(updates)
    return json.dumps(data)


def _is_staff_viewer(user: User) -> bool:
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK["assistant"]


def _message_metadata_for_viewer(message: Message, *, staff: bool) -> str | None:
    """Keep Booky provider operations private while retaining a safe UI state."""
    if staff or not message.metadata_json:
        return message.metadata_json
    try:
        metadata = json.loads(message.metadata_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    if message.message_type != "booky_confirmation" and "notification_delivery" not in metadata:
        return message.metadata_json
    delivery_status = str(metadata.get("delivery_status") or "")
    if delivery_status in {"opened", "clicked"}:
        delivery_status = "delivered"
    elif delivery_status == "local":
        delivery_status = "sent"
    if delivery_status not in {"queued", "sending", "sent", "delivered", "failed"}:
        return None
    if message.message_type == "booky_confirmation":
        return json.dumps({"delivery_status": delivery_status})
    safe_metadata = {"delivery_status": delivery_status}
    for key, value in metadata.items():
        if key not in {"notification_delivery", "delivery_status"}:
            safe_metadata[key] = value
    return json.dumps(safe_metadata)


def _audit_message(conversation_id: UUID, actor_id: UUID, action: str, details: dict | None = None) -> Message:
    now = datetime.now(UTC)
    return Message(
        conversation_id=conversation_id,
        sender_id=actor_id,
        content=f"Courier audit: {action}",
        message_type="audit",
        metadata_json=json.dumps(
            {
                "action": action,
                "actor_id": str(actor_id),
                "occurred_at": now.isoformat(),
                **(details or {}),
            }
        ),
        created_at=now,
        updated_at=now,
    )


async def _build_channel_context(conv_id: UUID, db: AsyncSession) -> dict[str, Any] | None:
    sms_log = await db.scalar(
        select(SmsMessage).where(SmsMessage.conversation_id == conv_id).order_by(desc(SmsMessage.created_at)).limit(1)
    )
    if sms_log:
        is_inbound = sms_log.direction == "inbound"
        business_number = sms_log.to_phone if is_inbound else sms_log.from_phone
        try:
            lane = resolve_sms_lane_by_number(business_number)
        except SmsLaneConfigurationError:
            lane = None
        return {
            "channel": "sms",
            "provider": "twilio",
            "direction": sms_log.direction,
            "lane_id": lane.id if lane else None,
            "lane_label": lane.label if lane else "Unverified",
            "assigned_agent_id": lane.assigned_agent_id if lane else None,
            "assigned_agent_label": lane.assigned_agent_label if lane else None,
            "assignment_status": "assigned" if lane else "unverified",
            "reply_mode": lane.reply_mode if lane else None,
            "voice_status": lane.voice_status if lane else "unverified",
            "business_number_masked": mask_sms_sender(business_number) if lane else None,
            "routing_status": "verified" if lane else "blocked",
            "customer_number": sms_log.from_phone if is_inbound else sms_log.to_phone,
            "status": sms_log.status,
            "last_log_id": str(sms_log.id),
            "last_seen_at": sms_log.created_at.isoformat() if sms_log.created_at else None,
        }

    msg = await db.scalar(
        select(Message)
        .where(Message.conversation_id == conv_id, Message.message_type.in_(("sms", "whatsapp")))
        .order_by(desc(Message.created_at))
        .limit(1)
    )
    if not msg or not msg.metadata_json:
        return None
    try:
        meta = json.loads(msg.metadata_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict):
        return None
    channel = meta.get("channel") or msg.message_type
    if channel not in {"sms", "whatsapp"}:
        return None
    direction = meta.get("direction")
    to_phone = meta.get("to")
    from_phone = meta.get("from")
    lane_id = str(meta.get("lane_id") or "").strip().lower()
    try:
        lane = resolve_sms_lane_by_id(lane_id) if lane_id else None
    except SmsLaneConfigurationError:
        lane = None
    return {
        "channel": channel,
        "provider": meta.get("provider"),
        "direction": direction,
        "lane_id": lane.id if lane else None,
        "lane_label": lane.label if lane else "Unverified",
        "assigned_agent_id": lane.assigned_agent_id if lane else meta.get("assigned_agent_id"),
        "assigned_agent_label": lane.assigned_agent_label if lane else meta.get("assigned_agent_label"),
        "assignment_status": "assigned" if lane else meta.get("assignment_status", "unverified"),
        "reply_mode": lane.reply_mode if lane else meta.get("reply_mode"),
        "voice_status": lane.voice_status if lane else meta.get("voice_status", "unverified"),
        "business_number_masked": mask_sms_sender(lane.from_number) if lane else None,
        "routing_status": "verified" if lane else "blocked",
        "customer_number": from_phone if direction == "inbound" else to_phone,
        "status": meta.get("delivery_status"),
        "last_log_id": meta.get("sms_log_id"),
        "last_seen_at": meta.get("received_at") or meta.get("sent_at"),
    }


async def _resolve_conversation_sms_lane_id(conv_id: UUID, db: AsyncSession) -> str:
    """Inherit a verified SMS lane, or use Bella only for a generic thread."""
    sms_log = await db.scalar(
        select(SmsMessage).where(SmsMessage.conversation_id == conv_id).order_by(desc(SmsMessage.created_at)).limit(1)
    )
    if sms_log:
        business_number = sms_log.to_phone if sms_log.direction == "inbound" else sms_log.from_phone
        try:
            return resolve_sms_lane_by_number(business_number).id
        except SmsLaneConfigurationError:
            return "blocked"

    message = await db.scalar(
        select(Message)
        .where(Message.conversation_id == conv_id, Message.message_type == "sms")
        .order_by(desc(Message.created_at))
        .limit(1)
    )
    if not message:
        return "bella"
    try:
        metadata = json.loads(message.metadata_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return "blocked"
    lane_id = str(metadata.get("lane_id") or "").strip().lower() if isinstance(metadata, dict) else ""
    if not lane_id:
        return "blocked"
    try:
        return resolve_sms_lane_by_id(lane_id).id
    except SmsLaneConfigurationError:
        return "blocked"


async def _build_conversation_response(
    conv,
    db: AsyncSession,
    viewer_ids: list[UUID] | None = None,
    *,
    viewer_is_staff: bool = False,
) -> dict:
    """Enrich a Conversation row with participant_count and last_message."""
    # Count participants
    count_result = await db.execute(select(func.count()).where(ConversationParticipant.conversation_id == conv.id))
    participant_count = count_result.scalar()

    # Last message
    msg_result = await db.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(desc(Message.created_at)).limit(1)
    )
    last_msg = msg_result.scalar_one_or_none()

    last_message = None
    if last_msg:
        # Get sender name
        sender_result = await db.execute(select(User.name).where(User.id == last_msg.sender_id))
        sender_name = sender_result.scalar_one_or_none()
        last_message = MessageResponse(
            id=last_msg.id,
            conversation_id=last_msg.conversation_id,
            sender_id=last_msg.sender_id,
            sender_name=sender_name,
            content=last_msg.content,
            message_type=last_msg.message_type,
            metadata_json=_message_metadata_for_viewer(last_msg, staff=viewer_is_staff),
            created_at=last_msg.created_at,
        )

    last_read_at = None
    unread_count = 0
    if viewer_ids:
        participant_result = await db.execute(
            select(ConversationParticipant)
            .where(
                and_(
                    ConversationParticipant.conversation_id == conv.id,
                    ConversationParticipant.user_id.in_(viewer_ids),
                )
            )
            .order_by(desc(ConversationParticipant.last_read_at))
            .limit(1)
        )
        participant = participant_result.scalar_one_or_none()
        if participant:
            last_read_at = participant.last_read_at
            unread_filters = [
                Message.conversation_id == conv.id,
                or_(
                    Message.sender_id.notin_(viewer_ids),
                    Message.message_type.in_(("email", "social")),
                ),
            ]
            if last_read_at:
                unread_filters.append(Message.created_at > last_read_at)
            unread_result = await db.execute(select(func.count()).where(and_(*unread_filters)))
            unread_count = unread_result.scalar() or 0

    booking_result = await db.execute(select(Booking).where(Booking.conversation_id == conv.id).limit(1))
    booking = booking_result.scalar_one_or_none()
    booking_context = None
    display_name = conv.name
    if booking:
        artist_result = await db.execute(select(User.name).where(User.id == booking.artist_id))
        artist_name = artist_result.scalar_one_or_none()
        display_name = booking.guest_name or (conv.name.removeprefix("Booking: ").strip() if conv.name else None)
        reference_urls: list[str] = []
        if booking.reference_urls:
            try:
                parsed_reference_urls = json.loads(booking.reference_urls)
                if isinstance(parsed_reference_urls, list):
                    reference_urls = [str(url) for url in parsed_reference_urls if url]
            except (TypeError, ValueError):
                reference_urls = [booking.reference_urls]
        booking_context = {
            "id": str(booking.id),
            "reference_code": booking.reference_code,
            "status": booking.status,
            "client_name": booking.guest_name,
            "client_email": booking.guest_email,
            "client_phone": booking.guest_phone,
            "artist_name": artist_name,
            "description": booking.description,
            "reference_urls": reference_urls,
            "booking_kind": booking.booking_kind or "tattoo_session",
            "consultation_method": booking.consultation_method,
            "placement": booking.placement,
            "style_preference": booking.style_preference,
            "preferred_date": booking.preferred_date.isoformat() if booking.preferred_date else None,
            "preferred_time": booking.preferred_time,
            "preferred_slots": (
                booking.preferred_slots_json if isinstance(booking.preferred_slots_json, list) else None
            ),
            "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
            "scheduled_time": booking.scheduled_time,
            "booking_url": ROUTES.BOOKING_REF.format(ref=booking.reference_code) if booking.reference_code else None,
        }
    elif conv.name and conv.name.lower().startswith("booking:"):
        display_name = conv.name.split(":", 1)[1].strip() or conv.name

    channel_context = await _build_channel_context(conv.id, db)

    return ConversationResponse(
        id=conv.id,
        name=display_name,
        is_group=conv.is_group,
        created_by=conv.created_by,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        participant_count=participant_count,
        last_message=last_message,
        unread_count=unread_count,
        is_unread=unread_count > 0,
        last_read_at=last_read_at,
        booking_context=booking_context,
        channel_context=channel_context,
    )


# Endpoints


@router.post("/", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    body: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a new conversation. Creator is auto-added as participant."""
    effective_user = await _resolve_effective_user(current_user, db)
    effective_user_id = effective_user.id
    visible_ids = _visible_user_ids(current_user)
    await _require_conversation_creation_access(body, effective_user, db)

    # Validate all participant IDs exist
    for pid in body.participant_ids:
        result = await db.execute(select(User.id).where(User.id == pid))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=f"User {pid} not found")

    # For 1:1, check if conversation already exists between these two users
    if not body.is_group and len(body.participant_ids) == 1:
        other_id = body.participant_ids[0]
        # Subquery: conversations where the effective sender is a participant
        my_convs = (
            select(ConversationParticipant.conversation_id).where(ConversationParticipant.user_id == effective_user_id)
        ).subquery()
        # Find non-group conversations that BOTH users are in
        existing = await db.execute(
            select(Conversation)
            .join(ConversationParticipant)
            .where(
                and_(
                    Conversation.is_group.is_(False),
                    Conversation.id.in_(select(my_convs.c.conversation_id)),
                    ConversationParticipant.user_id == other_id,
                )
            )
        )
        existing_conv = existing.scalar_one_or_none()
        if existing_conv:
            return await _build_conversation_response(
                existing_conv,
                db,
                visible_ids,
                viewer_is_staff=_is_staff_viewer(current_user),
            )

    # Create conversation
    conv = Conversation(
        name=body.name,
        is_group=body.is_group,
        created_by=effective_user_id,
    )
    db.add(conv)
    await db.flush()

    # Add effective creator as participant. A proxy operator acting as another
    # user creates the thread as that user, not as a thread of their own.
    all_participant_ids = set(body.participant_ids) | {effective_user_id}
    for uid in all_participant_ids:
        role = "owner" if uid == effective_user_id else "member"
        db.add(ConversationParticipant(conversation_id=conv.id, user_id=uid, role=role))

    await db.flush()
    return await _build_conversation_response(
        conv,
        db,
        visible_ids,
        viewer_is_staff=_is_staff_viewer(current_user),
    )


@router.get("/", response_model=ConversationListResponse)
async def list_my_conversations(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all conversations the current user belongs to.

    If the user has proxy_for set, they also see their proxy target's conversations.
    """
    # Collect all user IDs whose conversations should be visible
    visible_ids = _visible_user_ids(current_user)

    subq = (
        select(ConversationParticipant.conversation_id)
        .where(ConversationParticipant.user_id.in_(visible_ids))
        .distinct()
        .subquery()
    )

    # Count total
    count_result = await db.execute(select(func.count()).select_from(subq))
    total = count_result.scalar()

    # Fetch conversations ordered by last activity
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id.in_(select(subq)))
        .order_by(desc(Conversation.updated_at))
        .offset(skip)
        .limit(limit)
    )
    convs = result.scalars().all()

    conversations = []
    for conv in convs:
        conversations.append(
            await _build_conversation_response(
                conv,
                db,
                visible_ids,
                viewer_is_staff=_is_staff_viewer(current_user),
            )
        )

    return ConversationListResponse(conversations=conversations, total=total)


@router.get("/channels/status")
async def courier_channel_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return Ryozan Nexus Courier connector status for the current user."""
    settings = get_settings()
    effective_user = await _resolve_effective_user(current_user, db)
    email_result = await db.execute(select(EmailRoute).where(EmailRoute.user_id == effective_user.id))
    email_route = email_result.scalar_one_or_none()

    return build_channel_status(
        settings,
        CourierRouteContext(
            email_route=f"{email_route.handle}@ryozan.app" if email_route else None,
            email_forward_to=email_route.forward_to if email_route else None,
            email_verified=bool(email_route and email_route.verified),
            instagram_url=effective_user.instagram_url,
            facebook_url=effective_user.facebook_url,
        ),
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single conversation's details."""
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))

    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    visible_ids = _visible_user_ids(current_user)
    return await _build_conversation_response(
        conv,
        db,
        visible_ids,
        viewer_is_staff=_is_staff_viewer(current_user),
    )


@router.get("/{conversation_id}/participants", response_model=list[ParticipantResponse])
async def list_participants(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all participants in a conversation."""
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))

    result = await db.execute(
        select(ConversationParticipant, User.name)
        .join(User, ConversationParticipant.user_id == User.id)
        .where(ConversationParticipant.conversation_id == conversation_id)
    )
    rows = result.all()

    return [
        ParticipantResponse(
            id=p.id,
            user_id=p.user_id,
            user_name=name,
            role=p.role,
            joined_at=p.joined_at,
            last_read_at=p.last_read_at,
        )
        for p, name in rows
    ]


# Notifications


COURIER_NOTIFICATION_MAX_ATTEMPTS = 5
COURIER_NOTIFICATION_STALE_MINUTES = 10


def project_notification_delivery(metadata: dict[str, Any]) -> str:
    delivery = metadata.get("notification_delivery")
    intents = delivery.get("intents", []) if isinstance(delivery, dict) else []
    statuses = {str(intent.get("status") or "queued") for intent in intents if isinstance(intent, dict)}
    if not statuses:
        return str(metadata.get("delivery_status") or "queued")
    if statuses & {"failed", "undelivered", "bounced", "complained"}:
        return "failed"
    if "queued" in statuses:
        return "queued"
    if "sending" in statuses:
        return "sending"
    if statuses and statuses <= {"delivered", "opened", "clicked"}:
        return "delivered"
    if statuses <= {"sent", "delivered", "opened", "clicked", "dev_skipped"}:
        return "sent"
    return "queued"


def _notification_timestamp(value: Any, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return fallback


def _notification_metadata(value: str | None) -> dict[str, Any] | None:
    try:
        metadata = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    return metadata if isinstance(metadata, dict) else None


def _notification_has_eligible_intent(
    metadata: dict[str, Any],
    *,
    created_at: datetime,
    now: datetime,
    stale_before: datetime,
) -> bool:
    delivery = metadata.get("notification_delivery")
    if not isinstance(delivery, dict):
        return False
    for intent in delivery.get("intents", []):
        if not isinstance(intent, dict):
            continue
        status = str(intent.get("status") or "queued")
        attempts = int(intent.get("attempts") or 0)
        updated_at = _notification_timestamp(intent.get("updated_at"), created_at)
        next_attempt_at = _notification_timestamp(intent.get("next_attempt_at"), now)
        if status == "queued":
            return True
        if status == "failed" and attempts < COURIER_NOTIFICATION_MAX_ATTEMPTS and next_attempt_at <= now:
            return True
        if status == "sending" and attempts < COURIER_NOTIFICATION_MAX_ATTEMPTS and updated_at <= stale_before:
            return True
    return False


def _courier_notification_email_html(message: Message, delivery: dict[str, Any]) -> str:
    from html import escape as html_escape

    content = message.content if message.message_type in {"text", "email", "sms"} else ""
    if content:
        truncated = content[:800]
        body_html = html_escape(truncated).replace("\n", "<br>")
        if len(content) > 800:
            body_html += "&hellip;<br><em>(message truncated - tap Open Courier for full thread)</em>"
    elif message.message_type == "slot_proposal":
        body_html = "<em>Sent appointment slot options. Tap Open Courier to choose a time.</em>"
    elif message.message_type == "quote_proposal":
        body_html = "<em>Sent a quote. Tap Open Courier to review.</em>"
    else:
        body_html = "<em>Sent a message. Tap Open Courier to read.</em>"
    safe_sender_name = html_escape(str(delivery.get("sender_name") or "Someone"))
    return f"""<html>
<body style="margin:0;padding:0;background:#121312;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto;">
    <tr>
      <td style="padding:32px 24px;">
        <p style="color:#b9ad96;font-size:13px;margin:0 0 8px;text-align:center;letter-spacing:1px;">RYOZAN STUDIO</p>
        <h2 style="color:#faae15;margin:0 0 24px;font-size:18px;text-align:center;">Message from {safe_sender_name}</h2>
        <div style="background:#1d1c19;border-left:4px solid #faae15;padding:18px 22px;border-radius:4px;margin-bottom:24px;">
          <p style="color:#e8e0d0;font-size:15px;line-height:1.55;margin:0;">
            {body_html}
          </p>
        </div>
        <p style="color:#b9ad96;font-size:13px;line-height:1.5;margin:0 0 24px;text-align:center;">
          <strong style="color:#e8e0d0;">Reply to this email</strong> to respond, or open Courier for the full thread.
        </p>
        <p style="text-align:center;margin:0;">
          <a href="{get_settings().FRONTEND_URL}{ROUTES.COURIER_CONVO.format(id=message.conversation_id)}"
             style="display:inline-block;padding:12px 28px;background:#faae15;
                    color:#121312;text-decoration:none;font-weight:bold;
                    border-radius:6px;font-size:14px;">
            Open Courier
          </a>
        </p>
      </td>
    </tr>
  </table>
</body>
</html>"""


COLLECTOR_SMS_ALERT_TEMPLATE = "new_message_notification"
STAFF_SMS_ALERT_TEMPLATE = "staff_message_alert"


async def _queue_conversation_notifications(
    db: AsyncSession,
    *,
    message: Message,
    sender: User,
    conversation: Conversation,
    recipient_ids: list[UUID],
    allow_sms_channel_message: bool = False,
) -> None:
    """Queue email and SMS alerts for a Courier message.

    ``allow_sms_channel_message`` lets an inbound collector SMS alert staff by
    text. It never texts a non-staff recipient, so an SMS-origin message cannot
    echo back out to another collector.
    """
    if not recipient_ids:
        return
    recipients = list((await db.execute(select(User).where(User.id.in_(recipient_ids)))).scalars().all())
    sms_lane_id: str | None = None
    intents: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    sms_origin = allow_sms_channel_message and message.message_type == "sms"
    staff_rank = ROLE_RANK.get("assistant", 1)
    for recipient in recipients:
        content_can_sms = bool(message.content) and (
            message.message_type in {"text", "email"} or (sms_origin and ROLE_RANK.get(recipient.role, 0) >= staff_rank)
        )
        email_enabled = await should_notify(db, recipient.id, "email", "messages")
        if recipient.email and email_enabled:
            intents.append(
                {
                    "id": f"email:{recipient.id}",
                    "channel": "email",
                    "recipient_user_id": str(recipient.id),
                    "to": recipient.email,
                    "status": "queued",
                    "attempts": 0,
                    "next_attempt_at": now,
                }
            )
        if recipient.phone and recipient.sms_opt_in and recipient.sms_opt_out_at is None and content_can_sms:
            if sms_lane_id is None:
                sms_lane_id = (
                    await _resolve_conversation_sms_lane_id(conversation.id, db)
                    if str(conversation.name or "").lower().startswith("sms")
                    else "bella"
                )
            try:
                sms_lane = resolve_sms_lane_by_id(sms_lane_id)
            except SmsLaneConfigurationError:
                sms_lane = None
            intents.append(
                {
                    "id": f"sms:{recipient.id}",
                    "channel": "sms",
                    "recipient_user_id": str(recipient.id),
                    "to": recipient.phone,
                    "lane_id": sms_lane_id,
                    # Staff get a Courier link, not "reply here": a staff text to
                    # the shop line cannot be matched back to this thread.
                    "template": (
                        STAFF_SMS_ALERT_TEMPLATE
                        if ROLE_RANK.get(recipient.role, 0) >= staff_rank
                        else COLLECTOR_SMS_ALERT_TEMPLATE
                    ),
                    "assigned_agent_id": sms_lane.assigned_agent_id if sms_lane else None,
                    "assigned_agent_label": sms_lane.assigned_agent_label if sms_lane else None,
                    "assignment_status": "assigned" if sms_lane else "unverified",
                    "reply_mode": sms_lane.reply_mode if sms_lane else None,
                    "voice_status": sms_lane.voice_status if sms_lane else "unverified",
                    "status": "queued",
                    "attempts": 0,
                    "next_attempt_at": now,
                }
            )
    if not intents:
        return
    try:
        metadata = json.loads(message.metadata_json or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {"legacy_metadata": message.metadata_json}
    if not isinstance(metadata, dict):
        metadata = {"legacy_metadata": metadata}
    metadata["notification_delivery"] = {
        "sender_name": sender.name or "Someone",
        "conversation_name": conversation.name,
        "intents": intents,
        "updated_at": now,
    }
    metadata["delivery_status"] = "queued"
    message.metadata_json = json.dumps(metadata)


async def process_courier_notification_outbox_once(limit: int = 20, *, message_id: UUID | None = None) -> int:
    """Deliver durable Courier email and SMS intents and persist retry state."""
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=COURIER_NOTIFICATION_STALE_MINUTES)
    async with async_session() as session:
        base_query = select(Message).where(Message.metadata_json.like('%"notification_delivery"%'))
        if message_id is not None:
            base_query = base_query.where(Message.id == message_id)
        else:
            # Keep terminal rows out of the candidate set before LIMIT. The
            # JSON is stored as text, so PostgreSQL's regex operator is the
            # narrowest available indexed-owner-compatible prefilter.
            base_query = base_query.where(Message.metadata_json.op("~")(r'"status"\s*:\s*"(queued|sending|failed)"'))

        messages: list[Message] = []
        cursor_created: datetime | None = None
        cursor_id: UUID | None = None
        page_size = max(limit * 2, 50)
        while len(messages) < limit:
            query = base_query
            if cursor_created is not None and cursor_id is not None:
                query = query.where(
                    or_(
                        Message.created_at > cursor_created,
                        and_(Message.created_at == cursor_created, Message.id > cursor_id),
                    )
                )
            page = list(
                (
                    await session.execute(
                        query.order_by(Message.created_at, Message.id)
                        .with_for_update(skip_locked=True)
                        .limit(1 if message_id is not None else page_size)
                    )
                )
                .scalars()
                .all()
            )
            if not page:
                break
            for message in page:
                metadata = _notification_metadata(message.metadata_json)
                if metadata and _notification_has_eligible_intent(
                    metadata,
                    created_at=message.created_at,
                    now=now,
                    stale_before=stale_before,
                ):
                    messages.append(message)
                    if len(messages) >= limit:
                        break
            cursor_created = page[-1].created_at
            cursor_id = page[-1].id
            if message_id is not None or len(page) < page_size:
                break
        claimed: list[tuple[UUID, str]] = []
        for message in messages:
            metadata = _notification_metadata(message.metadata_json)
            if metadata is None:
                continue
            delivery = metadata.get("notification_delivery")
            if not isinstance(delivery, dict):
                continue
            for intent in delivery.get("intents", []):
                if not isinstance(intent, dict):
                    continue
                status = str(intent.get("status") or "queued")
                attempts = int(intent.get("attempts") or 0)
                updated_at = _notification_timestamp(intent.get("updated_at"), message.created_at)
                next_attempt_at = _notification_timestamp(intent.get("next_attempt_at"), now)
                eligible = status == "queued" or (
                    status == "failed" and attempts < COURIER_NOTIFICATION_MAX_ATTEMPTS and next_attempt_at <= now
                )
                if status == "sending" and updated_at <= stale_before:
                    eligible = True
                if not eligible:
                    continue
                intent["status"] = "sending"
                intent["attempts"] = attempts + 1
                intent["updated_at"] = now.isoformat()
                intent["last_error"] = None
                claimed.append((message.id, str(intent["id"])))
            delivery["updated_at"] = now.isoformat()
            metadata["delivery_status"] = project_notification_delivery(metadata)
            message.metadata_json = json.dumps(metadata)
        await session.commit()

        delivered = 0
        for claimed_message_id, intent_id in claimed:
            message = await session.get(Message, claimed_message_id, populate_existing=True)
            if not message:
                continue
            metadata = _notification_metadata(message.metadata_json)
            if metadata is None:
                continue
            delivery = metadata.get("notification_delivery") or {}
            intent = next((item for item in delivery.get("intents", []) if item.get("id") == intent_id), None)
            if not intent or intent.get("status") != "sending":
                continue
            error: str | None = None
            provider_status: str | None = None
            try:
                if intent["channel"] == "email":
                    conversation_name = delivery.get("conversation_name")
                    sender_name = delivery.get("sender_name") or "Someone"
                    subject = f"Re: {conversation_name}" if conversation_name else f"Message from {sender_name}"
                    sent = await send_email(
                        to=str(intent["to"]),
                        subject=subject,
                        html_body=_courier_notification_email_html(message, delivery),
                        from_alias=FROM_SHOP,
                        reply_to=conversation_reply_to(message.conversation_id),
                        tags=["courier-notification"],
                        courier_message_id=str(message.id),
                        courier_attempt=int(intent["attempts"]),
                    )
                    provider_status = "sent" if sent else "failed"
                    error = None if sent else "send_email returned False"
                else:
                    preview = message.content.strip().replace("\n", " ")
                    if len(preview) > 80:
                        preview = preview[:77] + "..."
                    template_slug = str(intent.get("template") or COLLECTOR_SMS_ALERT_TEMPLATE)
                    sms_log = await send_from_template(
                        db=session,
                        slug=template_slug,
                        to=str(intent["to"]),
                        variables={
                            "sender_name": str(delivery.get("sender_name") or "Someone"),
                            "preview": preview,
                            "courier_url": (
                                f"{get_settings().FRONTEND_URL}"
                                f"{ROUTES.COURIER_CONVO.format(id=message.conversation_id)}"
                            ),
                        },
                        conversation_id=message.conversation_id,
                        courier_message_id=message.id,
                        user_id=UUID(str(intent["recipient_user_id"])),
                        is_transactional=True,
                        lane_id=str(intent.get("lane_id") or "bella"),
                    )
                    if sms_log is None:
                        provider_status = "failed"
                        error = f"{template_slug} template unavailable"
                    else:
                        intent["sms_log_id"] = str(sms_log.id)
                        intent["provider_status"] = sms_log.status
                        provider_status = project_sms_delivery_status(sms_log.status)
                        error = sms_log.error_message
            except Exception as exc:  # noqa: BLE001 - durable intent records provider failure
                logger.exception("Courier notification attempt failed for message %s", message.id)
                provider_status = "failed"
                error = str(exc)[:1000]

            finished_at = datetime.now(UTC)
            intent["status"] = provider_status
            intent["updated_at"] = finished_at.isoformat()
            intent["last_error"] = error
            if provider_status == "failed":
                intent["next_attempt_at"] = (
                    finished_at + timedelta(minutes=min(2 ** int(intent["attempts"]), 60))
                ).isoformat()
            delivery["updated_at"] = finished_at.isoformat()
            metadata["delivery_status"] = project_notification_delivery(metadata)
            message.metadata_json = json.dumps(metadata)
            await session.commit()
            if provider_status == "sent":
                delivered += 1
        return delivered


async def _gala_auto_reply(
    conversation_id: UUID,
    artist_id: UUID,
    artist_name: str,
    client_name: str,
    mode: str,
) -> None:
    """
    Generate and save a Gala auto-reply message in the conversation.

    Runs as a fire-and-forget background task after a client sends a message
    to an artist who has gala_auto_reply enabled.

    mode: "transparent" (Gala identifies as AI) or "voice" (mimics artist tone)
    """
    try:
        settings = get_settings()
        # This path answers inbound CLIENT messages and has no requesting user, so
        # a role check is meaningless here: it must be off entirely during limited
        # Authenticated Portal AI and unattended client replies are separate
        # product boundaries. Never infer permission to message a client from
        # an account's ability to use a role-scoped agent surface.
        if not settings.GALA_AUTO_REPLY_ENABLED:
            logger.info("Gala auto-reply skipped: GALA_AUTO_REPLY_ENABLED is off")
            return
        if not settings.ARK_MODEL_GATEWAY_KEY:
            logger.warning("Gala auto-reply skipped: ARK_MODEL_GATEWAY_KEY not set")
            return

        async with async_session() as db:
            # Fetch recent messages for context (last 20)
            msg_result = await db.execute(
                select(Message, User.name)
                .join(User, Message.sender_id == User.id)
                .where(Message.conversation_id == conversation_id)
                .order_by(desc(Message.created_at))
                .limit(20)
            )
            rows = msg_result.all()
            if not rows:
                return

            # Build conversation transcript
            transcript_lines = []
            for msg, sender_name in reversed(rows):
                role_label = artist_name if msg.sender_id == artist_id else sender_name
                transcript_lines.append(f"{role_label}: {msg.content}")
            transcript = "\n".join(transcript_lines)

            # Build system prompt based on mode
            if mode == "transparent":
                system_prompt = (
                    "You are responding AS Gala, the AI assistant for Ryozan Studio "
                    "Make it clear you are an AI helping on behalf of the artist. "
                    f"Start with something like 'Hey! Gala here, {artist_name}'s assistant.' "
                    "Be warm, helpful, and concise. Keep replies 2-4 sentences max. "
                    "Never use emojis. Sound like a knowledgeable tattoo shop assistant, not corporate. "
                    "If you can answer the question (scheduling, pricing, aftercare, general info), do so. "
                    "If not, let them know the artist will follow up personally."
                )
            else:
                # "voice" mode: use the standard courier system prompt (mimics artist tone)
                system_prompt = GALA_COURIER_SYSTEM

            prompt = (
                f"You are replying on behalf of {artist_name} to {client_name}.\n\n"
                f"Conversation so far:\n{transcript}\n\n"
                f"Write the next reply."
            )

            from app.services.ai_routing import call_ark_gateway_chat_completion
            from app.services.api_budget import log_usage

            response = await call_ark_gateway_chat_completion(
                settings=settings,
                primary_model=settings.AI_PREMIUM_PRIMARY_MODEL,
                fallback_models=[settings.AI_PREMIUM_FALLBACK_MODEL, settings.AI_PAID_PRIMARY_MODEL],
                max_tokens=300,
                tools=[],
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )

            choice = ((response.get("choices") or [{}])[0]).get("message") or {}
            reply_text = _clean_ai_draft(choice.get("content") or "")
            if not reply_text:
                logger.warning("Gala auto-reply returned empty content for conversation %s", conversation_id)
                return

            usage = response.get("usage") or {}
            await log_usage(
                db,
                "conversations/auto-reply",
                int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                artist_id,
            )

            # Save as a message from the artist (so it appears as the artist in the thread)
            metadata = json.dumps({"gala_generated": True, "mode": mode})
            auto_msg = Message(
                conversation_id=conversation_id,
                sender_id=artist_id,
                content=reply_text,
                message_type="text",
                metadata_json=metadata,
            )
            db.add(auto_msg)

            # Update conversation timestamp
            conv_result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
            conv = conv_result.scalar_one_or_none()
            if conv:
                from datetime import UTC, datetime

                conv.updated_at = datetime.now(UTC)

            await db.commit()
            logger.info(
                "Gala auto-reply sent in conversation %s (mode=%s, artist=%s)",
                conversation_id,
                mode,
                artist_name,
            )
    except Exception:
        logger.exception("Gala auto-reply failed for conversation %s", conversation_id)


# Messages


@router.post("/{conversation_id}/messages", response_model=MessageResponse, status_code=201)
async def send_message(
    conversation_id: UUID,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send a message to a conversation.

    Plain-text messages pass through unchanged. Artifact message types
    (quote_proposal, slot_proposal, etc.) have their payload validated
    against the registered schema in app.services.artifacts and get
    an initial state stamped on them before insert.

    If current_user has proxy_for set, the message is attributed to the
    proxy target (the message appears to come from that user).
    """
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))

    # Resolve effective sender - proxy users send as their delegator
    effective_sender = await _resolve_effective_user(current_user, db)
    effective_sender_id = effective_sender.id

    # Validate + normalize artifact payload (no-op for plain text)
    artifact_payload: dict | None = None
    artifact_state: dict | None = None
    if artifacts.is_registered(body.message_type):
        try:
            artifact_payload = artifacts.validate_payload(body.message_type, body.artifact_payload)
        except artifacts.ArtifactError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        artifact_state = artifacts.initial_state(body.message_type)
        # For plain text the initial state is {} and the payload is {},
        # which is identical to leaving both null. Keep it null so the
        # GET response doesn't clutter every text bubble with empty dicts.
        if body.message_type == "text" and not artifact_payload and not artifact_state:
            artifact_payload = None
            artifact_state = None
    elif body.artifact_payload is not None:
        raise HTTPException(
            status_code=422,
            detail=f"unknown message_type={body.message_type!r}",
        )

    msg = Message(
        conversation_id=conversation_id,
        sender_id=effective_sender_id,
        content=body.content,
        message_type=body.message_type,
        metadata_json=body.metadata_json,
        artifact_payload=artifact_payload,
        artifact_state=artifact_state,
    )
    db.add(msg)

    # Update conversation timestamp
    conv_result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = conv_result.scalar_one()
    from datetime import UTC, datetime

    conv.updated_at = datetime.now(UTC)

    await db.flush()

    # In-app notification for other participants (exclude effective sender)
    part_result = await db.execute(
        select(ConversationParticipant.user_id).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id != effective_sender_id,
        )
    )
    recipient_ids = [row[0] for row in part_result.all()]
    if effective_sender.role == "collector" and recipient_ids:
        staff_recipient = await db.scalar(
            select(User.id)
            .where(
                User.id.in_(recipient_ids),
                User.role.in_(["assistant", "artist", "admin", "founder"]),
            )
            .limit(1)
        )
        if staff_recipient:
            await reward_user_by_id(
                db,
                effective_sender.id,
                "conversation.artist_interaction",
                f"{conversation_id}:{datetime.now(UTC).date().isoformat()}",
                metadata={"conversation_id": str(conversation_id)},
            )
    sender_first = (effective_sender.name or "Someone").split()[0]
    preview = body.content[:80] + ("..." if len(body.content) > 80 else "") if body.content else "Sent a file"
    conversation_action_url = ROUTES.COURIER_CONVO.format(id=conversation_id)
    for rid in recipient_ids:
        db.add(
            NotificationModel(
                user_id=rid,
                title=f"Message from {sender_first}",
                body=preview,
                notification_type="message",
                action_url=conversation_action_url,
            )
        )
        if await should_notify(db, rid, "push", "messages"):
            await send_push_notification(
                db,
                rid,
                title=f"Message from {sender_first}",
                body=preview,
                url=conversation_action_url,
            )
    await db.flush()

    # Persist external notification obligations in the same transaction as the
    # Courier message. The process worker claims them only after commit.
    await _queue_conversation_notifications(
        db,
        message=msg,
        sender=effective_sender,
        conversation=conv,
        recipient_ids=recipient_ids,
    )
    await db.flush()

    # Gala auto-reply: if sender is a client messaging an artist with auto-reply on
    sender_is_artist = ROLE_RANK.get(effective_sender.role, 0) >= ROLE_RANK["artist"]
    if not sender_is_artist and not conv.is_group:
        for rid in recipient_ids:
            artist_result = await db.execute(
                select(User).where(
                    User.id == rid,
                    User.gala_auto_reply.isnot(None),
                    User.role.in_(["artist", "admin", "founder"]),
                )
            )
            artist = artist_result.scalar_one_or_none()
            if artist and artist.gala_auto_reply in ("transparent", "voice"):
                asyncio.create_task(
                    _gala_auto_reply(
                        conversation_id=conversation_id,
                        artist_id=artist.id,
                        artist_name=artist.name,
                        client_name=effective_sender.name,
                        mode=artist.gala_auto_reply,
                    )
                )

    return MessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        sender_id=msg.sender_id,
        sender_name=effective_sender.name,
        content=msg.content,
        message_type=msg.message_type,
        metadata_json=_message_metadata_for_viewer(msg, staff=_is_staff_viewer(current_user)),
        artifact_payload=msg.artifact_payload,
        artifact_state=msg.artifact_state,
        created_at=msg.created_at,
        updated_at=msg.updated_at,
    )


async def _resolve_channel_recipient(
    db: AsyncSession,
    conversation_id: UUID,
    visible_ids: list[UUID],
    explicit_to: str | None,
) -> tuple[str, User | None]:
    result = await db.execute(
        select(User)
        .join(ConversationParticipant, ConversationParticipant.user_id == User.id)
        .where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id.notin_(visible_ids),
            User.phone.is_not(None),
        )
    )
    recipients = list(result.scalars().all())
    if explicit_to:
        e164 = normalise_phone(explicit_to)
        if not e164:
            raise HTTPException(status_code=400, detail="Invalid recipient phone number")
        recipient = next((user for user in recipients if normalise_phone(user.phone) == e164), None)
        if not recipient:
            raise HTTPException(status_code=400, detail="Recipient must be another participant in this Courier thread")
        return e164, recipient

    recipient = recipients[0] if recipients else None
    if not recipient or not normalise_phone(recipient.phone):
        raise HTTPException(status_code=400, detail="No phone-capable recipient found for this Courier thread")
    return normalise_phone(recipient.phone) or recipient.phone, recipient


@router.post("/{conversation_id}/channel-send", response_model=MessageResponse, status_code=201)
async def send_channel_message(
    conversation_id: UUID,
    body: ChannelSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a Courier message through an explicit external channel."""
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=403, detail="Staff role or higher required for external channel sends")

    effective_sender = await _resolve_effective_user(current_user, db)
    visible_ids = _visible_user_ids(current_user)
    channel = body.channel.lower()

    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    metadata: dict[str, Any] = {
        "channel": channel,
        "direction": "outbound",
        "sent_by": str(effective_sender.id),
        "sent_at": datetime.now(UTC).isoformat(),
        "delivery_status": "queued" if channel in {"sms", "whatsapp"} else "local",
    }
    msg = Message(
        conversation_id=conversation_id,
        sender_id=effective_sender.id,
        content=body.content,
        message_type=channel,
        metadata_json=json.dumps(metadata),
    )
    db.add(msg)
    await db.flush()
    if channel == "sms":
        to_phone, recipient = await _resolve_channel_recipient(db, conversation_id, visible_ids, body.to)
        lane_id = await _resolve_conversation_sms_lane_id(conversation_id, db)
        try:
            sms_lane = resolve_sms_lane_by_id(lane_id)
        except SmsLaneConfigurationError:
            sms_lane = None
        sms_log = await send_sms(
            db=db,
            to=to_phone,
            body=body.content,
            conversation_id=conversation_id,
            courier_message_id=msg.id,
            user_id=recipient.id if recipient else None,
            sender_id=effective_sender.id,
            is_transactional=body.is_transactional,
            lane_id=lane_id,
        )
        metadata.update(
            {
                "provider": "twilio",
                "lane_id": lane_id,
                "assigned_agent_id": sms_lane.assigned_agent_id if sms_lane else None,
                "assigned_agent_label": sms_lane.assigned_agent_label if sms_lane else None,
                "assignment_status": "assigned" if sms_lane else "unverified",
                "reply_mode": sms_lane.reply_mode if sms_lane else None,
                "voice_status": sms_lane.voice_status if sms_lane else "unverified",
                "to": to_phone,
                "sms_log_id": str(sms_log.id),
                "twilio_sid": sms_log.twilio_sid,
                "delivery_status": sms_log.status,
                "error_code": sms_log.error_code,
                "error_message": sms_log.error_message,
            }
        )
    elif channel == "whatsapp":
        to_phone, recipient = await _resolve_channel_recipient(db, conversation_id, visible_ids, body.to)
        whatsapp_log = await send_twilio_whatsapp_text(
            db=db,
            to=to_phone,
            body=body.content,
            conversation_id=conversation_id,
            courier_message_id=msg.id,
            user_id=recipient.id if recipient else None,
            sender_id=effective_sender.id,
        )
        metadata.update(
            {
                "provider": "twilio_whatsapp",
                "to": to_phone,
                "recipient_user_id": str(recipient.id) if recipient else None,
                "sms_log_id": str(whatsapp_log.id),
                "twilio_sid": whatsapp_log.twilio_sid,
                "delivery_status": whatsapp_log.status,
                "error_code": whatsapp_log.error_code,
                "error_message": whatsapp_log.error_message,
            }
        )
    else:
        metadata.update({"provider": "courier", "delivery_status": "local"})

    msg.metadata_json = json.dumps(metadata)
    conv.updated_at = datetime.now(UTC)
    await db.flush()

    return MessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        sender_id=msg.sender_id,
        sender_name=effective_sender.name,
        content=msg.content,
        message_type=msg.message_type,
        metadata_json=_message_metadata_for_viewer(msg, staff=True),
        artifact_payload=msg.artifact_payload,
        artifact_state=msg.artifact_state,
        created_at=msg.created_at,
        updated_at=msg.updated_at,
    )


@router.get("/{conversation_id}/messages", response_model=MessageListResponse)
async def get_messages(
    conversation_id: UUID,
    skip: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get message history for a conversation (newest first)."""
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))

    # Count total
    count_result = await db.execute(select(func.count()).where(Message.conversation_id == conversation_id))
    total = count_result.scalar()

    # Fetch messages
    result = await db.execute(
        select(Message, User.name)
        .join(User, Message.sender_id == User.id, isouter=True)
        .where(Message.conversation_id == conversation_id)
        .order_by(desc(Message.created_at))
        .offset(skip)
        .limit(limit)
    )
    rows = result.all()

    messages = [
        MessageResponse(
            id=msg.id,
            conversation_id=msg.conversation_id,
            sender_id=msg.sender_id,
            sender_name=sender_name,
            content=msg.content,
            message_type=msg.message_type,
            metadata_json=_message_metadata_for_viewer(msg, staff=_is_staff_viewer(current_user)),
            artifact_payload=msg.artifact_payload,
            artifact_state=msg.artifact_state,
            created_at=msg.created_at,
            updated_at=msg.updated_at,
        )
        for msg, sender_name in rows
    ]

    return MessageListResponse(
        messages=messages,
        total=total,
        has_more=(skip + limit < total),
    )


@router.post("/{conversation_id}/mark-read", status_code=204)
async def mark_conversation_read(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark all messages in this conversation as read for the current user."""
    participant = await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))
    participant.last_read_at = datetime.now(UTC)


@router.delete("/{conversation_id}/messages/{message_id}", status_code=204)
async def delete_message(
    conversation_id: UUID,
    message_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Redact a message while preserving an audit trail."""
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))
    result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
        )
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    owned_sender_ids = set(_visible_user_ids(current_user))
    if msg.sender_id not in owned_sender_ids and not is_admin:
        raise HTTPException(status_code=403, detail="Can only delete your own messages")

    msg.content = "[message removed]"
    msg.message_type = "deleted"
    msg.metadata_json = _merge_metadata(
        msg.metadata_json,
        {
            "deleted": True,
            "deleted_by": str(current_user.id),
            "deleted_at": datetime.now(UTC).isoformat(),
            "retention": "content_redacted_record_retained",
        },
    )
    msg.updated_at = datetime.now(UTC)
    db.add(
        _audit_message(
            conversation_id,
            current_user.id,
            "message_redacted",
            {"message_id": str(message_id)},
        )
    )
    await db.flush()


@router.post("/{conversation_id}/archive", status_code=204)
async def archive_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Archive a conversation for the current user. Removes them from
    the participant list so the conversation no longer appears in
    their inbox. The conversation itself is not deleted - other
    participants still see it.
    """
    participant = await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))
    db.add(_audit_message(conversation_id, current_user.id, "conversation_archived_for_user"))
    await db.delete(participant)
    await db.flush()


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Archive a conversation without destroying the retained thread.
    Admin/founder can archive for all participants. Regular users can only
    archive their own participation.
    """
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 0)

    db.add(
        _audit_message(
            conversation_id,
            current_user.id,
            "conversation_archived_for_all" if is_admin else "conversation_archived_for_user",
        )
    )
    if is_admin:
        result = await db.execute(
            select(ConversationParticipant).where(ConversationParticipant.conversation_id == conversation_id)
        )
        for participant in result.scalars().all():
            await db.delete(participant)
    else:
        participant = await _verify_participant(
            conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user)
        )
        await db.delete(participant)
    conv.updated_at = datetime.now(UTC)
    await db.flush()


# Typing indicators (in-memory, ephemeral)

_typing_state: dict[str, dict[str, float]] = {}  # conv_id -> {user_id: timestamp}


@router.post("/{conversation_id}/typing", status_code=204)
async def set_typing(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Signal that the current user is typing in this conversation."""
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))
    effective_user = await _resolve_effective_user(current_user, db)
    conv_key = str(conversation_id)
    if conv_key not in _typing_state:
        _typing_state[conv_key] = {}
    _typing_state[conv_key][str(effective_user.id)] = _time.time()


@router.get("/{conversation_id}/typing")
async def get_typing(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get who is currently typing (within last 5 seconds)."""
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))
    conv_key = str(conversation_id)
    now = _time.time()
    typers = _typing_state.get(conv_key, {})
    visible_ids = {str(uid) for uid in _visible_user_ids(current_user)}
    active = [uid for uid, ts in typers.items() if now - ts < 5 and uid not in visible_ids]
    # Resolve names
    names = []
    for uid in active:
        result = await db.execute(select(User.name).where(User.id == uid))
        name = result.scalar_one_or_none()
        if name:
            names.append(name.split()[0])
    return {"typing": names}


# Gala AI Assist

GALA_COURIER_SYSTEM = """You are Gala, the AI concierge for Ryozan Studio.
You draft reply messages on behalf of the artist. Your voice is calm, direct, warm, and grounded in tattoo-shop operations.

Rules:
- Match the artist's tone from their previous messages in the thread
- Keep replies 2-4 sentences max
- Sound like a real tattoo artist, not corporate
- Be warm but not overly enthusiastic
- Reference specific details from the conversation
- Never use emojis
- Never include a subject line or sign-off
- Do not state availability, booking timelines, rates, prices, deposits, or estimates unless staff already stated them in the conversation
- If scheduling is the next step, ask for availability or say the shop can confirm scheduling
- Write only the message body, nothing else"""


@router.post("/{conversation_id}/gala-assist")
async def gala_courier_assist(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a context-aware reply draft for the current conversation.
    Uses conversation history + Ryozan brand voice + artist tone matching.
    """
    await _verify_participant(conversation_id, current_user.id, db, proxy_for=_proxy_target(current_user))
    effective_user = await _resolve_effective_user(current_user, db)
    visible_ids = _visible_user_ids(current_user)

    from app.services.ai_routing import require_ai_access

    settings = get_settings()
    require_ai_access(settings, current_user)
    if not settings.ARK_MODEL_GATEWAY_KEY:
        raise HTTPException(status_code=503, detail="AI not configured. Set NEBIUS_API_KEY.")

    # Fetch recent messages for context (last 20)
    msg_result = await db.execute(
        select(Message, User.name)
        .join(User, Message.sender_id == User.id)
        .where(Message.conversation_id == conversation_id)
        .order_by(desc(Message.created_at))
        .limit(20)
    )
    rows = msg_result.all()
    if not rows:
        raise HTTPException(status_code=400, detail="No messages in conversation")

    # Build conversation transcript
    transcript_lines = []
    for msg, sender_name in reversed(rows):
        role_label = "You" if msg.sender_id in visible_ids else sender_name
        transcript_lines.append(f"{role_label}: {msg.content}")
    transcript = "\n".join(transcript_lines)

    # Get the other participant's name
    other_result = await db.execute(
        select(User.name)
        .join(ConversationParticipant, ConversationParticipant.user_id == User.id)
        .where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id.notin_(visible_ids),
        )
        .limit(1)
    )
    other_name = other_result.scalar_one_or_none() or "the client"

    artist_name = effective_user.name or "the artist"

    prompt = f"""Draft a reply for {artist_name} to send to {other_name}.

Conversation so far:
{transcript}

Write {artist_name}'s next message. Match their tone from their previous messages above."""

    from app.services.ai_routing import call_ark_gateway_chat_completion, resolve_ai_lane
    from app.services.api_budget import check_budget, log_usage

    await check_budget(db, settings.ANTHROPIC_MONTHLY_BUDGET_CAP)
    lane = await resolve_ai_lane(current_user, db, settings)
    response = await call_ark_gateway_chat_completion(
        settings=settings,
        primary_model=lane.primary_model,
        fallback_models=lane.fallback_models,
        max_tokens=300,
        tools=[],
        system_prompt=GALA_COURIER_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    choice = ((response.get("choices") or [{}])[0]).get("message") or {}
    draft = _clean_ai_draft(choice.get("content") or "")
    if not draft:
        raise HTTPException(status_code=502, detail="AI returned an empty draft")

    usage = response.get("usage") or {}
    await log_usage(
        db,
        "conversations/gala-assist",
        int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        effective_user.id,
    )
    return {"draft": draft, "conversation_id": str(conversation_id)}
