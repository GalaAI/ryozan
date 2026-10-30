"""
Preferred slots and flash hold compatibility tests.

Custom pieces, consultations and bring-your-own-flash let the collector propose
1 to 3 date/times with no hold and no deposit. Flash designs keep a real held
slot, and that hold must not
count as a conflict against the booking that consumes it. These are separate
from the scheduler suite because they cover the submit contract end to end with
the provider boundaries faked.
"""

import os
import unittest
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.api.v1.endpoints import flash as flash_endpoint
from app.api.v1.endpoints.bookings import _do_submit_public_booking, _enrich_booking
from app.api.v1.endpoints.flash import _assert_flash_slot_available, book_flash_design
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.booking_slot_hold import BookingSlotHold
from app.models.flash import FlashDesign
from app.models.user import BTTCOUser
from app.schemas.booking import PreferredSlot, PublicBookingDraft, PublicBookingSubmit
from app.schemas.flash import FlashBookRequest


class _Rows:
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

    def first(self):
        return self.rows[0] if self.rows else None


class _RecordingSession:
    """Serve scripted rows per execute call and remember every statement."""

    def __init__(self, rows_by_call, *, get_rows=None):
        self.rows_by_call = list(rows_by_call)
        self.statements: list[str] = []
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.get_rows = get_rows or {}

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        if not self.rows_by_call:
            raise AssertionError(f"unexpected execute call: {str(stmt)[:120]}")
        return _Rows(self.rows_by_call.pop(0))

    async def get(self, model, key):
        return self.get_rows.get(key)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


def _artist() -> BTTCOUser:
    return BTTCOUser(
        id=uuid.uuid4(),
        email="artist@example.test",
        password_hash="disabled",
        name="Artist",
        role="artist",
        slug="artist",
        booking_mode="api",
        booky_mode="manual",
        flash_deposit_amount=0,
        allow_prepay_full=False,
        booking_buffer_minutes=0,
        booking_min_notice_hours=0,
        booking_max_advance_days=180,
        booking_allow_same_day=True,
    )


def _submit(**overrides) -> PublicBookingSubmit:
    payload = {
        "artist_slug": "artist",
        "first_name": "Client",
        "last_name": "Name",
        "email": "client@example.test",
        "description": "Black and grey shoulder piece with florals.",
    }
    payload.update(overrides)
    return PublicBookingSubmit(**payload)


TWO_SLOTS = [{"date": "2026-10-09", "time": "11:00"}, {"date": "2026-10-16", "time": "14:30"}]


class PreferredSlotsSchemaTests(unittest.TestCase):
    def test_two_preferred_slots_parse_without_hold(self):
        body = _submit(preferred_slots=TWO_SLOTS)

        self.assertEqual(len(body.preferred_slots), 2)
        self.assertEqual(body.preferred_slots[0], PreferredSlot(date=date(2026, 10, 9), time="11:00"))
        self.assertIsNone(body.hold_token)
        self.assertIsNone(body.preferred_date)

    def test_four_preferred_slots_are_rejected(self):
        with self.assertRaises(ValidationError):
            _submit(
                preferred_slots=TWO_SLOTS
                + [{"date": "2026-10-23", "time": "10:00"}, {"date": "2026-10-30", "time": "10:00"}]
            )

    def test_empty_and_duplicate_preferred_slots_are_rejected(self):
        with self.assertRaises(ValidationError):
            _submit(preferred_slots=[])
        with self.assertRaises(ValidationError):
            _submit(preferred_slots=[TWO_SLOTS[0], dict(TWO_SLOTS[0])])
        with self.assertRaises(ValidationError):
            _submit(preferred_slots=[{"date": "2026-10-09", "time": "11am"}])

    def test_preferred_slots_are_exclusive_with_single_held_slot(self):
        with self.assertRaises(ValidationError):
            _submit(preferred_slots=TWO_SLOTS, preferred_date="2026-10-09")
        with self.assertRaises(ValidationError):
            _submit(preferred_slots=TWO_SLOTS, preferred_time="11:00")
        with self.assertRaises(ValidationError):
            _submit(preferred_slots=TWO_SLOTS, hold_token="hold-token")

    def test_single_held_slot_contract_is_unchanged(self):
        body = _submit(preferred_date="2026-10-09", preferred_time="11:00", hold_token="hold-token")

        self.assertEqual(body.preferred_date, date(2026, 10, 9))
        self.assertEqual(body.hold_token, "hold-token")
        self.assertIsNone(body.preferred_slots)
        with self.assertRaises(ValidationError):
            _submit(booking_kind="consultation", consultation_method="meet")

    def test_consultation_accepts_preferred_slots_with_a_method(self):
        body = _submit(booking_kind="consultation", consultation_method="meet", preferred_slots=TWO_SLOTS[:1])

        self.assertEqual(body.booking_kind, "consultation")
        self.assertEqual(len(body.preferred_slots), 1)
        with self.assertRaises(ValidationError):
            _submit(booking_kind="consultation", preferred_slots=TWO_SLOTS[:1])

    def test_draft_stores_preferred_slots_as_plain_dicts(self):
        draft = PublicBookingDraft(inquiry_id="inquiry-draft-1", preferred_slots=TWO_SLOTS)

        self.assertEqual(draft.model_dump(exclude_unset=True)["preferred_slots"], TWO_SLOTS)


