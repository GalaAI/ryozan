"""Focused release-policy, saga, and projection tests for Booky readiness.

Net-new artifact justification:
Existing scheduler tests prove the generic Calendar and booking lifecycle paths,
but no test owner covers Booky's account-bound authority or recovery contract.
These tests exercise that boundary while reusing the canonical service owners.
"""

import inspect
import json
import unittest
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.api.v1.endpoints import email_log as email_log_endpoint
from app.api.v1.endpoints.bookings import (
    _booky_projection,
    _booky_retry_blockers,
    _collector_identity_verified,
    _run_booky_confirmation_saga,
    _verify_existing_inquiry_capability,
)
from app.constants.status import BookingStatus
from app.core.config import Settings
from app.core.security import create_booky_capability_token, decode_booky_capability_token
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.email_log import EmailLog
from app.models.message import Message
from app.schemas.booking import BookyDecisionEnvelope, BookySettingsUpdate, PublicBookingSubmit
from app.services import calendar_sync
from app.services import email as email_service
from app.services.booky_policy import booky_action_id, resolve_booky_policy
from app.services.session_type_defaults import DEFAULT_SESSION_TYPE_SPECS, missing_default_session_rows


def _settings(**overrides):
    values = {
        "BOOKY_AUTOMATION_ENABLED": True,
        "BOOKY_POLICY_VERSION": "2026-08-20.v1",
        "BOOKY_CANARY_ARTIST_SLUGS": "justin",
        "BOOKY_CANARY_COLLECTOR_IDS": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _artist(mode="manual", *, opted_in=True):
    return SimpleNamespace(
        id=uuid4(),
        slug="justin",
        email="artist@example.test",
        name="Justin",
        role="artist",
        booky_mode=mode,
        booky_opted_in_at=datetime.now(UTC) if opted_in else None,
        google_refresh_token="refresh",
        google_calendar_id="calendar",
        availability_source="google_calendar",
        booking_buffer_enabled=False,
        booking_buffer_minutes=0,
        booking_min_notice_hours=0,
        booking_max_advance_days=365,
        booking_allow_same_day=True,
        booking_slot_interval_minutes=30,
    )


def _resolve(mode, **overrides):
    artist = overrides.pop("artist", _artist(mode))
    collector_id = overrides.pop("collector_id", uuid4())
    settings = overrides.pop(
        "settings",
        _settings(BOOKY_CANARY_COLLECTOR_IDS=str(collector_id)),
    )
    values = {
        "settings": settings,
        "artist": artist,
        "booking_kind": "consultation",
        "collector_id": collector_id,
        "collector_identity_verified": True,
        "consent_granted": True,
        "consent_policy_version": settings.BOOKY_POLICY_VERSION,
        "slot_hold_verified": True,
        "calendar_connected": True,
    }
    values.update(overrides)
    return resolve_booky_policy(**values)


class _Result:
    def __init__(self, rows=None, scalar=None):
        self.rows = list(rows or [])
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        if self.scalar is not None:
            return self.scalar
        return self.rows[0] if self.rows else None

    def scalar_one(self):
        return self.scalar if self.scalar is not None else self.rows[0]


class _ResendRequest:
    def __init__(self, event_type: str, email_id: str):
        self.headers = {}
        self._payload = {"type": event_type, "data": {"email_id": email_id}}

    async def body(self):
        return json.dumps(self._payload).encode()

    async def json(self):
        return self._payload


def _booking(*, status=BookingStatus.PENDING, method="meet"):
    now = datetime.now(UTC)
    return Booking(
        id=uuid4(),
        artist_id=uuid4(),
        client_id=uuid4(),
        status=status,
        description="Consultation request",
        booking_kind="consultation",
        consultation_method=method,
        reference_code="BKG-BOOKY",
        scheduled_date=date(2026, 8, 28),
        scheduled_time="10:00",
        duration_minutes=30,
        updated_at=now,
        created_at=now,
    )


class BookyPolicyTests(unittest.TestCase):
    def test_consent_snapshot_requires_matching_fields(self):
        base = {
            "artist_slug": "justin",
            "first_name": "Founder",
            "last_name": "Canary",
            "email": "collector@example.test",
            "description": "A sufficiently detailed booking request",
        }
        with self.assertRaises(ValidationError):
            PublicBookingSubmit(**base, booky_consent=True)
        with self.assertRaises(ValidationError):
            PublicBookingSubmit(**base, booky_consent_policy_version="v1")

    def test_modes_are_deterministic_and_session_auto_is_disabled(self):
        manual = _resolve("manual")
        self.assertEqual((manual.effective_mode, manual.truth_state), ("manual", "empty"))
        self.assertEqual(_resolve("shadow").effective_mode, "shadow")
        self.assertEqual(_resolve("approval").truth_state, "approval_needed")
        self.assertTrue(_resolve("consult_auto").can_auto_confirm)
        session = _resolve("session_auto")
        self.assertFalse(session.can_auto_confirm)
        self.assertIn("session_auto_disabled", session.blocked_reasons)

    def test_positive_config_identity_and_collector_canary_fail_closed(self):
        collector_id = uuid4()
        disabled = _resolve(
            "consult_auto",
            collector_id=collector_id,
            settings=_settings(
                BOOKY_AUTOMATION_ENABLED=False,
                BOOKY_CANARY_COLLECTOR_IDS=str(collector_id),
            ),
        )
        unverified = _resolve("consult_auto", collector_identity_verified=False)
        not_canary = _resolve("consult_auto", settings=_settings(BOOKY_CANARY_COLLECTOR_IDS=""))
        self.assertIn("automation_disabled", disabled.blocked_reasons)
        self.assertIn("collector_identity_required", unverified.blocked_reasons)
        self.assertIn("collector_not_in_canary", not_canary.blocked_reasons)
        self.assertFalse(disabled.can_auto_confirm)
        self.assertFalse(unverified.can_auto_confirm)
        self.assertFalse(not_canary.can_auto_confirm)

    def test_only_explicit_collector_uuid_is_accepted(self):
        founder_id = uuid4()
        other_id = uuid4()
        settings = _settings(BOOKY_CANARY_COLLECTOR_IDS=str(founder_id))
        self.assertTrue(_resolve("consult_auto", collector_id=founder_id, settings=settings).can_auto_confirm)
        blocked = _resolve("consult_auto", collector_id=other_id, settings=settings)
        self.assertIn("collector_not_in_canary", blocked.blocked_reasons)

    def test_shadow_and_approval_require_verified_capability(self):
        for mode in ("shadow", "approval"):
            decision = _resolve(mode, collector_identity_verified=False)
            self.assertEqual(decision.effective_mode, "manual")
            self.assertIn("collector_identity_required", decision.blocked_reasons)

    def test_collector_canary_configuration_rejects_non_uuids(self):
        with self.assertRaises(ValueError):
            Settings(BOOKY_CANARY_COLLECTOR_IDS="not-an-account-id")

    def test_public_capability_is_bound_to_account_and_inquiry(self):
        account_id = uuid4()
        other_id = uuid4()
        token = create_booky_capability_token(account_id, "inquiry-123")
        self.assertEqual(decode_booky_capability_token(token, "inquiry-123"), account_id)
        with self.assertRaises(ValueError):
            decode_booky_capability_token(token, "different-inquiry")
        self.assertTrue(_collector_identity_verified(account_id, account_id))
        with self.assertRaises(HTTPException) as mismatch:
            _collector_identity_verified(account_id, other_id)
        self.assertEqual(mismatch.exception.status_code, 403)
        self.assertFalse(_collector_identity_verified(None, account_id))
        existing = _booking()
        existing.client_id = other_id
        with self.assertRaises(HTTPException) as replay:
            _verify_existing_inquiry_capability(account_id, existing)
        self.assertEqual(replay.exception.status_code, 403)

    def test_public_submit_contains_no_ai_gateway_path(self):
        source = inspect.getsource(bookings_endpoint._do_submit_public_booking)
        self.assertNotIn("call_ark_gateway", source)
        self.assertNotIn("enrich_staff_recommendation", source)

    def test_action_id_is_stable(self):
        artist_id = uuid4()
        kwargs = dict(artist_id=artist_id, inquiry_id="inquiry", hold_token="hold", policy_version="v1")
        self.assertEqual(booky_action_id(**kwargs), booky_action_id(**kwargs))


class BookySettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_modes_transition_sequentially_and_preserve_first_opt_in(self):
        artist = _artist("manual", opted_in=False)
        founder = SimpleNamespace(id=uuid4(), role="founder")
        added = []
        db = SimpleNamespace(
            get=AsyncMock(return_value=artist),
            add=added.append,
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        with patch.object(bookings_endpoint, "get_settings", return_value=_settings()):
            with self.assertRaises(HTTPException) as skipped:
                await bookings_endpoint.update_booky_settings(
                    artist.id, BookySettingsUpdate(mode="approval"), db=db, current_user=founder
                )
            self.assertEqual(skipped.exception.status_code, 409)
            for mode in ("shadow", "approval", "consult_auto", "approval", "shadow", "manual"):
                await bookings_endpoint.update_booky_settings(
                    artist.id, BookySettingsUpdate(mode=mode), db=db, current_user=founder
                )

        self.assertEqual(artist.booky_mode, "manual")
        self.assertIsNotNone(artist.booky_opted_in_at)
        first_opt_in = artist.booky_opted_in_at
        self.assertTrue(all(event.details["consent_evidence_retained"] for event in added))
        self.assertTrue(all(event.details["initial_opt_in_at"] == first_opt_in.isoformat() for event in added))
        self.assertEqual([event.event_type for event in added], ["booky_mode_transition"] * 6)


class BookySagaTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_requires_durable_intent_and_invites_only_after_meet(self):
        booking = _booking()
        artist = _artist("consult_auto")
        conversation = SimpleNamespace(id=uuid4())
        create_call = AsyncMock(
            return_value={"event_id": "calendar-event", "conference_url": "https://meet.example.test/room"}
        )
        update_call = AsyncMock(
            return_value={"event_id": "calendar-event", "conference_url": "https://meet.example.test/room"}
        )
        missing_db = SimpleNamespace(execute=AsyncMock(return_value=_Result()), get=AsyncMock())
        with (
            patch.object(bookings_endpoint, "create_booking_event", create_call),
            patch.object(bookings_endpoint, "update_booking_event", update_call),
            self.assertRaises(RuntimeError),
        ):
            await _run_booky_confirmation_saga(
                missing_db,
                booking=booking,
                artist=artist,
                conversation=conversation,
                client_name="Collector",
                client_email="collector@example.test",
                action_id="booky-action",
                hold_token="hold",
                decision_details={},
                attempt=0,
                intent_provider_event_id="intent",
            )
        create_call.assert_not_awaited()

        results = [_Result(scalar=uuid4()), *[_Result() for _ in range(5)]]
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=results),
            get=AsyncMock(return_value=None),
            add=MagicMock(),
            flush=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        create_call.reset_mock()
        with (
            patch.object(bookings_endpoint, "_require_durable_booky_intent", new=AsyncMock()),
            patch.object(bookings_endpoint, "_booky_receipt_by_provider_id", new=AsyncMock(return_value=None)),
            patch.object(bookings_endpoint, "_add_booky_receipt_once", new=AsyncMock()),
            patch.object(bookings_endpoint, "create_booking_event", create_call),
            patch.object(bookings_endpoint, "update_booking_event", update_call),
        ):
            confirmed, conference_url, message = await _run_booky_confirmation_saga(
                db,
                booking=booking,
                artist=artist,
                conversation=conversation,
                client_name="Collector",
                client_email="collector@example.test",
                action_id="booky-action",
                hold_token="hold",
                decision_details={},
                attempt=0,
                intent_provider_event_id="intent",
            )

        self.assertTrue(confirmed)
        self.assertEqual(conference_url, "https://meet.example.test/room")
        self.assertIsInstance(message, Message)
        self.assertNotIn("client_email", create_call.await_args.args[0])
        self.assertEqual(update_call.await_args.args[1]["client_email"], "collector@example.test")
        self.assertEqual(db.commit.await_count, 3)
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

    async def test_meet_failure_leaves_booking_pending_and_recoverable(self):
        booking = _booking()
        db = SimpleNamespace(
            execute=AsyncMock(),
            add=MagicMock(),
            commit=AsyncMock(),
        )
        with (
            patch.object(bookings_endpoint, "_require_durable_booky_intent", new=AsyncMock()),
            patch.object(bookings_endpoint, "_booky_receipt_by_provider_id", new=AsyncMock(return_value=None)),
            patch.object(bookings_endpoint, "_add_booky_receipt_once", new=AsyncMock()),
            patch.object(
                bookings_endpoint,
                "create_booking_event",
                new=AsyncMock(return_value={"event_id": "calendar-event", "conference_url": None}),
            ),
        ):
            confirmed, _, message = await _run_booky_confirmation_saga(
                db,
                booking=booking,
                artist=_artist("consult_auto"),
                conversation=SimpleNamespace(id=uuid4()),
                client_name="Collector",
                client_email="collector@example.test",
                action_id="booky-meet-failure",
                hold_token="hold",
                decision_details={},
                attempt=0,
                intent_provider_event_id="intent",
            )
        self.assertFalse(confirmed)
        self.assertIsNone(message)
        self.assertEqual(booking.status, BookingStatus.PENDING)
        self.assertEqual(booking.google_calendar_event_id, "calendar-event")

    async def test_invitation_receipt_resumes_without_resending_invite(self):
        booking = _booking()
        artist = _artist("consult_auto")
        calendar_receipt = SimpleNamespace(details={"calendar_event_id": "calendar-event"})
        invitation_receipt = SimpleNamespace(details={"invitation_state": "sent"})

        async def receipt(_db, provider_event_id):
            if provider_event_id.endswith("calendar-ready"):
                return calendar_receipt
            if provider_event_id.endswith(":invitation"):
                return invitation_receipt
            return None

        db = SimpleNamespace(
            get=AsyncMock(return_value=None),
            add=MagicMock(),
            flush=AsyncMock(),
            commit=AsyncMock(),
            rollback=AsyncMock(),
            execute=AsyncMock(),
        )
        provider_state = {
            "event_id": "calendar-event",
            "conference_url": "https://meet.example.test/resume",
            "attendee_emails": ["collector@example.test"],
        }
        create_call = AsyncMock()
        update_call = AsyncMock()
        with (
            patch.object(bookings_endpoint, "_require_durable_booky_intent", new=AsyncMock()),
            patch.object(bookings_endpoint, "_booky_receipt_by_provider_id", side_effect=receipt),
            patch.object(bookings_endpoint, "_add_booky_receipt_once", new=AsyncMock()),
            patch.object(bookings_endpoint, "get_booking_event_state", new=AsyncMock(return_value=provider_state)),
            patch.object(bookings_endpoint, "create_booking_event", create_call),
            patch.object(bookings_endpoint, "update_booking_event", update_call),
        ):
            confirmed, _, _ = await _run_booky_confirmation_saga(
                db,
                booking=booking,
                artist=artist,
                conversation=SimpleNamespace(id=uuid4()),
                client_name="Collector",
                client_email="collector@example.test",
                action_id="booky-resume",
                hold_token="hold",
                decision_details={},
                attempt=1,
                intent_provider_event_id="intent",
            )
        self.assertTrue(confirmed)
        create_call.assert_not_awaited()
        update_call.assert_not_awaited()

    async def test_calendar_commit_failure_rolls_back_reconciles_and_records_failure(self):
        booking = _booking()
        added = AsyncMock()
        db = SimpleNamespace(
            get=AsyncMock(return_value=booking),
            add=MagicMock(),
            flush=AsyncMock(),
            commit=AsyncMock(side_effect=[RuntimeError("calendar commit"), None]),
            rollback=AsyncMock(),
            execute=AsyncMock(),
        )
        with (
            patch.object(bookings_endpoint, "_require_durable_booky_intent", new=AsyncMock()),
            patch.object(bookings_endpoint, "_booky_receipt_by_provider_id", new=AsyncMock(return_value=None)),
            patch.object(bookings_endpoint, "_add_booky_receipt_once", added),
            patch.object(
                bookings_endpoint,
                "create_booking_event",
                new=AsyncMock(return_value={"event_id": "calendar-event", "conference_url": "meet-url"}),
            ),
            patch.object(
                bookings_endpoint,
                "get_booking_event_state",
                new=AsyncMock(
                    return_value={
                        "event_id": "calendar-event",
                        "conference_url": "meet-url",
                        "attendee_emails": [],
                    }
                ),
            ),
        ):
            confirmed, _, _ = await _run_booky_confirmation_saga(
                db,
                booking=booking,
                artist=_artist("consult_auto"),
                conversation=SimpleNamespace(id=uuid4()),
                client_name="Collector",
                client_email="collector@example.test",
                action_id="booky-calendar-commit",
                hold_token="hold",
                decision_details={},
                attempt=0,
                intent_provider_event_id="intent",
            )
        self.assertFalse(confirmed)
        db.rollback.assert_awaited_once()
        event_types = [call.kwargs["event_type"] for call in added.await_args_list]
        self.assertEqual(
            event_types,
            ["booky_calendar_ready", "booky_calendar_reconciled", "booky_confirmation_failed"],
        )

    async def test_invitation_commit_failure_reconciles_attendee_without_resend(self):
        booking = _booking()
        booking.google_calendar_event_id = "calendar-event"
        calendar_receipt = SimpleNamespace(details={"calendar_event_id": "calendar-event"})

        async def receipt(_db, provider_event_id):
            return calendar_receipt if provider_event_id.endswith("calendar-ready") else None

        added = AsyncMock()
        db = SimpleNamespace(
            get=AsyncMock(return_value=booking),
            add=MagicMock(),
            flush=AsyncMock(),
            commit=AsyncMock(side_effect=[RuntimeError("invitation commit"), None]),
            rollback=AsyncMock(),
            execute=AsyncMock(),
        )
        state_call = AsyncMock(
            side_effect=[
                {"event_id": "calendar-event", "conference_url": "meet-url", "attendee_emails": []},
                {
                    "event_id": "calendar-event",
                    "conference_url": "meet-url",
                    "attendee_emails": ["collector@example.test"],
                },
            ]
        )
        update_call = AsyncMock(return_value={"event_id": "calendar-event", "conference_url": "meet-url"})
        with (
            patch.object(bookings_endpoint, "_require_durable_booky_intent", new=AsyncMock()),
            patch.object(bookings_endpoint, "_booky_receipt_by_provider_id", side_effect=receipt),
            patch.object(bookings_endpoint, "_add_booky_receipt_once", added),
            patch.object(bookings_endpoint, "get_booking_event_state", state_call),
            patch.object(bookings_endpoint, "update_booking_event", update_call),
        ):
            confirmed, _, _ = await _run_booky_confirmation_saga(
                db,
                booking=booking,
                artist=_artist("consult_auto"),
                conversation=SimpleNamespace(id=uuid4()),
                client_name="Collector",
                client_email="collector@example.test",
                action_id="booky-invite-commit",
                hold_token="hold",
                decision_details={},
                attempt=0,
                intent_provider_event_id="intent",
            )
        self.assertFalse(confirmed)
        update_call.assert_awaited_once()
        db.rollback.assert_awaited_once()
        event_types = [call.kwargs["event_type"] for call in added.await_args_list]
        self.assertEqual(
            event_types,
            ["booky_invitation_sent", "booky_invitation_reconciled", "booky_confirmation_failed"],
        )


class BookyProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_booking_projects_empty(self):
        booking = _booking()
        db = SimpleNamespace(execute=AsyncMock(return_value=_Result()))
        outcome = await _booky_projection(booking, db, staff=True)
        self.assertEqual(outcome.truth_state, "empty")
        self.assertEqual(outcome.receipt_ids, [])
        self.assertNotEqual(outcome.truth_state, "ready")

    async def test_collector_is_redacted_and_delivery_uses_email_log(self):
        booking = _booking(status=BookingStatus.CONFIRMED)
        now = datetime.now(UTC)
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=booking.artist_id,
            content="Confirmed",
            metadata_json='{"delivery_status":"sent","delivery_attempts":1}',
        )
        receipts = [
            BookingLifecycleEvent(
                id=uuid4(),
                booking_id=booking.id,
                artist_id=booking.artist_id,
                event_type="booky_recommendation",
                source="booky",
                summary="ready",
                details={
                    "action_id": "booky-projection",
                    "configured_mode": "consult_auto",
                    "effective_mode": "consult_auto",
                    "truth_state": "ready",
                    "recommendation": "Private staff action",
                    "explanation": "Policy details",
                    "confidence": "high",
                    "policy_version": "v1",
                    "risk": "low",
                    "required_approval": False,
                },
                created_at=now,
            ),
            BookingLifecycleEvent(
                id=uuid4(),
                booking_id=booking.id,
                artist_id=booking.artist_id,
                event_type="consultation_confirmed",
                source="booky",
                summary="confirmed",
                details={"conference_created": True, "invitation_state": "sent"},
                created_at=now + timedelta(seconds=1),
            ),
            BookingLifecycleEvent(
                id=uuid4(),
                booking_id=booking.id,
                artist_id=booking.artist_id,
                event_type="booky_delivery_intent",
                source="booky",
                summary="queued",
                details={"courier_message_id": str(message.id)},
                created_at=now + timedelta(seconds=2),
            ),
        ]
        email_log = EmailLog(
            id=uuid4(),
            to_email="collector@example.test",
            subject="Confirmed",
            template="booking-confirmation",
            status="sent",
            courier_message_id=message.id,
            courier_attempt=1,
            created_at=now + timedelta(seconds=3),
            updated_at=now + timedelta(seconds=3),
        )

        def projection_db():
            return SimpleNamespace(
                execute=AsyncMock(side_effect=[_Result(receipts), _Result(scalar=email_log)]),
                get=AsyncMock(return_value=message),
            )

        collector = await _booky_projection(booking, projection_db(), staff=False)
        staff = await _booky_projection(booking, projection_db(), staff=True)
        self.assertEqual(collector.truth_state, "completed")
        self.assertEqual(collector.courier_state, "sent")
        self.assertFalse(hasattr(collector, "action_id"))
        self.assertFalse(hasattr(collector, "receipt_ids"))
        self.assertFalse(hasattr(collector, "booking_id"))
        self.assertFalse(hasattr(collector, "recommendation"))
        self.assertIsInstance(staff, BookyDecisionEnvelope)
        self.assertEqual(staff.recommendation, "Private staff action")

    async def test_failed_email_is_retryable_after_confirmation(self):
        booking = _booking(status=BookingStatus.CONFIRMED, method="phone")
        now = datetime.now(UTC)
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=booking.artist_id,
            content="Confirmed",
            metadata_json='{"delivery_status":"failed","delivery_attempts":1}',
        )
        intent = BookingLifecycleEvent(
            id=uuid4(),
            booking_id=booking.id,
            artist_id=booking.artist_id,
            event_type="booky_delivery_intent",
            source="booky",
            summary="queued",
            details={"courier_message_id": str(message.id)},
            created_at=now,
        )
        email_log = EmailLog(
            id=uuid4(),
            to_email="collector@example.test",
            subject="Confirmed",
            template="booking-confirmation",
            status="failed",
            courier_message_id=message.id,
            courier_attempt=1,
            created_at=now,
            updated_at=now,
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result([intent]), _Result(scalar=email_log)]),
            get=AsyncMock(return_value=message),
        )
        outcome = await _booky_projection(booking, db, staff=False)
        self.assertEqual(outcome.truth_state, "failed")
        self.assertTrue(outcome.retry_eligible)

    async def test_current_nonstale_sending_attempt_ignores_older_failed_email_log(self):
        booking = _booking(status=BookingStatus.CONFIRMED, method="phone")
        now = datetime.now(UTC)
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=booking.artist_id,
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "sending",
                    "delivery_attempts": 2,
                    "delivery_attempt_started_at": now.isoformat(),
                    "delivery_updated_at": now.isoformat(),
                }
            ),
        )
        intent = BookingLifecycleEvent(
            id=uuid4(),
            booking_id=booking.id,
            artist_id=booking.artist_id,
            event_type="booky_delivery_intent",
            source="booky",
            summary="queued",
            details={"courier_message_id": str(message.id)},
            created_at=now - timedelta(minutes=20),
        )
        old_failure = EmailLog(
            id=uuid4(),
            to_email="collector@example.test",
            subject="Confirmed",
            template="booking-confirmation",
            status="failed",
            courier_message_id=message.id,
            courier_attempt=1,
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result([intent]), _Result(scalar=old_failure)]),
            get=AsyncMock(return_value=message),
        )

        outcome = await _booky_projection(booking, db, staff=True)

        self.assertEqual(outcome.courier_state, "sending")
        self.assertFalse(outcome.retry_eligible)
        self.assertNotIn(old_failure.id, outcome.receipt_ids)


class BookyRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_consumed_hold_does_not_hide_current_conflict(self):
        booking = _booking()
        artist = _artist("consult_auto")
        busy = [{"start": "2026-08-28T10:00:00-04:00", "end": "2026-08-28T11:00:00-04:00"}]
        with (
            patch.object(bookings_endpoint, "get_availability", new=AsyncMock(return_value=busy)),
            patch.object(
                bookings_endpoint,
                "_schedule_blocking_reasons",
                new=AsyncMock(return_value=["google_calendar_conflict"]),
            ) as blocker_call,
        ):
            blockers = await _booky_retry_blockers(SimpleNamespace(), booking=booking, artist=artist)
        self.assertEqual(blockers, ["google_calendar_conflict"])
        self.assertEqual(blocker_call.await_args.kwargs["busy_blocks"], busy)
        self.assertEqual(blocker_call.await_args.kwargs["exclude_booking_id"], booking.id)

    async def test_confirmed_retry_is_delivery_only(self):
        booking = _booking(status=BookingStatus.CONFIRMED, method="phone")
        artist = _artist("consult_auto")
        courier_message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=artist.id,
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "failed",
                    "delivery_attempts": email_service.BOOKY_COURIER_MAX_ATTEMPTS,
                    "delivery_staff_recovery_attempts": 0,
                    "delivery_to": "collector@example.test",
                }
            ),
        )
        failed = SimpleNamespace(
            courier_state="failed", action_id="booky-delivery", truth_state="failed", retry_eligible=True
        )
        delivered = SimpleNamespace(courier_state="delivered", action_id="booky-delivery", truth_state="completed")
        confirmation = SimpleNamespace(details={"conference_url": None})
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result(scalar=booking), _Result(scalar=0), _Result(scalar=confirmation)]),
            get=AsyncMock(return_value=artist),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        founder = SimpleNamespace(id=uuid4(), role="founder")
        receipts = AsyncMock()

        async def run_staff_recovery(*, message_id):
            self.assertEqual(message_id, courier_message.id)
            metadata = json.loads(courier_message.metadata_json)
            self.assertTrue(metadata["delivery_staff_override"])
            self.assertEqual(metadata["delivery_attempts"], email_service.BOOKY_COURIER_MAX_ATTEMPTS)
            metadata["delivery_status"] = "sent"
            metadata["delivery_attempts"] += 1
            metadata["delivery_staff_override"] = False
            courier_message.metadata_json = json.dumps(metadata)
            return 1

        with (
            patch.object(bookings_endpoint, "_booky_projection", new=AsyncMock(side_effect=[failed, delivered])),
            patch.object(bookings_endpoint, "_booky_delivery_message", new=AsyncMock(return_value=courier_message)),
            patch.object(bookings_endpoint, "_booking_contact", new=AsyncMock(return_value=("Collector", "c@test"))),
            patch.object(bookings_endpoint, "_add_booky_receipt_once", new=receipts),
            patch.object(
                bookings_endpoint, "process_booky_courier_outbox_once", new=AsyncMock(side_effect=run_staff_recovery)
            ) as send_call,
            patch.object(bookings_endpoint, "create_booking_event", new=AsyncMock()) as create_call,
            patch.object(bookings_endpoint, "update_booking_event", new=AsyncMock()) as update_call,
        ):
            result = await bookings_endpoint.retry_booky_confirmation(booking.id, db=db, current_user=founder)
        self.assertIs(result, delivered)
        send_call.assert_awaited_once()
        self.assertEqual(
            [call.kwargs["event_type"] for call in receipts.await_args_list],
            ["booky_delivery_retry_requested"],
        )
        create_call.assert_not_awaited()
        update_call.assert_not_awaited()

    async def test_active_retry_lease_blocks_a_second_provider_attempt(self):
        booking = _booking()
        active = BookingLifecycleEvent(
            id=uuid4(),
            booking_id=booking.id,
            artist_id=booking.artist_id,
            event_type="booky_retry_started",
            source="booky",
            summary="active",
            details={"retry_number": 1},
            created_at=datetime.now(UTC),
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=_Result([active])))
        with self.assertRaises(HTTPException) as conflict:
            await bookings_endpoint._claim_booky_retry_lease(
                db, booking=booking, action_id="booky-lease", hold_token="hold"
            )
        self.assertEqual(conflict.exception.status_code, 409)

    async def test_staff_retry_does_not_compete_with_nonstale_background_send(self):
        booking = _booking(status=BookingStatus.CONFIRMED, method="phone")
        artist = _artist("consult_auto")
        now = datetime.now(UTC)
        courier_message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=artist.id,
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "sending",
                    "delivery_attempts": email_service.BOOKY_COURIER_MAX_ATTEMPTS + 1,
                    "delivery_attempt_id": "booky:action:courier-attempt:6",
                    "delivery_attempt_started_at": now.isoformat(),
                    "delivery_updated_at": now.isoformat(),
                    "delivery_staff_recovery_attempts": 1,
                    "delivery_to": "collector@example.test",
                }
            ),
        )
        stale_projection = SimpleNamespace(
            courier_state="failed",
            action_id="booky-delivery-race",
            truth_state="failed",
            retry_eligible=True,
        )
        sending_projection = SimpleNamespace(
            courier_state="sending",
            action_id="booky-delivery-race",
            truth_state="ready",
            retry_eligible=False,
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result(scalar=booking)),
            get=AsyncMock(return_value=artist),
        )
        founder = SimpleNamespace(id=uuid4(), role="founder")
        receipts = AsyncMock()
        worker = AsyncMock()
        original_metadata = courier_message.metadata_json
        with (
            patch.object(
                bookings_endpoint,
                "_booky_projection",
                new=AsyncMock(side_effect=[stale_projection, sending_projection]),
            ),
            patch.object(
                bookings_endpoint,
                "_booky_delivery_message",
                new=AsyncMock(return_value=courier_message),
            ),
            patch.object(
                bookings_endpoint,
                "_booking_contact",
                new=AsyncMock(return_value=("Collector", "collector@example.test")),
            ),
            patch.object(bookings_endpoint, "_add_booky_receipt_once", new=receipts),
            patch.object(bookings_endpoint, "process_booky_courier_outbox_once", new=worker),
        ):
            result = await bookings_endpoint.retry_booky_confirmation(
                booking.id,
                db=db,
                current_user=founder,
            )

        self.assertIs(result, sending_projection)
        self.assertEqual(courier_message.metadata_json, original_metadata)
        worker.assert_not_awaited()
        receipts.assert_not_awaited()

    async def test_confirmed_delivery_staff_recovery_budget_is_bounded(self):
        booking = _booking(status=BookingStatus.CONFIRMED, method="phone")
        artist = _artist("consult_auto")
        courier_message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=artist.id,
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "failed",
                    "delivery_attempts": email_service.BOOKY_COURIER_MAX_ATTEMPTS,
                    "delivery_staff_recovery_attempts": (email_service.BOOKY_COURIER_STAFF_RECOVERY_MAX_ATTEMPTS),
                    "delivery_to": "collector@example.test",
                }
            ),
        )
        failed = SimpleNamespace(
            courier_state="failed",
            action_id="booky-delivery-budget",
            truth_state="failed",
            retry_eligible=True,
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result(scalar=booking)),
            get=AsyncMock(return_value=artist),
        )
        founder = SimpleNamespace(id=uuid4(), role="founder")
        with (
            patch.object(bookings_endpoint, "_booky_projection", new=AsyncMock(return_value=failed)),
            patch.object(
                bookings_endpoint,
                "_booky_delivery_message",
                new=AsyncMock(return_value=courier_message),
            ),
            patch.object(
                bookings_endpoint,
                "_booking_contact",
                new=AsyncMock(return_value=("Collector", "collector@example.test")),
            ),
            patch.object(
                bookings_endpoint,
                "process_booky_courier_outbox_once",
                new=AsyncMock(),
            ) as send_call,
            self.assertRaises(HTTPException) as conflict,
        ):
            await bookings_endpoint.retry_booky_confirmation(
                booking.id,
                db=db,
                current_user=founder,
            )
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(conflict.exception.detail, "Courier staff recovery limit reached")
        send_call.assert_not_awaited()


class BookyCourierOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_background_claim_ignores_old_failure_and_writes_one_terminal_receipt(self):
        booking_id = uuid4()
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=uuid4(),
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "sending",
                    "delivery_attempts": 2,
                    "delivery_attempt_id": "booky:concurrency:courier-attempt:2",
                    "delivery_attempt_started_at": (datetime.now(UTC) - timedelta(minutes=20)).isoformat(),
                    "delivery_updated_at": (datetime.now(UTC) - timedelta(minutes=20)).isoformat(),
                    "delivery_staff_recovery_attempts": 1,
                    "action_id": "concurrency",
                    "booking_id": str(booking_id),
                    "delivery_to": "collector@example.test",
                    "client_name": "Collector",
                    "artist_name": "Justin",
                    "description": "Consultation",
                    "placement": None,
                    "reference_code": "BKG-CONCURRENCY",
                    "consultation_method": "meet",
                    "scheduled_date": "2026-08-28",
                    "scheduled_time": "10:00",
                    "conference_url": "meet-url",
                }
            ),
        )
        old_failure = EmailLog(
            id=uuid4(),
            to_email="collector@example.test",
            subject="Confirmed",
            template="booking-confirmation",
            status="failed",
            courier_message_id=message.id,
            courier_attempt=1,
            created_at=datetime.now(UTC) - timedelta(minutes=30),
            updated_at=datetime.now(UTC) - timedelta(minutes=30),
        )
        execute_calls = 0

        async def execute(statement):
            nonlocal execute_calls
            execute_calls += 1
            if execute_calls == 1:
                return _Result([message])
            if execute_calls == 2:
                self.assertIn("courier_attempt", str(statement))
                self.assertEqual(old_failure.courier_attempt, 1)
                return _Result()
            if execute_calls == 3:
                return _Result()
            if execute_calls == 4:
                return _Result([message])
            raise AssertionError(f"unexpected execute call {execute_calls}")

        added = []
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=execute),
            commit=AsyncMock(),
            get=AsyncMock(return_value=message),
            add=added.append,
        )

        class SessionContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *_args):
                return False

        sender = AsyncMock(return_value=True)
        with (
            patch("app.core.database.async_session", return_value=SessionContext()),
            patch.object(email_service, "send_email", sender),
        ):
            first = await email_service.process_booky_courier_outbox_once(message_id=message.id)
            second = await email_service.process_booky_courier_outbox_once(message_id=message.id)

        self.assertEqual((first, second), (1, 0))
        sender.assert_awaited_once()
        self.assertEqual(sender.await_args.kwargs["courier_attempt"], 3)
        self.assertEqual(len(added), 1)
        terminal = added[0]
        self.assertEqual(terminal.event_type, "booky_delivery_retry_completed")
        self.assertEqual(
            terminal.provider_event_id,
            "booky:concurrency:courier-attempt:3:terminal",
        )

    async def test_stale_queued_message_is_delivered_after_restart(self):
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=uuid4(),
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "queued",
                    "delivery_attempts": 0,
                    "delivery_updated_at": (datetime.now(UTC) - timedelta(minutes=20)).isoformat(),
                    "delivery_to": "collector@example.test",
                    "client_name": "Collector",
                    "artist_name": "Justin",
                    "description": "Consultation",
                    "placement": None,
                    "reference_code": "BKG-OUTBOX",
                    "consultation_method": "meet",
                    "scheduled_date": "2026-08-28",
                    "scheduled_time": "10:00",
                    "conference_url": "meet-url",
                }
            ),
        )
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result([message]), _Result()]),
            commit=AsyncMock(),
            get=AsyncMock(return_value=message),
        )

        class SessionContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *_args):
                return False

        sender = AsyncMock(return_value=True)
        with (
            patch("app.core.database.async_session", return_value=SessionContext()),
            patch.object(email_service, "send_email", sender),
        ):
            delivered = await email_service.process_booky_courier_outbox_once(message_id=message.id)
        self.assertEqual(delivered, 1)
        self.assertEqual(json.loads(message.metadata_json)["delivery_status"], "sent")
        sender.assert_awaited_once()

    async def test_staff_override_delivers_after_automatic_attempt_limit(self):
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=uuid4(),
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "queued",
                    "delivery_attempts": email_service.BOOKY_COURIER_MAX_ATTEMPTS,
                    "delivery_staff_override": True,
                    "delivery_staff_recovery_attempts": 1,
                    "delivery_updated_at": datetime.now(UTC).isoformat(),
                    "delivery_to": "collector@example.test",
                    "client_name": "Collector",
                    "artist_name": "Justin",
                    "description": "Consultation",
                    "placement": None,
                    "reference_code": "BKG-STAFF-RECOVERY",
                    "consultation_method": "meet",
                    "scheduled_date": "2026-08-28",
                    "scheduled_time": "10:00",
                    "conference_url": "meet-url",
                }
            ),
        )
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result([message]), _Result()]),
            commit=AsyncMock(),
            get=AsyncMock(return_value=message),
        )

        class SessionContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *_args):
                return False

        sender = AsyncMock(return_value=True)
        with (
            patch("app.core.database.async_session", return_value=SessionContext()),
            patch.object(email_service, "send_email", sender),
        ):
            delivered = await email_service.process_booky_courier_outbox_once(message_id=message.id)
        metadata = json.loads(message.metadata_json)
        self.assertEqual(delivered, 1)
        self.assertEqual(metadata["delivery_status"], "sent")
        self.assertEqual(
            metadata["delivery_attempts"],
            email_service.BOOKY_COURIER_MAX_ATTEMPTS + 1,
        )
        self.assertFalse(metadata["delivery_staff_override"])
        sender.assert_awaited_once()

    async def test_stale_sending_reconciles_email_log_without_duplicate_send(self):
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=uuid4(),
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "sending",
                    "delivery_attempts": 1,
                    "delivery_updated_at": (datetime.now(UTC) - timedelta(minutes=20)).isoformat(),
                }
            ),
        )
        log = EmailLog(
            id=uuid4(),
            to_email="collector@example.test",
            subject="Confirmed",
            template="booking-confirmation",
            status="sent",
            courier_message_id=message.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result([message]), _Result(scalar=log)]),
            commit=AsyncMock(),
            get=AsyncMock(return_value=message),
        )

        class SessionContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *_args):
                return False

        sender = AsyncMock()
        with (
            patch("app.core.database.async_session", return_value=SessionContext()),
            patch.object(email_service, "send_email", sender),
        ):
            delivered = await email_service.process_booky_courier_outbox_once(message_id=message.id)
        self.assertEqual(delivered, 0)
        self.assertEqual(json.loads(message.metadata_json)["delivery_status"], "sent")
        sender.assert_not_awaited()


class ResendDeliveryOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def _dispatch(self, event_type: str, email_log: EmailLog, message: Message):
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[_Result(scalar=email_log), _Result(scalar=message)]),
            commit=AsyncMock(),
        )
        with patch.object(
            email_log_endpoint,
            "get_settings",
            return_value=SimpleNamespace(RESEND_WEBHOOK_SECRET=""),
        ):
            result = await email_log_endpoint.resend_webhook(
                _ResendRequest(event_type, str(email_log.resend_id)),
                db=db,
            )
        self.assertEqual(result, {"ok": True, "updated": 1})
        db.commit.assert_awaited_once()

    async def test_success_events_and_failure_evidence_are_monotonic(self):
        now = datetime.now(UTC)
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=uuid4(),
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps({"delivery_status": "opened", "delivery_attempts": 2}),
        )
        email_log = EmailLog(
            id=uuid4(),
            to_email="collector@example.test",
            subject="Confirmed",
            template="booking-confirmation",
            status="opened",
            resend_id="resend-monotonic",
            courier_message_id=message.id,
            courier_attempt=2,
            created_at=now,
            updated_at=now,
        )

        await self._dispatch("email.sent", email_log, message)
        await self._dispatch("email.delivered", email_log, message)
        self.assertEqual(email_log.status, "opened")
        self.assertEqual(json.loads(message.metadata_json)["delivery_status"], "opened")

        await self._dispatch("email.clicked", email_log, message)
        self.assertEqual(email_log.status, "clicked")
        self.assertEqual(json.loads(message.metadata_json)["delivery_status"], "clicked")

        await self._dispatch("email.bounced", email_log, message)
        await self._dispatch("email.delivery_delayed", email_log, message)
        await self._dispatch("email.sent", email_log, message)
        await self._dispatch("email.delivered", email_log, message)
        self.assertEqual(email_log.status, "bounced")
        self.assertEqual(json.loads(message.metadata_json)["delivery_status"], "bounced")

    async def test_complaint_is_terminal_and_delayed_success_cannot_overwrite_it(self):
        now = datetime.now(UTC)
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=uuid4(),
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps({"delivery_status": "delivered", "delivery_attempts": 1}),
        )
        email_log = EmailLog(
            id=uuid4(),
            to_email="collector@example.test",
            subject="Confirmed",
            template="booking-confirmation",
            status="delivered",
            resend_id="resend-complaint",
            courier_message_id=message.id,
            courier_attempt=1,
            created_at=now,
            updated_at=now,
        )

        await self._dispatch("email.complained", email_log, message)
        await self._dispatch("email.sent", email_log, message)
        await self._dispatch("email.delivered", email_log, message)
        await self._dispatch("email.clicked", email_log, message)
        self.assertEqual(email_log.status, "complained")
        self.assertEqual(json.loads(message.metadata_json)["delivery_status"], "complained")

    async def test_mismatched_attempt_updates_log_but_not_message_projection(self):
        now = datetime.now(UTC)
        message = Message(
            id=uuid4(),
            conversation_id=uuid4(),
            sender_id=uuid4(),
            content="Confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps({"delivery_status": "sent", "delivery_attempts": 2}),
        )
        email_log = EmailLog(
            id=uuid4(),
            to_email="collector@example.test",
            subject="Confirmed",
            template="booking-confirmation",
            status="sent",
            resend_id="resend-old-attempt",
            courier_message_id=message.id,
            courier_attempt=1,
            created_at=now,
            updated_at=now,
        )

        await self._dispatch("email.opened", email_log, message)
        self.assertEqual(email_log.status, "opened")
        self.assertEqual(json.loads(message.metadata_json)["delivery_status"], "sent")


