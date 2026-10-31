"""Tests for the shared charge routing resolver."""

from types import SimpleNamespace

from app.services.payments import (
    DESTINATION_ARTIST,
    DESTINATION_SHOP,
    apply_customer_receipt,
    connect_account_ready,
    customer_price_cents,
    effective_artist_rate,
    resolve_charge_routing,
)


def _artist(rate=None, account=None, onboarded=False, slug="test", role="artist"):
    return SimpleNamespace(
        role=role,
        commission_rate=rate,
        stripe_account_id=account,
        stripe_onboarding_complete=onboarded,
        slug=slug,
        email=f"{slug}@example.com",
        id=slug,
    )


def test_connected_artist_shop_takes_thirty_percent():
    r = resolve_charge_routing(_artist(70, "acct_1", True), 20000)
    assert r.destination == DESTINATION_ARTIST
    assert r.shop_cut_cents == 6000
    assert r.artist_cut_cents == 14000
    assert r.payout_owed_cents == 0
    assert r.settled_by_stripe is True
    assert r.payment_intent_data["application_fee_amount"] == 6000
    assert "transfer_data" not in r.payment_intent_data
    assert r.request_options() == {"stripe_account": "acct_1"}
    assert "on_behalf_of" not in r.payment_intent_data
    assert r.receipt_metadata() == {
        "commission_rate": "70",
        "commission_base_cents": "20000",
        "customer_price_adjustment_cents": "0",
        "customer_price_increase_bps": "400",
        "artist_cut_cents": "14000",
        "shop_cut_cents": "6000",
        "payout_owed_cents": "0",
        "shop_share_category": "contractor_contribution",
    }


def test_booth_rent_artist_pays_no_shop_cut():
    """Booth-rent artists: commission_rate 100, direct charge with no fee."""
    r = resolve_charge_routing(_artist(100, "acct_2", True, "jade"), 20000)
    assert r.destination == DESTINATION_ARTIST
    assert r.shop_cut_cents == 0
    assert r.artist_cut_cents == 20000
    assert "application_fee_amount" not in r.payment_intent_data
    assert "on_behalf_of" not in r.payment_intent_data
    assert r.reason == "connected_booth_rent"


def test_not_connected_falls_back_to_shop_and_records_payout():
    r = resolve_charge_routing(_artist(70, None, False), 20000)
    assert r.destination == DESTINATION_SHOP
    assert r.shop_cut_cents == 6000
    assert r.payout_owed_cents == 14000
    assert r.settled_by_stripe is False
    assert r.payment_intent_data == {}
    assert r.reason == "not_connected"


def test_onboarding_incomplete_does_not_route_to_artist():
    """Account id exists but charges are not enabled yet."""
    r = resolve_charge_routing(_artist(70, "acct_3", False), 10000)
    assert r.destination == DESTINATION_SHOP
    assert r.reason == "onboarding_incomplete"
    assert r.payout_owed_cents == 7000


def test_missing_rate_defaults_to_seventy():
    r = resolve_charge_routing(_artist(None, "acct_4", True), 10000)
    assert r.commission_rate == 70
    assert r.shop_cut_cents == 3000


def test_zero_rate_is_preserved_instead_of_defaulting_to_seventy():
    artist = _artist(0, "acct_zero", True)
    assert effective_artist_rate(artist) == 0
    r = resolve_charge_routing(artist, 10000)
    assert r.artist_cut_cents == 0
    assert r.shop_cut_cents == 10000
    assert r.destination == DESTINATION_SHOP
    assert r.payment_intent_data == {}
    assert r.reason == "zero_artist_share"


def test_unconfigured_founder_routes_everything_to_shop():
    founder = _artist(None, "acct_founder", True, "justin", role="founder")

    assert effective_artist_rate(founder) == 0
    routing = resolve_charge_routing(founder, 10_400, artist_share_base_cents=10_000)

    assert routing.commission_rate == 0
    assert routing.artist_cut_cents == 0
    assert routing.shop_cut_cents == 10_400
    assert routing.destination == DESTINATION_SHOP
    assert routing.settled_by_stripe is False
    assert routing.request_options() is None
    assert routing.reason == "zero_artist_share"


