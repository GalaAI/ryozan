"""The ORM models declare exactly the schema the baseline (and so production) has.

If a model and the schema drift, `alembic revision --autogenerate` proposes
changes nobody asked for: on 2026-10-31 it would have dropped 28 live indexes
the models never declared. Two checks, both on a real Postgres:

* `Base.metadata.create_all` on an empty database yields a schema identical to
  the baseline snapshot (tables, columns, defaults, indexes with their WHERE
  clauses and expressions, constraints). This is stricter than autogenerate,
  which does not compare everything.
* After `alembic upgrade head`, `alembic check` finds nothing to do.

Set BTTCO_TEST_DATABASE_URL to a LOCAL database this suite may wipe (its public
schema is dropped and recreated). Skipped otherwise.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

import sqlalchemy as sa

from app.core.schema_compare import compare_schemas, load_baseline_snapshot, snapshot_schema

API_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("BTTCO_TEST_DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://", 1)
LOCAL = urlparse(TEST_DATABASE_URL).hostname in {"localhost", "127.0.0.1", "::1"}


@unittest.skipUnless(TEST_DATABASE_URL and LOCAL, "set BTTCO_TEST_DATABASE_URL to a local database this suite may wipe")
class ModelSchemaParityPostgresTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine(TEST_DATABASE_URL)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")

    def test_model_metadata_builds_exactly_the_baseline(self):
        # The same registration alembic/env.py uses: the modules the API loads.
        from app.api.v1 import router as _api_router  # noqa: F401
        from app.core.database import Base
        from app.mcp import oauth as _mcp_oauth  # noqa: F401
        from app.mcp import router as _mcp_router  # noqa: F401

        Base.metadata.create_all(self.engine)
        baseline = load_baseline_snapshot()
        with self.engine.begin() as conn:
            diffs = compare_schemas(baseline, snapshot_schema(conn, set(baseline["tables"])))
        self.assertEqual(
            [str(d) for d in diffs],
            [],
            "a model and the baseline schema disagree; fix the model to match the schema, "
            "or add an Alembic revision that changes the schema to match the model",
        )

    def test_alembic_check_finds_nothing_after_upgrade_head(self):
        env = dict(os.environ)
        env.update(
            DATABASE_URL=TEST_DATABASE_URL,
            DATABASE_SYNC_URL=TEST_DATABASE_URL,
            ENABLE_API_DOCS="false",
            ENABLE_CONFIG_HEALTH="false",
            PYTHONIOENCODING="utf-8",
        )

        def alembic(*args: str) -> subprocess.CompletedProcess:
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

        upgrade = alembic("upgrade", "head")
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)
        check = alembic("check")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("No new upgrade operations detected.", check.stdout + check.stderr)


if __name__ == "__main__":
    unittest.main()
