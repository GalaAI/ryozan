"""Hustl's two E-con summaries must answer from one aggregate, one period.

HustlePage is the single Hustl surface and it calls /econ/ledger/summary and
/econ/payroll-tax/summary on every load. Those endpoints each issued the same
provider/bucket aggregate independently, so the same numbers were computed twice
and could drift apart on any edit to one of them. /ledger/summary additionally
ran `select(EconLedgerEntry)` with no bound at all, pulling every ledger row the
shop has ever recorded into memory.

Neither endpoint accepted a period, while /pos/summary on the same page does, so
Hustl rendered an all-time E-con figure beside a period-scoped POS figure.

These cover the reconciliation: one shared aggregate, shop-time periods on both,
bounded contribution reads, and backward compatibility when no period is passed.
"""

import unittest
from datetime import UTC, date, datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.api.v1.endpoints.econ import (
    _ledger_bucket_rows,
    _ledger_contribution_rows,
    _ledger_period_bounds,
    _ledger_tax_projection,
    get_ledger_summary,
    get_payroll_tax_summary,
)
from app.constants.timezones import shop_timezone

# (provider, bucket, count, amount_cents) -- the aggregate shape both share.
ROWS = [
    ("stripe", "income", 6, 93600),
    ("stripe", "expense", 2, -4820),
    ("stripe", None, 1, 104000),
]


def _founder():
    return MagicMock(role="founder", id="11111111-1111-1111-1111-111111111111")


def _db(agg_rows=ROWS, contribution_rows=()):
    """A db that returns the aggregate first and the contribution rows second."""
    calls = []

    async def execute(query):
        calls.append(query)
        result = MagicMock()
        # First call in an endpoint is always the aggregate.
        if len(calls) == 1:
            result.all = MagicMock(return_value=list(agg_rows))
        else:
            result.all = MagicMock(return_value=list(contribution_rows))
        return result

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    db._calls = calls
    return db


class LedgerPeriodBoundsTests(unittest.TestCase):
    def test_no_period_means_all_time(self):
        """Omitting both params must behave exactly as before periods existed."""
        self.assertIsNone(_ledger_period_bounds(None, None))

    def test_bounds_are_shop_time_aware(self):
        start, end = _ledger_period_bounds(date(2026, 9, 27), date(2026, 9, 27))
        self.assertEqual(start.tzinfo, shop_timezone())
        self.assertEqual(end.tzinfo, shop_timezone())

    def test_an_8pm_eastern_entry_belongs_to_that_eastern_day(self):
        """00:30 UTC on the 28th is still the 27th in the shop."""
        paid_at = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)
        start, end = _ledger_period_bounds(date(2026, 9, 27), date(2026, 9, 27))
        self.assertLessEqual(start, paid_at)
        self.assertGreaterEqual(end, paid_at)

    def test_end_only_defaults_start_to_year_start(self):
        start, end = _ledger_period_bounds(None, date(2026, 9, 27))
        self.assertEqual(start.date(), date(2026, 1, 1))
        self.assertEqual(end.date(), date(2026, 9, 27))

    def test_inverted_period_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            _ledger_period_bounds(date(2026, 9, 27), date(2026, 9, 1))
        self.assertEqual(ctx.exception.status_code, 400)


