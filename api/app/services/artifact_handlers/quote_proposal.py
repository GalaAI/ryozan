"""
Quote Proposal — interactive message artifact.

The artist (or any staff role: assistant, artist, admin, founder)
attaches pricing to a conversation message. The client sees an
interactive card inside the conversation with CONFIRM and CANCEL
actions.

State machine:

    pending ──confirm──> confirmed           (deposit == 0)
            ─confirm──> awaiting_payment ──stripe_paid──> confirmed
                                         (deposit > 0)
            ──cancel───> cancelled

When confirmed with a non-zero deposit, the handler creates a
Stripe Checkout session and returns its URL in the new state so
the frontend can redirect the client to pay. When the Stripe
webhook later reports the session as complete, the message's
state transitions to `confirmed` and the attached booking is
stamped as CONFIRMED.

When confirmed with a zero deposit, the booking is marked
CONFIRMED immediately (no Stripe round-trip).

Supersede behavior: a client can hold multiple quote_proposals on
the same booking. The newest one does not automatically invalidate
older ones — the frontend can highlight the latest and gray out
earlier history. If you want strict supersede semantics, pass
`supersede_previous=True` at composer time (not implemented here;
the frontend can fire a cancel action on older artifacts instead).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import stripe
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.constants.status import BookingStatus
from app.core.config import get_settings
from app.core.security import ROLE_RANK
from app.models.booking import Booking
from app.models.user import User
from app.services.artifacts import (
    ArtifactActionError,
    ArtifactError,
    ArtifactHandler,
    register,
)
from app.services.calendar_sync import artist_calendar_connected, artist_calendar_id, create_booking_event
from app.services.payments import apply_customer_receipt, customer_price_cents, resolve_charge_routing
from app.services.stripe_client import get_stripe_client

logger = logging.getLogger("ryozan.artifacts.quote_proposal")


# ── Payload schema ───────────────────────────────────────────────


class QuoteProposalPayload(BaseModel):
    """
    Immutable payload for a quote_proposal artifact.

    Every field except booking_id is optional so staff or the artist
    can send a proposal with pricing alone, a slot alone, or any mix.
    The zero-deposit auto-confirm branch handles the "I just want to
    lock this in for $0 down" case.
    """

    booking_id: UUID
    deposit_amount: float = Field(default=0, ge=0)
    estimated_total_min: float | None = Field(default=None, ge=0)
    estimated_total_max: float | None = Field(default=None, ge=0)
    expires_in_days: int = Field(default=7, ge=1, le=30)


# ── Initial state ────────────────────────────────────────────────


INITIAL_STATE: dict[str, Any] = {
    "status": "pending",
    "confirmed_at": None,
    "cancelled_at": None,
    "checkout_url": None,
    "stripe_checkout_session_id": None,
}


# ── Action handler ───────────────────────────────────────────────


def _init_stripe() -> None:
    stripe.api_key = get_settings().STRIPE_SECRET_KEY


def _expired(payload: dict[str, Any], message_created_at: datetime) -> bool:
    """True if the quote proposal's TTL window (default 7d) has passed."""
    days = int(payload.get("expires_in_days", 7))
    return datetime.now(UTC) > message_created_at + timedelta(days=days)


async def _load_booking(db: AsyncSession, booking_id: UUID) -> Booking:
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if booking is None:
        raise ArtifactError(f"booking {booking_id} not found")
    return booking


