"""
Calendar endpoints: Google Calendar sync for bookings.

- Availability queries for the booking form
- Upcoming events for the artist dashboard
- Manual sync trigger for individual bookings
- Direct event CRUD (create / update / delete) for Gala AI
"""

import contextlib
import logging
import re
import secrets
from datetime import UTC, timedelta
from datetime import date as d
from datetime import datetime as dt
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.status import BookingStatus
from app.constants.timezones import SHOP_TIMEZONE
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.booking_slot_hold import BookingSlotHold
from app.models.session_type import ArtistSessionType
from app.models.time_block import TimeBlock
from app.models.user import User
from app.services.calendar_sync import (
    artist_calendar_connected,
    artist_calendar_id,
    calendar_connection_configured,
    create_booking_event,
    delete_booking_event,
    get_availability,
    get_upcoming_events,
    update_booking_event,
    workspace_delegation_enabled,
)

# ── Request bodies ───────────────────────────────


class EventCreateBody(BaseModel):
    summary: str
    start: str  # ISO datetime or YYYY-MM-DD
    end: str  # ISO datetime or YYYY-MM-DD
    description: str | None = None
    location: str | None = None
    all_day: bool = False
    calendar_id: str = "primary"


class EventUpdateBody(BaseModel):
    summary: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None
    location: str | None = None
    calendar_id: str = "primary"


class AvailableSlot(BaseModel):
    date: str
    time: str
    start: str
    end: str
    label: str
    duration_minutes: int
    source: str
    confidence: str


class BlockedSlot(BaseModel):
    date: str
    time: str
    start: str
    end: str
    label: str
    duration_minutes: int
    reasons: list[str]


class TimeBlockCreate(BaseModel):
    """Staff blocks personal time on an artist's calendar."""

    artist_id: UUID | None = None  # admins may target another artist; defaults to self
    block_date: d
    all_day: bool = False
    start_time: str | None = Field(None, max_length=5)
    end_time: str | None = Field(None, max_length=5)
    reason: str | None = Field(None, max_length=255)
    # NOTE: Google Calendar mirroring for time blocks is not implemented yet.
    # The push_to_calendar field was removed to avoid a misleading no-op API;
    # add it back alongside the actual mirror logic when that lands.


class TimeBlockResponse(BaseModel):
    id: UUID
    artist_id: UUID
    block_date: str
    all_day: bool
    start_time: str | None
    end_time: str | None
    reason: str | None
    google_calendar_event_id: str | None


class SlotListResponse(BaseModel):
    artist_slug: str
    timezone: str
    calendar_connected: bool
    duration_minutes: int
    slot_interval_minutes: int
    buffer_minutes: int
    minimum_notice_hours: int
    max_advance_days: int
    slots: list[AvailableSlot]
    blocked_slots: list[BlockedSlot] | None = None


class SlotHoldRequest(BaseModel):
    artist_slug: str
    date: str
    time: str
    session_type_id: UUID | None = None
    size: str | None = None
    placement: str | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=720)
    buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    minimum_notice_hours: int | None = Field(default=None, ge=0, le=720)
    max_advance_days: int | None = Field(default=None, ge=1, le=365)
    allow_same_day: bool | None = None
    inquiry_id: str | None = None
    guest_email: str | None = None
    booking_kind: Literal["tattoo_session", "flash", "consultation"] = "tattoo_session"
    consultation: bool = False


class SlotHoldResponse(BaseModel):
    hold_token: str
    artist_slug: str
    date: str
    time: str
    duration_minutes: int
    expires_at: str


class SlotHoldReleaseResponse(BaseModel):
    hold_token: str
    status: str
    released: bool


class SchedulerContractAction(BaseModel):
    name: str
    method: str
    path: str
    auth: str
    purpose: str
    schema_ref: str


class SchedulerContractAppointmentType(BaseModel):
    id: UUID
    label: str
    size: str
    placements: list[str] | None
    duration_minutes: int
    buffer_minutes: int | None
    deposit_amount: float
    hourly_rate: float | None
    deposit_required: bool
    sms_reminder_required: bool
    minimum_notice_hours: int
    max_advance_days: int
    allow_same_day: bool
    price_min: float | None
    price_max: float | None


class SchedulerContractBooky(BaseModel):
    consultation_automation_available: bool
    authenticated_collector_canary_required: bool = True
    consent_required: bool = True
    consent_policy_version: str
    supported_booking_kind: str = "consultation"
    outcome_schema_ref: str = "BookingOutcomeEnvelope"


class SchedulerContractResponse(BaseModel):
    version: str
    artist_slug: str
    timezone: str
    rules: dict[str, int | bool]
    blocked_reason_labels: dict[str, str]
    booky: SchedulerContractBooky
    appointment_types: list[SchedulerContractAppointmentType]
    actions: list[SchedulerContractAction]


logger = logging.getLogger("ryozan.calendar")

router = APIRouter(prefix="/calendar", tags=["calendar"])

_WORK_SCHEDULE: dict[int, tuple[int, int] | None] = {
    0: None,
    1: None,
    2: (11, 18),
    3: (11, 18),
    4: (11, 20),
    5: (11, 20),
    6: None,
}
_CONSULTATION_SCHEDULE: dict[int, tuple[int, int] | None] = {
    0: None,
    1: (11, 17),
    2: (11, 17),
    3: (11, 17),
    4: (11, 17),
    5: (11, 17),
    6: None,
}


def _resolve_artist_work_schedule(
    artist: User | None,
    is_consultation: bool = False,
) -> dict[int, tuple[int, int] | None]:
    if is_consultation:
        return _CONSULTATION_SCHEDULE
    if not artist:
        return _WORK_SCHEDULE
    nexus = getattr(artist, "nexus_profile", None)
    if isinstance(nexus, dict) and "working_hours" in nexus and isinstance(nexus["working_hours"], dict):
        day_map = {
            "mon": 0,
            "monday": 0,
            "0": 0,
            0: 0,
            "tue": 1,
            "tuesday": 1,
            "1": 1,
            1: 1,
            "wed": 2,
            "wednesday": 2,
            "2": 2,
            2: 2,
            "thu": 3,
            "thursday": 3,
            "3": 3,
            3: 3,
            "fri": 4,
            "friday": 4,
            "4": 4,
            4: 4,
            "sat": 5,
            "saturday": 5,
            "5": 5,
            5: 5,
            "sun": 6,
            "sunday": 6,
            "6": 6,
            6: 6,
        }
        custom: dict[int, tuple[int, int] | None] = {}
        for k, v in nexus["working_hours"].items():
            day_idx = day_map.get(str(k).lower().strip())
            if day_idx is None:
                continue
            if v is None:
                custom[day_idx] = None
            elif isinstance(v, (list, tuple)) and len(v) == 2:
                with contextlib.suppress(ValueError, TypeError):
                    custom[day_idx] = (int(v[0]), int(v[1]))
        if custom:
            return {day: custom.get(day) for day in range(7)}
    return _WORK_SCHEDULE


_HOLD_TTL_MINUTES = 15
_DEFAULT_SLOT_INTERVAL_MINUTES = 30
_DEFAULT_BUFFER_MINUTES = 30
_DEFAULT_MINIMUM_NOTICE_HOURS = 24
_DEFAULT_MAX_ADVANCE_DAYS = 180
_SLOT_BLOCKING_BOOKING_STATUSES = [
    BookingStatus.PENDING,
    BookingStatus.QUOTED,
    BookingStatus.CONFIRMED,
    BookingStatus.COMPLETED,
]


