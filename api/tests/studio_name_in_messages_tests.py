"""
Studio name in client messages: email, SMS and seeded templates name the configured STUDIO_NAME.

Pins: a deployment that sets STUDIO_NAME sees its own name, HTML-escaped where the text is
HTML, and never the default "Ryozan Studio". The cached Settings instance is patched because
every sampled renderer reads get_settings() when it is called.
"""

import os
import unittest
from unittest.mock import patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.core.config import get_settings  # noqa: E402
from app.services import email, sms, sms_template_seeder  # noqa: E402
from app.services.email_foundry_booking import render_artist_booking_alert  # noqa: E402
from app.services.email_foundry_waiver import render_checkin_code  # noqa: E402

STUDIO = "Moth & Rose Tattoo"
STUDIO_HTML = "Moth &amp; Rose Tattoo"
STUDIO_HTML_UPPER = "MOTH &amp; ROSE TATTOO"
DEFAULT = "Ryozan Studio"


class StudioNameTestCase(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(get_settings(), "STUDIO_NAME", STUDIO)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_names_studio(self, text: str, expected: str) -> None:
        self.assertIn(expected, text)
        self.assertNotIn(DEFAULT, text)
        self.assertNotIn(DEFAULT.upper(), text)


class FoundryEmailTests(StudioNameTestCase):
    def test_artist_booking_alert_header(self):
        rendered = render_artist_booking_alert(
            client_name="Meredith",
            client_email="meredith@example.com",
            client_phone=None,
            artist_name="Alex Rivera",
            description="Black and grey rose",
            placement="Forearm",
            size=None,
            budget=None,
            timeline=None,
            reference_code="BKG-ABC123",
            accept_url="https://studio.example/accept",
            message_url="https://studio.example/message",
            decline_url="https://studio.example/decline",
        )
        self.assert_names_studio(rendered.html_body, STUDIO_HTML_UPPER)
        self.assertIn(f'alt="{STUDIO_HTML}"', rendered.html_body)
        self.assertNotIn(STUDIO, rendered.html_body)

    def test_checkin_code_header(self):
        rendered = render_checkin_code(code="482913")
        self.assert_names_studio(rendered.html_body, STUDIO_HTML)
        self.assertNotIn(STUDIO, rendered.html_body)


class SmsTests(StudioNameTestCase):
    def test_help_reply(self):
        self.assert_names_studio(sms.sms_help_reply(), f"{STUDIO}: For help,")

    def test_seed_templates(self):
        templates = sms_template_seeder.get_seed_templates()
        self.assertTrue(templates)
        for template in templates:
            with self.subTest(slug=template["slug"]):
                self.assert_names_studio(template["body"], f"{STUDIO}:")
        reminder = next(t for t in templates if t["slug"] == "appointment_reminder_1h")
        self.assertIn("{{time}}", reminder["body"])


class LegacyEmailTests(StudioNameTestCase):
    def test_welcome_html(self):
        for role in ("collector", "artist"):
            with self.subTest(role=role):
                html = email.build_welcome_html("Meredith", role)
                self.assert_names_studio(html, STUDIO_HTML)
                self.assertIn(STUDIO_HTML_UPPER, html)
                self.assertNotIn(STUDIO, html)


if __name__ == "__main__":
    unittest.main()
