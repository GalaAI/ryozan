"""Real PostgreSQL proof of the owner email and legacy scroll boot steps.

Owner emails queued before the link-only rule stored the scroll PDF, which can embed the artist's
government ID. The boot step clears those bytes from finished rows (sent, or exhausted past the retry
limit) only when the submission's archive PDF is confirmed stored per the DB record; otherwise the
bytes may be the only copy and stay. Only an owner archive record confirms: any caller could once
create an artist scroll record naming another submission. Pending rows are never touched. It is
idempotent and logs counts only. A second boot step counts document rows that still reference legacy
scrolls/ objects.

Runs in an isolated schema of RYOZAN_TEST_DATABASE_URL with synthetic rows only.
"""

import json
import logging
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import legacy_env
from app.core.database import Base
from app.models.document import DocumentInstance
from app.models.scroll_owner_email_outbox import ScrollOwnerEmailOutbox
from app.models.scroll_pdf_pending_upload import ScrollPdfPendingUpload
from app.models.user import User  # noqa: F401 - registers the founder foreign key target
from app.services import storage
from app.services.scroll_archive import (
    OWNER_ARCHIVE_CATEGORY,
    clear_sent_owner_email_attachments,
    count_legacy_scroll_references,
    hold_pdf_for_retry,
    retry_pending_scroll_pdfs,
    retry_unstored_scroll_pdfs,
)

