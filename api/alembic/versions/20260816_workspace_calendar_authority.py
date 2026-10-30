"""add delegated Workspace calendar identifier

Revision ID: 20260816_workspace_calendar
Revises: 20260814_huddl_hold_kind

Net-new artifact justification:
The existing user record owns artist availability and Google connection state,
but it cannot identify a shared secondary calendar once BTTCO stops using each
artist's OAuth `primary` alias. Extending that record avoids a parallel calendar
mapping table and keeps the current booking ownership contract intact.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_workspace_calendar"
down_revision: str | Sequence[str] | None = "20260814_huddl_hold_kind"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("bttco_users", "google_calendar_id"):
        op.add_column(
            "bttco_users",
            sa.Column("google_calendar_id", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    if _has_column("bttco_users", "google_calendar_id"):
        op.drop_column("bttco_users", "google_calendar_id")
