"""track POS commission base and customer price adjustment

Revision ID: 20260819_pos_price
Revises: 20260818_booth_due

Net-new artifact justification:
The existing POS transaction ledger stores only gross collected value, which
cannot preserve artist commission against a lower configured service price.
These columns extend that ledger rather than creating a parallel finance owner.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260819_pos_price"
down_revision: str | Sequence[str] | None = "20260818_booth_due"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("bttco_pos_transactions", "commission_base_cents"):
        op.add_column(
            "bttco_pos_transactions",
            sa.Column("commission_base_cents", sa.Integer(), nullable=False, server_default="0"),
        )
        op.execute("UPDATE bttco_pos_transactions SET commission_base_cents = amount_cents")
    if not _has_column("bttco_pos_transactions", "price_adjustment_cents"):
        op.add_column(
            "bttco_pos_transactions",
            sa.Column("price_adjustment_cents", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if _has_column("bttco_pos_transactions", "price_adjustment_cents"):
        op.drop_column("bttco_pos_transactions", "price_adjustment_cents")
    if _has_column("bttco_pos_transactions", "commission_base_cents"):
        op.drop_column("bttco_pos_transactions", "commission_base_cents")
