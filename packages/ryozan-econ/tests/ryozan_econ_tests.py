"""Invariants of the Ryozan bookkeeping core.

An accounting package is worth exactly what its invariants are worth, so they are
asserted directly rather than inferred from happy-path totals:

  * every entry balances, and an unbalanced one cannot be constructed
  * the trial balance always ties
  * the balance sheet always ties, including the period result
  * a deposit is a liability until earned, never revenue on receipt
  * fees are an expense, so revenue is never quietly recorded net
  * replaying a provider webhook posts nothing twice
  * one tenant's books never see another's

Amounts are ordinary studio shapes: a $520.00 card payment on a $500.00 service
price with a $15.38 processor fee, and a $156.00 gift card sale taken on an
artist's own connected account with a $51.00 platform fee to the shop.
"""

import unittest
from datetime import UTC, datetime

from ryozan_econ import (
    AccountType,
    DuplicateSourceError,
    Journal,
    JournalEntry,
    JournalLine,
    SourceTransaction,
    UnbalancedEntryError,
    UnknownAccountError,
    UnsupportedKindError,
    artist_payouts_owed,
    balance_sheet,
    credit,
    debit,
    income_statement,
    post,
    studio_chart,
    supported_kinds,
    trial_balance,
)
from ryozan_econ import accounts as a

TENANT = "entity:example-studio"
OTHER_TENANT = "entity:another-studio"
ARTIST = "11111111-1111-1111-1111-111111111111"
WHEN = datetime(2026, 9, 25, 21, 2, 32, tzinfo=UTC)


def _books(tenant=TENANT):
    chart = studio_chart(tenant)
    return chart, Journal(tenant, chart)


def _pos_charge(**over):
    """The real 2026-09-25 POS payment: $520.00 gross, $15.38 Stripe fee."""
    values = dict(
        tenant_id=TENANT,
        provider="stripe",
        external_id="pi_example_card_payment",
        kind="pos_payment",
        occurred_at=WHEN,
        gross_cents=52000,
        fee_cents=1538,
        artist_id=ARTIST,
        description="Tattoo payment - Walk-in",
    )
    values.update(over)
    return SourceTransaction(**values)


class JournalLineTests(unittest.TestCase):
    def test_a_line_is_exactly_one_side(self):
        with self.assertRaises(ValueError):
            JournalLine("1010", debit_cents=100, credit_cents=100)
        with self.assertRaises(ValueError):
            JournalLine("1010")

    def test_no_negative_amounts(self):
        with self.assertRaises(ValueError):
            JournalLine("1010", debit_cents=-100)

    def test_amount_reads_whichever_side_carries_it(self):
        self.assertEqual(debit("1010", 500).amount_cents, 500)
        self.assertEqual(credit("4000", 500).amount_cents, 500)
        self.assertTrue(debit("1010", 1).is_debit)
        self.assertFalse(credit("4000", 1).is_debit)


class JournalEntryTests(unittest.TestCase):
    def test_an_unbalanced_entry_cannot_be_constructed(self):
        """The core invariant. Not validated later -- impossible to hold."""
        with self.assertRaises(UnbalancedEntryError):
            JournalEntry(TENANT, WHEN, "wrong", [debit("1010", 100), credit("4000", 99)])

    def test_an_empty_entry_is_rejected(self):
        with self.assertRaises(UnbalancedEntryError):
            JournalEntry(TENANT, WHEN, "nothing", [])

    def test_a_balanced_entry_reports_its_size_once(self):
        entry = JournalEntry(TENANT, WHEN, "ok", [debit("1010", 100), credit("4000", 100)])
        self.assertEqual(entry.amount_cents, 100)
        self.assertEqual(entry.total_debits, entry.total_credits)

    def test_lines_must_name_accounts_the_tenant_has(self):
        chart, _ = _books()
        entry = JournalEntry(TENANT, WHEN, "typo", [debit("9999", 100), credit("4000", 100)])
        with self.assertRaises(UnknownAccountError):
            entry.validate_against(chart)

    def test_a_chart_from_another_tenant_is_refused(self):
        other_chart = studio_chart(OTHER_TENANT)
        entry = JournalEntry(TENANT, WHEN, "ok", [debit("1010", 1), credit("4000", 1)])
        with self.assertRaises(ValueError):
            entry.validate_against(other_chart)


