"""add artist booking scheduling rules

These columns extend the existing artist/user scheduling contract instead
of introducing a separate settings table. They persist Acuity-style booking
rules used by the public scheduler, Huddl diagnostics, and future GalaXEv3
booking assistance while preserving existing defaults for every artist.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260614_artist_booking_rules"
down_revision: str | Sequence[str] | None = "20260614_slot_holds"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing(
        "bttco_users",
        sa.Column("booking_slot_interval_minutes", sa.Integer(), nullable=False, server_default="30"),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("booking_buffer_minutes", sa.Integer(), nullable=False, server_default="30"),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("booking_min_notice_hours", sa.Integer(), nullable=False, server_default="24"),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("booking_max_advance_days", sa.Integer(), nullable=False, server_default="180"),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("booking_allow_same_day", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    for column_name in (
        "booking_allow_same_day",
        "booking_max_advance_days",
        "booking_min_notice_hours",
        "booking_buffer_minutes",
        "booking_slot_interval_minutes",
    ):
        if _has_column("bttco_users", column_name):
            op.drop_column("bttco_users", column_name)
