"""
Quote endpoints — create, send, and manage booking quotes.

Artist creates a quote → client gets email with link → client views
public quote page → picks slot → pays deposit via Stripe Checkout →
booking auto-confirmed.
"""

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.calendar import (
    _artist_booking_rules,
    _parse_local_date,
    _parse_local_time,
    _resolve_artist_work_schedule,
    _slot_is_available,
)
from app.constants.status import BookingStatus, QuoteStatus
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user, hash_password
from app.core.user_lookup import find_user_by_email
from app.models.booking import Booking
from app.models.booking_slot_hold import BookingSlotHold
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.quote import Quote
from app.models.user import BTTCOUser, normalize_email
from app.schemas.quote import QuoteAccept, QuoteCreate, QuotePublicView, QuoteResponse, TimeSlot
from app.services.calendar_sync import artist_calendar_connected, artist_calendar_id, get_availability
from app.services.email import FROM_SHOP, build_quote_email_html, render_transactional_email, send_email
from app.services.email_foundry import dispatch_foundry_trigger
from app.services.payments import apply_customer_receipt, customer_price_cents, resolve_charge_routing
from app.services.stripe_client import get_stripe_client

logger = logging.getLogger("bttco.quotes")
router = APIRouter(prefix="/quotes", tags=["quotes"])


def _require_role(user: BTTCOUser, min_role: str):
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} role")


def _init_stripe():
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY


