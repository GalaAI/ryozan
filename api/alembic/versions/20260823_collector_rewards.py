"""add collector reward receipts and minigame offers

Revision ID: 20260823_rewards
Revises: 20260820_booky_readiness

Net-new artifact justification:
Quest rows track milestone completion but cannot prove individual balance
changes or inventory unlocks. These append-only receipts add that missing
authority without mixing shop rewards into the real-money E-con ledger.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260823_rewards"
down_revision: str | Sequence[str] | None = "20260820_booky_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("bttco_reward_grants"):
        op.create_table(
            "bttco_reward_grants",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("source_id", sa.String(length=255), nullable=False),
            sa.Column("idempotency_key", sa.String(length=340), nullable=False),
            sa.Column("xp_delta", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("coin_delta", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("unlock_kind", sa.String(length=40), nullable=True),
            sa.Column("unlock_slug", sa.String(length=120), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["bttco_users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "idempotency_key", name="uq_reward_grant_user_key"),
        )
        op.create_index("ix_bttco_reward_grants_user_id", "bttco_reward_grants", ["user_id"])
        op.create_index("ix_bttco_reward_grants_event_type", "bttco_reward_grants", ["event_type"])
        op.create_index("ix_bttco_reward_grants_unlock_kind", "bttco_reward_grants", ["unlock_kind"])
        op.create_index("ix_bttco_reward_grants_created_at", "bttco_reward_grants", ["created_at"])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("bttco_reward_offers"):
        op.create_table(
            "bttco_reward_offers",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("trigger_grant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("offer_type", sa.String(length=40), nullable=False, server_default="mini_game"),
            sa.Column("game_slug", sa.String(length=80), nullable=False),
            sa.Column("reward_kind", sa.String(length=40), nullable=False),
            sa.Column("reward_slug", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="offered"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["trigger_grant_id"], ["bttco_reward_grants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["bttco_users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("trigger_grant_id"),
        )
        op.create_index("ix_bttco_reward_offers_user_id", "bttco_reward_offers", ["user_id"])
        op.create_index("ix_bttco_reward_offers_status", "bttco_reward_offers", ["status"])
        op.create_index("ix_bttco_reward_offers_created_at", "bttco_reward_offers", ["created_at"])


def downgrade() -> None:
    # Reward receipts are financial-adjacent audit evidence. Rollbacks stop new
    # writes without deleting earned balances, offers, or unlock provenance.
    pass
