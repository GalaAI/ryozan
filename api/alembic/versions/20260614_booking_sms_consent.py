"""add appointment sms consent to bookings

Booking SMS consent is intentionally stored on bttco_bookings instead of
reusing only bttco_users.sms_opt_in. Public booking creates collector users
automatically, and appointment reminders need an auditable consent snapshot
for the specific booking even before the client activates their account.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260614_booking_sms_consent"
down_revision: str | Sequence[str] | None = "20260614_artist_booking_rules"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing(
        "bttco_bookings",
        sa.Column("sms_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    _add_column_if_missing("bttco_bookings", sa.Column("sms_opt_in_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("bttco_bookings", sa.Column("sms_consent_text", sa.Text(), nullable=True))


def downgrade() -> None:
    for column_name in ("sms_consent_text", "sms_opt_in_at", "sms_opt_in"):
        if _has_column("bttco_bookings", column_name):
            op.drop_column("bttco_bookings", column_name)
