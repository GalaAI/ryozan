"""Tavily web_search tool and instant sign-up for a demo install."""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import json  # noqa: E402
import unittest  # noqa: E402
import uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import httpx  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from jose import jwt  # noqa: E402

from app.api.v1.endpoints import auth, gala_chat  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.schemas.user import UserCreate  # noqa: E402
from app.services import web_search  # noqa: E402


def _settings(**overrides):
    return SimpleNamespace(**{**get_settings().model_dump(), **overrides})


class TavilyClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_basic_search_with_bearer_key(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "query": "tattoo aftercare",
                    "answer": "Keep it clean.",
                    "results": [
                        {"title": "Aftercare", "url": "https://example.org/a", "content": "x" * 900, "score": 0.9}
                    ],
                    "request_id": "req-1",
                    "response_time": 0.8,
                },
            )

        real_client = httpx.AsyncClient
        with (
            patch.object(web_search, "get_settings", return_value=_settings(TAVILY_API_KEY="tvly-test")),
            patch.object(
                web_search.httpx,
                "AsyncClient",
                lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
            ),
        ):
            found = await web_search.tavily_search("tattoo aftercare", topic="bogus", max_results=50)

        self.assertEqual(seen["url"], "https://api.tavily.com/search")
        self.assertEqual(seen["auth"], "Bearer tvly-test")
        self.assertEqual(seen["body"]["search_depth"], "basic")
        self.assertEqual(seen["body"]["topic"], "general")
        self.assertEqual(seen["body"]["max_results"], web_search.MAX_RESULTS)
        self.assertEqual(found.request_id, "req-1")
        self.assertEqual(len(found.results[0]["snippet"]), web_search.SNIPPET_CHARS)

    async def test_http_error_raises_safe_message(self):
        real_client = httpx.AsyncClient
        with (
            patch.object(web_search, "get_settings", return_value=_settings(TAVILY_API_KEY="tvly-test")),
            patch.object(
                web_search.httpx,
                "AsyncClient",
                lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(401)), **kw),
            ),
            self.assertRaisesRegex(web_search.WebSearchError, "HTTP 401"),
        ):
            await web_search.tavily_search("q")

    async def test_missing_key_refuses(self):
        with (
            patch.object(web_search, "get_settings", return_value=_settings(TAVILY_API_KEY="")),
            self.assertRaises(web_search.WebSearchError),
        ):
            await web_search.tavily_search("q")


class WebSearchToolTests(unittest.IsolatedAsyncioTestCase):
    def test_offered_to_every_agent_and_role_only_when_configured(self):
        with patch.object(gala_chat, "web_search_configured", return_value=True):
            for agent_id in gala_chat.AGENT_PROFILES:
                for role in ("collector", "artist", "founder"):
                    self.assertTrue(gala_chat.tool_allowed_for_role(agent_id, "web_search", role), (agent_id, role))
            names = {tool["function"]["name"] for tool in gala_chat._openai_tools("gala", "collector")}
            self.assertIn("web_search", names)
        with patch.object(gala_chat, "web_search_configured", return_value=False):
            names = {tool["function"]["name"] for tool in gala_chat._openai_tools("gala", "collector")}
            self.assertNotIn("web_search", names)

    async def test_execute_returns_cited_text_and_card(self):
        found = web_search.WebSearchResult(
            query="q",
            answer="A",
            results=[{"title": "T", "url": "https://example.org", "snippet": "S", "score": 1, "published_date": None}],
            request_id="req-2",
            response_time=0.5,
        )
        user = SimpleNamespace(id="u", role="collector", email="c@example.org")
        with (
            patch.object(gala_chat, "web_search_configured", return_value=True),
            patch.object(gala_chat, "tavily_search", AsyncMock(return_value=found)),
            patch.object(gala_chat, "_monthly_web_searches_used", AsyncMock(return_value=0)),
            patch.object(gala_chat, "log_usage", AsyncMock()),
        ):
            text, components = await gala_chat.execute_tool("web_search", {"query": "q"}, user, db=None)
        self.assertIn("https://example.org", text)
        self.assertEqual(components[0]["type"], "web-results")
        self.assertEqual(components[0]["data"]["request_id"], "req-2")


