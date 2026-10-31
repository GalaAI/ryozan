"""Local-only consultation and deposit booking gauntlet.

This is a separate PostgreSQL integration test because the existing scheduler
suite deliberately replaces the database and provider boundaries with fakes.
The gauntlet exercises the public HTTP routes, real ORM persistence, a real
Stripe test Checkout Session, the signed webhook route, and MailHog delivery.
Google Calendar is captured at its network boundary so no external calendar is
mutated while the exact event payload remains assertable.
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
import unittest
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.api.v1.endpoints import calendar as calendar_endpoint
from app.api.v1.endpoints import stripe_webhooks
from app.constants.status import BookingStatus
from app.core import legacy_env
from app.core.database import async_session
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.session_type import ArtistSessionType
from app.models.user import User
from main import app

RUN_SANDBOX = legacy_env.getenv("RYOZAN_E2E_SANDBOX") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
LOCAL_DATABASE = "@127.0.0.1:5433/" in DATABASE_URL or "@localhost:5433/" in DATABASE_URL
TEST_STRIPE = STRIPE_SECRET_KEY.startswith("sk_test_")


def _stripe_signature(payload: bytes, secret: str, timestamp: int) -> str:
    signed_payload = f"{timestamp}.".encode() + payload
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


async def _mailhog_messages() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get("http://127.0.0.1:8025/api/v2/messages")
        response.raise_for_status()
        return response.json().get("items", [])


def _message_subject(message: dict) -> str:
    value = message.get("Content", {}).get("Headers", {}).get("Subject", [])
    return value[0] if isinstance(value, list) and value else str(value or "")


def _message_recipients(message: dict) -> set[str]:
    recipients = set()
    for path in (
        message.get("To", []),
        message.get("Content", {}).get("Headers", {}).get("To", []),
    ):
        for value in path if isinstance(path, list) else [path]:
            if isinstance(value, dict):
                mailbox = value.get("Mailbox")
                domain = value.get("Domain")
                if mailbox and domain:
                    recipients.add(f"{mailbox}@{domain}".lower())
            elif value:
                recipients.add(str(value).lower())
    return recipients


@unittest.skipUnless(RUN_SANDBOX, "set RYOZAN_E2E_SANDBOX=1 to run the local booking gauntlet")
@unittest.skipUnless(LOCAL_DATABASE, "booking gauntlet requires the isolated PostgreSQL database on port 5433")
@unittest.skipUnless(TEST_STRIPE, "booking gauntlet requires a Stripe sk_test key")
class BookingEndToEndSandboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete("http://127.0.0.1:8025/api/v1/messages")
            response.raise_for_status()

        stamp = uuid.uuid4().hex[:12]
        self.artist_slug = f"e2e-artist-{stamp}"
        self.artist_email = f"artist-{stamp}@example.test"
        self.consultation_email = f"consultation-{stamp}@example.test"
        self.deposit_email = f"deposit-{stamp}@example.test"
        self.webhook_secret = os.environ["STRIPE_WEBHOOK_SECRET"]
        self.artist = User(
            id=uuid.uuid4(),
            email=self.artist_email,
            password_hash="disabled",
            name="E2E Sandbox Artist",
            role="artist",
            slug=self.artist_slug,
            booking_mode="api",
            availability_source="google_calendar",
            booking_slot_interval_minutes=30,
            booking_buffer_minutes=0,
            booking_min_notice_hours=1,
            booking_max_advance_days=60,
            google_refresh_token="sandbox-refresh-token",
            google_calendar_id="sandbox-calendar@example.test",
            stripe_account_id=None,
            stripe_onboarding_complete=False,
            commission_rate=70,
        )
        self.session_type = ArtistSessionType(
            artist_id=self.artist.id,
            label="E2E Deposit Session",
            size="medium",
            placements=["forearm"],
            duration_minutes=120,
            buffer_minutes=0,
            deposit_amount=100,
            price_min=400,
            price_max=800,
            is_active=True,
        )
        async with async_session() as db:
            db.add(self.artist)
            await db.flush()
            db.add(self.session_type)
            await db.commit()

    async def _first_slot(self, client: httpx.AsyncClient, *, consultation: bool) -> dict:
        start = date.today() + timedelta(days=2)
        end = start + timedelta(days=30)
        params = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 1,
            "include_blocked": "true",
        }
        if consultation:
            params["consultation"] = "true"
        else:
            params["session_type_id"] = str(self.session_type.id)
        response = await client.get(f"/api/v1/calendar/slots/{self.artist_slug}", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["calendar_connected"])
        self.assertTrue(data["slots"], data)
        return data["slots"][0]

    async def _hold_slot(
        self,
        client: httpx.AsyncClient,
        *,
        slot: dict,
        email: str,
        consultation: bool,
    ) -> dict:
        payload = {
            "artist_slug": self.artist_slug,
            "date": slot["date"],
            "time": slot["time"],
            "inquiry_id": f"e2e-{uuid.uuid4().hex}",
            "guest_email": email,
            "booking_kind": "consultation" if consultation else "tattoo_session",
            "consultation": consultation,
        }
        if not consultation:
            payload["session_type_id"] = str(self.session_type.id)
        response = await client.post("/api/v1/calendar/slot-holds", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_consultation_and_deposit_booking_lanes(self):
        consultation_calendar: list[dict] = []
        deposit_calendar: list[dict] = []

        async def capture_consultation(data, **_kwargs):
            consultation_calendar.append(data)
            return {
                "event_id": f"consultation-{uuid.uuid4().hex}",
                "html_link": "https://calendar.example.test/consultation",
                "conference_url": "https://meet.google.com/e2e-sandbox",
            }

        async def capture_deposit(data, **_kwargs):
            deposit_calendar.append(data)
            return {
                "event_id": f"deposit-{uuid.uuid4().hex}",
                "html_link": "https://calendar.example.test/deposit",
                "conference_url": "",
            }

        transport = httpx.ASGITransport(app=app)
        with (
            patch.object(calendar_endpoint, "get_availability", new=AsyncMock(return_value=[])),
            patch.object(bookings_endpoint, "create_booking_event", new=capture_consultation),
            patch.object(stripe_webhooks, "create_booking_event", new=capture_deposit),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://e2e.local", timeout=45) as client:
                consultation_slot = await self._first_slot(client, consultation=True)
                consultation_hold = await self._hold_slot(
                    client,
                    slot=consultation_slot,
                    email=self.consultation_email,
                    consultation=True,
                )
                consultation_payload = {
                    "inquiry_id": f"consultation-{uuid.uuid4().hex}",
                    "artist_slug": self.artist_slug,
                    "first_name": "Consultation",
                    "last_name": "Sandbox",
                    "email": self.consultation_email,
                    "description": "E2E Google Meet consultation for a black and grey sleeve concept.",
                    "booking_kind": "consultation",
                    "consultation_method": "meet",
                    "preferred_date": consultation_slot["date"],
                    "preferred_time": consultation_slot["time"],
                    "hold_token": consultation_hold["hold_token"],
                }
                consultation_response = await client.post("/api/v1/bookings/submit", json=consultation_payload)
                self.assertEqual(consultation_response.status_code, 200, consultation_response.text)
                consultation = consultation_response.json()
                self.assertEqual(consultation["booking_status"], "confirmed")
                self.assertFalse(consultation["checkout_required"])
                self.assertIsNone(consultation["checkout_url"])
                self.assertIsNone(consultation["deposit_amount"])
                self.assertEqual(consultation["conference_url"], "https://meet.google.com/e2e-sandbox")
                self.assertEqual(len(consultation_calendar), 1)
                self.assertEqual(consultation_calendar[0]["client_email"], self.consultation_email)
                self.assertEqual(consultation_calendar[0]["booking_kind"], "consultation")
                self.assertEqual(consultation_calendar[0]["consultation_method"], "meet")

                deposit_slot = await self._first_slot(client, consultation=False)
                deposit_hold = await self._hold_slot(
                    client,
                    slot=deposit_slot,
                    email=self.deposit_email,
                    consultation=False,
                )
                deposit_payload = {
                    "inquiry_id": f"deposit-{uuid.uuid4().hex}",
                    "artist_slug": self.artist_slug,
                    "first_name": "Deposit",
                    "last_name": "Sandbox",
                    "email": self.deposit_email,
                    "description": "E2E deposit booking for a medium black and grey forearm tattoo.",
                    "booking_kind": "tattoo_session",
                    "session_type_id": str(self.session_type.id),
                    "placement": "forearm",
                    "size_estimate": "medium",
                    "preferred_date": deposit_slot["date"],
                    "preferred_time": deposit_slot["time"],
                    "hold_token": deposit_hold["hold_token"],
                    "save_payment_method": True,
                }
                deposit_response = await client.post("/api/v1/bookings/submit", json=deposit_payload)
                self.assertEqual(deposit_response.status_code, 200, deposit_response.text)
                deposit = deposit_response.json()
                self.assertEqual(deposit["booking_status"], "pending")
                self.assertTrue(deposit["checkout_required"])
                self.assertEqual(deposit["deposit_amount"], 100)
                self.assertRegex(deposit["checkout_url"], r"^https://checkout\.stripe\.com/")

                booking_id = uuid.UUID(deposit["booking_id"])
                async with async_session() as db:
                    pending = await db.get(Booking, booking_id)
                    self.assertEqual(pending.status, BookingStatus.PENDING)
                    self.assertFalse(pending.deposit_paid)
                    checkout_session_id = pending.stripe_checkout_session_id
                    self.assertTrue(checkout_session_id.startswith("cs_test_"))

                event_id = f"evt_e2e_{uuid.uuid4().hex}"
                payment_intent_id = f"pi_e2e_{uuid.uuid4().hex}"
                event = {
                    "id": event_id,
                    "object": "event",
                    "type": "checkout.session.completed",
                    "data": {
                        "object": {
                            "id": checkout_session_id,
                            "object": "checkout.session",
                            "payment_status": "paid",
                            "amount_total": 10000,
                            "currency": "usd",
                            "payment_intent": payment_intent_id,
                            "customer_details": {"email": self.deposit_email},
                            "metadata": {
                                "checkout_type": "public_booking_deposit",
                                "booking_id": str(booking_id),
                                "reference_code": deposit["reference_code"],
                                "slot_hold_token": deposit_hold["hold_token"],
                                "artist_id": str(self.artist.id),
                                "artist_slug": self.artist_slug,
                            },
                        }
                    },
                }
                payload = json.dumps(event, separators=(",", ":")).encode()
                timestamp = int(time.time())
                signature = _stripe_signature(payload, self.webhook_secret, timestamp)
                headers = {"Stripe-Signature": signature, "Content-Type": "application/json"}
                first_webhook = await client.post("/api/v1/stripe/webhook", content=payload, headers=headers)
                self.assertEqual(first_webhook.status_code, 200, first_webhook.text)
                replay_webhook = await client.post("/api/v1/stripe/webhook", content=payload, headers=headers)
                self.assertEqual(replay_webhook.status_code, 200, replay_webhook.text)

                await asyncio.sleep(1)

        async with async_session() as db:
            confirmed = await db.get(Booking, booking_id)
            self.assertEqual(confirmed.status, BookingStatus.CONFIRMED)
            self.assertTrue(confirmed.deposit_paid)
            self.assertEqual(float(confirmed.deposit_amount), 100)
            self.assertEqual(confirmed.stripe_checkout_session_id, checkout_session_id)
            self.assertEqual(confirmed.stripe_payment_intent_id, payment_intent_id)
            event_count = await db.scalar(
                select(func.count(BookingLifecycleEvent.id)).where(
                    BookingLifecycleEvent.booking_id == booking_id,
                    BookingLifecycleEvent.provider_event_id == event_id,
                )
            )
            self.assertEqual(event_count, 1)

        self.assertEqual(len(deposit_calendar), 1)
        self.assertEqual(deposit_calendar[0]["client_email"], self.deposit_email)

        messages = await _mailhog_messages()
        subjects_by_recipient: dict[str, set[str]] = {}
        for message in messages:
            subject = _message_subject(message)
            for recipient in _message_recipients(message):
                subjects_by_recipient.setdefault(recipient, set()).add(subject)
        self.assertIn(
            "Consultation Booked - Ryozan Studio",
            subjects_by_recipient.get(self.consultation_email, set()),
        )
        self.assertIn(
            "You're Booked! - Ryozan Studio",
            subjects_by_recipient.get(self.deposit_email, set()),
        )


if __name__ == "__main__":
    unittest.main()
