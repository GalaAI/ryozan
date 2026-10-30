"""Twilio WhatsApp transport and Bella release-gate tests.

Existing SMS tests do not cover WhatsApp address prefixes or the channel-specific
Bella gate. This file owns that provider boundary without changing the broader
Gala limited-release contract covered by ai_staff_gate_tests.py.
"""

import json
import os
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.v1.endpoints import sms, whatsapp
from app.core.config import SHARED_SECRET_FIELDS, Settings
from app.core.database import get_db
from app.models.message import Message
from app.models.sms_message import SmsMessage
from app.services import courier_channels
from app.services import sms as sms_service


class _FakeResponse:
    status_code = 201

    def json(self):
        return {"sid": "SM123", "status": "queued", "num_segments": "1"}


class _RecordingClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, auth=None):
        self.calls.append({"url": url, "data": data, "auth": auth})
        return _FakeResponse()


def _settings(**overrides):
    values = {
        "TWILIO_ACCOUNT_SID": "AC123",
        "TWILIO_AUTH_TOKEN": "token",
        "TWILIO_FROM_NUMBER": "+15555554145",
        "TWILIO_MESSAGING_SERVICE_SID": "MG123",
        "API_PUBLIC_BASE_URL": "https://api.ryozan.app",
        # Non-development settings refuse to load without private shared secrets.
        **{name: f"fixture-{name.lower()}-0123456789abcdef0123456789" for name in SHARED_SECRET_FIELDS},
    }
    values.update(overrides)
    return Settings(**values)


def _webhook_request(payload: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/v1/whatsapp/webhook",
            "raw_path": b"/api/v1/whatsapp/webhook",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("api.ryozan.app", 443),
        },
        receive,
    )


class TwilioWhatsAppTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_whatsapp_uses_prefixed_to_and_explicit_shop_sender(self):
        client = _RecordingClient()
        with (
            patch.object(sms_service, "get_settings", return_value=_settings()),
            patch.object(sms_service.httpx, "AsyncClient", return_value=client),
        ):
            result = await sms_service._twilio_post_message(
                to="+15555550123",
                body="Hello",
                channel="whatsapp",
            )

        self.assertEqual(result["sid"], "SM123")
        payload = client.calls[0]["data"]
        self.assertEqual(payload["To"], "whatsapp:+15555550123")
        self.assertEqual(payload["From"], "whatsapp:+15555554145")
        self.assertNotIn("MessagingServiceSid", payload)
        self.assertEqual(
            payload["StatusCallback"],
            "https://api.ryozan.app/api/v1/sms/webhook/status",
        )

    async def test_bella_default_off_returns_before_opening_database(self):
        class _ForbiddenSession:
            def __call__(self):
                raise AssertionError("database must not open while Bella is disabled")

        with (
            patch.object(sms, "get_settings", return_value=_settings(BELLA_WHATSAPP_AUTO_REPLY_ENABLED=False)),
            patch.object(sms, "async_session", _ForbiddenSession()),
        ):
            await sms._bella_whatsapp_reply(
                conversation_id=uuid.uuid4(),
                owner_id=uuid.uuid4(),
                recipient_id=uuid.uuid4(),
                recipient_phone="+15555550123",
            )


class MetaWhatsAppWebhookGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_secret_rejects_non_local_webhooks_before_database_writes(self):
        for app_env in ("production", "staging"):
            with self.subTest(app_env=app_env):
                db = AsyncMock()
                request = _webhook_request(b'{"entry":[{"changes":[]}]}')
                with (
                    patch.object(
                        whatsapp,
                        "get_settings",
                        return_value=_settings(APP_ENV=app_env, DEBUG=False, META_WHATSAPP_APP_SECRET=""),
                    ),
                    self.assertRaises(HTTPException) as raised,
                ):
                    await whatsapp.handle_whatsapp_webhook(request, db)

                self.assertEqual(raised.exception.status_code, 503)
                self.assertEqual(raised.exception.detail, "WhatsApp webhook signing secret not configured")
                db.execute.assert_not_awaited()
                db.scalar.assert_not_awaited()
                db.flush.assert_not_awaited()
                db.commit.assert_not_awaited()

    async def test_missing_secret_remains_explicitly_usable_in_test_environment(self):
        db = AsyncMock()
        request = _webhook_request(b'{"entry":[]}')
        with patch.object(
            whatsapp,
            "get_settings",
            return_value=_settings(APP_ENV="test", DEBUG=False, META_WHATSAPP_APP_SECRET=""),
        ):
            result = await whatsapp.handle_whatsapp_webhook(request, db)

        self.assertEqual(result, {"status": "ok", "archived": 0})
        db.commit.assert_awaited_once_with()


class CourierWhatsAppStatusTests(unittest.TestCase):
    def test_status_reports_twilio_owned_whatsapp_lane(self):
        status = courier_channels.build_channel_status(
            _settings(),
            courier_channels.CourierRouteContext(),
        )
        whatsapp = next(channel for channel in status["channels"] if channel["id"] == "whatsapp")
        self.assertTrue(whatsapp["connected"])
        self.assertEqual(whatsapp["provider"], "twilio_whatsapp")
        self.assertEqual(whatsapp["route"], "***-***-4145")

    def test_bella_release_flag_defaults_off(self):
        self.assertFalse(Settings().BELLA_WHATSAPP_AUTO_REPLY_ENABLED)
        self.assertFalse(Settings().BELLA_SMS_AUTO_REPLY_ENABLED)


class SmsCourierReceiptTests(unittest.TestCase):
    def test_status_callback_projects_to_correlated_courier_message(self):
        courier_message_id = uuid.uuid4()
        sms_log = SmsMessage(
            id=uuid.uuid4(),
            direction="outbound",
            to_phone="+15555550123",
            from_phone="+15555554145",
            body="Hello",
            status="queued",
            twilio_sid="SM-correlated",
            courier_message_id=courier_message_id,
        )
        courier_message = Message(
            id=courier_message_id,
            conversation_id=uuid.uuid4(),
            sender_id=uuid.uuid4(),
            content="Hello",
            message_type="text",
            metadata_json=json.dumps(
                {
                    "delivery_status": "queued",
                    "notification_delivery": {
                        "intents": [
                            {
                                "id": "sms:recipient",
                                "channel": "sms",
                                "sms_log_id": str(sms_log.id),
                                "status": "sent",
                            }
                        ]
                    },
                }
            ),
            created_at=datetime.now(UTC),
        )

        class ScalarResult:
            def scalar_one_or_none(self):
                return sms_log

        class FakeDb:
            async def execute(self, _query):
                return ScalarResult()

            async def get(self, _model, item_id, **_kwargs):
                return courier_message if item_id == courier_message_id else None

            async def flush(self):
                return None

        app = FastAPI()
        app.include_router(sms.router)

        async def fake_db():
            yield FakeDb()

        app.dependency_overrides[get_db] = fake_db
        with patch.object(sms, "_validate_twilio_signature", return_value=True):
            response = TestClient(app).post(
                "/sms/webhook/status",
                data={"MessageSid": "SM-correlated", "MessageStatus": "delivered"},
            )
        self.assertEqual(response.status_code, 204)
        metadata = json.loads(courier_message.metadata_json)
        self.assertEqual(metadata["delivery_status"], "delivered")
        self.assertEqual(metadata["notification_delivery"]["intents"][0]["provider_status"], "delivered")


if __name__ == "__main__":
    unittest.main()
