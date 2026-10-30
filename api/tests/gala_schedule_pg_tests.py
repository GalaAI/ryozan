"""Real PostgreSQL proof that the schedule.read action filters dates as dates.

Net-new artifact justification: ai_staff_gate_tests covers tool scoping with
mocked sessions, which never type-check a comparison. The 502 this guards
against (operator does not exist: date >= character varying) only appears on
PostgreSQL, so this bounded fixture runs execute_tool against real Booking
rows in an isolated schema.
"""

import os
import unittest
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints import gala_chat
from app.core.database import Base
from app.models.booking import Booking
from app.models.message import Conversation
from app.models.user import BTTCOUser

TEST_DATABASE_URL = os.environ.get("BTTCO_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL.startswith("postgresql://"):
    TEST_DATABASE_URL = TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


@unittest.skipUnless(TEST_DATABASE_URL, "set BTTCO_TEST_DATABASE_URL to run the schedule tool on PostgreSQL")
class GalaSchedulePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"gala_schedule_{uuid4().hex}"
        self.admin_engine = create_async_engine(TEST_DATABASE_URL)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addAsyncCleanup(self._cleanup)
        self.engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"server_settings": {"search_path": self.schema}},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        tables = [BTTCOUser.__table__, Conversation.__table__, Booking.__table__]
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))

        self.founder = BTTCOUser(
            id=uuid4(), email=f"founder-{uuid4().hex}@example.test", password_hash="x", name="Founder", role="founder"
        )
        today = date.today()
        self.bookings = [
            self._booking("Scheduled in window", "confirmed", scheduled=today + timedelta(days=2)),
            self._booking("Preferred in window", "pending", preferred=today + timedelta(days=3)),
            self._booking("Past session", "confirmed", scheduled=today - timedelta(days=2)),
            self._booking("Beyond window", "quoted", scheduled=today + timedelta(days=30)),
        ]
        async with self.sessions() as db:
            db.add(self.founder)
            await db.flush()
            db.add_all(self.bookings)
            await db.commit()

    async def _cleanup(self):
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    def _booking(self, guest_name, status, *, scheduled=None, preferred=None):
        return Booking(
            id=uuid4(),
            artist_id=self.founder.id,
            guest_name=guest_name,
            status=status,
            description="Session",
            scheduled_date=scheduled,
            preferred_date=preferred,
        )

    async def test_schedule_returns_only_the_requested_window(self):
        async with self.sessions() as db:
            text_result, _components = await gala_chat.execute_tool(
                "get_schedule", {"days": 7}, self.founder, db, agent_id="gala"
            )
        self.assertIn("Scheduled in window", text_result)
        self.assertIn("Preferred in window", text_result)
        self.assertNotIn("Past session", text_result)
        self.assertNotIn("Beyond window", text_result)

    async def test_stale_bookings_compare_dates_on_postgres(self):
        async with self.sessions() as db:
            text_result, _components = await gala_chat.execute_tool(
                "get_stale_bookings", {}, self.founder, db, agent_id="gala"
            )
        self.assertNotIn("Past session", text_result)  # confirmed bookings are never stale
        self.assertIsInstance(text_result, str)


if __name__ == "__main__":
    unittest.main()
