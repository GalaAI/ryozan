"""add artist session type rates and buffers

Net-new artifact justification:
The ArtistSessionType model and CRUD endpoint already exist, but this migration is missing from the tracked Alembic graph and the current Huddl work needs durable rate and per-session buffer fields.
This migration is deliberately idempotent so it can create the session type table on databases that never received it, or only add the new columns when the table already exists.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260622_session_rates"
down_revision: str | Sequence[str] | None = "20260614_booking_events"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    if not _has_table("bttco_artist_session_types"):
        op.create_table(
            "bttco_artist_session_types",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False),
            sa.Column("size", sa.String(length=50), nullable=False),
            sa.Column("placements", postgresql.ARRAY(sa.String()), nullable=True),
            sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="120"),
            sa.Column("buffer_minutes", sa.Integer(), nullable=True),
            sa.Column("deposit_amount", sa.Numeric(10, 2), nullable=False, server_default="100.00"),
            sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=True),
            sa.Column("price_min", sa.Numeric(10, 2), nullable=True),
            sa.Column("price_max", sa.Numeric(10, 2), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"], ondelete="CASCADE"),
        )
    else:
        _add_column_if_missing("bttco_artist_session_types", sa.Column("buffer_minutes", sa.Integer(), nullable=True))
        _add_column_if_missing("bttco_artist_session_types", sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=True))

    _create_index_if_missing(
        "ix_bttco_artist_session_types_artist_id",
        "bttco_artist_session_types",
        ["artist_id"],
    )
    _create_index_if_missing(
        "ix_bttco_artist_session_types_active",
        "bttco_artist_session_types",
        ["is_active"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_bttco_artist_session_types_active", "bttco_artist_session_types")
    _drop_index_if_exists("ix_bttco_artist_session_types_artist_id", "bttco_artist_session_types")
    if _has_table("bttco_artist_session_types"):
        if _has_column("bttco_artist_session_types", "hourly_rate"):
            op.drop_column("bttco_artist_session_types", "hourly_rate")
        if _has_column("bttco_artist_session_types", "buffer_minutes"):
            op.drop_column("bttco_artist_session_types", "buffer_minutes")
