"""Conservative booking service balances from persisted owner records.

Routing tests prove a single charge split and webhook tests prove writes, but
neither owns a booking's remaining service debt. This suite covers that pure
projection, including ambiguous historical evidence and duplicate owner mirrors.
"""

import unittest
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from pydantic import ValidationError

from app.schemas.pos import POSAttemptCreate, POSBalanceSnapshot, POSTransactionCreate
from app.services.payments import project_booking_service_balance


def _booking(**overrides):
    return SimpleNamespace(
        **{
            "id": "booking-fixture",
            "artist_id": "artist-fixture",
            "deposit_paid": False,
            "deposit_amount": 52,
            "estimated_total": 999,
            "stripe_payment_intent_id": None,
            "stripe_checkout_session_id": None,
            **overrides,
        }
    )


def _allocation(service=5_000, adjustment=200, tip=0):
    return {"version": 1, "service_cents": service, "price_adjustment_cents": adjustment, "tip_cents": tip}


def _payment(payment_id="pi_deposit", allocation=None, **overrides):
    allocation = allocation or _allocation()
    return {
        "id": str(uuid4()),
        "provider": "stripe",
        "entry_type": "payment",
        "status": "posted",
        "external_id": payment_id,
        "artist_id": "artist-fixture",
        "currency": "USD",
        "amount_cents": sum(allocation[key] for key in ("service_cents", "price_adjustment_cents", "tip_cents")),
        "raw": {
            "booking_id": "booking-fixture",
            "payment_intent": payment_id,
            "stripe_session_id": f"cs_{payment_id}",
            "stripe_account_id": "acct_artist",
            "livemode": False,
            "service_allocation": allocation,
            "checkout_type": "booking_deposit",
        },
        **overrides,
    }


def _pos(**overrides):
    return {
        "id": "pos-fixture",
        "booking_id": "booking-fixture",
        "artist_id": "artist-fixture",
        "created_by": "operator-fixture",
        "status": "completed",
        "attempt_version": 1,
        "attempt_state": "succeeded",
        "amount_cents": 6_200,
        "commission_base_cents": 5_000,
        "price_adjustment_cents": 200,
        "tip_cents": 1_000,
        "currency": "USD",
        "livemode": False,
        "stripe_payment_intent_id": "pi_pos",
        "stripe_session_id": "cs_pi_pos",
        "stripe_account_id": "acct_artist",
        **overrides,
    }


def _refund(refund_id="re_fixture", *, amount=1_040, allocation=None, **overrides):
    raw = {
        "payment_intent": "pi_deposit",
        "booking_id": "booking-fixture",
        "stripe_account_id": "acct_artist",
        "livemode": False,
    }
    if allocation is not None:
        raw["service_allocation"] = allocation
    return {
        "provider": "stripe",
        "entry_type": "refund",
        "status": "succeeded",
        "external_id": refund_id,
        "artist_id": "artist-fixture",
        "currency": "USD",
        "amount_cents": -amount,
        "raw": raw,
        **overrides,
    }


def _cash_pair(booking, *, allocation=None, livemode=False):
    allocation = allocation or _allocation()
    gross = sum(allocation[key] for key in ("service_cents", "price_adjustment_cents", "tip_cents"))
    pos = {
        "id": uuid4(),
        "booking_id": booking.id,
        "artist_id": booking.artist_id,
        "created_by": uuid4(),
        "payment_method": "cash",
        "processor": "manual",
        "status": "completed",
        "amount_cents": gross,
        "commission_base_cents": allocation["service_cents"],
        # Preserve the existing POS meaning: everything collected above base.
        "price_adjustment_cents": allocation["price_adjustment_cents"] + allocation["tip_cents"],
    }
    receipt = {
        "id": uuid4(),
        "provider": "manual",
        "external_id": f"pos-cash:{pos['id']}",
        "entry_type": "payment",
        "status": "posted",
        "category": "pos_payment",
        "artist_id": booking.artist_id,
        "currency": "USD",
        "amount_cents": gross,
        "raw": {
            "pos_cash_receipt_version": 1,
            "payment_method": "cash",
            "booking_id": str(booking.id),
            "artist_id": str(booking.artist_id),
            "pos_transaction_id": str(pos["id"]),
            "recorded_by": str(pos["created_by"]),
            "livemode": livemode,
            "service_allocation": allocation,
        },
    }
    return pos, receipt


