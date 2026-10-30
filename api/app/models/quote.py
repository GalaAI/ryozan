"""
Quote model — pricing + slot offers sent to clients.

A quote is created when an artist reviews a booking request and sends
pricing + available time slots to the client. The client accepts by
picking a slot and paying the deposit via Stripe Checkout.

Lifecycle: draft -> sent -> viewed -> accepted / expired / revised
"""

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Quote(Base):
    """A pricing + availability offer sent to a client for a booking."""

    __tablename__ = "bttco_quotes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bttco_bookings.id"), nullable=False)

    # Token for public access (client clicks link in email)
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True, default=lambda: secrets.token_urlsafe(32)
    )

    # Pricing
    estimated_total_min: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    estimated_total_max: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    deposit_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Available time slots (JSON array of {date, time, duration_minutes})
    available_slots_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Personal note from artist to client
    artist_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status tracking
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="sent"
    )  # sent, viewed, accepted, expired, revised

    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Client's chosen slot (set on acceptance)
    selected_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    selected_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Stripe
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
