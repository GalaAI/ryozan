"""case-insensitive unique index on bttco_users.email

`bttco_users` carried unique indexes only on `id` (primary key) and `google_id`
(partial). Nothing at the database level stopped two accounts from sharing an
email, and because several creation paths stored the address exactly as typed,
production ended up with pairs differing only by capitalization. Login looks
users up with `lower(email)` and `scalar_one_or_none()`, which raises
`MultipleResultsFound` on such a pair -- an HTTP 500 on every attempt for the
people who owned those rows.

This index is the database-level half of the fix. `app.models.user` normalizes
on write so no new duplicate can be created; this makes the guarantee real
rather than merely intended.

Production does not run Alembic -- it builds schema at boot from main.py's
_run_startup_migrations(), which creates this same index from
`_EMAIL_UNIQUE_INDEX_SQL`. This migration keeps the Alembic history a truthful
record for local work and CI, and keeps the two paths from drifting.

PREREQUISITE: this fails while duplicates exist. Run
`python scripts/dedupe_user_emails.py` (dry-run first, then --apply) before
upgrading.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260808_unique_lower_email"
down_revision: str | Sequence[str] | None = "20260805_session_pricing_buffer"
branch_labels = None
depends_on = None

_INDEX = "ux_bttco_users_email_lower"
_TABLE = "bttco_users"


def upgrade() -> None:
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} ON {_TABLE} (lower(email))")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
