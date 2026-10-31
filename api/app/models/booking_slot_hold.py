"""
Short-lived public booking slot hold.

This is intentionally separate from booking drafts and staff slot proposals:
drafts recover partially completed forms, slot proposals are conversation
artifacts, and this table protects self-booking availability while a client
finishes intake.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BookingSlotHold(Base):
    """Temporary reservation for one public booking slot."""

    __tablename__ = "ryozan_booking_slot_holds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hold_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    artist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ryozan_users.id"), nullable=False)
    artist_slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scheduled_time: Mapped[str] = mapped_column(String(5), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    booking_kind: Mapped[str] = mapped_column(String(50), nullable=False, default="tattoo_session")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    inquiry_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_bookings.id"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
