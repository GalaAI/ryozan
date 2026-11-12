"""The ORM models declare exactly the schema `alembic upgrade head` builds (and so production has).

If a model and the schema drift, `alembic revision --autogenerate` proposes
changes nobody asked for: on 2026-10-31 it would have dropped 28 live indexes
the models never declared. Two checks, both on a real Postgres:

* `Base.metadata.create_all` on an empty database yields a schema identical to
  the one `alembic upgrade head` builds on another empty database (tables,
  columns, defaults, indexes with their WHERE clauses and expressions,
  constraints). This is stricter than autogenerate, which does not compare
  everything. The reference is a database at head, not the baseline snapshot:
  the baseline is history (it still has the bttco_ names that revision
  20261031_ryozan_prefix renames).
* After `alembic upgrade head`, `alembic check` finds nothing to do.

Set RYOZAN_TEST_DATABASE_URL to a LOCAL database this suite may wipe (its public
schema is dropped and recreated). Skipped otherwise.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

import sqlalchemy as sa

from app.core import legacy_env
from app.core.config import psycopg2_url
from app.core.schema_compare import compare_schemas, load_baseline_snapshot, snapshot_schema

API_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = legacy_env.getenv("RYOZAN_TEST_DATABASE_URL", "").replace(
    "postgresql+asyncpg://", "postgresql://", 1
)
LOCAL = urlparse(TEST_DATABASE_URL).hostname in {"localhost", "127.0.0.1", "::1"}


def _alembic(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(
        DATABASE_URL=TEST_DATABASE_URL,
        DATABASE_SYNC_URL=TEST_DATABASE_URL,
        ENABLE_API_DOCS="false",
        ENABLE_CONFIG_HEALTH="false",
        PYTHONIOENCODING="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(API_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


@unittest.skipUnless(
    TEST_DATABASE_URL and LOCAL, "set RYOZAN_TEST_DATABASE_URL to a local database this suite may wipe"
)
class ModelSchemaParityPostgresTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine(psycopg2_url(TEST_DATABASE_URL))
        self.addCleanup(self.engine.dispose)
        self._reset()

    def _reset(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")

    def _snapshot(self):
        # The baseline's table list scopes the read to Ryozan's tables (it names
        # feedback_samples, which has no prefix); ryozan_/viber_ tables are owned by prefix.
        with self.engine.begin() as conn:
            return snapshot_schema(conn, set(load_baseline_snapshot()["tables"]))

    def test_model_metadata_builds_exactly_the_schema_at_head(self):
        upgrade = _alembic("upgrade", "head")
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)
        head = self._snapshot()
        self.assertIn("ryozan_users", head["tables"])
        self.assertFalse([name for name in head["tables"] if name.startswith("bttco_")])

        self._reset()
        # The same registration alembic/env.py uses: the modules the API loads.
        from app.api.v1 import router as _api_router  # noqa: F401
        from app.core.database import Base
        from app.mcp import oauth as _mcp_oauth  # noqa: F401
        from app.mcp import router as _mcp_router  # noqa: F401

        Base.metadata.create_all(self.engine)
        diffs = compare_schemas(head, self._snapshot())
        self.assertEqual(
            [str(d) for d in diffs],
            [],
            "a model and the schema at head disagree; fix the model to match the schema, "
            "or add an Alembic revision that changes the schema to match the model",
        )

    def test_alembic_check_finds_nothing_after_upgrade_head(self):
        upgrade = _alembic("upgrade", "head")
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)
        check = _alembic("check")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("No new upgrade operations detected.", check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()
