"""
Focused tests for recoverable public booking drafts.
"""

import os
import unittest

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints.bookings import _upsert_booking_draft
from app.models.booking_draft import BookingDraft
from app.schemas.booking import PublicBookingDraft


class _ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class _ScriptedSession:
    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)

    async def execute(self, _stmt):
        if not self.rows_by_call:
            raise AssertionError("unexpected execute call")
        return _ScalarRows(self.rows_by_call.pop(0))

    def add(self, _row):
        raise AssertionError("did not expect a new draft row")


class _CapturingSession(_ScriptedSession):
    def __init__(self, rows_by_call):
        super().__init__(rows_by_call)
        self.added = []

    def add(self, row):
        self.added.append(row)


class BookingDraftRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_resaving_reminded_draft_does_not_rearm_follow_up_email(self):
        existing = BookingDraft(
            inquiry_id="inquiry-reminded",
            status="reminder_sent",
            artist_slug="justin",
            guest_name="Old Name",
            guest_email="old@example.test",
        )
        db = _ScriptedSession([[existing]])

        row = await _upsert_booking_draft(
            db,
            PublicBookingDraft(
                inquiry_id="inquiry-reminded",
                artist_slug="justin",
                first_name="New",
                last_name="Client",
                email="new@example.test",
                description="Black and grey shoulder piece",
            ),
            status="draft",
        )

        self.assertIs(row, existing)
        self.assertEqual(row.status, "reminder_sent")
        self.assertEqual(row.guest_name, "New Client")
        self.assertEqual(row.guest_email, "new@example.test")

    async def test_image_upload_does_not_rearm_reminded_draft(self):
        existing = BookingDraft(
            inquiry_id="inquiry-reminded-upload",
            status="reminder_sent",
            artist_slug="justin",
            guest_name="Existing Client",
            guest_email="existing@example.test",
            image_urls=["https://assets.example.test/first.jpg"],
        )
        db = _ScriptedSession([[existing]])

        row = await _upsert_booking_draft(
            db,
            PublicBookingDraft(inquiry_id="inquiry-reminded-upload"),
            status="image_uploaded",
            image_url="https://assets.example.test/second.jpg",
        )

        self.assertEqual(row.status, "reminder_sent")
        self.assertEqual(
            row.image_urls,
            ["https://assets.example.test/first.jpg", "https://assets.example.test/second.jpg"],
        )

    async def test_partial_resave_preserves_previously_captured_identity_fields(self):
        existing = BookingDraft(
            inquiry_id="inquiry-partial-resave",
            status="draft",
            artist_slug="justin",
            guest_name="Existing Client",
            guest_email="existing@example.test",
            guest_phone="8645550100",
            form_data={
                "artist_slug": "justin",
                "first_name": "Existing",
                "last_name": "Client",
                "email": "existing@example.test",
                "phone": "8645550100",
                "sms_opt_in": True,
            },
        )
        db = _ScriptedSession([[existing]])

        row = await _upsert_booking_draft(
            db,
            PublicBookingDraft(
                inquiry_id="inquiry-partial-resave",
                first_name="Updated",
                email="updated@example.test",
            ),
            status="draft",
        )

        self.assertEqual(row.guest_name, "Updated Client")
        self.assertEqual(row.guest_email, "updated@example.test")
        self.assertEqual(row.guest_phone, "8645550100")
        self.assertEqual(row.form_data["last_name"], "Client")
        self.assertEqual(row.form_data["phone"], "8645550100")
        self.assertTrue(row.form_data["sms_opt_in"])

    async def test_explicit_null_clears_phone_while_omission_preserves_other_fields(self):
        existing = BookingDraft(
            inquiry_id="inquiry-clear-phone",
            status="draft",
            artist_slug="justin",
            guest_name="Existing Client",
            guest_email="existing@example.test",
            guest_phone="8645550100",
            form_data={
                "first_name": "Existing",
                "last_name": "Client",
                "email": "existing@example.test",
                "phone": "8645550100",
                "placement": "upper arm",
                "style_preference": "black and grey",
                "budget": "$800-$1,200",
            },
        )
        db = _ScriptedSession([[existing]])

        row = await _upsert_booking_draft(
            db,
            PublicBookingDraft(
                inquiry_id="inquiry-clear-phone",
                phone=None,
                style_preference=None,
                budget=None,
            ),
            status="draft",
        )

        self.assertIsNone(row.guest_phone)
        self.assertIsNone(row.form_data["phone"])
        self.assertIsNone(row.form_data["style_preference"])
        self.assertIsNone(row.form_data["budget"])
        self.assertEqual(row.form_data["placement"], "upper arm")

    async def test_whitespace_email_falls_back_to_existing_normalized_email(self):
        existing = BookingDraft(
            inquiry_id="inquiry-blank-email",
            status="draft",
            guest_name="Existing Client",
            guest_email="existing@example.test",
            form_data={
                "first_name": "Existing",
                "last_name": "Client",
                "email": "existing@example.test",
            },
        )
        db = _ScriptedSession([[existing]])

        row = await _upsert_booking_draft(
            db,
            PublicBookingDraft(
                inquiry_id="inquiry-blank-email",
                email="   ",
            ),
            status="draft",
        )

        self.assertEqual(row.guest_email, "existing@example.test")
        self.assertEqual(row.form_data["email"], "existing@example.test")

    async def test_whitespace_email_is_omitted_from_new_draft(self):
        db = _CapturingSession([[]])

        row = await _upsert_booking_draft(
            db,
            PublicBookingDraft(
                inquiry_id="inquiry-new-blank-email",
                email="   ",
            ),
            status="draft",
        )

        self.assertIsNone(row.guest_email)
        self.assertNotIn("email", row.form_data)
        self.assertEqual(db.added, [row])
        self.assertEqual(db.rows_by_call, [])
