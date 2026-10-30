"""add flash artist settings to bttco_users

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bttco_users",
        sa.Column("flash_deposit_amount", sa.Numeric(8, 2), nullable=True, server_default="100.00"),
    )
    op.add_column(
        "bttco_users",
        sa.Column("allow_prepay_full", sa.Boolean, nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("bttco_users", "allow_prepay_full")
    op.drop_column("bttco_users", "flash_deposit_amount")
