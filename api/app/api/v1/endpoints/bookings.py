"""
Booking endpoints - tattoo appointment lifecycle.

- Clients create booking requests
- Artists view inbound + update status
- Both sides can view their bookings
- Auto-creates a conversation thread per booking
- Public submit (no auth) with email confirmations
"""

import asyncio
import json
import logging
import random
import secrets
import string
import uuid as uuid_mod
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import Integer, and_, cast, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.calendar import (
    _WORK_SCHEDULE,
    _active_holds_for_range,
    _artist_booking_rules,
    _booking_interval,
    _confirmed_bookings_for_range,
    _generate_available_slots,
    _hold_interval,
    _parse_busy_datetime,
    _parse_local_time,
    _personal_blocks_for_range,
    _resolve_artist_work_schedule,
    _resolve_slot_duration,
    _rule_window,
    _slot_conflicts,
    _slot_is_available,
)
from app.api.v1.endpoints.conversations import (
    _queue_conversation_notifications,
    process_courier_notification_outbox_once,
)
from app.constants.status import BookingStatus, QuoteStatus
from app.constants.timezones import SHOP_TIMEZONE
from app.core.config import get_settings
from app.core.database import async_session, get_db
from app.core.rate_limit import limiter
from app.core.routes import ROUTES
from app.core.security import (
    BOOKY_CAPABILITY_EXPIRE_MINUTES,
    ROLE_RANK,
    create_booky_capability_token,
    decode_booky_capability_token,
    get_current_user,
    hash_password,
)
from app.core.user_lookup import find_user_by_email
from app.models.booking import Booking
from app.models.booking_addon import BookingAddOn, BookingAddOnSelection
from app.models.booking_draft import BookingDraft
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.booking_slot_hold import BookingSlotHold
from app.models.crm_pipeline_task import CrmPipelineTask
from app.models.email_log import EmailLog
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.notification import Notification as NotificationModel
from app.models.quote import Quote
from app.models.session_type import ArtistSessionType
from app.models.user import BTTCOUser, normalize_email
from app.schemas.booking import (
    BookingApprovalDefaults,
    BookingApprovalResult,
    BookingApprove,
    BookingCancel,
    BookingCreate,
    BookingDraftListResponse,
    BookingDraftRecord,
    BookingLifecycleEventResponse,
    BookingListResponse,
    BookingOutcomeEnvelope,
    BookingReschedule,
    BookingResponse,
    BookingScheduleSuggestion,
    BookingScheduleSuggestionResponse,
    BookingUpdate,
    BookyCapabilityRequest,
    BookyCapabilityResponse,
    BookyDecisionEnvelope,
    BookySettingsResponse,
    BookySettingsUpdate,
    PreferredSlot,
    PublicBookingDraft,
    PublicBookingDraftResponse,
    PublicBookingResponse,
    PublicBookingSubmit,
    PublicInquiryResponse,
    PublicInquirySubmit,
    StaffBookingCreate,
)
from app.services.account_setup import send_account_setup_email
from app.services.booky_policy import (
    artist_canary_allowed,
    booky_action_id,
    normalize_booky_mode,
    resolve_booky_policy,
)
from app.services.calendar_sync import (
    artist_calendar_connected,
    artist_calendar_id,
    create_booking_event,
    delete_booking_event,
    get_availability,
    get_booking_event_busy_block,
    get_booking_event_state,
    update_booking_event,
)
from app.services.conversation_threads import conversation_reply_to, find_or_create_direct_conversation
from app.services.email import (
    BOOKY_COURIER_STAFF_RECOVERY_MAX_ATTEMPTS,
    BOOKY_COURIER_STALE_MINUTES,
    FROM_ADMIN,
    FROM_SHOP,
    _format_time_12hr,
    build_booking_confirmation_html,
    process_booky_courier_outbox_once,
    render_transactional_email,
    send_email,
    should_notify,
)
from app.services.email_foundry import dispatch_foundry_trigger
from app.services.email_foundry_booking import render_artist_booking_alert, render_request_approved
from app.services.inquiry_mirror import mirror_general_inquiry
from app.services.payments import apply_customer_receipt, customer_price_cents, resolve_charge_routing
from app.services.push import send_push_notification
from app.services.quest_hooks import check_and_complete_quest
from app.services.reward_engine import reward_user_by_id
from app.services.sms import send_sms
from app.services.storage import upload_file
from app.services.stripe_client import get_stripe_client

logger = logging.getLogger("bttco.bookings")

router = APIRouter(prefix="/bookings", tags=["bookings"])

BOOKING_SMS_CONSENT_TEXT = (
    "I agree to receive booking confirmations, appointment reminders, reschedule updates, "
    "aftercare follow-ups, and replies to my questions from Ryozan Studio at the phone number provided. "
    "Message frequency varies. Message and data rates may apply. Reply HELP for help and STOP to opt out. "
    "Consent is not required to book. SMS Terms: https://ryozan.app/legal#sms. "
    "Privacy Policy: https://ryozan.app/legal#privacy."
)
BOOKING_EMAIL_MAX_ATTEMPTS = 5
BOOKING_EMAIL_STALE_MINUTES = 10
# General inquiries are Courier threads, not bookings. The public inquiry id is
# recorded on the existing drafts table under this status so a retried submit
# returns the same reference instead of opening a second thread.
INQUIRY_DRAFT_STATUS = "inquiry_submitted"
INQUIRY_REFERENCE_PREFIX = "INQ"
BYOF_REVIEW_WINDOW_HOURS = 48
BYOF_REVIEW_REMINDER_HOURS = 24


# Helpers


def _add_booking_lifecycle_event(
    db: AsyncSession,
    *,
    event_type: str,
    summary: str,
    source: str = "system",
    booking: Booking | None = None,
    artist_id: UUID | None = None,
    actor_id: UUID | None = None,
    hold_token: str | None = None,
    guest_email: str | None = None,
    details: dict | None = None,
    provider_event_id: str | None = None,
) -> BookingLifecycleEvent:
    event = BookingLifecycleEvent(
        booking_id=booking.id if booking else None,
        artist_id=booking.artist_id if booking else artist_id,
        actor_id=actor_id,
        event_type=event_type,
        source=source,
        provider_event_id=provider_event_id,
        reference_code=booking.reference_code if booking else None,
        hold_token=hold_token,
        guest_email=booking.guest_email if booking else guest_email,
        summary=summary,
        details=details,
    )
    db.add(event)
    return event


def _queue_booking_email_intent(
    db: AsyncSession,
    *,
    booking: Booking,
    kind: str,
    recipient: str,
    payload: dict,
    intent_key: str | None = None,
) -> BookingLifecycleEvent:
    """Persist a restart-safe booking email intent on the existing lifecycle owner.

    BookingLifecycleEvent already owns durable scheduler and provider receipts, so
    booking email delivery extends that authority instead of creating a parallel
    email queue. The worker below projects every attempt back onto this event.
    """
    recipient_key = uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, normalize_email(recipient)).hex
    now = datetime.now(UTC)
    return _add_booking_lifecycle_event(
        db,
        booking=booking,
        event_type="booking_email_intent",
        source="booking_email",
        summary=f"Booking {kind.replace('_', ' ')} email queued.",
        provider_event_id=(
            f"booking:{booking.id}:email:{kind}:{recipient_key}" + (f":{intent_key}" if intent_key else "")
        ),
        details={
            "kind": kind,
            "to": recipient,
            "status": "queued",
            "attempts": 0,
            "next_attempt_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "last_error": None,
            "payload": payload,
        },
    )


def _require_role(user: BTTCOUser, min_role: str):
    """Raise 403 if user doesn't meet minimum role."""
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")


def _require_booking_management_access(user: BTTCOUser, booking: Booking) -> None:
    """Require shop management scope or the assigned artist's own scope."""
    role_rank = ROLE_RANK.get(user.role, 0)
    is_shop_manager = user.role == "assistant" or role_rank >= ROLE_RANK.get("admin", 3)
    is_assigned_artist = role_rank >= ROLE_RANK.get("artist", 2) and user.id == booking.artist_id
    if not (is_shop_manager or is_assigned_artist):
        raise HTTPException(
            status_code=403,
            detail="Only the assigned artist or shop management can manage this booking",
        )


def _append_artist_note(existing: str | None, note: str) -> str:
    clean_existing = (existing or "").strip()
    clean_note = note.strip()
    if not clean_existing:
        return clean_note
    return f"{clean_existing}\n\n{clean_note}"


def _rank_schedule_suggestions(
    slots,
    *,
    preferred_date: date | None,
    preferred_time: str | None,
    limit: int,
) -> list[BookingScheduleSuggestion]:
    def score(slot) -> tuple[int, str, str]:
        rank = 0
        rationale_bits: list[str] = []
        if preferred_date and slot.date == preferred_date.isoformat():
            rank -= 1000
            rationale_bits.append("matches the client's preferred date")
        if preferred_time and slot.time == preferred_time[:5]:
            rank -= 250
            rationale_bits.append("matches the client's preferred time")
        if slot.confidence == "high":
            rank -= 25
            rationale_bits.append("verified against Google Calendar")
        if not rationale_bits:
            rationale_bits.append("earliest server-verified opening")
        return rank, slot.date, slot.time

    ranked = sorted(slots, key=score)[:limit]
    suggestions: list[BookingScheduleSuggestion] = []
    for slot in ranked:
        rationale_bits = []
        if preferred_date and slot.date == preferred_date.isoformat():
            rationale_bits.append("matches the client's preferred date")
        if preferred_time and slot.time == preferred_time[:5]:
            rationale_bits.append("matches the client's preferred time")
        if slot.confidence == "high":
            rationale_bits.append("verified against Google Calendar")
        if not rationale_bits:
            rationale_bits.append("earliest server-verified opening")
        suggestions.append(
            BookingScheduleSuggestion(
                date=slot.date,
                time=slot.time,
                label=slot.label,
                duration_minutes=slot.duration_minutes,
                source=slot.source,
                confidence=slot.confidence,
                rationale=", ".join(rationale_bits),
            )
        )
    return suggestions


async def _schedule_blocking_reasons(
    db: AsyncSession,
    *,
    artist_id: UUID,
    scheduled_date,
    scheduled_time: str,
    duration_minutes: int,
    buffer_minutes: int,
    minimum_notice_hours: int,
    max_advance_days: int,
    allow_same_day: bool,
    busy_blocks: list[dict],
    exclude_booking_id: UUID | None = None,
) -> list[str]:
    slot_start = datetime.combine(scheduled_date, datetime.min.time(), tzinfo=SHOP_TIMEZONE).replace(
        hour=int(scheduled_time[:2]),
        minute=int(scheduled_time[3:5]),
    )
    slot_end = slot_start + timedelta(minutes=duration_minutes)
    reasons: list[str] = []
    hours = _WORK_SCHEDULE[slot_start.weekday()]
    earliest, latest = _rule_window(
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
    )
    if not hours:
        reasons.append("closed_day")
    else:
        open_hour, close_hour = hours
        open_at = datetime.combine(scheduled_date, datetime.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=open_hour)
        close_at = datetime.combine(scheduled_date, datetime.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=close_hour)
        if slot_start < open_at or slot_end > close_at:
            reasons.append("outside_work_hours")
    if slot_start < earliest:
        reasons.append("minimum_notice")
    if slot_start > latest:
        reasons.append("max_advance")

    google_intervals = []
    for block in busy_blocks:
        busy_start = _parse_busy_datetime(str(block.get("start") or ""))
        busy_end = _parse_busy_datetime(str(block.get("end") or ""))
        if busy_start and busy_end:
            google_intervals.append((busy_start, busy_end))
    if _slot_conflicts(slot_start, slot_end, google_intervals, buffer_minutes):
        reasons.append("google_busy")

    booking_intervals = []
    for confirmed in await _confirmed_bookings_for_range(
        db,
        artist_id=artist_id,
        start_date=scheduled_date,
        end_date=scheduled_date,
        exclude_booking_id=exclude_booking_id,
    ):
        interval = _booking_interval(confirmed)
        if interval:
            booking_intervals.append(interval)
    if _slot_conflicts(slot_start, slot_end, booking_intervals, buffer_minutes):
        reasons.append("booking_conflict")

    hold_intervals = []
    for hold in await _active_holds_for_range(
        db,
        artist_id=artist_id,
        start_date=scheduled_date,
        end_date=scheduled_date,
    ):
        interval = _hold_interval(hold)
        if interval:
            hold_intervals.append(interval)
    if _slot_conflicts(slot_start, slot_end, hold_intervals, buffer_minutes):
        reasons.append("active_hold")
    return reasons


async def _authorize_schedule_override(
    db: AsyncSession,
    *,
    current_user: BTTCOUser,
    artist_id: UUID,
    scheduled_date,
    scheduled_time: str,
    duration_minutes: int,
    buffer_minutes: int,
    minimum_notice_hours: int,
    max_advance_days: int,
    allow_same_day: bool,
    busy_blocks: list[dict],
    exclude_booking_id: UUID | None,
    allow_conflict: bool,
    allow_out_of_bounds: bool,
    override_reason: str | None,
) -> dict:
    if not (allow_conflict or allow_out_of_bounds):
        raise HTTPException(status_code=409, detail="Slot is no longer available")
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Only founder/admin can override scheduler availability")
    clean_reason = (override_reason or "").strip()
    if not clean_reason:
        raise HTTPException(status_code=422, detail="override_reason is required for scheduler overrides")

    reasons = await _schedule_blocking_reasons(
        db,
        artist_id=artist_id,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        duration_minutes=duration_minutes,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
        busy_blocks=busy_blocks,
        exclude_booking_id=exclude_booking_id,
    )
    conflict_reasons = {"google_busy", "booking_conflict", "active_hold"}
    bounds_reasons = {"closed_day", "outside_work_hours", "minimum_notice", "max_advance"}
    unapproved = [
        reason
        for reason in reasons
        if (reason in conflict_reasons and not allow_conflict)
        or (reason in bounds_reasons and not allow_out_of_bounds)
        or (reason not in conflict_reasons and reason not in bounds_reasons)
    ]
    if unapproved:
        raise HTTPException(status_code=409, detail=f"Slot blocked by: {', '.join(unapproved)}")
    return {
        "used": True,
        "allow_conflict": allow_conflict,
        "allow_out_of_bounds": allow_out_of_bounds,
        "override_reason": clean_reason,
        "blocking_reasons": reasons,
    }


def _booking_calendar_payload(
    booking: Booking,
    client_name: str,
    client_email: str | None = None,
) -> dict:
    return {
        "client_name": client_name,
        "client_email": client_email,
        "description": booking.description,
        "placement": booking.placement,
        "reference_code": booking.reference_code,
        "scheduled_date": booking.scheduled_date,
        "scheduled_time": booking.scheduled_time or "12:00",
        "duration_minutes": booking.duration_minutes or 120,
    }


async def _booking_contact(db: AsyncSession, booking: Booking) -> tuple[str, str | None]:
    if booking.client_id:
        result = await db.execute(select(BTTCOUser.name, BTTCOUser.email).where(BTTCOUser.id == booking.client_id))
        row = result.one_or_none()
        if row:
            return row[0] or booking.guest_name or "Client", row[1] or booking.guest_email
    return booking.guest_name or "Client", booking.guest_email


async def _notify_booking_lifecycle(
    *,
    booking: Booking,
    db: AsyncSession,
    subject: str,
    body: str,
) -> bool:
    client_name, client_email = await _booking_contact(db, booking)
    if not client_email:
        return False
    html = f"""
    <p>Hi {client_name},</p>
    <p>{body}</p>
    <p>Reference: <strong>{booking.reference_code or booking.id}</strong></p>
    <p>Ryozan Studio</p>
    """
    return await send_email(
        to=client_email,
        subject=subject,
        html_body=html,
        tags=["booking_lifecycle"],
        from_alias=FROM_SHOP,
    )