def test_connect_account_must_be_fully_payout_ready():
    account = SimpleNamespace(
        details_submitted=True,
        charges_enabled=True,
        payouts_enabled=True,
        capabilities=SimpleNamespace(transfers="active"),
    )
    assert connect_account_ready(account) is True
    account.payouts_enabled = False
    assert connect_account_ready(account) is False


def test_accounts_v2_merchant_must_have_active_cards_and_payouts():
    account = {
        "configuration": {
            "merchant": {
                "capabilities": {
                    "card_payments": {"status": "active"},
                    "stripe_balance": {"payouts": {"status": "active"}},
                }
            }
        }
    }
    assert connect_account_ready(account) is True
    account["configuration"]["merchant"]["capabilities"]["card_payments"]["status"] = "inactive"
    assert connect_account_ready(account) is False


def test_rounding_remainder_goes_to_shop():
    r = resolve_charge_routing(_artist(70, "acct_5", True), 10001)
    assert r.artist_cut_cents + r.shop_cut_cents == 10001
    assert r.artist_cut_cents == 7000


def test_customer_price_is_four_percent_all_in_and_rounded_to_cents():
    assert customer_price_cents(10_000) == 10_400
    assert customer_price_cents(5_000) == 5_200
    assert customer_price_cents(13) == 14
    assert customer_price_cents(0) == 0


def test_price_adjustment_stays_with_shop_without_reducing_artist_service_share():
    r = resolve_charge_routing(
        _artist(70, "acct_adjusted", True),
        10_400,
        artist_share_base_cents=10_000,
    )

    assert r.amount_cents == 10_400
    assert r.commission_base_cents == 10_000
    assert r.price_adjustment_cents == 400
    assert r.artist_cut_cents == 7_000
    assert r.shop_cut_cents == 3_400
    assert r.payment_intent_data["application_fee_amount"] == 3_400


def test_booth_artist_keeps_full_service_price_while_shop_keeps_adjustment():
    r = resolve_charge_routing(
        _artist(100, "acct_booth", True),
        10_400,
        artist_share_base_cents=10_000,
    )

    assert r.artist_cut_cents == 10_000
    assert r.shop_cut_cents == 400
    assert r.payment_intent_data["application_fee_amount"] == 400
    assert r.reason == "connected_price_adjustment"


def test_unconnected_artist_is_owed_share_of_service_price_only():
    r = resolve_charge_routing(
        _artist(70, None, False),
        10_400,
        artist_share_base_cents=10_000,
    )

    assert r.payout_owed_cents == 7_000
    assert r.shop_cut_cents == 3_400


def test_customer_receipt_merges_routing_and_exact_payment_context():
    params = {
        "metadata": {"checkout_existing": "kept"},
        "payment_intent_data": {
            "transfer_data": {"destination": "acct_1"},
            "metadata": {"existing": "kept"},
        },
    }

    result = apply_customer_receipt(
        params,
        customer_email="client@example.test",
        description="Ryozan tattoo deposit - BKG-TEST1",
        metadata={"booking_id": "booking-1", "reference_code": "BKG-TEST1"},
    )

    payment_intent = result["payment_intent_data"]
    assert result["customer_email"] == "client@example.test"
    assert result["custom_text"]["submit"]["message"].startswith("All payments are final")
    assert payment_intent["receipt_email"] == "client@example.test"
    assert payment_intent["description"] == "Ryozan tattoo deposit - BKG-TEST1"
    assert payment_intent["transfer_data"] == {"destination": "acct_1"}
    assert payment_intent["metadata"] == {
        "existing": "kept",
        "booking_id": "booking-1",
        "reference_code": "BKG-TEST1",
        "refund_policy": "all_payments_final",
        "processing_fee_policy": "included_in_displayed_amount",
    }
    assert result["metadata"] == {
        "checkout_existing": "kept",
        "booking_id": "booking-1",
        "reference_code": "BKG-TEST1",
        "refund_policy": "all_payments_final",
        "processing_fee_policy": "included_in_displayed_amount",
    }


