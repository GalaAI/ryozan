"""add contractor contribution and cash booth-rent state

Revision ID: 20260818_contractor_contrib
Revises: 20260818_booth_rent_billing

Net-new artifact justification:
The existing booth-rent columns own the obligation but assume every renter
uses Stripe Billing. A payment-method field extends that same contract so cash
rent can be tracked without creating a second artist or billing identity.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_contractor_contrib"
down_revision: str | Sequence[str] | None = "20260818_booth_rent_billing"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("bttco_users", "booth_rent_payment_method"):
        op.add_column(
            "bttco_users",
            sa.Column(
                "booth_rent_payment_method",
                sa.String(length=16),
                nullable=False,
                server_default="stripe",
            ),
        )


def downgrade() -> None:
    if _has_column("bttco_users", "booth_rent_payment_method"):
        op.drop_column("bttco_users", "booth_rent_payment_method")
