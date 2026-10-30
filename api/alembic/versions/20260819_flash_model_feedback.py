"""canonize model feedback samples for flash segmentation

Revision ID: 20260819_flash_feedback
Revises: 20260819_pos_price

Net-new artifact justification:
The flash correction endpoint already writes a feedback_samples contract, but
no migration or durable accepted/corrected/rejected schema owns it. This
migration canonizes that existing contract so reviewed masks can feed versioned
training runs without adding a second feedback store.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260819_flash_feedback"
down_revision: str | Sequence[str] | None = "20260819_pos_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("feedback_samples"):
        op.create_table(
            "feedback_samples",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("model_id", sa.String(length=100), nullable=False),
            sa.Column("model_version", sa.String(length=255), nullable=False),
            sa.Column("input_type", sa.String(length=50), nullable=False, server_default="image"),
            sa.Column("input_ref", sa.Text(), nullable=False),
            sa.Column("artifact_ref", sa.Text(), nullable=True),
            sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("prediction", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("correction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("outcome", sa.String(length=24), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("module", sa.String(length=50), nullable=False, server_default="bttco"),
            sa.Column("training_status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        existing = {column["name"] for column in inspector.get_columns("feedback_samples")}
        additions = (
            sa.Column("artifact_ref", sa.Text(), nullable=True),
            sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("outcome", sa.String(length=24), nullable=False, server_default="corrected"),
            sa.Column("training_status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        for column in additions:
            if column.name not in existing:
                op.add_column("feedback_samples", column)

    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("feedback_samples")}
    if "ix_feedback_samples_model_created" not in indexes:
        op.create_index(
            "ix_feedback_samples_model_created",
            "feedback_samples",
            ["model_id", "created_at"],
        )
    if "ix_feedback_samples_subject_created" not in indexes:
        op.create_index(
            "ix_feedback_samples_subject_created",
            "feedback_samples",
            ["subject_id", "created_at"],
        )


def downgrade() -> None:
    # Training evidence is append-only operator data. A code rollback must not
    # erase reviewed source, mask, or correction history.
    pass
