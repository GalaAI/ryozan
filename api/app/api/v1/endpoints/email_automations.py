"""
Email automation endpoints — scheduled lifecycle emails.

Cron-triggered (n8n or Railway cron):
  POST /email-automations/process  — [API KEY] fire all pending automated emails

Sends:
  1. Appointment reminders (24h before confirmed sessions)
  2. Healing follow-ups (2 weeks after session)
  3. Healing follow-ups (4 weeks after session)
  4. Install app reminder (3h after registration, if no push subscription)
  5. Install app reminder (24h after registration, if no push subscription)
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.status import BookingStatus
from app.core.config import get_settings
from app.core.database import get_db
from app.core.routes import ROUTES
from app.models.booking import Booking
from app.models.booking_draft import BookingDraft
from app.models.document import DocumentInstance
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services.email import (
    FROM_ADMIN,
    FROM_SHOP,
    build_appointment_reminder_html,
    build_booking_resume_reminder_html,
    build_healing_followup_html,
    build_install_reminder_html,
    build_scroll_request_html,
    render_db_template,
    render_transactional_email,
    send_email,
    should_notify,
)
from app.services.email_foundry import (
    dispatch_foundry_trigger,
    process_foundry_campaigns,
    process_foundry_trigger_queue,
)
from app.services.push import send_push_notification
from app.services.sms import send_from_template

logger = logging.getLogger("ryozan.email_automations")

router = APIRouter(prefix="/email-automations", tags=["email-automations"])


def _automation_meta(raw_notes: str | None) -> dict:
    if not raw_notes or not raw_notes.startswith("{"):
        return {}
    try:
        parsed = json.loads(raw_notes)
    except (json.JSONDecodeError, AttributeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _can_send_booking_sms_reminder(booking: Booking) -> bool:
    return bool(booking.sms_opt_in and booking.sms_opt_in_at and booking.guest_phone)


@router.post("/process")
async def process_automated_emails(
    db: AsyncSession = Depends(get_db),
    x_api_key: str = Header(alias="X-Api-Key"),
):
    """
    [API KEY] Process all pending automated emails.

    Called by n8n/cron every hour. Requires X-Api-Key header.
    Idempotent: uses booking.notes JSON to track which emails were sent.
    """
    settings = get_settings()
    if x_api_key != settings.DOC_REMINDER_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    now = datetime.now(UTC)
    reminders_sent = 0
    sms_reminders_sent = 0
    followups_sent = 0
    booking_resume_reminders_sent = 0

    # ── 1. Appointment Reminders (24h before) ────────────────────────
    # Find confirmed bookings with scheduled_date tomorrow
    tomorrow_start = (now + timedelta(hours=20)).replace(hour=0, minute=0, second=0)
    tomorrow_end = tomorrow_start + timedelta(days=1)

    reminder_query = select(Booking).where(
        and_(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.scheduled_date >= tomorrow_start.date(),
            Booking.scheduled_date <= tomorrow_end.date(),
        )
    )
    result = await db.execute(reminder_query)
    upcoming_bookings = result.scalars().all()

    for booking in upcoming_bookings:
        # Check if reminders were already sent. Stored in notes JSON for now
        # because no dedicated automation state table exists yet.
        meta = _automation_meta(booking.artist_notes)

        email_already_sent = bool(meta.get("reminder_sent"))
        sms_already_sent = bool(meta.get("sms_reminder_24h_sent"))
        if email_already_sent and (sms_already_sent or not _can_send_booking_sms_reminder(booking)):
            continue

        # Get client info
        client_email = None
        client_name = booking.guest_name or "there"

        if booking.client_id:
            client_result = await db.execute(select(User.email, User.name).where(User.id == booking.client_id))
            client_row = client_result.first()
            if client_row:
                client_email, client_name = client_row
                # Check notification preferences
                if email_already_sent or not await should_notify(db, booking.client_id, "email", "bookings"):
                    client_email = None
        else:
            client_email = booking.guest_email

        # Get artist name
        artist_result = await db.execute(select(User.name).where(User.id == booking.artist_id))
        artist_name = artist_result.scalar_one_or_none() or "your artist"

        if client_email and not email_already_sent:
            fallback_subject = f"Reminder: Your session with {artist_name} is tomorrow"
            fallback_html = build_appointment_reminder_html(
                client_name=client_name.split(" ")[0],
                artist_name=artist_name,
                scheduled_date=str(booking.scheduled_date),
                scheduled_time=booking.scheduled_time,
                description=booking.description or "Tattoo session",
                reference_code=booking.reference_code or "",
            )
            foundry_variables = {
                "client_name": client_name.split(" ")[0],
                "artist_name": artist_name,
                "scheduled_date": str(booking.scheduled_date),
                "scheduled_time": booking.scheduled_time or "",
                "date_num": str(booking.scheduled_date.day),
                "date_mon": booking.scheduled_date.strftime("%b").upper(),
                "description": booking.description or "Tattoo session",
                "duration_minutes": str(booking.duration_minutes or ""),
                "reference_code": booking.reference_code or "",
                "confirm_url": f"{settings.FRONTEND_URL}{ROUTES.BOOKING_REF.format(ref=booking.reference_code or booking.id)}",
            }
            dispatch = {"matched": []}
            try:
                dispatch = await dispatch_foundry_trigger(
                    db,
                    "session_minus_24h",
                    recipient=client_email,
                    variables=foundry_variables,
                    event_id=f"booking:{booking.id}:session-minus-24h",
                    delivery={"from_alias": FROM_SHOP},
                )
            except Exception as exc:
                logger.error("Foundry appointment trigger failed for %s: %s", booking.reference_code, exc)

            try:
                if "appointment_reminder" not in dispatch["matched"]:
                    rendered = await render_transactional_email(
                        "appointment_reminder",
                        foundry_variables,
                        fallback_subject=fallback_subject,
                        fallback_html=fallback_html,
                    )
                    await send_email(
                        to=client_email,
                        subject=rendered.subject,
                        html_body=rendered.html_body,
                        from_alias=FROM_SHOP,
                    )
                meta["reminder_sent"] = now.isoformat()
                reminders_sent += 1
                logger.info("Reminder sent for booking %s to %s", booking.reference_code, client_email)
            except Exception as exc:
                logger.error("Reminder failed for %s: %s", booking.reference_code, exc)

        if _can_send_booking_sms_reminder(booking) and not sms_already_sent:
            try:
                sms_log = await send_from_template(
                    db=db,
                    slug="appointment_reminder_24h",
                    to=booking.guest_phone or "",
                    variables={
                        "artist_name": artist_name,
                        "time": booking.scheduled_time or "",
                    },
                    conversation_id=booking.conversation_id,
                    user_id=booking.client_id,
                    is_transactional=True,
                    skip_opt_in_check=True,
                )
                if sms_log and sms_log.status not in {"failed"}:
                    meta["sms_reminder_24h_sent"] = now.isoformat()
                    sms_reminders_sent += 1
                    logger.info("SMS reminder sent for booking %s to %s", booking.reference_code, booking.guest_phone)
            except Exception as exc:
                logger.error("SMS reminder failed for %s: %s", booking.reference_code, exc)

        if meta:
            booking.artist_notes = json.dumps(meta)

    # ── 2. Healing Follow-ups (2 weeks after) ────────────────────────
    two_weeks_ago = (now - timedelta(weeks=2)).date()
    four_weeks_ago = (now - timedelta(weeks=4)).date()

    followup_query = select(Booking).where(
        and_(
            Booking.status == BookingStatus.COMPLETED,
            or_(
                Booking.scheduled_date == two_weeks_ago,
                Booking.scheduled_date == four_weeks_ago,
            ),
        )
    )
    result = await db.execute(followup_query)
    completed_bookings = result.scalars().all()

    for booking in completed_bookings:
        meta = _automation_meta(booking.artist_notes)

        weeks_since = 2 if booking.scheduled_date == two_weeks_ago else 4
        flag_key = f"followup_{weeks_since}w_sent"

        if meta.get(flag_key):
            continue

        # Get client info
        client_email = None
        client_name = booking.guest_name or "there"

        if booking.client_id:
            client_result = await db.execute(select(User.email, User.name).where(User.id == booking.client_id))
            client_row = client_result.first()
            if client_row:
                client_email, client_name = client_row
                if not await should_notify(db, booking.client_id, "email", "bookings"):
                    continue
        else:
            client_email = booking.guest_email

        if not client_email:
            continue

        # Get artist name
        artist_result = await db.execute(select(User.name).where(User.id == booking.artist_id))
        artist_name = artist_result.scalar_one_or_none() or "your artist"

        fallback_subject = f"How's your tattoo healing? ({weeks_since} week check-in)"
        fallback_html = build_healing_followup_html(
            client_name=client_name.split(" ")[0],
            artist_name=artist_name,
            tattoo_description=booking.description or "your tattoo",
            weeks_since=weeks_since,
            reference_code=booking.reference_code or "",
        )
        rendered = await render_transactional_email(
            "aftercare_followup",
            {
                "client_name": client_name.split(" ")[0],
                "artist_name": artist_name,
                "description": booking.description or "your tattoo",
                "days_since": str(weeks_since * 7),
                "reference_code": booking.reference_code or "",
                "checkin_url": f"{settings.FRONTEND_URL}{ROUTES.BOOKING_REF.format(ref=booking.reference_code or booking.id)}",
            },
            fallback_subject=fallback_subject,
            fallback_html=fallback_html,
            legacy_slug="healing_followup",
        )

        try:
            await send_email(
                to=client_email,
                subject=rendered.subject,
                html_body=rendered.html_body,
                from_alias=FROM_SHOP,
            )
            meta[flag_key] = now.isoformat()
            booking.artist_notes = json.dumps(meta) if meta != {} else booking.artist_notes
            followups_sent += 1
            logger.info("Follow-up (%dw) sent for booking %s", weeks_since, booking.reference_code)
        except Exception as exc:
            logger.error("Follow-up failed for %s: %s", booking.reference_code, exc)

    # ── Foundry lifecycle trigger schedules ───────────────────────────────
    # These are separate from the built-in 2/4 week compatibility follow-ups.
    # Nothing sends unless the founder explicitly binds a template to one of
    # the locked session_done, heal_day_3, or heal_day_7 trigger ids.
    foundry_lifecycle_sent = 0
    foundry_lifecycle_queued = 0
    foundry_lifecycle_failed = 0
    for days_since, trigger in ((0, "session_done"), (3, "heal_day_3"), (7, "heal_day_7")):
        lifecycle_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.status == BookingStatus.COMPLETED,
                    Booking.scheduled_date == (now - timedelta(days=days_since)).date(),
                )
            )
        )
        for booking in lifecycle_result.scalars().all():
            client_email = booking.guest_email
            client_name = booking.guest_name or "there"
            if booking.client_id:
                client_result = await db.execute(select(User.email, User.name).where(User.id == booking.client_id))
                client_row = client_result.first()
                if not client_row or not await should_notify(db, booking.client_id, "email", "bookings"):
                    continue
                client_email, client_name = client_row
            if not client_email:
                continue
            artist_result = await db.execute(select(User.name).where(User.id == booking.artist_id))
            artist_name = artist_result.scalar_one_or_none() or "your artist"
            try:
                dispatch = await dispatch_foundry_trigger(
                    db,
                    trigger,
                    recipient=client_email,
                    variables={
                        "client_name": client_name.split(" ")[0],
                        "artist_name": artist_name,
                        "description": booking.description or "your tattoo",
                        "days_since": str(days_since),
                        "reference_code": booking.reference_code or "",
                        "checkin_url": f"{settings.FRONTEND_URL}{ROUTES.BOOKING_REF.format(ref=booking.reference_code or booking.id)}",
                    },
                    event_id=f"booking:{booking.id}:{trigger}",
                )
            except Exception as exc:
                logger.error("Foundry %s trigger failed for %s: %s", trigger, booking.reference_code, exc)
                foundry_lifecycle_failed += 1
                continue
            foundry_lifecycle_sent += dispatch["sent"]
            foundry_lifecycle_queued += dispatch["queued"]
            foundry_lifecycle_failed += dispatch["failed"]

    await db.flush()

    # ── 3. Booking Resume Reminders (single follow-up) ────────────────────
    reminder_cutoff = now - timedelta(hours=2)
    stale_cutoff = now - timedelta(days=14)
    draft_result = await db.execute(
        select(BookingDraft).where(
            and_(
                BookingDraft.status == "draft",
                BookingDraft.guest_email.is_not(None),
                BookingDraft.reference_code.is_(None),
                BookingDraft.updated_at <= reminder_cutoff,
                BookingDraft.created_at >= stale_cutoff,
            )
        )
    )
    draft_rows = draft_result.scalars().all()
    for draft in draft_rows:
        guest_email = (draft.guest_email or "").strip().lower()
        if not guest_email:
            continue
        first_name = (draft.guest_name or "").strip().split(" ")[0] or "there"
        book_url = f"{settings.FRONTEND_URL}{ROUTES.BOOK}"
        join_url = f"{settings.FRONTEND_URL}/register?intent=club"
        html = build_booking_resume_reminder_html(first_name, book_url, join_url)
        try:
            await send_email(
                to=guest_email,
                subject="Don't Forget! Finish your booking",
                html_body=html,
                from_alias=FROM_SHOP,
            )
            draft.status = "reminder_sent"
            draft.updated_at = now
            booking_resume_reminders_sent += 1
            logger.info("Booking resume reminder sent for inquiry %s", draft.inquiry_id)
        except Exception as exc:
            logger.error("Booking resume reminder failed for inquiry %s: %s", draft.inquiry_id, exc)

    await db.flush()

    # ── 4. Install App Reminders ──────────────────────────────────────────
    # Window approach: cron runs hourly, so each window is checked exactly once.
    # 3h window:  registered between (now - 4h) and (now - 3h)
    # 24h window: registered between (now - 25h) and (now - 24h)
    install_reminders_sent = 0

    no_push_sub = ~exists().where(PushSubscription.user_id == User.id)

    for hours_ago, slug, is_final in [
        (3, "install_reminder_3h", False),
        (24, "install_reminder_24h", True),
    ]:
        window_end = now - timedelta(hours=hours_ago)
        window_start = window_end - timedelta(hours=1)

        candidates = await db.execute(
            select(User).where(
                and_(
                    User.created_at >= window_start,
                    User.created_at < window_end,
                    User.role == "collector",
                    no_push_sub,
                )
            )
        )
        users = candidates.scalars().all()

        for user in users:
            if not user.email:
                continue
            first_name = (user.name or "there").split(" ")[0]

            html = await render_db_template(slug, {"client_name": first_name}) or build_install_reminder_html(
                client_name=first_name, is_final=is_final
            )

            subject = "Last chance: install the Ryozan app" if is_final else "You're one step away: Ryozan App"

            try:
                await send_email(to=user.email, subject=subject, html_body=html, from_alias=FROM_SHOP)
                install_reminders_sent += 1
                logger.info("Install reminder (%s) sent to user %s", slug, user.id)
            except Exception as exc:
                logger.error("Install reminder (%s) failed for user %s: %s", slug, user.id, exc)

    # ── 5. Artist Scroll Daily Reminders ─────────────────────────────────
    scroll_reminders_sent = 0
    cutoff = now - timedelta(hours=24)
    scroll_request_exists = exists().where(
        and_(
            Notification.user_id == User.id,
            Notification.notification_type.in_(("scroll_request", "scroll_remind")),
        )
    )
    recent_scroll_reminder_exists = exists().where(
        and_(
            Notification.user_id == User.id,
            Notification.notification_type == "scroll_remind",
            Notification.created_at >= cutoff,
        )
    )
    completed_scroll_exists = exists().where(
        and_(
            DocumentInstance.user_id == User.id,
            DocumentInstance.category == "scroll",
            DocumentInstance.status == "signed",
        )
    )

    pending_artists_result = await db.execute(
        select(User).where(
            and_(
                User.role.in_(("artist", "admin", "founder")),
                scroll_request_exists,
                ~recent_scroll_reminder_exists,
                ~completed_scroll_exists,
            )
        )
    )
    pending_artists = pending_artists_result.scalars().all()

    for artist in pending_artists:
        notif = Notification(
            user_id=artist.id,
            title="Reminder: Your Artist Scroll is Still Incomplete",
            body=(
                "Your Artist Scroll is still incomplete and needs to be filled out immediately. "
                "Open the portal, complete each section, and sign it for facility records."
            ),
            notification_type="scroll_remind",
            action_url="/dashboard?panel=codex",
            is_read=False,
        )
        db.add(notif)

        try:
            await send_push_notification(
                db,
                artist.id,
                title="Reminder: Artist Scroll Still Incomplete",
                body="Your Artist Scroll still needs to be filled out. Open the portal and sign it today.",
                url="/dashboard?panel=codex",
            )
        except Exception as exc:
            logger.error("Artist Scroll push reminder failed for user %s: %s", artist.id, exc)

        if artist.email:
            first_name = (artist.name or "there").split(" ")[0]
            html = await render_db_template(
                "scroll_request",
                {"artist_name": first_name},
            ) or build_scroll_request_html(first_name, is_reminder=True)
            try:
                await send_email(
                    to=artist.email,
                    subject="Reminder: Your Artist Scroll is Still Incomplete",
                    html_body=html,
                    from_alias=FROM_ADMIN,
                )
            except Exception as exc:
                logger.error("Artist Scroll email reminder failed for user %s: %s", artist.id, exc)

        scroll_reminders_sent += 1

    foundry_campaigns = await process_foundry_campaigns(db, now=now)
    foundry_trigger_queue = await process_foundry_trigger_queue(db, now=now)

    return {
        "processed_at": now.isoformat(),
        "reminders_sent": reminders_sent,
        "sms_reminders_sent": sms_reminders_sent,
        "followups_sent": followups_sent,
        "foundry_lifecycle_sent": foundry_lifecycle_sent,
        "foundry_lifecycle_queued": foundry_lifecycle_queued,
        "foundry_lifecycle_failed": foundry_lifecycle_failed,
        "booking_resume_reminders_sent": booking_resume_reminders_sent,
        "install_reminders_sent": install_reminders_sent,
        "scroll_reminders_sent": scroll_reminders_sent,
        "foundry_campaigns_due": foundry_campaigns["due"],
        "foundry_campaigns_sent": foundry_campaigns["sent"],
        "foundry_campaigns_failed": foundry_campaigns["failed"],
        "foundry_trigger_events_due": foundry_trigger_queue["due"],
        "foundry_trigger_events_sent": foundry_trigger_queue["sent"],
        "foundry_trigger_events_failed": foundry_trigger_queue["failed"],
    }
