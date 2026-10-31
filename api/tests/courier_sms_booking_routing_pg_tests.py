"""Real PostgreSQL proof that shop-line collector replies land in booking threads.

Net-new artifact justification:
sms_identity_routing_tests owns lane identity with mocked sessions, and the
booky_courier PostgreSQL fixture owns delivery leases. Neither exercises the
phone-to-booking match, which depends on PostgreSQL regexp_replace and real
participant, notification and SMS log rows. This bounded fixture reuses the
canonical ORM in an isolated schema and patches only provider-facing calls.
"""

import json
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints import sms as sms_endpoint
from app.constants.status import BookingStatus
from app.core.config import Settings
from app.core.database import Base
from app.models.booking import Booking
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.notification import Notification
from app.models.sms_message import SmsMessage
from app.models.user import BTTCOUser
from app.services import sms as sms_service

TEST_DATABASE_URL = os.environ.get("BTTCO_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL.startswith("postgresql://"):
    TEST_DATABASE_URL = TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

COLLECTOR_E164 = "+15550104961"


def _settings():
    return Settings(
        TWILIO_ACCOUNT_SID="AC-test",
        TWILIO_AUTH_TOKEN="fixture-token",
        TWILIO_MESSAGING_SERVICE_SID="MG-test",
        TWILIO_BELLA_FROM_NUMBER="+15550001001",
        TWILIO_GALA_FROM_NUMBER="+15550001002",
        TWILIO_RYOZAN_COLLECTOR_FROM_NUMBER="+15550001003",
    )


@unittest.skipUnless(TEST_DATABASE_URL, "set BTTCO_TEST_DATABASE_URL to run real SMS booking routing")
class CourierSmsBookingRoutingPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"sms_booking_routing_{uuid4().hex}"
        self.admin_engine = create_async_engine(TEST_DATABASE_URL)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addAsyncCleanup(self._cleanup)
        self.engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": self.schema}},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        tables = [
            BTTCOUser.__table__,
            Conversation.__table__,
            ConversationParticipant.__table__,
            Booking.__table__,
            Message.__table__,
            SmsMessage.__table__,
            Notification.__table__,
        ]
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))

        self.founder = self._user("Founder", "founder")
        self.artist = self._user("Rowan Artist", "artist", phone="+15550107777", sms_opt_in=True)
        self.collector = self._user("Maria Collector", "collector", phone="5550104961")
        async with self.sessions() as db:
            db.add_all([self.founder, self.artist, self.collector])
            await db.commit()

        self.patches = [
            patch.object(sms_service, "get_settings", return_value=_settings()),
            patch.object(sms_endpoint, "should_notify", new=AsyncMock(return_value=True)),
            patch.object(sms_endpoint, "send_push_notification", new=AsyncMock()),
            patch("app.api.v1.endpoints.conversations.should_notify", new=AsyncMock(return_value=True)),
        ]
        for active in self.patches:
            active.start()
            self.addCleanup(active.stop)

    async def _cleanup(self):
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    @staticmethod
    def _user(name, role, *, phone=None, sms_opt_in=False):
        return BTTCOUser(
            id=uuid4(),
            email=f"{role}-{uuid4().hex}@example.test",
            password_hash="disabled",
            name=name,
            role=role,
            phone=phone,
            sms_opt_in=sms_opt_in,
        )

    async def _booking_thread(self, db, *, client=None, guest_phone=None, status=BookingStatus.PENDING, age_days=0):
        created = datetime.now(UTC) - timedelta(days=age_days)
        conversation = Conversation(id=uuid4(), name="Booking thread", is_group=False, created_by=self.artist.id)
        db.add(conversation)
        await db.flush()
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=self.artist.id, role="owner"))
        if client:
            db.add(ConversationParticipant(conversation_id=conversation.id, user_id=client.id, role="member"))
        booking = Booking(
            id=uuid4(),
            client_id=client.id if client else None,
            artist_id=self.artist.id,
            guest_name="Maria Collector",
            guest_phone=guest_phone,
            status=status,
            description="Chess pawn",
            reference_code=f"BKG-{uuid4().hex[:8].upper()}",
            conversation_id=conversation.id,
            created_at=created,
            updated_at=created,
        )
        db.add(booking)
        await db.flush()
        return booking, conversation

    async def _staff_message(self, db, conversation, *, minutes_ago):
        db.add(
            Message(
                conversation_id=conversation.id,
                sender_id=self.artist.id,
                content="Does Saturday work?",
                message_type="text",
                created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            )
        )
        await db.flush()

    async def _inbound(self, db, *, lane_id="bella", lane_label="Bella", to_phone="+15550001001", phone=COLLECTOR_E164):
        log = SmsMessage(direction="inbound", to_phone=to_phone, from_phone=phone, body="How much?", status="received")
        db.add(log)
        await db.flush()
        result = await sms_endpoint._archive_sms_to_courier(
            db,
            log=log,
            phone_e164=phone,
            to_phone=to_phone,
            body_text="How much would it be?",
            message_sid=f"SM-{uuid4().hex}",
            num_media=0,
            lane_id=lane_id,
            lane_label=lane_label,
        )
        await db.commit()
        return log, result

    async def test_reply_lands_in_booking_thread_and_alerts_artist(self):
        async with self.sessions() as db:
            booking, thread = await self._booking_thread(db, client=self.collector)
            await self._staff_message(db, thread, minutes_ago=30)
            log, (conversation, _owner, sender, message) = await self._inbound(db)

            self.assertEqual(conversation.id, thread.id)
            self.assertEqual(sender.id, self.collector.id)
            self.assertEqual(log.conversation_id, thread.id)
            metadata = json.loads(message.metadata_json)
            self.assertEqual(metadata["routed_booking_id"], str(booking.id))
            self.assertEqual(metadata["lane_id"], "bella")

            lane_threads = await db.scalar(select(Conversation.id).where(Conversation.name.like("SMS - %")))
            self.assertIsNone(lane_threads)

            alerts = (await db.execute(select(Notification).where(Notification.user_id == self.artist.id))).scalars()
            self.assertEqual([alert.title for alert in alerts], ["Text from Maria"])

            intents = metadata["notification_delivery"]["intents"]
            sms_intents = [intent for intent in intents if intent["channel"] == "sms"]
            self.assertEqual([intent["recipient_user_id"] for intent in sms_intents], [str(self.artist.id)])
            self.assertEqual(sms_intents[0]["lane_id"], "bella")
            self.assertEqual(
                sms_intents[0]["template"], "staff_message_alert", "staff get a Courier link, not reply-here"
            )

    async def test_guest_booking_without_account_phone_match_uses_guest_phone(self):
        async with self.sessions() as db:
            guest = self._user("Guest", "collector")
            db.add(guest)
            await db.flush()
            _booking, thread = await self._booking_thread(db, guest_phone="(555) 010-4961")
            await db.execute(
                text(
                    "UPDATE ryozan_users SET phone = NULL WHERE id = :id",
                ),
                {"id": self.collector.id},
            )
            await db.commit()
            _log, (conversation, _owner, sender, _message) = await self._inbound(db)

            self.assertEqual(conversation.id, thread.id)
            participants = set(
                (
                    await db.execute(
                        select(ConversationParticipant.user_id).where(
                            ConversationParticipant.conversation_id == thread.id
                        )
                    )
                ).scalars()
            )
            self.assertIn(sender.id, participants)
            self.assertIn(self.artist.id, participants)

    async def test_thread_with_latest_staff_message_wins_over_newer_booking(self):
        async with self.sessions() as db:
            _older, older_thread = await self._booking_thread(db, client=self.collector, age_days=10)
            _newer, newer_thread = await self._booking_thread(db, client=self.collector, age_days=1)
            await self._staff_message(db, older_thread, minutes_ago=5)
            await self._staff_message(db, newer_thread, minutes_ago=600)
            _log, (conversation, *_rest) = await self._inbound(db)
            self.assertEqual(conversation.id, older_thread.id)

    async def test_latest_outbound_text_beats_older_staff_message(self):
        async with self.sessions() as db:
            _older, older_thread = await self._booking_thread(db, client=self.collector, age_days=10)
            _newer, newer_thread = await self._booking_thread(db, client=self.collector, age_days=1)
            await self._staff_message(db, older_thread, minutes_ago=120)
            db.add(
                SmsMessage(
                    direction="outbound",
                    to_phone=COLLECTOR_E164,
                    from_phone="+15550001001",
                    body="We received your request",
                    status="delivered",
                    conversation_id=newer_thread.id,
                    created_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            await db.flush()
            _log, (conversation, *_rest) = await self._inbound(db)
            self.assertEqual(conversation.id, newer_thread.id)

    async def test_newest_booking_wins_without_staff_activity(self):
        async with self.sessions() as db:
            await self._booking_thread(db, client=self.collector, age_days=10)
            _newer, newer_thread = await self._booking_thread(db, client=self.collector, age_days=1)
            _log, (conversation, *_rest) = await self._inbound(db)
            self.assertEqual(conversation.id, newer_thread.id)

    async def test_booking_without_thread_gets_one_with_artist(self):
        async with self.sessions() as db:
            booking = Booking(
                id=uuid4(),
                client_id=self.collector.id,
                artist_id=self.artist.id,
                guest_name="Maria Collector",
                status=BookingStatus.PENDING,
                description="Chess pawn",
            )
            db.add(booking)
            await db.flush()
            _log, (conversation, *_rest) = await self._inbound(db)
            await db.refresh(booking)
            self.assertEqual(booking.conversation_id, conversation.id)
            participants = set(
                (
                    await db.execute(
                        select(ConversationParticipant.user_id).where(
                            ConversationParticipant.conversation_id == conversation.id
                        )
                    )
                ).scalars()
            )
            self.assertEqual(participants, {self.artist.id, self.collector.id})

    async def test_reassigned_former_artist_is_not_alerted(self):
        async with self.sessions() as db:
            former = self._user("Former Artist", "artist", phone="+15550109999", sms_opt_in=True)
            db.add(former)
            await db.flush()
            booking, thread = await self._booking_thread(db, client=self.collector)
            db.add(ConversationParticipant(conversation_id=thread.id, user_id=former.id, role="member"))
            await db.flush()
            _log, (_conversation, _owner, _sender, message) = await self._inbound(db)
            alerted = set(
                (await db.execute(select(Notification.user_id).where(Notification.title.like("Text from%")))).scalars()
            )
            self.assertEqual(alerted, {self.artist.id})
            intents = json.loads(message.metadata_json)["notification_delivery"]["intents"]
            self.assertNotIn(str(former.id), {intent["recipient_user_id"] for intent in intents})

    async def test_booking_without_thread_never_borrows_an_older_conversation(self):
        async with self.sessions() as db:
            for _ in range(2):
                old = Conversation(id=uuid4(), name="Older appointment", is_group=False, created_by=self.artist.id)
                db.add(old)
                await db.flush()
                db.add_all(
                    [
                        ConversationParticipant(conversation_id=old.id, user_id=self.artist.id, role="owner"),
                        ConversationParticipant(conversation_id=old.id, user_id=self.collector.id, role="member"),
                    ]
                )
            booking = Booking(
                id=uuid4(),
                client_id=self.collector.id,
                artist_id=self.artist.id,
                guest_name="Maria Collector",
                status=BookingStatus.PENDING,
                description="New piece",
            )
            db.add(booking)
            await db.flush()
            _log, (conversation, *_rest) = await self._inbound(db)
            await db.refresh(booking)
            self.assertEqual(booking.conversation_id, conversation.id)
            self.assertNotEqual(conversation.name, "Older appointment")

    async def test_linking_a_guest_booking_keeps_the_phone_routable(self):
        from app.api.v1.endpoints import bookings as bookings_endpoint

        async with self.sessions() as db:
            account = self._user("Maria Account", "collector")
            db.add(account)
            await db.flush()
            booking, thread = await self._booking_thread(db, guest_phone="(555) 010-4961")
            await db.commit()
        async with self.sessions() as db:
            user = await db.get(BTTCOUser, account.id)
            with patch.object(bookings_endpoint, "_enrich_booking", new=AsyncMock(return_value=None)):
                await bookings_endpoint.link_booking_to_account(booking.reference_code, db=db, current_user=user)
            await db.commit()
        async with self.sessions() as db:
            self.assertEqual((await db.get(BTTCOUser, account.id)).phone, "(555) 010-4961")
            await db.execute(text("UPDATE ryozan_users SET phone = NULL WHERE id = :id"), {"id": self.collector.id})
            await db.commit()
        async with self.sessions() as db:
            _log, (conversation, *_rest) = await self._inbound(db)
            self.assertEqual(conversation.id, thread.id)

    async def test_international_caller_never_matches_a_us_suffix(self):
        async with self.sessions() as db:
            _booking, thread = await self._booking_thread(db, guest_phone="(555) 010-4961")
            _log, (conversation, *_rest) = await self._inbound(db, phone="+445550104961")
            self.assertNotEqual(conversation.id, thread.id)
            self.assertTrue(conversation.name.startswith("SMS - Bella: "))

    async def test_audit_rows_do_not_count_as_staff_contact(self):
        async with self.sessions() as db:
            _older, older_thread = await self._booking_thread(db, client=self.collector, age_days=10)
            _newer, newer_thread = await self._booking_thread(db, client=self.collector, age_days=1)
            db.add(
                Message(
                    conversation_id=older_thread.id,
                    sender_id=self.artist.id,
                    content="Archived conversation",
                    message_type="audit",
                    created_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            await self._staff_message(db, newer_thread, minutes_ago=600)
            _log, (conversation, *_rest) = await self._inbound(db)
            self.assertEqual(conversation.id, newer_thread.id)

    async def test_completed_window_runs_from_the_appointment_not_the_last_edit(self):
        async with self.sessions() as db:
            old, old_thread = await self._booking_thread(db, client=self.collector, status=BookingStatus.COMPLETED)
            old.scheduled_date = (datetime.now(UTC) - timedelta(days=200)).date()
            old.updated_at = datetime.now(UTC)
            _log, (conversation, *_rest) = await self._inbound(db)
            self.assertNotEqual(conversation.id, old_thread.id)

        async with self.sessions() as db:
            recent, recent_thread = await self._booking_thread(
                db, client=self.collector, status=BookingStatus.COMPLETED
            )
            recent.scheduled_date = (datetime.now(UTC) - timedelta(days=3)).date()
            _log, (conversation, *_rest) = await self._inbound(db)
            self.assertEqual(conversation.id, recent_thread.id)

    async def test_cancelled_booking_and_no_booking_fall_back_to_lane_thread(self):
        async with self.sessions() as db:
            await self._booking_thread(db, client=self.collector, status=BookingStatus.CANCELLED)
            _log, (conversation, owner, _sender, message) = await self._inbound(db)
            self.assertEqual(conversation.name, f"SMS - Bella: {COLLECTOR_E164}")
            self.assertEqual(owner.id, self.founder.id)
            self.assertIsNone(json.loads(message.metadata_json)["routed_booking_id"])
            self.assertIsNone(await db.scalar(select(Notification.id).where(Notification.user_id == self.artist.id)))

    async def test_gala_lane_never_routes_into_booking_thread(self):
        async with self.sessions() as db:
            _booking, thread = await self._booking_thread(db, client=self.collector)
            _log, (conversation, *_rest) = await self._inbound(
                db, lane_id="gala", lane_label="Gala", to_phone="+15550001002"
            )
            self.assertNotEqual(conversation.id, thread.id)
            self.assertEqual(conversation.name, f"SMS - Gala: {COLLECTOR_E164}")

    async def _stranded_bella_thread(self, db):
        thread = Conversation(
            id=uuid4(), name=f"SMS - Bella: {COLLECTOR_E164}", is_group=False, created_by=self.founder.id
        )
        db.add(thread)
        await db.flush()
        db.add_all(
            [
                ConversationParticipant(conversation_id=thread.id, user_id=self.founder.id, role="owner"),
                ConversationParticipant(conversation_id=thread.id, user_id=self.collector.id, role="member"),
                Message(
                    conversation_id=thread.id, sender_id=self.collector.id, content="How much?", message_type="sms"
                ),
                Message(
                    conversation_id=thread.id, sender_id=self.founder.id, content="About $150", message_type="text"
                ),
                SmsMessage(
                    direction="inbound",
                    to_phone="+15550001001",
                    from_phone=COLLECTOR_E164,
                    body="How much?",
                    status="received",
                    conversation_id=thread.id,
                ),
            ]
        )
        await db.flush()
        return thread

    async def _message_count(self, db, conversation_id):
        rows = await db.execute(select(Message.id).where(Message.conversation_id == conversation_id))
        return len(rows.all())

    async def test_cleanup_dry_run_changes_nothing_and_apply_moves_history_once(self):
        cleanup = _load_cleanup_script()
        async with self.sessions() as db:
            _booking, booking_thread = await self._booking_thread(db, client=self.collector)
            stranded = await self._stranded_bella_thread(db)
            await db.commit()

        async with self.sessions() as db:
            moves = await cleanup.plan_and_apply(db, apply=False, exclude_names=set())
            self.assertEqual([(m.target_id, m.skipped_reason) for m in moves], [(booking_thread.id, None)])
        async with self.sessions() as db:
            self.assertEqual(await self._message_count(db, stranded.id), 2)

        async with self.sessions() as db:
            await cleanup.plan_and_apply(db, apply=True, exclude_names=set())
        async with self.sessions() as db:
            self.assertEqual(await self._message_count(db, stranded.id), 0)
            self.assertEqual(await self._message_count(db, booking_thread.id), 2)
            log = await db.scalar(select(SmsMessage).where(SmsMessage.from_phone == COLLECTOR_E164))
            self.assertEqual(log.conversation_id, booking_thread.id)
            self.assertIsNotNone(await db.get(Conversation, stranded.id))

        async with self.sessions() as db:
            self.assertEqual(await cleanup.plan_and_apply(db, apply=True, exclude_names=set()), [])

    async def test_cleanup_skips_collectors_with_several_open_bookings(self):
        cleanup = _load_cleanup_script()
        async with self.sessions() as db:
            await self._booking_thread(db, client=self.collector, age_days=5)
            await self._booking_thread(db, client=self.collector, age_days=1)
            stranded = await self._stranded_bella_thread(db)
            await db.commit()
        async with self.sessions() as db:
            moves = await cleanup.plan_and_apply(db, apply=True, exclude_names=set())
            self.assertTrue(moves[0].skipped_reason.startswith("ambiguous"))
        async with self.sessions() as db:
            self.assertEqual(await self._message_count(db, stranded.id), 2)

    async def test_cleanup_leaves_history_older_than_the_booking(self):
        cleanup = _load_cleanup_script()
        async with self.sessions() as db:
            stranded = await self._stranded_bella_thread(db)
            for message in (await db.execute(select(Message).where(Message.conversation_id == stranded.id))).scalars():
                message.created_at = datetime.now(UTC) - timedelta(days=30)
            db.add(
                Message(
                    conversation_id=stranded.id,
                    sender_id=self.collector.id,
                    content="About my new one",
                    message_type="sms",
                    created_at=datetime.now(UTC),
                )
            )
            _booking, booking_thread = await self._booking_thread(db, client=self.collector, age_days=2)
            await db.commit()
        async with self.sessions() as db:
            [move] = await cleanup.plan_and_apply(db, apply=True, exclude_names=set())
            self.assertEqual((move.message_count, move.left_behind), (1, 2))
        async with self.sessions() as db:
            self.assertEqual(await self._message_count(db, stranded.id), 2)
            self.assertEqual(await self._message_count(db, booking_thread.id), 1)

    async def test_cleanup_honors_excluded_names(self):
        cleanup = _load_cleanup_script()
        async with self.sessions() as db:
            await self._booking_thread(db, client=self.collector)
            stranded = await self._stranded_bella_thread(db)
            await db.commit()
        async with self.sessions() as db:
            moves = await cleanup.plan_and_apply(db, apply=True, exclude_names={"Maria Collector"})
            self.assertEqual(moves[0].skipped_reason, "excluded")
        async with self.sessions() as db:
            self.assertEqual(await self._message_count(db, stranded.id), 2)

    async def test_cleanup_excludes_by_the_linked_account_name(self):
        cleanup = _load_cleanup_script()
        async with self.sessions() as db:
            sms_user = self._user(COLLECTOR_E164, "collector", phone=COLLECTOR_E164)
            db.add(sms_user)
            await db.flush()
            booking, _thread = await self._booking_thread(db, client=self.collector)
            booking.guest_name = None
            stranded = await self._stranded_bella_thread(db)
            for message in (await db.execute(select(Message).where(Message.conversation_id == stranded.id))).scalars():
                if message.sender_id == self.collector.id:
                    message.sender_id = sms_user.id
            await db.commit()
        async with self.sessions() as db:
            moves = await cleanup.plan_and_apply(db, apply=True, exclude_names={"Maria Collector"})
            self.assertEqual(moves[0].skipped_reason, "excluded")
        async with self.sessions() as db:
            self.assertEqual(await self._message_count(db, stranded.id), 2)


def _load_cleanup_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "move_bella_replies_to_booking_threads.py"
    spec = importlib.util.spec_from_file_location("move_bella_replies_to_booking_threads", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
