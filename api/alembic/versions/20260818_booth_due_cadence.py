"""add booth-rent due dates and biweekly cadence

Revision ID: 20260818_booth_due
Revises: 20260818_artist_pay

Net-new artifact justification:
The artist row already owns booth-rent terms but has no durable date for when
the next installment is owed. This migration extends that existing owner and
normalizes the unused weekly value to the new biweekly contract.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_booth_due"
down_revision: str | Sequence[str] | None = "20260818_artist_pay"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("bttco_users", "booth_rent_next_due_date"):
        op.add_column("bttco_users", sa.Column("booth_rent_next_due_date", sa.Date(), nullable=True))
    if _has_column("bttco_users", "booth_rent_interval"):
        op.execute("UPDATE bttco_users SET booth_rent_interval = 'biweekly' WHERE booth_rent_interval = 'week'")


def downgrade() -> None:
    if _has_column("bttco_users", "booth_rent_interval"):
        op.execute("UPDATE bttco_users SET booth_rent_interval = 'week' WHERE booth_rent_interval = 'biweekly'")
    if _has_column("bttco_users", "booth_rent_next_due_date"):
        op.drop_column("bttco_users", "booth_rent_next_due_date")
