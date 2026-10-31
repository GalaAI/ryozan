"""Local HTTP regressions; all users, persistence and delivery are test fixtures."""

import unittest
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.security import create_password_reset_token, create_user_access_token, get_current_user, hash_password
from app.models.booking import Booking
from app.models.quote import Quote
from app.models.user import User
from app.services import account_setup


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FixtureSession:
    def __init__(self, *rows):
        self.rows = list(rows)

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        params = stmt.compile().params
        rows = [row for row in self.rows if isinstance(row, entity)]
        for key, value in params.items():
            column = "email" if key.startswith("lower_") else key.rsplit("_", 1)[0]
            rows = [row for row in rows if getattr(row, column) == value]
        return _Rows(rows)

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        for row in self.rows:
            if not row.id:
                row.id = uuid.uuid4()

    async def commit(self):
        return None


def user(*, email="mailbox@example.com", role="collector", verified=False, google_id=None):
    return User(
        id=uuid.uuid4(),
        email=email,
        name="Test Identity",
        role=role,
        password_hash=hash_password("preclaimant-password"),
        google_id=google_id,
        must_reset_password=False,
        bttcoin_balance=0,
        quest_points=0,
        marketing_opt_in=False,
        membership_tier="freemium",
        membership_status="active",
        ai_monthly_message_cap=40,
        early_access_galax3=False,
        connected_apps_unlocked=False,
        gala_personality=75,
        created_at=datetime.now(UTC),
        email_verified_at=datetime.now(UTC) if verified else None,
    )


def fixture_app(db):
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(auth.router)

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db

    @app.get("/private-bookings")
    async def private_bookings(current=Depends(get_current_user)):
        return {"account": str(current.id)}

    return app


class MailboxOwnershipTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()
        self.stack = ExitStack()
        self.mail = self.stack.enter_context(
            patch.object(account_setup, "send_password_reset_email", new=AsyncMock(return_value=True))
        )
        self.stack.enter_context(patch.object(auth, "send_password_changed_email", new=AsyncMock()))
        self.stack.enter_context(patch.object(auth, "send_welcome_email", new=AsyncMock()))
        self.stack.enter_context(patch.object(auth, "send_password_reset_email", new=self.mail))

    def tearDown(self):
        self.stack.close()
        limiter.reset()

    def _emailed_token(self):
        call = self.mail.await_args.kwargs
        self.assertEqual(call["to_email"], "mailbox@example.com")
        return parse_qs(urlparse(call["reset_url"]).query)["token"][0]

    def _post_register(self, client):
        return client.post(
            "/auth/register",
            json={
                "email": "Mailbox@Example.com",
                "name": "Caller",
                "password": "attacker-chosen-password",
            },
        )

    def test_registration_requires_mailbox_redemption_before_booking_access(self):
        db = FixtureSession()
        client = TestClient(fixture_app(db))
        response = self._post_register(client)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["verification_required"])
        self.assertNotIn("access_token", response.json())
        account = db.rows[0]
        self.assertIsNone(account.email_verified_at)
        self.assertEqual(
            client.post(
                "/auth/login", json={"email": account.email, "password": "attacker-chosen-password"}
            ).status_code,
            401,
        )
        blocked = client.get(
            "/private-bookings", headers={"Authorization": f"Bearer {create_user_access_token(account)}"}
        )
        self.assertEqual(blocked.status_code, 403)
        token = self._emailed_token()
        self.assertNotIn(token, response.text)
        self.assertEqual(
            client.post(
                "/auth/reset-password", json={"token": token, "new_password": "mailbox-owner-password"}
            ).status_code,
            204,
        )
        self.assertIsNotNone(account.email_verified_at)
        login = client.post("/auth/login", json={"email": account.email, "password": "mailbox-owner-password"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(
            client.get(
                "/private-bookings", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
            ).status_code,
            200,
        )
        self.assertEqual(
            client.post("/auth/reset-password", json={"token": token, "new_password": "second-password"}).status_code,
            400,
        )

    def test_legacy_public_booking_reset_token_is_rejected_even_for_verified_user(self):
        for verified in (False, True):
            with self.subTest(verified=verified):
                account = user(verified=verified)
                client = TestClient(fixture_app(FixtureSession(account)))
                legacy = create_password_reset_token(account.id, account.password_hash)
                self.assertFalse(client.get("/auth/reset-password/validate", params={"token": legacy}).json()["usable"])
                self.assertEqual(
                    client.post(
                        "/auth/reset-password", json={"token": legacy, "new_password": "attacker-password"}
                    ).status_code,
                    400,
                )

    def test_matching_password_and_old_session_do_not_bypass_mailbox_proof(self):
        account = user()
        client = TestClient(fixture_app(FixtureSession(account)))
        self.assertEqual(
            client.post("/auth/login", json={"email": account.email, "password": "preclaimant-password"}).status_code,
            403,
        )
        self.assertEqual(
            client.get(
                "/private-bookings", headers={"Authorization": f"Bearer {create_user_access_token(account)}"}
            ).status_code,
            403,
        )

    def test_resubmission_preserves_existing_account_and_verified_account_is_conflict(self):
        account = user()
        db = FixtureSession(account)
        client = TestClient(fixture_app(db))
        before = account.password_hash
        self.assertEqual(self._post_register(client).status_code, 201)
        self.assertEqual(self._post_register(client).status_code, 201)
        self.assertEqual(len(db.rows), 1)
        self.assertEqual(account.password_hash, before)
        account.email_verified_at = datetime.now(UTC)
        self.assertEqual(self._post_register(client).status_code, 409)

    def test_quote_token_alone_cannot_open_a_session(self):
        booking = Booking(id=uuid.uuid4(), guest_email="mailbox@example.com", guest_name="Guest")
        quote = Quote(id=uuid.uuid4(), booking_id=booking.id, token="public-quote")
        db = FixtureSession(booking, quote)
        response = TestClient(fixture_app(db)).post(
            "/auth/register-from-booking", json={"quote_token": quote.token, "password": "attacker-password"}
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("access_token", response.json())
        self.assertEqual(self.mail.await_count, 1)

    def test_recovery_stays_generic_and_only_mailbox_receives_token(self):
        client = TestClient(fixture_app(FixtureSession(user())))
        known = client.post("/auth/forgot-password", json={"email": "mailbox@example.com"})
        unknown = client.post("/auth/forgot-password", json={"email": "unknown@example.com"})
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(self.mail.await_count, 1)
        self.assertNotIn(self._emailed_token(), known.text)

    def test_address_change_invalidates_proof_and_outstanding_email_token(self):
        account = user(verified=True)
        token = create_password_reset_token(account.id, account.password_hash, email=account.email)
        account.email = "different@example.com"
        self.assertIsNone(account.email_verified_at)
        client = TestClient(fixture_app(FixtureSession(account)))
        self.assertEqual(
            client.post("/auth/reset-password", json={"token": token, "new_password": "new-password"}).status_code, 400
        )

    def test_verified_google_owner_retires_preclaimant_password_and_session(self):
        account = user(google_id="preclaimant-sub")
        old_token = create_user_access_token(account)
        client = TestClient(fixture_app(FixtureSession(account)))
        with patch.object(
            auth,
            "_verify_google_credential",
            return_value={"sub": "owner-sub", "email": account.email, "email_verified": True},
        ):
            response = client.post("/auth/google", json={"credential": "fixture-google-proof"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(account.email_verified_at)
        self.assertEqual(account.google_id, "owner-sub")
        self.assertEqual(
            client.post("/auth/login", json={"email": account.email, "password": "preclaimant-password"}).status_code,
            401,
        )
        self.assertEqual(
            client.get("/private-bookings", headers={"Authorization": f"Bearer {old_token}"}).status_code, 401
        )
        self.assertEqual(
            client.get(
                "/private-bookings", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
            ).status_code,
            200,
        )

    def test_mailbox_redemption_retires_preclaimant_google_login(self):
        account = user(google_id="preclaimant-sub")
        original_id = account.id
        client = TestClient(fixture_app(FixtureSession(account)))
        token = create_password_reset_token(account.id, account.password_hash, email=account.email)
        response = client.post("/auth/reset-password", json={"token": token, "new_password": "owner-password"})
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(account.google_id)
        with patch.object(
            auth,
            "_verify_google_credential",
            return_value={
                "sub": "preclaimant-sub",
                "email": "preclaimant@example.com",
                "email_verified": True,
            },
        ):
            attacker = client.post("/auth/google", json={"credential": "fixture-google-proof"})
        self.assertEqual(attacker.status_code, 200)
        scope = client.get("/private-bookings", headers={"Authorization": f"Bearer {attacker.json()['access_token']}"})
        self.assertNotEqual(scope.json()["account"], str(original_id))

    def test_linked_different_google_email_cannot_verify_collector_mailbox(self):
        account = user(google_id="different-sub")
        client = TestClient(fixture_app(FixtureSession(account)))
        with patch.object(
            auth,
            "_verify_google_credential",
            return_value={"sub": "different-sub", "email": "different@example.com", "email_verified": True},
        ):
            response = client.post("/auth/google", json={"credential": "fixture-google-proof"})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(account.email_verified_at)

    def test_admin_provisioned_staff_password_login_remains_available(self):
        account = user(role="artist")
        client = TestClient(fixture_app(FixtureSession(account)))
        response = client.post("/auth/login", json={"email": account.email, "password": "preclaimant-password"})
        self.assertEqual(response.status_code, 200)

    def test_historical_collector_link_state_cannot_reattach_a_preclaimant_after_redemption(self):
        import asyncio

        account = user(verified=True)
        db = FixtureSession(account)
        client = TestClient(fixture_app(db))
        denied = client.get(
            "/auth/google/link", headers={"Authorization": f"Bearer {create_user_access_token(account)}"}
        )
        self.assertEqual(denied.status_code, 403)
        # The callback's finisher also gates the target, covering old signed
        # collector states issued by PR479 before this patch.
        with patch.object(
            auth,
            "_verify_google_credential",
            return_value={
                "sub": "preclaimant-sub",
                "email": "preclaimant@example.com",
                "email_verified": True,
            },
        ):
            response = asyncio.run(auth._finish_google_link(db, str(account.id), "fixture-google-proof"))
        self.assertIn("google_signin=error", response.headers["location"])
        self.assertIsNone(account.google_id)


if __name__ == "__main__":
    unittest.main()