class BookingServiceBalanceTests(unittest.TestCase):
    def project(self, **kwargs):
        booking = kwargs.pop("booking", _booking())
        total = kwargs.pop("service_total_cents", 20_000)
        include_gift_purchase = kwargs.pop("include_gift_purchase", True)
        kwargs.setdefault(
            "gift_cards",
            [
                {
                    "id": "gift-fixture",
                    "artist_id": "artist-fixture",
                    "currency": "USD",
                    "original_amount_cents": 10_000,
                    "status": "active",
                    "stripe_payment_intent_id": "pi_giftPurchase",
                }
            ],
        )
        if include_gift_purchase:
            purchase = _payment("pi_giftPurchase", _allocation(service=10_000, adjustment=400))
            purchase["raw"].update(booking_id=None, checkout_type="gift_card")
            kwargs["ledger_entries"] = [*kwargs.get("ledger_entries", ()), purchase]
        return project_booking_service_balance(booking, service_total_cents=total, livemode=False, **kwargs)

    def test_deposit_pos_tip_and_gift_redemption_reduce_only_service_debt(self):
        booking = _booking(deposit_paid=True, stripe_payment_intent_id="pi_deposit")
        result = self.project(
            booking=booking,
            pos_transactions=[_pos()],
            ledger_entries=[_payment()],
            gift_card_redemptions=[
                {"id": "redemption-1", "gift_card_id": "gift-fixture", "booking_id": booking.id, "amount_cents": 2_000}
            ],
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 12_000)
        self.assertEqual(result.remaining_service_cents, 8_000)
        self.assertEqual(POSBalanceSnapshot.model_validate(result.snapshot()).remaining_service_cents, 8_000)
        self.assertEqual(booking.deposit_amount, 52)
        self.assertEqual(booking.stripe_payment_intent_id, "pi_deposit")

    def test_pos_and_econ_mirror_are_counted_once_in_either_order(self):
        rows = [
            _payment("pi_pos", _allocation(tip=1_000)),
            _payment("pi_second", _allocation(service=1_000, adjustment=40)),
        ]
        for ledger in (rows, list(reversed(rows))):
            result = self.project(pos_transactions=[_pos()], ledger_entries=ledger)
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.paid_service_cents, 6_000)
            self.assertEqual(len(result.evidence_keys), 2)

    def test_backfilled_legacy_base_is_not_exact_payment_evidence(self):
        legacy = _pos(
            attempt_version=None,
            attempt_state=None,
            commission_base_cents=6_200,
            price_adjustment_cents=0,
            tip_cents=None,
        )
        result = self.project(pos_transactions=[legacy])
        self.assertEqual(result.status, "reconciliation_required")
        self.assertIsNone(result.remaining_service_cents)
        with self.assertRaises(ValueError):
            result.snapshot()

    def test_deposit_mirrored_in_pos_and_econ_is_counted_once(self):
        result = self.project(
            booking=_booking(deposit_paid=True, stripe_payment_intent_id="pi_deposit"),
            pos_transactions=[
                _pos(
                    stripe_payment_intent_id="pi_deposit",
                    stripe_session_id="cs_pi_deposit",
                    amount_cents=5_200,
                    tip_cents=0,
                )
            ],
            ledger_entries=[_payment()],
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 5_000)

    def test_gift_redemption_does_not_resolve_an_ambiguous_paid_deposit(self):
        result = self.project(
            booking=_booking(deposit_paid=True),
            gift_card_redemptions=[
                {
                    "id": "gift-use",
                    "gift_card_id": "gift-fixture",
                    "booking_id": "booking-fixture",
                    "amount_cents": 5_000,
                }
            ],
        )
        self.assertEqual(result.paid_service_cents, 5_000)
        self.assertIsNone(result.remaining_service_cents)
        self.assertIn("paid_deposit_missing_exact_evidence", result.reconciliation_reasons)

    def test_gift_redemption_does_not_hide_an_ambiguous_service_refund(self):
        result = self.project(
            ledger_entries=[_payment(), _refund()],
            gift_card_redemptions=[
                {
                    "id": "gift-use",
                    "gift_card_id": "gift-fixture",
                    "booking_id": "booking-fixture",
                    "amount_cents": 2_000,
                }
            ],
        )
        self.assertIsNone(result.remaining_service_cents)
        self.assertIn("partial_refund_allocation_required", result.reconciliation_reasons)

    def test_matching_exact_ledger_can_resolve_a_legacy_pos_mirror(self):
        legacy = _pos(attempt_version=None, attempt_state=None, tip_cents=None)
        result = self.project(pos_transactions=[legacy], ledger_entries=[_payment("pi_pos", _allocation(tip=1_000))])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 5_000)

    def test_paid_deposit_flag_and_gross_ledger_do_not_invent_service_credit(self):
        for ledger in ([], [_payment(raw={"booking_id": "booking-fixture", "payment_intent": "pi_deposit"})]):
            result = self.project(
                booking=_booking(deposit_paid=True, stripe_payment_intent_id="pi_deposit"),
                ledger_entries=ledger,
            )
            self.assertEqual(result.status, "reconciliation_required")
            self.assertIn("paid_deposit_missing_exact_evidence", result.reconciliation_reasons)

    def test_estimate_or_authorization_is_not_a_paid_deposit(self):
        result = self.project(service_total_cents=None)
        self.assertIsNone(result.remaining_service_cents)
        for state in ("authorized", "pending_checkout"):
            result = self.project(booking=_booking(deposit_authorization_status=state))
            self.assertEqual(result.paid_service_cents, 0)
            self.assertIn("unsettled_deposit_authorization", result.reconciliation_reasons)

    def test_exact_capture_is_counted_even_when_booking_paid_flag_is_stale(self):
        self.assertEqual(self.project(ledger_entries=[_payment()]).remaining_service_cents, 15_000)

    def test_partial_service_refund_reopens_only_service_amount(self):
        result = self.project(
            ledger_entries=[_payment(), _refund(allocation=_allocation(service=1_000, adjustment=40))]
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.refunded_service_cents, 1_000)
        self.assertEqual(result.remaining_service_cents, 16_000)

    def test_tip_only_refund_does_not_reopen_service_debt(self):
        refund = _refund(amount=1_000, allocation=_allocation(service=0, adjustment=0, tip=1_000))
        result = self.project(ledger_entries=[_payment(allocation=_allocation(tip=1_000)), refund])
        self.assertEqual(result.refunded_service_cents, 0)
        self.assertEqual(result.remaining_service_cents, 15_000)

    def test_full_refund_reverses_an_exact_original_without_guessing_a_base(self):
        result = self.project(ledger_entries=[_payment(), _refund(amount=5_200)])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.remaining_service_cents, 20_000)

    def test_unknown_partial_refund_or_account_requires_reconciliation(self):
        unknown_account = _refund(amount=5_200)
        del unknown_account["raw"]["stripe_account_id"]
        for refund in (_refund(), unknown_account):
            result = self.project(ledger_entries=[_payment(), refund])
            self.assertEqual(result.status, "reconciliation_required")
            self.assertIsNone(result.remaining_service_cents)

    def test_refund_without_booking_link_matches_its_payment_in_any_order(self):
        refund = _refund(amount=5_200)
        del refund["raw"]["booking_id"]
        for rows in ([_payment(), refund], [refund, _payment()]):
            result = self.project(ledger_entries=rows)
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.remaining_service_cents, 20_000)

    def test_duplicate_refund_and_redemption_ids_are_not_counted_twice(self):
        refund = _refund(amount=5_200)
        gift = {
            "id": "gift-use",
            "gift_card_id": "gift-fixture",
            "booking_id": "booking-fixture",
            "amount_cents": 2_000,
        }
        result = self.project(
            ledger_entries=[_payment(), refund, deepcopy(refund)], gift_card_redemptions=[gift, dict(gift)]
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 7_000)
        self.assertEqual(result.refunded_service_cents, 5_000)

    def test_conflicting_mirrors_or_excess_refunds_never_return_chargeable_balance(self):
        wrong_account = _payment("pi_pos", _allocation(tip=1_000))
        wrong_account["raw"]["stripe_account_id"] = "acct_different"
        wrong_amount = _payment("pi_pos", _allocation(tip=2_000))
        for ledger in ([wrong_account], [wrong_amount]):
            self.assertIsNone(self.project(pos_transactions=[_pos()], ledger_entries=ledger).remaining_service_cents)
        result = self.project(
            ledger_entries=[_payment(), _refund("re_one", amount=5_200), _refund("re_two", amount=5_200)]
        )
        self.assertIn("refund_exceeds_original_allocation", result.reconciliation_reasons)

    def test_unsettled_attempt_or_refund_blocks_a_second_collection(self):
        result = self.project(pos_transactions=[_pos(status="pending", attempt_state="cancel_requested")])
        self.assertIsNone(result.remaining_service_cents)
        result = self.project(ledger_entries=[_payment(), _refund(status="pending", amount_cents=0)])
        self.assertIsNone(result.remaining_service_cents)

    def test_failed_attempts_and_refunds_do_not_reduce_balance(self):
        result = self.project(
            pos_transactions=[_pos(status="voided", attempt_state="failed")],
            ledger_entries=[_payment(), _refund(status="failed", amount_cents=0)],
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.remaining_service_cents, 15_000)

    def test_other_booking_and_gift_purchase_are_not_service_credits(self):
        gift_purchase = _payment("pi_gift")
        gift_purchase["raw"]["checkout_type"] = "gift_card"
        unrelated = _payment("pi_other")
        unrelated["raw"]["booking_id"] = "other-booking"
        result = self.project(
            pos_transactions=[_pos(booking_id="other-booking")],
            ledger_entries=[gift_purchase, unrelated],
            gift_card_redemptions=[{"id": "other-use", "booking_id": "other-booking", "amount_cents": 5_000}],
        )
        self.assertEqual(result.remaining_service_cents, 20_000)

    def test_identity_currency_and_non_integer_evidence_fail_closed(self):
        for row in (
            _payment(artist_id="operator-fixture"),
            _payment(artist_id=None),
            _payment(currency="EUR"),
            _payment(amount_cents=5_200.0),
        ):
            self.assertIsNone(self.project(ledger_entries=[row]).remaining_service_cents)
        malformed = _payment()
        malformed["raw"]["service_allocation"]["service_cents"] = "5000"
        self.assertIsNone(self.project(ledger_entries=[malformed]).remaining_service_cents)

    def test_live_payment_evidence_cannot_be_reused_in_test_mode(self):
        live = _payment()
        live["raw"]["livemode"] = True
        result = self.project(ledger_entries=[live])
        self.assertIsNone(result.remaining_service_cents)
        self.assertIn("payment_mode_mismatch", result.reconciliation_reasons)
        unknown = _payment()
        del unknown["raw"]["livemode"]
        self.assertIsNone(self.project(ledger_entries=[unknown]).remaining_service_cents)

    def test_missing_gift_card_or_reassigned_booking_requires_reconciliation(self):
        gift = {
            "id": "gift-use",
            "gift_card_id": "gift-fixture",
            "booking_id": "booking-fixture",
            "amount_cents": 2_000,
        }
        missing = self.project(gift_card_redemptions=[gift], gift_cards=[])
        self.assertIn("gift_card_provenance_required", missing.reconciliation_reasons)
        reassigned = self.project(booking=_booking(artist_id="replacement-artist"), gift_card_redemptions=[gift])
        self.assertIn("gift_card_artist_mismatch", reassigned.reconciliation_reasons)
        self.assertIsNone(reassigned.remaining_service_cents)

    def test_refunded_or_foreign_currency_gift_card_cannot_credit_service(self):
        gift = {
            "id": "gift-use",
            "gift_card_id": "gift-fixture",
            "booking_id": "booking-fixture",
            "amount_cents": 2_000,
        }
        for currency, status in (("EUR", "active"), ("USD", "voided"), ("USD", "pending")):
            card = {
                "id": "gift-fixture",
                "artist_id": "artist-fixture",
                "currency": currency,
                "status": status,
                "original_amount_cents": 10_000,
            }
            result = self.project(gift_card_redemptions=[gift], gift_cards=[card])
            self.assertIsNone(result.remaining_service_cents)

    def test_gift_purchase_mode_must_be_proven_before_redemption_credit(self):
        gift = {
            "id": "gift-use",
            "gift_card_id": "gift-fixture",
            "booking_id": "booking-fixture",
            "amount_cents": 2_000,
        }
        missing = self.project(gift_card_redemptions=[gift], include_gift_purchase=False)
        self.assertIn("gift_card_purchase_mode_requires_reconciliation", missing.reconciliation_reasons)
        live_purchase = _payment("pi_giftPurchase", _allocation(service=10_000, adjustment=400))
        live_purchase["raw"].update(booking_id=None, checkout_type="gift_card", livemode=True)
        wrong_mode = self.project(
            gift_card_redemptions=[gift], ledger_entries=[live_purchase], include_gift_purchase=False
        )
        self.assertIsNone(wrong_mode.remaining_service_cents)
        self.assertIn("gift_card_purchase_mode_requires_reconciliation", wrong_mode.reconciliation_reasons)

    def test_settled_tip_only_and_overpayment_never_create_negative_service_debt(self):
        tip_only = _pos(amount_cents=1_000, commission_base_cents=0, price_adjustment_cents=0)
        self.assertEqual(self.project(pos_transactions=[tip_only]).remaining_service_cents, 20_000)
        result = self.project(service_total_cents=3_000, ledger_entries=[_payment()])
        self.assertEqual(result.remaining_service_cents, 0)
        self.assertEqual(result.paid_service_cents, 5_000)


