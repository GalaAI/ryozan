"""GET /integrations/options reports configured connections and role access, never secret values."""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import asyncio
import json
import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints import integrations, plaid, quests, stripe_connect
from app.core.config import Settings
from app.core.database import get_db
from app.core.security import ROLE_HIERARCHY, ROLE_RANK, get_optional_current_user, require_role
from app.services import calendar_sync

IDS = [
    "google_calendar",
    "google_signin_link",
    "pinterest",
    "plaid",
    "stripe_connect",
    "push",
    "email_forwarding",
    "shop_email",
]
# Roles the endpoint behind each id refuses.
REFUSED = {
    "stripe_connect": {"collector", "assistant", "admin"},
    "google_signin_link": {"collector"},
    "shop_email": {"collector", "assistant", "artist"},
}

SECRETS = {
    "GOOGLE_CLIENT_ID": "google-id-SECRET-1",
    "GOOGLE_CLIENT_SECRET": "google-secret-SECRET-2",
    "PINTEREST_CLIENT_ID": "pin-id-SECRET-3",
    "PINTEREST_CLIENT_SECRET": "pin-secret-SECRET-4",
    "PLAID_CLIENT_ID": "plaid-id-SECRET-5",
    "PLAID_SECRET": "plaid-secret-SECRET-6",
    "STRIPE_SECRET_KEY": "sk_test_SECRET-7",
    "VAPID_PUBLIC_KEY": "vapid-public-SECRET-8",
    "VAPID_PRIVATE_KEY": "vapid-private-SECRET-9",
    "RESEND_API_KEY": "re_SECRET-10",
}
# Non-secret settings a complete install also needs.
COMPLETE = {
    **SECRETS,
    "VAPID_SUBJECT": "mailto:owner@studio.example.test",
    "COURIER_REPLY_DOMAIN": "in.studio.example.test",
}

BLANK = {
    **{key: "" for key in COMPLETE},
    "SENDGRID_API_KEY": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "SHOP_EMAIL": "",
    "GOOGLE_SIGNIN_CLIENT_ID": "",
    "GOOGLE_SIGNIN_CLIENT_SECRET": "",
    "GOOGLE_CALENDAR_AUTH_MODE": "oauth",
    "PLAID_ENVIRONMENT": "sandbox",
}


def _settings(**overrides):
    return Settings(_env_file=None, APP_ENV="test", **{**BLANK, **overrides})


@contextmanager
def _patched(settings):
    with ExitStack() as stack:
        for module in (integrations, quests, calendar_sync, stripe_connect, plaid):
            stack.enter_context(patch.object(module, "get_settings", return_value=settings))
        yield


def _by_id(response):
    payload = response.model_dump() if hasattr(response, "model_dump") else response
    return {item["id"]: item for item in payload["integrations"]}


def _options(role, settings):
    with _patched(settings):
        return _by_id(integrations.integration_options(role))


def _configured(integration_id, **overrides):
    return _options(None, _settings(**overrides))[integration_id]["configured"]


