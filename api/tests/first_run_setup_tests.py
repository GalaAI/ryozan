"""
First-run setup and owner-saved studio settings.

Pins: a new install is claimed only with the setup code, and only once; an install
with a founder is never claimable; saved values run through the Settings validators,
apply at once and reach the public settings; only the listed values can be saved;
and the studio's zone drives shop time.
"""

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

from app.api.v1.router import api_router  # noqa: E402
from app.constants.timezones import shop_day_bounds, shop_timezone  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.core.security import get_current_user  # noqa: E402
from app.services import studio_config  # noqa: E402

SETUP = "/api/v1/studio/setup"
EDIT = "/api/v1/studio/settings/edit"
PUBLIC = "/api/v1/studio/settings"


def _claim_body(**overrides):
    body = {
        "setup_code": studio_config.setup_code(get_settings()),
        "studio_name": "Moth and Rose Tattoo",
        "timezone": "America/Chicago",
        "owner_name": "Rae Owner",
        "email": "Rae@Moth.example",
        "password": "correct-horse-9",
    }
    return {**body, **overrides}


class _StudioSettingsTestCase(unittest.TestCase):
    """Restores the shared Settings object so one test's save cannot reach another."""

    def setUp(self):
        settings = get_settings()
        saved = {key: getattr(settings, key) for key in studio_config.EDITABLE_SETTINGS}
        self.addCleanup(studio_config.apply_values, settings, saved)
        self.settings = settings
        self.settings.SECRET_KEY = self.settings.SECRET_KEY or "test-secret-key-long-enough-to-sign"

        self.db = MagicMock()
        self.db.get = AsyncMock(return_value=None)  # no claim row
        self.db.scalar = AsyncMock(return_value=0)  # no founder, no email clash
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        self.db.execute = AsyncMock(return_value=result)
        self.db.flush = AsyncMock()
        self.db.commit = AsyncMock()
        self.db.rollback = AsyncMock()

        async def _db():
            yield self.db

        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.include_router(api_router, prefix="/api/v1")
        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = TestClient(app)
        limiter.reset()

    def _as_role(self, role: str) -> None:
        user = MagicMock(role=role)
        self.app.dependency_overrides[get_current_user] = lambda: user


class SetupClaimTests(_StudioSettingsTestCase):
    def test_new_install_says_it_needs_setup_without_caching(self):
        response = self.client.get(SETUP)
        self.assertEqual(response.json(), {"needs_setup": True, "has_accounts": False})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_install_with_a_founder_is_not_claimable(self):
        self.db.scalar = AsyncMock(return_value=1)
        self.assertEqual(self.client.get(SETUP).json(), {"needs_setup": False, "has_accounts": True})
        response = self.client.post(SETUP, json=_claim_body())
        self.assertEqual(response.status_code, 409)
        self.db.add.assert_not_called()

    def test_accounts_without_a_founder_stay_claimable_but_are_reported(self):
        # All accounts, then founders: /portal keeps its login page for these people.
        self.db.scalar = AsyncMock(side_effect=[5, 0])
        self.assertEqual(self.client.get(SETUP).json(), {"needs_setup": True, "has_accounts": True})

    def test_claimed_install_is_not_claimable(self):
        self.db.get = AsyncMock(return_value=MagicMock())
        self.assertEqual(self.client.post(SETUP, json=_claim_body()).status_code, 409)
        self.db.add.assert_not_called()

    def test_wrong_code_is_refused_and_creates_nothing(self):
        response = self.client.post(SETUP, json=_claim_body(setup_code="AAAA-BBBB-CCCC"))
        self.assertEqual(response.status_code, 403)
        self.db.add.assert_not_called()
        self.assertNotEqual(self.settings.STUDIO_NAME, "Moth and Rose Tattoo")

    def test_claim_creates_the_owner_saves_the_identity_and_signs_in(self):
        self.settings.SHOP_EMAIL = ""
        response = self.client.post(SETUP, json=_claim_body())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["access_token"])

        added = [call.args[0] for call in self.db.add.call_args_list]
        owner = next(row for row in added if hasattr(row, "role"))
        self.assertEqual((owner.role, owner.email, owner.name), ("founder", "rae@moth.example", "Rae Owner"))
        self.assertFalse(owner.must_reset_password)
        self.assertIsNotNone(owner.email_verified_at)
        saved = {row.key: row.value for row in added if hasattr(row, "key")}
        self.assertIn(studio_config.SETUP_CLAIM_KEY, saved)
        self.assertEqual(saved["STUDIO_NAME"], "Moth and Rose Tattoo")
        self.assertEqual(saved["SHOP_EMAIL"], "rae@moth.example")

        public = self.client.get(PUBLIC).json()
        self.assertEqual(public["studio_name"], "Moth and Rose Tattoo")
        self.assertEqual(public["timezone"], "America/Chicago")
        self.assertEqual(public["owner_name"], "Rae Owner")

    def test_a_losing_concurrent_claim_gets_a_conflict(self):
        from sqlalchemy.exc import IntegrityError

        self.db.flush = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate key")))
        self.assertEqual(self.client.post(SETUP, json=_claim_body()).status_code, 409)
        self.db.rollback.assert_awaited()
        self.assertNotEqual(self.settings.STUDIO_NAME, "Moth and Rose Tattoo")

    def test_a_bad_zone_is_refused(self):
        response = self.client.post(SETUP, json=_claim_body(timezone="Mars/Olympus"))
        self.assertEqual(response.status_code, 422)
        self.db.add.assert_not_called()

    def test_email_already_on_an_account_is_refused(self):
        self.db.scalar = AsyncMock(side_effect=[0, 1])
        self.assertEqual(self.client.post(SETUP, json=_claim_body()).status_code, 409)
        self.db.add.assert_not_called()

    def test_short_password_is_refused(self):
        self.assertEqual(self.client.post(SETUP, json=_claim_body(password="short")).status_code, 422)