async def _apply_confirm(
    db: AsyncSession,
    message: Any,
    actor: User | None,
    payload: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Client (or staff on their behalf) confirms the quote."""
    if state.get("status") not in (None, "pending"):
        raise ArtifactActionError(f"cannot confirm a {state.get('status')} quote_proposal")

    if _expired(payload, message.created_at):
        state["status"] = "expired"
        return state

    booking = await _load_booking(db, UUID(payload["booking_id"]))

    # Record pricing on the booking so legacy UI still reflects the
    # accepted numbers.
    deposit = float(payload.get("deposit_amount") or 0)
    if deposit > 0:
        booking.deposit_amount = deposit
    if payload.get("estimated_total_max") is not None:
        booking.estimated_total = float(payload["estimated_total_max"])
    elif payload.get("estimated_total_min") is not None:
        booking.estimated_total = float(payload["estimated_total_min"])
    booking.updated_at = datetime.now(UTC)

    # ── Branch A: zero-deposit → confirm immediately ──
    if deposit <= 0:
        booking.status = BookingStatus.CONFIRMED
        state.update(
            {
                "status": "confirmed",
                "confirmed_at": datetime.now(UTC).isoformat(),
                "checkout_url": None,
                "stripe_checkout_session_id": None,
            }
        )
        logger.info(
            "quote_proposal auto-confirmed (zero deposit) for booking %s",
            booking.id,
        )

        # Auto-sync to Google Calendar
        if booking.scheduled_date:
            try:
                artist_r = await db.execute(select(User).where(User.id == booking.artist_id))
                artist = artist_r.scalar_one_or_none()
                if artist and artist_calendar_connected(artist):
                    client_name = booking.guest_name or "Client"
                    client_email = booking.guest_email
                    if booking.client_id:
                        client_r = await db.execute(select(User.name, User.email).where(User.id == booking.client_id))
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
                            "scheduled_date": booking.scheduled_date,
                            "scheduled_time": booking.scheduled_time or "12:00",
                            "duration_minutes": booking.duration_minutes or 120,
                        },
                        calendar_id=artist_calendar_id(artist),
                        refresh_token=artist.google_refresh_token,
                    )
                    booking.google_calendar_event_id = cal_event["event_id"]
                    logger.info("Auto-synced booking %s to calendar: %s", booking.id, cal_event["event_id"])
            except Exception as cal_err:
                logger.error("Calendar sync failed for booking %s: %s", booking.id, cal_err)

        return state

    # ── Branch B: deposit > 0 → Stripe Checkout ──
    _init_stripe()
    settings = get_settings()

    artist_result = await db.execute(select(User).where(User.id == booking.artist_id))
    artist = artist_result.scalar_one_or_none()
    if artist is None:
        raise ArtifactError("booking has no assigned artist")

    service_price_cents = int(deposit * 100)
    deposit_cents = customer_price_cents(service_price_cents)
    client_email: str | None = booking.guest_email
    if booking.client_id:
        client_result = await db.execute(select(User.email).where(User.id == booking.client_id))
        row = client_result.first()
        if row:
            client_email = row[0]

    checkout_params: dict[str, Any] = {
        "mode": "payment",
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Tattoo Deposit — {artist.name}",
                        "description": (f"Deposit for booking {booking.reference_code or booking.id}"),
                    },
                    "unit_amount": deposit_cents,
                },
                "quantity": 1,
            }
        ],
        "success_url": (f"{settings.FRONTEND_URL}/portal?booking={booking.id}&deposit=paid"),
        "cancel_url": (f"{settings.FRONTEND_URL}/portal?booking={booking.id}&deposit=cancelled"),
        "metadata": {
            "booking_id": str(booking.id),
            "message_id": str(message.id),
            "artifact_type": "quote_proposal",
            "artifact_message_id": str(message.id),
            "artist_slug": artist.slug or "",
        },
    }
    if client_email:
        checkout_params["customer_email"] = client_email

    # Single source of truth for the revenue split (same resolver as the
    # legacy /quotes/public/{token}/accept path). Routes to the artist's
    # Connect account only when onboarding is complete, so a half-onboarded
    # artist can no longer make Stripe reject the whole Checkout Session.
    routing = resolve_charge_routing(
        artist,
        deposit_cents,
        artist_share_base_cents=service_price_cents,
    )
    checkout_params = routing.apply_to(checkout_params)
    checkout_params = apply_customer_receipt(
        checkout_params,
        customer_email=client_email,
        description=f"Ryozan tattoo deposit - {booking.reference_code or booking.id}",
        metadata={
            "checkout_type": "quote_proposal_deposit",
            "booking_id": str(booking.id),
            "reference_code": booking.reference_code or "",
            "artist_id": str(artist.id),
            "routing_destination": routing.destination,
            "routing_reason": routing.reason,
            **routing.receipt_metadata(),
        },
    )

    try:
        session = get_stripe_client().v1.checkout.sessions.create(
            checkout_params,
            routing.request_options(idempotency_key=f"quote-proposal-deposit-{message.id}"),
        )
    except Exception as exc:
        logger.error("stripe checkout failed for quote_proposal %s: %s", message.id, exc)
        raise ArtifactError(f"could not create checkout session: {exc}") from exc

    state.update(
        {
            "status": "awaiting_payment",
            "checkout_url": session.url,
            "stripe_checkout_session_id": session.id,
        }
    )
    logger.info(
        "quote_proposal %s → awaiting_payment via stripe session %s",
        message.id,
        session.id,
    )
    return state


async def _apply_cancel(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Either side can cancel a pending or awaiting_payment quote."""
    if state.get("status") in ("cancelled", "confirmed"):
        raise ArtifactActionError(f"cannot cancel a {state.get('status')} quote_proposal")
    state["status"] = "cancelled"
    state["cancelled_at"] = datetime.now(UTC).isoformat()
    return state


async def _apply_action(
    db: AsyncSession,
    message: Any,
    action: str,
    actor: User | None,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(message.artifact_payload or {})
    state = dict(message.artifact_state or INITIAL_STATE)

    if action == "confirm":
        # The client (booking owner) is the primary confirmer. With
        # STAFF_CONFIRM_FOR_CLIENT_ENABLED, staff can also confirm on the
        # client's behalf, e.g. after the client says yes in a DM.
        if actor is not None:
            booking = await _load_booking(db, UUID(payload["booking_id"]))
            is_staff = ROLE_RANK.get(actor.role, 0) >= ROLE_RANK.get("assistant", 1)
            is_client = booking.client_id == actor.id
            if not is_client:
                if not is_staff:
                    raise ArtifactActionError("only the booking client or staff can confirm this quote")
                if not get_settings().STAFF_CONFIRM_FOR_CLIENT_ENABLED:
                    raise ArtifactActionError("only the booking client can confirm this quote")
        return await _apply_confirm(db, message, actor, payload, state)

    if action == "cancel":
        return await _apply_cancel(state)

    # _apply_action is only called for actions the registry knows about,
    # so this branch is defensive only.
    raise ArtifactActionError(f"unhandled quote_proposal action: {action}")


# ── Registration ─────────────────────────────────────────────────


register(
    ArtifactHandler(
        message_type="quote_proposal",
        payload_schema=QuoteProposalPayload,
        initial_state=INITIAL_STATE,
        allowed_actions=frozenset({"confirm", "cancel"}),
        apply_action=_apply_action,
    )
)
