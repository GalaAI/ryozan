"""Stripe return URLs land on the public confirm page, which reads the booking by session id.

Every Stripe boundary is faked; no network and no database.
"""

import ast
import os
import unittest
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.constants.status import BookingStatus
from app.models.booking import Booking
from app.models.booking_slot_hold import BookingSlotHold
from app.models.user import User
from app.services.payments import public_checkout_return_url

FRONTEND = "https://shop.example.test"


def _settings():
    return SimpleNamespace(
        STRIPE_SECRET_KEY="sk_test_redacted",
        LOCAL_DEV_MODE=True,
        FRONTEND_URL=FRONTEND,
        STUDIO_NAME="Example Studio",
    )


def _artist() -> User:
    return User(
        id=uuid.uuid4(),
        email="test-artist@example.test",
        password_hash="disabled",
        name="Test Artist",
        role="artist",
        slug="test-artist",
        commission_rate=70,
        stripe_onboarding_complete=False,
    )


def _booking(artist_id, **fields) -> Booking:
    values = {
        "id": uuid.uuid4(),
        "artist_id": artist_id,
        "status": BookingStatus.PENDING,
        "description": "Synthetic booking for return URL tests.",
        "booking_kind": "tattoo_session",
        "deposit_amount": 50,
        "deposit_paid": False,
        "reference_code": "BKG-TEST1",
        "guest_email": "collector@example.test",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    values.update(fields)
    return Booking(**values)


class _Db:
    def __init__(self, booking=None, artist=None):
        self.booking = booking
        self.artist = artist
        self.added = []
        self.commits = 0

    def add(self, row):
        self.added.append(row)

    async def get(self, _model, _key):
        return self.artist if _model is User else self.booking

    async def commit(self):
        self.commits += 1

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.booking)


class ReturnUrlHelperTests(unittest.TestCase):
    def test_success_url_is_the_public_confirm_page_with_the_session_placeholder(self):
        self.assertEqual(
            public_checkout_return_url(FRONTEND, "success", "BKG-TEST1"),
            f"{FRONTEND}/book/confirm?checkout=success&ref=BKG-TEST1&session_id={{CHECKOUT_SESSION_ID}}",
        )

    def test_cancel_url_has_no_session_placeholder(self):
        # Stripe substitutes {CHECKOUT_SESSION_ID} on the success redirect only.
        url = public_checkout_return_url(f"{FRONTEND}/", "cancelled", "BKG-TEST1")
        self.assertEqual(url, f"{FRONTEND}/book/confirm?checkout=cancelled&ref=BKG-TEST1")

    def test_reference_is_encoded_and_unknown_outcomes_are_refused(self):
        self.assertIn("ref=A%26B", public_checkout_return_url(FRONTEND, "cancelled", "A&B"))
        with self.assertRaises(ValueError):
            public_checkout_return_url(FRONTEND, "paid", "BKG-TEST1")


class PublicBookingCheckoutUrlTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_booking_checkout_returns_to_the_public_confirm_page(self):
        artist = _artist()
        booking = _booking(artist.id)
        hold = BookingSlotHold(
            hold_token="hold-test",
            artist_id=artist.id,
            scheduled_date=date(2030, 1, 2),
            scheduled_time="13:00",
        )
        create = MagicMock(return_value=SimpleNamespace(id="cs_test_public", url="https://checkout.test/public"))
        client = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(create=create))))

        with (
            patch.object(bookings_endpoint, "get_settings", return_value=_settings()),
            patch.object(bookings_endpoint, "get_stripe_client", return_value=client),
        ):
            url = await bookings_endpoint._create_public_booking_checkout(
                _Db(booking),
                booking=booking,
                artist=artist,
                slot_hold=hold,
                client_email="collector@example.test",
                deposit_amount=50,
                save_payment_method=False,
            )

        self.assertEqual(url, "https://checkout.test/public")
        params = create.call_args.args[0]
        self.assertEqual(
            params["success_url"],
            f"{FRONTEND}/book/confirm?checkout=success&ref=BKG-TEST1&session_id={{CHECKOUT_SESSION_ID}}",
        )
        self.assertEqual(params["cancel_url"], f"{FRONTEND}/book/confirm?checkout=cancelled&ref=BKG-TEST1")
        self.assertEqual(booking.stripe_checkout_session_id, "cs_test_public")


