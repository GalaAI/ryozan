"""The artist booking alert renders in Email Foundry's language.

Pins: the Foundry shell (starseed ground, tiger mark, coral pill), the contact and project wells, escaping
of every client-supplied value, reference images, the consultation-booked variant, and no em or en dashes.
"""

import os
import re
import unittest

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.services.email_foundry import STARSEED_TILE_URL, TIGER_MARK_URL  # noqa: E402
from app.services.email_foundry_booking import render_artist_booking_alert  # noqa: E402

TIGER = TIGER_MARK_URL
PILL = "background-color:#d38a68;border-radius:99px"


def _visible_text(html: str) -> str:
    html = re.sub(r"<style.*?</style>", "", html, flags=re.DOTALL)
    return re.sub(r"<[^>]+>", " ", html)


def _alert(**overrides):
    args = dict(
        client_name="Michael Thompson",
        client_email="michael@example.com",
        client_phone=None,
        artist_name="Sample Artist",
        description='Fine line Ironman tattoo, ~1.5" x 2".',
        placement="Right calf",
        size="Small (up to 3 in)",
        budget=None,
        timeline=None,
        reference_code="BKG-JX0OVT",
        accept_url="https://ryozan.app/dashboard/huddle?charm=bookings&ref=BKG-JX0OVT&action=accept",
        message_url="https://ryozan.app/dashboard/courier?convo=abc",
        decline_url="https://ryozan.app/dashboard/huddle?charm=bookings&ref=BKG-JX0OVT&action=reject",
        submitted_at="September 30, 2026 at 03:59 PM EST",
    )
    args.update(overrides)
    return render_artist_booking_alert(**args)


