"""Stash inventory models for Hustle commerce lanes."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StashItem(Base):
    """Creator-scoped sellable inventory across flash, physical goods, and digital products."""

    __tablename__ = "bttco_stash_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="SET NULL"), nullable=True
    )
    source_flash_design_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_flash_designs.id", ondelete="SET NULL"), nullable=True
    )

    lane: Mapped[str] = mapped_column(String(32), nullable=False)  # flash | crafts | digital
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    sku: Mapped[str | None] = mapped_column(String(120), nullable=True)
    asset_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    inventory_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    storage_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    ai_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    gala_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class StashCanvasSession(Base):
    """Canvas state for AI-assisted segmentation review and metadata enrichment."""

    __tablename__ = "bttco_stash_canvas_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="CASCADE"), nullable=False
    )
    sheet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_flash_sheets.id", ondelete="CASCADE"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="SET NULL"), nullable=True
    )

    lane: Mapped[str] = mapped_column(String(32), nullable=False, default="flash")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    canvas_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ai_suggestions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    gala_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
