"""
Self-hosting defaults: nothing a deployment sends or serves points at the GalaAI domains.

Pins: phone-only contacts get an undeliverable placeholder address, the SMS HELP reply names
the studio's own contact and site, email brand images come from the deployment, and the
public legal pages carry placeholders that the server fills from Settings.
"""

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from email_validator import validate_email  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.services import sms  # noqa: E402

PUBLIC_DIR = Path(__file__).resolve().parents[2] / "app" / "public"


class PlaceholderContactEmailTests(unittest.TestCase):
    def test_placeholder_is_valid_and_reserved(self):
        email = sms.placeholder_contact_email("sms-15555550123")
        self.assertEqual(email, "sms-15555550123@contacts.example.com")
        validate_email(email, check_deliverability=False)


class SmsHelpReplyTests(unittest.TestCase):
    def _settings(self, shop_email: str):
        return SimpleNamespace(
            SHOP_EMAIL=shop_email, public_site_url="https://studio.example", STUDIO_NAME="Ryozan Studio"
        )

    def test_names_the_studio_contact_and_site(self):
        with patch.object(sms, "get_settings", return_value=self._settings("hello@studio.example")):
            reply = sms.sms_help_reply()
        self.assertIn("email hello@studio.example or visit studio.example", reply)
        self.assertNotIn("ryozan.app", reply)
        self.assertLessEqual(len(reply), 160)

    def test_without_a_contact_points_at_the_site(self):
        with patch.object(sms, "get_settings", return_value=self._settings("")):
            reply = sms.sms_help_reply()
        self.assertIn("For help, visit studio.example.", reply)


class EmailAssetUrlTests(unittest.TestCase):
    def test_defaults_to_the_deployment_email_folder(self):
        settings = Settings(FRONTEND_URL="https://studio.example/", EMAIL_ASSET_BASE_URL="")
        self.assertEqual(settings.email_asset_url("tiger-mark.png"), "https://studio.example/email/tiger-mark.png")

    def test_explicit_base_wins(self):
        settings = Settings(FRONTEND_URL="https://studio.example", EMAIL_ASSET_BASE_URL="https://cdn.example/brand/")
        self.assertEqual(settings.email_asset_url("tiger-mark.png"), "https://cdn.example/brand/tiger-mark.png")


class PublicPageTests(unittest.TestCase):
    def test_pages_carry_placeholders_not_a_fixed_identity(self):
        for name in ("privacy.html", "sms-terms.html", "sitemap.xml", "robots.txt"):
            text = (PUBLIC_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("ryozan.app", text, name)
            self.assertIn("{{site_url}}", text, name)
        for name in ("privacy.html", "sms-terms.html"):
            text = (PUBLIC_DIR / name).read_text(encoding="utf-8")
            self.assertIn("{{studio_name}}", text, name)
            self.assertIn("{{contact_link}}", text, name)

    def test_email_brand_images_ship_with_the_app(self):
        for name in ("tiger-mark.png", "starseed-tile.png"):
            self.assertTrue((PUBLIC_DIR / "email" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
