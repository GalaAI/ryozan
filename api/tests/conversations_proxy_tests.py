"""
Courier conversation proxy tests.

This file is intentionally separate from auth, routing, and SPA security
tests because Courier proxy behavior is an endpoint-level messaging contract.
Those existing files are different contracts, and extending them would hide
regressions in unrelated suites instead of documenting the proxy boundary.
"""

import json
import os
import unittest
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import conversations
from app.models.booking import Booking
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.user import User
from app.schemas.message import ConversationCreate


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalar(self):
        return self.value


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self


class _AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


class _FakeSession:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.deleted = []
        self.flushes = 0

    async def execute(self, _stmt):
        if not self.results:
            raise AssertionError("unexpected database execute")
        return self.results.pop(0)

    async def scalar(self, stmt):
        result = await self.execute(stmt)
        if hasattr(result, "scalar_one_or_none"):
            return result.scalar_one_or_none()
        if hasattr(result, "scalar"):
            return result.scalar()
        return result

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    async def flush(self):
        self.flushes += 1
        now = datetime.now(UTC)
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()
            if isinstance(value, Conversation):
                if getattr(value, "created_at", None) is None:
                    value.created_at = now
                if getattr(value, "updated_at", None) is None:
                    value.updated_at = now

    async def commit(self):
        pass


