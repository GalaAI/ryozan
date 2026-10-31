"""Read a live Postgres schema, compare it with the Alembic baseline, plan safe fixes.

Ryozan has one schema path: ``alembic upgrade head``. The first revision,
``20261031_baseline``, creates the schema exactly as the old boot path left it
(``Base.metadata.create_all`` plus the hand-written DDL that used to live in
``main.py``). A database that was built by that old boot path has every table
but no ``alembic_version`` row, so before it can join the migration chain it
has to be shown to match the baseline and then stamped. ``scripts/adopt_schema.py``
does that; this module is the reusable part.

Postgres is the authority on its own catalog, so definitions come from its
deparse functions (``format_type``, ``pg_get_expr``, ``pg_get_indexdef``,
``pg_get_constraintdef``) rather than from a reimplementation of them.
Everything is read with ``search_path = pg_catalog`` so object references are
always schema-qualified and the text is stable across sessions.

Scope is the tables Ryozan owns: every table in the baseline plus any live
``bttco_``/``ryozan_``/``viber_`` table, because production shares its database
with other systems. ``bttco_`` is the baseline's prefix (what an unadopted
legacy database has); revision ``20261031_ryozan_prefix`` renames it to
``ryozan_``, the prefix ``alembic/env.py`` scopes autogenerate to.
Column order is deliberately not compared: a column added later by
``ALTER TABLE`` sits at the end of the table, which changes nothing a query sees.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OWNED_TABLE_PREFIXES = ("bttco_", "ryozan_", "viber_")
IGNORED_TABLES = frozenset({"alembic_version"})

API_ROOT = Path(__file__).resolve().parents[2]
BASELINE_REVISION = "20261031_baseline"
BASELINE_DIR = API_ROOT / "alembic" / "baseline"
BASELINE_SQL_PATH = BASELINE_DIR / f"{BASELINE_REVISION}_schema.sql"
BASELINE_SNAPSHOT_PATH = BASELINE_DIR / f"{BASELINE_REVISION}_schema.json"

_TABLES_SQL = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull AS not_null,
       pg_get_expr(d.adbin, d.adrelid) AS default_expr,
       a.attidentity AS identity,
       a.attgenerated AS generated
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attname
"""

# Indexes that back a constraint are reported with the constraint, not twice.
_INDEXES_SQL = """
SELECT t.relname AS table_name,
       i.relname AS index_name,
       pg_get_indexdef(i.oid) AS definition,
       x.indisvalid AS valid
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
  AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = x.indexrelid AND k.contype IN ('p', 'u', 'x'))
ORDER BY t.relname, i.relname
"""

# contype 'n' (NOT NULL as a catalog constraint, Postgres 18+) is excluded:
# nullability is compared per column through attnotnull on every version.
_CONSTRAINTS_SQL = """
SELECT t.relname AS table_name,
       k.conname AS constraint_name,
       k.contype AS kind,
       pg_get_constraintdef(k.oid) AS definition
FROM pg_constraint k
JOIN pg_class t ON t.oid = k.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND k.contype IN ('p', 'u', 'f', 'c', 'x')
ORDER BY t.relname, k.conname
"""

_TRIGGERS_SQL = """
SELECT t.relname AS table_name, g.tgname AS trigger_name, pg_get_triggerdef(g.oid) AS definition
FROM pg_trigger g
JOIN pg_class t ON t.oid = g.tgrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND NOT g.tgisinternal
ORDER BY t.relname, g.tgname
"""


def is_owned_table(name: str, baseline_tables: set[str] | frozenset[str] = frozenset()) -> bool:
    if name in IGNORED_TABLES:
        return False
    return name in baseline_tables or name.startswith(OWNED_TABLE_PREFIXES)


