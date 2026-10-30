"""add artist pay structure modes and guest classification

Revision ID: 20260818_artist_pay
Revises: 20260818_contractor_contrib

Net-new artifact justification:
The current artist row already owns commission and booth-rent terms, but it
cannot distinguish a guest artist or the two Stripe booth-payment contracts.
This migration extends that owner and preserves every existing live term.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_artist_pay"
down_revision: str | Sequence[str] | None = "20260818_contractor_contrib"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("bttco_users", "is_guest_artist"):
        op.add_column(
            "bttco_users",
            sa.Column("is_guest_artist", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if _has_column("bttco_users", "booth_rent_payment_method"):
        op.execute(
            "UPDATE bttco_users SET booth_rent_payment_method = 'autodraft' WHERE booth_rent_payment_method = 'stripe'"
        )
        op.alter_column(
            "bttco_users",
            "booth_rent_payment_method",
            existing_type=sa.String(length=16),
            server_default="cash",
            existing_nullable=False,
        )


def downgrade() -> None:
    if _has_column("bttco_users", "booth_rent_payment_method"):
        op.execute(
            "UPDATE bttco_users SET booth_rent_payment_method = 'stripe' "
            "WHERE booth_rent_payment_method IN ('one_time', 'autodraft')"
        )
        op.alter_column(
            "bttco_users",
            "booth_rent_payment_method",
            existing_type=sa.String(length=16),
            server_default="stripe",
            existing_nullable=False,
        )
    if _has_column("bttco_users", "is_guest_artist"):
        op.drop_column("bttco_users", "is_guest_artist")
