"""Artist Scroll PDFs whose upload to storage failed, held until a retry stores them privately.

Exists so a storage outage cannot lose the only copy of a scroll PDF, whether or not the shop has a
founder (owner email rows exist only per founder, so they cannot be the holding place). One row per
submission, added in the same transaction as the artist's scroll record. The bytes are deleted only
after the retry has stored the PDF under the private scrolls/ prefix and recorded its URL on the exact
records the server created for the submission. Never emailed, never logged.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScrollPdfPendingUpload(Base):
    __tablename__ = "ryozan_scroll_pdf_pending_uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The submission's private scrolls/<artist>/<submission> folder and the content-marked name (.id/.noid).
    folder_path: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The exact DocumentInstance ids the server created for this submission (JSON list) and the artist,
    # written at submit time. The retry records the stored URL on these records only.
    document_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    artist_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
