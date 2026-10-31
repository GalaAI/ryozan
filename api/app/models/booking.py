"""
Booking model: artist appointment requests from clients.

Lifecycle: pending → confirmed → completed / cancelled / no_show
Artists see inbound requests; clients track their bookings.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Booking(Base):
    """A tattoo appointment request / scheduled session."""

    __tablename__ = "bttco_bookings"
    __table_args__ = (
        Index("idx_bttco_bookings_artist_id", "artist_id"),
        Index("idx_bttco_bookings_artist_status", "artist_id", "status"),
        Index("idx_bttco_bookings_client_id", "client_id"),
        Index(
            "idx_bttco_bookings_conversation_id",
            "conversation_id",
            postgresql_where=text("conversation_id IS NOT NULL"),
        ),
        Index("idx_bttco_bookings_created_at", "created_at"),
        Index(
            "idx_bttco_bookings_reference_code",
            "reference_code",
            postgresql_where=text("reference_code IS NOT NULL"),
        ),
        Index(
            "idx_bttco_bookings_scheduled_date",
            "scheduled_date",
            postgresql_where=text("scheduled_date IS NOT NULL"),
        ),
        Index("idx_bttco_bookings_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Who's involved (client_id nullable for guest bookings)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=True)
    artist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=False)

    # Guest info (populated when no account; cleared on link-account)
    guest_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sms_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sms_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sms_consent_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tracking
    reference_code: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True, index=True)

    # Booking details
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending"
    )  # pending, quoted, confirmed, completed, cancelled, no_show, expired
    description: Mapped[str] = mapped_column(Text, nullable=False)
    placement: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_estimate: Mapped[str | None] = mapped_column(String(100), nullable=True)
    style_preference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference_urls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of URLs (consolidated)
    budget: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timeline: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Schedule
    preferred_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    preferred_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Collector-proposed alternatives for custom, consultation and BYOF requests:
    # [{"date": "YYYY-MM-DD", "time": "HH:MM"}, ...] (1 to 3 entries, no hold).
    preferred_slots_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    scheduled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    scheduled_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(nullable=True)

    # Pricing
    deposit_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    estimated_total: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    deposit_paid: Mapped[bool] = mapped_column(default=False)
    is_bring_your_own_flash: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    deposit_authorization_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    deposit_authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deposit_review_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deposit_review_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Notes
    artist_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stripe
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Google Calendar
    google_calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Import source: 'bttco' (native), 'google_calendar', 'acuity'
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="bttco")
    booking_kind: Mapped[str] = mapped_column(
        String(50), nullable=False, default="tattoo_session", server_default="tattoo_session"
    )
    consultation_method: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Snapshot the collector's Booky consent at submission time. This is
    # independent of SMS consent and remains auditable if policy changes later.
    booky_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    booky_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booky_consent_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    booky_collector_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booky_capability_inquiry_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Linked conversation (auto-created when booking is made)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_conversations.id"), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
