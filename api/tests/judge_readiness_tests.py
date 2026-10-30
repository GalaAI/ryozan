"""Contracts for the standalone (judge) build: Nemotron routing, secrets, sign-out."""

import asyncio
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.core import security
from app.core.config import SHARED_SECRET_FIELDS, Settings
from app.models.user import BTTCOUser
from app.services import ai_routing
from app.services.ai_routing import AILaneDecision, agent_model_lane

SUPER = "nvidia/nemotron-3-super-120b-a12b"
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def _lane(primary=SUPER, fallbacks=(NANO,)):
    return AILaneDecision(
        configured_tier="staff",
        effective_tier="staff",
        membership_status="active",
        primary_model=primary,
        fallback_models=list(fallbacks),
        ai_monthly_message_cap=None,
        ai_messages_used=0,
        ai_messages_remaining=None,
        early_access_galax3=True,
        connected_apps_unlocked=True,
        product_entitlements={},
    )


class NebiusConfigTests(unittest.TestCase):
    def test_defaults_target_token_factory_nemotron(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.ARK_MODEL_GATEWAY_BASE_URL, "https://api.tokenfactory.nebius.com/v1")
        for name in ("AI_FREEMIUM_PRIMARY_MODEL", "AI_PAID_PRIMARY_MODEL", "AI_PREMIUM_PRIMARY_MODEL"):
            self.assertEqual(getattr(settings, name), SUPER)
        self.assertEqual(settings.ARK_GATEWAY_UTILITY_MODEL, NANO)

    def test_nebius_variable_names_feed_the_shared_client(self):
        with patch.dict(
            "os.environ",
            {"NEBIUS_API_KEY": "nebius-fixture-key", "NEBIUS_BASE_URL": "https://tf.example.test/v1"},
            clear=False,
        ):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.ARK_MODEL_GATEWAY_KEY, "nebius-fixture-key")
        self.assertEqual(settings.ARK_MODEL_GATEWAY_BASE_URL, "https://tf.example.test/v1")

    def test_canonical_gateway_names_still_win(self):
        with patch.dict(
            "os.environ",
            {"NEBIUS_API_KEY": "nebius-fixture-key", "ARK_MODEL_GATEWAY_KEY": "ark-fixture-key"},
            clear=False,
        ):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.ARK_MODEL_GATEWAY_KEY, "ark-fixture-key")


class AgentModelLaneTests(unittest.TestCase):
    def test_agent_model_leads_and_tier_lane_becomes_fallback(self):
        lane = agent_model_lane(_lane(), NANO)
        self.assertEqual(lane.primary_model, NANO)
        self.assertEqual(lane.fallback_models, [SUPER])

    def test_ark_gateway_lane_is_left_unchanged(self):
        lane = agent_model_lane(_lane("ark/nemotron-3-super", ("ark/qwen",)), NANO)
        self.assertEqual(lane.primary_model, "ark/nemotron-3-super")
        self.assertEqual(lane.fallback_models, ["ark/qwen"])

    def test_every_chat_agent_names_a_nemotron_model(self):
        from app.api.v1.endpoints import gala_chat

        for agent_id in gala_chat.RYOZAN_AGENT_IDS:
            with self.subTest(agent_id=agent_id):
                self.assertTrue(gala_chat.AGENT_PROFILES[agent_id]["nemotron_model"].startswith("nvidia/"))


class _Response:
    def __init__(self, payload):
        self.status_code = 200
        self.is_success = True
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _RecordingClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.payloads.append({"url": url, "json": json})
        return self.responses.pop(0)


