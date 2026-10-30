"""
Flash design models — flash sheets and individual segmented designs.

Three tables:
  bttco_flash_sheets    — uploaded flash sheet images
  bttco_flash_designs   — individual designs segmented from sheets
  feedback_samples      — accepted, corrected, and rejected model evidence

Lifecycle:
  upload sheet → AI segment → artist reviews/names/prices → publish → collector claims
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FlashSheet(Base):
    """An uploaded flash sheet image containing multiple designs."""

    __tablename__ = "bttco_flash_sheets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="CASCADE"), nullable=False
    )
    image_url: Mapped[str] = mapped_column(Text, nullable=False)  # Google Drive or stored URL
    source_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    crop_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    crop_x: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default="0")
    crop_y: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0, server_default="0")
    crop_w: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=100, server_default="100")
    crop_h: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=100, server_default="100")
    asset_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="uploaded"
    )  # uploaded | segmented | published | archived
    design_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class FlashDesign(Base):
    """An individual design segmented from a flash sheet."""

    __tablename__ = "bttco_flash_designs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_flash_sheets.id", ondelete="CASCADE"), nullable=False
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="CASCADE"), nullable=False
    )

    # Crop coordinates on the original sheet (x, y, width, height as % of image)
    crop_x: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0)
    crop_y: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0)
    crop_w: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=100)
    crop_h: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=100)

    # Cropped image stored separately
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata (artist fills in after segmentation)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ai_suggested_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    placement: Mapped[str | None] = mapped_column(String(120), nullable=True)
    style: Mapped[str | None] = mapped_column(String(100), nullable=True)  # traditional, japanese, etc.
    mask_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    canvas_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ai_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    gala_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Status
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="draft"
    )  # draft | published | claimed | archived
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_bookings.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ModelFeedbackSample(Base):
    """Review evidence used to build versioned model-training datasets."""

    __tablename__ = "feedback_samples"
    __table_args__ = (
        Index("ix_feedback_samples_model_created", "model_id", "created_at"),
        Index("ix_feedback_samples_subject_created", "subject_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    input_type: Mapped[str] = mapped_column(String(50), nullable=False, default="image")
    input_ref: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    prediction: Mapped[dict] = mapped_column(JSONB, nullable=False)
    correction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False, default="bttco")
    training_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
