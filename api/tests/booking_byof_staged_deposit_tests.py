"""BYOF staged-deposit contract tests with every Stripe boundary faked."""

import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from pydantic import ValidationError

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.api.v1.endpoints import stripe_webhooks
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.user import BTTCOUser
from app.schemas.booking import PublicBookingSubmit


def _settings():
    return SimpleNamespace(
        STRIPE_SECRET_KEY="sk_test_redacted",
        LOCAL_DEV_MODE=True,
        FRONTEND_URL="http://127.0.0.1:5174",
    )


def _artist() -> BTTCOUser:
    return BTTCOUser(
        id=uuid.uuid4(),
        email="artist@example.test",
        password_hash="disabled",
        name="Artist",
        role="artist",
        slug="artist",
        commission_rate=70,
        stripe_onboarding_complete=False,
    )


def _booking(artist_id) -> Booking:
    return Booking(
        id=uuid.uuid4(),
        artist_id=artist_id,
        status="pending",
        description="Bring your own flash reference for review.",
        booking_kind="flash",
        is_bring_your_own_flash=True,
        deposit_amount=75,
        deposit_paid=False,
        reference_code="BKG-BYOF1",
        guest_email="collector@example.test",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class _Db:
    def __init__(self, booking=None):
        self.booking = booking
        self.added = []
        self.commits = 0

    def add(self, row):
        self.added.append(row)

    async def get(self, _model, _key):
        return self.booking

    async def commit(self):
        self.commits += 1


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _WorkerDb(_Db):
    def __init__(self, rows, artist):
        super().__init__()
        self.rows = rows
        self.artist = artist

    async def execute(self, _statement):
        return _Rows(self.rows)

    async def get(self, _model, _key):
        return self.artist


class _SessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class ByofSchemaTests(unittest.TestCase):
    def test_byof_marker_requires_flash_kind(self):
        base = {
            "artist_slug": "artist",
            "first_name": "Client",
            "last_name": "Name",
            "email": "collector@example.test",
            "description": "Bring your own design reference for artist review.",
            "is_bring_your_own_flash": True,
        }
        parsed = PublicBookingSubmit(**base, booking_kind="flash")
        self.assertTrue(parsed.is_bring_your_own_flash)
        with self.assertRaises(ValidationError):
            PublicBookingSubmit(**base, booking_kind="tattoo_session")


class ByofCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkout_is_card_only_manual_capture_without_a_slot_hold(self):
        artist = _artist()
        booking = _booking(artist.id)
        db = _Db(booking)
        create = MagicMock(return_value=SimpleNamespace(id="cs_test_byof", url="https://checkout.test/byof"))
        client = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=SimpleNamespace(create=create))))

        with (
            patch.object(bookings_endpoint, "get_settings", return_value=_settings()),
            patch.object(bookings_endpoint, "get_stripe_client", return_value=client),
        ):
            url = await bookings_endpoint._create_public_booking_checkout(
                db,
                booking=booking,
                artist=artist,
                slot_hold=None,
                client_email="collector@example.test",
                deposit_amount=75,
                save_payment_method=False,
                staged_deposit=True,
            )

        self.assertEqual(url, "https://checkout.test/byof")
        params = create.call_args.args[0]
        self.assertEqual(params["payment_method_types"], ["card"])
        self.assertEqual(params["payment_intent_data"]["capture_method"], "manual")
        self.assertEqual(params["metadata"]["staged_deposit"], "true")
        self.assertEqual(params["metadata"]["slot_hold_token"], "")
        self.assertEqual(booking.deposit_authorization_status, "pending_checkout")
        self.assertFalse(booking.deposit_paid)
        self.assertTrue(any(isinstance(row, BookingLifecycleEvent) for row in db.added))

    async def test_artist_confirmation_captures_and_decline_releases(self):
        artist = _artist()
        booking = _booking(artist.id)
        booking.stripe_payment_intent_id = "pi_test_byof"
        booking.deposit_authorization_status = "authorized"
        capture = MagicMock()
        cancel = MagicMock()
        expire = MagicMock()
        client = SimpleNamespace(
            v1=SimpleNamespace(
                payment_intents=SimpleNamespace(capture=capture, cancel=cancel),
                checkout=SimpleNamespace(sessions=SimpleNamespace(expire=expire)),
            )
        )

        with (
            patch.object(bookings_endpoint, "get_settings", return_value=_settings()),
            patch.object(bookings_endpoint, "get_stripe_client", return_value=client),
        ):
            await bookings_endpoint._capture_byof_authorization(booking, artist)
        self.assertEqual(booking.deposit_authorization_status, "captured")
        self.assertTrue(booking.deposit_paid)
        capture.assert_called_once()

        booking.deposit_authorization_status = "authorized"
        booking.deposit_paid = False
        with (
            patch.object(bookings_endpoint, "get_settings", return_value=_settings()),
            patch.object(bookings_endpoint, "get_stripe_client", return_value=client),
        ):
            await bookings_endpoint._release_byof_authorization(booking, artist)
        self.assertEqual(booking.deposit_authorization_status, "released")
        self.assertFalse(booking.deposit_paid)
        cancel.assert_called_once()

        booking.deposit_authorization_status = "pending_checkout"
        booking.stripe_payment_intent_id = None
        booking.stripe_checkout_session_id = "cs_test_byof"
        with (
            patch.object(bookings_endpoint, "get_settings", return_value=_settings()),
            patch.object(bookings_endpoint, "get_stripe_client", return_value=client),
        ):
            await bookings_endpoint._release_byof_authorization(booking, artist)
        expire.assert_called_once()

    async def test_checkout_completion_records_authorization_without_marking_paid(self):
        artist = _artist()
        booking = _booking(artist.id)
        db = _Db(booking)

        with patch.object(stripe_webhooks, "async_session", return_value=_SessionContext(db)):
            await stripe_webhooks._handle_byof_authorization_ready(
                payment_intent_id="pi_test_byof",
                booking_id=booking.id,
                checkout_session_id="cs_test_byof",
                provider_event_id=None,
                source_event="checkout.session.completed",
            )

        self.assertEqual(booking.deposit_authorization_status, "authorized")
        self.assertEqual(booking.stripe_payment_intent_id, "pi_test_byof")
        self.assertIsNotNone(booking.deposit_review_deadline_at)
        self.assertFalse(booking.deposit_paid)
        self.assertEqual(db.commits, 1)
        self.assertTrue(any(row.event_type == "byof_deposit_authorized" for row in db.added))

    async def test_review_worker_reminds_then_auto_releases(self):
        artist = _artist()
        now = datetime.now(UTC)
        expired = _booking(artist.id)
        expired.deposit_authorization_status = "authorized"
        expired.deposit_review_deadline_at = now - timedelta(minutes=1)
        reminder = _booking(artist.id)
        reminder.reference_code = "BKG-BYOF2"
        reminder.deposit_authorization_status = "authorized"
        reminder.deposit_review_deadline_at = now + timedelta(hours=12)
        db = _WorkerDb([expired, reminder], artist)

        async def release(booking, _artist, *, final_status="released"):
            booking.deposit_authorization_status = final_status
            booking.deposit_review_deadline_at = None

        with (
            patch.object(bookings_endpoint, "async_session", return_value=_SessionContext(db)),
            patch.object(bookings_endpoint, "_release_byof_authorization", new=AsyncMock(side_effect=release)),
            patch.object(bookings_endpoint, "_notify_booking_lifecycle", new=AsyncMock()) as notify,
        ):
            processed = await bookings_endpoint.process_byof_deposit_reviews_once()

        self.assertEqual(processed, 2)
        self.assertEqual(expired.deposit_authorization_status, "expired")
        self.assertIsNotNone(reminder.deposit_review_reminder_sent_at)
        self.assertEqual(db.commits, 1)
        self.assertEqual(notify.await_count, 1)


if __name__ == "__main__":
    unittest.main()