class FlashCheckoutUrlTests(unittest.TestCase):
    def test_flash_checkout_builds_both_urls_with_the_public_confirm_helper(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "flash.py").read_text()
        tree = ast.parse(source)
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "book_flash_design"
        )
        outcomes = [
            call.args[1].value
            for call in ast.walk(fn)
            if isinstance(call, ast.Call)
            and getattr(call.func, "id", None) == "public_checkout_return_url"
            and isinstance(call.args[1], ast.Constant)
        ]
        self.assertEqual(sorted(outcomes), ["cancelled", "success"])
        # The body only: the route decorator itself is "/book" (the flash API path, not a return URL).
        body = ast.Module(body=fn.body, type_ignores=[])
        literals = [n.value for n in ast.walk(body) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        literals += [
            part.value
            for n in ast.walk(body)
            if isinstance(n, ast.JoinedStr)
            for part in n.values
            if isinstance(part, ast.Constant)
        ]
        self.assertFalse([s for s in literals if "/book" in s], "flash must not hand-build a /book URL")


class PublicCheckoutStatusTests(unittest.IsolatedAsyncioTestCase):
    async def _status(self, booking, artist):
        handler = bookings_endpoint.get_public_checkout_status
        handler = getattr(handler, "__wrapped__", handler)
        return await handler(request=SimpleNamespace(), session_id="cs_test_status", db=_Db(booking, artist))

    async def test_status_reports_reference_artist_and_deposit_without_contact_details(self):
        artist = _artist()
        booking = _booking(
            artist.id,
            stripe_checkout_session_id="cs_test_status",
            deposit_paid=True,
            scheduled_date=date(2030, 1, 2),
            scheduled_time="13:00",
            status=BookingStatus.CONFIRMED,
        )
        result = await self._status(booking, artist)
        self.assertEqual(result.reference_code, "BKG-TEST1")
        self.assertEqual(result.artist_name, "Test Artist")
        self.assertEqual(result.booking_status, "confirmed")
        self.assertEqual(result.deposit_state, "paid")
        self.assertEqual(result.scheduled_time, "13:00")
        dumped = result.model_dump()
        for private in ("guest_email", "guest_name", "guest_phone", "email", "phone"):
            self.assertNotIn(private, dumped)

    async def test_status_distinguishes_held_and_unreported_deposits(self):
        artist = _artist()
        held = _booking(artist.id, deposit_authorization_status="authorized")
        self.assertEqual((await self._status(held, artist)).deposit_state, "authorized")
        waiting = _booking(artist.id)
        self.assertEqual((await self._status(waiting, artist)).deposit_state, "pending")
        free = _booking(artist.id, deposit_amount=None)
        self.assertEqual((await self._status(free, artist)).deposit_state, "none")

    async def test_unknown_session_is_not_found(self):
        with self.assertRaises(HTTPException) as caught:
            await self._status(None, None)
        self.assertEqual(caught.exception.status_code, 404)

    def test_route_is_public_and_only_accepts_checkout_session_ids(self):
        route = next(
            r
            for r in bookings_endpoint.router.routes
            if isinstance(r, APIRoute) and r.path == "/bookings/checkout-status/{session_id}"
        )
        self.assertEqual(route.methods, {"GET"})
        dependency_calls = {getattr(dep.call, "__name__", "") for dep in route.dependant.dependencies}
        self.assertNotIn("get_current_user", dependency_calls)
        import re

        pattern = re.compile(bookings_endpoint.CHECKOUT_SESSION_ID_PATTERN)
        self.assertIsNotNone(pattern.match("cs_test_a1B2c3D4e5"))
        self.assertIsNone(pattern.match("../../etc"))
        self.assertIsNone(pattern.match("pi_test_12345678"))


if __name__ == "__main__":
    unittest.main()
