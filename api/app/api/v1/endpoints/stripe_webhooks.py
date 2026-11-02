"""
Stripe webhook handler — processes payment events.

The critical event is checkout.session.completed, which fires when
a client pays their deposit. This auto-confirms the booking and
schedules the session.
"""

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import stripe
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints.bookings import _sync_hustl_deposit_followup_task
from app.api.v1.endpoints.gift_cards import activate_paid_gift_card, void_refunded_gift_card, void_unpaid_gift_card
from app.constants.status import BookingStatus, QuoteStatus
from app.constants.timezones import shop_timezone
from app.core.config import get_settings
from app.core.database import async_session
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.booking_slot_hold import BookingSlotHold
from app.models.flash import FlashDesign
from app.models.message import Message
from app.models.notification import Notification as NotificationModel
from app.models.pos_transaction import POSTransaction
from app.models.quote import Quote
from app.models.user import User
from app.services.calendar_sync import artist_calendar_connected, artist_calendar_id, create_booking_event
from app.services.email import (
    FROM_ADMIN,
    FROM_SERVICE,
    build_deposit_artist_notification_html,
    build_deposit_confirmation_html,
    render_transactional_email,
    send_email,
    should_notify,
)
from app.services.email_foundry import dispatch_foundry_trigger
from app.services.payments import connect_account_ready, upsert_stripe_ledger_entry
from app.services.quest_hooks import check_and_complete_quest
from app.services.reward_engine import grant_reward, reward_user_by_id
from app.services.stripe_client import get_stripe_client

logger = logging.getLogger("ryozan.stripe.webhooks")
router = APIRouter(prefix="/stripe", tags=["stripe-webhooks"])

CHECKOUT_EVENTS = {
    "checkout.session.async_payment_failed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.completed",
    "checkout.session.expired",
}

PAYMENT_INTENT_EVENTS = {
    "payment_intent.amount_capturable_updated",
    "payment_intent.canceled",
    "payment_intent.payment_failed",
    "payment_intent.succeeded",
}

CHARGE_EVENTS = {
    "charge.dispute.closed",
    "charge.dispute.created",
    "charge.failed",
    "charge.refunded",
    "charge.succeeded",
}


async def _render_deposit_paid_email(
    booking: Booking,
    *,
    client_name: str,
    artist_name: str,
    deposit_amount: float,
):
    """Render the client receipt from the locked Email Foundry dependency."""

    fallback_subject = f"You're Booked! — {get_settings().STUDIO_NAME}"
    fallback_html = build_deposit_confirmation_html(
        client_name=client_name,
        artist_name=artist_name,
        description=booking.description or "",
        placement=booking.placement,
        deposit_amount=deposit_amount,
        scheduled_date=str(booking.scheduled_date) if booking.scheduled_date else None,
        scheduled_time=str(booking.scheduled_time) if booking.scheduled_time else None,
        duration_minutes=booking.duration_minutes,
        reference_code=booking.reference_code or "",
    )
    scheduled_date = booking.scheduled_date
    return await render_transactional_email(
        "deposit_paid",
        {
            "client_name": client_name,
            "artist_name": artist_name,
            "description": booking.description or "",
            "placement": booking.placement or "",
            "deposit_amount": f"${deposit_amount:,.2f}",
            "scheduled_date": str(scheduled_date) if scheduled_date else "Date confirmed with your artist",
            "scheduled_time": str(booking.scheduled_time) if booking.scheduled_time else "",
            "date_num": str(scheduled_date.day) if scheduled_date else "",
            "date_mon": scheduled_date.strftime("%b").upper() if scheduled_date else "",
            "reference_code": booking.reference_code or "",
            "pass_url": f"{get_settings().FRONTEND_URL}/portal",
        },
        fallback_subject=fallback_subject,
        fallback_html=fallback_html,
        legacy_slug="deposit_confirmation",
    )


async def _render_deposit_artist_notification(
    booking: Booking,
    *,
    client_name: str,
    client_email: str,
    artist_name: str,
    deposit_amount: float,
):
    """Render the internal deposit notice from the locked Email Foundry dependency.

    Three webhook paths send this notice and every one of them called
    render_db_template directly, so the shop notice never reached Foundry at all
    while the client receipt did. That is why the two looked nothing alike.

    They also formatted the amount three different ways, none of them currency:
    `str(150.0)` renders "150.0". That decision now lives here, once.

    Foundry owns the design. The database row stays reachable as the legacy
    fallback, which matters because that row has a literal "$0" typed into it
    where the merge field belongs.
    """
    fallback_subject = f"Deposit Received — {client_name} ({booking.reference_code or 'N/A'})"
    fallback_html = build_deposit_artist_notification_html(
        client_name=client_name,
        client_email=client_email,
        artist_name=artist_name,
        description=booking.description or "",
        placement=booking.placement,
        deposit_amount=deposit_amount,
        scheduled_date=str(booking.scheduled_date) if booking.scheduled_date else None,
        scheduled_time=str(booking.scheduled_time) if booking.scheduled_time else None,
        duration_minutes=booking.duration_minutes,
        reference_code=booking.reference_code or "",
    )
    return await render_transactional_email(
        "deposit_artist_notification",
        {
            "client_name": client_name,
            "client_email": client_email,
            "artist_name": artist_name,
            "description": booking.description or "",
            "placement": booking.placement or "",
            "deposit_amount": f"${deposit_amount:,.2f}",
            "scheduled_date": str(booking.scheduled_date) if booking.scheduled_date else "",
            "scheduled_time": str(booking.scheduled_time) if booking.scheduled_time else "",
            "duration_minutes": str(booking.duration_minutes or ""),
            "reference_code": booking.reference_code or "",
        },
        fallback_subject=fallback_subject,
        fallback_html=fallback_html,
        legacy_slug="deposit_artist_notification",
    )


async def _send_deposit_paid_delivery(
    db,
    booking: Booking,
    *,
    client_email: str,
    client_name: str,
    artist_name: str,
    deposit_amount: float,
) -> None:
    """Send or schedule the locked deposit/session lifecycle templates once."""
    scheduled_date = booking.scheduled_date
    variables = {
        "client_name": client_name,
        "artist_name": artist_name,
        "description": booking.description or "",
        "placement": booking.placement or "",
        "deposit_amount": f"${deposit_amount:,.2f}",
        "scheduled_date": str(scheduled_date) if scheduled_date else "Date confirmed with your artist",
        "scheduled_time": str(booking.scheduled_time) if booking.scheduled_time else "",
        "date_num": str(scheduled_date.day) if scheduled_date else "",
        "date_mon": scheduled_date.strftime("%b").upper() if scheduled_date else "",
        "reference_code": booking.reference_code or "",
        "pass_url": f"{get_settings().FRONTEND_URL}/portal",
    }
    deposit_dispatch = {"matched": []}
    try:
        deposit_dispatch = await dispatch_foundry_trigger(
            db,
            "deposit_paid",
            recipient=client_email,
            variables=variables,
            event_id=f"booking:{booking.id}:deposit-paid",
            delivery={"from_alias": FROM_SERVICE},
        )
    except Exception as exc:
        logger.error("Foundry deposit-paid trigger failed for %s: %s", booking.id, exc)
    if scheduled_date:
        try:
            await dispatch_foundry_trigger(
                db,
                "session_booked",
                recipient=client_email,
                variables=variables,
                event_id=f"booking:{booking.id}:session-booked",
                delivery={"from_alias": FROM_SERVICE},
            )
        except Exception as exc:
            logger.error("Foundry session-booked trigger failed for %s: %s", booking.id, exc)
    if "deposit_paid" in deposit_dispatch["matched"]:
        return
    rendered = await _render_deposit_paid_email(
        booking,
        client_name=client_name,
        artist_name=artist_name,
        deposit_amount=deposit_amount,
    )
    await send_email(
        to=client_email,
        subject=rendered.subject,
        html_body=rendered.html_body,
        from_alias=FROM_SERVICE,
    )


CUSTOMER_EVENTS = {
    "customer.created",
    "customer.deleted",
    "customer.updated",
}

REFUND_EVENTS = {
    "refund.created",
    "refund.failed",
    "refund.updated",
}

INVOICE_EVENTS = {
    "invoice.finalized",
    "invoice.paid",
    "invoice.payment_failed",
}

CONNECT_EVENTS = {
    "transfer.created",
    "transfer.reversed",
    "transfer.canceled",
    "transfer.updated",
    "payout.paid",
    "payout.failed",
    "account.updated",
}

CONNECT_THIN_EVENTS = {
    "v2.core.account[configuration.customer].capability_status_updated",
    "v2.core.account[configuration.merchant].capability_status_updated",
    "v2.core.account[requirements].updated",
}


