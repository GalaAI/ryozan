"""Artist-specific gift-card checkout, delivery, and redemption."""

import secrets
from datetime import UTC, datetime
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_role
from app.models.booking import Booking
from app.models.gift_card import GiftCard, GiftCardRedemption
from app.models.user import User
from app.services.email import FROM_SERVICE, send_email
from app.services.email_foundry import render_gift_card_receipt
from app.services.payments import apply_customer_receipt, customer_price_cents, resolve_charge_routing
from app.services.stripe_client import get_stripe_client

router = APIRouter(prefix="/gift-cards", tags=["gift-cards"])
ARTIST_ROLES = {"artist", "founder"}


class GiftCardCheckoutRequest(BaseModel):
    artist_id: UUID
    amount_cents: int = Field(ge=2500, le=100000)
    purchaser_name: str = Field(min_length=1, max_length=255)
    purchaser_email: EmailStr
    recipient_name: str = Field(min_length=1, max_length=255)
    recipient_email: EmailStr | None = None
    send_to_recipient: bool = True
    message: str | None = Field(default=None, max_length=500)

    @field_validator("purchaser_name", "recipient_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None

    @model_validator(mode="after")
    def require_direct_recipient_email(self) -> "GiftCardCheckoutRequest":
        if self.send_to_recipient and self.recipient_email is None:
            raise ValueError("Recipient email is required for direct delivery")
        return self


class GiftCardCheckoutResponse(BaseModel):
    gift_card_id: UUID
    session_id: str
    checkout_url: str
    face_value_cents: int
    charged_amount_cents: int


class GiftCardStatusResponse(BaseModel):
    status: str
    face_value_cents: int
    recipient_email_hint: str
    delivered: bool


class GiftCardRedeemRequest(BaseModel):
    code: str = Field(min_length=8, max_length=32)
    amount_cents: int = Field(gt=0)
    booking_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class GiftCardRedeemResponse(BaseModel):
    gift_card_id: UUID
    artist_id: UUID
    redeemed_cents: int
    balance_cents: int
    status: str


def _new_code() -> str:
    token = secrets.token_hex(6).upper()
    prefix = get_settings().GIFT_CARD_CODE_PREFIX
    return f"{prefix}-{token[:4]}-{token[4:8]}-{token[8:]}"


def _email_hint(value: str) -> str:
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "recipient"


def _delivery_identity(card: GiftCard) -> tuple[str, str]:
    if card.send_to_recipient:
        if not card.recipient_email:
            raise HTTPException(status_code=500, detail="Gift-card recipient email is unavailable")
        return card.recipient_name, card.recipient_email
    return card.purchaser_name, card.purchaser_email


async def activate_paid_gift_card(session: dict) -> None:
    """Activate and deliver one paid card. Safe across webhook retries."""
    metadata = session.get("metadata", {}) or {}
    try:
        card_id = UUID(str(metadata.get("gift_card_id")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Gift-card checkout is missing its card ID") from exc

    from app.core.database import async_session

    async with async_session() as db:
        result = await db.execute(select(GiftCard).where(GiftCard.id == card_id).with_for_update())
        card = result.scalar_one_or_none()
        if not card:
            raise HTTPException(status_code=404, detail="Gift card not found")
        if card.status == "pending":
            card.status = "active"
            card.activated_at = datetime.now(UTC)
            card.stripe_payment_intent_id = str(session.get("payment_intent") or "") or None
            await db.commit()
        artist = await db.get(User, card.artist_id)
        if card.delivered_at is None:
            if not artist:
                raise HTTPException(status_code=500, detail="Gift-card artist is unavailable")
            delivery_name, delivery_email = _delivery_identity(card)
            receipt = render_gift_card_receipt(
                recipient_name=delivery_name,
                purchaser_name=card.purchaser_name,
                gift_recipient_name=card.recipient_name,
                artist_name=artist.name,
                amount_cents=card.original_amount_cents,
                code=card.code,
                message=card.message,
                sent_to_purchaser=not card.send_to_recipient,
            )
            sent = await send_email(
                to=delivery_email,
                subject=receipt.subject,
                html_body=receipt.html_body,
                tags=[receipt.slug],
                from_alias=FROM_SERVICE,
            )
            if not sent:
                raise HTTPException(status_code=503, detail="Gift-card delivery is pending retry")
            card.delivered_at = datetime.now(UTC)
            await db.commit()


async def void_unpaid_gift_card(session: dict) -> None:
    """Close a pending card when its Checkout expires or payment fails."""
    metadata = session.get("metadata", {}) or {}
    try:
        card_id = UUID(str(metadata.get("gift_card_id")))
    except (TypeError, ValueError):
        return
    from app.core.database import async_session

    async with async_session() as db:
        result = await db.execute(select(GiftCard).where(GiftCard.id == card_id).with_for_update())
        card = result.scalar_one_or_none()
        if card and card.status == "pending":
            card.status = "voided"
            card.balance_cents = 0
            card.payout_owed_cents = 0
            await db.commit()


async def void_refunded_gift_card(payment_intent_id: str | None) -> None:
    """Prevent any remaining stored value from being spent after a refund."""
    if not payment_intent_id:
        return
    from app.core.database import async_session

    async with async_session() as db:
        result = await db.execute(
            select(GiftCard).where(GiftCard.stripe_payment_intent_id == payment_intent_id).with_for_update()
        )
        card = result.scalar_one_or_none()
        if card and card.status in {"active", "redeemed"}:
            card.status = "voided"
            card.balance_cents = 0
            card.payout_owed_cents = 0
            await db.commit()


@router.post("/checkout", response_model=GiftCardCheckoutResponse)
async def create_gift_card_checkout(
    body: GiftCardCheckoutRequest, db: AsyncSession = Depends(get_db, scope="function")
):
    artist = await db.get(User, body.artist_id)
    if not artist or artist.role not in ARTIST_ROLES or not artist.slug:
        raise HTTPException(status_code=404, detail="Artist not found")

    code = _new_code()
    while (await db.execute(select(GiftCard.id).where(GiftCard.code == code))).scalar_one_or_none():
        code = _new_code()

    charged_amount = customer_price_cents(body.amount_cents)
    routing = resolve_charge_routing(artist, charged_amount, artist_share_base_cents=body.amount_cents)
    card = GiftCard(
        code=code,
        artist_id=artist.id,
        purchaser_name=body.purchaser_name,
        purchaser_email=str(body.purchaser_email),
        recipient_name=body.recipient_name,
        recipient_email=str(body.recipient_email) if body.recipient_email else None,
        send_to_recipient=body.send_to_recipient,
        message=body.message,
        original_amount_cents=body.amount_cents,
        balance_cents=body.amount_cents,
        routing_destination=routing.destination,
        routing_reason=routing.reason,
        artist_cut_cents=routing.artist_cut_cents,
        shop_cut_cents=routing.shop_cut_cents,
        payout_owed_cents=routing.payout_owed_cents,
    )
    db.add(card)
    await db.flush()

    settings = get_settings()
    params = {
        "mode": "payment",
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"{artist.name} tattoo gift card",
                        "description": f"${body.amount_cents / 100:,.2f} value for tattoo services",
                    },
                    "unit_amount": charged_amount,
                },
                "quantity": 1,
            }
        ],
        "metadata": {
            "checkout_type": "gift_card",
            "gift_card_id": str(card.id),
            "artist_id": str(artist.id),
            "routing_destination": routing.destination,
            "routing_reason": routing.reason,
            "delivery_destination": "recipient" if body.send_to_recipient else "purchaser",
            **routing.receipt_metadata(),
        },
        "success_url": f"{settings.FRONTEND_URL}/gift-cards?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{settings.FRONTEND_URL}/gift-cards?cancelled=1",
        "customer_email": str(body.purchaser_email),
    }
    params = routing.apply_to(params)
    params = apply_customer_receipt(
        params,
        customer_email=str(body.purchaser_email),
        description=f"Ryozan gift card for {artist.name}",
        metadata=params["metadata"],
    )
    try:
        session = get_stripe_client().v1.checkout.sessions.create(
            params, routing.request_options(idempotency_key=f"gift-card-{card.id}")
        )
    except stripe.error.StripeError as exc:
        await db.rollback()
        raise HTTPException(status_code=502, detail="Gift-card checkout could not be created") from exc
    card.stripe_session_id = session.id
    await db.commit()
    return GiftCardCheckoutResponse(
        gift_card_id=card.id,
        session_id=session.id,
        checkout_url=session.url,
        face_value_cents=body.amount_cents,
        charged_amount_cents=charged_amount,
    )


