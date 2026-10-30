"""add Booky artist policy and collector consent snapshots

Revision ID: 20260820_booky_readiness
Revises: 20260819_flash_feedback

Net-new artifact justification:
Existing user and booking rows are the canonical owners for artist scheduling
policy and submission consent. This migration extends those owners with safe
defaults instead of creating a parallel Booky settings or consent table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.services.session_type_defaults import seed_default_session_types

revision: str = "20260820_booky_readiness"
down_revision: str | Sequence[str] | None = "20260819_flash_feedback"
branch_labels = None
depends_on = None

COURIER_ATTEMPT_INDEX = "ix_bttco_email_log_courier_message_attempt"


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _add_index_if_missing(
    table_name: str,
    index_name: str,
    columns: list[str],
) -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return
    if index_name not in {index["name"] for index in inspector.get_indexes(table_name)}:
        op.create_index(index_name, table_name, columns, unique=False)


def upgrade() -> None:
    _add_column_if_missing(
        "bttco_users",
        sa.Column("booky_mode", sa.String(length=24), nullable=False, server_default="manual"),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("booky_opted_in_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "bttco_bookings",
        sa.Column("booky_consent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    _add_column_if_missing(
        "bttco_bookings",
        sa.Column("booky_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "bttco_bookings",
        sa.Column("booky_consent_policy_version", sa.String(length=64), nullable=True),
    )
    _add_column_if_missing(
        "bttco_bookings",
        sa.Column("booky_collector_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "bttco_bookings",
        sa.Column("booky_capability_inquiry_id", sa.String(length=100), nullable=True),
    )
    _add_column_if_missing(
        "bttco_email_log",
        sa.Column("courier_attempt", sa.Integer(), nullable=True),
    )
    _add_index_if_missing(
        "bttco_email_log",
        COURIER_ATTEMPT_INDEX,
        ["courier_message_id", "courier_attempt"],
    )
    seed_default_session_types(op.get_bind())


def downgrade() -> None:
    # Consent, account verification, first opt-in, and lifecycle evidence are
    # audit records. A rollback disables automation without destroying them.
    if _has_column("bttco_users", "booky_mode"):
        op.execute(sa.text("UPDATE bttco_users SET booky_mode = 'manual'"))
