"""PR479 HTTP fixture gauntlet and separate diagnoses, with no real Google accounts."""

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import Depends
from fastapi.testclient import TestClient
from jose import jwt
from mailbox_ownership_tests import FixtureSession, fixture_app, user

from app.api.v1.endpoints import auth
from app.core.rate_limit import limiter
from app.core.security import create_password_reset_token, create_user_access_token, require_role
from app.models.booking import Booking


class _TokenResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"id_token": "fixture-google-token"}


class _GoogleExchange:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return _TokenResponse()


class ArtistLoginLocalTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()
        self.artist = user(email="portal-artist@example.com", role="artist")
        self.collector = user(email="google-artist@example.com", verified=True, google_id="artist-google-sub")
        self.booking = Booking(id=uuid.uuid4(), client_id=self.collector.id, guest_email=self.collector.email)
        self.db = FixtureSession(self.artist, self.collector, self.booking)
        app = fixture_app(self.db)

        @app.get("/artist-only")
        async def artist_only(current=Depends(require_role("artist"))):
            return {"id": str(current.id), "role": current.role}

        self.client = TestClient(app)
        self.google = {"sub": "artist-google-sub", "email": self.collector.email, "email_verified": True}
        self.headers = {"Authorization": f"Bearer {create_user_access_token(self.artist)}"}

    def tearDown(self):
        limiter.reset()

    def _start(self):
        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", "fixture-client"),
            patch.object(auth._settings, "GOOGLE_CLIENT_SECRET", "fixture-secret"),
        ):
            response = self.client.get("/auth/google/link", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]

    def _finish(self, state, **params):
        with (
            patch.object(auth.httpx, "AsyncClient", return_value=_GoogleExchange()),
            patch.object(auth, "_verify_google_credential", return_value=self.google),
        ):
            return self.client.get(
                "/auth/google/callback-signin",
                params={"state": state, "code": "fixture-code", **params},
                follow_redirects=False,
            )

    def _google_login(self):
        with patch.object(auth, "_verify_google_credential", return_value=self.google):
            response = self.client.post("/auth/google", json={"credential": "fixture-google-proof"})
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_authenticated_link_followed_by_repeated_google_login_has_artist_scope(self):
        before = self._google_login()
        self.assertEqual(self.client.get("/artist-only", headers=before).status_code, 403)
        for _ in range(2):
            callback = self._finish(self._start())
            self.assertIn("google_signin=linked", callback.headers["location"])
            self.assertIn("workflow=connections", callback.headers["location"])
            headers = self._google_login()
            me = self.client.get("/auth/me", headers=headers)
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["id"], str(self.artist.id))
            self.assertEqual(me.json()["role"], "artist")
            scoped = self.client.get("/artist-only", headers=headers)
            self.assertEqual(scoped.status_code, 200)
            self.assertEqual(scoped.json()["id"], str(self.artist.id))
        self.assertTrue(self.client.get("/auth/google/link/status", headers=self.headers).json()["linked"])

    def test_unauthenticated_link_start_is_denied(self):
        self.assertEqual(self.client.get("/auth/google/link").status_code, 401)

    def test_redirect_signin_after_link_uses_the_same_artist_identity(self):
        self._finish(self._start())
        now = datetime.now(UTC)
        state = jwt.encode(
            {"purpose": "google_signin", "iat": now, "exp": now + timedelta(minutes=5)},
            auth._settings.SECRET_KEY,
            algorithm=auth._settings.ALGORITHM,
        )
        response = self._finish(state)
        token = parse_qs(urlparse(response.headers["location"]).fragment)["gsi_token"][0]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/artist-only", headers=headers).json()["id"], str(self.artist.id))

    def test_diagnosis_collector_booking_stays_on_old_uuid_after_identity_transfer(self):
        original_client = self.booking.client_id
        self._finish(self._start())
        self.assertEqual(self.artist.google_id, self.google["sub"])
        self.assertIsNone(self.collector.google_id)
        self.assertEqual(self.booking.client_id, original_client)
        self.assertNotEqual(self.booking.client_id, self.artist.id)

    def test_diagnosis_link_state_survives_reset_despite_bearer_revocation(self):
        state = self._start()
        reset = create_password_reset_token(self.artist.id, self.artist.password_hash, email=self.artist.email)
        with patch.object(auth, "send_password_changed_email", new=AsyncMock()):
            response = self.client.post(
                "/auth/reset-password", json={"token": reset, "new_password": "replacement-password"}
            )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/auth/me", headers=self.headers).status_code, 401)
        callback = self._finish(state)
        self.assertIn("google_signin=linked", callback.headers["location"])
        self.assertEqual(self.artist.google_id, self.google["sub"])

    def test_diagnosis_cancel_loses_connections_context_but_fresh_retry_works(self):
        state = self._start()
        cancelled = self._finish(state, error="access_denied")
        self.assertIn("/portal?gsi_error=cancelled", cancelled.headers["location"])
        self.assertNotIn("workflow=connections", cancelled.headers["location"])
        self.assertIsNone(self.artist.google_id)
        self.assertEqual(self.collector.google_id, self.google["sub"])
        retry = self._finish(self._start())
        self.assertIn("google_signin=linked", retry.headers["location"])

    def test_expired_or_tampered_state_changes_no_identity(self):
        state = self._start()
        claims = jwt.decode(state, auth._settings.SECRET_KEY, algorithms=[auth._settings.ALGORITHM])
        claims["exp"] = int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())
        expired = jwt.encode(claims, auth._settings.SECRET_KEY, algorithm=auth._settings.ALGORITHM)
        for invalid in (expired, state + "tampered"):
            callback = self._finish(invalid)
            self.assertIn("gsi_error=state", callback.headers["location"])
            self.assertIsNone(self.artist.google_id)
            self.assertEqual(self.collector.google_id, self.google["sub"])


if __name__ == "__main__":
    unittest.main()
