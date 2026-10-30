"""persist reversible overall flash sheet crops

Revision ID: 20260824_sheet_crop
Revises: 20260823_rewards

Net-new artifact justification:
FlashSheet already owns uploaded sheet assets, but it cannot distinguish the
original upload from a storefront-ready overall crop. These additive columns
extend that owner so crop edits remain real, reversible, and source-traceable.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260824_sheet_crop"
down_revision: str | Sequence[str] | None = "20260823_rewards"
branch_labels = None
depends_on = None


def _add_column_if_missing(column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_columns("bttco_flash_sheets")}
    if column.name not in existing:
        op.add_column("bttco_flash_sheets", column)


def upgrade() -> None:
    _add_column_if_missing(sa.Column("source_image_url", sa.Text(), nullable=True))
    _add_column_if_missing(sa.Column("crop_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    _add_column_if_missing(sa.Column("crop_x", sa.Numeric(8, 4), nullable=False, server_default="0"))
    _add_column_if_missing(sa.Column("crop_y", sa.Numeric(8, 4), nullable=False, server_default="0"))
    _add_column_if_missing(sa.Column("crop_w", sa.Numeric(8, 4), nullable=False, server_default="100"))
    _add_column_if_missing(sa.Column("crop_h", sa.Numeric(8, 4), nullable=False, server_default="100"))
    _add_column_if_missing(sa.Column("asset_version", sa.Integer(), nullable=False, server_default="1"))
    op.execute(sa.text("UPDATE bttco_flash_sheets SET source_image_url = image_url WHERE source_image_url IS NULL"))


def downgrade() -> None:
    # Stored crop assets and their original-source provenance are operator data.
    # A code rollback must not silently discard either representation.
    pass