async def _post_booking_calendar_event(
    db: AsyncSession,
    booking: Booking,
    artist: BTTCOUser | None,
    actor_id: UUID | None,
) -> bool:
    """Create (or update) the Google Calendar event for a scheduled booking.

    Idempotent: updates in place when google_calendar_event_id already exists,
    so it is safe to call after the Stripe webhook already created an event.
    Requires an active artist Calendar authority.
    """
    if not booking.scheduled_date or artist is None or not artist_calendar_connected(artist):
        return False
    client_name, client_email = await _booking_contact(db, booking)
    payload = _booking_calendar_payload(booking, client_name, client_email)
    try:
        if booking.google_calendar_event_id:
            await update_booking_event(
                booking.google_calendar_event_id,
                payload,
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
            action = "updated"
        else:
            result = await create_booking_event(
                payload,
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
            booking.google_calendar_event_id = result.get("event_id")
            action = "created"
        _add_booking_lifecycle_event(
            db,
            event_type="calendar_synced",
            source="booking_confirm",
            booking=booking,
            actor_id=actor_id,
            summary=f"Calendar event {action} for {booking.scheduled_date} {booking.scheduled_time or ''}.",
            details={"event_id": booking.google_calendar_event_id, "action": action},
        )
        return True
    except Exception as exc:
        logger.error("Calendar sync on confirm failed for booking %s: %s", booking.id, exc)
        return False


async def _send_booking_confirmed_sms(db: AsyncSession, booking: Booking) -> None:
    """Send a transactional confirmation SMS if a phone + opt-in exists.

    send_sms() enforces opt-in internally, so this is compliant by default.
    """
    phone: str | None = None
    user_id: UUID | None = None
    if booking.client_id:
        row = (
            await db.execute(select(BTTCOUser.phone, BTTCOUser.id).where(BTTCOUser.id == booking.client_id))
        ).one_or_none()
        if row:
            phone, user_id = row[0], row[1]
    phone = phone or booking.guest_phone
    if not phone:
        return
    when = f"{booking.scheduled_date} at {booking.scheduled_time}" if booking.scheduled_date else "soon"
    body = f"Ryozan Studio: your appointment is confirmed for {when}. Ref {booking.reference_code or booking.id}."
    try:
        await send_sms(
            db=db,
            to=phone,
            body=body,
            template_slug="booking_confirmed",
            conversation_id=booking.conversation_id,
            user_id=user_id,
            is_transactional=True,
        )
    except Exception as exc:
        logger.error("Confirmation SMS failed for booking %s: %s", booking.id, exc)


async def _send_booking_request_received_sms(
    db: AsyncSession,
    *,
    phone: str | None,
    client_name: str,
    artist_name: str,
    reference_code: str,
    client_id: UUID,
    conversation_id: UUID | None,
) -> None:
    """Send the public booking request receipt after explicit SMS opt-in."""
    if not phone:
        return
    first_name = client_name.split(" ", 1)[0] or "there"
    body = (
        f"Ryozan Studio: Hi {first_name}, we received your request with "
        f"{artist_name}. Ref {reference_code}. We'll follow up within 24-48 hours."
    )
    try:
        await send_sms(
            db=db,
            to=phone,
            body=body,
            template_slug="booking_request_received",
            conversation_id=conversation_id,
            user_id=client_id,
            is_transactional=True,
            skip_opt_in_check=True,
        )
    except Exception as exc:
        logger.error("Request received SMS failed for booking %s: %s", reference_code, exc)


async def _on_booking_confirmed(
    db: AsyncSession,
    booking: Booking,
    artist: BTTCOUser | None,
    actor_id: UUID | None,
    *,
    notify_client: bool = True,
) -> None:
    """Post a confirmed booking to Calendar and optionally notify the client."""
    await _post_booking_calendar_event(db, booking, artist, actor_id)
    if notify_client:
        await _notify_booking_lifecycle(
            booking=booking,
            db=db,
            subject="Your tattoo appointment is confirmed",
            body=(
                f"Your appointment is confirmed for {booking.scheduled_date} at {booking.scheduled_time}."
                if booking.scheduled_date
                else "Your appointment is confirmed."
            ),
        )
        await _send_booking_confirmed_sms(db, booking)
        return

    _add_booking_lifecycle_event(
        db,
        event_type="client_notification_suppressed",
        source="staff_update",
        booking=booking,
        actor_id=actor_id,
        summary="Legacy confirmation email and SMS were intentionally suppressed.",
        details={"calendar": True, "email": False, "sms": False},
    )


def _parse_ref_urls(raw: str | None) -> list[str]:
    """Parse a JSON-encoded URL list from a Text column, returning [] on failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [u for u in parsed if isinstance(u, str)]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _parse_preferred_slots(raw: object) -> list[PreferredSlot] | None:
    """Project Booking.preferred_slots_json onto the response contract, None when empty or malformed."""
    if not isinstance(raw, list):
        return None
    slots: list[PreferredSlot] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            slots.append(PreferredSlot(date=entry.get("date"), time=entry.get("time")))
        except (ValueError, TypeError):
            continue
    return slots or None


def _serialize_preferred_slots(slots: list[PreferredSlot] | None) -> list[dict[str, str]] | None:
    if not slots:
        return None
    return [{"date": slot.date.isoformat(), "time": slot.time} for slot in slots]


async def _enrich_booking(booking: Booking, db: AsyncSession) -> BookingResponse:
    """Add client_name, artist_name, and resolved identity fields."""
    client_row = None
    if booking.client_id:
        client_r = await db.execute(
            select(BTTCOUser.name, BTTCOUser.email, BTTCOUser.phone).where(BTTCOUser.id == booking.client_id)
        )
        client_row = client_r.one_or_none()

    artist_name = None
    if booking.artist_id:
        artist_r = await db.execute(select(BTTCOUser.name).where(BTTCOUser.id == booking.artist_id))
        artist_name = artist_r.scalar_one_or_none()

    # Resolved identity - coalesce guest fields with user record
    resolved_name = booking.guest_name or (client_row[0] if client_row else None)
    resolved_email = booking.guest_email or (client_row[1] if client_row else None)
    resolved_phone = booking.guest_phone or (client_row[2] if client_row else None)

    ref_urls = _parse_ref_urls(booking.reference_urls) or None

    return BookingResponse(
        id=booking.id,
        client_id=booking.client_id,
        artist_id=booking.artist_id,
        status=booking.status,
        description=booking.description,
        placement=booking.placement,
        size_estimate=booking.size_estimate,
        style_preference=booking.style_preference,
        reference_urls=ref_urls,
        preferred_date=booking.preferred_date,
        preferred_time=booking.preferred_time,
        preferred_slots=_parse_preferred_slots(booking.preferred_slots_json),
        scheduled_date=booking.scheduled_date,
        scheduled_time=booking.scheduled_time,
        duration_minutes=booking.duration_minutes,
        deposit_amount=float(booking.deposit_amount) if booking.deposit_amount else None,
        estimated_total=float(booking.estimated_total) if booking.estimated_total else None,
        deposit_paid=booking.deposit_paid,
        is_bring_your_own_flash=bool(booking.is_bring_your_own_flash),
        deposit_authorization_status=booking.deposit_authorization_status,
        deposit_authorized_at=booking.deposit_authorized_at,
        deposit_review_deadline_at=booking.deposit_review_deadline_at,
        deposit_review_reminder_sent_at=booking.deposit_review_reminder_sent_at,
        artist_notes=booking.artist_notes,
        client_notes=booking.client_notes,
        conversation_id=booking.conversation_id,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
        guest_email=booking.guest_email,
        guest_phone=booking.guest_phone,
        sms_opt_in=booking.sms_opt_in,
        sms_opt_in_at=booking.sms_opt_in_at,
        sms_consent_text=booking.sms_consent_text,
        reference_code=booking.reference_code,
        booking_kind=booking.booking_kind or "tattoo_session",
        consultation_method=booking.consultation_method,
        client_name=resolved_name,
        artist_name=artist_name,
        resolved_name=resolved_name,
        resolved_email=resolved_email,
        resolved_phone=resolved_phone,
    )


async def _booky_projection(
    booking: Booking,
    db: AsyncSession,
    *,
    staff: bool,
) -> BookingOutcomeEnvelope | BookyDecisionEnvelope:
    result = await db.execute(
        select(BookingLifecycleEvent)
        .where(
            BookingLifecycleEvent.booking_id == booking.id,
            BookingLifecycleEvent.source.in_({"booky", "booking_email"}),
        )
        .order_by(BookingLifecycleEvent.created_at, BookingLifecycleEvent.id)
    )
    receipts = list(result.scalars().all())
    latest_details: dict = {}
    for receipt in receipts:
        if receipt.details and receipt.event_type in {"booky_recommendation", "booky_blocked"}:
            latest_details = receipt.details

    action_id = str(latest_details.get("action_id") or f"booky_{booking.id.hex}")
    state_receipt = next(
        (
            receipt
            for receipt in reversed(receipts)
            if receipt.event_type in {"booky_confirmation_failed", "booky_retry_failed", "consultation_confirmed"}
        ),
        None,
    )
    state_event = state_receipt.event_type if state_receipt else None
    state_details = state_receipt.details or {} if state_receipt else {}
    failed = state_event in {"booky_confirmation_failed", "booky_retry_failed"}
    approval_needed = latest_details.get("truth_state") == "approval_needed"
    confirmed = str(booking.status) == BookingStatus.CONFIRMED

    delivery_receipt = next(
        (
            receipt
            for receipt in reversed(receipts)
            if receipt.event_type in {"booky_delivery_intent", "booky_delivery_queued"}
            or (
                receipt.event_type == "booking_email_intent"
                and (receipt.details or {}).get("kind") == "client_confirmation"
            )
        ),
        None,
    )
    delivery_details = delivery_receipt.details or {} if delivery_receipt else {}
    courier_message_id: UUID | None = None
    try:
        if delivery_details.get("courier_message_id"):
            courier_message_id = UUID(str(delivery_details["courier_message_id"]))
    except (TypeError, ValueError):
        courier_message_id = None

    message_status: str | None = None
    message_attempt = 0
    message_updated_at: datetime | None = None
    message_attempt_started_at: datetime | None = None
    email_log: EmailLog | None = None
    if delivery_receipt and delivery_receipt.event_type == "booking_email_intent":
        message_status = str(delivery_details.get("status") or "queued")
        if message_status == "delegated":
            message_status = "queued"
        message_attempt = int(delivery_details.get("attempts") or 0)
        raw_updated_at = delivery_details.get("updated_at")
        if raw_updated_at:
            try:
                message_updated_at = datetime.fromisoformat(str(raw_updated_at))
                if message_updated_at.tzinfo is None:
                    message_updated_at = message_updated_at.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                message_updated_at = None
    if courier_message_id:
        message = await db.get(Message, courier_message_id)
        if message and message.metadata_json:
            try:
                message_metadata = json.loads(message.metadata_json) or {}
                message_status = str(message_metadata.get("delivery_status") or "")
                message_attempt = int(message_metadata.get("delivery_attempts") or 0)
                raw_updated_at = message_metadata.get("delivery_updated_at")
                if raw_updated_at:
                    message_updated_at = datetime.fromisoformat(str(raw_updated_at))
                    if message_updated_at.tzinfo is None:
                        message_updated_at = message_updated_at.replace(tzinfo=UTC)
                raw_started_at = message_metadata.get("delivery_attempt_started_at")
                if raw_started_at:
                    message_attempt_started_at = datetime.fromisoformat(str(raw_started_at))
                    if message_attempt_started_at.tzinfo is None:
                        message_attempt_started_at = message_attempt_started_at.replace(tzinfo=UTC)
            except (TypeError, ValueError, json.JSONDecodeError):
                message_status = None
        email_query = select(EmailLog).where(EmailLog.courier_message_id == courier_message_id)
        email_query = email_query.where(
            EmailLog.courier_attempt == message_attempt if message_attempt > 0 else EmailLog.courier_attempt.is_(None)
        )
        email_result = await db.execute(email_query.order_by(EmailLog.created_at.desc(), EmailLog.id.desc()).limit(1))
        email_log = email_result.scalar_one_or_none()

    email_created_at = email_log.created_at if email_log else None
    if email_created_at and email_created_at.tzinfo is None:
        email_created_at = email_created_at.replace(tzinfo=UTC)
    email_log_at = (email_log.updated_at or email_created_at) if email_log else None
    if email_log_at and email_log_at.tzinfo is None:
        email_log_at = email_log_at.replace(tzinfo=UTC)
    email_is_current = bool(
        email_log
        and (not message_attempt_started_at or (email_created_at and email_created_at >= message_attempt_started_at))
    )
    email_status = email_log.status if email_is_current and email_log else None
    stale_before = datetime.now(UTC) - timedelta(minutes=BOOKY_COURIER_STALE_MINUTES)
    stale_sending = bool(message_status == "sending" and message_updated_at and message_updated_at <= stale_before)
    if message_status == "sending" and not stale_sending:
        courier_state = "sending"
    elif message_status == "sending" and email_status in {"delivered", "opened", "clicked"}:
        courier_state = "delivered"
    elif message_status == "sending" and email_status in {"sent", "dev_skipped"}:
        courier_state = "sent"
    elif message_status == "sending" and email_status in {"failed", "bounced", "complained"}:
        courier_state = "failed"
    elif message_status in {"delivered", "opened", "clicked"} or (
        message_status in {"local", "sent"} and email_status in {"delivered", "opened", "clicked"}
    ):
        courier_state = "delivered"
    elif message_status in {"local", "sent"}:
        courier_state = "sent"
    elif message_status in {"failed", "bounced", "complained"}:
        courier_state = "failed"
    elif message_status == "queued" or delivery_receipt:
        courier_state = "queued"
    elif email_status in {"delivered", "opened", "clicked"}:
        courier_state = "delivered"
    elif email_status in {"sent", "dev_skipped"}:
        courier_state = "sent"
    elif email_status in {"failed", "bounced", "complained"}:
        courier_state = "failed"
    else:
        courier_state = "not_queued"

    completed = confirmed and courier_state in {"sent", "delivered"}
    truth_state = (
        "empty"
        if not receipts
        else "failed"
        if courier_state == "failed" or (failed and not confirmed)
        else "completed"
        if completed
        else "approval_needed"
        if approval_needed
        else "ready"
        if confirmed and courier_state in {"queued", "sending"}
        else str(latest_details.get("truth_state") or "empty")
    )
    consultation_method = booking.consultation_method or ""
    confirmation_receipt = next(
        (receipt for receipt in reversed(receipts) if receipt.event_type == "consultation_confirmed"),
        None,
    )
    confirmation_details = confirmation_receipt.details if confirmation_receipt and confirmation_receipt.details else {}
    updated_at = receipts[-1].created_at if receipts else booking.updated_at
    if email_is_current and email_log_at and (not updated_at or email_log_at > updated_at):
        updated_at = email_log_at
    stale_delivery = bool(
        courier_state in {"queued", "sending"}
        and (message_updated_at or updated_at)
        and (message_updated_at or updated_at) <= stale_before
    )
    booking_email_retryable = bool(
        delivery_receipt
        and delivery_receipt.event_type == "booking_email_intent"
        and courier_state == "failed"
        and message_attempt < BOOKING_EMAIL_MAX_ATTEMPTS
    )
    common = dict(
        truth_state=truth_state,
        booking_status=booking.status.value if hasattr(booking.status, "value") else str(booking.status),
        calendar_state="created" if booking.google_calendar_event_id else "not_created",
        invitation_state=str(
            confirmation_details.get("invitation_state") or state_details.get("invitation_state") or "not_sent"
        ),
        meet_state=(
            "not_required"
            if consultation_method != "meet"
            else "created"
            if confirmation_details.get("conference_created") or state_details.get("conference_created")
            else "failed"
            if failed and state_details.get("failure_stage") == "meet"
            else "pending"
        ),
        courier_state=courier_state,
        retry_eligible=(
            booking_email_retryable
            or (
                courier_state == "failed"
                and not (delivery_receipt and delivery_receipt.event_type == "booking_email_intent")
            )
            or stale_delivery
            or (failed and not confirmed)
        ),
        updated_at=updated_at,
    )
    if not staff:
        return BookingOutcomeEnvelope(**common)
    return BookyDecisionEnvelope(
        **common,
        action_id=action_id,
        booking_id=booking.id,
        receipt_ids=[receipt.id for receipt in receipts] + ([email_log.id] if email_is_current and email_log else []),
        configured_mode=normalize_booky_mode(latest_details.get("configured_mode")),
        effective_mode=normalize_booky_mode(latest_details.get("effective_mode")),
        recommendation=str(latest_details.get("recommendation") or "No Booky recommendation recorded."),
        explanation=str(latest_details.get("explanation") or "This booking has no Booky decision receipt."),
        confidence=str(latest_details.get("confidence") or "none"),
        policy_version=str(latest_details.get("policy_version") or get_settings().BOOKY_POLICY_VERSION),
        risk=str(latest_details.get("risk") or "none"),
        required_approval=bool(latest_details.get("required_approval", True)),
        blocked_reasons=list(latest_details.get("blocked_reasons") or []),
        available_actions=list(latest_details.get("available_actions") or []),
    )


async def _add_booky_receipt_once(
    db: AsyncSession,
    *,
    booking: Booking,
    event_type: str,
    summary: str,
    details: dict,
    provider_event_id: str,
    hold_token: str | None,
) -> None:
    existing = await db.execute(
        select(BookingLifecycleEvent.id).where(BookingLifecycleEvent.provider_event_id == provider_event_id)
    )
    if existing.scalar_one_or_none():
        return
    _add_booking_lifecycle_event(
        db,
        event_type=event_type,
        source="booky",
        booking=booking,
        hold_token=hold_token,
        summary=summary,
        details=details,
        provider_event_id=provider_event_id,
    )


async def _require_durable_booky_intent(db: AsyncSession, provider_event_id: str) -> None:
    """Refuse provider work until its deterministic intent is committed."""
    result = await db.execute(
        select(BookingLifecycleEvent.id).where(
            BookingLifecycleEvent.provider_event_id == provider_event_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise RuntimeError("Booky provider intent is not durable")


async def _persist_booky_saga_failure(
    db: AsyncSession,
    *,
    booking: Booking,
    action_id: str,
    hold_token: str | None,
    stage: str,
    attempt: int,
    error: Exception,
    conference_url: str | None,
    invitation_state: str = "not_sent",
) -> None:
    booking.status = BookingStatus.PENDING
    await _add_booky_receipt_once(
        db,
        booking=booking,
        event_type="booky_confirmation_failed" if attempt == 0 else "booky_retry_failed",
        summary=f"Booky stopped at the {stage} stage and left the inquiry pending.",
        details={
            "action_id": action_id,
            "truth_state": "failed",
            "failure_stage": stage,
            "calendar_event_id": booking.google_calendar_event_id,
            "conference_created": bool(conference_url),
            "invitation_state": invitation_state,
            "retry_eligible": True,
            "attempt": attempt,
            "error_type": type(error).__name__,
        },
        provider_event_id=f"booky:{action_id}:attempt:{attempt}:failure:{stage}",
        hold_token=hold_token,
    )
    await db.commit()


async def _booky_receipt_by_provider_id(
    db: AsyncSession,
    provider_event_id: str,
) -> BookingLifecycleEvent | None:
    result = await db.execute(
        select(BookingLifecycleEvent).where(
            BookingLifecycleEvent.provider_event_id == provider_event_id,
        )
    )
    return result.scalar_one_or_none()


async def _recover_booky_stage_commit_failure(
    db: AsyncSession,
    *,
    booking_id: UUID,
    artist: BTTCOUser,
    action_id: str,
    hold_token: str | None,
    stage: str,
    attempt: int,
    error: Exception,
    event_id: str,
    client_email: str,
    decision_details: dict,
) -> tuple[Booking, str | None]:
    """Reconcile provider success after a DB commit failure in a clean transaction."""
    await db.rollback()
    booking = await db.get(Booking, booking_id)
    if not booking:
        raise error
    provider_state = await get_booking_event_state(
        event_id,
        calendar_id=artist_calendar_id(artist),
        refresh_token=artist.google_refresh_token,
    )
    conference_url = str((provider_state or {}).get("conference_url") or "") or None
    invitation_sent = client_email.lower() in {
        str(email).lower() for email in (provider_state or {}).get("attendee_emails", [])
    }
    if provider_state:
        booking.google_calendar_event_id = str(provider_state.get("event_id") or event_id)
        recovered_type = "booky_calendar_reconciled"
        recovered_suffix = "calendar-reconciled"
        recovered_summary = "Calendar provider state was reconciled after a database commit failure."
        if stage == "invitation" and invitation_sent:
            recovered_type = "booky_invitation_reconciled"
            recovered_suffix = "invitation-reconciled"
            recovered_summary = "Calendar invitation state was reconciled after a database commit failure."
        await _add_booky_receipt_once(
            db,
            booking=booking,
            event_type=recovered_type,
            summary=recovered_summary,
            details={
                **decision_details,
                "action_id": action_id,
                "calendar_event_id": booking.google_calendar_event_id,
                "conference_created": bool(conference_url),
                "invitation_state": "sent" if invitation_sent else "not_sent",
            },
            provider_event_id=f"booky:{action_id}:{recovered_suffix}",
            hold_token=hold_token,
        )
    await _persist_booky_saga_failure(
        db,
        booking=booking,
        action_id=action_id,
        hold_token=hold_token,
        stage=f"{stage}_commit",
        attempt=attempt,
        error=error,
        conference_url=conference_url,
        invitation_state="sent" if invitation_sent else "not_sent",
    )
    return booking, conference_url


async def _run_booky_confirmation_saga(
    db: AsyncSession,
    *,
    booking: Booking,
    artist: BTTCOUser,
    conversation: Conversation,
    client_name: str,
    client_email: str,
    action_id: str,
    hold_token: str | None,
    decision_details: dict,
    attempt: int,
    intent_provider_event_id: str,
) -> tuple[bool, str | None, Message | None]:
    """Resume the durable Calendar -> invitation -> Courier confirmation saga."""
    await _require_durable_booky_intent(db, intent_provider_event_id)
    conference_url: str | None = None
    booking_id = booking.id
    deterministic_event_id = f"bttco{booking.id.hex}"
    base_payload = {
        "client_name": client_name,
        "description": booking.description,
        "reference_code": booking.reference_code,
        "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
        "scheduled_time": booking.scheduled_time,
        "duration_minutes": booking.duration_minutes or 30,
        "booking_kind": "consultation",
        "consultation_method": booking.consultation_method,
        # Threads the client's reply back into this booking's own conversation
        # instead of the alias default, which starts a thread keyed on their
        # email address beside the real one.
        "conversation_id": booking.conversation_id,
        "event_id": deterministic_event_id,
        "conference_request_id": f"bttcomeet{booking.id.hex}",
    }

    calendar_receipt_id = f"booky:{action_id}:calendar-ready"
    calendar_receipt = await _booky_receipt_by_provider_id(db, calendar_receipt_id)
    try:
        calendar_result: dict | None = None
        existing_event_id = booking.google_calendar_event_id or (
            str((calendar_receipt.details or {}).get("calendar_event_id")) if calendar_receipt else None
        )
        if existing_event_id:
            calendar_result = await get_booking_event_state(
                existing_event_id,
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
            if calendar_receipt and not calendar_result:
                raise RuntimeError("Durable Calendar receipt no longer matches provider state")
        if not calendar_result:
            calendar_result = await create_booking_event(
                base_payload,
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
        booking.google_calendar_event_id = str(calendar_result.get("event_id") or deterministic_event_id)
        conference_url = str(calendar_result.get("conference_url") or "") or None
        if booking.consultation_method == "meet" and not conference_url:
            raise RuntimeError("Google Meet link was not created")
        if not calendar_receipt:
            await _add_booky_receipt_once(
                db,
                booking=booking,
                event_type="booky_calendar_ready",
                summary="Calendar event and virtual meeting requirements are ready.",
                details={
                    **decision_details,
                    "action_id": action_id,
                    "calendar_event_id": booking.google_calendar_event_id,
                    "conference_created": bool(conference_url),
                    "attendee_added": False,
                },
                provider_event_id=calendar_receipt_id,
                hold_token=hold_token,
            )
            try:
                await db.commit()
            except Exception as commit_exc:
                await _recover_booky_stage_commit_failure(
                    db,
                    booking_id=booking_id,
                    artist=artist,
                    action_id=action_id,
                    hold_token=hold_token,
                    stage="calendar",
                    attempt=attempt,
                    error=commit_exc,
                    event_id=booking.google_calendar_event_id,
                    client_email=client_email,
                    decision_details=decision_details,
                )
                return False, conference_url, None
    except Exception as exc:
        logger.error("Booky Calendar preparation failed for booking %s: %s", booking.id, exc)
        await _persist_booky_saga_failure(
            db,
            booking=booking,
            action_id=action_id,
            hold_token=hold_token,
            stage="meet" if booking.consultation_method == "meet" and booking.google_calendar_event_id else "calendar",
            attempt=attempt,
            error=exc,
            conference_url=conference_url,
        )
        return False, conference_url, None

    invitation_receipt_id = f"booky:{action_id}:invitation"
    invitation_receipt = await _booky_receipt_by_provider_id(db, invitation_receipt_id)
    if not invitation_receipt:
        invitation_receipt = await _booky_receipt_by_provider_id(db, f"booky:{action_id}:invitation-reconciled")
    try:
        if invitation_receipt:
            provider_state = await get_booking_event_state(
                booking.google_calendar_event_id,
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
            attendee_emails = {str(email).lower() for email in (provider_state or {}).get("attendee_emails", [])}
            if not provider_state or client_email.lower() not in attendee_emails:
                raise RuntimeError("Durable invitation receipt no longer matches provider state")
            conference_url = str(provider_state.get("conference_url") or "") or conference_url
        else:
            invitation_result = await update_booking_event(
                booking.google_calendar_event_id,
                {**base_payload, "client_email": client_email},
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
            conference_url = str(invitation_result.get("conference_url") or "") or conference_url
            if booking.consultation_method == "meet" and not conference_url:
                raise RuntimeError("Google Meet link disappeared while adding the attendee")
            await _add_booky_receipt_once(
                db,
                booking=booking,
                event_type="booky_invitation_sent",
                summary="Calendar accepted the collector attendee invitation.",
                details={
                    **decision_details,
                    "action_id": action_id,
                    "calendar_event_id": booking.google_calendar_event_id,
                    "conference_created": bool(conference_url),
                    "invitation_state": "sent",
                },
                provider_event_id=invitation_receipt_id,
                hold_token=hold_token,
            )
            try:
                await db.commit()
            except Exception as commit_exc:
                await _recover_booky_stage_commit_failure(
                    db,
                    booking_id=booking_id,
                    artist=artist,
                    action_id=action_id,
                    hold_token=hold_token,
                    stage="invitation",
                    attempt=attempt,
                    error=commit_exc,
                    event_id=booking.google_calendar_event_id,
                    client_email=client_email,
                    decision_details=decision_details,
                )
                return False, conference_url, None
    except Exception as exc:
        logger.error("Booky attendee invitation failed for booking %s: %s", booking.id, exc)
        await _persist_booky_saga_failure(
            db,
            booking=booking,
            action_id=action_id,
            hold_token=hold_token,
            stage="invitation",
            attempt=attempt,
            error=exc,
            conference_url=conference_url,
            invitation_state="sent" if invitation_receipt else "not_sent",
        )
        return False, conference_url, None

    try:
        message_id = uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, f"bttco:booky:{action_id}:courier")
        courier_message = await db.get(Message, message_id)
        if not courier_message:
            courier_message = Message(
                id=message_id,
                conversation_id=conversation.id,
                sender_id=artist.id,
                content=(
                    f"Your consultation is confirmed for {booking.scheduled_date} at "
                    f"{booking.scheduled_time}. Reference {booking.reference_code}."
                ),
                metadata_json=json.dumps(
                    {
                        "channel": "courier",
                        "direction": "outbound",
                        "delivery_status": "queued",
                        "delivery_attempts": 0,
                        "delivery_updated_at": datetime.now(UTC).isoformat(),
                        "booky_generated": True,
                        "booky_delivery": True,
                        "action_id": action_id,
                        "booking_id": str(booking.id),
                        "delivery_to": client_email,
                        "client_name": client_name,
                        "artist_name": artist.name or artist.slug or "your artist",
                        "description": booking.description,
                        "placement": booking.placement,
                        "reference_code": booking.reference_code or str(booking.id),
                        "consultation_method": booking.consultation_method,
                        "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
                        "scheduled_time": booking.scheduled_time,
                        "conference_url": conference_url,
                    }
                ),
                message_type="booky_confirmation",
            )
            db.add(courier_message)
        booking.status = BookingStatus.CONFIRMED
        await db.flush()
        await _add_booky_receipt_once(
            db,
            booking=booking,
            event_type="consultation_confirmed",
            summary="Booky confirmed the consultation after Calendar and invitation evidence persisted.",
            details={
                **decision_details,
                "action_id": action_id,
                "calendar_event_id": booking.google_calendar_event_id,
                "consultation_method": booking.consultation_method,
                "conference_created": bool(conference_url),
                "conference_url": conference_url,
                "invitation_state": "sent",
            },
            provider_event_id=f"booky:{action_id}:confirmed",
            hold_token=hold_token,
        )
        await _add_booky_receipt_once(
            db,
            booking=booking,
            event_type="booky_delivery_intent",
            summary="Courier delivery intent is durable and ready for post-commit delivery.",
            details={
                **decision_details,
                "action_id": action_id,
                "courier_message_id": str(courier_message.id),
                "delivery_state": "queued",
            },
            provider_event_id=f"booky:{action_id}:courier-intent",
            hold_token=hold_token,
        )
        await db.commit()
        return True, conference_url, courier_message
    except Exception as exc:
        booking_id = booking.id
        await db.rollback()
        booking = await db.get(Booking, booking_id)
        if not booking:
            raise
        await _persist_booky_saga_failure(
            db,
            booking=booking,
            action_id=action_id,
            hold_token=hold_token,
            stage="courier",
            attempt=attempt,
            error=exc,
            conference_url=conference_url,
        )
        return False, conference_url, None


# Public helpers

_MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _generate_reference_code(prefix: str) -> str:
    """Generate a PREFIX-XXXXXX reference code with the shared 6 char suffix."""
    chars = string.ascii_uppercase + string.digits
    return f"{prefix}-" + "".join(random.choices(chars, k=6))


def _generate_booking_ref() -> str:
    """Generate a BKG-XXXXXX reference code."""
    return _generate_reference_code("BKG")


def _booking_action_urls(reference_code: str, conversation_id: UUID | str | None) -> dict[str, str]:
    """Build dashboard deep links used by booking alert emails."""
    base_url = get_settings().FRONTEND_URL.rstrip("/")
    booking_url = f"{base_url}{ROUTES.BOOKING_REF.format(ref=reference_code)}"
    message_url = (
        f"{base_url}{ROUTES.COURIER_CONVO.format(id=conversation_id)}"
        if conversation_id
        else f"{booking_url}&focus=message"
    )
    return {
        "accept_url": f"{booking_url}&action=accept",
        "message_url": message_url,
        "decline_url": f"{booking_url}&action=reject",
        "hustle_deposits_url": f"{base_url}{ROUTES.HUSTLE_DEPOSITS}",
    }


def _hustl_deposit_rule_for_duration(duration_minutes: int | None) -> tuple[float | None, str | None]:
    """Return the shop deposit fallback when no artist session rule exists."""
    if not duration_minutes or duration_minutes <= 0:
        return None, None
    if duration_minutes >= 480:
        return 300.0, "full_day"
    if duration_minutes >= 181:
        return 200.0, "extended_or_half_day"
    if duration_minutes <= 45:
        return 50.0, "45_minute_minimalist"
    return 100.0, "up_to_3_hours"


def _resolve_public_booking_deposit(
    *,
    booking_kind: str,
    session_type: ArtistSessionType | None,
    slot_hold: BookingSlotHold | None,
) -> tuple[float | None, str | None]:
    if booking_kind == "consultation":
        return None, "consultation_no_deposit"
    if session_type and session_type.deposit_amount is not None:
        return float(session_type.deposit_amount), "session_type"
    duration_minutes = slot_hold.duration_minutes if slot_hold else None
    return _hustl_deposit_rule_for_duration(duration_minutes)


async def _attach_booking_conversation(
    booking: Booking,
    db: AsyncSession,
    client_id: UUID,
    client_name: str,
) -> Conversation:
    """Create the booking's Courier thread if it does not already exist."""
    if booking.conversation_id:
        result = await db.execute(select(Conversation).where(Conversation.id == booking.conversation_id))
        existing = result.scalar_one_or_none()
        if existing:
            return existing

    conv = Conversation(
        name=client_name,
        is_group=False,
        created_by=booking.artist_id,
    )
    db.add(conv)
    await db.flush()

    db.add(ConversationParticipant(conversation_id=conv.id, user_id=booking.artist_id, role="owner"))
    if client_id != booking.artist_id:
        db.add(ConversationParticipant(conversation_id=conv.id, user_id=client_id, role="member"))
    booking.conversation_id = conv.id
    await db.flush()
    return conv


async def _ensure_booking_client_account(
    booking: Booking,
    db: AsyncSession,
) -> tuple[UUID | None, str]:
    """Ensure guest bookings resolve to a collector account when possible."""
    client_name = booking.guest_name or "Client"
    if booking.client_id:
        return booking.client_id, client_name

    if not booking.guest_email:
        return None, client_name

    client_user = await _find_or_create_collector(
        db,
        booking.guest_email,
        (booking.guest_name or "Guest").strip()[:255],
    )

    booking.client_id = client_user.id
    await db.flush()
    return client_user.id, client_user.name or client_name


async def _find_or_create_collector(
    db: AsyncSession,
    email: str,
    name: str,
    phone: str | None = None,
) -> BTTCOUser:
    """Resolve one collector, surviving concurrent inserts on normalized email."""
    user, _created = await _find_or_create_collector_with_status(db, email, name, phone)
    return user


async def _find_or_create_collector_with_status(
    db: AsyncSession,
    email: str,
    name: str,
    phone: str | None = None,
) -> tuple[BTTCOUser, bool]:
    """Resolve one collector and report whether this request created the account."""
    normalized = normalize_email(email)
    existing = await find_user_by_email(db, normalized)
    if existing:
        return existing, False

    candidate = BTTCOUser(
        email=normalized,
        password_hash=hash_password(secrets.token_urlsafe(24)),
        name=name,
        role="collector",
        must_reset_password=True,
        marketing_opt_in=False,
        phone=phone,
    )
    try:
        async with db.begin_nested():
            db.add(candidate)
            await db.flush()
        return candidate, True
    except IntegrityError:
        winner = await find_user_by_email(db, normalized)
        if not winner:
            raise
        return winner, False


async def _repair_booking_conversation(
    booking: Booking,
    db: AsyncSession,
) -> tuple[Conversation | None, UUID | None, str, list[str], str | None]:
    """
    Ensure the booking conversation exists, links both sides, and contains the intake seed.

    Returns conversation, client_id, client_name, applied repairs, and an optional skip reason.
    """
    repairs: list[str] = []
    client_id, client_name = await _ensure_booking_client_account(booking, db)

    conv: Conversation | None = None
    if booking.conversation_id:
        conv = await db.get(Conversation, booking.conversation_id)
        if not conv:
            booking.conversation_id = None
            repairs.append("reset_stale_conversation_link")

    if not booking.conversation_id:
        if not client_id:
            return None, client_id, client_name, repairs, "missing guest_email"
        conv = await _attach_booking_conversation(
            booking=booking,
            db=db,
            client_id=client_id,
            client_name=client_name,
        )
        repairs.append("created_conversation")
    else:
        conv = conv or await db.get(Conversation, booking.conversation_id)

    if conv:
        participant_result = await db.execute(
            select(ConversationParticipant.user_id).where(ConversationParticipant.conversation_id == conv.id)
        )
        participant_ids = {row[0] for row in participant_result.all()}

        if booking.artist_id not in participant_ids:
            db.add(ConversationParticipant(conversation_id=conv.id, user_id=booking.artist_id, role="owner"))
            repairs.append("added_artist_participant")

        if client_id and client_id != booking.artist_id and client_id not in participant_ids:
            db.add(ConversationParticipant(conversation_id=conv.id, user_id=client_id, role="member"))
            repairs.append("added_client_participant")

        if client_id:
            intake_before = await db.execute(
                select(Message.id)
                .where(
                    and_(
                        Message.conversation_id == conv.id,
                        Message.message_type == "booking_intake",
                    )
                )
                .limit(1)
            )
            had_intake = intake_before.scalar_one_or_none() is not None
            await _ensure_booking_intake_message(
                booking=booking,
                db=db,
                sender_id=client_id,
                client_name=client_name,
                client_email=booking.guest_email,
                client_phone=booking.guest_phone,
            )
            if not had_intake:
                repairs.append("seeded_intake_message")

    await db.flush()
    return conv, client_id, client_name, repairs, None


def _booking_intake_message_content(
    *,
    client_name: str,
    client_email: str | None,
    client_phone: str | None,
    booking: Booking,
) -> str:
    note = (booking.client_notes or "").strip() or (booking.description or "").strip()
    if note:
        return note
    return f"Booking inquiry from {client_name}"


async def _ensure_booking_intake_message(
    *,
    booking: Booking,
    db: AsyncSession,
    sender_id: UUID,
    client_name: str,
    client_email: str | None,
    client_phone: str | None,
) -> Message | None:
    """Seed the Courier thread with the booking intake summary once."""
    if not booking.conversation_id:
        return None
    result = await db.execute(
        select(Message)
        .where(
            and_(
                Message.conversation_id == booking.conversation_id,
                Message.message_type == "booking_intake",
            )
        )
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    metadata = {
        "channel": "huddle",
        "direction": "inbound",
        "booking_id": str(booking.id),
        "reference_code": booking.reference_code,
        "source": "booking_submission",
        "archived_for_protection": True,
    }
    message = Message(
        conversation_id=booking.conversation_id,
        sender_id=sender_id,
        content=_booking_intake_message_content(
            client_name=client_name,
            client_email=client_email,
            client_phone=client_phone,
            booking=booking,
        ),
        message_type="booking_intake",
        metadata_json=json.dumps(metadata),
        created_at=booking.created_at,
    )
    db.add(message)
    conv = await db.get(Conversation, booking.conversation_id)
    if conv:
        conv.updated_at = booking.created_at
    await db.flush()
    return message


def _booking_pipeline_task_body(
    *,
    client_name: str,
    client_email: str,
    client_phone: str | None,
    artist_name: str,
    description: str,
    placement: str | None,
    size_estimate: str | None,
    budget: str | None,
    timeline: str | None,
    style_preference: str | None,
    reference_code: str,
    conversation_id: UUID | str | None,
) -> str:
    action_urls = _booking_action_urls(reference_code, conversation_id)
    lines = [
        f"Client: {client_name}",
        f"Email: {client_email}",
        f"Phone: {client_phone or 'not provided'}",
        f"Artist: {artist_name}",
        f"Reference: {reference_code}",
        f"Booking: {ROUTES.BOOKING_REF.format(ref=reference_code)}",
        f"Courier: {ROUTES.COURIER_CONVO.format(id=conversation_id) if conversation_id else action_urls['message_url']}",
        f"Hustl (deposits): {action_urls['hustle_deposits_url']}",
        "",
        f"Project: {description}",
    ]
    if placement:
        lines.append(f"Placement: {placement}")
    if size_estimate:
        lines.append(f"Size: {size_estimate}")
    if style_preference:
        lines.append(f"Style: {style_preference}")
    if budget:
        lines.append(f"Budget: {budget}")
    if timeline:
        lines.append(f"Timeline: {timeline}")
    return "\n".join(lines)


async def _ensure_booking_pipeline_task(
    *,
    db: AsyncSession,
    booking: Booking,
    client_name: str,
    client_email: str,
    client_phone: str | None,
    artist_name: str,
    assignee_id: UUID,
) -> CrmPipelineTask:
    result = await db.execute(
        select(CrmPipelineTask).where(
            CrmPipelineTask.booking_id == booking.id,
            CrmPipelineTask.status == "open",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    row = CrmPipelineTask(
        title=f"Reply to booking {booking.reference_code or booking.id}",
        body=_booking_pipeline_task_body(
            client_name=client_name,
            client_email=client_email,
            client_phone=client_phone,
            artist_name=artist_name,
            description=booking.description,
            placement=booking.placement,
            size_estimate=booking.size_estimate,
            budget=booking.budget,
            timeline=booking.timeline,
            style_preference=booking.style_preference,
            reference_code=booking.reference_code or str(booking.id),
            conversation_id=booking.conversation_id,
        ),
        status="open",
        priority="high",
        booking_id=booking.id,
        assignee_id=assignee_id,
    )
    db.add(row)
    await db.flush()
    return row


async def _close_booking_pipeline_tasks(db: AsyncSession, booking_id: UUID) -> None:
    result = await db.execute(
        select(CrmPipelineTask).where(
            CrmPipelineTask.booking_id == booking_id,
            CrmPipelineTask.status == "open",
        )
    )
    now = datetime.now(UTC)
    for task in result.scalars().all():
        task.status = "done"
        task.updated_at = now


async def _sync_hustl_deposit_followup_task(
    *,
    db: AsyncSession,
    booking: Booking,
    client_name: str,
    client_email: str | None,
    artist_name: str,
    assignee_id: UUID,
) -> None:
    """
    Keep a Hustl-oriented deposit follow-up task in sync with booking state.

    Opens a task when a quoted/confirmed booking has an unpaid deposit, and
    closes matching open tasks when the deposit is paid or no longer relevant.
    """
    deposit_amount = float(booking.deposit_amount or 0)
    should_be_open = (
        booking.status in {BookingStatus.QUOTED, BookingStatus.CONFIRMED}
        and deposit_amount > 0
        and not booking.deposit_paid
    )
    ref = booking.reference_code or str(booking.id)
    title = f"Collect deposit for booking {ref}"
    now = datetime.now(UTC)
    action_urls = _booking_action_urls(ref, booking.conversation_id)
    body_lines = [
        f"Client: {client_name}",
        f"Email: {client_email or booking.guest_email or 'not provided'}",
        f"Artist: {artist_name}",
        f"Reference: {ref}",
        f"Deposit due: ${deposit_amount:,.2f}",
        f"Booking: {ROUTES.BOOKING_REF.format(ref=ref)}",
        f"Courier: {ROUTES.COURIER_CONVO.format(id=booking.conversation_id) if booking.conversation_id else action_urls['message_url']}",
        f"Hustl (deposits): {action_urls['hustle_deposits_url']}",
    ]
    body = "\n".join(body_lines)
    result = await db.execute(
        select(CrmPipelineTask).where(
            CrmPipelineTask.booking_id == booking.id,
            CrmPipelineTask.status == "open",
            CrmPipelineTask.title.like("Collect deposit for booking%"),
        )
    )
    rows = result.scalars().all()
    if should_be_open:
        primary = rows[0] if rows else None
        if primary:
            primary.title = title
            primary.body = body
            primary.priority = "high"
            primary.assignee_id = assignee_id
            primary.updated_at = now
        else:
            db.add(
                CrmPipelineTask(
                    title=title,
                    body=body,
                    status="open",
                    priority="high",
                    booking_id=booking.id,
                    assignee_id=assignee_id,
                    updated_at=now,
                )
            )
        for duplicate in rows[1:]:
            duplicate.status = "done"
            duplicate.updated_at = now
        return

    for row in rows:
        row.status = "done"
        row.updated_at = now


async def _upsert_booking_draft(
    db: AsyncSession,
    draft: PublicBookingDraft,
    *,
    status: str = "draft",
    image_url: str | None = None,
    reference_code: str | None = None,
    last_error: str | None = None,
) -> BookingDraft:
    """Create or update a recoverable public booking snapshot."""
    result = await db.execute(select(BookingDraft).where(BookingDraft.inquiry_id == draft.inquiry_id))
    row = result.scalar_one_or_none()
    now = datetime.now(UTC)
    normalized_draft_email = draft.email.lower().strip() if draft.email else None
    if not row and normalized_draft_email:
        recent_duplicate_result = await db.execute(
            select(BookingDraft)
            .where(
                BookingDraft.guest_email == normalized_draft_email,
                BookingDraft.reference_code.is_(None),
                BookingDraft.status.in_(("draft", "image_uploaded", "reminder_sent")),
                BookingDraft.updated_at >= now - timedelta(hours=24),
            )
            .order_by(desc(BookingDraft.updated_at))
            .limit(1)
        )
        row = recent_duplicate_result.scalar_one_or_none()
        if row:
            row.inquiry_id = draft.inquiry_id
    incoming_form_data = draft.model_dump(exclude={"image_urls"}, exclude_unset=True)
    existing_form_data = dict(row.form_data or {}) if row else {}
    if "email" in incoming_form_data:
        if normalized_draft_email:
            incoming_form_data["email"] = normalized_draft_email
        else:
            incoming_form_data.pop("email")
    form_data = {**existing_form_data, **incoming_form_data}

    image_urls = list(row.image_urls or []) if row else []
    for candidate_url in [*(draft.image_urls or []), image_url]:
        if candidate_url and candidate_url not in image_urls:
            image_urls.append(candidate_url)

    first_name = form_data.get("first_name")
    last_name = form_data.get("last_name")
    guest_name = " ".join(part.strip() for part in [first_name or "", last_name or ""] if part and part.strip()) or (
        row.guest_name if row else None
    )
    email = form_data.get("email")
    normalized_email = email.lower().strip() if isinstance(email, str) else None
    guest_email = normalized_email or (row.guest_email if row else None)
    guest_phone = form_data.get("phone", row.guest_phone if row else None)
    artist_slug = form_data.get("artist_slug", row.artist_slug if row else None)

    if row:
        next_status = status
        if row.status == "reminder_sent" and status in {"draft", "image_uploaded"}:
            next_status = "reminder_sent"
        row.status = next_status
        row.artist_slug = artist_slug
        row.guest_name = guest_name
        row.guest_email = guest_email
        row.guest_phone = guest_phone
        row.form_data = form_data
        row.image_urls = image_urls or row.image_urls
        row.reference_code = reference_code or row.reference_code
        row.last_error = last_error
        row.updated_at = now
        return row

    row = BookingDraft(
        inquiry_id=draft.inquiry_id,
        status=status,
        artist_slug=artist_slug,
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone,
        form_data=form_data,
        image_urls=image_urls or None,
        reference_code=reference_code,
        last_error=last_error,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    return row


# Public endpoints (no auth)


@router.post("/upload-image")
@limiter.limit("20/minute")
async def upload_booking_image(
    request: Request,
    file: UploadFile = File(...),
    draft_json: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Public: upload a reference or placement image before booking.

    Accepts JPEG, PNG, WebP up to 10 MB.
    Returns a Cloudflare R2 asset URL to attach to the booking submission.
    """
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"File type '{file.content_type}' not allowed. Use JPEG, PNG, or WebP.",
        )

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_IMAGE_SIZE:
        raise HTTPException(status_code=422, detail="File too large. Maximum 10 MB.")

    ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    ext = ext_map.get(file.content_type, "jpg")
    filename = f"{uuid_mod.uuid4()}.{ext}"

    url = await upload_file(
        file_bytes=file_bytes,
        filename=filename,
        mime_type=file.content_type,
        folder_path="bookings/uploads",
    )

    if not url:
        raise HTTPException(status_code=500, detail="Image upload failed. Please try again.")

    if draft_json:
        try:
            draft = PublicBookingDraft.model_validate(json.loads(draft_json))
            await _upsert_booking_draft(db, draft, status="image_uploaded", image_url=url)
            await db.flush()
        except Exception as exc:
            logger.warning("Booking draft snapshot failed after image upload: %s", exc)

    return {"url": url}


@router.post("/draft", response_model=PublicBookingDraftResponse)
@limiter.limit("30/minute")
async def save_public_booking_draft(
    request: Request,
    body: PublicBookingDraft,
    db: AsyncSession = Depends(get_db),
):
    """Public: persist a recoverable snapshot before final submit."""
    row = await _upsert_booking_draft(db, body, status="draft")
    await db.flush()
    return PublicBookingDraftResponse(inquiry_id=row.inquiry_id, status=row.status)


@router.get("/drafts", response_model=BookingDraftListResponse)
async def list_booking_drafts(
    status_filter: str | None = Query(None, alias="status"),
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Staff: list recoverable public booking drafts.

    Artists see drafts routed to their slug and general inquiries. Assistants,
    admins, and founders see the whole shop recovery queue.
    """
    _require_role(current_user, "assistant")

    filters = []
    current_rank = ROLE_RANK.get(current_user.role, 0)
    is_manager = current_rank >= ROLE_RANK.get("admin", 3) or current_user.role == "assistant"
    if not is_manager:
        filters.append(or_(BookingDraft.artist_slug == current_user.slug, BookingDraft.artist_slug == "any"))
    if status_filter:
        filters.append(BookingDraft.status == status_filter)

    where_clause = and_(*filters) if filters else True
    total_result = await db.execute(select(func.count()).select_from(BookingDraft).where(where_clause))
    total = total_result.scalar() or 0
    result = await db.execute(
        select(BookingDraft).where(where_clause).order_by(desc(BookingDraft.updated_at)).offset(skip).limit(limit)
    )
    drafts = result.scalars().all()
    return BookingDraftListResponse(
        drafts=[BookingDraftRecord.model_validate(draft) for draft in drafts],
        total=total,
    )


@router.post("/backfill-conversations")
async def backfill_booking_conversations(
    limit: int = Query(default=100, le=500),
    repair_existing: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Admin: backfill Courier threads for bookings missing conversation_id.

    Idempotent for bookings that already have a conversation. Guest bookings
    get a pending collector account so Courier can address a real participant.
    """
    _require_role(current_user, "admin")

    query = select(Booking).order_by(Booking.created_at)
    if not repair_existing:
        query = query.where(Booking.conversation_id.is_(None))
    result = await db.execute(query.limit(limit))
    bookings = result.scalars().all()
    fixed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for booking in bookings:
        conv, _client_id, _client_name, repairs, skip_reason = await _repair_booking_conversation(booking, db)
        if skip_reason or not conv:
            skipped.append({"booking_id": str(booking.id), "reason": skip_reason or "conversation_unavailable"})
            continue
        if not repairs and repair_existing:
            continue
        fixed.append(
            {
                "booking_id": str(booking.id),
                "reference_code": booking.reference_code or "",
                "conversation_id": str(conv.id),
                "repairs": ", ".join(repairs) if repairs else "already_healthy",
            }
        )

    await db.flush()
    return {"fixed": fixed, "skipped": skipped, "total": len(bookings)}


@router.post("/backfill-courier-intake-messages")
async def backfill_booking_intake_messages(
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Admin: seed missing Courier intake messages for existing booking threads.

    This preserves the booking-to-Courier link while making legacy empty
    threads useful for staff review and future Nexus/AInbox ingestion.
    """
    _require_role(current_user, "admin")

    result = await db.execute(
        select(Booking).where(Booking.conversation_id.is_not(None)).order_by(desc(Booking.created_at)).limit(limit)
    )
    bookings = result.scalars().all()
    fixed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for booking in bookings:
        sender_id = booking.client_id
        if not sender_id:
            if not booking.guest_email:
                skipped.append({"booking_id": str(booking.id), "reason": "missing guest_email"})
                continue
            client_user = await _find_or_create_collector(
                db,
                booking.guest_email,
                (booking.guest_name or "Guest").strip()[:255],
            )
            booking.client_id = client_user.id
            sender_id = client_user.id

        message = await _ensure_booking_intake_message(
            booking=booking,
            db=db,
            sender_id=sender_id,
            client_name=(booking.guest_name or "Client").strip(),
            client_email=booking.guest_email,
            client_phone=booking.guest_phone,
        )
        if message:
            fixed.append(
                {
                    "booking_id": str(booking.id),
                    "reference_code": booking.reference_code or "",
                    "conversation_id": str(booking.conversation_id),
                    "message_id": str(message.id),
                }
            )

    await db.flush()
    return {"fixed": fixed, "skipped": skipped, "total": len(bookings)}


def _booky_settings_response(artist: BTTCOUser) -> BookySettingsResponse:
    settings = get_settings()
    return BookySettingsResponse(
        artist_id=artist.id,
        artist_slug=artist.slug or str(artist.id),
        mode=normalize_booky_mode(artist.booky_mode),
        opted_in_at=artist.booky_opted_in_at,
        automation_enabled=settings.BOOKY_AUTOMATION_ENABLED,
        policy_version=settings.BOOKY_POLICY_VERSION,
        artist_canary_allowed=artist_canary_allowed(settings, artist),
        collector_canary_configured=bool(settings.BOOKY_CANARY_COLLECTOR_IDS.strip()),
    )


@router.post("/booky/capability", response_model=BookyCapabilityResponse)
async def create_booky_capability(
    body: BookyCapabilityRequest,
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Create an inquiry-scoped capability for an authenticated collector."""
    now = datetime.now(UTC)
    return BookyCapabilityResponse(
        capability=create_booky_capability_token(current_user.id, body.inquiry_id),
        inquiry_id=body.inquiry_id,
        collector_id=current_user.id,
        expires_at=now + timedelta(minutes=BOOKY_CAPABILITY_EXPIRE_MINUTES),
        policy_version=get_settings().BOOKY_POLICY_VERSION,
    )


def _collector_identity_verified(
    capability_collector_id: UUID | None,
    resolved_collector_id: UUID,
) -> bool:
    """Bind public automation authority to the account resolved from form email."""
    if capability_collector_id is None:
        return False
    if capability_collector_id != resolved_collector_id:
        raise HTTPException(status_code=403, detail="Booky capability does not match this collector account")
    return True


def _verify_existing_inquiry_capability(
    capability_collector_id: UUID | None,
    booking: Booking | None,
) -> bool:
    """Prevent an authenticated account from replaying another inquiry."""
    if capability_collector_id is None:
        return False
    if not booking or capability_collector_id != booking.client_id:
        raise HTTPException(
            status_code=403,
            detail="Booky capability does not match the submitted inquiry account",
        )
    return True


@router.get("/booky/settings/{artist_id}", response_model=BookySettingsResponse)
async def get_booky_settings(
    artist_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    artist = await db.get(BTTCOUser, artist_id)
    if not artist or ROLE_RANK.get(artist.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=404, detail="Artist not found")
    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    if not is_admin and current_user.id != artist.id:
        raise HTTPException(status_code=403, detail="Only the artist or founder/admin can view Booky settings")
    return _booky_settings_response(artist)


@router.patch("/booky/settings/{artist_id}", response_model=BookySettingsResponse)
async def update_booky_settings(
    artist_id: UUID,
    body: BookySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    artist = await db.get(BTTCOUser, artist_id)
    if not artist or ROLE_RANK.get(artist.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=404, detail="Artist not found")
    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    if not is_admin and current_user.id != artist.id:
        raise HTTPException(status_code=403, detail="Only the artist or founder/admin can change Booky settings")
    if body.mode == "session_auto":
        raise HTTPException(status_code=422, detail="session_auto is reserved and not available")
    current_mode = normalize_booky_mode(artist.booky_mode)
    if body.mode == current_mode:
        return _booky_settings_response(artist)
    allowed_transitions = {
        "manual": {"shadow"},
        "shadow": {"manual", "approval"},
        "approval": {"shadow", "consult_auto"},
        "consult_auto": {"approval"},
        "session_auto": {"manual"},
    }
    if body.mode not in allowed_transitions[current_mode]:
        raise HTTPException(
            status_code=409,
            detail=f"Booky mode must transition sequentially from {current_mode}",
        )

    changed_at = datetime.now(UTC)
    artist.booky_mode = body.mode
    if body.mode != "manual" and artist.booky_opted_in_at is None:
        artist.booky_opted_in_at = changed_at
    transition_id = booky_action_id(
        artist_id=artist.id,
        inquiry_id=f"mode:{current_mode}:{body.mode}:{changed_at.isoformat()}",
        hold_token=None,
        policy_version=get_settings().BOOKY_POLICY_VERSION,
    )
    db.add(
        BookingLifecycleEvent(
            booking_id=None,
            artist_id=artist.id,
            actor_id=current_user.id,
            event_type="booky_mode_transition",
            source="booky",
            provider_event_id=f"booky:{transition_id}:mode",
            summary=f"Booky mode changed from {current_mode} to {body.mode}.",
            details={
                "from_mode": current_mode,
                "to_mode": body.mode,
                "policy_version": get_settings().BOOKY_POLICY_VERSION,
                "initial_opt_in_at": artist.booky_opted_in_at.isoformat() if artist.booky_opted_in_at else None,
                "consent_evidence_owner": "bttco_bookings",
                "consent_evidence_retained": True,
            },
        )
    )
    await db.commit()
    await db.refresh(artist)
    return _booky_settings_response(artist)


@router.get(
    "/{booking_id}/booky",
    response_model=BookyDecisionEnvelope | BookingOutcomeEnvelope,
)
async def get_booky_projection(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    booking = await db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    is_artist = current_user.id == booking.artist_id
    is_collector = current_user.id == booking.client_id
    if not (is_admin or is_artist or is_collector):
        raise HTTPException(status_code=403, detail="Not authorized to view this booking")
    return await _booky_projection(booking, db, staff=is_admin or is_artist)


async def _booky_retry_blockers(
    db: AsyncSession,
    *,
    booking: Booking,
    artist: BTTCOUser,
) -> list[str]:
    if not booking.scheduled_date or not booking.scheduled_time:
        return ["scheduled_slot_missing"]
    try:
        busy_blocks = await get_availability(
            start_date=booking.scheduled_date.isoformat(),
            end_date=booking.scheduled_date.isoformat(),
            calendar_id=artist_calendar_id(artist),
            refresh_token=artist.google_refresh_token,
        )
        if booking.google_calendar_event_id:
            own_block = await get_booking_event_busy_block(
                booking.google_calendar_event_id,
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
            own_start = _parse_busy_datetime(str((own_block or {}).get("start") or ""))
            own_end = _parse_busy_datetime(str((own_block or {}).get("end") or ""))
            if own_start and own_end:
                busy_blocks = [
                    block
                    for block in busy_blocks
                    if (
                        _parse_busy_datetime(str(block.get("start") or "")),
                        _parse_busy_datetime(str(block.get("end") or "")),
                    )
                    != (own_start, own_end)
                ]
    except Exception as exc:
        logger.error("Booky retry availability failed closed for booking %s: %s", booking.id, exc)
        return ["calendar_unavailable"]

    _, buffer_minutes, minimum_notice_hours, max_advance_days, allow_same_day = _artist_booking_rules(artist)
    return await _schedule_blocking_reasons(
        db,
        artist_id=artist.id,
        scheduled_date=booking.scheduled_date,
        scheduled_time=booking.scheduled_time,
        duration_minutes=booking.duration_minutes or 30,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
        busy_blocks=busy_blocks,
        exclude_booking_id=booking.id,
    )


async def _booky_delivery_message(db: AsyncSession, booking_id: UUID) -> Message | None:
    receipt_result = await db.execute(
        select(BookingLifecycleEvent)
        .where(
            BookingLifecycleEvent.booking_id == booking_id,
            BookingLifecycleEvent.event_type.in_({"booky_delivery_intent", "booky_delivery_queued"}),
        )
        .order_by(BookingLifecycleEvent.created_at.desc(), BookingLifecycleEvent.id.desc())
        .limit(1)
    )
    receipt = receipt_result.scalar_one_or_none()
    try:
        message_id = UUID(str((receipt.details or {}).get("courier_message_id"))) if receipt else None
    except (TypeError, ValueError):
        return None
    if not message_id:
        return None
    message_result = await db.execute(
        select(Message).where(Message.id == message_id).execution_options(populate_existing=True).with_for_update()
    )
    return message_result.scalar_one_or_none()


async def _claim_booky_retry_lease(
    db: AsyncSession,
    *,
    booking: Booking,
    action_id: str,
    hold_token: str | None,
) -> tuple[int, str]:
    """Claim one append-only retry number while the booking row is locked."""
    result = await db.execute(
        select(BookingLifecycleEvent)
        .where(
            BookingLifecycleEvent.booking_id == booking.id,
            BookingLifecycleEvent.source == "booky",
            BookingLifecycleEvent.event_type.in_(
                {"booky_retry_started", "booky_retry_completed", "booky_retry_failed"}
            ),
        )
        .order_by(BookingLifecycleEvent.created_at, BookingLifecycleEvent.id)
    )
    events = list(result.scalars().all())
    started: dict[int, BookingLifecycleEvent] = {}
    terminal: set[int] = set()
    for event in events:
        details = event.details or {}
        number = details.get("retry_number", details.get("attempt"))
        try:
            retry_number = int(number)
        except (TypeError, ValueError):
            continue
        if event.event_type == "booky_retry_started":
            started[retry_number] = event
        else:
            terminal.add(retry_number)
    active = [event for number, event in started.items() if number not in terminal]
    if active:
        latest = active[-1]
        created_at = latest.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at > datetime.now(UTC) - timedelta(minutes=10):
            raise HTTPException(status_code=409, detail="A Booky retry is already in progress")
    retry_number = max(started, default=0) + 1
    provider_event_id = f"booky:{action_id}:retry:{retry_number}:started"
    await _add_booky_receipt_once(
        db,
        booking=booking,
        event_type="booky_retry_started",
        summary="Staff claimed the Booky provider retry lease.",
        details={"action_id": action_id, "retry_number": retry_number, "lease_state": "in_progress"},
        provider_event_id=provider_event_id,
        hold_token=hold_token,
    )
    await db.commit()
    return retry_number, provider_event_id


@router.post("/{booking_id}/booky/retry", response_model=BookyDecisionEnvelope)
async def retry_booky_confirmation(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    booking_result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = booking_result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)
    if booking.booking_kind != "consultation":
        raise HTTPException(status_code=409, detail="Only a Booky consultation can be retried")

    if booking.status == BookingStatus.CONFIRMED:
        projection = await _booky_projection(booking, db, staff=True)
        courier_message = await _booky_delivery_message(db, booking.id)
        if not courier_message:
            raise HTTPException(status_code=409, detail="Courier delivery evidence is incomplete")
        metadata = json.loads(courier_message.metadata_json or "{}")
        if not metadata.get("delivery_to"):
            raise HTTPException(status_code=409, detail="Courier delivery evidence is incomplete")
        current_delivery_status = str(metadata.get("delivery_status") or "queued")
        if current_delivery_status in {"sent", "delivered", "opened", "clicked"}:
            return await _booky_projection(booking, db, staff=True)
        raw_updated_at = metadata.get("delivery_updated_at")
        try:
            delivery_updated_at = datetime.fromisoformat(str(raw_updated_at))
            if delivery_updated_at.tzinfo is None:
                delivery_updated_at = delivery_updated_at.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            delivery_updated_at = datetime.now(UTC)
        stale_before = datetime.now(UTC) - timedelta(minutes=BOOKY_COURIER_STALE_MINUTES)
        if current_delivery_status == "sending":
            if delivery_updated_at > stale_before:
                return await _booky_projection(booking, db, staff=True)
            # The existing worker owns stale-attempt reconciliation and may only
            # reclaim after checking attempt-bound EmailLog evidence.
            await db.commit()
            await process_booky_courier_outbox_once(message_id=courier_message.id)
            await db.refresh(courier_message)
            return await _booky_projection(booking, db, staff=True)
        if current_delivery_status == "queued" and delivery_updated_at > stale_before:
            raise HTTPException(status_code=409, detail="Confirmed booking delivery is not recoverable")
        if current_delivery_status not in {"failed", "bounced", "complained", "queued"}:
            raise HTTPException(status_code=409, detail="Confirmed booking delivery is not recoverable")
        delivery_attempt = int(metadata.get("delivery_attempts") or 0) + 1
        existing_staff_recovery_attempts = int(metadata.get("delivery_staff_recovery_attempts") or 0)
        staff_override_pending = metadata.get("delivery_staff_override") is True
        staff_recovery_attempt = (
            existing_staff_recovery_attempts
            if staff_override_pending and existing_staff_recovery_attempts > 0
            else existing_staff_recovery_attempts + 1
        )
        if staff_recovery_attempt > BOOKY_COURIER_STAFF_RECOVERY_MAX_ATTEMPTS:
            raise HTTPException(status_code=409, detail="Courier staff recovery limit reached")
        metadata["delivery_status"] = "queued"
        metadata["delivery_staff_override"] = True
        metadata["delivery_staff_recovery_attempts"] = staff_recovery_attempt
        metadata["delivery_next_attempt_at"] = datetime.now(UTC).isoformat()
        metadata["delivery_updated_at"] = datetime.now(UTC).isoformat()
        courier_message.metadata_json = json.dumps(metadata)
        await _add_booky_receipt_once(
            db,
            booking=booking,
            event_type="booky_delivery_retry_requested",
            summary="Staff queued durable Courier delivery recovery for the confirmed consultation.",
            details={
                "action_id": projection.action_id,
                "courier_message_id": str(courier_message.id),
                "delivery_attempt": delivery_attempt,
                "staff_recovery_attempt": staff_recovery_attempt,
            },
            provider_event_id=(f"booky:{projection.action_id}:delivery-recovery:{staff_recovery_attempt}:requested"),
            hold_token=None,
        )
        await db.commit()
        await process_booky_courier_outbox_once(message_id=courier_message.id)
        await db.refresh(courier_message)
        return await _booky_projection(booking, db, staff=True)

    booking_result = await db.execute(
        select(Booking).where(Booking.id == booking_id).execution_options(populate_existing=True).with_for_update()
    )
    booking = booking_result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)
    if booking.booking_kind != "consultation":
        raise HTTPException(status_code=409, detail="Only a Booky consultation can be retried")
    if booking.status != BookingStatus.PENDING:
        raise HTTPException(status_code=409, detail="Only a pending confirmation or failed delivery can be retried")

    artist = await db.get(BTTCOUser, booking.artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    client_name, client_email = await _booking_contact(db, booking)

    hold_result = await db.execute(
        select(BookingSlotHold).where(
            BookingSlotHold.booking_id == booking.id,
            BookingSlotHold.status == "consumed",
        )
    )
    slot_hold = hold_result.scalar_one_or_none()
    retry_hold_token = slot_hold.hold_token if slot_hold else None
    blockers = await _booky_retry_blockers(db, booking=booking, artist=artist)

    settings = get_settings()
    decision = resolve_booky_policy(
        settings=settings,
        artist=artist,
        booking_kind=booking.booking_kind,
        collector_id=booking.client_id,
        collector_identity_verified=bool(booking.client_id and booking.booky_collector_verified_at),
        consent_granted=booking.booky_consent,
        consent_policy_version=booking.booky_consent_policy_version,
        slot_hold_verified=not blockers,
        calendar_connected=artist_calendar_connected(artist),
    )
    if blockers:
        retry_count_result = await db.execute(
            select(func.count(BookingLifecycleEvent.id)).where(
                BookingLifecycleEvent.booking_id == booking.id,
                BookingLifecycleEvent.event_type == "booky_retry_failed",
            )
        )
        retry_number = int(retry_count_result.scalar_one() or 0) + 1
        action_id = booky_action_id(
            artist_id=artist.id,
            inquiry_id=booking.booky_capability_inquiry_id or str(booking.id),
            hold_token=retry_hold_token,
            policy_version=settings.BOOKY_POLICY_VERSION,
        )
        await _add_booky_receipt_once(
            db,
            booking=booking,
            event_type="booky_retry_failed",
            summary="Current availability no longer permits this consultation confirmation.",
            details={
                "action_id": action_id,
                "truth_state": "failed",
                "failure_stage": "availability",
                "blocked_reasons": blockers,
                "retry_eligible": True,
            },
            provider_event_id=f"booky:{action_id}:availability-retry:{retry_number}",
            hold_token=retry_hold_token,
        )
        await db.commit()
        return await _booky_projection(booking, db, staff=True)
    if not decision.can_auto_confirm:
        raise HTTPException(status_code=409, detail=f"Booky retry blocked: {', '.join(decision.blocked_reasons)}")

    decision_receipt_result = await db.execute(
        select(BookingLifecycleEvent)
        .where(
            BookingLifecycleEvent.booking_id == booking.id,
            BookingLifecycleEvent.source == "booky",
            BookingLifecycleEvent.event_type.in_({"booky_recommendation", "booky_blocked"}),
        )
        .order_by(BookingLifecycleEvent.created_at.desc(), BookingLifecycleEvent.id.desc())
        .limit(1)
    )
    decision_receipt = decision_receipt_result.scalar_one_or_none()
    action_id = str(
        ((decision_receipt.details or {}).get("action_id") if decision_receipt else None)
        or booky_action_id(
            artist_id=artist.id,
            inquiry_id=str(booking.id),
            hold_token=retry_hold_token,
            policy_version=settings.BOOKY_POLICY_VERSION,
        )
    )
    if not client_email:
        raise HTTPException(status_code=409, detail="Collector email is unavailable")
    conversation = await db.get(Conversation, booking.conversation_id) if booking.conversation_id else None
    if not conversation:
        client_id, resolved_client_name = await _ensure_booking_client_account(booking, db)
        if not client_id:
            raise HTTPException(status_code=409, detail="Courier client account is unavailable")
        conversation = await _attach_booking_conversation(
            booking=booking,
            db=db,
            client_id=client_id,
            client_name=resolved_client_name,
        )
    retry_number, retry_intent_provider_event_id = await _claim_booky_retry_lease(
        db,
        booking=booking,
        action_id=action_id,
        hold_token=retry_hold_token,
    )
    confirmed, conference_url, courier_message = await _run_booky_confirmation_saga(
        db,
        booking=booking,
        artist=artist,
        conversation=conversation,
        client_name=client_name,
        client_email=client_email,
        action_id=action_id,
        hold_token=retry_hold_token,
        decision_details={
            "configured_mode": decision.configured_mode,
            "effective_mode": decision.effective_mode,
            "policy_version": decision.policy_version,
        },
        attempt=retry_number,
        intent_provider_event_id=retry_intent_provider_event_id,
    )
    if confirmed and courier_message:
        await _add_booky_receipt_once(
            db,
            booking=booking,
            event_type="booky_retry_completed",
            summary="Booky provider retry completed and durable Courier delivery is queued.",
            details={"action_id": action_id, "retry_number": retry_number},
            provider_event_id=f"booky:{action_id}:retry:{retry_number}:completed",
            hold_token=retry_hold_token,
        )
        await db.commit()
        await process_booky_courier_outbox_once(message_id=courier_message.id)
    return await _booky_projection(booking, db, staff=True)


@router.post("/submit", response_model=PublicBookingResponse)
@limiter.limit("5/minute")
async def submit_public_booking(
    request: Request,
    body: PublicBookingSubmit,
    db: AsyncSession = Depends(get_db),
):
    """
    Public: submit a booking request without an account.

    Resolves artist_slug to artist UUID, creates a guest booking row,
    fires confirmation + artist notification emails in the background.
    """
    try:
        return await _do_submit_public_booking(body, db)
    except HTTPException:
        raise
    except Exception as exc:
        correlation_id = f"booking-{uuid_mod.uuid4().hex}"
        logger.exception("Public booking submission failed correlation_id=%s", correlation_id)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "booking_submission_failed",
                "message": "We could not submit this booking right now. Please try again.",
                "retryable": True,
                "correlation_id": correlation_id,
            },
        ) from exc


@router.post("/inquiry", response_model=PublicInquiryResponse)
@limiter.limit("5/minute")
async def submit_public_inquiry(
    request: Request,
    body: PublicInquirySubmit,
    db: AsyncSession = Depends(get_db),
):
    """
    Public: general (non tattoo) inquiry from the booking flow's Artist step.

    A general inquiry is not a booking. It opens a Courier thread with the
    collector's message as the first inbound message, alerts staff the way an
    inbound Courier message does, and returns an INQ reference. No booking row,
    hold, deposit or Booky decision is created.
    """
    try:
        return await _do_submit_public_inquiry(body, db)
    except HTTPException:
        raise
    except Exception as exc:
        correlation_id = f"inquiry-{uuid_mod.uuid4().hex}"
        logger.exception("Public inquiry submission failed correlation_id=%s", correlation_id)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "inquiry_submission_failed",
                "message": "We could not send this inquiry right now. Please try again.",
                "retryable": True,
                "correlation_id": correlation_id,
            },
        ) from exc


async def _resolve_inquiry_staff(db: AsyncSession, artist_slug: str | None) -> tuple[list[BTTCOUser], BTTCOUser | None]:
    """Return the staff accounts an inquiry thread belongs to, plus the artist when one was named.

    Conversations carry no artist column; ownership is participant identity.
    "any" or no slug means the shop queue: every founder and admin account,
    founder first, which is the same manager set booking alerts fan out to.
    """
    slug = (artist_slug or "").strip().lower()
    if slug in {"", "any"}:
        result = await db.execute(
            select(BTTCOUser).where(BTTCOUser.role.in_(["founder", "admin"])).order_by(BTTCOUser.created_at)
        )
        managers = list(result.scalars().all())
        managers.sort(key=lambda user: 0 if user.role == "founder" else 1)
        if not managers:
            raise HTTPException(status_code=503, detail="The shop inbox is not available right now")
        return managers, None

    result = await db.execute(
        select(BTTCOUser).where(BTTCOUser.slug == slug).where(BTTCOUser.role.in_(["artist", "admin", "founder"]))
    )
    artist = result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    return [artist], artist


def _spawn_post_commit_task(coro, *, label: str) -> asyncio.Task:
    """Fire and forget work after commit; a failure is logged, never raised into the request."""
    task = asyncio.create_task(coro)

    def _log_outcome(done: asyncio.Task) -> None:
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            logger.warning("Post-commit %s task failed: %s", label, type(exc).__name__)

    task.add_done_callback(_log_outcome)
    return task


async def _find_accepted_inquiry(db: AsyncSession, inquiry_id: str) -> BookingDraft | None:
    result = await db.execute(
        select(BookingDraft).where(
            BookingDraft.inquiry_id == inquiry_id,
            BookingDraft.status == INQUIRY_DRAFT_STATUS,
            BookingDraft.reference_code.is_not(None),
        )
    )
    return result.scalar_one_or_none()


def _inquiry_conversation_id(row: BookingDraft) -> UUID | None:
    raw = (row.form_data or {}).get("conversation_id")
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def _do_submit_public_inquiry(
    body: PublicInquirySubmit,
    db: AsyncSession,
) -> PublicInquiryResponse:
    if body.inquiry_id:
        accepted = await _find_accepted_inquiry(db, body.inquiry_id)
        if accepted and accepted.reference_code:
            return PublicInquiryResponse(
                reference_code=accepted.reference_code,
                conversation_id=_inquiry_conversation_id(accepted),
                message="We already have this one. We will reply by email.",
                idempotent_acknowledgement=True,
            )

    staff, artist = await _resolve_inquiry_staff(db, body.artist_slug)
    artist_slug = artist.slug if artist else None

    # Reference code (retry on collision), same generator as BKG references.
    ref_code = _generate_reference_code(INQUIRY_REFERENCE_PREFIX)
    for _ in range(5):
        existing = await db.execute(select(BookingDraft.id).where(BookingDraft.reference_code == ref_code))
        if not existing.scalar_one_or_none():
            break
        ref_code = _generate_reference_code(INQUIRY_REFERENCE_PREFIX)

    # Resolve or auto-create the collector exactly like public booking submit.
    client_name = f"{body.first_name} {body.last_name}".strip()
    client_user = await _find_or_create_collector(db, body.email, client_name, body.phone)
    if body.phone and not client_user.phone:
        client_user.phone = body.phone

    sms_opted_in_at = datetime.now(UTC) if body.sms_opt_in and body.phone else None
    if sms_opted_in_at:
        client_user.phone = body.phone
        client_user.sms_opt_in = True
        client_user.sms_opt_in_at = sms_opted_in_at
        client_user.sms_opt_out_at = None

    participant_ids = {member.id for member in staff} | {client_user.id}
    if len(participant_ids) < 2:
        raise HTTPException(status_code=422, detail="Staff accounts message the shop from the portal Courier")

    now = datetime.now(UTC)
    thread_name = f"General inquiry: {client_name}"
    conv, _created = await find_or_create_direct_conversation(
        db,
        participant_ids,
        name=thread_name,
        created_by=staff[0].id,
    )
    conv.updated_at = now

    message_text = body.message.strip()
    metadata = {
        "channel": "huddle",
        "direction": "inbound",
        "source": "general_inquiry",
        "reference_code": ref_code,
        "inquiry_id": body.inquiry_id,
        "artist_slug": artist_slug,
        "client_email": normalize_email(body.email),
        "client_phone": body.phone,
        "sms_opt_in": bool(sms_opted_in_at),
    }
    message = Message(
        conversation_id=conv.id,
        sender_id=client_user.id,
        content=f"General inquiry {ref_code}\n\n{message_text}",
        message_type="text",
        metadata_json=json.dumps(metadata),
        created_at=now,
    )
    db.add(message)
    await db.flush()

    _add_booking_lifecycle_event(
        db,
        event_type="general_inquiry_submitted",
        source="public_inquiry",
        artist_id=artist.id if artist else None,
        actor_id=client_user.id,
        guest_email=normalize_email(body.email),
        summary=f"General inquiry {ref_code} submitted by {client_name}.",
        details={
            "reference_code": ref_code,
            "conversation_id": str(conv.id),
            "message_id": str(message.id),
            "inquiry_id": body.inquiry_id,
            "artist_slug": artist_slug,
            "shop_queue": artist is None,
            "sms_opt_in": bool(sms_opted_in_at),
        },
    )

    # Staff alerts: the same in-app, push, email and SMS hooks an inbound
    # Courier message uses in conversations.send_message.
    recipient_ids = sorted(participant_ids - {client_user.id}, key=str)
    inquiry_action_url = ROUTES.COURIER_CONVO.format(id=conv.id)
    alert_title = "New General Inquiry"
    alert_body = (
        f"{client_name} sent a general inquiry for {artist.name or artist_slug} ({ref_code})."
        if artist
        else f"{client_name} sent a general inquiry to the shop ({ref_code})."
    )
    for rid in recipient_ids:
        db.add(
            NotificationModel(
                user_id=rid,
                title=alert_title,
                body=alert_body,
                notification_type="message",
                action_url=inquiry_action_url,
            )
        )
        if await should_notify(db, rid, "push", "messages"):
            await send_push_notification(
                db,
                rid,
                title=alert_title,
                body=alert_body,
                url=inquiry_action_url,
            )
    await _queue_conversation_notifications(
        db,
        message=message,
        sender=client_user,
        conversation=conv,
        recipient_ids=recipient_ids,
    )
    await db.flush()

    if body.inquiry_id:
        draft_row = await _upsert_booking_draft(
            db,
            PublicBookingDraft(
                inquiry_id=body.inquiry_id,
                artist_slug=body.artist_slug,
                first_name=body.first_name,
                last_name=body.last_name,
                email=body.email,
                phone=body.phone,
                sms_opt_in=body.sms_opt_in,
                description=message_text,
            ),
            status=INQUIRY_DRAFT_STATUS,
            reference_code=ref_code,
        )
        draft_row.form_data = {
            **(draft_row.form_data or {}),
            "kind": "general_inquiry",
            "conversation_id": str(conv.id),
            "message_id": str(message.id),
        }

    await db.commit()
    _spawn_post_commit_task(
        process_courier_notification_outbox_once(message_id=message.id),
        label="inquiry courier notification",
    )
    _spawn_post_commit_task(
        mirror_general_inquiry(
            {
                "reference_code": ref_code,
                "conversation_id": str(conv.id),
                "artist_slug": artist_slug,
                "name": client_name,
                "email": normalize_email(body.email),
                "phone": body.phone,
                "message": message_text,
                "received_at": now.isoformat(),
            }
        ),
        label="inquiry AInbox mirror",
    )

    return PublicInquiryResponse(
        reference_code=ref_code,
        conversation_id=conv.id,
        message="Got it. We will reply by email.",
    )


async def _create_public_booking_checkout(
    db: AsyncSession,
    *,
    booking: Booking,
    artist: BTTCOUser,
    slot_hold: BookingSlotHold | None,
    client_email: str,
    deposit_amount: float,
    save_payment_method: bool,
    staged_deposit: bool = False,
) -> str:
    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe checkout is not configured")
    if settings.LOCAL_DEV_MODE and settings.STRIPE_SECRET_KEY.startswith("sk_live"):
        raise HTTPException(status_code=503, detail="Live Stripe checkout is disabled in local dev")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    service_price_cents = int(round(deposit_amount * 100))
    if service_price_cents <= 0:
        raise HTTPException(status_code=422, detail="Checkout amount must be greater than zero")
    deposit_cents = customer_price_cents(service_price_cents)

    routing = resolve_charge_routing(
        artist,
        deposit_cents,
        artist_share_base_cents=service_price_cents,
    )

    params: dict[str, object] = {
        "mode": "payment",
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Tattoo Deposit - {artist.name or 'Ryozan Studio'}",
                        "description": (
                            "Authorization held while your artist reviews your flash"
                            if staged_deposit
                            else f"Deposit for {slot_hold.scheduled_date} at {slot_hold.scheduled_time}"
                        ),
                    },
                    "unit_amount": deposit_cents,
                },
                "quantity": 1,
            }
        ],
        "success_url": f"{settings.FRONTEND_URL}/book?checkout=success&ref={booking.reference_code}",
        "cancel_url": f"{settings.FRONTEND_URL}/book?checkout=cancelled&ref={booking.reference_code}",
        "metadata": {
            "checkout_type": "public_booking_deposit",
            "booking_id": str(booking.id),
            "reference_code": booking.reference_code or "",
            "slot_hold_token": slot_hold.hold_token if slot_hold else "",
            "artist_id": str(artist.id),
            "artist_slug": artist.slug or "",
            "staged_deposit": "true" if staged_deposit else "false",
        },
    }
    routing.apply_to(params)
    payment_metadata = {
        "checkout_type": "public_booking_deposit",
        "booking_id": str(booking.id),
        "reference_code": booking.reference_code or "",
        "artist_id": str(artist.id),
        "routing_destination": routing.destination,
        "routing_reason": routing.reason,
        "staged_deposit": "true" if staged_deposit else "false",
        **routing.receipt_metadata(),
    }
    apply_customer_receipt(
        params,
        customer_email=client_email,
        description=(
            f"Ryozan Studio deposit for booking {booking.reference_code or booking.id}, "
            + (
                f" pending review by {artist.name or 'your artist'}"
                if staged_deposit
                else f"{slot_hold.scheduled_date} at {slot_hold.scheduled_time} with {artist.name or 'your artist'}"
            )
        ),
        metadata=payment_metadata,
    )
    if staged_deposit:
        params["payment_method_types"] = ["card"]
        params["payment_intent_data"]["capture_method"] = "manual"
    if save_payment_method:
        params["customer_creation"] = "always"
        params["payment_intent_data"]["setup_future_usage"] = "off_session"

    try:
        session = get_stripe_client().v1.checkout.sessions.create(
            params,
            routing.request_options(
                idempotency_key=(
                    f"public-booking-staged-deposit-{booking.id}"
                    if staged_deposit
                    else f"public-booking-deposit-{slot_hold.hold_token}"
                )
            ),
        )
    except Exception as exc:
        logger.error("Stripe public booking checkout failed for %s: %s", booking.id, exc)
        raise HTTPException(status_code=502, detail="Could not create Stripe checkout") from exc

    if not session.url:
        logger.error("Stripe public booking checkout returned no URL for %s", booking.id)
        raise HTTPException(status_code=502, detail="Stripe checkout did not return a redirect URL")

    booking.stripe_checkout_session_id = session.id
    _add_booking_lifecycle_event(
        db,
        event_type="checkout_created",
        source="public_booking",
        booking=booking,
        hold_token=slot_hold.hold_token if slot_hold else None,
        summary=(
            "Stripe staged-deposit authorization checkout created for BYOF booking."
            if staged_deposit
            else "Stripe deposit checkout created for public booking."
        ),
        details={
            "stripe_session_id": session.id,
            "deposit_amount": deposit_cents / 100,
            "configured_service_price": service_price_cents / 100,
            "route_mode": routing.reason,
            "destination": routing.destination,
            "artist_cut_cents": routing.artist_cut_cents,
            "shop_cut_cents": routing.shop_cut_cents,
            "payout_owed_cents": routing.payout_owed_cents,
            "artist_connect_account": bool(routing.artist_account_id),
            "save_payment_method": save_payment_method,
            "staged_deposit": staged_deposit,
        },
    )
    if staged_deposit:
        booking.deposit_authorization_status = "pending_checkout"
    return session.url


def _byof_stripe_request_options(booking: Booking, artist: BTTCOUser, *, action: str) -> dict | None:
    service_price_cents = int(round(float(booking.deposit_amount or 0) * 100))
    routing = resolve_charge_routing(
        artist,
        customer_price_cents(service_price_cents),
        artist_share_base_cents=service_price_cents,
    )
    return routing.request_options(idempotency_key=f"byof-{action}-{booking.id}")


async def _capture_byof_authorization(booking: Booking, artist: BTTCOUser) -> None:
    if not booking.is_bring_your_own_flash or booking.deposit_authorization_status != "authorized":
        return
    if not booking.stripe_payment_intent_id:
        raise HTTPException(status_code=409, detail="This BYOF deposit authorization is not ready")
    settings = get_settings()
    if settings.LOCAL_DEV_MODE and settings.STRIPE_SECRET_KEY.startswith("sk_live"):
        raise HTTPException(status_code=503, detail="Live Stripe capture is disabled in local dev")
    try:
        get_stripe_client().v1.payment_intents.capture(
            booking.stripe_payment_intent_id,
            {},
            _byof_stripe_request_options(booking, artist, action="capture"),
        )
    except Exception as exc:
        logger.error("Stripe BYOF authorization capture failed for %s: %s", booking.id, exc)
        raise HTTPException(status_code=502, detail="Could not capture the BYOF deposit authorization") from exc
    booking.deposit_authorization_status = "captured"
    booking.deposit_paid = True
    booking.deposit_review_deadline_at = None
    booking.updated_at = datetime.now(UTC)


async def _release_byof_authorization(
    booking: Booking,
    artist: BTTCOUser,
    *,
    final_status: str = "released",
) -> None:
    if not booking.is_bring_your_own_flash:
        return
    if booking.deposit_authorization_status not in {"authorized", "pending_checkout"}:
        return
    if booking.deposit_authorization_status == "pending_checkout" and booking.stripe_checkout_session_id:
        settings = get_settings()
        if settings.LOCAL_DEV_MODE and settings.STRIPE_SECRET_KEY.startswith("sk_live"):
            raise HTTPException(status_code=503, detail="Live Stripe checkout expiry is disabled in local dev")
        try:
            get_stripe_client().v1.checkout.sessions.expire(
                booking.stripe_checkout_session_id,
                {},
                _byof_stripe_request_options(booking, artist, action=f"expire-{final_status}"),
            )
        except Exception as exc:
            logger.error("Stripe BYOF checkout expiry failed for %s: %s", booking.id, exc)
            raise HTTPException(status_code=502, detail="Could not close the BYOF deposit checkout") from exc
    if booking.stripe_payment_intent_id and booking.deposit_authorization_status == "authorized":
        settings = get_settings()
        if settings.LOCAL_DEV_MODE and settings.STRIPE_SECRET_KEY.startswith("sk_live"):
            raise HTTPException(status_code=503, detail="Live Stripe authorization release is disabled in local dev")
        try:
            get_stripe_client().v1.payment_intents.cancel(
                booking.stripe_payment_intent_id,
                {"cancellation_reason": "requested_by_customer"},
                _byof_stripe_request_options(booking, artist, action=final_status),
            )
        except Exception as exc:
            logger.error("Stripe BYOF authorization release failed for %s: %s", booking.id, exc)
            raise HTTPException(status_code=502, detail="Could not release the BYOF deposit authorization") from exc
    booking.deposit_authorization_status = final_status
    booking.deposit_paid = False
    booking.deposit_review_deadline_at = None
    booking.updated_at = datetime.now(UTC)


async def process_byof_deposit_reviews_once(limit: int = 20) -> int:
    """Remind staff at 24 hours and release uncaptured BYOF holds at 48 hours."""
    now = datetime.now(UTC)
    reminder_cutoff = now + timedelta(hours=BYOF_REVIEW_REMINDER_HOURS)
    processed = 0
    async with async_session() as db:
        result = await db.execute(
            select(Booking)
            .where(
                Booking.is_bring_your_own_flash.is_(True),
                Booking.deposit_authorization_status == "authorized",
                Booking.deposit_review_deadline_at.is_not(None),
                Booking.deposit_review_deadline_at <= reminder_cutoff,
            )
            .order_by(Booking.deposit_review_deadline_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        for booking in result.scalars().all():
            artist = await db.get(BTTCOUser, booking.artist_id)
            if not artist:
                continue
            if booking.deposit_review_deadline_at and booking.deposit_review_deadline_at <= now:
                try:
                    await _release_byof_authorization(booking, artist, final_status="expired")
                except HTTPException as exc:
                    logger.error("BYOF authorization auto-release failed for %s: %s", booking.id, exc.detail)
                    continue
                _add_booking_lifecycle_event(
                    db,
                    event_type="byof_deposit_expired",
                    source="review_worker",
                    booking=booking,
                    summary="BYOF deposit authorization released after the review window expired.",
                )
                await _notify_booking_lifecycle(
                    booking=booking,
                    db=db,
                    subject="Your tattoo deposit hold was released",
                    body="Your artist did not confirm the design within the review window, so the deposit hold was released.",
                )
                processed += 1
                continue
            if booking.deposit_review_reminder_sent_at is None:
                db.add(
                    NotificationModel(
                        user_id=booking.artist_id,
                        title="BYOF deposit review due",
                        body=f"Review booking {booking.reference_code or booking.id} before its deposit hold expires.",
                        notification_type="booking",
                        action_url=ROUTES.HUSTLE_DEPOSITS,
                    )
                )
                booking.deposit_review_reminder_sent_at = now
                _add_booking_lifecycle_event(
                    db,
                    event_type="byof_review_reminder_sent",
                    source="review_worker",
                    booking=booking,
                    summary="Artist reminded that the BYOF deposit authorization is nearing expiry.",
                )
                processed += 1
        if processed:
            await db.commit()
    return processed


async def _do_submit_public_booking(
    body: PublicBookingSubmit,
    db: AsyncSession,
) -> PublicBookingResponse:
    # Resolve artist slug to user
    slug = body.artist_slug
    if slug == "any":
        slug = "justin"  # Default to Justin for "any artist"

    result = await db.execute(
        select(BTTCOUser).where(BTTCOUser.slug == slug).where(BTTCOUser.role.in_(["artist", "admin", "founder"]))
    )
    artist = result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    artist_id = artist.id
    artist_name = artist.name or slug
    artist_email = artist.email
    artist_booking_mode = artist.booking_mode
    capability_collector_id: UUID | None = None
    if body.booky_capability:
        try:
            capability_collector_id = decode_booky_capability_token(
                body.booky_capability,
                body.inquiry_id or "",
            )
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Invalid or expired Booky capability") from exc

    if body.inquiry_id:
        existing_draft_result = await db.execute(
            select(BookingDraft).where(
                BookingDraft.inquiry_id == body.inquiry_id,
                BookingDraft.status == "submitted",
                BookingDraft.reference_code.is_not(None),
            )
        )
        existing_draft = existing_draft_result.scalar_one_or_none()
        if existing_draft and existing_draft.reference_code:
            existing_booking_result = await db.execute(
                select(Booking).where(Booking.reference_code == existing_draft.reference_code)
            )
            existing_booking = existing_booking_result.scalar_one_or_none()
            capability_verified = _verify_existing_inquiry_capability(capability_collector_id, existing_booking)
            if not capability_verified:
                return PublicBookingResponse(
                    message="This inquiry was already submitted.",
                    booking_status=None,
                    idempotent_acknowledgement=True,
                )
            return PublicBookingResponse(
                reference_code=existing_draft.reference_code,
                artist_name=artist_name,
                message="Your booking request has already been submitted.",
                booking_id=existing_booking.id if existing_booking else None,
                booking_status=(
                    existing_booking.status.value
                    if existing_booking and hasattr(existing_booking.status, "value")
                    else str(existing_booking.status)
                    if existing_booking
                    else "pending"
                ),
                scheduled_date=existing_booking.scheduled_date if existing_booking else None,
                scheduled_time=existing_booking.scheduled_time if existing_booking else None,
                preferred_slots=(
                    _parse_preferred_slots(existing_booking.preferred_slots_json) if existing_booking else None
                ),
                booky_outcome=(
                    await _booky_projection(existing_booking, db, staff=False) if existing_booking else None
                ),
            )

    slot_hold: BookingSlotHold | None = None
    if body.preferred_date and body.preferred_time:
        if not body.hold_token:
            raise HTTPException(status_code=409, detail="Refresh this time slot before submitting")
        hold_result = await db.execute(
            select(BookingSlotHold)
            .where(
                BookingSlotHold.hold_token == body.hold_token,
                BookingSlotHold.artist_id == artist_id,
                BookingSlotHold.scheduled_date == body.preferred_date,
                BookingSlotHold.scheduled_time == body.preferred_time[:5],
                BookingSlotHold.booking_kind == body.booking_kind,
                BookingSlotHold.status == "active",
                BookingSlotHold.expires_at > datetime.now(UTC),
            )
            .with_for_update()
        )
        slot_hold = hold_result.scalar_one_or_none()
        if not slot_hold:
            raise HTTPException(status_code=409, detail="This time slot expired or is no longer available")

    session_type: ArtistSessionType | None = None
    if body.session_type_id:
        session_type_result = await db.execute(
            select(ArtistSessionType).where(
                ArtistSessionType.id == body.session_type_id,
                ArtistSessionType.artist_id == artist_id,
                ArtistSessionType.is_active.is_(True),
            )
        )
        session_type = session_type_result.scalar_one_or_none()
        if not session_type:
            raise HTTPException(status_code=422, detail="Selected appointment type is not available")

    selected_add_ons: list[BookingAddOn] = []
    requested_add_on_ids = body.booking_add_on_ids or []
    if requested_add_on_ids:
        add_on_result = await db.execute(
            select(BookingAddOn).where(
                BookingAddOn.id.in_(requested_add_on_ids),
                BookingAddOn.artist_id == artist_id,
                BookingAddOn.is_active.is_(True),
            )
        )
        selected_add_ons = list(add_on_result.scalars().all())
        if len(selected_add_ons) != len(set(requested_add_on_ids)):
            raise HTTPException(status_code=422, detail="One or more selected add-ons are not available")

    # Generate reference code (retry on collision)
    ref_code = _generate_booking_ref()
    for _ in range(5):
        existing = await db.execute(select(Booking.id).where(Booking.reference_code == ref_code))
        if not existing.scalar_one_or_none():
            break
        ref_code = _generate_booking_ref()

    # Resolve or auto-create client account
    client_name = f"{body.first_name} {body.last_name}"
    client_user, account_created = await _find_or_create_collector_with_status(db, body.email, client_name, body.phone)
    collector_identity_verified = _collector_identity_verified(capability_collector_id, client_user.id)
    if body.phone and not client_user.phone:
        client_user.phone = body.phone

    sms_opted_in_at = datetime.now(UTC) if body.sms_opt_in and body.phone else None
    if sms_opted_in_at:
        client_user.phone = body.phone
        client_user.sms_opt_in = True
        client_user.sms_opt_in_at = sms_opted_in_at
        client_user.sms_opt_out_at = None

    preferred_slots_json = _serialize_preferred_slots(body.preferred_slots)
    if preferred_slots_json and not body.is_bring_your_own_flash:
        # Proposed times carry no hold and no deposit; the artist sets both when
        # confirming one of them through PATCH /bookings/{id}.
        deposit_amount, deposit_rule_tier = None, "preferred_slots_no_deposit"
    else:
        deposit_amount, deposit_rule_tier = _resolve_public_booking_deposit(
            booking_kind=body.booking_kind,
            session_type=session_type,
            slot_hold=slot_hold,
        )
    settings = get_settings()
    action_id = booky_action_id(
        artist_id=artist.id,
        inquiry_id=body.inquiry_id,
        hold_token=slot_hold.hold_token if slot_hold else body.hold_token,
        policy_version=settings.BOOKY_POLICY_VERSION,
    )
    booky_decision = resolve_booky_policy(
        settings=settings,
        artist=artist,
        booking_kind=body.booking_kind,
        collector_id=client_user.id,
        collector_identity_verified=collector_identity_verified,
        consent_granted=body.booky_consent,
        consent_policy_version=body.booky_consent_policy_version,
        slot_hold_verified=slot_hold is not None,
        calendar_connected=artist_calendar_connected(artist),
    )
    attempt_auto_consultation = booky_decision.can_auto_confirm
    consultation_confirmed = False

    # Create booking linked to account
    booking = Booking(
        client_id=client_user.id,
        artist_id=artist_id,
        guest_name=client_name,
        guest_email=body.email,
        guest_phone=body.phone,
        sms_opt_in=bool(sms_opted_in_at),
        sms_opt_in_at=sms_opted_in_at,
        sms_consent_text=BOOKING_SMS_CONSENT_TEXT if sms_opted_in_at else None,
        reference_code=ref_code,
        status=BookingStatus.PENDING,
        description=body.description,
        booking_kind=body.booking_kind,
        is_bring_your_own_flash=body.is_bring_your_own_flash,
        consultation_method=body.consultation_method,
        booky_consent=body.booky_consent,
        booky_consent_at=datetime.now(UTC) if body.booky_consent else None,
        booky_consent_policy_version=body.booky_consent_policy_version if body.booky_consent else None,
        booky_collector_verified_at=datetime.now(UTC) if collector_identity_verified else None,
        booky_capability_inquiry_id=body.inquiry_id if collector_identity_verified else None,
        placement=body.placement,
        size_estimate=body.size_estimate,
        style_preference=body.style_preference,
        budget=body.budget,
        timeline=body.timeline,
        preferred_date=body.preferred_date,
        preferred_time=body.preferred_time,
        preferred_slots_json=preferred_slots_json,
        scheduled_date=slot_hold.scheduled_date if slot_hold else None,
        scheduled_time=slot_hold.scheduled_time if slot_hold else None,
        duration_minutes=slot_hold.duration_minutes
        if slot_hold
        else session_type.duration_minutes
        if session_type
        else None,
        deposit_amount=deposit_amount,
        estimated_total=float(session_type.price_max) if session_type and session_type.price_max is not None else None,
        reference_urls=json.dumps(body.reference_image_urls) if body.reference_image_urls else None,
        client_notes=body.client_notes,
    )
    db.add(booking)
    await db.flush()
    if not body.inquiry_id and not body.hold_token:
        action_id = booky_action_id(
            artist_id=artist.id,
            inquiry_id=str(booking.id),
            hold_token=None,
            policy_version=settings.BOOKY_POLICY_VERSION,
        )

    for add_on in selected_add_ons:
        db.add(
            BookingAddOnSelection(
                booking_id=booking.id,
                add_on_id=add_on.id,
                label=add_on.label,
                description=add_on.description,
                price_amount=add_on.price_amount,
                duration_minutes=add_on.duration_minutes,
            )
        )
    if slot_hold:
        slot_hold.status = "consumed"
        slot_hold.booking_id = booking.id
        slot_hold.updated_at = datetime.now(UTC)
        _add_booking_lifecycle_event(
            db,
            event_type="hold_consumed",
            source="public_booking",
            booking=booking,
            hold_token=slot_hold.hold_token,
            summary=f"Public booking consumed hold for {slot_hold.scheduled_date} {slot_hold.scheduled_time}.",
            details={
                "scheduled_date": slot_hold.scheduled_date.isoformat(),
                "scheduled_time": slot_hold.scheduled_time,
                "duration_minutes": slot_hold.duration_minutes,
            },
        )
    _add_booking_lifecycle_event(
        db,
        event_type="booking_submitted",
        source="public_booking",
        booking=booking,
        hold_token=slot_hold.hold_token if slot_hold else None,
        summary=f"Public booking request submitted by {client_name}.",
        details={
            "preferred_date": body.preferred_date.isoformat() if body.preferred_date else None,
            "preferred_time": body.preferred_time,
            "preferred_slots": preferred_slots_json,
            "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
            "scheduled_time": booking.scheduled_time,
            "sms_opt_in": booking.sms_opt_in,
            "booking_kind": booking.booking_kind,
            "is_bring_your_own_flash": booking.is_bring_your_own_flash,
            "consultation_method": booking.consultation_method,
            "session_type_id": str(session_type.id) if session_type else None,
            "deposit_rule_tier": deposit_rule_tier,
            "deposit_amount": deposit_amount,
            "add_on_ids": [str(add_on.id) for add_on in selected_add_ons],
        },
    )

    decision_details = {
        "action_id": action_id,
        "configured_mode": booky_decision.configured_mode,
        "effective_mode": booky_decision.effective_mode,
        "truth_state": booky_decision.truth_state,
        "recommendation": booky_decision.recommendation,
        "explanation": booky_decision.explanation,
        "confidence": booky_decision.confidence,
        "policy_version": booky_decision.policy_version,
        "risk": booky_decision.risk,
        "required_approval": booky_decision.required_approval,
        "blocked_reasons": list(booky_decision.blocked_reasons),
        "available_actions": list(booky_decision.available_actions),
    }
    if booky_decision.configured_mode != "manual":
        _add_booking_lifecycle_event(
            db,
            event_type="booky_blocked" if booky_decision.truth_state == "blocked" else "booky_recommendation",
            source="booky",
            booking=booking,
            hold_token=slot_hold.hold_token if slot_hold else None,
            summary=booky_decision.explanation,
            details=decision_details,
            provider_event_id=f"booky:{action_id}:decision",
        )

    conv = await _attach_booking_conversation(
        booking=booking,
        db=db,
        client_id=client_user.id,
        client_name=client_name,
    )
    await _ensure_booking_intake_message(
        booking=booking,
        db=db,
        sender_id=client_user.id,
        client_name=client_name,
        client_email=body.email,
        client_phone=body.phone,
    )

    conference_url: str | None = None
    courier_confirmation_message: Message | None = None
    if attempt_auto_consultation:
        await _add_booky_receipt_once(
            db,
            booking=booking,
            event_type="booky_provider_intent",
            summary="Booky provider saga is planned and awaits durable commit.",
            details={
                **decision_details,
                "action_id": action_id,
                "calendar_event_id": f"bttco{booking.id.hex}",
                "conference_request_id": f"bttcomeet{booking.id.hex}",
                "sequence": ["calendar", "meet", "invitation", "courier"],
            },
            provider_event_id=f"booky:{action_id}:provider-intent",
            hold_token=slot_hold.hold_token,
        )
        provider_intent_id = f"booky:{action_id}:provider-intent"
        # The pending booking, consumed hold, policy action, Courier thread, and
        # intake message must be durable before the first provider mutation.
        await db.commit()
        consultation_confirmed, conference_url, courier_confirmation_message = await _run_booky_confirmation_saga(
            db,
            booking=booking,
            artist=artist,
            conversation=conv,
            client_name=client_name,
            client_email=body.email,
            action_id=action_id,
            hold_token=slot_hold.hold_token,
            decision_details=decision_details,
            attempt=0,
            intent_provider_event_id=provider_intent_id,
        )

    if not consultation_confirmed:
        await _ensure_booking_pipeline_task(
            db=db,
            booking=booking,
            client_name=client_name,
            client_email=body.email,
            client_phone=body.phone,
            artist_name=artist_name,
            assignee_id=artist_id,
        )

    # Durable email intents. Skip auto emails for venue.ink artists because
    # they manage booking communication externally.
    settings = get_settings()
    booking_email_intents: list[BookingLifecycleEvent] = []
    if account_created:
        booking_email_intents.append(
            _queue_booking_email_intent(
                db,
                booking=booking,
                kind="client_account_setup",
                recipient=client_user.email,
                payload={"user_id": str(client_user.id)},
            )
        )

    if artist_booking_mode != "venue.ink":
        # 1. Confirmation to the person who filled out the form (guest)
        if not consultation_confirmed:
            booking_email_intents.append(
                _queue_booking_email_intent(
                    db,
                    booking=booking,
                    kind="client_confirmation",
                    recipient=body.email,
                    payload={
                        "client_name": client_name,
                        "artist_name": artist_name,
                        "description": body.description,
                        "placement": body.placement,
                        "reference_code": ref_code,
                        "booking_kind": body.booking_kind,
                        "consultation_method": body.consultation_method,
                        "confirmed": False,
                        "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
                        "scheduled_time": booking.scheduled_time,
                        "conference_url": conference_url,
                        "reference_image_urls": body.reference_image_urls,
                    },
                )
            )

        # 2. Notification to artist + shop + owner (all 3 always get it)
        submitted_at = datetime.now(UTC).astimezone(SHOP_TIMEZONE).strftime("%B %d, %Y at %I:%M %p EST")
        notification_recipients = sorted(
            recipient
            for recipient in {
                artist_email,
                settings.SHOP_EMAIL,
                settings.SHOP_OWNER_EMAIL,
            }
            if recipient
        )
        for recipient in notification_recipients:
            booking_email_intents.append(
                _queue_booking_email_intent(
                    db,
                    booking=booking,
                    kind="artist_alert",
                    recipient=recipient,
                    payload={
                        "client_name": client_name,
                        "client_email": body.email,
                        "client_phone": body.phone,
                        "artist_name": artist_name,
                        "description": body.description,
                        "placement": body.placement,
                        "size": body.size_estimate,
                        "budget": body.budget,
                        "timeline": body.timeline,
                        "booking_kind": body.booking_kind,
                        "consultation_method": body.consultation_method,
                        "preferred_date": body.preferred_date.isoformat() if body.preferred_date else None,
                        "preferred_time": body.preferred_time,
                        "preferred_slots": preferred_slots_json,
                        "client_notes": body.client_notes,
                        "reference_code": ref_code,
                        "conversation_id": str(conv.id),
                        "style_preference": body.style_preference,
                        "submitted_at": submitted_at,
                        "reference_image_urls": body.reference_image_urls,
                        "confirmed": consultation_confirmed,
                        "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
                        "scheduled_time": booking.scheduled_time,
                    },
                )
            )
    else:
        logger.info(
            "Skipping booking emails for venue.ink artist %s (booking %s)",
            artist_name,
            ref_code,
        )

    # In-app notification for the artist
    booking_action_url = ROUTES.BOOKING_REF.format(ref=ref_code)
    notification_title = "Consultation Booked" if consultation_confirmed else "New Booking Request"
    notification_body = (
        f"{client_name} booked a consultation for {booking.scheduled_date} at {booking.scheduled_time}."
        if consultation_confirmed
        else f"{client_name} submitted a booking request."
    )
    db.add(
        NotificationModel(
            user_id=artist_id,
            title=notification_title,
            body=notification_body,
            notification_type="booking",
            action_url=booking_action_url,
        )
    )
    if await should_notify(db, artist_id, "push", "bookings"):
        await send_push_notification(
            db,
            artist_id,
            title=notification_title,
            body=notification_body,
            url=booking_action_url,
        )

    # Also notify founder + admin users (shop managers) if different from the artist
    manager_result = await db.execute(select(BTTCOUser.id).where(BTTCOUser.role.in_(["founder", "admin"])))
    manager_ids = [row[0] for row in manager_result.all()]
    for mgr_id in manager_ids:
        if mgr_id != artist_id:
            db.add(
                NotificationModel(
                    user_id=mgr_id,
                    title=notification_title,
                    body=(
                        f"{client_name} booked a consultation with {artist_name} for "
                        f"{booking.scheduled_date} at {booking.scheduled_time}."
                        if consultation_confirmed
                        else f"{client_name} submitted a booking request for {artist_name}."
                    ),
                    notification_type="booking",
                    action_url=booking_action_url,
                )
            )
            if await should_notify(db, mgr_id, "push", "bookings"):
                await send_push_notification(
                    db,
                    mgr_id,
                    title=notification_title,
                    body=(
                        f"{client_name} booked a consultation with {artist_name} for "
                        f"{booking.scheduled_date} at {booking.scheduled_time}."
                        if consultation_confirmed
                        else f"{client_name} submitted a booking request for {artist_name}."
                    ),
                    url=booking_action_url,
                )
    await db.flush()
    if sms_opted_in_at:
        if consultation_confirmed:
            await _send_booking_confirmed_sms(db, booking)
        else:
            await _send_booking_request_received_sms(
                db,
                phone=body.phone,
                client_name=client_name,
                artist_name=artist_name,
                reference_code=ref_code,
                client_id=client_user.id,
                conversation_id=conv.id,
            )

    # Quest: auto-complete "first-booking" (always has a user now)
    await check_and_complete_quest(db, client_user.id, "first-booking")
    await reward_user_by_id(
        db,
        client_user.id,
        "booking.inquiry_submitted",
        str(booking.id),
        metadata={"reference_code": ref_code},
    )
    if body.inquiry_id:
        draft = PublicBookingDraft(
            inquiry_id=body.inquiry_id,
            artist_slug=body.artist_slug,
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            phone=body.phone,
            description=body.description,
            booking_kind=body.booking_kind,
            is_bring_your_own_flash=body.is_bring_your_own_flash,
            consultation_method=body.consultation_method,
            style_preference=body.style_preference,
            placement=body.placement,
            size_estimate=body.size_estimate,
            budget=body.budget,
            timeline=body.timeline,
            preferred_date=str(body.preferred_date) if body.preferred_date else None,
            preferred_time=body.preferred_time,
            preferred_slots=preferred_slots_json,
            client_notes=body.client_notes,
            image_urls=body.reference_image_urls,
        )
        await _upsert_booking_draft(
            db,
            draft,
            status="submitted",
            reference_code=ref_code,
        )
    checkout_url: str | None = None
    checkout_required = False
    deposit_amount = float(booking.deposit_amount or 0)
    if deposit_amount > 0 and (slot_hold or body.is_bring_your_own_flash):
        checkout_required = True
        checkout_url = await _create_public_booking_checkout(
            db,
            booking=booking,
            artist=artist,
            slot_hold=slot_hold,
            client_email=body.email,
            deposit_amount=deposit_amount,
            save_payment_method=body.save_payment_method,
            staged_deposit=body.is_bring_your_own_flash,
        )

    await db.commit()
    if consultation_confirmed and courier_confirmation_message:
        asyncio.create_task(process_booky_courier_outbox_once(message_id=courier_confirmation_message.id))
    if booking_email_intents:
        asyncio.create_task(process_booking_email_outbox_once())
    booky_outcome = await _booky_projection(booking, db, staff=False)

    return PublicBookingResponse(
        reference_code=ref_code,
        artist_name=artist_name,
        booking_id=booking.id,
        checkout_url=checkout_url,
        checkout_required=checkout_required,
        deposit_amount=deposit_amount if deposit_amount > 0 else None,
        message="Your consultation is booked."
        if consultation_confirmed
        else "Your booking request has been submitted!",
        booking_status=booking.status.value if hasattr(booking.status, "value") else str(booking.status),
        scheduled_date=booking.scheduled_date,
        scheduled_time=booking.scheduled_time,
        preferred_slots=body.preferred_slots,
        conference_url=conference_url,
        booky_outcome=booky_outcome,
    )


@router.post("/{reference_code}/link-account", response_model=BookingResponse)
async def link_booking_to_account(
    reference_code: str,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Auth required: link an existing guest booking to a newly-created account.

    Called after the user registers from the post-submit prompt.
    Updates client_id and clears guest fields.
    """
    result = await db.execute(select(Booking).where(Booking.reference_code == reference_code))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.client_id is not None:
        raise HTTPException(status_code=400, detail="Booking already linked to an account")

    # Link to user and clear guest fields. Keep the phone on the account when it
    # has none, so the collector's texts still match this booking.
    if booking.guest_phone and not current_user.phone:
        current_user.phone = booking.guest_phone
    booking.client_id = current_user.id
    booking.guest_name = None
    booking.guest_email = None
    booking.guest_phone = None
    booking.updated_at = datetime.now(UTC)

    await db.flush()
    return await _enrich_booking(booking, db)


# Background email tasks


async def _send_booking_confirmation(
    to: str,
    client_name: str,
    artist_name: str,
    description: str,
    placement: str | None,
    reference_code: str,
    booking_kind: str = "tattoo_session",
    consultation_method: str | None = None,
    confirmed: bool = False,
    scheduled_date: str | None = None,
    scheduled_time: str | None = None,
    conference_url: str | None = None,
    courier_message_id: UUID | None = None,
    reference_image_urls: list[str] | None = None,
    conversation_id: UUID | None = None,
) -> str:
    """Send a booking confirmation and return sent, delegated, or failed.

    `conversation_id` threads the client's reply. Without it the reply-to is
    the alias default, so replying to a booking confirmation starts a thread
    keyed on the sender's address rather than rejoining the booking's own
    conversation.
    """
    try:
        template_context = {
            "client_name": client_name,
            "artist_name": artist_name,
            "description": description or "",
            "placement": placement or "",
            "reference_code": reference_code,
            "booking_kind": booking_kind,
            "consultation_method": consultation_method or "",
            "confirmed": confirmed,
            "scheduled_date": scheduled_date or "",
            "scheduled_time": scheduled_time or "",
            "conference_url": conference_url or "",
        }
        fallback_html = build_booking_confirmation_html(
            client_name=client_name,
            artist_name=artist_name,
            description=description,
            placement=placement,
            reference_code=reference_code,
            booking_kind=booking_kind,
            consultation_method=consultation_method,
            confirmed=confirmed,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            conference_url=conference_url,
        )
        fallback_subject = (
            "Consultation Booked - Ryozan Studio"
            if booking_kind == "consultation" and confirmed
            else "Consultation Request Received - Ryozan Studio"
            if booking_kind == "consultation"
            else "Booking Request Received - Ryozan Studio"
        )
        settings = get_settings()
        references = [str(url).strip() for url in (reference_image_urls or []) if str(url).strip()][:2]
        if booking_kind == "consultation":
            default_intro = (
                "Your consultation has been confirmed. We look forward to meeting with you."
                if confirmed
                else "We received your consultation request and will follow up soon."
            )
        else:
            default_intro = "We received your booking request and will follow up soon."
        foundry_variables = {
            **template_context,
            "intro_message": description or default_intro,
            "ref_image_1": references[0] if references else "",
            "ref_image_2": references[1] if len(references) > 1 else "",
            "track_url": f"{settings.FRONTEND_URL}{ROUTES.BOOKING_REF.format(ref=reference_code)}",
        }
        dispatch = {"matched": []}
        try:
            async with async_session() as foundry_db:
                dispatch = await dispatch_foundry_trigger(
                    foundry_db,
                    "booking_filed",
                    recipient=to,
                    variables=foundry_variables,
                    event_id=f"booking:{reference_code}:filed",
                    delivery={
                        "from_alias": FROM_SHOP,
                        "courier_message_id": str(courier_message_id) if courier_message_id else "",
                    },
                )
                await foundry_db.commit()
        except Exception as exc:
            logger.error("Foundry booking trigger failed for %s: %s", reference_code, exc)
        if "booking_confirmation" in dispatch.get("matched", []):
            if int(dispatch.get("failed") or 0) > 0:
                return "failed"
            if int(dispatch.get("queued") or 0) > 0:
                return "delegated"
            if int(dispatch.get("sent") or 0) > 0:
                return "sent"
            # An idempotent trigger match with no new send still means
            # Foundry owns the delivery receipt. Do not duplicate-send.
            return "delegated"
        rendered = await render_transactional_email(
            "booking_confirmation",
            foundry_variables,
            fallback_subject=fallback_subject,
            fallback_html=fallback_html,
        )
        subject = rendered.subject
        html = rendered.html_body
        accepted = await send_email(
            to=to,
            subject=subject,
            html_body=html,
            tags=["booking-confirmation"],
            from_alias=FROM_SHOP,
            reply_to=conversation_reply_to(conversation_id),
            courier_message_id=str(courier_message_id) if courier_message_id else None,
        )
        return "sent" if accepted else "failed"
    except Exception as exc:
        logger.error("Booking confirmation email failed for %s: %s", to, exc)
        return "failed"


async def _send_artist_booking_alert(
    to: str,
    client_name: str,
    client_email: str,
    client_phone: str | None,
    artist_name: str,
    description: str,
    placement: str | None,
    size: str | None,
    budget: str | None,
    timeline: str | None,
    reference_code: str,
    conversation_id: UUID | str | None = None,
    style_preference: str | None = None,
    submitted_at: str | None = None,
    reference_image_urls: list[str] | None = None,
    confirmed: bool = False,
    scheduled_date: str | None = None,
    scheduled_time: str | None = None,
    booking_kind: str | None = None,
    consultation_method: str | None = None,
    preferred_date: str | None = None,
    preferred_time: str | None = None,
    preferred_slots: list[dict[str, str]] | None = None,
    client_notes: str | None = None,
) -> bool:
    """Notify a booking recipient and return provider acceptance."""
    try:
        action_urls = _booking_action_urls(reference_code, conversation_id)
        rendered = render_artist_booking_alert(
            client_name=client_name,
            client_email=client_email,
            client_phone=client_phone,
            artist_name=artist_name,
            description=description,
            placement=placement,
            size=size,
            budget=budget,
            timeline=timeline,
            reference_code=reference_code,
            style_preference=style_preference,
            submitted_at=submitted_at,
            reference_image_urls=reference_image_urls,
            confirmed=confirmed,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            accept_url=action_urls["accept_url"],
            message_url=action_urls["message_url"],
            decline_url=action_urls["decline_url"],
            booking_kind=booking_kind,
            consultation_method=consultation_method,
            preferred_date=preferred_date,
            preferred_time=preferred_time,
            preferred_slots=preferred_slots,
            client_notes=client_notes,
        )
        return await send_email(
            to=to,
            subject=rendered.subject,
            html_body=rendered.html_body,
            reply_to=client_email,
            tags=["booking-artist-alert"],
            from_alias=FROM_ADMIN,
        )
    except Exception as exc:
        logger.error("Artist booking alert failed for %s: %s", to, exc)
        return False


def _booking_email_timestamp(value: object, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value)) if value else fallback
    except (TypeError, ValueError):
        parsed = fallback
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


async def _send_request_approved(
    *,
    to: str,
    client_name: str,
    artist_name: str,
    reference_code: str,
    scheduled_date: str,
    scheduled_time: str,
    duration_minutes: int | None,
    deposit_amount: float,
    deposit_url: str | None = None,
    artist_message: str | None = None,
    booking_kind: str | None = None,
    conversation_id: str | None = None,
) -> bool:
    """Collector "request approved, deposit pending" email. Never the booking confirmation."""
    rendered = render_request_approved(
        client_name=client_name,
        artist_name=artist_name,
        reference_code=reference_code,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        duration_minutes=duration_minutes,
        deposit_amount=float(deposit_amount or 0),
        deposit_url=deposit_url,
        artist_message=artist_message,
        booking_kind=booking_kind,
    )
    return await send_email(
        to=to,
        subject=rendered.subject,
        html_body=rendered.html_body,
        from_alias=FROM_SHOP,
        reply_to=conversation_reply_to(UUID(conversation_id)) if conversation_id else None,
        tags=["request_approved"],
    )


async def process_booking_email_outbox_once(limit: int = 20) -> int:
    """Deliver durable client confirmations and staff booking alerts."""
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=BOOKING_EMAIL_STALE_MINUTES)
    async with async_session() as session:
        status_expr = BookingLifecycleEvent.details["status"].astext
        attempts_expr = func.coalesce(cast(BookingLifecycleEvent.details["attempts"].astext, Integer), 0)
        updated_at_expr = BookingLifecycleEvent.details["updated_at"].astext
        next_attempt_at_expr = BookingLifecycleEvent.details["next_attempt_at"].astext
        result = await session.execute(
            select(BookingLifecycleEvent)
            .where(
                BookingLifecycleEvent.event_type == "booking_email_intent",
                attempts_expr < BOOKING_EMAIL_MAX_ATTEMPTS,
                or_(
                    status_expr == "queued",
                    and_(status_expr == "failed", next_attempt_at_expr <= now.isoformat()),
                    and_(status_expr == "sending", updated_at_expr <= stale_before.isoformat()),
                ),
            )
            .order_by(BookingLifecycleEvent.created_at, BookingLifecycleEvent.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        claimed: list[tuple[UUID, dict]] = []
        for event in result.scalars().all():
            details = dict(event.details or {})
            status = str(details.get("status") or "queued")
            attempts = int(details.get("attempts") or 0)
            updated_at = _booking_email_timestamp(details.get("updated_at"), event.created_at)
            next_attempt_at = _booking_email_timestamp(details.get("next_attempt_at"), now)
            eligible = status == "queued" or (
                status == "failed" and attempts < BOOKING_EMAIL_MAX_ATTEMPTS and next_attempt_at <= now
            )
            if status == "sending" and attempts < BOOKING_EMAIL_MAX_ATTEMPTS and updated_at <= stale_before:
                eligible = True
            if not eligible:
                continue
            details.update(
                {
                    "status": "sending",
                    "attempts": attempts + 1,
                    "updated_at": now.isoformat(),
                    "last_error": None,
                }
            )
            event.details = details
            event.summary = f"Booking {str(details.get('kind') or 'notification').replace('_', ' ')} email sending."
            claimed.append((event.id, details))
        await session.commit()

        delivered = 0
        for event_id, claimed_details in claimed:
            kind = str(claimed_details.get("kind") or "")
            recipient = str(claimed_details.get("to") or "")
            payload = claimed_details.get("payload")
            error: str | None = None
            try:
                if not recipient or not isinstance(payload, dict):
                    raise ValueError("Booking email intent payload is incomplete")
                if kind == "client_confirmation":
                    delivery_status = await _send_booking_confirmation(to=recipient, **payload)
                elif kind == "artist_alert":
                    delivery_status = "sent" if await _send_artist_booking_alert(to=recipient, **payload) else "failed"
                elif kind == "request_approved":
                    delivery_status = "sent" if await _send_request_approved(to=recipient, **payload) else "failed"
                elif kind == "client_account_setup":
                    user = await session.get(BTTCOUser, UUID(payload["user_id"]), populate_existing=True)
                    if user is None or normalize_email(user.email) != normalize_email(recipient):
                        raise ValueError("Account setup recipient no longer matches the account")
                    delivery_status = "sent"
                    if not user.email_verified_at:
                        accepted = await send_account_setup_email(
                            user_id=user.id, email=user.email, password_hash=user.password_hash, name=user.name
                        )
                        delivery_status = "sent" if accepted else "failed"
                else:
                    raise ValueError(f"Unsupported booking email intent kind: {kind}")
                if delivery_status == "failed":
                    error = "Email provider did not accept the message"
            except Exception as exc:  # noqa: BLE001 - durable state records the attempt
                delivery_status = "failed"
                error = str(exc)[:1000]
                logger.exception("Booking email outbox attempt failed for event %s", event_id)

            event = await session.get(
                BookingLifecycleEvent,
                event_id,
                with_for_update=True,
                populate_existing=True,
            )
            if not event:
                continue
            details = dict(event.details or {})
            attempt = int(claimed_details.get("attempts") or 1)
            if details.get("status") != "sending" or int(details.get("attempts") or 0) != attempt:
                continue
            finished_at = datetime.now(UTC)
            details.update(
                {
                    "status": delivery_status,
                    "updated_at": finished_at.isoformat(),
                    "last_error": error,
                }
            )
            if delivery_status == "failed":
                details["next_attempt_at"] = (finished_at + timedelta(minutes=min(2**attempt, 60))).isoformat()
            event.details = details
            event.summary = (
                f"Booking {kind.replace('_', ' ')} email accepted by the provider."
                if delivery_status == "sent"
                else f"Booking {kind.replace('_', ' ')} email delegated to Foundry."
                if delivery_status == "delegated"
                else f"Booking {kind.replace('_', ' ')} email delivery failed."
            )
            await session.commit()
            if delivery_status == "sent":
                delivered += 1
        return delivered


# Authenticated endpoints


@router.post("/", response_model=BookingResponse, status_code=201)
async def create_booking(
    body: BookingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Client submits a booking request to an artist.
    Auto-creates a conversation thread between client and artist.
    """
    # Verify artist exists and is actually an artist
    artist_result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == body.artist_id))
    artist = artist_result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    if ROLE_RANK.get(artist.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=400, detail="Selected user is not an artist")

    # Reuse this client and artist's existing thread if they already have one.
    # This previously built a fresh Conversation on every call, so a client
    # booking a second time got a second thread with the same artist.
    conv, _created = await find_or_create_direct_conversation(
        db,
        {current_user.id, body.artist_id},
        name=current_user.name,
        created_by=current_user.id,
    )

    # Create booking linked to conversation
    booking = Booking(
        client_id=current_user.id,
        artist_id=body.artist_id,
        description=body.description,
        placement=body.placement,
        size_estimate=body.size_estimate,
        style_preference=body.style_preference,
        reference_urls=json.dumps(body.reference_urls) if body.reference_urls else None,
        preferred_date=body.preferred_date,
        preferred_time=body.preferred_time,
        client_notes=body.client_notes,
        conversation_id=conv.id,
    )
    db.add(booking)
    await db.flush()
    await _ensure_booking_intake_message(
        booking=booking,
        db=db,
        sender_id=current_user.id,
        client_name=current_user.name,
        client_email=current_user.email,
        client_phone=current_user.phone,
    )
    await _ensure_booking_pipeline_task(
        db=db,
        booking=booking,
        client_name=current_user.name or "Client",
        client_email=current_user.email or "not provided",
        client_phone=current_user.phone,
        artist_name=artist.name or "Artist",
        assignee_id=artist.id,
    )

    # Create in-app notification for the artist
    db.add(
        NotificationModel(
            user_id=body.artist_id,
            title="New Booking Request",
            body=f"{current_user.name} submitted a booking request.",
            notification_type="booking",
            action_url=ROUTES.HUDDLE,
        )
    )

    # Also notify founder + admin users (shop managers)
    mgr_result = await db.execute(select(BTTCOUser.id).where(BTTCOUser.role.in_(["founder", "admin"])))
    for (mgr_id,) in mgr_result.all():
        if mgr_id != body.artist_id and mgr_id != current_user.id:
            db.add(
                NotificationModel(
                    user_id=mgr_id,
                    title="New Booking Request",
                    body=f"{current_user.name} submitted a booking request.",
                    notification_type="booking",
                    action_url=ROUTES.HUDDLE,
                )
            )
    await db.flush()

    return await _enrich_booking(booking, db)


@router.post("/staff", response_model=BookingResponse, status_code=201)
async def staff_create_booking(
    body: StaffBookingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Admin/founder books on behalf of an artist (e.g. Kathryn booking Justin's clients).

    Accepts an existing client_id or guest contact details for a new/walk-in
    client. When created as confirmed with a date, fans out calendar + email +
    SMS via _on_booking_confirmed.
    """
    _require_role(current_user, "admin")

    artist = (await db.execute(select(BTTCOUser).where(BTTCOUser.id == body.artist_id))).scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    if ROLE_RANK.get(artist.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=400, detail="Selected user is not an artist")

    client = None
    if body.client_id:
        client = (await db.execute(select(BTTCOUser).where(BTTCOUser.id == body.client_id))).scalar_one_or_none()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
    if client is None and not (body.guest_name and (body.guest_email or body.guest_phone)):
        raise HTTPException(status_code=400, detail="Provide client_id or guest contact details")

    # A guest booking has no client account yet, so there is no participant set
    # to match on and the thread is necessarily new. Only the account-backed
    # case can reuse an existing thread.
    thread_name = (client.name if client else body.guest_name) or "Booking"
    if client:
        conv, _created = await find_or_create_direct_conversation(
            db,
            {artist.id, client.id},
            name=thread_name,
            created_by=current_user.id,
        )
    else:
        conv = Conversation(name=thread_name, is_group=False, created_by=current_user.id)
        db.add(conv)
        await db.flush()
        db.add(ConversationParticipant(conversation_id=conv.id, user_id=artist.id, role="member"))

    scheduled_time = _parse_local_time(body.scheduled_time) if body.scheduled_time else None
    booking = Booking(
        client_id=client.id if client else None,
        artist_id=artist.id,
        description=body.description,
        placement=body.placement,
        size_estimate=body.size_estimate,
        style_preference=body.style_preference,
        scheduled_date=body.scheduled_date,
        scheduled_time=scheduled_time,
        duration_minutes=body.duration_minutes,
        deposit_amount=body.deposit_amount,
        estimated_total=body.estimated_total,
        artist_notes=_append_artist_note(
            body.artist_notes,
            f"Created by {current_user.name} (staff) on behalf of {artist.name}.",
        ),
        status=body.status,
        guest_name=None if client else body.guest_name,
        guest_email=None if client else body.guest_email,
        guest_phone=None if client else body.guest_phone,
        reference_code=_generate_booking_ref(),
        source="staff",
        conversation_id=conv.id,
    )
    db.add(booking)
    await db.flush()

    _add_booking_lifecycle_event(
        db,
        event_type="created",
        source="staff_create",
        booking=booking,
        actor_id=current_user.id,
        summary=f"Booking created by {current_user.name} on behalf of {artist.name}.",
        details={"status": body.status, "artist_id": str(artist.id)},
    )
    db.add(
        NotificationModel(
            user_id=artist.id,
            title="New Booking (added by staff)",
            body=f"{current_user.name} booked an appointment for you.",
            notification_type="booking",
            action_url=ROUTES.HUDDLE,
        )
    )
    await db.flush()

    if booking.status == BookingStatus.CONFIRMED and body.confirm_calendar:
        await _on_booking_confirmed(db, booking, artist, current_user.id)
        await db.flush()

    return await _enrich_booking(booking, db)


@router.get("/", response_model=BookingListResponse)
async def list_bookings(
    status_filter: str | None = Query(None, alias="status"),
    show_all_param: bool = Query(False, alias="all"),
    for_artist: str | None = Query(None, alias="for_artist"),
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    List bookings relevant to the current user.
    - Clients see their own bookings
    - Artists see bookings assigned to them (artist or client side)
    - Assistants see the entire shop pipeline (they manage bookings on behalf
      of artists). Same scope as admin+ with ?all=true.
    - Founders/admins default to their own bookings; pass ?all=true for all
    - Admins can pass ?for_artist=<uuid> to scope to one artist's bookings
    """
    base_filter = []

    current_rank = ROLE_RANK.get(current_user.role, 0)
    is_admin = current_rank >= ROLE_RANK.get("admin", 3)
    is_assistant = current_user.role == "assistant"

    # Admins can pass ?all=true for reports/debrief.
    # Assistants always see the full shop pipeline - that IS their job.
    show_all = (show_all_param and is_admin) or is_assistant

    # Admins can scope to a specific artist's bookings
    if for_artist and is_admin:
        base_filter.append(or_(Booking.artist_id == UUID(for_artist), Booking.client_id == UUID(for_artist)))
    elif show_all:
        pass  # No filter - full shop view
    elif current_rank >= ROLE_RANK.get("artist", 2):
        # Artists + founders (default) see bookings assigned to them
        base_filter.append(or_(Booking.artist_id == current_user.id, Booking.client_id == current_user.id))
    else:
        # Clients see only their own
        base_filter.append(Booking.client_id == current_user.id)

    if status_filter:
        base_filter.append(Booking.status == status_filter)

    where_clause = and_(*base_filter) if base_filter else True

    # Count
    count_result = await db.execute(select(func.count()).select_from(Booking).where(where_clause))
    total = count_result.scalar()

    # Fetch
    result = await db.execute(
        select(Booking).where(where_clause).order_by(desc(Booking.created_at)).offset(skip).limit(limit)
    )
    bookings = result.scalars().all()

    enriched = [await _enrich_booking(b, db) for b in bookings]
    return BookingListResponse(bookings=enriched, total=total)


@router.get("/{booking_id}/history", response_model=list[BookingLifecycleEventResponse])
async def get_booking_history(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Staff view: append-only lifecycle timeline for one booking."""
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)

    event_result = await db.execute(
        select(BookingLifecycleEvent)
        .where(BookingLifecycleEvent.booking_id == booking.id)
        .order_by(BookingLifecycleEvent.created_at, BookingLifecycleEvent.id)
    )
    return list(event_result.scalars().all())


@router.get("/{booking_id}/schedule-suggestions", response_model=BookingScheduleSuggestionResponse)
async def get_booking_schedule_suggestions(
    booking_id: UUID,
    days: int = Query(default=45, ge=1, le=180),
    limit: int = Query(default=5, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Staff view: deterministic schedule suggestions from the shared scheduler."""
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)

    artist = await db.get(BTTCOUser, booking.artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    now_date = datetime.now(SHOP_TIMEZONE).date()
    start_date = booking.preferred_date if booking.preferred_date and booking.preferred_date >= now_date else now_date
    end_date = start_date + timedelta(days=days)
    (
        slot_interval_minutes,
        buffer_minutes,
        minimum_notice_hours,
        max_advance_days,
        allow_same_day,
    ) = _artist_booking_rules(artist)
    duration = await _resolve_slot_duration(
        db,
        artist_id=artist.id,
        session_type_id=None,
        size=booking.size_estimate,
        placement=booking.placement,
        duration_minutes=booking.duration_minutes or 120,
    )

    busy_blocks: list[dict] = []
    calendar_connected = artist_calendar_connected(artist)
    if calendar_connected:
        try:
            busy_blocks = await get_availability(
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
        except Exception as exc:
            logger.error("Schedule suggestion calendar read failed for booking %s: %s", booking.id, exc)
            busy_blocks = []
            calendar_connected = False

    confirmed_bookings = await _confirmed_bookings_for_range(
        db,
        artist_id=artist.id,
        start_date=start_date,
        end_date=end_date,
        exclude_booking_id=booking.id,
    )
    active_holds = await _active_holds_for_range(
        db,
        artist_id=artist.id,
        start_date=start_date,
        end_date=end_date,
    )
    personal_blocks = await _personal_blocks_for_range(
        db,
        artist_id=artist.id,
        start_date=start_date,
        end_date=end_date,
    )
    slots = _generate_available_slots(
        start_date=start_date,
        end_date=end_date,
        duration_minutes=duration,
        slot_interval_minutes=slot_interval_minutes,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
        busy_blocks=busy_blocks,
        confirmed_bookings=confirmed_bookings,
        calendar_connected=calendar_connected,
        limit=max(limit * 4, limit),
        active_holds=active_holds,
        personal_blocks=personal_blocks,
    )
    return BookingScheduleSuggestionResponse(
        booking_id=booking.id,
        artist_id=artist.id,
        duration_minutes=duration,
        calendar_connected=calendar_connected,
        suggestions=_rank_schedule_suggestions(
            slots,
            preferred_date=booking.preferred_date,
            preferred_time=booking.preferred_time,
            limit=limit,
        ),
    )


@router.get("/{booking_id}", response_model=BookingResponse)
async def get_booking(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Get a single booking. Must be client, artist, or admin."""
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if current_user.id != booking.client_id:
        _require_booking_management_access(current_user, booking)

    return await _enrich_booking(booking, db)


@router.patch("/{booking_id}", response_model=BookingResponse)
async def update_booking(
    booking_id: UUID,
    body: BookingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Update a booking. Only the assigned artist or admin can modify.
    """
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    _require_booking_management_access(current_user, booking)

    old_status = booking.status
    old_deposit_paid = booking.deposit_paid
    update_data = body.model_dump(exclude_unset=True)
    next_artist_id = update_data.pop("artist_id", None)
    allow_conflict = bool(update_data.pop("allow_conflict", False))
    allow_out_of_bounds = bool(update_data.pop("allow_out_of_bounds", False))
    override_reason = update_data.pop("override_reason", None)
    notify_client = bool(update_data.pop("notify_client", True))
    if next_artist_id and next_artist_id != booking.artist_id and update_data.get("status") == BookingStatus.CONFIRMED:
        raise HTTPException(status_code=422, detail="Reassign the BYOF request before confirming it")
    override_details: dict | None = None
    schedule_fields = {"scheduled_date", "scheduled_time", "duration_minutes"}
    if schedule_fields.intersection(update_data):
        next_date = update_data.get("scheduled_date", booking.scheduled_date)
        next_time_raw = update_data.get("scheduled_time", booking.scheduled_time)
        if next_date and next_time_raw:
            artist = await db.get(BTTCOUser, booking.artist_id)
            if not artist:
                raise HTTPException(status_code=404, detail="Artist not found")
            scheduled_time = _parse_local_time(next_time_raw)
            duration = await _resolve_slot_duration(
                db,
                artist_id=artist.id,
                session_type_id=None,
                size=booking.size_estimate,
                placement=booking.placement,
                duration_minutes=update_data.get("duration_minutes") or booking.duration_minutes or 120,
            )
            _, buffer_minutes, min_notice_hours, max_advance_days, allow_same_day = _artist_booking_rules(artist)
            busy_blocks: list[dict] = []
            if artist_calendar_connected(artist):
                try:
                    busy_blocks = await get_availability(
                        start_date=next_date.isoformat(),
                        end_date=next_date.isoformat(),
                        calendar_id=artist_calendar_id(artist),
                        refresh_token=artist.google_refresh_token,
                    )
                except Exception as exc:
                    logger.error("Booking patch calendar read failed for booking %s: %s", booking.id, exc)
                    raise HTTPException(status_code=502, detail="Calendar availability check failed") from exc
            available = await _slot_is_available(
                db,
                artist_id=artist.id,
                scheduled_date=next_date,
                scheduled_time=scheduled_time,
                duration_minutes=duration,
                buffer_minutes=buffer_minutes,
                minimum_notice_hours=min_notice_hours,
                max_advance_days=max_advance_days,
                allow_same_day=allow_same_day,
                busy_blocks=busy_blocks,
                exclude_booking_id=booking.id,
                work_schedule=_resolve_artist_work_schedule(artist),
            )
            if not available:
                override_details = await _authorize_schedule_override(
                    db,
                    current_user=current_user,
                    artist_id=artist.id,
                    scheduled_date=next_date,
                    scheduled_time=scheduled_time,
                    duration_minutes=duration,
                    buffer_minutes=buffer_minutes,
                    minimum_notice_hours=min_notice_hours,
                    max_advance_days=max_advance_days,
                    allow_same_day=allow_same_day,
                    busy_blocks=busy_blocks,
                    exclude_booking_id=booking.id,
                    allow_conflict=allow_conflict,
                    allow_out_of_bounds=allow_out_of_bounds,
                    override_reason=override_reason,
                )
            update_data["scheduled_time"] = scheduled_time
            update_data["duration_minutes"] = duration
    if update_data.get("status") == BookingStatus.CONFIRMED and booking.deposit_authorization_status == "authorized":
        current_artist = await db.get(BTTCOUser, booking.artist_id)
        if not current_artist:
            raise HTTPException(status_code=404, detail="Artist not found")
        await _capture_byof_authorization(booking, current_artist)
    for field, value in update_data.items():
        setattr(booking, field, value)
    booking.updated_at = datetime.now(UTC)
    if old_status != booking.status:
        _add_booking_lifecycle_event(
            db,
            event_type="status_changed",
            source="staff_update",
            booking=booking,
            actor_id=current_user.id,
            summary=f"Status changed from {old_status} to {booking.status}.",
            details={"old_status": old_status, "new_status": booking.status},
        )
    if schedule_fields.intersection(update_data):
        _add_booking_lifecycle_event(
            db,
            event_type="schedule_updated",
            source="staff_update",
            booking=booking,
            actor_id=current_user.id,
            summary=f"Schedule updated to {booking.scheduled_date or 'unscheduled'} {booking.scheduled_time or ''}.",
            details={
                "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
                "scheduled_time": booking.scheduled_time,
                "duration_minutes": booking.duration_minutes,
                "override": override_details,
            },
        )
    if override_details:
        _add_booking_lifecycle_event(
            db,
            event_type="schedule_override",
            source="staff_update",
            booking=booking,
            actor_id=current_user.id,
            summary=f"Scheduler override used for {booking.scheduled_date} {booking.scheduled_time}.",
            details=override_details,
        )

    if next_artist_id and next_artist_id != booking.artist_id:
        previous_artist = await db.get(BTTCOUser, booking.artist_id)
        next_artist = await db.get(BTTCOUser, next_artist_id)
        if not previous_artist or not next_artist or next_artist.role not in {"artist", "admin", "founder"}:
            raise HTTPException(status_code=422, detail="The receiving artist is not available")
        if current_user.role not in {"admin", "founder"}:
            raise HTTPException(status_code=403, detail="Only founder or admin can reassign a booking")
        previous_artist_id = booking.artist_id
        if booking.deposit_authorization_status in {"authorized", "pending_checkout"}:
            await _release_byof_authorization(booking, previous_artist, final_status="reauthorization_required")
        booking.artist_id = next_artist.id
        _add_booking_lifecycle_event(
            db,
            event_type="artist_reassigned",
            source="staff_update",
            booking=booking,
            actor_id=current_user.id,
            summary=f"Booking reassigned to {next_artist.name or next_artist.slug or next_artist.id}.",
            details={
                "previous_artist_id": str(previous_artist_id),
                "new_artist_id": str(next_artist.id),
                "deposit_authorization_status": booking.deposit_authorization_status,
            },
        )
        if notify_client:
            await _notify_booking_lifecycle(
                booking=booking,
                db=db,
                subject="Your tattoo request has a new artist",
                body=(
                    f"Your request is now with {next_artist.name or 'another Ryozan artist'}. "
                    "Any existing deposit hold was released. We will send a new authorization link if one is needed."
                ),
            )

    await db.flush()

    # Auto-complete 'first-booking' quest when a booking is confirmed
    if booking.status == BookingStatus.CONFIRMED and old_status != BookingStatus.CONFIRMED and booking.client_id:
        await check_and_complete_quest(db, booking.client_id, "first-booking")
    if booking.deposit_paid and not old_deposit_paid:
        await reward_user_by_id(
            db,
            booking.client_id,
            "booking.deposit_paid",
            str(booking.id),
        )
    if booking.status == BookingStatus.COMPLETED and old_status != BookingStatus.COMPLETED:
        await reward_user_by_id(
            db,
            booking.client_id,
            "appointment.completed",
            str(booking.id),
        )
    # On manual confirm: post to Google Calendar + email + SMS the client.
    # Idempotent vs the Stripe-deposit path (won't duplicate the calendar event).
    if booking.status == BookingStatus.CONFIRMED and old_status != BookingStatus.CONFIRMED:
        artist_row = await db.get(BTTCOUser, booking.artist_id)
        await _on_booking_confirmed(
            db,
            booking,
            artist_row,
            current_user.id,
            notify_client=notify_client,
        )
        await db.flush()
    if old_status == BookingStatus.PENDING and booking.status != BookingStatus.PENDING:
        await _close_booking_pipeline_tasks(db, booking.id)
        await db.flush()
    client_name, client_email = await _booking_contact(db, booking)
    artist_name_result = await db.execute(select(BTTCOUser.name).where(BTTCOUser.id == booking.artist_id))
    await _sync_hustl_deposit_followup_task(
        db=db,
        booking=booking,
        client_name=client_name,
        client_email=client_email,
        artist_name=artist_name_result.scalar_one_or_none() or "Artist",
        assignee_id=booking.artist_id,
    )

    return await _enrich_booking(booking, db)


async def _validate_staff_slot(
    db: AsyncSession,
    *,
    booking: Booking,
    artist: BTTCOUser,
    current_user: BTTCOUser,
    scheduled_date: date,
    scheduled_time_raw: str,
    duration_minutes: int | None,
    allow_conflict: bool,
    allow_out_of_bounds: bool,
    override_reason: str | None,
    action: str,
) -> tuple[str, int, dict | None]:
    """Check a staff-chosen slot against shop rules, holds, bookings and Google busy blocks.

    Returns the normalized time, resolved duration, and override details when an
    authorized staff override was needed. Shared by reschedule and approve.
    """
    scheduled_time = _parse_local_time(scheduled_time_raw)
    duration = await _resolve_slot_duration(
        db,
        artist_id=artist.id,
        session_type_id=None,
        size=booking.size_estimate,
        placement=booking.placement,
        duration_minutes=duration_minutes or booking.duration_minutes or 120,
    )
    _, buffer_minutes, min_notice_hours, max_advance_days, allow_same_day = _artist_booking_rules(artist)
    override_details: dict | None = None

    busy_blocks: list[dict] = []
    if artist_calendar_connected(artist):
        try:
            busy_blocks = await get_availability(
                start_date=scheduled_date.isoformat(),
                end_date=scheduled_date.isoformat(),
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
        except Exception as exc:
            logger.error("%s calendar read failed for booking %s: %s", action, booking.id, exc)
            raise HTTPException(status_code=502, detail="Calendar availability check failed") from exc

    available = await _slot_is_available(
        db,
        artist_id=artist.id,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        duration_minutes=duration,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=min_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
        busy_blocks=busy_blocks,
        exclude_booking_id=booking.id,
        work_schedule=_resolve_artist_work_schedule(artist),
    )
    if not available:
        override_details = await _authorize_schedule_override(
            db,
            current_user=current_user,
            artist_id=artist.id,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            duration_minutes=duration,
            buffer_minutes=buffer_minutes,
            minimum_notice_hours=min_notice_hours,
            max_advance_days=max_advance_days,
            allow_same_day=allow_same_day,
            busy_blocks=busy_blocks,
            exclude_booking_id=booking.id,
            allow_conflict=allow_conflict,
            allow_out_of_bounds=allow_out_of_bounds,
            override_reason=override_reason,
        )
    return scheduled_time, duration, override_details


@router.post("/{booking_id}/reschedule", response_model=BookingResponse)
async def reschedule_booking(
    booking_id: UUID,
    body: BookingReschedule,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Staff action: move a booking to a validated available slot.

    Unlike the generic PATCH endpoint, this checks shop rules, buffers,
    active holds, existing bookings, and Google Calendar busy blocks before
    it changes the booking.
    """
    result = await db.execute(select(Booking).where(Booking.id == booking_id).with_for_update())
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)
    if booking.status in {BookingStatus.CANCELLED, BookingStatus.NO_SHOW, BookingStatus.EXPIRED}:
        raise HTTPException(status_code=409, detail=f"Cannot reschedule a {booking.status} booking")

    artist = await db.get(BTTCOUser, booking.artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    scheduled_time, duration, override_details = await _validate_staff_slot(
        db,
        booking=booking,
        artist=artist,
        current_user=current_user,
        scheduled_date=body.scheduled_date,
        scheduled_time_raw=body.scheduled_time,
        duration_minutes=body.duration_minutes,
        allow_conflict=body.allow_conflict,
        allow_out_of_bounds=body.allow_out_of_bounds,
        override_reason=body.override_reason,
        action="Reschedule",
    )

    old_status = booking.status
    old_date = booking.scheduled_date
    old_time = booking.scheduled_time
    if booking.status == BookingStatus.QUOTED and not booking.deposit_paid:
        open_quote = await db.scalar(
            select(Quote.id).where(Quote.booking_id == booking.id, Quote.status.in_((QuoteStatus.SENT, "viewed")))
        )
        if open_quote:
            # An approved time with a deposit pending lives in its quote; moving
            # it here would confirm an unpaid booking behind a live Checkout.
            raise HTTPException(
                status_code=409,
                detail="Deposit is pending on the approved time. Approve again with the new time instead.",
            )
    booking.scheduled_date = body.scheduled_date
    booking.scheduled_time = scheduled_time
    booking.duration_minutes = duration
    if booking.status in {BookingStatus.PENDING, BookingStatus.QUOTED}:
        booking.status = BookingStatus.CONFIRMED
    if body.reason:
        booking.artist_notes = _append_artist_note(
            booking.artist_notes,
            f"Rescheduled from {old_date or 'unscheduled'} {old_time or ''} to {body.scheduled_date} {scheduled_time}. Reason: {body.reason}",
        )
    if override_details:
        booking.artist_notes = _append_artist_note(
            booking.artist_notes,
            f"Scheduler override used. Reason: {override_details['override_reason']}",
        )
    booking.updated_at = datetime.now(UTC)
    _add_booking_lifecycle_event(
        db,
        event_type="rescheduled",
        source="staff_reschedule",
        booking=booking,
        actor_id=current_user.id,
        summary=f"Rescheduled from {old_date or 'unscheduled'} {old_time or ''} to {body.scheduled_date} {scheduled_time}.",
        details={
            "old_status": old_status,
            "new_status": booking.status,
            "old_date": old_date.isoformat() if old_date else None,
            "old_time": old_time,
            "scheduled_date": body.scheduled_date.isoformat(),
            "scheduled_time": scheduled_time,
            "duration_minutes": duration,
            "reason": body.reason,
            "notify_client": body.notify_client,
            "override": override_details,
        },
    )
    if override_details:
        _add_booking_lifecycle_event(
            db,
            event_type="schedule_override",
            source="staff_reschedule",
            booking=booking,
            actor_id=current_user.id,
            summary=f"Scheduler override used for {body.scheduled_date} {scheduled_time}.",
            details=override_details,
        )
    await db.flush()

    if booking.status == BookingStatus.CONFIRMED and old_status != BookingStatus.CONFIRMED and booking.client_id:
        await check_and_complete_quest(db, booking.client_id, "first-booking")
    if old_status == BookingStatus.PENDING and booking.status != BookingStatus.PENDING:
        await _close_booking_pipeline_tasks(db, booking.id)
    client_name, client_email = await _booking_contact(db, booking)
    await _sync_hustl_deposit_followup_task(
        db=db,
        booking=booking,
        client_name=client_name,
        client_email=client_email,
        artist_name=artist.name or "Artist",
        assignee_id=booking.artist_id,
    )

    if booking.google_calendar_event_id and artist_calendar_connected(artist):
        client_name, client_email = await _booking_contact(db, booking)
        try:
            await update_booking_event(
                booking.google_calendar_event_id,
                _booking_calendar_payload(booking, client_name, client_email),
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
        except Exception as exc:
            logger.error("Calendar reschedule sync failed for booking %s: %s", booking.id, exc)

    if body.notify_client:
        await _notify_booking_lifecycle(
            booking=booking,
            db=db,
            subject="Your tattoo appointment was rescheduled",
            body=f"Your appointment has been rescheduled to {body.scheduled_date} at {scheduled_time}.",
        )

    await db.commit()
    await db.refresh(booking)
    return await _enrich_booking(booking, db)


def _artist_stripe_ready(artist: BTTCOUser) -> bool:
    """Deposit links route to the artist's Connect account, so both fields must be set."""
    return bool(artist.stripe_account_id and artist.stripe_onboarding_complete)


async def _approval_default_deposit(db: AsyncSession, booking: Booking, duration: int) -> tuple[float, str]:
    """Prefill for the approval card: saved deposit, then session type, then the shop duration rule."""
    if booking.deposit_amount is not None:
        return float(booking.deposit_amount), "booking"
    if booking.booking_kind == "consultation":
        return 0.0, "consultation"
    if booking.size_estimate:
        query = select(ArtistSessionType).where(
            ArtistSessionType.artist_id == booking.artist_id,
            ArtistSessionType.is_active.is_(True),
            ArtistSessionType.size == booking.size_estimate,
        )
        session_type = None
        if booking.placement:
            session_type = await db.scalar(
                query.where(ArtistSessionType.placements.contains([booking.placement])).limit(1)
            )
        if session_type is None:
            session_type = await db.scalar(query.order_by(ArtistSessionType.sort_order).limit(1))
        if session_type is not None:
            return float(session_type.deposit_amount), "session_type"
    amount, _tier = _hustl_deposit_rule_for_duration(duration)
    return float(amount or 0), "duration_rule"


async def _supersede_request_approved_intents(db: AsyncSession, booking: Booking) -> None:
    """Retire earlier approval emails that have not gone out, so a retry never sends a stale time or link."""
    rows = await db.execute(
        select(BookingLifecycleEvent).where(
            BookingLifecycleEvent.booking_id == booking.id,
            BookingLifecycleEvent.event_type == "booking_email_intent",
            BookingLifecycleEvent.details["kind"].astext == "request_approved",
            BookingLifecycleEvent.details["status"].astext.in_(("queued", "failed", "sending")),
        )
    )
    now = datetime.now(UTC).isoformat()
    for event in rows.scalars():
        event.details = {**(event.details or {}), "status": "superseded", "updated_at": now}


async def _close_quote_checkout(quote: Quote, artist: BTTCOUser) -> None:
    """Expire the Stripe Checkout a superseded quote opened, so its old slot and amount cannot be paid.

    A session the collector already completed means that deposit is paid: the
    approval stops instead of replacing it.
    """
    await _close_checkout_session(
        quote.stripe_checkout_session_id,
        artist,
        deposit_amount=quote.deposit_amount,
        idempotency_key=f"expire-quote-{quote.id}",
    )


async def _close_checkout_session(
    session_id: str | None,
    artist: BTTCOUser,
    *,
    deposit_amount,
    idempotency_key: str,
) -> None:
    if not session_id:
        return
    settings = get_settings()
    if settings.LOCAL_DEV_MODE and settings.STRIPE_SECRET_KEY.startswith("sk_live"):
        raise HTTPException(status_code=503, detail="Live Stripe checkout expiry is disabled in local dev")
    service_cents = int(round(float(deposit_amount or 0) * 100))
    routing = resolve_charge_routing(artist, customer_price_cents(service_cents), artist_share_base_cents=service_cents)
    client = get_stripe_client()
    try:
        session = client.v1.checkout.sessions.retrieve(session_id, {}, routing.request_options())
        status = getattr(session, "status", None) or (session.get("status") if isinstance(session, dict) else None)
        if status == "complete":
            raise HTTPException(
                status_code=409,
                detail="The collector already paid the deposit from an earlier approval. Refresh the booking.",
            )
        if status == "open":
            client.v1.checkout.sessions.expire(session_id, {}, routing.request_options(idempotency_key=idempotency_key))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Stripe checkout expiry failed (%s): %s", idempotency_key, exc)
        raise HTTPException(status_code=502, detail="Could not close the earlier deposit checkout") from exc


@router.get("/{booking_id}/approval-defaults", response_model=BookingApprovalDefaults)
async def get_booking_approval_defaults(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Staff: what the approval card prefills and whether a deposit link can go out."""
    booking = await db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)
    artist = await db.get(BTTCOUser, booking.artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    duration = booking.duration_minutes or await _resolve_slot_duration(
        db,
        artist_id=artist.id,
        session_type_id=None,
        size=booking.size_estimate,
        placement=booking.placement,
        duration_minutes=None,
    )
    amount, source = await _approval_default_deposit(db, booking, duration)
    return BookingApprovalDefaults(
        deposit_amount=amount,
        deposit_source=source,
        duration_minutes=duration,
        stripe_ready=_artist_stripe_ready(artist),
        can_self_onboard=current_user.id == artist.id,
        artist_name=artist.name or "Artist",
    )


@router.post("/{booking_id}/approve", response_model=BookingApprovalResult)
async def approve_booking_request(
    booking_id: UUID,
    body: BookingApprove,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Staff approves a request with one validated time and a deposit amount.

    Deposit above zero: the booking becomes quoted (approved, deposit pending)
    and the collector gets the "request approved" email. The email carries a
    deposit link (a single-slot quote, paid through the existing quote checkout
    and webhook) only when the artist's Stripe Connect account is ready.
    Zero deposit: the booking is confirmed and posted to the artist's calendar.
    The time is held by the booking itself only once the deposit is paid.
    """
    booking = await db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)
    if booking.status not in {BookingStatus.PENDING, BookingStatus.QUOTED}:
        raise HTTPException(status_code=409, detail=f"Cannot approve a {booking.status} booking")
    if booking.deposit_authorization_status == "authorized":
        # A bring-your-own-flash deposit is already held on the card; confirming
        # the booking captures it. A second deposit request would double-charge.
        raise HTTPException(
            status_code=409,
            detail="This booking already has a held deposit. Confirm it from the booking to capture that deposit.",
        )
    artist = await db.get(BTTCOUser, booking.artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    # The collector may be mid-Checkout on the current quote, holding its slot.
    # That quote is being replaced, so its hold must not block the new time.
    # (An error below rolls this back with the rest of the request.)
    await db.execute(
        update(BookingSlotHold)
        .where(
            BookingSlotHold.booking_id == booking.id,
            BookingSlotHold.status == "active",
            BookingSlotHold.inquiry_id.like("quote:%"),
        )
        .values(status="released")
    )

    scheduled_time, duration, override_details = await _validate_staff_slot(
        db,
        booking=booking,
        artist=artist,
        current_user=current_user,
        scheduled_date=body.scheduled_date,
        scheduled_time_raw=body.scheduled_time,
        duration_minutes=body.duration_minutes,
        allow_conflict=body.allow_conflict,
        allow_out_of_bounds=body.allow_out_of_bounds,
        override_reason=body.override_reason,
        action="Approval",
    )

    now = datetime.now(UTC)
    deposit = round(float(body.deposit_amount), 2)
    stripe_ready = _artist_stripe_ready(artist)
    old_status = booking.status
    # A public request can carry its own deposit Checkout from the slot hold.
    # Close it first: paying it would confirm the old time behind this approval.
    if booking.stripe_checkout_session_id and not booking.deposit_paid:
        await _close_checkout_session(
            booking.stripe_checkout_session_id,
            artist,
            deposit_amount=booking.deposit_amount,
            idempotency_key=f"expire-booking-{booking.id}",
        )
    booking.duration_minutes = duration
    booking.deposit_amount = deposit
    booking.updated_at = now
    artist_message = (body.artist_message or "").strip() or None

    # Only one live deposit link per booking: retire earlier unpaid quotes.
    open_quotes = (
        await db.execute(
            select(Quote).where(Quote.booking_id == booking.id, Quote.status.in_((QuoteStatus.SENT, "viewed")))
        )
    ).scalars()
    for stale in open_quotes:
        await _close_quote_checkout(stale, artist)
        stale.status = QuoteStatus.EXPIRED

    deposit_url: str | None = None
    quote: Quote | None = None
    if deposit > 0:
        booking.status = BookingStatus.QUOTED
        # The time is not held until the deposit is paid; a schedule left from a
        # public slot hold or an earlier pass must not keep blocking the calendar.
        booking.scheduled_date = None
        booking.scheduled_time = None
        if stripe_ready:
            quote = Quote(
                booking_id=booking.id,
                deposit_amount=deposit,
                available_slots_json=json.dumps(
                    [
                        {
                            "date": body.scheduled_date.isoformat(),
                            "time": scheduled_time,
                            "duration_minutes": duration,
                            # Staff validated this slot against the artist's own
                            # hours (or authorized an override); acceptance reuses that.
                            "staff_approved": True,
                            "staff_override": bool(override_details),
                        }
                    ]
                ),
                artist_message=artist_message,
                expires_at=now + timedelta(days=7),
            )
            db.add(quote)
            await db.flush()
            deposit_url = f"{get_settings().FRONTEND_URL}/quote/{quote.token}"
    else:
        booking.status = BookingStatus.CONFIRMED
        booking.scheduled_date = body.scheduled_date
        booking.scheduled_time = scheduled_time

    if override_details:
        booking.artist_notes = _append_artist_note(
            booking.artist_notes,
            f"Scheduler override used on approval. Reason: {override_details['override_reason']}",
        )
    approval_key = uuid_mod.uuid4().hex
    _add_booking_lifecycle_event(
        db,
        event_type="request_approved",
        source="staff_approval",
        booking=booking,
        actor_id=current_user.id,
        summary=(
            f"Request approved for {body.scheduled_date} {scheduled_time}; "
            + (f"${deposit:,.2f} deposit pending." if deposit > 0 else "no deposit, confirmed.")
        ),
        details={
            "old_status": old_status,
            "new_status": booking.status,
            "scheduled_date": body.scheduled_date.isoformat(),
            "scheduled_time": scheduled_time,
            "duration_minutes": duration,
            "deposit_amount": deposit,
            "stripe_ready": stripe_ready,
            "deposit_link": bool(deposit_url),
            "quote_id": str(quote.id) if quote else None,
            "override": override_details,
            "approval_key": approval_key,
        },
    )
    if override_details:
        _add_booking_lifecycle_event(
            db,
            event_type="schedule_override",
            source="staff_approval",
            booking=booking,
            actor_id=current_user.id,
            summary=f"Scheduler override used for {body.scheduled_date} {scheduled_time}.",
            details=override_details,
        )

    # Collector-facing amounts carry the shop's customer price increase, the
    # same figure the deposit checkout charges.
    collector_deposit = customer_price_cents(int(round(deposit * 100))) / 100 if deposit > 0 else 0.0
    client_name, client_email = await _booking_contact(db, booking)
    email_queued = False
    await _supersede_request_approved_intents(db, booking)
    if client_email:
        _queue_booking_email_intent(
            db,
            booking=booking,
            kind="request_approved",
            recipient=client_email,
            intent_key=approval_key,
            payload={
                "client_name": client_name,
                "artist_name": artist.name or "Artist",
                "reference_code": booking.reference_code or str(booking.id)[:8].upper(),
                "scheduled_date": body.scheduled_date.isoformat(),
                "scheduled_time": scheduled_time,
                "duration_minutes": duration,
                "deposit_amount": collector_deposit,
                "deposit_url": deposit_url,
                "artist_message": artist_message,
                "booking_kind": booking.booking_kind,
                "conversation_id": str(booking.conversation_id) if booking.conversation_id else None,
            },
        )
        email_queued = True

    if booking.conversation_id:
        day = f"{body.scheduled_date.strftime('%a, %b')} {body.scheduled_date.day}"
        when = f"{day} at {_format_time_12hr(scheduled_time) or scheduled_time}"
        db.add(
            Message(
                conversation_id=booking.conversation_id,
                sender_id=current_user.id,
                content=(
                    f"Request approved for {when}. "
                    + (
                        f"${collector_deposit:,.2f} deposit "
                        + ("link sent." if deposit_url else "due. Your artist will reach out about paying it.")
                        if deposit > 0
                        else "No deposit needed; booked."
                    )
                ),
                message_type="booking_approval",
                metadata_json=json.dumps(
                    {
                        "booking_id": str(booking.id),
                        "deposit_amount": deposit,
                        "deposit_link": bool(deposit_url),
                        "status": booking.status,
                    }
                ),
                created_at=now,
            )
        )
    await db.flush()

    if old_status == BookingStatus.PENDING:
        await _close_booking_pipeline_tasks(db, booking.id)
    await _sync_hustl_deposit_followup_task(
        db=db,
        booking=booking,
        client_name=client_name,
        client_email=client_email,
        artist_name=artist.name or "Artist",
        assignee_id=booking.artist_id,
    )
    if booking.status == BookingStatus.CONFIRMED:
        await _on_booking_confirmed(db, booking, artist, current_user.id, notify_client=False)
        if booking.client_id:
            await check_and_complete_quest(db, booking.client_id, "first-booking")

    await db.commit()
    if email_queued:
        asyncio.create_task(process_booking_email_outbox_once())
    await db.refresh(booking)
    return BookingApprovalResult(
        booking=await _enrich_booking(booking, db),
        status=booking.status,
        deposit_amount=deposit,
        deposit_link_sent=bool(deposit_url),
        stripe_ready=stripe_ready,
        email_queued=email_queued,
        deposit_url=deposit_url,
        scheduled_date=body.scheduled_date,
        scheduled_time=scheduled_time,
    )


@router.post("/{booking_id}/cancel", response_model=BookingResponse)
async def cancel_booking(
    booking_id: UUID,
    body: BookingCancel,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Staff action: cancel a booking and optionally remove its calendar event."""
    result = await db.execute(select(Booking).where(Booking.id == booking_id).with_for_update())
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)
    if booking.status == BookingStatus.CANCELLED:
        return await _enrich_booking(booking, db)

    artist = await db.get(BTTCOUser, booking.artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    old_status = booking.status
    booking.status = BookingStatus.CANCELLED
    if booking.deposit_authorization_status in {"authorized", "pending_checkout"}:
        await _release_byof_authorization(booking, artist)
    if body.reason:
        booking.artist_notes = _append_artist_note(booking.artist_notes, f"Cancelled. Reason: {body.reason}")
    booking.updated_at = datetime.now(UTC)
    await _close_booking_pipeline_tasks(db, booking.id)
    client_name, client_email = await _booking_contact(db, booking)
    await _sync_hustl_deposit_followup_task(
        db=db,
        booking=booking,
        client_name=client_name,
        client_email=client_email,
        artist_name=artist.name or "Artist",
        assignee_id=booking.artist_id,
    )
    _add_booking_lifecycle_event(
        db,
        event_type="cancelled",
        source="staff_cancel",
        booking=booking,
        actor_id=current_user.id,
        summary=f"Cancelled from {old_status}.",
        details={
            "old_status": old_status,
            "reason": body.reason,
            "notify_client": body.notify_client,
            "delete_calendar_event": body.delete_calendar_event,
            "deposit_authorization_status": booking.deposit_authorization_status,
        },
    )

    if body.delete_calendar_event and booking.google_calendar_event_id and artist_calendar_connected(artist):
        deleted = await delete_booking_event(
            booking.google_calendar_event_id,
            calendar_id=artist_calendar_id(artist),
            refresh_token=artist.google_refresh_token,
        )
        if deleted:
            booking.google_calendar_event_id = None

    if body.notify_client:
        await _notify_booking_lifecycle(
            booking=booking,
            db=db,
            subject="Your tattoo appointment was cancelled",
            body=f"Your appointment has been cancelled. {body.reason or ''}".strip(),
        )

    logger.info("Booking %s cancelled from %s by %s", booking.id, old_status, current_user.id)
    await db.commit()
    await db.refresh(booking)
    return await _enrich_booking(booking, db)


# Gala Assist


@router.post("/{booking_id}/gala-assist")
async def gala_assist(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Ask Gala to draft a response message for a pending booking request.
    Returns { draft: str } - the artist reviews and sends manually.
    Artist+ only; must be the assigned artist or an admin.
    """
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    is_artist = current_user.id == booking.artist_id
    if not (is_admin or is_artist):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Resolve client name
    client_name = "there"
    if booking.client_id:
        r = await db.execute(select(BTTCOUser.name).where(BTTCOUser.id == booking.client_id))
        client_name = r.scalar_one_or_none() or "there"
    elif booking.guest_name:
        client_name = booking.guest_name.split()[0]

    artist_name = current_user.name or "the artist"
    placement = booking.placement or "tattoo"
    description = booking.description or ""
    style = booking.style_preference or ""

    settings = get_settings()
    from app.services.ai_routing import require_ai_access

    require_ai_access(settings, current_user)
    if not settings.ARK_MODEL_GATEWAY_KEY:
        raise HTTPException(status_code=503, detail="AI not configured. Set NEBIUS_API_KEY.")

    prompt = f"""You are Gala, the AI assistant for {artist_name} at Ryozan Studio.
Draft a short, warm, professional response to a new booking request.

Client name: {client_name}
Placement: {placement}
{f"Style: {style}" if style else ""}
{f"Description: {description}" if description else ""}

Requirements:
- Address the client by first name
- Acknowledge their project with specific detail (placement, style, or description)
- Express genuine enthusiasm without sounding generic
- Ask 1-2 clarifying questions if info is sparse (size, color/black-and-grey preference, reference images)
- Keep it to 3-4 sentences max
- Sound like a real tattoo artist, not corporate
- Do not use emojis
- Do not include a subject line or sign-off, just the message body

Write only the message body."""

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
        system_prompt=(
            "You draft concise booking replies for Ryozan Studio "
            "Sound like a real tattoo artist, never use emojis, and write only the message body."
        ),
        messages=[{"role": "user", "content": prompt}],
    )

    choice = ((response.get("choices") or [{}])[0]).get("message") or {}
    draft = (choice.get("content") or "").strip()
    if not draft:
        raise HTTPException(status_code=502, detail="AI returned an empty draft")

    usage = response.get("usage") or {}
    await log_usage(
        db,
        "bookings/gala-assist",
        int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        current_user.id,
    )
    logger.info("Gala drafted response for booking %s", booking_id)
    return {"draft": draft, "booking_id": str(booking_id)}


@router.post("/{booking_id}/conversation")
async def ensure_booking_conversation(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Ensure a conversation thread exists for this booking and return
    its id. Creates one on demand if the booking has no conversation
    yet. The conversation links the assigned artist + client (or a
    pending-account guest promoted from guest_email).

    Used by the Courier toolkit before sending the first message on
    a booking that was submitted as a guest without an account.
    """
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    _require_booking_management_access(current_user, booking)

    conv, _client_id, _client_name, _repairs, skip_reason = await _repair_booking_conversation(booking, db)
    if skip_reason or not conv:
        raise HTTPException(
            status_code=400,
            detail="Booking has no client or guest email - cannot create conversation",
        )

    return {"conversation_id": str(conv.id)}
