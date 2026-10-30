"""add membership and entitlement fields to users

No subscription or entitlement schema existed in BTTCO to extend, so this
revision establishes the first durable tier contract on `bttco_users`. It is
intentionally narrow so portal gating and AI routing can ship before Stripe
recurring billing is layered in.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260528_add_membership_tiers"
down_revision: str | Sequence[str] | None = "20260522b_sms_template_owner"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing(
        "bttco_users",
        sa.Column("membership_tier", sa.String(length=50), nullable=False, server_default="freemium"),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("membership_status", sa.String(length=50), nullable=False, server_default="active"),
    )
    _add_column_if_missing("bttco_users", sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))
    _add_column_if_missing(
        "bttco_users",
        sa.Column("ai_monthly_message_cap", sa.Integer(), nullable=False, server_default="40"),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("early_access_galax3", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("connected_apps_unlocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column(
            "product_entitlements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    if not _has_index("bttco_users", "ix_bttco_users_membership_tier"):
        op.create_index("ix_bttco_users_membership_tier", "bttco_users", ["membership_tier"], unique=False)


def downgrade() -> None:
    if _has_index("bttco_users", "ix_bttco_users_membership_tier"):
        op.drop_index("ix_bttco_users_membership_tier", table_name="bttco_users")
    for column_name in (
        "product_entitlements",
        "connected_apps_unlocked",
        "early_access_galax3",
        "ai_monthly_message_cap",
        "stripe_customer_id",
        "membership_status",
        "membership_tier",
    ):
        if _has_column("bttco_users", column_name):
            op.drop_column("bttco_users", column_name)