class SharedAggregateTests(IsolatedAsyncioTestCase):
    async def test_both_summaries_agree_on_the_same_period(self):
        """The regression: two endpoints, two copies of one query, free to drift."""
        ledger = await get_ledger_summary(
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            current_user=_founder(),
            db=_db(),
        )
        tax = await get_payroll_tax_summary(
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            current_user=_founder(),
            db=_db(),
        )
        for key in ("income_cents", "expense_cents", "net_taxable_cents", "estimated_tax_reserve_cents"):
            self.assertEqual(ledger[key], tax[key], f"{key} disagrees between the two Hustl summaries")
        self.assertEqual(ledger["buckets"], tax["buckets"])

    async def test_ledger_summary_response_is_a_superset_of_the_old_shape(self):
        """HustlePage already reads these keys; they must not disappear."""
        out = await get_ledger_summary(
            period_start=None,
            period_end=None,
            current_user=_founder(),
            db=_db(),
        )
        for key in ("buckets", "contractor_contributions_cents", "contractor_contribution_count"):
            self.assertIn(key, out)

    async def test_uncategorized_bucket_is_labelled_not_dropped(self):
        out = await get_payroll_tax_summary(
            period_start=None,
            period_end=None,
            current_user=_founder(),
            db=_db(),
        )
        self.assertIn("uncategorized", [b["bucket"] for b in out["buckets"]])
        self.assertEqual(out["uncategorized_cents"], 104000)

    async def test_ledger_summary_issues_exactly_two_queries(self):
        """One aggregate plus one bounded contribution read -- not a table scan."""
        db = _db()
        await get_ledger_summary(period_start=None, period_end=None, current_user=_founder(), db=db)
        self.assertEqual(len(db._calls), 2)

    async def test_payroll_tax_summary_issues_exactly_one_query(self):
        db = _db()
        await get_payroll_tax_summary(period_start=None, period_end=None, current_user=_founder(), db=db)
        self.assertEqual(len(db._calls), 1)


class BoundedReadsTests(IsolatedAsyncioTestCase):
    async def test_aggregate_is_period_filtered_when_bounds_given(self):
        db = _db()
        bounds = _ledger_period_bounds(date(2026, 9, 1), date(2026, 9, 30))
        await _ledger_bucket_rows(db, bounds)
        rendered = str(db._calls[0])
        self.assertIn("occurred_at", rendered)

    async def test_aggregate_is_unfiltered_when_bounds_are_none(self):
        db = _db()
        await _ledger_bucket_rows(db, None)
        self.assertNotIn("occurred_at", str(db._calls[0]))

    async def test_contribution_read_selects_only_three_columns(self):
        """It used to select whole entities with no bound."""
        # Called directly, so the first stubbed response is the contribution read.
        db = _db(agg_rows=[("contractor_contribution", 52000, None)])
        rows = await _ledger_contribution_rows(db, None)
        rendered = str(db._calls[0])
        self.assertIn("category", rendered)
        self.assertIn("amount_cents", rendered)
        self.assertEqual(rows[0].amount_cents, 52000)

    async def test_contribution_rows_feed_the_projection_unchanged(self):
        """Shop cut only, never the whole collector payment."""
        db = _db(
            contribution_rows=[
                ("contractor_contribution", 52000, None),
                ("pos_payment", 15600, {"shop_share_category": "contractor_contribution", "shop_cut_cents": 5100}),
                ("pos_payment", 10400, {"shop_share_category": "artist_share"}),
            ]
        )
        out = await get_ledger_summary(
            period_start=None,
            period_end=None,
            current_user=_founder(),
            db=db,
        )
        self.assertEqual(out["contractor_contributions_cents"], 52000 + 5100)
        self.assertEqual(out["contractor_contribution_count"], 2)


class ProjectionInvariantTests(unittest.TestCase):
    def test_reserve_is_a_quarter_of_net_taxable(self):
        out = _ledger_tax_projection([("stripe", "income", 1, 100000)])
        self.assertEqual(out["net_taxable_cents"], 100000)
        self.assertEqual(out["estimated_tax_reserve_cents"], 25000)

    def test_net_taxable_never_goes_negative(self):
        out = _ledger_tax_projection([("stripe", "expense", 1, -500000)])
        self.assertEqual(out["net_taxable_cents"], 0)
        self.assertEqual(out["estimated_tax_reserve_cents"], 0)

    def test_uncategorized_income_blocks_tax_ready(self):
        out = _ledger_tax_projection([("stripe", "income", 1, 100000), ("stripe", None, 1, 5000)])
        self.assertEqual(out["prep_status"], "needs_review")

    def test_fully_categorized_income_is_tax_ready(self):
        out = _ledger_tax_projection([("stripe", "income", 1, 100000)])
        self.assertEqual(out["prep_status"], "ready")


if __name__ == "__main__":
    unittest.main()
