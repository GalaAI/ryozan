"""Record public scroll PDF objects awaiting deletion after the move to private storage.

The boot move (scroll_archive.move_public_scroll_pdfs) writes a pending row in the same commit as the
reference rewrite, before any delete, so a delete R2 does not confirm, or one a stopped process never
ran, is retried until confirmed instead of leaving a signature-bearing PDF public with nothing able to
rediscover it.

Revision ID: 20261110_scroll_public_pdf_del
Revises: 20261110_scroll_outbox_targets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_scroll_public_pdf_del"
down_revision: str | None = "20261110_scroll_outbox_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "ryozan_scroll_public_pdf_deletions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False, unique=True),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("ignore_document_ids", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(f"ix_{TABLE}_status", TABLE, ["status"])
    op.create_index(f"ix_{TABLE}_next_attempt_at", TABLE, ["next_attempt_at"])


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_next_attempt_at", table_name=TABLE)
    op.drop_index(f"ix_{TABLE}_status", table_name=TABLE)
    op.drop_table(TABLE)