TEST_DATABASE_URL = legacy_env.getenv("RYOZAN_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL.startswith("postgresql://"):
    TEST_DATABASE_URL = TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

SYNTHETIC_EMAIL = "owner-fixture@example.test"
SYNTHETIC_PDF = b"%PDF-1.4 synthetic"
ASSETS = "https://assets.example.test"

# name: (status, has bytes, archive record for the submission)
#   "archive": owner archive copy with a stored file_url
#   "scroll": an artist scroll record with scroll_pdf_url in data_json only (never confirms: client-creatable)
#   "missing": an archive copy whose PDF was generated but never stored (file_url and scroll_pdf_url empty)
#   None: no document row at all
ROWS = {
    "sent_stored": ("sent", True, "archive"),
    "sent_stored_via_scroll": ("sent", True, "scroll"),
    "sent_missing": ("sent", True, "missing"),
    "sent_no_record": ("sent", True, None),
    "sent_empty": ("sent", False, "archive"),
    "exhausted_stored": ("exhausted", True, "archive"),
    "exhausted_missing": ("exhausted", True, "missing"),
    "queued_stored": ("queued", True, "archive"),
    "failed_stored": ("failed", True, "archive"),
    "processing_stored": ("processing", True, "archive"),
}


@unittest.skipUnless(TEST_DATABASE_URL, "set RYOZAN_TEST_DATABASE_URL for the owner email attachment proof")
class OwnerEmailBootStepPostgresTests(unittest.IsolatedAsyncioTestCase):
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
        async with self.sessions() as db:
            for name, (status, has_bytes, record) in ROWS.items():
                submission_id = uuid4().hex
                row = ScrollOwnerEmailOutbox(
                    id=uuid4(),
                    founder_id=None,
                    submission_id=submission_id,
                    to_email=SYNTHETIC_EMAIL,
                    subject="Artist Scroll Submitted: Synthetic Artist",
                    html_body="<p>synthetic</p>",
                    attachment_filename="artist-scroll-Synthetic_Artist.pdf" if has_bytes else None,
                    attachment_content=SYNTHETIC_PDF if has_bytes else None,
                    status=status,
                    attempts=5 if status == "exhausted" else 1,
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                self.rows[name] = row
                pdf_url = f"{ASSETS}/scrolls/artist-1/{submission_id}/artist-scroll.id.pdf"
                if record == "archive":
                    db.add(self._doc(OWNER_ARCHIVE_CATEGORY, submission_id, pdf_url, pdf_url))
                elif record == "scroll":
                    db.add(self._doc("scroll", submission_id, None, pdf_url))
                elif record == "missing":
                    db.add(self._doc(OWNER_ARCHIVE_CATEGORY, submission_id, None, None))
            # A row whose data_json names the submission id only as unrelated text never confirms.
            db.add(
                DocumentInstance(
                    category="scroll_shop",
                    title="Synthetic note",
                    status="submitted",
                    data_json=json.dumps({"note": self.rows["sent_no_record"].submission_id}),
                    file_url=f"{ASSETS}/scrolls-public/shop/u-1/note.pdf",
                )
            )
            # Legacy references for the count step: two rows point at scrolls/, one only at scrolls-public/.
            db.add(
                DocumentInstance(
                    category="scroll_shop",
                    title="Legacy upload",
                    status="submitted",
                    file_url=f"{ASSETS}/scrolls/shop/u-1/legacy.pdf",
                )
            )
            await db.commit()

    @staticmethod
    def _doc(category: str, submission_id: str, file_url: str | None, scroll_pdf_url: str | None):
        return DocumentInstance(
            category=category,
            title="Artist Scroll: Synthetic Artist",
            status="signed",
            data_json=json.dumps(
                {
                    "submission_id": submission_id,
                    "storage_prefix": f"scrolls/artist-1/{submission_id}",
                    "scroll_pdf_url": scroll_pdf_url,
                    "fields": {"legal_name": "Synthetic", "scroll_pdf_url": scroll_pdf_url},
                }
            ),
            file_url=file_url,
        )

    async def _cleanup(self):
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    async def _run(self, step) -> tuple[object, list[str]]:
        with self.assertLogs("ryozan.scroll_archive", level=logging.INFO) as captured:
            async with self.engine.begin() as connection:
                outcome = await step(connection)
        return outcome, [record.getMessage() for record in captured.records]

    async def _bytes(self) -> dict[str, bytes | None]:
        async with self.sessions() as db:
            stored = (await db.execute(select(ScrollOwnerEmailOutbox))).scalars().all()
        by_id = {row.id: row.attachment_content for row in stored}
        return {name: by_id[row.id] for name, row in self.rows.items()}

    def _assert_counts_only(self, messages: list[str]) -> None:
        joined = "\n".join(messages)
        for needle in (
            SYNTHETIC_EMAIL,
            "Synthetic",
            "artist-scroll",
            "assets.example.test",
            *(str(row.id) for row in self.rows.values()),
            *(row.submission_id for row in self.rows.values()),
        ):
            self.assertNotIn(needle, joined)
        for message in messages:
            self.assertRegex(message, r": \d+$")

    async def test_only_finished_rows_with_a_stored_archive_pdf_are_cleared(self):
        first, first_logs = await self._run(clear_sent_owner_email_attachments)
        # Finished rows holding bytes: 4 sent + 2 exhausted. Cleared: the 2 confirmed by an owner archive
        # record (1 sent, 1 exhausted). An artist scroll record alone never confirms.
        self.assertEqual(first, (6, 2, 4))

        after = await self._bytes()
        for name in ("sent_stored", "exhausted_stored", "sent_empty"):
            self.assertIsNone(after[name], name)
        for name in (
            "sent_stored_via_scroll",
            "sent_missing",
            "sent_no_record",
            "exhausted_missing",
            "queued_stored",
            "failed_stored",
            "processing_stored",
        ):
            self.assertEqual(after[name], SYNTHETIC_PDF, name)

        second, second_logs = await self._run(clear_sent_owner_email_attachments)
        self.assertEqual(second, (4, 0, 4))
        self.assertEqual(await self._bytes(), after)

        self.assertEqual(
            first_logs,
            [
                "Finished owner emails still holding PDF bytes: 6",
                "Cleared PDF bytes from finished owner emails with a stored archive PDF: 2",
                "Kept PDF bytes on finished owner emails with no confirmed archive PDF: 4",
            ],
        )
        self.assertEqual(
            second_logs,
            [
                "Finished owner emails still holding PDF bytes: 4",
                "Cleared PDF bytes from finished owner emails with a stored archive PDF: 0",
                "Kept PDF bytes on finished owner emails with no confirmed archive PDF: 4",
            ],
        )
        self._assert_counts_only(first_logs + second_logs)

    async def test_a_planted_nested_submission_id_never_confirms_or_clears(self):
        # An artist's own archive with a stored PDF carries the pending submission's id nested in their
        # typed fields. Only the archive's top-level submission id counts, so the pending bytes stay.
        pending = self.rows["sent_missing"]
        async with self.sessions() as db:
            planter_sid = uuid4().hex
            planter_url = f"{ASSETS}/scrolls/artist-2/{planter_sid}/artist-scroll.id.pdf"
            planted = self._doc(OWNER_ARCHIVE_CATEGORY, planter_sid, planter_url, planter_url)
            data = json.loads(planted.data_json)
            data["fields"]["notes"] = {"submission_id": pending.submission_id}
            data["fields"]["submission_id"] = pending.submission_id
            planted.data_json = json.dumps(data)
            db.add(planted)
            await db.commit()

        (found, cleared, kept), _logs = await self._run(clear_sent_owner_email_attachments)
        self.assertEqual((found, cleared, kept), (6, 2, 4))
        self.assertEqual((await self._bytes())["sent_missing"], SYNTHETIC_PDF)

        async def _upload(*, file_bytes, filename, mime_type, folder_path):
            return None  # storage failing: the bytes must survive

        with patch.object(storage, "upload_file", _upload), self.assertLogs("ryozan.scroll_archive", level="INFO"):
            async with self.sessions() as db:
                pending_count, stored, _failed = await retry_unstored_scroll_pdfs(db)
        # Still a retry case (not skipped as confirmed); nothing stored, so nothing cleared.
        self.assertEqual((pending_count, stored), (4, 0))
        (found, cleared, kept), _logs = await self._run(clear_sent_owner_email_attachments)
        self.assertEqual((found, cleared, kept), (4, 0, 4))
        self.assertEqual((await self._bytes())["sent_missing"], SYNTHETIC_PDF)

    async def test_a_failed_pdf_store_is_retried_recorded_and_cleared(self):
        succeed_for = self.rows["sent_missing"].submission_id
        uploaded: list[tuple[str, str]] = []

        async def _upload(*, file_bytes, filename, mime_type, folder_path):
            self.assertEqual(file_bytes, SYNTHETIC_PDF)
            if succeed_for not in folder_path:
                return None  # storage still failing for this submission
            uploaded.append((folder_path, filename))
            return f"{ASSETS}/{folder_path}/{filename}"

        async def _retry():
            async with self.sessions() as db:
                return await retry_unstored_scroll_pdfs(db)

        with patch.object(storage, "upload_file", _upload):
            with self.assertLogs("ryozan.scroll_archive", level=logging.INFO) as captured:
                first = await _retry()
            # Unconfirmed rows with bytes: sent_missing (stored now), exhausted_missing (upload fails),
            # sent_no_record (no archive record, so no storage prefix), sent_stored_via_scroll (only an
            # artist scroll record that cannot be proven the submission's own: skipped, bytes kept).
            self.assertEqual(first, (4, 1, 3))
            with self.assertLogs("ryozan.scroll_archive", level=logging.INFO) as again:
                second = await _retry()
            self.assertEqual(second, (3, 0, 3))

        # Stored under the private prefix with the ID marker name (content unknown, so private).
        self.assertEqual(uploaded, [(f"scrolls/artist-1/{succeed_for}", "artist-scroll.id.pdf")])
        after = await self._bytes()
        self.assertIsNone(after["sent_missing"])
        self.assertEqual(after["exhausted_missing"], SYNTHETIC_PDF)
        self.assertEqual(after["sent_no_record"], SYNTHETIC_PDF)
        self.assertEqual(after["sent_stored_via_scroll"], SYNTHETIC_PDF)
        self.assertEqual(after["queued_stored"], SYNTHETIC_PDF)  # confirmed archive: not a retry case

        stored_url = f"{ASSETS}/scrolls/artist-1/{succeed_for}/artist-scroll.id.pdf"
        async with self.sessions() as db:
            docs = (await db.execute(select(DocumentInstance))).scalars().all()
        doc = next(d for d in docs if d.data_json and succeed_for in d.data_json)
        self.assertEqual(doc.file_url, stored_url)
        data = json.loads(doc.data_json)
        self.assertEqual(data["scroll_pdf_url"], stored_url)
        self.assertEqual(data["fields"]["scroll_pdf_url"], stored_url)

        messages = [r.getMessage() for r in captured.records + again.records]
        self.assertEqual(
            messages[:4],
            [
                "Scroll PDFs waiting to be stored: 4",
                "Scroll PDFs stored on retry: 1",
                "Scroll PDF store retries that failed (bytes kept): 3",
                "Scroll PDF records skipped as unproven: 1",
            ],
        )
        self._assert_counts_only(messages)

        # The guarded clear step afterwards: finished rows with bytes are the 2 confirmed ones from
        # setup plus the 3 still unstored; the 2 confirmed are cleared, the 3 unstored kept.
        (found, cleared, kept), _logs = await self._run(clear_sent_owner_email_attachments)
        self.assertEqual((found, cleared, kept), (5, 2, 3))

    async def test_a_held_pdf_with_no_founder_is_kept_until_a_private_store_succeeds(self):
        submission_id = uuid4().hex
        prefix = f"scrolls/artist-1/{submission_id}"
        async with self.sessions() as db:
            record = self._doc("scroll", submission_id, None, None)
            record.id = uuid4()
            db.add(record)
            hold_pdf_for_retry(
                db,
                submission_id=submission_id,
                folder_path=prefix,
                filename=storage.SCROLL_NOID_PDF_FILENAME,
                content=SYNTHETIC_PDF,
                document_ids=[record.id],
                artist_user_id="artist-1",
            )
            await db.commit()

        fail = True

        async def _upload(*, file_bytes, filename, mime_type, folder_path):
            return None if fail else f"{ASSETS}/{folder_path}/{filename}"

        with patch.object(storage, "upload_file", _upload), self.assertLogs("ryozan.scroll_archive", level="INFO"):
            async with self.sessions() as db:
                self.assertEqual(await retry_pending_scroll_pdfs(db), (1, 0, 1))
        async with self.sessions() as db:
            (held,) = (await db.execute(select(ScrollPdfPendingUpload))).scalars().all()
            self.assertEqual((held.content, held.attempts), (SYNTHETIC_PDF, 1))
            held.next_attempt_at = datetime.now(UTC)
            await db.commit()

        fail = False
        with patch.object(storage, "upload_file", _upload), self.assertLogs("ryozan.scroll_archive", level="INFO"):
            async with self.sessions() as db:
                self.assertEqual(await retry_pending_scroll_pdfs(db), (1, 1, 0))
        url = f"{ASSETS}/{prefix}/{storage.SCROLL_NOID_PDF_FILENAME}"
        async with self.sessions() as db:
            self.assertEqual((await db.execute(select(ScrollPdfPendingUpload))).scalars().all(), [])
            stored = await db.get(DocumentInstance, record.id)
        self.assertEqual(stored.file_url, url)
        self.assertEqual(json.loads(stored.data_json)["scroll_pdf_url"], url)

    async def test_legacy_scroll_references_are_counted_and_nothing_changes(self):
        async with self.sessions() as db:
            before = [
                (d.id, d.file_url, d.data_json) for d in (await db.execute(select(DocumentInstance))).scalars().all()
            ]
        # Archive, scroll and missing records all reference scrolls/ (file_url or data_json) except
        # "missing" (no URL); plus the legacy upload. The scrolls-public/ note does not count.
        expected = sum(1 for _status, _bytes, record in ROWS.values() if record in ("archive", "scroll")) + 1
        count, logs = await self._run(count_legacy_scroll_references)
        self.assertEqual(count, expected)
        self.assertEqual(logs, [f"Document rows referencing legacy scrolls/ objects (served signed): {expected}"])
        again, _logs = await self._run(count_legacy_scroll_references)
        self.assertEqual(again, expected)
        async with self.sessions() as db:
            after = [
                (d.id, d.file_url, d.data_json) for d in (await db.execute(select(DocumentInstance))).scalars().all()
            ]
        self.assertEqual(sorted(after, key=str), sorted(before, key=str))
        self._assert_counts_only(logs)


if __name__ == "__main__":
    unittest.main()