class CourierProxyTests(unittest.IsolatedAsyncioTestCase):
    # These tests document the proxy contract, which runs only when the studio
    # turns STAFF_PROXY_ENABLED on (studio_settings_tests covers the off state).
    def setUp(self):
        enabled = conversations.get_settings().model_copy(update={"STAFF_PROXY_ENABLED": True})
        patcher = patch.object(conversations, "get_settings", return_value=enabled)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_inquiry_context_preserves_requested_times_without_confirming_them(self):
        now = datetime.now(UTC)
        conv = Conversation(
            id=uuid.uuid4(),
            name="Booking: Synthetic Client",
            is_group=False,
            created_by=uuid.uuid4(),
            created_at=now,
            updated_at=now,
        )
        slots = [{"date": "2026-11-03", "time": "14:30"}, {"date": "2026-11-05", "time": "11:00"}]
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            conversation_id=conv.id,
            status="pending",
            guest_name="Synthetic Client",
            description="Synthetic inquiry",
            preferred_date=date(2026, 11, 3),
            preferred_time="14:30",
            preferred_slots_json=slots,
        )
        db = _FakeSession([_ScalarResult(2), _ScalarResult(), _ScalarResult(booking), _ScalarResult("Artist")])
        with patch.object(conversations, "_build_channel_context", new=AsyncMock(return_value=None)):
            response = await conversations._build_conversation_response(conv, db)
        self.assertEqual(response.booking_context["preferred_date"], "2026-11-03")
        self.assertEqual(response.booking_context["preferred_time"], "14:30")
        self.assertEqual(response.booking_context["preferred_slots"], slots)
        self.assertIsNone(response.booking_context["scheduled_date"])
        self.assertIsNone(response.booking_context["scheduled_time"])

    @staticmethod
    def _booky_message(conversation_id, sender_id):
        return Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            sender_id=sender_id,
            content="Your consultation is confirmed.",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "sending",
                    "action_id": "booky-private-action",
                    "booking_id": str(uuid.uuid4()),
                    "delivery_to": "collector@example.test",
                    "delivery_attempts": 5,
                    "delivery_next_attempt_at": datetime.now(UTC).isoformat(),
                    "delivery_last_error": "provider details",
                    "retry_after": 120,
                    "errors": ["internal"],
                }
            ),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def test_collector_message_response_redacts_booky_operational_metadata(self):
        conversation_id = uuid.uuid4()
        collector = User(
            id=uuid.uuid4(),
            email="collector@example.test",
            name="Collector",
            role="collector",
        )
        participant = ConversationParticipant(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            user_id=collector.id,
            role="member",
        )
        message = self._booky_message(conversation_id, uuid.uuid4())
        db = _FakeSession(
            [
                _ScalarResult(participant),
                _ScalarResult(1),
                _RowsResult([(message, "Booky")]),
            ]
        )

        response = await conversations.get_messages(
            conversation_id,
            skip=0,
            limit=50,
            db=db,
            current_user=collector,
        )

        self.assertEqual(json.loads(response.messages[0].metadata_json), {"delivery_status": "sending"})

    async def test_staff_message_response_retains_booky_operational_metadata(self):
        conversation_id = uuid.uuid4()
        staff = User(
            id=uuid.uuid4(),
            email="assistant@example.test",
            name="Assistant",
            role="assistant",
        )
        participant = ConversationParticipant(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            user_id=staff.id,
            role="member",
        )
        message = self._booky_message(conversation_id, uuid.uuid4())
        db = _FakeSession(
            [
                _ScalarResult(participant),
                _ScalarResult(1),
                _RowsResult([(message, "Booky")]),
            ]
        )

        response = await conversations.get_messages(
            conversation_id,
            skip=0,
            limit=50,
            db=db,
            current_user=staff,
        )
        metadata = json.loads(response.messages[0].metadata_json)

        self.assertEqual(metadata["action_id"], "booky-private-action")
        self.assertEqual(metadata["delivery_to"], "collector@example.test")
        self.assertEqual(metadata["delivery_attempts"], 5)
        self.assertEqual(metadata["delivery_last_error"], "provider details")
        self.assertEqual(metadata["retry_after"], 120)

    async def test_create_conversation_uses_proxy_target_as_owner(self):
        proxy_user_id = uuid.uuid4()
        target_user_id = uuid.uuid4()
        client_user_id = uuid.uuid4()
        proxy_user = User(
            id=proxy_user_id,
            email="assistant@example.com",
            name="Assistant",
            role="assistant",
            proxy_for=target_user_id,
        )
        target_user = User(
            id=target_user_id,
            email="artist@example.com",
            name="Artist",
            role="founder",
        )
        db = _FakeSession(
            [
                _ScalarResult(target_user),
                _ScalarResult(client_user_id),
                _ScalarResult(None),
                _ScalarResult(2),
                _ScalarResult(None),
                _ScalarResult(None),
                _ScalarResult(None),
                _ScalarResult(None),
                _ScalarResult(None),
            ]
        )

        response = await conversations.create_conversation(
            ConversationCreate(participant_ids=[client_user_id], is_group=False),
            db,
            proxy_user,
        )

        created = next(value for value in db.added if isinstance(value, Conversation))
        participants = [value for value in db.added if isinstance(value, ConversationParticipant)]

        self.assertEqual(created.created_by, target_user_id)
        self.assertEqual(response.created_by, target_user_id)
        self.assertEqual({participant.user_id for participant in participants}, {target_user_id, client_user_id})
        self.assertIn(
            ("owner", target_user_id),
            {(participant.role, participant.user_id) for participant in participants},
        )
        self.assertNotIn(proxy_user_id, {participant.user_id for participant in participants})

    async def test_proxy_user_can_redact_message_sent_by_proxy_target(self):
        proxy_user_id = uuid.uuid4()
        target_user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        message_id = uuid.uuid4()
        proxy_user = User(
            id=proxy_user_id,
            email="assistant@example.com",
            name="Assistant",
            role="assistant",
            proxy_for=target_user_id,
        )
        participant = ConversationParticipant(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            user_id=target_user_id,
            role="owner",
        )
        message = Message(
            id=message_id,
            conversation_id=conversation_id,
            sender_id=target_user_id,
            content="Proxy target message",
            message_type="text",
        )
        db = _FakeSession([_ScalarResult(participant), _ScalarResult(message)])

        await conversations.delete_message(conversation_id, message_id, db, proxy_user)

        self.assertEqual(message.content, "[message removed]")
        self.assertEqual(message.message_type, "deleted")
        self.assertIn("deleted_by", message.metadata_json or "")
        self.assertTrue(any(isinstance(value, Message) and value.message_type == "audit" for value in db.added))

    async def test_courier_notification_is_queued_with_safe_projection_and_reply_address(self):
        conversation_id = uuid.uuid4()
        sender_id = uuid.uuid4()
        recipient_id = uuid.uuid4()
        recipient = User(
            id=recipient_id,
            email="client@example.com",
            name="Client",
            role="collector",
            phone=None,
            sms_opt_in=False,
        )
        sender = User(id=sender_id, email="artist@example.com", name="Artist", role="artist")
        conversation = Conversation(
            id=conversation_id,
            name="Booking thread",
            is_group=False,
            created_by=sender_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            sender_id=sender_id,
            content="Can you come in Friday?",
            message_type="text",
            created_at=datetime.now(UTC),
        )
        db = _FakeSession([_RowsResult([recipient])])

        async def fake_should_notify(_db, user_id, channel, category):
            self.assertEqual(user_id, recipient_id)
            self.assertEqual(channel, "email")
            self.assertEqual(category, "messages")
            return True

        with patch.object(conversations, "should_notify", fake_should_notify):
            await conversations._queue_conversation_notifications(
                db,
                message=message,
                sender=sender,
                conversation=conversation,
                recipient_ids=[recipient_id],
            )

        metadata = json.loads(message.metadata_json)
        self.assertEqual(metadata["delivery_status"], "queued")
        self.assertEqual(metadata["notification_delivery"]["intents"][0]["to"], "client@example.com")
        safe = json.loads(conversations._message_metadata_for_viewer(message, staff=False))
        self.assertEqual(safe, {"delivery_status": "queued"})
        html = conversations._courier_notification_email_html(message, metadata["notification_delivery"])
        self.assertIn(f"convo-{conversation_id}@ryozan.app", f"convo-{conversation_id}@ryozan.app")
        self.assertIn("Reply to this email", html)

    async def test_email_preference_does_not_suppress_explicit_sms_opt_in(self):
        conversation_id = uuid.uuid4()
        sender_id = uuid.uuid4()
        recipient_id = uuid.uuid4()
        recipient = User(
            id=recipient_id,
            email="client@example.com",
            name="Client",
            role="collector",
            phone="+18645550123",
            sms_opt_in=True,
            sms_opt_out_at=None,
        )
        sender = User(id=sender_id, email="artist@example.com", name="Artist", role="artist")
        conversation = Conversation(
            id=conversation_id,
            name="Booking thread",
            is_group=False,
            created_by=sender_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            sender_id=sender_id,
            content="Can you come in Friday?",
            message_type="text",
            created_at=datetime.now(UTC),
        )
        db = _FakeSession([_RowsResult([recipient])])

        with patch.object(conversations, "should_notify", return_value=False):
            await conversations._queue_conversation_notifications(
                db,
                message=message,
                sender=sender,
                conversation=conversation,
                recipient_ids=[recipient_id],
            )

        metadata = json.loads(message.metadata_json)
        intents = metadata["notification_delivery"]["intents"]
        self.assertEqual([intent["channel"] for intent in intents], ["sms"])
        self.assertEqual(intents[0]["to"], "+18645550123")

    async def test_explicit_phone_recipient_must_be_another_participant(self):
        conversation_id = uuid.uuid4()
        current_id = uuid.uuid4()
        participant = User(
            id=uuid.uuid4(),
            email="client@example.com",
            name="Client",
            role="collector",
            phone="+18645550123",
        )
        db = _FakeSession([_RowsResult([participant])])
        with self.assertRaisesRegex(Exception, "Recipient must be another participant"):
            await conversations._resolve_channel_recipient(
                db,
                conversation_id,
                [current_id],
                "+18645550999",
            )

    async def test_proxy_typing_indicator_uses_proxy_target_identity(self):
        proxy_user_id = uuid.uuid4()
        target_user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        proxy_user = User(
            id=proxy_user_id,
            email="assistant@example.com",
            name="Assistant",
            role="assistant",
            proxy_for=target_user_id,
        )
        target_user = User(
            id=target_user_id,
            email="artist@example.com",
            name="Artist",
            role="founder",
        )
        participant = ConversationParticipant(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            user_id=target_user_id,
            role="owner",
        )
        db = _FakeSession([_ScalarResult(participant), _ScalarResult(target_user)])
        conversations._typing_state.clear()

        await conversations.set_typing(conversation_id, db, proxy_user)

        self.assertIn(str(target_user_id), conversations._typing_state[str(conversation_id)])
        self.assertNotIn(str(proxy_user_id), conversations._typing_state[str(conversation_id)])

    async def test_proxy_typing_indicator_does_not_echo_target_to_proxy(self):
        proxy_user_id = uuid.uuid4()
        target_user_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        proxy_user = User(
            id=proxy_user_id,
            email="assistant@example.com",
            name="Assistant",
            role="assistant",
            proxy_for=target_user_id,
        )
        participant = ConversationParticipant(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            user_id=target_user_id,
            role="owner",
        )
        conversations._typing_state.clear()
        conversations._typing_state[str(conversation_id)] = {
            str(target_user_id): conversations._time.time(),
            str(proxy_user_id): conversations._time.time(),
        }
        db = _FakeSession([_ScalarResult(participant)])

        result = await conversations.get_typing(conversation_id, db, proxy_user)

        self.assertEqual(result, {"typing": []})


if __name__ == "__main__":
    unittest.main()