class PreferredSlotsSubmitTests(unittest.IsolatedAsyncioTestCase):
    async def _run_submit(
        self,
        body: PublicBookingSubmit,
        artist: BTTCOUser,
        *,
        account_created: bool = True,
    ):
        client = BTTCOUser(
            id=uuid.uuid4(),
            email=body.email,
            password_hash="disabled",
            name="Client Name",
            role="collector",
        )
        conversation = SimpleNamespace(id=uuid.uuid4())
        # artist lookup, submitted-draft lookup, reference code collision check, manager lookup
        db = _RecordingSession([[artist], [], [], []])
        upsert_draft = AsyncMock()
        with (
            patch.object(
                bookings_endpoint,
                "_find_or_create_collector_with_status",
                new=AsyncMock(return_value=(client, account_created)),
            ),
            patch.object(bookings_endpoint, "_attach_booking_conversation", new=AsyncMock(return_value=conversation)),
            patch.object(bookings_endpoint, "_ensure_booking_intake_message", new=AsyncMock()),
            patch.object(bookings_endpoint, "_ensure_booking_pipeline_task", new=AsyncMock()),
            patch.object(bookings_endpoint, "should_notify", new=AsyncMock(return_value=False)),
            patch.object(bookings_endpoint, "check_and_complete_quest", new=AsyncMock()),
            patch.object(bookings_endpoint, "reward_user_by_id", new=AsyncMock()),
            patch.object(bookings_endpoint, "_upsert_booking_draft", new=upsert_draft),
            patch.object(bookings_endpoint, "_booky_projection", new=AsyncMock(return_value=None)),
            patch.object(bookings_endpoint, "process_booking_email_outbox_once", new=AsyncMock()),
        ):
            response = await _do_submit_public_booking(body, db)
        return response, db, upsert_draft

    async def test_submit_with_preferred_slots_creates_pending_booking_without_hold_or_checkout(self):
        artist = _artist()
        body = _submit(inquiry_id="inquiry-preferred-1", preferred_slots=TWO_SLOTS, session_type_id=None)

        response, db, upsert_draft = await self._run_submit(body, artist)

        bookings = [row for row in db.added if isinstance(row, Booking)]
        self.assertEqual(len(bookings), 1)
        booking = bookings[0]
        self.assertEqual(booking.preferred_slots_json, TWO_SLOTS)
        self.assertIsNone(booking.preferred_date)
        self.assertIsNone(booking.preferred_time)
        self.assertIsNone(booking.scheduled_date)
        self.assertIsNone(booking.deposit_amount)

        self.assertEqual(response.booking_status, "pending")
        self.assertIsNone(response.checkout_url)
        self.assertFalse(response.checkout_required)
        self.assertIsNone(response.deposit_amount)
        self.assertEqual([slot.model_dump(mode="json") for slot in response.preferred_slots], TWO_SLOTS)
        self.assertEqual(response.message, "Your booking request has been submitted!")
        self.assertIsNone(response.account_setup_token)
        setup = [
            row
            for row in db.added
            if isinstance(row, BookingLifecycleEvent) and (row.details or {}).get("kind") == "client_account_setup"
        ]
        self.assertEqual(len(setup), 1)
        self.assertEqual(set(setup[0].details["payload"]), {"user_id"})

        # No hold row is read or written on this path.
        self.assertFalse(any("bttco_booking_slot_holds" in stmt for stmt in db.statements))
        self.assertFalse(any(isinstance(row, BookingSlotHold) for row in db.added))
        events = {row.event_type for row in db.added if isinstance(row, BookingLifecycleEvent)}
        self.assertNotIn("hold_consumed", events)
        self.assertIn("booking_submitted", events)
        submitted = next(
            row for row in db.added if isinstance(row, BookingLifecycleEvent) and row.event_type == "booking_submitted"
        )
        self.assertEqual(submitted.details["preferred_slots"], TWO_SLOTS)
        self.assertEqual(submitted.details["deposit_rule_tier"], "preferred_slots_no_deposit")

        # Request-received notifications are unchanged: client confirmation plus artist/shop alerts.
        email_kinds = [
            row.details["kind"]
            for row in db.added
            if isinstance(row, BookingLifecycleEvent) and row.event_type == "booking_email_intent"
        ]
        self.assertIn("client_confirmation", email_kinds)
        self.assertIn("artist_alert", email_kinds)
        self.assertEqual(db.commits, 1)

        draft = upsert_draft.await_args.args[1]
        self.assertEqual(draft.preferred_slots, TWO_SLOTS)
        self.assertIsNone(draft.hold_token)

    async def test_existing_collector_never_receives_an_account_setup_token(self):
        response, _db, _upsert = await self._run_submit(
            _submit(inquiry_id="inquiry-existing-account", preferred_slots=TWO_SLOTS[:1]),
            _artist(),
            account_created=False,
        )

        self.assertIsNone(response.account_setup_token)

    async def test_consultation_with_preferred_slots_stays_pending(self):
        artist = _artist()
        body = _submit(booking_kind="consultation", consultation_method="whatsapp", preferred_slots=TWO_SLOTS[:1])

        response, db, _ = await self._run_submit(body, artist)

        booking = next(row for row in db.added if isinstance(row, Booking))
        self.assertEqual(booking.booking_kind, "consultation")
        self.assertEqual(booking.consultation_method, "whatsapp")
        self.assertEqual(booking.preferred_slots_json, TWO_SLOTS[:1])
        self.assertEqual(response.booking_status, "pending")
        self.assertIsNone(response.conference_url)
        self.assertIsNone(response.checkout_url)
        self.assertFalse(any("bttco_booking_slot_holds" in stmt for stmt in db.statements))

    async def test_booking_response_exposes_preferred_slots(self):
        artist = _artist()
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=artist.id,
            status="pending",
            description="Preferred slots",
            preferred_slots_json=TWO_SLOTS,
            deposit_paid=False,
            sms_opt_in=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db = _RecordingSession([[artist.name]])

        enriched = await _enrich_booking(booking, db)

        self.assertEqual([slot.model_dump(mode="json") for slot in enriched.preferred_slots], TWO_SLOTS)

        booking.preferred_slots_json = None
        enriched = await _enrich_booking(booking, _RecordingSession([[artist.name]]))
        self.assertIsNone(enriched.preferred_slots)


class FlashHoldCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def _hold(self, artist_id, slot_day, *, token="flash-hold-token") -> BookingSlotHold:
        return BookingSlotHold(
            id=uuid.uuid4(),
            hold_token=token,
            artist_id=artist_id,
            artist_slug="artist",
            scheduled_date=slot_day,
            scheduled_time="13:00",
            duration_minutes=60,
            booking_kind="flash",
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

    async def test_active_hold_blocks_flash_slot_without_a_token(self):
        artist = _artist()
        slot_day = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
        hold = self._hold(artist.id, slot_day)

        class _HoldOnlySession(_RecordingSession):
            async def execute(self, stmt):
                text = str(stmt)
                self.statements.append(text)
                return _Rows([hold] if "bttco_booking_slot_holds" in text else [])

        with self.assertRaises(HTTPException) as err:
            await _assert_flash_slot_available(
                _HoldOnlySession([]),
                artist=artist,
                scheduled_date=slot_day,
                scheduled_time="13:00",
                duration_minutes=60,
            )
        self.assertEqual(err.exception.status_code, 409)

        with patch.object(flash_endpoint, "_slot_is_available", new=AsyncMock(return_value=True)) as slot_check:
            await _assert_flash_slot_available(
                _HoldOnlySession([]),
                artist=artist,
                scheduled_date=slot_day,
                scheduled_time="13:00",
                duration_minutes=60,
                exclude_hold_token=hold.hold_token,
            )
        self.assertEqual(slot_check.await_args.kwargs["exclude_hold_token"], hold.hold_token)

    async def test_flash_book_with_matching_hold_excludes_it_and_marks_it_consumed(self):
        artist = _artist()
        slot_day = date.today() + timedelta(days=7)
        hold = self._hold(artist.id, slot_day)
        design = FlashDesign(
            id=uuid.uuid4(),
            sheet_id=uuid.uuid4(),
            artist_id=artist.id,
            name="Panther",
            price=0,
            estimated_minutes=60,
            status="published",
            image_url="https://cdn.example.test/panther.png",
        )
        artist_row = (artist.id, artist.name, artist.slug, 0, False)
        collector = BTTCOUser(
            id=uuid.uuid4(), email="client@example.test", password_hash="disabled", name="Client", role="collector"
        )
        # design lookup, artist row, hold lookup
        db = _RecordingSession([[design], [artist_row], [hold]], get_rows={artist.id: artist})

        with (
            patch.object(flash_endpoint, "_slot_is_available", new=AsyncMock(return_value=True)) as slot_check,
            patch.object(flash_endpoint, "find_user_by_email", new=AsyncMock(return_value=collector)),
        ):
            response = await book_flash_design(
                background_tasks=BackgroundTasks(),
                body=FlashBookRequest(
                    design_id=str(design.id),
                    scheduled_date=slot_day.isoformat(),
                    scheduled_time="13:00",
                    guest_name="Client Name",
                    guest_email="client@example.test",
                    hold_token=hold.hold_token,
                ),
                db=db,
            )

        self.assertFalse(response.checkout_required)
        self.assertIsNone(response.account_setup_token)
        self.assertEqual(slot_check.await_args.kwargs["exclude_hold_token"], hold.hold_token)
        booking = next(row for row in db.added if isinstance(row, Booking))
        self.assertEqual(hold.status, "consumed")
        self.assertEqual(hold.booking_id, booking.id)
        self.assertEqual(design.booking_id, booking.id)
        self.assertEqual(db.commits, 1)

    async def test_flash_book_with_stale_hold_falls_back_to_live_check(self):
        artist = _artist()
        slot_day = date.today() + timedelta(days=7)
        design = FlashDesign(
            id=uuid.uuid4(),
            sheet_id=uuid.uuid4(),
            artist_id=artist.id,
            name="Panther",
            price=0,
            estimated_minutes=60,
            status="published",
        )
        artist_row = (artist.id, artist.name, artist.slug, 0, False)
        collector = BTTCOUser(
            id=uuid.uuid4(), email="client@example.test", password_hash="disabled", name="Client", role="collector"
        )
        db = _RecordingSession([[design], [artist_row], []], get_rows={artist.id: artist})

        with (
            patch.object(flash_endpoint, "_slot_is_available", new=AsyncMock(return_value=True)) as slot_check,
            patch.object(flash_endpoint, "find_user_by_email", new=AsyncMock(return_value=collector)),
        ):
            await book_flash_design(
                background_tasks=BackgroundTasks(),
                body=FlashBookRequest(
                    design_id=str(design.id),
                    scheduled_date=slot_day.isoformat(),
                    scheduled_time="13:00",
                    guest_name="Client Name",
                    guest_email="client@example.test",
                    hold_token="stale-token",
                ),
                db=db,
            )

        self.assertIsNone(slot_check.await_args.kwargs["exclude_hold_token"])
        self.assertTrue(any("bttco_booking_slot_holds" in stmt for stmt in db.statements))


if __name__ == "__main__":
    unittest.main()
