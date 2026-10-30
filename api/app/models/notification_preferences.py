"""
Notification preferences model — per-user opt-in/out for email and push channels.

Allows users to control which notification categories they receive.
One row per user, created with defaults on first access.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NotificationPreferences(Base):
    __tablename__ = "bttco_notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bttco_users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # ── Email channel ──
    email_bookings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_messages: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_quests: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_marketing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Push channel ──
    push_bookings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    push_messages: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    push_quests: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
