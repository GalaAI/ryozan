"""create booking lifecycle events

Booking lifecycle events are a net-new Huddl audit boundary, separate from
Courier messages and mutable booking notes. They must capture scheduler events
that happen before a conversation exists, then continue as an append-only
booking timeline for staff review and connector debugging.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260614_booking_events"
down_revision: str | Sequence[str] | None = "20260614_booking_sms_consent"
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
    if not _has_table("bttco_booking_lifecycle_events"):
        op.create_table(
            "bttco_booking_lifecycle_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=False, server_default="system"),
            sa.Column("reference_code", sa.String(length=40), nullable=True),
            sa.Column("hold_token", sa.String(length=128), nullable=True),
            sa.Column("guest_email", sa.String(length=255), nullable=True),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["actor_id"], ["bttco_users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["booking_id"], ["bttco_bookings.id"], ondelete="CASCADE"),
        )

    _create_index_if_missing("ix_bttco_booking_lifecycle_events_booking_id", "bttco_booking_lifecycle_events", ["booking_id"])
    _create_index_if_missing("ix_bttco_booking_lifecycle_events_artist_id", "bttco_booking_lifecycle_events", ["artist_id"])
    _create_index_if_missing("ix_bttco_booking_lifecycle_events_event_type", "bttco_booking_lifecycle_events", ["event_type"])
    _create_index_if_missing("ix_bttco_booking_lifecycle_events_reference_code", "bttco_booking_lifecycle_events", ["reference_code"])
    _create_index_if_missing("ix_bttco_booking_lifecycle_events_hold_token", "bttco_booking_lifecycle_events", ["hold_token"])
    _create_index_if_missing("ix_bttco_booking_lifecycle_events_guest_email", "bttco_booking_lifecycle_events", ["guest_email"])
    _create_index_if_missing("ix_bttco_booking_lifecycle_events_created_at", "bttco_booking_lifecycle_events", ["created_at"])


def downgrade() -> None:
    _drop_index_if_exists("ix_bttco_booking_lifecycle_events_created_at", "bttco_booking_lifecycle_events")
    _drop_index_if_exists("ix_bttco_booking_lifecycle_events_guest_email", "bttco_booking_lifecycle_events")
    _drop_index_if_exists("ix_bttco_booking_lifecycle_events_hold_token", "bttco_booking_lifecycle_events")
    _drop_index_if_exists("ix_bttco_booking_lifecycle_events_reference_code", "bttco_booking_lifecycle_events")
    _drop_index_if_exists("ix_bttco_booking_lifecycle_events_event_type", "bttco_booking_lifecycle_events")
    _drop_index_if_exists("ix_bttco_booking_lifecycle_events_artist_id", "bttco_booking_lifecycle_events")
    _drop_index_if_exists("ix_bttco_booking_lifecycle_events_booking_id", "bttco_booking_lifecycle_events")
    if _has_table("bttco_booking_lifecycle_events"):
        op.drop_table("bttco_booking_lifecycle_events")
