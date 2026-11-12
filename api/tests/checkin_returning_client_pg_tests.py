"""Returning-client check-in, end to end over HTTP on an isolated PostgreSQL fixture.

Distinct from checkin_verify_tests.py, which pins the pure code and gate functions. This one drives the real
routes in order (lookup, verify, form, complete) against real rows, because the autofill is only correct when
every hop agrees on who the client is. It follows the opt-in fixture pattern of account_status_pg_tests.py on
its own port and database and never accepts a remote URL.
"""

import json
import os
import re
import secrets
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from app.api.v1.endpoints import documents
from app.core import legacy_env
from app.core.database import Base, async_session, engine
from app.core.rate_limit import limiter
from app.models.document import DocumentInstance, DocumentTemplate

ENABLED = legacy_env.getenv("RYOZAN_CHECKIN_PG_TESTS") == "1"
FIXTURE_DB = os.environ.get("DATABASE_URL") == "postgresql+asyncpg://fixture@127.0.0.1:55435/checkin_test"

HEALTH = {"pregnant": "No", "blood_thinners": "No", "allergies": "Latex"}


@unittest.skipUnless(ENABLED and FIXTURE_DB, "requires isolated check-in PostgreSQL fixture on port 55435")
class ReturningClientCheckinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import main  # noqa: F401  (registers every model on Base without starting the app)

        limiter.reset()
        self.sent_sms: list[tuple[str, str]] = []
        self.sent_email: list[tuple[str, str]] = []

        async def fake_sms(*, db, to, body, **_):
            self.sent_sms.append((to, body))
            return SimpleNamespace(status="queued")

        async def fake_code_email(to, code):
            self.sent_email.append((to, code))
            return True

        self.stack = ExitStack()
        self.stack.enter_context(patch.object(documents, "send_sms", new=fake_sms))
        self.stack.enter_context(patch.object(documents, "_send_checkin_code_email", new=fake_code_email))
        self.stack.enter_context(patch.object(documents, "send_email", new=AsyncMock(return_value=True)))

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        tag = secrets.token_hex(4)
        self.email = f"returning-{tag}@example.com"
        self.phone = "864" + f"{secrets.randbelow(10**7):07d}"
        self.prior_data = {
            "full_name": "Riley Returning",
            "email": self.email,
            "phone": f"({self.phone[:3]}) {self.phone[3:6]}-{self.phone[6:]}",
            "date_of_birth": "1990-04-12",
            "street_address": "12 Pendleton St, Greenville, SC",
            "emergency_contact": "Sam Returning, 864-555-0101",
            "photo_id_url": f"https://assets.example.test/waivers/{tag}/id.jpg",
            "signature_url": f"https://assets.example.test/waivers/{tag}/signature.png",
            **HEALTH,
        }
        async with async_session() as db:
            template = (
                await db.execute(select(DocumentTemplate).where(DocumentTemplate.name == "Tattoo Consent Waiver"))
            ).scalar_one_or_none()
            if not template:
                template = DocumentTemplate(
                    name="Tattoo Consent Waiver", category="waiver", fields_json="{}", requires_signature=True
                )
                db.add(template)
                await db.flush()
            prior = DocumentInstance(
                template_id=template.id,
                category="waiver",
                title=template.name,
                status="signed",
                guest_name="Riley Returning",
                guest_email=self.email,
                signed_at=datetime.now(UTC) - timedelta(days=90),
                data_json=json.dumps(self.prior_data),
            )
            db.add(prior)
            await db.commit()
            self.prior_id = str(prior.id)

        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(documents.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture.local")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.stack.close()
        await engine.dispose()
        limiter.reset()

    async def _verified(self, contact: str) -> dict:
        lookup = await self.client.post("/documents/checkin/lookup", json={"contact": contact})
        self.assertEqual(lookup.status_code, 200, lookup.text)
        body = lookup.json()
        self.assertTrue(body["found"], body)
        code = self.sent_sms[-1][1] if body["channel"] == "phone" else self.sent_email[-1][1]
        code = re.search(r"\d{6}", code).group(0)
        verify = await self.client.post(
            "/documents/checkin/verify", json={"challenge": body["challenge"], "code": code}
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        return verify.json()

    async def _assert_autofill_and_reuse(self, verified: dict) -> None:
        self.assertEqual(verified["visits"], 1)
        self.assertEqual(verified["first_name"], "Riley")
        on_file = verified["on_file"]
        self.assertEqual(on_file["photo_id_url"], self.prior_data["photo_id_url"])
        self.assertEqual(on_file["signature_url"], self.prior_data["signature_url"])
        self.assertEqual(on_file["health"], HEALTH)

        form = await self.client.get(f"/documents/public/{verified['token']}")
        self.assertEqual(form.status_code, 200, form.text)
        filled = form.json()["initial_values"]
        for field in ("full_name", "email", "date_of_birth", "street_address", "emergency_contact"):
            self.assertEqual(filled[field], self.prior_data[field], field)
        self.assertEqual(documents.checkin_verify.phone_digits(filled["phone"]), self.phone)

        done = await self.client.post(
            f"/documents/public/{verified['token']}/complete",
            json={
                "data": {
                    **filled,
                    **HEALTH,
                    # A check-in waiver files every yes/no health answer (_require_waiver_intake_answers).
                    "blood_disorder": "No",
                    "skin_condition": "No",
                    "tattoo_description": "Traditional swallow",
                    "tattoo_placement": "Left forearm",
                    "quoted_price": "250",
                    "intoxicated": "No",
                    "health_and_eligibility": True,
                    "risks_and_consent": True,
                    "accept_terms": True,
                    "reuse_photo_id": True,
                    "reuse_signature": True,
                },
                "signature_data": None,
            },
        )
        self.assertEqual(done.status_code, 200, done.text)
        async with async_session() as db:
            new = (
                await db.execute(select(DocumentInstance).where(DocumentInstance.send_token == verified["token"]))
            ).scalar_one()
            stored = json.loads(new.data_json)
        self.assertEqual(stored["photo_id_url"], self.prior_data["photo_id_url"])
        self.assertEqual(stored["photo_id_reused_from"], self.prior_id)
        self.assertEqual(stored["signature_url"], self.prior_data["signature_url"])
        self.assertEqual(stored["signature_adopted_from"], self.prior_id)
        self.assertIs(stored["checkin_verified"], True)

    async def test_email_checkin_autofills_and_reuses_id_and_signature(self):
        verified = await self._verified(self.email)
        self.assertEqual(self.sent_email[-1][0], self.email)
        await self._assert_autofill_and_reuse(verified)

    async def test_phone_only_walk_in_is_matched_by_the_number_on_their_waiver(self):
        # No account and no booking: the number exists only inside the prior waiver's answers.
        verified = await self._verified(self.phone)
        self.assertEqual(self.sent_sms[-1][0], self.phone)
        await self._assert_autofill_and_reuse(verified)

    async def test_unknown_phone_reveals_nothing_and_sends_nothing(self):
        lookup = await self.client.post("/documents/checkin/lookup", json={"contact": "8640000000"})
        self.assertEqual(lookup.json(), {"found": False})
        self.assertEqual(self.sent_sms, [])
        self.assertEqual(self.sent_email, [])

    async def test_failed_text_falls_back_to_the_email_on_the_waiver(self):
        async def failed_sms(*, db, to, body, **_):
            return SimpleNamespace(status="failed")

        with patch.object(documents, "send_sms", new=failed_sms):
            lookup = await self.client.post("/documents/checkin/lookup", json={"contact": self.phone})
        body = lookup.json()
        self.assertTrue(body["found"])
        self.assertEqual(body["channel"], "email")
        self.assertEqual(self.sent_email[-1][0], self.email)
        self.assertNotIn(self.email, body["sent_to"])

    async def test_a_stranger_email_never_inherits_a_waiver_through_a_typed_phone(self):
        # Phone matching applies only when the phone itself is the verified contact.
        matches = None
        async with async_session() as db:
            contact = documents.checkin_verify.normalize_contact("stranger@example.com")
            matches = await documents._checkin_matches(db, contact)
        self.assertIsNone(matches["prior"])
        self.assertEqual(matches["visits"], 0)


if __name__ == "__main__":
    unittest.main()
