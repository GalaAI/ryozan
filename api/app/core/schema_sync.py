"""Additive column reconciliation for the startup schema path.

Production builds its schema at boot with ``Base.metadata.create_all()``, which
creates tables that do not exist yet and never touches one that does. A brand
new table therefore appears in production automatically, but a new column on an
existing table is silently skipped. The ORM then selects a column the database
does not have, and every query against that table raises ``UndefinedColumn`` on
the first request after the deploy -- not at deploy time, when someone is
watching.

``main.py`` has papered over this with a hand-maintained list of
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` statements. That works only when
whoever adds a model column also remembers to add a line of SQL. This module
derives the same statements from the ORM metadata instead, so the two cannot
drift: if it is on the model and the table already exists, it gets added.

Additive by construction. It emits ``ADD COLUMN`` and nothing else -- never a
drop, rename, retype, constraint, or index -- and it refuses any ``NOT NULL``
column with no server default, because that cannot be applied to a table that
already has rows. Refusals are reported, not silently swallowed.

This complements ``create_all`` rather than replacing it: ``create_all`` owns
new tables, this owns new columns on existing tables. Alembic remains the
source of history for local work and CI, but it is not what builds production.
The separately invoked POS v1 installer below owns only its explicit versioned
constraints and indexes; the general column planner still never infers them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

if TYPE_CHECKING:
    from sqlalchemy import MetaData, Table
    from sqlalchemy.engine.interfaces import Dialect
    from sqlalchemy.schema import Column


@dataclass
class ReconcilePlan:
    """What reconciliation would do, separated from doing it."""

    statements: list[str] = field(default_factory=list)
    # (table.column, reason) for columns that are missing but cannot be added safely.
    refused: list[tuple[str, str]] = field(default_factory=list)
    # Tables on the model that do not exist in the database at all. create_all
    # owns those; recorded so a caller can tell "not yet created" from "drifted".
    absent_tables: list[str] = field(default_factory=list)


def _is_safely_additive(column: Column) -> tuple[bool, str]:
    """A column can be added to a populated table only if existing rows get a value.

    A Python-side ``default=`` does not count: SQLAlchemy applies it on insert,
    so it leaves rows already in the table with nothing. Postgres needs the
    column to be nullable or to carry a server default.
    """
    if column.primary_key:
        return False, "primary key column cannot be added to an existing table"
    if column.nullable:
        return True, ""
    if column.server_default is not None or column.server_onupdate is not None:
        return True, ""
    if column.default is not None:
        return (
            False,
            "NOT NULL with a Python-side default only; existing rows would violate it. "
            "Add server_default=... or make it nullable.",
        )
    return False, "NOT NULL with no server default; existing rows would violate it"


def _add_column_sql(table: Table, column: Column, dialect: Dialect) -> str:
    """Render ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS <col spec>``.

    The column spec comes from SQLAlchemy's own DDL compiler so the rendered
    type, NOT NULL and DEFAULT match what ``create_all`` would have produced for
    a fresh table. IF NOT EXISTS keeps it idempotent across every boot, which is
    the same guarantee the hand-written statements in main.py relied on.
    """
    prep = dialect.identifier_preparer
    spec = CreateColumn(column).compile(dialect=dialect).string.strip()
    return f"ALTER TABLE {prep.format_table(table)} ADD COLUMN IF NOT EXISTS {spec}"


def plan_additive_columns(
    metadata: MetaData,
    existing: dict[str, set[str]],
    dialect: Dialect,
) -> ReconcilePlan:
    """Diff ORM metadata against the live schema and plan the additive fixes.

    ``existing`` maps table name -> set of column names already in the database.
    A table absent from ``existing`` is left to ``create_all``.

    Pure: no database access, so the decision logic is unit-testable without a
    server.
    """
    plan = ReconcilePlan()

    for table in metadata.sorted_tables:
        present = existing.get(table.name)
        if present is None:
            plan.absent_tables.append(table.name)
            continue

        for column in table.columns:
            if column.name in present:
                continue
            ok, reason = _is_safely_additive(column)
            if not ok:
                plan.refused.append((f"{table.name}.{column.name}", reason))
                continue
            plan.statements.append(_add_column_sql(table, column, dialect))

    return plan


def _reflect_existing_columns(sync_conn, metadata: MetaData) -> dict[str, set[str]]:
    """Read the live column set for each table the model knows about."""
    inspector = inspect(sync_conn)
    live_tables = set(inspector.get_table_names())
    return {
        table.name: {c["name"] for c in inspector.get_columns(table.name)}
        for table in metadata.sorted_tables
        if table.name in live_tables
    }


async def reconcile_additive_columns(conn, metadata: MetaData) -> ReconcilePlan:
    """Add every model column missing from an existing table.

    ``conn`` is an open SQLAlchemy ``AsyncConnection``. Runs after
    ``create_all`` so newly created tables are already complete and contribute
    nothing here.
    """
    existing = await conn.run_sync(_reflect_existing_columns, metadata)
    plan = plan_additive_columns(metadata, existing, conn.dialect)

    for statement in plan.statements:
        await conn.execute(text(statement))

    return plan


def install_pos_attempt_v1_constraints(sync_conn) -> None:
    """Install only the versioned POS guarantees after additive columns exist.

    The general column reconciler deliberately never infers constraints. This
    explicit owner is shared by the POS migration and production startup, and
    never rewrites legacy records or touches another table's constraints.
    """
    from sqlalchemy.schema import AddConstraint

    from app.models.pos_transaction import POSTransaction

    table = POSTransaction.__table__
    inspector = inspect(sync_conn)
    existing = {item["name"] for item in inspector.get_check_constraints(table.name)}
    for constraint in sorted(table.constraints, key=lambda item: item.name or ""):
        if (constraint.name or "").startswith("ck_pos_attempt_v1_") and constraint.name not in existing:
            sync_conn.execute(AddConstraint(constraint, isolate_from_table=False))
    for index in sorted(table.indexes, key=lambda item: item.name):
        if index.name.startswith("ux_pos_attempt_v1_"):
            index.create(sync_conn, checkfirst=True)
