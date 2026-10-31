"""Deterministic multi-number SMS identity routing tests.

Existing Twilio tests own WhatsApp prefixes and receipt projection, while Courier
tests own generic conversation behavior. This focused module is justified because
no current test owns strict receiving-number identity, lane inheritance, or the
dual Messaging Service plus explicit From payload required by BTTCO-050.
"""

import inspect
import json
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import conversations, sms
from app.core.config import Settings
from app.core.database import get_db
from app.models.message import Conversation
from app.models.sms_message import SmsMessage
from app.models.user import User
from app.services import sms as sms_service


def _settings(**overrides):
    values = {
        "TWILIO_ACCOUNT_SID": "AC-test",
        "TWILIO_AUTH_TOKEN": "fixture-token",
        "TWILIO_MESSAGING_SERVICE_SID": "MG-test",
        "TWILIO_BELLA_FROM_NUMBER": "+15550001001",
        "TWILIO_GALA_FROM_NUMBER": "+15550001002",
        "TWILIO_RYOZAN_COLLECTOR_FROM_NUMBER": "+15550001003",
        "API_PUBLIC_BASE_URL": "https://api.example.test",
    }
    values.update(overrides)
    return Settings(**values)


class _Response:
    status_code = 201

    @staticmethod
    def json():
        return {"sid": "SM-test", "status": "queued", "num_segments": "1"}


class _RecordingClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, data=None, auth=None):
        self.calls.append({"url": url, "data": data, "auth": auth})
        return _Response()


class SmsLaneResolverTests(unittest.TestCase):
    def test_actual_receiving_number_resolves_each_lane(self):
        settings = _settings()
        self.assertEqual(sms_service.resolve_sms_lane_by_number("(555) 000-1001", settings).id, "bella")
        self.assertEqual(sms_service.resolve_sms_lane_by_number("+15550001002", settings).id, "gala")
        self.assertEqual(sms_service.resolve_sms_lane_by_number("15550001003", settings).id, "ryozan-collector")

    def test_ryozan_collector_is_durably_assigned_to_sigma_without_changing_other_agents(self):
        settings = _settings()
        bella = sms_service.resolve_sms_lane_by_id("bella", settings)
        gala = sms_service.resolve_sms_lane_by_id("gala", settings)
        collector = sms_service.resolve_sms_lane_by_id("ryozan-collector", settings)

        self.assertEqual((bella.assigned_agent_id, bella.assigned_agent_label), ("bella", "Bella"))
        self.assertEqual((gala.assigned_agent_id, gala.assigned_agent_label), ("gala", "Gala"))
        self.assertEqual((collector.assigned_agent_id, collector.assigned_agent_label), ("sigma", "Sigma"))
        self.assertEqual(collector.reply_mode, "human_operated")
        self.assertEqual(collector.voice_status, "not_configured")

    def test_unknown_missing_and_duplicate_senders_fail_closed(self):
        with self.assertRaisesRegex(sms_service.SmsLaneConfigurationError, "exactly one"):
            sms_service.resolve_sms_lane_by_number("+15550001999", _settings())
        with self.assertRaisesRegex(sms_service.SmsLaneConfigurationError, "not configured"):
            sms_service.resolve_sms_lane_by_id("gala", _settings(TWILIO_GALA_FROM_NUMBER=""))
        with self.assertRaisesRegex(sms_service.SmsLaneConfigurationError, "both bella and gala"):
            sms_service.resolve_sms_lane_by_number(
                "+15550001001",
                _settings(TWILIO_GALA_FROM_NUMBER="+15550001001"),
            )

    def test_public_status_returns_masked_labels_only(self):
        status = sms_service.public_sms_lane_status(_settings())
        self.assertEqual([lane["id"] for lane in status], ["bella", "gala", "ryozan-collector"])
        self.assertEqual(status[0]["masked_number"], "***-***-1001")
        self.assertEqual(status[0]["assigned_agent_id"], "bella")
        self.assertEqual(status[1]["assigned_agent_id"], "gala")
        self.assertEqual(status[2]["assigned_agent_id"], "sigma")
        self.assertEqual(status[2]["assigned_agent_label"], "Sigma")
        self.assertEqual(status[2]["reply_mode"], "human_operated")
        self.assertFalse(status[2]["auto_reply_enabled"])
        self.assertEqual(status[2]["voice_status"], "not_configured")
        self.assertNotIn("+1555", str(status))

    def test_only_bella_lane_may_auto_reply(self):
        settings = _settings(BELLA_SMS_AUTO_REPLY_ENABLED=True, BELLA_WHATSAPP_AUTO_REPLY_ENABLED=True)
        self.assertTrue(sms._sms_lane_auto_reply_enabled("bella", is_whatsapp=False, settings=settings))
        self.assertFalse(sms._sms_lane_auto_reply_enabled("gala", is_whatsapp=False, settings=settings))
        self.assertFalse(sms._sms_lane_auto_reply_enabled("ryozan-collector", is_whatsapp=True, settings=settings))

    def test_generic_and_template_sends_default_to_bella(self):
        self.assertEqual(inspect.signature(sms_service.send_sms).parameters["lane_id"].default, "bella")
        self.assertEqual(inspect.signature(sms_service.send_from_template).parameters["lane_id"].default, "bella")


class TwilioPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_sms_includes_messaging_service_and_explicit_lane_sender(self):
        settings = _settings()
        client = _RecordingClient()
        lane = sms_service.resolve_sms_lane_by_id("gala", settings)
        with (
            patch.object(sms_service, "get_settings", return_value=settings),
            patch.object(sms_service.httpx, "AsyncClient", return_value=client),
        ):
            await sms_service._twilio_post_message(
                to="+15550002000",
                body="Test",
                sms_lane=lane,
            )

        payload = client.calls[0]["data"]
        self.assertEqual(payload["MessagingServiceSid"], "MG-test")
        self.assertEqual(payload["From"], "+15550001002")
        self.assertEqual(payload["To"], "+15550002000")

    async def test_missing_bella_sender_never_falls_back_to_legacy_sender(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

            async def flush(self):
                return None

        settings = _settings(
            TWILIO_BELLA_FROM_NUMBER="",
            TWILIO_FROM_NUMBER="+15550001998",
        )
        db = FakeDb()
        with (
            patch.object(sms_service, "get_settings", return_value=settings),
            patch.object(sms_service, "_twilio_post_message", new=AsyncMock()) as send,
        ):
            log = await sms_service.send_sms(
                db=db,
                to="+15550002000",
                body="Test",
            )

        self.assertEqual(log.status, "failed")
        self.assertEqual(log.error_code, "SENDER_NOT_CONFIGURED")
        self.assertEqual(log.from_phone, "(unset)")
        send.assert_not_awaited()


class _ScalarSession:
    def __init__(self, values):
        self.values = list(values)

    async def scalar(self, _query):
        if not self.values:
            raise AssertionError("unexpected scalar query")
        return self.values.pop(0)


class CourierLaneInheritanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_courier_reply_inherits_verified_gala_lane(self):
        sms_log = SmsMessage(
            id=uuid.uuid4(),
            direction="inbound",
            to_phone="+15550001002",
            from_phone="+15550002000",
            body="Hello",
            status="received",
        )
        with patch.object(sms_service, "get_settings", return_value=_settings()):
            lane_id = await conversations._resolve_conversation_sms_lane_id(
                uuid.uuid4(),
                _ScalarSession([sms_log]),
            )
        self.assertEqual(lane_id, "gala")

    async def test_generic_conversation_defaults_to_bella(self):
        lane_id = await conversations._resolve_conversation_sms_lane_id(
            uuid.uuid4(),
            _ScalarSession([None, None]),
        )
        self.assertEqual(lane_id, "bella")

    async def test_channel_context_projects_sigma_assignment_for_ryozan_collector(self):
        sms_log = SmsMessage(
            id=uuid.uuid4(),
            direction="inbound",
            to_phone="+15550001003",
            from_phone="+15550002000",
            body="Hello",
            status="received",
        )
        with patch.object(sms_service, "get_settings", return_value=_settings()):
            context = await conversations._build_channel_context(
                uuid.uuid4(),
                _ScalarSession([sms_log]),
            )

        self.assertEqual(context["lane_id"], "ryozan-collector")
        self.assertEqual(context["assigned_agent_id"], "sigma")
        self.assertEqual(context["assigned_agent_label"], "Sigma")
        self.assertEqual(context["assignment_status"], "assigned")
        self.assertEqual(context["reply_mode"], "human_operated")
        self.assertEqual(context["voice_status"], "not_configured")


class _EmptyResult:
    @staticmethod
    def scalar_one_or_none():
        return None


class _ConversationSession:
    def __init__(self):
        self.added = []

    async def execute(self, _query):
        return _EmptyResult()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()


class CourierConversationIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_caller_has_distinct_conversation_identity_per_lane(self):
        owner = User(id=uuid.uuid4(), email="owner@example.test", name="Owner", role="founder")
        sender = User(id=uuid.uuid4(), email="caller@example.test", name="Caller", role="collector")
        db = _ConversationSession()
        bella = await sms._resolve_sms_conversation(
            db,
            owner,
            sender,
            "+15550002000",
            lane_id="bella",
            lane_label="Bella",
        )
        gala = await sms._resolve_sms_conversation(
            db,
            owner,
            sender,
            "+15550002000",
            lane_id="gala",
            lane_label="Gala",
        )
        self.assertIsInstance(bella, Conversation)
        self.assertNotEqual(bella.name, gala.name)
        self.assertIn("Bella", bella.name or "")
        self.assertIn("Gala", gala.name or "")

    async def test_existing_lookup_is_lane_qualified(self):
        owner = User(id=uuid.uuid4(), email="owner@example.test", name="Owner", role="founder")
        sender = User(id=uuid.uuid4(), email="caller@example.test", name="Caller", role="collector")
        existing_bella = Conversation(
            id=uuid.uuid4(),
            name="SMS - Bella: +15550002000",
            is_group=False,
            created_by=owner.id,
        )

        class LaneLookupSession(_ConversationSession):
            async def execute(self, query):
                params = query.compile().params
                requested_name = next((value for key, value in params.items() if key.startswith("name_")), None)
                value = existing_bella if requested_name == existing_bella.name else None
                return SimpleNamespace(scalar_one_or_none=lambda: value)

        db = LaneLookupSession()
        bella = await sms._resolve_sms_conversation(
            db,
            owner,
            sender,
            "+15550002000",
            lane_id="bella",
            lane_label="Bella",
        )
        gala = await sms._resolve_sms_conversation(
            db,
            owner,
            sender,
            "+15550002000",
            lane_id="gala",
            lane_label="Gala",
        )
        self.assertIs(bella, existing_bella)
        self.assertNotEqual(gala.id, existing_bella.id)
        self.assertEqual(gala.name, "SMS - Gala: +15550002000")


class InboundUnclassifiedTests(unittest.TestCase):
    def test_unknown_receiving_number_returns_empty_twiml_and_archives_no_courier_message(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            async def scalar(self, _query):
                return None

            def add(self, value):
                self.added.append(value)

            async def flush(self):
                for value in self.added:
                    if getattr(value, "id", None) is None:
                        value.id = uuid.uuid4()

        db = FakeDb()
        app = FastAPI()
        app.include_router(sms.router)

        async def fake_db():
            yield db

        app.dependency_overrides[get_db] = fake_db
        with (
            patch.object(sms, "_validate_twilio_signature", return_value=True),
            patch.object(sms_service, "get_settings", return_value=_settings()),
            patch.object(sms, "_archive_sms_to_courier", new=AsyncMock()) as archive,
            patch.object(sms, "queue_pack_capture", new=AsyncMock()) as pack,
        ):
            response = TestClient(app).post(
                "/sms/webhook/inbound",
                data={
                    "From": "+15550002000",
                    "To": "+15550001999",
                    "Body": "X" * 900,
                    "MessageSid": "SM-unclassified",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, '<?xml version="1.0" encoding="UTF-8"?><Response/>')
        self.assertEqual(len(db.added), 1)
        self.assertIsInstance(db.added[0], SmsMessage)
        self.assertEqual(db.added[0].status, "unclassified")
        self.assertEqual(db.added[0].error_code, "UNKNOWN_DESTINATION")
        self.assertLessEqual(len(db.added[0].body), 600)
        archive.assert_not_awaited()
        pack.assert_not_awaited()


class InboundSigmaCaptureTests(unittest.TestCase):
    def test_ryozan_collector_inbound_capture_assigns_sigma_without_enabling_reply_or_voice(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            async def scalar(self, _query):
                return None

            def add(self, value):
                self.added.append(value)

            async def flush(self):
                for value in self.added:
                    if getattr(value, "id", None) is None:
                        value.id = uuid.uuid4()

        db = FakeDb()
        app = FastAPI()
        app.include_router(sms.router)

        async def fake_db():
            yield db

        app.dependency_overrides[get_db] = fake_db
        conversation = Conversation(
            id=uuid.uuid4(),
            name="SMS - ryozan-collector: caller",
            is_group=False,
            created_by=uuid.uuid4(),
        )
        owner = User(id=uuid.uuid4(), email="owner@example.test", name="Owner", role="founder")
        sender = User(id=uuid.uuid4(), email="caller@example.test", name="Caller", role="collector")
        courier_message = SimpleNamespace(id=uuid.uuid4())
        with (
            patch.object(sms, "_validate_twilio_signature", return_value=True),
            patch.object(sms_service, "get_settings", return_value=_settings()),
            patch.object(
                sms,
                "_archive_sms_to_courier",
                new=AsyncMock(return_value=(conversation, owner, sender, courier_message)),
            ) as archive,
            patch.object(sms, "queue_pack_capture", new=AsyncMock()) as pack,
        ):
            response = TestClient(app).post(
                "/sms/webhook/inbound",
                data={
                    "From": "+15550002000",
                    "To": "+15550001003",
                    "Body": "Hello from ryozan-collector",
                    "MessageSid": "SM-ryozan-collector-sigma",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(archive.await_args.kwargs["assigned_agent_id"], "sigma")
        self.assertEqual(archive.await_args.kwargs["assigned_agent_label"], "Sigma")
        self.assertEqual(archive.await_args.kwargs["reply_mode"], "human_operated")
        self.assertEqual(archive.await_args.kwargs["voice_status"], "not_configured")
        capture = pack.await_args.args[1]
        self.assertEqual(capture["actor"], "sigma")
        self.assertEqual(capture["metadata"]["assignedAgentId"], "sigma")
        self.assertEqual(capture["metadata"]["assignedAgentLabel"], "Sigma")
        self.assertEqual(capture["metadata"]["replyMode"], "human_operated")
        self.assertEqual(capture["metadata"]["voiceStatus"], "not_configured")


class InboundBookingRoutedAutoReplyTests(unittest.TestCase):
    def _post(self, metadata, sender_role="collector"):
        class FakeDb:
            def __init__(self):
                self.added = []

            async def scalar(self, _query):
                return None

            def add(self, value):
                self.added.append(value)

            async def flush(self):
                for value in self.added:
                    if getattr(value, "id", None) is None:
                        value.id = uuid.uuid4()

            async def commit(self):
                return None

        db = FakeDb()
        app = FastAPI()
        app.include_router(sms.router)

        async def fake_db():
            yield db

        app.dependency_overrides[get_db] = fake_db
        conversation = Conversation(id=uuid.uuid4(), name="Booking thread", is_group=False, created_by=uuid.uuid4())
        owner = User(id=uuid.uuid4(), email="owner@example.test", name="Owner", role="founder")
        sender = User(id=uuid.uuid4(), email="caller@example.test", name="Caller", role=sender_role)
        courier_message = SimpleNamespace(id=uuid.uuid4(), metadata_json=json.dumps(metadata))
        settings = _settings(BELLA_SMS_AUTO_REPLY_ENABLED=True)
        with (
            patch.object(sms, "_validate_twilio_signature", return_value=True),
            patch.object(sms_service, "get_settings", return_value=settings),
            patch.object(sms, "get_settings", return_value=settings),
            patch.object(
                sms,
                "_archive_sms_to_courier",
                new=AsyncMock(return_value=(conversation, owner, sender, courier_message)),
            ),
            patch.object(sms, "queue_pack_capture", new=AsyncMock()),
            patch.object(sms, "_bella_channel_reply", new=AsyncMock()) as bella_reply,
        ):
            response = TestClient(app).post(
                "/sms/webhook/inbound",
                data={"From": "+15550002000", "To": "+15550001001", "Body": "How much?", "MessageSid": "SM-x"},
            )
        self.assertEqual(response.status_code, 200)
        return bella_reply

    def test_booking_routed_reply_never_triggers_bella(self):
        bella_reply = self._post({"routed_booking_id": str(uuid.uuid4())})
        bella_reply.assert_not_awaited()

    def test_lane_thread_reply_keeps_bella_auto_reply(self):
        bella_reply = self._post({"routed_booking_id": None})
        bella_reply.assert_awaited_once()

    def test_staff_texting_the_shop_line_never_triggers_bella(self):
        bella_reply = self._post({"routed_booking_id": None}, sender_role="artist")
        bella_reply.assert_not_awaited()


class SmsOriginNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sms_origin_message_texts_staff_but_never_collectors(self):
        artist = User(
            id=uuid.uuid4(), email="a@example.test", name="Artist", role="artist", phone="+15550107777", sms_opt_in=True
        )
        other_collector = User(
            id=uuid.uuid4(),
            email="c@example.test",
            name="Other",
            role="collector",
            phone="+15550108888",
            sms_opt_in=True,
        )
        sender = User(id=uuid.uuid4(), email="s@example.test", name="Maria", role="collector")
        conversation = Conversation(id=uuid.uuid4(), name="Booking thread", is_group=False, created_by=artist.id)
        message = SimpleNamespace(id=uuid.uuid4(), content="How much?", message_type="sms", metadata_json="{}")

        class Db:
            async def execute(self, _query):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [artist, other_collector]))

        with (
            patch.object(conversations, "should_notify", new=AsyncMock(return_value=False)),
            patch.object(conversations, "_resolve_conversation_sms_lane_id", new=AsyncMock(return_value="bella")),
            patch.object(sms_service, "get_settings", return_value=_settings()),
        ):
            await conversations._queue_conversation_notifications(
                Db(),
                message=message,
                sender=sender,
                conversation=conversation,
                recipient_ids=[artist.id, other_collector.id],
                allow_sms_channel_message=True,
            )
        intents = json.loads(message.metadata_json)["notification_delivery"]["intents"]
        self.assertEqual([intent["recipient_user_id"] for intent in intents], [str(artist.id)])


class CourierEmailSmsContentTests(unittest.TestCase):
    def test_staff_email_shows_the_collectors_text(self):
        from types import SimpleNamespace

        from app.api.v1.endpoints.conversations import _courier_notification_email_html

        message = SimpleNamespace(
            message_type="sms", content="Can we do <Saturday>?", conversation_id="00000000-0000-0000-0000-000000000001"
        )
        html = _courier_notification_email_html(message, {"sender_name": "Maria"})
        self.assertIn("Can we do &lt;Saturday&gt;?", html)
        self.assertNotIn("Sent a message", html)


if __name__ == "__main__":
    unittest.main()
