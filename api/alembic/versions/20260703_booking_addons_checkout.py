"""create booking add-ons and public checkout metadata

Net-new artifact justification:
Booking add-ons were previously static frontend options, so staff could not
configure them and booking submissions had no structured selection record.
This migration adds the minimum durable add-on tables and leaves checkout
events on the existing booking lifecycle ledger.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260703_booking_addons"
down_revision: str | Sequence[str] | None = "bd59beb36970"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
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
    if not _has_table("bttco_booking_addons"):
        op.create_table(
            "bttco_booking_addons",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("price_amount", sa.Numeric(10, 2), nullable=True),
            sa.Column("duration_minutes", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"], ondelete="CASCADE"),
        )

    if not _has_table("bttco_booking_addon_selections"):
        op.create_table(
            "bttco_booking_addon_selections",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("add_on_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("price_amount", sa.Numeric(10, 2), nullable=True),
            sa.Column("duration_minutes", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["booking_id"], ["bttco_bookings.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["add_on_id"], ["bttco_booking_addons.id"], ondelete="SET NULL"),
        )

    _create_index_if_missing("ix_bttco_booking_addons_artist_id", "bttco_booking_addons", ["artist_id"])
    _create_index_if_missing(
        "ix_bttco_booking_addons_artist_active",
        "bttco_booking_addons",
        ["artist_id", "is_active"],
    )
    _create_index_if_missing(
        "ix_bttco_booking_addon_selections_booking_id",
        "bttco_booking_addon_selections",
        ["booking_id"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_bttco_booking_addon_selections_booking_id", "bttco_booking_addon_selections")
    _drop_index_if_exists("ix_bttco_booking_addons_artist_active", "bttco_booking_addons")
    _drop_index_if_exists("ix_bttco_booking_addons_artist_id", "bttco_booking_addons")
    if _has_table("bttco_booking_addon_selections"):
        op.drop_table("bttco_booking_addon_selections")
    if _has_table("bttco_booking_addons"):
        op.drop_table("bttco_booking_addons")