class SessionDefaultsTests(unittest.TestCase):
    def test_exact_four_defaults_are_idempotent_and_exclude_consultation(self):
        artist_id = uuid4()
        first = missing_default_session_rows([artist_id], set())
        existing = {(artist_id, row["label"]) for row in first}
        self.assertEqual(missing_default_session_rows([artist_id], existing), [])
        self.assertEqual(len(first), 4)
        self.assertEqual([row["label"] for row in first], [spec["label"] for spec in DEFAULT_SESSION_TYPE_SPECS])
        self.assertFalse(any("consult" in row["label"].lower() for row in first))

    def test_default_ids_are_concurrency_stable_and_preserve_legacy_rows(self):
        artist_id = uuid4()
        first = missing_default_session_rows([artist_id], set())
        concurrent = missing_default_session_rows([artist_id], set())
        self.assertEqual([row["id"] for row in first], [row["id"] for row in concurrent])
        legacy_existing = {(artist_id, first[0]["label"])}
        remaining = missing_default_session_rows([artist_id], legacy_existing)
        self.assertEqual(len(remaining), 3)
        self.assertNotIn(first[0]["id"], {row["id"] for row in remaining})


class BookyMigrationTests(unittest.TestCase):
    def test_email_log_model_defines_non_unique_attempt_lookup_index(self):
        indexes = {index.name: index for index in EmailLog.__table__.indexes}
        attempt_index = indexes["ix_ryozan_email_log_courier_message_attempt"]
        self.assertFalse(bool(attempt_index.unique))
        self.assertEqual(
            [column.name for column in attempt_index.columns],
            ["courier_message_id", "courier_attempt"],
        )

    def test_baseline_carries_the_attempt_lookup_index(self):
        from app.core.schema_compare import BASELINE_SQL_PATH

        self.assertIn(
            "CREATE INDEX ix_bttco_email_log_courier_message_attempt ON public.bttco_email_log "
            "USING btree (courier_message_id, courier_attempt);",
            BASELINE_SQL_PATH.read_text(encoding="utf-8"),
        )


class BookyCalendarRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_calendar_update_adds_attendee_and_meet_request(self):
        patch_call = MagicMock()
        patch_call.execute.return_value = {
            "id": "calendar-event",
            "htmlLink": "https://calendar.example.test/event",
            "hangoutLink": "https://meet.google.com/test-room",
        }
        events = MagicMock()
        events.patch.return_value = patch_call
        service = MagicMock()
        service.events.return_value = events
        updates = {
            "client_name": "Collector",
            "client_email": "collector@example.test",
            "description": "Consultation",
            "reference_code": "BKG-BOOKY",
            "scheduled_date": "2026-08-28",
            "scheduled_time": "10:00",
            "duration_minutes": 30,
            "booking_kind": "consultation",
            "consultation_method": "meet",
            "conference_request_id": "bttcomeetstable",
        }
        with (
            patch.object(calendar_sync, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=False)),
            patch.object(calendar_sync, "_get_calendar_service", return_value=service),
        ):
            await calendar_sync.update_booking_event(
                "calendar-event", updates, calendar_id="artist-calendar", refresh_token="refresh"
            )
        kwargs = events.patch.call_args.kwargs
        self.assertEqual(kwargs["sendUpdates"], "all")
        self.assertEqual(kwargs["body"]["attendees"], [{"email": "collector@example.test"}])
        self.assertEqual(kwargs["conferenceDataVersion"], 1)


if __name__ == "__main__":
    unittest.main()
