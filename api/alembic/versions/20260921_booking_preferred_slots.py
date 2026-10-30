"""Store collector-proposed preferred slots on bookings.

Revision ID: 20260921_preferred_slots
Revises: 20260911_gift_delivery

Custom pieces, consultations and bring-your-own-flash requests let the
collector propose 1 to 3 date/times with no hold and no deposit. The booking
row is the durable owner of that request, so this extends bttco_bookings with a
nullable JSONB list instead of adding a second proposal table. Flash designs
keep using a real held slot and never populate this column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260921_preferred_slots"
down_revision: str | None = "20260911_gift_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bttco_bookings",
        sa.Column("preferred_slots_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bttco_bookings", "preferred_slots_json")
