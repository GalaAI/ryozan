"""Adopt a database built by the old boot path into the Alembic chain.

Before 2026-10-31 production (and every local database the old ``main.py``
built) got its schema from ``Base.metadata.create_all`` plus hand-written DDL
at boot and has no ``alembic_version`` table. ``alembic upgrade head`` refuses
such a database (the baseline would collide with the existing tables). This
script is the one-time bridge:

    python scripts/adopt_schema.py                 # dry run (default): diff only, changes nothing
    python scripts/adopt_schema.py --apply         # reconcile listed differences, stamp the baseline
    python scripts/adopt_schema.py --apply --accept extra_table:bttco_old_thing

It connects to ``DATABASE_URL`` (the same setting the API uses) and:

1. refuses unless the database has no ``alembic_version`` table;
2. compares the live schema with the baseline snapshot
   (``alembic/baseline/20261031_baseline_schema.json``): tables, columns, types,
   nullability, defaults, indexes, constraints and triggers of the tables
   Ryozan owns, ignoring column order;
3. prints every difference and what would be done about it;
4. with ``--apply``, and only when every difference is either reconcilable
   (``schema_compare.RECONCILABLE_KINDS``) or an extra object explicitly
   named with ``--accept``, runs the listed statements, re-compares, stamps
   ``20261031_baseline`` in ``alembic_version``, and commits, all in ONE
   transaction. Any failure rolls everything back.

Adoption is always against the baseline, so the names it compares and accepts
are the baseline's ``bttco_`` names. After it stamps ``20261031_baseline``,
``alembic upgrade head`` applies the later revisions, including
``20261031_ryozan_prefix``, which renames them to ``ryozan_``.

Nothing is ever dropped. Back the database up first (see the runbook in
``alembic/README.md``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENABLE_API_DOCS", "false")
os.environ.setdefault("ENABLE_CONFIG_HEALTH", "false")

import sqlalchemy as sa  # noqa: E402

from app.core.schema_compare import (  # noqa: E402
    BASELINE_REVISION,
    RECONCILABLE_KINDS,
    ReconcilePlan,
    SchemaDifference,
    compare_schemas,
    load_baseline_snapshot,
    plan_reconciliation,
    snapshot_schema,
)


def _sync_url(url: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def _redacted(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.hostname}:{parsed.port or 5432}{parsed.path}"


def _has_alembic_version(conn) -> bool:
    return bool(conn.execute(sa.text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar())


def diff_live_schema(conn, baseline: dict) -> list[SchemaDifference]:
    """Compare the database behind ``conn`` with a baseline snapshot."""
    live = snapshot_schema(conn, set(baseline["tables"]))
    return compare_schemas(baseline, live)


def _stamp_baseline(conn) -> None:
    """Record the baseline revision with Alembic's own stamp, on this connection and transaction.

    Alembic owns alembic_version (its DDL, constraint name and row), so it
    writes it; the table comes out identical to the one `alembic upgrade head`
    creates on an empty database.
    """
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    MigrationContext.configure(connection=conn).stamp(ScriptDirectory.from_config(config), BASELINE_REVISION)


def _print_report(diffs: list[SchemaDifference], plan: ReconcilePlan) -> None:
    if not diffs:
        print("Schema diff: EMPTY. The live schema matches the baseline exactly.")
        return
    print(f"Schema diff: {len(diffs)} difference(s)")
    for diff in plan.reconciled:
        print(f"  RECONCILE  {diff.describe()}\n      action:   {RECONCILABLE_KINDS[diff.kind]}")
    for diff in plan.accepted:
        print(f"  ACCEPTED   {diff.describe()}\n      action:   left exactly as it is (--accept)")
    for diff, reason in plan.refused:
        print(f"  REFUSED    {diff.describe()}\n      reason:   {reason}")
    if plan.statements:
        print("\nStatements --apply would run, in order, in one transaction:")
        for statement in plan.statements:
            print(f"  {statement};")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="report only (default)")
    mode.add_argument("--apply", action="store_true", help="reconcile and stamp the baseline")
    parser.add_argument(
        "--accept",
        action="append",
        default=[],
        metavar="KEY",
        help="keep an extra live object the baseline does not have, e.g. extra_column:bttco_users.legacy_flag",
    )
    parser.add_argument("--database-url", help="defaults to DATABASE_URL from the environment / api settings")
    args = parser.parse_args(argv)

    if args.database_url:
        url = args.database_url
    else:
        from app.core.config import get_settings

        url = get_settings().DATABASE_URL
    url = _sync_url(url)
    baseline = load_baseline_snapshot()
    print(f"Database: {_redacted(url)}")
    print(f"Baseline: {BASELINE_REVISION} ({len(baseline['tables'])} tables)")

    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn, conn.begin():
            if _has_alembic_version(conn):
                version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
                print(
                    f"REFUSED: alembic_version already exists (revision {version}). This database is "
                    "managed by Alembic; use `alembic upgrade head`, not adoption."
                )
                return 2
            diffs = diff_live_schema(conn, baseline)
            plan = plan_reconciliation(diffs, set(args.accept))
            _print_report(diffs, plan)
            unused = sorted(set(args.accept) - {d.key for d in plan.accepted})
            if unused:
                print(f"\nREFUSED: --accept named nothing in the diff: {', '.join(unused)}")
                return 2

            if not args.apply:
                verdict = "can be adopted" if plan.can_apply else "can NOT be adopted as it stands"
                print(f"\nDry run: nothing changed. This database {verdict}.")
                return 0 if plan.can_apply else 1

            if not plan.can_apply:
                print("\nREFUSED: resolve the REFUSED differences above, then re-run. Nothing changed.")
                return 1

            for statement in plan.statements:
                print(f"applying: {statement}")
                conn.exec_driver_sql(statement)

            remaining = [d for d in diff_live_schema(conn, baseline) if d.key not in set(args.accept)]
            if remaining:
                print("\nREFUSED: the schema still differs after reconciliation; rolling back:")
                for diff in remaining:
                    print(f"  {diff.describe()}")
                raise SystemExit(1)  # raising out of conn.begin() rolls everything back

            _stamp_baseline(conn)
            # leaving conn.begin() commits: reconciliation and stamp land together or not at all
        print(f"\nAdopted: {len(plan.statements)} statement(s) applied, stamped {BASELINE_REVISION}.")
        print(
            "Next: deploy. The container runs `alembic upgrade head` before the API starts, which applies "
            "every later revision (20261031_ryozan_prefix renames the bttco_ objects to ryozan_)."
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
