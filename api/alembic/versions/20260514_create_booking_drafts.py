"""create booking draft recovery table

Revision ID: 20260514_create_booking_drafts
Revises: 20260512_add_google_id
Create Date: 2026-05-14
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260514_create_booking_drafts"
down_revision = "20260512_add_google_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bttco_booking_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("inquiry_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("artist_slug", sa.String(100), nullable=True),
        sa.Column("guest_name", sa.String(255), nullable=True),
        sa.Column("guest_email", sa.String(255), nullable=True),
        sa.Column("guest_phone", sa.String(50), nullable=True),
        sa.Column("form_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("image_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reference_code", sa.String(20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint("uq_bttco_booking_drafts_inquiry_id", "bttco_booking_drafts", ["inquiry_id"])
    op.create_index("ix_bttco_booking_drafts_inquiry_id", "bttco_booking_drafts", ["inquiry_id"])
    op.create_index("ix_bttco_booking_drafts_guest_email", "bttco_booking_drafts", ["guest_email"])
    op.create_index("ix_bttco_booking_drafts_reference_code", "bttco_booking_drafts", ["reference_code"])


def downgrade() -> None:
    op.drop_index("ix_bttco_booking_drafts_reference_code", table_name="bttco_booking_drafts")
    op.drop_index("ix_bttco_booking_drafts_guest_email", table_name="bttco_booking_drafts")
    op.drop_index("ix_bttco_booking_drafts_inquiry_id", table_name="bttco_booking_drafts")
    op.drop_constraint("uq_bttco_booking_drafts_inquiry_id", "bttco_booking_drafts", type_="unique")
    op.drop_table("bttco_booking_drafts")
