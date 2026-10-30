"""Booking lifecycle event log.

This table is intentionally separate from Courier messages and booking notes.
Courier audits are conversation-scoped, while booking holds can exist before a
conversation or booking row. Notes are mutable text fields, but Huddl needs an
append-only scheduler timeline for holds, submits, reschedules, cancellations,
calendar sync, quote acceptance, flash claims, and webhook completion.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BookingLifecycleEvent(Base):
    """Append-only booking lifecycle event for Huddl and scheduler audits."""

    __tablename__ = "bttco_booking_lifecycle_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_bookings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    artist_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="system")
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    reference_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    hold_token: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )
