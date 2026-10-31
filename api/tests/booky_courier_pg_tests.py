"""Real PostgreSQL concurrency proof for confirmed Booky delivery recovery.

Net-new artifact justification:
The focused Booky suite deliberately mocks database locking, while the existing
booking gauntlet also requires Stripe and MailHog. This bounded fixture reuses
the canonical ORM and Courier worker in an isolated PostgreSQL schema so the
two-session Message lease race is exercised without external provider writes.
"""

import asyncio
import json
import os
import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.api.v1.endpoints import email_log as email_log_endpoint
from app.constants.status import BookingStatus
from app.core import legacy_env
from app.core.database import Base
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.email_log import EmailLog
from app.models.message import Conversation, Message
from app.models.user import User
from app.services import email as email_service

TEST_DATABASE_URL = legacy_env.getenv("RYOZAN_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL.startswith("postgresql://"):
    TEST_DATABASE_URL = TEST_DATABASE_URL.replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "set RYOZAN_TEST_DATABASE_URL to run the real Booky Courier race",
)
class BookyCourierPostgresConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"booky_courier_test_{uuid4().hex}"
        self.admin_engine = create_async_engine(TEST_DATABASE_URL)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addAsyncCleanup(self._cleanup_resources)

        self.engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": self.schema}},
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )
        tables = [
            User.__table__,
            Conversation.__table__,
            Booking.__table__,
            Message.__table__,
            BookingLifecycleEvent.__table__,
            EmailLog.__table__,
        ]
        async with self.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection,
                    tables=tables,
                )
            )

        self.founder = User(
            id=uuid4(),
            email=f"founder-{uuid4().hex}@example.test",
            password_hash="disabled",
            name="Founder",
            role="founder",
        )
        self.conversation = Conversation(
            id=uuid4(),
            name="Booky race",
            is_group=False,
            created_by=self.founder.id,
        )
        self.booking = Booking(
            id=uuid4(),
            artist_id=self.founder.id,
            guest_name="Collector",
            guest_email="collector@example.test",
            status=BookingStatus.CONFIRMED,
            description="Consultation",
            booking_kind="consultation",
            consultation_method="phone",
            reference_code=f"BKG-{uuid4().hex[:12].upper()}",
            scheduled_date=date(2026, 8, 28),
            scheduled_time="10:00",
            duration_minutes=30,
            conversation_id=self.conversation.id,
        )
        self.message = Message(
            id=uuid4(),
            conversation_id=self.conversation.id,
            sender_id=self.founder.id,
            content="Consultation confirmed",
            message_type="booky_confirmation",
            metadata_json=json.dumps(
                {
                    "delivery_status": "failed",
                    "delivery_attempts": 0,
                    "delivery_updated_at": datetime.now(UTC).isoformat(),
                    "action_id": "postgres-race",
                    "booking_id": str(self.booking.id),
                    "delivery_to": "collector@example.test",
                    "client_name": "Collector",
                    "artist_name": "Founder",
                    "description": "Consultation",
                    "placement": None,
                    "reference_code": self.booking.reference_code,
                    "consultation_method": "phone",
                    "scheduled_date": "2026-08-28",
                    "scheduled_time": "10:00",
                    "conference_url": None,
                }
            ),
        )
        self.delivery_intent = BookingLifecycleEvent(
            id=uuid4(),
            booking_id=self.booking.id,
            artist_id=self.founder.id,
            event_type="booky_delivery_intent",
            source="booky",
            provider_event_id="booky:postgres-race:delivery-intent",
            reference_code=self.booking.reference_code,
            summary="Courier intent committed.",
            details={"courier_message_id": str(self.message.id)},
        )
        async with self.session_factory() as session:
            session.add(self.founder)
            await session.flush()
            session.add(self.conversation)
            await session.flush()
            session.add(self.booking)
            await session.flush()
            session.add(self.message)
            await session.flush()
            session.add(self.delivery_intent)
            await session.commit()

    async def _cleanup_resources(self):
        engine = getattr(self, "engine", None)
        if engine is not None:
            await engine.dispose()
        if self.schema.startswith("booky_courier_test_"):
            async with self.admin_engine.begin() as connection:
                await connection.execute(
                    text(f'DROP SCHEMA "{self.schema}" CASCADE')
                )
        await self.admin_engine.dispose()

    async def test_worker_claim_between_projection_and_staff_lock_is_not_requeued(self):
        projection_complete = asyncio.Event()
        worker_claimed = asyncio.Event()
        release_projection = asyncio.Event()
        release_provider = asyncio.Event()
        provider_calls = 0
        original_projection = bookings_endpoint._booky_projection

        async def gated_projection(booking, db, *, staff):
            outcome = await original_projection(booking, db, staff=staff)
            if staff and not projection_complete.is_set():
                projection_complete.set()
                await asyncio.wait_for(release_projection.wait(), timeout=10)
            return outcome

        async def held_provider_send(**_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            worker_claimed.set()
            await asyncio.wait_for(release_provider.wait(), timeout=10)
            return True

        async with self.session_factory() as staff_session:
            with (
                patch.object(bookings_endpoint, "_booky_projection", new=gated_projection),
                patch("app.core.database.async_session", self.session_factory),
                patch.object(email_service, "send_email", new=held_provider_send),
            ):
                staff_task = asyncio.create_task(
                    bookings_endpoint.retry_booky_confirmation(
                        self.booking.id,
                        db=staff_session,
                        current_user=self.founder,
                    )
                )
                await asyncio.wait_for(projection_complete.wait(), timeout=10)
                worker_task = asyncio.create_task(
                    email_service.process_booky_courier_outbox_once(
                        message_id=self.message.id
                    )
                )
                await asyncio.wait_for(worker_claimed.wait(), timeout=10)
                release_projection.set()
                staff_result = await asyncio.wait_for(staff_task, timeout=10)

                self.assertEqual(staff_result.courier_state, "sending")
                self.assertFalse(staff_result.retry_eligible)
                await staff_session.rollback()
                release_provider.set()
                delivered = await asyncio.wait_for(worker_task, timeout=10)

        self.assertEqual(delivered, 1)
        self.assertEqual(provider_calls, 1)
        async with self.session_factory() as verification_session:
            final_message = await verification_session.get(Message, self.message.id)
            final_metadata = json.loads(final_message.metadata_json or "{}")
            self.assertEqual(final_metadata["delivery_status"], "sent")
            self.assertEqual(final_metadata["delivery_attempts"], 1)
            retry_requests = await verification_session.scalar(
                select(func.count(BookingLifecycleEvent.id)).where(
                    BookingLifecycleEvent.booking_id == self.booking.id,
                    BookingLifecycleEvent.event_type
                    == "booky_delivery_retry_requested",
                )
            )
            terminal_receipts = await verification_session.scalar(
                select(func.count(BookingLifecycleEvent.id)).where(
                    BookingLifecycleEvent.booking_id == self.booking.id,
                    BookingLifecycleEvent.provider_event_id
                    == "booky:postgres-race:courier-attempt:1:terminal",
                )
            )
            self.assertEqual(retry_requests, 0)
            self.assertEqual(terminal_receipts, 1)

            async with self.engine.connect() as connection:
                indexes = await connection.run_sync(
                    lambda sync_connection: inspect(sync_connection).get_indexes(
                        "ryozan_email_log"
                    )
                )
            attempt_index = next(
                index
                for index in indexes
                if index["name"]
                == "ix_ryozan_email_log_courier_message_attempt"
            )
            self.assertFalse(attempt_index["unique"])
            self.assertEqual(
                attempt_index["column_names"],
                ["courier_message_id", "courier_attempt"],
            )

    async def test_stale_worker_completion_cannot_overwrite_newer_attempt(self):
        provider_started = asyncio.Event()
        release_provider = asyncio.Event()

        async def held_provider_send(**_kwargs):
            provider_started.set()
            await asyncio.wait_for(release_provider.wait(), timeout=10)
            return True

        with (
            patch("app.core.database.async_session", self.session_factory),
            patch.object(email_service, "send_email", new=held_provider_send),
        ):
            worker_task = asyncio.create_task(
                email_service.process_booky_courier_outbox_once(
                    message_id=self.message.id
                )
            )
            await asyncio.wait_for(provider_started.wait(), timeout=10)

            async with self.session_factory() as newer_session:
                newer = await newer_session.get(
                    Message,
                    self.message.id,
                    with_for_update=True,
                    populate_existing=True,
                )
                metadata = json.loads(newer.metadata_json or "{}")
                metadata.update(
                    {
                        "delivery_status": "sending",
                        "delivery_attempts": 2,
                        "delivery_attempt_id": "booky:postgres-race:courier-attempt:2",
                        "delivery_attempt_started_at": datetime.now(UTC).isoformat(),
                        "delivery_attempt_finished_at": None,
                    }
                )
                newer.metadata_json = json.dumps(metadata)
                await newer_session.commit()

            release_provider.set()
            delivered = await asyncio.wait_for(worker_task, timeout=10)

        self.assertEqual(delivered, 0)
        async with self.session_factory() as verification_session:
            final_message = await verification_session.get(Message, self.message.id)
            final_metadata = json.loads(final_message.metadata_json or "{}")
            self.assertEqual(final_metadata["delivery_status"], "sending")
            self.assertEqual(final_metadata["delivery_attempts"], 2)
            self.assertEqual(
                final_metadata["delivery_attempt_id"],
                "booky:postgres-race:courier-attempt:2",
            )
            stale_terminal = await verification_session.scalar(
                select(func.count(BookingLifecycleEvent.id)).where(
                    BookingLifecycleEvent.provider_event_id
                    == "booky:postgres-race:courier-attempt:1:terminal"
                )
            )
            self.assertEqual(stale_terminal, 0)

    async def test_stale_webhook_cannot_overwrite_newer_worker_attempt(self):
        async with self.session_factory() as seed_session:
            message = await seed_session.get(Message, self.message.id)
            metadata = json.loads(message.metadata_json or "{}")
            metadata.update(
                {
                    "delivery_status": "sent",
                    "delivery_attempts": 1,
                    "delivery_attempt_id": "booky:postgres-race:courier-attempt:1",
                }
            )
            message.metadata_json = json.dumps(metadata)
            seed_session.add(
                EmailLog(
                    to_email="collector@example.test",
                    subject="Consultation Booked",
                    template="custom",
                    from_alias="shop",
                    resend_id="resend-attempt-1",
                    status="sent",
                    courier_message_id=self.message.id,
                    courier_attempt=1,
                )
            )
            await seed_session.commit()

        async with self.session_factory() as webhook_session:
            # Prime this session's identity map with attempt 1 before another
            # session advances the durable Message lease to attempt 2.
            await webhook_session.get(Message, self.message.id)
            async with self.session_factory() as worker_session:
                current = await worker_session.get(
                    Message,
                    self.message.id,
                    with_for_update=True,
                    populate_existing=True,
                )
                metadata = json.loads(current.metadata_json or "{}")
                metadata.update(
                    {
                        "delivery_status": "sending",
                        "delivery_attempts": 2,
                        "delivery_attempt_id": "booky:postgres-race:courier-attempt:2",
                        "delivery_attempt_started_at": datetime.now(UTC).isoformat(),
                        "delivery_attempt_finished_at": None,
                    }
                )
                current.metadata_json = json.dumps(metadata)
                await worker_session.commit()

            class WebhookRequest:
                headers = {}

                async def body(self):
                    return b"{}"

                async def json(self):
                    return {
                        "type": "email.delivered",
                        "data": {"email_id": "resend-attempt-1"},
                    }

            with patch.object(
                email_log_endpoint,
                "get_settings",
                return_value=type("Settings", (), {"RESEND_WEBHOOK_SECRET": ""})(),
            ):
                response = await email_log_endpoint.resend_webhook(
                    WebhookRequest(),
                    db=webhook_session,
                )
            self.assertEqual(response, {"ok": True, "updated": 1})

        async with self.session_factory() as verification_session:
            final_message = await verification_session.get(Message, self.message.id)
            final_metadata = json.loads(final_message.metadata_json or "{}")
            self.assertEqual(final_metadata["delivery_status"], "sending")
            self.assertEqual(final_metadata["delivery_attempts"], 2)
            self.assertEqual(
                final_metadata["delivery_attempt_id"],
                "booky:postgres-race:courier-attempt:2",
            )


if __name__ == "__main__":
    unittest.main()
