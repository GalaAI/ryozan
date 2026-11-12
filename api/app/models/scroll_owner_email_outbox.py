"""Durable owner-email outbox for submitted Artist Scrolls.

This is intentionally separate from EmailLog. EmailLog records provider attempts after
they happen; this row is committed atomically with the owner archive so a process exit
cannot lose the obligation to notify the founder.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScrollOwnerEmailOutbox(Base):
    __tablename__ = "ryozan_scroll_owner_email_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    founder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_users.id", ondelete="SET NULL"), nullable=True
    )
    submission_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attachment_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Set only when the row holds the bytes of a scroll PDF whose upload failed: the exact DocumentInstance
    # ids the server created for this submission (JSON list) and the submitting artist, written at submit
    # time. The retry writes the stored PDF URL onto these records only, never onto a record found by
    # matching client-writable data_json. NULL on rows queued before ids were recorded.
    document_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    artist_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
