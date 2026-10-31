"""
Personal time block.

A staff-owned unavailable range on an artist's calendar (lunch, personal time,
appointments, days off). The scheduler treats these as busy intervals alongside
Google Calendar busy blocks, confirmed bookings, and active slot holds, so a
blocked window cannot be self-booked or staff-booked.

Distinct from booking slot holds (short-lived public race protection) and from
confirmed bookings (client appointments): time blocks carry no client and never
expire on their own.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TimeBlock(Base):
    """An artist's personal unavailable window."""

    __tablename__ = "ryozan_time_blocks"

    # Declare the composite (artist_id, block_date) index on the model so it is
    # created by BOTH paths: Alembic migration AND Base.metadata.create_all (prod
    # uses startup create_all, not Alembic, so a migration-only index would be
    # silently missing in prod). This is the index the scheduler range queries hit.
    __table_args__ = (Index("ix_ryozan_time_blocks_artist_date", "artist_id", "block_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    block_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # start_time / end_time are HH:MM (24h). Ignored when all_day is True.
    start_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # google_calendar_event_id lets us mirror the block onto the artist's calendar.
    google_calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
