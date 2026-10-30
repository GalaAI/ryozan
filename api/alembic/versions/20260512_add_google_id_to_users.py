"""add google_id column to bttco_users

Revision ID: 20260512_add_google_id
Revises: 20260506_add_booking_source, e5f6a7b8c9d0
Create Date: 2026-05-12
"""

from alembic import op
import sqlalchemy as sa

revision = "20260512_add_google_id"
down_revision = ("20260506_add_booking_source", "e5f6a7b8c9d0")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bttco_users",
        sa.Column("google_id", sa.String(255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_bttco_users_google_id",
        "bttco_users",
        ["google_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_bttco_users_google_id", "bttco_users", type_="unique")
    op.drop_column("bttco_users", "google_id")