async def _assert_quote_slot_available(
    *,
    db: AsyncSession,
    booking: Booking,
    artist: BTTCOUser,
    selected: dict,
) -> tuple:
    selected_date = _parse_local_date(str(selected.get("date") or ""), "date")
    selected_time = _parse_local_time(str(selected.get("time") or ""))
    duration = int(selected.get("duration_minutes") or booking.duration_minutes or 120)
    if selected.get("staff_approved") and selected.get("staff_override"):
        # Management approved this exact slot past the normal rules.
        return selected_date, selected_time, duration
    _, buffer_minutes, min_notice_hours, max_advance_days, allow_same_day = _artist_booking_rules(artist)

    busy_blocks: list[dict] = []
    if artist_calendar_connected(artist):
        try:
            busy_blocks = await get_availability(
                start_date=selected_date.isoformat(),
                end_date=selected_date.isoformat(),
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
        except Exception as exc:
            logger.error("Quote acceptance calendar read failed for booking %s: %s", booking.id, exc)
            raise HTTPException(status_code=502, detail="Calendar availability check failed") from exc

    available = await _slot_is_available(
        db,
        artist_id=artist.id,
        scheduled_date=selected_date,
        scheduled_time=selected_time,
        duration_minutes=duration,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=min_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
        busy_blocks=busy_blocks,
        exclude_booking_id=booking.id,
        work_schedule=_resolve_artist_work_schedule(artist) if selected.get("staff_approved") else None,
    )
    if not available:
        raise HTTPException(status_code=409, detail="This time is no longer available")
    return selected_date, selected_time, duration


@router.post("/", response_model=QuoteResponse, status_code=201)
async def create_quote(
    body: QuoteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Staff creates a quote (or conversational reply) for a pending booking.

    All pricing + scheduling fields are optional: an artist or assistant can
    send a pure message as the first touch. Adding a slot makes it bookable,
    adding a deposit routes the client through Stripe on acceptance.

    Allowed roles: assistant, artist, admin, founder. Assistants can quote on
    behalf of any artist (the shop manages their calendar).
    """
    _require_role(current_user, "assistant")

    # Fetch the booking
    result = await db.execute(select(Booking).where(Booking.id == body.booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Assigned artist owns their bookings; assistants + admin+ can quote on
    # behalf of any artist in the shop.
    current_rank = ROLE_RANK.get(current_user.role, 0)
    is_staff = current_rank >= ROLE_RANK.get("assistant", 1)
    is_assigned = booking.artist_id == current_user.id
    if not (is_assigned or is_staff):
        raise HTTPException(status_code=403, detail="Not assigned to this booking")

    if booking.status not in (BookingStatus.PENDING, BookingStatus.QUOTED):
        raise HTTPException(status_code=400, detail=f"Cannot quote a {booking.status} booking")

    # Create quote
    slots_json = json.dumps([s.model_dump() for s in body.available_slots])
    quote = Quote(
        booking_id=booking.id,
        estimated_total_min=body.estimated_total_min,
        estimated_total_max=body.estimated_total_max,
        deposit_amount=body.deposit_amount,
        available_slots_json=slots_json,
        artist_message=body.artist_message,
        expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days),
    )
    db.add(quote)

    # Update booking status to quoted
    booking.status = BookingStatus.QUOTED
    booking.deposit_amount = body.deposit_amount
    booking.estimated_total = body.estimated_total_max or body.estimated_total_min
    booking.updated_at = datetime.now(UTC)

    await db.flush()

    # Get client email (guest or registered)
    client_email = booking.guest_email
    client_name = booking.guest_name or "Client"
    if booking.client_id:
        client_result = await db.execute(
            select(BTTCOUser.email, BTTCOUser.name).where(BTTCOUser.id == booking.client_id)
        )
        client_row = client_result.first()
        if client_row:
            client_email, client_name = client_row

    # Get artist name
    artist_result = await db.execute(select(BTTCOUser.name).where(BTTCOUser.id == booking.artist_id))
    artist_name = artist_result.scalar_one_or_none() or "Your Artist"

    # Send quote email
    if client_email:
        settings = get_settings()
        quote_url = f"{settings.FRONTEND_URL}/quote/{quote.token}"
        customer_deposit = customer_price_cents(int(float(body.deposit_amount) * 100)) / 100
        customer_estimated_min = (
            customer_price_cents(int(float(body.estimated_total_min) * 100)) / 100 if body.estimated_total_min else None
        )
        customer_estimated_max = (
            customer_price_cents(int(float(body.estimated_total_max) * 100)) / 100 if body.estimated_total_max else None
        )
        try:
            fallback_subject = f"Your Tattoo Quote from {artist_name} — Ryozan Studio"
            fallback_html = build_quote_email_html(
                client_name=client_name,
                artist_name=artist_name,
                deposit_amount=customer_deposit,
                estimated_min=customer_estimated_min,
                estimated_max=customer_estimated_max,
                slots=body.available_slots,
                artist_message=body.artist_message,
                quote_url=quote_url,
                reference_code=booking.reference_code,
            )
            estimated_range = (
                f"${customer_estimated_min:,.2f} - ${customer_estimated_max:,.2f}"
                if customer_estimated_min is not None and customer_estimated_max is not None
                else f"${customer_estimated_min or customer_estimated_max:,.2f}"
                if customer_estimated_min is not None or customer_estimated_max is not None
                else "Final total confirmed with your artist"
            )
            foundry_variables = {
                "client_name": client_name,
                "artist_name": artist_name,
                "deposit_amount": f"${customer_deposit:,.2f}",
                "estimated_range": estimated_range,
                "artist_message": body.artist_message or "",
                "quote_url": quote_url,
                "reference_code": booking.reference_code,
            }
            dispatch = {"matched": []}
            try:
                dispatch = await dispatch_foundry_trigger(
                    db,
                    "quote_dealt",
                    recipient=client_email,
                    variables=foundry_variables,
                    event_id=f"quote:{quote.id}:dealt",
                    delivery={"from_alias": FROM_SHOP},
                )
            except Exception as exc:
                logger.error("Foundry quote trigger failed for %s: %s", quote.id, exc)
            if "deposit_request" not in dispatch["matched"]:
                rendered = await render_transactional_email(
                    "deposit_request",
                    foundry_variables,
                    fallback_subject=fallback_subject,
                    fallback_html=fallback_html,
                    legacy_slug="quote_sent",
                )
                await send_email(
                    to=client_email,
                    subject=rendered.subject,
                    html_body=rendered.html_body,
                    from_alias=FROM_SHOP,
                )
        except Exception as exc:
            logger.error("Quote email failed for %s: %s", client_email, exc)

    # ── Auto-create conversation thread ──
    # Only if the client has an account (guest bookings have no user to message)
    if booking.client_id:
        try:
            # Check if a 1:1 conversation already exists between artist and client
            my_convs = (
                select(ConversationParticipant.conversation_id).where(
                    ConversationParticipant.user_id == current_user.id
                )
            ).subquery()
            existing = await db.execute(
                select(Conversation)
                .join(ConversationParticipant)
                .where(
                    Conversation.is_group.is_(False),
                    Conversation.id.in_(select(my_convs.c.conversation_id)),
                    ConversationParticipant.user_id == booking.client_id,
                )
            )
            conv = existing.scalar_one_or_none()

            if not conv:
                # Create a new conversation named after the client
                conv = Conversation(
                    name=client_name,
                    is_group=False,
                    created_by=current_user.id,
                )
                db.add(conv)
                await db.flush()
                # Add both participants
                db.add(
                    ConversationParticipant(
                        conversation_id=conv.id,
                        user_id=current_user.id,
                        role="owner",
                    )
                )
                db.add(
                    ConversationParticipant(
                        conversation_id=conv.id,
                        user_id=booking.client_id,
                        role="member",
                    )
                )

            # Post the artist's quote message as the first message
            if body.artist_message:
                db.add(
                    Message(
                        conversation_id=conv.id,
                        sender_id=current_user.id,
                        content=body.artist_message,
                        message_type="text",
                    )
                )
                conv.updated_at = datetime.now(UTC)

            # Link conversation to booking if not already linked
            if not booking.conversation_id:
                booking.conversation_id = conv.id

            await db.flush()
        except Exception as exc:
            logger.error("Auto-conversation failed for booking %s: %s", booking.id, exc)

    # Build response
    return QuoteResponse(
        id=quote.id,
        booking_id=quote.booking_id,
        token=quote.token,
        estimated_total_min=float(quote.estimated_total_min) if quote.estimated_total_min else None,
        estimated_total_max=float(quote.estimated_total_max) if quote.estimated_total_max else None,
        deposit_amount=float(quote.deposit_amount),
        available_slots=body.available_slots,
        artist_message=quote.artist_message,
        status=quote.status,
        sent_at=quote.sent_at,
        expires_at=quote.expires_at,
    )


@router.get("/booking/{booking_id}", response_model=list[QuoteResponse])
async def get_quotes_for_booking(
    booking_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Get all quotes for a booking (staff view — assistant+)."""
    _require_role(current_user, "assistant")

    result = await db.execute(select(Quote).where(Quote.booking_id == booking_id).order_by(Quote.created_at.desc()))
    quotes = result.scalars().all()

    responses = []
    for q in quotes:
        slots = json.loads(q.available_slots_json) if q.available_slots_json else []
        responses.append(
            QuoteResponse(
                id=q.id,
                booking_id=q.booking_id,
                token=q.token,
                estimated_total_min=float(q.estimated_total_min) if q.estimated_total_min else None,
                estimated_total_max=float(q.estimated_total_max) if q.estimated_total_max else None,
                deposit_amount=float(q.deposit_amount),
                available_slots=[TimeSlot(**s) for s in slots],
                artist_message=q.artist_message,
                status=q.status,
                sent_at=q.sent_at,
                viewed_at=q.viewed_at,
                accepted_at=q.accepted_at,
                expires_at=q.expires_at,
                selected_date=q.selected_date,
                selected_time=q.selected_time,
                selected_duration=q.selected_duration,
            )
        )
    return responses


# ── Public endpoints (no auth) ──────────────────


@router.get("/public/{token}", response_model=QuotePublicView)
async def get_public_quote(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public: client views their quote via token link from email."""
    result = await db.execute(select(Quote).where(Quote.token == token))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    # Mark as viewed
    if not quote.viewed_at:
        quote.viewed_at = datetime.now(UTC)
        if quote.status == QuoteStatus.SENT:
            quote.status = "viewed"
        await db.flush()

    # Check expiry
    expired = quote.expires_at and datetime.now(UTC) > quote.expires_at

    # Get booking + artist info
    booking_r = await db.execute(select(Booking).where(Booking.id == quote.booking_id))
    booking = booking_r.scalar_one()

    artist_r = await db.execute(select(BTTCOUser.name, BTTCOUser.avatar_url).where(BTTCOUser.id == booking.artist_id))
    artist_row = artist_r.first()

    client_name = booking.guest_name or "Client"
    if booking.client_id:
        client_r = await db.execute(select(BTTCOUser.name).where(BTTCOUser.id == booking.client_id))
        client_name = client_r.scalar_one_or_none() or client_name

    slots = json.loads(quote.available_slots_json) if quote.available_slots_json else []

    return QuotePublicView(
        artist_name=artist_row[0] if artist_row else "Artist",
        artist_avatar=artist_row[1] if artist_row else None,
        client_name=client_name,
        description=booking.description,
        placement=booking.placement,
        size_estimate=booking.size_estimate,
        estimated_total_min=float(quote.estimated_total_min) if quote.estimated_total_min else None,
        estimated_total_max=float(quote.estimated_total_max) if quote.estimated_total_max else None,
        deposit_amount=float(quote.deposit_amount),
        available_slots=[TimeSlot(**s) for s in slots],
        artist_message=quote.artist_message,
        status=quote.status,
        expired=expired or False,
        reference_code=booking.reference_code,
        selected_date=quote.selected_date,
        selected_time=quote.selected_time,
        selected_duration=quote.selected_duration,
    )


@router.post("/public/{token}/accept")
async def accept_quote(
    token: str,
    body: QuoteAccept,
    db: AsyncSession = Depends(get_db),
):
    """
    Public: client accepts a quote.

    Two branches based on the artist's pricing:
      * Deposit > 0  → redirect to Stripe Checkout to collect the deposit;
        the booking is confirmed by the Stripe webhook after payment succeeds.
      * Deposit == 0 → no payment, skip Stripe entirely, confirm the quote
        and booking immediately, and return the confirmation URL.
    """
    settings = get_settings()

    result = await db.execute(select(Quote).where(Quote.token == token))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    if quote.status in (QuoteStatus.ACCEPTED, QuoteStatus.EXPIRED):
        raise HTTPException(status_code=400, detail=f"Quote is already {quote.status}")

    # Check expiry
    if quote.expires_at and datetime.now(UTC) > quote.expires_at:
        quote.status = QuoteStatus.EXPIRED
        await db.flush()
        raise HTTPException(status_code=400, detail="Quote has expired")

    # Parse slots and validate selection
    slots = json.loads(quote.available_slots_json) if quote.available_slots_json else []
    if body.selected_slot_index >= len(slots):
        raise HTTPException(status_code=400, detail="Invalid slot selection")
    selected = slots[body.selected_slot_index]

    # Get booking + artist info
    booking_r = await db.execute(select(Booking).where(Booking.id == quote.booking_id))
    booking = booking_r.scalar_one()

    artist_r = await db.execute(select(BTTCOUser).where(BTTCOUser.id == booking.artist_id))
    artist = artist_r.scalar_one()
    selected_date, selected_time, selected_duration = await _assert_quote_slot_available(
        db=db,
        booking=booking,
        artist=artist,
        selected=selected,
    )

    client_email = booking.guest_email
    if booking.client_id:
        client_r = await db.execute(select(BTTCOUser.email).where(BTTCOUser.id == booking.client_id))
        client_row = client_r.first()
        if client_row:
            client_email = client_row[0]

    service_price_cents = int(float(quote.deposit_amount) * 100)

    # Always record the selected slot on the quote
    quote.selected_date = datetime.combine(selected_date, datetime.min.time(), tzinfo=UTC)
    quote.selected_time = selected_time
    quote.selected_duration = selected_duration

    # ── Branch A: zero-deposit → confirm immediately, no Stripe ──
    if service_price_cents <= 0:
        quote.status = QuoteStatus.ACCEPTED
        quote.accepted_at = datetime.now(UTC)
        booking.status = BookingStatus.CONFIRMED
        booking.scheduled_date = selected_date
        booking.scheduled_time = selected_time
        booking.duration_minutes = selected_duration
        booking.updated_at = datetime.now(UTC)
        await db.flush()
        return {"checkout_url": f"{settings.FRONTEND_URL}/quote/{token}/confirmed"}

    # ── Branch B: deposit > 0 → Stripe Checkout ──
    _init_stripe()
    deposit_cents = customer_price_cents(service_price_cents)
    hold_token = secrets.token_urlsafe(32)
    hold_expires_at = datetime.now(UTC) + timedelta(minutes=31)
    db.add(
        BookingSlotHold(
            hold_token=hold_token,
            artist_id=artist.id,
            artist_slug=artist.slug or "artist",
            scheduled_date=selected_date,
            scheduled_time=selected_time,
            duration_minutes=selected_duration,
            status="active",
            inquiry_id=f"quote:{quote.id}",
            guest_email=client_email,
            booking_id=booking.id,
            expires_at=hold_expires_at,
        )
    )

    checkout_params = {
        "mode": "payment",
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Tattoo Deposit — {artist.name}",
                        "description": f"Deposit for tattoo session on {selected_date} at {selected_time}",
                    },
                    "unit_amount": deposit_cents,
                },
                "quantity": 1,
            }
        ],
        "expires_at": int(hold_expires_at.timestamp()),
        "success_url": f"{settings.FRONTEND_URL}/quote/{token}/confirmed",
        "cancel_url": f"{settings.FRONTEND_URL}/quote/{token}",
        "metadata": {
            "quote_id": str(quote.id),
            "booking_id": str(booking.id),
            "quote_token": token,
            "slot_hold_token": hold_token,
            "artist_slug": artist.slug or "",
        },
    }

    # Add customer email for receipt
    if client_email:
        checkout_params["customer_email"] = client_email

    # Single source of truth for the revenue split. Routes to the artist's
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
            "checkout_type": "quote_deposit",
            "booking_id": str(booking.id),
            "reference_code": booking.reference_code or "",
            "artist_id": str(artist.id),
            "routing_destination": routing.destination,
            "routing_reason": routing.reason,
            **routing.receipt_metadata(),
        },
    )

    session = get_stripe_client().v1.checkout.sessions.create(
        checkout_params,
        routing.request_options(idempotency_key=f"quote-deposit-{hold_token}"),
    )
    quote.stripe_checkout_session_id = session.id
    await db.flush()

    return {"checkout_url": session.url}


# ── Guest reply flow ──────────────────────────────────────────────
#
# Before this endpoint existed, the public quote page had a chat input
# that POSTed to a non-existent /quotes/public/{token}/reply route. The
# frontend caught the 404 and fell back to opening a mailto: link in the
# client's browser — so guest replies disappeared into email rather than
# landing in the portal conversation thread.
#
# This endpoint fixes that by:
#   1. Resolving a conversation for the booking, creating one on demand
#      if the client is a guest (no bttco_users row yet).
#   2. Creating a "pending account" for the guest keyed by their email
#      with a random unusable password + must_reset_password=True. On
#      their next real login they'll be upgraded into a real collector.
#   3. Posting the reply as a message in the conversation so staff can
#      respond in-portal via the existing Courier UI.


class GuestQuoteReply(BaseModel):
    """Body for POST /quotes/public/{token}/reply."""

    message: str = Field(min_length=1, max_length=4000)


async def _ensure_guest_user(db: AsyncSession, email: str, name: str | None) -> BTTCOUser:
    """
    Look up (or create) a pending-account collector row for a guest
    email. Ensures every guest conversation has a real sender_id to
    satisfy the bttco_messages.sender_id NOT NULL constraint.

    Guests created this way get `must_reset_password=True` so their
    next password-reset link upgrades them into a full account with
    their booking history already attached.
    """
    normalized = normalize_email(email)
    user = await find_user_by_email(db, normalized)
    if user:
        return user

    # Random 32-char disabled password — the guest can never log in with
    # this. They'll only gain portal access via the password-reset flow.
    disabled_pw = hash_password(secrets.token_urlsafe(24))
    user = BTTCOUser(
        email=normalized,
        password_hash=disabled_pw,
        name=(name or "Guest").strip()[:255],
        role="collector",
        must_reset_password=True,
    )
    db.add(user)
    await db.flush()
    logger.info("created pending-account user for guest reply: %s", normalized)
    return user


async def _ensure_conversation_for_booking(
    db: AsyncSession,
    booking: Booking,
    artist_id: UUID,
    client_user: BTTCOUser,
) -> Conversation:
    """
    Resolve or create the 1:1 conversation between this booking's
    assigned artist and the client. Re-used by both the guest reply
    flow and the staff quote-create flow so there's exactly one
    thread per artist/client pair.
    """
    if booking.conversation_id:
        existing = await db.execute(select(Conversation).where(Conversation.id == booking.conversation_id))
        conv = existing.scalar_one_or_none()
        if conv:
            return conv

    # Look for an existing 1:1 between this artist + client
    my_convs = (
        select(ConversationParticipant.conversation_id).where(ConversationParticipant.user_id == artist_id)
    ).subquery()
    existing = await db.execute(
        select(Conversation)
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .where(
            Conversation.is_group.is_(False),
            Conversation.id.in_(select(my_convs.c.conversation_id)),
            ConversationParticipant.user_id == client_user.id,
        )
    )
    conv = existing.scalar_one_or_none()
    if conv:
        booking.conversation_id = conv.id
        return conv

    # Create a fresh 1:1
    conv = Conversation(
        name=client_user.name or booking.guest_name or "Client",
        is_group=False,
        created_by=artist_id,
    )
    db.add(conv)
    await db.flush()
    db.add(
        ConversationParticipant(
            conversation_id=conv.id,
            user_id=artist_id,
            role="owner",
        )
    )
    db.add(
        ConversationParticipant(
            conversation_id=conv.id,
            user_id=client_user.id,
            role="member",
        )
    )
    booking.conversation_id = conv.id
    await db.flush()
    return conv


@router.post("/public/{token}/reply", status_code=204)
async def public_quote_reply(
    token: str,
    body: GuestQuoteReply,
    db: AsyncSession = Depends(get_db),
):
    """
    Public (no auth): the client replies to a quote from the public
    quote page. Lands in a real conversation thread that staff can
    read and respond to from the portal.

    This endpoint is deliberately permissive about quote status — a
    client can reply even if the quote is viewed, accepted, or
    expired. The only hard gate is that the token must resolve.
    """
    result = await db.execute(select(Quote).where(Quote.token == token))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    booking_r = await db.execute(select(Booking).where(Booking.id == quote.booking_id))
    booking = booking_r.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Resolve the client — registered account takes precedence, else
    # promote the guest_email into a pending-account row.
    if booking.client_id:
        client_r = await db.execute(select(BTTCOUser).where(BTTCOUser.id == booking.client_id))
        client_user = client_r.scalar_one_or_none()
        if not client_user:
            raise HTTPException(status_code=404, detail="Client account missing")
    else:
        if not booking.guest_email:
            raise HTTPException(
                status_code=400,
                detail="Cannot attribute reply — booking has no email on file",
            )
        client_user = await _ensure_guest_user(db, booking.guest_email, booking.guest_name)
        # Link the booking to the new pending-account client so future
        # messages route through the normal authed flow.
        booking.client_id = client_user.id

    conv = await _ensure_conversation_for_booking(db, booking, booking.artist_id, client_user)

    message = Message(
        conversation_id=conv.id,
        sender_id=client_user.id,
        content=body.message.strip(),
        message_type="text",
    )
    db.add(message)
    conv.updated_at = datetime.now(UTC)
    await db.flush()
    logger.info("guest reply posted for quote %s (booking %s)", token, booking.id)
