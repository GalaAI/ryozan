"""Consultation MCP contract tests."""

import asyncio
import unittest
from unittest.mock import patch

from app.mcp import tools


class ConsultationMcpTests(unittest.TestCase):
    def test_availability_uses_consultation_contract(self):
        captured = {}

        async def fake_get_json(path, token, params=None):
            captured.update(path=path, token=token, params=params)
            return {
                "calendar_connected": True,
                "slots": [
                    {
                        "label": "Tue Aug 18 - 11:00 AM",
                        "start": "2026-08-18T11:00:00-04:00",
                        "confidence": "high",
                    }
                ],
            }

        with patch.object(tools, "get_json", fake_get_json):
            result = asyncio.run(
                tools.tool_consultation_availability(
                    "token",
                    {
                        "artist_slug": "justin",
                        "start": "2026-08-18",
                        "end": "2026-08-22",
                    },
                )
            )

        self.assertEqual(captured["path"], "/calendar/slots/justin")
        self.assertEqual(captured["params"]["consultation"], "true")
        self.assertIn("Tue Aug 18 - 11:00 AM", result)
        self.assertIn("Google Calendar connected: yes", result)

    def test_triage_combines_booking_and_live_slots_without_writing(self):
        calls = []

        async def fake_get_json(path, token, params=None):
            calls.append((path, params))
            if path == "/bookings/booking-1":
                return {
                    "resolved_name": "Cole",
                    "resolved_email": "cole@example.com",
                    "status": "pending",
                    "description": "Traditional eagle",
                    "placement": "forearm",
                    "reference_code": "BTT-123",
                    "conversation_id": None,
                }
            return {
                "calendar_connected": True,
                "slots": [
                    {
                        "label": "Tue Aug 18 - 11:00 AM",
                        "start": "2026-08-18T11:00:00-04:00",
                    }
                ],
            }

        with patch.object(tools, "get_json", fake_get_json):
            result = asyncio.run(
                tools.tool_triage_booking_with_calendar(
                    "token",
                    {
                        "booking_id": "booking-1",
                        "artist_slug": "justin",
                        "start": "2026-08-18",
                        "end": "2026-08-22",
                    },
                )
            )

        self.assertEqual(calls[0][0], "/bookings/booking-1")
        self.assertEqual(calls[1][0], "/calendar/slots/justin")
        self.assertEqual(calls[1][1]["consultation"], "true")
        self.assertIn("Traditional eagle", result)
        self.assertIn("Tue Aug 18 - 11:00 AM", result)
        self.assertIn("review-only triage", result)
        self.assertIn("Do not message", result)



    def test_availability_fails_closed_without_google(self):
        async def fake_get_json(path, token, params=None):
            return {"calendar_connected": False, "slots": [{"label": "fallback"}]}

        with patch.object(tools, "get_json", fake_get_json):
            result = asyncio.run(
                tools.tool_consultation_availability(
                    "token",
                    {
                        "artist_slug": "justin",
                        "start": "2026-08-18",
                        "end": "2026-08-22",
                    },
                )
            )

        self.assertIn("unavailable", result)
        self.assertIn("Do not offer fallback times", result)


if __name__ == "__main__":
    unittest.main()