class WebSearchCapTests(unittest.IsolatedAsyncioTestCase):
    def _found(self):
        return web_search.WebSearchResult(
            query="q",
            answer="",
            results=[{"title": "T", "url": "https://example.org", "snippet": "S", "score": 1, "published_date": None}],
            request_id="r",
            response_time=0.1,
        )

    async def test_client_over_monthly_cap_is_refused_before_tavily(self):
        user = SimpleNamespace(id=uuid.uuid4(), role="collector", email="c@example.org")
        search = AsyncMock(return_value=self._found())
        with (
            patch.object(gala_chat, "web_search_configured", return_value=True),
            patch.object(gala_chat, "get_settings", return_value=_settings(TAVILY_MONTHLY_SEARCHES_PER_ACCOUNT=3)),
            patch.object(gala_chat, "_monthly_web_searches_used", AsyncMock(return_value=3)),
            patch.object(gala_chat, "tavily_search", search),
            self.assertRaisesRegex(web_search.WebSearchError, "3 web searches"),
        ):
            await gala_chat.execute_tool("web_search", {"query": "q"}, user, db=None)
        search.assert_not_awaited()

    async def test_each_search_is_logged_for_the_cap(self):
        user = SimpleNamespace(id=uuid.uuid4(), role="collector", email="c@example.org")
        log = AsyncMock()
        with (
            patch.object(gala_chat, "web_search_configured", return_value=True),
            patch.object(gala_chat, "_monthly_web_searches_used", AsyncMock(return_value=0)),
            patch.object(gala_chat, "tavily_search", AsyncMock(return_value=self._found())),
            patch.object(gala_chat, "log_usage", log),
        ):
            await gala_chat.execute_tool("web_search", {"query": "q"}, user, db="db")
        log.assert_awaited_once_with("db", gala_chat.WEB_SEARCH_USAGE_ENDPOINT, 0, 0, user.id)


class DemoAccountExpiryTests(unittest.TestCase):
    def test_instant_sign_up_accounts_lose_ai_after_the_date(self):
        from app.services.ai_routing import demo_account_expired

        demo = SimpleNamespace(password_change_method="signup")
        staff = SimpleNamespace(password_change_method="change")
        self.assertTrue(demo_account_expired(_settings(OPEN_SIGNUP_UNTIL="2020-01-01"), demo))
        self.assertFalse(demo_account_expired(_settings(OPEN_SIGNUP_UNTIL="2999-01-01"), demo))
        self.assertFalse(demo_account_expired(_settings(OPEN_SIGNUP_UNTIL=""), demo))
        self.assertTrue(demo_account_expired(_settings(OPEN_SIGNUP_UNTIL="soon"), demo))
        self.assertFalse(demo_account_expired(_settings(OPEN_SIGNUP_UNTIL="2020-01-01"), staff))


class _FakeDb:
    def __init__(self, existing_signups=0):
        self.added = []
        self.existing_signups = existing_signups

    def add(self, row):
        self.added.append(row)

    async def scalar(self, _statement):
        return self.existing_signups

    async def flush(self):
        # The database assigns the primary key on flush.
        for row in self.added:
            if row.id is None:
                row.id = uuid.uuid4()


SIGNUP_ON = {
    "OPEN_SIGNUP_ENABLED": True,
    "OPEN_SIGNUP_CODE": "NEBIUS-JUDGE",
    "OPEN_SIGNUP_UNTIL": "",
    "OPEN_SIGNUP_MAX_ACCOUNTS": 200,
}


class InstantSignupTests(unittest.IsolatedAsyncioTestCase):
    def _body(self, code="NEBIUS-JUDGE"):
        return UserCreate(email="judge@example.org", password="correct-horse-9", name="Judge", signup_code=code)

    async def _signup(self, db, body, existing=None, **overrides):
        with patch.object(auth, "get_settings", return_value=_settings(**{**SIGNUP_ON, **overrides})):
            return await auth._instant_signup(db, body, "judge@example.org", existing)

    async def test_creates_verified_collector_with_chosen_password_and_token(self):
        db = _FakeDb()
        result = await self._signup(db, self._body())
        user = db.added[0]
        self.assertEqual(user.role, "collector")
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(auth.verify_password("correct-horse-9", user.password_hash))
        self.assertFalse(result.verification_required)
        claims = jwt.get_unverified_claims(result.access_token)
        self.assertEqual(claims["sub"], str(user.id))

    async def test_existing_email_is_never_taken_over(self):
        with self.assertRaises(HTTPException) as caught:
            await self._signup(_FakeDb(), self._body(), existing=SimpleNamespace(role="collector"))
        self.assertEqual(caught.exception.status_code, 409)

    async def test_invite_code_is_required_when_set(self):
        for code in (None, "wrong"):
            with self.assertRaises(HTTPException) as caught:
                await self._signup(_FakeDb(), self._body(code), OPEN_SIGNUP_CODE="NEBIUS-JUDGE")
            self.assertEqual(caught.exception.status_code, 403)
        db = _FakeDb()
        await self._signup(db, self._body(" NEBIUS-JUDGE "), OPEN_SIGNUP_CODE="NEBIUS-JUDGE")
        self.assertEqual(len(db.added), 1)

    async def test_account_cap_stops_sign_up(self):
        db = _FakeDb(existing_signups=3)
        with self.assertRaises(HTTPException) as caught:
            await self._signup(db, self._body(), OPEN_SIGNUP_MAX_ACCOUNTS=3)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(db.added, [])

    def test_expiry_closes_instant_sign_up(self):
        self.assertTrue(auth.instant_signup_open(_settings(**SIGNUP_ON)))
        self.assertTrue(auth.instant_signup_open(_settings(**{**SIGNUP_ON, "OPEN_SIGNUP_UNTIL": "2999-01-01"})))
        self.assertFalse(auth.instant_signup_open(_settings(**{**SIGNUP_ON, "OPEN_SIGNUP_UNTIL": "2020-01-01"})))
        self.assertFalse(auth.instant_signup_open(_settings(**{**SIGNUP_ON, "OPEN_SIGNUP_UNTIL": "soon"})))
        self.assertFalse(auth.instant_signup_open(_settings(**{**SIGNUP_ON, "OPEN_SIGNUP_ENABLED": False})))
        # A blank code keeps instant sign-up closed instead of opening it to everyone.
        self.assertFalse(auth.instant_signup_open(_settings(**{**SIGNUP_ON, "OPEN_SIGNUP_CODE": "  "})))
        self.assertFalse(auth.instant_signup_open(_settings(**{**SIGNUP_ON, "OPEN_SIGNUP_CODE": "short"})))

    async def test_blank_code_refuses_even_if_reached(self):
        with self.assertRaises(HTTPException) as caught:
            await self._signup(_FakeDb(), self._body(""), OPEN_SIGNUP_CODE="")
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            await self._signup(_FakeDb(), self._body("short"), OPEN_SIGNUP_CODE="short")
        self.assertEqual(caught.exception.status_code, 403)

    async def test_options_advertise_instant_signup_and_code_only_when_open(self):
        with patch.object(
            auth, "get_settings", return_value=_settings(**{**SIGNUP_ON, "OPEN_SIGNUP_CODE": "NEBIUS-JUDGE"})
        ):
            options = await auth.auth_options()
        self.assertTrue(options["registration"])
        self.assertTrue(options["instant_signup"])
        self.assertTrue(options["signup_code_required"])
        with patch.object(auth, "get_settings", return_value=_settings(**{**SIGNUP_ON, "OPEN_SIGNUP_ENABLED": False})):
            options = await auth.auth_options()
        self.assertFalse(options["instant_signup"])
        self.assertFalse(options["signup_code_required"])


