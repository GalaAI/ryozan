"""Signed ElevenLabs phone event contract tests."""

import hashlib
import hmac
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import phone_agents
from app.core.database import get_db

SECRET = "fixture-elevenlabs-secret"
NOW = 1_725_000_000


def signature(raw: bytes, timestamp: int = NOW) -> str:
    digest = hmac.new(SECRET.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={timestamp},v0={digest}"


class SignatureTests(unittest.TestCase):
    def test_accepts_valid_signature_and_rejects_tampering_or_stale_timestamp(self):
        raw = b'{"type":"post_call_transcription"}'
        self.assertTrue(phone_agents.verify_elevenlabs_signature(raw, signature(raw), SECRET, now=NOW))
        self.assertFalse(phone_agents.verify_elevenlabs_signature(raw + b"x", signature(raw), SECRET, now=NOW))
        self.assertFalse(phone_agents.verify_elevenlabs_signature(raw, signature(raw, NOW - 2000), SECRET, now=NOW))

    def test_extracts_twilio_and_dynamic_caller_numbers(self):
        dynamic = {
            "conversation_initiation_client_data": {"dynamic_variables": {"system__caller_id": "(555) 010-0101"}}
        }
        twilio = {"metadata": {"body": {"From": "+15550100102"}}}
        self.assertEqual(phone_agents._extract_phone(dynamic), "+15550100101")
        self.assertEqual(phone_agents._extract_phone(twilio), "+15550100102")


class WebhookRouteTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(phone_agents.router)

        class FakeDb:
            async def commit(self):
                return None

        async def fake_db():
            yield FakeDb()

        app.dependency_overrides[get_db] = fake_db
        self.client = TestClient(app)
        self.settings = SimpleNamespace(
            ELEVENLABS_WEBHOOK_SECRET=SECRET,
            ELEVENLABS_TOOL_SECRET="fixture-tool-secret",
            ELEVENLABS_BTTCO_AGENT_ID="bella-agent",
            ELEVENLABS_JUSTIN_AGENT_ID="gala-agent",
        )

    def test_rejects_invalid_signature_before_processing(self):
        with patch.object(phone_agents, "get_settings", return_value=self.settings):
            response = self.client.post(
                "/phone-agents/webhooks/elevenlabs",
                content=b"{}",
                headers={"elevenlabs-signature": "t=1,v0=nope"},
            )
        self.assertEqual(response.status_code, 401)

    def test_routes_gala_to_pack_bridge_without_writing_bttco_business_state(self):
        payload = {
            "type": "post_call_transcription",
            "data": {
                "agent_id": "gala-agent",
                "conversation_id": "conversation-1",
                "status": "done",
                "analysis": {"call_successful": "success", "transcript_summary": "Personal follow-up."},
                "transcript": [{"role": "user", "message": "Remind me tomorrow."}],
            },
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        receipt = SimpleNamespace(to_dict=lambda: {"status": "delivered", "item_id": "intake-1"})
        outbox = SimpleNamespace(id=uuid.uuid4())
        with (
            patch.object(phone_agents, "get_settings", return_value=self.settings),
            patch.object(phone_agents.time, "time", return_value=NOW),
            patch.object(phone_agents, "queue_pack_capture", new=AsyncMock(return_value=outbox)) as queue,
            patch.object(phone_agents, "process_pack_bridge_outbox_once", new=AsyncMock(return_value=receipt)),
            patch.object(phone_agents, "_process_bella_event", new=AsyncMock()) as bella,
        ):
            response = self.client.post(
                "/phone-agents/webhooks/elevenlabs",
                content=raw,
                headers={"content-type": "application/json", "elevenlabs-signature": signature(raw)},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["agent"], "gala")
        self.assertIsNone(response.json()["local"])
        bella.assert_not_awaited()
        capture = queue.await_args.args[1]
        self.assertEqual(capture["sourceId"], "bttco:phone:gala")
        self.assertEqual(capture["metadata"]["packTargets"], ["ainbox", "courier", "mercury", "flow"])

    def test_take_message_routes_bella_to_local_packs_and_ainbox(self):
        receipt = SimpleNamespace(to_dict=lambda: {"status": "delivered", "item_id": "intake-2"})
        courier_message_id = uuid.uuid4()
        local = {
            "courier_conversation_id": "courier-1",
            "courier_message_id": str(courier_message_id),
            "crm_task_id": "task-1",
        }
        outbox = SimpleNamespace(id=uuid.uuid4())
        with (
            patch.object(phone_agents, "get_settings", return_value=self.settings),
            patch.object(phone_agents, "_process_bella_event", new=AsyncMock(return_value=local)) as bella,
            patch.object(phone_agents, "queue_pack_capture", new=AsyncMock(return_value=outbox)) as queue,
            patch.object(phone_agents, "process_pack_bridge_outbox_once", new=AsyncMock(return_value=receipt)),
        ):
            response = self.client.post(
                "/phone-agents/tools/take-message",
                headers={"Authorization": "Bearer fixture-tool-secret"},
                json={
                    "agent": "bella",
                    "conversation_id": "conversation-2",
                    "caller_phone": "+18645550123",
                    "caller_name": "Sam",
                    "message": "I need help with my appointment.",
                    "urgency": "high",
                    "callback_requested": True,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["local"], local)
        bella.assert_awaited_once()
        capture = queue.await_args.args[1]
        self.assertEqual(capture["metadata"]["urgency"], "high")
        self.assertEqual(capture["metadata"]["packTargets"], ["ainbox", "courier", "huddle", "flow"])
        self.assertEqual(queue.await_args.kwargs["courier_message_id"], courier_message_id)


if __name__ == "__main__":
    unittest.main()
