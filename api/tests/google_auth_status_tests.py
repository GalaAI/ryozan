import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import google_auth, pinterest  # noqa: E402
from app.models.user import User  # noqa: E402

# Boundary: auth_rate_limit_tests covers login and OAuth rate limits. These tests
# isolate Google Calendar connector health plus Google and Pinterest callback
# routing without calling either provider during CI.


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, value=None):
        self._value = value
        self.execute_count = 0
        self.flush_count = 0
        self.commit_count = 0

    async def execute(self, _stmt):
        self.execute_count += 1
        return _FakeResult(self._value)

    async def flush(self):
        self.flush_count += 1

    async def commit(self):
        self.commit_count += 1


def _health(**overrides):
    base = {
        "healthy": True,
        "calendar_accessible": True,
        "calendar_id": "primary",
        "summary": "Ryozan Calendar",
        "reason": None,
        "reconnect_required": False,
    }
    base.update(overrides)
    return base


class GoogleAuthStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_managed_status_uses_artist_calendar_without_oauth_token(self):
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Artist",
            role="artist",
            google_calendar_id="shared-artist@example.com",
        )

        with (
            patch.object(google_auth, "workspace_delegation_enabled", return_value=True),
            patch.object(google_auth, "artist_calendar_connected", return_value=True),
            patch.object(google_auth, "artist_calendar_id", return_value="shared-artist@example.com"),
            patch.object(google_auth, "check_calendar_connection", return_value=_health()) as check,
        ):
            status = await google_auth.google_status(current_user=user, db=_FakeSession())

        check.assert_called_once_with(calendar_id="shared-artist@example.com")
        self.assertTrue(status.connected)
        self.assertTrue(status.managed)
        self.assertEqual(status.source, "workspace_delegated")
        self.assertFalse(status.reconnect_required)

    async def test_workspace_managed_connect_refuses_per_user_calendar_oauth(self):
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Artist",
            role="artist",
        )

        with (
            patch.object(google_auth, "workspace_delegation_enabled", return_value=True),
            self.assertRaises(HTTPException) as raised,
        ):
            await google_auth.google_connect(current_user=user)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_status_validates_current_user_token(self):
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Artist",
            role="artist",
            google_refresh_token="artist-refresh",
        )

        with patch.object(google_auth, "check_calendar_connection", return_value=_health()) as check:
            status = await google_auth.google_status(current_user=user, db=_FakeSession())

        check.assert_called_once_with("artist-refresh")
        self.assertTrue(status.connected)
        self.assertTrue(status.healthy)
        self.assertEqual(status.source, "self")
        self.assertEqual(status.email, "artist@example.com")
        self.assertEqual(status.calendar_summary, "Ryozan Calendar")

    async def test_admin_without_token_uses_founder_calendar_health(self):
        admin = User(
            id=uuid.uuid4(),
            email="admin@example.com",
            name="Admin",
            role="admin",
        )
        founder = User(
            id=uuid.uuid4(),
            email="founder@example.com",
            name="Founder",
            role="founder",
            google_refresh_token="founder-refresh",
        )
        db = _FakeSession(founder)

        with patch.object(google_auth, "check_calendar_connection", return_value=_health()) as check:
            status = await google_auth.google_status(current_user=admin, db=db)

        check.assert_called_once_with("founder-refresh")
        self.assertEqual(db.execute_count, 1)
        self.assertTrue(status.connected)
        self.assertEqual(status.source, "founder_fallback")
        self.assertEqual(status.email, "founder@example.com")

    async def test_disconnected_user_requires_reconnect(self):
        user = User(
            id=uuid.uuid4(),
            email="collector@example.com",
            name="Collector",
            role="collector",
        )
        db = _FakeSession()

        status = await google_auth.google_status(current_user=user, db=db)

        self.assertEqual(db.execute_count, 0)
        self.assertFalse(status.connected)
        self.assertFalse(status.healthy)
        self.assertEqual(status.source, "none")
        self.assertEqual(status.reason, "not_connected")
        self.assertTrue(status.reconnect_required)

    async def test_connected_but_stale_token_reports_reconnect_required(self):
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Artist",
            role="artist",
            google_refresh_token="stale-refresh",
        )
        stale_health = _health(
            healthy=False,
            calendar_accessible=False,
            calendar_id=None,
            summary=None,
            reason="refresh_failed",
            reconnect_required=True,
        )

        with patch.object(google_auth, "check_calendar_connection", return_value=stale_health):
            status = await google_auth.google_status(current_user=user, db=_FakeSession())

        self.assertTrue(status.connected)
        self.assertFalse(status.healthy)
        self.assertEqual(status.reason, "refresh_failed")
        self.assertTrue(status.reconnect_required)


class ConnectionCallbackReturnTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_callback_returns_to_hub_connections(self):
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Artist",
            role="artist",
        )
        db = _FakeSession(user)
        settings = SimpleNamespace(
            SECRET_KEY="test-secret",
            ALGORITHM="HS256",
            FRONTEND_URL="https://example.test/",
        )
        flow = MagicMock()
        flow.credentials = SimpleNamespace(refresh_token="google-refresh")

        with (
            patch.object(google_auth, "get_settings", return_value=settings),
            patch("jose.jwt.decode", return_value={"sub": str(user.id), "purpose": "google_oauth"}),
            patch.object(google_auth, "_build_flow", return_value=flow),
        ):
            response = await google_auth.google_callback(code="test-code", state="test-state", db=db)

        self.assertEqual(
            response.headers["location"],
            "https://example.test/dashboard?portal=hub&workflow=connections&google=connected",
        )
        self.assertEqual(user.google_refresh_token, "google-refresh")
        self.assertEqual(db.flush_count, 1)

    async def test_google_callback_error_returns_to_hub_connections(self):
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Artist",
            role="artist",
        )
        settings = SimpleNamespace(
            SECRET_KEY="test-secret",
            ALGORITHM="HS256",
            FRONTEND_URL="https://example.test",
        )
        flow = MagicMock()
        flow.credentials = SimpleNamespace(refresh_token=None)

        with (
            patch.object(google_auth, "get_settings", return_value=settings),
            patch("jose.jwt.decode", return_value={"sub": str(user.id), "purpose": "google_oauth"}),
            patch.object(google_auth, "_build_flow", return_value=flow),
        ):
            response = await google_auth.google_callback(
                code="test-code",
                state="test-state",
                db=_FakeSession(user),
            )

        self.assertEqual(
            response.headers["location"],
            ("https://example.test/dashboard?portal=hub&workflow=connections&google=error&reason=no_refresh_token"),
        )

    async def test_pinterest_callback_returns_to_hub_connections(self):
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Artist",
            role="artist",
        )
        db = _FakeSession(user)
        settings = SimpleNamespace(
            SECRET_KEY="test-secret",
            ALGORITHM="HS256",
            FRONTEND_URL="https://example.test/",
        )

        with (
            patch.object(pinterest, "get_settings", return_value=settings),
            patch("jose.jwt.decode", return_value={"sub": str(user.id), "purpose": "pinterest_oauth"}),
            patch.object(
                pinterest,
                "_exchange_code",
                new=AsyncMock(return_value={"refresh_token": "pinterest-refresh"}),
            ),
        ):
            response = await pinterest.pinterest_callback(code="test-code", state="test-state", db=db)

        self.assertEqual(
            response.headers["location"],
            "https://example.test/dashboard?portal=hub&workflow=connections&pinterest=connected",
        )
        self.assertEqual(user.pinterest_refresh_token, "pinterest-refresh")
        self.assertEqual(db.commit_count, 1)

    async def test_pinterest_callback_error_returns_to_hub_connections(self):
        user = User(
            id=uuid.uuid4(),
            email="artist@example.com",
            name="Artist",
            role="artist",
        )
        settings = SimpleNamespace(
            SECRET_KEY="test-secret",
            ALGORITHM="HS256",
            FRONTEND_URL="https://example.test",
        )

        with (
            patch.object(pinterest, "get_settings", return_value=settings),
            patch("jose.jwt.decode", return_value={"sub": str(user.id), "purpose": "pinterest_oauth"}),
            patch.object(pinterest, "_exchange_code", new=AsyncMock(return_value={})),
        ):
            response = await pinterest.pinterest_callback(
                code="test-code",
                state="test-state",
                db=_FakeSession(user),
            )

        self.assertEqual(
            response.headers["location"],
            ("https://example.test/dashboard?portal=hub&workflow=connections&pinterest=error&reason=no_refresh_token"),
        )


if __name__ == "__main__":
    unittest.main()