class NemotronTokenFloorTests(unittest.TestCase):
    def test_small_budgets_are_raised_for_nemotron_and_empty_reasoning_turns_fall_through(self):
        empty = _Response({"model": NANO, "choices": [{"finish_reason": "length", "message": {"content": ""}}]})
        good = _Response({"model": SUPER, "choices": [{"finish_reason": "stop", "message": {"content": "flash"}}]})
        client = _RecordingClient([empty, good])
        settings = Settings(_env_file=None, ARK_MODEL_GATEWAY_KEY="fixture")
        with patch.object(httpx, "AsyncClient", return_value=client):
            text, _usage = asyncio.run(
                ai_routing.call_ark_gateway_text(
                    settings=settings,
                    system_prompt="classify",
                    user_prompt="file",
                    primary_model=NANO,
                    fallback_models=[SUPER],
                    max_tokens=20,
                )
            )
        self.assertEqual(text, "flash")
        self.assertEqual([p["json"]["max_tokens"] for p in client.payloads], [1024, 1024])
        self.assertTrue(client.payloads[0]["url"].startswith("https://api.tokenfactory.nebius.com/v1"))


class SharedSecretGuardTests(unittest.TestCase):
    def test_non_development_refuses_public_or_blank_secrets(self):
        for app_env in ("production", "staging"):
            with self.subTest(app_env=app_env), self.assertRaisesRegex(ValueError, "Refusing to start"):
                Settings(_env_file=None, APP_ENV=app_env, SECRET_KEY="bttco-dev-secret-change-in-production")

    def test_development_generates_and_persists_random_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dev-secrets.json"
            first = Settings(_env_file=None, APP_ENV="development", DEV_SECRETS_FILE=str(path))
            second = Settings(_env_file=None, APP_ENV="development", DEV_SECRETS_FILE=str(path))
            stored = json.loads(path.read_text(encoding="utf-8"))
        for name in SHARED_SECRET_FIELDS:
            with self.subTest(name=name):
                self.assertGreaterEqual(len(getattr(first, name)), 32)
                self.assertEqual(getattr(first, name), getattr(second, name))
                self.assertEqual(stored[name], getattr(first, name))


class _Result:
    def __init__(self, user):
        self.user = user

    def scalar_one_or_none(self):
        return self.user


class SignOutTests(unittest.TestCase):
    def _user(self):
        return BTTCOUser(
            id=uuid.uuid4(),
            email="artist@ryozan.app",
            name="Artist",
            password_hash="$2b$12$fixturehashfixturehashfixturehashfixtureha",
            role="artist",
            token_version=0,
        )

    def test_token_issued_before_sign_out_is_rejected(self):
        user = self._user()
        token = security.create_user_access_token(user)
        db = AsyncMock()
        db.execute.return_value = _Result(user)

        self.assertIs(asyncio.run(security.get_current_user(token=token, db=db)), user)

        from app.api.v1.endpoints import auth

        asyncio.run(auth.logout(current_user=user, db=db))
        self.assertEqual(user.token_version, 1)
        db.commit.assert_awaited()
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(security.get_current_user(token=token, db=db))
        self.assertEqual(raised.exception.status_code, 401)

        fresh = security.create_user_access_token(user)
        self.assertIs(asyncio.run(security.get_current_user(token=fresh, db=db)), user)


if __name__ == "__main__":
    unittest.main()


LEGACY_IDENTITY = ("BLIND TIGER", "Blind Tiger", "Greenville", "Pendleton", "29611", "BTTCO", "82.3940")


