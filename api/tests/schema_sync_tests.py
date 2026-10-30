"""Additive column reconciliation, the gap Base.metadata.create_all leaves.

create_all builds tables that are missing and never alters one that exists, so
a new column on an existing table reaches production as nothing at all. The ORM
then selects a column the database does not have and every query on that table
raises UndefinedColumn on the first request after the deploy.

These cover the decision logic without a database: what gets added, what is
refused because it cannot be applied to a table that already has rows, and the
guarantee that nothing destructive is ever emitted.
"""

import unittest

from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, Text, text
from sqlalchemy.dialects import postgresql

from app.core.schema_sync import plan_additive_columns
from app.models.booking import Booking
from app.models.pos_transaction import POS_ATTEMPT_V1_COLUMNS, POSTransaction
from app.models.user import BTTCOUser

DIALECT = postgresql.dialect()


def _metadata() -> MetaData:
    """A table shaped like the real session types table plus the new columns."""
    meta = MetaData()
    Table(
        "bttco_artist_session_types",
        meta,
        Column("id", Integer, primary_key=True),
        Column("label", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("pricing_mode", String(50), nullable=False, server_default="range"),
    )
    return meta


class PlanAdditiveColumnsTests(unittest.TestCase):
    def test_pos_attempt_columns_are_nullable_without_legacy_backfill(self):
        meta = MetaData()
        Table(
            "bttco_pos_transactions",
            meta,
            Column("id", Integer, primary_key=True),
            *(Column(name, POSTransaction.__table__.c[name].type, nullable=True) for name in POS_ATTEMPT_V1_COLUMNS),
        )
        plan = plan_additive_columns(meta, {"bttco_pos_transactions": {"id"}}, DIALECT)
        self.assertEqual(len(plan.statements), len(POS_ATTEMPT_V1_COLUMNS))
        self.assertEqual(plan.refused, [])
        for name in POS_ATTEMPT_V1_COLUMNS:
            column = POSTransaction.__table__.c[name]
            self.assertTrue(column.nullable)
            self.assertIsNone(column.default)
            self.assertIsNone(column.server_default)

    def test_nullable_column_missing_from_existing_table_is_added(self):
        plan = plan_additive_columns(
            _metadata(),
            {"bttco_artist_session_types": {"id", "label", "pricing_mode"}},
            DIALECT,
        )
        self.assertEqual(len(plan.statements), 1)
        self.assertIn("ADD COLUMN IF NOT EXISTS description TEXT", plan.statements[0])
        self.assertEqual(plan.refused, [])

    def test_not_null_column_with_server_default_is_added(self):
        """Safe because existing rows get the server default."""
        plan = plan_additive_columns(
            _metadata(),
            {"bttco_artist_session_types": {"id", "label", "description"}},
            DIALECT,
        )
        self.assertEqual(len(plan.statements), 1)
        statement = plan.statements[0]
        self.assertIn("pricing_mode VARCHAR(50)", statement)
        self.assertIn("DEFAULT 'range'", statement)
        self.assertIn("NOT NULL", statement)

    def test_booking_kind_model_column_is_safe_for_production_reconciliation(self):
        """The production boot path can add booking_kind to a populated table."""
        booking_kind = Booking.__table__.c.booking_kind
        self.assertIsNotNone(booking_kind.server_default)
        meta = MetaData()
        Table(
            "bttco_bookings",
            meta,
            Column("id", Integer, primary_key=True),
            Column(
                booking_kind.name,
                booking_kind.type,
                nullable=booking_kind.nullable,
                server_default=booking_kind.server_default.arg,
            ),
        )
        plan = plan_additive_columns(
            meta,
            {"bttco_bookings": {"id"}},
            DIALECT,
        )
        self.assertEqual(len(plan.statements), 1)
        self.assertIn("booking_kind VARCHAR(50) DEFAULT 'tattoo_session' NOT NULL", plan.statements[0])

    def test_not_null_without_server_default_is_refused(self):
        """Existing rows would violate the constraint, so it must not be attempted."""
        meta = MetaData()
        Table(
            "bttco_users",
            meta,
            Column("id", Integer, primary_key=True),
            Column("needs_backfill", String(10), nullable=False),
        )
        plan = plan_additive_columns(meta, {"bttco_users": {"id"}}, DIALECT)
        self.assertEqual(plan.statements, [])
        self.assertEqual(len(plan.refused), 1)
        name, reason = plan.refused[0]
        self.assertEqual(name, "bttco_users.needs_backfill")
        self.assertIn("no server default", reason)

    def test_python_side_default_does_not_count_as_a_server_default(self):
        """default= applies on insert, so rows already in the table get nothing."""
        meta = MetaData()
        Table(
            "bttco_users",
            meta,
            Column("id", Integer, primary_key=True),
            Column("flag", Boolean, nullable=False, default=True),
        )
        plan = plan_additive_columns(meta, {"bttco_users": {"id"}}, DIALECT)
        self.assertEqual(plan.statements, [])
        self.assertIn("Python-side default", plan.refused[0][1])

    def test_boolean_flag_with_server_default_true_is_added(self):
        """The booking buffer flag: existing artists keep the buffer switched on."""
        meta = MetaData()
        Table(
            "bttco_users",
            meta,
            Column("id", Integer, primary_key=True),
            Column("booking_buffer_enabled", Boolean, nullable=False, server_default=text("true")),
        )
        plan = plan_additive_columns(meta, {"bttco_users": {"id"}}, DIALECT)
        self.assertEqual(len(plan.statements), 1)
        self.assertIn("booking_buffer_enabled BOOLEAN DEFAULT true NOT NULL", plan.statements[0])

    def test_booth_rent_state_columns_are_safe_for_production_reconciliation(self):
        """Existing users receive deterministic rent state during production boot."""
        payment_method = BTTCOUser.__table__.c.booth_rent_payment_method
        status = BTTCOUser.__table__.c.booth_rent_status
        cancel = BTTCOUser.__table__.c.booth_rent_cancel_at_period_end
        guest = BTTCOUser.__table__.c.is_guest_artist
        self.assertIsNotNone(payment_method.server_default)
        self.assertIsNotNone(status.server_default)
        self.assertIsNotNone(cancel.server_default)

        meta = MetaData()
        Table(
            "bttco_users",
            meta,
            Column("id", Integer, primary_key=True),
            Column(
                payment_method.name,
                payment_method.type,
                nullable=payment_method.nullable,
                server_default=payment_method.server_default.arg,
            ),
            Column(status.name, status.type, nullable=status.nullable, server_default=status.server_default.arg),
            Column(cancel.name, cancel.type, nullable=cancel.nullable, server_default=cancel.server_default.arg),
            Column(guest.name, guest.type, nullable=guest.nullable, server_default=guest.server_default.arg),
        )
        plan = plan_additive_columns(meta, {"bttco_users": {"id"}}, DIALECT)

        self.assertEqual(plan.refused, [])
        self.assertEqual(len(plan.statements), 4)
        self.assertIn("booth_rent_payment_method VARCHAR(16) DEFAULT 'cash' NOT NULL", plan.statements[0])
        self.assertIn("booth_rent_status VARCHAR(24) DEFAULT 'not_configured' NOT NULL", plan.statements[1])
        self.assertIn("booth_rent_cancel_at_period_end BOOLEAN DEFAULT false NOT NULL", plan.statements[2])
        self.assertIn("is_guest_artist BOOLEAN DEFAULT false NOT NULL", plan.statements[3])

    def test_columns_already_present_produce_nothing(self):
        plan = plan_additive_columns(
            _metadata(),
            {"bttco_artist_session_types": {"id", "label", "description", "pricing_mode"}},
            DIALECT,
        )
        self.assertEqual(plan.statements, [])
        self.assertEqual(plan.refused, [])

    def test_missing_table_is_left_to_create_all(self):
        """A table that does not exist yet is create_all's job, not this one."""
        plan = plan_additive_columns(_metadata(), {}, DIALECT)
        self.assertEqual(plan.statements, [])
        self.assertEqual(plan.absent_tables, ["bttco_artist_session_types"])

    def test_primary_key_column_is_never_added(self):
        meta = MetaData()
        Table(
            "bttco_thing",
            meta,
            Column("id", Integer, primary_key=True),
            Column("other", String(10), nullable=True),
        )
        plan = plan_additive_columns(meta, {"bttco_thing": {"other"}}, DIALECT)
        self.assertEqual(plan.statements, [])
        self.assertIn("primary key", plan.refused[0][1])

    def test_only_additive_statements_are_ever_emitted(self):
        """The safety property: no drop, rename, retype, or truncate, ever.

        A column present in the database but absent from the model is left
        alone -- reconciliation is one-directional on purpose.
        """
        plan = plan_additive_columns(
            _metadata(),
            {"bttco_artist_session_types": {"id", "label", "legacy_column_not_on_model"}},
            DIALECT,
        )
        for statement in plan.statements:
            self.assertIn("ADD COLUMN IF NOT EXISTS", statement)
            for forbidden in ("DROP", "ALTER COLUMN", "RENAME", "TRUNCATE", "DELETE"):
                self.assertNotIn(forbidden, statement.upper())


if __name__ == "__main__":
    unittest.main()