class IntegrationOptionsTests(unittest.TestCase):
    def test_contract_lists_every_integration_in_order(self):
        response = _options(None, _settings()).values()
        self.assertEqual([item["id"] for item in response], IDS)
        for item in response:
            self.assertEqual(set(item), {"id", "configured", "allowed"})

    def test_anonymous_gets_null_allowed(self):
        for item in _options(None, _settings(**COMPLETE)).values():
            self.assertIsNone(item["allowed"])
            self.assertTrue(item["configured"], item["id"])

    def test_allowed_matches_each_endpoint_for_every_role(self):
        for role in ROLE_HIERARCHY:
            options = _options(role, _settings())
            self.assertEqual(
                {key: item["allowed"] for key, item in options.items()},
                {key: role not in REFUSED.get(key, set()) for key in IDS},
                role,
            )

    def test_stripe_connect_endpoint_enforces_the_same_role_set(self):
        self.assertIs(stripe_connect.STRIPE_CONNECT_ROLES, integrations.STRIPE_CONNECT_ROLES)
        for role in ROLE_HIERARCHY:
            user = SimpleNamespace(role=role)
            if role in integrations.STRIPE_CONNECT_ROLES:
                stripe_connect._require_connect_artist(user)
            else:
                with self.assertRaises(HTTPException):
                    stripe_connect._require_connect_artist(user)

    def test_google_signin_link_roles_match_the_link_endpoint_gate(self):
        # GET /auth/google/link depends on require_role("assistant").
        gate = require_role("assistant")
        for role in ROLE_HIERARCHY:
            user = SimpleNamespace(role=role)
            if role in integrations.GOOGLE_SIGNIN_LINK_ROLES:
                self.assertIs(asyncio.run(gate(current_user=user)), user)
            else:
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(gate(current_user=user))
                self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            integrations.GOOGLE_SIGNIN_LINK_ROLES, {r for r in ROLE_HIERARCHY if ROLE_RANK[r] >= ROLE_RANK["assistant"]}
        )

    def test_nothing_configured_on_bare_install(self):
        self.assertFalse(any(item["configured"] for item in _options("artist", _settings()).values()))

    def test_each_integration_needs_its_full_credential_pair(self):
        cases = {
            "google_calendar": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
            "google_signin_link": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
            "pinterest": ("PINTEREST_CLIENT_ID", "PINTEREST_CLIENT_SECRET"),
            "plaid": ("PLAID_CLIENT_ID", "PLAID_SECRET"),
        }
        for integration_id, (first, second) in cases.items():
            self.assertFalse(_configured(integration_id, **{first: "x"}), integration_id)
            self.assertTrue(_configured(integration_id, **{first: "x", second: "y"}), integration_id)

    def test_push_needs_keys_and_a_usable_subject(self):
        keys = {"VAPID_PUBLIC_KEY": "pub", "VAPID_PRIVATE_KEY": "priv"}
        self.assertFalse(_configured("push", **keys))
        self.assertFalse(_configured("push", VAPID_PUBLIC_KEY="pub", VAPID_SUBJECT="mailto:o@example.test"))
        self.assertTrue(_configured("push", **keys, VAPID_SUBJECT="mailto:o@example.test"))
        # SHOP_EMAIL is the documented fallback subject.
        self.assertTrue(_configured("push", **keys, SHOP_EMAIL="shop@example.test"))

    def test_email_forwarding_needs_resend_and_a_receiving_domain(self):
        domain = {"COURIER_REPLY_DOMAIN": "in.example.test"}
        self.assertFalse(_configured("email_forwarding", RESEND_API_KEY="re_x"))
        self.assertTrue(_configured("email_forwarding", RESEND_API_KEY="re_x", **domain))
        # Handles are received and forwarded by Resend; another provider alone cannot run them.
        self.assertFalse(_configured("email_forwarding", SENDGRID_API_KEY="SG.x", **domain))
        self.assertFalse(_configured("email_forwarding", SMTP_USER="u", SMTP_PASSWORD="p", **domain))

    def test_smtp_password_alone_is_not_an_outbound_provider(self):
        with _patched(_settings(SMTP_PASSWORD="p")):
            self.assertFalse(quests.outbound_email_configured())
        with _patched(_settings(SMTP_USER="u", SMTP_PASSWORD="p")):
            self.assertTrue(quests.outbound_email_configured())

    def test_stripe_follows_its_owner_helper(self):
        self.assertTrue(_configured("stripe_connect", STRIPE_SECRET_KEY="sk"))
        self.assertFalse(_configured("stripe_connect", STRIPE_SECRET_KEY="  "))

    def test_plaid_is_not_configured_with_an_unsupported_environment(self):
        keys = {"PLAID_CLIENT_ID": "x", "PLAID_SECRET": "y"}
        self.assertTrue(_configured("plaid", **keys, PLAID_ENVIRONMENT="production"))
        self.assertFalse(_configured("plaid", **keys, PLAID_ENVIRONMENT="development"))

    def test_signin_link_needs_the_secret_of_a_separate_signin_client(self):
        shared = {"GOOGLE_CLIENT_ID": "calendar-client", "GOOGLE_CLIENT_SECRET": "calendar-secret"}
        self.assertFalse(_configured("google_signin_link", **shared, GOOGLE_SIGNIN_CLIENT_ID="signin-client"))
        self.assertTrue(
            _configured(
                "google_signin_link",
                **shared,
                GOOGLE_SIGNIN_CLIENT_ID="signin-client",
                GOOGLE_SIGNIN_CLIENT_SECRET="signin-secret",
            )
        )
        # Calendar does not depend on the sign-in client.
        self.assertTrue(_configured("google_calendar", **shared, GOOGLE_SIGNIN_CLIENT_ID="signin-client"))

    def test_service_account_calendar_mode_is_not_user_connectable(self):
        settings = _settings(
            GOOGLE_CLIENT_ID="x",
            GOOGLE_CLIENT_SECRET="y",
            GOOGLE_CALENDAR_AUTH_MODE=calendar_sync.WORKSPACE_DELEGATED_MODE,
        )
        options = _options("artist", settings)
        self.assertFalse(options["google_calendar"]["configured"])
        # Sign-in linking uses its own OAuth flow and stays available.
        self.assertTrue(options["google_signin_link"]["configured"])

    def test_response_never_contains_secret_values(self):
        with _patched(_settings(**COMPLETE)):
            body = json.dumps(integrations.integration_options("founder").model_dump())
        for value in SECRETS.values():
            self.assertNotIn(value, body)


async def _no_db():
    yield None


class IntegrationOptionsHttpTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(integrations.router)
        app.dependency_overrides[get_db] = _no_db
        self.client = TestClient(app)

    def test_anonymous_request_succeeds_with_null_allowed(self):
        with _patched(_settings(**COMPLETE)):
            response = self.client.get("/integrations/options")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["integrations"]], IDS)
        self.assertTrue(all(item["allowed"] is None for item in response.json()["integrations"]))
        for value in SECRETS.values():
            self.assertNotIn(value, response.text)

    def test_unusable_token_is_treated_as_anonymous(self):
        with _patched(_settings()):
            response = self.client.get("/integrations/options", headers={"Authorization": "Bearer not-a-jwt"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(item["allowed"] is None for item in response.json()["integrations"]))

    def test_signed_in_collector_gets_role_answers(self):
        self.client.app.dependency_overrides[get_optional_current_user] = lambda: SimpleNamespace(role="collector")
        with _patched(_settings()):
            items = {
                item["id"]: item["allowed"] for item in self.client.get("/integrations/options").json()["integrations"]
            }
        self.assertEqual(items, {key: "collector" not in REFUSED.get(key, set()) for key in IDS})

    def test_optional_user_without_token_is_none(self):
        self.assertIsNone(asyncio.run(get_optional_current_user(token=None, db=None)))


if __name__ == "__main__":
    unittest.main()
