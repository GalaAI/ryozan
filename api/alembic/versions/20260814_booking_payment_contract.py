"""persist booking payment eligibility metadata

Net-new artifact justification:
An older rescue branch contains the same consultation columns, but its migration
is based on a retired 2026-07 schema head and cannot be applied to current main.
This current-head migration preserves that reviewed contract and adds no
parallel table or payment system.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_booking_payment"
down_revision: str | Sequence[str] | None = "20260810_merge_email_heads"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("bttco_bookings", "booking_kind"):
        op.add_column(
            "bttco_bookings",
            sa.Column("booking_kind", sa.String(length=50), nullable=False, server_default="tattoo_session"),
        )
    if not _has_column("bttco_bookings", "consultation_method"):
        op.add_column("bttco_bookings", sa.Column("consultation_method", sa.String(length=50), nullable=True))


def downgrade() -> None:
    if _has_column("bttco_bookings", "consultation_method"):
        op.drop_column("bttco_bookings", "consultation_method")
    if _has_column("bttco_bookings", "booking_kind"):
        op.drop_column("bttco_bookings", "booking_kind")
