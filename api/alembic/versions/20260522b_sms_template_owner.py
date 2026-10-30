"""sms_template_owner: per-artist template ownership

Revision ID: 20260522b_sms_template_owner
Revises: 20260522_sms_messaging
Create Date: 2026-05-22

Adds `owner_id` to `bttco_sms_templates` so artists can own their personal
templates without editing shop-level templates. NULL = shop-level (founder/admin
only). Non-NULL = owned by that user; only that user + admin+ can edit.

Personal-template slugs are conventionally prefixed `staff:<owner_uuid>:`
(the API auto-prefixes on POST), so log slugs are still legible and
shop-level slugs stay un-prefixed for backward compat.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260522b_sms_template_owner"
down_revision = "20260522_sms_messaging"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _has_column("bttco_sms_templates", "owner_id"):
        op.add_column(
            "bttco_sms_templates",
            sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if not _has_index("bttco_sms_templates", "ix_sms_templates_owner_id"):
        op.create_index(
            "ix_sms_templates_owner_id",
            "bttco_sms_templates",
            ["owner_id"],
        )


def downgrade() -> None:
    if _has_index("bttco_sms_templates", "ix_sms_templates_owner_id"):
        op.drop_index("ix_sms_templates_owner_id", table_name="bttco_sms_templates")
    if _has_column("bttco_sms_templates", "owner_id"):
        op.drop_column("bttco_sms_templates", "owner_id")
