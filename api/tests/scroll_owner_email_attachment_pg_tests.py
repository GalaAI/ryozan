"""Real PostgreSQL proof that sent owner emails drop their stored scroll PDF bytes on boot.

Owner emails queued before the link-only rule stored the scroll PDF, which can embed the artist's
government ID. The boot step clears those bytes from rows already sent, leaves pending rows alone
(the outbox processor clears them on claim), is idempotent, and logs counts only.

Runs in an isolated schema of RYOZAN_TEST_DATABASE_URL with synthetic rows only.
"""

import logging
import unittest
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import legacy_env
from app.core.database import Base
from app.models.scroll_owner_email_outbox import ScrollOwnerEmailOutbox
from app.models.user import User  # noqa: F401 - registers the founder foreign key target
from app.services.scroll_archive import clear_sent_owner_email_attachments

TEST_DATABASE_URL = legacy_env.getenv("RYOZAN_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL.startswith("postgresql://"):
    TEST_DATABASE_URL = TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

SYNTHETIC_EMAIL = "owner-fixture@example.test"
SYNTHETIC_PDF = b"%PDF-1.4 synthetic"


@unittest.skipUnless(TEST_DATABASE_URL, "set RYOZAN_TEST_DATABASE_URL for the owner email attachment proof")
class SentOwnerEmailAttachmentPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"owner_email_{uuid4().hex}"
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

        now = datetime.now(UTC)
        self.rows: dict[str, ScrollOwnerEmailOutbox] = {}
        specs = {
            "sent_a": ("sent", SYNTHETIC_PDF),
            "sent_b": ("sent", SYNTHETIC_PDF),
            "sent_empty": ("sent", None),
            "queued": ("queued", SYNTHETIC_PDF),
            "failed": ("failed", SYNTHETIC_PDF),
            "processing": ("processing", SYNTHETIC_PDF),
        }
        async with self.sessions() as db:
            for name, (status, content) in specs.items():
                row = ScrollOwnerEmailOutbox(
                    id=uuid4(),
                    founder_id=None,
                    submission_id=f"synthetic-{name}",
                    to_email=SYNTHETIC_EMAIL,
                    subject="Artist Scroll Submitted: Synthetic Artist",
                    html_body="<p>synthetic</p>",
                    attachment_filename="artist-scroll-Synthetic_Artist.pdf" if content else None,
                    attachment_content=content,
                    status=status,
                    attempts=1 if status != "queued" else 0,
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                self.rows[name] = row
            await db.commit()

    async def _cleanup(self):
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    async def _run(self) -> tuple[tuple[int, int], list[str]]:
        with self.assertLogs("ryozan.scroll_archive", level=logging.INFO) as captured:
            async with self.engine.begin() as connection:
                outcome = await clear_sent_owner_email_attachments(connection)
        return outcome, [record.getMessage() for record in captured.records]

    async def _contents(self) -> dict[str, tuple[bytes | None, str | None]]:
        async with self.sessions() as db:
            stored = (await db.execute(select(ScrollOwnerEmailOutbox))).scalars().all()
        by_id = {row.id: (row.attachment_content, row.attachment_filename) for row in stored}
        return {name: by_id[row.id] for name, row in self.rows.items()}

    async def test_sent_rows_are_cleared_pending_rows_keep_bytes_and_rerun_clears_nothing(self):
        first, first_logs = await self._run()
        self.assertEqual(first, (2, 2))

        contents = await self._contents()
        for name in ("sent_a", "sent_b", "sent_empty"):
            self.assertEqual(contents[name], (None, None), name)
        for name in ("queued", "failed", "processing"):
            self.assertEqual(contents[name][0], SYNTHETIC_PDF, name)

        second, second_logs = await self._run()
        self.assertEqual(second, (0, 0))
        self.assertEqual((await self._contents())["queued"][0], SYNTHETIC_PDF)

        # Counts only: no row data, address, id, subject or filename ever reaches the log.
        self.assertEqual(
            first_logs,
            ["Sent owner emails still holding PDF bytes: 2", "Cleared PDF bytes from sent owner emails: 2"],
        )
        self.assertEqual(
            second_logs,
            ["Sent owner emails still holding PDF bytes: 0", "Cleared PDF bytes from sent owner emails: 0"],
        )
        joined = "\n".join(first_logs + second_logs)
        for needle in (
            SYNTHETIC_EMAIL,
            "synthetic-",
            "Synthetic",
            "artist-scroll",
            *(str(r.id) for r in self.rows.values()),
        ):
            self.assertNotIn(needle, joined)


if __name__ == "__main__":
    unittest.main()
