"""Client-side and NPC shop tools on the Ryozan MCP.

Net-new file: mcp_shop_write_tests covers staff booking writes. This contract
covers the tools a person uses as themselves (the Eden matrix NPCs use the same
ones): the ryozan.client.write scope, caller-bound flash booking, in-app
messages, time blocks, approvals and flash edits.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.mcp import tools, upstream
from app.mcp.oauth import CLIENT_WRITE_SCOPE, SHOP_READ_SCOPE, SHOP_WRITE_SCOPE, _grantable_scopes


def _run_with_fakes(coro_fn, token, args, get_replies=None, write_replies=None):
    calls = []

    async def fake_get_json(path, token, params=None):
        calls.append(("GET", path, params))
        return (get_replies or {})[path]

    async def fake_write_json(path, token, method, payload):
        calls.append((method, path, payload))
        return (write_replies or {}).get(path, {})

    with patch.object(tools, "get_json", fake_get_json), patch.object(tools, "write_json", fake_write_json):
        body = asyncio.run(coro_fn(token, args))
    return calls, body


ARTISTS = [
    {"id": "j-1", "slug": "justin", "name": "Justin Walker"},
    {"id": "a-1", "slug": "sample-artist", "name": "Sample Artist"},
]


class McpClientWriteTests(unittest.TestCase):
    def test_every_role_can_grant_client_write(self):
        for role in ("collector", "assistant", "artist", "admin", "founder"):
            granted = _grantable_scopes({SHOP_READ_SCOPE, CLIENT_WRITE_SCOPE}, role)
            self.assertEqual(granted, {SHOP_READ_SCOPE, CLIENT_WRITE_SCOPE}, role)

    def test_client_tools_need_client_write_and_staff_tools_stay_staff(self):
        collector = {SHOP_READ_SCOPE, CLIENT_WRITE_SCOPE}
        for name in ("ryozan_request_booking", "ryozan_book_flash", "ryozan_start_conversation", "ryozan_post_message"):
            self.assertTrue(tools.tool_allowed(name, "collector", collector), name)
            self.assertFalse(tools.tool_allowed(name, "collector", {SHOP_READ_SCOPE}), name)
        for name in ("ryozan_block_time", "ryozan_approve_booking", "ryozan_update_flash"):
            self.assertFalse(tools.tool_allowed(name, "collector", collector | {SHOP_WRITE_SCOPE}), name)
            self.assertTrue(tools.tool_allowed(name, "artist", {SHOP_READ_SCOPE, SHOP_WRITE_SCOPE}), name)

    def test_every_spec_has_a_policy_and_a_handler(self):
        for spec in tools.TOOL_SPECS:
            self.assertIn(spec["name"], tools.TOOL_ACCESS_POLICY)
            self.assertTrue(callable(spec["handler"]))

    def test_book_flash_always_uses_the_callers_own_name_and_email(self):
        calls, body = _run_with_fakes(
            tools.tool_book_flash,
            "collector-token",
            {"design_id": "d-1", "scheduled_date": "2026-11-20", "scheduled_time": "13:00"},
            get_replies={"/auth/me": {"name": "Sample Collector", "email": "sample-collector@example.com"}},
            write_replies={"/flash/book": {"booking_reference": "BKG-1", "checkout_required": True}},
        )
        method, path, payload = calls[1]
        self.assertEqual((method, path), ("POST", "/flash/book"))
        self.assertEqual(payload["guest_email"], "sample-collector@example.com")
        self.assertEqual(payload["guest_name"], "Sample Collector")
        self.assertFalse(payload["pay_full"])
        self.assertEqual(json.loads(body)["booking_reference"], "BKG-1")

    def test_start_conversation_opens_the_thread_then_posts_the_first_message(self):
        calls, body = _run_with_fakes(
            tools.tool_start_conversation,
            "artist-token",
            {"participant_ids": ["owner-1"], "content": "Can I take Friday off?"},
            write_replies={"/conversations/": {"id": "c-1"}, "/conversations/c-1/messages": {"id": "m-1"}},
        )
        self.assertEqual(
            calls,
            [
                ("POST", "/conversations/", {"participant_ids": ["owner-1"], "is_group": False}),
                ("POST", "/conversations/c-1/messages", {"content": "Can I take Friday off?"}),
            ],
        )
        self.assertEqual(json.loads(body), {"conversation_id": "c-1", "message_id": "m-1"})

    def test_block_time_posts_only_the_given_fields(self):
        calls, _ = _run_with_fakes(
            tools.tool_block_time,
            "owner-token",
            {"artist_id": "Sample Artist", "block_date": "2026-11-21", "all_day": True, "reason": "Time off"},
            get_replies={"/artists/": ARTISTS},
            write_replies={"/calendar/time-blocks": {"id": "t-1", "block_date": "2026-11-21", "all_day": True}},
        )
        self.assertEqual(
            calls[1:],
            [
                (
                    "POST",
                    "/calendar/time-blocks",
                    {"artist_id": "a-1", "block_date": "2026-11-21", "all_day": True, "reason": "Time off"},
                )
            ],
        )

    def test_approve_reads_the_approval_result_shape(self):
        _, body = _run_with_fakes(
            tools.tool_approve_booking,
            "owner-token",
            {"booking_id": "b-1", "scheduled_date": "2026-11-22", "scheduled_time": "12:00", "deposit_amount": 0},
            write_replies={
                "/bookings/b-1/approve": {
                    "booking": {"id": "b-1"},
                    "status": "confirmed",
                    "scheduled_date": "2026-11-22",
                    "scheduled_time": "12:00",
                    "deposit_amount": 0,
                    "deposit_link_sent": False,
                }
            },
        )
        self.assertEqual(json.loads(body)["status"], "confirmed")

    def test_update_flash_refuses_an_empty_change(self):
        calls, body = _run_with_fakes(tools.tool_update_flash, "owner-token", {"design_id": "d-1"})
        self.assertEqual(calls, [])
        self.assertIn("Nothing to change", body)


class McpCloneFindingsTests(unittest.TestCase):
    """Defects the Eden matrix Clones found in their first runs."""

    def test_an_artist_can_be_named_by_id_slug_full_name_or_first_name(self):
        for named in ("j-1", "justin", "Justin Walker", "Justin"):
            calls, _ = _run_with_fakes(
                tools.tool_request_booking,
                "collector-token",
                {"artist_id": named, "description": "Dragon with Umbrella flash"},
                get_replies={"/artists/": ARTISTS},
                write_replies={"/bookings/": {"id": "b-1", "status": "pending"}},
            )
            self.assertEqual(calls[-1][2]["artist_id"], "j-1", named)

    def test_an_unknown_artist_is_refused_with_a_way_forward(self):
        with self.assertRaises(upstream.UpstreamError) as ctx:
            _run_with_fakes(
                tools.tool_find_slots,
                "t",
                {"artist_slug": "Nobody", "start": "2026-11-18", "end": "2026-11-18"},
                get_replies={"/artists/": ARTISTS},
            )
        self.assertIn("ryozan_list_artists", str(ctx.exception))

    def test_a_text_search_reads_a_full_page_before_it_applies_the_limit(self):
        rows = [{"id": f"b-{i}", "status": "pending", "client_name": "Walk-in"} for i in range(5)]
        rows.append({"id": "b-9", "status": "pending", "client_name": "Sample Collector"})
        calls, body = _run_with_fakes(
            tools.tool_search_bookings,
            "owner-token",
            {"status": "pending", "query": "Sample Collector", "limit": 1},
            get_replies={"/bookings/": {"bookings": rows, "total": 6}},
        )
        self.assertEqual(calls[0][2]["limit"], 100)
        self.assertIn("1 booking(s) shown", body)
        self.assertIn("b-9", body)


class McpWriteRetryTests(unittest.TestCase):
    """A write is sent again only when it never reached the API."""

    def _write(self, responses):
        sent = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def request(self, method, url, headers=None, json=None):
                sent.append(url)
                outcome = responses.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        async def no_sleep(_seconds):
            return None

        with (
            patch.object(upstream.httpx, "AsyncClient", FakeClient),
            patch.object(upstream.asyncio, "sleep", no_sleep),
        ):
            try:
                return sent, asyncio.run(upstream.write_json("/flash/book", "t", "POST", {})), None
            except upstream.UpstreamError as exc:
                return sent, None, str(exc)

    def test_a_502_is_reported_once_with_the_routes_reason(self):
        request = httpx.Request("POST", "http://api/api/v1/flash/book")
        reply = httpx.Response(502, json={"detail": "Payment provider unavailable"}, request=request)
        sent, _, error = self._write([reply])
        self.assertEqual(len(sent), 1)
        self.assertEqual(error, "Ryozan returned 502: Payment provider unavailable")

    def test_a_timeout_is_not_resent(self):
        sent, _, error = self._write([httpx.ReadTimeout("slow")])
        self.assertEqual(len(sent), 1)
        self.assertIn("may have been applied", error)

    def test_a_connection_refusal_is_retried(self):
        request = httpx.Request("POST", "http://api/api/v1/flash/book")
        sent, body, _ = self._write([httpx.ConnectError("down"), httpx.Response(200, json={"ok": 1}, request=request)])
        self.assertEqual(len(sent), 2)
        self.assertEqual(body, {"ok": 1})

    def test_a_refusal_carries_the_routes_reason(self):
        request = httpx.Request("POST", "http://api/api/v1/flash/book")
        reply = httpx.Response(409, json={"detail": "Slot is no longer available"}, request=request)
        _, _, error = self._write([reply])
        self.assertEqual(error, "Nothing was changed: Slot is no longer available")


if __name__ == "__main__":
    unittest.main()
