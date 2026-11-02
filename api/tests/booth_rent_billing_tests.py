"""Focused Stripe booth-rent billing contract tests.

Net-new artifact justification: existing payment tests cover collector checkout
and Connect routing, while booth rent is a separate artist-to-shop subscription.
Keeping this boundary explicit prevents membership and deposit assertions from
masking changes to the rent flow.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api.v1.endpoints.stripe_connect import (
    BoothRentCashPaymentRequest,
    BoothRentConfigRequest,
    configure_artist_booth_rent,
    create_booth_rent_checkout,
    record_artist_booth_rent_cash_payment,
)
from app.api.v1.endpoints.stripe_webhooks import (
    _handle_booth_rent_checkout_event,
    _handle_invoice_event,
)

FUTURE_DUE_DATE = date.today() + timedelta(days=30)


def _artist(**overrides):
    values = {
        "id": "11111111-1111-1111-1111-111111111111",
        "role": "artist",
        "email": "artist@example.test",
        "name": "Artist Example",
        "slug": "artist-example",
        "commission_rate": 70,
        "stripe_account_id": None,
        "stripe_onboarding_complete": False,
        "stripe_customer_id": None,
        "booth_rent_amount_cents": None,
        "booth_rent_interval": None,
        "booth_rent_payment_method": "cash",
        "booth_rent_status": "not_configured",
        "booth_rent_subscription_id": None,
        "booth_rent_checkout_session_id": None,
        "booth_rent_current_period_end": None,
        "booth_rent_next_due_date": None,
        "booth_rent_cancel_at_period_end": False,
        "is_guest_artist": False,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Scalars:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return _Scalars(self.value)


class _Db:
    def __init__(self, value):
        self.values = value if isinstance(value, list) else [value]
        self.index = 0
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.add = MagicMock()

    async def execute(self, _statement):
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return _Result(value)


class _SessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class BoothRentBillingTests(IsolatedAsyncioTestCase):
    async def test_founder_cannot_start_an_artist_booth_rent_checkout(self):
        with self.assertRaises(HTTPException) as raised:
            await create_booth_rent_checkout(db=_Db(_artist(role="founder")), current_user=_artist(role="founder"))

        self.assertEqual(raised.exception.status_code, 403)

    async def test_admin_configuration_defaults_new_booth_artist_to_cash(self):
        artist = _artist()
        db = _Db(artist)
        admin = _artist(role="founder")

        result = await configure_artist_booth_rent(
            artist.id,
            BoothRentConfigRequest(
                enabled=True,
                amount_cents=125_00,
                interval="week",
                next_due_date=FUTURE_DUE_DATE,
            ),
            db=db,
            current_user=admin,
        )

        self.assertEqual(artist.commission_rate, 100)
        self.assertEqual(artist.booth_rent_amount_cents, 125_00)
        self.assertEqual(artist.booth_rent_interval, "biweekly")
        self.assertEqual(artist.booth_rent_status, "scheduled")
        self.assertEqual(artist.booth_rent_next_due_date, FUTURE_DUE_DATE)
        self.assertEqual(artist.booth_rent_payment_method, "cash")
        self.assertTrue(result["booth_rent"]["configured"])
        db.flush.assert_awaited_once()

    async def test_admin_can_configure_monthly_cash_rent_without_subscription(self):
        artist = _artist()
        result = await configure_artist_booth_rent(
            artist.id,
            BoothRentConfigRequest(
                enabled=True,
                amount_cents=1_000_00,
                interval="month",
                payment_method="cash",
                next_due_date=FUTURE_DUE_DATE,
            ),
            db=_Db(artist),
            current_user=_artist(role="founder"),
        )

        self.assertEqual(artist.commission_rate, 100)
        self.assertEqual(artist.booth_rent_payment_method, "cash")
        self.assertEqual(artist.booth_rent_status, "scheduled")
        self.assertEqual(result["booth_rent"]["installment_amount_cents"], 1_000_00)
        self.assertEqual(result["booth_rent"]["next_due_date"], FUTURE_DUE_DATE.isoformat())
        self.assertIsNone(artist.booth_rent_subscription_id)
        self.assertEqual(result["booth_rent"]["payment_method"], "cash")

    async def test_admin_can_preserve_commission_and_mark_guest_artist(self):
        artist = _artist(commission_rate=70)
        result = await configure_artist_booth_rent(
            artist.id,
            BoothRentConfigRequest(enabled=False, commission_rate=70, is_guest_artist=True),
            db=_Db(artist),
            current_user=_artist(role="founder"),
        )

        self.assertEqual(artist.commission_rate, 70)
        self.assertTrue(artist.is_guest_artist)
        self.assertFalse(result["booth_rent"]["configured"])
        self.assertTrue(result["is_guest_artist"])

    async def test_legacy_stripe_configuration_maps_to_autodraft(self):
        artist = _artist()
        await configure_artist_booth_rent(
            artist.id,
            BoothRentConfigRequest(
                enabled=True,
                amount_cents=1_000_00,
                payment_method="stripe",
                next_due_date=FUTURE_DUE_DATE,
            ),
            db=_Db(artist),
            current_user=_artist(role="founder"),
        )

        self.assertEqual(artist.booth_rent_payment_method, "autodraft")
        self.assertEqual(artist.booth_rent_status, "scheduled")

    async def test_jade_biweekly_installment_preserves_monthly_obligation(self):
        artist = _artist()
        result = await configure_artist_booth_rent(
            artist.id,
            BoothRentConfigRequest(
                enabled=True,
                amount_cents=1_300_00,
                interval="biweekly",
                payment_method="cash",
                next_due_date=FUTURE_DUE_DATE,
            ),
            db=_Db(artist),
            current_user=_artist(role="founder"),
        )

        self.assertEqual(artist.booth_rent_amount_cents, 1_300_00)
        self.assertEqual(result["booth_rent"]["installment_amount_cents"], 600_00)
        self.assertEqual(result["booth_rent"]["interval"], "biweekly")
        self.assertEqual(result["booth_rent"]["status"], "scheduled")

    async def test_cash_configured_rent_cannot_open_stripe_checkout(self):
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_000_00,
            booth_rent_interval="month",
            booth_rent_payment_method="cash",
            booth_rent_status="due",
        )
        with (
            patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True),
            self.assertRaises(HTTPException) as raised,
        ):
            await create_booth_rent_checkout(db=_Db(artist), current_user=artist)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("cash", raised.exception.detail.lower())

    @patch("app.api.v1.endpoints.stripe_connect.get_settings")
    @patch("app.api.v1.endpoints.stripe_connect._init_stripe")
    @patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
    async def test_one_time_checkout_uses_payment_mode_without_subscription(self, _configured, init, settings):
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_000_00,
            booth_rent_interval="month",
            booth_rent_payment_method="one_time",
            booth_rent_status="due",
        )
        settings.return_value = SimpleNamespace(FRONTEND_URL="https://ryozan.app", STUDIO_NAME="Ryozan Studio")
        create = init.return_value.v1.checkout.sessions.create
        create.return_value = SimpleNamespace(id="cs_once", url="https://checkout.stripe.test/once")

        result = await create_booth_rent_checkout(db=_Db(artist), current_user=artist)

        params = create.call_args.args[0]
        self.assertEqual(params["mode"], "payment")
        self.assertNotIn("recurring", params["line_items"][0]["price_data"])
        self.assertEqual(params["payment_intent_data"]["metadata"]["booth_rent_payment_method"], "one_time")
        self.assertNotIn("subscription_data", params)
        self.assertEqual(result["payment_method"], "one_time")

    async def test_cash_rent_records_one_contractor_contribution_for_period(self):
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_300_00,
            booth_rent_interval="month",
            booth_rent_payment_method="cash",
            booth_rent_status="due",
        )
        db = _Db([artist, None])

        result = await record_artist_booth_rent_cash_payment(
            artist.id,
            BoothRentCashPaymentRequest(period="2026-08"),
            db=db,
            current_user=_artist(role="founder"),
        )

        entry = db.add.call_args.args[0]
        self.assertFalse(result["already_recorded"])
        self.assertEqual(entry.amount_cents, 1_300_00)
        self.assertEqual(entry.category, "contractor_contribution")
        self.assertEqual(entry.raw["payment_method"], "cash")
        self.assertEqual(artist.booth_rent_status, "paid")
        self.assertEqual(artist.booth_rent_current_period_end.month, 9)

    async def test_biweekly_cash_rent_records_installment_and_advances_fourteen_days(self):
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_300_00,
            booth_rent_interval="biweekly",
            booth_rent_payment_method="cash",
            booth_rent_status="scheduled",
            booth_rent_next_due_date=FUTURE_DUE_DATE,
        )
        db = _Db([artist, None])

        result = await record_artist_booth_rent_cash_payment(
            artist.id,
            BoothRentCashPaymentRequest(period="2026-09-01"),
            db=db,
            current_user=_artist(role="founder"),
        )

        self.assertEqual(result["payment"]["amount_cents"], 600_00)
        self.assertEqual(artist.booth_rent_next_due_date, date(2026, 9, 15))

    async def test_venmo_rent_records_the_same_contribution_as_cash(self):
        """Artists pay booth rent in cash or Venmo. Only cash could be logged.

        Both are contractor contributions and both need logging for tax; the
        endpoint used to 409 on anything but cash, so Venmo rent had nowhere to go.
        """
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_000_00,
            booth_rent_interval="month",
            booth_rent_payment_method="venmo",
            booth_rent_status="due",
        )
        db = _Db([artist, None])

        result = await record_artist_booth_rent_cash_payment(
            artist.id,
            BoothRentCashPaymentRequest(period="2026-09"),
            db=db,
            current_user=_artist(role="founder"),
        )

        entry = db.add.call_args.args[0]
        self.assertFalse(result["already_recorded"])
        self.assertEqual(entry.amount_cents, 1_000_00)
        self.assertEqual(entry.category, "contractor_contribution")
        self.assertEqual(entry.payroll_tax_bucket, "income", "it has to count as income for tax")
        self.assertEqual(entry.raw["payment_method"], "venmo", "the method actually used, not 'cash'")
        self.assertEqual(result["payment"]["payment_method"], "venmo")
        self.assertEqual(artist.booth_rent_status, "paid")

    async def test_the_receipt_is_keyed_on_the_period_not_the_method(self):
        """A period is paid once, whichever way the money arrived."""
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_000_00,
            booth_rent_interval="month",
            booth_rent_payment_method="venmo",
            booth_rent_status="due",
        )
        db = _Db([artist, None])
        await record_artist_booth_rent_cash_payment(
            artist.id,
            BoothRentCashPaymentRequest(period="2026-09"),
            db=db,
            current_user=_artist(role="founder"),
        )
        entry = db.add.call_args.args[0]
        self.assertEqual(entry.external_id, f"booth-rent:{artist.id}:2026-09")
        self.assertNotIn("cash", entry.external_id, "a Venmo receipt must not be keyed as cash")

    async def test_a_stripe_configured_artist_still_cannot_be_hand_recorded(self):
        """Autodrafted rent is reported by Stripe; recording it by hand would double it."""
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_000_00,
            booth_rent_interval="month",
            booth_rent_payment_method="autodraft",
            booth_rent_status="scheduled",
        )
        db = _Db([artist, None])

        with self.assertRaises(HTTPException) as ctx:
            await record_artist_booth_rent_cash_payment(
                artist.id,
                BoothRentCashPaymentRequest(period="2026-09"),
                db=db,
                current_user=_artist(role="founder"),
            )
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_venmo_is_a_configurable_booth_rent_method(self):
        artist = _artist()
        result = await configure_artist_booth_rent(
            artist.id,
            BoothRentConfigRequest(
                enabled=True,
                amount_cents=1_300_00,
                interval="month",
                payment_method="venmo",
                next_due_date=FUTURE_DUE_DATE,
            ),
            db=_Db(artist),
            current_user=_artist(role="founder"),
        )
        self.assertEqual(artist.booth_rent_payment_method, "venmo")
        self.assertTrue(result["booth_rent"]["configured"])

    async def test_active_subscription_terms_cannot_drift_from_stripe(self):
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=500_00,
            booth_rent_interval="month",
            booth_rent_status="active",
            booth_rent_subscription_id="sub_live",
        )
        db = _Db(artist)

        with self.assertRaises(HTTPException) as raised:
            await configure_artist_booth_rent(
                artist.id,
                BoothRentConfigRequest(enabled=True, amount_cents=550_00, interval="month"),
                db=db,
                current_user=_artist(role="founder"),
            )

        self.assertEqual(raised.exception.status_code, 409)

    @patch("app.api.v1.endpoints.stripe_connect.get_settings")
    @patch("app.api.v1.endpoints.stripe_connect._init_stripe")
    @patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
    async def test_checkout_uses_stripe_billing_without_connect_transfer(self, _configured, init, settings):
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=500_00,
            booth_rent_interval="month",
            booth_rent_payment_method="autodraft",
            booth_rent_status="pending",
        )
        db = _Db(artist)
        settings.return_value = SimpleNamespace(FRONTEND_URL="https://ryozan.app", STUDIO_NAME="Ryozan Studio")
        create = init.return_value.v1.checkout.sessions.create
        create.return_value = SimpleNamespace(id="cs_rent", url="https://checkout.stripe.test/rent")

        result = await create_booth_rent_checkout(db=db, current_user=artist)

        self.assertEqual(result["session_id"], "cs_rent")
        params = create.call_args.args[0]
        self.assertEqual(params["mode"], "subscription")
        self.assertEqual(params["line_items"][0]["price_data"]["unit_amount"], 500_00)
        self.assertEqual(params["subscription_data"]["metadata"]["bttco_billing_kind"], "booth_rent")
        self.assertNotIn("payment_intent_data", params)
        self.assertNotIn("transfer_data", params)
        self.assertEqual(artist.booth_rent_status, "checkout_pending")

    @patch("app.api.v1.endpoints.stripe_connect.get_settings")
    @patch("app.api.v1.endpoints.stripe_connect._init_stripe")
    @patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
    async def test_biweekly_autodraft_uses_two_week_price_and_future_anchor(self, _configured, init, settings):
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_300_00,
            booth_rent_interval="biweekly",
            booth_rent_payment_method="autodraft",
            booth_rent_status="scheduled",
            booth_rent_next_due_date=FUTURE_DUE_DATE,
        )
        settings.return_value = SimpleNamespace(FRONTEND_URL="https://ryozan.app", STUDIO_NAME="Ryozan Studio")
        create = init.return_value.v1.checkout.sessions.create
        create.return_value = SimpleNamespace(id="cs_biweekly", url="https://checkout.stripe.test/biweekly")

        await create_booth_rent_checkout(db=_Db(artist), current_user=artist)

        params = create.call_args.args[0]
        price = params["line_items"][0]["price_data"]
        self.assertEqual(price["unit_amount"], 600_00)
        self.assertEqual(price["recurring"], {"interval": "week", "interval_count": 2})
        self.assertEqual(params["subscription_data"]["proration_behavior"], "none")
        self.assertEqual(params["metadata"]["booth_rent_due_date"], FUTURE_DUE_DATE.isoformat())

    @patch("app.api.v1.endpoints.stripe_webhooks.upsert_stripe_ledger_entry", new_callable=AsyncMock)
    @patch("app.api.v1.endpoints.stripe_webhooks.async_session")
    async def test_one_time_checkout_records_contribution_and_paid_period(self, session_factory, upsert):
        artist = _artist(
            commission_rate=100,
            booth_rent_amount_cents=1_300_00,
            booth_rent_interval="month",
            booth_rent_payment_method="one_time",
            booth_rent_status="checkout_pending",
        )
        db = _Db(artist)
        session_factory.return_value = _SessionContext(db)

        await _handle_booth_rent_checkout_event(
            "checkout.session.completed",
            {
                "id": "cs_once",
                "payment_intent": "pi_once",
                "payment_status": "paid",
                "amount_total": 1_300_00,
                "currency": "usd",
                "metadata": {
                    "bttco_billing_kind": "booth_rent",
                    "bttco_user_id": artist.id,
                    "booth_rent_payment_method": "one_time",
                    "booth_rent_period": "2026-08",
                    "booth_rent_period_end": "1788235200",
                },
            },
        )

        self.assertEqual(artist.booth_rent_status, "paid")
        self.assertIsNotNone(artist.booth_rent_current_period_end)
        self.assertIsNone(artist.booth_rent_subscription_id)
        self.assertEqual(upsert.await_args.kwargs["category"], "contractor_contribution")
        self.assertEqual(upsert.await_args.kwargs["raw"]["payment_method"], "one_time")

    @patch("app.api.v1.endpoints.stripe_webhooks.upsert_stripe_ledger_entry", new_callable=AsyncMock)
    @patch("app.api.v1.endpoints.stripe_webhooks.async_session")
    async def test_booth_rent_invoice_updates_rent_without_mutating_membership(self, session_factory, upsert):
        artist = _artist(
            membership_status="active",
            commission_rate=100,
            stripe_customer_id="cus_rent",
            booth_rent_subscription_id="sub_rent",
            booth_rent_status="active",
        )
        db = _Db(artist)
        session_factory.return_value = _SessionContext(db)
        invoice = {
            "id": "in_failed",
            "customer": "cus_rent",
            "currency": "usd",
            "amount_due": 500_00,
            "amount_paid": 0,
            "created": 1_700_000_000,
            "billing_reason": "subscription_cycle",
            "parent": {
                "subscription_details": {
                    "subscription": "sub_rent",
                    "metadata": {
                        "bttco_billing_kind": "booth_rent",
                        "bttco_user_id": artist.id,
                    },
                }
            },
            "lines": {"data": [{"period": {"end": 1_800_000_000}}]},
        }

        await _handle_invoice_event("invoice.payment_failed", invoice)

        self.assertEqual(artist.booth_rent_status, "past_due")
        self.assertEqual(artist.membership_status, "active")
        self.assertIsNotNone(artist.booth_rent_current_period_end)
        self.assertEqual(upsert.await_args.kwargs["category"], "contractor_contribution")
        self.assertEqual(upsert.await_args.kwargs["raw"]["contribution_type"], "booth_rent")
        self.assertEqual(upsert.await_args.kwargs["status"], "failed")

    @patch("app.api.v1.endpoints.stripe_webhooks.async_session")
    async def test_completed_checkout_links_platform_customer_and_subscription(self, session_factory):
        artist = _artist(commission_rate=100, booth_rent_status="checkout_pending")
        db = _Db(artist)
        session_factory.return_value = _SessionContext(db)

        await _handle_booth_rent_checkout_event(
            "checkout.session.completed",
            {
                "id": "cs_rent",
                "customer": "cus_rent",
                "subscription": "sub_rent",
                "payment_status": "paid",
                "metadata": {
                    "bttco_billing_kind": "booth_rent",
                    "bttco_user_id": artist.id,
                    "booth_rent_payment_method": "autodraft",
                },
            },
        )

        self.assertEqual(artist.stripe_customer_id, "cus_rent")
        self.assertEqual(artist.booth_rent_subscription_id, "sub_rent")
        self.assertEqual(artist.booth_rent_status, "active")