class ArtistBookingAlertTests(unittest.TestCase):
    def test_new_inquiry_uses_the_foundry_shell(self) -> None:
        email = _alert()
        html = email.html_body
        self.assertEqual(email.slug, "artist_booking_alert")
        self.assertEqual(email.subject, "New Booking Request from Michael Thompson")
        self.assertIn(STARSEED_TILE_URL, html)
        self.assertIn(TIGER, html)
        self.assertIn("Booking Request // New", html)
        self.assertIn("New request", html)
        # catalog card stub: oxblood well, Booky title, barcode and seal
        self.assertIn("border:1px solid #5e2222;border-radius:14px", html)
        self.assertIn('color:#a33a31;text-transform:uppercase;">New</div>', html)
        self.assertIn("&#34382;", html)
        self.assertIn("Ancient as time, modern as tomorrow", html)
        self.assertIn(PILL, html)
        for text in (
            "Sample Artist",
            "Right calf",
            "Small (up to 3 in)",
            "BKG-JX0OVT",
            "mailto:michael@example.com",
        ):
            self.assertIn(text, html)
        self.assertIn("ref=BKG-JX0OVT&amp;action=accept", html)
        self.assertIn("convo=abc", html)
        self.assertIn("ref=BKG-JX0OVT&amp;action=reject", html)

    def test_empty_fields_are_omitted(self) -> None:
        visible = _visible_text(_alert().html_body)
        self.assertNotIn("Budget", visible)
        self.assertNotIn("Timeline", visible)
        self.assertNotIn("Style", visible)
        self.assertNotIn("Phone", visible)
        self.assertNotIn("Reference images", visible)

    def test_client_values_are_escaped(self) -> None:
        html = _alert(client_name="<b>Eve</b>", description="<script>x</script>\nline two").html_body
        self.assertNotIn("<b>Eve</b>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;b&gt;Eve&lt;/b&gt;", html)
        self.assertIn("line two", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;<br>line two", html)

    def test_reference_images_render_https_only(self) -> None:
        html = _alert(
            reference_image_urls=[
                "https://assets.ryozan.app/bookings/one.png",
                "javascript:alert(1)",
                "https://assets.ryozan.app/bookings/two.png",
            ]
        ).html_body
        self.assertIn("Reference images", html)
        self.assertIn("https://assets.ryozan.app/bookings/one.png", html)
        self.assertIn("https://assets.ryozan.app/bookings/two.png", html)
        self.assertNotIn("javascript:", html)
        self.assertEqual(html.count("Enlarge</a>"), 2)
        self.assertIn("max-height:120px;object-fit:contain", html)

    def test_colours_come_from_the_design_lock(self) -> None:
        html = _alert(reference_image_urls=["https://assets.ryozan.app/bookings/one.png"]).html_body
        # state chip uses the lock's pending colour, never gold
        self.assertIn("border:1px solid #e2dac2;border-radius:99px", html)
        # gold is never label text: Enlarge and secondary actions use the unselected-chip colour
        self.assertNotIn("color:#a67d43", html)
        self.assertIn(
            'color:rgba(207,197,176,.5);text-transform:uppercase;text-decoration:none;">&#8599;',
            html,
        )
        self.assertIn("background-color:#d38a68;border-radius:99px", html)

    def test_all_submitted_reference_images_are_available(self) -> None:
        urls = [f"https://assets.ryozan.app/bookings/{index}.png" for index in range(6)]
        html = _alert(reference_image_urls=urls).html_body
        for url in urls:
            self.assertIn(url, html)
        self.assertEqual(html.count("Enlarge</a>"), 6)

    def test_consultation_booked_variant(self) -> None:
        email = _alert(confirmed=True, scheduled_date="Friday, October 2", scheduled_time="15:00")
        self.assertEqual(email.subject, "Consultation Booked with Michael Thompson")
        self.assertIn("Consultation // Booked", email.html_body)
        self.assertIn("FRIDAY, OCTOBER 2 AT 3:00 PM".lower(), email.html_body.lower())
        self.assertIn("border:1px solid #70c27b", email.html_body)
        # a booked consult is past accept/decline: only the thread link remains
        self.assertNotIn("action=accept", email.html_body)
        self.assertNotIn("action=reject", email.html_body)
        self.assertNotIn("Nothing moves until you do", email.html_body)
        self.assertIn("convo=abc", email.html_body)

    def test_long_description_is_preserved(self) -> None:
        html = _alert(description="a" * 600 + " final detail").html_body
        self.assertIn("a" * 600 + " final detail", html)

    def test_submitted_intake_details_are_visible(self) -> None:
        html = _alert(
            booking_kind="consultation",
            consultation_method="meet",
            budget="200-500",
            timeline="flexible",
            preferred_date="2026-10-20",
            preferred_time="15:00",
            client_notes="Please email me.\nAfter work.",
        ).html_body
        for value in (
            "Request type",
            "consultation",
            "Google Meet",
            "200-500",
            "flexible",
            "Requested date",
            "2026-10-20",
            "Requested time",
            "3:00 PM",
            "Client notes:",
            "Please email me.<br>After work.",
        ):
            self.assertIn(value, html)
        self.assertNotIn(">Scheduled<", html)

    def test_requested_options_do_not_claim_a_confirmed_appointment(self) -> None:
        html = _alert(
            preferred_slots=[
                {"date": "2026-10-20", "time": "11:00"},
                {"date": "2026-10-22", "time": "15:00"},
                {"date": "2026-10-24", "time": "12:00"},
            ]
        ).html_body
        for value in (
            "Requested option 1",
            "2026-10-20 at 11:00 AM",
            "Requested option 2",
            "2026-10-22 at 3:00 PM",
            "Requested option 3",
            "2026-10-24 at 12:00 PM",
        ):
            self.assertIn(value, html)
        self.assertNotIn(">Scheduled<", html)

    def test_new_intake_fields_are_escaped_and_optional(self) -> None:
        html = _alert(
            client_notes="<script>notes</script>",
            preferred_date="<b>date</b>",
            booking_kind="<b>kind</b>",
            consultation_method="<b>method</b>",
        ).html_body
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;script&gt;notes&lt;/script&gt;", html)
        empty = _visible_text(_alert().html_body)
        for label in (
            "Requested date",
            "Requested time",
            "Requested option",
            "Client notes:",
            "Request type",
            "Consultation method",
        ):
            self.assertNotIn(label, empty)

    def test_copy_has_no_em_or_en_dashes(self) -> None:
        visible = _visible_text(_alert().html_body) + _visible_text(_alert(confirmed=True).html_body)
        self.assertNotIn("—", visible)
        self.assertNotIn("–", visible)
        self.assertNotIn("&mdash;", visible)


if __name__ == "__main__":
    unittest.main()
