"""
General inquiry tests.

The public booking flow's Artist step offers "General inquiry" for non tattoo
questions. A general inquiry is not a booking:
it opens a Courier thread with the collector's message as the first inbound
message, alerts staff through the inbound Courier hooks, and returns an INQ
reference. These tests cover that contract with the provider boundaries faked,
separate from the submit and scheduler suites because no booking row exists.
"""

import asyncio
import os
import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.api.v1.endpoints.bookings import _do_submit_public_inquiry
from app.models.booking import Booking
from app.models.booking_draft import BookingDraft
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.message import Message
from app.models.notification import Notification
from app.models.user import User
from app.schemas.booking import PublicInquirySubmit
from app.services import inquiry_mirror


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class _RecordingSession:
    """Serve scripted rows per execute call and remember every statement."""

    def __init__(self, rows_by_call):
        self.rows_by_call = list(rows_by_call)
        self.statements: list[str] = []
        self.added = []
        self.flushes = 0
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        if not self.rows_by_call:
            raise AssertionError(f"unexpected execute call: {str(stmt)[:120]}")
        return _Rows(self.rows_by_call.pop(0))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


def _staff(role: str, *, slug: str | None = None, name: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{name.lower()}@example.test",
        password_hash="disabled",
        name=name,
        role=role,
        slug=slug,
        created_at=datetime.now(UTC),
    )


def _collector(email: str = "client@example.test") -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash="disabled",
        name="Client Name",
        role="collector",
        sms_opt_in=False,
    )


def _inquiry(**overrides) -> PublicInquirySubmit:
    payload = {
        "artist_slug": "any",
        "first_name": "Client",
        "last_name": "Name",
        "email": "client@example.test",
        "message": "Do you sell gift cards for the holidays?",
    }
    payload.update(overrides)
    return PublicInquirySubmit(**payload)


class _FakeConversation:
    def __init__(self):
        self.id = uuid.uuid4()
        self.name = None
        self.updated_at = None


class InquirySchemaTests(unittest.TestCase):
    def test_short_message_is_rejected(self):
        with self.assertRaises(ValidationError):
            _inquiry(message="too short")

    def test_artist_slug_is_optional(self):
        body = _inquiry(artist_slug=None)
        self.assertIsNone(body.artist_slug)
        self.assertFalse(body.sms_opt_in)


class InquirySubmitTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, body: PublicInquirySubmit, rows_by_call, *, client=None, mirror=None):
        client = client or _collector(body.email)
        conversation = _FakeConversation()
        created_calls = []

        async def _fake_find_or_create(db, participant_ids, *, name, created_by):
            created_calls.append({"participant_ids": set(participant_ids), "name": name, "created_by": created_by})
            conversation.name = name
            return conversation, True

        draft_row = SimpleNamespace(form_data=None)
        upsert_draft = AsyncMock(return_value=draft_row)
        queue_notifications = AsyncMock()
        outbox = AsyncMock()
        mirror = mirror if mirror is not None else AsyncMock()
        db = _RecordingSession(rows_by_call)
        with (
            patch.object(bookings_endpoint, "_find_or_create_collector", new=AsyncMock(return_value=client)),
            patch.object(bookings_endpoint, "find_or_create_direct_conversation", new=_fake_find_or_create),
            patch.object(bookings_endpoint, "should_notify", new=AsyncMock(return_value=False)),
            patch.object(bookings_endpoint, "_queue_conversation_notifications", new=queue_notifications),
            patch.object(bookings_endpoint, "process_courier_notification_outbox_once", new=outbox),
            patch.object(bookings_endpoint, "_upsert_booking_draft", new=upsert_draft),
            patch.object(bookings_endpoint, "mirror_general_inquiry", new=mirror),
        ):
            response = await _do_submit_public_inquiry(body, db)
            # Let the fire and forget tasks scheduled after commit run, then
            # their done callbacks.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        return SimpleNamespace(
            response=response,
            db=db,
            client=client,
            conversation=conversation,
            created_calls=created_calls,
            upsert_draft=upsert_draft,
            draft_row=draft_row,
            queue_notifications=queue_notifications,
            outbox=outbox,
            mirror=mirror,
        )

    async def test_inquiry_opens_thread_and_never_a_booking_row(self):
        founder = _staff("founder", name="Founder")
        admin = _staff("admin", name="Admin")
        # inquiry draft lookup, manager lookup, reference collision check
        run = await self._run(_inquiry(inquiry_id="inq-general-1"), [[], [admin, founder], []])

        self.assertTrue(run.response.reference_code.startswith("INQ-"))
        self.assertEqual(len(run.response.reference_code), 10)
        self.assertEqual(run.response.conversation_id, run.conversation.id)
        self.assertEqual(run.response.message, "Got it. We will reply by email.")
        self.assertFalse(run.response.idempotent_acknowledgement)

        self.assertFalse(any(isinstance(row, Booking) for row in run.db.added))
        self.assertFalse(any("ryozan_bookings" in stmt and "INSERT" in stmt for stmt in run.db.statements))
        messages = [row for row in run.db.added if isinstance(row, Message)]
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message.conversation_id, run.conversation.id)
        self.assertEqual(message.sender_id, run.client.id)
        self.assertIn("Do you sell gift cards for the holidays?", message.content)
        self.assertIn(run.response.reference_code, message.content)
        self.assertIn('"direction": "inbound"', message.metadata_json)
        self.assertIn('"source": "general_inquiry"', message.metadata_json)
        self.assertEqual(run.db.commits, 1)

        # Staff alerts go through the inbound Courier hooks, founder first.
        self.assertEqual(run.created_calls[0]["created_by"], founder.id)
        notified = {row.user_id for row in run.db.added if isinstance(row, Notification)}
        self.assertEqual(notified, {founder.id, admin.id})
        self.assertTrue(
            all(row.notification_type == "message" for row in run.db.added if isinstance(row, Notification))
        )
        run.queue_notifications.assert_awaited_once()
        self.assertEqual(
            set(run.queue_notifications.await_args.kwargs["recipient_ids"]),
            {founder.id, admin.id},
        )
        run.outbox.assert_awaited_once_with(message_id=message.id)

        events = [row for row in run.db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual([event.event_type for event in events], ["general_inquiry_submitted"])
        self.assertIsNone(events[0].booking_id)
        self.assertTrue(events[0].details["shop_queue"])

        # Idempotency record on the existing drafts table.
        run.upsert_draft.assert_awaited_once()
        self.assertEqual(run.upsert_draft.await_args.kwargs["status"], "inquiry_submitted")
        self.assertEqual(run.upsert_draft.await_args.kwargs["reference_code"], run.response.reference_code)
        self.assertEqual(run.draft_row.form_data["conversation_id"], str(run.conversation.id))

    async def test_any_slug_lands_in_shop_queue_not_on_an_artist(self):
        founder = _staff("founder", name="Founder")
        admin = _staff("admin", name="Admin")
        run = await self._run(_inquiry(artist_slug="any"), [[founder, admin], []])

        # The conversation model has no artist column: an unassigned thread is
        # one whose staff participants are the shop managers, not an artist.
        self.assertEqual(run.created_calls[0]["participant_ids"], {founder.id, admin.id, run.client.id})
        self.assertEqual(run.created_calls[0]["name"], "General inquiry: Client Name")
        self.assertFalse(any("WHERE ryozan_users.slug" in stmt for stmt in run.db.statements))
        events = [row for row in run.db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertIsNone(events[0].artist_id)
        run.upsert_draft.assert_not_awaited()

    async def test_missing_slug_also_lands_in_shop_queue(self):
        founder = _staff("founder", name="Founder")
        run = await self._run(_inquiry(artist_slug=None), [[founder], []])

        self.assertEqual(run.created_calls[0]["participant_ids"], {founder.id, run.client.id})

    async def test_real_slug_ties_thread_to_that_artist(self):
        artist = _staff("artist", slug="artist", name="Artist")
        run = await self._run(_inquiry(artist_slug="artist"), [[artist], []])

        self.assertEqual(run.created_calls[0]["participant_ids"], {artist.id, run.client.id})
        self.assertEqual(run.created_calls[0]["created_by"], artist.id)
        self.assertTrue(any("WHERE ryozan_users.slug" in stmt for stmt in run.db.statements))
        notified = {row.user_id for row in run.db.added if isinstance(row, Notification)}
        self.assertEqual(notified, {artist.id})
        events = [row for row in run.db.added if isinstance(row, BookingLifecycleEvent)]
        self.assertEqual(events[0].artist_id, artist.id)
        self.assertFalse(events[0].details["shop_queue"])
        self.assertEqual(run.mirror.await_args.args[0]["artist_slug"], "artist")

    async def test_unknown_slug_is_404(self):
        with self.assertRaises(HTTPException) as err:
            await self._run(_inquiry(artist_slug="nobody"), [[]])
        self.assertEqual(err.exception.status_code, 404)

    async def test_sms_consent_is_stamped_on_the_user_only_when_phone_and_opt_in(self):
        founder = _staff("founder", name="Founder")

        run = await self._run(_inquiry(phone="+15555550100", sms_opt_in=True), [[founder], []])
        self.assertTrue(run.client.sms_opt_in)
        self.assertIsNotNone(run.client.sms_opt_in_at)
        self.assertIsNone(run.client.sms_opt_out_at)
        self.assertEqual(run.client.phone, "+15555550100")
        self.assertFalse(any(isinstance(row, Booking) for row in run.db.added))

        run = await self._run(_inquiry(phone="+15555550100", sms_opt_in=False), [[founder], []])
        self.assertFalse(run.client.sms_opt_in)
        self.assertIsNone(run.client.sms_opt_in_at)
        self.assertEqual(run.client.phone, "+15555550100")

        run = await self._run(_inquiry(phone=None, sms_opt_in=True), [[founder], []])
        self.assertFalse(run.client.sms_opt_in)
        self.assertIsNone(run.client.sms_opt_in_at)
        self.assertIsNone(run.client.phone)

    async def test_repeated_inquiry_id_returns_existing_reference(self):
        conversation_id = uuid.uuid4()
        accepted = BookingDraft(
            id=uuid.uuid4(),
            inquiry_id="inq-general-2",
            status="inquiry_submitted",
            reference_code="INQ-ABC123",
            form_data={"kind": "general_inquiry", "conversation_id": str(conversation_id)},
        )
        run = await self._run(_inquiry(inquiry_id="inq-general-2"), [[accepted]])

        self.assertEqual(run.response.reference_code, "INQ-ABC123")
        self.assertEqual(run.response.conversation_id, conversation_id)
        self.assertTrue(run.response.idempotent_acknowledgement)
        self.assertEqual(run.created_calls, [])
        self.assertFalse(any(isinstance(row, Message) for row in run.db.added))
        self.assertEqual(run.db.commits, 0)
        run.mirror.assert_not_awaited()

    async def test_mirror_is_called_after_commit_and_its_failure_never_fails_the_request(self):
        founder = _staff("founder", name="Founder")
        run = await self._run(_inquiry(phone="+15555550100"), [[founder], []])
        run.mirror.assert_awaited_once()
        payload = run.mirror.await_args.args[0]
        self.assertEqual(payload["reference_code"], run.response.reference_code)
        self.assertEqual(payload["conversation_id"], str(run.conversation.id))
        self.assertEqual(payload["email"], "client@example.test")
        self.assertEqual(payload["phone"], "+15555550100")
        self.assertIn("received_at", payload)
        self.assertEqual(run.db.commits, 1)

        failing = AsyncMock(side_effect=RuntimeError("ainbox down"))
        with self.assertLogs("ryozan.bookings", level="WARNING") as logs:
            run = await self._run(_inquiry(), [[founder], []], mirror=failing)
        failing.assert_awaited_once()
        self.assertTrue(any("inquiry AInbox mirror" in line for line in logs.output))
        self.assertTrue(run.response.reference_code.startswith("INQ-"))
        self.assertEqual(run.db.commits, 1)


class InquiryMirrorTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self, url=None, token=None):
        return SimpleNamespace(ARK_AINBOX_MIRROR_URL=url, ARK_AINBOX_MIRROR_TOKEN=token)

    async def test_mirror_is_skipped_when_url_is_unset(self):
        with (
            patch.object(inquiry_mirror, "get_settings", return_value=self._settings()),
            patch.object(inquiry_mirror.httpx, "AsyncClient") as client_cls,
        ):
            await inquiry_mirror.mirror_general_inquiry({"reference_code": "INQ-000000"})
        client_cls.assert_not_called()

    async def test_mirror_posts_json_with_bearer_when_url_is_set(self):
        post = AsyncMock(return_value=SimpleNamespace(status_code=202))
        client = AsyncMock()
        client.__aenter__.return_value = SimpleNamespace(post=post)
        with (
            patch.object(
                inquiry_mirror,
                "get_settings",
                return_value=self._settings("https://ark.example.test/ainbox", "secret-token"),
            ),
            patch.object(inquiry_mirror.httpx, "AsyncClient", return_value=client) as client_cls,
        ):
            await inquiry_mirror.mirror_general_inquiry({"reference_code": "INQ-000001", "name": "Client Name"})

        client_cls.assert_called_once_with(timeout=5.0)
        post.assert_awaited_once()
        self.assertEqual(post.await_args.args[0], "https://ark.example.test/ainbox")
        sent = post.await_args.kwargs["json"]
        self.assertEqual(sent["source"], "bttco")
        self.assertEqual(sent["kind"], "general_inquiry")
        self.assertEqual(sent["reference_code"], "INQ-000001")
        self.assertEqual(post.await_args.kwargs["headers"]["Authorization"], "Bearer secret-token")

    async def test_mirror_swallows_transport_errors(self):
        client = AsyncMock()
        client.__aenter__.side_effect = inquiry_mirror.httpx.ConnectError("refused")
        with (
            patch.object(inquiry_mirror, "get_settings", return_value=self._settings("https://ark.example.test/x")),
            patch.object(inquiry_mirror.httpx, "AsyncClient", return_value=client),
            self.assertLogs("ryozan.inquiry_mirror", level="WARNING") as logs,
        ):
            await inquiry_mirror.mirror_general_inquiry({"reference_code": "INQ-000002"})
        self.assertTrue(any("AInbox mirror failed" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
