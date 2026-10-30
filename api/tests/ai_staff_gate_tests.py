"""Authenticated AI access and unattended-reply gate tests.

The model transport is covered separately. This file proves every authenticated
account role reaches its allowed agent surface while client-facing automation
remains independently fail-closed.
"""

import io
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException
from PIL import Image

from app.api.v1.endpoints import conversations, gala_chat
from app.core.config import Settings
from app.models.user import BTTCOUser
from app.services import ai_routing


def _settings(auto_reply_enabled=False):
    return Settings(
        ARK_MODEL_GATEWAY_BASE_URL="http://127.0.0.1:8788/v1",
        ARK_MODEL_GATEWAY_KEY="test-key",
        GALA_AUTO_REPLY_ENABLED=auto_reply_enabled,
    )


def _user(role):
    return BTTCOUser(id=uuid.uuid4(), email=f"{role}@example.com", name=role, role=role)


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color="black").save(buffer, format="PNG")
    return buffer.getvalue()


class _GatePassedError(Exception):
    """Raised past the gate to prove the request was NOT blocked."""


class _RecordingSessionFactory:
    """Stands in for async_session, recording whether a session was ever opened."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self

    async def __aenter__(self):
        raise _GatePassedError("session opened past the gate")

    async def __aexit__(self, *exc):
        return False


class AIAccessGatePolicyTests(unittest.TestCase):
    """The shared authenticated-account boundary in app.services.ai_routing."""

    def test_every_authenticated_account_role_reaches_role_scoped_ai(self):
        s = _settings()
        for role in ("collector", "assistant", "artist", "admin", "founder"):
            with self.subTest(role=role):
                self.assertTrue(ai_routing.ai_access_allowed(s, _user(role)))
                ai_routing.require_ai_access(s, _user(role))  # must not raise

    def test_anonymous_automation_never_inherits_account_access(self):
        self.assertFalse(ai_routing.ai_access_allowed(_settings(auto_reply_enabled=True), None))
        with self.assertRaises(HTTPException) as ctx:
            ai_routing.require_ai_access(_settings(), None)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("authenticated Ryozan account", ctx.exception.detail)


class GalaChatEndpointGateTests(unittest.IsolatedAsyncioTestCase):
    """The authenticated Portal chat endpoint for every account role."""

    async def _call_gala_chat(self, role):
        body = gala_chat.ChatRequest(message="hi")
        with (
            patch.object(gala_chat, "get_settings", return_value=_settings()),
            patch(
                "app.services.api_budget.check_budget",
                new=AsyncMock(side_effect=_GatePassedError()),
            ),
        ):
            await gala_chat.gala_chat(body, db=object(), current_user=_user(role))

    async def test_every_role_reaches_its_agent_and_model_path(self):
        # _GatePassedError means execution got past the gate to the budget check.
        for role in ("collector", "assistant", "artist", "admin", "founder"):
            with self.subTest(role=role), self.assertRaises(_GatePassedError):
                await self._call_gala_chat(role)

    async def test_exhausted_gateway_routes_return_retryable_http_failure(self):
        body = gala_chat.ChatRequest(
            message="What needs attention?",
            context={"agent_id": "booky"},
        )
        lane = SimpleNamespace(
            primary_model="ark/primary",
            fallback_models=["ark/fallback"],
            effective_tier="staff",
        )
        with (
            patch.object(gala_chat, "get_settings", return_value=_settings()),
            patch("app.services.api_budget.check_budget", new=AsyncMock()),
            patch.object(gala_chat, "resolve_ai_lane", new=AsyncMock(return_value=lane)),
            patch.object(gala_chat, "build_memory_context", new=AsyncMock(return_value="")),
            patch.object(
                gala_chat,
                "call_ark_gateway_chat_completion",
                new=AsyncMock(side_effect=HTTPException(502, "all routes failed")),
            ),
            self.assertRaises(HTTPException) as ctx,
        ):
            await gala_chat.gala_chat(body, db=object(), current_user=_user("founder"))

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["code"], "model_gateway_unavailable")
        self.assertEqual(
            ctx.exception.detail["message"],
            "The AI service is temporarily unavailable. Please try again.",
        )
        self.assertTrue(ctx.exception.detail["retryable"])
        self.assertTrue(ctx.exception.detail["correlation_id"].startswith("gala-"))


class GalaActionIntentContractTests(unittest.IsolatedAsyncioTestCase):
    """Typed Portal actions must execute once or fail without a success receipt."""

    async def test_founder_business_stats_executes_exactly_once_without_model_selection(self):
        body = gala_chat.ChatRequest(
            message="Give me a quick business overview.",
            action_intent="business_stats.read",
            context={"agent_id": "gala"},
        )
        execute = AsyncMock(
            return_value=(
                "Current business totals.",
                [{"type": "stat-card", "data": {"total_bookings": 4}}],
            )
        )
        gateway = AsyncMock()

        with (
            patch.object(gala_chat, "get_settings", return_value=_settings()),
            patch.object(gala_chat, "execute_tool", new=execute),
            patch.object(gala_chat, "call_ark_gateway_chat_completion", new=gateway),
        ):
            result = await gala_chat.gala_chat(body, db=object(), current_user=_user("founder"))

        execute.assert_awaited_once()
        call = execute.await_args
        self.assertEqual(call.args[0], "get_business_stats")
        self.assertEqual(call.args[1], {})
        self.assertEqual(call.kwargs["agent_id"], "gala")
        gateway.assert_not_awaited()
        self.assertEqual(result.tool_calls[0]["intent_id"], "business_stats.read")
        self.assertEqual(result.tool_calls[0]["tool"], "get_business_stats")
        self.assertEqual(result.tool_calls[0]["status"], "succeeded")
        self.assertTrue(result.tool_calls[0]["correlation_id"].startswith("gala-action-"))

    async def test_schedule_action_uses_each_accounts_server_owned_agent(self):
        execute = AsyncMock(return_value=("Current schedule.", []))
        expected_agents = {
            "founder": "gala",
            "admin": "gala",
            "artist": "spyder",
            "assistant": "spyder",
            "collector": "smoku",
        }

        with (
            patch.object(gala_chat, "get_settings", return_value=_settings()),
            patch.object(gala_chat, "execute_tool", new=execute),
        ):
            for role, expected_agent in expected_agents.items():
                with self.subTest(role=role):
                    execute.reset_mock()
                    result = await gala_chat.gala_chat(
                        gala_chat.ChatRequest(message="Schedule", action_intent="schedule.read"),
                        db=object(),
                        current_user=_user(role),
                    )
                    execute.assert_awaited_once()
                    self.assertEqual(execute.await_args.args[0], "get_schedule")
                    self.assertEqual(execute.await_args.kwargs["agent_id"], expected_agent)
                    self.assertEqual(result.tool_calls[0]["agent_id"], expected_agent)

    async def test_forbidden_action_returns_403_without_execution(self):
        execute = AsyncMock()
        body = gala_chat.ChatRequest(
            message="Business stats",
            action_intent="business_stats.read",
            context={"agent_id": "smoku"},
        )
        with (
            patch.object(gala_chat, "get_settings", return_value=_settings()),
            patch.object(gala_chat, "execute_tool", new=execute),
            self.assertRaises(HTTPException) as ctx,
        ):
            await gala_chat.gala_chat(body, db=object(), current_user=_user("collector"))

        self.assertEqual(ctx.exception.status_code, 403)
        execute.assert_not_awaited()

    def test_unsupported_action_is_rejected_by_the_server_contract(self):
        with self.assertRaises(ValueError):
            gala_chat.ChatRequest(message="Do it", action_intent="pretend.success")

    async def test_failed_action_returns_502_and_no_success_response(self):
        execute = AsyncMock(side_effect=RuntimeError("database unavailable"))
        body = gala_chat.ChatRequest(message="Schedule", action_intent="schedule.read")
        with (
            patch.object(gala_chat, "get_settings", return_value=_settings()),
            patch.object(gala_chat, "execute_tool", new=execute),
            self.assertRaises(HTTPException) as ctx,
        ):
            await gala_chat.gala_chat(body, db=object(), current_user=_user("artist"))

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["code"], "gala_action_failed")
        self.assertIn("no success receipt", ctx.exception.detail["message"])
        self.assertTrue(ctx.exception.detail["retryable"])
        self.assertTrue(ctx.exception.detail["correlation_id"].startswith("gala-action-"))


class GalaChatGatewayFailureTests(unittest.IsolatedAsyncioTestCase):
    async def _call_chat(self, gateway, *, execute_tool=None):
        body = gala_chat.ChatRequest(message="hi", context={"agent_id": "xar"})
        lane = SimpleNamespace(
            primary_model="ark/test",
            fallback_models=[],
            effective_tier="test",
        )
        patches = [
            patch.object(gala_chat, "get_settings", return_value=_settings()),
            patch("app.services.api_budget.check_budget", new=AsyncMock()),
            patch("app.services.api_budget.log_usage", new=AsyncMock()),
            patch.object(gala_chat, "resolve_ai_lane", new=AsyncMock(return_value=lane)),
            patch.object(gala_chat, "build_memory_context", new=AsyncMock(return_value="")),
            patch.object(gala_chat, "call_ark_gateway_chat_completion", new=gateway),
        ]
        if execute_tool is not None:
            patches.append(patch.object(gala_chat, "execute_tool", new=execute_tool))

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            if execute_tool is None:
                return await gala_chat.gala_chat(body, db=object(), current_user=_user("admin"))
            with patches[6]:
                return await gala_chat.gala_chat(body, db=object(), current_user=_user("admin"))

    async def test_gateway_exception_returns_safe_typed_503_with_correlation_id(self):
        provider_error = "raw provider secret response"
        gateway = AsyncMock(side_effect=RuntimeError(provider_error))

        with self.assertRaises(HTTPException) as ctx:
            await self._call_chat(gateway)

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail["code"], "model_gateway_unavailable")
        self.assertTrue(ctx.exception.detail["retryable"])
        self.assertTrue(ctx.exception.detail["correlation_id"].startswith("gala-"))
        self.assertNotIn(provider_error, str(ctx.exception.detail))

    async def test_empty_gateway_output_returns_typed_502(self):
        gateway = AsyncMock(return_value={"choices": [{"message": {"content": ""}}]})

        with self.assertRaises(HTTPException) as ctx:
            await self._call_chat(gateway)

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["code"], "model_gateway_empty_output")
        self.assertTrue(ctx.exception.detail["correlation_id"].startswith("gala-"))

    async def test_unreceipted_completed_action_is_replaced_with_honest_response(self):
        gateway = AsyncMock(
            return_value={"choices": [{"message": {"content": "I've routed your refund request."}}]}
        )

        response = await self._call_chat(gateway)

        self.assertIn("no matching tool receipt", response.response)
        self.assertNotIn("I've routed", response.response)

    async def test_unverified_future_action_is_replaced_with_honest_response(self):
        gateway = AsyncMock(
            return_value={"choices": [{"message": {"content": "They will contact you directly."}}]}
        )

        response = await self._call_chat(gateway)

        self.assertIn("no matching tool receipt", response.response)

    async def test_matching_notification_receipt_allows_completed_send_claim(self):
        gateway = AsyncMock(
            side_effect=[
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {"name": "send_notification", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                },
                {"choices": [{"message": {"content": "I sent the notification."}}]},
            ]
        )
        execute_tool = AsyncMock(return_value=("Notification sent", []))

        response = await self._call_chat(gateway, execute_tool=execute_tool)

        self.assertEqual(response.response, "I sent the notification.")
        self.assertEqual(response.tool_calls[0]["tool"], "send_notification")

    async def test_tool_loop_exhaustion_returns_typed_502(self):
        gateway = AsyncMock(
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {"name": "lookup_bookings", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        )
        execute_tool = AsyncMock(return_value=("tool result", []))

        with self.assertRaises(HTTPException) as ctx:
            await self._call_chat(gateway, execute_tool=execute_tool)

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["code"], "model_gateway_loop_exhausted")
        self.assertTrue(ctx.exception.detail["correlation_id"].startswith("gala-"))
        self.assertEqual(gateway.await_count, 5)


class GalaUploadFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_rejection_returns_retryable_503_without_fake_success(self):
        upload = SimpleNamespace(
            filename="reference.png",
            content_type="image/png",
            read=AsyncMock(side_effect=[b"image-bytes", b""]),
        )

        with (
            patch("app.services.storage.upload_file", new=AsyncMock(return_value=None)),
            self.assertRaises(HTTPException) as ctx,
        ):
            await gala_chat.gala_smart_upload(
                file=upload,
                category="other",
                note="portal attachment",
                memory_consent=False,
                db=object(),
                current_user=_user("admin"),
            )

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail["code"], "upload_storage_unavailable")
        self.assertTrue(ctx.exception.detail["retryable"])
        self.assertTrue(ctx.exception.detail["correlation_id"].startswith("gala-upload-"))

    async def test_non_staff_roles_are_rejected_before_reading_or_storing(self):
        for role in ("artist", "collector"):
            upload = SimpleNamespace(
                filename="reference.png",
                content_type="image/png",
                read=AsyncMock(side_effect=[b"image-bytes", b""]),
            )
            store = AsyncMock(return_value="https://assets.example.test/upload.png")
            memory = AsyncMock()

            with (
                self.subTest(role=role),
                patch("app.services.storage.upload_file", new=store),
                patch.object(gala_chat, "_maybe_write_upload_memory", new=memory),
                self.assertRaises(HTTPException) as ctx,
            ):
                await gala_chat.gala_smart_upload(
                    file=upload,
                    category="other",
                    note="",
                    memory_consent=False,
                    db=object(),
                    current_user=_user(role),
                )

            self.assertEqual(ctx.exception.status_code, 403)
            upload.read.assert_not_awaited()
            store.assert_not_awaited()
            memory.assert_not_awaited()

    async def test_upload_over_12_mb_is_rejected_without_storage(self):
        upload = SimpleNamespace(
            filename="too-large.png",
            content_type="image/png",
            read=AsyncMock(
                side_effect=[
                    b"x" * (8 * 1024 * 1024),
                    b"x" * (5 * 1024 * 1024),
                    b"",
                ]
            ),
        )
        store = AsyncMock(return_value="https://assets.example.test/too-large.png")
        memory = AsyncMock()

        with (
            patch("app.services.storage.upload_file", new=store),
            patch.object(gala_chat, "_maybe_write_upload_memory", new=memory),
            self.assertRaises(HTTPException) as ctx,
        ):
            await gala_chat.gala_smart_upload(
                file=upload,
                category="portfolio",
                note="",
                memory_consent=True,
                db=object(),
                current_user=_user("admin"),
            )

        self.assertEqual(ctx.exception.status_code, 413)
        store.assert_not_awaited()
        memory.assert_not_awaited()

    async def test_storage_failure_does_not_write_memory_even_with_consent(self):
        image_bytes = _png_bytes()
        upload = SimpleNamespace(
            filename="portfolio.png",
            content_type="image/png",
            read=AsyncMock(side_effect=[image_bytes, b""]),
        )
        memory = AsyncMock()

        with (
            patch("app.services.storage.upload_file", new=AsyncMock(return_value=None)),
            patch.object(gala_chat, "_maybe_write_upload_memory", new=memory),
            self.assertRaises(HTTPException) as ctx,
        ):
            await gala_chat.gala_smart_upload(
                file=upload,
                category="portfolio",
                note="approved for memory",
                memory_consent=True,
                db=object(),
                current_user=_user("founder"),
            )

        self.assertEqual(ctx.exception.status_code, 503)
        memory.assert_not_awaited()

    async def test_success_without_explicit_consent_does_not_write_memory(self):
        image_bytes = _png_bytes()
        upload = SimpleNamespace(
            filename="portfolio.png",
            content_type="image/png",
            read=AsyncMock(side_effect=[image_bytes, b""]),
        )
        url = "https://assets.example.test/portfolio.png"
        memory = AsyncMock()
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

        with (
            patch("app.services.storage.upload_file", new=AsyncMock(return_value=url)),
            patch.object(gala_chat, "_maybe_write_upload_memory", new=memory),
        ):
            result = await gala_chat.gala_smart_upload(
                file=upload,
                category="portfolio",
                note="no consent",
                memory_consent=False,
                db=db,
                current_user=_user("admin"),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["url"], url)
        self.assertEqual(result["category"], "portfolio")
        memory.assert_not_awaited()


class AgentOrchestrationContractTests(unittest.IsolatedAsyncioTestCase):
    """Server-owned identities, role scopes, and close-time compaction gates."""

    def test_dev_access_filters_tools_and_does_not_change_mobile_or_production(self):
        user = _user("founder")
        policy = {"revision": 1, "tools": ["get_schedule"], "scopes": ["schedule:read"]}
        user.nexus_profile = {"desktop_agent_access": {"gala": policy}}
        with patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=True)):
            self.assertEqual(gala_chat._desktop_access(user, "gala", {"surface": "desktop"}), policy)
            self.assertIsNone(gala_chat._desktop_access(user, "gala", {}))
        with patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=False)):
            self.assertIsNone(gala_chat._desktop_access(user, "gala", {"surface": "desktop"}))
        self.assertEqual([tool["function"]["name"] for tool in gala_chat._openai_tools("gala", "founder", policy)], ["get_schedule"])
        with self.assertRaises(HTTPException):
            gala_chat.resolve_action_intent("bookings.read", "gala", "founder", policy)

    async def test_dev_access_is_rechecked_at_tool_execution(self):
        with self.assertRaises(PermissionError):
            await gala_chat.execute_tool("get_schedule", {}, _user("founder"), object(), access={"tools": ["get_schedule"], "scopes": []})

    async def test_access_update_persists_and_rejects_stale_revision(self):
        user = _user("founder")
        user.nexus_profile = {"unrelated": {"preserve": True}}
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(scalar_one=lambda: user)
        body = gala_chat.AgentAccessUpdate(revision=0, tools=["get_schedule"], scopes=["schedule:read"])
        with patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=True)):
            result = await gala_chat.update_agent_access("gala", body, db, user)
            self.assertEqual(result["revision"], 1)
            self.assertEqual(result["tools"], ["get_schedule"])
            self.assertEqual(user.nexus_profile["unrelated"], {"preserve": True})
            db.commit.assert_awaited_once()
            with self.assertRaises(HTTPException) as ctx:
                await gala_chat.update_agent_access("gala", body, db, user)
            self.assertEqual(ctx.exception.status_code, 409)
            db.commit.assert_awaited_once()

    async def test_access_update_rejects_unknown_tools_scopes_and_unauthorized_roles(self):
        with patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=True)):
            for role in ("artist", "assistant", "admin", "collector"):
                with self.assertRaises(HTTPException) as ctx:
                    await gala_chat.get_agent_access("gala", AsyncMock(), _user(role))
                self.assertEqual(ctx.exception.status_code, 403)
            for tools, scopes in [(["fake_tool"], []), ([], ["root:*"]), (["get_schedule"], [])]:
                db = AsyncMock()
                with self.assertRaises(HTTPException) as ctx:
                    await gala_chat.update_agent_access("gala", gala_chat.AgentAccessUpdate(revision=0, tools=tools, scopes=scopes), db, _user("founder"))
                self.assertEqual(ctx.exception.status_code, 422)
                db.commit.assert_not_awaited()
            with self.assertRaises(HTTPException) as ctx:
                await gala_chat.get_agent_access("docbot", AsyncMock(), _user("founder"))
            self.assertEqual(ctx.exception.status_code, 404)
        with patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=False)):
            # Outside local development the founder still reads the agent's tools
            # (the Scopes & tools card), but saved policies are neither applied nor editable.
            founder = _user("founder")
            founder.nexus_profile = {"desktop_agent_access": {"gala": {"revision": 4, "tools": [], "scopes": []}}}
            snapshot = await gala_chat.get_agent_access("gala", AsyncMock(), founder)
            self.assertFalse(snapshot["editable"])
            self.assertEqual(snapshot["environment"], "standard")
            self.assertEqual(snapshot["revision"], 0)
            self.assertIn("get_schedule", snapshot["tools"])
            with self.assertRaises(HTTPException) as ctx:
                await gala_chat.get_agent_access("gala", AsyncMock(), _user("artist"))
            self.assertEqual(ctx.exception.status_code, 403)
            db = AsyncMock()
            with self.assertRaises(HTTPException) as ctx:
                await gala_chat.update_agent_access(
                    "gala", gala_chat.AgentAccessUpdate(revision=0, tools=[], scopes=[]), db, founder
                )
            self.assertEqual(ctx.exception.status_code, 403)
            db.commit.assert_not_awaited()

    def test_default_agent_matches_account_role(self):
        self.assertEqual(gala_chat.resolve_agent_id(_user("founder")), "gala")
        self.assertEqual(gala_chat.resolve_agent_id(_user("artist")), "spyder")
        self.assertEqual(gala_chat.resolve_agent_id(_user("assistant")), "spyder")
        self.assertEqual(gala_chat.resolve_agent_id(_user("collector")), "smoku")

    def test_role_cannot_claim_a_more_privileged_agent(self):
        self.assertEqual(gala_chat.resolve_agent_id(_user("collector"), "booky"), "booky")
        with self.assertRaises(HTTPException) as ctx:
            gala_chat.resolve_agent_id(_user("collector"), "gala")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_tool_list_is_filtered_by_agent_and_account_role(self):
        collector_herald = {item["function"]["name"] for item in gala_chat._openai_tools("herald", "collector")}
        artist_booky = {item["function"]["name"] for item in gala_chat._openai_tools("booky", "artist")}
        founder_booky = {item["function"]["name"] for item in gala_chat._openai_tools("booky", "founder")}
        self.assertNotIn("get_business_stats", collector_herald)
        self.assertNotIn("get_daily_debrief", artist_booky)
        self.assertIn("get_daily_debrief", founder_booky)

    async def test_execute_tool_rechecks_role_even_if_model_attempts_it(self):
        with self.assertRaises(PermissionError):
            await gala_chat.execute_tool(
                "get_business_stats",
                {},
                _user("artist"),
                db=object(),
                agent_id="herald",
            )

    async def test_unknown_tool_cannot_be_recorded_as_a_success(self):
        with self.assertRaises(ValueError):
            await gala_chat.execute_tool(
                "unknown_tool",
                {},
                _user("founder"),
                db=object(),
                agent_id="gala",
            )

    def test_agent_prompt_keeps_visible_identity_and_courier_boundary(self):
        prompt = gala_chat.get_agent_system_prompt(
            _user("collector"),
            "booky",
            "",
            {"portal_tab": "books"},
        )
        # The legacy booky id carries the Bandit identity, never the alias.
        self.assertIn("You are Saishin · Little Whirlwind", prompt)
        self.assertNotIn("Booky", prompt)
        self.assertIn("durable client and artist record", prompt)
        self.assertIn("Current Ryozan Studio resident artist roster", prompt)
        self.assertNotIn("You are Gala", prompt)
        for legacy in ("BTTCO", "Blind Tiger", "Greenville", "Justin Walker"):
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, prompt)

    def test_founder_prompt_uses_trusted_identity_and_bandit_roster(self):
        user = BTTCOUser(
            id=uuid.uuid4(),
            email="founder@ryozan.app",
            name="Morgan Z.",
            password_hash="test-hash",
            role="founder",
        )
        prompt = gala_chat.get_agent_system_prompt(user, "gala", "", {"portal_tab": "hub"})

        self.assertIn("speaking with Studio Founder", prompt)
        self.assertNotIn("Morgan Z.", prompt)
        self.assertIn("working tattoo artist", prompt)
        self.assertIn("no simulated completion", prompt)
        for agent_name in ("Sōkō", "Ensei", "Saishin", "Taisō", "Shishin", "Riki", "Rochishin", "Andōzen"):
            with self.subTest(agent_name=agent_name):
                self.assertIn(agent_name, prompt)
        for legacy in ("Spyder", "Xar", "Mercury", "Smoku", "Bella", "Booky", "Herald", "BTTCO"):
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, prompt)
        self.assertIn("Do not claim that a separate agent accepted, executed, or completed", prompt)

    def test_session_compaction_parser_ignores_wrapping_and_bounds_facts(self):
        raw = (
            'result: {"digest":"Keep this","shared_facts":['
            + ",".join('{"topic":"schedule","key":"x","value":"y"}' for _ in range(10))
            + "]} done"
        )
        digest, facts = gala_chat._parse_session_compaction(raw)
        self.assertEqual(digest, "Keep this")
        self.assertEqual(len(facts), 8)

    async def test_close_without_memory_consent_writes_nothing(self):
        body = gala_chat.SessionCloseRequest(
            agent_id="booky",
            turns=[gala_chat.HistoryMessage(role="user", content="Remember Tuesday")],
            memory_consent=False,
        )
        with patch.object(gala_chat, "get_settings", return_value=_settings()):
            result = await gala_chat.close_agent_session(body, db=object(), current_user=_user("founder"))
        self.assertFalse(result.compacted)
        self.assertEqual(result.memories_written, 0)

    async def test_close_endpoint_is_available_to_collectors(self):
        body = gala_chat.SessionCloseRequest(agent_id="booky", memory_consent=False)
        with patch.object(gala_chat, "get_settings", return_value=_settings()):
            result = await gala_chat.close_agent_session(body, db=object(), current_user=_user("collector"))
        self.assertFalse(result.compacted)


class GalaAutoReplyGateTests(unittest.IsolatedAsyncioTestCase):
    """
    The inbound-client auto-reply. This path is public facing by definition and
    has no requesting user, so during limited release it must not run at all --
    not merely be role-checked.
    """

    async def _run_auto_reply(self, enabled):
        gateway = AsyncMock()
        session = _RecordingSessionFactory()
        with (
            patch.object(conversations, "get_settings", return_value=_settings(auto_reply_enabled=enabled)),
            patch.object(conversations, "async_session", session),
            patch.object(ai_routing, "call_ark_gateway_chat_completion", gateway),
        ):
            # _gala_auto_reply is fire-and-forget and swallows its own exceptions,
            # so assert on what it touched rather than on what it raised.
            await conversations._gala_auto_reply(
                conversation_id=uuid.uuid4(),
                artist_id=uuid.uuid4(),
                artist_name="Artist",
                client_name="Client",
                mode="transparent",
            )
        return gateway, session

    async def test_disabled_auto_reply_never_calls_the_gateway(self):
        gateway, session = await self._run_auto_reply(enabled=False)
        gateway.assert_not_called()
        # It bails out before opening a session, so no client work happens at all.
        self.assertEqual(session.calls, 0)

    async def test_explicit_auto_reply_enable_resumes_the_path(self):
        _, session = await self._run_auto_reply(enabled=True)
        self.assertEqual(session.calls, 1)


if __name__ == "__main__":
    unittest.main()
