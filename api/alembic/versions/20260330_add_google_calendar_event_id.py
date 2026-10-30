"""add google_calendar_event_id to bttco_bookings

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bttco_bookings",
        sa.Column("google_calendar_event_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bttco_bookings", "google_calendar_event_id")