class ManualCashBalanceTests(unittest.TestCase):
    def setUp(self):
        self.booking = _booking(id=uuid4(), artist_id=uuid4())

    def project(self, *, pos=(), receipts=(), livemode=False, booking=None):
        return project_booking_service_balance(
            booking or self.booking,
            service_total_cents=20_000,
            livemode=livemode,
            pos_transactions=pos,
            ledger_entries=receipts,
        )

    def assert_reconciliation(self, **kwargs):
        result = self.project(**kwargs)
        self.assertEqual(result.status, "reconciliation_required")
        self.assertIsNone(result.remaining_service_cents)
        return result

    def test_exact_cash_and_its_pos_mirror_credit_service_once_without_a_stripe_account(self):
        pos, receipt = _cash_pair(self.booking)
        before = deepcopy((pos, receipt))
        result = self.project(pos=[pos], receipts=[receipt])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 5_000)
        self.assertEqual(result.remaining_service_cents, 15_000)
        self.assertEqual(result.evidence_keys, (f"payment:manual:pos:{pos['id']}",))
        self.assertNotEqual(pos["created_by"], pos["artist_id"])
        self.assertEqual((pos, receipt), before)

    def test_exact_cash_and_paid_stripe_deposit_are_distinct_credits(self):
        pos, receipt = _cash_pair(self.booking)
        deposit = _payment(artist_id=self.booking.artist_id)
        deposit["raw"]["booking_id"] = str(self.booking.id)
        self.booking.deposit_paid = True
        self.booking.stripe_payment_intent_id = "pi_deposit"
        for receipts in ([receipt, deposit], [deposit, receipt]):
            result = self.project(pos=[pos], receipts=receipts)
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.paid_service_cents, 10_000)
            self.assertEqual(len(result.evidence_keys), 2)

    def test_legacy_cash_or_an_unversioned_manual_row_does_not_prove_an_allocation(self):
        pos, receipt = _cash_pair(self.booking)
        self.assert_reconciliation(pos=[pos])
        del receipt["raw"]["pos_cash_receipt_version"]
        self.assert_reconciliation(pos=[pos], receipts=[receipt])
        self.assert_reconciliation(receipts=[receipt])

    def test_a_manual_receipt_requires_its_matching_persisted_pos_row(self):
        pos, receipt = _cash_pair(self.booking)
        self.assert_reconciliation(receipts=[receipt])
        receipt["raw"]["pos_transaction_id"] = str(uuid4())
        self.assert_reconciliation(pos=[pos], receipts=[receipt])

    def test_each_canonical_receipt_identity_is_required_and_must_match(self):
        for key in ("booking_id", "artist_id", "pos_transaction_id", "recorded_by"):
            for value in (None, "", "not-a-uuid", str(uuid4())):
                with self.subTest(key=key, value=value):
                    pos, receipt = _cash_pair(self.booking)
                    receipt["raw"][key] = value
                    self.assert_reconciliation(pos=[pos], receipts=[receipt])
        pos, receipt = _cash_pair(self.booking)
        self.assert_reconciliation(
            pos=[pos], receipts=[receipt], booking=_booking(id=self.booking.id, artist_id=uuid4())
        )

    def test_receipt_status_amount_currency_provider_and_identity_are_validated(self):
        for key, value in (
            ("id", None),
            ("id", "not-a-uuid"),
            ("external_id", "pos-cash:wrong"),
            ("provider", "stripe"),
            ("provider", "plaid"),
            ("entry_type", "refund"),
            ("status", "pending"),
            ("status", "removed"),
            ("category", "contractor_contribution"),
            ("artist_id", uuid4()),
            ("currency", "EUR"),
            ("amount_cents", 5_201),
            ("amount_cents", 5_200.0),
            ("amount_cents", True),
            ("amount_cents", 0),
        ):
            with self.subTest(key=key, value=value):
                pos, receipt = _cash_pair(self.booking)
                receipt[key] = value
                self.assert_reconciliation(pos=[pos], receipts=[receipt])

    def test_receipt_version_method_and_exact_integer_components_cannot_be_coerced(self):
        for key, value in (
            ("pos_cash_receipt_version", True),
            ("pos_cash_receipt_version", "1"),
            ("pos_cash_receipt_version", 2),
            ("payment_method", "card"),
            ("service_allocation", None),
            ("service_allocation", _allocation(service="5000")),
            ("service_allocation", _allocation(adjustment=200.0)),
            ("service_allocation", _allocation(tip=True)),
            ("service_allocation", _allocation(tip=1)),
        ):
            with self.subTest(key=key, value=value):
                pos, receipt = _cash_pair(self.booking)
                receipt["raw"][key] = value
                self.assert_reconciliation(pos=[pos], receipts=[receipt])

    def test_missing_receipt_fields_do_not_become_defaults(self):
        for key in _cash_pair(self.booking)[1]["raw"]:
            with self.subTest(key=key):
                pos, receipt = _cash_pair(self.booking)
                del receipt["raw"][key]
                self.assert_reconciliation(pos=[pos], receipts=[receipt])
        for raw in (None, [], "invalid"):
            pos, receipt = _cash_pair(self.booking)
            receipt["raw"] = raw
            self.assert_reconciliation(pos=[pos], receipts=[receipt])

    def test_pos_must_be_completed_cash_manual_and_agree_with_the_receipt(self):
        for key, value in (
            ("booking_id", uuid4()),
            ("artist_id", uuid4()),
            ("created_by", uuid4()),
            ("payment_method", "card"),
            ("processor", None),
            ("processor", "stripe"),
            ("status", "pending"),
            ("status", "voided"),
            ("amount_cents", 5_201),
            ("amount_cents", 5_200.0),
            ("commission_base_cents", 5_200),
            ("price_adjustment_cents", 0),
            ("attempt_version", 1),
            ("attempt_state", "succeeded"),
            ("tip_cents", 0),
            ("livemode", False),
            ("stripe_session_id", "cs_cash"),
            ("stripe_payment_intent_id", "pi_cash"),
            ("stripe_account_id", "acct_cash"),
        ):
            with self.subTest(key=key, value=value):
                pos, receipt = _cash_pair(self.booking)
                pos[key] = value
                self.assert_reconciliation(pos=[pos], receipts=[receipt])

    def test_cash_receipt_cannot_claim_platform_or_connected_stripe_provenance(self):
        for key, value in (
            ("stripe_account_id", None),
            ("stripe_account_id", "acct_artist"),
            ("payment_intent", "pi_cash"),
            ("stripe_session_id", "cs_cash"),
        ):
            pos, receipt = _cash_pair(self.booking)
            receipt["raw"][key] = value
            self.assert_reconciliation(pos=[pos], receipts=[receipt])

    def test_stripe_payment_cannot_reuse_a_manual_cash_pos_identity(self):
        pos, receipt = _cash_pair(self.booking)
        stripe_payment = _payment(artist_id=self.booking.artist_id)
        stripe_payment["raw"].update(
            booking_id=str(self.booking.id), payment_intent=f"pos:{pos['id']}", stripe_account_id=None
        )
        for receipts in ([receipt, stripe_payment], [stripe_payment, receipt]):
            self.assert_reconciliation(pos=[pos], receipts=receipts)

    def test_tip_allocation_preserves_legacy_gross_minus_base_mirror_semantics(self):
        pos, receipt = _cash_pair(self.booking, allocation=_allocation(tip=1_000))
        self.assertEqual(pos["price_adjustment_cents"], 1_200)
        result = self.project(pos=[pos], receipts=[receipt])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 5_000)
        pos["price_adjustment_cents"] = 200
        self.assert_reconciliation(pos=[pos], receipts=[receipt])

    def test_explicit_tip_only_cash_never_extinguishes_service_debt(self):
        pos, receipt = _cash_pair(self.booking, allocation=_allocation(service=0, adjustment=0, tip=1_000))
        result = self.project(pos=[pos], receipts=[receipt])
        self.assertEqual(pos["price_adjustment_cents"], 1_000)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 0)
        self.assertEqual(result.remaining_service_cents, 20_000)

    def test_odd_cent_cash_components_are_preserved_without_recalculating_a_base(self):
        pos, receipt = _cash_pair(self.booking, allocation=_allocation(service=13, adjustment=1, tip=3))
        result = self.project(pos=[pos], receipts=[receipt])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 13)
        self.assertEqual(pos["amount_cents"], 17)
        self.assertEqual(pos["price_adjustment_cents"], 4)

    def test_duplicate_receipts_or_pos_identities_require_reconciliation(self):
        pos, receipt = _cash_pair(self.booking)
        self.assert_reconciliation(pos=[pos, deepcopy(pos)], receipts=[receipt])
        result = self.assert_reconciliation(pos=[pos], receipts=[receipt, deepcopy(receipt)])
        self.assertIn("duplicate_manual_cash_receipt", result.reconciliation_reasons)
        conflicting = deepcopy(receipt)
        conflicting["id"] = uuid4()
        conflicting["raw"]["service_allocation"] = _allocation(service=5_100, adjustment=100)
        self.assert_reconciliation(pos=[pos], receipts=[receipt, conflicting])
        other_pos, other_receipt = _cash_pair(self.booking)
        other_receipt["id"] = receipt["id"]
        self.assert_reconciliation(pos=[pos, other_pos], receipts=[receipt, other_receipt])

    def test_separate_exact_cash_payments_are_counted_independently(self):
        first_pos, first_receipt = _cash_pair(self.booking)
        second_pos, second_receipt = _cash_pair(self.booking)
        result = self.project(pos=[first_pos, second_pos], receipts=[first_receipt, second_receipt])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 10_000)
        self.assertEqual(len(result.evidence_keys), 2)

    def test_cash_mode_is_explicit_and_cannot_cross_test_live_boundaries(self):
        for expected in (False, True):
            pos, receipt = _cash_pair(self.booking, livemode=expected)
            self.assertEqual(self.project(pos=[pos], receipts=[receipt], livemode=expected).status, "ready")
            for mode in (not expected, None, "false", 0):
                receipt["raw"]["livemode"] = mode
                self.assert_reconciliation(pos=[pos], receipts=[receipt], livemode=expected)
        first_pos, first_receipt = _cash_pair(self.booking, livemode=False)
        second_pos, second_receipt = _cash_pair(self.booking, livemode=True)
        for expected in (False, True):
            self.assert_reconciliation(
                pos=[first_pos, second_pos], receipts=[first_receipt, second_receipt], livemode=expected
            )

    def test_cash_and_stripe_payment_modes_cannot_be_mixed_for_one_booking(self):
        for expected in (False, True):
            pos, receipt = _cash_pair(self.booking, livemode=expected)
            stripe_payment = _payment(artist_id=self.booking.artist_id)
            stripe_payment["raw"].update(booking_id=str(self.booking.id), livemode=not expected)
            self.assert_reconciliation(pos=[pos], receipts=[receipt, stripe_payment], livemode=expected)

    def test_other_booking_cash_and_manual_booth_rent_are_not_service_credit(self):
        other = _booking(id=uuid4(), artist_id=self.booking.artist_id)
        pos, receipt = _cash_pair(other)
        booth_rent = {
            "provider": "manual",
            "entry_type": "payment",
            "status": "posted",
            "amount_cents": 50_000,
            "artist_id": self.booking.artist_id,
            "category": "contractor_contribution",
            "raw": {"contribution_type": "booth_rent", "payment_method": "cash"},
        }
        result = self.project(pos=[pos], receipts=[receipt, booth_rent])
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.paid_service_cents, 0)

    def test_cash_cannot_resolve_a_missing_stripe_deposit_or_take_a_stripe_refund(self):
        pos, receipt = _cash_pair(self.booking)
        self.booking.deposit_paid = True
        self.booking.stripe_payment_intent_id = "pi_unproven"
        self.assert_reconciliation(pos=[pos], receipts=[receipt])
        self.booking.deposit_paid = False
        refund = _refund(amount=5_200, artist_id=self.booking.artist_id)
        refund["raw"].update(booking_id=str(self.booking.id), payment_intent=f"pos:{pos['id']}", stripe_account_id=None)
        result = self.assert_reconciliation(pos=[pos], receipts=[receipt, refund])
        self.assertIn("refund_missing_exact_payment", result.reconciliation_reasons)

    def test_an_unproven_manual_refund_cannot_be_ignored_beside_an_exact_cash_receipt(self):
        pos, receipt = _cash_pair(self.booking)
        refund = {
            "provider": "manual",
            "external_id": "cash-refund:unproven",
            "entry_type": "refund",
            "amount_cents": -5_200,
            "raw": {"booking_id": str(self.booking.id), "pos_transaction_id": str(pos["id"])},
        }
        self.assert_reconciliation(pos=[pos], receipts=[receipt, refund])


