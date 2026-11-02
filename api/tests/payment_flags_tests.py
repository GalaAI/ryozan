"""
Payment flag regression tests.

Covers the audited payment bugs on this branch:
  * Flash deposits must confirm the booking when Stripe reports
    checkout.session.completed, and webhook retries must be idempotent.
  * Quote paths must route the revenue split through
    resolve_charge_routing(), so a half-onboarded artist (account id
    present, charges disabled) falls back to the shop account instead of
    breaking Checkout Session creation.
"""

import json
import os
import unittest
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints.flash import _resolve_flash_charge
from app.api.v1.endpoints.pos import record_pos_transaction
from app.api.v1.endpoints.quotes import accept_quote
from app.api.v1.endpoints.stripe_webhooks import (
    _handle_checkout_completed,
    _handle_checkout_event,
    _handle_flash_checkout_completed,
    _release_flash_design_for_unpaid_checkout,
)
from app.constants.status import BookingStatus
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.flash import FlashDesign
from app.models.quote import Quote
from app.models.user import User
from app.schemas.pos import POSTransactionCreate
from app.schemas.quote import QuoteAccept
from app.services.artifact_handlers.quote_proposal import INITIAL_STATE, _apply_confirm


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalar_one(self):
        if not self.rows:
            raise AssertionError("expected one row")
        return self.rows[0]

    def first(self):
        return self.rows[0] if self.rows else None


class _WebhookSession:
    """Scripted async-context session for stripe_webhooks handlers."""

    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)
        self.commits = 0
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _stmt):
        if not self.rows_by_call:
            raise AssertionError("unexpected execute call")
        return _ScalarRows(self.rows_by_call.pop(0))

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


class _EndpointSession:
    """Scripted session for endpoint/handler functions using flush()."""

    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)
        self.added = []
        self.flushed = False

    async def execute(self, _stmt):
        if not self.rows_by_call:
            raise AssertionError("unexpected execute call")
        return _ScalarRows(self.rows_by_call.pop(0))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed = True

    async def refresh(self, row):
        if row.id is None:
            row.id = uuid.uuid4()
        if row.created_at is None:
            row.created_at = datetime.now(UTC)


_FAKE_SETTINGS = type(
    "FakeSettings",
    (),
    {
        "STRIPE_SECRET_KEY": "sk_test_redacted",
        "FRONTEND_URL": "https://ryozan.app",
        "SHOP_EMAIL": "shop@example.test",
        "LOCAL_DEV_MODE": False,
        "STUDIO_NAME": "Ryozan Studio",
    },
)()


def _artist(**overrides) -> User:
    fields = {
        "id": uuid.uuid4(),
        "email": "artist@example.test",
        "password_hash": "disabled",
        "name": "Artist",
        "role": "artist",
        "slug": "artist",
        "commission_rate": 70,
    }
    fields.update(overrides)
    return User(**fields)


