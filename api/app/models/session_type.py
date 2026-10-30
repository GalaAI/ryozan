"""
Per-artist session type configuration.

Each artist defines their own size/placement/duration/pricing matrix.
When a booking inquiry comes in, the system looks up matching session
types based on the client's selected size and placement to auto-suggest
duration, deposit, and price range.

Used by:
  - The public booking form's smart slot suggestions (Phase 8)
  - The Courier toolkit's quote composer (auto-fill pricing)
  - The flash claim flow (flash designs link to a session type)
  - Gala AI (draft responses with accurate pricing)
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ArtistSessionType(Base):
    """
    A bookable session configuration for a specific artist.

    Examples:
      - "Small Piece" — size=small, duration=90min, deposit=$100, price=$150-250
      - "Large Back Piece" — size=xl, placements=[back], duration=360min, deposit=$300, price=$800-1500
      - "Touch-Up" — size=small, duration=60min, deposit=$50, price=$75-100
    """

    __tablename__ = "bttco_artist_session_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # Artist-facing blurb shown under the label on the booking form. Optional.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # How this session type quotes: 'range' uses price_min/price_max, 'hourly'
    # uses hourly_rate, 'flat' uses price_min alone. server_default='range'
    # because every row that predates this column carries a price_min/price_max
    # range, so the default keeps their existing quoting behaviour unchanged.
    pricing_mode: Mapped[str] = mapped_column(String(50), nullable=False, server_default="range", default="range")
    # Size tier — must match the system constants (small, medium, large, xl)
    size: Mapped[str] = mapped_column(String(50), nullable=False)
    # Which body placements this session type applies to (empty = all)
    placements: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True, default=list)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    buffer_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deposit_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=100)
    hourly_rate: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_min: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_max: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
