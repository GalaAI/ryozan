"""add artist booth-rent billing state

Revision ID: 20260818_booth_rent_billing
Revises: 20260816_workspace_calendar

Net-new artifact justification:
The existing user record owns both artist revenue configuration and the Stripe
customer link, but it cannot represent a booth-rent obligation or subscription.
These columns extend that owner without creating a parallel billing identity.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_booth_rent_billing"
down_revision: str | Sequence[str] | None = "20260816_workspace_calendar"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    columns = (
        sa.Column("booth_rent_amount_cents", sa.Integer(), nullable=True),
        sa.Column("booth_rent_interval", sa.String(length=16), nullable=True),
        sa.Column(
            "booth_rent_status",
            sa.String(length=24),
            nullable=False,
            server_default="not_configured",
        ),
        sa.Column("booth_rent_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("booth_rent_checkout_session_id", sa.String(length=255), nullable=True),
        sa.Column("booth_rent_current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "booth_rent_cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    for column in columns:
        if not _has_column("bttco_users", column.name):
            op.add_column("bttco_users", column)

    index_name = "ux_bttco_users_booth_rent_subscription_id"
    if not _has_index("bttco_users", index_name):
        op.create_index(
            index_name,
            "bttco_users",
            ["booth_rent_subscription_id"],
            unique=True,
            postgresql_where=sa.text("booth_rent_subscription_id IS NOT NULL"),
        )


def downgrade() -> None:
    index_name = "ux_bttco_users_booth_rent_subscription_id"
    if _has_index("bttco_users", index_name):
        op.drop_index(index_name, table_name="bttco_users")

    for column_name in (
        "booth_rent_cancel_at_period_end",
        "booth_rent_current_period_end",
        "booth_rent_checkout_session_id",
        "booth_rent_subscription_id",
        "booth_rent_status",
        "booth_rent_interval",
        "booth_rent_amount_cents",
    ):
        if _has_column("bttco_users", column_name):
            op.drop_column("bttco_users", column_name)
