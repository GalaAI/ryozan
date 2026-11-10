"""Gift-card checkout keeps the established per-artist payment contract."""

import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints.gift_cards import (
    MAX_AMOUNT_CENTS,
    MIN_AMOUNT_CENTS,
    GiftCardCheckoutRequest,
    activate_paid_gift_card,
    create_gift_card_checkout,
    get_gift_card_status,
    get_gift_card_terms,
    get_gift_cards_summary,
    void_refunded_gift_card,
    void_unpaid_gift_card,
)
from app.services.email_foundry import render_gift_card_receipt
from app.services.payments import CUSTOMER_PRICE_INCREASE_BPS, customer_price_cents


class _ScalarResult:
    def scalar_one_or_none(self):
        return None


class _Db:
    def __init__(self, artist):
        self.artist = artist
        self.card = None

    async def get(self, _model, _id):
        return self.artist

    async def execute(self, _statement):
        return _ScalarResult()

    def add(self, value):
        self.card = value

    async def flush(self):
        if self.card.id is None:
            self.card.id = uuid.uuid4()

    async def commit(self):
        return None

    async def rollback(self):
        return None


class _ActivationDb:
    def __init__(self, card, artist):
        self.card = card
        self.artist = artist
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.card)

    async def get(self, _model, _id):
        return self.artist

    async def commit(self):
        self.commits += 1


def _artist(*, name: str, role: str = "artist", rate: int | None, account: str | None, ready: bool):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug=name.lower(),
        name=name,
        role=role,
        commission_rate=rate,
        stripe_account_id=account,
        stripe_onboarding_complete=ready,
        email=f"{name.lower()}@example.test",
    )


def _body(artist_id):
    return GiftCardCheckoutRequest(
        artist_id=artist_id,
        amount_cents=10_000,
        purchaser_name="Buyer",
        purchaser_email="buyer@example.com",
        recipient_name="Recipient",
        recipient_email="recipient@example.com",
        message="For your next tattoo",
    )


class GiftCardCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def _checkout(self, artist, body=None):
        db = _Db(artist)
        create = unittest.mock.Mock(
            return_value=SimpleNamespace(id="cs_test_gift", url="https://checkout.stripe.test/gift")
        )
        stripe_client = SimpleNamespace(
            v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(create=create)))
        )
        settings = SimpleNamespace(FRONTEND_URL="https://ryozan.app", GIFT_CARD_CODE_PREFIX="GC")
        with (
            patch("app.api.v1.endpoints.gift_cards.get_stripe_client", return_value=stripe_client),
            patch("app.api.v1.endpoints.gift_cards.get_settings", return_value=settings),
        ):
            response = await create_gift_card_checkout(body or _body(artist.id), db)
        return db, create, response

    def test_direct_delivery_requires_recipient_email(self):
        with self.assertRaisesRegex(ValidationError, "Recipient email is required for direct delivery"):
            GiftCardCheckoutRequest(
                artist_id=uuid.uuid4(),
                amount_cents=10_000,
                purchaser_name="Buyer",
                purchaser_email="buyer@example.com",
                recipient_name="Recipient",
                recipient_email=None,
                send_to_recipient=True,
            )

    async def test_purchaser_delivery_allows_missing_recipient_email(self):
        artist = _artist(name="Jade", rate=100, account="acct_jade", ready=True)
        body = _body(artist.id)
        body.send_to_recipient = False
        body.recipient_email = None

        db, create, _response = await self._checkout(artist, body)

        params, _options = create.call_args.args
        self.assertFalse(db.card.send_to_recipient)
        self.assertIsNone(db.card.recipient_email)
        self.assertEqual(params["metadata"]["delivery_destination"], "purchaser")

    async def test_artist_routes_seventy_percent_and_shop_keeps_thirty_plus_adjustment(self):
        db, create, response = await self._checkout(_artist(name="Rowan", rate=70, account="acct_rowan", ready=True))
        params, options = create.call_args.args
        self.assertEqual(response.face_value_cents, 10_000)
        self.assertEqual(response.charged_amount_cents, 10_400)
        self.assertEqual(params["payment_intent_data"]["application_fee_amount"], 3_400)
        self.assertEqual(options["stripe_account"], "acct_rowan")
        self.assertEqual(db.card.artist_cut_cents, 7_000)
        self.assertEqual(db.card.shop_cut_cents, 3_400)
        self.assertEqual(db.card.payout_owed_cents, 0)

    async def test_unconnected_artists_are_captured_by_shop_and_marked_manually_owed(self):
        for name, rate, artist_cut, shop_cut in (
            ("Sample Artist B", 70, 7_000, 3_400),
            ("Sample Artist", 50, 5_000, 5_400),
            ("Devin", 70, 7_000, 3_400),
        ):
            with self.subTest(artist=name):
                db, create, _response = await self._checkout(_artist(name=name, rate=rate, account=None, ready=False))
                params, options = create.call_args.args
                self.assertNotIn("stripe_account", options)
                self.assertNotIn("application_fee_amount", params["payment_intent_data"])
                self.assertEqual(db.card.routing_destination, "shop")
                self.assertEqual(db.card.artist_cut_cents, artist_cut)
                self.assertEqual(db.card.payout_owed_cents, artist_cut)
                self.assertEqual(db.card.shop_cut_cents, shop_cut)

    async def test_founder_gift_card_settles_fully_to_shop(self):
        db, create, _response = await self._checkout(
            _artist(name="Justin", role="founder", rate=None, account="acct_founder", ready=True)
        )
        _params, options = create.call_args.args
        self.assertNotIn("stripe_account", options)
        self.assertEqual(db.card.artist_cut_cents, 0)
        self.assertEqual(db.card.shop_cut_cents, 10_400)
        self.assertEqual(db.card.payout_owed_cents, 0)

    async def test_booth_artists_get_full_face_value_and_shop_keeps_adjustment(self):
        for name in ("Jade", "Juan"):
            with self.subTest(artist=name):
                db, create, _response = await self._checkout(
                    _artist(name=name, rate=100, account=f"acct_{name.lower()}", ready=True)
                )
                params, options = create.call_args.args
                self.assertEqual(options["stripe_account"], f"acct_{name.lower()}")
                self.assertEqual(params["payment_intent_data"]["application_fee_amount"], 400)
                self.assertEqual(db.card.artist_cut_cents, 10_000)
                self.assertEqual(db.card.shop_cut_cents, 400)

    async def test_paid_card_stays_active_and_delivery_failure_requests_a_retry(self):
        card_id = uuid.uuid4()
        card = SimpleNamespace(
            id=card_id,
            artist_id=uuid.uuid4(),
            status="pending",
            activated_at=None,
            stripe_payment_intent_id=None,
            delivered_at=None,
            recipient_email="recipient@example.com",
            recipient_name="Recipient",
            send_to_recipient=True,
            purchaser_name="Buyer",
            purchaser_email="buyer@example.com",
            message=None,
            code="BT-TEST-TEST-TEST",
            original_amount_cents=10_000,
        )
        artist = SimpleNamespace(name="Jade")
        db = _ActivationDb(card, artist)
        session = {"metadata": {"gift_card_id": str(card_id)}, "payment_intent": "pi_paid"}
        sender = AsyncMock(return_value=False)
        with (
            patch("app.core.database.async_session", return_value=db),
            patch("app.api.v1.endpoints.gift_cards.send_email", new=sender),
            self.assertRaisesRegex(HTTPException, "pending retry"),
        ):
            await activate_paid_gift_card(session)
        self.assertEqual(card.status, "active")
        self.assertEqual(card.stripe_payment_intent_id, "pi_paid")
        self.assertIsInstance(card.activated_at, datetime)
        self.assertEqual(card.activated_at.tzinfo, UTC)
        self.assertIsNone(card.delivered_at)
        self.assertEqual(db.commits, 1)
        self.assertEqual(sender.await_args.kwargs["tags"], ["gift_card_receipt"])
        self.assertIn("#d38a68", sender.await_args.kwargs["html_body"])

    async def test_paid_card_can_deliver_only_to_purchaser(self):
        card_id = uuid.uuid4()
        card = SimpleNamespace(
            id=card_id,
            artist_id=uuid.uuid4(),
            status="pending",
            activated_at=None,
            stripe_payment_intent_id=None,
            delivered_at=None,
            recipient_email=None,
            recipient_name="Recipient",
            send_to_recipient=False,
            purchaser_name="Buyer",
            purchaser_email="buyer@example.com",
            message=None,
            code="BT-TEST-TEST-TEST",
            original_amount_cents=10_000,
        )
        artist = SimpleNamespace(name="Jade")
        db = _ActivationDb(card, artist)
        session = {"metadata": {"gift_card_id": str(card_id)}, "payment_intent": "pi_paid"}
        sender = AsyncMock(return_value=True)
        with (
            patch("app.core.database.async_session", return_value=db),
            patch("app.api.v1.endpoints.gift_cards.send_email", new=sender),
        ):
            await activate_paid_gift_card(session)

        self.assertEqual(sender.await_args.kwargs["to"], "buyer@example.com")
        self.assertIn("gift for <strong", sender.await_args.kwargs["html_body"])
        self.assertIn("Recipient", sender.await_args.kwargs["html_body"])
        self.assertIsNotNone(card.delivered_at)


