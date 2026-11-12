"""Owner email outbox rows record the exact records a held scroll PDF belongs to.

When storing a scroll PDF fails, its bytes are held on the owner email outbox rows until a retry
stores them. The retry used to find the records to update by category and a submission id inside
client-writable data_json. submit_scroll now records the exact document ids it created (and the
artist id) on the row, and the retry writes the stored PDF URL onto those records only. NULL on rows
queued before this revision; those resolve targets only through trusted relationships.

Revision ID: 20261110_scroll_outbox_targets
Revises: 20261102_studio_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261110_scroll_outbox_targets"
down_revision: str | None = "20261102_studio_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ryozan_scroll_owner_email_outbox", sa.Column("document_ids", sa.Text(), nullable=True))
    op.add_column("ryozan_scroll_owner_email_outbox", sa.Column("artist_user_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("ryozan_scroll_owner_email_outbox", "artist_user_id")
    op.drop_column("ryozan_scroll_owner_email_outbox", "document_ids")
