"""Hold scroll PDFs whose upload failed until a retry stores them, whether or not a founder exists.

Owner email outbox rows exist only per founder, so a shop with no founder lost the only copy of a
scroll PDF whose store failed. The bytes now go to their own table in the submission transaction,
with the exact record ids to update, and a backed-off retry stores them privately.

Revision ID: 20261110_scroll_pdf_pending
Revises: 20261110_scroll_public_pdf_del
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_scroll_pdf_pending"
down_revision: str | None = "20261110_scroll_public_pdf_del"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "ryozan_scroll_pdf_pending_uploads"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("submission_id", sa.String(64), nullable=False, unique=True),
        sa.Column("folder_path", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("document_ids", sa.Text(), nullable=True),
        sa.Column("artist_user_id", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(f"ix_{TABLE}_next_attempt_at", TABLE, ["next_attempt_at"])


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_next_attempt_at", table_name=TABLE)
    op.drop_table(TABLE)
