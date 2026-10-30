"""Add durable BYOF staged-deposit authorization state.

Revision ID: 20260922_byof_staged
Revises: 20260922_portfolio_focus

This extends the existing booking payment owner rather than introducing a
second deposit record. A manual-capture authorization is not a paid deposit,
so its review state and deadline must remain distinct from deposit_paid.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260922_byof_staged"
down_revision: str | None = "20260922_portfolio_focus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bttco_bookings",
        sa.Column("is_bring_your_own_flash", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("bttco_bookings", sa.Column("deposit_authorization_status", sa.String(length=40), nullable=True))
    op.add_column("bttco_bookings", sa.Column("deposit_authorized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bttco_bookings", sa.Column("deposit_review_deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "bttco_bookings", sa.Column("deposit_review_reminder_sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_bttco_bookings_deposit_review_deadline_at",
        "bttco_bookings",
        ["deposit_review_deadline_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_bttco_bookings_deposit_review_deadline_at", table_name="bttco_bookings")
    op.drop_column("bttco_bookings", "deposit_review_reminder_sent_at")
    op.drop_column("bttco_bookings", "deposit_review_deadline_at")
    op.drop_column("bttco_bookings", "deposit_authorized_at")
    op.drop_column("bttco_bookings", "deposit_authorization_status")
    op.drop_column("bttco_bookings", "is_bring_your_own_flash")
