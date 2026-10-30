"""sms_messaging: opt-in/out columns on users + sms_messages + sms_templates

Revision ID: 20260522_sms_messaging
Revises: 20260519_stash_inventory_canvas
Create Date: 2026-05-22

Adds the data layer for the Twilio A2P 10DLC SMS program:

* `bttco_users.sms_opt_in` / `sms_opt_in_at` / `sms_opt_out_at`
* `bttco_sms_messages` — outbound + inbound log (status, error, twilio sid)
* `bttco_sms_templates` — editable {{var}} templates (mirrors email_templates)

Phone normalisation (E.164) is handled in the service layer so this
migration stays vanilla SQL.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260522_sms_messaging"
down_revision = "20260519_stash_inventory_canvas"
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
    # ── 1. user opt-in tracking ─────────────────────────────────────────
    _add_column_if_missing(
        "bttco_users",
        sa.Column("sms_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("sms_opt_in_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "bttco_users",
        sa.Column("sms_opt_out_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 2. message log (outbound + inbound) ─────────────────────────────
    if not _has_table("bttco_sms_messages"):
        op.create_table(
            "bttco_sms_messages",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
            sa.Column("direction", sa.String(10), nullable=False),  # outbound | inbound
            sa.Column("to_phone", sa.String(20), nullable=False),
            sa.Column("from_phone", sa.String(20), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
            # queued | sending | sent | delivered | undelivered | failed | received
            sa.Column("error_code", sa.String(20), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("twilio_sid", sa.String(64), nullable=True, unique=True),
            sa.Column("template_slug", sa.String(100), nullable=True),
            sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("sender_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("price_cents", sa.Integer(), nullable=True),
            sa.Column("num_segments", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                default=lambda: datetime.now(UTC),
                server_default=sa.text("now()"),
            ),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        )
    _create_index_if_missing("ix_sms_messages_to_phone", "bttco_sms_messages", ["to_phone"])
    _create_index_if_missing("ix_sms_messages_created_at", "bttco_sms_messages", ["created_at"])
    _create_index_if_missing("ix_sms_messages_status", "bttco_sms_messages", ["status"])
    _create_index_if_missing("ix_sms_messages_direction", "bttco_sms_messages", ["direction"])

    # ── 3. editable templates ───────────────────────────────────────────
    if not _has_table("bttco_sms_templates"):
        op.create_table(
            "bttco_sms_templates",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
            sa.Column("slug", sa.String(100), nullable=False, unique=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("category", sa.String(50), nullable=False, server_default="transactional"),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column(
                "variables",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )


def downgrade() -> None:
    if _has_table("bttco_sms_templates"):
        op.drop_table("bttco_sms_templates")
    _drop_index_if_exists("ix_sms_messages_direction", "bttco_sms_messages")
    _drop_index_if_exists("ix_sms_messages_status", "bttco_sms_messages")
    _drop_index_if_exists("ix_sms_messages_created_at", "bttco_sms_messages")
    _drop_index_if_exists("ix_sms_messages_to_phone", "bttco_sms_messages")
    if _has_table("bttco_sms_messages"):
        op.drop_table("bttco_sms_messages")
    _drop_column_if_exists("bttco_users", "sms_opt_out_at")
    _drop_column_if_exists("bttco_users", "sms_opt_in_at")
    _drop_column_if_exists("bttco_users", "sms_opt_in")
