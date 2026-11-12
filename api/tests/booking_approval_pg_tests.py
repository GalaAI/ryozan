"""Real PostgreSQL proof for the staff "approve request" action.

Net-new artifact justification:
booking_scheduler_tests own slot rules with mocked sessions, and no existing
test owns approval, the single-slot deposit quote, the Stripe Connect gate, or
the request-approved email intent. Those touch Booking, Quote, lifecycle,
Courier and CRM rows together, so this fixture runs the endpoint function
against the canonical ORM in an isolated schema and patches only calendar,
email delivery and response enrichment.
"""

import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints import bookings as bookings_endpoint
from app.constants.status import BookingStatus, QuoteStatus
from app.core import legacy_env
from app.core.database import Base
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.message import Conversation, Message
from app.models.quote import Quote
from app.models.session_type import ArtistSessionType
from app.models.user import User
from app.schemas.booking import BookingApprove, BookingReschedule, BookingResponse

TEST_DATABASE_URL = legacy_env.getenv("RYOZAN_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL.startswith("postgresql://"):
    TEST_DATABASE_URL = TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


def _next_open_day() -> date:
    """A Wednesday at least ten days out: inside the default shop week and advance window."""
    day = date.today() + timedelta(days=10)
    while day.weekday() != 2:
        day += timedelta(days=1)
    return day


SLOT_DAY = _next_open_day()


@unittest.skipUnless(TEST_DATABASE_URL, "set RYOZAN_TEST_DATABASE_URL to run real booking approval")
class BookingApprovalPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"booking_approval_{uuid4().hex}"
        self.admin_engine = create_async_engine(TEST_DATABASE_URL)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addAsyncCleanup(self._cleanup)
        self.engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": self.schema}},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.artist = User(
            id=uuid4(),
            email=f"artist-{uuid4().hex}@example.test",
            password_hash="disabled",
            name="Rowan",
            role="artist",
        )
        self.other_artist = User(
            id=uuid4(), email=f"other-{uuid4().hex}@example.test", password_hash="disabled", name="Jade", role="artist"
        )
        self.collector = User(
            id=uuid4(),
            email=f"maria-{uuid4().hex}@example.test",
            password_hash="disabled",
            name="Maria",
            role="collector",
        )
        self.conversation = Conversation(id=uuid4(), name="Maria", is_group=False, created_by=self.artist.id)
        self.booking = Booking(
            id=uuid4(),
            client_id=self.collector.id,
            artist_id=self.artist.id,
            guest_name="Maria",
            status=BookingStatus.PENDING,
            description="Chess pawn",
            size_estimate="small",
            placement="Right wrist",
            reference_code="BKG-APPROVE",
            conversation_id=self.conversation.id,
            preferred_slots_json=[{"date": SLOT_DAY.isoformat(), "time": "13:00"}],
        )
        async with self.sessions() as db:
            db.add_all([self.artist, self.other_artist, self.collector])
            await db.flush()
            db.add(self.conversation)
            await db.flush()
            db.add(self.booking)
            await db.commit()

        enriched = BookingResponse.model_construct(id=self.booking.id)
        self.patches = [
            patch.object(bookings_endpoint, "_enrich_booking", new=AsyncMock(return_value=enriched)),
            patch.object(bookings_endpoint, "process_booking_email_outbox_once", new=AsyncMock(return_value=0)),
            patch.object(bookings_endpoint, "_post_booking_calendar_event", new=AsyncMock()),
            patch.object(bookings_endpoint, "check_and_complete_quest", new=AsyncMock()),
            patch.object(bookings_endpoint, "artist_calendar_connected", return_value=False),
        ]
        for active in self.patches:
            active.start()
            self.addCleanup(active.stop)

    async def _cleanup(self):
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    async def _set_stripe(self, ready: bool):
        async with self.sessions() as db:
            artist = await db.get(User, self.artist.id)
            artist.stripe_account_id = "acct_fixture" if ready else None
            artist.stripe_onboarding_complete = ready
            await db.commit()

    async def _approve(self, *, deposit, user=None, time="13:00"):
        body = BookingApprove(
            scheduled_date=SLOT_DAY, scheduled_time=time, duration_minutes=120, deposit_amount=deposit
        )
        async with self.sessions() as db:
            actor = await db.get(User, (user or self.artist).id)
            return await bookings_endpoint.approve_booking_request(self.booking.id, body, db=db, current_user=actor)

    async def _intents(self, db):
        rows = await db.execute(
            select(BookingLifecycleEvent).where(
                BookingLifecycleEvent.booking_id == self.booking.id,
                BookingLifecycleEvent.event_type == "booking_email_intent",
            )
        )
        return [row for row in rows.scalars() if (row.details or {}).get("kind") == "request_approved"]

    async def test_deposit_with_stripe_ready_quotes_booking_and_emails_deposit_link(self):
        await self._set_stripe(True)
        result = await self._approve(deposit=100)
        self.assertEqual(result.status, BookingStatus.QUOTED)
        self.assertTrue(result.deposit_link_sent)
        async with self.sessions() as db:
            booking = await db.get(Booking, self.booking.id)
            self.assertEqual(booking.status, BookingStatus.QUOTED)
            self.assertEqual(float(booking.deposit_amount), 100.0)
            self.assertIsNone(booking.scheduled_date, "time is not booked until the deposit is paid")
            quote = await db.scalar(select(Quote).where(Quote.booking_id == booking.id))
            self.assertIn(SLOT_DAY.isoformat(), quote.available_slots_json)
            [intent] = await self._intents(db)
            payload = intent.details["payload"]
            self.assertTrue(payload["deposit_url"].endswith(f"/quote/{quote.token}"))
            self.assertEqual(payload["scheduled_time"], "13:00")
            self.assertEqual(intent.details["to"], self.collector.email)
            note = await db.scalar(
                select(Message).where(
                    Message.conversation_id == self.conversation.id, Message.message_type == "booking_approval"
                )
            )
            self.assertIn("deposit link sent", note.content)
            approved = await db.scalar(
                select(BookingLifecycleEvent).where(BookingLifecycleEvent.event_type == "request_approved")
            )
            self.assertTrue(approved.details["deposit_link"])

    async def test_reapproval_expires_the_older_quote_checkout(self):
        await self._set_stripe(True)
        await self._approve(deposit=100)
        async with self.sessions() as db:
            old = await db.scalar(select(Quote).where(Quote.booking_id == self.booking.id))
            old.stripe_checkout_session_id = "cs_test_old"
            await db.commit()
        close = AsyncMock()
        with patch.object(bookings_endpoint, "_close_quote_checkout", new=close):
            await self._approve(deposit=150, time="15:00")
        close.assert_awaited_once()
        self.assertEqual(close.await_args.args[0].stripe_checkout_session_id, "cs_test_old")
        async with self.sessions() as db:
            old = await db.get(Quote, old.id)
            self.assertEqual(old.status, QuoteStatus.EXPIRED)

    async def test_reapproval_stops_when_older_checkout_was_paid(self):
        await self._set_stripe(True)
        await self._approve(deposit=100)
        paid = AsyncMock(side_effect=HTTPException(status_code=409, detail="paid"))
        with patch.object(bookings_endpoint, "_close_quote_checkout", new=paid):
            async with self.sessions() as db:
                old = await db.scalar(select(Quote).where(Quote.booking_id == self.booking.id))
                old.stripe_checkout_session_id = "cs_test_paid"
                await db.commit()
            with self.assertRaises(HTTPException) as ctx:
                await self._approve(deposit=150, time="15:00")
        self.assertEqual(ctx.exception.status_code, 409)
        async with self.sessions() as db:
            quotes = (await db.scalars(select(Quote).where(Quote.booking_id == self.booking.id))).all()
            self.assertEqual(len(quotes), 1, "no second quote when the first deposit is already paid")

    async def test_quote_checkout_completion_closes_the_deposit_task(self):
        from app.api.v1.endpoints import stripe_webhooks

        await self._set_stripe(True)
        await self._approve(deposit=100)
        async with self.sessions() as db:
            quote = await db.scalar(select(Quote).where(Quote.booking_id == self.booking.id))
        seen = {}

        async def record_sync(db, *, booking, source_event, payment_intent_id):
            seen.update(status=booking.status, paid=booking.deposit_paid, source=source_event, pi=payment_intent_id)

        session = {
            "id": "cs_test_paid",
            "payment_intent": "pi_test_paid",
            "amount_total": 10400,
            "payment_status": "paid",
            "metadata": {"quote_token": quote.token},
        }
        with (
            patch.object(stripe_webhooks, "async_session", new=self.sessions),
            patch.object(stripe_webhooks, "_sync_hustl_deposit_task_with_visibility", new=record_sync),
            patch.object(stripe_webhooks, "reward_user_by_id", new=AsyncMock()),
            patch.object(stripe_webhooks, "_send_deposit_paid_delivery", new=AsyncMock()),
        ):
            await stripe_webhooks._handle_checkout_completed(session)
        self.assertEqual(seen["source"], "quote_checkout_completed")
        self.assertEqual(seen["status"], BookingStatus.CONFIRMED)
        self.assertTrue(seen["paid"])
        self.assertEqual(seen["pi"], "pi_test_paid")

    async def test_deposit_approval_clears_a_stale_schedule(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.booking.id)
            booking.scheduled_date = SLOT_DAY
            booking.scheduled_time = "10:00"
            await db.commit()
        await self._set_stripe(True)
        await self._approve(deposit=100, time="15:00")
        async with self.sessions() as db:
            booking = await db.get(Booking, self.booking.id)
            self.assertIsNone(booking.scheduled_date)
            self.assertIsNone(booking.scheduled_time)

    async def test_reapproval_supersedes_the_unsent_approval_email(self):
        await self._set_stripe(False)
        await self._approve(deposit=100)
        await self._approve(deposit=150, time="15:00")
        async with self.sessions() as db:
            intents = sorted(await self._intents(db), key=lambda row: row.created_at)
            self.assertEqual([row.details["status"] for row in intents], ["superseded", "queued"])
            self.assertEqual(intents[1].details["payload"]["scheduled_time"], "15:00")

    async def test_reschedule_refuses_a_deposit_pending_approval(self):
        await self._set_stripe(True)
        await self._approve(deposit=100)
        body = BookingReschedule(scheduled_date=SLOT_DAY, scheduled_time="15:00")
        async with self.sessions() as db:
            actor = await db.get(User, self.artist.id)
            with self.assertRaises(HTTPException) as ctx:
                await bookings_endpoint.reschedule_booking(self.booking.id, body, db=db, current_user=actor)
        self.assertEqual(ctx.exception.status_code, 409)
        async with self.sessions() as db:
            booking = await db.get(Booking, self.booking.id)
            self.assertEqual(booking.status, BookingStatus.QUOTED)

    async def test_approval_closes_the_public_request_checkout(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.booking.id)
            booking.stripe_checkout_session_id = "cs_test_public"
            booking.deposit_amount = 50
            await db.commit()
        await self._set_stripe(True)
        close = AsyncMock()
        with patch.object(bookings_endpoint, "_close_checkout_session", new=close):
            result = await self._approve(deposit=100)
        close.assert_awaited_once()
        self.assertEqual(close.await_args.args[0], "cs_test_public")
        self.assertEqual(float(close.await_args.kwargs["deposit_amount"]), 50.0)
        self.assertTrue(result.deposit_url.endswith("/quote/" + result.deposit_url.rsplit("/", 1)[-1]))

    async def test_expiry_of_a_superseded_checkout_keeps_the_reapproved_booking(self):
        from app.api.v1.endpoints import stripe_webhooks

        await self._set_stripe(True)
        await self._approve(deposit=100)
        async with self.sessions() as db:
            first = await db.scalar(select(Quote).where(Quote.booking_id == self.booking.id))
        with patch.object(bookings_endpoint, "_close_quote_checkout", new=AsyncMock()):
            await self._approve(deposit=150, time="15:00")
        async with self.sessions() as db:
            second = await db.scalar(select(Quote).where(Quote.booking_id == self.booking.id, Quote.id != first.id))

        def expired(quote):
            return {
                "id": f"cs_{quote.id.hex}",
                "metadata": {"quote_token": quote.token, "booking_id": str(self.booking.id)},
            }

        with (
            patch.object(stripe_webhooks, "async_session", new=self.sessions),
            patch.object(stripe_webhooks, "_sync_hustl_deposit_task_with_visibility", new=AsyncMock()) as sync,
        ):
            await stripe_webhooks._handle_checkout_expired(expired(first))
            async with self.sessions() as db:
                self.assertEqual((await db.get(Booking, self.booking.id)).status, BookingStatus.QUOTED)
            sync.assert_not_awaited()

            await stripe_webhooks._handle_checkout_expired(expired(second))
            async with self.sessions() as db:
                self.assertEqual((await db.get(Booking, self.booking.id)).status, BookingStatus.EXPIRED)

    async def test_held_flash_deposit_is_not_approved_twice(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.booking.id)
            booking.deposit_authorization_status = "authorized"
            await db.commit()
        with self.assertRaises(HTTPException) as ctx:
            await self._approve(deposit=100)
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_reapproval_releases_the_collectors_own_checkout_hold(self):
        from app.models.booking_slot_hold import BookingSlotHold

        await self._set_stripe(True)
        await self._approve(deposit=100)
        async with self.sessions() as db:
            quote = await db.scalar(select(Quote).where(Quote.booking_id == self.booking.id))
            hold = BookingSlotHold(
                hold_token=uuid4().hex,
                artist_id=self.artist.id,
                artist_slug="rowan",
                scheduled_date=SLOT_DAY,
                scheduled_time="13:00",
                duration_minutes=120,
                status="active",
                inquiry_id=f"quote:{quote.id}",
                booking_id=self.booking.id,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
            db.add(hold)
            await db.commit()
        with patch.object(bookings_endpoint, "_close_quote_checkout", new=AsyncMock()):
            result = await self._approve(deposit=100)
        self.assertEqual(result.status, BookingStatus.QUOTED)
        async with self.sessions() as db:
            self.assertEqual((await db.get(BookingSlotHold, hold.id)).status, "released")

    async def test_deposit_without_stripe_sends_no_link(self):
        await self._set_stripe(False)
        result = await self._approve(deposit=150)
        self.assertEqual(result.status, BookingStatus.QUOTED)
        self.assertFalse(result.deposit_link_sent)
        self.assertFalse(result.stripe_ready)
        async with self.sessions() as db:
            self.assertIsNone(await db.scalar(select(Quote.id).where(Quote.booking_id == self.booking.id)))
            [intent] = await self._intents(db)
            self.assertIsNone(intent.details["payload"]["deposit_url"])
            self.assertEqual(intent.details["payload"]["deposit_amount"], 156.0, "collector sees the customer price")

    async def test_zero_deposit_confirms_and_schedules(self):
        result = await self._approve(deposit=0)
        self.assertEqual(result.status, BookingStatus.CONFIRMED)
        bookings_endpoint._post_booking_calendar_event.assert_awaited_once()
        async with self.sessions() as db:
            booking = await db.get(Booking, self.booking.id)
            self.assertEqual(booking.scheduled_date, SLOT_DAY)
            self.assertEqual(booking.scheduled_time, "13:00")
            [intent] = await self._intents(db)
            self.assertEqual(intent.details["payload"]["deposit_amount"], 0.0)

    async def test_reapproval_retires_old_link_and_queues_a_new_email(self):
        await self._set_stripe(True)
        await self._approve(deposit=100)
        await self._approve(deposit=120, time="15:00")
        async with self.sessions() as db:
            quotes = list((await db.execute(select(Quote).where(Quote.booking_id == self.booking.id))).scalars())
            self.assertEqual(sorted(q.status for q in quotes), [QuoteStatus.EXPIRED, QuoteStatus.SENT])
            self.assertEqual(len(await self._intents(db)), 2)

    async def test_confirmed_booking_cannot_be_approved(self):
        await self._approve(deposit=0)
        with self.assertRaises(HTTPException) as raised:
            await self._approve(deposit=0)
        self.assertEqual(raised.exception.status_code, 409)

    async def test_other_artist_cannot_approve(self):
        with self.assertRaises(HTTPException) as raised:
            await self._approve(deposit=100, user=self.other_artist)
        self.assertEqual(raised.exception.status_code, 403)

    async def test_defaults_prefer_session_type_and_report_stripe_gate(self):
        await self._set_stripe(False)
        async with self.sessions() as db:
            db.add(
                ArtistSessionType(
                    artist_id=self.artist.id,
                    label="Small",
                    size="small",
                    placements=["Right wrist"],
                    duration_minutes=90,
                    deposit_amount=75,
                )
            )
            await db.commit()
        async with self.sessions() as db:
            artist = await db.get(User, self.artist.id)
            defaults = await bookings_endpoint.get_booking_approval_defaults(
                self.booking.id, db=db, current_user=artist
            )
        self.assertEqual((defaults.deposit_amount, defaults.deposit_source), (75.0, "session_type"))
        self.assertFalse(defaults.stripe_ready)
        self.assertTrue(defaults.can_self_onboard)

    async def test_defaults_fall_back_to_duration_rule_and_consultation_zero(self):
        async with self.sessions() as db:
            artist = await db.get(User, self.artist.id)
            defaults = await bookings_endpoint.get_booking_approval_defaults(
                self.booking.id, db=db, current_user=artist
            )
            self.assertEqual(defaults.deposit_source, "duration_rule")
            booking = await db.get(Booking, self.booking.id)
            booking.booking_kind = "consultation"
            await db.commit()
        async with self.sessions() as db:
            artist = await db.get(User, self.artist.id)
            defaults = await bookings_endpoint.get_booking_approval_defaults(
                self.booking.id, db=db, current_user=artist
            )
        self.assertEqual((defaults.deposit_amount, defaults.deposit_source), (0.0, "consultation"))


if __name__ == "__main__":
    unittest.main()
