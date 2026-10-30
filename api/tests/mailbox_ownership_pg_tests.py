"""Opt-in HTTP + real PostgreSQL gauntlet on the disposable fixture port only."""

import os
import unittest
import uuid
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI
from sqlalchemy import select, text

from app.api.v1.endpoints import auth, bookings
from app.core.database import async_session, engine
from app.core.rate_limit import limiter
from app.core.security import create_password_reset_token, create_user_access_token, hash_password
from app.models.booking import Booking
from app.models.user import BTTCOUser
from app.services import account_setup

ENABLED = os.environ.get("BTTCO_MAILBOX_PG_TESTS") == "1"
FIXTURE_DB = "@127.0.0.1:55433/mailbox_test" in os.environ.get("DATABASE_URL", "")


@unittest.skipUnless(ENABLED and FIXTURE_DB, "requires disposable mailbox PostgreSQL fixture on port 55433")
class MailboxPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        limiter.reset()
        self.stack = ExitStack()
        self.mail = self.stack.enter_context(
            patch.object(account_setup, "send_password_reset_email", new=AsyncMock(return_value=True))
        )
        self.stack.enter_context(patch.object(auth, "send_password_changed_email", new=AsyncMock()))
        self.stack.enter_context(patch.object(bookings, "should_notify", new=AsyncMock(return_value=False)))
        self.stack.enter_context(
            patch.object(bookings, "_send_booking_confirmation", new=AsyncMock(return_value="sent"))
        )
        self.stack.enter_context(patch.object(bookings, "_send_artist_booking_alert", new=AsyncMock(return_value=True)))
        stamp = uuid.uuid4().hex[:12]
        self.email = f"collector-{stamp}@example.com"
        self.slug = f"fixture-artist-{stamp}"
        async with async_session() as db:
            db.add(
                BTTCOUser(
                    email=f"artist-{stamp}@example.com",
                    password_hash=hash_password("fixture-artist-password"),
                    role="artist",
                    name="Fixture Artist",
                    slug=self.slug,
                    booking_mode="api",
                    booky_mode="manual",
                )
            )
            await db.commit()
        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(auth.router)
        app.include_router(bookings.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture.local")

    async def asyncTearDown(self):
        await self.client.aclose()
        await engine.dispose()
        self.stack.close()
        limiter.reset()

    async def _submit(self):
        # Manual preferred-slot inquiries avoid Calendar, Stripe and slot holds.
        with patch.object(bookings, "process_booking_email_outbox_once", new=AsyncMock()):
            response = await self.client.post(
                "/bookings/submit",
                json={
                    "artist_slug": self.slug,
                    "first_name": "Fixture",
                    "last_name": "Collector",
                    "email": self.email,
                    "description": "Fixture black and grey shoulder piece with florals.",
                    "preferred_slots": [{"date": (date.today() + timedelta(days=10)).isoformat(), "time": "11:00"}],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["account_setup_token"])
        return response.json()

    async def _account(self):
        async with async_session() as db:
            return (await db.execute(select(BTTCOUser).where(BTTCOUser.email == self.email))).scalar_one()

    async def test_guest_submission_mailbox_outbox_redemption_and_repeated_booking(self):
        first = await self._submit()
        account = await self._account()
        self.assertIsNone(account.email_verified_at)
        self.assertGreaterEqual(await bookings.process_booking_email_outbox_once(), 3)
        self.assertEqual(self.mail.await_count, 1)
        kwargs = self.mail.await_args.kwargs
        self.assertEqual(kwargs["to_email"], self.email)
        token = parse_qs(urlparse(kwargs["reset_url"]).query)["token"][0]
        before = await self.client.get(
            "/bookings/", headers={"Authorization": f"Bearer {create_user_access_token(account)}"}
        )
        self.assertEqual(before.status_code, 403)
        reset = await self.client.post(
            "/auth/reset-password", json={"token": token, "new_password": "mailbox-owner-password"}
        )
        self.assertEqual(reset.status_code, 204, reset.text)
        login = await self.client.post("/auth/login", json={"email": self.email, "password": "mailbox-owner-password"})
        self.assertEqual(login.status_code, 200, login.text)
        rows = await self.client.get("/bookings/", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
        self.assertEqual(rows.status_code, 200, rows.text)
        self.assertIn(first["reference_code"], rows.text)
        await self._submit()
        after = await self._account()
        self.assertEqual(account.id, after.id)
        self.assertIsNotNone(after.email_verified_at)
        async with async_session() as db:
            owned = (await db.execute(select(Booking).where(Booking.client_id == account.id))).scalars().all()
        self.assertEqual(len(owned), 2)
        replay = await self.client.post(
            "/auth/reset-password", json={"token": token, "new_password": "replay-password"}
        )
        self.assertEqual(replay.status_code, 400)

    async def test_preregistered_email_cannot_read_a_later_guest_booking_or_redeem_legacy_token(self):
        register = await self.client.post(
            "/auth/register", json={"email": self.email, "name": "Untrusted Caller", "password": "attacker-password"}
        )
        self.assertEqual(register.status_code, 201, register.text)
        self.assertNotIn("access_token", register.json())
        await self._submit()
        account = await self._account()
        login = await self.client.post("/auth/login", json={"email": self.email, "password": "attacker-password"})
        self.assertEqual(login.status_code, 401)
        legacy = create_password_reset_token(account.id, account.password_hash)
        redemption = await self.client.post(
            "/auth/reset-password", json={"token": legacy, "new_password": "attacker-password"}
        )
        self.assertEqual(redemption.status_code, 400)
        private = await self.client.get(
            "/bookings/", headers={"Authorization": f"Bearer {create_user_access_token(account)}"}
        )
        self.assertEqual(private.status_code, 403)
        async with async_session() as db:
            persisted = (await db.execute(select(Booking).where(Booking.client_id == account.id))).scalars().all()
        self.assertEqual(len(persisted), 1)

    async def test_new_verification_column_is_nullable_and_has_no_unsafe_backfill(self):
        async with async_session() as db:
            column = (
                await db.execute(
                    text(
                        "SELECT is_nullable, column_default FROM information_schema.columns WHERE table_name='bttco_users' AND column_name='email_verified_at'"
                    )
                )
            ).one()
        self.assertEqual(tuple(column), ("YES", None))


if __name__ == "__main__":
    unittest.main()
