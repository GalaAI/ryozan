"""Portfolio thumbnails: original_url -> 640px WebP thumb url per artist.

Revision ID: 20260921_portfolio_thumbs
Revises: 20260921_sms_error_code

Gallery surfaces were loading each portfolio original (up to 4096px) into a tile a
few hundred pixels wide. Uploads now store a WebP companion under
portfolios/<slug>/thumbs/ and record it here; scripts/backfill_portfolio_thumbs.py
fills in the images uploaded before this existed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260921_portfolio_thumbs"
down_revision: str | None = "20260921_sms_error_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bttco_users",
        sa.Column("portfolio_thumbs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bttco_users", "portfolio_thumbs")
