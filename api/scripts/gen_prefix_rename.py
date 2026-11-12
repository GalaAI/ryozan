"""Generate the explicit RENAME statements of the 20261031_ryozan_prefix revision.

The app began as a studio called BTTCO, so the baseline schema names every
table, index and constraint with a ``bttco`` prefix. The revision
``alembic/versions/20261031_ryozan_prefix.py`` renames them to ``ryozan``. It
holds literal, deterministic ``ALTER ... RENAME`` statements, not catalog
queries, so what runs in production is exactly what was reviewed. This script
writes those literals from the catalog of a database at the baseline, and must
be run against a Postgres 18 database (production's version) so the NOT NULL
lists are filled:

    alembic upgrade 20261031_baseline                       # on an EMPTY scratch Postgres 18 database
    python scripts/gen_prefix_rename.py <scratch-db-url>    # prints the statement lists
    python scripts/gen_prefix_rename.py <scratch-db-url> --check alembic/versions/20261031_ryozan_prefix.py

Postgres is the authority on what exists, so the names come from its catalog
(``pg_class``, ``pg_constraint``), not from parsing the baseline SQL:

* tables (``relkind`` r/p) named ``bttco_*``      -> ``ALTER TABLE ... RENAME TO``
* sequences named ``*bttco*``                     -> ``ALTER SEQUENCE ... RENAME TO``
* constraints (p/u/f/c/x) named ``*bttco*``       -> ``ALTER TABLE ... RENAME CONSTRAINT``
  (renaming a primary-key/unique/exclusion constraint renames its index too)
* other indexes named ``*bttco*``                 -> ``ALTER INDEX ... RENAME TO``
* Postgres 18+ only: NOT NULL constraints (``contype = 'n'``) named ``*bttco*``
  -> ``ALTER TABLE ... RENAME CONSTRAINT``, in separate ``NOT_NULL_*`` lists the
  revision runs only on a server >= 18. Postgres 18 records every NOT NULL as a
  named catalog constraint (``<table>_<column>_not_null``); earlier versions
  have no such object, so there is nothing to rename there.

Views, materialized views, functions, triggers, rules, policies and standalone
types are refused: none exist at the baseline, and if one ever did it might
need its body rewritten, not just a rename. Every new name is checked to fit
Postgres' 63-byte identifier limit (a longer one would be silently truncated)
and not to collide with an existing name.

``--check FILE`` fails unless FILE's lists equal what the catalog yields, so the
committed revision is provably the generated one (the NOT_NULL lists are only
comparable on a Postgres 18+ database).
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import sqlalchemy as sa

# The prefix with its underscore: the column ``bttcoin_balance`` (the BTTCoin
# currency, not the studio prefix) appears inside the Postgres 18 NOT NULL name
# ``bttco_users_bttcoin_balance_not_null`` and must come out as
# ``ryozan_users_bttcoin_balance_not_null``, the name Postgres gives that
# constraint on a table created as ``ryozan_users``.
OLD, NEW = "bttco_", "ryozan_"
MAX_IDENTIFIER = 63
LISTS = ("UPGRADE", "DOWNGRADE", "NOT_NULL_UPGRADE", "NOT_NULL_DOWNGRADE")

_TABLES = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname LIKE 'bttco\\_%' ORDER BY c.relname
"""
_SEQUENCES = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'S' AND c.relname LIKE '%bttco%' ORDER BY c.relname
"""
_CONSTRAINTS = """
SELECT t.relname AS table_name, k.conname, k.contype
FROM pg_constraint k JOIN pg_class t ON t.oid = k.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND k.contype IN ('p', 'u', 'f', 'c', 'x', 'n') AND k.conname LIKE '%bttco%'
ORDER BY t.relname, k.conname
"""
_INDEXES = """
SELECT t.relname AS table_name, i.relname AS index_name
FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND i.relname LIKE '%bttco%'
  AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = x.indexrelid AND k.contype IN ('p', 'u', 'x'))