class TenantIsolationTests(unittest.TestCase):
    def test_a_journal_refuses_another_tenants_entry(self):
        _, journal = _books()
        foreign = JournalEntry(OTHER_TENANT, WHEN, "theirs", [debit("1010", 1), credit("4000", 1)])
        with self.assertRaises(ValueError):
            journal.post(foreign)

    def test_a_journal_refuses_a_mismatched_chart(self):
        with self.assertRaises(ValueError):
            Journal(TENANT, studio_chart(OTHER_TENANT))

    def test_two_tenants_books_do_not_mix(self):
        _, mine = _books(TENANT)
        _, theirs = _books(OTHER_TENANT)
        mine.post(post(_pos_charge()))
        theirs.post(post(_pos_charge(tenant_id=OTHER_TENANT, external_id="pi_theirs")))
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(theirs), 1)
        self.assertNotEqual(mine.entries[0].entry_id, theirs.entries[0].entry_id)


class IdempotencyTests(unittest.TestCase):
    def test_replaying_a_webhook_posts_nothing_twice(self):
        """Stripe retries. A retry must not double the books."""
        _, journal = _books()
        self.assertIsNotNone(journal.post_if_new(post(_pos_charge())))
        self.assertIsNone(journal.post_if_new(post(_pos_charge())))
        self.assertEqual(len(journal), 1)

    def test_a_hard_post_of_a_known_source_raises(self):
        _, journal = _books()
        journal.post(post(_pos_charge()))
        with self.assertRaises(DuplicateSourceError):
            journal.post(post(_pos_charge()))

    def test_has_source_reports_what_was_recorded(self):
        _, journal = _books()
        journal.post(post(_pos_charge()))
        self.assertTrue(journal.has_source("stripe", "pi_example_card_payment"))
        self.assertFalse(journal.has_source("stripe", "pi_never_seen"))


