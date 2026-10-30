"""merge the user-email and scroll-email migration heads

Both migrations legitimately extend the same deployed schema revision and must
remain independently reversible. This no-op merge preserves both histories
while restoring the single Alembic head required by CI and future migrations.
"""

from collections.abc import Sequence

revision: str = "20260810_merge_email_heads"
down_revision: str | Sequence[str] | None = (
    "20260808_unique_lower_email",
    "20260810_scroll_email_outbox",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
