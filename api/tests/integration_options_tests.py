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

from app.api.v1.endpoints import integrations, quests, stripe_connect
from app.core.database import get_db
from app.core.security import ROLE_HIERARCHY, get_optional_current_user
from app.services import calendar_sync

IDS = ["google_calendar", "pinterest", "plaid", "stripe_connect", "push", "email_forwarding"]

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


def _settings(**overrides):
    base = {key: "" for key in SECRETS}
    base.update(SENDGRID_API_KEY="", SMTP_PASSWORD="", GOOGLE_CALENDAR_AUTH_MODE="oauth")
    return SimpleNamespace(**{**base, **overrides})


@contextmanager
def _patched(settings):
    with ExitStack() as stack:
        for module in (integrations, quests, calendar_sync, stripe_connect):
            stack.enter_context(patch.object(module, "get_settings", return_value=settings))
        yield


def _by_id(response):
    payload = response.model_dump() if hasattr(response, "model_dump") else response
    return {item["id"]: item for item in payload["integrations"]}


def _options(role, settings):
    with _patched(settings):
        return _by_id(integrations.integration_options(role))


class IntegrationOptionsTests(unittest.TestCase):
    def test_contract_lists_every_integration_in_order(self):
        response = _options(None, _settings()).values()
        self.assertEqual([item["id"] for item in response], IDS)
        for item in response:
            self.assertEqual(set(item), {"id", "configured", "allowed"})

    def test_anonymous_gets_null_allowed(self):
        for item in _options(None, _settings(**SECRETS)).values():
            self.assertIsNone(item["allowed"])
            self.assertTrue(item["configured"])

    def test_collector_may_connect_everything_but_stripe(self):
        options = _options("collector", _settings())
        self.assertEqual(
            {key: item["allowed"] for key, item in options.items()},
            {key: key != "stripe_connect" for key in IDS},
        )

    def test_artist_and_founder_may_connect_everything(self):
        for role in ("artist", "founder"):
            self.assertTrue(all(item["allowed"] for item in _options(role, _settings()).values()), role)

    def test_assistant_and_admin_match_stripe_connect_gate(self):
        for role in ("assistant", "admin"):
            self.assertFalse(_options(role, _settings())["stripe_connect"]["allowed"], role)

    def test_stripe_connect_endpoint_enforces_the_same_role_set(self):
        self.assertIs(stripe_connect.STRIPE_CONNECT_ROLES, integrations.STRIPE_CONNECT_ROLES)
        for role in ROLE_HIERARCHY:
            user = SimpleNamespace(role=role)
            if role in integrations.STRIPE_CONNECT_ROLES:
                stripe_connect._require_connect_artist(user)
            else:
                with self.assertRaises(HTTPException):
                    stripe_connect._require_connect_artist(user)

    def test_nothing_configured_on_bare_install(self):
        self.assertFalse(any(item["configured"] for item in _options("artist", _settings()).values()))

    def test_each_integration_needs_its_full_credential_pair(self):
        cases = {
            "google_calendar": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
            "pinterest": ("PINTEREST_CLIENT_ID", "PINTEREST_CLIENT_SECRET"),
            "plaid": ("PLAID_CLIENT_ID", "PLAID_SECRET"),
            "push": ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"),
        }
        for integration_id, (first, second) in cases.items():
            only_first = _options(None, _settings(**{first: "x"}))[integration_id]["configured"]
            both = _options(None, _settings(**{first: "x", second: "y"}))[integration_id]["configured"]
            self.assertFalse(only_first, integration_id)
            self.assertTrue(both, integration_id)

    def test_stripe_and_email_follow_their_owner_helpers(self):
        self.assertTrue(_options(None, _settings(STRIPE_SECRET_KEY="sk"))["stripe_connect"]["configured"])
        self.assertFalse(_options(None, _settings(STRIPE_SECRET_KEY="  "))["stripe_connect"]["configured"])
        for provider in ("RESEND_API_KEY", "SENDGRID_API_KEY", "SMTP_PASSWORD"):
            self.assertTrue(_options(None, _settings(**{provider: "k"}))["email_forwarding"]["configured"], provider)

    def test_service_account_calendar_mode_is_not_user_connectable(self):
        settings = _settings(
            GOOGLE_CLIENT_ID="x",
            GOOGLE_CLIENT_SECRET="y",
            GOOGLE_CALENDAR_AUTH_MODE=calendar_sync.WORKSPACE_DELEGATED_MODE,
        )
        self.assertFalse(_options("artist", settings)["google_calendar"]["configured"])

    def test_response_never_contains_secret_values(self):
        with _patched(_settings(**SECRETS)):
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
        with _patched(_settings(**SECRETS)):
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
        self.assertEqual(items, {key: key != "stripe_connect" for key in IDS})

    def test_optional_user_without_token_is_none(self):
        self.assertIsNone(asyncio.run(get_optional_current_user(token=None, db=None)))


if __name__ == "__main__":
    unittest.main()
