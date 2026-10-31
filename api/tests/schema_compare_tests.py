"""Pure tests for the baseline comparison and the adoption planner (no database)."""

import copy
import unittest

from app.core.schema_compare import (
    BASELINE_SQL_PATH,
    compare_schemas,
    load_baseline_snapshot,
    normalize_pg_dump,
    plan_reconciliation,
)


def _baseline():
    return load_baseline_snapshot()


class BaselineFilesTests(unittest.TestCase):
    def test_snapshot_and_sql_describe_the_same_tables(self):
        sql = BASELINE_SQL_PATH.read_text(encoding="utf-8")
        tables = set(_baseline()["tables"])
        self.assertEqual(len(tables), sql.count("\nCREATE TABLE public.") + sql.startswith("CREATE TABLE public."))
        for table in tables:
            self.assertIn(f"CREATE TABLE public.{table} (", sql)

    def test_baseline_sql_is_plain_ddl(self):
        sql = BASELINE_SQL_PATH.read_text(encoding="utf-8")
        self.assertNotIn("\\restrict", sql)
        self.assertNotIn("set_config('search_path'", sql)
        self.assertNotIn("CONCURRENTLY", sql)
        self.assertNotIn("%", sql, "psycopg2 would treat % as a placeholder marker")

    def test_identical_snapshots_have_no_differences(self):
        self.assertEqual(compare_schemas(_baseline(), _baseline()), [])


class NormalizeDumpTests(unittest.TestCase):
    def test_strips_versions_restrict_key_and_session_settings(self):
        dump = (
            "--\n-- PostgreSQL database dump\n--\n\n\\restrict abc123\n\n-- Dumped from database version 16.15\n"
            "SET statement_timeout = 0;\nSELECT pg_catalog.set_config('search_path', '', false);\n\n\n"
            "CREATE TABLE public.t (\n    id uuid NOT NULL\n);\n\n\\unrestrict abc123\n"
        )
        self.assertEqual(normalize_pg_dump(dump), "CREATE TABLE public.t (\n    id uuid NOT NULL\n);\n")


class PlanReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.expected = _baseline()
        self.live = copy.deepcopy(self.expected)
        self.users = self.live["tables"]["bttco_users"]

    def _plan(self, accept=frozenset()):
        diffs = compare_schemas(self.expected, self.live)
        return diffs, plan_reconciliation(diffs, accept)

    def test_column_order_is_not_a_difference(self):
        self.users["columns"] = dict(reversed(list(self.users["columns"].items())))
        self.assertEqual(self._plan()[0], [])

    def test_missing_index_is_created_from_the_baseline_definition(self):
        del self.users["indexes"]["ux_bttco_users_email_lower"]
        diffs, plan = self._plan()
        self.assertEqual([d.key for d in diffs], ["missing_index:bttco_users.ux_bttco_users_email_lower"])
        self.assertTrue(plan.can_apply)
        self.assertEqual(
            plan.statements,
            ["CREATE UNIQUE INDEX ux_bttco_users_email_lower ON public.bttco_users USING btree (lower((email)::text))"],
        )

    def test_renamed_index_is_renamed_not_dropped(self):
        index = self.users["indexes"].pop("idx_bttco_users_role")
        index["definition"] = index["definition"].replace("idx_bttco_users_role", "ix_users_role_legacy")
        self.users["indexes"]["ix_users_role_legacy"] = index
        _, plan = self._plan()
        self.assertEqual(
            plan.statements, ['ALTER INDEX public."ix_users_role_legacy" RENAME TO "idx_bttco_users_role"']
        )

    def test_invalid_index_is_rebuilt(self):
        self.users["indexes"]["idx_bttco_users_role"]["valid"] = False
        _, plan = self._plan()
        self.assertEqual(plan.statements, ['REINDEX INDEX public."idx_bttco_users_role"'])

    def test_default_and_nullability_drift_are_reconciled(self):
        column = self.users["columns"]["booth_rent_payment_method"]
        column["default"] = None
        column["not_null"] = False
        _, plan = self._plan()
        self.assertTrue(plan.can_apply)
        expected_default = self.expected["tables"]["bttco_users"]["columns"]["booth_rent_payment_method"]["default"]
        self.assertIn(
            f'ALTER TABLE public."bttco_users" ALTER COLUMN "booth_rent_payment_method" SET DEFAULT {expected_default}',
            plan.statements,
        )
        self.assertIn(
            'ALTER TABLE public."bttco_users" ALTER COLUMN "booth_rent_payment_method" SET NOT NULL',
            plan.statements,
        )

    def test_only_varchar_widening_is_a_safe_type_change(self):
        sms = self.live["tables"]["bttco_sms_messages"]["columns"]["error_code"]
        sms["type"] = "character varying(20)"
        _, plan = self._plan()
        self.assertEqual(
            plan.statements,
            ['ALTER TABLE public."bttco_sms_messages" ALTER COLUMN "error_code" TYPE character varying(40)'],
        )
        sms["type"] = "integer"
        _, plan = self._plan()
        self.assertFalse(plan.can_apply)

    def test_missing_nullable_column_is_added(self):
        del self.users["columns"]["bio"]
        _, plan = self._plan()
        self.assertEqual(plan.statements, ['ALTER TABLE public."bttco_users" ADD COLUMN "bio" text'])

    def test_extra_objects_are_refused_until_accepted_and_never_dropped(self):
        self.users["columns"]["legacy_flag"] = {
            "type": "boolean",
            "not_null": False,
            "default": None,
            "identity": "",
            "generated": "",
        }
        self.live["tables"]["bttco_legacy"] = {"columns": {}, "indexes": {}, "constraints": {}, "triggers": {}}
        _, plan = self._plan()
        self.assertFalse(plan.can_apply)
        self.assertEqual(
            sorted(d.key for d, _ in plan.refused),
            ["extra_column:bttco_users.legacy_flag", "extra_table:bttco_legacy"],
        )
        _, plan = self._plan({"extra_column:bttco_users.legacy_flag", "extra_table:bttco_legacy"})
        self.assertTrue(plan.can_apply)
        self.assertEqual(plan.statements, [])

    def test_missing_table_is_refused(self):
        del self.live["tables"]["bttco_quests"]
        _, plan = self._plan()
        self.assertFalse(plan.can_apply)
        self.assertEqual(plan.refused[0][0].key, "missing_table:bttco_quests")


if __name__ == "__main__":
    unittest.main()
