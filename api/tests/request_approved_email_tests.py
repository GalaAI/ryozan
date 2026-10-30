"""Request-approved collector email: renderer copy and the outbox sender.

Net-new artifact justification: the renderer is new and sits beside the locked
Foundry catalog like render_artist_booking_alert, whose tests own only the
staff alert. This module locks the collector-facing promises: no deposit link
without one, never the booking confirmation, and no em or en dashes.
"""

import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.services.email_foundry_booking import render_request_approved


def _render(**overrides):
    values = {
        "client_name": "Maria",
        "artist_name": "Rowan",
        "reference_code": "BKG-Y11SRV",
        "scheduled_date": "2026-10-21",
        "scheduled_time": "13:00",
        "duration_minutes": 120,
        "deposit_amount": 100.0,
        "deposit_url": "https://ryozan.app/quote/token123",
    }
    values.update(overrides)
    return render_request_approved(**values)


class RequestApprovedRendererTests(unittest.TestCase):
    def test_deposit_link_renders_pay_pill_with_time_and_amount(self):
        email = _render()
        self.assertEqual(email.slug, "request_approved")
        self.assertIn("Request approved", email.subject)
        self.assertIn("deposit pending", email.subject)
        self.assertIn("https://ryozan.app/quote/token123", email.html_body)
        self.assertIn("Pay $100 deposit", email.html_body)
        self.assertIn("Wed, Oct 21, 2026 at 1:00 PM", email.html_body)
        self.assertIn("Deposit pending", email.html_body)

    def test_no_link_never_renders_a_payment_button(self):
        email = _render(deposit_url=None)
        self.assertNotIn("/quote/", email.html_body)
        self.assertNotIn("Pay $100", email.html_body)
        self.assertIn("Your artist will reach out about paying the deposit", email.html_body)

    def test_zero_deposit_reads_as_approved_without_payment_language(self):
        email = _render(deposit_amount=0.0, deposit_url=None, booking_kind="consultation")
        self.assertEqual(email.subject, "Request approved with Rowan")
        self.assertIn("No deposit is needed for this consultation", email.html_body)
        self.assertNotIn("Deposit pending", email.html_body)

    def test_is_not_the_booking_confirmation_and_has_no_long_dashes(self):
        for email in (_render(), _render(deposit_url=None), _render(deposit_amount=0.0, deposit_url=None)):
            self.assertNotIn("Booking Confirmation", email.html_body)
            self.assertNotIn("—", email.subject + email.html_body)
            self.assertNotIn("–", email.subject + email.html_body)

    def test_artist_message_is_escaped(self):
        email = _render(artist_message="<script>x</script> See you soon")
        self.assertNotIn("<script>", email.html_body)
        self.assertIn("&lt;script&gt;", email.html_body)


class RequestApprovedSenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sender_threads_replies_into_the_booking_conversation(self):
        conversation_id = uuid4()
        with patch.object(bookings_endpoint, "send_email", new=AsyncMock(return_value=True)) as send:
            accepted = await bookings_endpoint._send_request_approved(
                to="maria@example.test",
                client_name="Maria",
                artist_name="Rowan",
                reference_code="BKG-Y11SRV",
                scheduled_date="2026-10-21",
                scheduled_time="13:00",
                duration_minutes=120,
                deposit_amount=100.0,
                deposit_url=None,
                conversation_id=str(conversation_id),
            )
        self.assertTrue(accepted)
        kwargs = send.await_args.kwargs
        self.assertEqual(kwargs["to"], "maria@example.test")
        self.assertEqual(kwargs["tags"], ["request_approved"])
        self.assertIn(str(conversation_id), kwargs["reply_to"])
        self.assertIn("Request approved", kwargs["subject"])


if __name__ == "__main__":
    unittest.main()
