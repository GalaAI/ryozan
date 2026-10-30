"""Waiver-flow mail renders in Email Foundry's language and the send paths fire it.

Pins: the Foundry shell (starseed ground, tiger mark or Doc Bot, coral pill), escaping, no em or en dashes
in client copy, placement tips carried over, and that the aftercare, check-in code, waiver link, and waiver
reminder sends use these renderers with attributable tags.
"""

import asyncio
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import documents, waiver  # noqa: E402
from app.services.email_foundry_waiver import (  # noqa: E402
    render_aftercare_instructions,
    render_checkin_code,
    render_waiver_link,
)

STARSEED = "https://assets.ryozan.app/email/starseed-tile.png"
TIGER = "https://assets.ryozan.app/email/tiger-mark.png"
PILL = "background-color:#d38a68;border-radius:99px"


def _visible_text(html: str) -> str:
    html = re.sub(r"<style.*?</style>", "", html, flags=re.DOTALL)
    return re.sub(r"<[^>]+>", " ", html)


def _aftercare(**overrides):
    args = dict(
        client_name="Sam Collector",
        artist_name="Justin Walker",
        tattoo_description="Snake wrap",
        tattoo_placement="Right forearm",
        reference_code="WVR-ABC123",
        portal_url="https://ryozan.app/portal?email=sam%40example.com",
    )
    args.update(overrides)
    return render_aftercare_instructions(**args)


class RendererTests(unittest.TestCase):
    def test_aftercare_is_doc_bot_on_the_foundry_shell(self) -> None:
        email = _aftercare()
        self.assertEqual(email.slug, "aftercare_instructions")
        self.assertIn(STARSEED, email.html_body)
        self.assertIn("Doc Bot", email.html_body)
        self.assertIn(PILL, email.html_body)
        self.assertIn("Hey Sam,", email.html_body)
        self.assertIn("Snake wrap", email.html_body)
        self.assertIn("WVR-ABC123", email.html_body)
        self.assertIn("Save my details for next time", email.html_body)
        # forearm tips from the shared placement table
        self.assertIn("Avoid resting your arm on dirty surfaces", email.html_body)

    def test_member_cta_and_escaping(self) -> None:
        email = _aftercare(member=True, client_name="<b>Eve</b>", tattoo_description='"x" & <script>')
        self.assertIn("Open my portal", email.html_body)
        self.assertNotIn("<script>", email.html_body)
        self.assertNotIn("<b>Eve</b>", email.html_body)
        self.assertIn("&lt;b&gt;Eve&lt;/b&gt;", email.html_body)

    def test_no_em_or_en_dashes_in_client_copy(self) -> None:
        for placement in ("knee", "left forearm", "ribs", "upper back", "thigh", "hip", "ankle", "mystery"):
            text = _visible_text(_aftercare(tattoo_placement=placement).html_body)
            self.assertNotIn("—", text, placement)
            self.assertNotIn("–", text, placement)
        for email in (
            render_checkin_code(code="123456"),
            render_waiver_link(client_name="Bo", artist_name="Justin", document_title="Tattoo waiver", form_url="x"),
            render_waiver_link(
                client_name="Bo", artist_name="", document_title="Tattoo waiver", form_url="x", hours_remaining=20
            ),
        ):
            text = _visible_text(email.html_body) + email.subject
            self.assertNotIn("—", text, email.slug)
            self.assertNotIn("–", text, email.slug)

    def test_checkin_code(self) -> None:
        email = render_checkin_code(code="042917")
        self.assertEqual(email.slug, "checkin_code")
        self.assertIn(TIGER, email.html_body)
        self.assertIn("042917", email.html_body)
        self.assertIn("042917", email.subject)
        self.assertIn("Expires in 10 minutes", email.html_body)

    def test_waiver_link_and_reminder(self) -> None:
        link = render_waiver_link(
            client_name="Bo Booker",
            artist_name="Justin Walker",
            document_title="Tattoo waiver",
            form_url="https://ryozan.app/checkin?t=abc&x=1",
        )
        self.assertEqual(link.slug, "waiver_link")
        self.assertIn('href="https://ryozan.app/checkin?t=abc&amp;x=1"', link.html_body)
        self.assertIn("Justin Walker sent your tattoo waiver", link.html_body)
        reminder = render_waiver_link(
            client_name="Bo", artist_name="", document_title="Tattoo waiver", form_url="u", hours_remaining=20
        )
        self.assertEqual(reminder.slug, "waiver_reminder")
        self.assertIn("about 20 more hours", reminder.html_body)
        self.assertTrue(reminder.subject.startswith("Reminder:"))