class PostingRuleTests(unittest.TestCase):
    def test_a_pos_payment_books_gross_revenue_and_fee_as_expense(self):
        """Revenue is the gross, never quietly net of the card fee."""
        chart, journal = _books()
        journal.post(post(_pos_charge()))
        pnl = income_statement(journal, chart)
        self.assertEqual(pnl.revenue_cents, 52000)
        self.assertEqual(pnl.expense_cents, 1538)
        self.assertEqual(pnl.net_income_cents, 52000 - 1538)
        self.assertEqual(pnl.by_account[a.PROCESSING_FEES], 1538)

    def test_a_deposit_is_a_liability_not_revenue(self):
        """The most common way a small-business ledger goes wrong."""
        chart, journal = _books()
        journal.post(
            post(_pos_charge(kind="public_booking_deposit", external_id="pi_dep", gross_cents=10400, fee_cents=332))
        )
        pnl = income_statement(journal, chart)
        sheet = balance_sheet(journal, chart)
        self.assertEqual(pnl.revenue_cents, 0, "a deposit is not income on receipt")
        self.assertEqual(sheet.by_account[a.CLIENT_DEPOSITS_HELD], 10400)
        self.assertTrue(sheet.balanced)

    def test_every_deposit_kind_posts_the_same_way(self):
        for kind in ("public_booking_deposit", "flash_deposit", "quote_deposit", "quote_proposal_deposit"):
            chart, journal = _books()
            journal.post(post(_pos_charge(kind=kind, external_id=f"pi_{kind}", gross_cents=10000, fee_cents=0)))
            sheet = balance_sheet(journal, chart)
            self.assertEqual(sheet.by_account[a.CLIENT_DEPOSITS_HELD], 10000, kind)
            self.assertEqual(income_statement(journal, chart).revenue_cents, 0, kind)

    def test_a_deposit_becomes_revenue_only_when_earned(self):
        chart, journal = _books()
        journal.post(
            post(_pos_charge(kind="public_booking_deposit", external_id="pi_dep", gross_cents=10000, fee_cents=0))
        )
        journal.post(
            post(_pos_charge(kind="deposit_earned", external_id="session_done", gross_cents=10000, fee_cents=0))
        )
        pnl = income_statement(journal, chart)
        sheet = balance_sheet(journal, chart)
        self.assertEqual(pnl.revenue_cents, 10000)
        self.assertEqual(sheet.by_account.get(a.CLIENT_DEPOSITS_HELD, 0), 0, "the liability is discharged")
        self.assertTrue(sheet.balanced)

    def test_a_gift_card_sale_is_unearned_until_redeemed(self):
        chart, journal = _books()
        journal.post(post(_pos_charge(kind="gift_card", external_id="pi_gc", gross_cents=15600, fee_cents=0)))
        self.assertEqual(income_statement(journal, chart).revenue_cents, 0)
        self.assertEqual(balance_sheet(journal, chart).by_account[a.GIFT_CARD_LIABILITY], 15600)

        journal.post(
            post(_pos_charge(kind="gift_card_redemption", external_id="gc_redeem", gross_cents=15600, fee_cents=0))
        )
        self.assertEqual(income_statement(journal, chart).revenue_cents, 15600)
        self.assertEqual(balance_sheet(journal, chart).by_account.get(a.GIFT_CARD_LIABILITY, 0), 0)

    def test_a_direct_to_artist_charge_books_only_the_shop_cut(self):
        """The real 2026-09-24 gift card sale: $156 on the artist's own account,
        $51 application fee to the shop. The shop never held the $156."""
        chart, journal = _books()
        journal.post(
            post(
                _pos_charge(
                    kind="gift_card",
                    external_id="pi_example_gift_card_sale",
                    gross_cents=15600,
                    fee_cents=0,
                    direct_to_artist=True,
                    shop_cut_cents=5100,
                )
            )
        )
        sheet = balance_sheet(journal, chart)
        self.assertEqual(sheet.by_account[a.GIFT_CARD_LIABILITY], 5100)
        self.assertEqual(sheet.by_account[a.STRIPE_CLEARING], 5100)
        self.assertTrue(sheet.balanced)

    def test_an_artist_share_becomes_a_cost_and_a_payable(self):
        chart, journal = _books()
        journal.post(post(_pos_charge(artist_cut_cents=26000)))
        pnl = income_statement(journal, chart)
        self.assertEqual(pnl.by_account[a.ARTIST_COMMISSION_EXPENSE], 26000)
        self.assertEqual(balance_sheet(journal, chart).by_account[a.ARTIST_PAYOUTS_PAYABLE], 26000)

    def test_booth_rent_cash_is_income_and_undeposited_cash(self):
        """The entry that has never once been recorded in production."""
        chart, journal = _books()
        journal.post(
            post(
                SourceTransaction(
                    tenant_id=TENANT,
                    provider="manual",
                    external_id="booth-2026-09-artist-a",
                    kind="booth_rent_cash",
                    occurred_at=WHEN,
                    gross_cents=100000,
                    artist_id=ARTIST,
                    description="September booth rent",
                )
            )
        )
        pnl = income_statement(journal, chart)
        self.assertEqual(pnl.by_account[a.BOOTH_RENT_INCOME], 100000)
        self.assertEqual(balance_sheet(journal, chart).by_account[a.UNDEPOSITED_FUNDS], 100000)

    def test_an_unknown_kind_is_refused_loudly(self):
        with self.assertRaises(UnsupportedKindError):
            post(_pos_charge(kind="interpretive_dance"))

    def test_supported_kinds_are_discoverable(self):
        kinds = supported_kinds()
        self.assertIn("pos_payment", kinds)
        self.assertIn("booth_rent_cash", kinds)
        self.assertIn("public_booking_deposit", kinds)

    def test_a_nonsense_transaction_is_refused(self):
        with self.assertRaises(ValueError):
            _pos_charge(gross_cents=0)
        with self.assertRaises(ValueError):
            _pos_charge(fee_cents=99999)
        with self.assertRaises(ValueError):
            _pos_charge(artist_cut_cents=99999)


