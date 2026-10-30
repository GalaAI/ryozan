"""Portfolio focus derivatives: original_url -> 1400px WebP url per artist.

Revision ID: 20260922_portfolio_focus
Revises: 20260921_portfolio_thumbs

The enlarged view of a portfolio piece was decoding the 4096px original in the browser
and resizing it there on every open. Uploads now store a 1400px WebP companion under
portfolios/<slug>/focus/ next to the 640px thumb and record it here; the startup backfill
fills in the images uploaded before this existed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260922_portfolio_focus"
down_revision: str | None = "20260921_portfolio_thumbs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bttco_users",
        sa.Column("portfolio_focus", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bttco_users", "portfolio_focus")
