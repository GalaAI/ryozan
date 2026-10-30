"""
EmailLog model — tracks every outbound email send attempt.

Populated by send_email() on every call.
Status updated by Resend webhook (delivered, bounced, opened).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailLog(Base):
    """Record of a single outbound email send."""

    __tablename__ = "bttco_email_log"
    __table_args__ = (
        Index(
            "ix_bttco_email_log_courier_message_attempt",
            "courier_message_id",
            "courier_attempt",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Addressing
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    template: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    from_alias: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Status: queued | sent | failed | delivered | bounced | complained | opened | clicked
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)

    # Resend message ID — returned on successful send, used for webhook correlation
    resend_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)

    # Error detail if send failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Link back to Courier message (for delivery receipts)
    courier_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Booky Courier attempts need their own correlation key. `retry_count` is
    # owned by the generic founder email-log retry flow and cannot serve as it.
    courier_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Retry tracking
    retry_count: Mapped[int] = mapped_column(default=0)
    last_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
