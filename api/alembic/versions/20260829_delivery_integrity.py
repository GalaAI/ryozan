"""add Courier SMS correlation and durable Pack bridge outbox

Revision ID: 20260829_delivery_integrity
Revises: 20260825_mcp_oauth
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260829_delivery_integrity"
down_revision: str | Sequence[str] | None = "20260825_mcp_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bttco_sms_messages",
        sa.Column("courier_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_bttco_sms_messages_courier_message_id",
        "bttco_sms_messages",
        "bttco_messages",
        ["courier_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_bttco_sms_messages_courier_message_id",
        "bttco_sms_messages",
        ["courier_message_id"],
    )
    op.create_table(
        "bttco_pack_bridge_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", sa.String(160), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("courier_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("item_id", sa.String(255), nullable=True),
        sa.Column("created", sa.Boolean(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["courier_message_id"], ["bttco_messages.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_bttco_pack_bridge_outbox_source_id", "bttco_pack_bridge_outbox", ["source_id"])
    op.create_index(
        "ix_bttco_pack_bridge_outbox_idempotency_key",
        "bttco_pack_bridge_outbox",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_bttco_pack_bridge_outbox_courier_message_id",
        "bttco_pack_bridge_outbox",
        ["courier_message_id"],
    )
    op.create_index("ix_bttco_pack_bridge_outbox_status", "bttco_pack_bridge_outbox", ["status"])
    op.create_index(
        "ix_bttco_pack_bridge_outbox_next_attempt_at",
        "bttco_pack_bridge_outbox",
        ["next_attempt_at"],
    )
    op.create_table(
        "bttco_gallery_deletion_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("allowed_prefix", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_bttco_gallery_deletion_outbox_artist_id",
        "bttco_gallery_deletion_outbox",
        ["artist_id"],
    )
    op.create_index(
        "ix_bttco_gallery_deletion_outbox_idempotency_key",
        "bttco_gallery_deletion_outbox",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_bttco_gallery_deletion_outbox_status",
        "bttco_gallery_deletion_outbox",
        ["status"],
    )
    op.create_index(
        "ix_bttco_gallery_deletion_outbox_next_attempt_at",
        "bttco_gallery_deletion_outbox",
        ["next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_table("bttco_gallery_deletion_outbox")
    op.drop_table("bttco_pack_bridge_outbox")
    op.drop_index("ix_bttco_sms_messages_courier_message_id", table_name="bttco_sms_messages")
    op.drop_constraint(
        "fk_bttco_sms_messages_courier_message_id",
        "bttco_sms_messages",
        type_="foreignkey",
    )
    op.drop_column("bttco_sms_messages", "courier_message_id")
