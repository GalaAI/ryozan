"""Ryozan to Ark Pack bridge adapter tests."""

import json
import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import pack_bridge
from app.services.pack_bridge import (
    PackBridgeReceipt,
    _post_pack_capture,
    build_booking_learning_capture,
    build_courier_learning_capture,
    build_pack_capture,
    deliver_pack_capture,
)


class PackBridgeTests(unittest.IsolatedAsyncioTestCase):
    def test_builds_a_bounded_typed_capture(self):
        capture = build_pack_capture(
            source_id="bttco:phone:bella",
            kind="courier",
            channel="voice",
            actor="bella",
            summary="Call summary",
            idempotency_key="elevenlabs:conversation-1",
            metadata={"packTargets": ["ainbox", "courier", "huddle"]},
        )
        self.assertEqual(capture["sourceId"], "bttco:phone:bella")
        self.assertEqual(capture["kind"], "courier")
        self.assertEqual(capture["metadata"]["packTargets"], ["ainbox", "courier", "huddle"])

    def test_learning_capture_keeps_raw_courier_content_in_ryozan(self):
        message = SimpleNamespace(
            id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            content="private client message that must remain in Ryozan",
            message_type="text",
            created_at=datetime.now(UTC),
        )
        capture = build_courier_learning_capture(message)
        encoded = json.dumps(capture)
        self.assertEqual(capture["metadata"]["trainingUse"], "needs_review")
        self.assertFalse(capture["metadata"]["trainingUseApproved"])
        self.assertIn("bttco://courier/", capture["contentRef"])
        self.assertNotIn(message.content, encoded)

    def test_booking_capture_omits_contact_data_and_mutable_details(self):
        event = SimpleNamespace(
            id=uuid.uuid4(),
            booking_id=uuid.uuid4(),
            hold_token=None,
            event_type="confirmed",
            source="staff",
            guest_email="private@example.test",
            summary="Private booking note",
            details={"phone": "+15555550123"},
            created_at=datetime.now(UTC),
        )
        capture = build_booking_learning_capture(event)
        encoded = json.dumps(capture)
        self.assertEqual(capture["metadata"]["outcomeLabel"], "confirmed")
        self.assertNotIn(event.guest_email, encoded)
        self.assertNotIn(event.summary, encoded)
        self.assertNotIn("+15555550123", encoded)

    async def test_missing_runtime_binding_returns_an_explicit_disabled_receipt(self):
        settings = SimpleNamespace(ARK_PACK_BRIDGE_URL="", ARK_PACK_BRIDGE_KEY="", APP_ENV="production")
        with patch("app.services.pack_bridge.get_settings", return_value=settings):
            receipt = await _post_pack_capture({"idempotencyKey": "one"})
        self.assertEqual(receipt.status, "disabled")
        self.assertEqual(receipt.error, "pack_bridge_not_configured")

    async def test_compatibility_delivery_persists_before_processing(self):
        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def commit(self):
                return None

        outbox = SimpleNamespace(id="00000000-0000-0000-0000-000000000123")
        receipt = PackBridgeReceipt(status="disabled", outbox_id=str(outbox.id), retryable=True)
        with (
            patch.object(pack_bridge, "async_session", return_value=FakeSession()),
            patch.object(pack_bridge, "queue_pack_capture", new=AsyncMock(return_value=outbox)) as queue,
            patch.object(
                pack_bridge, "process_pack_bridge_outbox_once", new=AsyncMock(return_value=receipt)
            ) as process,
        ):
            observed = await deliver_pack_capture(
                {"sourceId": "bttco:phone:gala", "idempotencyKey": "elevenlabs:event:one"}
            )
        queue.assert_awaited_once()
        process.assert_awaited_once_with(outbox_id=outbox.id)
        self.assertEqual(observed.status, "disabled")
        self.assertTrue(observed.retryable)


if __name__ == "__main__":
    unittest.main()
