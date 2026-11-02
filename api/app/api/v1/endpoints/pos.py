"""
POS (Point of Sale) endpoints — day-of payment collection for artists.

Artists create a Checkout Session for the remaining balance after a deposit.
Clients scan a QR code → pay via card, Apple Pay, Google Pay, or Venmo.
Payments flow through Stripe Connect as direct charges on the artist merchant.
"""

import logging
from datetime import date
from html import escape
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.timezones import shop_day_bounds, shop_today
from app.core.config import get_settings
from app.core.database import get_db
from app.core.routes import ROUTES
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking import Booking
from app.models.pos_transaction import POSTransaction
from app.models.user import User
from app.schemas.pos import POSTransactionCreate, POSTransactionResponse
from app.services.email import FROM_SERVICE, render_transactional_email, send_email
from app.services.payments import (
    apply_customer_receipt,
    customer_price_cents,
    effective_artist_rate,
    resolve_charge_routing,
)
from app.services.stripe_client import get_stripe_client, stripe_request_options

logger = logging.getLogger("ryozan.pos")
router = APIRouter(prefix="/pos", tags=["pos"])


def _init_stripe():
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY


def _require_role(user: User, min_role: str):
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")


def _require_booking_charge_access(booking: Booking, current_user: User) -> None:
    """Limit artist POS charges to bookings assigned to that artist."""
    if current_user.role in {"admin", "founder"}:
        return
    if booking.artist_id != current_user.id:
        raise HTTPException(status_code=403, detail="Booking is not assigned to this artist")


# ── Request / Response schemas ──────────────────────────


class POSChargeRequest(BaseModel):
    amount_cents: int = Field(..., gt=0, description="Charge amount in cents (e.g. 15000 = $150.00)")
    booking_id: UUID | None = Field(None, description="Link to existing booking (optional)")
    client_name: str | None = Field(None, description="Client name for walk-ins")
    client_email: str | None = Field(None, description="Client email for receipt")
    description: str | None = Field(None, description="Service description")


class POSChargeResponse(BaseModel):
    session_id: str
    checkout_url: str
    amount_cents: int
    description: str


class POSInvoiceRequest(POSChargeRequest):
    client_email: EmailStr = Field(..., description="Client email that receives the payment link")


class POSInvoiceResponse(POSChargeResponse):
    recipient_email: EmailStr
    email_sent: bool


class POSStatusResponse(BaseModel):
    session_id: str
    status: str  # "open", "complete", "expired"
    payment_status: str  # "unpaid", "paid", "no_payment_required"
    amount_cents: int


class POSArtistSummary(BaseModel):
    artist_id: UUID
    artist_name: str
    commission_rate: int | None
    transaction_count: int
    gross_cents: int
    cash_cents: int
    card_cents: int
    estimated_artist_share_cents: int
    estimated_shop_share_cents: int
    estimated_tax_reserve_cents: int


class POSIntegrationSummary(BaseModel):
    period_start: date
    period_end: date
    transaction_count: int
    gross_cents: int
    cash_cents: int
    card_cents: int
    stripe_cents: int
    manual_cents: int
    estimated_tax_reserve_cents: int
    estimated_artist_share_cents: int
    estimated_shop_share_cents: int
    by_artist: list[POSArtistSummary]
    recent_transactions: list[POSTransactionResponse]


# ── Endpoints ───────────────────────────────────────────


