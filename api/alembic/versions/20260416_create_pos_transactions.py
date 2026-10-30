"""create bttco_pos_transactions table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bttco_pos_transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("artist_id", UUID(as_uuid=True), sa.ForeignKey("bttco_users.id"), nullable=False),
        sa.Column("booking_id", UUID(as_uuid=True), sa.ForeignKey("bttco_bookings.id"), nullable=True),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("processor", sa.String(50), nullable=True),
        sa.Column("client_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("stripe_session_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("bttco_users.id"), nullable=False),
    )
    op.create_index("ix_pos_transactions_artist_id", "bttco_pos_transactions", ["artist_id"])
    op.create_index("ix_pos_transactions_created_at", "bttco_pos_transactions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_pos_transactions_created_at", table_name="bttco_pos_transactions")
    op.drop_index("ix_pos_transactions_artist_id", table_name="bttco_pos_transactions")
    op.drop_table("bttco_pos_transactions")
