"""Print a normalized ``pg_dump --schema-only`` of a LOCAL database, or write the baseline from it.

    python scripts/schema_dump.py postgresql://postgres:pw@127.0.0.1:55432/a > a.sql
    python scripts/schema_dump.py postgresql://postgres:pw@127.0.0.1:55432/b > b.sql
    diff a.sql b.sql            # empty when the two schemas are identical

    python scripts/schema_dump.py <db-built-the-old-way> --write-baseline --verify-url <empty-db>

``--write-baseline`` is how ``alembic/baseline/20261031_baseline_schema.{sql,json}``
were produced: from a scratch database built by the pre-baseline boot path
(``main._run_startup_migrations`` at origin/main 0e7f54d), never by hand.
Normalization is ``app.core.schema_compare.normalize_pg_dump``: comments, the
random ``\\restrict`` key and session ``SET`` lines are removed, nothing else.

A dump is not always a fixed point: Postgres deparses ``col IN ('a', 'b')`` on a
varchar column as ``ANY ((ARRAY['a'::varchar, ...])::text[])``, and re-parsing
that text pushes the cast into the elements, a different (equivalent) stored
expression. So the dump is replayed into the empty ``--verify-url`` database
and compared object by object. Any CHECK constraint or index whose replay
differs gets the source text it was originally created from, which is the
ORM's own compiled DDL, frozen into the file at generation time; the replay is
then repeated and must match exactly or nothing is written.

pg_dump is taken from ``PG_DUMP`` if set, else from PATH. Refuses any host that
is not localhost: this tool is for scratch databases, never production.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import psycopg2_url  # noqa: E402
from app.core.schema_compare import (  # noqa: E402
    BASELINE_SNAPSHOT_PATH,
    BASELINE_SQL_PATH,
    compare_schemas,
    dump_snapshot,
    normalize_pg_dump,
    snapshot_schema,
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _plain_url(url: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def _require_local(url: str) -> None:
    if urlparse(url).hostname not in _LOCAL_HOSTS:
        raise SystemExit("Refusing: this tool only touches local scratch databases.")


def normalized_dump(url: str) -> str:
    pg_dump = os.environ.get("PG_DUMP") or shutil.which("pg_dump")
    if not pg_dump:
        raise SystemExit("pg_dump not found: set PG_DUMP or put it on PATH")
    result = subprocess.run(
        [pg_dump, "--schema-only", "--no-owner", "--no-privileges", "--dbname", url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"pg_dump failed: {result.stderr.strip()}")
    return normalize_pg_dump(result.stdout)


def _snapshot(url: str, tables: set[str] | None = None) -> tuple[dict, set[str]]:
    import sqlalchemy as sa

    engine = sa.create_engine(psycopg2_url(url))
    try:
        with engine.begin() as conn:
            every_table = set(sa.inspect(conn).get_table_names(schema="public")) - {"alembic_version"}
            return snapshot_schema(conn, tables if tables is not None else every_table), every_table
    finally:
        engine.dispose()


def _assert_empty(verify_url: str) -> None:
    import sqlalchemy as sa

    engine = sa.create_engine(psycopg2_url(verify_url))
    try:
        with engine.connect() as conn:
            if sa.inspect(conn).get_table_names(schema="public"):
                raise SystemExit("Refusing: --verify-url must point at an EMPTY database.")
    finally:
        engine.dispose()


def _replay(sql: str, verify_url: str) -> None:
    """Run the candidate baseline in one transaction on the (empty) verify database."""
    import sqlalchemy as sa

    engine = sa.create_engine(psycopg2_url(verify_url))
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(sql)
    finally:
        engine.dispose()


def _reset(verify_url: str) -> None:
    """Empty the verify database again. Only ever called on a database _assert_empty passed."""
    import sqlalchemy as sa

    engine = sa.create_engine(psycopg2_url(verify_url))
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    finally:
        engine.dispose()


def _orm_source_ddl() -> tuple[dict[str, str], dict[str, str]]:
    """CHECK expressions and CREATE INDEX statements exactly as the ORM emits them."""
    from sqlalchemy import CheckConstraint
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    from app.api.v1 import router as _router  # noqa: F401  registers every model
    from app.core.database import Base

    dialect = postgresql.dialect()
    checks: dict[str, str] = {}
    indexes: dict[str, str] = {}
    for table in Base.metadata.sorted_tables:
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and constraint.name:
                checks[str(constraint.name)] = str(
                    constraint.sqltext.compile(dialect=dialect, compile_kwargs={"literal_binds": True})
                )
        for index in table.indexes:
            indexes[str(index.name)] = str(CreateIndex(index).compile(dialect=dialect)).strip()
    return checks, indexes


def _substitute(sql: str, constraint_names: set[str], index_names: set[str]) -> str:
    checks, indexes = _orm_source_ddl()
    out: list[str] = []
    for line in sql.split("\n"):
        check = re.match(r"^(\s*CONSTRAINT (\S+) CHECK )\(.*\)(,?)$", line)
        index = re.match(r"^CREATE (?:UNIQUE )?INDEX (\S+) ON ", line)
        if check and check.group(2) in constraint_names:
            name = check.group(2)
            if name not in checks:
                raise SystemExit(f"{name} does not round-trip and has no ORM source to rebuild it from")
            line = f"{check.group(1)}({checks[name]}){check.group(3)}"
        elif index and index.group(1) in index_names:
            name = index.group(1)
            if name not in indexes:
                raise SystemExit(f"{name} does not round-trip and has no ORM source to rebuild it from")
            line = " ".join(indexes[name].split()) + ";"
        out.append(line)
    return "\n".join(out)


def write_baseline(source_url: str, verify_url: str) -> int:
    dump = normalized_dump(source_url)
    source, tables = _snapshot(source_url)

    _assert_empty(verify_url)
    sql = dump
    for attempt in (1, 2):
        try:
            _replay(sql, verify_url)
            replayed, _ = _snapshot(verify_url, tables)
        finally:
            _reset(verify_url)
        diffs = compare_schemas(source, replayed)
        if not diffs:
            break
        if attempt == 2:
            for diff in diffs:
                print(f"still differs after substitution: {diff.describe()}", file=sys.stderr)
            return 1
        unsupported = [d for d in diffs if d.kind not in {"constraint_definition", "index_definition"}]
        if unsupported:
            for diff in unsupported:
                print(f"dump does not replay: {diff.describe()}", file=sys.stderr)
            return 1
        constraints = {d.name for d in diffs if d.kind == "constraint_definition"}
        index_names = {d.name for d in diffs if d.kind == "index_definition"}
        print(f"not a fixed point, rebuilt from ORM source: {sorted(constraints | index_names)}")
        sql = _substitute(dump, constraints, index_names)

    BASELINE_SQL_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_SQL_PATH.write_text(sql, encoding="utf-8", newline="\n")
    BASELINE_SNAPSHOT_PATH.write_text(dump_snapshot(source), encoding="utf-8", newline="\n")
    print(f"wrote {BASELINE_SQL_PATH} ({sql.count('CREATE TABLE')} tables, replay verified identical)")
    print(f"wrote {BASELINE_SNAPSHOT_PATH} ({len(source['tables'])} tables)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database_url")
    parser.add_argument("--write-baseline", action="store_true", help="write the baseline SQL and snapshot files")
    parser.add_argument("--verify-url", help="an EMPTY local database to replay the baseline into (required to write)")
    args = parser.parse_args(argv)

    url = _plain_url(args.database_url)
    _require_local(url)
    if not args.write_baseline:
        sys.stdout.write(normalized_dump(url))
        return 0
    if not args.verify_url:
        parser.error("--write-baseline needs --verify-url")
    verify_url = _plain_url(args.verify_url)
    _require_local(verify_url)
    return write_baseline(url, verify_url)


if __name__ == "__main__":
    raise SystemExit(main())
