"""POS card payments must reach ryozan_pos_transactions from the webhook.

Net-new artifact justification: the existing POS tests cover the charge and
manual-record endpoints, and the Stripe webhook tests cover deposits, booth rent
and gift cards. Neither covered the POS ledger write, which is exactly why it was
missing in production: ryozan_pos_transactions held zero rows while Stripe had
taken a real $520.00 POS payment on 2026-09-25, because the only writer was a
fire-and-forget call from a browser polling loop that died with the modal.

The metadata fixture below is the real session metadata from that payment
(cs_live_a1978z...), so these tests assert against a shape production actually
produced rather than an invented one.
"""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1.endpoints.stripe_webhooks import _handle_pos_completed, _record_pos_transaction

ARTIST_ID = "68a8d9d0-940a-46ec-ae73-803d1a0aa093"
SESSION_ID = "cs_live_a1978z1d1NTkvZjzgaJUloIT3FOgarUd0RgKrfDbNPtolXBABzcycNi59D"

# Verbatim from the 2026-09-25 production session.
POS_METADATA = {
    "pos_charge": "true",
    "checkout_type": "pos_payment",
    "artist_id": ARTIST_ID,
    "artist_name": "Justin Walker",
    "client_name": "Walk-in",
    "booking_id": "",
    "commission_base_cents": "50000",
    "customer_price_adjustment_cents": "2000",
    "description": "Tattoo session",
}


def _session(**overrides):
    values = {
        "id": SESSION_ID,
        "amount_total": 52000,
        "currency": "usd",
        "payment_status": "paid",
        "payment_intent": "pi_3UJk0ZRrvzLnK6i20OdcctJj",
        "metadata": dict(POS_METADATA),
    }
    values.update(overrides)
    return values


