"""create personal time blocks

Personal time blocks are a net-new scheduler boundary: staff-owned unavailable
windows (lunch, personal time, days off) that the availability engine treats as
busy intervals alongside Google busy blocks, confirmed bookings, and slot holds.
Separate from slot holds (public race protection) and bookings (client work).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260627_time_blocks"
down_revision: str | Sequence[str] | None = "20260622_session_rates"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    if not _has_table("bttco_time_blocks"):
        op.create_table(
            "bttco_time_blocks",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("block_date", sa.Date(), nullable=False),
            sa.Column("start_time", sa.String(length=5), nullable=True),
            sa.Column("end_time", sa.String(length=5), nullable=True),
            sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("google_calendar_event_id", sa.String(length=255), nullable=True),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["bttco_users.id"], ondelete="SET NULL"),
        )
    _create_index_if_missing("ix_bttco_time_blocks_artist_id", "bttco_time_blocks", ["artist_id"])
    _create_index_if_missing("ix_bttco_time_blocks_block_date", "bttco_time_blocks", ["block_date"])
    _create_index_if_missing(
        "ix_bttco_time_blocks_artist_date",
        "bttco_time_blocks",
        ["artist_id", "block_date"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_bttco_time_blocks_artist_date", "bttco_time_blocks")
    _drop_index_if_exists("ix_bttco_time_blocks_block_date", "bttco_time_blocks")
    _drop_index_if_exists("ix_bttco_time_blocks_artist_id", "bttco_time_blocks")
    if _has_table("bttco_time_blocks"):
        op.drop_table("bttco_time_blocks")
