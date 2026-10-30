"""Linking a Google login to a signed-in account.

Google sign-in resolves by linked Google ID, then email, and creates a collector
when neither matches. Staff whose Google address differs from their portal
address landed on that collector. These pin the link flow that fixes it: the
signed state binds the signed-in user, the verified Google ID moves off the
collector onto that user, and bad inputs change nothing.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import asyncio  # noqa: E402
import unittest  # noqa: E402
import uuid  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from jose import jwt  # noqa: E402

from app.api.v1.endpoints import auth  # noqa: E402
from app.core.config import get_settings  # noqa: E402


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    """Answers `where id == ?` and `where google_id == ?` from a small user list."""

    def __init__(self, users, aliases=None):
        self.users = users
        self.aliases = aliases or {}
        self.flushes = 0

    async def execute(self, stmt):
        clause = stmt.whereclause
        if hasattr(clause, "clauses"):
            clause = list(clause.clauses)[0]
        column = clause.left.key
        value = clause.right.value
        for user in self.users:
            if getattr(user, column) == value:
                return _Result(user)
        if column == "google_id":
            return _Result(self.aliases.get(value))
        return _Result(None)

    async def flush(self):
        # google_id is unique; a flush must never see two rows holding one.
        held = [u.google_id for u in self.users if u.google_id]
        assert len(held) == len(set(held)), "duplicate google_id at flush"
        self.flushes += 1


def _user(role, google_id=None):
    return SimpleNamespace(id=uuid.uuid4(), role=role, google_id=google_id)


def _link(session, user_id, sub="google-sub-123"):
    with patch.object(auth, "_verify_google_credential", return_value={"sub": sub, "email_verified": True}):
        return asyncio.run(auth._finish_google_link(session, str(user_id), "id-token"))


def _outcome(response):
    return parse_qs(urlparse(response.headers["location"]).query)["google_signin"][0]


class FinishLinkTests(unittest.TestCase):
    def test_cannot_move_another_artists_merged_google_login(self):
        artist = _user("artist")
        owner = _user("artist")
        session = _Session([artist, owner], aliases={"google-sub-123": owner})
        self.assertEqual(_outcome(_link(session, artist.id)), "error")
        self.assertIsNone(artist.google_id)
        self.assertEqual(session.flushes, 0)

    def test_own_merged_google_login_is_already_linked(self):
        artist = _user("artist")
        session = _Session([artist], aliases={"google-sub-123": artist})
        self.assertEqual(_outcome(_link(session, artist.id)), "linked")
        self.assertEqual(session.flushes, 0)

    def test_moves_google_login_from_collector_to_artist(self):
        artist = _user("artist")
        collector = _user("collector", google_id="google-sub-123")
        session = _Session([artist, collector])

        response = _link(session, artist.id)

        self.assertEqual(_outcome(response), "linked")
        self.assertEqual(artist.google_id, "google-sub-123")
        self.assertIsNone(collector.google_id)
        self.assertIn("workflow=connections", response.headers["location"])

    def test_links_when_no_account_holds_the_google_login(self):
        artist = _user("artist")
        response = _link(_Session([artist]), artist.id)
        self.assertEqual(_outcome(response), "linked")
        self.assertEqual(artist.google_id, "google-sub-123")

    def test_relinking_the_same_google_login_is_a_no_op(self):
        artist = _user("artist", google_id="google-sub-123")
        session = _Session([artist])
        self.assertEqual(_outcome(_link(session, artist.id)), "linked")
        self.assertEqual(session.flushes, 0)

    def test_invalid_google_credential_changes_nothing(self):
        artist = _user("artist")
        collector = _user("collector", google_id="google-sub-123")
        with patch.object(auth, "_verify_google_credential", side_effect=HTTPException(status_code=401)):
            response = asyncio.run(auth._finish_google_link(_Session([artist, collector]), str(artist.id), "bad"))
        self.assertEqual(_outcome(response), "error")
        self.assertIsNone(artist.google_id)
        self.assertEqual(collector.google_id, "google-sub-123")

    def test_unknown_or_malformed_user_is_an_error(self):
        self.assertEqual(_outcome(_link(_Session([]), uuid.uuid4())), "error")
        self.assertEqual(_outcome(_link(_Session([]), "not-a-uuid")), "error")


class LinkStartTests(unittest.TestCase):
    def test_state_binds_the_signed_in_user_and_reuses_the_signin_callback(self):
        user = _user("artist")
        settings = get_settings()
        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", "client-id"),
            patch.object(auth._settings, "GOOGLE_CLIENT_SECRET", "secret"),
        ):
            response = asyncio.run(auth.google_link_start.__wrapped__(SimpleNamespace(), current_user=user))
        query = parse_qs(urlparse(response.authorization_url).query)
        self.assertTrue(query["redirect_uri"][0].endswith("/api/v1/auth/google/callback-signin"))
        claims = jwt.decode(query["state"][0], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        self.assertEqual(claims["purpose"], "google_link")
        self.assertEqual(claims["sub"], str(user.id))

    def test_status_reports_whether_a_google_login_is_linked(self):
        linked = asyncio.run(auth.google_link_status(current_user=_user("artist", google_id="x")))
        unlinked = asyncio.run(auth.google_link_status(current_user=_user("artist")))
        self.assertTrue(linked.linked)
        self.assertFalse(unlinked.linked)


class CallbackRoutingTests(unittest.TestCase):
    def _state(self, purpose, sub):
        settings = get_settings()
        now = datetime.now(UTC)
        return jwt.encode(
            {"purpose": purpose, "sub": sub, "iat": now, "exp": now + timedelta(minutes=5)},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )

    def test_link_state_routes_to_the_link_finisher_not_signin(self):
        user_id = str(uuid.uuid4())

        class _Token:
            def raise_for_status(self):
                return None

            def json(self):
                return {"id_token": "id-token"}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, *args, **kwargs):
                return _Token()

        async def fake_finish(db, sub, token):
            fake_finish.called = (sub, token)
            return auth._link_result("linked")

        with (
            patch.object(auth.httpx, "AsyncClient", return_value=_Client()),
            patch.object(auth, "_finish_google_link", side_effect=fake_finish),
            patch.object(auth, "_resolve_google_user") as resolve,
        ):
            response = asyncio.run(
                auth.google_signin_callback.__wrapped__(
                    SimpleNamespace(),
                    background_tasks=None,
                    code="code",
                    state=self._state("google_link", user_id),
                    error=None,
                    db=None,
                )
            )
        self.assertEqual(fake_finish.called, (user_id, "id-token"))
        resolve.assert_not_called()
        self.assertEqual(_outcome(response), "linked")

    def test_unknown_purpose_is_rejected(self):
        response = asyncio.run(
            auth.google_signin_callback.__wrapped__(
                SimpleNamespace(),
                background_tasks=None,
                code="code",
                state=self._state("something_else", "x"),
                error=None,
                db=None,
            )
        )
        self.assertIn("gsi_error=state", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
