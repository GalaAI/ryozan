"""create public booking slot holds

Slot holds are a net-new scheduler boundary, separate from booking drafts
and Courier slot proposals. Drafts recover form state, proposals are staff
message artifacts, and this table prevents public self-booking races while
clients finish intake.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260614_slot_holds"
down_revision: str | Sequence[str] | None = "20260613_econ_finance"
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
    if not _has_table("bttco_booking_slot_holds"):
        op.create_table(
            "bttco_booking_slot_holds",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("hold_token", sa.String(length=64), nullable=False),
            sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("artist_slug", sa.String(length=100), nullable=False),
            sa.Column("scheduled_date", sa.Date(), nullable=False),
            sa.Column("scheduled_time", sa.String(length=5), nullable=False),
            sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="120"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
            sa.Column("inquiry_id", sa.String(length=100), nullable=True),
            sa.Column("guest_email", sa.String(length=255), nullable=True),
            sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["booking_id"], ["bttco_bookings.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("hold_token", name="uq_bttco_booking_slot_holds_token"),
        )
    _create_index_if_missing("ix_bttco_booking_slot_holds_token", "bttco_booking_slot_holds", ["hold_token"])
    _create_index_if_missing("ix_bttco_booking_slot_holds_artist_slug", "bttco_booking_slot_holds", ["artist_slug"])
    _create_index_if_missing(
        "ix_bttco_booking_slot_holds_scheduled_date",
        "bttco_booking_slot_holds",
        ["scheduled_date"],
    )
    _create_index_if_missing("ix_bttco_booking_slot_holds_status", "bttco_booking_slot_holds", ["status"])
    _create_index_if_missing("ix_bttco_booking_slot_holds_inquiry_id", "bttco_booking_slot_holds", ["inquiry_id"])
    _create_index_if_missing("ix_bttco_booking_slot_holds_guest_email", "bttco_booking_slot_holds", ["guest_email"])
    _create_index_if_missing("ix_bttco_booking_slot_holds_expires_at", "bttco_booking_slot_holds", ["expires_at"])
    _create_index_if_missing(
        "ix_bttco_booking_slot_holds_active_lookup",
        "bttco_booking_slot_holds",
        ["artist_id", "scheduled_date", "status", "expires_at"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_bttco_booking_slot_holds_active_lookup", "bttco_booking_slot_holds")
    _drop_index_if_exists("ix_bttco_booking_slot_holds_expires_at", "bttco_booking_slot_holds")
    _drop_index_if_exists("ix_bttco_booking_slot_holds_guest_email", "bttco_booking_slot_holds")
    _drop_index_if_exists("ix_bttco_booking_slot_holds_inquiry_id", "bttco_booking_slot_holds")
    _drop_index_if_exists("ix_bttco_booking_slot_holds_status", "bttco_booking_slot_holds")
    _drop_index_if_exists("ix_bttco_booking_slot_holds_scheduled_date", "bttco_booking_slot_holds")
    _drop_index_if_exists("ix_bttco_booking_slot_holds_artist_slug", "bttco_booking_slot_holds")
    _drop_index_if_exists("ix_bttco_booking_slot_holds_token", "bttco_booking_slot_holds")
    if _has_table("bttco_booking_slot_holds"):
        op.drop_table("bttco_booking_slot_holds")