def snapshot_schema(sync_conn, baseline_tables: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """Return a JSON-serializable description of the owned part of a live schema.

    ``sync_conn`` is a synchronous SQLAlchemy ``Connection``. Read-only: it
    sets ``search_path`` to ``pg_catalog`` for the read (so every reference is
    schema-qualified) and restores the caller's value before returning.
    """
    from sqlalchemy import text

    previous = sync_conn.execute(text("SELECT current_setting('search_path')")).scalar()
    sync_conn.execute(text("SELECT set_config('search_path', 'pg_catalog', true)"))
    try:
        return _read_snapshot(sync_conn, baseline_tables)
    finally:
        sync_conn.execute(text("SELECT set_config('search_path', :value, true)"), {"value": previous})


def _read_snapshot(sync_conn, baseline_tables: set[str] | frozenset[str]) -> dict[str, Any]:
    from sqlalchemy import text

    tables = [row[0] for row in sync_conn.execute(text(_TABLES_SQL))]
    owned = {name for name in tables if is_owned_table(name, baseline_tables)}

    snapshot: dict[str, Any] = {"tables": {name: _empty_table() for name in sorted(owned)}}
    for row in sync_conn.execute(text(_COLUMNS_SQL)).mappings():
        if row["table_name"] in owned:
            snapshot["tables"][row["table_name"]]["columns"][row["column_name"]] = {
                "type": row["data_type"],
                "not_null": bool(row["not_null"]),
                "default": row["default_expr"],
                "identity": row["identity"] or "",
                "generated": row["generated"] or "",
            }
    for row in sync_conn.execute(text(_INDEXES_SQL)).mappings():
        if row["table_name"] in owned:
            snapshot["tables"][row["table_name"]]["indexes"][row["index_name"]] = {
                "definition": row["definition"],
                "valid": bool(row["valid"]),
            }
    for row in sync_conn.execute(text(_CONSTRAINTS_SQL)).mappings():
        if row["table_name"] in owned:
            snapshot["tables"][row["table_name"]]["constraints"][row["constraint_name"]] = {
                "kind": row["kind"],
                "definition": row["definition"],
            }
    for row in sync_conn.execute(text(_TRIGGERS_SQL)).mappings():
        if row["table_name"] in owned:
            snapshot["tables"][row["table_name"]]["triggers"][row["trigger_name"]] = row["definition"]
    return snapshot


def _empty_table() -> dict[str, dict]:
    return {"columns": {}, "indexes": {}, "constraints": {}, "triggers": {}}


def load_baseline_snapshot(path: Path = BASELINE_SNAPSHOT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_snapshot(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ── Comparison ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SchemaDifference:
    """One way the live schema departs from the baseline.

    ``key`` is stable and unique (``kind:object``); it is what an operator
    passes to ``adopt_schema.py --accept`` to accept an extra object explicitly.
    """

    kind: str
    table: str
    name: str
    expected: Any = None
    actual: Any = None

    @property
    def key(self) -> str:
        if self.kind.endswith("table"):
            return f"{self.kind}:{self.table}"
        return f"{self.kind}:{self.table}.{self.name}"

    def describe(self) -> str:
        detail = ""
        if self.expected is not None or self.actual is not None:
            detail = f"\n      baseline: {_short(self.expected)}\n      live:     {_short(self.actual)}"
        return f"{self.key}{detail}"


def _short(value: Any) -> str:
    return "-" if value is None else json.dumps(value, sort_keys=True, ensure_ascii=False)


_INDEX_NAME_RE = re.compile(r"^(CREATE (?:UNIQUE )?INDEX) \S+ ON ")


def index_shape(definition: str) -> str:
    """An index definition with its own name removed, for rename detection."""
    return _INDEX_NAME_RE.sub(r"\1 ON ", definition)


def compare_schemas(expected: dict[str, Any], actual: dict[str, Any]) -> list[SchemaDifference]:
    """Every difference between two snapshots, in a deterministic order."""
    diffs: list[SchemaDifference] = []
    exp_tables, act_tables = expected["tables"], actual["tables"]

    for table in sorted(set(exp_tables) - set(act_tables)):
        diffs.append(SchemaDifference("missing_table", table, table))
    for table in sorted(set(act_tables) - set(exp_tables)):
        diffs.append(SchemaDifference("extra_table", table, table))

    for table in sorted(set(exp_tables) & set(act_tables)):
        exp, act = exp_tables[table], act_tables[table]
        diffs.extend(_compare_columns(table, exp["columns"], act["columns"]))
        diffs.extend(_compare_named(table, "index", exp["indexes"], act["indexes"]))
        diffs.extend(_compare_named(table, "constraint", exp["constraints"], act["constraints"]))
        diffs.extend(_compare_named(table, "trigger", exp["triggers"], act["triggers"]))
    return diffs


def _compare_columns(table: str, exp: dict, act: dict) -> list[SchemaDifference]:
    diffs: list[SchemaDifference] = []
    for name in sorted(set(exp) - set(act)):
        diffs.append(SchemaDifference("missing_column", table, name, exp[name], None))
    for name in sorted(set(act) - set(exp)):
        diffs.append(SchemaDifference("extra_column", table, name, None, act[name]))
    for name in sorted(set(exp) & set(act)):
        e, a = exp[name], act[name]
        if e["type"] != a["type"]:
            diffs.append(SchemaDifference("column_type", table, name, e["type"], a["type"]))
        if e["not_null"] != a["not_null"]:
            diffs.append(SchemaDifference("column_nullability", table, name, e["not_null"], a["not_null"]))
        if e["default"] != a["default"]:
            diffs.append(SchemaDifference("column_default", table, name, e["default"], a["default"]))
        if (e["identity"], e["generated"]) != (a["identity"], a["generated"]):
            diffs.append(
                SchemaDifference(
                    "column_generation",
                    table,
                    name,
                    {"identity": e["identity"], "generated": e["generated"]},
                    {"identity": a["identity"], "generated": a["generated"]},
                )
            )
    return diffs


def _compare_named(table: str, noun: str, exp: dict, act: dict) -> list[SchemaDifference]:
    diffs: list[SchemaDifference] = []
    missing = sorted(set(exp) - set(act))
    extra = sorted(set(act) - set(exp))

    # Same object under another name: report one rename instead of a drop + add.
    shape = (lambda v: index_shape(v["definition"])) if noun == "index" else (lambda v: json.dumps(v, sort_keys=True))
    extra_by_shape: dict[str, list[str]] = {}
    for name in extra:
        extra_by_shape.setdefault(shape(act[name]), []).append(name)
    for name in list(missing):
        candidates = extra_by_shape.get(shape(exp[name]))
        if candidates:
            old = candidates.pop(0)
            missing.remove(name)
            extra.remove(old)
            diffs.append(SchemaDifference(f"renamed_{noun}", table, name, name, old))

    for name in missing:
        diffs.append(SchemaDifference(f"missing_{noun}", table, name, exp[name], None))
    for name in extra:
        diffs.append(SchemaDifference(f"extra_{noun}", table, name, None, act[name]))
    for name in sorted(set(exp) & set(act)):
        if noun == "index":
            if exp[name]["definition"] != act[name]["definition"]:
                diffs.append(SchemaDifference("index_definition", table, name, exp[name], act[name]))
            elif exp[name]["valid"] and not act[name]["valid"]:
                diffs.append(SchemaDifference("invalid_index", table, name, True, False))
        elif exp[name] != act[name]:
            diffs.append(SchemaDifference(f"{noun}_definition", table, name, exp[name], act[name]))
    return diffs


# ── Reconciliation ───────────────────────────────────────────────────────────

# Extra objects are never dropped: a live table, column, index or constraint the
# baseline does not have may carry data or enforce something someone relies on.
# They block adoption until an operator names them with --accept, after which
# they are left exactly as they are.
ACCEPTABLE_KINDS = frozenset({"extra_table", "extra_column", "extra_index", "extra_constraint", "extra_trigger"})

# What each reconcilable kind does. Everything is metadata-only or validated by
# Postgres inside the one adoption transaction, so a statement that cannot hold
# (a NULL under SET NOT NULL, a duplicate under a unique index, an orphan under
# a foreign key) aborts the whole adoption and leaves the database untouched.
RECONCILABLE_KINDS = {
    "missing_column": "ADD COLUMN with the baseline type/default/nullability (nullable or defaulted only)",
    "column_default": "ALTER COLUMN ... SET DEFAULT <baseline> / DROP DEFAULT",
    "column_nullability": "ALTER COLUMN ... DROP NOT NULL, or SET NOT NULL (Postgres validates every row)",
    "column_type": "ALTER COLUMN ... TYPE, only to widen character varying(n) to a larger n or to text",
    "missing_index": "CREATE INDEX from the baseline definition (a unique one is validated against the data)",
    "invalid_index": "REINDEX INDEX (an INVALID leftover of a failed CREATE INDEX CONCURRENTLY)",
    "renamed_index": "ALTER INDEX ... RENAME TO <baseline name> (identical definition)",
    "missing_constraint": "ALTER TABLE ... ADD CONSTRAINT from the baseline definition (validated against the data)",
    "renamed_constraint": "ALTER TABLE ... RENAME CONSTRAINT ... TO <baseline name> (identical definition)",
}


@dataclass
class ReconcilePlan:
    statements: list[str] = field(default_factory=list)
    reconciled: list[SchemaDifference] = field(default_factory=list)
    accepted: list[SchemaDifference] = field(default_factory=list)
    refused: list[tuple[SchemaDifference, str]] = field(default_factory=list)

    @property
    def can_apply(self) -> bool:
        return not self.refused


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table(table: str) -> str:
    return f"public.{_q(table)}"


_VARCHAR_RE = re.compile(r"^character varying\((\d+)\)$")


def _is_varchar_widening(expected: str, actual: str) -> bool:
    want, have = _VARCHAR_RE.match(expected), _VARCHAR_RE.match(actual)
    if have is None:
        return False
    if expected == "text":
        return True
    return want is not None and int(want.group(1)) > int(have.group(1))


def plan_reconciliation(
    diffs: list[SchemaDifference], accept: set[str] | frozenset[str] = frozenset()
) -> ReconcilePlan:
    """Turn differences into explicit statements, or refuse.

    Pure: no database access. Statement order is columns, then constraints that
    depend on them, then indexes, so a unique index or foreign key added here
    always finds its columns already present.
    """
    plan = ReconcilePlan()
    column_sql: list[str] = []
    constraint_sql: list[str] = []
    index_sql: list[str] = []

    for diff in diffs:
        t = _table(diff.table)
        if diff.kind in ACCEPTABLE_KINDS:
            if diff.key in accept:
                plan.accepted.append(diff)
            else:
                plan.refused.append(
                    (diff, f"not in the baseline; never dropped. Re-run with --accept {diff.key} to keep it as is")
                )
            continue

        if diff.kind == "missing_column":
            spec = diff.expected
            if spec["identity"] or spec["generated"]:
                plan.refused.append((diff, "identity/generated column; add it by hand"))
                continue
            if spec["not_null"] and spec["default"] is None:
                plan.refused.append((diff, "NOT NULL with no default cannot be added to a table that has rows"))
                continue
            sql = f"ALTER TABLE {t} ADD COLUMN {_q(diff.name)} {spec['type']}"
            if spec["default"] is not None:
                sql += f" DEFAULT {spec['default']}"
            if spec["not_null"]:
                sql += " NOT NULL"
            column_sql.append(sql)
        elif diff.kind == "column_default":
            if diff.expected is None:
                column_sql.append(f"ALTER TABLE {t} ALTER COLUMN {_q(diff.name)} DROP DEFAULT")
            else:
                column_sql.append(f"ALTER TABLE {t} ALTER COLUMN {_q(diff.name)} SET DEFAULT {diff.expected}")
        elif diff.kind == "column_nullability":
            verb = "SET NOT NULL" if diff.expected else "DROP NOT NULL"
            column_sql.append(f"ALTER TABLE {t} ALTER COLUMN {_q(diff.name)} {verb}")
        elif diff.kind == "column_type":
            if not _is_varchar_widening(diff.expected, diff.actual):
                plan.refused.append((diff, "only widening character varying is reconciled automatically"))
                continue
            column_sql.append(f"ALTER TABLE {t} ALTER COLUMN {_q(diff.name)} TYPE {diff.expected}")
        elif diff.kind == "missing_index":
            index_sql.append(diff.expected["definition"])
        elif diff.kind == "invalid_index":
            index_sql.append(f"REINDEX INDEX public.{_q(diff.name)}")
        elif diff.kind == "renamed_index":
            index_sql.append(f"ALTER INDEX public.{_q(diff.actual)} RENAME TO {_q(diff.expected)}")
        elif diff.kind == "missing_constraint":
            constraint_sql.append(f"ALTER TABLE {t} ADD CONSTRAINT {_q(diff.name)} {diff.expected['definition']}")
        elif diff.kind == "renamed_constraint":
            constraint_sql.append(f"ALTER TABLE {t} RENAME CONSTRAINT {_q(diff.actual)} TO {_q(diff.expected)}")
        else:
            plan.refused.append((diff, "no safe automatic reconciliation; resolve by hand and re-run"))
            continue
        plan.reconciled.append(diff)

    plan.statements = column_sql + constraint_sql + index_sql
    return plan


# ── pg_dump normalization (baseline generation and schema-equality proofs) ───

_DUMP_DROP_LINE = re.compile(r"^(--.*|\\(un)?restrict .*|SET .*;|SELECT pg_catalog\.set_config\(.*\);)$")


def normalize_pg_dump(text: str) -> str:
    """Reduce ``pg_dump --schema-only`` output to its DDL.

    Removes comments (they carry the server and client versions), the random
    ``\\restrict`` key newer pg_dump clients emit, and session ``SET``
    statements, then collapses blank-line runs. What remains is the ordered DDL,
    which pg_dump already emits in a deterministic dependency/name order, so two
    databases with the same schema normalize to identical text.
    """
    lines = [line.rstrip() for line in text.splitlines() if not _DUMP_DROP_LINE.match(line.rstrip())]
    out: list[str] = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"
