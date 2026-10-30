"""Stash inventory and flash canvas metadata.

Revision ID: 20260519_stash_inventory_canvas
Revises: 20260515_crm_pipeline_tasks
Create Date: 2026-05-19
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260519_stash_inventory_canvas"
down_revision = "20260515_crm_pipeline_tasks"
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


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _has_column(table_name, column_name):
        op.drop_column(table_name, column_name)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    _add_column_if_missing("bttco_flash_designs", sa.Column("size_label", sa.String(100), nullable=True))
    _add_column_if_missing("bttco_flash_designs", sa.Column("placement", sa.String(120), nullable=True))
    _add_column_if_missing(
        "bttco_flash_designs",
        sa.Column("mask_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _add_column_if_missing(
        "bttco_flash_designs",
        sa.Column("canvas_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _add_column_if_missing(
        "bttco_flash_designs",
        sa.Column("ai_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _add_column_if_missing(
        "bttco_flash_designs",
        sa.Column("gala_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    if not _has_table("bttco_stash_items"):
        op.create_table(
            "bttco_stash_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("source_flash_design_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("lane", sa.String(32), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("sku", sa.String(120), nullable=True),
            sa.Column("asset_url", sa.Text(), nullable=True),
            sa.Column("price", sa.Numeric(10, 2), nullable=True),
            sa.Column("inventory_count", sa.Integer(), nullable=True),
            sa.Column("size_label", sa.String(100), nullable=True),
            sa.Column("estimated_minutes", sa.Integer(), nullable=True),
            sa.Column("delivery_kind", sa.String(50), nullable=True),
            sa.Column("storage_ref", sa.Text(), nullable=True),
            sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("ai_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("gala_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_id"], ["bttco_users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["source_flash_design_id"], ["bttco_flash_designs.id"], ondelete="SET NULL"),
        )
    _create_index_if_missing("ix_bttco_stash_items_artist_lane", "bttco_stash_items", ["artist_id", "lane"])
    _create_index_if_missing("ix_bttco_stash_items_status", "bttco_stash_items", ["status"])

    if not _has_table("bttco_stash_canvas_sessions"):
        op.create_table(
            "bttco_stash_canvas_sessions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("sheet_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("lane", sa.String(32), nullable=False, server_default="flash"),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("canvas_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("ai_suggestions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("source_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("gala_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["sheet_id"], ["bttco_flash_sheets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_id"], ["bttco_users.id"], ondelete="SET NULL"),
        )
    _create_index_if_missing(
        "ix_bttco_stash_canvas_artist_lane",
        "bttco_stash_canvas_sessions",
        ["artist_id", "lane"],
    )
    _create_index_if_missing("ix_bttco_stash_canvas_sheet_id", "bttco_stash_canvas_sessions", ["sheet_id"])


def downgrade() -> None:
    _drop_index_if_exists("ix_bttco_stash_canvas_sheet_id", "bttco_stash_canvas_sessions")
    _drop_index_if_exists("ix_bttco_stash_canvas_artist_lane", "bttco_stash_canvas_sessions")
    if _has_table("bttco_stash_canvas_sessions"):
        op.drop_table("bttco_stash_canvas_sessions")

    _drop_index_if_exists("ix_bttco_stash_items_status", "bttco_stash_items")
    _drop_index_if_exists("ix_bttco_stash_items_artist_lane", "bttco_stash_items")
    if _has_table("bttco_stash_items"):
        op.drop_table("bttco_stash_items")

    _drop_column_if_exists("bttco_flash_designs", "gala_sync_enabled")
    _drop_column_if_exists("bttco_flash_designs", "ai_metadata")
    _drop_column_if_exists("bttco_flash_designs", "canvas_state")
    _drop_column_if_exists("bttco_flash_designs", "mask_json")
    _drop_column_if_exists("bttco_flash_designs", "placement")
    _drop_column_if_exists("bttco_flash_designs", "size_label")
