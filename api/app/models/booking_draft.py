"""
Recoverable public booking draft.

Created before the final booking submit so partial public inquiries can be
traced if image upload succeeds but the JSON submit fails.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BookingDraft(Base):
    """Public booking form snapshot keyed by a browser-generated inquiry id."""

    __tablename__ = "bttco_booking_drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inquiry_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    artist_slug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    guest_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    guest_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    form_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    image_urls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    reference_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
