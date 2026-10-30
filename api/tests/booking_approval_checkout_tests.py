"""Re-approval closes the Stripe Checkout an older quote opened (PR 536 review)."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.api.v1.endpoints import bookings as bookings_endpoint


def _quote(session_id: str | None = "cs_test_old"):
    return SimpleNamespace(id=uuid4(), stripe_checkout_session_id=session_id, deposit_amount=100)


def _artist():
    return SimpleNamespace(stripe_account_id="acct_fixture", stripe_onboarding_complete=True)


def _client(status: str):
    client = MagicMock()
    client.v1.checkout.sessions.retrieve.return_value = SimpleNamespace(status=status)
    return client


class CloseQuoteCheckoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routing = MagicMock()
        routing.request_options.return_value = {}
        self._patches = [
            patch.object(bookings_endpoint, "resolve_charge_routing", return_value=routing),
            patch.object(
                bookings_endpoint,
                "get_settings",
                return_value=SimpleNamespace(LOCAL_DEV_MODE=False, STRIPE_SECRET_KEY="sk_test_x"),
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    async def test_open_session_is_expired(self):
        client = _client("open")
        with patch.object(bookings_endpoint, "get_stripe_client", return_value=client):
            await bookings_endpoint._close_quote_checkout(_quote(), _artist())
        client.v1.checkout.sessions.expire.assert_called_once()
        self.assertEqual(client.v1.checkout.sessions.expire.call_args.args[0], "cs_test_old")

    async def test_completed_session_blocks_approval(self):
        client = _client("complete")
        with (
            patch.object(bookings_endpoint, "get_stripe_client", return_value=client),
            self.assertRaises(HTTPException) as ctx,
        ):
            await bookings_endpoint._close_quote_checkout(_quote(), _artist())
        self.assertEqual(ctx.exception.status_code, 409)
        client.v1.checkout.sessions.expire.assert_not_called()

    async def test_already_expired_session_is_left_alone(self):
        client = _client("expired")
        with patch.object(bookings_endpoint, "get_stripe_client", return_value=client):
            await bookings_endpoint._close_quote_checkout(_quote(), _artist())
        client.v1.checkout.sessions.expire.assert_not_called()

    async def test_quote_without_checkout_skips_stripe(self):
        with patch.object(bookings_endpoint, "get_stripe_client") as factory:
            await bookings_endpoint._close_quote_checkout(_quote(None), _artist())
        factory.assert_not_called()

    async def test_stripe_error_stops_approval(self):
        client = MagicMock()
        client.v1.checkout.sessions.retrieve.side_effect = RuntimeError("stripe down")
        with (
            patch.object(bookings_endpoint, "get_stripe_client", return_value=client),
            self.assertRaises(HTTPException) as ctx,
        ):
            await bookings_endpoint._close_quote_checkout(_quote(), _artist())
        self.assertEqual(ctx.exception.status_code, 502)


class ApprovedQuoteSlotTests(unittest.IsolatedAsyncioTestCase):
    async def test_override_slot_is_taken_as_approved(self):
        from app.api.v1.endpoints import quotes

        booking = SimpleNamespace(id=uuid4(), duration_minutes=120)
        artist = SimpleNamespace(id=uuid4())
        selected = {
            "date": "2026-11-04",
            "time": "21:00",
            "duration_minutes": 90,
            "staff_approved": True,
            "staff_override": True,
        }
        with patch.object(quotes, "_slot_is_available") as available:
            result = await quotes._assert_quote_slot_available(
                db=None, booking=booking, artist=artist, selected=selected
            )
        available.assert_not_called()
        self.assertEqual(result[2], 90)

    async def test_approved_slot_is_checked_against_the_artists_own_hours(self):
        from app.api.v1.endpoints import quotes

        booking = SimpleNamespace(id=uuid4(), duration_minutes=120)
        artist = SimpleNamespace(id=uuid4())
        selected = {"date": "2026-11-04", "time": "13:00", "staff_approved": True}
        schedule = {0: (9, 22)}
        with (
            patch.object(quotes, "_artist_booking_rules", return_value=(None, 0, 0, 365, True)),
            patch.object(quotes, "artist_calendar_connected", return_value=False),
            patch.object(quotes, "_resolve_artist_work_schedule", return_value=schedule),
            patch.object(quotes, "_slot_is_available", new=AsyncMock(return_value=True)) as available,
        ):
            await quotes._assert_quote_slot_available(db=None, booking=booking, artist=artist, selected=selected)
        self.assertIs(available.await_args.kwargs["work_schedule"], schedule)


if __name__ == "__main__":
    unittest.main()
