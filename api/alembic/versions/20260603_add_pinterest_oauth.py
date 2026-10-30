"""add Pinterest OAuth fields to users

Stores the per-user Pinterest API v5 refresh token (continuous, 60-day,
refreshable) plus the connected username for display. Mirrors the existing
google_refresh_token pattern. App "GalaX", Trial access.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260603_add_pinterest_oauth"
down_revision: str | Sequence[str] | None = "20260602c_create_viber_proposals"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    for column in (
        sa.Column("pinterest_refresh_token", sa.String(length=1024), nullable=True),
        sa.Column("pinterest_access_token", sa.String(length=1024), nullable=True),
        sa.Column("pinterest_username", sa.String(length=255), nullable=True),
    ):
        if not _has_column("bttco_users", column.name):
            op.add_column("bttco_users", column)


def downgrade() -> None:
    for column_name in ("pinterest_username", "pinterest_access_token", "pinterest_refresh_token"):
        if _has_column("bttco_users", column_name):
            op.drop_column("bttco_users", column_name)
