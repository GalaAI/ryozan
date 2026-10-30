"""add bttco_scroll_categories table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bttco_scroll_categories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("icon", sa.String(60), nullable=True),       # emoji or icon name
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ai_digest_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("ai_digest_prompt", sa.Text, nullable=True),  # how Gala extracts value
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("bttco_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # Seed the three built-in tabs
    op.execute("""
        INSERT INTO bttco_scroll_categories (slug, label, icon, description, is_builtin, sort_order)
        VALUES
          ('artist',   'Artist',   '📜', 'Artist compliance scrolls — BBP cert, employment agreements, license records.', true, 0),
          ('shop',     'Shop',     '🏪', 'Shop-level documents — vendor agreements, insurance, lease summaries.', true, 1),
          ('misc',     'Misc',     '📁', 'General uploads that do not fit another category.', true, 2)
    """)


def downgrade() -> None:
    op.drop_table("bttco_scroll_categories")