class FlashCheckoutWebhookTests(unittest.IsolatedAsyncioTestCase):
    def _session_payload(self, booking_id, design_id, session_id="cs_flash_1"):
        return {
            "id": session_id,
            "payment_intent": "pi_flash_1",
            "amount_total": 10000,
            "metadata": {
                "flash_booking": "true",
                "checkout_type": "flash_deposit",
                "booking_id": str(booking_id),
                "design_id": str(design_id),
                "bttcoin_reward": "25",
                "is_deposit": "true",
            },
        }

    async def test_flash_checkout_completion_confirms_booking(self):
        design_id = uuid.uuid4()
        artist = _artist(google_refresh_token="google-refresh-token")
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist.id,
            client_id=None,
            guest_name="Client Name",
            guest_email="client@example.test",
            reference_code="FLX-TEST01",
            status=BookingStatus.PENDING,
            deposit_paid=False,
            deposit_amount=100,
            description="Flash: Panther",
            scheduled_date=date(2026, 8, 14),
            scheduled_time="11:00",
            duration_minutes=90,
        )
        design = FlashDesign(
            id=design_id,
            sheet_id=uuid.uuid4(),
            artist_id=artist.id,
            image_url="https://assets.example/panther.webp",
            name="Panther",
            status="claimed",
            claimed_by=None,
            booking_id=booking.id,
        )
        fake_db = _WebhookSession([[booking], [design], [artist]])

        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=fake_db),
            patch("app.api.v1.endpoints.stripe_webhooks.artist_calendar_connected", return_value=True),
            patch(
                "app.api.v1.endpoints.stripe_webhooks.create_booking_event",
                new_callable=AsyncMock,
                return_value={"event_id": "evt_flash"},
            ) as cal_mock,
            patch("app.api.v1.endpoints.stripe_webhooks.send_email", new_callable=AsyncMock) as email_mock,
            patch("app.api.v1.endpoints.stripe_webhooks.get_settings", return_value=_FAKE_SETTINGS),
        ):
            await _handle_flash_checkout_completed(self._session_payload(booking.id, design_id))

        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertTrue(booking.deposit_paid)
        self.assertEqual(booking.deposit_amount, 100.0)
        self.assertEqual(booking.stripe_checkout_session_id, "cs_flash_1")
        self.assertEqual(booking.stripe_payment_intent_id, "pi_flash_1")
        self.assertEqual(booking.google_calendar_event_id, "evt_flash")
        self.assertEqual(design.booking_id, booking.id)
        self.assertEqual(design.status, "claimed")
        cal_mock.assert_awaited_once()
        events = [row for row in fake_db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual([event.event_type for event in events], ["checkout_completed"])
        self.assertEqual(events[0].details["design_id"], str(design_id))
        self.assertTrue(events[0].details["is_deposit"])
        # Client confirmation + artist notification + shop inbox
        self.assertEqual(email_mock.await_count, 3)
        self.assertGreaterEqual(fake_db.commits, 2)

    async def test_flash_checkout_completion_is_idempotent_on_retry(self):
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            guest_email="client@example.test",
            reference_code="FLX-TEST02",
            status=BookingStatus.CONFIRMED,
            deposit_paid=True,
            stripe_checkout_session_id="cs_flash_dup",
        )
        fake_db = _WebhookSession([[booking]])

        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=fake_db),
            patch("app.api.v1.endpoints.stripe_webhooks.send_email", new_callable=AsyncMock) as email_mock,
        ):
            await _handle_flash_checkout_completed(
                self._session_payload(booking.id, uuid.uuid4(), session_id="cs_flash_dup")
            )

        events = [row for row in fake_db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual([event.event_type for event in events], ["checkout_duplicate"])
        self.assertEqual(fake_db.commits, 1)
        email_mock.assert_not_awaited()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

    async def test_unpaid_terminal_checkout_releases_flash_inventory(self):
        booking_id = uuid.uuid4()
        design = FlashDesign(
            id=uuid.uuid4(),
            sheet_id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            image_url="https://assets.example/tiger.webp",
            status="claimed",
            claimed_by=uuid.uuid4(),
            claimed_at=datetime.now(UTC),
            booking_id=booking_id,
        )
        fake_db = _WebhookSession([[design]])

        released = await _release_flash_design_for_unpaid_checkout(
            fake_db,
            metadata={"design_id": str(design.id)},
            booking_id=booking_id,
        )

        self.assertIs(released, design)
        self.assertEqual(design.status, "published")
        self.assertIsNone(design.claimed_by)
        self.assertIsNone(design.claimed_at)
        self.assertIsNone(design.booking_id)

    async def test_checkout_completed_dispatches_legacy_flash_metadata(self):
        """In-flight sessions created before this fix only carry flash_booking."""
        with patch(
            "app.api.v1.endpoints.stripe_webhooks._handle_flash_checkout_completed",
            new_callable=AsyncMock,
        ) as flash_mock:
            await _handle_checkout_completed(
                {
                    "id": "cs_flash_legacy",
                    "metadata": {"flash_booking": "true", "booking_id": str(uuid.uuid4())},
                }
            )

        flash_mock.assert_awaited_once()


class FlashDepositConfigurationTests(unittest.TestCase):
    def test_design_can_disable_deposit_without_falling_back_to_artist_default(self):
        decision = _resolve_flash_charge(
            configured_design_deposit=0,
            flash_deposit_amount=100,
            pay_full=False,
            allow_prepay_full=False,
            design_price=500,
        )

        self.assertEqual(decision.deposit_amount, 0)
        self.assertEqual(decision.charge_dollars, 0)
        self.assertFalse(decision.checkout_required)
        self.assertFalse(decision.is_deposit)

    def test_design_deposit_overrides_artist_default(self):
        decision = _resolve_flash_charge(
            configured_design_deposit=75,
            flash_deposit_amount=100,
            pay_full=False,
            allow_prepay_full=False,
            design_price=500,
        )

        self.assertEqual(decision.deposit_amount, 75)
        self.assertEqual(decision.charge_dollars, 75)
        self.assertTrue(decision.checkout_required)
        self.assertTrue(decision.is_deposit)

    def test_full_prepay_remains_available_when_deposit_is_disabled(self):
        decision = _resolve_flash_charge(
            configured_design_deposit=0,
            flash_deposit_amount=100,
            pay_full=True,
            allow_prepay_full=True,
            design_price=500,
        )

        self.assertEqual(decision.deposit_amount, 0)
        self.assertEqual(decision.charge_dollars, 500)
        self.assertTrue(decision.checkout_required)
        self.assertFalse(decision.is_deposit)


class CheckoutPaymentStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_unpaid_checkout_is_not_fulfilled_or_recorded(self):
        session = {
            "id": "cs_unpaid",
            "payment_status": "unpaid",
            "amount_total": 10000,
            "metadata": {"checkout_type": "public_booking_deposit"},
        }
        with (
            patch(
                "app.api.v1.endpoints.stripe_webhooks._record_checkout_payment",
                new_callable=AsyncMock,
            ) as ledger_mock,
            patch(
                "app.api.v1.endpoints.stripe_webhooks._handle_checkout_completed",
                new_callable=AsyncMock,
            ) as completed_mock,
        ):
            await _handle_checkout_event("checkout.session.completed", session, provider_event_id="evt_unpaid")

        ledger_mock.assert_not_awaited()
        completed_mock.assert_not_awaited()

    async def test_paid_checkout_is_recorded_before_fulfillment(self):
        session = {
            "id": "cs_paid",
            "payment_intent": "pi_paid",
            "payment_status": "paid",
            "amount_total": 10000,
            "metadata": {"checkout_type": "public_booking_deposit"},
        }
        with (
            patch(
                "app.api.v1.endpoints.stripe_webhooks._record_checkout_payment",
                new_callable=AsyncMock,
            ) as ledger_mock,
            patch(
                "app.api.v1.endpoints.stripe_webhooks._handle_checkout_completed",
                new_callable=AsyncMock,
            ) as completed_mock,
        ):
            await _handle_checkout_event("checkout.session.completed", session, provider_event_id="evt_paid")

        ledger_mock.assert_awaited_once_with(session)
        completed_mock.assert_awaited_once_with(session, provider_event_id="evt_paid")


class QuoteRoutingTests(unittest.IsolatedAsyncioTestCase):
    def _quote_fixtures(self, artist: User):
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist.id,
            client_id=None,
            guest_email="client@example.test",
            reference_code="BKG-QUOTE1",
            status=BookingStatus.QUOTED,
        )
        quote = Quote(
            id=uuid.uuid4(),
            booking_id=booking.id,
            token="quote-token-1",
            deposit_amount=150,
            status="viewed",
            expires_at=None,
            available_slots_json=json.dumps([{"date": "2026-08-14", "time": "11:00", "duration_minutes": 120}]),
        )
        return booking, quote

    async def _accept(self, artist: User):
        booking, quote = self._quote_fixtures(artist)
        db = _EndpointSession([[quote], [booking], [artist]])
        created = {}
        request_options = {}

        def fake_create(params, options=None):
            created.update(params)
            request_options.update(options or {})
            return type("FakeStripeSession", (), {"id": "cs_test_quote", "url": "https://checkout.stripe.test/quote"})()

        stripe_client = MagicMock()
        stripe_client.v1.checkout.sessions.create.side_effect = fake_create
        with (
            patch("app.api.v1.endpoints.quotes.get_settings", return_value=_FAKE_SETTINGS),
            patch(
                "app.api.v1.endpoints.quotes._assert_quote_slot_available",
                new_callable=AsyncMock,
                return_value=(date(2026, 8, 14), "11:00", 120),
            ),
            patch("app.api.v1.endpoints.quotes.get_stripe_client", return_value=stripe_client),
        ):
            response = await accept_quote("quote-token-1", QuoteAccept(selected_slot_index=0), db)

        return response, created, request_options, quote, db

    async def test_quote_accept_skips_destination_charge_when_onboarding_incomplete(self):
        artist = _artist(stripe_account_id="acct_half_onboarded", stripe_onboarding_complete=False)

        response, created, request_options, quote, db = await self._accept(artist)

        self.assertEqual(response, {"checkout_url": "https://checkout.stripe.test/quote"})
        payment_intent_data = created["payment_intent_data"]
        self.assertNotIn("transfer_data", payment_intent_data)
        self.assertEqual(payment_intent_data["receipt_email"], "client@example.test")
        self.assertIn("BKG-QUOTE1", payment_intent_data["description"])
        self.assertTrue(request_options["idempotency_key"].startswith("quote-deposit-"))
        self.assertNotIn("stripe_account", request_options)
        self.assertEqual(quote.stripe_checkout_session_id, "cs_test_quote")
        self.assertTrue(db.flushed)

    async def test_quote_accept_routes_destination_charge_when_onboarded(self):
        artist = _artist(stripe_account_id="acct_full_onboarded", stripe_onboarding_complete=True)

        _response, created, request_options, _quote, _db = await self._accept(artist)

        payment_intent_data = created["payment_intent_data"]
        self.assertNotIn("transfer_data", payment_intent_data)
        self.assertEqual(request_options["stripe_account"], "acct_full_onboarded")
        self.assertNotIn("on_behalf_of", payment_intent_data)
        # The customer sees one $156 price. The artist still earns 70% of the
        # configured $150 service price, while Ryozan keeps $45 plus the $6
        # all-in price adjustment.
        self.assertEqual(created["line_items"][0]["price_data"]["unit_amount"], 15600)
        self.assertEqual(payment_intent_data["application_fee_amount"], 5100)

    async def test_quote_proposal_confirm_skips_destination_when_onboarding_incomplete(self):
        artist = _artist(stripe_account_id="acct_half_onboarded", stripe_onboarding_complete=False)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist.id,
            client_id=None,
            guest_email="client@example.test",
            reference_code="BKG-PROP1",
            status=BookingStatus.QUOTED,
        )
        message = type(
            "FakeMessage",
            (),
            {"id": uuid.uuid4(), "created_at": datetime.now(UTC)},
        )()
        db = _EndpointSession([[booking], [artist]])
        created = {}
        request_options = {}

        def fake_create(params, options=None):
            created.update(params)
            request_options.update(options or {})
            return type(
                "FakeStripeSession", (), {"id": "cs_test_proposal", "url": "https://checkout.stripe.test/proposal"}
            )()

        stripe_client = MagicMock()
        stripe_client.v1.checkout.sessions.create.side_effect = fake_create
        with (
            patch("app.services.artifact_handlers.quote_proposal.get_settings", return_value=_FAKE_SETTINGS),
            patch("app.services.artifact_handlers.quote_proposal.get_stripe_client", return_value=stripe_client),
        ):
            state = await _apply_confirm(
                db,
                message,
                None,
                {"booking_id": str(booking.id), "deposit_amount": 100.0},
                dict(INITIAL_STATE),
            )

        self.assertEqual(state["status"], "awaiting_payment")
        self.assertEqual(state["stripe_checkout_session_id"], "cs_test_proposal")
        payment_intent_data = created["payment_intent_data"]
        self.assertNotIn("transfer_data", payment_intent_data)
        self.assertEqual(payment_intent_data["receipt_email"], "client@example.test")
        self.assertIn("BKG-PROP1", payment_intent_data["description"])
        self.assertEqual(request_options["idempotency_key"], f"quote-proposal-deposit-{message.id}")
        self.assertEqual(booking.deposit_amount, 100.0)


class POSPriceAdjustmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_cash_transaction_preserves_commission_base_and_adjustment(self):
        artist = _artist()
        db = _EndpointSession([])

        response = await record_pos_transaction(
            POSTransactionCreate(
                amount_cents=10400,
                commission_base_cents=10000,
                payment_method="cash",
                processor="manual",
            ),
            db,
            artist,
        )

        self.assertEqual(response.amount_cents, 10400)
        self.assertEqual(response.commission_base_cents, 10000)
        self.assertEqual(response.price_adjustment_cents, 400)
        self.assertEqual(len(db.added), 1)


if __name__ == "__main__":
    unittest.main()
