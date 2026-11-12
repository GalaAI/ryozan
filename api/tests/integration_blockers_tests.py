"""A stranger's install must work with its own settings and never leak mail to the original studio.

Covers the blockers listed under "Known limits" in docs/integrations: inbound
routing, sender identities, Plaid environments, the Stripe Connect country, the
Google sign-in client pair, and the Resend delivery webhook signature.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import asyncio
import base64
import json
import re
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import plaid as plaid_sdk
from fastapi import HTTPException
from jose import jwt
from pydantic import ValidationError
from starlette.requests import Request

from app.api.v1.endpoints import auth, email_connect, email_inbound, email_log, stripe_connect
from app.api.v1.endpoints import plaid as plaid_endpoint
from app.core.config import SHARED_SECRET_FIELDS, Settings
from app.services import email as email_service

API = Path(__file__).resolve().parents[1]
ORIGINAL_DOMAIN = "ryozan" + ".app"
NON_DEV_SHARED_SECRETS = {name: f"fixture-{name.lower()}-0123456789abcdef0123456789" for name in SHARED_SECRET_FIELDS}
TEST_WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"ryozan-test-only-not-a-real-secret").decode()


def _settings(**overrides):
    base = {
        "APP_ENV": "test",
        "SHOP_EMAIL": "",
        "SHOP_OWNER_EMAIL": "",
        "COURIER_REPLY_DOMAIN": "",
        "RESEND_FROM_EMAIL": "",
        "RESEND_FROM_NAME": "",
        "SENDGRID_FROM_EMAIL": "",
        "SENDGRID_FROM_NAME": "",
        "SMTP_FROM_EMAIL": "",
        "SMTP_FROM_NAME": "",
        "SMTP_USER": "",
        "STUDIO_NAME": "Fixture Studio",
    }
    return Settings(_env_file=None, **{**base, **overrides})


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar(self):
        return self.value


class _Session:
    """Answers execute() calls in order and records the statements."""

    def __init__(self, *values):
        self.values = list(values)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        if not self.values:
            raise AssertionError("unexpected database execute")
        return _Result(self.values.pop(0))


def _request(body: bytes, headers: dict | None = None, path="/api/v1/webhooks/email-inbound") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": ("127.0.0.1", 12345),
            "server": ("studio.example.test", 443),
        },
        receive,
    )


def _received(to="info@in.example.test", sender="client@example.test"):
    return json.dumps(
        {"type": "email.received", "data": {"from": sender, "to": [to], "subject": "Hello", "text": "Hi"}}
    ).encode()


class _CapturingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


# ── 1. Inbound mail privacy ───────────────────────────────────────────────


class NoOriginalStudioAddressInSourceTests(unittest.TestCase):
    def test_mail_modules_name_no_address_on_the_original_domain(self):
        for relative in (
            "app/api/v1/endpoints/email_inbound.py",
            "app/api/v1/endpoints/email_connect.py",
            "app/api/v1/endpoints/conversations.py",
            "app/services/email.py",
            "app/services/conversation_threads.py",
            "app/core/config.py",
        ):
            source = (API / relative).read_text(encoding="utf-8")
            self.assertNotIn(f"@{ORIGINAL_DOMAIN}", source, relative)

    def test_bare_install_has_no_owner_inbox_or_receiving_domain(self):
        for name in ("SHOP_OWNER_EMAIL", "COURIER_REPLY_DOMAIN", "RESEND_FROM_EMAIL", "SENDGRID_FROM_EMAIL"):
            self.assertEqual(Settings.model_fields[name].default, "", name)
        self.assertIsNone(_settings().inbound_address("convo-x"))
        self.assertEqual(_settings().owner_inbox_email, "")


class InboundOwnerRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def _route(self, settings, to, *db_values):
        db = _Session(None, *db_values)  # first execute: no artist handle matches
        with patch.object(email_inbound, "get_settings", return_value=settings):
            return await email_inbound._resolve_destination([to], db, "client@example.test"), db

    async def test_reserved_and_catch_all_go_to_shop_owner_email(self):
        owner_id = uuid.uuid4()
        settings = _settings(SHOP_OWNER_EMAIL="owner@studio.example.test", SHOP_EMAIL="hello@studio.example.test")
        for to in ("info@in.example.test", "bookings@in.example.test", "anything@in.example.test"):
            with self.subTest(to=to):
                route, db = await self._route(settings, to, owner_id)
                self.assertEqual(route.destination, "owner@studio.example.test")
                self.assertEqual(route.owner_user_id, owner_id)

    async def test_shop_email_is_the_fallback_owner_inbox(self):
        settings = _settings(SHOP_EMAIL="hello@studio.example.test")
        route, _db = await self._route(settings, "support@in.example.test", uuid.uuid4())
        self.assertEqual(route.destination, "hello@studio.example.test")

    async def test_nothing_configured_archives_to_the_oldest_founder_without_a_destination(self):
        founder_id = uuid.uuid4()
        route, db = await self._route(_settings(), "info@in.example.test", founder_id)
        self.assertEqual(route.destination, "")
        self.assertEqual(route.owner_user_id, founder_id)
        self.assertIn("role", db.statements[-1])

    async def test_owner_address_without_an_account_still_lands_in_a_founder_inbox(self):
        founder_id = uuid.uuid4()
        settings = _settings(SHOP_OWNER_EMAIL="owner@studio.example.test")
        route, _db = await self._route(settings, "info@in.example.test", None, founder_id)
        self.assertEqual(route.destination, "owner@studio.example.test")
        self.assertEqual(route.owner_user_id, founder_id)

    async def test_unconfigured_install_never_forwards_and_logs_a_warning(self):
        settings = _settings(RESEND_API_KEY="re_fixture", RESEND_INBOUND_WEBHOOK_SECRET="")
        route = email_inbound.InboundRoute("", uuid.uuid4(), "info")
        forward = AsyncMock()
        with (
            patch.object(email_inbound, "get_settings", return_value=settings),
            patch.object(email_inbound, "_resolve_destination", AsyncMock(return_value=route)),
            patch.object(email_inbound, "_archive_email_to_courier", AsyncMock(return_value="thread-1")) as archive,
            patch.object(email_inbound, "_forward_email", forward),
            self.assertLogs("ryozan.email_inbound", level="WARNING") as logs,
        ):
            result = await email_inbound.handle_inbound_email(_request(_received()), AsyncMock())
        self.assertEqual(
            result,
            {"status": "archived", "reason": "no_owner_email_configured", "courier_conversation_id": "thread-1"},
        )
        archive.assert_awaited_once()
        forward.assert_not_awaited()
        self.assertIn("SHOP_OWNER_EMAIL", "\n".join(logs.output))

    async def test_no_sender_address_archives_instead_of_failing_the_webhook(self):
        settings = _settings(RESEND_API_KEY="re_fixture", SHOP_OWNER_EMAIL="", SHOP_EMAIL="")
        route = email_inbound.InboundRoute("artist@example.test", uuid.uuid4(), "artist")
        forward = AsyncMock()
        with (
            patch.object(email_inbound, "get_settings", return_value=settings),
            patch.object(email_inbound, "_resolve_destination", AsyncMock(return_value=route)),
            patch.object(email_inbound, "_archive_email_to_courier", AsyncMock(return_value="thread-2")),
            patch.object(email_inbound, "_forward_email", forward),
        ):
            result = await email_inbound.handle_inbound_email(_request(_received()), AsyncMock())
        self.assertEqual(result["reason"], "no_sender_configured")
        forward.assert_not_awaited()

    async def test_forward_is_sent_from_the_configured_resend_identity(self):
        settings = _settings(RESEND_FROM_EMAIL="mail@studio.example.test", RESEND_FROM_NAME="Studio Mail")
        client = _CapturingClient(httpx.Response(200, json={"id": "x"}, request=httpx.Request("POST", "https://r")))
        with (
            patch.object(email_inbound, "get_settings", return_value=settings),
            patch.object(email_inbound.httpx, "AsyncClient", return_value=client),
        ):
            await email_inbound._forward_email(
                {"from": "client@example.test", "subject": "Hi", "text": "Hello"}, "owner@example.test", "re_x"
            )
        self.assertEqual(client.calls[0][1]["json"]["from"], "Studio Mail <mail@studio.example.test>")

    async def test_forward_sender_falls_back_to_shop_email_and_studio_name(self):
        settings = _settings(SHOP_EMAIL="hello@studio.example.test")
        client = _CapturingClient(httpx.Response(200, json={"id": "x"}, request=httpx.Request("POST", "https://r")))
        with (
            patch.object(email_inbound, "get_settings", return_value=settings),
            patch.object(email_inbound.httpx, "AsyncClient", return_value=client),
        ):
            await email_inbound._forward_email(
                {"from": "client@example.test", "subject": "Hi", "text": "Hello"}, "owner@example.test", "re_x"
            )
        self.assertEqual(client.calls[0][1]["json"]["from"], "Fixture Studio <hello@studio.example.test>")


class ArtistHandleDomainTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_address_uses_the_receiving_domain(self):
        settings = _settings(COURIER_REPLY_DOMAIN="in.studio.example.test")
        with patch.object(email_connect, "get_settings", return_value=settings):
            result = await email_connect.check_handle_availability("Sarah", db=_Session(0))
        self.assertEqual(result.bttco_email, "sarah@in.studio.example.test")
        self.assertTrue(result.available)

    async def test_no_receiving_domain_refuses_new_handles_and_names_the_setting(self):
        with patch.object(email_connect, "get_settings", return_value=_settings()):
            with self.assertRaises(HTTPException) as raised:
                await email_connect.check_handle_availability("sarah", db=_Session())
            self.assertEqual(raised.exception.status_code, 503)
            self.assertIn("COURIER_REPLY_DOMAIN", raised.exception.detail)
            body = email_connect.EmailConnectRequest(handle="sarah", forward_to="sarah@example.com")
            with self.assertRaises(HTTPException) as raised:
                await email_connect.connect_email(body, db=_Session(), current_user=SimpleNamespace(id=uuid.uuid4()))
            self.assertEqual(raised.exception.status_code, 503)

    async def test_status_shows_no_address_without_a_domain(self):
        route = SimpleNamespace(handle="sarah", forward_to="sarah@example.test", verified=True)
        with patch.object(email_connect, "get_settings", return_value=_settings()):
            status = await email_connect.email_status(db=_Session(route), current_user=SimpleNamespace(id=1))
        self.assertIsNone(status.bttco_email)
        self.assertTrue(status.connected)


# ── 2. Outbound sender identity ───────────────────────────────────────────


class SenderIdentityTests(unittest.TestCase):
    def test_each_provider_uses_its_own_configured_address_and_name(self):
        settings = _settings(
            SENDGRID_FROM_EMAIL="sg@studio.example.test",
            SENDGRID_FROM_NAME="SG Name",
            RESEND_FROM_EMAIL="rs@studio.example.test",
            RESEND_FROM_NAME="RS Name",
            SMTP_FROM_EMAIL="smtp@studio.example.test",
            SMTP_FROM_NAME="SMTP Name",
            SHOP_EMAIL="hello@studio.example.test",
        )
        self.assertEqual(email_service.sender_identity("sendgrid", settings), ("SG Name", "sg@studio.example.test"))
        self.assertEqual(email_service.sender_identity("resend", settings), ("RS Name", "rs@studio.example.test"))
        self.assertEqual(email_service.sender_identity("smtp", settings), ("SMTP Name", "smtp@studio.example.test"))

    def test_blank_sender_falls_back_to_shop_email_and_studio_name(self):
        settings = _settings(SHOP_EMAIL="hello@studio.example.test")
        for provider in ("sendgrid", "resend", "smtp"):
            self.assertEqual(
                email_service.sender_identity(provider, settings), ("Fixture Studio", "hello@studio.example.test")
            )
        # SMTP sends from its own mailbox before the shop address.
        self.assertEqual(
            email_service.sender_identity("smtp", _settings(SMTP_USER="box@example.test"))[1], "box@example.test"
        )

    def test_nothing_configured_yields_no_address(self):
        self.assertEqual(email_service.sender_identity("resend", _settings())[1], "")


class ProviderSendTests(unittest.TestCase):
    def _send(self, sender, settings, status=200, **kwargs):
        response = httpx.Response(status, json={"id": "provider-id"}, request=httpx.Request("POST", "https://p"))
        response.headers["X-Message-Id"] = "provider-id"
        client = _CapturingClient(response)
        with (
            patch.object(email_service, "get_settings", return_value=settings),
            patch.object(email_service.httpx, "AsyncClient", return_value=client),
        ):
            result = asyncio.run(sender("to@example.test", "Subject", "<p>Hi</p>", **kwargs))
        return result, client

    def test_resend_sends_from_resend_from_email(self):
        settings = _settings(RESEND_API_KEY="re_x", RESEND_FROM_EMAIL="rs@studio.example.test", RESEND_FROM_NAME="RS")
        (ok, _id, _err), client = self._send(email_service._send_via_resend, settings)
        self.assertTrue(ok)
        self.assertEqual(client.calls[0][1]["json"]["from"], "RS <rs@studio.example.test>")

    def test_resend_without_any_sender_fails_without_calling_resend(self):
        (ok, _id, error), client = self._send(email_service._send_via_resend, _settings(RESEND_API_KEY="re_x"))
        self.assertFalse(ok)
        self.assertIn("RESEND_FROM_EMAIL", error)
        self.assertEqual(client.calls, [])

    def test_sendgrid_alias_uses_the_configured_domain(self):
        settings = _settings(
            SENDGRID_API_KEY="SG.x", SENDGRID_FROM_EMAIL="noreply@studio.example.test", SENDGRID_USE_ALIAS_FROM=True
        )
        (ok, _id, _err), client = self._send(email_service._send_via_sendgrid, settings, status=202, from_alias="admin")
        self.assertTrue(ok)
        self.assertEqual(
            client.calls[0][1]["json"]["from"], {"email": "admin@studio.example.test", "name": "Fixture Studio"}
        )

    def test_send_email_reaches_resend_with_the_configured_identity(self):
        settings = _settings(RESEND_API_KEY="re_x", RESEND_FROM_EMAIL="rs@studio.example.test")
        response = httpx.Response(200, json={"id": "provider-id"}, request=httpx.Request("POST", "https://p"))
        client = _CapturingClient(response)
        with (
            patch.object(email_service, "get_settings", return_value=settings),
            patch.object(email_service.httpx, "AsyncClient", return_value=client),
            patch.object(email_service, "_log_email_send", AsyncMock()),
        ):
            sent = asyncio.run(
                email_service.send_email("to@example.test", "Subject", "<p>Hi</p>", from_alias=email_service.FROM_ADMIN)
            )
        self.assertTrue(sent)
        self.assertEqual(client.calls[0][1]["json"]["from"], "Fixture Studio <rs@studio.example.test>")

    def test_smtp_from_header_uses_the_shared_identity(self):
        captured = {}

        async def fake_send(message, **_kwargs):
            captured["from"] = message["From"]

        settings = _settings(SMTP_USER="box@example.test", SMTP_PASSWORD="p")
        with (
            patch.object(email_service, "get_settings", return_value=settings),
            patch.object(email_service.aiosmtplib, "send", fake_send),
        ):
            self.assertTrue(asyncio.run(email_service._send_via_smtp("to@example.test", "S", "<p>x</p>")))
        self.assertEqual(captured["from"], "Fixture Studio <box@example.test>")


# ── 3. Plaid environments ─────────────────────────────────────────────────


class PlaidEnvironmentTests(unittest.TestCase):
    def _with(self, environment):
        settings = _settings(PLAID_CLIENT_ID="id", PLAID_SECRET="secret", PLAID_ENVIRONMENT=environment)
        return patch.object(plaid_endpoint, "get_settings", return_value=settings)

    def test_pinned_sdk_has_no_development_environment(self):
        self.assertFalse(hasattr(plaid_sdk.Environment, "Development"))

    def test_sandbox_and_production_build_a_client(self):
        for environment, host in (
            ("sandbox", plaid_sdk.Environment.Sandbox),
            (" Production ", plaid_sdk.Environment.Production),
            ("", plaid_sdk.Environment.Sandbox),
        ):
            with self.subTest(environment=environment), self._with(environment):
                client = plaid_endpoint._get_plaid_client()
                self.assertEqual(client.api_client.configuration.host, host)

    def test_unknown_environment_is_a_503_naming_the_setting(self):
        for environment in ("development", "staging"):
            with self.subTest(environment=environment), self._with(environment):
                self.assertFalse(plaid_endpoint.plaid_environment_supported())
                with self.assertRaises(HTTPException) as raised:
                    plaid_endpoint._get_plaid_client()
                self.assertEqual(raised.exception.status_code, 503)
                self.assertIn("PLAID_ENVIRONMENT", raised.exception.detail)


# ── 4. Stripe Connect country ─────────────────────────────────────────────


class StripeConnectCountryTests(unittest.TestCase):
    def test_default_is_us_and_values_are_normalized(self):
        self.assertEqual(Settings.model_fields["STRIPE_CONNECT_COUNTRY"].default, "US")
        self.assertEqual(_settings(STRIPE_CONNECT_COUNTRY=" gb ").STRIPE_CONNECT_COUNTRY, "GB")

    def test_rejects_anything_but_an_assigned_alpha2_code(self):
        for value in ("", "USA", "U1", "XX", "uk"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                _settings(STRIPE_CONNECT_COUNTRY=value)

    def test_connected_account_is_created_in_the_configured_country(self):
        stripe_client = MagicMock()
        with (
            patch.object(stripe_connect, "get_settings", return_value=_settings(STRIPE_CONNECT_COUNTRY="CA")),
            patch.object(stripe_connect, "_init_stripe", return_value=stripe_client),
        ):
            stripe_connect._create_connected_account(SimpleNamespace(name="Artist", email="a@example.test"))
        payload = stripe_client.v2.core.accounts.create.call_args.args[0]
        self.assertEqual(payload["identity"], {"country": "ca"})


# ── 5. Google sign-in client pair ─────────────────────────────────────────


class GoogleSigninClientTests(unittest.TestCase):
    def test_shared_client_is_used_when_no_signin_client_is_set(self):
        settings = _settings(GOOGLE_CLIENT_ID="shared", GOOGLE_CLIENT_SECRET="shared-secret")
        self.assertEqual(settings.google_signin_client, ("shared", "shared-secret"))
        self.assertEqual(settings.google_signin_audience, "shared")

    def test_separate_signin_client_needs_its_own_secret(self):
        base = {"GOOGLE_CLIENT_ID": "calendar", "GOOGLE_CLIENT_SECRET": "calendar-secret"}
        self.assertEqual(_settings(**base, GOOGLE_SIGNIN_CLIENT_ID="signin").google_signin_client, ("", ""))
        paired = _settings(**base, GOOGLE_SIGNIN_CLIENT_ID="signin", GOOGLE_SIGNIN_CLIENT_SECRET="signin-secret")
        self.assertEqual(paired.google_signin_client, ("signin", "signin-secret"))
        self.assertEqual(paired.google_signin_audience, "signin")

    def test_signin_id_equal_to_the_shared_client_reuses_its_secret(self):
        settings = _settings(GOOGLE_CLIENT_ID="same", GOOGLE_CLIENT_SECRET="secret", GOOGLE_SIGNIN_CLIENT_ID="same")
        self.assertEqual(settings.google_signin_client, ("same", "secret"))

    def _patched(self, **values):
        stack = []
        for name, value in values.items():
            stack.append(patch.object(auth._settings, name, value))
        return stack

    def test_link_start_names_the_missing_signin_secret(self):
        patches = self._patched(
            GOOGLE_CLIENT_ID="calendar",
            GOOGLE_CLIENT_SECRET="calendar-secret",
            GOOGLE_SIGNIN_CLIENT_ID="signin",
            GOOGLE_SIGNIN_CLIENT_SECRET="",
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        user = SimpleNamespace(id=uuid.uuid4(), role="artist")
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(auth.google_link_start.__wrapped__(SimpleNamespace(), current_user=user))
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("GOOGLE_SIGNIN_CLIENT_SECRET", raised.exception.detail)

    def test_token_exchange_sends_the_signin_clients_own_secret(self):
        patches = self._patched(
            GOOGLE_CLIENT_ID="calendar",
            GOOGLE_CLIENT_SECRET="calendar-secret",
            GOOGLE_SIGNIN_CLIENT_ID="signin",
            GOOGLE_SIGNIN_CLIENT_SECRET="signin-secret",
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        captured = {}

        class _Token:
            def raise_for_status(self):
                return None

            def json(self):
                return {"id_token": "token"}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, _url, data=None, **_kwargs):
                captured.update(data)
                return _Token()

        now = datetime.now(UTC)
        state = jwt.encode(
            {"purpose": "google_signin", "iat": now, "exp": now + timedelta(minutes=5)},
            auth._settings.SECRET_KEY,
            algorithm=auth._settings.ALGORITHM,
        )
        with (
            patch.object(auth.httpx, "AsyncClient", return_value=_Client()),
            patch.object(auth, "_resolve_google_user", AsyncMock(side_effect=HTTPException(401))),
        ):
            asyncio.run(
                auth.google_signin_callback.__wrapped__(
                    SimpleNamespace(), background_tasks=None, code="c", state=state, error=None, db=None
                )
            )
        self.assertEqual((captured["client_id"], captured["client_secret"]), ("signin", "signin-secret"))

    def test_auth_options_google_flag_follows_the_signin_pair(self):
        unpaired = _settings(GOOGLE_CLIENT_ID="calendar", GOOGLE_CLIENT_SECRET="s", GOOGLE_SIGNIN_CLIENT_ID="signin")
        with patch.object(auth, "get_settings", return_value=unpaired):
            self.assertFalse(asyncio.run(auth.auth_options())["google"])
        with patch.object(auth, "get_settings", return_value=_settings(GOOGLE_CLIENT_ID="c", GOOGLE_CLIENT_SECRET="s")):
            self.assertTrue(asyncio.run(auth.auth_options())["google"])


# ── 7. Resend delivery webhook signature ──────────────────────────────────


class ResendDeliveryWebhookSignatureTests(unittest.IsolatedAsyncioTestCase):
    BODY = json.dumps({"type": "email.delivered", "data": {}}).encode()

    async def _call(self, settings, headers=None):
        db = AsyncMock()
        with patch.object(email_log, "get_settings", return_value=settings):
            result = await email_log.resend_webhook(_request(self.BODY, headers, "/api/v1/webhooks/resend"), db=db)
        return result, db

    async def test_blank_secret_is_refused_outside_local_environments(self):
        for app_env in ("production", "staging"):
            settings = _settings(APP_ENV=app_env, DEBUG=False, RESEND_WEBHOOK_SECRET="", **NON_DEV_SHARED_SECRETS)
            with self.subTest(app_env=app_env), self.assertRaises(HTTPException) as raised:
                await self._call(settings)
            self.assertEqual(raised.exception.status_code, 503)
            self.assertIn("signing secret not configured", raised.exception.detail)

    async def test_blank_secret_still_works_in_test_and_development(self):
        result, _db = await self._call(_settings(APP_ENV="test", RESEND_WEBHOOK_SECRET=""))
        self.assertEqual(result, {"ok": True})

    async def test_bad_or_missing_signature_is_rejected(self):
        settings = _settings(APP_ENV="production", RESEND_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET, **NON_DEV_SHARED_SECRETS)
        with self.assertRaises(HTTPException) as raised:
            await self._call(settings)
        self.assertEqual(raised.exception.status_code, 400)
        bad = {
            "svix-id": "msg_1",
            "svix-timestamp": str(int(datetime.now(UTC).timestamp())),
            "svix-signature": "v1,AAAA",
        }
        with self.assertRaises(HTTPException) as raised:
            await self._call(settings, bad)
        self.assertEqual(raised.exception.status_code, 401)

    async def test_valid_signature_is_accepted(self):
        from svix.webhooks import Webhook

        settings = _settings(APP_ENV="production", RESEND_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET, **NON_DEV_SHARED_SECRETS)
        timestamp = datetime.now(UTC)
        signature = Webhook(TEST_WEBHOOK_SECRET).sign("msg_1", timestamp, self.BODY.decode())
        headers = {"svix-id": "msg_1", "svix-timestamp": str(int(timestamp.timestamp())), "svix-signature": signature}
        result, _db = await self._call(settings, headers)
        self.assertEqual(result, {"ok": True})


# ── 8. Docs match the code ────────────────────────────────────────────────


class DocsMatchCodeTests(unittest.TestCase):
    def test_new_settings_are_documented_in_env_example_and_readme(self):
        example = (API / ".env.example").read_text(encoding="utf-8")
        readme = (API.parent / "README.md").read_text(encoding="utf-8")
        for name in (
            "STRIPE_CONNECT_COUNTRY",
            "GOOGLE_SIGNIN_CLIENT_SECRET",
            "SHOP_OWNER_EMAIL",
            "COURIER_REPLY_DOMAIN",
        ):
            self.assertIn(name, example, name)
            self.assertIn(name, readme, name)

    def test_removed_publishable_key_is_not_advertised(self):
        for path in (API.parent / "README.md", API / ".env.example", API.parent / "Dockerfile"):
            self.assertNotIn("STRIPE_PUBLISHABLE_KEY", path.read_text(encoding="utf-8"), path.name)

    def test_plaid_development_is_not_offered_as_an_environment(self):
        example = (API / ".env.example").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"PLAID_ENVIRONMENT=.*development", example))


if __name__ == "__main__":
    unittest.main()
