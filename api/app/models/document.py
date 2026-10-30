"""
Document models — templates + instances for waivers, forms, and uploads.

Two tables:
  bttco_document_templates — reusable form definitions (waiver, job app, etc.)
  bttco_document_instances — each filled/signed/uploaded document

Categories:
  waiver        — tattoo consent / liability waivers
  employment    — job applications, W-9, I-9
  license       — artist licenses, health permits
  aftercare     — aftercare instructions acknowledged
  upload        — generic file uploads (portfolio, ID scans, etc.)

Lifecycle (send flow):
  draft → sent → viewed → signed → approved/rejected
  sent → expired (after expires_at passes)
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentTemplate(Base):
    """Reusable form definition — one per form type (e.g. 'Tattoo Waiver v2')."""

    __tablename__ = "bttco_document_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # waiver | employment | license | aftercare | upload
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON schema defining form fields
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requires_signature: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class DocumentInstance(Base):
    """A single filled/signed/uploaded document belonging to a user."""

    __tablename__ = "bttco_document_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_document_templates.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=True)
    # Guest fields — populated when a public waiver is submitted without login
    guest_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_social: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="draft"
    )  # draft | submitted | signed | pending_review | completed | approved | rejected | expired
    data_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # form field values as JSON
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # uploaded file path
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Send / lifecycle fields ──────────────────────────
    send_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=True)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Artist counter-signature fields ──────────────────
    artist_signature: Mapped[str | None] = mapped_column(Text, nullable=True)  # base64 signature data
    artist_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    artist_id_reviewed: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