async def _resolve_calendar_access(
    user: User,
    db: AsyncSession,
) -> tuple[str | None, str]:
    """Resolve the legacy token and concrete calendar target for staff actions."""
    user_owns_calendar_target = (
        not workspace_delegation_enabled() or user.role in {"artist", "founder"} or bool(user.google_calendar_id)
    )
    if user_owns_calendar_target and artist_calendar_connected(user):
        return user.google_refresh_token, artist_calendar_id(user)

    if ROLE_RANK.get(user.role, 0) >= ROLE_RANK.get("admin", 3):
        result = await db.execute(
            select(User).where(
                User.role == "founder",
            )
        )
        founder = result.scalar_one_or_none()
        if founder and artist_calendar_connected(founder):
            return founder.google_refresh_token, artist_calendar_id(founder)

    return None, artist_calendar_id(user)


def _normalize_guest_name(value: str | None) -> str:
    """Return a stable comparison key for calendar-import client names."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _normalize_time(value: str | None) -> str | None:
    if not value:
        return None
    return value[:5]


def _parse_local_time(value: str, label: str = "time") -> str:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value.strip())
    if not match:
        raise HTTPException(status_code=422, detail=f"{label} must be HH:MM")
    return f"{match.group(1)}:{match.group(2)}"


async def _find_existing_calendar_booking(
    db: AsyncSession,
    *,
    artist_id: UUID,
    guest_name: str | None,
    scheduled_date: d | None,
    scheduled_time: str | None = None,
) -> Booking | None:
    if not guest_name or not scheduled_date:
        return None

    normalized_guest_name = _normalize_guest_name(guest_name)
    if not normalized_guest_name:
        return None

    result = await db.execute(
        select(Booking).where(
            Booking.artist_id == artist_id,
            Booking.scheduled_date == scheduled_date,
        )
    )
    candidates = [
        row for row in result.scalars().all() if _normalize_guest_name(row.guest_name) == normalized_guest_name
    ]
    if not candidates:
        return None

    normalized_time = _normalize_time(scheduled_time)
    if normalized_time:
        exact_time_candidates = [row for row in candidates if _normalize_time(row.scheduled_time) == normalized_time]
        unscheduled_candidates = [row for row in candidates if not row.scheduled_time]
        candidates = exact_time_candidates or unscheduled_candidates
        if not candidates:
            return None

    candidates.sort(
        key=lambda booking: (
            0 if booking.reference_code else 1,
            0 if booking.source != "google_calendar" else 1,
            booking.created_at,
        )
    )
    return candidates[0]


def _calendar_import_note(location: str) -> str:
    if location:
        return f"Imported from Google Calendar. Location: {location}"
    return "Imported from Google Calendar"


def _append_calendar_import_note(existing: str | None, note: str) -> str:
    clean_existing = (existing or "").strip()
    clean_note = note.strip()
    if not clean_existing:
        return clean_note
    if clean_note in clean_existing:
        return clean_existing
    return f"{clean_existing}\n\n{clean_note}"


def _is_sparse_calendar_import(booking: Booking) -> bool:
    notes = booking.artist_notes or ""
    return booking.source == "google_calendar" or (
        booking.reference_code is None and "Imported from Google Calendar" in notes
    )


def _link_calendar_event_to_booking(
    booking: Booking,
    *,
    event_id: str,
    scheduled_time: str | None,
    import_note: str,
) -> None:
    booking.google_calendar_event_id = event_id
    if scheduled_time and not booking.scheduled_time:
        booking.scheduled_time = scheduled_time
    if not booking.artist_notes:
        booking.artist_notes = import_note
    elif "Imported from Google Calendar" not in booking.artist_notes:
        booking.artist_notes = f"{booking.artist_notes}\n\n{import_note}"
    booking.updated_at = dt.now(UTC)


def _parse_local_date(value: str, label: str) -> d:
    try:
        return d.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} must be YYYY-MM-DD") from exc


def _parse_busy_datetime(value: str) -> dt | None:
    if not value:
        return None
    try:
        parsed = dt.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHOP_TIMEZONE)
    return parsed.astimezone(SHOP_TIMEZONE)


def _slot_conflicts(slot_start: dt, slot_end: dt, intervals: list[tuple[dt, dt]], buffer_minutes: int = 0) -> bool:
    buffer = timedelta(minutes=max(buffer_minutes, 0))
    return any(slot_start < busy_end + buffer and slot_end > busy_start - buffer for busy_start, busy_end in intervals)


def _booking_interval(booking: Booking) -> tuple[dt, dt] | None:
    if not booking.scheduled_date or not booking.scheduled_time:
        return None
    try:
        start = dt.fromisoformat(f"{booking.scheduled_date.isoformat()}T{booking.scheduled_time[:5]}:00").replace(
            tzinfo=SHOP_TIMEZONE
        )
    except ValueError:
        return None
    return (start, start + timedelta(minutes=booking.duration_minutes or 120))


def _hold_interval(hold: BookingSlotHold) -> tuple[dt, dt] | None:
    try:
        start = dt.fromisoformat(f"{hold.scheduled_date.isoformat()}T{hold.scheduled_time[:5]}:00").replace(
            tzinfo=SHOP_TIMEZONE
        )
    except ValueError:
        return None
    return (start, start + timedelta(minutes=hold.duration_minutes or 120))


def _block_interval(block: TimeBlock) -> tuple[dt, dt] | None:
    """Busy interval for a personal time block. All-day blocks cover the full
    work day (falling back to 00:00-23:59 when the shop is closed that day)."""
    day = block.block_date
    if block.all_day or not block.start_time or not block.end_time:
        hours = _WORK_SCHEDULE[dt.combine(day, dt.min.time()).weekday()]
        if hours:
            open_hour, close_hour = hours
        else:
            open_hour, close_hour = 0, 23
        start = dt.combine(day, dt.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=open_hour)
        end = dt.combine(day, dt.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=close_hour, minute=59)
        return (start, end)
    try:
        start = dt.fromisoformat(f"{day.isoformat()}T{block.start_time[:5]}:00").replace(tzinfo=SHOP_TIMEZONE)
        end = dt.fromisoformat(f"{day.isoformat()}T{block.end_time[:5]}:00").replace(tzinfo=SHOP_TIMEZONE)
    except ValueError:
        return None
    if end <= start:
        return None
    return (start, end)


def _slot_label(slot_start: dt) -> str:
    date_label = slot_start.strftime("%a %b %d").replace(" 0", " ")
    time_label = slot_start.strftime("%I:%M %p").lstrip("0")
    return f"{date_label} - {time_label}"


def _rule_window(
    *,
    minimum_notice_hours: int,
    max_advance_days: int,
    allow_same_day: bool,
) -> tuple[dt, dt]:
    now = dt.now(SHOP_TIMEZONE)
    earliest = now + timedelta(hours=minimum_notice_hours)
    if not allow_same_day:
        tomorrow_start = dt.combine(now.date() + timedelta(days=1), dt.min.time(), tzinfo=SHOP_TIMEZONE)
        earliest = max(earliest, tomorrow_start)
    latest = now + timedelta(days=max_advance_days)
    return earliest, latest


def _artist_rule_value(value: int | None, fallback: int) -> int:
    return fallback if value is None else value


def _artist_booking_rules(
    artist: User,
    *,
    slot_interval_minutes: int | None = None,
    buffer_minutes: int | None = None,
    minimum_notice_hours: int | None = None,
    max_advance_days: int | None = None,
    allow_same_day: bool | None = None,
) -> tuple[int, int, int, int, bool]:
    resolved_interval = slot_interval_minutes or _artist_rule_value(
        artist.booking_slot_interval_minutes, _DEFAULT_SLOT_INTERVAL_MINUTES
    )
    resolved_buffer = (
        buffer_minutes
        if buffer_minutes is not None
        else _artist_rule_value(artist.booking_buffer_minutes, _DEFAULT_BUFFER_MINUTES)
    )
    resolved_notice = (
        minimum_notice_hours
        if minimum_notice_hours is not None
        else _artist_rule_value(artist.booking_min_notice_hours, _DEFAULT_MINIMUM_NOTICE_HOURS)
    )
    resolved_advance = max_advance_days or _artist_rule_value(
        artist.booking_max_advance_days, _DEFAULT_MAX_ADVANCE_DAYS
    )
    resolved_same_day = artist.booking_allow_same_day if allow_same_day is None else allow_same_day
    return resolved_interval, resolved_buffer, resolved_notice, resolved_advance, bool(resolved_same_day)


def _generate_available_slots(
    *,
    start_date: d,
    end_date: d,
    duration_minutes: int,
    slot_interval_minutes: int,
    buffer_minutes: int,
    minimum_notice_hours: int,
    max_advance_days: int,
    allow_same_day: bool,
    busy_blocks: list[dict],
    confirmed_bookings: list[Booking],
    calendar_connected: bool,
    limit: int,
    active_holds: list[BookingSlotHold] | None = None,
    personal_blocks: list[TimeBlock] | None = None,
    work_schedule: dict[int, tuple[int, int] | None] | None = None,
) -> list[AvailableSlot]:
    earliest, latest = _rule_window(
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
    )
    intervals: list[tuple[dt, dt]] = []

    for block in busy_blocks:
        busy_start = _parse_busy_datetime(str(block.get("start") or ""))
        busy_end = _parse_busy_datetime(str(block.get("end") or ""))
        if busy_start and busy_end:
            intervals.append((busy_start, busy_end))

    for booking in confirmed_bookings:
        interval = _booking_interval(booking)
        if interval:
            intervals.append(interval)

    for hold in active_holds or []:
        interval = _hold_interval(hold)
        if interval:
            intervals.append(interval)

    for block in personal_blocks or []:
        interval = _block_interval(block)
        if interval:
            intervals.append(interval)

    slots: list[AvailableSlot] = []
    schedule = work_schedule or _WORK_SCHEDULE
    day = start_date
    while day <= end_date and len(slots) < limit:
        hours = schedule[dt.combine(day, dt.min.time()).weekday()]
        if not hours:
            day += timedelta(days=1)
            continue

        open_hour, close_hour = hours
        close_at = dt.combine(day, dt.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=close_hour)
        cursor = dt.combine(day, dt.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=open_hour)
        while cursor + timedelta(minutes=duration_minutes) <= close_at and len(slots) < limit:
            slot_end = cursor + timedelta(minutes=duration_minutes)
            if earliest <= cursor <= latest and not _slot_conflicts(cursor, slot_end, intervals, buffer_minutes):
                slots.append(
                    AvailableSlot(
                        date=cursor.date().isoformat(),
                        time=cursor.strftime("%H:%M"),
                        start=cursor.isoformat(),
                        end=slot_end.isoformat(),
                        label=_slot_label(cursor),
                        duration_minutes=duration_minutes,
                        source="google_calendar" if calendar_connected else "shop_hours",
                        confidence="high" if calendar_connected else "fallback",
                    )
                )
            cursor += timedelta(minutes=slot_interval_minutes)
        day += timedelta(days=1)

    return slots


def _generate_blocked_slots(
    *,
    start_date: d,
    end_date: d,
    duration_minutes: int,
    slot_interval_minutes: int,
    buffer_minutes: int,
    minimum_notice_hours: int,
    max_advance_days: int,
    allow_same_day: bool,
    busy_blocks: list[dict],
    confirmed_bookings: list[Booking],
    active_holds: list[BookingSlotHold] | None = None,
    personal_blocks: list[TimeBlock] | None = None,
    limit: int = 120,
    work_schedule: dict[int, tuple[int, int] | None] | None = None,
) -> list[BlockedSlot]:
    earliest, latest = _rule_window(
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
    )
    google_intervals: list[tuple[dt, dt]] = []
    booking_intervals: list[tuple[dt, dt]] = []
    hold_intervals: list[tuple[dt, dt]] = []
    personal_intervals: list[tuple[dt, dt]] = []

    for block in busy_blocks:
        busy_start = _parse_busy_datetime(str(block.get("start") or ""))
        busy_end = _parse_busy_datetime(str(block.get("end") or ""))
        if busy_start and busy_end:
            google_intervals.append((busy_start, busy_end))

    for booking in confirmed_bookings:
        interval = _booking_interval(booking)
        if interval:
            booking_intervals.append(interval)

    for hold in active_holds or []:
        interval = _hold_interval(hold)
        if interval:
            hold_intervals.append(interval)

    for block in personal_blocks or []:
        interval = _block_interval(block)
        if interval:
            personal_intervals.append(interval)

    blocked: list[BlockedSlot] = []
    schedule = work_schedule or _WORK_SCHEDULE
    day = start_date
    while day <= end_date and len(blocked) < limit:
        hours = schedule[dt.combine(day, dt.min.time()).weekday()]
        if not hours:
            closed_start = dt.combine(day, dt.min.time(), tzinfo=SHOP_TIMEZONE)
            blocked.append(
                BlockedSlot(
                    date=day.isoformat(),
                    time="00:00",
                    start=closed_start.isoformat(),
                    end=(closed_start + timedelta(days=1)).isoformat(),
                    label=f"{day.strftime('%a %b %d').replace(' 0', ' ')} - closed",
                    duration_minutes=0,
                    reasons=["closed_day"],
                )
            )
            day += timedelta(days=1)
            continue

        open_hour, close_hour = hours
        close_at = dt.combine(day, dt.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=close_hour)
        cursor = dt.combine(day, dt.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=open_hour)
        while cursor + timedelta(minutes=duration_minutes) <= close_at and len(blocked) < limit:
            slot_end = cursor + timedelta(minutes=duration_minutes)
            reasons: list[str] = []
            if cursor < earliest:
                reasons.append("minimum_notice")
            if cursor > latest:
                reasons.append("max_advance")
            if _slot_conflicts(cursor, slot_end, google_intervals, buffer_minutes):
                reasons.append("google_busy")
            if _slot_conflicts(cursor, slot_end, booking_intervals, buffer_minutes):
                reasons.append("booking_conflict")
            if _slot_conflicts(cursor, slot_end, hold_intervals, buffer_minutes):
                reasons.append("active_hold")
            if _slot_conflicts(cursor, slot_end, personal_intervals, buffer_minutes):
                reasons.append("personal_block")

            if reasons:
                blocked.append(
                    BlockedSlot(
                        date=cursor.date().isoformat(),
                        time=cursor.strftime("%H:%M"),
                        start=cursor.isoformat(),
                        end=slot_end.isoformat(),
                        label=_slot_label(cursor),
                        duration_minutes=duration_minutes,
                        reasons=reasons,
                    )
                )
            cursor += timedelta(minutes=slot_interval_minutes)
        day += timedelta(days=1)

    return blocked


async def _resolve_artist_for_slots(db: AsyncSession, artist_slug: str) -> User:
    artist_result = await db.execute(
        select(User).where(
            User.slug == artist_slug,
            User.role.in_(["artist", "founder"]),
        )
    )
    artist = artist_result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    return artist


async def _resolve_slot_session_rules(
    db: AsyncSession,
    *,
    artist_id: UUID,
    session_type_id: UUID | None,
    size: str | None,
    placement: str | None,
    duration_minutes: int | None,
) -> tuple[int, int | None]:
    resolved_duration = duration_minutes or 120
    session_query = select(ArtistSessionType).where(
        ArtistSessionType.artist_id == artist_id,
        ArtistSessionType.is_active.is_(True),
    )
    if session_type_id:
        session_query = session_query.where(ArtistSessionType.id == session_type_id)
    elif size:
        session_query = session_query.where(ArtistSessionType.size == size)
        if placement:
            placement_result = await db.execute(
                session_query.where(ArtistSessionType.placements.contains([placement])).limit(1)
            )
            placement_match = placement_result.scalar_one_or_none()
            if placement_match:
                return resolved_duration if duration_minutes is not None else placement_match.duration_minutes, (
                    placement_match.buffer_minutes
                )
        session_query = session_query.order_by(ArtistSessionType.sort_order).limit(1)
    else:
        return resolved_duration, None

    session_result = await db.execute(session_query)
    session_type = session_result.scalar_one_or_none()
    if not session_type:
        return resolved_duration, None

    if session_type_id and placement and session_type.placements:
        normalized_placement = placement.strip().lower()
        allowed_placements = {item.strip().lower() for item in session_type.placements if item}
        if allowed_placements and normalized_placement not in allowed_placements:
            raise HTTPException(status_code=422, detail="Appointment type is not available for this placement")

    return resolved_duration if duration_minutes is not None else session_type.duration_minutes, (
        session_type.buffer_minutes
    )


async def _resolve_slot_duration(
    db: AsyncSession,
    *,
    artist_id: UUID,
    session_type_id: UUID | None,
    size: str | None,
    placement: str | None,
    duration_minutes: int | None,
) -> int:
    resolved_duration, _ = await _resolve_slot_session_rules(
        db,
        artist_id=artist_id,
        session_type_id=session_type_id,
        size=size,
        placement=placement,
        duration_minutes=duration_minutes,
    )
    return resolved_duration


async def _active_holds_for_range(
    db: AsyncSession,
    *,
    artist_id: UUID,
    start_date: d,
    end_date: d,
    exclude_token: str | None = None,
) -> list[BookingSlotHold]:
    conditions = [
        BookingSlotHold.artist_id == artist_id,
        BookingSlotHold.scheduled_date >= start_date,
        BookingSlotHold.scheduled_date <= end_date,
        BookingSlotHold.status == "active",
        BookingSlotHold.expires_at > dt.now(UTC),
    ]
    if exclude_token:
        conditions.append(BookingSlotHold.hold_token != exclude_token)
    result = await db.execute(select(BookingSlotHold).where(and_(*conditions)))
    return list(result.scalars().all())


async def _confirmed_bookings_for_range(
    db: AsyncSession,
    *,
    artist_id: UUID,
    start_date: d,
    end_date: d,
    exclude_booking_id: UUID | None = None,
) -> list[Booking]:
    filters = [
        Booking.artist_id == artist_id,
        Booking.scheduled_date >= start_date,
        Booking.scheduled_date <= end_date,
        Booking.status.in_(_SLOT_BLOCKING_BOOKING_STATUSES),
    ]
    if exclude_booking_id:
        filters.append(Booking.id != exclude_booking_id)
    booking_result = await db.execute(select(Booking).where(and_(*filters)))
    return list(booking_result.scalars().all())


async def _personal_blocks_for_range(
    db: AsyncSession,
    *,
    artist_id: UUID,
    start_date: d,
    end_date: d,
) -> list[TimeBlock]:
    result = await db.execute(
        select(TimeBlock).where(
            and_(
                TimeBlock.artist_id == artist_id,
                TimeBlock.block_date >= start_date,
                TimeBlock.block_date <= end_date,
            )
        )
    )
    return list(result.scalars().all())


async def _expire_replaced_public_holds(
    db: AsyncSession,
    *,
    artist_id: UUID,
    inquiry_id: str | None,
    guest_email: str | None,
    now: dt,
) -> int:
    identity_filters = []
    clean_inquiry_id = inquiry_id.strip() if inquiry_id else None
    clean_guest_email = guest_email.lower().strip() if guest_email else None
    if clean_inquiry_id:
        identity_filters.append(BookingSlotHold.inquiry_id == clean_inquiry_id)
    if clean_guest_email:
        identity_filters.append(BookingSlotHold.guest_email == clean_guest_email)
    if not identity_filters:
        return 0

    result = await db.execute(
        select(BookingSlotHold).where(
            BookingSlotHold.artist_id == artist_id,
            BookingSlotHold.status == "active",
            BookingSlotHold.expires_at > now,
            or_(*identity_filters),
        )
    )
    replaced = list(result.scalars().all())
    for hold in replaced:
        hold.status = "expired"
        hold.updated_at = now
    return len(replaced)


async def _slot_is_available(
    db: AsyncSession,
    *,
    artist_id: UUID,
    scheduled_date: d,
    scheduled_time: str,
    duration_minutes: int,
    buffer_minutes: int,
    minimum_notice_hours: int,
    max_advance_days: int,
    allow_same_day: bool,
    busy_blocks: list[dict] | None = None,
    exclude_booking_id: UUID | None = None,
    exclude_hold_token: str | None = None,
    work_schedule: dict[int, tuple[int, int] | None] | None = None,
) -> bool:
    slot_start = dt.fromisoformat(f"{scheduled_date.isoformat()}T{scheduled_time}:00").replace(tzinfo=SHOP_TIMEZONE)
    slot_end = slot_start + timedelta(minutes=duration_minutes)
    earliest, latest = _rule_window(
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
    )
    hours = (work_schedule or _WORK_SCHEDULE)[slot_start.weekday()]
    if not hours:
        return False
    open_hour, close_hour = hours
    open_at = dt.combine(scheduled_date, dt.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=open_hour)
    close_at = dt.combine(scheduled_date, dt.min.time(), tzinfo=SHOP_TIMEZONE).replace(hour=close_hour)
    if slot_start < earliest or slot_start > latest or slot_start < open_at or slot_end > close_at:
        return False

    intervals: list[tuple[dt, dt]] = []
    for block in busy_blocks or []:
        busy_start = _parse_busy_datetime(str(block.get("start") or ""))
        busy_end = _parse_busy_datetime(str(block.get("end") or ""))
        if busy_start and busy_end:
            intervals.append((busy_start, busy_end))
    for booking in await _confirmed_bookings_for_range(
        db,
        artist_id=artist_id,
        start_date=scheduled_date,
        end_date=scheduled_date,
        exclude_booking_id=exclude_booking_id,
    ):
        interval = _booking_interval(booking)
        if interval:
            intervals.append(interval)
    for hold in await _active_holds_for_range(
        db,
        artist_id=artist_id,
        start_date=scheduled_date,
        end_date=scheduled_date,
        exclude_token=exclude_hold_token,
    ):
        interval = _hold_interval(hold)
        if interval:
            intervals.append(interval)
    for block in await _personal_blocks_for_range(
        db,
        artist_id=artist_id,
        start_date=scheduled_date,
        end_date=scheduled_date,
    ):
        interval = _block_interval(block)
        if interval:
            intervals.append(interval)
    return not _slot_conflicts(slot_start, slot_end, intervals, buffer_minutes)


@router.get("/availability")
async def calendar_availability(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    current_user: User = Depends(get_current_user),
):
    """
    Return free/busy blocks for a date range.
    Used by the booking form to show available appointment slots.
    """
    try:
        busy_blocks = await get_availability(
            start_date=start,
            end_date=end,
            calendar_id=artist_calendar_id(current_user),
            refresh_token=current_user.google_refresh_token,
        )
        return {"start": start, "end": end, "busy": busy_blocks}
    except Exception as exc:
        logger.error("Availability endpoint failed: %s", exc)
        raise HTTPException(status_code=502, detail="Calendar service unavailable") from exc


@router.get("/upcoming")
async def calendar_upcoming(
    days: int = Query(default=7, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return upcoming calendar events for the dashboard.
    Artist+ only. Admins without a connected calendar fall back to the founder's calendar.
    """
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=403, detail="Artist role or higher required")

    refresh_token, calendar_id = await _resolve_calendar_access(current_user, db)
    if not calendar_connection_configured(refresh_token):
        raise HTTPException(status_code=400, detail="No Google Calendar connected")

    try:
        events = await get_upcoming_events(
            days=days,
            calendar_id=calendar_id,
            refresh_token=refresh_token,
        )
        return {"days": days, "count": len(events), "events": events}
    except Exception as exc:
        logger.error("Upcoming events endpoint failed: %s", exc)
        raise HTTPException(status_code=502, detail="Calendar service unavailable") from exc