@router.get("/status/{session_id}", response_model=GiftCardStatusResponse)
async def get_gift_card_status(session_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    card = (await db.execute(select(GiftCard).where(GiftCard.stripe_session_id == session_id))).scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Gift card not found")
    _delivery_name, delivery_email = _delivery_identity(card)
    return GiftCardStatusResponse(
        status=card.status,
        face_value_cents=card.original_amount_cents,
        recipient_email_hint=_email_hint(delivery_email),
        delivered=card.delivered_at is not None,
    )


@router.post("/redeem", response_model=GiftCardRedeemResponse)
async def redeem_gift_card(
    body: GiftCardRedeemRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("assistant")),
):
    code = body.code.strip().upper()
    card = (await db.execute(select(GiftCard).where(GiftCard.code == code).with_for_update())).scalar_one_or_none()
    if not card or card.status not in {"active", "redeemed"}:
        raise HTTPException(status_code=404, detail="Active gift card not found")
    if current_user.role == "artist" and card.artist_id != current_user.id:
        raise HTTPException(status_code=403, detail="Gift card belongs to another artist")
    if body.amount_cents > card.balance_cents:
        raise HTTPException(status_code=409, detail="Gift-card balance is too low")
    if body.booking_id:
        booking = await db.get(Booking, body.booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.artist_id != card.artist_id:
            raise HTTPException(status_code=409, detail="Gift card and booking artists do not match")
    card.balance_cents -= body.amount_cents
    card.status = "redeemed" if card.balance_cents == 0 else "active"
    db.add(
        GiftCardRedemption(
            gift_card_id=card.id,
            amount_cents=body.amount_cents,
            booking_id=body.booking_id,
            note=body.note,
            redeemed_by=current_user.id,
        )
    )
    await db.commit()
    return GiftCardRedeemResponse(
        gift_card_id=card.id,
        artist_id=card.artist_id,
        redeemed_cents=body.amount_cents,
        balance_cents=card.balance_cents,
        status=card.status,
    )


@router.get("/summary")
async def get_gift_cards_summary(
    artist_id: UUID | None = None,
    status: str | None = None,
    limit: int = 25,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("assistant")),
):
    """Gift card counts, open balance and artist payout owed, newest cards first.

    Artists see only their own cards. Voided, refunded and disputed cards owe nothing.
    """
    stmt = select(GiftCard)
    if current_user.role == "artist":
        stmt = stmt.where(GiftCard.artist_id == current_user.id)
    elif artist_id is not None:
        stmt = stmt.where(GiftCard.artist_id == artist_id)

    if status:
        stmt = stmt.where(GiftCard.status == status.strip().lower())

    all_rows = (await db.execute(stmt.order_by(GiftCard.created_at.desc()))).scalars().all()

    def owed(row: GiftCard) -> int:
        return int(row.payout_owed_cents or 0) if row.status in {"active", "redeemed"} else 0

    bounded_limit = max(1, min(limit, 100))
    cards = [
        {
            "id": str(row.id),
            "code": row.code,
            "artist_id": str(row.artist_id),
            "status": row.status,
            "original_amount_cents": int(row.original_amount_cents or 0),
            "balance_cents": int(row.balance_cents or 0),
            "payout_owed_cents": owed(row),
            "routing_destination": row.routing_destination,
            "delivered": row.delivered_at is not None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in all_rows[:bounded_limit]
    ]

    return {
        "total_count": len(all_rows),
        "active_count": sum(1 for row in all_rows if row.status == "active"),
        "pending_count": sum(1 for row in all_rows if row.status == "pending"),
        "redeemed_count": sum(1 for row in all_rows if row.status == "redeemed"),
        "voided_count": sum(1 for row in all_rows if row.status in {"voided", "refunded", "disputed"}),
        "active_balance_cents": sum(int(row.balance_cents or 0) for row in all_rows if row.status == "active"),
        "payout_owed_cents": sum(max(owed(row), 0) for row in all_rows),
        "undelivered_active_count": sum(1 for row in all_rows if row.status == "active" and row.delivered_at is None),
        "cards": cards,
    }
