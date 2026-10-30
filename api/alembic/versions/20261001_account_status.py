"""Nonsecret account status; no inferred history or credential reads."""

import sqlalchemy as sa

from alembic import op

revision = "20261001_account_status"
down_revision = "20261001_email_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, kind in (
        ("password_state", sa.String(24)),
        ("password_changed_at", sa.DateTime(timezone=True)),
        ("password_change_method", sa.String(16)),
        ("last_login_at", sa.DateTime(timezone=True)),
        ("last_login_method", sa.String(16)),
    ):
        op.add_column("bttco_users", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name in (
        "last_login_method",
        "last_login_at",
        "password_change_method",
        "password_changed_at",
        "password_state",
    ):
        op.drop_column("bttco_users", name)