@router.get("/availability/{artist_slug}")
async def public_artist_availability(
    artist_slug: str,
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
):
    """
    Public: get busy blocks for an artist by slug.
    Used by the public booking form to show available appointment slots.
    Returns empty busy list if the artist hasn't connected Google Calendar.
    """
    result = await db.execute(
        select(User).where(
            User.slug == artist_slug,
            User.role.in_(["artist", "founder"]),
        )
    )
    artist = result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    if not artist_calendar_connected(artist):
        return {"start": start, "end": end, "busy": [], "calendar_connected": False}

    try:
        busy_blocks = await get_availability(
            start_date=start,
            end_date=end,
            calendar_id=artist_calendar_id(artist),
            refresh_token=artist.google_refresh_token,
        )
        return {"start": start, "end": end, "busy": busy_blocks, "calendar_connected": True}
    except Exception as exc:
        logger.error("Public availability failed for %s: %s", artist_slug, exc)
        return {"start": start, "end": end, "busy": [], "calendar_connected": False}


@router.get("/slots/{artist_slug}", response_model=SlotListResponse)
async def public_artist_slots(
    artist_slug: str,
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    session_type_id: UUID | None = Query(default=None),
    size: str | None = Query(default=None),
    placement: str | None = Query(default=None),
    duration_minutes: int | None = Query(default=None, ge=15, le=720),
    slot_interval_minutes: int | None = Query(default=None, ge=15, le=120),
    buffer_minutes: int | None = Query(default=None, ge=0, le=240),
    minimum_notice_hours: int | None = Query(default=None, ge=0, le=720),
    max_advance_days: int | None = Query(default=None, ge=1, le=365),
    allow_same_day: bool | None = Query(default=None),
    limit: int = Query(default=60, ge=1, le=240),
    include_blocked: bool = Query(default=False),
    blocked_limit: int = Query(default=120, ge=1, le=480),
    consultation: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
):
    """
    Public: calculate bookable slots for an artist.

    This is the native scheduler contract for the public booking flow. It
    combines shop hours, session type duration, Google busy blocks when
    connected, and existing confirmed Ryozan bookings.
    """
    start_date = _parse_local_date(start, "start")
    end_date = _parse_local_date(end, "end")
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="end must be on or after start")
    normalized_session_type_id = session_type_id if isinstance(session_type_id, UUID) else None
    normalized_size = size if isinstance(size, str) else None
    normalized_placement = placement if isinstance(placement, str) else None
    normalized_duration = duration_minutes if isinstance(duration_minutes, int) else None

    artist = await _resolve_artist_for_slots(db, artist_slug)
    (
        resolved_slot_interval,
        resolved_buffer,
        resolved_min_notice,
        resolved_max_advance,
        resolved_allow_same_day,
    ) = _artist_booking_rules(
        artist,
        slot_interval_minutes=slot_interval_minutes,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
    )
    normalized_consultation = consultation if isinstance(consultation, bool) else False
    if normalized_consultation:
        normalized_session_type_id = None
        normalized_size = None
        normalized_placement = None
        normalized_duration = 30
        resolved_slot_interval = 30
        resolved_buffer = 0

    resolved_duration, session_buffer_minutes = await _resolve_slot_session_rules(
        db,
        artist_id=artist.id,
        session_type_id=normalized_session_type_id,
        size=normalized_size,
        placement=normalized_placement,
        duration_minutes=normalized_duration,
    )
    if session_buffer_minutes is not None and buffer_minutes is None:
        resolved_buffer = session_buffer_minutes

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
            logger.error("Slot availability calendar read failed for %s: %s", artist_slug, exc)
            busy_blocks = []
            calendar_connected = False

    confirmed_bookings = await _confirmed_bookings_for_range(
        db,
        artist_id=artist.id,
        start_date=start_date,
        end_date=end_date,
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

    available_slots = (
        []
        if normalized_consultation and not calendar_connected
        else _generate_available_slots(
            start_date=start_date,
            end_date=end_date,
            duration_minutes=resolved_duration,
            slot_interval_minutes=resolved_slot_interval,
            buffer_minutes=resolved_buffer,
            minimum_notice_hours=resolved_min_notice,
            max_advance_days=resolved_max_advance,
            allow_same_day=resolved_allow_same_day,
            busy_blocks=busy_blocks,
            confirmed_bookings=confirmed_bookings,
            calendar_connected=calendar_connected,
            limit=limit,
            active_holds=active_holds,
            personal_blocks=personal_blocks,
            work_schedule=_resolve_artist_work_schedule(artist, normalized_consultation),
        )
    )

    blocked_slots = None
    if include_blocked and not (normalized_consultation and not calendar_connected):
        blocked_slots = _generate_blocked_slots(
            start_date=start_date,
            end_date=end_date,
            duration_minutes=resolved_duration,
            slot_interval_minutes=resolved_slot_interval,
            buffer_minutes=resolved_buffer,
            minimum_notice_hours=resolved_min_notice,
            max_advance_days=resolved_max_advance,
            allow_same_day=resolved_allow_same_day,
            busy_blocks=busy_blocks,
            confirmed_bookings=confirmed_bookings,
            active_holds=active_holds,
            personal_blocks=personal_blocks,
            limit=blocked_limit,
            work_schedule=_resolve_artist_work_schedule(artist, normalized_consultation),
        )

    return SlotListResponse(
        artist_slug=artist_slug,
        timezone="America/New_York",
        calendar_connected=calendar_connected,
        duration_minutes=resolved_duration,
        slot_interval_minutes=resolved_slot_interval,
        buffer_minutes=resolved_buffer,
        minimum_notice_hours=resolved_min_notice,
        max_advance_days=resolved_max_advance,
        slots=available_slots,
        blocked_slots=blocked_slots,
    )


@router.get("/scheduler-contract/{artist_slug}", response_model=SchedulerContractResponse)
async def public_scheduler_contract(
    artist_slug: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public: expose the narrow scheduler tool contract for GalaXEv3.

    The contract is discovery only. AI and connector layers must still call
    the server-owned scheduler endpoints for availability, holds, drafts,
    and booking submission so booking rules remain centralized.
    """
    artist = await _resolve_artist_for_slots(db, artist_slug)
    (
        resolved_slot_interval,
        resolved_buffer,
        resolved_min_notice,
        resolved_max_advance,
        resolved_allow_same_day,
    ) = _artist_booking_rules(artist)

    result = await db.execute(
        select(ArtistSessionType)
        .where(
            ArtistSessionType.artist_id == artist.id,
            ArtistSessionType.is_active.is_(True),
        )
        .order_by(ArtistSessionType.sort_order, ArtistSessionType.label)
    )
    session_types = result.scalars().all()

    settings = get_settings()
    return SchedulerContractResponse(
        version="2026-08-20",
        artist_slug=artist_slug,
        timezone="America/New_York",
        rules={
            "slot_interval_minutes": resolved_slot_interval,
            "buffer_minutes": resolved_buffer,
            "minimum_notice_hours": resolved_min_notice,
            "max_advance_days": resolved_max_advance,
            "allow_same_day": resolved_allow_same_day,
        },
        blocked_reason_labels={
            "active_hold": "another client is holding that time",
            "booking_conflict": "existing booking conflict",
            "closed_day": "shop is closed that day",
            "google_busy": "artist calendar is busy",
            "max_advance": "outside the booking window",
            "minimum_notice": "inside the required notice window",
            "past": "time has already passed",
        },
        booky=SchedulerContractBooky(
            # This is an unauthenticated contract, so it cannot truthfully claim
            # availability for the account-bound founder canary.
            consultation_automation_available=False,
            consent_policy_version=settings.BOOKY_POLICY_VERSION,
        ),
        appointment_types=[
            SchedulerContractAppointmentType(
                id=session_type.id,
                label=session_type.label,
                size=session_type.size,
                placements=session_type.placements,
                duration_minutes=session_type.duration_minutes,
                buffer_minutes=session_type.buffer_minutes
                if session_type.buffer_minutes is not None
                else resolved_buffer,
                deposit_amount=float(session_type.deposit_amount or 0),
                hourly_rate=float(session_type.hourly_rate) if session_type.hourly_rate is not None else None,
                deposit_required=bool(session_type.deposit_amount and float(session_type.deposit_amount) > 0),
                sms_reminder_required=True,
                minimum_notice_hours=resolved_min_notice,
                max_advance_days=resolved_max_advance,
                allow_same_day=resolved_allow_same_day,
                price_min=float(session_type.price_min) if session_type.price_min is not None else None,
                price_max=float(session_type.price_max) if session_type.price_max is not None else None,
            )
            for session_type in session_types
        ],
        actions=[
            SchedulerContractAction(
                name="list_appointment_types",
                method="GET",
                path=f"/api/v1/session-types/artist/{artist_slug}",
                auth="public",
                purpose="List active tattoo appointment types for this artist.",
                schema_ref="SessionTypeResponse[]",
            ),
            SchedulerContractAction(
                name="list_slots",
                method="GET",
                path=f"/api/v1/calendar/slots/{artist_slug}",
                auth="public",
                purpose="Calculate bookable slots from artist rules, holds, bookings, and calendar busy blocks.",
                schema_ref="SlotListResponse",
            ),
            SchedulerContractAction(
                name="explain_blocked_slot",
                method="GET",
                path=f"/api/v1/calendar/slots/{artist_slug}?include_blocked=true",
                auth="public",
                purpose="Return blocked slot reasons for client-safe scheduling explanations.",
                schema_ref="SlotListResponse.blocked_slots",
            ),
            SchedulerContractAction(
                name="hold_slot",
                method="POST",
                path="/api/v1/calendar/slot-holds",
                auth="public",
                purpose="Hold a selected slot before final booking submission.",
                schema_ref="SlotHoldRequest",
            ),
            SchedulerContractAction(
                name="release_hold",
                method="DELETE",
                path="/api/v1/calendar/slot-holds/{hold_token}",
                auth="public",
                purpose="Release a held slot when the client changes or clears the selected time.",
                schema_ref="SlotHoldReleaseResponse",
            ),
            SchedulerContractAction(
                name="submit_booking_draft",
                method="POST",
                path="/api/v1/bookings/draft",
                auth="public",
                purpose="Save a recoverable public booking draft before final submit.",
                schema_ref="PublicBookingDraft",
            ),
            SchedulerContractAction(
                name="submit_booking",
                method="POST",
                path="/api/v1/bookings/submit",
                auth="public",
                purpose="Submit the booking request. Existing slot holds are consumed server-side.",
                schema_ref="PublicBookingSubmit",
            ),
        ],
    )


@router.post("/slot-holds", response_model=SlotHoldResponse)
async def hold_public_slot(
    body: SlotHoldRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Public: hold one bookable slot while the client finishes intake.

    The hold is short-lived and must be consumed by final booking submit.
    It prevents the Acuity failure mode where a visible slot can disappear
    or be double-selected while the client is still filling the form.
    """
    scheduled_date = _parse_local_date(body.date, "date")
    scheduled_time = _parse_local_time(body.time)
    if body.consultation and body.booking_kind == "flash":
        raise HTTPException(status_code=422, detail="Flash slot holds cannot use consultation scheduling")
    is_consultation = body.consultation or body.booking_kind == "consultation"
    booking_kind = "consultation" if is_consultation else body.booking_kind

    artist = await _resolve_artist_for_slots(db, body.artist_slug)
    await db.execute(select(User.id).where(User.id == artist.id).with_for_update())
    if is_consultation and not artist_calendar_connected(artist):
        raise HTTPException(
            status_code=503,
            detail="Live consultation availability is temporarily unavailable",
        )
    normalized_session_type_id = None if is_consultation else body.session_type_id
    normalized_size = None if is_consultation else body.size
    normalized_placement = None if is_consultation else body.placement
    normalized_duration = 30 if is_consultation else body.duration_minutes
    resolved_duration, session_buffer_minutes = await _resolve_slot_session_rules(
        db,
        artist_id=artist.id,
        session_type_id=normalized_session_type_id,
        size=normalized_size,
        placement=normalized_placement,
        duration_minutes=normalized_duration,
    )
    _, buffer_minutes, minimum_notice_hours, max_advance_days, allow_same_day = _artist_booking_rules(
        artist,
        buffer_minutes=body.buffer_minutes,
        minimum_notice_hours=body.minimum_notice_hours,
        max_advance_days=body.max_advance_days,
        allow_same_day=body.allow_same_day,
    )
    if is_consultation:
        buffer_minutes = 0
    elif session_buffer_minutes is not None and body.buffer_minutes is None:
        buffer_minutes = session_buffer_minutes
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
            logger.error("Slot hold calendar read failed for %s: %s", body.artist_slug, exc)
            raise HTTPException(status_code=502, detail="Calendar availability check failed") from exc

    now = dt.now(UTC)
    await _expire_replaced_public_holds(
        db,
        artist_id=artist.id,
        inquiry_id=body.inquiry_id,
        guest_email=body.guest_email,
        now=now,
    )
    available = await _slot_is_available(
        db,
        artist_id=artist.id,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        duration_minutes=resolved_duration,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=minimum_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
        busy_blocks=busy_blocks,
        work_schedule=_resolve_artist_work_schedule(artist, is_consultation),
    )
    if not available:
        raise HTTPException(status_code=409, detail="Slot is no longer available")

    hold = BookingSlotHold(
        hold_token=secrets.token_urlsafe(32),
        artist_id=artist.id,
        artist_slug=body.artist_slug,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        duration_minutes=resolved_duration,
        booking_kind=booking_kind,
        status="active",
        inquiry_id=body.inquiry_id,
        guest_email=body.guest_email.lower().strip() if body.guest_email else None,
        expires_at=now + timedelta(minutes=_HOLD_TTL_MINUTES),
        created_at=now,
        updated_at=now,
    )
    db.add(hold)
    db.add(
        BookingLifecycleEvent(
            artist_id=artist.id,
            event_type="hold_created",
            source="public_scheduler",
            hold_token=hold.hold_token,
            guest_email=hold.guest_email,
            summary=f"Public scheduler held {scheduled_date.isoformat()} {scheduled_time}.",
            details={
                "artist_slug": body.artist_slug,
                "scheduled_date": scheduled_date.isoformat(),
                "scheduled_time": scheduled_time,
                "duration_minutes": resolved_duration,
                "expires_at": hold.expires_at.isoformat(),
                "inquiry_id": body.inquiry_id,
                "consultation": is_consultation,
                "booking_kind": booking_kind,
            },
        )
    )
    await db.flush()

    return SlotHoldResponse(
        hold_token=hold.hold_token,
        artist_slug=hold.artist_slug,
        date=hold.scheduled_date.isoformat(),
        time=hold.scheduled_time,
        duration_minutes=hold.duration_minutes,
        expires_at=hold.expires_at.isoformat(),
    )


@router.delete("/slot-holds/{hold_token}", response_model=SlotHoldReleaseResponse)
async def release_public_slot_hold(
    hold_token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public: release a slot hold the client no longer wants.

    Release is intentionally idempotent so booking UI cleanup can fire on
    clear/change events without turning stale client state into user-visible
    errors.
    """
    result = await db.execute(select(BookingSlotHold).where(BookingSlotHold.hold_token == hold_token))
    hold = result.scalar_one_or_none()
    if not hold:
        return SlotHoldReleaseResponse(hold_token=hold_token, status="missing", released=False)

    if hold.status == "active" and hold.booking_id is None:
        now = dt.now(UTC)
        hold.status = "released"
        hold.updated_at = now
        await db.flush()
        return SlotHoldReleaseResponse(hold_token=hold_token, status=hold.status, released=True)

    return SlotHoldReleaseResponse(hold_token=hold_token, status=hold.status, released=False)


# Personal time blocks


def _serialize_time_block(block: TimeBlock) -> TimeBlockResponse:
    return TimeBlockResponse(
        id=block.id,
        artist_id=block.artist_id,
        block_date=block.block_date.isoformat(),
        all_day=block.all_day,
        start_time=block.start_time,
        end_time=block.end_time,
        reason=block.reason,
        google_calendar_event_id=block.google_calendar_event_id,
    )


def _resolve_block_artist_id(current_user: User, requested: UUID | None) -> UUID:
    """Artists manage their own blocks; admins+ may target another artist."""
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=403, detail="Artist role or higher required")
    if requested and requested != current_user.id:
        if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 3):
            raise HTTPException(status_code=403, detail="Admins only may manage another artist's time blocks")
        return requested
    return current_user.id


@router.get("/time-blocks", response_model=list[TimeBlockResponse])
async def list_time_blocks(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    artist_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List personal time blocks for an artist within a date range."""
    target_artist = _resolve_block_artist_id(current_user, artist_id)
    try:
        start_date = d.fromisoformat(start)
        end_date = d.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format") from exc
    blocks = await _personal_blocks_for_range(db, artist_id=target_artist, start_date=start_date, end_date=end_date)
    return [_serialize_time_block(b) for b in blocks]


@router.post("/time-blocks", response_model=TimeBlockResponse, status_code=201)
async def create_time_block(
    body: TimeBlockCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Block personal time on an artist's calendar so it cannot be booked."""
    target_artist = _resolve_block_artist_id(current_user, body.artist_id)
    if not body.all_day:
        if not body.start_time or not body.end_time:
            raise HTTPException(status_code=400, detail="start_time and end_time required unless all_day")
        _parse_local_time(body.start_time)
        _parse_local_time(body.end_time)
    block = TimeBlock(
        artist_id=target_artist,
        block_date=body.block_date,
        all_day=body.all_day,
        start_time=None if body.all_day else body.start_time,
        end_time=None if body.all_day else body.end_time,
        reason=body.reason,
        created_by=current_user.id,
    )
    db.add(block)
    await db.flush()
    await db.commit()
    await db.refresh(block)
    return _serialize_time_block(block)


@router.delete("/time-blocks/{block_id}", status_code=200)
async def delete_time_block(
    block_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a personal time block (owner or admin+)."""
    block = (await db.execute(select(TimeBlock).where(TimeBlock.id == block_id))).scalar_one_or_none()
    if not block:
        raise HTTPException(status_code=404, detail="Time block not found")
    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    if block.artist_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Cannot delete another artist's time block")
    await db.delete(block)
    await db.commit()
    return {"id": str(block_id), "deleted": True}


@router.post("/sync/{booking_id}")
async def sync_booking_to_calendar(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually sync a booking to Google Calendar.
    Creates a calendar event from the booking data.
    Artist+ only.
    """
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=403, detail="Artist role or higher required")

    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Only the assigned artist or admin can sync
    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    if not is_admin and current_user.id != booking.artist_id:
        raise HTTPException(status_code=403, detail="Not authorized to sync this booking")

    # Resolve client name
    if booking.client_id:
        from app.models.user import User

        client_r = await db.execute(select(User.name, User.email).where(User.id == booking.client_id))
        client_row = client_r.first()
        client_name = client_row[0] if client_row else "Client"
        client_email = client_row[1] if client_row else booking.guest_email
    else:
        client_name = booking.guest_name or "Guest"
        client_email = booking.guest_email

    # Idempotency: don't create a duplicate event if this booking is already synced.
    if booking.google_calendar_event_id:
        return {
            "booking_id": str(booking_id),
            "event_id": booking.google_calendar_event_id,
            "html_link": "",
            "already_synced": True,
        }

    booking_data = {
        "client_name": client_name,
        "client_email": client_email,
        "description": booking.description,
        "placement": booking.placement,
        "reference_code": booking.reference_code,
        "scheduled_date": booking.scheduled_date,
        "scheduled_time": booking.scheduled_time or "12:00",
        "duration_minutes": booking.duration_minutes or 120,
    }

    artist_result = await db.execute(select(User).where(User.id == booking.artist_id))
    calendar_artist = artist_result.scalar_one_or_none()
    if not calendar_artist or not artist_calendar_connected(calendar_artist):
        raise HTTPException(status_code=400, detail="No Google Calendar connected")

    try:
        event = await create_booking_event(
            booking_data,
            calendar_id=artist_calendar_id(calendar_artist),
            refresh_token=calendar_artist.google_refresh_token,
        )
        # Persist the Google Calendar event ID so we can update/delete it later
        booking.google_calendar_event_id = event["event_id"]
        await db.flush()
        return {
            "booking_id": str(booking_id),
            "event_id": event["event_id"],
            "html_link": event["html_link"],
            "already_synced": False,
        }
    except Exception as exc:
        logger.error("Calendar sync failed for booking %s: %s", booking_id, exc)
        raise HTTPException(status_code=502, detail="Calendar sync failed") from exc


# ── Direct event CRUD (for Gala AI) ─────────────


@router.post("/events", status_code=201)
async def create_event(
    body: EventCreateBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a standalone Google Calendar event (not tied to a booking).
    Founder/admin only. Used by Gala AI to schedule anything on your behalf.
    """
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Admin role or higher required")

    from app.services.calendar_sync import _get_calendar_service

    refresh_token, resolved_calendar_id = await _resolve_calendar_access(current_user, db)
    if not calendar_connection_configured(refresh_token):
        raise HTTPException(status_code=400, detail="No Google Calendar connected")

    try:
        service = _get_calendar_service(refresh_token)

        if body.all_day:
            start = {"date": body.start[:10]}
            end = {"date": body.end[:10]}
        else:
            start = {"dateTime": body.start, "timeZone": "America/New_York"}
            end = {"dateTime": body.end, "timeZone": "America/New_York"}

        event_body = {
            "summary": body.summary,
            "start": start,
            "end": end,
        }
        if body.description:
            event_body["description"] = body.description
        if body.location:
            event_body["location"] = body.location

        event = (
            service.events()
            .insert(
                calendarId=resolved_calendar_id if body.calendar_id == "primary" else body.calendar_id,
                body=event_body,
            )
            .execute()
        )

        logger.info("Gala created calendar event %s for user %s", event["id"], current_user.id)
        return {"event_id": event["id"], "html_link": event.get("htmlLink", "")}

    except Exception as exc:
        logger.error("Calendar event create failed: %s", exc)
        raise HTTPException(status_code=502, detail="Calendar event create failed") from exc


@router.put("/events/{event_id}")
async def update_event(
    event_id: str,
    body: EventUpdateBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update an existing Google Calendar event by its Google event ID.
    Founder/admin only. Used by Gala AI to reschedule or edit events.
    """
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Admin role or higher required")

    patch = {}
    if body.summary is not None:
        patch["summary"] = body.summary
    if body.description is not None:
        patch["description"] = body.description
    if body.location is not None:
        patch["location"] = body.location
    if body.start is not None:
        patch["start"] = {"dateTime": body.start, "timeZone": "America/New_York"}
    if body.end is not None:
        patch["end"] = {"dateTime": body.end, "timeZone": "America/New_York"}

    refresh_token, resolved_calendar_id = await _resolve_calendar_access(current_user, db)
    if not calendar_connection_configured(refresh_token):
        raise HTTPException(status_code=400, detail="No Google Calendar connected")

    try:
        result = await update_booking_event(
            event_id,
            patch,
            calendar_id=resolved_calendar_id if body.calendar_id == "primary" else body.calendar_id,
            refresh_token=refresh_token,
        )
        logger.info("Gala updated calendar event %s for user %s", event_id, current_user.id)
        return result
    except Exception as exc:
        logger.error("Calendar event update failed for %s: %s", event_id, exc)
        raise HTTPException(status_code=502, detail="Calendar event update failed") from exc


@router.get("/schedule")
async def get_schedule(
    current_user: User = Depends(get_current_user),
):
    """Return the shop working schedule for use by the booking slot picker."""
    return {
        "timezone": "America/New_York",
        "working_days": {
            "wednesday": {"open": "11:00", "close": "18:00"},
            "thursday": {"open": "11:00", "close": "18:00"},
            "friday": {"open": "11:00", "close": "20:00"},
            "saturday": {"open": "11:00", "close": "20:00"},
        },
        "off_days": ["sunday", "monday", "tuesday"],
    }


@router.post("/import")
async def import_calendar_events(
    days: int = Query(default=30, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Import upcoming Google Calendar events as Ryozan bookings.
    Skips events already imported (matched by google_calendar_event_id).
    Parses Acuity-style titles: "Client Name: Service (Artist Name)"
    """
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=403, detail="Artist role or higher required")

    # Resolve the artist calendar (with admin fallback to the founder).
    calendar_artist = current_user
    if not artist_calendar_connected(calendar_artist) and ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get(
        "admin", 3
    ):
        result = await db.execute(
            select(User).where(
                User.role == "founder",
            )
        )
        founder = result.scalar_one_or_none()
        if founder:
            calendar_artist = founder

    if not artist_calendar_connected(calendar_artist):
        raise HTTPException(status_code=400, detail="No Google Calendar connected")
    refresh_token = calendar_artist.google_refresh_token
    artist_id = calendar_artist.id
    calendar_id = artist_calendar_id(calendar_artist)

    # Fetch upcoming events
    try:
        events = await get_upcoming_events(
            days=days,
            calendar_id=calendar_id,
            refresh_token=refresh_token,
        )
    except Exception as exc:
        logger.error("Calendar import fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Calendar service unavailable") from exc

    if not events:
        return {"imported": 0, "skipped": 0, "total": 0}

    # Get existing google_calendar_event_ids to avoid duplicates and reconcile
    # sparse import rows back onto native Ryozan bookings when possible.
    event_ids = [e["id"] for e in events]
    existing_result = await db.execute(select(Booking).where(Booking.google_calendar_event_id.in_(event_ids)))
    existing_by_event_id = {
        booking.google_calendar_event_id: booking
        for booking in existing_result.scalars().all()
        if booking.google_calendar_event_id
    }

    imported = 0
    updated = 0
    reconciled = 0
    skipped = 0

    for event in events:
        event_id = event["id"]

        summary = event.get("summary", "").strip()
        location = event.get("location", "") or ""

        # Parse "Client Name: Service (Artist Name)" format (Acuity style)
        guest_name = None
        description = summary
        if ": " in summary:
            parts = summary.split(": ", 1)
            guest_name = parts[0].strip()
            service = parts[1].strip()
            # Strip trailing "(Artist Name)" suffix
            if service.endswith(")") and "(" in service:
                service = service[: service.rfind("(")].strip()
            description = service

        # Parse start datetime
        start_str = event.get("start", "")
        scheduled_date = None
        scheduled_time = None
        if start_str:
            try:
                if "T" in start_str:
                    dt_obj = dt.fromisoformat(start_str.replace("Z", "+00:00"))
                    scheduled_date = dt_obj.date()
                    scheduled_time = dt_obj.strftime("%H:%M")
                else:
                    scheduled_date = d.fromisoformat(start_str)
            except ValueError:
                pass

        import_note = _calendar_import_note(location)
        existing_booking = await _find_existing_calendar_booking(
            db,
            artist_id=artist_id,
            guest_name=guest_name,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
        )
        event_booking = existing_by_event_id.get(event_id)
        if event_booking:
            if (
                existing_booking
                and existing_booking.id != event_booking.id
                and existing_booking.reference_code
                and _is_sparse_calendar_import(event_booking)
            ):
                event_booking.google_calendar_event_id = None
                event_booking.status = BookingStatus.EXPIRED
                event_booking.artist_notes = _append_calendar_import_note(
                    event_booking.artist_notes,
                    f"Superseded by native booking {existing_booking.reference_code}.",
                )
                event_booking.updated_at = dt.now(UTC)
                _link_calendar_event_to_booking(
                    existing_booking,
                    event_id=event_id,
                    scheduled_time=scheduled_time,
                    import_note=import_note,
                )
                reconciled += 1
            else:
                skipped += 1
            continue

        if existing_booking:
            _link_calendar_event_to_booking(
                existing_booking,
                event_id=event_id,
                scheduled_time=scheduled_time,
                import_note=import_note,
            )
            updated += 1
            continue

        booking = Booking(
            artist_id=artist_id,
            guest_name=guest_name,
            status="confirmed",
            description=description or summary or "Imported from Google Calendar",
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            google_calendar_event_id=event_id,
            artist_notes=import_note,
            source="google_calendar",
        )
        db.add(booking)
        imported += 1

    await db.commit()

    return {
        "imported": imported,
        "updated": updated,
        "reconciled": reconciled,
        "skipped": skipped,
        "total": len(events),
    }


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(
    event_id: str,
    calendar_id: str = Query(default="primary"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a Google Calendar event by its Google event ID.
    Founder/admin only. Used by Gala AI to remove cancelled events.
    """
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Admin role or higher required")

    refresh_token, resolved_calendar_id = await _resolve_calendar_access(current_user, db)
    if not calendar_connection_configured(refresh_token):
        raise HTTPException(status_code=400, detail="No Google Calendar connected")

    success = await delete_booking_event(
        event_id,
        calendar_id=resolved_calendar_id if calendar_id == "primary" else calendar_id,
        refresh_token=refresh_token,
    )
    if not success:
        raise HTTPException(status_code=502, detail="Calendar event delete failed")
    logger.info("Gala deleted calendar event %s for user %s", event_id, current_user.id)
