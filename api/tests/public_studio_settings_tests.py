"""GET /api/v1/studio/settings: public, cacheable, no secrets, neutral by default.

One install is one studio. The browser reads the studio's display and booking
settings from this endpoint instead of hardcoding one studio's details, so a
new install must get neutral values and a configured studio must get its own.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.api.v1.endpoints import push as push_endpoints  # noqa: E402
from app.api.v1.router import api_router  # noqa: E402
from app.constants.timezones import SHOP_TIMEZONE  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.services import push as push_service  # noqa: E402

PATH = "/api/v1/studio/settings"

NEUTRAL = {
    "STUDIO_OWNER_NAME": "",
    "SHOP_EMAIL": "",
    "STUDIO_PHONE": "",
    "STUDIO_ADDRESS": "",
    "DEFAULT_ARTIST_SLUG": "",
    "BOOKING_CLOSED_WEEKDAYS": "",
    "BOOKING_REQUEST_ONLY_WEEKDAYS": "",
    "BOOKING_REQUEST_ONLY_ARTIST_SLUGS": "",
    "WAIVER_TEMPLATE_ID": "",
    "SAMPLE_FLASH_URLS": "",
    "VAPID_SUBJECT": "",
    "PUBLIC_SITE_MODE": "product",
    "STUDIO_SITE_HOSTS": "",
    "STUDIO_TAGLINE": "",
    "STUDIO_STORY": "",
    "STUDIO_HOURS": "",
    "STUDIO_HERO_IMAGE_URL": "",
    "STUDIO_GALLERY_IMAGE_URLS": "",
    "STUDIO_SOCIAL_LINKS": "",
}

NEUTRAL_SITE = {
    "mode": "product",
    "studio_hosts": [],
    "tagline": "",
    "story_paragraphs": [],
    "hours": [],
    "hero_image_url": "",
    "gallery_image_urls": [],
    "social_links": [],
}

# Values that only a configured settings object could produce.
CONFIGURED = {
    "STUDIO_NAME": "Example Ink",
    "STUDIO_OWNER_NAME": "Sam Example",
    "SHOP_EMAIL": "shop@example.test",
    "STUDIO_PHONE": "+1 555 0100",
    "STUDIO_ADDRESS": "1 Example St",
    "DEFAULT_ARTIST_SLUG": "sam",
    "BOOKING_CLOSED_WEEKDAYS": "sun,mon",
    "BOOKING_REQUEST_ONLY_WEEKDAYS": "tue",
    "BOOKING_REQUEST_ONLY_ARTIST_SLUGS": "sam",
    "WAIVER_TEMPLATE_ID": "00000000-0000-4000-8000-000000000001",
    "SAMPLE_FLASH_URLS": "https://cdn.example.test/flash-1.jpg, https://cdn.example.test/flash-2.jpg",
    "COURIER_REPLY_DOMAIN": "in.example.test",
    "PUBLIC_SITE_MODE": "studio",
    "STUDIO_SITE_HOSTS": "example.test",
    "STUDIO_TAGLINE": "Custom work by appointment.",
    "STUDIO_STORY": "We opened in a small room.\n\nNow there are three chairs.",
    "STUDIO_HOURS": "Tue-Sat 11am-7pm\nSun-Mon closed",
    "STUDIO_HERO_IMAGE_URL": "https://cdn.example.test/front.jpg",
    "STUDIO_GALLERY_IMAGE_URLS": "https://cdn.example.test/a.jpg, /media/b.jpg",
    "STUDIO_SOCIAL_LINKS": "Instagram=https://instagram.example.test/ink",
}

SECRET_VALUES = {
    "SECRET_KEY": "studio-settings-secret-key-0123456789abcdef",
    "VAPID_PRIVATE_KEY": "vapid-private-key-value",
    "STRIPE_SECRET_KEY": "sk_test_studio_settings",
    "SHOP_OWNER_EMAIL": "owner-private@example.test",
}


def _settings(**overrides) -> Settings:
    return get_settings().model_copy(update={**NEUTRAL, **overrides})


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app)


def _get(settings: Settings):
    with patch("app.api.v1.endpoints.studio.get_settings", return_value=settings):
        return _client().get(PATH)


class PublicStudioSettingsEndpointTests(unittest.TestCase):
    def test_public_and_cacheable(self):
        response = _get(_settings())
        self.assertEqual(response.status_code, 200)
        self.assertIn("public", response.headers["cache-control"])
        self.assertIn("max-age=", response.headers["cache-control"])

    def test_neutral_defaults_for_a_new_install(self):
        defaults = Settings(_env_file=None)
        for name in NEUTRAL:
            self.assertEqual(getattr(defaults, name), NEUTRAL[name], name)
        body = _get(_settings()).json()
        self.assertEqual(body["owner_name"], "")
        self.assertEqual(body["contact_email"], "")
        self.assertEqual(body["contact_phone"], "")
        self.assertEqual(body["address"], "")
        self.assertEqual(body["default_artist_slug"], "")
        self.assertEqual(body["waiver_template_id"], "")
        self.assertEqual(body["sample_flash_urls"], [])
        self.assertEqual(body["email_domain"], "")
        self.assertEqual(
            body["booking_days"],
            {"closed_weekdays": [], "request_only_weekdays": [], "request_only_artist_slugs": []},
        )
        self.assertEqual(body["timezone"], SHOP_TIMEZONE.key)
        self.assertEqual(body["site"], NEUTRAL_SITE)

    def test_values_reflect_settings(self):
        body = _get(_settings(**CONFIGURED)).json()
        self.assertEqual(
            body,
            {
                "studio_name": "Example Ink",
                "owner_name": "Sam Example",
                "contact_email": "shop@example.test",
                "contact_phone": "+1 555 0100",
                "address": "1 Example St",
                "timezone": SHOP_TIMEZONE.key,
                "booking_days": {
                    "closed_weekdays": ["sun", "mon"],
                    "request_only_weekdays": ["tue"],
                    "request_only_artist_slugs": ["sam"],
                },
                "default_artist_slug": "sam",
                "waiver_template_id": "00000000-0000-4000-8000-000000000001",
                "sample_flash_urls": [
                    "https://cdn.example.test/flash-1.jpg",
                    "https://cdn.example.test/flash-2.jpg",
                ],
                "email_domain": "in.example.test",
                "site": {
                    "mode": "studio",
                    "studio_hosts": ["example.test"],
                    "tagline": "Custom work by appointment.",
                    "story_paragraphs": ["We opened in a small room.", "Now there are three chairs."],
                    "hours": ["Tue-Sat 11am-7pm", "Sun-Mon closed"],
                    "hero_image_url": "https://cdn.example.test/front.jpg",
                    "gallery_image_urls": ["https://cdn.example.test/a.jpg", "/media/b.jpg"],
                    "social_links": [{"label": "Instagram", "url": "https://instagram.example.test/ink"}],
                },
            },
        )

    def test_no_secret_reaches_the_response(self):
        response = _get(_settings(**CONFIGURED, **SECRET_VALUES))
        for name, value in SECRET_VALUES.items():
            self.assertNotIn(value, response.text, name)


class StudioSettingValidationTests(unittest.TestCase):
    def test_weekdays_normalize_to_short_sunday_first(self):
        settings = Settings(_env_file=None, BOOKING_CLOSED_WEEKDAYS=" Monday, sun ,MON ")
        self.assertEqual(settings.BOOKING_CLOSED_WEEKDAYS, "sun,mon")
        self.assertEqual(settings.booking_closed_weekdays, ["sun", "mon"])

    def test_unknown_weekday_fails_at_startup(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, BOOKING_REQUEST_ONLY_WEEKDAYS="tues")

    def test_waiver_template_id_must_be_a_uuid(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, WAIVER_TEMPLATE_ID="waiver")


class PublicSiteModeTests(unittest.TestCase):
    """PUBLIC_SITE_MODE and the studio website values behind it."""

    def test_new_install_shows_the_product_landing_with_no_studio_content(self):
        defaults = Settings(_env_file=None)
        self.assertEqual(defaults.PUBLIC_SITE_MODE, "product")
        for name in (
            "STUDIO_SITE_HOSTS",
            "STUDIO_TAGLINE",
            "STUDIO_STORY",
            "STUDIO_HOURS",
            "STUDIO_HERO_IMAGE_URL",
            "STUDIO_GALLERY_IMAGE_URLS",
            "STUDIO_SOCIAL_LINKS",
        ):
            self.assertEqual(getattr(defaults, name), NEUTRAL[name], name)
        site = _get(defaults.model_copy(update={"STUDIO_NAME": "Ryozan Studio"})).json()["site"]
        self.assertEqual(site, NEUTRAL_SITE)

    def test_mode_is_case_insensitive_and_blank_means_product(self):
        self.assertEqual(Settings(_env_file=None, PUBLIC_SITE_MODE=" Studio ").PUBLIC_SITE_MODE, "studio")
        self.assertEqual(Settings(_env_file=None, PUBLIC_SITE_MODE="").PUBLIC_SITE_MODE, "product")

    def test_unknown_mode_fails_at_startup(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, PUBLIC_SITE_MODE="shop")

    def test_site_hosts_are_bare_lowercase_hostnames(self):
        settings = Settings(_env_file=None, STUDIO_SITE_HOSTS=" Ink.Example.test, www.ink.example.test ")
        self.assertEqual(settings.studio_site_hosts, ["ink.example.test", "www.ink.example.test"])
        for bad in ("https://ink.example.test", "ink.example.test:443", "ink example"):
            with self.assertRaises(ValidationError, msg=bad):
                Settings(_env_file=None, STUDIO_SITE_HOSTS=bad)

    def test_image_urls_must_be_https_or_a_site_path(self):
        settings = Settings(_env_file=None, STUDIO_GALLERY_IMAGE_URLS="https://cdn.example.test/a.jpg,/media/b.jpg")
        self.assertEqual(settings.studio_gallery_image_urls, ["https://cdn.example.test/a.jpg", "/media/b.jpg"])
        for bad in (
            "http://cdn.example.test/a.jpg",
            "//cdn.example.test/a.jpg",
            "javascript:alert(1)",
            "/a.jpg'),url(x",
        ):
            with self.assertRaises(ValidationError, msg=bad):
                Settings(_env_file=None, STUDIO_HERO_IMAGE_URL=bad)
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, STUDIO_HERO_IMAGE_URL="/a.jpg,/b.jpg")

    def test_social_links_need_a_label_and_an_https_url(self):
        settings = Settings(_env_file=None, STUDIO_SOCIAL_LINKS="Instagram=https://ig.example.test/x?a=b")
        self.assertEqual(settings.studio_social_links, [{"label": "Instagram", "url": "https://ig.example.test/x?a=b"}])
        for bad in ("https://ig.example.test", "Instagram=", "=https://ig.example.test", "Mail=mailto:a@example.test"):
            with self.assertRaises(ValidationError, msg=bad):
                Settings(_env_file=None, STUDIO_SOCIAL_LINKS=bad)

    def test_unvalidated_values_never_reach_the_response(self):
        # model_copy skips validators; the response properties filter again.
        site = _get(
            _settings(
                PUBLIC_SITE_MODE="shop",
                STUDIO_SITE_HOSTS="https://x.example.test",
                STUDIO_HERO_IMAGE_URL="javascript:alert(1)",
                STUDIO_GALLERY_IMAGE_URLS="//evil.example.test/a.jpg",
                STUDIO_SOCIAL_LINKS="X=javascript:alert(1)",
            )
        ).json()["site"]
        self.assertEqual(site, NEUTRAL_SITE)

    def test_story_splits_on_blank_lines_and_escaped_newlines(self):
        settings = _settings(STUDIO_STORY="First line\nstill first.\n\n\nSecond.\\n\\nThird.")
        self.assertEqual(settings.studio_story_paragraphs, ["First line still first.", "Second.", "Third."])

    def test_site_settings_carry_no_secret(self):
        response = _get(_settings(**CONFIGURED, **SECRET_VALUES))
        site = response.json()["site"]
        for name, value in SECRET_VALUES.items():
            self.assertNotIn(value, str(site), name)


class VapidSubjectTests(unittest.TestCase):
    def test_subject_falls_back_to_shop_email(self):
        self.assertEqual(_settings(SHOP_EMAIL="shop@example.test").vapid_subject, "mailto:shop@example.test")

    def test_explicit_subject_wins(self):
        settings = _settings(SHOP_EMAIL="shop@example.test", VAPID_SUBJECT="https://studio.example.test")
        self.assertEqual(settings.vapid_subject, "https://studio.example.test")

    def test_no_subject_without_either(self):
        settings = _settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
        self.assertEqual(settings.vapid_subject, "")
        self.assertFalse(settings.push_configured)

    def test_malformed_subject_fails_at_startup(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, VAPID_SUBJECT="shop@example.test")


class PushWithoutSubjectTests(unittest.IsolatedAsyncioTestCase):
    async def test_vapid_key_is_refused_when_no_subject(self):
        settings = _settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
        with patch.object(push_endpoints, "settings", settings), self.assertRaises(HTTPException) as caught:
            await push_endpoints.get_vapid_key()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("VAPID_SUBJECT", caught.exception.detail)

    async def test_send_skips_without_calling_webpush(self):
        settings = _settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv", LOCAL_DEV_MODE=False)
        subscription = type("Sub", (), {"id": 1, "endpoint": "https://push.example.test/x", "p256dh": "k", "auth": "a"})
        result = type("Result", (), {"scalars": lambda self: type("S", (), {"all": lambda s: [subscription]})()})()
        db = AsyncMock()
        db.execute.return_value = result
        with (
            patch.object(push_service, "settings", settings),
            patch.object(push_service, "webpush") as webpush,
            self.assertLogs(push_service.logger, level="ERROR") as logs,
        ):
            sent = await push_service.send_push_notification(db, user_id=None, title="t", body="b")
        self.assertEqual(sent, 0)
        webpush.assert_not_called()
        self.assertIn("VAPID_SUBJECT", "\n".join(logs.output))

    async def test_send_uses_the_resolved_subject(self):
        settings = _settings(
            VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv", LOCAL_DEV_MODE=False, SHOP_EMAIL="shop@example.test"
        )
        subscription = type("Sub", (), {"id": 1, "endpoint": "https://push.example.test/x", "p256dh": "k", "auth": "a"})
        result = type("Result", (), {"scalars": lambda self: type("S", (), {"all": lambda s: [subscription]})()})()
        db = AsyncMock()
        db.execute.return_value = result
        with patch.object(push_service, "settings", settings), patch.object(push_service, "webpush") as webpush:
            sent = await push_service.send_push_notification(db, user_id=None, title="t", body="b")
        self.assertEqual(sent, 1)
        self.assertEqual(webpush.call_args.kwargs["vapid_claims"], {"sub": "mailto:shop@example.test"})


if __name__ == "__main__":
    unittest.main()
