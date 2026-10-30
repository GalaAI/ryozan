"""Real persistence/migration evidence on the isolated local fixture only.

The mailbox PostgreSQL gauntlet establishes this opt-in pattern. This separate
port/database prevents cross-feature test state and never accepts a remote URL.
"""

import asyncio
import importlib.util
import io
import os
import secrets
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from app.api.v1.endpoints import auth, users
from app.core.database import Base, async_session, engine
from app.core.rate_limit import limiter
from app.core.security import create_password_reset_token, create_user_access_token, hash_password
from app.models.user import BTTCOUser
from app.services.account_status import set_user_password

ENABLED = os.environ.get("BTTCO_ACCOUNT_STATUS_PG_TESTS") == "1"
FIXTURE_DB = os.environ.get("DATABASE_URL") == "postgresql+asyncpg://fixture@127.0.0.1:55434/account_status_test"


@unittest.skipUnless(ENABLED and FIXTURE_DB, "requires isolated account-status PostgreSQL fixture on port 55434")
class AccountStatusPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Import the canonical router/model registry without starting its lifespan.
        import main  # noqa: F401

        limiter.reset()
        self.stack = ExitStack()
        for sender in ("send_password_changed_email", "send_welcome_email", "send_password_reset_email"):
            self.stack.enter_context(patch.object(auth, sender, new=AsyncMock()))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.secret = secrets.token_urlsafe(24)
        async with async_session() as db:
            self.artist = BTTCOUser(
                email=f"{secrets.token_hex(8)}@example.com",
                name="Fixture Artist",
                role="artist",
                password_hash=hash_password(self.secret),
                must_reset_password=True,
            )
            self.owner = BTTCOUser(
                email=f"{secrets.token_hex(8)}@example.com",
                name="Fixture Owner",
                role="founder",
                password_hash=hash_password(secrets.token_urlsafe(24)),
            )
            db.add_all([self.artist, self.owner])
            await db.commit()
            self.artist_id = self.artist.id
            self.owner_headers = {"Authorization": f"Bearer {create_user_access_token(self.owner)}"}
        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(auth.router)
        app.include_router(users.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture.local")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.stack.close()
        await engine.dispose()
        limiter.reset()

    async def test_reset_and_successful_login_are_persisted_in_separate_sessions(self):
        async with async_session() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            self.assertEqual(artist.password_state, "setup_required")
            token = create_password_reset_token(artist.id, artist.password_hash, email=artist.email)
            email = artist.email
        chosen = secrets.token_urlsafe(24)
        response = await self.client.post("/auth/reset-password", json={"token": token, "new_password": chosen})
        self.assertEqual(response.status_code, 204)
        async with async_session() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            self.assertEqual(artist.password_state, "user_set")
            self.assertEqual(artist.password_change_method, "reset")
            self.assertIsNotNone(artist.password_changed_at)
            self.assertIsNone(artist.last_login_at)
        response = await self.client.post("/auth/login", json={"email": email, "password": chosen})
        self.assertEqual(response.status_code, 200)
        async with async_session() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            self.assertEqual(artist.last_login_method, "password")
            self.assertIsNotNone(artist.last_login_at)
        response = await self.client.get("/users/artist-account-status", headers=self.owner_headers)
        self.assertEqual(response.status_code, 200)
        evidence = next(row for row in response.json() if row["id"] == str(self.artist_id))
        self.assertEqual(evidence["password_status"], "user_set")
        self.assertEqual(evidence["last_login_method"], "password")

    async def test_revision_roundtrip_preserves_credential_and_leaves_historical_evidence_null(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        path = Path(__file__).resolve().parents[1] / "alembic/versions/20261001_account_status.py"
        spec = importlib.util.spec_from_file_location("account_status_revision", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        def roundtrip(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                migration.upgrade()

        async with engine.begin() as conn:
            await conn.run_sync(roundtrip)
        async with async_session() as db:
            result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == self.artist_id))
            artist = result.scalar_one()
            for field in (
                "password_state",
                "password_changed_at",
                "password_change_method",
                "last_login_at",
                "last_login_method",
            ):
                self.assertIsNone(getattr(artist, field))
            email = artist.email
        response = await self.client.post("/auth/login", json={"email": email, "password": self.secret})
        self.assertEqual(response.status_code, 200)

    async def test_startup_additive_reconciliation_preserves_unknowns_and_is_repeatable(self):
        from sqlalchemy import text

        from app.core.schema_sync import reconcile_additive_columns

        fields = (
            "password_state",
            "password_changed_at",
            "password_change_method",
            "last_login_at",
            "last_login_method",
        )
        async with engine.begin() as conn:
            for field in fields:
                await conn.execute(text(f"ALTER TABLE bttco_users DROP COLUMN {field}"))
            first = await reconcile_additive_columns(conn, Base.metadata)
            self.assertEqual(len(first.statements), 5)
            self.assertEqual(first.refused, [])
            second = await reconcile_additive_columns(conn, Base.metadata)
            self.assertEqual(second.statements, [])
        async with async_session() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            for field in fields:
                self.assertIsNone(getattr(artist, field))

    async def test_full_lifespan_preserves_history_and_records_only_fixture_staff_resets(self):
        import main

        self.assertEqual(main.settings.DATABASE_URL, os.environ["DATABASE_URL"])
        self.assertTrue(main.settings.LOCAL_DEV_MODE)
        # Exercise the real lifespan and migrations. Only unrelated outbound
        # worker inputs are mocked; all account writes and reads use this DB.
        for worker in (
            "process_owner_email_outbox_once",
            "process_booking_email_outbox_once",
            "process_byof_deposit_reviews_once",
            "process_booky_courier_outbox_once",
            "process_courier_notification_outbox_once",
            "process_gallery_deletion_outbox_once",
            "queue_recent_learning_captures_once",
            "process_pack_bridge_outbox_once",
            "process_apex_training_manifest_once",
        ):
            self.stack.enter_context(patch.object(main, worker, new=AsyncMock()))
        self.stack.enter_context(patch.object(main.settings, "PORTFOLIO_DERIVATIVES_ON_STARTUP", False))
        async with async_session() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            set_user_password(artist, self.secret, "change")
            owner = await db.get(BTTCOUser, self.owner.id)
            owner.must_reset_password = False
            owner.password_state = None
            owner.password_changed_at = None
            owner.password_change_method = None
            collector = BTTCOUser(
                email=f"{secrets.token_hex(8)}@example.com", name="Fixture Collector", role="collector"
            )
            set_user_password(collector, secrets.token_urlsafe(24), "change")
            db.add(collector)
            await db.commit()
            original_owner_hash = owner.password_hash
            artist_change_time = artist.password_changed_at
            collector_id = collector.id
            collector_hash = collector.password_hash
            collector_change_time = collector.password_changed_at

        async def boot(reset):
            output = io.StringIO()
            with patch.dict(os.environ, {"RESET_ALL_PASSWORDS": reset}), redirect_stdout(output):
                async with main.lifespan(main.app):
                    await asyncio.sleep(0)  # Let the real worker loop reach its mocked inputs.
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=main.app), base_url="http://fixture.local"
                    ) as client:
                        response = await client.get("/api/v1/users/artist-account-status", headers=self.owner_headers)
            self.assertIn("Startup migrations: OK", output.getvalue())
            self.assertNotIn("Startup migration error", output.getvalue())
            return response

        response = await boot("0")
        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.json() if row["id"] == str(self.artist_id))
        self.assertEqual(row["password_status"], "user_set")
        async with async_session() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            owner = await db.get(BTTCOUser, self.owner.id)
            self.assertEqual(artist.password_changed_at, artist_change_time)
            self.assertEqual(owner.password_hash, original_owner_hash)
            self.assertIsNone(owner.password_state)
            self.assertIsNone(owner.password_changed_at)

        # This flag is set only within this exact loopback-fixture test.
        self.assertEqual((await boot("1")).status_code, 401)  # Previous fixture bearer is revoked.
        async with async_session() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            owner = await db.get(BTTCOUser, self.owner.id)
            for actor in (artist, owner):
                self.assertTrue(actor.must_reset_password)
                self.assertEqual(actor.password_state, "setup_required")
                self.assertEqual(actor.password_change_method, "setup")
                self.assertIsNotNone(actor.password_changed_at)
                self.assertIsNone(actor.last_login_at)
            self.assertNotEqual(owner.password_hash, original_owner_hash)
            reset_time = artist.password_changed_at
            self.owner_headers = {"Authorization": f"Bearer {create_user_access_token(owner)}"}
            collector = await db.get(BTTCOUser, collector_id)
            self.assertEqual(collector.password_hash, collector_hash)
            self.assertEqual(collector.password_changed_at, collector_change_time)
            self.assertEqual(collector.password_state, "user_set")
        response = await boot("0")
        self.assertEqual(response.status_code, 200)
        async with async_session() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            self.assertEqual(artist.password_changed_at, reset_time)
            self.assertIsNone(artist.last_login_at)
