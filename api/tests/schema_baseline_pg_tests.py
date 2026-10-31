"""One schema path, proven on a real Postgres.

* An empty database brought to head by `alembic upgrade head` matches the
  baseline snapshot exactly (the baseline SQL is a fixed point).
* A database built the old way (tables, no alembic_version) is refused by the
  baseline revision, and scripts/adopt_schema.py reconciles listed drift,
  refuses extras until they are accepted, changes nothing on refusal, and
  stamps the baseline on success.

Everything runs in subprocesses, exactly as an operator runs it, so
`get_settings()` caching in this process can never point a step at the wrong
database. Set BTTCO_TEST_DATABASE_URL to a LOCAL database this suite may wipe
(its public schema is dropped and recreated). Skipped otherwise.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

import sqlalchemy as sa

from app.core.schema_compare import BASELINE_REVISION, compare_schemas, load_baseline_snapshot, snapshot_schema

API_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("BTTCO_TEST_DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://", 1)
LOCAL = urlparse(TEST_DATABASE_URL).hostname in {"localhost", "127.0.0.1", "::1"}


def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(
        DATABASE_URL=TEST_DATABASE_URL,
        DATABASE_SYNC_URL=TEST_DATABASE_URL,
        ENABLE_API_DOCS="false",
        ENABLE_CONFIG_HEALTH="false",
        PYTHONIOENCODING="utf-8",
    )
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(API_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


@unittest.skipUnless(TEST_DATABASE_URL and LOCAL, "set BTTCO_TEST_DATABASE_URL to a local database this suite may wipe")
class SchemaBaselinePostgresTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine(TEST_DATABASE_URL)
        self.addCleanup(self.engine.dispose)
        self._sql("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        upgrade = _run("-m", "alembic", "upgrade", "head")
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)

    def _sql(self, *statements: str) -> None:
        with self.engine.begin() as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)

    def _diff(self):
        baseline = load_baseline_snapshot()
        with self.engine.begin() as conn:
            return compare_schemas(baseline, snapshot_schema(conn, set(baseline["tables"])))

    def _version(self):
        with self.engine.connect() as conn:
            if not conn.execute(sa.text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar():
                return None
            return conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()

    def test_fresh_upgrade_head_is_exactly_the_baseline(self):
        self.assertEqual(self._diff(), [])
        self.assertEqual(self._version(), BASELINE_REVISION)
        again = _run("-m", "alembic", "upgrade", "head")
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_adoption_of_a_database_built_the_old_way(self):
        # The old boot path leaves every table and no alembic_version; give it
        # the kinds of drift a long-lived database accumulates.
        self._sql(
            "DROP TABLE alembic_version",
            "DROP INDEX idx_bttco_users_role",
            "ALTER INDEX ix_bttco_users_membership_tier RENAME TO ix_users_tier_legacy",
            "ALTER TABLE bttco_sms_messages ALTER COLUMN error_code TYPE VARCHAR(20)",
            "ALTER TABLE bttco_users ALTER COLUMN booth_rent_payment_method DROP DEFAULT",
            "ALTER TABLE bttco_users ADD COLUMN legacy_flag BOOLEAN",
        )
        self.assertEqual(len(self._diff()), 5)

        refused_upgrade = _run("-m", "alembic", "upgrade", "head")
        self.assertNotEqual(refused_upgrade.returncode, 0)
        self.assertIn("scripts/adopt_schema.py", refused_upgrade.stderr)
        self.assertIsNone(self._version(), "a refused upgrade must not leave alembic_version behind")

        dry = _run("scripts/adopt_schema.py")
        self.assertEqual(dry.returncode, 1, dry.stdout)
        self.assertIn("REFUSED    extra_column:bttco_users.legacy_flag", dry.stdout)
        self.assertIn("RECONCILE  missing_index:bttco_users.idx_bttco_users_role", dry.stdout)
        self.assertIn("RECONCILE  renamed_index:bttco_users.ix_bttco_users_membership_tier", dry.stdout)

        refused = _run("scripts/adopt_schema.py", "--apply")
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertEqual(len(self._diff()), 5, "a refused adoption must change nothing")
        self.assertIsNone(self._version())

        applied = _run("scripts/adopt_schema.py", "--apply", "--accept", "extra_column:bttco_users.legacy_flag")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(self._version(), BASELINE_REVISION)
        self.assertEqual([d.key for d in self._diff()], ["extra_column:bttco_users.legacy_flag"])

        noop = _run("-m", "alembic", "upgrade", "head")
        self.assertEqual(noop.returncode, 0, noop.stderr)
        again = _run("scripts/adopt_schema.py")
        self.assertEqual(again.returncode, 2, "an adopted database is Alembic's; adoption must refuse")


if __name__ == "__main__":
    unittest.main()