def _fake_db(existing_id=None):
    """A db whose existence probe returns existing_id."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=existing_id)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _patch_session(db):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "app.api.v1.endpoints.stripe_webhooks.async_session",
        MagicMock(return_value=cm),
    )


class RecordPosTransactionTests(IsolatedAsyncioTestCase):
    async def test_paid_pos_session_is_written_to_the_pos_ledger(self):
        """The regression: this row did not exist for a real $520.00 payment."""
        db = _fake_db()
        with _patch_session(db):
            await _record_pos_transaction(_session(), POS_METADATA)

        db.add.assert_called_once()
        db.commit.assert_awaited_once()
        row = db.add.call_args.args[0]
        self.assertEqual(row.amount_cents, 52000)
        self.assertEqual(str(row.artist_id), ARTIST_ID)
        self.assertEqual(row.payment_method, "card")
        self.assertEqual(row.processor, "stripe")
        self.assertEqual(row.status, "completed")
        self.assertEqual(row.stripe_session_id, SESSION_ID)
        self.assertEqual(row.client_name, "Walk-in")
        self.assertIsNone(row.booking_id)

    async def test_commission_base_and_adjustment_come_from_metadata(self):
        """$500 service price + $20 card adjustment, not $520 of base."""
        db = _fake_db()
        with _patch_session(db):
            await _record_pos_transaction(_session(), POS_METADATA)
        row = db.add.call_args.args[0]
        self.assertEqual(row.commission_base_cents, 50000)
        self.assertEqual(row.price_adjustment_cents, 2000)

    async def test_existing_row_for_the_session_is_not_duplicated(self):
        """Stripe retries webhooks; a second delivery must not double-count."""
        db = _fake_db(existing_id="already-there")
        with _patch_session(db):
            await _record_pos_transaction(_session(), POS_METADATA)
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_unique_index_violation_is_absorbed(self):
        """Two concurrent retries can both pass the probe; the index decides."""
        from sqlalchemy.exc import IntegrityError

        db = _fake_db()
        db.commit = AsyncMock(side_effect=IntegrityError("dup", None, Exception("dup")))
        with _patch_session(db):
            await _record_pos_transaction(_session(), POS_METADATA)
        db.rollback.assert_awaited_once()

    async def test_missing_artist_id_is_not_recorded(self):
        db = _fake_db()
        meta = dict(POS_METADATA, artist_id="")
        with _patch_session(db):
            await _record_pos_transaction(_session(metadata=meta), meta)
        db.add.assert_not_called()

    async def test_zero_amount_is_not_recorded(self):
        db = _fake_db()
        with _patch_session(db):
            await _record_pos_transaction(_session(amount_total=0), POS_METADATA)
        db.add.assert_not_called()

    async def test_commission_base_above_amount_is_clamped(self):
        """A bad metadata value must not invent a negative adjustment."""
        db = _fake_db()
        meta = dict(POS_METADATA, commission_base_cents="999999")
        with _patch_session(db):
            await _record_pos_transaction(_session(), meta)
        row = db.add.call_args.args[0]
        self.assertEqual(row.commission_base_cents, 52000)
        self.assertEqual(row.price_adjustment_cents, 0)

    async def test_unparseable_commission_base_falls_back_to_amount(self):
        db = _fake_db()
        meta = dict(POS_METADATA, commission_base_cents="not-a-number")
        with _patch_session(db):
            await _record_pos_transaction(_session(), meta)
        row = db.add.call_args.args[0]
        self.assertEqual(row.commission_base_cents, 52000)

    async def test_booking_linked_pos_payment_carries_the_booking_id(self):
        booking_id = "c2df3152-1024-4cfd-96c4-75301bc463dd"
        db = _fake_db()
        meta = dict(POS_METADATA, booking_id=booking_id)
        with _patch_session(db):
            await _record_pos_transaction(_session(metadata=meta), meta)
        row = db.add.call_args.args[0]
        self.assertEqual(str(row.booking_id), booking_id)


class HandlePosCompletedTests(IsolatedAsyncioTestCase):
    async def test_pos_completion_records_the_transaction(self):
        """_handle_pos_completed used to only logger.info the payment."""
        with patch(
            "app.api.v1.endpoints.stripe_webhooks._record_pos_transaction",
            new=AsyncMock(),
        ) as recorder:
            # No booking and no customer email: isolates the ledger write.
            session = _session(metadata=dict(POS_METADATA), customer_details={}, customer_email=None)
            await _handle_pos_completed(session)
        recorder.assert_awaited_once()
        self.assertEqual(recorder.await_args.args[0]["id"], SESSION_ID)


class ShopDayBoundsTests(IsolatedAsyncioTestCase):
    """POS reporting buckets by shop day, not by the server's UTC day.

    created_at is timestamptz and the API runs TZ=Etc/UTC on Railway, so naive
    bounds are interpreted as UTC. A tattoo shop takes most of its money in the
    evening: 8pm Eastern is 00:00 UTC the next day, which filed those sales
    under tomorrow and made a same-day report look empty after 8pm.
    """

    def test_bounds_are_timezone_aware(self):
        from datetime import date as _date

        from app.constants.timezones import shop_day_bounds

        start, end = shop_day_bounds(_date(2026, 9, 27), _date(2026, 9, 27))
        self.assertIsNotNone(start.tzinfo)
        self.assertIsNotNone(end.tzinfo)

    def test_an_8pm_eastern_payment_belongs_to_that_eastern_day(self):
        from datetime import date as _date
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo

        from app.constants.timezones import shop_day_bounds

        # Stored instant: 2026-09-28 00:30 UTC. In shop time that is still the 27th.
        paid_at = _dt(2026, 9, 28, 0, 30, tzinfo=ZoneInfo("Etc/UTC"))
        start, end = shop_day_bounds(_date(2026, 9, 27), _date(2026, 9, 27))
        self.assertLessEqual(start, paid_at)
        self.assertGreaterEqual(end, paid_at)

        # And it must NOT also land in the 28th's bucket.
        next_start, next_end = shop_day_bounds(_date(2026, 9, 28), _date(2026, 9, 28))
        self.assertFalse(next_start <= paid_at <= next_end)

    def test_shop_today_uses_shop_timezone(self):
        from datetime import datetime as _dt

        from app.constants.timezones import shop_today
        from app.constants.timezones import shop_timezone

        self.assertEqual(shop_today(), _dt.now(shop_timezone()).date())