class StudioIdentityTests(unittest.TestCase):
    """Client-facing output carries the configured studio identity, never the legacy shop."""

    def _assert_clean(self, text: str, where: str):
        for legacy in LEGACY_IDENTITY:
            self.assertNotIn(legacy, text, f"{legacy!r} leaked into {where}")

    def test_every_foundry_template_renders_the_studio_name_and_no_legacy_identity(self):
        from app.services.email_foundry import _catalog, render_foundry_email

        for slug in _catalog()["templates"]:
            rendered = render_foundry_email(slug, {})
            self.assertIsNotNone(rendered)
            self._assert_clean(rendered.subject + rendered.html_body, slug)
            self.assertIn("Ryozan Studio", rendered.html_body, slug)

    def test_postal_address_prints_only_when_configured(self):
        from app.services import email_foundry

        class _Cfg:
            STUDIO_NAME = "Ryozan Studio"
            STUDIO_ADDRESS = ""

        with patch("app.core.config.get_settings", return_value=_Cfg()):
            self.assertEqual(email_foundry.studio_address_html("margin:0"), "")
            _Cfg.STUDIO_ADDRESS = "1 Example Way"
            self.assertIn("1 Example Way", email_foundry.studio_address_html("margin:0"))
            rendered = email_foundry.render_foundry_email("booking_confirmation", {})
            self.assertIn("1 Example Way", rendered.html_body)

    def test_agent_prompts_and_boot_identity_name_no_legacy_shop(self):
        root = Path(__file__).resolve().parents[1]
        sources = [
            root / "main.py",
            root / "app" / "api" / "v1" / "endpoints" / "conversations.py",
            root / "app" / "api" / "v1" / "endpoints" / "dashboard.py",
            root / "app" / "api" / "v1" / "endpoints" / "bookings.py",
            root / "app" / "api" / "v1" / "endpoints" / "sms.py",
            root / "app" / "api" / "v1" / "endpoints" / "ai.py",
            root / "app" / "services" / "email.py",
            root / "app" / "services" / "email_foundry_waiver.py",
            root / "app" / "services" / "scroll_pdf.py",
            root / "app" / "services" / "sms_template_seeder.py",
            root / "app" / "services" / "calendar_sync.py",
        ]
        for source in sources:
            text = source.read_text(encoding="utf-8")
            for legacy in ("Greenville", "Pendleton", "Blind Tiger", "[BTTCO]"):
                self.assertNotIn(legacy, text, f"{legacy!r} in {source.name}")
        self.assertEqual(Settings(APP_ENV="test").APP_NAME, "Ryozan API")


class JudgeQaRoundTests(unittest.TestCase):
    """Contracts from the 2026-10-29 UI QA round on the standalone build."""

    def test_missing_key_error_is_not_retryable_but_outages_are(self):
        from app.api.v1.endpoints import gala_chat

        missing = gala_chat._model_gateway_error(503, "model_gateway_not_configured", "Set NEBIUS_API_KEY.")
        outage = gala_chat._model_gateway_error(503, "model_gateway_unavailable", "Try again.")
        self.assertFalse(missing.detail["retryable"])
        self.assertTrue(outage.detail["retryable"])

    def test_chat_message_has_a_server_bound(self):
        from pydantic import ValidationError

        from app.api.v1.endpoints import gala_chat

        gala_chat.ChatRequest(message="x" * gala_chat.CHAT_MESSAGE_MAX_LENGTH)
        with self.assertRaises(ValidationError):
            gala_chat.ChatRequest(message="x" * (gala_chat.CHAT_MESSAGE_MAX_LENGTH + 1))

    def test_capabilities_report_access_editing(self):
        from types import SimpleNamespace

        from app.api.v1.endpoints import gala_chat

        settings = SimpleNamespace(
            ARK_MODEL_GATEWAY_KEY="",
            ARK_MODEL_GATEWAY_BASE_URL="https://api.tokenfactory.nebius.com/v1",
            ARK_PACK_BRIDGE_URL="",
            ARK_PACK_BRIDGE_KEY="",
            LOCAL_DEV_MODE=False,
        )
        with patch.object(gala_chat, "get_settings", return_value=settings):
            result = asyncio.run(gala_chat.get_agent_capabilities(BTTCOUser(id=uuid.uuid4(), role="founder")))
        self.assertIs(result["access_editing"], False)
        self.assertIs(result["ai_configured"], False)

    def test_validation_errors_do_not_echo_the_submitted_password(self):
        import main

        async def call():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                return await client.post(
                    "/api/v1/auth/login",
                    content="email=judge%40example.com&password=hunter2-fixture",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

        response = asyncio.run(call())
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("hunter2-fixture", response.text)
        self.assertTrue(all("input" not in item for item in response.json()["detail"]))
        self.assertTrue(all({"loc", "msg", "type"} <= set(item) for item in response.json()["detail"]))
