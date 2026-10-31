import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.endpoints import auth, bookings
from app.core.rate_limit import get_rate_limit_key, limiter
from app.core.schema_compare import BASELINE_SQL_PATH  # noqa: E402
from app.core.security import hash_password
from app.models.user import BTTCOUser
from app.schemas.booking import PublicBookingResponse


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        # Email lookups now go through the duplicate-tolerant resolver, which
        # reads the whole result set instead of demanding exactly one row.
        return _FakeScalars([] if self._value is None else [self._value])


class _EmptySession:
    def __init__(self):
        self._last_added = None

    async def execute(self, _stmt):
        return _FakeResult(None)

    def add(self, value):
        self._last_added = value

    async def flush(self):
        if self._last_added is not None and getattr(self._last_added, "id", None) is None:
            self._last_added.id = uuid.uuid4()


class _LoginSession:
    def __init__(self, user):
        self._user = user

    async def execute(self, _stmt):
        return _FakeResult(self._user)


def _build_test_app(session):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.include_router(auth.router)

    async def override_get_db():
        yield session

    app.dependency_overrides[auth.get_db] = override_get_db
    return app


def _build_booking_test_app(session):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.include_router(bookings.router)

    async def override_get_db():
        yield session

    app.dependency_overrides[bookings.get_db] = override_get_db
    return app


class AuthRateLimitTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_rate_limit_key_prefers_forwarded_header(self):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [(b"x-forwarded-for", b"198.51.100.8, 10.0.0.1")],
            "client": ("10.0.0.2", 1234),
        }

        from fastapi import Request

        self.assertEqual(get_rate_limit_key(Request(scope)), "198.51.100.8")

    def test_register_returns_429_after_limit(self):
        app = _build_test_app(_EmptySession())

        with patch("app.api.v1.endpoints.auth.send_account_setup_email", autospec=True):
            client = TestClient(app)
            headers = {"X-Forwarded-For": "203.0.113.10"}

            for attempt in range(5):
                response = client.post(
                    "/auth/register",
                    json={
                        "email": f"user{attempt}@example.com",
                        "password": "password123",
                        "name": "Rate Limited User",
                    },
                    headers=headers,
                )
                self.assertEqual(response.status_code, 201)

            response = client.post(
                "/auth/register",
                json={
                    "email": "overflow@example.com",
                    "password": "password123",
                    "name": "Rate Limited User",
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 429)

    def test_login_succeeds_until_limit_then_returns_429(self):
        user = BTTCOUser(
            id=uuid.uuid4(),
            email="user@example.com",
            email_verified_at=datetime.now(UTC),
            password_hash=hash_password("password123"),
            name="Collector",
            role="collector",
        )
        app = _build_test_app(_LoginSession(user))
        client = TestClient(app)
        headers = {"X-Forwarded-For": "203.0.113.11"}

        for _ in range(10):
            response = client.post(
                "/auth/login",
                json={"email": "user@example.com", "password": "password123"},
                headers=headers,
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("access_token", response.json())

        response = client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "password123"},
            headers=headers,
        )

        self.assertEqual(response.status_code, 429)

    def test_google_auth_returns_429_after_limit(self):
        app = _build_test_app(_EmptySession())
        client = TestClient(app)
        headers = {"X-Forwarded-For": "203.0.113.12"}

        with (
            patch.object(auth._settings, "GOOGLE_CLIENT_ID", "active-bttco-client.apps.googleusercontent.com"),
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", ""),
            patch("app.api.v1.endpoints.auth.google_id_token.verify_oauth2_token", side_effect=ValueError),
        ):
            for _ in range(10):
                response = client.post("/auth/google", json={"credential": "bad-token"}, headers=headers)
                self.assertEqual(response.status_code, 401)

            response = client.post("/auth/google", json={"credential": "bad-token"}, headers=headers)

        self.assertEqual(response.status_code, 429)

    def test_public_booking_submit_returns_429_after_limit(self):
        app = _build_booking_test_app(_EmptySession())
        client = TestClient(app)
        headers = {"X-Forwarded-For": "203.0.113.13"}
        payload = {
            "artist_slug": "justin",
            "first_name": "Rate",
            "last_name": "Limited",
            "email": "booking-rate@example.test",
            "description": "Public booking submit rate limit test request",
        }

        with patch(
            "app.api.v1.endpoints.bookings._do_submit_public_booking",
            autospec=True,
            return_value=PublicBookingResponse(reference_code="BKG-RATE01", artist_name="Justin Walker"),
        ):
            for _ in range(5):
                response = client.post("/bookings/submit", json=payload, headers=headers)
                self.assertEqual(response.status_code, 200)

            response = client.post("/bookings/submit", json=payload, headers=headers)

        self.assertEqual(response.status_code, 429)


class BaselinePerformanceIndexTests(unittest.TestCase):
    def test_performance_indexes_use_real_columns(self):
        baseline = BASELINE_SQL_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "CREATE INDEX idx_bttco_bookings_conversation_id ON public.bttco_bookings USING btree "
            "(conversation_id) WHERE (conversation_id IS NOT NULL);",
            baseline,
        )
        self.assertIn(
            "CREATE INDEX idx_bttco_notifications_read ON public.bttco_notifications USING btree "
            "(user_id, read) WHERE (read = false);",
            baseline,
        )
        self.assertNotIn("is_read", baseline)


if __name__ == "__main__":
    unittest.main()
