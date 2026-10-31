"""
ScrollCategory model — persists dynamic scroll tabs created via the Axiom + wizard.

Built-in tabs (artist, shop, misc) are seeded by migration.
Custom tabs (e.g. financial, health) are created via POST /scrolls/categories.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScrollCategory(Base):
    __tablename__ = "bttco_scroll_categories"
    # Redundant with the slug unique constraint (bttco_scroll_categories_slug_key),
    # but the schema has both; declared so autogenerate does not propose dropping it.
    __table_args__ = (Index("ux_bttco_scroll_categories_slug", "slug", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(60), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Gala AI digest
    ai_digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_digest_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
