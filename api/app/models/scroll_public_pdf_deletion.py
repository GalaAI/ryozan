"""Public scroll PDF objects awaiting deletion after their records moved to the private copy.

Exists so a failed or interrupted delete cannot leave a signature-bearing PDF public forever. The
boot move rewrites every trusted reference to the private copy, so once that commit lands nothing
else would ever rediscover the public object. A row is written in the same transaction as the
reference rewrite, before any delete is attempted, and is retried with backoff until R2 confirms the
delete. Never logged beyond counts.

status: pending (delete not yet confirmed), deleted (R2 confirmed), kept (another record still
mentions the object, so it is deliberately not deleted).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScrollPublicPdfDeletion(Base):
    __tablename__ = "ryozan_scroll_public_pdf_deletions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The object key under scrolls-public/ and the stored reference delete_file is called with.
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    # Records proven not to belong to the PDF's artist (JSON list of ids): their reference never keeps
    # the public object alive.
    ignore_document_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
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
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
