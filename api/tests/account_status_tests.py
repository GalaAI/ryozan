"""Local lifecycle and authorization contracts using synthetic identities only.

Existing mailbox fixtures own isolated HTTP/database and provider boundaries.
Extend them here to prove owner evidence, rather than inspecting live credentials.
"""

import asyncio
import secrets
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from mailbox_ownership_tests import FixtureSession, fixture_app, user

from app.api.v1.endpoints import auth, users
from app.core.rate_limit import limiter
from app.core.security import create_access_token, create_password_reset_token, create_user_access_token, hash_password
from app.models.user import User
from app.schemas.user import UserResponse
from app.services.account_status import password_status, record_password_setup


class AccountSession(FixtureSession):
    def __init__(self, *rows):
        super().__init__(*rows)
        self.statements = []
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        return await super().execute(stmt)

    async def commit(self):
        self.commits += 1

    async def scalar(self, stmt):
        self.statements.append(stmt)
        return None


class AccountStatusTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()
        self.secret = secrets.token_urlsafe(24)
        self.artist = user(email="artist@example.com", role="artist")
        self.artist.password_hash = hash_password(self.secret)
        self.owner = user(email="owner@example.com", role="founder")
        self.db = AccountSession(self.artist, self.owner)
        app = fixture_app(self.db)
        app.include_router(users.router)
        self.client = TestClient(app)
        self.stack = ExitStack()
        for sender in ("send_password_changed_email", "send_welcome_email", "send_password_reset_email"):
            self.stack.enter_context(patch.object(auth, sender, new=AsyncMock()))

    def tearDown(self):
        self.stack.close()
        limiter.reset()

    def headers(self, actor=None):
        return {"Authorization": f"Bearer {create_user_access_token(actor or self.artist)}"}

    def status(self):
        response = self.client.get("/users/artist-account-status", headers=self.headers(self.owner))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()[0]

    def test_history_is_unknown_without_inspecting_existing_hash_or_updated_at(self):
        self.artist.updated_at = datetime.now(UTC)
        self.assertEqual(self.status()["password_status"], "historical_unknown")
        self.assertIsNone(self.status()["password_changed_at"])
        self.artist.must_reset_password = True
        self.assertEqual(self.status()["password_status"], "setup_required")
        self.assertIsNone(self.status()["password_changed_at"])

    def test_only_founder_can_access_even_their_own_status(self):
        for role in ("admin", "artist", "assistant", "collector"):
            actor = user(email=f"{role}@example.com", role=role, verified=True)
            self.db.add(actor)
            response = self.client.get("/users/artist-account-status", headers=self.headers(actor))
            self.assertEqual(response.status_code, 403, role)
        self.assertEqual(self.client.get("/users/artist-account-status").status_code, 401)

    def test_status_does_not_select_or_return_secrets_or_leak_into_profiles(self):
        result = self.status()
        self.assertEqual(
            set(result),
            {
                "id",
                "password_status",
                "password_changed_at",
                "password_change_method",
                "last_login_at",
                "last_login_method",
            },
        )
        selected = str(self.db.statements[-1])
        for field in ("password_hash", "google_id", "refresh_token"):
            self.assertNotIn(field, selected)
        profile = UserResponse.model_validate(self.artist).model_dump()
        for field in set(result) - {"id"}:
            self.assertNotIn(field, profile)

    def test_password_login_records_method_but_never_claims_password_was_user_set(self):
        for _ in range(2):
            response = self.client.post("/auth/login", json={"email": self.artist.email, "password": self.secret})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.status()["last_login_method"], "password")
            self.assertIsNotNone(self.artist.last_login_at)
            self.assertEqual(password_status(self.artist), "historical_unknown")

    def test_failed_login_and_failed_change_leave_evidence_untouched(self):
        response = self.client.post(
            "/auth/login", json={"email": self.artist.email, "password": secrets.token_urlsafe(24)}
        )
        self.assertEqual(response.status_code, 401)
        response = self.client.patch(
            "/auth/change-password",
            headers=self.headers(),
            json={"current_password": secrets.token_urlsafe(24), "new_password": secrets.token_urlsafe(24)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.artist.password_changed_at)
        self.assertIsNone(self.artist.last_login_at)

    def test_successful_change_sets_evidence_and_revokes_old_session(self):
        previous = self.headers()
        response = self.client.patch(
            "/auth/change-password",
            headers=previous,
            json={"current_password": self.secret, "new_password": secrets.token_urlsafe(24)},
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.status()["password_status"], "user_set")
        self.assertEqual(self.status()["password_change_method"], "change")
        self.assertIsNotNone(self.artist.password_changed_at)
        self.assertEqual(self.client.get("/auth/me", headers=previous).status_code, 401)
        self.assertGreater(self.db.commits, 0)

    def test_reset_commits_new_evidence_replay_cannot_change_it_and_it_is_not_a_login(self):
        self.artist.must_reset_password = True
        token = create_password_reset_token(self.artist.id, self.artist.password_hash, email=self.artist.email)
        response = self.client.post(
            "/auth/reset-password", json={"token": token, "new_password": secrets.token_urlsafe(24)}
        )
        self.assertEqual(response.status_code, 204)
        observed = self.artist.password_changed_at
        self.assertEqual(self.status()["password_status"], "user_set")
        self.assertEqual(self.status()["password_change_method"], "reset")
        self.assertIsNone(self.artist.last_login_at)
        self.assertFalse(self.artist.must_reset_password)
        self.assertGreater(self.db.commits, 0)
        response = self.client.post(
            "/auth/reset-password", json={"token": token, "new_password": secrets.token_urlsafe(24)}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.artist.password_changed_at, observed)

    def test_invite_acceptance_records_password_choice_and_successful_invite_login(self):
        invite = create_access_token(
            {"invite": True, "email": self.artist.email, "name": "Test Artist", "role": "artist"}
        )
        response = self.client.post(
            "/auth/register-invite", json={"invite_token": invite, "password": secrets.token_urlsafe(24)}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.status()["password_status"], "user_set")
        self.assertEqual(self.status()["password_change_method"], "invite")
        self.assertEqual(self.status()["last_login_method"], "invite")

    def test_google_login_preserves_password_choice_and_records_google_method(self):
        record_password_setup(self.artist)
        self.artist.must_reset_password = True
        previous = self.artist.password_changed_at
        old_hash = self.artist.password_hash
        with patch.object(
            auth,
            "_verify_google_credential",
            return_value={"sub": "fixture-subject", "email": self.artist.email, "email_verified": True},
        ):
            response = self.client.post("/auth/google", json={"credential": "fixture-credential"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.status()["password_status"], "setup_required")
        self.assertEqual(self.status()["last_login_method"], "google")
        self.assertEqual(self.artist.password_changed_at, previous)
        self.assertTrue(old_hash == self.artist.password_hash, "artist credential was changed")

    def test_link_start_and_cancellation_are_not_login_or_password_creation(self):
        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", "fixture-client"),
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_SECRET", "fixture-secret"),
        ):
            response = self.client.get("/auth/google/link", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        state = parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]
        response = self.client.get(
            "/auth/google/callback-signin", params={"state": state, "error": "access_denied"}, follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        self.assertIsNone(self.artist.last_login_at)
        self.assertIsNone(self.artist.password_changed_at)

    def test_successful_google_link_is_not_login_or_password_creation(self):
        old_hash = self.artist.password_hash
        with patch.object(
            auth,
            "_verify_google_credential",
            return_value={
                "sub": "fixture-link-subject",
                "email": "different-google@example.com",
                "email_verified": True,
            },
        ):
            response = asyncio.run(auth._finish_google_link(self.db, self.artist.id, "fixture-credential"))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.artist.google_id, "fixture-link-subject")
        self.assertTrue(old_hash == self.artist.password_hash, "artist credential was changed")
        self.assertIsNone(self.artist.last_login_at)
        self.assertIsNone(self.artist.password_changed_at)
        self.assertEqual(password_status(self.artist), "historical_unknown")

    def test_new_invited_artist_has_observed_user_set_password(self):
        email = "new-artist@example.com"
        invite = create_access_token({"invite": True, "email": email, "name": "New Fixture Artist", "role": "artist"})
        response = self.client.post(
            "/auth/register-invite", json={"invite_token": invite, "password": secrets.token_urlsafe(24)}
        )
        self.assertEqual(response.status_code, 201)
        created = next(row for row in self.db.rows if isinstance(row, User) and row.email == email)
        self.assertEqual(password_status(created), "user_set")
        self.assertEqual(created.password_change_method, "invite")
        self.assertEqual(created.last_login_method, "invite")
        self.assertEqual(created.slug, "new-fixture-artist")

    def test_new_orm_accounts_default_to_setup_not_user_choice(self):
        for field in ("password_state", "password_changed_at", "password_change_method"):
            self.assertIsNotNone(User.__table__.c[field].default)
            self.assertIsNone(User.__table__.c[field].server_default)


class AccountStatusFrontendTests(unittest.TestCase):
    def test_owner_column_uses_existing_ui_and_does_not_treat_fetch_failure_as_history(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        page = (root / "app/src/pages/dashboard/AdminUsers.tsx").read_text(encoding="utf-8")
        self.assertIn("state.user?.role === 'founder'", page)
        self.assertIn("if (isOwner)", page)
        self.assertIn("usersApi.artistAccountStatus()", page)
        self.assertIn("{isOwner && <th>Artist account</th>}", page)
        self.assertIn("import { Button, ReceiptBlock } from '@ryozan/ui'", page)
        for label in (
            "Setup/change required",
            "User-set password",
            "Historical unknown",
            "Not recorded",
            "Last successful login",
        ):
            self.assertIn(label, page)
        self.assertIn("Status unavailable. Refresh to retry.", page)
        self.assertIn("Refresh status", page)
        self.assertNotIn("password_hash", page)
        self.assertIn("flexWrap: 'wrap'", page)
        self.assertIn("paddingBottom: 'calc(var(--space-20) + env(safe-area-inset-bottom, 0))'", page)
        layout = (root / "app/src/components/layout/DashboardLayout.tsx").read_text(encoding="utf-8")
        self.assertIn("location.pathname === ROUTES.ADMIN_USERS && user?.role === 'founder'", layout)
        self.assertIn("!portalConfigRequested && !ownerAccountsRequested", layout)
        self.assertIn(
            "ownerAccountsRequested && isMobilePortalViewport ? { height: 'auto', minHeight: 0, flex: '1 1 auto' }",
            layout,
        )
