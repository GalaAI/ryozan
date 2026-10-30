"""add provider event idempotency to booking lifecycle events

This extends the existing booking lifecycle ledger instead of creating a
parallel Stripe receipt table. The nullable unique key is claimed in the same
transaction as booking and Huddl task changes, so failed processing can retry.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260727_stripe_events"
down_revision: str | Sequence[str] | None = "20260703_booking_addons"
branch_labels = None
depends_on = None

_TABLE = "bttco_booking_lifecycle_events"
_INDEX = "ix_bttco_booking_lifecycle_events_provider_event_id"


def _has_column(column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(_TABLE)}


def _has_index(index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(_TABLE)}


def upgrade() -> None:
    if not _has_column("provider_event_id"):
        op.add_column(_TABLE, sa.Column("provider_event_id", sa.String(length=255), nullable=True))
    if not _has_index(_INDEX):
        op.create_index(_INDEX, _TABLE, ["provider_event_id"], unique=True)


def downgrade() -> None:
    if _has_index(_INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _has_column("provider_event_id"):
        op.drop_column(_TABLE, "provider_event_id")
