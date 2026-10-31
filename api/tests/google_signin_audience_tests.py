"""Regression guards for the active Ryozan Google OAuth project.

The browser and API must agree on the GSI token audience. Production may set an
explicit GOOGLE_SIGNIN_CLIENT_ID, otherwise the API uses the active Ryozan web
OAuth client that also owns the server-side Calendar flow. Source must never
fall back to the deleted legacy Gmodz client.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.endpoints import auth
from app.core.config import get_settings
from app.core.rate_limit import limiter

WEB_CLIENT_ID = "628400721124-example.apps.googleusercontent.com"
LEGACY_PROJECT_NUMBER = "656109371073"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _UserSession:
    """Minimal AsyncSession stand-in that returns one row then nothing."""

    def __init__(self, user):
        self._user = user
        self._served = False

    async def execute(self, _stmt):
        if self._served:
            return _FakeResult(None)
        self._served = True
        return _FakeResult(self._user)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = uuid.uuid4()

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, _value):
        return None


def _build_app(session):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.include_router(auth.router)

    async def override_get_db():
        yield session

    app.dependency_overrides[auth.get_db] = override_get_db
    return app


class GoogleSignInAudienceTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_settings_do_not_embed_the_deleted_legacy_client(self):
        from pathlib import Path

        settings = get_settings()
        source = Path(auth.__file__).parents[3].joinpath("core", "config.py").read_text(encoding="utf-8")

        self.assertNotIn(LEGACY_PROJECT_NUMBER, settings.GOOGLE_SIGNIN_CLIENT_ID)
        self.assertNotIn(LEGACY_PROJECT_NUMBER, source)

    def test_google_auth_uses_explicit_signin_client_id(self):
        from app.models.user import User

        user = User(
            id=uuid.uuid4(),
            email="collector@example.com",
            password_hash="x",
            name="Collector",
            role="collector",
        )
        app = _build_app(_UserSession(user))
        client = TestClient(app)

        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", WEB_CLIENT_ID),
            patch.object(auth._settings, "GOOGLE_CLIENT_ID", "calendar-client.apps.googleusercontent.com"),
            patch(
                "app.api.v1.endpoints.auth.google_id_token.verify_oauth2_token",
                return_value={
                    "email": "collector@example.com",
                    "email_verified": True,
                    "sub": "g-123",
                    "name": "Collector",
                },
            ) as verify,
        ):
            response = client.post("/auth/google", json={"credential": "a-google-id-token"})

        self.assertEqual(response.status_code, 200, response.text)
        verify.assert_called_once()

        audience = verify.call_args.args[2]
        self.assertEqual(audience, WEB_CLIENT_ID)

    def test_google_auth_falls_back_to_active_ryozan_web_client(self):
        from app.models.user import User

        user = User(
            id=uuid.uuid4(),
            email="collector@example.com",
            password_hash="x",
            name="Collector",
            role="collector",
        )
        client = TestClient(_build_app(_UserSession(user)))

        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", ""),
            patch.object(auth._settings, "GOOGLE_CLIENT_ID", WEB_CLIENT_ID),
            patch(
                "app.api.v1.endpoints.auth.google_id_token.verify_oauth2_token",
                return_value={
                    "email": "collector@example.com",
                    "email_verified": True,
                    "sub": "g-123",
                    "name": "Collector",
                },
            ) as verify,
        ):
            response = client.post("/auth/google", json={"credential": "a-google-id-token"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(verify.call_args.args[2], WEB_CLIENT_ID)

    def test_google_auth_fails_closed_without_a_client_id(self):
        client = TestClient(_build_app(_UserSession(None)))

        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", ""),
            patch.object(auth._settings, "GOOGLE_CLIENT_ID", ""),
        ):
            response = client.post("/auth/google", json={"credential": "a-google-id-token"})

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "Google sign-in is not configured")

    def test_google_signin_rejects_an_unverified_email(self):
        client = TestClient(_build_app(_UserSession(None)))

        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", WEB_CLIENT_ID),
            patch(
                "app.api.v1.endpoints.auth.google_id_token.verify_oauth2_token",
                return_value={
                    "email": "artist@example.com",
                    "email_verified": False,
                    "sub": "g-artist",
                    "name": "Artist",
                },
            ),
        ):
            response = client.post("/auth/google", json={"credential": "a-google-id-token"})

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"], "Invalid Google credential")

    def test_verified_google_signin_claims_an_unclaimed_artist_without_warning(self):
        from app.models.user import User

        artist = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            password_hash="unpublished-placeholder",
            name="Artist",
            role="artist",
            must_reset_password=True,
        )
        client = TestClient(_build_app(_UserSession(artist)))

        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", WEB_CLIENT_ID),
            patch(
                "app.api.v1.endpoints.auth.google_id_token.verify_oauth2_token",
                return_value={
                    "email": artist.email,
                    "email_verified": True,
                    "sub": "g-artist",
                    "name": artist.name,
                },
            ),
        ):
            response = client.post("/auth/google", json={"credential": "a-google-id-token"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("access_token", response.json())
        self.assertEqual(artist.google_id, "g-artist")
        self.assertFalse(artist.must_reset_password)

    def test_calendar_oauth_pair_is_untouched(self):
        """google_auth.py must keep using the matched GOOGLE_CLIENT_ID/SECRET pair."""
        from pathlib import Path

        source = Path(auth.__file__).with_name("google_auth.py").read_text(encoding="utf-8")

        self.assertIn("settings.GOOGLE_CLIENT_ID", source)
        self.assertIn("settings.GOOGLE_CLIENT_SECRET", source)
        self.assertNotIn("GOOGLE_SIGNIN_CLIENT_ID", source)

    def test_calendar_oauth_requests_only_registered_scopes(self):
        import asyncio
        from types import SimpleNamespace
        from urllib.parse import parse_qs, urlparse

        from app.api.v1.endpoints import google_auth
        from app.models.user import User
        from app.services import calendar_sync

        calendar_scopes = {"https://www.googleapis.com/auth/calendar"}
        identity_scopes = {
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "openid",
        }

        self.assertEqual(set(calendar_sync.SCOPES), calendar_scopes)
        self.assertEqual(set(google_auth.SCOPES), identity_scopes | calendar_scopes)

        requested = " ".join(google_auth.SCOPES)
        for unused_api in ("gmail", "drive", "contacts", "directory"):
            self.assertNotIn(unused_api, requested)

        settings = SimpleNamespace(
            GOOGLE_CLIENT_ID=WEB_CLIENT_ID,
            GOOGLE_CLIENT_SECRET="test-secret",
            GOOGLE_REDIRECT_URI="https://api.example.com/google/callback",
        )
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            password_hash="x",
            name="Artist",
            role="artist",
        )
        with patch.object(google_auth, "get_settings", return_value=settings):
            response = asyncio.run(google_auth.google_connect(current_user=user))

        query = parse_qs(urlparse(response.authorization_url).query)
        self.assertEqual(set(query["scope"][0].split()), identity_scopes | calendar_scopes)
        self.assertNotIn("include_granted_scopes", query)


if __name__ == "__main__":
    unittest.main()
