"""add durable Artist Scroll owner email outbox

Revision ID: 20260810_scroll_email_outbox
Revises: 20260805_session_pricing_buffer
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260810_scroll_email_outbox"
down_revision = "20260805_session_pricing_buffer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bttco_scroll_owner_email_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "founder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bttco_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submission_id", sa.String(64), nullable=False),
        sa.Column("to_email", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("attachment_filename", sa.String(255), nullable=True),
        sa.Column("attachment_content", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_scroll_owner_email_outbox_status_next",
        "bttco_scroll_owner_email_outbox",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_scroll_owner_email_outbox_submission_id",
        "bttco_scroll_owner_email_outbox",
        ["submission_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_scroll_owner_email_outbox_submission_id", table_name="bttco_scroll_owner_email_outbox")
    op.drop_index("ix_scroll_owner_email_outbox_status_next", table_name="bttco_scroll_owner_email_outbox")
    op.drop_table("bttco_scroll_owner_email_outbox")
