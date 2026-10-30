"""create Hustle E-con finance tables

E-con needs durable provider state before Plaid, Clover, and Mercury can become
more than live reads. These tables store connection status, sync audits, and
normalized ledger entries while keeping raw connector secrets in env or vault
references instead of application rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260613_econ_finance"
down_revision: str | Sequence[str] | None = "20260603_add_pinterest_oauth"
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
    if not _has_table("bttco_econ_connections"):
        op.create_table(
            "bttco_econ_connections",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("display_name", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("environment", sa.String(length=32), nullable=True),
            sa.Column("external_account_id", sa.String(length=255), nullable=True),
            sa.Column("auth_secret_ref", sa.String(length=255), nullable=True),
            sa.Column("sync_cursor", sa.Text(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("provider", "owner_user_id", name="uq_econ_connection_provider_owner"),
        )
    _create_index_if_missing("ix_bttco_econ_connections_provider", "bttco_econ_connections", ["provider"])
    _create_index_if_missing(
        "ix_bttco_econ_connections_owner_user_id",
        "bttco_econ_connections",
        ["owner_user_id"],
    )

    if not _has_table("bttco_econ_sync_runs"):
        op.create_table(
            "bttco_econ_sync_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="started"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("added_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("modified_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("removed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    _create_index_if_missing("ix_bttco_econ_sync_runs_provider", "bttco_econ_sync_runs", ["provider"])
    _create_index_if_missing("ix_bttco_econ_sync_runs_connection_id", "bttco_econ_sync_runs", ["connection_id"])

    if not _has_table("bttco_econ_ledger_entries"):
        op.create_table(
            "bttco_econ_ledger_entries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("external_id", sa.String(length=255), nullable=False),
            sa.Column("entry_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="posted"),
            sa.Column("amount_cents", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("counterparty", sa.String(length=255), nullable=True),
            sa.Column("category", sa.String(length=120), nullable=True),
            sa.Column("payroll_tax_bucket", sa.String(length=64), nullable=True),
            sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("provider", "external_id", name="uq_econ_ledger_provider_external_id"),
        )
    _create_index_if_missing("ix_bttco_econ_ledger_entries_provider", "bttco_econ_ledger_entries", ["provider"])
    _create_index_if_missing(
        "ix_bttco_econ_ledger_entries_connection_id",
        "bttco_econ_ledger_entries",
        ["connection_id"],
    )
    _create_index_if_missing(
        "ix_bttco_econ_ledger_entries_occurred_at",
        "bttco_econ_ledger_entries",
        ["occurred_at"],
    )
    _create_index_if_missing("ix_bttco_econ_ledger_entries_artist_id", "bttco_econ_ledger_entries", ["artist_id"])


def downgrade() -> None:
    _drop_index_if_exists("ix_bttco_econ_ledger_entries_artist_id", "bttco_econ_ledger_entries")
    _drop_index_if_exists("ix_bttco_econ_ledger_entries_occurred_at", "bttco_econ_ledger_entries")
    _drop_index_if_exists("ix_bttco_econ_ledger_entries_connection_id", "bttco_econ_ledger_entries")
    _drop_index_if_exists("ix_bttco_econ_ledger_entries_provider", "bttco_econ_ledger_entries")
    if _has_table("bttco_econ_ledger_entries"):
        op.drop_table("bttco_econ_ledger_entries")
    _drop_index_if_exists("ix_bttco_econ_sync_runs_connection_id", "bttco_econ_sync_runs")
    _drop_index_if_exists("ix_bttco_econ_sync_runs_provider", "bttco_econ_sync_runs")
    if _has_table("bttco_econ_sync_runs"):
        op.drop_table("bttco_econ_sync_runs")
    _drop_index_if_exists("ix_bttco_econ_connections_owner_user_id", "bttco_econ_connections")
    _drop_index_if_exists("ix_bttco_econ_connections_provider", "bttco_econ_connections")
    if _has_table("bttco_econ_connections"):
        op.drop_table("bttco_econ_connections")