class GiftCardPublicPageContractTests(unittest.IsolatedAsyncioTestCase):
    """The public /gift-cards page reads these: the amount range up front, then the status after Stripe."""

    async def test_terms_match_the_checkout_validation_and_price_adjustment(self):
        terms = await get_gift_card_terms()
        self.assertEqual(terms.currency, "usd")
        self.assertEqual((terms.min_amount_cents, terms.max_amount_cents), (MIN_AMOUNT_CENTS, MAX_AMOUNT_CENTS))
        self.assertEqual(terms.price_adjustment_bps, CUSTOMER_PRICE_INCREASE_BPS)
        common = {
            "artist_id": uuid.uuid4(),
            "purchaser_name": "Buyer",
            "purchaser_email": "buyer@example.com",
            "recipient_name": "Recipient",
            "send_to_recipient": False,
        }
        GiftCardCheckoutRequest(amount_cents=terms.min_amount_cents, **common)
        GiftCardCheckoutRequest(amount_cents=terms.max_amount_cents, **common)
        for amount in (terms.min_amount_cents - 1, terms.max_amount_cents + 1):
            with self.assertRaises(ValidationError):
                GiftCardCheckoutRequest(amount_cents=amount, **common)
        # The page shows face value * (1 + bps); the server charges the same figure.
        self.assertEqual(customer_price_cents(10_000), 10_000 + 10_000 * terms.price_adjustment_bps // 10_000)

    async def test_stripe_returns_buyers_to_the_public_gift_card_page(self):
        artist = _artist(name="Jade", rate=100, account="acct_jade", ready=True)
        db = _Db(artist)
        create = unittest.mock.Mock(
            return_value=SimpleNamespace(id="cs_test_gift", url="https://checkout.stripe.test/g")
        )
        stripe_client = SimpleNamespace(
            v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(create=create)))
        )
        with (
            patch("app.api.v1.endpoints.gift_cards.get_stripe_client", return_value=stripe_client),
            patch(
                "app.api.v1.endpoints.gift_cards.get_settings",
                return_value=SimpleNamespace(FRONTEND_URL="https://shop.example.test", GIFT_CARD_CODE_PREFIX="GC"),
            ),
        ):
            await create_gift_card_checkout(_body(artist.id), db)
        params, _options = create.call_args.args
        self.assertEqual(params["success_url"], "https://shop.example.test/gift-cards?session_id={CHECKOUT_SESSION_ID}")
        self.assertEqual(params["cancel_url"], "https://shop.example.test/gift-cards?cancelled=1")

    async def test_status_masks_the_delivery_email(self):
        card = SimpleNamespace(
            status="active",
            original_amount_cents=5_000,
            send_to_recipient=True,
            recipient_name="Recipient",
            recipient_email="recipient@example.com",
            purchaser_name="Buyer",
            purchaser_email="buyer@example.com",
            delivered_at=datetime.now(UTC),
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: card)))
        status = await get_gift_card_status("cs_test_fake", db)
        self.assertEqual(status.status, "active")
        self.assertEqual(status.face_value_cents, 5_000)
        self.assertEqual(status.recipient_email_hint, "r***@example.com")
        self.assertTrue(status.delivered)

    async def test_status_for_an_unknown_session_is_not_found(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
        with self.assertRaises(HTTPException) as caught:
            await get_gift_card_status("cs_test_missing", db)
        self.assertEqual(caught.exception.status_code, 404)


class GiftCardReceiptTests(unittest.TestCase):
    def test_foundry_receipt_uses_portal_design_and_escapes_customer_content(self):
        receipt = render_gift_card_receipt(
            recipient_name="Justin <Walker>",
            purchaser_name="Buyer & Friend",
            artist_name="Jade",
            amount_cents=10_000,
            code="BT-TEST-TEST-TEST",
            message="For <your> next tattoo",
        )

        self.assertEqual(receipt.slug, "gift_card_receipt")
        self.assertIn("$100.00", receipt.subject)
        self.assertIn("border-radius:24px", receipt.html_body)
        self.assertIn("color:#d38a68", receipt.html_body)
        self.assertIn("Justin &lt;Walker&gt;", receipt.html_body)
        self.assertIn("Buyer &amp; Friend", receipt.html_body)
        self.assertIn("For &lt;your&gt; next tattoo", receipt.html_body)
        self.assertNotIn("For <your>", receipt.html_body)

    def test_foundry_receipt_addresses_purchaser_when_they_keep_delivery(self):
        receipt = render_gift_card_receipt(
            recipient_name="Buyer",
            purchaser_name="Buyer",
            gift_recipient_name="Recipient",
            artist_name="Jade",
            amount_cents=10_000,
            code="BT-TEST-TEST-TEST",
            sent_to_purchaser=True,
        )

        self.assertIn("gift card for Recipient", receipt.subject)
        self.assertIn("Hey Buyer", receipt.html_body)
        self.assertIn("gift for <strong", receipt.html_body)
        self.assertIn("Recipient", receipt.html_body)


def _card(*, status: str, balance: int = 10_000, owed: int = 7_000, artist_id=None, delivered: bool = True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        code="RYO-TEST",
        artist_id=artist_id or uuid.uuid4(),
        status=status,
        original_amount_cents=10_000,
        balance_cents=balance,
        payout_owed_cents=owed,
        routing_destination="artist",
        delivered_at=datetime.now(UTC) if delivered else None,
        created_at=datetime.now(UTC),
    )


class GiftCardVoidTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_checkout_voids_card_and_clears_payout_owed(self):
        card = _card(status="pending")
        db = _ActivationDb(card, None)
        with patch("app.core.database.async_session", return_value=db):
            await void_unpaid_gift_card({"metadata": {"gift_card_id": str(card.id)}})
        self.assertEqual((card.status, card.balance_cents, card.payout_owed_cents), ("voided", 0, 0))
        self.assertEqual(db.commits, 1)

    async def test_refund_voids_card_and_clears_payout_owed(self):
        card = _card(status="active")
        db = _ActivationDb(card, None)
        with patch("app.core.database.async_session", return_value=db):
            await void_refunded_gift_card("pi_test_refund")
        self.assertEqual((card.status, card.balance_cents, card.payout_owed_cents), ("voided", 0, 0))
        self.assertEqual(db.commits, 1)


class _SummaryDb:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        rows = self.rows
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


class GiftCardSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_counts_and_owes_nothing_on_voided_cards(self):
        rows = [
            _card(status="active", balance=6_000, owed=7_000, delivered=False),
            _card(status="redeemed", balance=0, owed=3_500),
            _card(status="voided", balance=0, owed=9_999),
            _card(status="pending", balance=10_000, owed=7_000),
        ]
        owner = SimpleNamespace(id=uuid.uuid4(), role="owner")
        result = await get_gift_cards_summary(None, None, 25, _SummaryDb(rows), owner)

        self.assertEqual(result["total_count"], 4)
        self.assertEqual(
            (result["active_count"], result["redeemed_count"], result["pending_count"], result["voided_count"]),
            (1, 1, 1, 1),
        )
        self.assertEqual(result["active_balance_cents"], 6_000)
        self.assertEqual(result["payout_owed_cents"], 10_500)
        self.assertEqual(result["undelivered_active_count"], 1)
        voided = next(card for card in result["cards"] if card["status"] == "voided")
        self.assertEqual(voided["payout_owed_cents"], 0)

    async def test_artist_summary_is_scoped_to_their_own_cards(self):
        artist = SimpleNamespace(id=uuid.uuid4(), role="artist")
        db = _SummaryDb([])
        await get_gift_cards_summary(uuid.uuid4(), None, 25, db, artist)
        compiled = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn(artist.id.hex, compiled.replace("-", ""))
