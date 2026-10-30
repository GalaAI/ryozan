"""
Password authentication + recovery regression tests.

Covers two production defects:

BUG 1: long passwords returned HTTP 500.
  api/requirements.txt pins bcrypt==5.0.0, which RAISES
  ``ValueError: password cannot be longer than 72 bytes`` where bcrypt 4.x
  silently truncated. Neither verify_password nor hash_password guarded the
  length, so the ValueError escaped the request and FastAPI answered 500 on
  both login and registration.

BUG 2: there was no password recovery at all. The portal rendered a
  "Forgot username/password?" link whose handler only showed a toast.

The fix for BUG 1 is byte truncation, not pre-hashing: every hash already in
production was produced by bcrypt 4.x, which considered only the first 72
bytes. Truncating reproduces that byte for byte, so stored credentials keep
working. Pre-hashing would invalidate all of them, as asserted below.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import bcrypt
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.endpoints import auth
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.security import (
    PASSWORD_RESET_EXPIRE_MINUTES,
    PASSWORD_RESET_TOKEN_TYPE,
    create_access_token,
    create_password_reset_token,
    create_user_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.user import BTTCOUser, UserIdentityAlias

_settings = get_settings()

# 100 bytes: the exact shape that 500'd in production.
LONG_PASSWORD = "A" * 100
# Same first 72 bytes, different tail: bcrypt cannot tell these apart, and
# neither could bcrypt 4.x, so both must authenticate against the same hash.
LONG_PASSWORD_SAME_PREFIX = "A" * 72 + "-a-completely-different-tail"


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


class _UserSession:
    """Returns the same user for every query; records commits."""

    def __init__(self, user):
        self._user = user
        self.commits = 0
        self.added = None

    async def execute(self, _stmt):
        if _stmt.column_descriptions[0].get("entity") is UserIdentityAlias:
            return _FakeResult(None)
        return _FakeResult(self._user)

    def add(self, value):
        self.added = value

    async def flush(self):
        if self.added is not None and getattr(self.added, "id", None) is None:
            self.added.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1


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


def _build_bearer_probe_app(session):
    """Minimal app exposing a single get_current_user-protected route.

    Deliberately not /auth/me: this asserts on the dependency's accept/reject
    decision, not on UserResponse serialisation.
    """
    app = FastAPI()

    @app.get("/probe")
    async def probe(current_user=Depends(get_current_user)):
        return {"id": str(current_user.id)}

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    return app


def _make_user(password="password123", **kwargs):
    fields = {
        "id": uuid.uuid4(),
        "email": "collector@example.com",
        "password_hash": hash_password(password),
        "name": "Collector",
        "role": "collector",
        "must_reset_password": False,
        "email_verified_at": datetime.now(UTC),
    }
    fields.update(kwargs)
    return BTTCOUser(**fields)


# ── BUG 1: long passwords ──────────────────────────────────────────────────


class LongPasswordTests(unittest.TestCase):
    """Before the fix every assertion in this class raised or 500'd."""

    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_hash_password_accepts_password_longer_than_72_bytes(self):
        # bcrypt 5.0 raises ValueError here without the truncation guard.
        digest = hash_password(LONG_PASSWORD)
        self.assertTrue(digest.startswith("$2b$"))

    def test_verify_password_accepts_password_longer_than_72_bytes(self):
        digest = hash_password("short-password")
        self.assertFalse(verify_password(LONG_PASSWORD, digest))

    def test_multibyte_password_over_72_bytes_does_not_raise(self):
        # 40 x 3-byte characters = 120 bytes; the 72-byte cut lands mid-character.
        multibyte = "あ" * 40
        digest = hash_password(multibyte)
        self.assertTrue(verify_password(multibyte, digest))

    def test_login_with_long_wrong_password_returns_401_not_500(self):
        user = _make_user(password="password123")
        client = TestClient(_build_app(_UserSession(user)))

        response = client.post(
            "/auth/login",
            json={"email": "collector@example.com", "password": LONG_PASSWORD},
            headers={"X-Forwarded-For": "203.0.113.40"},
        )

        self.assertEqual(response.status_code, 401)

    def test_login_authenticates_when_first_72_bytes_match(self):
        user = _make_user(password=LONG_PASSWORD)
        client = TestClient(_build_app(_UserSession(user)))

        response = client.post(
            "/auth/login",
            json={"email": "collector@example.com", "password": LONG_PASSWORD_SAME_PREFIX},
            headers={"X-Forwarded-For": "203.0.113.41"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.json())

    def test_register_with_long_password_succeeds(self):
        session = _UserSession(None)
        client = TestClient(_build_app(session))

        with patch("app.api.v1.endpoints.auth.send_account_setup_email", autospec=True):
            response = client.post(
                "/auth/register",
                json={
                    "email": "newcollector@example.com",
                    "password": LONG_PASSWORD,
                    "name": "New Collector",
                },
                headers={"X-Forwarded-For": "203.0.113.42"},
            )

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("access_token", response.json())
        self.assertTrue(response.json()["verification_required"])


class LegacyHashCompatibilityTests(unittest.TestCase):
    """The stored-credential contract: byte truncation, never pre-hashing."""

    # Produced by bcrypt for the literal password "password123". Frozen here so
    # a future change to hash_password that alters the input bcrypt sees (for
    # example SHA-256 pre-hashing) fails loudly instead of silently locking
    # every existing account out.
    LEGACY_HASH = "$2b$12$4bGd22uozwQjOdaUb.TpF.UX10pgIYtV3Rxnhv8Ahq0abrQY7gdQK"

    def test_existing_bcrypt_hash_still_verifies(self):
        self.assertTrue(verify_password("password123", self.LEGACY_HASH))
        self.assertFalse(verify_password("wrong-password", self.LEGACY_HASH))

    def test_bcrypt4_style_truncated_hash_still_verifies(self):
        # bcrypt 4.x hashed only the first 72 bytes, so this is byte-identical
        # to what production stored for a 100-character password.
        legacy = bcrypt.hashpw(LONG_PASSWORD.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")

        self.assertTrue(verify_password(LONG_PASSWORD, legacy))
        self.assertTrue(verify_password(LONG_PASSWORD_SAME_PREFIX, legacy))

    def test_hash_is_not_prehashed(self):
        # If hash_password pre-hashed, raw bcrypt over the truncated bytes
        # would no longer verify against it.
        digest = hash_password(LONG_PASSWORD)

        self.assertTrue(bcrypt.checkpw(LONG_PASSWORD.encode("utf-8")[:72], digest.encode("utf-8")))

    def test_malformed_stored_hash_returns_false_instead_of_raising(self):
        self.assertFalse(verify_password("anything", ""))
        self.assertFalse(verify_password("anything", "not-a-bcrypt-hash"))


# ── ARTIST FIRST LOGIN ────────────────────────────────────────────────────


class ArtistFirstLoginTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_unclaimed_artist_can_use_the_temporary_password(self):
        artist = _make_user(
            password="unpublished-random-value",
            role="artist",
            must_reset_password=True,
        )
        client = TestClient(_build_app(_UserSession(artist)))

        response = client.post(
            "/auth/login",
            json={"email": artist.email, "password": "tattoo"},
            headers={"X-Forwarded-For": "203.0.113.43"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("access_token", response.json())

    def test_established_artist_does_not_accept_the_temporary_password(self):
        artist = _make_user(
            password="artist-chosen-password",
            role="artist",
            must_reset_password=False,
        )
        client = TestClient(_build_app(_UserSession(artist)))

        response = client.post(
            "/auth/login",
            json={"email": artist.email, "password": "tattoo"},
            headers={"X-Forwarded-For": "203.0.113.44"},
        )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"], "Invalid email or password")

    def test_non_artist_does_not_accept_the_temporary_password(self):
        collector = _make_user(
            password="unpublished-random-value",
            role="collector",
            must_reset_password=True,
        )
        client = TestClient(_build_app(_UserSession(collector)))

        response = client.post(
            "/auth/login",
            json={"email": collector.email, "password": "tattoo"},
            headers={"X-Forwarded-For": "203.0.113.45"},
        )

        self.assertEqual(response.status_code, 401, response.text)

    def test_authenticated_first_login_can_choose_a_private_password(self):
        artist = _make_user(
            password="unpublished-random-value",
            role="artist",
            must_reset_password=True,
        )
        client = TestClient(_build_app(_UserSession(artist)))
        session_token = create_user_access_token(artist)

        response = client.patch(
            "/auth/change-password",
            json={
                "current_password": "tattoo",
                "new_password": "artist-private-password",
            },
            headers={"Authorization": f"Bearer {session_token}"},
        )

        self.assertEqual(response.status_code, 204, response.text)
        self.assertFalse(artist.must_reset_password)
        self.assertTrue(verify_password("artist-private-password", artist.password_hash))

    def test_established_account_still_requires_its_current_password(self):
        artist = _make_user(
            password="artist-chosen-password",
            role="artist",
            must_reset_password=False,
        )
        client = TestClient(_build_app(_UserSession(artist)))
        session_token = create_user_access_token(artist)

        response = client.patch(
            "/auth/change-password",
            json={
                "current_password": "tattoo",
                "new_password": "attacker-selected-password",
            },
            headers={"Authorization": f"Bearer {session_token}"},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertTrue(verify_password("artist-chosen-password", artist.password_hash))


# ── BUG 2: password recovery ───────────────────────────────────────────────


class ForgotPasswordTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_response_is_identical_for_known_and_unknown_email(self):
        known = TestClient(_build_app(_UserSession(_make_user())))
        unknown = TestClient(_build_app(_UserSession(None)))

        with patch("app.api.v1.endpoints.auth.send_password_reset_email", autospec=True):
            known_response = known.post(
                "/auth/forgot-password",
                json={"email": "collector@example.com"},
                headers={"X-Forwarded-For": "203.0.113.50"},
            )
            unknown_response = unknown.post(
                "/auth/forgot-password",
                json={"email": "nobody@example.com"},
                headers={"X-Forwarded-For": "203.0.113.51"},
            )

        self.assertEqual(known_response.status_code, 200)
        self.assertEqual(known_response.status_code, unknown_response.status_code)
        self.assertEqual(known_response.json(), unknown_response.json())
        self.assertNotIn("exist", known_response.json()["message"].split("If an account exists")[-1])

    def test_email_is_queued_only_when_the_account_exists(self):
        user = _make_user()

        with patch("app.api.v1.endpoints.auth.send_password_reset_email", autospec=True) as sender:
            TestClient(_build_app(_UserSession(user))).post(
                "/auth/forgot-password",
                json={"email": "collector@example.com"},
                headers={"X-Forwarded-For": "203.0.113.52"},
            )
            self.assertEqual(sender.call_count, 1)
            reset_url = sender.call_args.kwargs["reset_url"]

        with patch("app.api.v1.endpoints.auth.send_password_reset_email", autospec=True) as sender:
            TestClient(_build_app(_UserSession(None))).post(
                "/auth/forgot-password",
                json={"email": "nobody@example.com"},
                headers={"X-Forwarded-For": "203.0.113.53"},
            )
            self.assertEqual(sender.call_count, 0)

        # Link points at the frontend reset screen and carries a reset-typed token.
        self.assertTrue(reset_url.startswith(f"{_settings.FRONTEND_URL}/reset-password?token="))
        token = reset_url.split("token=", 1)[1]
        claims = jwt.decode(token, _settings.SECRET_KEY, algorithms=[_settings.ALGORITHM])
        self.assertEqual(claims["typ"], PASSWORD_RESET_TOKEN_TYPE)

    def test_forgot_password_is_rate_limited(self):
        client = TestClient(_build_app(_UserSession(None)))
        headers = {"X-Forwarded-For": "203.0.113.54"}

        for _ in range(5):
            response = client.post("/auth/forgot-password", json={"email": "a@example.com"}, headers=headers)
            self.assertEqual(response.status_code, 200)

        response = client.post("/auth/forgot-password", json={"email": "a@example.com"}, headers=headers)
        self.assertEqual(response.status_code, 429)


class ResetPasswordTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_token_works_once_then_is_rejected(self):
        user = _make_user(password="old-password-123")
        session = _UserSession(user)
        client = TestClient(_build_app(session))
        token = create_password_reset_token(user.id, user.password_hash, email=user.email)
        headers = {"X-Forwarded-For": "203.0.113.60"}

        first = client.post(
            "/auth/reset-password",
            json={"token": token, "new_password": "brand-new-password"},
            headers=headers,
        )
        self.assertEqual(first.status_code, 204)
        self.assertEqual(session.commits, 1)
        self.assertTrue(verify_password("brand-new-password", user.password_hash))
        self.assertFalse(verify_password("old-password-123", user.password_hash))

        # Replay: the hash the token was bound to is gone, so the token is dead.
        second = client.post(
            "/auth/reset-password",
            json={"token": token, "new_password": "another-password"},
            headers=headers,
        )
        self.assertEqual(second.status_code, 400)
        self.assertTrue(verify_password("brand-new-password", user.password_hash))

    def test_redemption_locks_the_user_row(self):
        statement = str(auth._locked_user_by_id(uuid.uuid4()))
        self.assertIn("FOR UPDATE", statement)

    def test_reset_revokes_existing_bearer_sessions(self):
        user = _make_user(password="old-password-123")
        session = _UserSession(user)
        bearer_client = TestClient(_build_bearer_probe_app(session))
        reset_client = TestClient(_build_app(session))
        old_session = create_user_access_token(user)
        reset_token = create_password_reset_token(user.id, user.password_hash, email=user.email)

        before = bearer_client.get("/probe", headers={"Authorization": f"Bearer {old_session}"})
        self.assertEqual(before.status_code, 200)

        changed = reset_client.post(
            "/auth/reset-password",
            json={"token": reset_token, "new_password": "brand-new-password"},
            headers={"X-Forwarded-For": "203.0.113.67"},
        )
        self.assertEqual(changed.status_code, 204)

        after = bearer_client.get("/probe", headers={"Authorization": f"Bearer {old_session}"})
        self.assertEqual(after.status_code, 401)

    def test_token_is_dead_after_password_changes_by_another_route(self):
        user = _make_user(password="old-password-123")
        token = create_password_reset_token(user.id, user.password_hash, email=user.email)

        # Simulate the user changing their password via /auth/change-password.
        user.password_hash = hash_password("changed-elsewhere")

        client = TestClient(_build_app(_UserSession(user)))
        response = client.post(
            "/auth/reset-password",
            json={"token": token, "new_password": "attacker-password"},
            headers={"X-Forwarded-For": "203.0.113.61"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(verify_password("changed-elsewhere", user.password_hash))

    def test_reset_clears_must_reset_password(self):
        user = _make_user(password="temp-password-123", must_reset_password=True)
        client = TestClient(_build_app(_UserSession(user)))
        token = create_password_reset_token(user.id, user.password_hash, email=user.email)

        response = client.post(
            "/auth/reset-password",
            json={"token": token, "new_password": "chosen-by-the-user"},
            headers={"X-Forwarded-For": "203.0.113.62"},
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(user.must_reset_password)

    def test_expired_token_is_rejected_with_400(self):
        user = _make_user()
        client = TestClient(_build_app(_UserSession(user)))
        expired = jwt.encode(
            {
                "sub": str(user.id),
                "typ": PASSWORD_RESET_TOKEN_TYPE,
                "pwf": "0" * 32,
                "exp": datetime.now(UTC) - timedelta(minutes=1),
            },
            _settings.SECRET_KEY,
            algorithm=_settings.ALGORITHM,
        )

        response = client.post(
            "/auth/reset-password",
            json={"token": expired, "new_password": "does-not-matter"},
            headers={"X-Forwarded-For": "203.0.113.63"},
        )

        self.assertEqual(response.status_code, 400)

    def test_tampered_and_garbage_tokens_are_rejected_with_400(self):
        user = _make_user()
        client = TestClient(_build_app(_UserSession(user)))
        valid = create_password_reset_token(user.id, user.password_hash, email=user.email)
        tampered = valid[:-4] + ("aaaa" if not valid.endswith("aaaa") else "bbbb")

        for candidate in (tampered, "not-a-jwt", ""):
            response = client.post(
                "/auth/reset-password",
                json={"token": candidate, "new_password": "does-not-matter"},
                headers={"X-Forwarded-For": "203.0.113.64"},
            )
            self.assertEqual(response.status_code, 400, candidate[:12])
            self.assertNotIn(candidate[:20] or "\x00", response.text)

    def test_access_token_cannot_be_redeemed_as_a_reset_token(self):
        user = _make_user()
        client = TestClient(_build_app(_UserSession(user)))
        access = create_access_token({"sub": str(user.id), "role": user.role})

        response = client.post(
            "/auth/reset-password",
            json={"token": access, "new_password": "does-not-matter"},
            headers={"X-Forwarded-For": "203.0.113.65"},
        )

        self.assertEqual(response.status_code, 400)

    def test_reset_token_cannot_be_used_as_a_bearer_session_token(self):
        user = _make_user()
        client = TestClient(_build_bearer_probe_app(_UserSession(user)))
        reset_token = create_password_reset_token(user.id, user.password_hash, email=user.email)

        response = client.get("/probe", headers={"Authorization": f"Bearer {reset_token}"})

        self.assertEqual(response.status_code, 401)

    def test_ordinary_access_token_still_opens_a_session(self):
        user = _make_user()
        client = TestClient(_build_bearer_probe_app(_UserSession(user)))

        session = client.get(
            "/probe",
            headers={"Authorization": "Bearer " + create_user_access_token(user)},
        )

        self.assertEqual(session.status_code, 200)

    def test_legacy_unbound_access_token_is_rejected(self):
        user = _make_user()
        client = TestClient(_build_bearer_probe_app(_UserSession(user)))
        legacy = create_access_token({"sub": str(user.id), "role": user.role})

        response = client.get("/probe", headers={"Authorization": f"Bearer {legacy}"})

        self.assertEqual(response.status_code, 401)

    def test_reset_token_expiry_matches_the_configured_window(self):
        # The window is configuration now. It must be long enough to survive an
        # emailed link being opened the next day (the 30 minute version 400'd a
        # real artist 15 hours after the send) and still bounded.
        self.assertEqual(PASSWORD_RESET_EXPIRE_MINUTES, _settings.PASSWORD_RESET_EXPIRE_MINUTES)
        self.assertGreaterEqual(PASSWORD_RESET_EXPIRE_MINUTES, 60 * 12)
        self.assertLessEqual(PASSWORD_RESET_EXPIRE_MINUTES, 60 * 24 * 7)

        user = _make_user()
        token = create_password_reset_token(user.id, user.password_hash, email=user.email)
        claims = jwt.decode(token, _settings.SECRET_KEY, algorithms=[_settings.ALGORITHM])
        lifetime_minutes = (claims["exp"] - claims["iat"]) / 60

        self.assertEqual(lifetime_minutes, PASSWORD_RESET_EXPIRE_MINUTES)

    def test_error_body_never_echoes_the_token(self):
        user = _make_user()
        client = TestClient(_build_app(_UserSession(user)))
        stale = create_password_reset_token(uuid.uuid4(), "$2b$12$" + "x" * 53)

        response = client.post(
            "/auth/reset-password",
            json={"token": stale, "new_password": "does-not-matter"},
            headers={"X-Forwarded-For": "203.0.113.66"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(stale, response.text)
        self.assertNotIn(stale.split(".")[2], response.text)


if __name__ == "__main__":
    unittest.main()