async def _create_pos_checkout(
    body: POSChargeRequest,
    db: AsyncSession,
    current_user: User,
) -> POSChargeResponse:
    _require_role(current_user, "artist")

    settings = get_settings()

    # Build description
    desc = body.description or "Tattoo session"
    client_name = body.client_name or "Walk-in"

    # If linked to a booking, pull client info
    booking = None
    if body.booking_id:
        result = await db.execute(select(Booking).where(Booking.id == body.booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        _require_booking_charge_access(booking, current_user)
        client_name = booking.guest_name or client_name
        desc = booking.description or desc
        if not body.client_email and booking.guest_email:
            body.client_email = booking.guest_email

    _init_stripe()

    service_price_cents = body.amount_cents
    customer_amount_cents = customer_price_cents(service_price_cents)

    # Build Checkout Session params
    session_params = {
        "mode": "payment",
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Tattoo — {desc}",
                        "description": f"Artist: {current_user.name} | Client: {client_name}",
                    },
                    "unit_amount": customer_amount_cents,
                },
                "quantity": 1,
            }
        ],
        "metadata": {
            "pos_charge": "true",
            "artist_id": str(current_user.id),
            "artist_name": current_user.name,
            "client_name": client_name,
            "booking_id": str(body.booking_id) if body.booking_id else "",
            # The webhook records the POS ledger row from this metadata, so the
            # service description has to travel with the session. Stripe caps a
            # metadata value at 500 characters.
            "description": desc[:400],
        },
        "success_url": f"{settings.FRONTEND_URL}{ROUTES.VAULT}?pos=success",
        "cancel_url": f"{settings.FRONTEND_URL}{ROUTES.VAULT}?pos=cancelled",
    }

    # Add customer email for receipt if available
    if body.client_email:
        session_params["customer_email"] = body.client_email

    routing = resolve_charge_routing(
        current_user,
        customer_amount_cents,
        artist_share_base_cents=service_price_cents,
    )
    session_params = routing.apply_to(session_params)
    session_params = apply_customer_receipt(
        session_params,
        customer_email=body.client_email,
        description=f"Ryozan tattoo payment - {booking.reference_code if booking else client_name}",
        metadata={
            "checkout_type": "pos_payment",
            "booking_id": str(body.booking_id) if body.booking_id else "",
            "reference_code": booking.reference_code if booking and booking.reference_code else "",
            "artist_id": str(current_user.id),
            "routing_destination": routing.destination,
            "routing_reason": routing.reason,
            **routing.receipt_metadata(),
        },
    )

    session = get_stripe_client().v1.checkout.sessions.create(session_params, routing.request_options())

    # If linked to a booking, save the checkout session ID
    if booking:
        booking.stripe_checkout_session_id = session.id
        await db.flush()

    logger.info(
        "POS charge created: $%.2f for %s by %s (session: %s)",
        customer_amount_cents / 100,
        client_name,
        current_user.name,
        session.id,
    )

    return POSChargeResponse(
        session_id=session.id,
        checkout_url=session.url,
        amount_cents=customer_amount_cents,
        description=desc,
    )