class _DemoDb:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.committed = False

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.existing)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True


DEMO = {
    "DEMO_ACCOUNT_EMAIL": "Judge@Ryozan.App",
    "DEMO_ACCOUNT_PASSWORD": "shared-demo-password-1",
    "DEMO_ACCOUNT_MONTHLY_AI_TURNS": 300,
}


class DemoAccountTests(unittest.IsolatedAsyncioTestCase):
    async def _ensure(self, db, **overrides):
        from app.services import demo_account

        with patch.object(demo_account, "get_settings", return_value=_settings(**{**DEMO, **overrides})):
            return await demo_account.ensure_demo_account(db)

    async def test_not_configured_or_short_password_creates_nothing(self):
        db = _DemoDb()
        self.assertEqual(await self._ensure(db, DEMO_ACCOUNT_EMAIL=""), "not configured")
        self.assertIn("12 or more", await self._ensure(db, DEMO_ACCOUNT_PASSWORD="short"))
        self.assertEqual(db.added, [])

    async def test_creates_verified_client_with_shared_caps(self):
        db = _DemoDb()
        self.assertEqual(await self._ensure(db), "created")
        user = db.added[0]
        self.assertEqual(
            (user.email, user.role, user.password_change_method), ("judge@ryozan.app", "collector", "signup")
        )
        self.assertIsNotNone(user.email_verified_at)
        self.assertEqual(user.ai_monthly_message_cap, 300)
        self.assertTrue(auth.verify_password("shared-demo-password-1", user.password_hash))
        self.assertTrue(db.committed)

    async def test_existing_account_gets_the_configured_password_back(self):
        existing = SimpleNamespace(
            email="judge@ryozan.app",
            role="collector",
            password_hash=auth.hash_password("someone-changed-it"),
            email_verified_at=None,
        )
        self.assertEqual(await self._ensure(_DemoDb(existing)), "password updated")
        self.assertTrue(auth.verify_password("shared-demo-password-1", existing.password_hash))
        self.assertEqual(await self._ensure(_DemoDb(existing)), "verified")

    async def test_staff_account_at_the_demo_address_is_left_alone(self):
        staff = SimpleNamespace(email="judge@ryozan.app", role="founder", password_hash="h")
        self.assertIn("founder", await self._ensure(_DemoDb(staff)))
        self.assertEqual(staff.password_hash, "h")

    async def test_password_change_and_deletion_are_refused(self):
        from app.services import demo_account

        demo = SimpleNamespace(email="judge@ryozan.app")
        with patch.object(demo_account, "get_settings", return_value=_settings(**DEMO)):
            self.assertTrue(demo_account.is_demo_account(demo))
            self.assertFalse(demo_account.is_demo_account(SimpleNamespace(email="other@example.org")))
            with self.assertRaises(HTTPException) as caught:
                await auth.change_password(body=SimpleNamespace(), db=None, current_user=demo)
            self.assertEqual(caught.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
