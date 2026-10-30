"""session type description and pricing mode, artist buffer enable flag

Production does not run Alembic -- it builds schema at boot with create_all
plus the additive column reconciler in app/core/schema_sync.py, which derives
these same three columns from the ORM metadata. This migration exists so the
Alembic history stays a truthful record of the schema for local work and CI,
and so the two paths cannot drift apart.

All three are additive and safe on a populated table: description is nullable,
and the other two carry server defaults that preserve existing behaviour --
every session type that predates pricing_mode quotes from price_min/price_max
('range'), and every artist has always had the booking buffer in force (true).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260805_session_pricing_buffer"
down_revision: str | Sequence[str] | None = "20260727_stripe_events"
branch_labels = None
depends_on = None

_SESSION_TYPES = "bttco_artist_session_types"
_USERS = "bttco_users"


def _has_column(table: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column(_SESSION_TYPES, "description"):
        op.add_column(_SESSION_TYPES, sa.Column("description", sa.Text(), nullable=True))
    if not _has_column(_SESSION_TYPES, "pricing_mode"):
        op.add_column(
            _SESSION_TYPES,
            sa.Column("pricing_mode", sa.String(length=50), nullable=False, server_default="range"),
        )
    if not _has_column(_USERS, "booking_buffer_enabled"):
        op.add_column(
            _USERS,
            sa.Column(
                "booking_buffer_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )


def downgrade() -> None:
    if _has_column(_USERS, "booking_buffer_enabled"):
        op.drop_column(_USERS, "booking_buffer_enabled")
    if _has_column(_SESSION_TYPES, "pricing_mode"):
        op.drop_column(_SESSION_TYPES, "pricing_mode")
    if _has_column(_SESSION_TYPES, "description"):
        op.drop_column(_SESSION_TYPES, "description")
