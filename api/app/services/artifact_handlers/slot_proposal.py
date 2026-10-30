"""
Slot Proposal — interactive message artifact.

The artist whitelists specific time slots for the client to pick from.
The client sees a mini-calendar showing ONLY those slots and can
confirm one. On confirm, the booking's scheduled_date/time lock in
and (if the artist has Google Calendar connected) a calendar event
is created automatically.

State machine:

    pending ──select_slot(index)──> confirmed
            ──cancel──────────────> cancelled

The artist can also cancel or supersede with a new slot_proposal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.api.v1.endpoints.calendar import (
    _artist_booking_rules,
    _parse_local_date,
    _parse_local_time,
    _slot_is_available,
)
from app.constants.status import BookingStatus
from app.core.security import ROLE_RANK
from app.models.booking import Booking
from app.models.user import BTTCOUser
from app.services.artifacts import (
    ArtifactActionError,
    ArtifactError,
    ArtifactHandler,
    register,
)
from app.services.calendar_sync import (
    artist_calendar_connected,
    artist_calendar_id,
    create_booking_event,
    get_availability,
)

logger = logging.getLogger("bttco.artifacts.slot_proposal")


# ── Payload schema ───────────────────────────────────────────────


class ProposedSlot(BaseModel):
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    duration_minutes: int = Field(ge=15, le=720)
    label: str | None = None  # human-friendly e.g. "Thu Apr 10 · 1:00 PM"


class SlotProposalPayload(BaseModel):
    """
    Immutable payload for a slot_proposal artifact.

    The artist picks 1-N slots from their calendar and attaches them
    to a message. The client can select exactly one.
    """

    booking_id: UUID
    slots: list[ProposedSlot] = Field(min_length=1, max_length=10)


# ── Initial state ────────────────────────────────────────────────


INITIAL_STATE: dict[str, Any] = {
    "status": "pending",
    "selected_index": None,
    "confirmed_at": None,
    "cancelled_at": None,
}


# ── Action handler ───────────────────────────────────────────────


async def _load_booking(db: AsyncSession, booking_id: UUID) -> Booking:
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if booking is None:
        raise ArtifactError(f"booking {booking_id} not found")
    return booking


async def _apply_select_slot(
    db: AsyncSession,
    message: Any,
    actor: BTTCOUser | None,
    payload: dict[str, Any],
    state: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    """Client selects one of the proposed slots."""
    if state.get("status") != "pending":
        raise ArtifactActionError(f"cannot select a slot on a {state.get('status')} slot_proposal")

    index = data.get("slot_index")
    if index is None:
        raise ArtifactActionError("slot_index is required in data")

    slots = payload.get("slots", [])
    if not isinstance(index, int) or index < 0 or index >= len(slots):
        raise ArtifactActionError(f"slot_index {index} out of range (0..{len(slots) - 1})")

    selected = slots[index]
    booking = await _load_booking(db, UUID(payload["booking_id"]))
    artist_r = await db.execute(select(BTTCOUser).where(BTTCOUser.id == booking.artist_id))
    artist_user = artist_r.scalar_one_or_none()
    if artist_user is None:
        raise ArtifactActionError("booking has no assigned artist")

    # Authorization: only the booking client or staff can select
    if actor is not None:
        is_staff = ROLE_RANK.get(actor.role, 0) >= ROLE_RANK.get("assistant", 1)
        is_client = booking.client_id == actor.id
        if not (is_client or is_staff):
            raise ArtifactActionError("only the booking client or staff can select a slot")

    selected_date = _parse_local_date(str(selected.get("date") or ""), "date")
    selected_time = _parse_local_time(str(selected.get("time") or ""))
    duration = int(selected.get("duration_minutes") or 120)
    _, buffer_minutes, min_notice_hours, max_advance_days, allow_same_day = _artist_booking_rules(artist_user)

    busy_blocks: list[dict] = []
    if artist_calendar_connected(artist_user):
        try:
            busy_blocks = await get_availability(
                start_date=selected_date.isoformat(),
                end_date=selected_date.isoformat(),
                calendar_id=artist_calendar_id(artist_user),
                refresh_token=artist_user.google_refresh_token,
            )
        except Exception as exc:
            logger.error("Slot proposal calendar read failed for booking %s: %s", booking.id, exc)
            raise ArtifactActionError("calendar availability check failed") from exc

    available = await _slot_is_available(
        db,
        artist_id=artist_user.id,
        scheduled_date=selected_date,
        scheduled_time=selected_time,
        duration_minutes=duration,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=min_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
        busy_blocks=busy_blocks,
        exclude_booking_id=booking.id,
    )
    if not available:
        raise ArtifactActionError("this time is no longer available")

    # Lock the booking's schedule
    booking.scheduled_date = selected_date
    booking.scheduled_time = selected_time
    booking.duration_minutes = duration
    booking.updated_at = datetime.now(UTC)

    # If the booking doesn't have a confirmed status yet, move it there
    # only if a deposit has already been collected or is $0.
    # Otherwise leave it in 'quoted' — the deposit artifact will confirm it.
    if booking.status in (
        BookingStatus.PENDING,
        BookingStatus.QUOTED,
    ):
        has_deposit = (booking.deposit_amount or 0) > 0
        deposit_paid = booking.deposit_paid
        if not has_deposit or deposit_paid:
            booking.status = BookingStatus.CONFIRMED

    state.update(
        {
            "status": "confirmed",
            "selected_index": index,
            "confirmed_at": datetime.now(UTC).isoformat(),
        }
    )

    logger.info(
        "slot_proposal confirmed: booking %s scheduled for %s %s",
        booking.id,
        selected_date,
        selected_time,
    )

    # Auto-sync to Google Calendar
    try:
        if artist_user and artist_calendar_connected(artist_user):
            client_name = booking.guest_name or "Client"
            client_email = booking.guest_email
            if booking.client_id:
                client_r = await db.execute(
                    select(BTTCOUser.name, BTTCOUser.email).where(BTTCOUser.id == booking.client_id)
                )
                client_row = client_r.first()
                if client_row:
                    client_name = client_row[0] or client_name
                    client_email = client_row[1] or client_email
            cal_event = await create_booking_event(
                {
                    "client_name": client_name,
                    "client_email": client_email,
                    "description": booking.description or "",
                    "placement": booking.placement,
                    "reference_code": booking.reference_code,
                    "scheduled_date": selected_date,
                    "scheduled_time": selected_time,
                    "duration_minutes": duration,
                },
                calendar_id=artist_calendar_id(artist_user),
                refresh_token=artist_user.google_refresh_token,
            )
            booking.google_calendar_event_id = cal_event["event_id"]
            logger.info("Auto-synced booking %s to calendar: %s", booking.id, cal_event["event_id"])
    except Exception as cal_err:
        logger.error("Calendar sync failed for booking %s: %s", booking.id, cal_err)

    return state


async def _apply_cancel(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("status") in ("cancelled", "confirmed"):
        raise ArtifactActionError(f"cannot cancel a {state.get('status')} slot_proposal")
    state["status"] = "cancelled"
    state["cancelled_at"] = datetime.now(UTC).isoformat()
    return state


async def _apply_action(
    db: AsyncSession,
    message: Any,
    action: str,
    actor: BTTCOUser | None,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(message.artifact_payload or {})
    state = dict(message.artifact_state or INITIAL_STATE)

    if action == "select_slot":
        return await _apply_select_slot(db, message, actor, payload, state, data)

    if action == "cancel":
        return await _apply_cancel(state)

    raise ArtifactActionError(f"unhandled slot_proposal action: {action}")


# ── Registration ─────────────────────────────────────────────────

register(
    ArtifactHandler(
        message_type="slot_proposal",
        payload_schema=SlotProposalPayload,
        initial_state=INITIAL_STATE,
        allowed_actions=frozenset({"select_slot", "cancel"}),
        apply_action=_apply_action,
    )
)
