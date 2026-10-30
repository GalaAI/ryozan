"""Client-facing agent tools tell the truth about what they did.

A client asking "is my waiver signed?" gets their own waivers without being asked
for a name, and no client is offered send_notification, which only writes to the
caller's own notifications.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints import gala_chat


def _empty_db():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


class ClientToolTests(unittest.IsolatedAsyncioTestCase):
    def test_clients_are_not_offered_self_notification(self):
        for agent in ("taiso", "ensei"):
            self.assertFalse(gala_chat.tool_allowed_for_role(agent, "send_notification", "collector"), agent)
        self.assertTrue(gala_chat.tool_allowed_for_role("taiso", "send_notification", "founder"))

    def test_waiver_tool_needs_no_name(self):
        schema = next(tool for tool in gala_chat.GALA_TOOLS if tool["name"] == "get_waiver_status")["input_schema"]
        self.assertEqual(schema["required"], [])

    async def test_client_waiver_lookup_uses_own_records(self):
        user = SimpleNamespace(id="u", role="collector", email="c@example.org")
        db = _empty_db()
        text, components = await gala_chat.execute_tool("get_waiver_status", {}, user, db=db)
        self.assertEqual(text, "No waivers found for you.")
        self.assertEqual(components, [])
        where = str(db.execute.await_args.args[0]).split("WHERE", 1)[1]
        self.assertIn("user_id", where)
        self.assertNotIn("guest_name", where)

    async def test_staff_waiver_lookup_asks_for_a_name(self):
        user = SimpleNamespace(id="u", role="founder", email="f@example.org")
        text, _ = await gala_chat.execute_tool("get_waiver_status", {}, user, db=_empty_db())
        self.assertEqual(text, "Name the client whose waivers to check.")


if __name__ == "__main__":
    unittest.main()
