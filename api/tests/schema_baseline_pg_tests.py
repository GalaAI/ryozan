"""One schema path, proven on a real Postgres.

* An empty database brought to ``20261031_baseline`` matches the baseline
  snapshot exactly (the baseline SQL is a fixed point).
* ``upgrade head`` from there applies ``20261031_ryozan_prefix``: no table,
  index or constraint keeps a bttco name; ``downgrade 20261031_baseline`` puts
  back exactly the baseline, and upgrading again rebuilds exactly the same head.
* A database built the old way (tables, no alembic_version) is refused by the
  baseline revision, and scripts/adopt_schema.py reconciles listed drift,
  refuses extras until they are accepted, changes nothing on refusal, and
  stamps the baseline on success; ``upgrade head`` then renames it to the same
  schema a fresh ``upgrade head`` builds.

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
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.schema_compare import BASELINE_REVISION, compare_schemas, load_baseline_snapshot, snapshot_schema

API_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("BTTCO_TEST_DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://", 1)
LOCAL = urlparse(TEST_DATABASE_URL).hostname in {"localhost", "127.0.0.1", "::1"}


# Tables created by revisions after the baseline (each named here when its revision lands).
POST_BASELINE_TABLES = (
    "ryozan_studio_settings",  # 20261102_studio_settings
    "ryozan_scroll_public_pdf_deletions",  # 20261110_scroll_public_pdf_del
    "ryozan_scroll_pdf_pending_uploads",  # 20261110_scroll_pdf_pending
)


def _head_revision() -> str:
    config = Config()
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


# Every relation (table, index, sequence, view) and constraint in public whose
# name carries the bttco_ prefix. (The column bttcoin_balance, the BTTCoin
# currency, is not renamed; on Postgres 18 its NOT NULL constraint becomes
# ryozan_users_bttcoin_balance_not_null.)
_BTTCO_NAMES_SQL = r"""
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname ILIKE '%bttco\_%'
UNION ALL
SELECT k.conname FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
WHERE n.nspname = 'public' AND k.conname ILIKE '%bttco\_%'
"""


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
        self.baseline = load_baseline_snapshot()
        self._sql("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        self._alembic("upgrade", BASELINE_REVISION)

    def _alembic(self, *args: str) -> None:
        result = _run("-m", "alembic", *args)
        self.assertEqual(result.returncode, 0, f"alembic {' '.join(args)}: {result.stderr}")

    def _sql(self, *statements: str) -> None:
        with self.engine.begin() as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)

    def _snapshot(self):
        with self.engine.begin() as conn:
            return snapshot_schema(conn, set(self.baseline["tables"]))

    def _diff(self):
        return compare_schemas(self.baseline, self._snapshot())

    def _bttco_names(self) -> list[str]:
        with self.engine.connect() as conn:
            return sorted(row[0] for row in conn.execute(sa.text(_BTTCO_NAMES_SQL)))

    def _version(self):
        with self.engine.connect() as conn:
            if not conn.execute(sa.text("SELECT to_regclass('public.alembic_version') IS NOT NULL")).scalar():
                return None
            return conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()

    def test_fresh_baseline_is_exactly_the_baseline_snapshot(self):
        self.assertEqual(self._diff(), [])
        self.assertEqual(self._version(), BASELINE_REVISION)

    def test_head_renames_every_bttco_object_and_round_trips(self):
        # 61 tables + 103 plain indexes + 76 indexes behind the 61 primary keys and
        # 15 unique constraints (pg_class) + the 144 constraints (pg_constraint),
        # plus on Postgres 18 the 496 named NOT NULL constraints.
        with self.engine.connect() as conn:
            server = int(conn.execute(sa.text("SHOW server_version_num")).scalar())
        not_nulls = 496 if server >= 180000 else 0
        self.assertEqual(len(self._bttco_names()), 61 + 103 + 76 + 144 + not_nulls)

        self._alembic("upgrade", "head")
        self.assertEqual(self._version(), _head_revision())
        self.assertEqual(self._bttco_names(), [])
        head = self._snapshot()
        self.assertIn("ryozan_users", head["tables"])
        self.assertIn("ryozan_users_pkey", head["tables"]["ryozan_users"]["constraints"])
        self.assertIn("idx_ryozan_users_role", head["tables"]["ryozan_users"]["indexes"])
        self.assertIn("feedback_samples", head["tables"])
        self.assertIn("viber_accounts", head["tables"])
        # The baseline tables, plus each table a later revision adds, by name.
        for table in POST_BASELINE_TABLES:
            self.assertIn(table, head["tables"])
        self.assertEqual(len(head["tables"]), len(self.baseline["tables"]) + len(POST_BASELINE_TABLES))

        again = _run("-m", "alembic", "upgrade", "head")
        self.assertEqual(again.returncode, 0, again.stderr)

        self._alembic("downgrade", BASELINE_REVISION)
        self.assertEqual(self._version(), BASELINE_REVISION)
        self.assertEqual(self._diff(), [], "downgrade must restore exactly the baseline")

        self._alembic("upgrade", "head")
        self.assertEqual(compare_schemas(head, self._snapshot()), [], "a second upgrade must rebuild the same head")

    def test_adoption_of_a_database_built_the_old_way(self):
        # The schema a fresh `upgrade head` builds, to compare the adopted database with at the end.
        self._alembic("upgrade", "head")
        fresh_head = self._snapshot()
        self._alembic("downgrade", BASELINE_REVISION)

        # The old boot path leaves every table and no alembic_version; give it
        # the kinds of drift a long-lived database accumulates. It has the
        # baseline's bttco_ names: adoption compares against the baseline.
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

        # The container's `alembic upgrade head` then applies the rename.
        self._alembic("upgrade", "head")
        self.assertEqual(self._version(), _head_revision())
        self.assertEqual(self._bttco_names(), [])
        self.assertEqual(
            [d.key for d in compare_schemas(fresh_head, self._snapshot())],
            ["extra_column:ryozan_users.legacy_flag"],
        )
        again = _run("scripts/adopt_schema.py")
        self.assertEqual(again.returncode, 2, "an adopted database is Alembic's; adoption must refuse")


if __name__ == "__main__":
    unittest.main()
