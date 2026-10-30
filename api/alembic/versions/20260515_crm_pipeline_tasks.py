"""CRM pipeline tasks (minimal v1 for native Booking CRM Mod).

Revision ID: 20260515_crm_pipeline_tasks
Revises: 20260514_create_booking_drafts
Create Date: 2026-05-15
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260515_crm_pipeline_tasks"
down_revision = "20260514_create_booking_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bttco_crm_pipeline_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(16), nullable=True),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bttco_crm_pipeline_tasks_status", "bttco_crm_pipeline_tasks", ["status"])
    op.create_index("ix_bttco_crm_pipeline_tasks_booking_id", "bttco_crm_pipeline_tasks", ["booking_id"])


def downgrade() -> None:
    op.drop_index("ix_bttco_crm_pipeline_tasks_booking_id", table_name="bttco_crm_pipeline_tasks")
    op.drop_index("ix_bttco_crm_pipeline_tasks_status", table_name="bttco_crm_pipeline_tasks")
    op.drop_table("bttco_crm_pipeline_tasks")
