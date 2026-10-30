"""Focused Portal identity, conversation, document, and POS authorization regressions.

Existing suites cover authentication, AI release gating, and Courier proxying.
This module is separate because it owns cross-role and cross-user authorization
boundaries that must fail before private data or provider operations are reached.
"""

import inspect
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api.v1.endpoints import ai_memories, conversations, documents, pos, users
from app.models.booking import Booking
from app.models.user import BTTCOUser
from app.schemas.message import ConversationCreate
from app.schemas.user import UserRoleUpdate


def _user(role: str, *, user_id=None) -> BTTCOUser:
    return BTTCOUser(
        id=user_id or uuid.uuid4(),
        email=f"{role}-{uuid.uuid4()}@example.test",
        name=role.title(),
        password_hash="test-hash",
        role=role,
    )


class _Result:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = list(rows or [])

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, *results):
        self.results = list(results)
        self.execute_count = 0
        self.commit_count = 0
        self.refreshed = []

    async def execute(self, _statement):
        self.execute_count += 1
        if not self.results:
            raise AssertionError("unexpected database execute")
        return self.results.pop(0)

    async def commit(self):
        self.commit_count += 1

    async def refresh(self, value):
        self.refreshed.append(value)


class RoleChangeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_cannot_promote_self_to_founder(self):
        admin = _user("admin")
        db = _Session()

        with self.assertRaises(HTTPException) as ctx:
            await users.change_user_role(admin.id, UserRoleUpdate(role="founder"), db, admin)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(db.execute_count, 0)
        self.assertEqual(db.commit_count, 0)

    async def test_admin_cannot_assign_founder(self):
        admin = _user("admin")
        artist = _user("artist")
        db = _Session(_Result(scalar=artist))

        with self.assertRaises(HTTPException) as ctx:
            await users.change_user_role(artist.id, UserRoleUpdate(role="founder"), db, admin)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(artist.role, "artist")
        self.assertEqual(db.commit_count, 0)

    async def test_admin_cannot_remove_founder(self):
        admin = _user("admin")
        founder = _user("founder")
        db = _Session(_Result(scalar=founder))

        with self.assertRaises(HTTPException) as ctx:
            await users.change_user_role(founder.id, UserRoleUpdate(role="admin"), db, admin)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(founder.role, "founder")
        self.assertEqual(db.commit_count, 0)

    async def test_founder_can_assign_founder_to_another_user(self):
        caller = _user("founder")
        admin = _user("admin")
        db = _Session(_Result(scalar=admin))

        result = await users.change_user_role(admin.id, UserRoleUpdate(role="founder"), db, caller)

        self.assertIs(result, admin)
        self.assertEqual(admin.role, "founder")
        self.assertEqual(db.commit_count, 1)

    async def test_founder_can_change_another_founder(self):
        caller = _user("founder")
        target = _user("founder")
        db = _Session(_Result(scalar=target))

        await users.change_user_role(target.id, UserRoleUpdate(role="admin"), db, caller)

        self.assertEqual(target.role, "admin")
        self.assertEqual(db.commit_count, 1)


class FullUserProfileBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_can_read_self(self):
        collector = _user("collector")
        db = _Session(_Result(scalar=collector))

        result = await users.get_user(collector.id, db, collector)

        self.assertIs(result, collector)
        self.assertEqual(db.execute_count, 1)

    async def test_admin_can_read_another_user(self):
        admin = _user("admin")
        collector = _user("collector")
        db = _Session(_Result(scalar=collector))

        result = await users.get_user(collector.id, db, admin)

        self.assertIs(result, collector)
        self.assertEqual(db.execute_count, 1)

    async def test_assigned_artist_can_read_client(self):
        artist = _user("artist")
        collector = _user("collector")
        db = _Session(_Result(scalar=uuid.uuid4()), _Result(scalar=collector))

        result = await users.get_user(collector.id, db, artist)

        self.assertIs(result, collector)
        self.assertEqual(db.execute_count, 2)

    async def test_unassigned_artist_cannot_read_client(self):
        artist = _user("artist")
        collector = _user("collector")
        db = _Session(_Result(scalar=None))

        with self.assertRaises(HTTPException) as ctx:
            await users.get_user(collector.id, db, artist)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(db.execute_count, 1)

    async def test_collector_cannot_read_another_user(self):
        caller = _user("collector")
        target = _user("collector")
        db = _Session()

        with self.assertRaises(HTTPException) as ctx:
            await users.get_user(target.id, db, caller)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(db.execute_count, 0)


class AIMemoryContextBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_can_read_own_context(self):
        collector = _user("collector")
        db = _Session(_Result(scalar=collector), _Result(rows=[]))

        result = await ai_memories.get_memory_context(collector.id, "gala", db, collector)

        self.assertEqual(result.user_id, collector.id)
        self.assertEqual(result.memory_count, 0)
        self.assertEqual(db.execute_count, 2)

    async def test_admin_can_read_another_users_context(self):
        admin = _user("admin")
        collector = _user("collector")
        db = _Session(_Result(scalar=collector), _Result(rows=[]))

        result = await ai_memories.get_memory_context(collector.id, "gala", db, admin)

        self.assertEqual(result.user_id, collector.id)
        self.assertEqual(db.execute_count, 2)

    async def test_assigned_artist_can_read_client_context(self):
        artist = _user("artist")
        collector = _user("collector")
        db = _Session(
            _Result(scalar=uuid.uuid4()),
            _Result(scalar=collector),
            _Result(rows=[]),
        )

        result = await ai_memories.get_memory_context(collector.id, "smoku", db, artist)

        self.assertEqual(result.user_id, collector.id)
        self.assertEqual(result.agent, "smoku")
        self.assertEqual(db.execute_count, 3)

    async def test_unassigned_artist_cannot_read_client_context(self):
        artist = _user("artist")
        collector = _user("collector")
        db = _Session(_Result(scalar=None))

        with self.assertRaises(HTTPException) as ctx:
            await ai_memories.get_memory_context(collector.id, "gala", db, artist)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(db.execute_count, 1)

    async def test_collector_cannot_read_another_users_context(self):
        caller = _user("collector")
        target = _user("collector")
        db = _Session()

        with self.assertRaises(HTTPException) as ctx:
            await ai_memories.get_memory_context(target.id, "gala", db, caller)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(db.execute_count, 0)


class ConversationCreationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_assigned_artist_can_start_one_to_one_client_thread(self):
        artist = _user("artist")
        collector = _user("collector")
        db = _Session(_Result(scalar=uuid.uuid4()))

        await conversations._require_conversation_creation_access(
            ConversationCreate(participant_ids=[collector.id]),
            artist,
            db,
        )

        self.assertEqual(db.execute_count, 1)

    async def test_unassigned_artist_is_blocked_before_conversation_creation(self):
        artist = _user("artist")
        collector = _user("collector")
        db = _Session(_Result(scalar=None))

        with self.assertRaises(HTTPException) as ctx:
            await conversations.create_conversation(
                ConversationCreate(participant_ids=[collector.id]),
                db,
                artist,
            )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(db.execute_count, 1)

    async def test_collector_can_start_thread_only_with_their_booked_artist(self):
        collector = _user("collector")
        artist = _user("artist")
        db = _Session(_Result(scalar=uuid.uuid4()))

        await conversations._require_conversation_creation_access(
            ConversationCreate(participant_ids=[artist.id]),
            collector,
            db,
        )

        self.assertEqual(db.execute_count, 1)

    async def test_artist_and_collector_group_creation_is_blocked(self):
        target_ids = [uuid.uuid4(), uuid.uuid4()]
        for role in ("artist", "collector"):
            with self.subTest(role=role), self.assertRaises(HTTPException) as ctx:
                await conversations._require_conversation_creation_access(
                    ConversationCreate(participant_ids=target_ids, is_group=True),
                    _user(role),
                    _Session(),
                )
            self.assertEqual(ctx.exception.status_code, 403)


class DocumentTemplateBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_template_mutations_require_admin_or_founder(self):
        artist = _user("artist")
        admin = _user("admin")

        for endpoint in (documents.create_template, documents.update_template):
            dependency = inspect.signature(endpoint).parameters["current_user"].default.dependency
            with (
                self.subTest(endpoint=endpoint.__name__),
                self.assertRaises(HTTPException) as ctx,
            ):
                await dependency(artist)
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertIs(await dependency(admin), admin)


class POSBookingBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_pos_charge_returns_stripe_checkout_url(self):
        artist = _user("artist")
        db = _Session()
        routing = MagicMock()
        routing.apply_to.side_effect = lambda params: params
        routing.request_options.return_value = {}
        routing.receipt_metadata.return_value = {}
        routing.destination = "platform"
        routing.reason = "test"
        stripe_client = MagicMock()
        stripe_client.v1.checkout.sessions.create.return_value = SimpleNamespace(
            id="cs_test_bttco_pos",
            url="https://checkout.stripe.com/c/pay/cs_test_bttco_pos",
        )

        with (
            patch.object(pos, "_init_stripe"),
            patch.object(pos, "resolve_charge_routing", return_value=routing),
            patch.object(pos, "get_stripe_client", return_value=stripe_client),
        ):
            result = await pos.create_pos_charge(
                pos.POSChargeRequest(
                    amount_cents=10000,
                    client_name="Checkout Proof",
                    description="Tattoo session",
                ),
                db,
                artist,
            )

        self.assertEqual(result.session_id, "cs_test_bttco_pos")
        self.assertRegex(result.checkout_url, r"^https://checkout\.stripe\.com/")
        stripe_client.v1.checkout.sessions.create.assert_called_once()

    async def test_pos_invoice_reuses_checkout_and_shared_email_delivery(self):
        artist = _user("artist")
        db = _Session()
        routing = MagicMock()
        routing.apply_to.side_effect = lambda params: params
        routing.request_options.return_value = {}
        routing.receipt_metadata.return_value = {}
        routing.destination = "platform"
        routing.reason = "test"
        stripe_client = MagicMock()
        stripe_client.v1.checkout.sessions.create.return_value = SimpleNamespace(
            id="cs_test_bttco_invoice",
            url="https://checkout.stripe.com/c/pay/cs_test_bttco_invoice",
        )
        email_send = AsyncMock(return_value=True)

        with (
            patch.object(pos, "_init_stripe"),
            patch.object(pos, "resolve_charge_routing", return_value=routing),
            patch.object(pos, "get_stripe_client", return_value=stripe_client),
            patch.object(pos, "send_email", email_send),
        ):
            result = await pos.send_pos_invoice(
                pos.POSInvoiceRequest(
                    amount_cents=10000,
                    client_name="Invoice Proof",
                    client_email="collector@example.com",
                    description="Tattoo session",
                ),
                db,
                artist,
            )

        self.assertEqual(result.session_id, "cs_test_bttco_invoice")
        self.assertEqual(str(result.recipient_email), "collector@example.com")
        self.assertTrue(result.email_sent)
        email_send.assert_awaited_once()
        email_call = email_send.await_args.kwargs
        self.assertEqual(email_call["tags"], ["pos_invoice"])
        self.assertIn(result.checkout_url, email_call["html_body"])

    async def test_artist_cannot_charge_another_artists_booking(self):
        artist = _user("artist")
        booking = Booking(
            id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            description="Private appointment",
        )
        db = _Session(_Result(scalar=booking))

        with (
            patch.object(pos, "_init_stripe") as stripe_init,
            self.assertRaises(HTTPException) as ctx,
        ):
            await pos.create_pos_charge(
                pos.POSChargeRequest(amount_cents=10000, booking_id=booking.id),
                db,
                artist,
            )

        self.assertEqual(ctx.exception.status_code, 403)
        stripe_init.assert_not_called()

    async def test_artist_can_charge_their_booking_and_admin_retains_shop_access(self):
        artist = _user("artist")
        own_booking = Booking(id=uuid.uuid4(), artist_id=artist.id, description="Own appointment")
        other_booking = Booking(id=uuid.uuid4(), artist_id=uuid.uuid4(), description="Shop appointment")

        pos._require_booking_charge_access(own_booking, artist)
        for role in ("admin", "founder"):
            pos._require_booking_charge_access(other_booking, _user(role))

    async def test_unknown_booking_stops_before_stripe(self):
        artist = _user("artist")
        db = _Session(_Result(scalar=None))

        with (
            patch.object(pos, "_init_stripe") as stripe_init,
            self.assertRaises(HTTPException) as ctx,
        ):
            await pos.create_pos_charge(
                pos.POSChargeRequest(amount_cents=10000, booking_id=uuid.uuid4()),
                db,
                artist,
            )

        self.assertEqual(ctx.exception.status_code, 404)
        stripe_init.assert_not_called()


if __name__ == "__main__":
    unittest.main()
