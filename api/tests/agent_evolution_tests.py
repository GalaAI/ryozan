"""Founder-scoped Bella and Gala evolution adapter tests."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import gala_chat
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.agent_evolution import (
    _replace_managed_section,
    apply_profile_to_elevenlabs,
    project_agent_evolution_for_bttco,
    render_managed_behavior,
)


class AgentEvolutionPromptTests(unittest.TestCase):
    def test_replaces_only_the_managed_prompt_section(self):
        profile = {
            "id": "apex.agent.bella.v2",
            "revision": 4,
            "traits": {
                "curiosity": 84,
                "warmth": 80,
                "initiative": 70,
                "precision": 90,
                "brevity": 72,
                "questionBudget": 2,
                "oneQuestionAtATime": True,
            },
            "directives": ["Ask a grounded follow-up."],
            "boundaries": ["Never invent availability."],
        }
        first = _replace_managed_section("Original shop prompt.", render_managed_behavior(profile))
        profile["revision"] = 5
        second = _replace_managed_section(first, render_managed_behavior(profile))
        self.assertTrue(second.startswith("Original shop prompt."))
        self.assertEqual(second.count("APEX BEHAVIOR PROFILE START"), 1)
        self.assertIn("revision 5", second)
        self.assertNotIn("revision 4", second)

    def test_projects_ark_evaluation_status_into_plain_bttco_language(self):
        raw_status = "cru" + "cible_passed"
        projected = project_agent_evolution_for_bttco(
            {"profile": {"status": raw_status}, "versions": [{"status": raw_status}]}
        )
        self.assertEqual(projected["profile"]["status"], "evaluation_passed")
        self.assertEqual(projected["versions"][0]["status"], "evaluation_passed")


class AgentEvolutionProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_applies_only_the_managed_section_to_the_configured_phone_agent(self):
        profile = {
            "id": "apex.agent.bella.v3",
            "version": 3,
            "revision": 7,
            "status": "promoted",
            "traits": {
                "curiosity": 80,
                "warmth": 85,
                "initiative": 70,
                "precision": 90,
                "brevity": 75,
                "questionBudget": 2,
                "oneQuestionAtATime": True,
            },
            "directives": ["Ask a grounded follow-up."],
            "boundaries": ["Never invent availability."],
        }
        current_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"conversation_config": {"agent": {"prompt": {"prompt": "Existing live phone prompt."}}}},
        )
        updated_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"agent_id": "bella-provider-agent"},
        )
        managed_prompt = _replace_managed_section("Existing live phone prompt.", render_managed_behavior(profile))
        observed_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "agent_id": "bella-provider-agent",
                "conversation_config": {"agent": {"prompt": {"prompt": managed_prompt}}},
            },
        )
        client = SimpleNamespace(
            get=AsyncMock(side_effect=[current_response, observed_response]),
            patch=AsyncMock(return_value=updated_response),
        )
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = None

        with (
            patch(
                "app.services.agent_evolution.get_settings",
                return_value=SimpleNamespace(
                    ELEVENLABS_API_KEY="provider-key",
                    ELEVENLABS_BTTCO_AGENT_ID="bella-provider-agent",
                    ELEVENLABS_JUSTIN_AGENT_ID="gala-provider-agent",
                ),
            ),
            patch("app.services.agent_evolution.httpx.AsyncClient", return_value=context),
        ):
            receipt = await apply_profile_to_elevenlabs("bella", profile)

        self.assertEqual(receipt["agentId"], "bella")
        self.assertEqual(receipt["profileId"], "apex.agent.bella.v3")
        self.assertEqual(receipt["profileVersion"], 3)
        self.assertEqual(receipt["profileRevision"], 7)
        self.assertEqual(receipt["appliedDigest"], receipt["readBackDigest"])
        self.assertIn("syncedAt", receipt)
        self.assertEqual(client.get.await_count, 2)
        patch_payload = client.patch.await_args.kwargs["json"]
        prompt = patch_payload["conversation_config"]["agent"]["prompt"]["prompt"]
        self.assertTrue(prompt.startswith("Existing live phone prompt."))
        self.assertEqual(prompt.count("APEX BEHAVIOR PROFILE START"), 1)

    async def test_rejects_provider_success_when_read_back_does_not_match(self):
        profile = {
            "id": "apex.agent.gala.v2",
            "version": 2,
            "revision": 3,
            "status": "promoted",
            "traits": {
                "curiosity": 70,
                "warmth": 70,
                "initiative": 75,
                "precision": 90,
                "brevity": 65,
                "questionBudget": 1,
                "oneQuestionAtATime": True,
            },
            "directives": [],
            "boundaries": ["Never invent state."],
        }
        current_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"conversation_config": {"agent": {"prompt": {"prompt": "Existing."}}}},
        )
        updated_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"agent_id": "gala-provider-agent"},
        )
        drifted_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "agent_id": "gala-provider-agent",
                "conversation_config": {"agent": {"prompt": {"prompt": "Provider drift."}}},
            },
        )
        client = SimpleNamespace(
            get=AsyncMock(side_effect=[current_response, drifted_response]),
            patch=AsyncMock(return_value=updated_response),
        )
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = None
        with (
            patch(
                "app.services.agent_evolution.get_settings",
                return_value=SimpleNamespace(
                    ELEVENLABS_API_KEY="provider-key",
                    ELEVENLABS_BTTCO_AGENT_ID="bella-provider-agent",
                    ELEVENLABS_JUSTIN_AGENT_ID="gala-provider-agent",
                ),
            ),
            patch("app.services.agent_evolution.httpx.AsyncClient", return_value=context),
            self.assertRaisesRegex(Exception, "read-back did not match"),
        ):
            await apply_profile_to_elevenlabs("gala", profile)


class OrchestraModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_override_is_limited_to_model_configuration(self):
        from app.services.agent_evolution import BTTCO_ORCHESTRA_MODELS_PATH, request_apex_agent
        response = SimpleNamespace(status_code=200, content=b"{}", json=lambda: {"ok": True})
        for local_mode, path, expected_origin in [
            (True, BTTCO_ORCHESTRA_MODELS_PATH, "http://127.0.0.1:8790"),
            (True, "agents/gala", "https://hosted.example"),
            (False, BTTCO_ORCHESTRA_MODELS_PATH, "https://hosted.example"),
        ]:
            client = AsyncMock()
            client.__aenter__.return_value = client
            client.request.return_value = response
            settings = SimpleNamespace(LOCAL_DEV_MODE=local_mode, ARK_ORCHESTRA_CONFIG_URL="http://127.0.0.1:8790")
            with patch("app.services.agent_evolution._apex_base_url", return_value=("https://hosted.example", "fixture-token")), patch("app.services.agent_evolution.get_settings", return_value=settings), patch("app.services.agent_evolution.httpx.AsyncClient", return_value=client):
                await request_apex_agent("GET", path)
            self.assertEqual(client.request.call_args.args[1], f"{expected_origin}/api/apex/{path}")

    async def test_absent_resource_retains_default_but_other_failures_are_not_hidden(self):
        from app.services.agent_evolution import AgentEvolutionBridgeError, resolve_orchestra_model_lane
        lane = SimpleNamespace(primary_model="ark/bttco/gemini")
        for status in [404, 401, 403, 503]:
            with patch("app.services.agent_evolution.request_apex_agent", new=AsyncMock(side_effect=AgentEvolutionBridgeError(status, "Unavailable"))):
                if status == 404:
                    self.assertIs(await resolve_orchestra_model_lane("gala", lane), lane)
                else:
                    with self.assertRaises(AgentEvolutionBridgeError):
                        await resolve_orchestra_model_lane("gala", lane)

    async def test_each_request_reads_the_shared_assignment_and_preserves_global_lane(self):
        from dataclasses import make_dataclass

        from app.services.agent_evolution import BTTCO_ORCHESTRA_MODELS_PATH, resolve_orchestra_model_lane
        lane_type = make_dataclass("Lane", [("primary_model", str), ("fallback_models", list)])
        inherited = lane_type("ark/bttco/gemini", ["ark/deepseek"])
        def snapshot(primary):
            return {"ok": True, "routing": {"orchestraId": "orchestra:bttco", "scope": "bttco_chat", "models": [{"id": "ark/qwen"}, {"id": "ark/deepseek"}], "members": [{"actorRef": "agent:bttco-spyder", "assignment": {"defaultModel": primary, "fallbacks": []}}]}}
        with patch("app.services.agent_evolution.request_apex_agent", new=AsyncMock(side_effect=[snapshot("ark/qwen"), snapshot("ark/deepseek"), snapshot(None)])) as api:
            first = await resolve_orchestra_model_lane("spyder", inherited)
            second = await resolve_orchestra_model_lane("spyder", inherited)
            third = await resolve_orchestra_model_lane("spyder", inherited)
        self.assertEqual(first.primary_model, "ark/qwen")
        self.assertEqual(second.primary_model, "ark/deepseek")
        self.assertIs(third, inherited)
        self.assertEqual(inherited.primary_model, "ark/bttco/gemini")
        self.assertEqual(api.await_count, 3)
        api.assert_awaited_with("GET", BTTCO_ORCHESTRA_MODELS_PATH)

    async def test_wrong_orchestra_and_missing_agent_fail_closed(self):
        from app.services.agent_evolution import AgentEvolutionBridgeError, resolve_orchestra_model_lane
        for orchestra in ["orchestra:other", "orchestra:bttco"]:
            with patch("app.services.agent_evolution.request_apex_agent", new=AsyncMock(return_value={"ok": True, "routing": {"orchestraId": orchestra, "scope": "bttco_chat", "members": []}})), self.assertRaises(AgentEvolutionBridgeError):
                await resolve_orchestra_model_lane("gala", SimpleNamespace())


class BenchPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_failure_is_safe_and_does_not_record_a_success(self):
        from fastapi import HTTPException

        for failure in [HTTPException(502, 'Provider error with private upstream payload'), HTTPException(502, '402 Insufficient credits with private upstream payload'), ValueError('Malformed provider payload')]:
            db = AsyncMock()
            with (
                patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=True, ANTHROPIC_MONTHLY_BUDGET_CAP=10)),
                patch.object(gala_chat, "get_agent_system_prompt", return_value="Selected agent purpose"),
                patch.object(gala_chat, "resolve_ai_lane", new=AsyncMock(return_value=SimpleNamespace(primary_model="ark/test", fallback_models=[]))),
                patch.object(gala_chat, "resolve_orchestra_model_lane", new=AsyncMock(side_effect=lambda _agent, lane: lane)),
                patch.object(gala_chat, "call_ark_gateway_chat_completion", new=AsyncMock(side_effect=failure)),
                patch.object(gala_chat, "execute_tool", new=AsyncMock()) as execute,
                patch("app.services.api_budget.check_budget", new=AsyncMock()),
                patch("app.services.api_budget.log_usage", new=AsyncMock()) as usage,
                self.assertRaises(HTTPException) as error,
            ):
                await gala_chat.preview_agent_response("spyder", gala_chat.BenchPreviewRequest(scenario="Sample inquiry scenario"), db, SimpleNamespace(id="founder-1", role="founder", nexus_profile={}))
            self.assertEqual(error.exception.status_code, 502)
            self.assertIn("Nothing was applied", error.exception.detail)
            self.assertNotIn("payload", error.exception.detail)
            if isinstance(failure, HTTPException) and "402" in failure.detail:
                self.assertIn("insufficient credits", error.exception.detail)
            usage.assert_not_awaited()
            db.commit.assert_not_awaited()
            execute.assert_not_awaited()

    async def test_preview_uses_server_config_and_never_executes_tools_or_writes_profiles(self):
        user = SimpleNamespace(id="founder-1", role="founder", nexus_profile={})
        settings = SimpleNamespace(LOCAL_DEV_MODE=True, ANTHROPIC_MONTHLY_BUDGET_CAP=10)
        profile = {"agentId": "gala", "status": "promoted", "revision": 5, "traits": {"warmth": 50}}
        gateway = AsyncMock(return_value={"choices": [{"message": {"content": "Illustrative comparison"}}], "model": "ark/test"})
        db = AsyncMock()
        with (
            patch.object(gala_chat, "get_settings", return_value=settings),
            patch.object(gala_chat, "request_apex_agent", new=AsyncMock(return_value={"promotedProfile": profile, "profile": profile})) as apex,
            patch.object(gala_chat, "get_agent_system_prompt", return_value="Server-owned Gala purpose"),
            patch.object(gala_chat, "resolve_ai_lane", new=AsyncMock(return_value=SimpleNamespace(primary_model="ark/test", fallback_models=[]))),
            patch.object(gala_chat, "resolve_orchestra_model_lane", new=AsyncMock(side_effect=lambda _agent,lane:lane)),
            patch.object(gala_chat, "call_ark_gateway_chat_completion", new=gateway),
            patch.object(gala_chat, "execute_tool", new=AsyncMock()) as execute,
            patch("app.services.api_budget.check_budget", new=AsyncMock()),
            patch("app.services.api_budget.log_usage", new=AsyncMock()) as usage,
        ):
            result = await gala_chat.preview_agent_response("gala", gala_chat.BenchPreviewRequest(
                scenario="An inquiry needs a follow-up. No records supplied.", profile_revision=5, traits={"warmth": 80},
                access={"revision": 0, "tools": [], "scopes": []},
            ), db, user)
        self.assertEqual(result["mode"], "illustrative_preview")
        self.assertEqual(result["profile_revision"], 5)
        self.assertEqual(gateway.await_args.kwargs["tools"], [])
        import json
        context = json.loads(gateway.await_args.kwargs["messages"][0]["content"])
        self.assertEqual(context["baseline_profile"]["traits"]["warmth"], 50)
        self.assertEqual(context["draft_profile"]["traits"]["warmth"], 80)
        self.assertEqual(context["draft_tools"], [])
        self.assertTrue(context["baseline_tools"])
        self.assertEqual(result["tool_context"], context["tool_context"])
        self.assertFalse(result["tool_context"]["executed"])
        self.assertEqual(result["tool_context"]["draft"], [])
        self.assertEqual(result["tool_context"]["added"], [])
        expected_names = sorted(tool["function"]["name"] for tool in context["baseline_tools"])
        self.assertEqual(result["tool_context"]["removed"], expected_names)
        for summary, tool in zip(result["tool_context"]["current"], context["baseline_tools"], strict=True):
            self.assertEqual(summary["name"], tool["function"]["name"])
            self.assertEqual(summary["description"], tool["function"]["description"])
            self.assertEqual(summary["scope"], gala_chat.TOOL_SCOPES[summary["name"]])
        self.assertEqual(result["context_provenance"], context["context_provenance"])
        self.assertEqual(result["context_provenance"]["live_records"], "not_provided")
        self.assertTrue(result["context_provenance"]["limitations"])
        db.execute.assert_not_awaited()
        db.add.assert_not_called()
        apex.assert_awaited_once_with("GET", "agents/gala")
        execute.assert_not_awaited()
        usage.assert_awaited_once()

    async def test_preview_denies_non_founder_production_and_stale_access_before_model_call(self):
        from fastapi import HTTPException
        for role, local, revision, status in [("artist", True, 0, 403), ("founder", False, 0, 403), ("founder", True, 2, 409)]:
            with (
                patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=local)),
                patch.object(gala_chat, "call_ark_gateway_chat_completion", new=AsyncMock()) as gateway,
                self.assertRaises(HTTPException) as error,
            ):
                await gala_chat.preview_agent_response("gala", gala_chat.BenchPreviewRequest(
                    scenario="Sample inquiry scenario", access={"revision": revision, "tools": [], "scopes": []},
                ), AsyncMock(), SimpleNamespace(id="founder-1", role=role, nexus_profile={}))
            self.assertEqual(error.exception.status_code, status)
            gateway.assert_not_awaited()

    async def test_preview_fails_closed_without_promoted_profile(self):
        from fastapi import HTTPException
        with (
            patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=True)),
            patch.object(gala_chat, "request_apex_agent", new=AsyncMock(return_value={"profile": {"status": "candidate"}})),
            patch.object(gala_chat, "call_ark_gateway_chat_completion", new=AsyncMock()) as gateway,
            self.assertRaises(HTTPException) as error,
        ):
            await gala_chat.preview_agent_response("gala", gala_chat.BenchPreviewRequest(scenario="Sample inquiry scenario"), AsyncMock(), SimpleNamespace(id="founder-1", role="founder", nexus_profile={}))
        self.assertEqual(error.exception.status_code, 409)
        gateway.assert_not_awaited()

    async def test_unknown_tools_and_missing_scopes_fail_before_generation(self):
        from fastapi import HTTPException
        for tools in [["not_a_registered_tool"], ["lookup_bookings"]]:
            with (
                patch.object(gala_chat, "get_settings", return_value=SimpleNamespace(LOCAL_DEV_MODE=True)),
                patch.object(gala_chat, "call_ark_gateway_chat_completion", new=AsyncMock()) as gateway,
                self.assertRaises(HTTPException) as error,
            ):
                await gala_chat.preview_agent_response("gala", gala_chat.BenchPreviewRequest(
                    scenario="Sample inquiry scenario", access={"revision": 0, "tools": tools, "scopes": []},
                ), AsyncMock(), SimpleNamespace(id="founder-1", role="founder", nexus_profile={}))
            self.assertEqual(error.exception.status_code, 422)
            gateway.assert_not_awaited()


class AgentEvolutionRouteTests(unittest.TestCase):
    def _client(self, role: str) -> TestClient:
        app = FastAPI()
        app.include_router(gala_chat.router)

        async def current_user():
            return SimpleNamespace(id="founder-1", role=role, name="Justin Walker", email="justin@example.com")

        async def database():
            yield AsyncMock()

        app.dependency_overrides[get_current_user] = current_user
        app.dependency_overrides[get_db] = database
        return TestClient(app)

    def test_founder_dialogue_forwards_media_references_to_apex(self):
        profile = {"agentId": "bella", "status": "candidate"}
        with (
            patch.object(
                gala_chat,
                "request_apex_agent",
                new=AsyncMock(return_value={"ok": True, "profile": profile, "response": "Updated."}),
            ) as request,
            patch.object(
                gala_chat,
                "deliver_pack_capture",
                new=AsyncMock(return_value=SimpleNamespace(status="delivered", item_id="ainbox-item-1")),
            ) as deliver,
        ):
            response = self._client("founder").post(
                "/gala/evolution/bella/dialogue",
                json={
                    "message": "Make Bella more curious.",
                    "history": [],
                    "input_refs": ["https://assets.example.test/voice-note.wav"],
                },
            )
        self.assertEqual(response.status_code, 200)
        payload = request.await_args.args[2]
        self.assertEqual(
            payload["inputRefs"],
            [
                "https://assets.example.test/voice-note.wav",
                "urn:ainbox:item:ainbox-item-1",
            ],
        )
        self.assertEqual(payload["source"], "bttco.maestro")
        self.assertEqual(
            payload["operatorGate"],
            {"confirmed": True, "approvedBy": "Justin Walker"},
        )
        capture = deliver.await_args.args[0]
        self.assertEqual(capture["sourceId"], "bttco:apex:evolution")
        self.assertEqual(capture["metadata"]["packTargets"], ["ainbox", "apex"])

    def test_dialogue_stops_when_ainbox_does_not_accept_the_input(self):
        with (
            patch.object(
                gala_chat,
                "request_apex_agent",
                new=AsyncMock(),
            ) as request,
            patch.object(
                gala_chat,
                "deliver_pack_capture",
                new=AsyncMock(return_value=SimpleNamespace(status="failed", item_id=None)),
            ),
        ):
            response = self._client("founder").post(
                "/gala/evolution/gala/dialogue",
                json={"message": "Remember that I prefer short replies."},
            )

        self.assertEqual(response.status_code, 503)
        request.assert_not_awaited()

    def test_crucible_forwards_only_the_server_generated_evaluation_artifact(self):
        profile = {"agentId": "gala", "status": "crucible_passed"}
        with patch.object(
            gala_chat,
            "request_apex_agent",
            new=AsyncMock(return_value={"ok": True, "profile": profile}),
        ) as request:
            response = self._client("founder").post(
                "/gala/evolution/gala/evaluation",
                json={"evaluation_artifact_id": "artifact.apex.agent.gala.test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            request.await_args.args[2],
            {
                "evaluationArtifactId": "artifact.apex.agent.gala.test",
                "source": "bttco.maestro",
                "operatorGate": {
                    "confirmed": True,
                    "approvedBy": "Justin Walker",
                },
            },
        )

    def test_profile_and_gauntlet_carry_signed_operator_approval(self):
        profile = {
            "agentId": "gala",
            "status": "candidate",
            "traits": {"precision": 96},
        }
        with patch.object(
            gala_chat,
            "request_apex_agent",
            new=AsyncMock(return_value={"ok": True, "profile": profile}),
        ) as request:
            client = self._client("founder")
            profile_response = client.post(
                "/gala/evolution/gala/profile",
                json={
                    "traits": {"precision": 96},
                    "directives": ["Lead with the action."],
                    "boundaries": ["Do not invent live state."],
                },
            )
            gauntlet_response = client.post("/gala/evolution/gala/gauntlet")

        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(gauntlet_response.status_code, 200)
        profile_payload = request.await_args_list[0].args[2]
        gauntlet_payload = request.await_args_list[1].args[2]
        self.assertEqual(profile_payload["operatorGate"]["confirmed"], True)
        self.assertEqual(profile_payload["operatorGate"]["approvedBy"], "Justin Walker")
        self.assertEqual(gauntlet_payload["operatorGate"], profile_payload["operatorGate"])

    def test_portal_promotion_is_independent_of_phone_provider_sync(self):
        promoted = {"agentId": "gala", "status": "promoted"}
        with (
            patch.object(
                gala_chat,
                "request_apex_agent",
                new=AsyncMock(return_value={"ok": True, "profile": promoted}),
            ) as request,
            patch.object(
                gala_chat,
                "apply_profile_to_elevenlabs",
                new=AsyncMock(),
            ) as apply_phone,
        ):
            response = self._client("founder").post("/gala/evolution/gala/promote")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile"]["status"], "promoted")
        self.assertEqual(request.await_args.args[1], "agents/gala/promote")
        self.assertEqual(
            request.await_args.args[2]["operatorGate"],
            {"confirmed": True, "approvedBy": "Justin Walker"},
        )
        apply_phone.assert_not_awaited()

    def test_collector_cannot_open_agent_evolution(self):
        response = self._client("collector").get("/gala/evolution")
        self.assertEqual(response.status_code, 403)

    def test_founder_can_replay_only_service_selected_learning_failures(self):
        with (
            patch.object(
                gala_chat,
                "requeue_terminal_learning_captures",
                new=AsyncMock(return_value=["row-1", "row-2"]),
            ) as requeue,
            patch.object(
                gala_chat,
                "process_pack_bridge_outbox_once",
                new=AsyncMock(),
            ) as process,
        ):
            response = self._client("founder").post("/gala/evolution/recover-learning-captures")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["requeued"], 2)
        requeue.assert_awaited_once()
        process.assert_awaited_once_with(limit=2)


if __name__ == "__main__":
    unittest.main()
