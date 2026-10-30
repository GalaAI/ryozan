"""MCP Streamable HTTP transport tests."""

import asyncio
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.mcp import oauth
from main import app

FOUNDER_SCOPES = {
    "ryozan.shop.read",
    "ryozan.shop.send",
    "ryozan.dev.read",
    "ryozan.dev.write",
    "ryozan.service.read",
}


def rpc(client, method, params=None, request_id=1, authenticated=True):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    headers = {"Authorization": "Bearer test-token"} if authenticated else {}
    return client.post("/mcp", json=body, headers=headers)


class AuthenticatedMcpCase(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch(
            "app.mcp.router.authenticate_mcp_token",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    role="founder",
                    scopes=FOUNDER_SCOPES,
                    site_token="site-token",
                    user=None,
                )
            ),
        )
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.client = TestClient(app)


class McpHandshakeTests(AuthenticatedMcpCase):
    def test_initialize_negotiates_protocol(self):
        response = rpc(self.client, "initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "ryozan")
        self.assertIn("tools", result["capabilities"])

    def test_initialize_falls_back_on_unknown_protocol(self):
        response = rpc(self.client, "initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(response.json()["result"]["protocolVersion"], "2025-06-18")

    def test_notification_returns_202_with_no_body(self):
        response = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 202)

    def test_get_is_405_not_spa_html(self):
        response = self.client.get("/mcp")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["allow"], "POST")

    def test_malformed_json_is_parse_error(self):
        response = self.client.post("/mcp", content=b"not json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], -32700)


class McpToolSurfaceTests(AuthenticatedMcpCase):
    def setUp(self):
        super().setUp()
        self.tools = rpc(self.client, "tools/list").json()["result"]["tools"]

    def test_expected_tools_are_listed(self):
        names = {tool["name"] for tool in self.tools}
        self.assertEqual(
            names,
            {
                "ryozan_today",
                "ryozan_search_bookings",
                "ryozan_get_booking",
                "ryozan_list_threads",
                "ryozan_read_thread",
                "ryozan_draft_reply",
                "ryozan_send_channel_message",
                "ryozan_search_contacts",
                "ryozan_service_health",
                "ryozan_consultation_availability",
                "ryozan_triage_booking_with_calendar",
                "ryozan_messaging_manifest",
                "ryozan_foundry_state",
                "ryozan_foundry_connectors",
                "ryozan_foundry_connector_action",
                "ryozan_foundry_put_state",
                "ryozan_foundry_approve_outbox",
                "ryozan_foundry_assist",
                "ryozan_dev_workspaces",
                "ryozan_dev_workspace_status",
                "ryozan_dev_feature_matrix",
                "ryozan_dev_todos",
                "ryozan_dev_recent_log",
                "ryozan_dev_scope_manifest",
                "ryozan_dev_create_todo",
                "ryozan_dev_update_todo",
                "ryozan_dev_append_log",
                "ryozan_dev_upsert_feature",
            },
        )

    def test_shop_tools_stay_read_only_while_dev_coordination_has_writes(self):
        write_tools = {
            "ryozan_dev_create_todo",
            "ryozan_dev_update_todo",
            "ryozan_dev_append_log",
            "ryozan_dev_upsert_feature",
            "ryozan_send_channel_message",
            "ryozan_foundry_put_state",
            "ryozan_foundry_connector_action",
            "ryozan_foundry_approve_outbox",
            "ryozan_foundry_assist",
        }
        for tool in self.tools:
            self.assertFalse(tool["annotations"]["destructiveHint"], f"{tool['name']} is destructive")
            self.assertEqual(tool["name"] not in write_tools, tool["annotations"]["readOnlyHint"])
        forbidden = ("delete", "cancel", "refund", "charge")
        for tool in self.tools:
            for word in forbidden:
                self.assertNotIn(word, tool["name"], f"{tool['name']} looks like a mutating tool")
        send_tool = next(tool for tool in self.tools if tool["name"] == "ryozan_send_channel_message")
        self.assertFalse(send_tool["annotations"]["readOnlyHint"])
        self.assertTrue(send_tool["annotations"]["openWorldHint"])

    def test_dev_context_tools_are_exposed(self):
        names = {tool["name"] for tool in self.tools}
        self.assertTrue(
            {
                "ryozan_dev_workspaces",
                "ryozan_dev_workspace_status",
                "ryozan_dev_feature_matrix",
                "ryozan_dev_todos",
                "ryozan_dev_recent_log",
                "ryozan_dev_scope_manifest",
                "ryozan_dev_create_todo",
                "ryozan_dev_update_todo",
                "ryozan_dev_append_log",
                "ryozan_dev_upsert_feature",
            }.issubset(names)
        )

    def test_every_tool_has_a_trigger_rich_description(self):
        for tool in self.tools:
            self.assertGreater(len(tool["description"]), 80, f"{tool['name']} description is too thin")
            self.assertIn("inputSchema", tool)

    def test_unknown_tool_is_invalid_params(self):
        response = rpc(self.client, "tools/call", {"name": "ryozan_nope", "arguments": {}})
        self.assertEqual(response.json()["error"]["code"], -32602)

    def test_unknown_method_is_method_not_found(self):
        self.assertEqual(rpc(self.client, "resources/list").json()["error"]["code"], -32601)

    def test_send_tool_reuses_courier_channel_send_and_returns_provider_receipt(self):
        with patch(
            "app.mcp.tools.write_json",
            new=AsyncMock(
                return_value={
                    "id": "message-1",
                    "metadata_json": '{"provider":"twilio","delivery_status":"queued","twilio_sid":"SM123"}',
                }
            ),
        ) as write:
            response = rpc(
                self.client,
                "tools/call",
                {
                    "name": "ryozan_send_channel_message",
                    "arguments": {
                        "conversation_id": "conversation-1",
                        "channel": "sms",
                        "content": "Your artist will follow up shortly.",
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        text = response.json()["result"]["content"][0]["text"]
        self.assertIn('"message_id": "message-1"', text)
        self.assertIn('"twilio_sid": "SM123"', text)
        write.assert_awaited_once_with(
            "/conversations/conversation-1/channel-send",
            "site-token",
            "POST",
            {
                "channel": "sms",
                "content": "Your artist will follow up shortly.",
                "is_transactional": True,
            },
        )

    def test_foundry_assist_uses_the_ryozan_gateway_endpoint(self):
        payload = {
            "system": "Return JSON only.",
            "messages": [{"role": "user", "content": "Draft this email."}],
            "max_tokens": 800,
        }
        with patch(
            "app.mcp.tools.write_json",
            new=AsyncMock(return_value={"text": '{"fields":{"body":"Proof"}}'}),
        ) as write:
            response = rpc(
                self.client,
                "tools/call",
                {"name": "ryozan_foundry_assist", "arguments": payload},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '"text": "{\\"fields\\":{\\"body\\":\\"Proof\\"}}"', response.json()["result"]["content"][0]["text"]
        )
        write.assert_awaited_once_with("/email-templates/foundry/assist", "site-token", "POST", payload)


class McpAuthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_service_health_does_not_require_account_auth(self):
        with patch(
            "app.mcp.tools.get_health",
            new=AsyncMock(
                return_value={
                    "status": "ready",
                    "deploy": {"branch": "main", "commit": "test-sha"},
                    "checks": {},
                }
            ),
        ):
            response = rpc(
                self.client,
                "tools/call",
                {"name": "ryozan_service_health", "arguments": {}},
                authenticated=False,
            )
        self.assertEqual(response.status_code, 200)
        text = response.json()["result"]["content"][0]["text"]
        self.assertIn("Ryozan service health", text)

    def test_missing_token_returns_oauth_challenge(self):
        response = rpc(
            self.client,
            "tools/call",
            {"name": "ryozan_today", "arguments": {}},
            authenticated=False,
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("resource_metadata", response.headers["www-authenticate"])

    def test_missing_required_argument_is_reported_cleanly(self):
        with patch(
            "app.mcp.router.authenticate_mcp_token",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    role="founder",
                    scopes=FOUNDER_SCOPES,
                    site_token="site-token",
                    user=None,
                )
            ),
        ):
            response = rpc(self.client, "tools/call", {"name": "ryozan_get_booking", "arguments": {}})
            result = response.json()["result"]
            self.assertIn("booking_id is required", result["content"][0]["text"])

    def test_staff_discovery_is_role_filtered(self):
        from app.mcp.tools import public_tool_list

        names = {tool["name"] for tool in public_tool_list("assistant", {"ryozan.shop.read", "ryozan.service.read"})}
        self.assertIn("ryozan_search_contacts", names)
        self.assertIn("ryozan_triage_booking_with_calendar", names)
        self.assertIn("ryozan_consultation_availability", names)
        self.assertNotIn("ryozan_messaging_manifest", names)
        self.assertNotIn("ryozan_dev_workspaces", names)
        self.assertNotIn("ryozan_dev_create_todo", names)
        self.assertNotIn("ryozan_send_channel_message", names)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeTokenDb:
    def __init__(self, record, user):
        self.record = record
        self.user = user
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def execute(self, _query):
        return _ScalarResult(self.record)

    async def get(self, _model, _item_id):
        return self.user


class McpOAuthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_authorize_defaults_missing_resource_to_mcp_resource(self):
        with patch(
            "app.mcp.oauth._load_client",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    client_id="client-1",
                    client_name="Fixture client",
                    redirect_uris=["http://127.0.0.1/callback"],
                )
            ),
        ):
            response = self.client.get(
                "/oauth/authorize",
                params={
                    "client_id": "client-1",
                    "redirect_uri": "http://127.0.0.1/callback",
                    "response_type": "code",
                    "code_challenge": "x" * 43,
                    "code_challenge_method": "S256",
                    "scope": "ryozan.shop.read",
                },
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("mcp_oauth_request=", response.headers["location"])

    def test_refresh_uses_stored_resource_when_client_omits_resource(self):
        record = SimpleNamespace(
            revoked_at=None,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            client_id="client-1",
            resource="https://api.ryozan.app/mcp",
            user_id=uuid.uuid4(),
            scope="ryozan.shop.read",
            created_at=datetime.now(UTC),
        )
        user = SimpleNamespace(id=record.user_id, role="collector", email_verified_at=record.created_at)

        async def override_db():
            yield _FakeTokenDb(record, user)

        app.dependency_overrides[oauth.get_db] = override_db
        try:
            with (
                patch(
                    "app.mcp.oauth.MCP_RESOURCE",
                    "https://api.ryozan.app/mcp",
                ),
                patch(
                    "app.mcp.oauth._load_client",
                    new=AsyncMock(return_value=SimpleNamespace(client_id="client-1")),
                ),
                patch(
                    "app.mcp.oauth._issue_tokens",
                    new=AsyncMock(return_value={"access_token": "access", "refresh_token": "next"}),
                ) as issue,
            ):
                response = self.client.post(
                    "/oauth/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": "client-1",
                        "refresh_token": "refresh",
                    },
                )
        finally:
            app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        issue.assert_awaited_once()
        self.assertEqual(issue.await_args.args[4], "https://api.ryozan.app/mcp")


class McpMailboxOwnershipTests(unittest.TestCase):
    def _user(self, *, role="collector", verified=True):
        return SimpleNamespace(
            id=uuid.uuid4(),
            role=role,
            password_hash="fixture-password-hash",
            email_verified_at=datetime.now(UTC) if verified else None,
        )

    def _exchange(self, grant_type, user, *, stale=False):
        now = datetime.now(UTC)
        record = SimpleNamespace(
            user_id=user.id,
            client_id="fixture-client",
            resource=oauth.MCP_RESOURCE,
            scope=oauth.SHOP_READ_SCOPE,
            expires_at=now + timedelta(minutes=5),
            created_at=now - timedelta(days=1) if stale else now,
            revoked_at=None,
            used_at=None,
            redirect_uri="http://127.0.0.1/callback",
            code_challenge=oauth._pkce_challenge("v" * 43),
        )
        db = _FakeTokenDb(record, user)

        async def override_db():
            yield db

        app.dependency_overrides[oauth.get_db] = override_db
        try:
            with patch.object(oauth, "_load_client", new=AsyncMock(return_value=SimpleNamespace())):
                response = TestClient(app).post(
                    "/oauth/token",
                    data={
                        "grant_type": grant_type,
                        "client_id": record.client_id,
                        "refresh_token": "fixture-refresh",
                        "code": "fixture-code",
                        "code_verifier": "v" * 43,
                        "redirect_uri": record.redirect_uri,
                    },
                )
        finally:
            app.dependency_overrides.clear()
        return response, db

    def test_historical_mcp_access_cannot_authenticate_unverified_collector(self):
        user = self._user(verified=False)
        token = oauth._create_mcp_access_token(user, "fixture-client", oauth.SHOP_READ_SCOPE, oauth.MCP_RESOURCE)
        with patch.object(oauth, "async_session", return_value=_FakeTokenDb(None, user)):
            self.assertIsNone(asyncio.run(oauth.authenticate_mcp_token(token)))

    def test_email_proof_removal_denies_existing_access_but_staff_remains_available(self):
        for role in ("collector", "artist"):
            with self.subTest(role=role):
                user = self._user(role=role)
                token = oauth._create_mcp_access_token(
                    user, "fixture-client", oauth.SHOP_READ_SCOPE, oauth.MCP_RESOURCE
                )
                with patch.object(oauth, "async_session", return_value=_FakeTokenDb(None, user)):
                    self.assertIsNotNone(asyncio.run(oauth.authenticate_mcp_token(token)))
                    user.email_verified_at = None
                    actor = asyncio.run(oauth.authenticate_mcp_token(token))
                if role == "collector":
                    self.assertIsNone(actor)
                else:
                    self.assertEqual(actor.role, role)

    def test_preclaimant_grants_stay_denied_after_mailbox_owner_verifies(self):
        for grant_type in ("authorization_code", "refresh_token"):
            for verified in (False, True):
                with self.subTest(grant_type=grant_type, verified=verified):
                    response, db = self._exchange(grant_type, self._user(verified=verified), stale=True)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(response.json()["error"], "invalid_grant")
                    self.assertEqual(db.added, [])

    def test_current_verified_collector_and_existing_staff_grants_remain_available(self):
        for grant_type in ("authorization_code", "refresh_token"):
            for role in ("collector", "artist"):
                with self.subTest(grant_type=grant_type, role=role):
                    user = self._user(role=role, verified=role == "collector")
                    response, db = self._exchange(grant_type, user, stale=role == "artist")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn("access_token", response.json())
                    self.assertEqual(len(db.added), 1)


if __name__ == "__main__":
    unittest.main()