ORDER BY t.relname, i.relname
"""
# Objects a rename would not carry along correctly. Each must come back empty.
_REFUSED = {
    "views/materialized views": """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm')""",
    "functions/procedures": """
        SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'""",
    "user triggers": """
        SELECT g.tgname FROM pg_trigger g JOIN pg_class t ON t.oid = g.tgrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'public' AND NOT g.tgisinternal""",
    "types named *bttco* (other than table row and array types)": """
        SELECT y.typname FROM pg_type y JOIN pg_namespace n ON n.oid = y.typnamespace
        WHERE n.nspname = 'public' AND y.typname LIKE '%bttco%' AND y.typrelid = 0
          AND y.typname NOT LIKE '\\_%'""",
    "rules": """
        SELECT r.rulename FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind <> 'v'""",
    "policies": """
        SELECT p.polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'""",
}
_ALL_NAMES = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'
UNION SELECT k.conname FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace WHERE n.nspname = 'public'
"""


def renamed(name: str) -> str:
    return name.replace(OLD, NEW)


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _t(name: str) -> str:
    return f"public.{_q(name)}"


def generate(conn) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Return ({list name: statements}, counts) for the database behind conn."""
    problems: list[str] = []
    for what, sql in _REFUSED.items():
        found = [row[0] for row in conn.execute(sa.text(sql))]
        if found:
            problems.append(f"{what} exist and are not handled by a plain rename: {', '.join(found)}")

    tables = [row[0] for row in conn.execute(sa.text(_TABLES))]
    sequences = [row[0] for row in conn.execute(sa.text(_SEQUENCES))]
    all_constraints = list(conn.execute(sa.text(_CONSTRAINTS)).mappings())
    constraints = [c for c in all_constraints if c["contype"] != "n"]
    not_nulls = [c for c in all_constraints if c["contype"] == "n"]
    indexes = list(conn.execute(sa.text(_INDEXES)).mappings())

    existing = {row[0] for row in conn.execute(sa.text(_ALL_NAMES))}
    old_names = (
        set(tables) | set(sequences) | {c["conname"] for c in all_constraints} | {i["index_name"] for i in indexes}
    )
    for old in sorted(old_names):
        new = renamed(old)
        if len(new.encode("utf-8")) > MAX_IDENTIFIER:
            problems.append(f"{old} -> {new} is longer than {MAX_IDENTIFIER} bytes and would be truncated")
        if new in existing and new not in old_names:
            problems.append(f"{old} -> {new} collides with an existing object")
    if problems:
        raise SystemExit("REFUSED:\n  " + "\n  ".join(problems))

    def rename_constraint(c, *, back: bool = False) -> str:
        old, new = c["conname"], renamed(c["conname"])
        if back:
            old, new = new, old
        return f"ALTER TABLE {_t(renamed(c['table_name']))} RENAME CONSTRAINT {_q(old)} TO {_q(new)}"

    up: list[str] = []
    up += [f"ALTER TABLE {_t(name)} RENAME TO {_q(renamed(name))}" for name in tables]
    up += [f"ALTER SEQUENCE {_t(name)} RENAME TO {_q(renamed(name))}" for name in sequences]
    up += [rename_constraint(c) for c in constraints]
    up += [f"ALTER INDEX {_t(i['index_name'])} RENAME TO {_q(renamed(i['index_name']))}" for i in indexes]

    # Downgrade: the exact inverse, in reverse order (indexes and constraints
    # while their tables still carry the new name, tables last).
    down: list[str] = []
    down += [f"ALTER INDEX {_t(renamed(i['index_name']))} RENAME TO {_q(i['index_name'])}" for i in reversed(indexes)]
    down += [rename_constraint(c, back=True) for c in reversed(constraints)]
    down += [f"ALTER SEQUENCE {_t(renamed(name))} RENAME TO {_q(name)}" for name in reversed(sequences)]
    down += [f"ALTER TABLE {_t(renamed(name))} RENAME TO {_q(name)}" for name in reversed(tables)]

    lists = {
        "UPGRADE": up,
        "DOWNGRADE": down,
        # Run after UPGRADE's table renames, and before DOWNGRADE's.
        "NOT_NULL_UPGRADE": [rename_constraint(c) for c in not_nulls],
        "NOT_NULL_DOWNGRADE": [rename_constraint(c, back=True) for c in reversed(not_nulls)],
    }

    kinds = {"p": "primary_key", "u": "unique", "f": "foreign_key", "c": "check", "x": "exclusion"}
    counts = {"tables": len(tables), "sequences": len(sequences), "indexes": len(indexes)}
    for c in constraints:
        key = f"constraints_{kinds[c['contype']]}"
        counts[key] = counts.get(key, 0) + 1
    counts["constraints"] = len(constraints)
    counts["not_null_constraints_pg18"] = len(not_nulls)
    return lists, counts


def _literal_list(name: str, statements: list[str]) -> str:
    body = "".join(f"    {s!r},\n" for s in statements)
    return f"{name}: tuple[str, ...] = (\n{body})\n"


def _read_lists(path: Path) -> dict[str, list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.target.id: list(ast.literal_eval(node.value))
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in LISTS
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database_url", help="a scratch database at 20261031_baseline (read only)")
    parser.add_argument("--check", type=Path, help="revision file whose statement lists must equal the output")
    args = parser.parse_args(argv)

    # SQLAlchemy 2.1 maps bare postgresql:// to psycopg (v3); Ryozan installs psycopg2.
    url = args.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            if conn.execute(sa.text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar():
                head = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
                if head != "20261031_baseline":
                    raise SystemExit(f"REFUSED: database is at {head}, generate from 20261031_baseline")
            server = int(conn.execute(sa.text("SHOW server_version_num")).scalar())
            lists, counts = generate(conn)
    finally:
        engine.dispose()

    if args.check:
        committed = _read_lists(args.check)
        names = LISTS if server >= 180000 else LISTS[:2]
        ok = True
        for name in names:
            same = committed.get(name) == lists[name]
            ok = ok and same
            print(f"  {name}: {'matches' if same else 'DIFFERS'} ({len(lists[name])} statements from the catalog)")
        if server < 180000:
            print(f"  NOT_NULL_*: not checked (server {server} < 180000 has no NOT NULL constraint objects)")
        print(f"{args.check}: {'matches' if ok else 'DIFFERS FROM'} the catalog")
        return 0 if ok else 1

    if server < 180000:
        sys.stderr.write("WARNING: server < 18, so the NOT_NULL lists are empty; generate on Postgres 18.\n")
    sys.stdout.write("\n".join(_literal_list(name, lists[name]) for name in LISTS))
    sys.stderr.write(" ".join(f"{k}={v}" for k, v in sorted(counts.items())) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