class StatementInvariantTests(unittest.TestCase):
    def _busy_books(self):
        chart, journal = _books()
        journal.post(post(_pos_charge(artist_cut_cents=26000)))
        journal.post(
            post(_pos_charge(kind="public_booking_deposit", external_id="pi_dep", gross_cents=10400, fee_cents=332))
        )
        journal.post(post(_pos_charge(kind="gift_card", external_id="pi_gc", gross_cents=15600, fee_cents=468)))
        journal.post(
            post(
                SourceTransaction(
                    tenant_id=TENANT,
                    provider="manual",
                    external_id="booth-artist-a",
                    kind="booth_rent_cash",
                    occurred_at=WHEN,
                    gross_cents=100000,
                    artist_id=ARTIST,
                )
            )
        )
        return chart, journal

    def test_the_trial_balance_always_ties(self):
        _, journal = self._busy_books()
        tb = trial_balance(journal)
        self.assertTrue(tb.balanced, f"debits {tb.total_debits} != credits {tb.total_credits}")

    def test_the_balance_sheet_always_ties(self):
        chart, journal = self._busy_books()
        sheet = balance_sheet(journal, chart)
        self.assertTrue(
            sheet.balanced,
            f"assets {sheet.asset_cents} != liabilities+equity+net {sheet.liabilities_and_equity_cents}",
        )

    def test_an_empty_period_still_ties(self):
        chart, journal = _books()
        self.assertTrue(trial_balance(journal).balanced)
        self.assertTrue(balance_sheet(journal, chart).balanced)
        self.assertEqual(income_statement(journal, chart).net_income_cents, 0)

    def test_a_period_window_excludes_entries_outside_it(self):
        chart, journal = _books()
        journal.post(post(_pos_charge()))
        after = datetime(2026, 10, 1, tzinfo=UTC)
        later = datetime(2026, 10, 31, tzinfo=UTC)
        self.assertEqual(income_statement(journal, chart, after, later).revenue_cents, 0)
        self.assertEqual(income_statement(journal, chart, WHEN, later).revenue_cents, 52000)

    def test_revenue_and_expense_are_positive_in_their_natural_direction(self):
        chart, journal = self._busy_books()
        pnl = income_statement(journal, chart)
        self.assertGreater(pnl.revenue_cents, 0)
        self.assertGreater(pnl.expense_cents, 0)
        for code, amount in pnl.by_account.items():
            self.assertGreater(amount, 0, f"{code} should read positive on its own side")


class ArtistPayoutTests(unittest.TestCase):
    def test_owed_rises_on_collection_and_falls_on_payout(self):
        _, journal = _books()
        journal.post(post(_pos_charge(artist_cut_cents=26000)))
        self.assertEqual(artist_payouts_owed(journal), {ARTIST: 26000})

        journal.post(
            post(
                SourceTransaction(
                    tenant_id=TENANT,
                    provider="manual",
                    external_id="payout-1",
                    kind="artist_payout",
                    occurred_at=WHEN,
                    gross_cents=26000,
                    artist_id=ARTIST,
                )
            )
        )
        self.assertEqual(artist_payouts_owed(journal), {}, "fully paid artists drop off the list")

    def test_a_payout_is_not_an_expense_a_second_time(self):
        chart, journal = _books()
        journal.post(post(_pos_charge(artist_cut_cents=26000)))
        before = income_statement(journal, chart).expense_cents
        journal.post(
            post(
                SourceTransaction(
                    tenant_id=TENANT,
                    provider="manual",
                    external_id="payout-1",
                    kind="artist_payout",
                    occurred_at=WHEN,
                    gross_cents=26000,
                    artist_id=ARTIST,
                )
            )
        )
        self.assertEqual(income_statement(journal, chart).expense_cents, before)


