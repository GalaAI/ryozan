"""Allow purchaser-only gift-card email delivery.

Revision ID: 20260911_gift_delivery
Revises: 20260909_artist_gift_cards

The existing gift-card record is the durable delivery owner, so this extends
that table instead of introducing a second receipt or checkout contract.
Existing rows retain direct-recipient delivery through the true default.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260911_gift_delivery"
down_revision: str | None = "20260909_artist_gift_cards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bttco_gift_cards",
        sa.Column("send_to_recipient", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.alter_column(
        "bttco_gift_cards",
        "recipient_email",
        existing_type=sa.String(length=320),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("UPDATE bttco_gift_cards SET recipient_email = purchaser_email WHERE recipient_email IS NULL")
    op.alter_column(
        "bttco_gift_cards",
        "recipient_email",
        existing_type=sa.String(length=320),
        nullable=False,
    )
    op.drop_column("bttco_gift_cards", "send_to_recipient")
