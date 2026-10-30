"""tool_today formatting is pinned to the real /dashboard/artist-summary shape.

The dashboard endpoint returns a nested, hand-built dict (not a Pydantic
schema), so nothing else enforces this contract. If that endpoint is
restructured, this test should fail loudly rather than the tool silently
rendering zeros.
"""

import asyncio
import unittest
from unittest.mock import patch

from app.mcp import tools

# Captured from app/api/v1/endpoints/dashboard.py::get_artist_summary
SUMMARY_FIXTURE = {
    "user": {"name": "Justin", "role": "founder", "bttcoin_balance": 420, "quest_points": 75},
    "stripe": {
        "connected": True,
        "available_balance": 128350,
        "pending_balance": 4500,
        "currency": "usd",
        "next_payout_date": "2026-08-05",
    },
    "messages": {"unread_count": 3},
    "today": {
        "bookings": [
            {
                "id": "b1",
                "client_name": "Dana",
                "client_email": None,
                "scheduled_date": "2026-08-02",
                "scheduled_time": "14:00",
                "description": "Japanese half sleeve, session two",
                "status": "confirmed",
                "deposit_amount": 150.0,
            }
        ],
        "booking_count": 1,
    },
    "quests": {"active": [], "total_active": 2, "completed_count": 1},
    "briefing": {"generated_at": "2026-08-02T09:00:00", "summary": "One session today."},
}


class TodayShapeTests(unittest.TestCase):
    def _render(self, payload):
        async def fake_get_json(path, token, params=None):
            self.assertEqual(path, "/dashboard/artist-summary")
            return payload

        with patch.object(tools, "get_json", fake_get_json):
            return asyncio.run(tools.tool_today("token", {}))

    def test_renders_every_section(self):
        out = self._render(SUMMARY_FIXTURE)
        self.assertIn("Justin", out)
        self.assertIn("Unread messages: 3", out)
        self.assertIn("Stripe available: $1,283.50", out)  # cents, not dollars
        self.assertIn("Stripe pending: $45.00", out)
        self.assertIn("Next payout: 2026-08-05", out)
        self.assertIn("RYO: 420", out)
        self.assertIn("Active quests: 2", out)
        self.assertIn("Today's bookings (1)", out)
        self.assertIn("14:00 · Dana", out)
        self.assertIn("Japanese half sleeve", out)
        self.assertIn("deposit $150.00", out)
        self.assertIn("booking_id: b1", out)
        self.assertIn("One session today.", out)

    def test_balance_is_read_as_cents_not_dollars(self):
        """Guards the single most damaging silent bug in this tool."""
        out = self._render(SUMMARY_FIXTURE)
        self.assertNotIn("$128,350", out)

    def test_disconnected_stripe_does_not_show_fake_zero(self):
        payload = {**SUMMARY_FIXTURE, "stripe": {"connected": False}}
        out = self._render(payload)
        self.assertIn("Stripe: not connected", out)
        self.assertNotIn("Stripe available", out)

    def test_empty_day_says_so(self):
        payload = {**SUMMARY_FIXTURE, "today": {"bookings": [], "booking_count": 0}}
        self.assertIn("nothing on the books today", self._render(payload))

    def test_missing_sections_do_not_crash(self):
        out = self._render({})
        self.assertIn("Unread messages: 0", out)
        self.assertIn("nothing on the books today", out)


if __name__ == "__main__":
    unittest.main()
