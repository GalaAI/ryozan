"""add plaid_access_token to bttco_users

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bttco_users",
        sa.Column("plaid_access_token", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bttco_users", "plaid_access_token")
