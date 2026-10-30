"""
Courier inbound email threading tests.

This file is intentionally separate from auth email normalization, Vercel
routing, and Courier proxy tests because inbound webhook archiving is its own
endpoint contract. Keeping it focused makes reply-address threading regressions
visible without coupling them to unrelated auth, deployment, or proxy behavior.
"""

import base64
import json
import os
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from app.api.v1.endpoints import email_inbound
from app.core.config import SHARED_SECRET_FIELDS, Settings
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.user import BTTCOUser
from app.services import conversation_threads

# Non-development settings refuse to load without private shared secrets.
NON_DEV_SHARED_SECRETS = {name: f"fixture-{name.lower()}-0123456789abcdef0123456789" for name in SHARED_SECRET_FIELDS}


# Svix-format test secret built from an obviously fake string; never a real key.
TEST_WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"ryozan-test-only-not-a-real-secret").decode()

class _ScalarValues:
    def __init__(self, value=None):
        self.value = value

    def first(self):
        return self.value

    def all(self):
        return [] if self.value is None else [self.value]


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        # The inbound sender lookup reads .scalars().first() so duplicate rows
        # sharing an email cannot raise MultipleResultsFound and 500 the webhook.
        return _ScalarValues(self.value)


class _RowsResult:
    def __init__(self, row=None):
        self.row = row

    def one_or_none(self):
        return self.row

    def all(self):
        return self.row or []


class _FakeSession:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.flushes = 0
        self.rollbacks = 0

    async def execute(self, _stmt):
        _stmt.compile(dialect=postgresql.dialect())
        if not self.results:
            raise AssertionError("unexpected database execute")
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def rollback(self):
        self.rollbacks += 1
        self.added.clear()

    async def flush(self):
        self.flushes += 1
        now = datetime.now(UTC)
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()
            if isinstance(value, Conversation):
                if getattr(value, "created_at", None) is None:
                    value.created_at = now
                if getattr(value, "updated_at", None) is None:
                    value.updated_at = now


def _webhook_request(payload: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/v1/webhooks/email-inbound",
            "raw_path": b"/api/v1/webhooks/email-inbound",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("api.ryozan.app", 443),
        },
        receive,
    )


class CourierInboundEmailTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_event_fetches_body_before_archiving_and_forwarding(self):
        email_id = str(uuid.uuid4())
        data = {
            "email_id": email_id,
            "from": "client@example.test",
            "to": ["info@ryozan.app"],
            "subject": "Synthetic reply",
        }
        received = {"id": email_id, "text": "The proposed date works.", "html": None}
        client = AsyncMock()
        client.get.return_value = httpx.Response(
            200, json=received, request=httpx.Request("GET", "https://api.resend.com/fixture")
        )
        settings = Settings(
            _env_file=None, APP_ENV="test", DEBUG=False, RESEND_API_KEY="re_synthetic", RESEND_INBOUND_WEBHOOK_SECRET=""
        )
        archive, forward = AsyncMock(return_value="synthetic-thread"), AsyncMock()
        with (
            patch.object(email_inbound, "get_settings", return_value=settings),
            patch.object(email_inbound.httpx, "AsyncClient") as factory,
            patch.object(
                email_inbound,
                "_resolve_destination",
                AsyncMock(return_value=email_inbound.InboundRoute("artist@example.test", uuid.uuid4(), "info")),
            ),
            patch.object(email_inbound, "_archive_email_to_courier", archive),
            patch.object(email_inbound, "_forward_email", forward),
        ):
            factory.return_value.__aenter__.return_value = client
            result = await email_inbound.handle_inbound_email(
                _webhook_request(json.dumps({"type": "email.received", "data": data}).encode()), AsyncMock()
            )
        self.assertEqual(result["status"], "forwarded")
        self.assertEqual(archive.await_args.args[0]["text"], received["text"])
        self.assertEqual(forward.await_args.args[0]["text"], received["text"])
        self.assertEqual(client.get.await_args.args[0], f"https://api.resend.com/emails/receiving/{email_id}")

    async def test_failed_body_fetch_is_retryable_and_never_acknowledged_as_empty(self):
        client = AsyncMock()
        client.get.side_effect = httpx.ReadTimeout("synthetic timeout")
        with patch.object(email_inbound.httpx, "AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            with self.assertRaises(HTTPException) as raised:
                await email_inbound._retrieve_received_body({"email_id": str(uuid.uuid4())}, "re_synthetic")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("synthetic timeout", raised.exception.detail)

    async def test_received_body_rejects_mismatched_identity_and_invalid_id(self):
        client = AsyncMock()
        client.get.return_value = httpx.Response(
            200,
            json={"id": str(uuid.uuid4()), "text": "Wrong message"},
            request=httpx.Request("GET", "https://api.resend.com/fixture"),
        )
        with patch.object(email_inbound.httpx, "AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            with self.assertRaises(HTTPException) as wrong:
                await email_inbound._retrieve_received_body({"email_id": str(uuid.uuid4())}, "re_synthetic")
            self.assertEqual(wrong.exception.status_code, 503)
            client.get.reset_mock()
            with self.assertRaises(HTTPException) as invalid:
                await email_inbound._retrieve_received_body({"email_id": "../../unapproved"}, "re_synthetic")
            self.assertEqual(invalid.exception.status_code, 400)
            client.get.assert_not_awaited()

    async def test_inline_legacy_bodies_need_no_provider_fetch(self):
        data = {"text": "Synthetic inline body"}
        with patch.object(email_inbound.httpx, "AsyncClient") as factory:
            self.assertEqual(await email_inbound._retrieve_received_body(data, "re_synthetic"), data)
            factory.assert_not_called()

    async def test_attachment_only_email_is_explicitly_visible(self):
        email_id = str(uuid.uuid4())
        client = AsyncMock()
        client.get.return_value = httpx.Response(
            200,
            json={"id": email_id, "attachments": [{"id": "synthetic-file"}], "text": None, "html": None},
            request=httpx.Request("GET", "https://api.resend.com/fixture"),
        )
        with patch.object(email_inbound.httpx, "AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            result = await email_inbound._retrieve_received_body({"email_id": email_id}, "re_synthetic")
        self.assertIn("1 attachment(s)", result["text"])

    async def test_missing_secret_rejects_non_local_webhooks_before_database_or_provider_calls(self):
        payload = json.dumps(
            {
                "type": "email.received",
                "data": {
                    "from": "Client <client@example.com>",
                    "to": ["info@ryozan.app"],
                    "subject": "Booking",
                    "text": "I would like to book.",
                },
            }
        ).encode()

        for app_env in ("production", "staging"):
            with self.subTest(app_env=app_env):
                db = AsyncMock()
                forward = AsyncMock()
                archive = AsyncMock()
                request = _webhook_request(payload)
                settings = Settings(
                    _env_file=None,
                    APP_ENV=app_env,
                    DEBUG=False,
                    RESEND_API_KEY="re_test",
                    RESEND_INBOUND_WEBHOOK_SECRET="",
                    **NON_DEV_SHARED_SECRETS,
                )
                with (
                    patch.object(email_inbound, "get_settings", return_value=settings),
                    patch.object(email_inbound, "_forward_email", forward),
                    patch.object(email_inbound, "_archive_email_to_courier", archive),
                    self.assertRaises(HTTPException) as raised,
                ):
                    await email_inbound.handle_inbound_email(request, db)

                self.assertEqual(raised.exception.status_code, 503)
                self.assertEqual(raised.exception.detail, "Inbound email webhook signing secret not configured")
                db.execute.assert_not_awaited()
                db.flush.assert_not_awaited()
                forward.assert_not_awaited()
                archive.assert_not_awaited()

    async def test_missing_secret_remains_explicitly_usable_in_test_environment(self):
        request = _webhook_request(b'{"type":"verification"}')
        settings = Settings(
            _env_file=None,
            APP_ENV="test",
            DEBUG=False,
            RESEND_API_KEY="re_test",
            RESEND_INBOUND_WEBHOOK_SECRET="",
        )
        with patch.object(email_inbound, "get_settings", return_value=settings):
            result = await email_inbound.handle_inbound_email(request, AsyncMock())

        self.assertEqual(result, {"status": "ignored", "event": "verification"})

    async def test_archive_reply_uses_existing_thread_and_known_sender(self):
        owner_user_id = uuid.uuid4()
        sender = BTTCOUser(
            id=uuid.uuid4(),
            email="client@example.com",
            password_hash="placeholder",
            name="Client",
            role="collector",
        )
        sender_user_id = sender.id
        conversation_id = uuid.uuid4()
        conv = Conversation(
            id=conversation_id,
            name="Client thread",
            is_group=False,
            created_by=owner_user_id,
        )
        db = _FakeSession(
            [
                _ScalarResult(conv),
                _ScalarResult(uuid.uuid4()),
            ]
        )

        with patch.object(email_inbound, "find_user_by_email", AsyncMock(return_value=sender)):
            archived_id = await email_inbound._archive_email_to_courier(
                {
                    "from": "Client Name <client@example.com>",
                    "to": [f"convo-{conversation_id}@ryozan.app"],
                    "subject": "Re: booking",
                    "text": "That date works for me.",
                },
                owner_user_id,
                f"convo-{conversation_id}",
                db,
                conversation_id,
                sender_user_id,
            )

        message = next(value for value in db.added if isinstance(value, Message))
        participants = [value for value in db.added if isinstance(value, ConversationParticipant)]
        metadata = json.loads(message.metadata_json or "{}")

        self.assertEqual(archived_id, str(conversation_id))
        self.assertEqual(message.conversation_id, conversation_id)
        self.assertEqual(message.sender_id, sender_user_id)
        self.assertEqual(message.message_type, "email")
        self.assertEqual(metadata["direction"], "inbound")
        self.assertEqual(metadata["from_email"], "client@example.com")
        self.assertTrue(metadata["threaded_from_reply_to"])
        self.assertEqual(metadata["threaded_conversation_id"], str(conversation_id))
        self.assertTrue(metadata["sender_resolved_from_email"])
        self.assertEqual(participants, [])


class SenderAwareRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_artist_reply_targets_collector_instead_of_self(self):
        artist_id, collector_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        rows = _RowsResult(("artist@example.test", artist_id))
        rows.all = lambda: [
            ("artist@example.test", artist_id, "artist", "owner"),
            ("collector@example.test", collector_id, "collector", "member"),
        ]
        route = await email_inbound._resolve_destination(
            [f"convo-{conversation_id}@ryozan.app"],
            _FakeSession([rows]),
            "Artist <ARTIST@example.test>",
        )
        self.assertEqual(route.destination, "collector@example.test")
        self.assertEqual(route.sender_user_id, artist_id)
        self.assertEqual(route.owner_user_id, collector_id)

    async def test_collector_reply_targets_artist(self):
        artist_id, collector_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        rows = _RowsResult(("artist@example.test", artist_id))
        rows.all = lambda: [
            ("artist@example.test", artist_id, "artist", "owner"),
            ("collector@example.test", collector_id, "collector", "member"),
        ]
        route = await email_inbound._resolve_destination(
            ["info@ryozan.app", f"convo-{conversation_id}@ryozan.app"],
            _FakeSession([rows]),
            "collector@example.test",
        )
        self.assertEqual(route.destination, "artist@example.test")
        self.assertEqual(route.sender_user_id, collector_id)

    async def test_unknown_sender_cannot_enter_existing_thread(self):
        rows = _RowsResult(("artist@example.test", uuid.uuid4()))
        rows.all = lambda: [("artist@example.test", uuid.uuid4(), "artist", "owner")]
        db = _FakeSession([rows])
        with self.assertRaises(HTTPException) as raised:
            await email_inbound._resolve_destination(
                [f"convo-{uuid.uuid4()}@ryozan.app"],
                db,
                "stranger@example.test",
            )
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(db.added, [])
        self.assertEqual(db.flushes, 0)

    async def test_forward_loop_is_ignored_before_database_or_send(self):
        settings = Settings(
            _env_file=None, APP_ENV="test", RESEND_API_KEY="re_synthetic", RESEND_INBOUND_WEBHOOK_SECRET=""
        )
        payload = {
            "type": "email.received",
            "data": {
                "from": "artist@example.test",
                "to": [f"convo-{uuid.uuid4()}@ryozan.app"],
                "text": "Synthetic loop",
                "headers": {"X-BTTCO-Courier-Forward": "1"},
            },
        }
        db, forward = AsyncMock(), AsyncMock()
        with (
            patch.object(email_inbound, "get_settings", return_value=settings),
            patch.object(email_inbound, "_forward_email", forward),
        ):
            result = await email_inbound.handle_inbound_email(_webhook_request(json.dumps(payload).encode()), db)
        self.assertEqual(result, {"status": "ignored", "reason": "automatic_or_forwarded_email"})
        db.execute.assert_not_awaited()
        forward.assert_not_awaited()


class ReplySafetyAndExchangeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conversation_id = uuid.uuid4()
        self.artist_id, self.collector_id = uuid.uuid4(), uuid.uuid4()
        self.people = [
            ("artist@example.test", self.artist_id, "artist", "owner"),
            ("collector@example.test", self.collector_id, "collector", "member"),
        ]
        self.alias = f"convo-{self.conversation_id}@ryozan.app"
        self.settings = Settings(
            _env_file=None,
            APP_ENV="test",
            RESEND_API_KEY="re_synthetic",
            RESEND_INBOUND_WEBHOOK_SECRET="",
        )

    def payload(self, sender="artist@example.test", **changes):
        return {
            "type": "email.received",
            "data": {
                "from": sender,
                "to": [self.alias],
                "text": "Synthetic exchange",
                "subject": "Fixture",
                "authentication": {"dmarc": "pass"},
                **changes,
            },
        }

    async def invoke(self, payload, db):
        with patch.object(email_inbound, "get_settings", return_value=self.settings):
            return await email_inbound.handle_inbound_email(_webhook_request(json.dumps(payload).encode()), db)

    async def test_two_direction_exchange_archives_real_sender_and_forwards_one_peer(self):
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200, json={"id": "synthetic"}, request=httpx.Request("POST", "https://api.resend.com/emails")
        )
        for sender, sender_id, recipient in [
            ("artist@example.test", self.artist_id, "collector@example.test"),
            ("collector@example.test", self.collector_id, "artist@example.test"),
        ]:
            with self.subTest(sender=sender):
                conv = Conversation(id=self.conversation_id, created_by=self.artist_id, is_group=False)
                db = _FakeSession([_RowsResult(self.people), _ScalarResult(conv), _ScalarResult(uuid.uuid4())])
                with (
                    patch.object(email_inbound.httpx, "AsyncClient") as factory,
                    patch.object(email_inbound, "find_user_by_email", AsyncMock()) as lookup,
                ):
                    factory.return_value.__aenter__.return_value = client
                    result = await self.invoke(self.payload(sender), db)
                lookup.assert_not_awaited()
                self.assertEqual(result["to"], recipient)
                self.assertEqual(result["courier_conversation_id"], str(self.conversation_id))
                self.assertEqual(len(db.added), 1)
                message = db.added[0]
                self.assertIsInstance(message, Message)
                self.assertEqual(message.sender_id, sender_id)
                self.assertEqual(message.conversation_id, self.conversation_id)
                request = client.post.await_args.kwargs["json"]
                self.assertEqual(request["to"], [recipient])
                self.assertEqual(request["reply_to"], self.alias)
                self.assertEqual(request["headers"]["Auto-Submitted"], "auto-generated")
                self.assertEqual(request["headers"]["X-BTTCO-Courier-Forward"], "1")
        self.assertEqual(client.post.await_count, 2)

    async def test_unknown_sender_rejected_without_archive_or_forward(self):
        db = _FakeSession([_RowsResult(self.people)])
        with (
            patch.object(email_inbound, "_forward_email", AsyncMock()) as forward,
            self.assertRaises(HTTPException) as raised,
        ):
            await self.invoke(self.payload("unrecognized@example.test"), db)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(db.added, [])
        self.assertEqual(db.flushes, 0)
        forward.assert_not_awaited()

    async def test_ambiguous_sender_or_collector_fails_closed(self):
        cases = [
            self.people + [("artist@example.test", uuid.uuid4(), "admin", "member")],
            self.people + [("other@example.test", uuid.uuid4(), "collector", "member")],
            self.people + [("collector@example.test", uuid.uuid4(), "assistant", "member")],
            [self.people[0]],
            [],
        ]
        for people in cases:
            with self.subTest(people=people), self.assertRaises(HTTPException) as raised:
                await email_inbound._resolve_destination(
                    [self.alias], _FakeSession([_RowsResult(people)]), "artist@example.test"
                )
            self.assertIn(raised.exception.status_code, (403, 409))

    async def test_general_inquiry_staff_reply_targets_collector_and_return_uses_staff_priority(self):
        founder_id = uuid.uuid4()
        people = self.people + [
            ("founder@example.test", founder_id, "founder", "member"),
            ("admin@example.test", uuid.uuid4(), "admin", "owner"),
        ]
        for sender, expected in [
            ("founder@example.test", "collector@example.test"),
            ("collector@example.test", "founder@example.test"),
        ]:
            route = await email_inbound._resolve_destination([self.alias], _FakeSession([_RowsResult(people)]), sender)
            self.assertEqual(route.destination, expected)

    async def test_bad_or_multiple_conversation_aliases_never_fall_back(self):
        cases = [
            ["convo-not-a-uuid@ryozan.app"],
            [self.alias, f"convo-{uuid.uuid4()}@ryozan.app"],
            [self.alias.replace("ryozan.app", "unapproved.example.test")],
        ]
        for addresses in cases:
            with self.subTest(addresses=addresses), self.assertRaises(HTTPException) as raised:
                await email_inbound._resolve_destination(addresses, _FakeSession(), "artist@example.test")
            self.assertEqual(raised.exception.status_code, 400)

    async def test_reserved_inbox_route_is_retained(self):
        db = _FakeSession([_ScalarResult(None), _ScalarResult(self.artist_id)])
        route = await email_inbound._resolve_destination(["info@ryozan.app"], db, "new@example.test")
        self.assertEqual(route.destination, email_inbound.FALLBACK_ROUTES["info"])
        self.assertIsNone(route.conversation_id)

    async def test_auto_replies_and_forward_markers_are_not_archived(self):
        for headers in ({"auto-submitted": "auto-replied"}, {"x-bttco-courier-forward": "1"}):
            db = AsyncMock()
            with patch.object(email_inbound, "_forward_email", AsyncMock()) as forward:
                result = await self.invoke(self.payload(headers=headers), db)
            self.assertEqual(result["status"], "ignored")
            db.execute.assert_not_awaited()
            forward.assert_not_awaited()

    async def test_failed_or_absent_sender_auth_cannot_be_overridden_by_email_headers(self):
        for authentication in (None, {}, {"dmarc": "fail"}, {"spf": "pass", "dkim": "pass"}):
            db = AsyncMock()
            with self.assertRaises(HTTPException) as raised:
                await self.invoke(
                    self.payload(authentication=authentication, headers={"Authentication-Results": "dmarc=pass"}), db
                )
            self.assertEqual(raised.exception.status_code, 403)
            db.execute.assert_not_awaited()

    async def test_provider_headers_and_auth_replace_inline_claims(self):
        email_id = str(uuid.uuid4())
        received = {
            "id": email_id,
            "text": "Provider body",
            "headers": {"Auto-Submitted": "auto-replied"},
            "authentication": {"dmarc": "fail"},
        }
        client = AsyncMock()
        client.get.return_value = httpx.Response(
            200, json=received, request=httpx.Request("GET", "https://api.resend.com/fixture")
        )
        with patch.object(email_inbound.httpx, "AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            result = await email_inbound._retrieve_received_body(
                {"email_id": email_id, "text": "Inline body", "authentication": {"dmarc": "pass"}}, "re_synthetic"
            )
        self.assertEqual(result["headers"], received["headers"])
        self.assertEqual(result["authentication"], {"dmarc": "fail"})
        self.assertTrue(email_inbound._is_automatic_email(result))

    async def test_removed_sender_is_not_readded_to_conversation(self):
        conv = Conversation(id=self.conversation_id, created_by=self.artist_id, is_group=False)
        db = _FakeSession([_ScalarResult(conv), _ScalarResult(None)])
        with self.assertRaises(HTTPException) as raised:
            await email_inbound._archive_email_to_courier(
                self.payload()["data"], self.collector_id, "convo-fixture", db, self.conversation_id, self.artist_id
            )
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(db.added, [])
        self.assertEqual(db.flushes, 0)

    async def test_self_forward_is_rejected_before_archive(self):
        route = email_inbound.InboundRoute(
            "artist@example.test", self.artist_id, "convo-fixture", self.conversation_id, self.artist_id
        )
        db = AsyncMock()
        with (
            patch.object(email_inbound, "_resolve_destination", AsyncMock(return_value=route)),
            self.assertRaises(HTTPException) as raised,
        ):
            await self.invoke(self.payload(), db)
        self.assertEqual(raised.exception.status_code, 409)
        db.add.assert_not_called()

    async def test_provider_failure_returns_retryable_error(self):
        db = _FakeSession(
            [
                _RowsResult(self.people),
                _ScalarResult(Conversation(id=self.conversation_id)),
                _ScalarResult(uuid.uuid4()),
            ]
        )
        with (
            patch.object(email_inbound, "_forward_email", AsyncMock(side_effect=httpx.ReadTimeout("synthetic"))),
            self.assertRaises(HTTPException) as raised,
        ):
            await self.invoke(self.payload(), db)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("synthetic", raised.exception.detail)
        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(db.added, [])

    async def test_shared_reply_domain_controls_emission_and_recognition(self):
        settings = Settings(_env_file=None, COURIER_REPLY_DOMAIN="reply.ryozan.app")
        with (
            patch.object(conversation_threads, "get_settings", return_value=settings),
            patch.object(email_inbound, "get_settings", return_value=settings),
        ):
            alias = conversation_threads.conversation_reply_to(self.conversation_id)
            self.assertEqual(alias, self.alias.replace("@ryozan.app", "@reply.ryozan.app"))
            for address in (alias, self.alias):
                route = await email_inbound._resolve_destination(
                    [address], _FakeSession([_RowsResult(self.people)]), "artist@example.test"
                )
                self.assertEqual(route.destination, "collector@example.test")
            self.assertIsNone(conversation_threads.conversation_reply_to(None))

    def test_reply_domain_rejects_header_injection_or_url(self):
        for value in (
            "https://reply.example.test",
            "x@y.example.test",
            "reply.example.test\nBcc: x",
            "",
            "-bad.example.test",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Settings(_env_file=None, COURIER_REPLY_DOMAIN=value)
        self.assertEqual(
            Settings(_env_file=None, COURIER_REPLY_DOMAIN="REPLY.example.test").COURIER_REPLY_DOMAIN,
            "reply.example.test",
        )

    def test_sender_parser_rejects_multiple_mailboxes_and_header_injection(self):
        for value in (
            "artist@example.test, stranger@example.test",
            ["artist@example.test", "stranger@example.test"],
            "artist@example.test\r\nBcc: stranger@example.test",
            "no-address",
        ):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                email_inbound._sender_address(value)
        self.assertEqual(
            email_inbound._sender_address('"Artist, Example" <ARTIST@example.test>'), "artist@example.test"
        )

    async def test_invalid_signature_never_reaches_provider_or_database(self):
        self.settings.RESEND_INBOUND_WEBHOOK_SECRET = TEST_WEBHOOK_SECRET
        self.settings.APP_ENV = "production"
        db = AsyncMock()
        with (
            patch.object(email_inbound, "_retrieve_received_body", AsyncMock()) as retrieve,
            self.assertRaises(HTTPException) as raised,
        ):
            await self.invoke(self.payload(), db)
        self.assertEqual(raised.exception.status_code, 400)
        retrieve.assert_not_awaited()
        db.execute.assert_not_awaited()

    async def test_signed_metadata_event_uses_provider_body_auth_and_headers(self):
        from svix.webhooks import Webhook

        self.settings.RESEND_INBOUND_WEBHOOK_SECRET = TEST_WEBHOOK_SECRET
        self.settings.APP_ENV = "production"
        email_id = str(uuid.uuid4())
        payload = self.payload(email_id=email_id)
        payload["data"].pop("text")
        payload["data"].pop("authentication")
        body = json.dumps(payload).encode()
        timestamp = datetime.now(UTC)
        signature = Webhook(self.settings.RESEND_INBOUND_WEBHOOK_SECRET).sign("msg_synthetic", timestamp, body.decode())
        request = _webhook_request(body)
        request.scope["headers"] = [
            (b"svix-id", b"msg_synthetic"),
            (b"svix-timestamp", str(int(timestamp.timestamp())).encode()),
            (b"svix-signature", signature.encode()),
        ]
        received = {
            "id": email_id,
            "text": "Synthetic signed reply",
            "headers": {},
            "authentication": {"dmarc": "pass"},
        }
        client = AsyncMock()
        client.get.return_value = httpx.Response(
            200, json=received, request=httpx.Request("GET", "https://api.resend.com/fixture")
        )
        client.post.return_value = httpx.Response(
            200, json={"id": "synthetic"}, request=httpx.Request("POST", "https://api.resend.com/emails")
        )
        db = _FakeSession(
            [
                _RowsResult(self.people),
                _ScalarResult(Conversation(id=self.conversation_id)),
                _ScalarResult(uuid.uuid4()),
            ]
        )
        with (
            patch.object(email_inbound, "get_settings", return_value=self.settings),
            patch.object(email_inbound.httpx, "AsyncClient") as factory,
        ):
            factory.return_value.__aenter__.return_value = client
            result = await email_inbound.handle_inbound_email(request, db)
        self.assertEqual(result["to"], "collector@example.test")
        self.assertEqual(len(db.added), 1)
        self.assertIn("Synthetic signed reply", db.added[0].content)
        client.get.assert_awaited_once()
        client.post.assert_awaited_once()

    async def test_courier_notification_worker_emits_configured_reply_domain(self):
        from app.api.v1.endpoints import conversations

        message = Message(
            id=uuid.uuid4(),
            conversation_id=self.conversation_id,
            sender_id=self.artist_id,
            content="Synthetic notification",
            message_type="text",
            created_at=datetime.now(UTC),
        )
        message.metadata_json = json.dumps(
            {
                "delivery_status": "queued",
                "notification_delivery": {
                    "sender_name": "Fixture artist",
                    "conversation_name": "Fixture",
                    "intents": [
                        {
                            "id": "email:fixture",
                            "channel": "email",
                            "to": "collector@example.test",
                            "recipient_user_id": str(self.collector_id),
                            "status": "queued",
                            "attempts": 0,
                        }
                    ],
                },
            }
        )
        db = AsyncMock()
        db.execute.return_value = _ScalarResult(message)
        db.get.return_value = message
        context = AsyncMock()
        context.__aenter__.return_value = db
        settings = Settings(_env_file=None, COURIER_REPLY_DOMAIN="reply.ryozan.app")
        with (
            patch.object(conversations, "async_session", return_value=context),
            patch.object(conversations, "send_email", AsyncMock(return_value=True)) as send,
            patch.object(conversation_threads, "get_settings", return_value=settings),
        ):
            delivered = await conversations.process_courier_notification_outbox_once(message_id=message.id)
        self.assertEqual(delivered, 1)
        send.assert_awaited_once()
        self.assertEqual(
            send.await_args.kwargs["reply_to"], self.alias.replace("@ryozan.app", "@reply.ryozan.app")
        )
        self.assertEqual(send.await_args.kwargs["to"], "collector@example.test")

    async def test_reserved_inbox_automatic_mail_is_still_archived_and_forwarded(self):
        route = email_inbound.InboundRoute("owner@example.test", self.artist_id, "info")
        with (
            patch.object(email_inbound, "_resolve_destination", AsyncMock(return_value=route)),
            patch.object(
                email_inbound, "_archive_email_to_courier", AsyncMock(return_value="fixture-thread")
            ) as archive,
            patch.object(email_inbound, "_forward_email", AsyncMock()) as forward,
        ):
            result = await self.invoke(
                self.payload(
                    "calendar@example.test",
                    to=["info@ryozan.app"],
                    headers={"Auto-Submitted": "auto-generated"},
                ),
                AsyncMock(),
            )
        self.assertEqual(result["status"], "forwarded")
        archive.assert_awaited_once()
        forward.assert_awaited_once()

    async def test_reserved_self_forward_archives_without_sending_or_retry_error(self):
        route = email_inbound.InboundRoute("artist@example.test", self.artist_id, "bookings")
        with (
            patch.object(email_inbound, "_resolve_destination", AsyncMock(return_value=route)),
            patch.object(
                email_inbound, "_archive_email_to_courier", AsyncMock(return_value="fixture-thread")
            ) as archive,
            patch.object(email_inbound, "_forward_email", AsyncMock()) as forward,
        ):
            result = await self.invoke(self.payload(to=["bookings@ryozan.app"]), AsyncMock())
        self.assertEqual(
            result,
            {"status": "archived", "reason": "self_forward_skipped", "courier_conversation_id": "fixture-thread"},
        )
        archive.assert_awaited_once()
        forward.assert_not_awaited()

    async def test_forward_marker_is_still_suppressed_on_reserved_inbox(self):
        db = AsyncMock()
        result = await self.invoke(
            self.payload(to=["info@ryozan.app"], headers={"X-BTTCO-Courier-Forward": "1"}), db
        )
        self.assertEqual(result["status"], "ignored")
        db.execute.assert_not_awaited()

    async def test_general_routing_retains_existing_sender_parsing(self):
        db = _FakeSession([_ScalarResult(None), _ScalarResult(self.artist_id)])
        route = await email_inbound._resolve_destination(["info@ryozan.app"], db, "malformed legacy sender")
        self.assertEqual(route.destination, email_inbound.FALLBACK_ROUTES["info"])

    async def test_bad_stored_recipient_reports_recipient_problem(self):
        people = [self.people[0], ("invalid recipient", self.collector_id, "collector", "member")]
        with self.assertRaises(HTTPException) as raised:
            await email_inbound._resolve_destination(
                [self.alias], _FakeSession([_RowsResult(people)]), "artist@example.test"
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "Conversation recipient email is invalid")

    async def test_forward_accepts_missing_subject_and_escapes_plain_text(self):
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200, json={"id": "synthetic"}, request=httpx.Request("POST", "https://api.resend.com/emails")
        )
        with patch.object(email_inbound.httpx, "AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            await email_inbound._forward_email(
                {"from": "artist@example.test", "subject": None, "text": "Literal <value>"},
                "collector@example.test",
                "re_synthetic",
                self.conversation_id,
            )
        sent = client.post.await_args.kwargs["json"]
        self.assertEqual(sent["subject"], "[Ryozan] (no subject)")
        self.assertIn("Literal &lt;value&gt;", sent["html"])


if __name__ == "__main__":
    unittest.main()
