"""Real PostgreSQL constraints and POS attempt data proof.

The existing booking fixture owns an isolated-schema test convention; unit
tests cannot prove concurrent uniqueness. This suite reuses that convention for
the existing POS record only. The POS v1 columns and constraints themselves are
part of the Alembic baseline (alembic/baseline/20261031_baseline_schema.sql).
"""

import asyncio
import contextlib
import io
import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import asyncpg
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.booking import Booking
from app.models.message import Conversation  # noqa: F401 - register Booking's existing foreign key target
from app.models.pos_transaction import POSTransaction
from app.models.user import BTTCOUser
from app.schemas.pos import POSAttemptResponse, POSBalanceSnapshot, POSReceiptContext

TEST_DATABASE_URL = os.environ.get("BTTCO_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL.startswith("postgresql://"):
    TEST_DATABASE_URL = TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


@unittest.skipUnless(TEST_DATABASE_URL, "set BTTCO_TEST_DATABASE_URL for isolated POS PostgreSQL proof")
class POSAttemptPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"pos_attempt_{uuid4().hex}"
        self.admin_engine = create_async_engine(TEST_DATABASE_URL)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": self.schema}},
        )
        self.addAsyncCleanup(self._cleanup)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.artist_id, self.operator_id, self.other_artist_id = uuid4(), uuid4(), uuid4()
        self.booking_id = uuid4()
        async with self.sessions() as db:
            for identity, role in (
                (self.artist_id, "artist"),
                (self.operator_id, "assistant"),
                (self.other_artist_id, "artist"),
            ):
                db.add(
                    BTTCOUser(
                        id=identity,
                        email=f"fixture-{identity}@example.test",
                        password_hash="disabled",
                        name="POS fixture",
                        role=role,
                    )
                )
            await db.flush()
            db.add(
                Booking(
                    id=self.booking_id,
                    artist_id=self.artist_id,
                    description="POS schema fixture",
                    deposit_amount=52,
                    deposit_paid=False,
                    stripe_payment_intent_id="pi_originalDeposit",
                    stripe_checkout_session_id="cs_originalDeposit",
                )
            )
            await db.commit()

    async def _cleanup(self):
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    def _attempt(self, **overrides):
        return POSTransaction(
            **{
                "id": uuid4(),
                "artist_id": self.artist_id,
                "created_by": self.operator_id,
                "booking_id": None,
                "amount_cents": 12_400,
                "commission_base_cents": 10_000,
                "price_adjustment_cents": 400,
                "payment_method": "card",
                "processor": "stripe",
                "status": "pending",
                "attempt_version": 1,
                "request_key": f"attempt-{uuid4().hex}",
                "request_fingerprint": "f" * 64,
                "collection_method": "qr",
                "attempt_state": "prepared",
                "attempt_revision": 0,
                "currency": "USD",
                "livemode": False,
                "tip_cents": 2_000,
                "stripe_account_id": "acct_artist",
                "routing_destination": "artist",
                "routing_reason": "connected",
                "commission_rate": 70,
                "artist_cut_cents": 9_000,
                "artist_tip_cents": 2_000,
                "shop_cut_cents": 3_400,
                "payout_owed_cents": 0,
                "balance_snapshot": POSBalanceSnapshot(
                    livemode=False,
                    service_total_cents=10_000,
                    paid_service_cents=0,
                    refunded_service_cents=0,
                    remaining_service_cents=10_000,
                    evidence_keys=[],
                ).model_dump(),
                "receipt_context": POSReceiptContext(description="POS schema fixture").model_dump(),
                "updated_at": datetime.now(UTC),
                **overrides,
            }
        )

    async def _save(self, row):
        async with self.sessions() as db:
            db.add(row)
            await db.commit()
        return row

    async def _reject(self, **overrides):
        with self.assertRaises(IntegrityError):
            await self._save(self._attempt(**overrides))

    async def test_persisted_attempt_retains_operator_artist_receipt_and_deposit_references(self):
        row = await self._save(self._attempt(booking_id=self.booking_id))
        async with self.sessions() as db:
            saved = await db.get(POSTransaction, row.id)
            response = POSAttemptResponse.model_validate(saved)
            self.assertEqual(response.artist_id, self.artist_id)
            self.assertEqual(response.created_by, self.operator_id)
            self.assertNotEqual(response.artist_id, response.created_by)
            self.assertEqual(response.receipt_context.description, "POS schema fixture")
            booking = await db.get(Booking, self.booking_id)
            self.assertEqual(booking.stripe_payment_intent_id, "pi_originalDeposit")
            self.assertEqual(booking.stripe_checkout_session_id, "cs_originalDeposit")
            self.assertEqual(float(booking.deposit_amount), 52)
            self.assertFalse(booking.deposit_paid)

    async def test_request_identity_and_payment_identity_are_account_scoped(self):
        first = await self._save(self._attempt(stripe_payment_intent_id="pi_duplicate"))
        await self._reject(request_key=first.request_key)
        await self._reject(request_key=first.request_key, request_fingerprint="a" * 64)
        await self._reject(stripe_payment_intent_id="pi_duplicate")
        await self._save(self._attempt(request_key=first.request_key, created_by=self.other_artist_id))
        await self._save(self._attempt(stripe_payment_intent_id="pi_duplicate", stripe_account_id="acct_other"))

    async def test_saved_payment_owner_survives_artist_account_change(self):
        row = await self._save(self._attempt())
        async with self.sessions() as db:
            artist = await db.get(BTTCOUser, self.artist_id)
            artist.stripe_account_id = "acct_changed"
            await db.commit()
        async with self.sessions() as db:
            saved = await db.get(POSTransaction, row.id)
            self.assertEqual(saved.stripe_account_id, "acct_artist")

    async def test_concurrent_requests_cannot_reserve_the_same_booking(self):
        results = await asyncio.gather(
            self._save(self._attempt(booking_id=self.booking_id)),
            self._save(self._attempt(booking_id=self.booking_id)),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, POSTransaction) for result in results), 1)
        self.assertEqual(sum(isinstance(result, IntegrityError) for result in results), 1)
        winner = next(result for result in results if isinstance(result, POSTransaction))
        async with self.sessions() as db:
            row = await db.get(POSTransaction, winner.id)
            row.attempt_state, row.status, row.settled_at = "succeeded", "completed", datetime.now(UTC)
            row.stripe_payment_intent_id = "pi_winner"
            await db.commit()
        await self._save(self._attempt(booking_id=self.booking_id))

    async def test_platform_reader_cannot_be_reserved_by_two_connected_artists(self):
        reader = {
            "collection_method": "reader",
            "terminal_reader_id": "tmr_shared",
            "reader_ownership": "platform_preview",
            "reader_eligibility_ref": "isolated-test-fixture:preview-enrollment",
        }
        first = await self._save(self._attempt(**reader))
        await self._reject(**reader, artist_id=self.other_artist_id, stripe_account_id="acct_other")
        async with self.sessions() as db:
            row = await db.get(POSTransaction, first.id)
            row.attempt_state, row.status = "canceled", "voided"
            await db.commit()
        await self._save(self._attempt(**reader, artist_id=self.other_artist_id, stripe_account_id="acct_other"))

    async def test_connected_reader_owner_must_match_direct_charge_account(self):
        reader = {
            "collection_method": "reader",
            "terminal_reader_id": "tmr_connected",
            "reader_ownership": "connected_account",
        }
        await self._reject(**reader, reader_account_id="acct_wrong")
        await self._save(self._attempt(**reader, reader_account_id="acct_artist"))
        await self._reject(collection_method="reader", terminal_reader_id="tmr_other", reader_ownership="platform")

    async def test_preview_reader_requires_server_verified_eligibility_reference(self):
        await self._reject(
            collection_method="reader", terminal_reader_id="tmr_preview", reader_ownership="platform_preview"
        )

    async def test_money_state_context_and_identity_constraints_reject_invalid_attempts(self):
        for invalid in (
            {"tip_cents": -1},
            {"amount_cents": 12_401},
            {"artist_cut_cents": 8_999},
            {"artist_tip_cents": 500},
            {"payout_owed_cents": 1},
            {"processor": None},
            {"request_fingerprint": "bad"},
            {"request_key": "bad"},
            {"currency": "EUR"},
            {"livemode": None},
            {"livemode": True},
            {"balance_snapshot": {}},
            {"receipt_context": {}},
            {"attempt_revision": -1},
            {"attempt_state": "succeeded", "status": "completed"},
            {"status": "completed"},
            {"attempt_version": None},
            {"stripe_account_id": None},
        ):
            with self.subTest(invalid=invalid):
                await self._reject(**invalid)

    async def test_real_startup_preserves_tip_only_attempt_and_legacy_values(self):
        import main

        # The seeds write to tables beyond this fixture's POS slice (scroll
        # categories, quests). Startup no longer creates tables, so the fixture
        # schema gets every table main.py's import chain registers.
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        balance = POSBalanceSnapshot(
            livemode=False,
            service_total_cents=10_000,
            paid_service_cents=10_000,
            refunded_service_cents=0,
            remaining_service_cents=0,
            evidence_keys=["payment:platform:pi_previous"],
        ).model_dump()
        tip = await self._save(
            self._attempt(
                amount_cents=2_000,
                commission_base_cents=0,
                price_adjustment_cents=0,
                artist_cut_cents=2_000,
                shop_cut_cents=0,
                balance_snapshot=balance,
            )
        )
        legacy = await self._save(
            POSTransaction(
                id=uuid4(),
                artist_id=self.artist_id,
                created_by=self.operator_id,
                amount_cents=5_200,
                commission_base_cents=5_000,
                price_adjustment_cents=200,
                payment_method="cash",
                status="completed",
            )
        )
        original_connect = asyncpg.connect

        async def scoped_connect(*args, **kwargs):
            kwargs["server_settings"] = {**kwargs.get("server_settings", {}), "search_path": self.schema}
            return await original_connect(*args, **kwargs)

        raw_url = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
        boot_output = io.StringIO()
        with (
            patch.object(main, "engine", self.engine),
            patch.object(main, "settings", SimpleNamespace(DATABASE_URL=raw_url)),
            patch("asyncpg.connect", side_effect=scoped_connect),
            contextlib.redirect_stdout(boot_output),
        ):
            await main._run_startup_seeds()
            await main._run_startup_seeds()
        self.assertNotIn("Startup seed FAILED", boot_output.getvalue())
        self.assertEqual(boot_output.getvalue().count("Startup seeds: done"), 2)
        async with self.sessions() as db:
            rows = (
                (await db.execute(select(POSTransaction).where(POSTransaction.id.in_([tip.id, legacy.id]))))
                .scalars()
                .all()
            )
            by_id = {row.id: row for row in rows}
            self.assertEqual(by_id[tip.id].commission_base_cents, 0)
            self.assertEqual(by_id[tip.id].tip_cents, 2_000)
            self.assertEqual(by_id[tip.id].status, "pending")
            self.assertEqual(by_id[legacy.id].commission_base_cents, 5_000)
            self.assertIsNone(by_id[legacy.id].attempt_version)
