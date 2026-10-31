"""
Huddl booking scheduler tests.

This file is separate from auth, Courier, and routing tests because scheduler
availability is a public booking contract: date/time payload parsing, slot
conflict math, and Google Calendar import dedupe all need to fail together
when the booking surface regresses.
"""

import json
import os
import unittest
import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.api.v1.endpoints import calendar as calendar_endpoint
from app.api.v1.endpoints import stripe_webhooks as stripe_webhooks_endpoint
from app.api.v1.endpoints.bookings import (
    _create_public_booking_checkout,
    _do_submit_public_booking,
    _hustl_deposit_rule_for_duration,
    _on_booking_confirmed,
    _require_booking_management_access,
    _resolve_public_booking_deposit,
    _send_artist_booking_alert,
    _send_booking_request_received_sms,
    get_booking_history,
    get_booking_schedule_suggestions,
    reschedule_booking,
    update_booking,
)
from app.api.v1.endpoints.calendar import (
    _find_existing_calendar_booking,
    _generate_available_slots,
    _generate_blocked_slots,
    _resolve_slot_duration,
    _slot_is_available,
    import_calendar_events,
)
from app.api.v1.endpoints.email_automations import _can_send_booking_sms_reminder
from app.api.v1.endpoints.flash import _assert_flash_slot_available
from app.api.v1.endpoints.quotes import _assert_quote_slot_available
from app.api.v1.endpoints.stripe_webhooks import (
    _consume_slot_hold_for_checkout,
    _expire_slot_hold_for_checkout,
    _handle_checkout_expired,
    _handle_payment_intent_succeeded,
    _handle_payment_intent_terminal_failure,
    _handle_public_booking_checkout_completed,
    _handle_quote_proposal_checkout_completed,
    stripe_webhook,
)
from app.constants.status import BookingStatus
from app.constants.timezones import SHOP_TIMEZONE
from app.core.security import create_booky_capability_token
from app.models.booking import Booking
from app.models.booking_draft import BookingDraft
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.booking_slot_hold import BookingSlotHold
from app.models.session_type import ArtistSessionType
from app.models.user import User
from app.schemas.booking import BookingReschedule, BookingUpdate, PublicBookingSubmit
from app.services import calendar_sync as calendar_sync_service
from app.services.email import build_booking_confirmation_html
from app.services.sms import send_from_template


def _is_time_block_query(stmt) -> bool:
    """True if the SQL statement selects personal time blocks.

    The scheduler's _slot_is_available now also queries ryozan_time_blocks. These
    mocks script results per-query by position; rather than thread an empty result
    into every test, the mocks detect the time-blocks query and return empty for
    it (no blocks configured in these tests), serving queued rows to all others.
    """
    try:
        return "ryozan_time_blocks" in str(stmt)
    except Exception:
        return False


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalar_one(self):
        if not self.rows:
            raise AssertionError("expected one row")
        return self.rows[0]

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []

    async def execute(self, _stmt):
        if _is_time_block_query(_stmt):
            return _ScalarRows([])
        return _ScalarRows(self.rows)

    def add(self, row):
        self.added.append(row)


class _BookingEmailOutboxSession:
    def __init__(self, events):
        self.events = {event.id: event for event in events}
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _stmt):
        return _ScalarRows(list(self.events.values()))

    async def get(self, _model, event_id, **_kwargs):
        return self.events.get(event_id)

    async def commit(self):
        self.commits += 1


class _ScriptedSession:
    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)
        self.calls = 0

    async def execute(self, _stmt):
        if _is_time_block_query(_stmt):
            return _ScalarRows([])
        self.calls += 1
        if not self.rows_by_call:
            raise AssertionError("unexpected execute call")
        return _ScalarRows(self.rows_by_call.pop(0))


class _PatchSession:
    def __init__(self, booking: Booking, artist: User, rows_by_call):
        self.booking = booking
        self.artist = artist
        self.rows_by_call = list(rows_by_call)
        self.flushed = False
        self.added = []

    async def execute(self, _stmt):
        if _is_time_block_query(_stmt):
            return _ScalarRows([])
        if not self.rows_by_call:
            raise AssertionError("unexpected execute call")
        return _ScalarRows(self.rows_by_call.pop(0))

    async def get(self, model, item_id):
        if model is User and item_id == self.artist.id:
            return self.artist
        return None

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed = True


class _RescheduleSession(_PatchSession):
    def __init__(self, booking: Booking, artist: User, rows_by_call):
        super().__init__(booking, artist, rows_by_call)
        self.committed = False
        self.refreshed = False

    async def commit(self):
        self.committed = True

    async def refresh(self, booking):
        if booking is not self.booking:
            raise AssertionError("unexpected refresh target")
        self.refreshed = True

    async def scalar(self, _stmt):
        # Open-quote probe for deposit-pending approvals; these fixtures have none.
        return None


class _WebhookSession:
    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.added = []
        self.pending = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _stmt):
        if _is_time_block_query(_stmt):
            return _ScalarRows([])
        if not self.rows_by_call:
            raise AssertionError("unexpected execute call")
        return _ScalarRows(self.rows_by_call.pop(0))

    async def get(self, _model, _item_id):
        # Webhook unit fixtures do not model a signed-in collector. Production
        # sessions resolve the collector before the reward engine is invoked.
        return None

    def add(self, row):
        self.added.append(row)
        self.pending.append(row)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1
        self.pending.clear()

    async def rollback(self):
        self.rollbacks += 1
        for row in self.pending:
            if row in self.added:
                self.added.remove(row)
        self.pending.clear()


class _ConcurrentWebhookSession(_WebhookSession):
    async def flush(self):
        self.flushes += 1
        raise IntegrityError("INSERT provider_event_id", {}, RuntimeError("unique collision"))


class _WebhookRequest:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None):
        self._payload = payload
        self.headers = headers or {}

    async def body(self):
        return self._payload


class _CalendarImportSession:
    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        if _is_time_block_query(_stmt):
            return _ScalarRows([])
        if not self.rows_by_call:
            raise AssertionError("unexpected execute call")
        return _ScalarRows(self.rows_by_call.pop(0))

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


class _HoldSession:
    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)
        self.added = []
        self.flushed = False

    async def execute(self, _stmt):
        if _is_time_block_query(_stmt):
            return _ScalarRows([])
        if not self.rows_by_call:
            raise AssertionError("unexpected execute call")
        return _ScalarRows(self.rows_by_call.pop(0))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed = True


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        frozen = datetime(2026, 7, 17, 10, 0, tzinfo=calendar_endpoint.SHOP_TIMEZONE)
        return frozen if tz is None else frozen.astimezone(tz)


def _next_weekday(target_weekday: int) -> date:
    today = date.today()
    days = (target_weekday - today.weekday()) % 7
    if days == 0:
        days = 7
    return today + timedelta(days=days)


def _shop_iso(day: date, hhmm: str) -> str:
    """ISO timestamp for a shop wall-clock time on ``day``, with that day's real UTC offset.

    A hardcoded ``-04:00`` is only right during EDT; from November to March the
    shop is on EST (``-05:00``) and such a block lands an hour early, so tests
    built on ``_next_weekday`` failed whenever the next Friday fell outside DST.
    """
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=SHOP_TIMEZONE).isoformat()


def _deposit_booking(payment_intent_id: str | None = None) -> Booking:
    return Booking(
        id=uuid.uuid4(),
        artist_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        status=BookingStatus.QUOTED,
        guest_name="Guest Name",
        guest_email="guest@example.test",
        deposit_amount=200,
        deposit_paid=False,
        stripe_payment_intent_id=payment_intent_id,
    )


def _named_webhook_session(booking: Booking) -> _WebhookSession:
    return _WebhookSession([[booking], [], ["Justin Walker"], [("Collector Name", "collector@example.test")]])


def _stripe_test_settings(*, local_dev: bool, app_env: str, webhook_secret: str = ""):
    return type(
        "Settings",
        (),
        {
            "STRIPE_SECRET_KEY": "sk_test_redacted",
            "STRIPE_WEBHOOK_SECRET": webhook_secret,
            "LOCAL_DEV_MODE": local_dev,
            "DEBUG": False,
            "APP_ENV": app_env,
        },
    )()


class BookingSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def test_booking_management_scope_includes_assistant_and_preserves_artist_self_scope(self):
        artist_id = uuid.uuid4()
        booking = Booking(id=uuid.uuid4(), artist_id=artist_id, description="Scope test")
        users = {
            role: User(
                id=artist_id if role == "artist" else uuid.uuid4(),
                email=f"{role}@example.test",
                password_hash="disabled",
                name=role,
                role=role,
            )
            for role in ("artist", "assistant", "admin", "founder")
        }

        for role, user in users.items():
            with self.subTest(role=role):
                _require_booking_management_access(user, booking)

        for role in ("artist", "collector"):
            outsider = User(
                id=uuid.uuid4(),
                email=f"outside-{role}@example.test",
                password_hash="disabled",
                name=f"Outside {role}",
                role=role,
            )
            with self.subTest(outside_role=role), self.assertRaises(HTTPException) as ctx:
                _require_booking_management_access(outsider, booking)
            self.assertEqual(ctx.exception.status_code, 403)

    async def test_booking_management_endpoints_share_one_authorization_gate(self):
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            client_id=uuid.uuid4(),
            description="Scope test",
            status="pending",
        )
        assistant = User(
            id=uuid.uuid4(),
            email="assistant@example.test",
            password_hash="disabled",
            name="Assistant",
            role="assistant",
        )
        cases = (
            ("detail", bookings_endpoint.get_booking, (booking.id,)),
            ("history", bookings_endpoint.get_booking_history, (booking.id,)),
            ("update", bookings_endpoint.update_booking, (booking.id, object())),
            ("reschedule", bookings_endpoint.reschedule_booking, (booking.id, object())),
            ("cancel", bookings_endpoint.cancel_booking, (booking.id, object())),
            ("conversation", bookings_endpoint.ensure_booking_conversation, (booking.id,)),
        )

        for name, endpoint, args in cases:
            db = _ScriptedSession([[booking]])
            with (
                self.subTest(endpoint=name),
                patch.object(
                    bookings_endpoint,
                    "_require_booking_management_access",
                    side_effect=RuntimeError("scope checked"),
                ) as require_access,
                self.assertRaisesRegex(RuntimeError, "scope checked"),
            ):
                await endpoint(*args, db=db, current_user=assistant)
            require_access.assert_called_once_with(assistant, booking)

    async def test_public_submit_failure_returns_safe_correlation_error_and_logs(self):
        provider_error = "provider secret response must not reach the client"
        with (
            patch.object(
                bookings_endpoint,
                "_do_submit_public_booking",
                new=AsyncMock(side_effect=RuntimeError(provider_error)),
            ),
            patch.object(bookings_endpoint.logger, "exception") as log_exception,
            self.assertRaises(HTTPException) as ctx,
        ):
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/bookings/submit",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                }
            )
            await bookings_endpoint.submit_public_booking(request, object(), object())

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.detail["code"], "booking_submission_failed")
        self.assertTrue(ctx.exception.detail["retryable"])
        self.assertTrue(ctx.exception.detail["correlation_id"].startswith("booking-"))
        self.assertNotIn(provider_error, str(ctx.exception.detail))
        log_exception.assert_called_once()

    def test_session_type_placement_lookup_uses_production_text_array_type(self):
        statement = select(ArtistSessionType).where(ArtistSessionType.placements.contains(["Chest"]))

        compiled = str(statement.compile(dialect=postgresql.dialect()))

        self.assertIn("::TEXT[]", compiled)
        self.assertNotIn("::VARCHAR[]", compiled)

    def test_public_submit_accepts_separate_date_and_time(self):
        body = PublicBookingSubmit(
            artist_slug="justin",
            first_name="Client",
            last_name="Name",
            email="client@example.com",
            description="Black and grey shoulder piece",
            preferred_date="2026-07-18",
            preferred_time="11:30",
            hold_token="hold-token",
        )

        self.assertEqual(body.preferred_date, date(2026, 7, 18))
        self.assertEqual(body.preferred_time, "11:30")
        self.assertEqual(body.hold_token, "hold-token")

    def test_public_submit_accepts_optional_sms_consent(self):
        body = PublicBookingSubmit(
            artist_slug="justin",
            first_name="Client",
            last_name="Name",
            email="client@example.com",
            phone="864-555-0100",
            sms_opt_in=True,
            description="Black and grey shoulder piece",
        )

        self.assertTrue(body.sms_opt_in)

    def test_booking_model_preserves_sms_consent_snapshot(self):
        consented_at = datetime.now(UTC)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            description="Black and grey shoulder piece",
            guest_phone="864-555-0100",
            sms_opt_in=True,
            sms_opt_in_at=consented_at,
            sms_consent_text="Appointment SMS consent text",
        )

        self.assertTrue(booking.sms_opt_in)
        self.assertEqual(booking.sms_opt_in_at, consented_at)
        self.assertEqual(booking.sms_consent_text, "Appointment SMS consent text")

    def test_booking_sms_reminder_requires_booking_consent_and_phone(self):
        consented_at = datetime.now(UTC)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            guest_phone="864-555-0100",
            sms_opt_in=True,
            sms_opt_in_at=consented_at,
        )

        self.assertTrue(_can_send_booking_sms_reminder(booking))

        booking.sms_opt_in = False
        self.assertFalse(_can_send_booking_sms_reminder(booking))
        booking.sms_opt_in = True
        booking.sms_opt_in_at = None
        self.assertFalse(_can_send_booking_sms_reminder(booking))
        booking.sms_opt_in_at = consented_at
        booking.guest_phone = None
        self.assertFalse(_can_send_booking_sms_reminder(booking))

    def test_hustl_deposit_fallback_has_no_181_to_239_minute_gap(self):
        self.assertEqual(_hustl_deposit_rule_for_duration(45), (50.0, "45_minute_minimalist"))
        self.assertEqual(_hustl_deposit_rule_for_duration(180), (100.0, "up_to_3_hours"))
        self.assertEqual(_hustl_deposit_rule_for_duration(181), (200.0, "extended_or_half_day"))
        self.assertEqual(_hustl_deposit_rule_for_duration(239), (200.0, "extended_or_half_day"))
        self.assertEqual(_hustl_deposit_rule_for_duration(480), (300.0, "full_day"))

    def test_public_booking_deposit_prefers_artist_session_type(self):
        artist_id = uuid.uuid4()
        session_type = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist_id,
            label="Custom session",
            size="medium",
            duration_minutes=240,
            deposit_amount=175,
            is_active=True,
            sort_order=0,
        )
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="hold-rule-check",
            artist_id=artist_id,
            artist_slug="justin",
            scheduled_date=_next_weekday(4),
            scheduled_time="11:00",
            duration_minutes=480,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

        self.assertEqual(
            _resolve_public_booking_deposit(
                booking_kind="tattoo_session",
                session_type=session_type,
                slot_hold=hold,
            ),
            (175.0, "session_type"),
        )

    def test_public_booking_respects_session_type_with_deposit_disabled(self):
        artist_id = uuid.uuid4()
        session_type = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist_id,
            label="No deposit session",
            size="medium",
            duration_minutes=180,
            deposit_amount=0,
            is_active=True,
            sort_order=0,
        )

        self.assertEqual(
            _resolve_public_booking_deposit(
                booking_kind="tattoo_session",
                session_type=session_type,
                slot_hold=None,
            ),
            (0.0, "session_type"),
        )

    def test_consultation_never_requires_a_deposit(self):
        body = PublicBookingSubmit(
            artist_slug="justin",
            first_name="Client",
            last_name="Name",
            email="client@example.test",
            description="Consultation via Google Meet about a sleeve.",
            booking_kind="consultation",
            consultation_method="meet",
            preferred_date="2026-07-18",
            preferred_time="11:30",
            hold_token="held-consultation",
        )

        self.assertEqual(body.booking_kind, "consultation")
        self.assertEqual(body.consultation_method, "meet")
        self.assertEqual(
            _resolve_public_booking_deposit(
                booking_kind=body.booking_kind,
                session_type=None,
                slot_hold=None,
            ),
            (None, "consultation_no_deposit"),
        )

    def test_consultation_requires_a_method(self):
        with self.assertRaises(ValidationError):
            PublicBookingSubmit(
                artist_slug="justin",
                first_name="Client",
                last_name="Name",
                email="client@example.test",
                description="Consultation request about a sleeve tattoo.",
                booking_kind="consultation",
            )

    async def test_send_from_template_passes_skip_opt_in_check(self):
        fake_template = type(
            "FakeTemplate",
            (),
            {"is_active": True, "body": "Hi {{name}}", "category": "transactional"},
        )()
        fake_db = _FakeSession([fake_template])

        with patch("app.services.sms.send_sms", new_callable=AsyncMock) as send_mock:
            await send_from_template(
                db=fake_db,
                slug="appointment_reminder_24h",
                to="864-555-0100",
                variables={"name": "Client"},
                skip_opt_in_check=True,
            )

        self.assertTrue(send_mock.await_args.kwargs["skip_opt_in_check"])

    async def test_booking_request_received_sms_uses_captured_form_consent(self):
        client_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        with patch("app.api.v1.endpoints.bookings.send_sms", new_callable=AsyncMock) as send_mock:
            await _send_booking_request_received_sms(
                _FakeSession([]),
                phone="864-555-0100",
                client_name="Client Name",
                artist_name="Justin Walker",
                reference_code="BKG-SMSOK",
                client_id=client_id,
                conversation_id=conversation_id,
            )

        send_mock.assert_awaited_once()
        kwargs = send_mock.await_args.kwargs
        self.assertEqual(kwargs["to"], "864-555-0100")
        self.assertEqual(kwargs["template_slug"], "booking_request_received")
        self.assertEqual(kwargs["user_id"], client_id)
        self.assertEqual(kwargs["conversation_id"], conversation_id)
        self.assertTrue(kwargs["is_transactional"])
        self.assertTrue(kwargs["skip_opt_in_check"])

    async def test_public_submit_is_idempotent_by_submitted_inquiry_id(self):
        artist_id = uuid.uuid4()
        artist = User(
            id=artist_id,
            email="justin@example.test",
            password_hash="disabled",
            name="Justin Walker",
            role="artist",
            slug="justin",
            booking_mode="mailto",
        )
        draft = BookingDraft(
            inquiry_id="inquiry-idempotent",
            status="submitted",
            reference_code="BKG-IDEMP1",
            artist_slug="justin",
        )
        db = _ScriptedSession(
            [
                [artist],
                [draft],
                [],
            ]
        )

        response = await _do_submit_public_booking(
            PublicBookingSubmit(
                inquiry_id="inquiry-idempotent",
                artist_slug="justin",
                first_name="Client",
                last_name="Name",
                email="client@example.test",
                description="Black and grey shoulder piece",
                preferred_date="2026-07-18",
                preferred_time="11:30",
                hold_token="consumed-hold",
            ),
            db,
        )

        self.assertIsNone(response.reference_code)
        self.assertIsNone(response.artist_name)
        self.assertIsNone(response.booking_status)
        self.assertIsNone(response.booky_outcome)
        self.assertTrue(response.idempotent_acknowledgement)
        self.assertEqual(response.message, "This inquiry was already submitted.")
        self.assertEqual(db.calls, 3)

    async def test_public_idempotent_projection_requires_matching_capability(self):
        artist_id = uuid.uuid4()
        collector_id = uuid.uuid4()
        artist = User(
            id=artist_id, email="justin@example.test", password_hash="disabled",
            name="Justin Walker", role="artist", slug="justin", booking_mode="mailto",
        )
        draft = BookingDraft(
            inquiry_id="inquiry-capability", status="submitted",
            reference_code="BKG-CAP001", artist_slug="justin",
        )
        booking = Booking(
            id=uuid.uuid4(), artist_id=artist_id, client_id=collector_id,
            status=BookingStatus.PENDING, description="Existing inquiry",
            booking_kind="tattoo_session", reference_code="BKG-CAP001",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        db = _ScriptedSession([[artist], [draft], [booking], []])
        response = await _do_submit_public_booking(
            PublicBookingSubmit(
                inquiry_id="inquiry-capability", artist_slug="justin",
                first_name="Client", last_name="Name", email="client@example.test",
                description="Black and grey shoulder piece",
                booky_capability=create_booky_capability_token(collector_id, "inquiry-capability"),
            ),
            db,
        )
        self.assertEqual(response.reference_code, "BKG-CAP001")
        self.assertEqual(response.booking_id, booking.id)
        self.assertIsNotNone(response.booky_outcome)
        self.assertFalse(response.idempotent_acknowledgement)

    def test_consultation_calendar_event_includes_invite_and_meet(self):
        event = calendar_sync_service._build_event_body(
            {
                "client_name": "Client Name",
                "client_email": "client@example.test",
                "description": "Sleeve consultation",
                "reference_code": "BKG-HUDDL",
                "scheduled_date": "2026-07-18",
                "scheduled_time": "11:30",
                "duration_minutes": 30,
                "booking_kind": "consultation",
                "consultation_method": "meet",
                "event_id": "bttcoabc123",
                "conference_request_id": "bttcomeetabc123",
            }
        )

        self.assertEqual(event["summary"], "Client Name - Consultation")
        self.assertEqual(event["id"], "bttcoabc123")
        self.assertEqual(event["attendees"], [{"email": "client@example.test"}])
        self.assertEqual(event["conferenceData"]["createRequest"]["requestId"], "bttcomeetabc123")

    def test_tattoo_calendar_event_includes_client_invite(self):
        event = calendar_sync_service._build_event_body(
            {
                "client_name": "Client Name",
                "client_email": "client@example.test",
                "description": "Back piece",
                "reference_code": "BKG-INVITE",
                "scheduled_date": "2026-07-18",
                "scheduled_time": "11:30",
                "duration_minutes": 240,
                "booking_kind": "tattoo_session",
            }
        )

        self.assertEqual(event["attendees"], [{"email": "client@example.test"}])

    async def test_booking_event_sends_google_attendee_updates(self):
        service = MagicMock()
        service.events.return_value.insert.return_value.execute.return_value = {
            "id": "event-1",
            "htmlLink": "https://calendar.google.test/event-1",
        }
        settings = SimpleNamespace(LOCAL_DEV_MODE=False, STUDIO_NAME="Ryozan Studio", STUDIO_ADDRESS="")

        with (
            patch.object(calendar_sync_service, "get_settings", return_value=settings),
            patch.object(calendar_sync_service, "_get_calendar_service", return_value=service),
        ):
            result = await calendar_sync_service.create_booking_event(
                {
                    "client_name": "Client Name",
                    "client_email": "client@example.test",
                    "scheduled_date": "2026-07-18",
                    "scheduled_time": "11:30",
                    "booking_kind": "tattoo_session",
                },
                calendar_id="artist@example.test",
            )

        self.assertEqual(result["event_id"], "event-1")
        insert_kwargs = service.events.return_value.insert.call_args.kwargs
        self.assertEqual(insert_kwargs["calendarId"], "artist@example.test")
        self.assertEqual(insert_kwargs["sendUpdates"], "all")

    async def test_artist_booking_alert_sends_foundry_markup_with_current_action_urls(self):
        send_mock = AsyncMock(return_value=True)
        conversation_id = uuid.uuid4()

        with patch("app.api.v1.endpoints.bookings.send_email", new=send_mock):
            accepted = await _send_artist_booking_alert(
                to="artist@example.test",
                client_name="Client Name",
                client_email="client@example.test",
                client_phone=None,
                artist_name="Artist Name",
                description="Black and grey forearm piece",
                placement="forearm",
                size="medium",
                budget="$500-$1000",
                timeline="flexible",
                reference_code="BKG-ACTIONS",
                conversation_id=conversation_id,
            )

        self.assertTrue(accepted)
        send_mock.assert_awaited_once()
        kwargs = send_mock.await_args.kwargs
        html = kwargs["html_body"]
        self.assertEqual(kwargs["subject"], "New Booking Request from Client Name")
        self.assertEqual(kwargs["reply_to"], "client@example.test")
        self.assertEqual(kwargs["tags"], ["booking-artist-alert"])
        self.assertIn("starseed-tile.png", html)
        self.assertIn("tiger-mark.png", html)
        self.assertIn("Booking Request // New", html)
        self.assertIn("ref=BKG-ACTIONS&amp;action=accept", html)
        self.assertIn("ref=BKG-ACTIONS&amp;action=reject", html)
        self.assertIn(f"convo={conversation_id}", html)

    async def test_artist_booking_alert_includes_reference_images(self):
        send_mock = AsyncMock(return_value=True)
        reference_urls = [
            "https://assets.ryozan.app/bookings/uploads/reference-one.png",
            "https://assets.ryozan.app/bookings/uploads/reference-two.png",
        ]

        with patch("app.api.v1.endpoints.bookings.send_email", new=send_mock):
            await _send_artist_booking_alert(
                to="artist@example.test",
                client_name="Client Name",
                client_email="client@example.test",
                client_phone=None,
                artist_name="Artist Name",
                description="Black and grey forearm piece",
                placement="forearm",
                size="medium",
                budget="$500-$1000",
                timeline="flexible",
                reference_code="BKG-REFS",
                conversation_id=uuid.uuid4(),
                reference_image_urls=reference_urls,
            )

        html = send_mock.await_args.kwargs["html_body"]
        self.assertIn("Reference images", html)
        self.assertIn(reference_urls[0], html)
        self.assertIn(reference_urls[1], html)

    async def test_booking_email_outbox_persists_provider_acceptance(self):
        event = BookingLifecycleEvent(
            id=uuid.uuid4(),
            booking_id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            event_type="booking_email_intent",
            source="booking_email",
            provider_event_id="booking:test:email:client",
            reference_code="BKG-EMAIL",
            summary="Booking client confirmation email queued.",
            created_at=datetime.now(UTC),
            details={
                "kind": "client_confirmation",
                "to": "client@example.test",
                "status": "queued",
                "attempts": 0,
                "updated_at": datetime.now(UTC).isoformat(),
                "payload": {
                    "client_name": "Client Name",
                    "artist_name": "Artist Name",
                    "description": "Tattoo request",
                    "placement": "arm",
                    "reference_code": "BKG-EMAIL",
                },
            },
        )
        session = _BookingEmailOutboxSession([event])
        with (
            patch("app.api.v1.endpoints.bookings.async_session", return_value=session),
            patch(
                "app.api.v1.endpoints.bookings._send_booking_confirmation",
                new_callable=AsyncMock,
                return_value="sent",
            ) as send_mock,
        ):
            delivered = await bookings_endpoint.process_booking_email_outbox_once()

        self.assertEqual(delivered, 1)
        self.assertEqual(event.details["status"], "sent")
        self.assertEqual(event.details["attempts"], 1)
        self.assertEqual(session.commits, 2)
        send_mock.assert_awaited_once_with(
            to="client@example.test",
            client_name="Client Name",
            artist_name="Artist Name",
            description="Tattoo request",
            placement="arm",
            reference_code="BKG-EMAIL",
        )

    async def test_client_confirmation_binds_uploaded_reference_images_into_foundry(self):
        foundry_session = AsyncMock()
        foundry_session.__aenter__.return_value = foundry_session
        foundry_session.__aexit__.return_value = None
        dispatch_mock = AsyncMock(return_value={"matched": ["booking_confirmation"], "sent": 1})
        reference_urls = [
            "https://assets.ryozan.app/bookings/uploads/reference-one.png",
            "https://assets.ryozan.app/bookings/uploads/reference-two.png",
            "https://assets.ryozan.app/bookings/uploads/reference-three.png",
        ]

        with (
            patch("app.api.v1.endpoints.bookings.async_session", return_value=foundry_session),
            patch("app.api.v1.endpoints.bookings.dispatch_foundry_trigger", new=dispatch_mock),
            patch(
                "app.api.v1.endpoints.bookings.get_settings",
                return_value=SimpleNamespace(FRONTEND_URL="https://ryozan.app"),
            ),
        ):
            result = await bookings_endpoint._send_booking_confirmation(
                to="client@example.test",
                client_name="Client Name",
                artist_name="Artist Name",
                description="Tattoo request",
                placement="arm",
                reference_code="BKG-REFS",
                reference_image_urls=reference_urls,
            )

        self.assertEqual(result, "sent")
        variables = dispatch_mock.await_args.kwargs["variables"]
        self.assertEqual(variables["ref_image_1"], reference_urls[0])
        self.assertEqual(variables["ref_image_2"], reference_urls[1])
        self.assertNotIn(reference_urls[2], variables.values())

    async def test_booking_email_outbox_retains_retryable_failure(self):
        event = BookingLifecycleEvent(
            id=uuid.uuid4(),
            booking_id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            event_type="booking_email_intent",
            source="booking_email",
            provider_event_id="booking:test:email:artist",
            reference_code="BKG-ALERT",
            summary="Booking artist alert email queued.",
            created_at=datetime.now(UTC),
            details={
                "kind": "artist_alert",
                "to": "artist@example.test",
                "status": "queued",
                "attempts": 0,
                "updated_at": datetime.now(UTC).isoformat(),
                "payload": {
                    "client_name": "Client Name",
                    "client_email": "client@example.test",
                    "client_phone": None,
                    "artist_name": "Artist Name",
                    "description": "Tattoo request",
                    "placement": "arm",
                    "size": "medium",
                    "budget": None,
                    "timeline": None,
                    "reference_code": "BKG-ALERT",
                },
            },
        )
        session = _BookingEmailOutboxSession([event])
        with (
            patch("app.api.v1.endpoints.bookings.async_session", return_value=session),
            patch(
                "app.api.v1.endpoints.bookings._send_artist_booking_alert",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            delivered = await bookings_endpoint.process_booking_email_outbox_once()

        self.assertEqual(delivered, 0)
        self.assertEqual(event.details["status"], "failed")
        self.assertEqual(event.details["attempts"], 1)
        self.assertIn("next_attempt_at", event.details)
        self.assertNotIn("example.test", event.details["last_error"] or "")

    def test_workspace_delegation_uses_service_account_subject(self):
        settings = SimpleNamespace(
            GOOGLE_CALENDAR_AUTH_MODE="workspace_delegated",
            GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON='{"client_email":"calendar@test.invalid"}',
            GOOGLE_CALENDAR_DELEGATED_USER="booking@ryozan.app",
            GOOGLE_CALENDAR_DEFAULT_ID="",
            GOOGLE_CLIENT_ID="",
            GOOGLE_CLIENT_SECRET="",
            GOOGLE_REFRESH_TOKEN="",
        )
        base_credentials = MagicMock()
        delegated_credentials = MagicMock()
        base_credentials.with_subject.return_value = delegated_credentials

        with (
            patch.object(calendar_sync_service, "get_settings", return_value=settings),
            patch.object(
                calendar_sync_service.service_account.Credentials,
                "from_service_account_info",
                return_value=base_credentials,
            ) as from_info,
        ):
            credentials = calendar_sync_service._get_credentials()

        self.assertIs(credentials, delegated_credentials)
        from_info.assert_called_once_with(
            {"client_email": "calendar@test.invalid"},
            scopes=calendar_sync_service.SCOPES,
        )
        base_credentials.with_subject.assert_called_once_with("booking@ryozan.app")

    def test_workspace_artist_calendar_prefers_explicit_shared_calendar(self):
        settings = SimpleNamespace(GOOGLE_CALENDAR_AUTH_MODE="workspace_delegated")
        artist = SimpleNamespace(
            email="artist@example.test",
            google_calendar_id="studio-calendar@example.test",
        )

        with patch.object(calendar_sync_service, "get_settings", return_value=settings):
            calendar_id = calendar_sync_service.artist_calendar_id(artist)

        self.assertEqual(calendar_id, "studio-calendar@example.test")

    async def test_google_calendar_freebusy_errors_fail_closed(self):
        service = MagicMock()
        service.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {"primary": {"errors": [{"reason": "notFound"}], "busy": []}}
        }

        with (
            patch.object(calendar_sync_service, "_get_calendar_service", return_value=service),
            self.assertRaisesRegex(RuntimeError, "notFound"),
        ):
            await calendar_sync_service.get_availability(
                "2026-07-18",
                "2026-07-18",
                refresh_token="refresh-token",
            )

    def test_confirmed_consultation_email_has_schedule_and_meet(self):
        html = build_booking_confirmation_html(
            client_name="Client Name",
            artist_name="Justin Walker",
            description="Sleeve consultation",
            placement=None,
            reference_code="BKG-HUDDL",
            booking_kind="consultation",
            consultation_method="meet",
            confirmed=True,
            scheduled_date="2026-07-18",
            scheduled_time="11:30",
            conference_url="https://meet.google.com/test-room",
        )

        self.assertIn("CONSULTATION BOOKED", html)
        self.assertIn("2026-07-18 at 11:30", html)
        self.assertIn("https://meet.google.com/test-room", html)

    async def test_public_booking_checkout_uses_shop_fallback_without_artist_connect(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            stripe_account_id=None,
            stripe_onboarding_complete=False,
            commission_rate=70,
        )
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist.id,
            reference_code="BKG-CHECK1",
            guest_name="Client Name",
            deposit_amount=200,
        )
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="checkout-hold",
            artist_id=artist.id,
            artist_slug="artist",
            scheduled_date=date(2026, 7, 18),
            scheduled_time="12:00",
            duration_minutes=240,
            status="consumed",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        db = _FakeSession([])
        created = {}
        request_options = {}

        def fake_create(params, options=None):
            created.update(params)
            request_options.update(options or {})
            return type(
                "FakeStripeSession", (), {"id": "cs_test_booking", "url": "https://checkout.stripe.test/session"}
            )()

        fake_settings = type(
            "FakeSettings",
            (),
            {
                "STRIPE_SECRET_KEY": "sk_test_redacted",
                "FRONTEND_URL": "https://ryozan.app",
                "LOCAL_DEV_MODE": False,
            },
        )()

        stripe_client = MagicMock()
        stripe_client.v1.checkout.sessions.create.side_effect = fake_create
        with (
            patch("app.api.v1.endpoints.bookings.get_settings", return_value=fake_settings),
            patch("app.api.v1.endpoints.bookings.get_stripe_client", return_value=stripe_client),
        ):
            url = await _create_public_booking_checkout(
                db,
                booking=booking,
                artist=artist,
                slot_hold=hold,
                client_email="client@example.test",
                deposit_amount=200,
                save_payment_method=False,
            )

        self.assertEqual(url, "https://checkout.stripe.test/session")
        self.assertEqual(booking.stripe_checkout_session_id, "cs_test_booking")
        payment_intent_data = created["payment_intent_data"]
        self.assertNotIn("transfer_data", payment_intent_data)
        self.assertEqual(payment_intent_data["receipt_email"], "client@example.test")
        self.assertIn("BKG-CHECK1", payment_intent_data["description"])
        self.assertEqual(created["metadata"]["checkout_type"], "public_booking_deposit")
        events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(events[0].details["route_mode"], "not_connected")
        self.assertFalse(events[0].details["artist_connect_account"])
        self.assertNotIn("stripe_account", request_options)

    async def test_public_booking_checkout_routes_to_artist_connect_and_saves_payment_method(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            stripe_account_id="acct_test_artist",
            stripe_onboarding_complete=True,
            commission_rate=70,
        )
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist.id,
            reference_code="BKG-CHECK2",
            guest_name="Client Name",
            deposit_amount=300,
        )
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="checkout-hold-connect",
            artist_id=artist.id,
            artist_slug="artist",
            scheduled_date=date(2026, 7, 19),
            scheduled_time="13:00",
            duration_minutes=420,
            status="consumed",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        db = _FakeSession([])
        created = {}
        request_options = {}

        def fake_create(params, options=None):
            created.update(params)
            request_options.update(options or {})
            return type(
                "FakeStripeSession", (), {"id": "cs_test_connect", "url": "https://checkout.stripe.test/connect"}
            )()

        fake_settings = type(
            "FakeSettings",
            (),
            {
                "STRIPE_SECRET_KEY": "sk_test_redacted",
                "FRONTEND_URL": "https://ryozan.app",
                "LOCAL_DEV_MODE": False,
            },
        )()

        stripe_client = MagicMock()
        stripe_client.v1.checkout.sessions.create.side_effect = fake_create
        with (
            patch("app.api.v1.endpoints.bookings.get_settings", return_value=fake_settings),
            patch("app.api.v1.endpoints.bookings.get_stripe_client", return_value=stripe_client),
        ):
            url = await _create_public_booking_checkout(
                db,
                booking=booking,
                artist=artist,
                slot_hold=hold,
                client_email="client@example.test",
                deposit_amount=300,
                save_payment_method=True,
            )

        self.assertEqual(url, "https://checkout.stripe.test/connect")
        payment_intent_data = created["payment_intent_data"]
        self.assertNotIn("transfer_data", payment_intent_data)
        self.assertEqual(request_options["stripe_account"], "acct_test_artist")
        self.assertNotIn("on_behalf_of", payment_intent_data)
        self.assertEqual(payment_intent_data["setup_future_usage"], "off_session")
        self.assertEqual(created["customer_creation"], "always")
        events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(events[0].details["route_mode"], "connected")
        self.assertTrue(events[0].details["artist_connect_account"])

    async def test_public_booking_checkout_rejects_live_stripe_key_in_local_dev(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
        )
        booking = Booking(id=uuid.uuid4(), artist_id=artist.id, reference_code="BKG-LIVEKEY")
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="checkout-live-key",
            artist_id=artist.id,
            artist_slug="artist",
            scheduled_date=date(2026, 7, 20),
            scheduled_time="12:00",
            duration_minutes=120,
            status="consumed",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        fake_settings = type(
            "FakeSettings",
            (),
            {
                "STRIPE_SECRET_KEY": "sk_live_redacted",
                "FRONTEND_URL": "https://ryozan.app",
                "LOCAL_DEV_MODE": True,
            },
        )()

        with (
            patch("app.api.v1.endpoints.bookings.get_settings", return_value=fake_settings),
            patch("app.api.v1.endpoints.bookings.get_stripe_client") as create_mock,
            self.assertRaises(HTTPException) as err,
        ):
            await _create_public_booking_checkout(
                _FakeSession([]),
                booking=booking,
                artist=artist,
                slot_hold=hold,
                client_email="client@example.test",
                deposit_amount=200,
                save_payment_method=False,
            )

        self.assertEqual(err.exception.status_code, 503)
        self.assertEqual(err.exception.detail, "Live Stripe checkout is disabled in local dev")
        create_mock.assert_not_called()

    async def test_generic_patch_rejects_conflicting_schedule_change(self):
        artist_id = uuid.uuid4()
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="quoted",
            description="Shoulder piece",
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        conflicting = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="confirmed",
            scheduled_date=_next_weekday(4),
            scheduled_time="13:00",
            duration_minutes=120,
        )
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        db = _PatchSession(booking, artist, [[booking], [conflicting], []])

        with self.assertRaises(HTTPException) as err:
            await update_booking(
                booking.id,
                BookingUpdate(
                    scheduled_date=conflicting.scheduled_date,
                    scheduled_time="13:00",
                    duration_minutes=120,
                ),
                db,
                artist,
            )

        self.assertEqual(err.exception.status_code, 409)
        self.assertFalse(db.flushed)

    async def test_admin_generic_patch_can_override_conflicting_schedule_change(self):
        artist_id = uuid.uuid4()
        admin_id = uuid.uuid4()
        slot_day = _next_weekday(4)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="quoted",
            description="Shoulder piece",
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        conflicting = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="confirmed",
            scheduled_date=slot_day,
            scheduled_time="13:00",
            duration_minutes=120,
        )
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        admin = User(
            id=admin_id,
            email="admin@example.test",
            password_hash="disabled",
            name="Admin",
            role="admin",
        )
        db = _PatchSession(
            booking, artist, [[booking], [conflicting], [], [conflicting], [], ["Artist"], [], ["Artist"]]
        )

        response = await update_booking(
            booking.id,
            BookingUpdate(
                scheduled_date=slot_day,
                scheduled_time="13:00",
                duration_minutes=120,
                allow_conflict=True,
                override_reason="Founder approved existing booking overlap",
            ),
            db,
            admin,
        )

        self.assertTrue(db.flushed)
        self.assertEqual(response.scheduled_time, "13:00")
        events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual([event.event_type for event in events], ["schedule_updated", "schedule_override"])
        self.assertEqual(events[-1].details["blocking_reasons"], ["booking_conflict"])

    async def test_generic_confirm_can_suppress_legacy_client_notifications(self):
        artist_id = uuid.uuid4()
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="pending",
            description="Full-day chest session",
            guest_name="Client Name",
            guest_email="client@example.test",
            scheduled_date=_next_weekday(4),
            scheduled_time="12:00",
            duration_minutes=420,
            deposit_amount=0,
            estimated_total=1000,
            deposit_paid=False,
            sms_opt_in=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
        )
        db = _PatchSession(booking, artist, [[booking], [], ["Artist"], [], ["Artist"]])

        with (
            patch(
                "app.api.v1.endpoints.bookings._post_booking_calendar_event",
                new=AsyncMock(),
            ) as calendar_mock,
            patch(
                "app.api.v1.endpoints.bookings._notify_booking_lifecycle",
                new=AsyncMock(),
            ) as email_mock,
            patch(
                "app.api.v1.endpoints.bookings._send_booking_confirmed_sms",
                new=AsyncMock(),
            ) as sms_mock,
        ):
            response = await update_booking(
                booking.id,
                BookingUpdate(status="confirmed", notify_client=False),
                db,
                artist,
            )

        self.assertEqual(response.status, BookingStatus.CONFIRMED)
        calendar_mock.assert_awaited_once()
        email_mock.assert_not_awaited()
        sms_mock.assert_not_awaited()
        events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(
            [event.event_type for event in events],
            ["status_changed", "client_notification_suppressed"],
        )
        self.assertEqual(events[-1].details, {"calendar": True, "email": False, "sms": False})

    async def test_confirmation_default_keeps_legacy_client_notifications(self):
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            status="confirmed",
            description="Full-day chest session",
            scheduled_date=_next_weekday(4),
            scheduled_time="12:00",
        )
        artist = User(
            id=booking.artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
        )
        db = _FakeSession([])

        with (
            patch(
                "app.api.v1.endpoints.bookings._post_booking_calendar_event",
                new=AsyncMock(),
            ) as calendar_mock,
            patch(
                "app.api.v1.endpoints.bookings._notify_booking_lifecycle",
                new=AsyncMock(),
            ) as email_mock,
            patch(
                "app.api.v1.endpoints.bookings._send_booking_confirmed_sms",
                new=AsyncMock(),
            ) as sms_mock,
        ):
            await _on_booking_confirmed(db, booking, artist, artist.id)

        calendar_mock.assert_awaited_once()
        email_mock.assert_awaited_once()
        sms_mock.assert_awaited_once()
        self.assertEqual(db.added, [])

    async def test_staff_reschedule_confirms_pending_booking_on_available_slot(self):
        artist_id = uuid.uuid4()
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="pending",
            description="Shoulder piece",
            guest_name="Client Name",
            guest_email="client@example.test",
            size_estimate=None,
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        slot_day = _next_weekday(4)
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        db = _RescheduleSession(
            booking,
            artist,
            [
                [booking],
                [],
                [],
                [],
                [],
                ["Artist"],
            ],
        )

        response = await reschedule_booking(
            booking.id,
            BookingReschedule(
                scheduled_date=slot_day,
                scheduled_time="11:00",
                duration_minutes=120,
                reason="Client requested morning",
                notify_client=False,
            ),
            db,
            artist,
        )

        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(booking.scheduled_date, slot_day)
        self.assertEqual(booking.scheduled_time, "11:00")
        self.assertEqual(booking.duration_minutes, 120)
        self.assertIn("Client requested morning", booking.artist_notes)
        self.assertTrue(db.flushed)
        self.assertTrue(db.committed)
        self.assertTrue(db.refreshed)
        self.assertEqual(response.scheduled_time, "11:00")
        events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "rescheduled")
        self.assertEqual(events[0].booking_id, booking.id)
        self.assertEqual(events[0].actor_id, artist.id)

    async def test_booking_history_returns_staff_lifecycle_events(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
        )
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist.id,
            status="confirmed",
            description="Shoulder piece",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        events = [
            BookingLifecycleEvent(
                id=uuid.uuid4(),
                booking_id=booking.id,
                artist_id=artist.id,
                event_type="hold_consumed",
                source="public_booking",
                summary="Hold consumed.",
                created_at=datetime.now(UTC),
            ),
            BookingLifecycleEvent(
                id=uuid.uuid4(),
                booking_id=booking.id,
                artist_id=artist.id,
                event_type="booking_submitted",
                source="public_booking",
                summary="Booking submitted.",
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            ),
        ]
        db = _ScriptedSession([[booking], events])

        response = await get_booking_history(booking.id, db=db, current_user=artist)

        self.assertEqual([event.event_type for event in response], ["hold_consumed", "booking_submitted"])
        self.assertEqual(db.calls, 2)

    async def test_booking_schedule_suggestions_rank_preferred_time(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_slot_interval_minutes=60,
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        preferred_day = _next_weekday(4)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist.id,
            status="quoted",
            description="Shoulder piece",
            preferred_date=preferred_day,
            preferred_time="13:00",
            duration_minutes=120,
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db = _RescheduleSession(booking, artist, [[booking], [], []])

        response = await get_booking_schedule_suggestions(
            booking.id,
            days=7,
            limit=3,
            db=db,
            current_user=artist,
        )

        self.assertEqual(response.booking_id, booking.id)
        self.assertEqual(response.duration_minutes, 120)
        self.assertGreaterEqual(len(response.suggestions), 1)
        self.assertEqual(response.suggestions[0].date, preferred_day.isoformat())
        self.assertEqual(response.suggestions[0].time, "13:00")
        self.assertIn("preferred time", response.suggestions[0].rationale)

    async def test_staff_reschedule_rejects_conflicting_slot_before_commit(self):
        artist_id = uuid.uuid4()
        slot_day = _next_weekday(4)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="quoted",
            description="Shoulder piece",
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        conflicting = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="confirmed",
            scheduled_date=slot_day,
            scheduled_time="11:00",
            duration_minutes=120,
        )
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        db = _RescheduleSession(booking, artist, [[booking], [conflicting], []])

        with self.assertRaises(HTTPException) as err:
            await reschedule_booking(
                booking.id,
                BookingReschedule(
                    scheduled_date=slot_day,
                    scheduled_time="11:00",
                    duration_minutes=120,
                    notify_client=False,
                ),
                db,
                artist,
            )

        self.assertEqual(err.exception.status_code, 409)
        self.assertFalse(db.flushed)
        self.assertFalse(db.committed)
        self.assertIsNone(booking.scheduled_date)

    async def test_admin_reschedule_can_override_conflicting_slot_with_audit_reason(self):
        artist_id = uuid.uuid4()
        admin_id = uuid.uuid4()
        slot_day = _next_weekday(4)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="quoted",
            description="Shoulder piece",
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        conflicting = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="confirmed",
            scheduled_date=slot_day,
            scheduled_time="11:00",
            duration_minutes=120,
        )
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        admin = User(
            id=admin_id,
            email="admin@example.test",
            password_hash="disabled",
            name="Admin",
            role="admin",
        )
        db = _RescheduleSession(booking, artist, [[booking], [conflicting], [], [conflicting], [], [], ["Artist"]])

        response = await reschedule_booking(
            booking.id,
            BookingReschedule(
                scheduled_date=slot_day,
                scheduled_time="11:00",
                duration_minutes=120,
                notify_client=False,
                allow_conflict=True,
                override_reason="Founder approved double-book for walk-in gap",
            ),
            db,
            admin,
        )

        self.assertEqual(response.scheduled_time, "11:00")
        self.assertTrue(db.flushed)
        self.assertTrue(db.committed)
        self.assertIn("Scheduler override used", booking.artist_notes)
        events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual([event.event_type for event in events], ["rescheduled", "schedule_override"])
        self.assertEqual(events[-1].details["blocking_reasons"], ["booking_conflict"])
        self.assertEqual(events[-1].details["override_reason"], "Founder approved double-book for walk-in gap")

    async def test_artist_reschedule_cannot_override_conflicting_slot(self):
        artist_id = uuid.uuid4()
        slot_day = _next_weekday(4)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="quoted",
            description="Shoulder piece",
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        conflicting = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="confirmed",
            scheduled_date=slot_day,
            scheduled_time="11:00",
            duration_minutes=120,
        )
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        db = _RescheduleSession(booking, artist, [[booking], [conflicting], []])

        with self.assertRaises(HTTPException) as err:
            await reschedule_booking(
                booking.id,
                BookingReschedule(
                    scheduled_date=slot_day,
                    scheduled_time="11:00",
                    duration_minutes=120,
                    notify_client=False,
                    allow_conflict=True,
                    override_reason="Artist trying to bypass conflict",
                ),
                db,
                artist,
            )

        self.assertEqual(err.exception.status_code, 403)
        self.assertFalse(db.flushed)
        self.assertFalse(db.committed)
        self.assertIsNone(booking.scheduled_date)

    async def test_admin_reschedule_can_override_out_of_bounds_closed_day_separately(self):
        artist_id = uuid.uuid4()
        admin_id = uuid.uuid4()
        closed_day = _next_weekday(6)
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="quoted",
            description="Shoulder piece",
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        admin = User(
            id=admin_id,
            email="admin@example.test",
            password_hash="disabled",
            name="Admin",
            role="admin",
        )
        db = _RescheduleSession(booking, artist, [[booking], [], [], [], ["Artist"]])

        response = await reschedule_booking(
            booking.id,
            BookingReschedule(
                scheduled_date=closed_day,
                scheduled_time="11:00",
                duration_minutes=120,
                notify_client=False,
                allow_out_of_bounds=True,
                override_reason="Private closed-day appointment approved",
            ),
            db,
            admin,
        )

        self.assertEqual(response.scheduled_date, closed_day)
        events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(events[-1].event_type, "schedule_override")
        self.assertEqual(events[-1].details["blocking_reasons"], ["closed_day"])

    def test_public_submit_rejects_datetime_in_date_field(self):
        with self.assertRaises(ValidationError):
            PublicBookingSubmit(
                artist_slug="justin",
                first_name="Client",
                last_name="Name",
                email="client@example.com",
                description="Black and grey shoulder piece",
                preferred_date="2026-07-18 11:30",
            )

    def test_available_slots_exclude_busy_and_confirmed_booking_windows(self):
        slot_day = _next_weekday(4)
        booking = Booking(
            id=uuid.uuid4(),
            scheduled_date=slot_day,
            scheduled_time="13:00",
            duration_minutes=120,
        )

        slots = _generate_available_slots(
            start_date=slot_day,
            end_date=slot_day,
            duration_minutes=120,
            slot_interval_minutes=30,
            buffer_minutes=0,
            minimum_notice_hours=0,
            max_advance_days=180,
            allow_same_day=True,
            busy_blocks=[{"start": _shop_iso(slot_day, "11:00"), "end": _shop_iso(slot_day, "12:00")}],
            confirmed_bookings=[booking],
            calendar_connected=True,
            limit=40,
        )
        times = {slot.time for slot in slots}

        self.assertNotIn("11:00", times)
        self.assertNotIn("11:30", times)
        self.assertNotIn("13:00", times)
        self.assertNotIn("14:30", times)
        self.assertIn("15:00", times)

    def test_available_slots_use_python_weekday_numbering(self):
        wednesday = _next_weekday(2)
        sunday = _next_weekday(6)

        wednesday_slots = _generate_available_slots(
            start_date=wednesday,
            end_date=wednesday,
            duration_minutes=60,
            slot_interval_minutes=30,
            buffer_minutes=0,
            minimum_notice_hours=0,
            max_advance_days=180,
            allow_same_day=True,
            busy_blocks=[],
            confirmed_bookings=[],
            calendar_connected=False,
            limit=5,
        )
        sunday_slots = _generate_available_slots(
            start_date=sunday,
            end_date=sunday,
            duration_minutes=60,
            slot_interval_minutes=30,
            buffer_minutes=0,
            minimum_notice_hours=0,
            max_advance_days=180,
            allow_same_day=True,
            busy_blocks=[],
            confirmed_bookings=[],
            calendar_connected=False,
            limit=5,
        )

        self.assertEqual(wednesday_slots[0].time, "11:00")
        self.assertEqual(sunday_slots, [])

    def test_available_slots_exclude_active_holds(self):
        slot_day = _next_weekday(4)
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="active-hold",
            artist_id=uuid.uuid4(),
            artist_slug="justin",
            scheduled_date=slot_day,
            scheduled_time="11:30",
            duration_minutes=120,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

        slots = _generate_available_slots(
            start_date=slot_day,
            end_date=slot_day,
            duration_minutes=120,
            slot_interval_minutes=30,
            buffer_minutes=0,
            minimum_notice_hours=0,
            max_advance_days=180,
            allow_same_day=True,
            busy_blocks=[],
            confirmed_bookings=[],
            calendar_connected=False,
            limit=40,
            active_holds=[hold],
        )
        times = {slot.time for slot in slots}

        self.assertNotIn("11:00", times)
        self.assertNotIn("11:30", times)
        self.assertNotIn("12:00", times)
        self.assertNotIn("13:00", times)
        self.assertIn("13:30", times)

    def test_blocked_slots_explain_conflict_sources(self):
        slot_day = _next_weekday(4)
        artist_id = uuid.uuid4()
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            scheduled_date=slot_day,
            scheduled_time="13:00",
            duration_minutes=120,
        )
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="active-hold",
            artist_id=artist_id,
            artist_slug="justin",
            scheduled_date=slot_day,
            scheduled_time="15:00",
            duration_minutes=120,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

        blocked = _generate_blocked_slots(
            start_date=slot_day,
            end_date=slot_day,
            duration_minutes=120,
            slot_interval_minutes=30,
            buffer_minutes=0,
            minimum_notice_hours=0,
            max_advance_days=180,
            allow_same_day=True,
            busy_blocks=[
                {
                    "start": _shop_iso(slot_day, "11:00"),
                    "end": _shop_iso(slot_day, "12:00"),
                }
            ],
            confirmed_bookings=[booking],
            active_holds=[hold],
            limit=40,
        )
        by_time = {slot.time: set(slot.reasons) for slot in blocked}

        self.assertIn("google_busy", by_time["11:00"])
        self.assertIn("booking_conflict", by_time["13:00"])
        self.assertIn("active_hold", by_time["15:00"])

    def test_blocked_slots_explain_closed_days(self):
        closed_day = _next_weekday(0)

        blocked = _generate_blocked_slots(
            start_date=closed_day,
            end_date=closed_day,
            duration_minutes=120,
            slot_interval_minutes=30,
            buffer_minutes=0,
            minimum_notice_hours=0,
            max_advance_days=180,
            allow_same_day=True,
            busy_blocks=[],
            confirmed_bookings=[],
            active_holds=[],
            limit=40,
        )

        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].date, closed_day.isoformat())
        self.assertEqual(blocked[0].time, "00:00")
        self.assertEqual(blocked[0].duration_minutes, 0)
        self.assertEqual(blocked[0].reasons, ["closed_day"])
        self.assertIn("closed", blocked[0].label)

    def test_available_slots_apply_minimum_notice_and_buffer_rules(self):
        slot_day = _next_weekday(4)
        booking = Booking(
            id=uuid.uuid4(),
            scheduled_date=slot_day,
            scheduled_time="13:00",
            duration_minutes=120,
        )

        slots = _generate_available_slots(
            start_date=slot_day,
            end_date=slot_day,
            duration_minutes=60,
            slot_interval_minutes=30,
            buffer_minutes=30,
            minimum_notice_hours=0,
            max_advance_days=180,
            allow_same_day=True,
            busy_blocks=[],
            confirmed_bookings=[booking],
            calendar_connected=True,
            limit=40,
        )
        times = {slot.time for slot in slots}

        self.assertNotIn("12:30", times)
        self.assertNotIn("15:00", times)
        self.assertIn("15:30", times)

    def test_blocked_slots_explain_minimum_notice(self):
        slot_day = _next_weekday(4)
        blocked = _generate_blocked_slots(
            start_date=slot_day,
            end_date=slot_day,
            duration_minutes=60,
            slot_interval_minutes=30,
            buffer_minutes=0,
            minimum_notice_hours=720,
            max_advance_days=180,
            allow_same_day=False,
            busy_blocks=[],
            confirmed_bookings=[],
            active_holds=[],
            limit=40,
        )

        self.assertTrue(any("minimum_notice" in slot.reasons for slot in blocked))

    def test_same_day_rule_still_applies_minimum_notice(self):
        slot_day = date(2026, 7, 17)

        with patch.object(calendar_endpoint, "dt", _FrozenDateTime):
            slots = _generate_available_slots(
                start_date=slot_day,
                end_date=slot_day,
                duration_minutes=60,
                slot_interval_minutes=30,
                buffer_minutes=0,
                minimum_notice_hours=4,
                max_advance_days=180,
                allow_same_day=True,
                busy_blocks=[],
                confirmed_bookings=[],
                calendar_connected=False,
                limit=40,
            )

        times = {slot.time for slot in slots}
        self.assertNotIn("11:00", times)
        self.assertNotIn("13:30", times)
        self.assertIn("14:00", times)

    def test_disallow_same_day_blocks_today_even_with_zero_notice(self):
        slot_day = date(2026, 7, 17)

        with patch.object(calendar_endpoint, "dt", _FrozenDateTime):
            slots = _generate_available_slots(
                start_date=slot_day,
                end_date=slot_day,
                duration_minutes=60,
                slot_interval_minutes=30,
                buffer_minutes=0,
                minimum_notice_hours=0,
                max_advance_days=180,
                allow_same_day=False,
                busy_blocks=[],
                confirmed_bookings=[],
                calendar_connected=False,
                limit=40,
            )

        self.assertEqual(slots, [])

    async def test_slot_availability_rechecks_google_busy_blocks(self):
        slot_day = _next_weekday(4)

        available = await _slot_is_available(
            _FakeSession([]),
            artist_id=uuid.uuid4(),
            scheduled_date=slot_day,
            scheduled_time="11:00",
            duration_minutes=120,
            buffer_minutes=0,
            minimum_notice_hours=0,
            max_advance_days=180,
            allow_same_day=True,
            busy_blocks=[
                {
                    "start": _shop_iso(slot_day, "11:30"),
                    "end": _shop_iso(slot_day, "12:30"),
                }
            ],
        )

        self.assertFalse(available)

    async def test_public_slots_fall_back_when_google_availability_fails(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            google_refresh_token="stale-refresh-token",
            booking_slot_interval_minutes=30,
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        slot_day = _next_weekday(4)

        with (
            patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist),
            patch.object(calendar_endpoint, "_resolve_slot_duration", new_callable=AsyncMock, return_value=60),
            patch.object(calendar_endpoint, "_confirmed_bookings_for_range", new_callable=AsyncMock, return_value=[]),
            patch.object(calendar_endpoint, "_active_holds_for_range", new_callable=AsyncMock, return_value=[]),
            patch.object(calendar_endpoint, "_personal_blocks_for_range", new_callable=AsyncMock, return_value=[]),
            patch.object(
                calendar_endpoint, "get_availability", new_callable=AsyncMock, side_effect=RuntimeError("stale")
            ),
        ):
            response = await calendar_endpoint.public_artist_slots(
                "artist",
                start=slot_day.isoformat(),
                end=slot_day.isoformat(),
                duration_minutes=60,
                slot_interval_minutes=30,
                buffer_minutes=0,
                minimum_notice_hours=0,
                max_advance_days=180,
                allow_same_day=True,
                limit=60,
                include_blocked=False,
                blocked_limit=120,
                db=object(),
            )

        self.assertFalse(response.calendar_connected)
        self.assertGreater(len(response.slots), 0)
        self.assertEqual({slot.source for slot in response.slots}, {"shop_hours"})
        self.assertEqual({slot.confidence for slot in response.slots}, {"fallback"})

    async def test_consultation_slots_fail_closed_when_google_availability_fails(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            google_refresh_token="stale-refresh-token",
            booking_slot_interval_minutes=30,
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        slot_day = _next_weekday(4)

        with (
            patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist),
            patch.object(calendar_endpoint, "_confirmed_bookings_for_range", new_callable=AsyncMock, return_value=[]),
            patch.object(calendar_endpoint, "_active_holds_for_range", new_callable=AsyncMock, return_value=[]),
            patch.object(calendar_endpoint, "_personal_blocks_for_range", new_callable=AsyncMock, return_value=[]),
            patch.object(
                calendar_endpoint, "get_availability", new_callable=AsyncMock, side_effect=RuntimeError("stale")
            ),
        ):
            response = await calendar_endpoint.public_artist_slots(
                "artist",
                start=slot_day.isoformat(),
                end=slot_day.isoformat(),
                duration_minutes=30,
                slot_interval_minutes=30,
                buffer_minutes=0,
                minimum_notice_hours=0,
                max_advance_days=180,
                allow_same_day=True,
                limit=60,
                include_blocked=True,
                blocked_limit=120,
                consultation=True,
                db=object(),
            )

        self.assertFalse(response.calendar_connected)
        self.assertEqual(response.slots, [])
        self.assertIsNone(response.blocked_slots)

    async def test_slot_hold_fails_closed_when_google_availability_fails(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            google_refresh_token="stale-refresh-token",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        slot_day = _next_weekday(4)

        with (
            patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist),
            patch.object(calendar_endpoint, "_resolve_slot_duration", new_callable=AsyncMock, return_value=60),
            patch.object(
                calendar_endpoint, "get_availability", new_callable=AsyncMock, side_effect=RuntimeError("stale")
            ),
            self.assertRaises(HTTPException) as err,
        ):
            await calendar_endpoint.hold_public_slot(
                calendar_endpoint.SlotHoldRequest(
                    artist_slug="artist",
                    date=slot_day.isoformat(),
                    time="11:00",
                    duration_minutes=60,
                ),
                db=_FakeSession([]),
            )

        self.assertEqual(err.exception.status_code, 502)

    async def test_slot_hold_replaces_previous_active_hold_for_same_inquiry(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        slot_day = _next_weekday(4)
        previous_hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="previous-hold",
            artist_id=artist.id,
            artist_slug="artist",
            scheduled_date=slot_day,
            scheduled_time="11:00",
            duration_minutes=120,
            status="active",
            inquiry_id="same-inquiry",
            guest_email="client@example.test",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db = _HoldSession([[], [previous_hold]])

        with (
            patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist),
            patch.object(calendar_endpoint, "_resolve_slot_duration", new_callable=AsyncMock, return_value=120),
            patch.object(calendar_endpoint, "_slot_is_available", new_callable=AsyncMock, return_value=True),
        ):
            response = await calendar_endpoint.hold_public_slot(
                calendar_endpoint.SlotHoldRequest(
                    artist_slug="artist",
                    date=slot_day.isoformat(),
                    time="13:00",
                    duration_minutes=120,
                    inquiry_id="same-inquiry",
                    guest_email="CLIENT@example.test",
                ),
                db=db,
            )

        self.assertEqual(previous_hold.status, "expired")
        added_holds = [row for row in db.added if isinstance(row, BookingSlotHold)]
        added_events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(len(added_holds), 1)
        self.assertEqual(len(added_events), 1)
        self.assertEqual(added_events[0].event_type, "hold_created")
        self.assertTrue(db.flushed)
        self.assertEqual(added_holds[0].status, "active")
        self.assertEqual(added_holds[0].scheduled_time, "13:00")
        self.assertEqual(added_holds[0].guest_email, "client@example.test")
        self.assertEqual(response.time, "13:00")

    async def test_flash_slot_hold_preserves_booking_kind_for_submit_and_checkout(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        slot_day = _next_weekday(4)
        db = _HoldSession([[], []])

        with (
            patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist),
            patch.object(calendar_endpoint, "_resolve_slot_duration", new_callable=AsyncMock, return_value=120),
            patch.object(calendar_endpoint, "_slot_is_available", new_callable=AsyncMock, return_value=True),
        ):
            await calendar_endpoint.hold_public_slot(
                calendar_endpoint.SlotHoldRequest(
                    artist_slug="artist",
                    date=slot_day.isoformat(),
                    time="13:00",
                    duration_minutes=120,
                    booking_kind="flash",
                ),
                db=db,
            )

        added_holds = [row for row in db.added if isinstance(row, BookingSlotHold)]
        self.assertEqual(len(added_holds), 1)
        self.assertEqual(added_holds[0].booking_kind, "flash")

    async def test_release_public_slot_hold_marks_active_hold_released(self):
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="public-hold-token",
            artist_id=uuid.uuid4(),
            artist_slug="justin",
            scheduled_date=_next_weekday(4),
            scheduled_time="11:00",
            duration_minutes=120,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        db = _HoldSession([[hold]])

        response = await calendar_endpoint.release_public_slot_hold("public-hold-token", db=db)

        self.assertTrue(response.released)
        self.assertEqual(response.status, "released")
        self.assertEqual(hold.status, "released")
        self.assertIsNotNone(hold.updated_at)
        self.assertTrue(db.flushed)

    async def test_release_public_slot_hold_does_not_release_consumed_hold(self):
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="consumed-hold-token",
            artist_id=uuid.uuid4(),
            artist_slug="justin",
            scheduled_date=_next_weekday(4),
            scheduled_time="11:00",
            duration_minutes=120,
            status="consumed",
            booking_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        db = _HoldSession([[hold]])

        response = await calendar_endpoint.release_public_slot_hold("consumed-hold-token", db=db)

        self.assertFalse(response.released)
        self.assertEqual(response.status, "consumed")
        self.assertEqual(hold.status, "consumed")
        self.assertFalse(db.flushed)

    async def test_release_public_slot_hold_is_idempotent_for_missing_hold(self):
        db = _HoldSession([[]])

        response = await calendar_endpoint.release_public_slot_hold("missing-hold-token", db=db)

        self.assertFalse(response.released)
        self.assertEqual(response.status, "missing")
        self.assertFalse(db.flushed)

    async def test_public_scheduler_contract_exposes_galaxev3_actions(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_slot_interval_minutes=45,
            booking_buffer_minutes=15,
            booking_min_notice_hours=12,
            booking_max_advance_days=120,
            booking_allow_same_day=False,
        )
        session_type = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist.id,
            label="Half-Day Session",
            size="large",
            placements=["forearm"],
            duration_minutes=240,
            deposit_amount=200,
            price_min=800,
            price_max=1200,
            sort_order=0,
        )
        db = _ScriptedSession([[session_type]])

        with patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist):
            response = await calendar_endpoint.public_scheduler_contract("artist", db=db)

        self.assertEqual(response.version, "2026-08-20")
        self.assertEqual(response.artist_slug, "artist")
        self.assertEqual(response.rules["slot_interval_minutes"], 45)
        self.assertEqual(response.rules["buffer_minutes"], 15)
        self.assertEqual(response.rules["minimum_notice_hours"], 12)
        self.assertEqual(response.rules["max_advance_days"], 120)
        self.assertFalse(response.rules["allow_same_day"])
        self.assertEqual(len(response.appointment_types), 1)
        self.assertEqual(response.appointment_types[0].label, "Half-Day Session")
        self.assertTrue(response.appointment_types[0].deposit_required)
        self.assertTrue(response.appointment_types[0].sms_reminder_required)
        self.assertEqual(response.appointment_types[0].buffer_minutes, 15)
        self.assertEqual(response.appointment_types[0].minimum_notice_hours, 12)
        self.assertEqual(response.appointment_types[0].max_advance_days, 120)
        self.assertFalse(response.appointment_types[0].allow_same_day)
        action_names = {action.name for action in response.actions}
        self.assertEqual(
            action_names,
            {
                "list_appointment_types",
                "list_slots",
                "explain_blocked_slot",
                "hold_slot",
                "release_hold",
                "submit_booking_draft",
                "submit_booking",
            },
        )
        self.assertTrue(all(action.path.startswith("/api/v1/") for action in response.actions))
        self.assertFalse(response.booky.consultation_automation_available)
        self.assertTrue(response.booky.authenticated_collector_canary_required)
        self.assertTrue(response.booky.consent_required)
        self.assertEqual(response.booky.supported_booking_kind, "consultation")
        self.assertEqual(response.booky.outcome_schema_ref, "BookingOutcomeEnvelope")

    async def test_public_slots_use_placement_specific_session_duration(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_slot_interval_minutes=30,
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        placement_session = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist.id,
            label="Forearm Large Session",
            size="large",
            placements=["forearm"],
            duration_minutes=180,
            deposit_amount=150,
            sort_order=0,
        )
        slot_day = _next_weekday(4)
        db = _ScriptedSession([[placement_session], [], []])

        with patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist):
            response = await calendar_endpoint.public_artist_slots(
                "artist",
                start=slot_day.isoformat(),
                end=slot_day.isoformat(),
                size="large",
                placement="forearm",
                duration_minutes=None,
                slot_interval_minutes=30,
                buffer_minutes=0,
                minimum_notice_hours=0,
                max_advance_days=180,
                allow_same_day=True,
                limit=60,
                include_blocked=False,
                blocked_limit=120,
                db=db,
            )

        self.assertEqual(response.duration_minutes, 180)
        self.assertGreater(len(response.slots), 0)
        self.assertTrue(all(slot.duration_minutes == 180 for slot in response.slots))
        self.assertEqual(db.calls, 3)

    async def test_explicit_staff_duration_overrides_placement_default(self):
        artist_id = uuid.uuid4()
        placement_session = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist_id,
            label="Full-Day Session",
            size="xlarge",
            placements=["Chest"],
            duration_minutes=480,
            buffer_minutes=30,
            deposit_amount=0,
            sort_order=0,
        )
        db = _ScriptedSession([[placement_session]])

        duration = await _resolve_slot_duration(
            db,
            artist_id=artist_id,
            session_type_id=None,
            size="xlarge",
            placement="Chest",
            duration_minutes=420,
        )

        self.assertEqual(duration, 420)

    async def test_slot_hold_uses_placement_specific_session_duration(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        placement_session = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist.id,
            label="Forearm Large Session",
            size="large",
            placements=["forearm"],
            duration_minutes=180,
            deposit_amount=150,
            sort_order=0,
        )
        slot_day = _next_weekday(4)
        db = _HoldSession([[], [placement_session], [], []])

        with patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist):
            response = await calendar_endpoint.hold_public_slot(
                calendar_endpoint.SlotHoldRequest(
                    artist_slug="artist",
                    date=slot_day.isoformat(),
                    time="11:00",
                    size="large",
                    placement="forearm",
                ),
                db=db,
            )

        self.assertEqual(response.duration_minutes, 180)
        added_holds = [row for row in db.added if isinstance(row, BookingSlotHold)]
        added_events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(len(added_holds), 1)
        self.assertEqual(len(added_events), 1)
        self.assertEqual(added_holds[0].duration_minutes, 180)
        self.assertEqual(added_holds[0].scheduled_time, "11:00")
        self.assertEqual(added_events[0].event_type, "hold_created")
        self.assertTrue(db.flushed)

    async def test_public_slots_use_explicit_session_type_duration(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_slot_interval_minutes=30,
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        session_type = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist.id,
            label="Half-Day Session",
            size="large",
            placements=[],
            duration_minutes=240,
            deposit_amount=200,
            sort_order=0,
        )
        slot_day = _next_weekday(4)
        db = _ScriptedSession([[session_type], [], []])

        with patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist):
            response = await calendar_endpoint.public_artist_slots(
                "artist",
                start=slot_day.isoformat(),
                end=slot_day.isoformat(),
                session_type_id=session_type.id,
                size="small",
                placement=None,
                duration_minutes=None,
                slot_interval_minutes=30,
                buffer_minutes=0,
                minimum_notice_hours=0,
                max_advance_days=180,
                allow_same_day=True,
                limit=60,
                include_blocked=False,
                blocked_limit=120,
                db=db,
            )

        self.assertEqual(response.duration_minutes, 240)
        self.assertGreater(len(response.slots), 0)
        self.assertTrue(all(slot.duration_minutes == 240 for slot in response.slots))

    async def test_public_slots_reject_explicit_session_type_for_disallowed_placement(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_slot_interval_minutes=30,
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        session_type = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist.id,
            label="Forearm Session",
            size="large",
            placements=["forearm"],
            duration_minutes=180,
            deposit_amount=150,
            sort_order=0,
        )
        slot_day = _next_weekday(4)
        db = _ScriptedSession([[session_type]])

        with (
            patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist),
            self.assertRaises(HTTPException) as err,
        ):
            await calendar_endpoint.public_artist_slots(
                "artist",
                start=slot_day.isoformat(),
                end=slot_day.isoformat(),
                session_type_id=session_type.id,
                size="large",
                placement="ribs",
                duration_minutes=None,
                slot_interval_minutes=30,
                buffer_minutes=0,
                minimum_notice_hours=0,
                max_advance_days=180,
                allow_same_day=True,
                limit=60,
                include_blocked=False,
                blocked_limit=120,
                db=db,
            )

        self.assertEqual(err.exception.status_code, 422)
        self.assertIn("placement", err.exception.detail)
        self.assertEqual(db.calls, 1)

    async def test_slot_hold_uses_explicit_session_type_duration(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        session_type = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist.id,
            label="Full-Day Session",
            size="xlarge",
            placements=[],
            duration_minutes=420,
            deposit_amount=300,
            sort_order=0,
        )
        slot_day = _next_weekday(4)
        db = _HoldSession([[], [session_type], [], []])

        with patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist):
            response = await calendar_endpoint.hold_public_slot(
                calendar_endpoint.SlotHoldRequest(
                    artist_slug="artist",
                    date=slot_day.isoformat(),
                    time="11:00",
                    session_type_id=session_type.id,
                    size="small",
                ),
                db=db,
            )

        self.assertEqual(response.duration_minutes, 420)
        added_holds = [row for row in db.added if isinstance(row, BookingSlotHold)]
        added_events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(len(added_holds), 1)
        self.assertEqual(len(added_events), 1)
        self.assertEqual(added_holds[0].duration_minutes, 420)
        self.assertEqual(added_events[0].event_type, "hold_created")
        self.assertTrue(db.flushed)

    async def test_slot_hold_rejects_explicit_session_type_for_disallowed_placement(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        session_type = ArtistSessionType(
            id=uuid.uuid4(),
            artist_id=artist.id,
            label="Forearm Session",
            size="large",
            placements=["forearm"],
            duration_minutes=180,
            deposit_amount=150,
            sort_order=0,
        )
        slot_day = _next_weekday(4)
        db = _HoldSession([[], [session_type]])

        with (
            patch.object(calendar_endpoint, "_resolve_artist_for_slots", new_callable=AsyncMock, return_value=artist),
            self.assertRaises(HTTPException) as err,
        ):
            await calendar_endpoint.hold_public_slot(
                calendar_endpoint.SlotHoldRequest(
                    artist_slug="artist",
                    date=slot_day.isoformat(),
                    time="11:00",
                    session_type_id=session_type.id,
                    placement="ribs",
                ),
                db=db,
            )

        self.assertEqual(err.exception.status_code, 422)
        self.assertIn("placement", err.exception.detail)
        self.assertEqual(len(db.added), 0)
        self.assertFalse(db.flushed)

    async def test_quote_acceptance_reuses_scheduler_rules(self):
        artist_id = uuid.uuid4()
        blocked_day = _next_weekday(0)
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            guest_name="Client Name",
            description="Black and grey shoulder piece",
        )

        with self.assertRaises(HTTPException) as err:
            await _assert_quote_slot_available(
                db=_FakeSession([]),
                booking=booking,
                artist=artist,
                selected={
                    "date": blocked_day.isoformat(),
                    "time": "11:00",
                    "duration_minutes": 120,
                },
            )

        self.assertEqual(err.exception.status_code, 409)

    async def test_flash_booking_reuses_scheduler_rules(self):
        artist_id = uuid.uuid4()
        slot_day = _next_weekday(4)
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )
        conflicting = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            status="confirmed",
            scheduled_date=slot_day,
            scheduled_time="13:00",
            duration_minutes=120,
        )

        with self.assertRaises(HTTPException) as err:
            await _assert_flash_slot_available(
                _FakeSession([conflicting]),
                artist=artist,
                scheduled_date=slot_day,
                scheduled_time="13:00",
                duration_minutes=120,
            )

        self.assertEqual(err.exception.status_code, 409)

    async def test_flash_booking_allows_open_scheduler_slot(self):
        artist_id = uuid.uuid4()
        slot_day = _next_weekday(4)
        artist = User(
            id=artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            booking_buffer_minutes=0,
            booking_min_notice_hours=0,
            booking_max_advance_days=180,
            booking_allow_same_day=True,
        )

        await _assert_flash_slot_available(
            _FakeSession([]),
            artist=artist,
            scheduled_date=slot_day,
            scheduled_time="13:00",
            duration_minutes=120,
        )

    async def test_checkout_completion_consumes_quote_slot_hold(self):
        booking_id = uuid.uuid4()
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="quote-hold-token",
            artist_id=uuid.uuid4(),
            artist_slug="justin",
            scheduled_date=_next_weekday(4),
            scheduled_time="11:00",
            duration_minutes=120,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        booking = Booking(id=booking_id, artist_id=hold.artist_id)

        db = _FakeSession([hold])
        consumed = await _consume_slot_hold_for_checkout(
            db,
            metadata={"slot_hold_token": "quote-hold-token"},
            booking=booking,
        )

        self.assertIs(consumed, hold)
        self.assertEqual(hold.status, "consumed")
        self.assertEqual(hold.booking_id, booking_id)
        events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "hold_consumed")
        self.assertEqual(events[0].hold_token, "quote-hold-token")

        repeated_db = _FakeSession([hold])
        repeated = await _consume_slot_hold_for_checkout(
            repeated_db,
            metadata={"slot_hold_token": "quote-hold-token"},
            booking=booking,
        )
        self.assertIs(repeated, hold)
        self.assertEqual(
            [row for row in repeated_db.added if isinstance(row, BookingLifecycleEvent)],
            [],
        )

    async def test_checkout_terminal_event_expires_active_quote_slot_hold(self):
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="quote-hold-token",
            artist_id=uuid.uuid4(),
            artist_slug="justin",
            scheduled_date=_next_weekday(4),
            scheduled_time="11:00",
            duration_minutes=120,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

        expired = await _expire_slot_hold_for_checkout(
            _FakeSession([hold]),
            metadata={"slot_hold_token": "quote-hold-token"},
        )

        self.assertIs(expired, hold)
        self.assertEqual(hold.status, "expired")

    async def test_quote_proposal_checkout_completion_consumes_slot_hold(self):
        booking_id = uuid.uuid4()
        booking = Booking(
            id=booking_id,
            artist_id=uuid.uuid4(),
            status=BookingStatus.QUOTED,
            guest_name="Client Name",
            deposit_paid=False,
        )
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="proposal-hold-token",
            artist_id=booking.artist_id,
            artist_slug="justin",
            scheduled_date=_next_weekday(4),
            scheduled_time="11:00",
            duration_minutes=120,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        message = type(
            "FakeMessage",
            (),
            {
                "id": uuid.uuid4(),
                "artifact_state": {"status": "awaiting_payment"},
                "updated_at": None,
            },
        )()
        fake_db = _WebhookSession([[booking], [hold], [message], []])

        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=fake_db),
            patch("app.api.v1.endpoints.stripe_webhooks.send_email", new_callable=AsyncMock),
            patch(
                "app.api.v1.endpoints.stripe_webhooks._sync_hustl_deposit_task_with_visibility",
                new_callable=AsyncMock,
            ),
        ):
            await _handle_quote_proposal_checkout_completed(
                {
                    "id": "cs_test_quote_proposal",
                    "payment_intent": "pi_test_quote_proposal",
                    "amount_total": 5000,
                    "metadata": {
                        "booking_id": str(booking_id),
                        "artifact_message_id": str(message.id),
                        "slot_hold_token": "proposal-hold-token",
                    },
                }
            )

        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertTrue(booking.deposit_paid)
        self.assertEqual(hold.status, "consumed")
        self.assertEqual(hold.booking_id, booking_id)
        self.assertEqual(message.artifact_state["status"], "confirmed")
        events = [row for row in fake_db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual([event.event_type for event in events], ["hold_consumed", "quote_checkout_completed"])
        self.assertGreaterEqual(fake_db.commits, 1)

    async def test_duplicate_quote_proposal_checkout_completion_still_consumes_slot_hold(self):
        booking_id = uuid.uuid4()
        booking = Booking(
            id=booking_id,
            artist_id=uuid.uuid4(),
            status=BookingStatus.CONFIRMED,
            deposit_paid=True,
            stripe_checkout_session_id="cs_duplicate",
        )
        hold = BookingSlotHold(
            id=uuid.uuid4(),
            hold_token="proposal-hold-token",
            artist_id=booking.artist_id,
            artist_slug="justin",
            scheduled_date=_next_weekday(4),
            scheduled_time="11:00",
            duration_minutes=120,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        fake_db = _WebhookSession([[booking], [hold]])

        with patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=fake_db):
            await _handle_quote_proposal_checkout_completed(
                {
                    "id": "cs_duplicate",
                    "payment_intent": "pi_duplicate",
                    "metadata": {
                        "booking_id": str(booking_id),
                        "slot_hold_token": "proposal-hold-token",
                    },
                }
            )

        self.assertEqual(hold.status, "consumed")
        self.assertEqual(hold.booking_id, booking_id)
        self.assertEqual(fake_db.commits, 1)
        events = [row for row in fake_db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual([event.event_type for event in events], ["hold_consumed"])

    async def test_quote_proposal_checkout_duplicate_provider_event_stops_before_transition(self):
        booking = _deposit_booking()
        fake_db = _WebhookSession([[booking], [uuid.uuid4()]])

        with patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=fake_db):
            await _handle_quote_proposal_checkout_completed(
                {
                    "id": "cs_quote_duplicate_event",
                    "payment_intent": "pi_quote_duplicate_event",
                    "amount_total": 20000,
                    "metadata": {"booking_id": str(booking.id)},
                },
                provider_event_id="evt_quote_duplicate",
            )

        self.assertEqual(booking.status, BookingStatus.QUOTED)
        self.assertFalse(booking.deposit_paid)
        self.assertEqual(fake_db.commits, 0)
        self.assertEqual(fake_db.added, [])

    async def test_public_checkout_duplicate_provider_event_stops_before_transition(self):
        booking = _deposit_booking()
        fake_db = _WebhookSession([[booking], [uuid.uuid4()]])

        with patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=fake_db):
            await _handle_public_booking_checkout_completed(
                {
                    "id": "cs_public_duplicate_event",
                    "payment_intent": "pi_public_duplicate_event",
                    "amount_total": 20000,
                    "metadata": {"booking_id": str(booking.id)},
                },
                provider_event_id="evt_public_duplicate",
            )

        self.assertEqual(booking.status, BookingStatus.QUOTED)
        self.assertFalse(booking.deposit_paid)
        self.assertEqual(fake_db.commits, 0)
        self.assertEqual(fake_db.added, [])

    async def test_public_checkout_calendar_event_invites_the_client(self):
        booking = _deposit_booking()
        booking.scheduled_date = _next_weekday(4)
        booking.scheduled_time = "11:00"
        booking.duration_minutes = 120
        artist = User(
            id=booking.artist_id,
            email="artist@example.test",
            password_hash="disabled",
            name="Artist Name",
            role="artist",
            slug="artist",
            google_refresh_token="test-refresh",
        )
        client_email = "collector@example.test"
        fake_db = _WebhookSession([[booking], [], [artist], [(client_email, "Collector Name")]])
        calendar_mock = AsyncMock(return_value={"event_id": "event-test"})

        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=fake_db),
            patch("app.api.v1.endpoints.stripe_webhooks.artist_calendar_connected", return_value=True),
            patch("app.api.v1.endpoints.stripe_webhooks.create_booking_event", new=calendar_mock),
            patch("app.api.v1.endpoints.stripe_webhooks.should_notify", new_callable=AsyncMock, return_value=True),
            patch("app.api.v1.endpoints.stripe_webhooks.send_email", new_callable=AsyncMock),
        ):
            await _handle_public_booking_checkout_completed(
                {
                    "id": "cs_public_attendee",
                    "payment_intent": "pi_public_attendee",
                    "amount_total": 20000,
                    "metadata": {"booking_id": str(booking.id)},
                },
                provider_event_id="evt_public_attendee",
            )

        calendar_mock.assert_awaited_once()
        self.assertEqual(calendar_mock.await_args.args[0]["client_email"], client_email)

    async def test_public_checkout_concurrent_provider_claim_collision_rolls_back(self):
        booking = _deposit_booking()
        fake_db = _ConcurrentWebhookSession([[booking], []])

        with patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=fake_db):
            await _handle_public_booking_checkout_completed(
                {
                    "id": "cs_public_concurrent",
                    "payment_intent": "pi_public_concurrent",
                    "amount_total": 20000,
                    "metadata": {"booking_id": str(booking.id)},
                },
                provider_event_id="evt_public_concurrent",
            )

        self.assertEqual(booking.status, BookingStatus.QUOTED)
        self.assertFalse(booking.deposit_paid)
        self.assertEqual(fake_db.rollbacks, 1)
        self.assertEqual(fake_db.commits, 0)
        self.assertEqual(fake_db.added, [])

    async def test_unsigned_stripe_webhook_is_rejected_outside_local_dev(self):
        payload = json.dumps({"id": "evt_unsigned", "type": "unknown.event", "data": {"object": {}}}).encode()
        settings = _stripe_test_settings(local_dev=False, app_env="production")

        with (
            patch("app.api.v1.endpoints.stripe_webhooks.get_settings", return_value=settings),
            self.assertRaises(HTTPException) as err,
        ):
            await stripe_webhook(_WebhookRequest(payload))

        self.assertEqual(err.exception.status_code, 503)

    async def test_unsigned_stripe_webhook_remains_available_in_local_dev(self):
        payload = json.dumps({"id": "evt_local", "type": "unknown.event", "data": {"object": {}}}).encode()
        settings = _stripe_test_settings(local_dev=True, app_env="development")

        with patch("app.api.v1.endpoints.stripe_webhooks.get_settings", return_value=settings):
            response = await stripe_webhook(_WebhookRequest(payload))

        self.assertEqual(response, {"received": True})

    async def test_signed_stripe_v15_objects_are_normalized_for_every_event_family(self):
        settings = _stripe_test_settings(
            local_dev=False,
            app_env="production",
            webhook_secret="whsec_test_redacted",
        )
        event_families = (
            ("checkout.session.async_payment_failed", "_handle_checkout_event"),
            ("payment_intent.amount_capturable_updated", "_handle_payment_intent_event"),
            ("charge.succeeded", "_handle_charge_event"),
            ("customer.updated", "_handle_customer_event"),
            ("refund.updated", "_handle_refund_event"),
            ("invoice.finalized", "_handle_invoice_event"),
            ("payout.paid", "_handle_connect_event"),
        )

        for event_type, handler_name in event_families:
            stripe_event = stripe_webhooks_endpoint.stripe.Event.construct_from(
                {
                    "id": f"evt_{event_type.replace('.', '_')}",
                    "type": event_type,
                    "data": {"object": {"id": "obj_v15", "metadata": {"source": "test"}}},
                },
                settings.STRIPE_SECRET_KEY,
            )
            with (
                self.subTest(event_type=event_type),
                patch("app.api.v1.endpoints.stripe_webhooks.get_settings", return_value=settings),
                patch(
                    "app.api.v1.endpoints.stripe_webhooks.stripe.Webhook.construct_event",
                    return_value=stripe_event,
                ),
                patch(
                    f"app.api.v1.endpoints.stripe_webhooks.{handler_name}",
                    new_callable=AsyncMock,
                ) as handler,
            ):
                response = await stripe_webhook(_WebhookRequest(b"{}", {"stripe-signature": "signed"}))

            self.assertEqual(response, {"received": True})
            normalized_object = handler.await_args.args[1]
            self.assertIs(type(normalized_object), dict)
            self.assertEqual(normalized_object["id"], "obj_v15")
            self.assertEqual(normalized_object["metadata"], {"source": "test"})

    async def test_payment_intent_success_records_once_and_uses_real_huddl_names(self):
        booking = _deposit_booking("pi_success")
        first_db = _named_webhook_session(booking)

        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=first_db),
            patch(
                "app.api.v1.endpoints.stripe_webhooks._sync_hustl_deposit_followup_task",
                new_callable=AsyncMock,
            ) as sync_mock,
        ):
            await _handle_payment_intent_succeeded(
                {"id": "pi_success", "status": "succeeded"},
                provider_event_id="evt_success_once",
            )

        self.assertTrue(booking.deposit_paid)
        self.assertEqual(first_db.commits, 1)
        event = next(row for row in first_db.added if isinstance(row, BookingLifecycleEvent))
        self.assertEqual(event.provider_event_id, "evt_success_once")
        self.assertEqual(sync_mock.await_args.kwargs["client_name"], "Collector Name")
        self.assertEqual(sync_mock.await_args.kwargs["artist_name"], "Justin Walker")

        duplicate_db = _WebhookSession([[booking], [uuid.uuid4()]])
        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=duplicate_db),
            patch(
                "app.api.v1.endpoints.stripe_webhooks._sync_hustl_deposit_followup_task",
                new_callable=AsyncMock,
            ) as duplicate_sync,
        ):
            await _handle_payment_intent_succeeded(
                {"id": "pi_success", "status": "succeeded"},
                provider_event_id="evt_success_once",
            )

        duplicate_sync.assert_not_awaited()
        self.assertEqual(duplicate_db.commits, 0)
        self.assertFalse(duplicate_db.added)

    async def test_failed_payment_intent_processing_keeps_event_retryable(self):
        booking = _deposit_booking("pi_retry")
        failed_db = _named_webhook_session(booking)
        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=failed_db),
            patch(
                "app.api.v1.endpoints.stripe_webhooks._sync_hustl_deposit_followup_task",
                new_callable=AsyncMock,
                side_effect=RuntimeError("provider payload secret should not persist"),
            ),
            self.assertRaises(RuntimeError),
        ):
            await _handle_payment_intent_succeeded(
                {"id": "pi_retry", "status": "succeeded"},
                provider_event_id="evt_retryable",
            )

        self.assertEqual(failed_db.rollbacks, 1)
        failure_event = next(row for row in failed_db.added if isinstance(row, BookingLifecycleEvent))
        self.assertEqual(failure_event.event_type, "deposit_task_sync_failed")
        self.assertIsNone(failure_event.provider_event_id)
        self.assertEqual(failure_event.details["error_class"], "RuntimeError")
        self.assertNotIn("secret", json.dumps(failure_event.details))

        retry_db = _named_webhook_session(booking)
        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=retry_db),
            patch(
                "app.api.v1.endpoints.stripe_webhooks._sync_hustl_deposit_followup_task",
                new_callable=AsyncMock,
            ),
        ):
            await _handle_payment_intent_succeeded(
                {"id": "pi_retry", "status": "succeeded"},
                provider_event_id="evt_retryable",
            )

        retry_event = next(row for row in retry_db.added if isinstance(row, BookingLifecycleEvent))
        self.assertEqual(retry_event.provider_event_id, "evt_retryable")
        self.assertEqual(retry_db.commits, 1)

    async def test_payment_failure_evidence_redacts_nested_provider_payload(self):
        booking = _deposit_booking("pi_failed")
        db = _named_webhook_session(booking)
        last_error = {
            "code": "card_declined",
            "decline_code": "insufficient_funds",
            "message": "Sensitive provider message",
            "payment_method": {"billing_details": {"name": "Private Name"}},
        }
        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=db),
            patch(
                "app.api.v1.endpoints.stripe_webhooks._sync_hustl_deposit_followup_task",
                new_callable=AsyncMock,
            ),
        ):
            await _handle_payment_intent_terminal_failure(
                "payment_intent.payment_failed",
                {"id": "pi_failed", "status": "requires_payment_method", "last_payment_error": last_error},
                provider_event_id="evt_failed_redacted",
            )

        event = next(row for row in db.added if isinstance(row, BookingLifecycleEvent))
        self.assertEqual(event.details["error_code"], "card_declined")
        self.assertEqual(event.details["decline_code"], "insufficient_funds")
        serialized = json.dumps(event.details)
        self.assertNotIn("Sensitive provider message", serialized)
        self.assertNotIn("Private Name", serialized)
        self.assertNotIn("last_payment_error", event.details)

    async def test_checkout_expiration_syncs_huddl_with_real_names(self):
        booking = _deposit_booking()
        # Rows: booking, slot hold, newer open quote (none), artist name, client.
        db = _WebhookSession([[booking], [], [], ["Justin Walker"], [("Collector Name", "collector@example.test")]])
        with (
            patch("app.api.v1.endpoints.stripe_webhooks.async_session", return_value=db),
            patch(
                "app.api.v1.endpoints.stripe_webhooks._sync_hustl_deposit_followup_task",
                new_callable=AsyncMock,
            ) as sync_mock,
        ):
            await _handle_checkout_expired(
                {
                    "id": "cs_expired",
                    "payment_intent": "pi_expired",
                    "metadata": {"booking_id": str(booking.id)},
                },
                provider_event_id="evt_expired",
            )

        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        self.assertEqual(sync_mock.await_args.kwargs["client_name"], "Collector Name")
        self.assertEqual(sync_mock.await_args.kwargs["artist_name"], "Justin Walker")
        event = next(row for row in db.added if isinstance(row, BookingLifecycleEvent))
        self.assertEqual(event.provider_event_id, "evt_expired")

    async def test_calendar_import_match_prefers_same_time_and_skips_wrong_same_day_booking(self):
        artist_id = uuid.uuid4()
        scheduled_date = date(2026, 7, 18)
        early = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            guest_name="Client Name",
            scheduled_date=scheduled_date,
            scheduled_time="11:00",
            reference_code="BKG-EARLY",
        )
        late = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            guest_name="Client Name",
            scheduled_date=scheduled_date,
            scheduled_time="15:00",
            reference_code="BKG-LATE",
        )

        match = await _find_existing_calendar_booking(
            _FakeSession([early, late]),
            artist_id=artist_id,
            guest_name="client  name",
            scheduled_date=scheduled_date,
            scheduled_time="15:00",
        )

        self.assertEqual(match.id, late.id)

    async def test_calendar_import_reconciles_sparse_duplicate_to_native_booking(self):
        artist_id = uuid.uuid4()
        scheduled_date = date(2026, 7, 18)
        native = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            guest_name="Client Name",
            scheduled_date=scheduled_date,
            scheduled_time="15:00",
            reference_code="BKG-NATIVE",
            status=BookingStatus.QUOTED,
            source="bttco",
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        imported_duplicate = Booking(
            id=uuid.uuid4(),
            artist_id=artist_id,
            guest_name="Client Name",
            scheduled_date=scheduled_date,
            scheduled_time="15:00",
            google_calendar_event_id="gcal-1",
            reference_code=None,
            status=BookingStatus.CONFIRMED,
            source="google_calendar",
            artist_notes="Imported from Google Calendar",
            created_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
        user = User(
            id=artist_id,
            email="founder@example.com",
            name="Founder",
            role="founder",
            google_refresh_token="refresh-token",
        )
        fake_db = _CalendarImportSession(
            [
                [imported_duplicate],
                [native, imported_duplicate],
            ]
        )

        with patch.object(
            calendar_endpoint,
            "get_upcoming_events",
            new_callable=AsyncMock,
            return_value=[
                {
                    "id": "gcal-1",
                    "summary": "Client Name: Tattoo Session (Justin)",
                    "start": "2026-07-18T15:00:00-04:00",
                    "end": "2026-07-18T17:00:00-04:00",
                    "location": "Ryozan Studio",
                }
            ],
        ):
            result = await import_calendar_events(days=30, current_user=user, db=fake_db)

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["reconciled"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(native.google_calendar_event_id, "gcal-1")
        self.assertEqual(imported_duplicate.google_calendar_event_id, None)
        self.assertEqual(imported_duplicate.status, BookingStatus.EXPIRED)
        self.assertIn("Superseded by native booking BKG-NATIVE", imported_duplicate.artist_notes)
        self.assertEqual(fake_db.added, [])
        self.assertEqual(fake_db.commits, 1)



    def test_custom_artist_working_hours_override_default_schedule(self):
        artist = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Custom Artist",
            role="artist",
            slug="custom-artist",
            nexus_profile={
                "working_hours": {
                    "0": [10, 16],
                    "2": None,
                }
            },
        )
        self.assertEqual(artist.working_hours, {"0": [10, 16], "2": None})
        schedule = calendar_endpoint._resolve_artist_work_schedule(artist)
        self.assertEqual(schedule[0], (10, 16))
        self.assertIsNone(schedule[2])


if __name__ == "__main__":
    unittest.main()
