"""rename misc scroll tab to templates; create bttco_response_templates

Revision ID: d7e8f9a0b1c2
Revises: c3d4e5f6a7b8
Branch_labels: None
Depends_on: None
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "d7e8f9a0b1c2"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Rename the built-in 'misc' scroll category ─────────────────
    op.execute("""
        UPDATE bttco_scroll_categories
        SET slug        = 'templates',
            label       = 'Templates',
            icon        = 'message-square',
            description = 'Reusable auto-message response templates for staff quick-replies.'
        WHERE slug = 'misc' AND is_builtin = true
    """)

    # ── 2. Create bttco_response_templates ────────────────────────────
    op.create_table(
        "bttco_response_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("bttco_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # Seed from the hardcoded QUICK_REPLIES defaults
    op.execute("""
        INSERT INTO bttco_response_templates (title, body, sort_order)
        VALUES
          ('On my way',            'On my way!',              0),
          ('Running late',         'Running 15 min late',     1),
          ('See you soon',         'See you soon!',           2),
          ('Appointment confirmed','Appointment confirmed',   3),
          ('Check your email',     'Please check your email', 4)
    """)


def downgrade() -> None:
    op.drop_table("bttco_response_templates")
    op.execute("""
        UPDATE bttco_scroll_categories
        SET slug        = 'misc',
            label       = 'Misc',
            icon        = '📁',
            description = 'General uploads that do not fit another category.'
        WHERE slug = 'templates' AND is_builtin = true
    """)
