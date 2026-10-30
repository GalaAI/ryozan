"""create Viber account + connection tables

Viber is the vibe-coder dev cockpit (devtool/VIBER_SPEC.md). Its accounts and
connections live in nexus but model Viber's own identity, connecting OUT to
project backends (BTTCO API first) as an extension. Connections carry a
capability gate (read | read_write); writes pass approval gates per the Axiom
Delivery Flow canon.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260602b_create_viber"
down_revision: str | Sequence[str] | None = "20260602_create_workspaces"
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


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    if not _has_table("viber_accounts"):
        op.create_table(
            "viber_accounts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("plan", sa.String(length=24), nullable=False, server_default="founder"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    _create_index_if_missing("ix_viber_accounts_email", "viber_accounts", ["email"], unique=True)

    if not _has_table("viber_connections"):
        op.create_table(
            "viber_connections",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="bttco_api"),
            sa.Column("base_url", sa.String(length=512), nullable=False),
            sa.Column("capability", sa.String(length=16), nullable=False, server_default="read"),
            sa.Column("auth_scheme", sa.String(length=32), nullable=False, server_default="workspace_token"),
            sa.Column("auth_secret_ref", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("introspection", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    _create_index_if_missing("ix_viber_connections_account_id", "viber_connections", ["account_id"])

    if not _has_table("viber_ai_providers"):
        op.create_table(
            "viber_ai_providers",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("provider_type", sa.String(length=16), nullable=False, server_default="byok"),
            sa.Column("label", sa.String(length=80), nullable=False, server_default="Default"),
            sa.Column("base_url", sa.String(length=512), nullable=True),
            sa.Column("default_model", sa.String(length=120), nullable=True),
            sa.Column("key_secret_ref", sa.String(length=255), nullable=True),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    _create_index_if_missing("ix_viber_ai_providers_account_id", "viber_ai_providers", ["account_id"])


def downgrade() -> None:
    _drop_index_if_exists("ix_viber_ai_providers_account_id", "viber_ai_providers")
    if _has_table("viber_ai_providers"):
        op.drop_table("viber_ai_providers")
    _drop_index_if_exists("ix_viber_connections_account_id", "viber_connections")
    if _has_table("viber_connections"):
        op.drop_table("viber_connections")
    _drop_index_if_exists("ix_viber_accounts_email", "viber_accounts")
    if _has_table("viber_accounts"):
        op.drop_table("viber_accounts")
