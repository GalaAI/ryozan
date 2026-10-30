"""create shared collaboration workspaces

Adds the reusable "BTTCO micro-app" core: a workspaces table plus
workspace-scoped notes, todos, and log, and a tracker-specific feature_status
table. The dev tracker is workspace #1; future micro-apps (landing, signup,
countdown) are additional workspace rows, not new schema.

Backs the Discord dev-collab bot and the shared web tracker.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260602_create_workspaces"
down_revision: str | Sequence[str] | None = "20260528_add_membership_tiers"
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
    if not _has_table("bttco_workspaces"):
        op.create_table(
            "bttco_workspaces",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="tracker"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    _create_index_if_missing("ix_bttco_workspaces_slug", "bttco_workspaces", ["slug"], unique=True)

    if not _has_table("bttco_workspace_notes"):
        op.create_table(
            "bttco_workspace_notes",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("author", sa.String(length=80), nullable=False, server_default="unknown"),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=24), nullable=False, server_default="web"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    _create_index_if_missing("ix_bttco_workspace_notes_workspace_id", "bttco_workspace_notes", ["workspace_id"])

    if not _has_table("bttco_workspace_todos"):
        op.create_table(
            "bttco_workspace_todos",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(length=280), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
            sa.Column("assignee", sa.String(length=80), nullable=True),
            sa.Column("author", sa.String(length=80), nullable=False, server_default="unknown"),
            sa.Column("source", sa.String(length=24), nullable=False, server_default="web"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        )
    _create_index_if_missing("ix_bttco_workspace_todos_workspace_id", "bttco_workspace_todos", ["workspace_id"])

    if not _has_table("bttco_workspace_log"):
        op.create_table(
            "bttco_workspace_log",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("author", sa.String(length=80), nullable=False, server_default="unknown"),
            sa.Column("entry", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=24), nullable=False, server_default="web"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    _create_index_if_missing("ix_bttco_workspace_log_workspace_id", "bttco_workspace_log", ["workspace_id"])

    if not _has_table("bttco_workspace_channels"):
        op.create_table(
            "bttco_workspace_channels",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("discord_channel_id", sa.String(length=32), nullable=False),
            sa.Column("discord_guild_id", sa.String(length=32), nullable=True),
            sa.Column("bound_by", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    _create_index_if_missing("ix_bttco_workspace_channels_workspace_id", "bttco_workspace_channels", ["workspace_id"])
    _create_index_if_missing(
        "ix_bttco_workspace_channels_discord_channel_id",
        "bttco_workspace_channels",
        ["discord_channel_id"],
        unique=True,
    )

    if not _has_table("bttco_workspace_feature_status"):
        op.create_table(
            "bttco_workspace_feature_status",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("domain", sa.String(length=120), nullable=False),
            sa.Column("live_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("db_tables", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("api", sa.String(length=255), nullable=True),
            sa.Column("ui", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="empty"),
            sa.Column("verdict", sa.String(length=16), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
    _create_index_if_missing(
        "ix_bttco_workspace_feature_status_workspace_id",
        "bttco_workspace_feature_status",
        ["workspace_id"],
    )


def downgrade() -> None:
    _drop_index_if_exists("ix_bttco_workspace_feature_status_workspace_id", "bttco_workspace_feature_status")
    if _has_table("bttco_workspace_feature_status"):
        op.drop_table("bttco_workspace_feature_status")
    _drop_index_if_exists("ix_bttco_workspace_channels_discord_channel_id", "bttco_workspace_channels")
    _drop_index_if_exists("ix_bttco_workspace_channels_workspace_id", "bttco_workspace_channels")
    if _has_table("bttco_workspace_channels"):
        op.drop_table("bttco_workspace_channels")
    _drop_index_if_exists("ix_bttco_workspace_log_workspace_id", "bttco_workspace_log")
    if _has_table("bttco_workspace_log"):
        op.drop_table("bttco_workspace_log")
    _drop_index_if_exists("ix_bttco_workspace_todos_workspace_id", "bttco_workspace_todos")
    if _has_table("bttco_workspace_todos"):
        op.drop_table("bttco_workspace_todos")
    _drop_index_if_exists("ix_bttco_workspace_notes_workspace_id", "bttco_workspace_notes")
    if _has_table("bttco_workspace_notes"):
        op.drop_table("bttco_workspace_notes")
    _drop_index_if_exists("ix_bttco_workspaces_slug", "bttco_workspaces")
    if _has_table("bttco_workspaces"):
        op.drop_table("bttco_workspaces")