class ChartTests(unittest.TestCase):
    def test_the_studio_template_covers_every_posting_rule_it_needs(self):
        """A rule referencing a missing account must fail here, not at month end."""
        chart, journal = _books()
        for kind, extra in (
            ("pos_payment", {"artist_cut_cents": 100}),
            ("public_booking_deposit", {}),
            ("gift_card", {}),
            ("gift_card_redemption", {}),
            ("deposit_earned", {}),
            ("booth_rent_cash", {}),
            ("artist_payout", {}),
        ):
            entry = post(_pos_charge(kind=kind, external_id=f"x-{kind}", gross_cents=1000, fee_cents=10, **extra))
            entry.validate_against(chart)
        del journal

    def test_duplicate_account_codes_are_refused(self):
        chart = studio_chart(TENANT)
        with self.assertRaises(ValueError):
            chart.add(a.Account(a.SERVICE_REVENUE, "Dupe", AccountType.REVENUE))

    def test_a_tenant_is_required(self):
        with self.assertRaises(ValueError):
            studio_chart("")

    def test_account_normal_sides_follow_the_convention(self):
        chart = studio_chart(TENANT)
        self.assertTrue(chart.get(a.STRIPE_CLEARING).debit_normal)
        self.assertTrue(chart.get(a.PROCESSING_FEES).debit_normal)
        self.assertFalse(chart.get(a.SERVICE_REVENUE).debit_normal)
        self.assertFalse(chart.get(a.CLIENT_DEPOSITS_HELD).debit_normal)


if __name__ == "__main__":
    unittest.main()


class TenancyConventionTests(unittest.TestCase):
    """Books belong to a business, not to software.

    ArkStation manages vessel:ryozan and vessel:example-studio:platform as peers -- Ryozan
    is the studio workflow and AI product; a studio is a thing that uses it. So the
    tenant of a set of books is the Ark *entity*, never the vessel.
    Pointing a ledger at a vessel ref is invisible until two deployments disagree
    about whose money they are counting, so it is refused outright.
    """

    def test_an_ark_entity_ref_is_the_canonical_tenant(self):
        from ryozan_econ import ark_entity_tenant, is_ark_entity, normalize_tenant_id

        self.assertEqual(ark_entity_tenant("example-studio"), "entity:example-studio")
        self.assertEqual(ark_entity_tenant("entity:example-studio"), "entity:example-studio")
        self.assertTrue(is_ark_entity("entity:example-studio"))
        self.assertEqual(normalize_tenant_id("entity:example-studio"), "entity:example-studio")

    def test_a_vessel_ref_is_refused_as_a_tenant(self):
        from ryozan_econ import RYOZAN_VESSEL, TenantIdError, normalize_tenant_id

        for ref in (RYOZAN_VESSEL, "vessel:example-studio:platform"):
            with self.assertRaises(TenantIdError, msg=ref):
                normalize_tenant_id(ref)

    def test_a_journal_refuses_a_vessel_as_its_tenant(self):
        from ryozan_econ import RYOZAN_VESSEL, TenantIdError

        with self.assertRaises(TenantIdError):
            Journal(RYOZAN_VESSEL)
        with self.assertRaises(TenantIdError):
            studio_chart("vessel:example-studio:platform")

    def test_a_plain_identifier_still_works_off_platform(self):
        """A studio running Ryozan standalone still needs books."""
        from ryozan_econ import is_ark_entity, normalize_tenant_id

        self.assertEqual(normalize_tenant_id("  cornerstone-tattoo  "), "cornerstone-tattoo")
        self.assertFalse(is_ark_entity("cornerstone-tattoo"))
        chart = studio_chart("cornerstone-tattoo")
        self.assertEqual(Journal("cornerstone-tattoo", chart).tenant_id, "cornerstone-tattoo")

    def test_an_unusable_tenant_id_is_refused(self):
        from ryozan_econ import TenantIdError, normalize_tenant_id

        for bad in ("", "   ", None, 12, "x" * 200):
            with self.assertRaises(TenantIdError, msg=repr(bad)):
                normalize_tenant_id(bad)

    def test_an_entry_normalizes_its_tenant_too(self):
        entry = JournalEntry("  entity:example-studio  ", WHEN, "ok", [debit("1010", 1), credit("4000", 1)])
        self.assertEqual(entry.tenant_id, "entity:example-studio")


