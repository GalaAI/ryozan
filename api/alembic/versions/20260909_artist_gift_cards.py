"""Add artist-specific gift cards and redemption ledger.

Revision ID: 20260909_artist_gift_cards
Revises: 20260829_delivery_integrity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260909_artist_gift_cards"
down_revision: str | None = "20260829_delivery_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bttco_gift_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchaser_name", sa.String(length=255), nullable=False),
        sa.Column("purchaser_email", sa.String(length=320), nullable=False),
        sa.Column("recipient_name", sa.String(length=255), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("original_amount_cents", sa.Integer(), nullable=False),
        sa.Column("balance_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("stripe_session_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("routing_destination", sa.String(length=20), nullable=False),
        sa.Column("routing_reason", sa.String(length=40), nullable=False),
        sa.Column("artist_cut_cents", sa.Integer(), nullable=False),
        sa.Column("shop_cut_cents", sa.Integer(), nullable=False),
        sa.Column("payout_owed_cents", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artist_id"], ["bttco_users.id"]),
        sa.CheckConstraint("original_amount_cents > 0", name="ck_gift_card_original_amount_positive"),
        sa.CheckConstraint("balance_cents >= 0", name="ck_gift_card_balance_nonnegative"),
        sa.CheckConstraint("balance_cents <= original_amount_cents", name="ck_gift_card_balance_within_original"),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'redeemed', 'voided')",
            name="ck_gift_card_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_session_id"),
    )
    op.create_index("ix_bttco_gift_cards_artist_id", "bttco_gift_cards", ["artist_id"])
    op.create_index("ix_bttco_gift_cards_code", "bttco_gift_cards", ["code"], unique=True)
    op.create_table(
        "bttco_gift_card_redemptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gift_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("redeemed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bttco_bookings.id"]),
        sa.ForeignKeyConstraint(["gift_card_id"], ["bttco_gift_cards.id"]),
        sa.ForeignKeyConstraint(["redeemed_by"], ["bttco_users.id"]),
        sa.CheckConstraint("amount_cents > 0", name="ck_gift_card_redemption_amount_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bttco_gift_card_redemptions_gift_card_id",
        "bttco_gift_card_redemptions",
        ["gift_card_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bttco_gift_card_redemptions_gift_card_id", table_name="bttco_gift_card_redemptions")
    op.drop_table("bttco_gift_card_redemptions")
    op.drop_index("ix_bttco_gift_cards_code", table_name="bttco_gift_cards")
    op.drop_index("ix_bttco_gift_cards_artist_id", table_name="bttco_gift_cards")
    op.drop_table("bttco_gift_cards")
