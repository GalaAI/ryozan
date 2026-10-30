"""Editable SMS templates with {{variable}} placeholders.

Mirrors `EmailTemplate` for structural symmetry. The SMS service renders
these at send-time. Templates without a DB row fall back to hardcoded defaults
defined in `app.services.sms_template_seeder`.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SmsTemplate(Base):
    __tablename__ = "bttco_sms_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="transactional")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL = shop-level template (founder/admin only).
    # Non-NULL = personal template owned by that user; only that user + admin+
    # can edit. Intentionally no hard FK so deleting an artist preserves audit.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
