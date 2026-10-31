"""Booking write tools and per-role OAuth scope narrowing for the Ryozan MCP.

Net-new file: the existing MCP transport tests cover discovery and reads. This
contract covers the shop write scope Ark agents and individual staff use.
"""

import asyncio
import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.mcp import oauth, tools
from app.mcp.oauth import SHOP_READ_SCOPE, SHOP_SEND_SCOPE, SHOP_WRITE_SCOPE, _grantable_scopes
from main import app


class McpShopWriteTests(unittest.TestCase):
    def test_artist_grant_narrows_to_read_and_write_without_send(self):
        requested = {SHOP_READ_SCOPE, SHOP_SEND_SCOPE, SHOP_WRITE_SCOPE}

        self.assertEqual(_grantable_scopes(requested, "artist"), {SHOP_READ_SCOPE, SHOP_WRITE_SCOPE})
        self.assertEqual(_grantable_scopes(requested, "admin"), requested)
        self.assertIsNone(_grantable_scopes({SHOP_SEND_SCOPE}, "artist"))
        self.assertIsNone(_grantable_scopes(requested, "unknown-role"))

    def test_booking_writes_need_the_write_scope(self):
        for name in ("ryozan_reschedule_booking", "ryozan_cancel_booking"):
            self.assertTrue(tools.tool_allowed(name, "artist", {SHOP_READ_SCOPE, SHOP_WRITE_SCOPE}))
            self.assertFalse(tools.tool_allowed(name, "artist", {SHOP_READ_SCOPE}))
            self.assertFalse(tools.tool_allowed(name, "collector", {SHOP_READ_SCOPE, SHOP_WRITE_SCOPE}))

    def test_reschedule_posts_to_staff_route_and_stays_silent_by_default(self):
        calls = []

        async def fake_write_json(path, token, method, payload):
            calls.append((path, token, method, payload))
            return {"id": "b-1", "status": "confirmed", "scheduled_date": "2026-11-04", "scheduled_time": "13:00"}

        with patch.object(tools, "write_json", fake_write_json):
            body = asyncio.run(
                tools.tool_reschedule_booking(
                    "user-token",
                    {"booking_id": "b-1", "scheduled_date": "2026-11-04", "scheduled_time": "13:00"},
                )
            )

        self.assertEqual(
            calls,
            [
                (
                    "/bookings/b-1/reschedule",
                    "user-token",
                    "POST",
                    {"scheduled_date": "2026-11-04", "scheduled_time": "13:00", "notify_client": False},
                )
            ],
        )
        self.assertFalse(json.loads(body)["client_notified"])

    def test_cancel_keeps_reason_and_only_notifies_when_asked(self):
        calls = []

        async def fake_write_json(path, token, method, payload):
            calls.append((path, payload))
            return {"id": "b-2", "status": "cancelled"}

        with patch.object(tools, "write_json", fake_write_json):
            asyncio.run(
                tools.tool_cancel_booking(
                    "user-token", {"booking_id": "b-2", "reason": "fixture", "notify_client": True}
                )
            )

        self.assertEqual(
            calls,
            [
                (
                    "/bookings/b-2/cancel",
                    {"delete_calendar_event": True, "reason": "fixture", "notify_client": True},
                )
            ],
        )


class McpRevocationTests(unittest.TestCase):
    def _revoke(self, record, client_id="client-1"):
        class Db:
            async def get(self, _model, key):
                return record if record and key == oauth._hash("refresh") else None

        async def override_db():
            yield Db()

        app.dependency_overrides[oauth.get_db] = override_db
        try:
            with patch.object(oauth, "_load_client", new=AsyncMock(return_value=SimpleNamespace())):
                return TestClient(app).post("/oauth/revoke", data={"client_id": client_id, "token": "refresh"})
        finally:
            app.dependency_overrides.clear()

    def test_revoke_ends_the_issuing_clients_refresh_token(self):
        record = SimpleNamespace(client_id="client-1", revoked_at=None)

        self.assertEqual(self._revoke(record).status_code, 200)
        self.assertIsInstance(record.revoked_at, datetime)
        self.assertEqual(record.revoked_at.tzinfo, UTC)

    def test_revoke_ignores_another_clients_token_and_unknown_tokens(self):
        record = SimpleNamespace(client_id="client-1", revoked_at=None)

        self.assertEqual(self._revoke(record, client_id="client-2").status_code, 200)
        self.assertIsNone(record.revoked_at)
        self.assertEqual(self._revoke(None).status_code, 200)


if __name__ == "__main__":
    unittest.main()
