"""bind public slot holds to their booking kind

Revision ID: 20260814_huddl_hold_kind
Revises: 20260814_booking_payment

Net-new artifact justification:
The existing slot-hold table is the correct concurrency owner, but it does not
record whether a hold was verified for an immediate consultation. Extending it
prevents a standard tattoo hold from being replayed through Huddl confirmation.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_huddl_hold_kind"
down_revision: str | Sequence[str] | None = "20260814_booking_payment"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("bttco_booking_slot_holds", "booking_kind"):
        op.add_column(
            "bttco_booking_slot_holds",
            sa.Column(
                "booking_kind",
                sa.String(length=50),
                nullable=False,
                server_default="tattoo_session",
            ),
        )


def downgrade() -> None:
    if _has_column("bttco_booking_slot_holds", "booking_kind"):
        op.drop_column("bttco_booking_slot_holds", "booking_kind")