class AttemptInputContractTests(unittest.TestCase):
    def payload(self, **overrides):
        return {
            "request_key": "attempt-fixture",
            "artist_id": uuid4(),
            "collection_method": "qr",
            "service_total_cents": 10_000,
            **overrides,
        }

    def test_attempt_input_cannot_override_operator_account_or_split(self):
        for field, value in (
            ("created_by", uuid4()),
            ("stripe_account_id", "acct_override"),
            ("amount_cents", 1),
            ("commission_rate", 100),
            ("livemode", True),
            ("reader_eligibility_ref", "untrusted-claim"),
        ):
            with self.assertRaises(ValidationError):
                POSAttemptCreate.model_validate(self.payload(**{field: value}))

    def test_tip_is_integer_cents_and_reader_selection_matches_collection(self):
        self.assertEqual(POSAttemptCreate.model_validate(self.payload()).tip_cents, 0)
        for overrides in (
            {"tip_cents": -1},
            {"tip_cents": 1.5},
            {"tip_cents": True},
            {"collection_method": "reader"},
            {"terminal_reader_id": "tmr_fixture"},
        ):
            with self.assertRaises(ValidationError):
                POSAttemptCreate.model_validate(self.payload(**overrides))
        body = POSAttemptCreate.model_validate(
            self.payload(collection_method="reader", terminal_reader_id="tmr_fixture")
        )
        self.assertEqual(body.terminal_reader_id, "tmr_fixture")

    def test_legacy_manual_record_input_is_unchanged(self):
        legacy = POSTransactionCreate(amount_cents=1_000, payment_method="cash")
        self.assertIsNone(legacy.commission_base_cents)
        self.assertNotIn("attempt_version", legacy.model_dump())

    def test_inconsistent_balance_snapshot_is_rejected(self):
        snapshot = project_booking_service_balance(_booking(), service_total_cents=10_000, livemode=False).snapshot()
        snapshot["remaining_service_cents"] = 20_000
        with self.assertRaises(ValidationError):
            POSBalanceSnapshot.model_validate(snapshot)
