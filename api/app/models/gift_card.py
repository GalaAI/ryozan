"""Artist-specific gift cards and their redemption history.

Gift cards need a durable balance and redemption ledger that the existing POS
transaction model cannot represent without pretending a future service already
happened. Stripe and E-con remain payment authorities. These tables own only
the stored-value liability and its use.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GiftCard(Base):
    __tablename__ = "bttco_gift_cards"
    __table_args__ = (
        CheckConstraint("original_amount_cents > 0", name="ck_gift_card_original_amount_positive"),
        CheckConstraint("balance_cents >= 0", name="ck_gift_card_balance_nonnegative"),
        CheckConstraint("balance_cents <= original_amount_cents", name="ck_gift_card_balance_within_original"),
        CheckConstraint("status IN ('pending', 'active', 'redeemed', 'voided')", name="ck_gift_card_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    artist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bttco_users.id"), index=True)
    purchaser_name: Mapped[str] = mapped_column(String(255), nullable=False)
    purchaser_email: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    send_to_recipient: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    stripe_session_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_destination: Mapped[str] = mapped_column(String(20), nullable=False)
    routing_reason: Mapped[str] = mapped_column(String(40), nullable=False)
    artist_cut_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    shop_cut_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_owed_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class GiftCardRedemption(Base):
    __tablename__ = "bttco_gift_card_redemptions"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="ck_gift_card_redemption_amount_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gift_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_gift_cards.id"), nullable=False, index=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_bookings.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    redeemed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
