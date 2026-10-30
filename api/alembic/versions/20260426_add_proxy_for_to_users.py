"""add proxy_for to bttco_users

Revision ID: 20260426_add_proxy_for
Revises: 20260426_rename_misc_and_create_response_templates
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260426_add_proxy_for"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bttco_users",
        sa.Column(
            "proxy_for",
            UUID(as_uuid=True),
            sa.ForeignKey("bttco_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("bttco_users", "proxy_for")