@router.post("/connect/webhook")
async def stripe_connect_thin_webhook(request: Request):
    """Verify an Accounts v2 thin event and refresh merchant readiness."""
    settings = get_settings()
    secret = settings.STRIPE_CONNECT_THIN_WEBHOOK_SECRET.strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe Connect thin webhook signing is not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Stripe signature is required")

    stripe_client = get_stripe_client()
    try:
        notification = stripe_client.parse_event_notification(payload, sig_header, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    event_type = notification.type
    if event_type not in CONNECT_THIN_EVENTS:
        logger.info("Unhandled Stripe Connect thin event: %s", event_type)
        return {"received": True}

    try:
        related_account = notification.fetch_related_object()
        account_id = getattr(related_account, "id", None)
        if not account_id:
            raise ValueError("Stripe thin event did not resolve an account")
        account = stripe_client.v2.core.accounts.retrieve(
            account_id,
            {"include": ["configuration.merchant", "requirements"]},
        )
    except (ValueError, stripe.error.StripeError) as exc:
        logger.warning("Stripe Connect thin event refresh failed: type=%s", event_type)
        raise HTTPException(status_code=502, detail="Stripe Connect account refresh failed") from exc

    routing_ready = connect_account_ready(account)
    async with async_session() as db:
        result = await db.execute(select(User).where(User.stripe_account_id == account_id))
        user = result.scalar_one_or_none()
        if user:
            user.stripe_onboarding_complete = routing_ready
            user.updated_at = datetime.now(UTC)
            await db.commit()
            logger.info(
                "Artist %s Stripe merchant readiness refreshed: %s",
                user.id,
                routing_ready,
            )
    return {"received": True}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Handle Stripe webhook events.

    IMPORTANT: This endpoint must receive the raw body for signature
    verification. Do NOT parse JSON before verification.
    """
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    signing_secrets = [
        secret
        for secret in (
            settings.STRIPE_WEBHOOK_SECRET.strip(),
            getattr(settings, "STRIPE_CONNECT_WEBHOOK_SECRET", "").strip(),
        )
        if secret
    ]
    if signing_secrets:
        event = None
        for secret in signing_secrets:
            try:
                event = stripe.Webhook.construct_event(payload, sig_header, secret)
                break
            except stripe.error.SignatureVerificationError:
                continue
        if event is None:
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        is_local_dev = (
            settings.LOCAL_DEV_MODE
            or settings.DEBUG
            or settings.APP_ENV.lower()
            in {
                "development",
                "dev",
                "local",
                "test",
            }
        )
        if not is_local_dev:
            logger.error("stripe.webhook.no_secret_configured - rejecting webhook in non-dev environment")
            raise HTTPException(status_code=503, detail="Stripe webhook signing is not configured")
        logger.warning("stripe.webhook.no_secret_configured - accepting unsigned webhook in local/dev")
        event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)

    event_type = event.type
    event_object = event.data.object.to_dict()
    provider_event_id = getattr(event, "id", None)
    if event_type in CHECKOUT_EVENTS | PAYMENT_INTENT_EVENTS and not provider_event_id:
        raise HTTPException(status_code=400, detail="Stripe event ID is required")

    # Route events
    if event_type in CHECKOUT_EVENTS:
        await _handle_checkout_event(event_type, event_object, provider_event_id=provider_event_id)
    elif event_type in PAYMENT_INTENT_EVENTS:
        await _handle_payment_intent_event(event_type, event_object, provider_event_id=provider_event_id)
    elif event_type in CHARGE_EVENTS:
        await _handle_charge_event(event_type, event_object)
    elif event_type in CUSTOMER_EVENTS:
        await _handle_customer_event(event_type, event_object)
    elif event_type in REFUND_EVENTS:
        await _handle_refund_event(event_type, event_object)
    elif event_type in INVOICE_EVENTS:
        await _handle_invoice_event(event_type, event_object)
    elif event_type in CONNECT_EVENTS:
        await _handle_connect_event(event_type, event_object)
    else:
        logger.info("Unhandled Stripe event: %s", event_type)

    return {"received": True}


async def _handle_checkout_event(event_type: str, session, *, provider_event_id: str | None = None):
    metadata = session.get("metadata", {}) or {}
    if metadata.get("bttco_billing_kind") == "booth_rent":
        await _handle_booth_rent_checkout_event(event_type, session)
        return

    if event_type == "checkout.session.completed" and metadata.get("staged_deposit") == "true":
        await _handle_byof_authorization_ready(
            payment_intent_id=session.get("payment_intent"),
            booking_id=_parse_uuid(metadata.get("booking_id")),
            checkout_session_id=session.get("id"),
            provider_event_id=provider_event_id,
            source_event=event_type,
        )
        return

    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        payment_status = session.get("payment_status")
        amount_total = int(session.get("amount_total") or 0)
        if payment_status != "paid" or amount_total <= 0:
            logger.info(
                "Stripe checkout not fulfilled: event=%s session=%s payment_status=%s amount_total=%s",
                event_type,
                session.get("id"),
                payment_status,
                amount_total,
            )
            return
        await _record_checkout_payment(session)
        metadata = session.get("metadata", {})
        if metadata.get("checkout_type") == "gift_card":
            await activate_paid_gift_card(session)
        elif metadata.get("pos_charge") == "true":
            await _handle_pos_completed(session)
        else:
            await _handle_checkout_completed(session, provider_event_id=provider_event_id)
        return

    if event_type == "checkout.session.async_payment_failed":
        if metadata.get("checkout_type") == "gift_card":
            await void_unpaid_gift_card(session)
            return
        await _handle_checkout_async_payment_failed(session)
        return

    if event_type == "checkout.session.expired":
        if metadata.get("checkout_type") == "gift_card":
            await void_unpaid_gift_card(session)
            return
        await _handle_checkout_expired(session, provider_event_id=provider_event_id)


async def _handle_payment_intent_event(event_type: str, payment_intent, *, provider_event_id: str | None = None):
    if event_type == "payment_intent.succeeded":
        await _handle_payment_intent_succeeded(payment_intent, provider_event_id=provider_event_id)
    elif event_type in {"payment_intent.payment_failed", "payment_intent.canceled"}:
        await _handle_payment_intent_terminal_failure(
            event_type,
            payment_intent,
            provider_event_id=provider_event_id,
        )
    else:
        metadata = payment_intent.get("metadata", {}) or {}
        if metadata.get("staged_deposit") == "true":
            await _handle_byof_authorization_ready(
                payment_intent_id=payment_intent.get("id"),
                booking_id=_parse_uuid(metadata.get("booking_id")),
                checkout_session_id=None,
                provider_event_id=provider_event_id,
                source_event=event_type,
            )
        else:
            logger.info(
                "Stripe payment intent capturable amount updated: intent=%s amount_capturable=%s",
                payment_intent.get("id"),
                payment_intent.get("amount_capturable"),
            )


async def _handle_byof_authorization_ready(
    *,
    payment_intent_id: str | None,
    booking_id: uuid.UUID | None,
    checkout_session_id: str | None,
    provider_event_id: str | None,
    source_event: str,
) -> None:
    """Persist a manual-capture BYOF authorization without calling it paid."""
    if not payment_intent_id and not booking_id:
        logger.warning("BYOF authorization event has no booking or PaymentIntent identity")
        return
    async with async_session() as db:
        booking = await db.get(Booking, booking_id) if booking_id else None
        if booking is None and payment_intent_id:
            booking = await _find_booking_by_payment_intent(db, payment_intent_id)
        if booking is None or not booking.is_bring_your_own_flash:
            logger.warning("BYOF authorization event did not resolve a BYOF booking: %s", booking_id)
            return
        if provider_event_id and not await _claim_provider_lifecycle_event(
            db,
            booking=booking,
            provider_event_id=provider_event_id,
            event_type="byof_deposit_authorized",
            summary="BYOF deposit authorization is ready for artist review.",
            details={
                "stripe_session_id": _bounded_stripe_value(checkout_session_id, 255),
                "payment_intent": _bounded_stripe_value(payment_intent_id, 255),
                "source_event": source_event,
            },
        ):
            return
        now = datetime.now(UTC)
        booking.stripe_payment_intent_id = payment_intent_id or booking.stripe_payment_intent_id
        booking.stripe_checkout_session_id = checkout_session_id or booking.stripe_checkout_session_id
        booking.deposit_authorization_status = "authorized"
        booking.deposit_authorized_at = booking.deposit_authorized_at or now
        booking.deposit_review_deadline_at = booking.deposit_review_deadline_at or now + timedelta(hours=48)
        booking.deposit_paid = False
        booking.updated_at = now
        if not provider_event_id:
            db.add(
                BookingLifecycleEvent(
                    booking_id=booking.id,
                    artist_id=booking.artist_id,
                    event_type="byof_deposit_authorized",
                    source="stripe_webhook",
                    reference_code=booking.reference_code,
                    guest_email=booking.guest_email,
                    summary="BYOF deposit authorization is ready for artist review.",
                    details={
                        "stripe_session_id": checkout_session_id,
                        "payment_intent": payment_intent_id,
                        "source_event": source_event,
                        "review_deadline_at": booking.deposit_review_deadline_at.isoformat(),
                    },
                )
            )
        await db.commit()


async def _handle_charge_event(event_type: str, charge):
    if event_type == "charge.dispute.created":
        dispute = charge
        logger.warning(
            "Stripe dispute created: dispute=%s charge=%s amount=%s reason=%s",
            dispute.get("id"),
            dispute.get("charge"),
            dispute.get("amount"),
            dispute.get("reason"),
        )
        return

    if event_type == "charge.dispute.closed":
        dispute = charge
        logger.info(
            "Stripe dispute closed: dispute=%s charge=%s status=%s",
            dispute.get("id"),
            dispute.get("charge"),
            dispute.get("status"),
        )
        return

    payment_intent_id = charge.get("payment_intent")
    if event_type == "charge.succeeded":
        logger.info(
            "Stripe charge succeeded: charge=%s payment_intent=%s amount=%s",
            charge.get("id"),
            payment_intent_id,
            charge.get("amount"),
        )
    elif event_type == "charge.failed":
        logger.warning(
            "Stripe charge failed: charge=%s payment_intent=%s failure=%s",
            charge.get("id"),
            payment_intent_id,
            charge.get("failure_message") or charge.get("failure_code"),
        )
    elif event_type == "charge.refunded":
        await void_refunded_gift_card(str(payment_intent_id) if payment_intent_id else None)
        logger.info(
            "Stripe charge refunded: charge=%s payment_intent=%s amount_refunded=%s",
            charge.get("id"),
            payment_intent_id,
            charge.get("amount_refunded"),
        )


async def _handle_customer_event(event_type: str, customer):
    customer_id = customer.get("id")
    if not customer_id:
        logger.warning("Stripe customer event missing id: %s", event_type)
        return

    async with async_session() as db:
        user = await _find_user_for_customer(db, customer)
        if not user:
            logger.info(
                "Stripe customer event had no matching Ryozan user: event=%s customer=%s", event_type, customer_id
            )
            return

        if event_type in {"customer.created", "customer.updated"}:
            if user.stripe_customer_id != customer_id:
                user.stripe_customer_id = customer_id
            user.updated_at = datetime.now(UTC)
            await db.commit()
            logger.info("Linked Stripe customer %s to user %s", customer_id, user.id)
            return

        if event_type == "customer.deleted":
            user.stripe_customer_id = None
            user.membership_status = "inactive"
            user.updated_at = datetime.now(UTC)
            await db.commit()
            logger.info("Unlinked deleted Stripe customer %s from user %s", customer_id, user.id)


async def _handle_refund_event(event_type: str, refund):
    payment_intent_id = refund.get("payment_intent")
    refund_id = refund.get("id")
    refund_status = str(refund.get("status") or "pending")
    amount_cents = int(refund.get("amount") or 0)
    if refund_status == "succeeded":
        await void_refunded_gift_card(str(payment_intent_id) if payment_intent_id else None)
    if refund_id:
        async with async_session() as db:
            booking = None
            if payment_intent_id:
                booking_result = await db.execute(
                    select(Booking).where(Booking.stripe_payment_intent_id == payment_intent_id)
                )
                booking = booking_result.scalar_one_or_none()
            created = refund.get("created")
            occurred_at = datetime.fromtimestamp(created, tz=UTC) if created else datetime.now(UTC)
            await upsert_stripe_ledger_entry(
                db,
                external_id=str(refund_id),
                entry_type="refund",
                status=refund_status,
                amount_cents=-amount_cents if refund_status == "succeeded" else 0,
                currency=str(refund.get("currency") or "usd"),
                occurred_at=occurred_at,
                description=(
                    f"Stripe refund - {booking.reference_code}"
                    if booking and booking.reference_code
                    else "Stripe refund"
                ),
                category="stripe_refund",
                artist_id=booking.artist_id if booking else None,
                raw={
                    "refund_id": _bounded_stripe_value(refund_id, 255),
                    "payment_intent": _bounded_stripe_value(payment_intent_id, 255),
                    "booking_id": str(booking.id) if booking else None,
                    "reference_code": booking.reference_code if booking else None,
                    "requested_amount_cents": amount_cents,
                },
            )
            await db.commit()
    logger.info(
        "Stripe refund event: event=%s refund=%s payment_intent=%s status=%s amount=%s",
        event_type,
        refund.get("id"),
        payment_intent_id,
        refund.get("status"),
        refund.get("amount"),
    )


async def _record_checkout_payment(session) -> None:
    """Write every paid Checkout Session into the normalized E-con ledger."""
    metadata = session.get("metadata", {}) or {}
    payment_intent_id = session.get("payment_intent")
    session_id = session.get("id")
    external_id = payment_intent_id or session_id
    if not external_id:
        logger.warning("Paid Stripe checkout had no payment intent or session id")
        return

    booking_id = _parse_uuid(metadata.get("booking_id"))
    artist_id = _parse_uuid(metadata.get("artist_id"))
    async with async_session() as db:
        booking = None
        if booking_id:
            booking_result = await db.execute(select(Booking).where(Booking.id == booking_id))
            booking = booking_result.scalar_one_or_none()
            if booking and not artist_id:
                artist_id = booking.artist_id

        created = session.get("created")
        occurred_at = datetime.fromtimestamp(created, tz=UTC) if created else datetime.now(UTC)
        checkout_type = str(metadata.get("checkout_type") or "checkout_payment")
        reference_code = metadata.get("reference_code") or (booking.reference_code if booking else None)
        await upsert_stripe_ledger_entry(
            db,
            external_id=str(external_id),
            entry_type="payment",
            status="posted",
            amount_cents=int(session.get("amount_total") or 0),
            currency=str(session.get("currency") or "usd"),
            occurred_at=occurred_at,
            description=f"Stripe payment - {reference_code}" if reference_code else "Stripe payment",
            category=checkout_type,
            artist_id=artist_id,
            raw={
                "stripe_session_id": _bounded_stripe_value(session_id, 255),
                "payment_intent": _bounded_stripe_value(payment_intent_id, 255),
                "booking_id": str(booking_id) if booking_id else None,
                "reference_code": reference_code,
                "checkout_type": checkout_type,
                "routing_destination": metadata.get("routing_destination"),
                "routing_reason": metadata.get("routing_reason"),
                "commission_rate": metadata.get("commission_rate"),
                "artist_cut_cents": metadata.get("artist_cut_cents"),
                "shop_cut_cents": metadata.get("shop_cut_cents"),
                "payout_owed_cents": metadata.get("payout_owed_cents"),
                "shop_share_category": metadata.get("shop_share_category"),
            },
        )
        await db.commit()


async def _handle_invoice_event(event_type: str, invoice):
    customer_id = invoice.get("customer")
    if not customer_id:
        logger.info("Stripe invoice event without customer: event=%s invoice=%s", event_type, invoice.get("id"))
        return

    async with async_session() as db:
        subscription_id, subscription_metadata = _invoice_subscription_context(invoice)
        metadata_user_id = _parse_uuid(subscription_metadata.get("bttco_user_id"))
        condition = None
        if metadata_user_id:
            condition = User.id == metadata_user_id
        elif subscription_id and subscription_metadata.get("bttco_billing_kind") == "booth_rent":
            condition = User.booth_rent_subscription_id == subscription_id
        else:
            condition = User.stripe_customer_id == customer_id
        result = await db.execute(select(User).where(condition))
        user = result.scalars().first()
        if not user:
            logger.info(
                "Stripe invoice event had no matching Ryozan user: event=%s customer=%s", event_type, customer_id
            )
            return

        is_booth_rent = subscription_metadata.get("bttco_billing_kind") == "booth_rent" or bool(
            subscription_id and subscription_id == user.booth_rent_subscription_id
        )
        if is_booth_rent:
            await _handle_booth_rent_invoice(db, event_type, invoice, user, subscription_id)
            await db.commit()
            return

        if event_type == "invoice.paid":
            user.membership_status = "active"
        elif event_type == "invoice.payment_failed":
            user.membership_status = "past_due"
        user.updated_at = datetime.now(UTC)
        await db.commit()
        logger.info(
            "Handled Stripe invoice event: event=%s invoice=%s user=%s",
            event_type,
            invoice.get("id"),
            user.id,
        )


def _invoice_subscription_context(invoice: dict) -> tuple[str | None, dict]:
    """Read both legacy and current Stripe invoice subscription shapes."""
    metadata = dict(invoice.get("metadata") or {})
    subscription_id = invoice.get("subscription")
    parent = invoice.get("parent") or {}
    subscription_details = parent.get("subscription_details") or {}
    subscription_id = subscription_id or subscription_details.get("subscription")
    metadata.update(subscription_details.get("metadata") or {})

    if not subscription_id:
        lines = (invoice.get("lines") or {}).get("data") or []
        if lines:
            line_parent = lines[0].get("parent") or {}
            item_details = line_parent.get("subscription_item_details") or {}
            subscription_id = item_details.get("subscription")
    return (str(subscription_id) if subscription_id else None, metadata)


def _invoice_period_end(invoice: dict) -> datetime | None:
    timestamp = invoice.get("period_end")
    if not timestamp:
        lines = (invoice.get("lines") or {}).get("data") or []
        if lines:
            timestamp = (lines[0].get("period") or {}).get("end")
    if not timestamp:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=UTC)


async def _handle_booth_rent_checkout_event(event_type: str, session: dict) -> None:
    metadata = session.get("metadata", {}) or {}
    user_id = _parse_uuid(metadata.get("bttco_user_id"))
    if not user_id:
        logger.warning("Booth-rent Checkout event missing Ryozan user id: event=%s", event_type)
        return

    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id).with_for_update())
        artist = result.scalar_one_or_none()
        if not artist:
            logger.warning("Booth-rent Checkout event had no matching artist: event=%s", event_type)
            return

        session_id = session.get("id")
        payment_method = str(
            metadata.get("booth_rent_payment_method")
            or ("autodraft" if session.get("subscription") else artist.booth_rent_payment_method)
            or "cash"
        )
        if payment_method == "stripe":
            payment_method = "autodraft"
        if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            if session.get("customer"):
                artist.stripe_customer_id = str(session["customer"])
            if payment_method == "autodraft" and session.get("subscription"):
                artist.booth_rent_subscription_id = str(session["subscription"])
            paid = session.get("payment_status") == "paid"
            if payment_method == "one_time" and paid:
                artist.booth_rent_checkout_session_id = None
                artist.booth_rent_status = "paid"
                period_end = metadata.get("booth_rent_period_end")
                if period_end:
                    artist.booth_rent_current_period_end = datetime.fromtimestamp(int(period_end), tz=UTC)
                    artist.booth_rent_next_due_date = artist.booth_rent_current_period_end.astimezone(
                        shop_timezone()
                    ).date()
                external_id = session.get("payment_intent") or session_id
                if external_id and int(session.get("amount_total") or 0) > 0:
                    created = session.get("created")
                    occurred_at = datetime.fromtimestamp(created, tz=UTC) if created else datetime.now(UTC)
                    await upsert_stripe_ledger_entry(
                        db,
                        external_id=str(external_id),
                        entry_type="payment",
                        status="posted",
                        amount_cents=int(session.get("amount_total") or 0),
                        currency=str(session.get("currency") or "usd"),
                        occurred_at=occurred_at,
                        description=f"Booth rent - {artist.name} - {metadata.get('booth_rent_period') or 'current period'}",
                        category="contractor_contribution",
                        artist_id=artist.id,
                        raw={
                            "stripe_session_id": _bounded_stripe_value(session_id, 255),
                            "payment_intent": _bounded_stripe_value(session.get("payment_intent"), 255),
                            "artist_id": str(artist.id),
                            "contribution_type": "booth_rent",
                            "payment_method": "one_time",
                            "booth_rent_period": metadata.get("booth_rent_period"),
                        },
                    )
            else:
                artist.booth_rent_checkout_session_id = str(session_id) if session_id else None
                due_date_value = metadata.get("booth_rent_due_date")
                due_date = date.fromisoformat(due_date_value) if due_date_value else None
                if due_date:
                    artist.booth_rent_next_due_date = due_date
                if paid and payment_method == "autodraft":
                    artist.booth_rent_status = "active"
                elif due_date and due_date > datetime.now(shop_timezone()).date():
                    artist.booth_rent_status = "scheduled"
                else:
                    artist.booth_rent_status = "pending"
        elif not artist.booth_rent_checkout_session_id or artist.booth_rent_checkout_session_id == session_id:
            artist.booth_rent_checkout_session_id = None
            if artist.booth_rent_status not in {"active", "past_due"}:
                due_date_value = metadata.get("booth_rent_due_date")
                due_date = date.fromisoformat(due_date_value) if due_date_value else None
                if due_date and due_date > datetime.now(shop_timezone()).date():
                    artist.booth_rent_status = "scheduled"
                else:
                    artist.booth_rent_status = "pending" if payment_method == "autodraft" else "due"
        artist.updated_at = datetime.now(UTC)
        await db.commit()


async def _handle_booth_rent_invoice(db, event_type, invoice, artist: User, subscription_id: str | None):
    invoice_id = invoice.get("id")
    customer_id = invoice.get("customer")
    if customer_id:
        artist.stripe_customer_id = str(customer_id)
    if subscription_id:
        artist.booth_rent_subscription_id = subscription_id
    artist.booth_rent_current_period_end = _invoice_period_end(invoice)
    if artist.booth_rent_current_period_end:
        artist.booth_rent_next_due_date = artist.booth_rent_current_period_end.astimezone(shop_timezone()).date()
    artist.updated_at = datetime.now(UTC)

    if event_type == "invoice.paid":
        artist.booth_rent_status = "active"
    elif event_type == "invoice.payment_failed":
        artist.booth_rent_status = "past_due"
    elif artist.booth_rent_status not in {"active", "past_due"}:
        artist.booth_rent_status = "pending"

    if event_type not in {"invoice.paid", "invoice.payment_failed"} or not invoice_id:
        return

    created = invoice.get("created")
    occurred_at = datetime.fromtimestamp(created, tz=UTC) if created else datetime.now(UTC)
    amount_cents = int(invoice.get("amount_paid") or 0) if event_type == "invoice.paid" else 0
    await upsert_stripe_ledger_entry(
        db,
        external_id=str(invoice_id),
        entry_type="payment",
        status="posted" if event_type == "invoice.paid" else "failed",
        amount_cents=amount_cents,
        currency=str(invoice.get("currency") or "usd"),
        occurred_at=occurred_at,
        description=f"Booth rent - {artist.name}",
        category="contractor_contribution",
        artist_id=artist.id,
        raw={
            "invoice_id": _bounded_stripe_value(invoice_id, 255),
            "subscription_id": _bounded_stripe_value(subscription_id, 255),
            "artist_id": str(artist.id),
            "amount_due_cents": int(invoice.get("amount_due") or 0),
            "billing_reason": _bounded_stripe_value(invoice.get("billing_reason")),
            "contribution_type": "booth_rent",
            "payment_method": "autodraft",
        },
    )


async def _handle_connect_event(event_type: str, event_object):
    if event_type == "account.updated":
        await _handle_account_updated(event_object)
        return

    logger.info(
        "Stripe Connect event: event=%s object=%s id=%s status=%s amount=%s",
        event_type,
        event_object.get("object"),
        event_object.get("id"),
        event_object.get("status"),
        event_object.get("amount"),
    )


async def _handle_checkout_async_payment_failed(session):
    session_id = session.get("id")
    payment_intent_id = session.get("payment_intent")
    metadata = session.get("metadata", {}) or {}
    async with async_session() as db:
        await _expire_slot_hold_for_checkout(db, metadata=metadata)
        await _release_flash_design_for_unpaid_checkout(db, metadata=metadata)
        await db.commit()
    logger.warning(
        "Stripe Checkout async payment failed: session=%s payment_intent=%s",
        session_id,
        payment_intent_id,
    )


def _bounded_stripe_value(value: Any, limit: int = 120) -> str | None:
    return str(value)[:limit] if value is not None else None


async def _claim_provider_lifecycle_event(
    db,
    *,
    booking: Booking,
    provider_event_id: str | None,
    event_type: str,
    summary: str,
    details: dict[str, Any],
) -> bool:
    if provider_event_id:
        result = await db.execute(
            select(BookingLifecycleEvent.id).where(
                BookingLifecycleEvent.provider_event_id == provider_event_id,
            )
        )
        if result.scalar_one_or_none():
            return False

    db.add(
        BookingLifecycleEvent(
            booking_id=booking.id,
            artist_id=booking.artist_id,
            event_type=event_type,
            source="stripe_webhook",
            provider_event_id=provider_event_id,
            reference_code=booking.reference_code,
            guest_email=booking.guest_email,
            summary=summary,
            details=details,
        )
    )
    if provider_event_id:
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return False
    return True


async def _sync_hustl_deposit_task_with_visibility(
    db,
    *,
    booking: Booking,
    source_event: str,
    payment_intent_id: str | None,
) -> None:
    snapshot = {
        "booking_id": booking.id,
        "artist_id": booking.artist_id,
        "reference_code": booking.reference_code,
        "guest_email": booking.guest_email,
        "booking_status": booking.status,
        "deposit_paid": booking.deposit_paid,
        "deposit_amount": float(booking.deposit_amount or 0),
    }
    try:
        artist_result = await db.execute(select(User.name).where(User.id == booking.artist_id))
        artist_name = artist_result.scalar_one_or_none() or "Unassigned artist"
        client_name = booking.guest_name or "Client"
        client_email = booking.guest_email
        if booking.client_id:
            client_result = await db.execute(select(User.name, User.email).where(User.id == booking.client_id))
            client_row = client_result.first()
            if client_row:
                client_name = client_row[0] or client_name
                client_email = client_row[1] or client_email
        await _sync_hustl_deposit_followup_task(
            db=db,
            booking=booking,
            client_name=client_name,
            client_email=client_email,
            artist_name=artist_name,
            assignee_id=booking.artist_id,
        )
    except Exception as exc:
        await db.rollback()
        db.add(
            BookingLifecycleEvent(
                booking_id=snapshot["booking_id"],
                artist_id=snapshot["artist_id"],
                event_type="deposit_task_sync_failed",
                source="stripe_webhook",
                reference_code=snapshot["reference_code"],
                guest_email=snapshot["guest_email"],
                summary="Failed to sync Huddl deposit task from Stripe webhook.",
                details={
                    "source_event": _bounded_stripe_value(source_event),
                    "payment_intent": _bounded_stripe_value(payment_intent_id, 255),
                    "booking_status": snapshot["booking_status"],
                    "deposit_paid": snapshot["deposit_paid"],
                    "deposit_amount": snapshot["deposit_amount"],
                    "error_class": type(exc).__name__[:120],
                },
            )
        )
        await db.commit()
        logger.error("Deposit task sync failed for booking %s (%s)", snapshot["booking_id"], type(exc).__name__)
        raise


async def _booking_has_newer_open_quote(db, *, booking: Booking, quote: Quote | None) -> bool:
    """True when a live quote other than this session's own is waiting on the booking."""
    query = select(Quote.id).where(Quote.booking_id == booking.id, Quote.status.in_((QuoteStatus.SENT, "viewed")))
    if quote is not None:
        query = query.where(Quote.id != quote.id)
    return (await db.execute(query.limit(1))).scalar_one_or_none() is not None


async def _handle_checkout_expired(session, *, provider_event_id: str | None = None):
    session_id = session.get("id")
    metadata = session.get("metadata", {})
    quote_token = metadata.get("quote_token")
    booking_id = _parse_uuid(metadata.get("booking_id"))
    message_id = _parse_uuid(metadata.get("artifact_message_id") or metadata.get("message_id"))

    async with async_session() as db:
        quote = None
        if quote_token:
            quote_r = await db.execute(select(Quote).where(Quote.token == quote_token))
            quote = quote_r.scalar_one_or_none()
            if not booking_id and quote:
                booking_id = quote.booking_id

        booking = None
        if booking_id:
            # Lock the booking: an approval that expired this session holds the
            # same lock until its replacement quote commits, so the check below
            # sees that quote instead of racing it.
            booking_r = await db.execute(select(Booking).where(Booking.id == booking_id).with_for_update())
            booking = booking_r.scalar_one_or_none()
            if booking and not await _claim_provider_lifecycle_event(
                db,
                booking=booking,
                provider_event_id=provider_event_id,
                event_type="checkout_expired",
                summary="Stripe deposit checkout expired before payment.",
                details={
                    "stripe_session_id": _bounded_stripe_value(session_id, 255),
                    "payment_intent": _bounded_stripe_value(session.get("payment_intent"), 255),
                },
            ):
                logger.info("Ignoring duplicate Stripe provider event %s", provider_event_id)
                return

        await _expire_slot_hold_for_checkout(db, metadata=metadata)
        if quote and quote.status != QuoteStatus.ACCEPTED:
            quote.status = QuoteStatus.EXPIRED
        if booking and await _booking_has_newer_open_quote(db, booking=booking, quote=quote):
            # Staff re-approved and expired this older Checkout on purpose; the
            # booking now waits on the newer quote, so leave it alone.
            logger.info("Checkout %s expired after re-approval; booking %s kept", session_id, booking.id)
            booking = None
        if booking:
            if booking and booking.status not in {BookingStatus.CONFIRMED, BookingStatus.COMPLETED}:
                booking.status = BookingStatus.EXPIRED
                booking.updated_at = datetime.now(UTC)
                await _release_flash_design_for_unpaid_checkout(
                    db,
                    metadata=metadata,
                    booking_id=booking.id,
                )
            await _sync_hustl_deposit_task_with_visibility(
                db,
                booking=booking,
                source_event="checkout.session.expired",
                payment_intent_id=session.get("payment_intent"),
            )

        if message_id:
            msg_r = await db.execute(select(Message).where(Message.id == message_id))
            message = msg_r.scalar_one_or_none()
            if message:
                state = dict(message.artifact_state or {})
                state.update({"status": "expired", "expired_at": datetime.now(UTC).isoformat()})
                message.artifact_state = state
                message.updated_at = datetime.now(UTC)

        await db.commit()

    logger.info(
        "Stripe Checkout expired: session=%s quote_token_present=%s booking_id=%s message_id=%s",
        session_id,
        bool(quote_token),
        booking_id,
        message_id,
    )


async def _handle_payment_intent_succeeded(payment_intent, *, provider_event_id: str | None = None):
    payment_intent_id = payment_intent.get("id")
    if not payment_intent_id:
        logger.warning("Stripe payment_intent.succeeded missing id")
        return

    async with async_session() as db:
        booking = await _find_booking_by_payment_intent(db, payment_intent_id)
        if booking:
            if not await _claim_provider_lifecycle_event(
                db,
                booking=booking,
                provider_event_id=provider_event_id,
                event_type="payment_intent_succeeded",
                summary="Stripe payment intent succeeded for booking deposit.",
                details={
                    "payment_intent": _bounded_stripe_value(payment_intent_id, 255),
                    "status": _bounded_stripe_value(payment_intent.get("status")),
                },
            ):
                logger.info("Ignoring duplicate Stripe provider event %s", provider_event_id)
                return
            booking.deposit_paid = True
            if booking.is_bring_your_own_flash:
                booking.deposit_authorization_status = "captured"
                booking.deposit_review_deadline_at = None
            await reward_user_by_id(
                db,
                booking.client_id,
                "booking.deposit_paid",
                str(booking.id),
            )
            booking.updated_at = datetime.now(UTC)
            await _sync_hustl_deposit_task_with_visibility(
                db,
                booking=booking,
                source_event="payment_intent.succeeded",
                payment_intent_id=payment_intent_id,
            )
            await db.commit()
            logger.info("Marked booking %s deposit paid from payment intent %s", booking.id, payment_intent_id)
            return

    logger.info("Stripe payment intent succeeded with no matching booking: %s", payment_intent_id)


async def _handle_payment_intent_terminal_failure(
    event_type: str,
    payment_intent,
    *,
    provider_event_id: str | None = None,
):
    payment_intent_id = payment_intent.get("id")
    logger.warning(
        "Stripe payment intent terminal failure: event=%s payment_intent=%s status=%s",
        event_type,
        payment_intent_id,
        payment_intent.get("status"),
    )
    if not payment_intent_id:
        return

    async with async_session() as db:
        booking = await _find_booking_by_payment_intent(db, payment_intent_id)
        if not booking:
            return
        last_error = payment_intent.get("last_payment_error") or {}
        if not await _claim_provider_lifecycle_event(
            db,
            booking=booking,
            provider_event_id=provider_event_id,
            event_type="payment_intent_terminal_failure",
            summary=f"Stripe payment intent failed ({event_type}).",
            details={
                "event_type": _bounded_stripe_value(event_type),
                "payment_intent": _bounded_stripe_value(payment_intent_id, 255),
                "status": _bounded_stripe_value(payment_intent.get("status")),
                "error_code": _bounded_stripe_value(last_error.get("code")),
                "decline_code": _bounded_stripe_value(last_error.get("decline_code")),
            },
        ):
            logger.info("Ignoring duplicate Stripe provider event %s", provider_event_id)
            return
        if booking.is_bring_your_own_flash and booking.deposit_authorization_status in {
            "authorized",
            "pending_checkout",
        }:
            booking.deposit_authorization_status = "released" if event_type == "payment_intent.canceled" else "failed"
            booking.deposit_review_deadline_at = None
        booking.updated_at = datetime.now(UTC)
        await _sync_hustl_deposit_task_with_visibility(
            db,
            booking=booking,
            source_event=event_type,
            payment_intent_id=payment_intent_id,
        )
        await db.commit()


async def _find_booking_by_payment_intent(db, payment_intent_id: str) -> Booking | None:
    result = await db.execute(select(Booking).where(Booking.stripe_payment_intent_id == payment_intent_id))
    booking = result.scalar_one_or_none()
    if booking:
        return booking

    quote_r = await db.execute(select(Quote).where(Quote.stripe_payment_intent_id == payment_intent_id))
    quote = quote_r.scalar_one_or_none()
    if not quote:
        return None

    booking_r = await db.execute(select(Booking).where(Booking.id == quote.booking_id))
    return booking_r.scalar_one_or_none()


async def _find_user_for_customer(db, customer) -> User | None:
    customer_id = customer.get("id")
    email = customer.get("email")
    metadata = customer.get("metadata", {}) or {}
    metadata_user_id = _parse_uuid(metadata.get("bttco_user_id") or metadata.get("user_id"))

    conditions: list[Any] = [User.stripe_customer_id == customer_id]
    if metadata_user_id:
        conditions.append(User.id == metadata_user_id)
    if email:
        conditions.append(User.email == email)

    result = await db.execute(select(User).where(or_(*conditions)))
    return result.scalars().first()


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _booking_already_processed_for_session(booking: Booking, session) -> bool:
    session_id = session.get("id")
    payment_intent_id = session.get("payment_intent")
    if booking.status not in {BookingStatus.CONFIRMED, BookingStatus.COMPLETED}:
        return False
    if not booking.deposit_paid:
        return False
    return bool(
        (session_id and booking.stripe_checkout_session_id == session_id)
        or (payment_intent_id and booking.stripe_payment_intent_id == payment_intent_id)
    )


async def _consume_slot_hold_for_checkout(db, *, metadata: dict[str, Any], booking: Booking) -> BookingSlotHold | None:
    hold_token = metadata.get("slot_hold_token")
    if not hold_token:
        return None

    result = await db.execute(select(BookingSlotHold).where(BookingSlotHold.hold_token == hold_token))
    hold = result.scalar_one_or_none()
    if not hold:
        logger.warning("Checkout completed with missing slot hold: token=%s booking=%s", hold_token, booking.id)
        return None

    if hold.booking_id and hold.booking_id != booking.id:
        logger.warning(
            "Checkout slot hold booking mismatch: token=%s hold_booking=%s checkout_booking=%s",
            hold_token,
            hold.booking_id,
            booking.id,
        )

    if hold.status == "consumed" and hold.booking_id == booking.id:
        return hold

    hold.status = "consumed"
    hold.booking_id = booking.id
    hold.updated_at = datetime.now(UTC)
    db.add(
        BookingLifecycleEvent(
            booking_id=booking.id,
            artist_id=booking.artist_id,
            event_type="hold_consumed",
            source="stripe_webhook",
            reference_code=booking.reference_code,
            hold_token=hold.hold_token,
            guest_email=booking.guest_email,
            summary="Stripe checkout consumed slot hold.",
            details={
                "scheduled_date": hold.scheduled_date.isoformat(),
                "scheduled_time": hold.scheduled_time,
                "duration_minutes": hold.duration_minutes,
                "stripe_session_id": metadata.get("stripe_session_id") or metadata.get("session_id"),
            },
        )
    )
    return hold


async def _expire_slot_hold_for_checkout(db, *, metadata: dict[str, Any]) -> BookingSlotHold | None:
    hold_token = metadata.get("slot_hold_token")
    if not hold_token:
        return None

    result = await db.execute(select(BookingSlotHold).where(BookingSlotHold.hold_token == hold_token))
    hold = result.scalar_one_or_none()
    if not hold:
        logger.info("Checkout terminal event referenced missing slot hold: token=%s", hold_token)
        return None

    if hold.status == "active":
        hold.status = "expired"
        hold.updated_at = datetime.now(UTC)
    return hold


async def _release_flash_design_for_unpaid_checkout(
    db,
    *,
    metadata: dict[str, Any],
    booking_id: uuid.UUID | None = None,
) -> FlashDesign | None:
    """Release only the flash reservation linked to an unpaid terminal checkout."""
    design_id = _parse_uuid(metadata.get("design_id"))
    is_flash_checkout = bool(
        design_id or metadata.get("checkout_type") == "flash_deposit" or metadata.get("flash_booking") == "true"
    )
    if not is_flash_checkout:
        return None
    if not design_id and not booking_id:
        return None
    query = select(FlashDesign)
    query = query.where(FlashDesign.id == design_id) if design_id else query.where(FlashDesign.booking_id == booking_id)
    result = await db.execute(query)
    design = result.scalar_one_or_none()
    if not design:
        return None
    if booking_id and design.booking_id not in {None, booking_id}:
        return None
    design.status = "published"
    design.claimed_by = None
    design.claimed_at = None
    design.booking_id = None
    design.updated_at = datetime.now(UTC)
    return design


async def _handle_checkout_completed(session, *, provider_event_id: str | None = None):
    """
    Deposit paid — confirm booking, schedule session, notify parties.
    """
    metadata = session.get("metadata", {})
    if metadata.get("checkout_type") == "public_booking_deposit":
        await _handle_public_booking_checkout_completed(session, provider_event_id=provider_event_id)
        return
    # Flash sessions carry flash_booking (legacy, still set by in-flight
    # sessions) plus checkout_type=flash_deposit going forward.
    if metadata.get("checkout_type") == "flash_deposit" or metadata.get("flash_booking") == "true":
        await _handle_flash_checkout_completed(session, provider_event_id=provider_event_id)
        return
    quote_token = metadata.get("quote_token")
    if not quote_token:
        if metadata.get("artifact_type") == "quote_proposal" or metadata.get("artifact_message_id"):
            await _handle_quote_proposal_checkout_completed(session, provider_event_id=provider_event_id)
            return
        logger.warning("Checkout completed without quote_token in metadata")
        return

    async with async_session() as db:
        # Find quote
        result = await db.execute(select(Quote).where(Quote.token == quote_token))
        quote = result.scalar_one_or_none()
        if not quote:
            logger.error("Quote not found for token: %s", quote_token)
            return

        # Update quote
        quote.status = QuoteStatus.ACCEPTED
        quote.accepted_at = datetime.now(UTC)
        quote.stripe_payment_intent_id = session.get("payment_intent")
        amount_paid = (
            float(session.get("amount_total")) / 100
            if session.get("amount_total") is not None
            else float(quote.deposit_amount)
        )

        # Update booking
        booking_r = await db.execute(select(Booking).where(Booking.id == quote.booking_id))
        booking = booking_r.scalar_one()
        already_processed = _booking_already_processed_for_session(booking, session)
        if provider_event_id and not await _claim_provider_lifecycle_event(
            db,
            booking=booking,
            provider_event_id=provider_event_id,
            event_type="checkout_duplicate" if already_processed else "checkout_completed",
            summary=(
                "Duplicate Stripe checkout completion ignored."
                if already_processed
                else "Deposit checkout completed and booking confirmed."
            ),
            details={
                "stripe_session_id": _bounded_stripe_value(session.get("id"), 255),
                "payment_intent": _bounded_stripe_value(session.get("payment_intent"), 255),
                "quote_token": _bounded_stripe_value(quote_token, 255),
                "deposit_amount": amount_paid,
            },
        ):
            return
        if already_processed:
            await _consume_slot_hold_for_checkout(db, metadata=metadata, booking=booking)
            await db.commit()
            logger.info(
                "Ignoring duplicate checkout completion for booking %s session %s", booking.id, session.get("id")
            )
            return

        booking.status = BookingStatus.CONFIRMED
        booking.deposit_paid = True
        await reward_user_by_id(
            db,
            booking.client_id,
            "booking.deposit_paid",
            str(booking.id),
        )
        booking.deposit_amount = amount_paid
        booking.stripe_checkout_session_id = session.get("id")
        booking.stripe_payment_intent_id = session.get("payment_intent")

        # Set schedule from selected slot
        if quote.selected_date:
            booking.scheduled_date = (
                quote.selected_date.date() if hasattr(quote.selected_date, "date") else quote.selected_date
            )
        if quote.selected_time:
            booking.scheduled_time = quote.selected_time
        if quote.selected_duration:
            booking.duration_minutes = quote.selected_duration

        await _consume_slot_hold_for_checkout(db, metadata=metadata, booking=booking)
        booking.updated_at = datetime.now(UTC)
        if not provider_event_id:
            db.add(
                BookingLifecycleEvent(
                    booking_id=booking.id,
                    artist_id=booking.artist_id,
                    event_type="checkout_completed",
                    source="stripe_webhook",
                    reference_code=booking.reference_code,
                    guest_email=booking.guest_email,
                    summary="Deposit checkout completed and booking confirmed.",
                    details={
                        "stripe_session_id": session.get("id"),
                        "payment_intent": session.get("payment_intent"),
                        "quote_token": quote_token,
                        "deposit_amount": amount_paid,
                        "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
                        "scheduled_time": booking.scheduled_time,
                    },
                )
            )

        # Close the "Collect deposit" follow-up the approval opened, in the same
        # transaction as the confirmation, like the other checkout branches. The
        # payment-intent event can arrive before the IDs above are stored.
        await _sync_hustl_deposit_task_with_visibility(
            db,
            booking=booking,
            source_event="quote_checkout_completed",
            payment_intent_id=session.get("payment_intent"),
        )
        await db.commit()

        # Auto-sync to Google Calendar if the artist has it connected
        try:
            artist_r = await db.execute(select(User).where(User.id == booking.artist_id))
            artist_for_cal = artist_r.scalar_one_or_none()
            if artist_for_cal and artist_calendar_connected(artist_for_cal) and booking.scheduled_date:
                client_name_cal = booking.guest_name or "Client"
                client_email_cal = booking.guest_email
                if booking.client_id:
                    client_r_cal = await db.execute(select(User.name, User.email).where(User.id == booking.client_id))
                    client_row_cal = client_r_cal.first()
                    if client_row_cal:
                        client_name_cal = client_row_cal[0] or client_name_cal
                        client_email_cal = client_row_cal[1] or client_email_cal
                cal_event = await create_booking_event(
                    {
                        "client_name": client_name_cal,
                        "client_email": client_email_cal,
                        "description": booking.description or "",
                        "placement": booking.placement,
                        "reference_code": booking.reference_code,
                        "scheduled_date": booking.scheduled_date,
                        "scheduled_time": booking.scheduled_time or "12:00",
                        "duration_minutes": booking.duration_minutes or 120,
                    },
                    calendar_id=artist_calendar_id(artist_for_cal),
                    refresh_token=artist_for_cal.google_refresh_token,
                )
                booking.google_calendar_event_id = cal_event["event_id"]
                await db.commit()
                logger.info("Auto-synced booking %s to Google Calendar: %s", booking.id, cal_event["event_id"])
        except Exception as cal_err:
            logger.error("Auto-calendar-sync failed for booking %s: %s", booking.id, cal_err)

        # Send deposit confirmation email to client
        try:
            # Get artist name
            artist_r = await db.execute(select(User).where(User.id == booking.artist_id))
            artist = artist_r.scalar_one_or_none()
            artist_name = artist.name if artist else "your artist"

            client_email = booking.guest_email
            client_name = booking.guest_name or "there"

            # Respect notification preferences if booking is linked to an account
            if booking.client_id and not await should_notify(db, booking.client_id, "email", "bookings"):
                client_email = None  # skip client email

            if client_email:
                await _send_deposit_paid_delivery(
                    db,
                    booking,
                    client_email=client_email,
                    client_name=client_name,
                    artist_name=artist_name,
                    deposit_amount=amount_paid,
                )
        except Exception as e:
            logger.error("Failed to send deposit confirmation email: %s", e)

        # Send deposit notification to artist + shop inbox
        try:
            notification = await _render_deposit_artist_notification(
                booking,
                client_name=client_name,
                client_email=client_email or "",
                artist_name=artist_name,
                deposit_amount=float(amount_paid or 0),
            )
            notification_html = notification.html_body
            subject = notification.subject

            # Send to artist
            if artist and artist.email:
                await send_email(
                    to=artist.email,
                    subject=subject,
                    html_body=notification_html,
                    from_alias=FROM_ADMIN,
                )

            # Send to shop inbox
            if get_settings().SHOP_EMAIL:
                await send_email(
                    to=get_settings().SHOP_EMAIL,
                    subject=subject,
                    html_body=notification_html,
                    from_alias=FROM_ADMIN,
                )
        except Exception as e:
            logger.error("Failed to send deposit artist notification: %s", e)

        logger.info(
            "Booking %s confirmed via deposit. Session: %s, Amount: $%s",
            booking.id,
            session.get("id"),
            amount_paid,
        )


async def _handle_quote_proposal_checkout_completed(session, *, provider_event_id: str | None = None):
    """
    Deposit paid from a Courier quote_proposal card.
    """
    metadata = session.get("metadata", {})
    booking_id = _parse_uuid(metadata.get("booking_id"))
    message_id = metadata.get("artifact_message_id") or metadata.get("message_id")
    if not booking_id:
        logger.warning("Quote proposal checkout completed without booking_id in metadata")
        return

    async with async_session() as db:
        booking_r = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = booking_r.scalar_one_or_none()
        if not booking:
            logger.error("Booking not found for quote proposal checkout: %s", booking_id)
            return
        deposit_amount = None
        if session.get("amount_total") is not None:
            deposit_amount = float(session.get("amount_total")) / 100
        already_processed = _booking_already_processed_for_session(booking, session)
        if provider_event_id and not await _claim_provider_lifecycle_event(
            db,
            booking=booking,
            provider_event_id=provider_event_id,
            event_type="checkout_duplicate" if already_processed else "quote_checkout_completed",
            summary=(
                "Duplicate quote proposal checkout completion ignored."
                if already_processed
                else "Courier quote proposal checkout completed and booking confirmed."
            ),
            details={
                "stripe_session_id": _bounded_stripe_value(session.get("id"), 255),
                "payment_intent": _bounded_stripe_value(session.get("payment_intent"), 255),
                "message_id": _bounded_stripe_value(message_id, 255),
                "deposit_amount": deposit_amount,
            },
        ):
            return
        if already_processed:
            await _consume_slot_hold_for_checkout(db, metadata=metadata, booking=booking)
            await db.commit()
            logger.info(
                "Ignoring duplicate quote proposal checkout completion for booking %s session %s",
                booking.id,
                session.get("id"),
            )
            return

        booking.status = BookingStatus.CONFIRMED
        booking.deposit_paid = True
        await reward_user_by_id(
            db,
            booking.client_id,
            "booking.deposit_paid",
            str(booking.id),
        )
        if deposit_amount is not None:
            booking.deposit_amount = deposit_amount
        booking.stripe_checkout_session_id = session.get("id")
        booking.stripe_payment_intent_id = session.get("payment_intent")
        booking.updated_at = datetime.now(UTC)
        await _consume_slot_hold_for_checkout(db, metadata=metadata, booking=booking)
        if not provider_event_id:
            db.add(
                BookingLifecycleEvent(
                    booking_id=booking.id,
                    artist_id=booking.artist_id,
                    event_type="quote_checkout_completed",
                    source="stripe_webhook",
                    reference_code=booking.reference_code,
                    guest_email=booking.guest_email,
                    summary="Courier quote proposal checkout completed and booking confirmed.",
                    details={
                        "stripe_session_id": session.get("id"),
                        "payment_intent": session.get("payment_intent"),
                        "message_id": str(message_id) if message_id else None,
                        "deposit_amount": deposit_amount,
                    },
                )
            )

        quote_message = None
        if message_id:
            msg_r = await db.execute(select(Message).where(Message.id == message_id))
            quote_message = msg_r.scalar_one_or_none()
            if quote_message:
                state = dict(quote_message.artifact_state or {})
                state.update(
                    {
                        "status": "confirmed",
                        "confirmed_at": datetime.now(UTC).isoformat(),
                        "stripe_checkout_session_id": session.get("id"),
                    }
                )
                quote_message.artifact_state = state
                quote_message.updated_at = datetime.now(UTC)

        await db.commit()

        artist = None
        artist_name = "your artist"
        client_email = booking.guest_email
        client_name = booking.guest_name or "there"

        try:
            artist_r = await db.execute(select(User).where(User.id == booking.artist_id))
            artist = artist_r.scalar_one_or_none()
            artist_name = artist.name if artist else "your artist"

            if booking.client_id:
                client_r = await db.execute(select(User.email, User.name).where(User.id == booking.client_id))
                client_row = client_r.first()
                if client_row:
                    client_email, client_name = client_row
                if not await should_notify(db, booking.client_id, "email", "bookings"):
                    client_email = None

            if client_email:
                await _send_deposit_paid_delivery(
                    db,
                    booking,
                    client_email=client_email,
                    client_name=client_name,
                    artist_name=artist_name,
                    deposit_amount=float(deposit_amount or booking.deposit_amount or 0),
                )
        except Exception as e:
            logger.error("Failed to send quote proposal deposit confirmation: %s", e)

        try:
            notification = await _render_deposit_artist_notification(
                booking,
                client_name=client_name,
                client_email=client_email or "",
                artist_name=artist_name,
                deposit_amount=float(deposit_amount or booking.deposit_amount or 0),
            )
            notification_html = notification.html_body
            subject = notification.subject
            if artist and artist.email:
                await send_email(to=artist.email, subject=subject, html_body=notification_html, from_alias=FROM_ADMIN)
            if get_settings().SHOP_EMAIL:
                await send_email(
                    to=get_settings().SHOP_EMAIL,
                    subject=subject,
                    html_body=notification_html,
                    from_alias=FROM_ADMIN,
                )
        except Exception as e:
            logger.error("Failed to send quote proposal artist notification: %s", e)

        logger.info(
            "Booking %s confirmed via quote_proposal deposit. Session: %s",
            booking.id,
            session.get("id"),
        )


async def _handle_public_booking_checkout_completed(session, *, provider_event_id: str | None = None):
    """
    Deposit paid from the native public booking flow.
    """
    metadata = session.get("metadata", {}) or {}
    booking_id = _parse_uuid(metadata.get("booking_id"))
    if not booking_id:
        logger.warning("Public booking checkout completed without booking_id")
        return

    async with async_session() as db:
        booking_r = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = booking_r.scalar_one_or_none()
        if not booking:
            logger.error("Booking not found for public booking checkout: %s", booking_id)
            return

        deposit_amount = None
        if session.get("amount_total") is not None:
            deposit_amount = float(session.get("amount_total")) / 100
        already_processed = _booking_already_processed_for_session(booking, session)
        if provider_event_id and not await _claim_provider_lifecycle_event(
            db,
            booking=booking,
            provider_event_id=provider_event_id,
            event_type="checkout_duplicate" if already_processed else "checkout_completed",
            summary=(
                "Duplicate public booking checkout completion ignored."
                if already_processed
                else "Public booking deposit checkout completed and booking confirmed."
            ),
            details={
                "stripe_session_id": _bounded_stripe_value(session.get("id"), 255),
                "payment_intent": _bounded_stripe_value(session.get("payment_intent"), 255),
                "deposit_amount": deposit_amount,
            },
        ):
            return
        if already_processed:
            await _consume_slot_hold_for_checkout(db, metadata=metadata, booking=booking)
            await db.commit()
            return

        booking.status = BookingStatus.CONFIRMED
        booking.deposit_paid = True
        await reward_user_by_id(
            db,
            booking.client_id,
            "booking.deposit_paid",
            str(booking.id),
        )
        if deposit_amount is not None:
            booking.deposit_amount = deposit_amount
        booking.stripe_checkout_session_id = session.get("id")
        booking.stripe_payment_intent_id = session.get("payment_intent")
        booking.updated_at = datetime.now(UTC)
        await _consume_slot_hold_for_checkout(db, metadata=metadata, booking=booking)
        if not provider_event_id:
            db.add(
                BookingLifecycleEvent(
                    booking_id=booking.id,
                    artist_id=booking.artist_id,
                    event_type="checkout_completed",
                    source="stripe_webhook",
                    reference_code=booking.reference_code,
                    guest_email=booking.guest_email,
                    summary="Public booking deposit checkout completed and booking confirmed.",
                    details={
                        "stripe_session_id": session.get("id"),
                        "payment_intent": session.get("payment_intent"),
                        "deposit_amount": deposit_amount,
                        "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
                        "scheduled_time": booking.scheduled_time,
                    },
                )
            )
        await db.commit()

        artist = None
        artist_name = "your artist"
        client_email = booking.guest_email
        client_name = booking.guest_name or "there"
        try:
            artist_r = await db.execute(select(User).where(User.id == booking.artist_id))
            artist = artist_r.scalar_one_or_none()
            artist_name = artist.name if artist else artist_name
            if booking.client_id:
                client_r = await db.execute(select(User.email, User.name).where(User.id == booking.client_id))
                client_row = client_r.first()
                if client_row:
                    client_email, client_name = client_row
                if not await should_notify(db, booking.client_id, "email", "bookings"):
                    client_email = None

            if artist and artist_calendar_connected(artist) and booking.scheduled_date:
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
                await db.commit()

            if client_email:
                await _send_deposit_paid_delivery(
                    db,
                    booking,
                    client_email=client_email,
                    client_name=client_name,
                    artist_name=artist_name,
                    deposit_amount=float(deposit_amount or booking.deposit_amount or 0),
                )
        except Exception as exc:
            logger.error("Failed public booking checkout follow-up for booking %s: %s", booking.id, exc)

        try:
            notification = await _render_deposit_artist_notification(
                booking,
                client_name=client_name,
                client_email=client_email or "",
                artist_name=artist_name,
                deposit_amount=float(deposit_amount or booking.deposit_amount or 0),
            )
            notification_html = notification.html_body
            subject = notification.subject
            if artist and artist.email:
                await send_email(to=artist.email, subject=subject, html_body=notification_html, from_alias=FROM_ADMIN)
            if get_settings().SHOP_EMAIL:
                await send_email(
                    to=get_settings().SHOP_EMAIL,
                    subject=subject,
                    html_body=notification_html,
                    from_alias=FROM_ADMIN,
                )
        except Exception as exc:
            logger.error("Failed public booking checkout artist notification: %s", exc)

        logger.info("Booking %s confirmed via public booking checkout", booking.id)


async def _handle_flash_checkout_completed(session, *, provider_event_id: str | None = None):
    """
    Flash design deposit (or full prepay) paid — confirm the booking,
    sync the calendar, and notify the client, artist, and shop.

    The design itself was already claimed and linked to the booking when
    /flash/book created the checkout session, and the FlashDesign status
    vocabulary (draft | published | claimed | archived) has no separate
    post-payment state, so the design stays "claimed" here.
    """
    metadata = session.get("metadata", {}) or {}
    booking_id = _parse_uuid(metadata.get("booking_id"))
    if not booking_id:
        logger.warning("Flash checkout completed without booking_id")
        return

    async with async_session() as db:
        booking_r = await db.execute(select(Booking).where(Booking.id == booking_id))
        booking = booking_r.scalar_one_or_none()
        if not booking:
            logger.error("Booking not found for flash checkout: %s", booking_id)
            return

        already_processed = _booking_already_processed_for_session(booking, session)
        if not await _claim_provider_lifecycle_event(
            db,
            booking=booking,
            provider_event_id=provider_event_id,
            event_type="checkout_duplicate" if already_processed else "checkout_completed",
            summary=(
                "Duplicate flash checkout completion ignored."
                if already_processed
                else "Flash deposit checkout completed and booking confirmed."
            ),
            details={
                "stripe_session_id": _bounded_stripe_value(session.get("id"), 255),
                "payment_intent": _bounded_stripe_value(session.get("payment_intent"), 255),
                "design_id": _bounded_stripe_value(metadata.get("design_id"), 255),
                "is_deposit": metadata.get("is_deposit") != "false",
                "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
                "scheduled_time": booking.scheduled_time,
            },
        ):
            return
        if already_processed:
            await db.commit()
            logger.info(
                "Ignoring duplicate flash checkout completion for booking %s session %s", booking.id, session.get("id")
            )
            return

        amount_paid = None
        if session.get("amount_total") is not None:
            amount_paid = float(session.get("amount_total")) / 100
        is_deposit = metadata.get("is_deposit") != "false"

        design = None
        design_id = _parse_uuid(metadata.get("design_id"))
        if design_id:
            design_r = await db.execute(select(FlashDesign).where(FlashDesign.id == design_id))
            design = design_r.scalar_one_or_none()
            if design:
                design.status = "claimed"
                design.claimed_by = booking.client_id
                design.claimed_at = design.claimed_at or datetime.now(UTC)
                design.booking_id = booking.id
                design.updated_at = datetime.now(UTC)

        booking.status = BookingStatus.CONFIRMED
        booking.deposit_paid = True
        await reward_user_by_id(
            db,
            booking.client_id,
            "booking.deposit_paid",
            str(booking.id),
        )
        # Only a deposit payment overwrites deposit_amount; a full prepay
        # keeps the artist's configured flash deposit on record.
        if is_deposit and amount_paid is not None:
            booking.deposit_amount = amount_paid
        booking.stripe_checkout_session_id = session.get("id")
        booking.stripe_payment_intent_id = session.get("payment_intent")
        booking.updated_at = datetime.now(UTC)
        if booking.client_id and design:
            client_r = await db.execute(select(User).where(User.id == booking.client_id))
            client = client_r.scalar_one_or_none()
            if client:
                reward_amount = max(0, int(metadata.get("bttcoin_reward") or 0))
                await grant_reward(
                    db,
                    client,
                    event_type="flash.claimed",
                    source_id=str(design.id),
                    xp=0,
                    coin=reward_amount,
                    description=f"Paid for flash: {design.name or 'Flash design'}",
                    metadata={"booking_id": str(booking.id)},
                )
                await check_and_complete_quest(db, client.id, "first-booking")
        db.add(
            NotificationModel(
                user_id=booking.artist_id,
                title="Flash Payment Received",
                body=f"{booking.guest_name or 'A client'} paid for {booking.description or 'a flash design'}.",
                notification_type="booking",
                action_url="/dashboard/huddle",
            )
        )
        await db.commit()

        artist = None
        artist_name = "your artist"
        client_email = booking.guest_email
        client_name = booking.guest_name or "there"
        try:
            artist_r = await db.execute(select(User).where(User.id == booking.artist_id))
            artist = artist_r.scalar_one_or_none()
            artist_name = artist.name if artist else artist_name
            if booking.client_id:
                client_r = await db.execute(select(User.email, User.name).where(User.id == booking.client_id))
                client_row = client_r.first()
                if client_row:
                    client_email, client_name = client_row
                if not await should_notify(db, booking.client_id, "email", "bookings"):
                    client_email = None

            if artist and artist_calendar_connected(artist) and booking.scheduled_date:
                cal_event = await create_booking_event(
                    {
                        "client_name": client_name,
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
                await db.commit()

            if client_email:
                await _send_deposit_paid_delivery(
                    db,
                    booking,
                    client_email=client_email,
                    client_name=client_name,
                    artist_name=artist_name,
                    deposit_amount=float(amount_paid or booking.deposit_amount or 0),
                )
        except Exception as exc:
            logger.error("Failed flash checkout follow-up for booking %s: %s", booking.id, exc)

        try:
            notification = await _render_deposit_artist_notification(
                booking,
                client_name=client_name,
                client_email=client_email or "",
                artist_name=artist_name,
                deposit_amount=float(amount_paid or booking.deposit_amount or 0),
            )
            notification_html = notification.html_body
            subject = notification.subject
            if artist and artist.email:
                await send_email(to=artist.email, subject=subject, html_body=notification_html, from_alias=FROM_ADMIN)
            if get_settings().SHOP_EMAIL:
                await send_email(
                    to=get_settings().SHOP_EMAIL,
                    subject=subject,
                    html_body=notification_html,
                    from_alias=FROM_ADMIN,
                )
        except Exception as exc:
            logger.error("Failed flash checkout artist notification: %s", exc)

        logger.info("Booking %s confirmed via flash checkout", booking.id)


async def _record_pos_transaction(session, metadata: dict) -> None:
    """Write a completed POS card payment into the POS ledger.

    The browser used to own this write. POSModal polled /pos/status every three
    seconds and called /pos/record only when it personally observed
    payment_status=paid, fire-and-forget with the error swallowed. That write
    died with the modal: a back button at the QR step, a closed tab, a navigation
    or a sleeping tablet lost the row permanently and silently, while Stripe had
    the money. ryozan_pos_transactions held zero rows as a result, so
    /pos/summary and /pos/transactions -- the Hustle E-con finance surfaces --
    reported no POS revenue at all.

    Stripe is the only party that reliably knows the payment settled, so the
    record belongs on this side of the webhook. Every field comes from the
    session metadata that _create_pos_checkout already writes.

    Idempotent on stripe_session_id, because Stripe retries webhooks.
    """
    session_id = session.get("id")
    if not session_id:
        logger.warning("POS completion carried no session id; not recorded")
        return

    artist_id = _parse_uuid(metadata.get("artist_id"))
    amount_cents = int(session.get("amount_total") or 0)
    if not artist_id or amount_cents <= 0:
        logger.warning(
            "POS completion not recorded: session=%s artist_id=%r amount_cents=%s",
            session_id,
            metadata.get("artist_id"),
            amount_cents,
        )
        return

    # The configured service price, before the customer-facing card adjustment.
    # resolve_charge_routing puts it on the session via apply_customer_receipt.
    raw_base = metadata.get("commission_base_cents")
    try:
        commission_base_cents = int(raw_base) if raw_base not in (None, "") else amount_cents
    except (TypeError, ValueError):
        commission_base_cents = amount_cents
    # Never let a bad metadata value invent a negative adjustment.
    commission_base_cents = max(0, min(commission_base_cents, amount_cents))

    booking_id = _parse_uuid(metadata.get("booking_id"))
    client_name = metadata.get("client_name") or "Walk-in"
    description = metadata.get("description") or None

    async with async_session() as db:
        existing = await db.execute(
            select(POSTransaction.id).where(POSTransaction.stripe_session_id == str(session_id))
        )
        if existing.scalar_one_or_none():
            logger.info("POS transaction already recorded for session %s", session_id)
            return

        db.add(
            POSTransaction(
                artist_id=artist_id,
                booking_id=booking_id,
                amount_cents=amount_cents,
                commission_base_cents=commission_base_cents,
                price_adjustment_cents=amount_cents - commission_base_cents,
                payment_method="card",
                processor="stripe",
                client_name=client_name,
                description=description,
                stripe_session_id=str(session_id),
                status="completed",
                # No acting staff session inside a webhook. The artist who
                # raised the charge is the closest true actor.
                created_by=artist_id,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            # ux_ryozan_pos_transactions_stripe_session_id fired: a concurrent
            # Stripe retry won the race after our existence check above. The row
            # exists, which is the outcome we wanted.
            await db.rollback()
            logger.info("POS transaction already recorded for session %s (unique index)", session_id)
            return

    logger.info(
        "POS transaction recorded from webhook: $%.2f artist=%s session=%s",
        amount_cents / 100,
        artist_id,
        session_id,
    )


async def _handle_pos_completed(session):
    """
    POS payment completed — record it, update the booking if linked, receipt the client.
    """
    metadata = session.get("metadata", {})
    await _record_pos_transaction(session, metadata)
    booking_id = _parse_uuid(metadata.get("booking_id"))
    artist_name = metadata.get("artist_name", "Unknown")
    client_name = metadata.get("client_name", "Walk-in")
    amount = session.get("amount_total", 0)
    customer_details = session.get("customer_details") or {}
    client_email = customer_details.get("email") or session.get("customer_email")

    if booking_id:
        async with async_session() as db:
            result = await db.execute(select(Booking).where(Booking.id == booking_id))
            booking = result.scalar_one_or_none()
            if booking:
                if booking.status == BookingStatus.COMPLETED and booking.stripe_payment_intent_id == session.get(
                    "payment_intent"
                ):
                    logger.info("Ignoring duplicate POS completion for booking %s", booking_id)
                    return
                booking.status = BookingStatus.COMPLETED
                booking.stripe_payment_intent_id = session.get("payment_intent")
                await reward_user_by_id(
                    db,
                    booking.client_id,
                    "appointment.completed",
                    str(booking.id),
                )
                await db.commit()
                logger.info("POS payment completed for booking %s", booking_id)

    if client_email:
        amount_display = f"${amount / 100:,.2f}"
        receipt_number = str(session.get("id") or "Ryozan")[-12:].upper()
        paid_at = datetime.now(shop_timezone())
        rendered = await render_transactional_email(
            "transaction_receipt",
            {
                "client_name": client_name,
                "receipt_number": receipt_number,
                "total_amount": amount_display,
                "paid_at": f"{paid_at:%B} {paid_at.day}, {paid_at:%Y} at {paid_at:%I:%M %p}".replace(" at 0", " at "),
                "payment_method": "Stripe checkout",
                "artist_name": artist_name,
                "item_1": f"Tattoo payment for {client_name}",
                "item_1_amount": amount_display,
                "item_2": "",
                "item_2_amount": "",
            },
            fallback_subject=f"Receipt {receipt_number} — {get_settings().STUDIO_NAME}",
            fallback_html=f"<p>Payment received from {client_name}: {amount_display}</p>",
        )
        await send_email(
            to=client_email,
            subject=rendered.subject,
            html_body=rendered.html_body,
            tags=["transaction_receipt", "pos"],
            from_alias=FROM_SERVICE,
        )

    logger.info(
        "POS payment: $%.2f from %s (artist: %s, session: %s)",
        amount / 100,
        client_name,
        artist_name,
        session.get("id"),
    )


async def _handle_account_updated(account):
    """
    Artist Connect account updated — check if onboarding is complete.
    """
    account_id = account.get("id")
    if not account_id:
        return

    onboarding_complete = connect_account_ready(account)
    async with async_session() as db:
        result = await db.execute(select(User).where(User.stripe_account_id == account_id))
        user = result.scalar_one_or_none()
        if user and user.stripe_onboarding_complete != onboarding_complete:
            user.stripe_onboarding_complete = onboarding_complete
            user.updated_at = datetime.now(UTC)
            await db.commit()
            logger.info(
                "Artist %s Stripe onboarding status updated: %s",
                user.name,
                onboarding_complete,
            )
