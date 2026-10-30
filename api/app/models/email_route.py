"""
Email route model -- dynamic routing table for inbound @ryozan.app mail.

Each artist picks a handle during onboarding. Inbound mail to that handle
gets forwarded to their personal email. Verified via confirmation link.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailRoute(Base):
    __tablename__ = "bttco_email_routes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handle: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )  # e.g. "sarah" -> sarah@ryozan.app
    forward_to: Mapped[str] = mapped_column(String(255), nullable=False)  # personal email to forward to
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bttco_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
