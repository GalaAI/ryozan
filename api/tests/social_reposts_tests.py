"""Herald artist repost bridge tests."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import social_reposts
from app.core.security import get_current_user
from app.services.agent_evolution import AgentEvolutionBridgeError, request_ark_bridge


class SocialRepostRouteTests(unittest.TestCase):
    def _client(self, role: str) -> TestClient:
        app = FastAPI()
        app.include_router(social_reposts.router)

        async def current_user():
            return SimpleNamespace(id="founder-1", role=role, name="Justin Walker", email="justin@example.com")

        app.dependency_overrides[get_current_user] = current_user
        return TestClient(app)

    def test_only_founders_and_admins_reach_ark(self):
        with patch.object(social_reposts, "request_ark_bridge", new=AsyncMock()) as request:
            response = self._client("artist").get("/herald/reposts")
        self.assertEqual(response.status_code, 403)
        request.assert_not_awaited()

    def test_list_forwards_a_known_status_filter_only(self):
        with patch.object(
            social_reposts,
            "request_ark_bridge",
            new=AsyncMock(return_value={"ok": True, "drafts": []}),
        ) as request:
            client = self._client("admin")
            self.assertEqual(client.get("/herald/reposts?status=approved").status_code, 200)
            self.assertEqual(request.await_args.args[:2], ("GET", "/api/social/reposts?status=approved"))
            client.get("/herald/reposts?status=../../x")
            self.assertEqual(request.await_args.args[:2], ("GET", "/api/social/reposts"))

    def test_scan_and_review_carry_the_signed_in_operator(self):
        with patch.object(
            social_reposts,
            "request_ark_bridge",
            new=AsyncMock(return_value={"ok": True}),
        ) as request:
            client = self._client("founder")
            client.post("/herald/reposts/scan", json={"handles": ["artist.handle"], "max_per_artist": 2})
            method, path, payload = request.await_args.args
            self.assertEqual((method, path), ("POST", "/api/social/reposts/scan"))
            self.assertEqual(
                payload,
                {
                    "handles": ["artist.handle"],
                    "maxPerArtist": 2,
                    "operatorGate": {"confirmed": True, "approvedBy": "Justin Walker"},
                },
            )
            self.assertEqual(request.await_args.kwargs["timeout"], 120.0)

            client.post("/herald/reposts/repost_18001/review", json={"decision": "approve"})
            method, path, payload = request.await_args.args
            self.assertEqual(path, "/api/social/reposts/repost_18001/review")
            self.assertEqual(payload["decision"], "approve")
            self.assertNotIn("note", payload)

    def test_edit_normalizes_hashtags_and_can_clear_the_schedule(self):
        with patch.object(
            social_reposts,
            "request_ark_bridge",
            new=AsyncMock(return_value={"ok": True}),
        ) as request:
            client = self._client("founder")
            client.patch(
                "/herald/reposts/repost_18001",
                json={
                    "caption": "Rose by @artist.handle.",
                    "hashtags": ["#rose", "greenvillesc"],
                    "scheduled_for": "2026-10-24T15:00:00Z",
                },
            )
            method, path, payload = request.await_args.args
            self.assertEqual(method, "PATCH")
            self.assertEqual(payload["hashtags"], ["rose", "greenvillesc"])
            self.assertEqual(payload["scheduledFor"], "2026-10-24T15:00:00Z")

            client.patch("/herald/reposts/repost_18001", json={"clear_schedule": True})
            self.assertIsNone(request.await_args.args[2]["scheduledFor"])

    def test_rejects_bad_draft_ids_and_surfaces_ark_errors(self):
        client = self._client("founder")
        self.assertEqual(client.post("/herald/reposts/..%2Fscan/publish", json={}).status_code, 404)
        self.assertEqual(client.post("/herald/reposts/REPOST-1/publish", json={}).status_code, 400)
        with patch.object(
            social_reposts,
            "request_ark_bridge",
            new=AsyncMock(side_effect=AgentEvolutionBridgeError(409, "draft-must-be-approved")),
        ) as request:
            response = client.post("/herald/reposts/repost_18001/publish", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "draft-must-be-approved")
        self.assertEqual(request.await_args.kwargs["timeout"], 300.0)


class ArkBridgeRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_issues_become_the_error_detail(self):
        response = SimpleNamespace(
            status_code=400,
            content=b"{}",
            json=lambda: {"ok": False, "issues": [{"path": "caption", "message": "Too long"}]},
        )
        client = AsyncMock()
        client.request = AsyncMock(return_value=response)
        context = AsyncMock()
        context.__aenter__.return_value = client
        settings = SimpleNamespace(
            ARK_PACK_BRIDGE_URL="https://ark.example.test/api/pack-bridge/bttco/intake",
            ARK_PACK_BRIDGE_KEY="bridge-token",
        )
        with (
            patch("app.services.agent_evolution.get_settings", return_value=settings),
            patch("app.services.agent_evolution.httpx.AsyncClient", return_value=context),
        ):
            with self.assertRaises(AgentEvolutionBridgeError) as raised:
                await request_ark_bridge("PATCH", "/api/social/reposts/repost_1", {"caption": "x"})
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(str(raised.exception), "caption: Too long")
        self.assertEqual(
            client.request.await_args.args[:2],
            ("PATCH", "https://ark.example.test/api/social/reposts/repost_1"),
        )


if __name__ == "__main__":
    unittest.main()