class _NoSession:
    async def __aenter__(self):
        raise RuntimeError("no db in unit tests")

    async def __aexit__(self, *exc):
        return False


class SendPathTests(unittest.TestCase):
    def test_aftercare_send_uses_foundry_and_reports_acceptance(self) -> None:
        send = AsyncMock(return_value=True)
        with patch.object(waiver, "send_email", send), patch.object(waiver, "async_session", lambda: _NoSession()):
            accepted = asyncio.run(
                waiver._send_aftercare_email(
                    to="sam@example.com",
                    client_name="Sam Collector",
                    artist_name="Justin Walker",
                    tattoo_description="Snake wrap",
                    tattoo_placement="Right forearm",
                    reference_code="WVR-ABC123",
                )
            )
        self.assertTrue(accepted)
        kwargs = send.await_args.kwargs
        self.assertEqual(kwargs["to"], "sam@example.com")
        self.assertEqual(kwargs["tags"][0], "aftercare_instructions")
        self.assertIn("Doc Bot", kwargs["subject"])
        self.assertIn("from=aftercare", kwargs["html_body"])

    def test_aftercare_send_never_raises(self) -> None:
        send = AsyncMock(side_effect=RuntimeError("provider down"))
        with patch.object(waiver, "send_email", send), patch.object(waiver, "async_session", lambda: _NoSession()):
            accepted = asyncio.run(
                waiver._send_aftercare_email(
                    to="sam@example.com",
                    client_name="Sam",
                    artist_name="",
                    tattoo_description="",
                    tattoo_placement="",
                    reference_code="",
                )
            )
        self.assertFalse(accepted)

    def test_background_tasks_are_held_until_done(self) -> None:
        async def run() -> int:
            gate = asyncio.Event()

            async def job() -> None:
                await gate.wait()

            task = waiver.spawn_background(job())
            held = task in waiver._BACKGROUND_TASKS
            gate.set()
            await task
            await asyncio.sleep(0)
            return int(held) + int(task not in waiver._BACKGROUND_TASKS)

        self.assertEqual(asyncio.run(run()), 2)

    def test_checkin_code_email(self) -> None:
        send = AsyncMock(return_value=True)
        with patch.object(documents, "send_email", send):
            self.assertTrue(asyncio.run(documents._send_checkin_code_email("sam@example.com", "123456")))
        self.assertEqual(send.await_args.kwargs["tags"], ["checkin_code"])

    def test_waiver_reminder_goes_to_checkin(self) -> None:
        send = AsyncMock(return_value=True)
        settings = SimpleNamespace(FRONTEND_URL="https://ryozan.app/")
        inst = SimpleNamespace(
            category="waiver", recipient_name="Bo", recipient_email="bo@example.com", send_token="tok", title="x"
        )
        with patch.object(documents, "send_email", send):
            asyncio.run(documents._send_document_reminder(settings, inst, 20))
        kwargs = send.await_args.kwargs
        self.assertEqual(kwargs["tags"], ["waiver_reminder"])
        self.assertIn("https://ryozan.app/checkin?t=tok", kwargs["html_body"])


if __name__ == "__main__":
    unittest.main()
