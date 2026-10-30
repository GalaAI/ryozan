"""Fail-closed mailbox ownership for email-linked collector records."""

import sqlalchemy as sa

from alembic import op

revision = "20261001_email_ownership"
down_revision = "20260922_byof_staged"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical passwords, reset redemption and Google links are not proof:
    # public setup tokens existed and linked Google addresses can differ.
    op.add_column("bttco_users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("bttco_users", "email_verified_at")
