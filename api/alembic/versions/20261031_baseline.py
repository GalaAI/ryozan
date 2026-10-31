"""Baseline: the complete Ryozan schema as the old boot path built it.

Revision ID: 20261031_baseline
Revises: (none -- this is the root of the chain)
Create Date: 2026-10-31

Until this revision Ryozan had two schema paths. Production was built at every
boot by ``main._run_startup_migrations`` (``Base.metadata.create_all`` plus about
a hundred hand-written CREATE/ALTER statements) and never ran Alembic; the 58
Alembic revisions were exercised only locally and in CI. This revision replaces
both. The superseded revisions are deleted from the tree (git history keeps
them, last present at 0e7f54d).

The DDL is ``alembic/baseline/20261031_baseline_schema.sql``: a schema-only
``pg_dump`` of a scratch Postgres 16 database built by that boot path at
origin/main 0e7f54d, normalized by ``app.core.schema_compare.normalize_pg_dump``
and regenerated with ``scripts/schema_dump.py --write-baseline``. It is not
hand-written. ``20261031_baseline_schema.json`` is the catalog snapshot of the
same database that ``scripts/adopt_schema.py`` compares live databases against.

A database that already has the Ryozan tables (production, any database the old
boot path built) must NOT run this revision. Adopt it instead:

    python scripts/adopt_schema.py            # dry run: prints the diff
    python scripts/adopt_schema.py --apply    # reconciles, then stamps this revision

The guard below refuses loudly instead of half-applying.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op
from app.core.schema_compare import BASELINE_SQL_PATH

revision: str = "20261031_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql = BASELINE_SQL_PATH.read_text(encoding="utf-8")
    if context.is_offline_mode():
        op.execute(sql)
        return

    bind = op.get_bind()
    existing = sorted(
        name
        for name in sa.inspect(bind).get_table_names(schema="public")
        if name.startswith(("bttco_", "viber_")) or name == "feedback_samples"
    )
    if existing:
        raise RuntimeError(
            f"Refusing to create the baseline schema: {len(existing)} Ryozan tables already exist "
            f"(e.g. {', '.join(existing[:3])}) but this database has no Alembic revision. It was built "
            "by the old boot path. Back it up, then run `python scripts/adopt_schema.py` (dry run) and "
            "`python scripts/adopt_schema.py --apply` to verify it against the baseline and stamp it."
        )
    # One multi-statement script, run as-is inside Alembic's transaction: the
    # baseline is created completely or not at all.
    bind.exec_driver_sql(sql)


def downgrade() -> None:
    raise NotImplementedError(
        "The baseline is the root of the schema; downgrading it would drop every Ryozan table. "
        "Restore from a backup instead."
    )