class BoothRentMethodTests(unittest.TestCase):
    """Artists pay booth rent in cash or Venmo. Both are income, logged for tax.

    Income either way, but the money lands somewhere different, and keeping them
    apart is what lets the safe and the Venmo balance each be reconciled. Booking
    a Venmo transfer as undeposited cash would make the safe look fuller than it
    is, which is the kind of error that is only found by counting.
    """

    def _rent(self, kind, cents=100000):
        chart, journal = _books()
        journal.post(
            post(
                SourceTransaction(
                    tenant_id=TENANT,
                    provider="manual",
                    external_id=f"booth-{kind}",
                    kind=kind,
                    occurred_at=WHEN,
                    gross_cents=cents,
                    artist_id=ARTIST,
                    description="September booth rent",
                )
            )
        )
        return chart, journal

    def test_cash_booth_rent_lands_in_undeposited_funds(self):
        chart, journal = self._rent("booth_rent_cash")
        sheet = balance_sheet(journal, chart)
        self.assertEqual(sheet.by_account[a.UNDEPOSITED_FUNDS], 100000)
        self.assertNotIn(a.VENMO_CLEARING, sheet.by_account)

    def test_venmo_booth_rent_lands_in_the_venmo_balance(self):
        chart, journal = self._rent("booth_rent_venmo")
        sheet = balance_sheet(journal, chart)
        self.assertEqual(sheet.by_account[a.VENMO_CLEARING], 100000)
        self.assertNotIn(a.UNDEPOSITED_FUNDS, sheet.by_account)

    def test_both_methods_are_booth_rent_income_for_tax(self):
        for kind in ("booth_rent_cash", "booth_rent_venmo"):
            chart, journal = self._rent(kind)
            pnl = income_statement(journal, chart)
            self.assertEqual(pnl.by_account[a.BOOTH_RENT_INCOME], 100000, kind)
            self.assertEqual(pnl.revenue_cents, 100000, kind)
            self.assertTrue(balance_sheet(journal, chart).balanced, kind)

    def test_venmo_is_a_supported_kind(self):
        self.assertIn("booth_rent_venmo", supported_kinds())

    def test_mixed_methods_keep_separate_asset_balances(self):
        """One artist pays cash, another Venmo. Both counted, neither conflated."""
        chart, journal = _books()
        for kind, cents, who in (("booth_rent_cash", 100000, "artist-a"), ("booth_rent_venmo", 130000, "artist-b")):
            journal.post(
                post(
                    SourceTransaction(
                        tenant_id=TENANT,
                        provider="manual",
                        external_id=f"booth-{who}",
                        kind=kind,
                        occurred_at=WHEN,
                        gross_cents=cents,
                        artist_id=who,
                    )
                )
            )
        sheet = balance_sheet(journal, chart)
        self.assertEqual(sheet.by_account[a.UNDEPOSITED_FUNDS], 100000)
        self.assertEqual(sheet.by_account[a.VENMO_CLEARING], 130000)
        self.assertEqual(income_statement(journal, chart).by_account[a.BOOTH_RENT_INCOME], 230000)
        self.assertTrue(sheet.balanced)