@router.post("/charge", response_model=POSChargeResponse)
async def create_pos_charge(
    body: POSChargeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a Stripe Checkout Session for a POS charge.
    Returns a checkout URL that the artist displays as a QR code.
    Client scans and pays with an available Stripe payment method.
    """
    return await _create_pos_checkout(body, db, current_user)


def _build_pos_invoice_email_html(
    *,
    client_name: str,
    artist_name: str,
    description: str,
    amount_cents: int,
    checkout_url: str,
) -> str:
    safe_client = escape(client_name)
    safe_artist = escape(artist_name)
    safe_description = escape(description)
    safe_checkout_url = escape(checkout_url, quote=True)
    safe_studio = escape(get_settings().STUDIO_NAME)
    amount = f"${amount_cents / 100:,.2f}"
    return f"""<!doctype html>
<html>
  <body style="margin:0;background:#0b0c0b;color:#e2dac2;font-family:Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0b0c0b;padding:28px 14px;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#121412;border:1px solid #514633;border-radius:18px;overflow:hidden;">
          <tr><td style="padding:26px 28px 18px;border-bottom:1px solid #302d27;">
            <div style="color:#b48a4b;font-size:11px;letter-spacing:2px;text-transform:uppercase;">{safe_studio}</div>
            <h1 style="margin:8px 0 0;color:#e2dac2;font-size:28px;line-height:1.15;">Payment Request</h1>
          </td></tr>
          <tr><td style="padding:24px 28px;">
            <p style="margin:0 0 16px;color:#c8c0aa;font-size:15px;line-height:1.6;">Hi {safe_client}, {safe_artist} sent you a secure payment link for {safe_description}.</p>
            <div style="margin:20px 0;padding:18px;border:1px solid #3b382f;border-radius:12px;text-align:center;">
              <div style="color:#8e897b;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;">Amount Due</div>
              <div style="margin-top:6px;color:#e2dac2;font-size:34px;font-weight:700;">{amount}</div>
            </div>
            <p style="margin:24px 0;text-align:center;"><a href="{safe_checkout_url}" style="display:inline-block;padding:14px 24px;border-radius:999px;background:#e2dac2;color:#111310;font-size:13px;font-weight:700;letter-spacing:1px;text-decoration:none;text-transform:uppercase;">Pay Securely</a></p>
            <p style="margin:0;color:#777268;font-size:12px;line-height:1.5;">Stripe securely handles payment details. {safe_studio} does not receive your full card number.</p>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


@router.post("/invoice", response_model=POSInvoiceResponse)
async def send_pos_invoice(
    body: POSInvoiceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # A POS invoice deliberately reuses the existing Checkout contract so pricing,
    # Connect routing, booking scope, receipts, and webhook evidence stay authoritative.
    # It adds delivery through the shared Ryozan email service, not a second accounting path.
    checkout = await _create_pos_checkout(body, db, current_user)
    recipient = str(body.client_email)
    client_name = body.client_name or "there"
    fallback_subject = f"Payment request from {get_settings().STUDIO_NAME}"
    fallback_html = _build_pos_invoice_email_html(
        client_name=client_name,
        artist_name=current_user.name,
        description=checkout.description,
        amount_cents=checkout.amount_cents,
        checkout_url=checkout.checkout_url,
    )
    rendered = await render_transactional_email(
        "deposit_request",
        {
            "client_name": client_name,
            "artist_name": current_user.name,
            "reference_code": checkout.session_id,
            "estimated_range": f"${checkout.amount_cents / 100:,.2f}",
            "deposit_amount": f"${checkout.amount_cents / 100:,.2f}",
            "artist_message": f"Secure payment request for {checkout.description}.",
            "quote_url": checkout.checkout_url,
        },
        fallback_subject=fallback_subject,
        fallback_html=fallback_html,
    )
    sent = await send_email(
        to=recipient,
        subject=rendered.subject,
        html_body=rendered.html_body,
        tags=["pos_invoice"],
        sender_id=str(current_user.id),
        sender_role=current_user.role,
        from_alias=FROM_SERVICE,
    )
    if not sent:
        raise HTTPException(status_code=502, detail="Invoice email could not be sent")
    logger.info("POS invoice sent to %s by %s (session: %s)", recipient, current_user.name, checkout.session_id)
    return POSInvoiceResponse(
        **checkout.model_dump(),
        recipient_email=recipient,
        email_sent=True,
    )


@router.get("/status/{session_id}", response_model=POSStatusResponse)
async def get_pos_status(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Poll the payment status of a POS Checkout Session.
    Frontend polls this to show real-time confirmation.
    """
    _init_stripe()
    _require_role(current_user, "artist")

    try:
        session = get_stripe_client().v1.checkout.sessions.retrieve(
            session_id,
            options=stripe_request_options(
                account_id=current_user.stripe_account_id if current_user.stripe_onboarding_complete else None
            ),
        )
    except stripe.error.InvalidRequestError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    return POSStatusResponse(
        session_id=session.id,
        status=session.status,  # "open", "complete", "expired"
        payment_status=session.payment_status,  # "unpaid", "paid"
        amount_cents=session.amount_total or 0,
    )


@router.get("/summary", response_model=POSIntegrationSummary)
async def get_pos_summary(
    period_start: date | None = Query(None, description="Start date, defaults to first day of current year"),
    period_end: date | None = Query(None, description="End date, defaults to today"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Finance summary for Hustle E-con.

    Uses existing POS transaction records only. Stripe/Plaid/Mercury/Clover
    provider ledgers are intentionally kept out until each rail has its own
    auditable sync model and reconciliation rules.
    """
    _require_role(current_user, "assistant")

    today = shop_today()
    start = period_start or date(today.year, 1, 1)
    end = period_end or today
    if start > end:
        raise HTTPException(status_code=400, detail="period_start must be before period_end")

    day_start, day_end = shop_day_bounds(start, end)

    query = select(POSTransaction).where(
        and_(
            POSTransaction.created_at >= day_start,
            POSTransaction.created_at <= day_end,
            POSTransaction.status == "completed",
        )
    )
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 99):
        query = query.where(POSTransaction.artist_id == current_user.id)
    query = query.order_by(POSTransaction.created_at.desc())

    result = await db.execute(query)
    transactions = result.scalars().all()

    artist_ids = {txn.artist_id for txn in transactions}
    artists_by_id: dict[UUID, User] = {}
    if artist_ids:
        artist_result = await db.execute(select(User).where(User.id.in_(artist_ids)))
        artists_by_id = {artist.id: artist for artist in artist_result.scalars().all()}

    gross_cents = sum(txn.amount_cents for txn in transactions)
    cash_cents = sum(txn.amount_cents for txn in transactions if txn.payment_method == "cash")
    card_cents = sum(txn.amount_cents for txn in transactions if txn.payment_method == "card")
    stripe_cents = sum(txn.amount_cents for txn in transactions if txn.processor == "stripe")
    manual_cents = sum(txn.amount_cents for txn in transactions if txn.processor == "manual")

    by_artist: list[POSArtistSummary] = []
    estimated_artist_share_cents = 0
    estimated_shop_share_cents = 0
    for artist_id in sorted(artist_ids, key=str):
        artist_txns = [txn for txn in transactions if txn.artist_id == artist_id]
        artist = artists_by_id.get(artist_id)
        commission_rate = artist.commission_rate if artist else None
        effective_rate = effective_artist_rate(artist) if artist else 70
        artist_gross = sum(txn.amount_cents for txn in artist_txns)
        artist_cash = sum(txn.amount_cents for txn in artist_txns if txn.payment_method == "cash")
        artist_card = sum(txn.amount_cents for txn in artist_txns if txn.payment_method == "card")
        artist_share = sum(txn.commission_base_cents * effective_rate // 100 for txn in artist_txns)
        shop_share = artist_gross - artist_share
        estimated_artist_share_cents += artist_share
        estimated_shop_share_cents += shop_share
        by_artist.append(
            POSArtistSummary(
                artist_id=artist_id,
                artist_name=artist.name if artist else "Unknown artist",
                commission_rate=commission_rate,
                transaction_count=len(artist_txns),
                gross_cents=artist_gross,
                cash_cents=artist_cash,
                card_cents=artist_card,
                estimated_artist_share_cents=artist_share,
                estimated_shop_share_cents=shop_share,
                estimated_tax_reserve_cents=int(artist_share * 0.30),
            )
        )

    return POSIntegrationSummary(
        period_start=start,
        period_end=end,
        transaction_count=len(transactions),
        gross_cents=gross_cents,
        cash_cents=cash_cents,
        card_cents=card_cents,
        stripe_cents=stripe_cents,
        manual_cents=manual_cents,
        estimated_tax_reserve_cents=int(estimated_artist_share_cents * 0.30),
        estimated_artist_share_cents=estimated_artist_share_cents,
        estimated_shop_share_cents=estimated_shop_share_cents,
        by_artist=by_artist,
        recent_transactions=[POSTransactionResponse.model_validate(txn) for txn in transactions[:10]],
    )


# ── Transaction Recording ─────────────────────────────


@router.post("/record", response_model=POSTransactionResponse)
async def record_pos_transaction(
    body: POSTransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Record a POS transaction (cash or card).
    Artist/admin/founder/assistant can record payments.
    The artist_id defaults to the current user for artists,
    or must be the current user for non-admin roles.
    """
    _require_role(current_user, "assistant")

    commission_base_cents = body.amount_cents if body.commission_base_cents is None else body.commission_base_cents
    if commission_base_cents > body.amount_cents:
        raise HTTPException(status_code=422, detail="Commission base cannot exceed the collected amount")

    txn = POSTransaction(
        artist_id=current_user.id,
        booking_id=body.booking_id,
        amount_cents=body.amount_cents,
        commission_base_cents=commission_base_cents,
        price_adjustment_cents=body.amount_cents - commission_base_cents,
        payment_method=body.payment_method,
        processor=body.processor,
        client_name=body.client_name,
        description=body.description,
        stripe_session_id=body.stripe_session_id,
        notes=body.notes,
        status="completed",
        created_by=current_user.id,
    )
    db.add(txn)
    await db.flush()
    await db.refresh(txn)

    logger.info(
        "POS transaction recorded: $%.2f %s by %s (id: %s)",
        body.amount_cents / 100,
        body.payment_method,
        current_user.name,
        txn.id,
    )

    return POSTransactionResponse.model_validate(txn)


@router.get("/transactions", response_model=list[POSTransactionResponse])
async def list_pos_transactions(
    date_filter: date | None = Query(None, alias="date", description="Filter by date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List POS transactions.
    Artists see only their own transactions.
    Admin/founder see all transactions.
    Supports optional ?date=YYYY-MM-DD filter.
    """
    _require_role(current_user, "assistant")

    query = select(POSTransaction).order_by(POSTransaction.created_at.desc())

    # Role-based scoping: admin/founder see all, others see only their own
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 99):
        query = query.where(POSTransaction.artist_id == current_user.id)

    # Optional date filter
    if date_filter:
        day_start, day_end = shop_day_bounds(date_filter, date_filter)
        query = query.where(
            POSTransaction.created_at >= day_start,
            POSTransaction.created_at <= day_end,
        )

    result = await db.execute(query)
    rows = result.scalars().all()

    return [POSTransactionResponse.model_validate(r) for r in rows]