class SetupCodeTests(unittest.TestCase):
    def test_code_is_stable_per_secret_and_never_contains_it(self):
        one = Settings(SECRET_KEY="first-secret-key-long-enough-to-sign")
        two = Settings(SECRET_KEY="other-secret-key-long-enough-to-sign")
        self.assertEqual(studio_config.setup_code(one), studio_config.setup_code(one))
        self.assertNotEqual(studio_config.setup_code(one), studio_config.setup_code(two))
        self.assertRegex(studio_config.setup_code(one), r"^[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}$")

    def test_match_forgives_case_spaces_and_dashes_only(self):
        settings = Settings(SECRET_KEY="first-secret-key-long-enough-to-sign")
        code = studio_config.setup_code(settings)
        self.assertTrue(studio_config.setup_code_matches(settings, f" {code.lower().replace('-', ' ')} "))
        self.assertFalse(studio_config.setup_code_matches(settings, code[:-1]))
        self.assertFalse(studio_config.setup_code_matches(settings, ""))


class SettingsEditTests(_StudioSettingsTestCase):
    def test_only_the_founder_can_read_or_save(self):
        self.assertEqual(self.client.get(EDIT).status_code, 401)
        self._as_role("admin")
        self.assertEqual(self.client.get(EDIT).status_code, 403)
        self.assertEqual(self.client.put(EDIT, json={"STUDIO_NAME": "Nope"}).status_code, 403)
        self.assertNotEqual(self.settings.STUDIO_NAME, "Nope")

    def test_save_applies_at_once_and_reaches_the_public_settings(self):
        self._as_role("founder")
        response = self.client.put(
            EDIT,
            json={"STUDIO_NAME": "Rose Hall", "STUDIO_TIMEZONE": "Europe/London", "BOOKING_CLOSED_WEEKDAYS": "Sunday,mon"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["STUDIO_NAME"], "Rose Hall")
        self.db.commit.assert_awaited()
        public = self.client.get(PUBLIC).json()
        self.assertEqual(public["studio_name"], "Rose Hall")
        self.assertEqual(public["timezone"], "Europe/London")
        self.assertEqual(public["booking_days"]["closed_weekdays"], ["sun", "mon"])

    def test_invalid_values_are_refused_and_change_nothing(self):
        self._as_role("founder")
        for body in (
            {"STUDIO_TIMEZONE": "Mars/Olympus"},
            {"BOOKING_CLOSED_WEEKDAYS": "someday"},
            {"STUDIO_SOCIAL_LINKS": "not a link"},
            {"STUDIO_NAME": "   "},
            {"SHOP_EMAIL": "not-an-email"},
        ):
            before = self.settings.STUDIO_TIMEZONE
            self.assertEqual(self.client.put(EDIT, json=body).status_code, 422, body)
            self.assertEqual(self.settings.STUDIO_TIMEZONE, before)
        self.db.commit.assert_not_awaited()

    def test_secrets_and_urls_cannot_be_saved(self):
        self._as_role("founder")
        before = self.settings.SECRET_KEY
        response = self.client.put(EDIT, json={"SECRET_KEY": "stolen", "FRONTEND_URL": "https://evil.example"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.settings.SECRET_KEY, before)
        self.assertNotEqual(self.settings.FRONTEND_URL, "https://evil.example")
        with self.assertRaises(studio_config.StudioSettingsError):
            studio_config.validated_changes(self.settings, {"SECRET_KEY": "stolen"})

    def test_a_blank_clears_an_optional_value(self):
        self._as_role("founder")
        self.settings.STUDIO_PHONE = "555-0100"
        self.assertEqual(self.client.put(EDIT, json={"STUDIO_PHONE": ""}).status_code, 200)
        self.assertEqual(self.settings.STUDIO_PHONE, "")


class SavedSettingsLoadTests(_StudioSettingsTestCase):
    def _rows(self, **values):
        rows = [MagicMock(key=key, value=value) for key, value in values.items()]
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        self.db.execute = AsyncMock(return_value=result)

    async def _load(self):
        return await studio_config.load_saved_settings(self.db)

    def test_saved_values_win_over_the_environment(self):
        import asyncio

        self._rows(STUDIO_NAME="Saved Name", STUDIO_TIMEZONE="Asia/Tokyo")
        self.assertEqual(asyncio.run(self._load()), 2)
        self.assertEqual(self.settings.STUDIO_NAME, "Saved Name")
        self.assertEqual(shop_timezone().key, "Asia/Tokyo")

    def test_the_claim_row_a_bad_row_and_a_stray_key_are_skipped(self):
        import asyncio

        before = self.settings.STUDIO_TIMEZONE
        self._rows(
            **{studio_config.SETUP_CLAIM_KEY: "rae@moth.example"},
            STUDIO_TIMEZONE="Mars/Olympus",
            SECRET_KEY="stolen",
        )
        secret = self.settings.SECRET_KEY
        self.assertEqual(asyncio.run(self._load()), 0)
        self.assertEqual(self.settings.STUDIO_TIMEZONE, before)
        self.assertEqual(self.settings.SECRET_KEY, secret)


class StudioZoneTests(_StudioSettingsTestCase):
    def test_shop_time_follows_the_saved_zone(self):
        self.settings.STUDIO_TIMEZONE = "America/Los_Angeles"
        start, _ = shop_day_bounds(datetime(2026, 7, 4).date(), datetime(2026, 7, 4).date())
        self.assertEqual(start.astimezone(UTC).hour, 7)
        self.settings.STUDIO_TIMEZONE = "Asia/Tokyo"
        start, _ = shop_day_bounds(datetime(2026, 7, 4).date(), datetime(2026, 7, 4).date())
        self.assertEqual(start.astimezone(UTC).hour, 15)

    def test_client_facing_time_names_the_real_zone(self):
        from app.services.email import _format_datetime_shop

        moment = datetime(2026, 7, 4, 20, 0, tzinfo=UTC)
        self.settings.STUDIO_TIMEZONE = "America/Chicago"
        self.assertEqual(_format_datetime_shop(moment), "July 04, 2026 at 03:00 PM CDT")
        self.settings.STUDIO_TIMEZONE = "America/New_York"
        self.assertEqual(_format_datetime_shop(datetime(2026, 1, 15, 20, 0, tzinfo=UTC)), "January 15, 2026 at 03:00 PM EST")

    def test_a_bad_zone_in_the_environment_fails_at_startup(self):
        with self.assertRaises(ValueError):
            Settings(STUDIO_TIMEZONE="Mars/Olympus")


class RenamedStudioReachesPromptsTests(_StudioSettingsTestCase):
    def test_booking_consent_and_courier_prompt_read_the_current_name(self):
        from app.api.v1.endpoints.bookings import booking_sms_consent_text
        from app.api.v1.endpoints.conversations import gala_courier_system

        self.settings.STUDIO_NAME = "Rose Hall"
        self.assertIn("Rose Hall", booking_sms_consent_text())
        self.assertIn("Rose Hall", gala_courier_system())


if __name__ == "__main__":
    unittest.main()
