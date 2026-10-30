"""add source column to bttco_bookings

Revision ID: 20260506_add_booking_source
Revises: 20260426_add_proxy_for
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa

revision = "20260506_add_booking_source"
down_revision = "20260426_add_proxy_for"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bttco_bookings",
        sa.Column("source", sa.String(50), nullable=False, server_default="bttco"),
    )


def downgrade() -> None:
    op.drop_column("bttco_bookings", "source")