def test_customer_receipt_preserves_other_checkout_custom_text():
    params = {"custom_text": {"after_submit": {"message": "Check your email for the receipt."}}}

    result = apply_customer_receipt(
        params,
        customer_email=None,
        description="Ryozan tattoo payment",
        metadata={},
    )

    assert result["custom_text"]["after_submit"] == {"message": "Check your email for the receipt."}
    assert result["custom_text"]["submit"]["message"].startswith("All payments are final")


def test_tip_goes_to_service_artist_without_an_additional_price_increase():
    routing = resolve_charge_routing(
        _artist(70, "acct_service_artist", True),
        12_400,
        artist_share_base_cents=10_000,
        tip_cents=2_000,
    )
    assert routing.commission_base_cents == 10_000
    assert routing.price_adjustment_cents == 400
    assert routing.tip_cents == routing.artist_tip_cents == 2_000
    assert routing.artist_cut_cents == 9_000
    assert routing.shop_cut_cents == routing.payment_intent_data["application_fee_amount"] == 3_400
    assert routing.request_options(idempotency_key="attempt-fixture") == {
        "stripe_account": "acct_service_artist",
        "idempotency_key": "attempt-fixture",
    }
    assert routing.receipt_metadata()["tip_cents"] == "2000"
    assert routing.ledger_fields()["artist_tip_cents"] == 2_000
    assert "transfer_data" not in routing.payment_intent_data


def test_tip_is_manually_owed_when_the_service_artist_is_not_connected():
    routing = resolve_charge_routing(_artist(70), 12_400, artist_share_base_cents=10_000, tip_cents=2_000)
    assert routing.destination == DESTINATION_SHOP
    assert routing.payout_owed_cents == 9_000
    assert routing.shop_cut_cents == 3_400
    assert routing.request_options() is None


def test_founder_tip_preserves_full_shop_settlement():
    for rate in (None, 0):
        routing = resolve_charge_routing(
            _artist(rate, "acct_founder", True, role="founder"),
            12_400,
            artist_share_base_cents=10_000,
            tip_cents=2_000,
        )
        assert routing.destination == DESTINATION_SHOP
        assert routing.artist_cut_cents == routing.artist_tip_cents == routing.payout_owed_cents == 0
        assert routing.shop_cut_cents == 12_400
        assert routing.tip_cents == 2_000
        assert routing.request_options() is None


def test_tip_only_payment_and_zero_commission_artist_keep_tip_separate():
    for rate in (0, 70, 100):
        routing = resolve_charge_routing(
            _artist(rate, "acct_artist", True),
            2_000,
            artist_share_base_cents=0,
            tip_cents=2_000,
        )
        assert routing.commission_base_cents == routing.price_adjustment_cents == 0
        assert routing.artist_cut_cents == routing.artist_tip_cents == 2_000
        assert routing.shop_cut_cents == 0
        assert routing.payment_intent_data == {}


def test_odd_cent_service_rounds_adjustment_once_before_adding_tip():
    service, tip = 13, 3
    routing = resolve_charge_routing(
        _artist(70, "acct_artist", True),
        customer_price_cents(service) + tip,
        artist_share_base_cents=service,
        tip_cents=tip,
    )
    assert routing.amount_cents == 17
    assert routing.price_adjustment_cents == 1
    assert routing.artist_cut_cents == 12
    assert routing.shop_cut_cents == 5


def test_explicit_zero_tip_preserves_every_legacy_routing_result():
    for role in ("artist", "founder"):
        for rate in (None, 0, 70, 100):
            for account, ready in ((None, False), ("acct_fixture", False), ("acct_fixture", True)):
                artist = _artist(rate, account, ready, role=role)
                legacy = resolve_charge_routing(artist, 10_400, artist_share_base_cents=10_000)
                explicit = resolve_charge_routing(artist, 10_400, artist_share_base_cents=10_000, tip_cents=0)
                assert explicit == legacy
                assert "tip_cents" not in explicit.receipt_metadata()
                assert "tip_cents" not in explicit.ledger_fields()
