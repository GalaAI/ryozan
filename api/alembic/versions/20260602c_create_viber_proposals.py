"""create Viber staged proposal table

Viber Phase 3 adds agent proposals that can describe connected-project writes
without executing them. This is net-new because Courier artifacts approve
client communication actions, while Viber proposals approve project mutation
plans across connected backends.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260602c_create_viber_proposals"
down_revision: str | Sequence[str] | None = "20260602b_create_viber"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    if not _has_table("viber_proposals"):
        op.create_table(
            "viber_proposals",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent", sa.String(length=32), nullable=False, server_default="smith"),
            sa.Column("intent", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="staged"),
            sa.Column("proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("approval_notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        )
    _create_index_if_missing("ix_viber_proposals_account_id", "viber_proposals", ["account_id"])
    _create_index_if_missing("ix_viber_proposals_connection_id", "viber_proposals", ["connection_id"])


def downgrade() -> None:
    _drop_index_if_exists("ix_viber_proposals_connection_id", "viber_proposals")
    _drop_index_if_exists("ix_viber_proposals_account_id", "viber_proposals")
    if _has_table("viber_proposals"):
        op.drop_table("viber_proposals")
