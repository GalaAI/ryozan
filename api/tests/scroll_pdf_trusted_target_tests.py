"""
Scroll PDF recovery writes a PDF URL only onto records the server itself created for the submission.

data_json is client-writable, so a caller who knows another submission's id could once create a record
carrying it. Every recovery path (held-PDF retry, boot move, boot outbox clear) must never hand that
record the victim's private PDF, while the legitimate records still get it. Callers below admin can no
longer create such a record at all (document_submit_provenance_tests).

Synthetic fixtures only: no real artist, ID or signature data; hosts are *.example.test.
"""

import base64
import json
import logging
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from scroll_public_pdf_move_tests import (  # noqa: E402
    _R2,
    HOST,
    PDF_BYTES,
    PUBLIC,
    _Db,
    _outbox_row,
    _r2_patches,
)

from app.api.v1.endpoints import documents  # noqa: E402
from app.models.document import DocumentInstance  # noqa: E402
from app.services import scroll_archive, storage  # noqa: E402
from app.services.scroll_archive import OWNER_ARCHIVE_CATEGORY  # noqa: E402

SID = "synthetic-victim-submission"
VICTIM = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
COLLECTOR = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
PREFIX = f"scrolls/{VICTIM}/20261110T120000-abcd1234"
ID_URL = f"https://{HOST}/{PREFIX}/{storage.SCROLL_ID_PDF_FILENAME}"


def _doc(category, owner, file_url=None, *, prefix=PREFIX, artist=VICTIM):
    data = {
        "submission_id": SID,
        "storage_prefix": prefix,
        "scroll_pdf_url": file_url,
        "fields": {"scroll_pdf_url": file_url},
    }
    if category == OWNER_ARCHIVE_CATEGORY and artist is not None:
        data["artist_user_id"] = str(artist)
    return DocumentInstance(
        id=uuid.uuid4(), user_id=owner, category=category, title="t", data_json=json.dumps(data), file_url=file_url
    )


def _records():
    """The victim's archive copy and own scroll, plus a collector's forged record naming the same submission."""
    archive = _doc(OWNER_ARCHIVE_CATEGORY, uuid.uuid4())
    artist = _doc("scroll", VICTIM)
    forged = _doc("scroll", COLLECTOR, prefix=f"scrolls/{COLLECTOR}/forged")
    return archive, artist, forged


def _row(document_ids=None, artist=None, filename=storage.SCROLL_ID_PDF_FILENAME):
    row = _outbox_row(filename)
    row.submission_id = SID
    row.document_ids = json.dumps([str(i) for i in document_ids]) if document_ids is not None else None
    row.artist_user_id = str(artist) if artist else None
    return row


async def _retry(test, db, r2):
    with (
        patch.object(storage, "upload_file", r2.upload),
        test.assertLogs("ryozan.scroll_archive", level=logging.INFO) as captured,
    ):
        outcome = await scroll_archive.retry_unstored_scroll_pdfs(db)
    return outcome, [r.getMessage() for r in captured.records]


class RetryTrustedTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_ids_get_the_pdf_and_a_forged_record_never_does(self):
        archive, artist, forged = _records()
        row = _row([artist.id, archive.id], VICTIM)
        r2 = _R2()
        outcome, logs = await _retry(self, _Db([archive, artist, forged], outbox=[row]), r2)
        self.assertEqual(outcome, (1, 1, 0))
        self.assertEqual((archive.file_url, artist.file_url), (ID_URL, ID_URL))
        self.assertIsNone(forged.file_url)
        self.assertNotIn(ID_URL, forged.data_json)
        self.assertEqual(set(r2.objects), {f"{PREFIX}/{storage.SCROLL_ID_PDF_FILENAME}"})
        self.assertIsNone(row.attachment_content)
        self.assertIn("Scroll PDF records skipped as unproven: 1", logs)
        self.assertNotIn(SID, "\n".join(logs))

    async def test_the_folder_comes_from_trusted_records_never_a_forged_one(self):
        archive, artist, forged = _records()
        # The forged record is listed first, as a database may return it.
        row = _row([artist.id, archive.id], VICTIM)
        r2 = _R2()
        await _retry(self, _Db([forged, archive, artist], outbox=[row]), r2)
        self.assertEqual(set(r2.objects), {f"{PREFIX}/{storage.SCROLL_ID_PDF_FILENAME}"})

    async def test_legacy_row_uses_only_trusted_relationships_and_counts_the_rest(self):
        archive, artist, forged = _records()
        row = _row(None)
        outcome, logs = await _retry(self, _Db([forged, archive, artist], outbox=[row]), _R2())
        self.assertEqual(outcome, (1, 1, 0))
        self.assertEqual((archive.file_url, artist.file_url), (ID_URL, ID_URL))
        self.assertIsNone(forged.file_url)
        self.assertIn("Scroll PDF records skipped as unproven: 1", logs)

    async def test_legacy_unprovable_targets_keep_the_bytes_and_are_counted(self):
        forged = _doc("scroll", COLLECTOR, prefix=f"scrolls/{COLLECTOR}/forged")
        row = _row(None)
        r2 = _R2()
        outcome, logs = await _retry(self, _Db([forged], outbox=[row]), r2)
        self.assertEqual(outcome, (1, 0, 1))
        self.assertEqual(r2.objects, {})
        self.assertIsNone(forged.file_url)
        self.assertEqual(row.attachment_content, PDF_BYTES, "the only copy is kept")
        self.assertIn("Scroll PDF records skipped as unproven: 1", logs)

    async def test_archive_artist_must_match_the_folder(self):
        archive = _doc(OWNER_ARCHIVE_CATEGORY, uuid.uuid4(), prefix=f"scrolls/{COLLECTOR}/x")
        r2 = _R2()
        await _retry(self, _Db([archive], outbox=[_row([archive.id], VICTIM)]), r2)
        # The recorded artist wins over a folder naming someone else: scrolls/<artist>/<submission>.
        self.assertEqual(set(r2.objects), {f"scrolls/{VICTIM}/{SID}/{storage.SCROLL_ID_PDF_FILENAME}"})


class ConfirmedArchiveTests(unittest.TestCase):
    def test_only_an_owner_archive_record_confirms_a_stored_pdf(self):
        sql = scroll_archive._archive_pdf_confirmed_sql("o")
        self.assertIn(f"d.category = '{OWNER_ARCHIVE_CATEGORY}'", sql)
        self.assertNotIn("'scroll',", sql)


class BootMoveTrustedTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_forged_record_is_never_repointed_at_the_private_copy(self):
        old_key = f"{PUBLIC}/{storage.SCROLL_NOID_PDF_FILENAME}"
        old_url = f"https://{HOST}/{old_key}"
        new_url = f"https://{HOST}/{storage.private_scroll_prefix(PUBLIC)}/{storage.SCROLL_NOID_PDF_FILENAME}"
        archive = _doc(OWNER_ARCHIVE_CATEGORY, uuid.uuid4(), old_url)
        artist = _doc("scroll", VICTIM, old_url)
        forged = _doc("scroll", COLLECTOR, old_url)
        db, r2 = _Db([archive, artist, forged]), _R2({old_key: PDF_BYTES})
        p1, p2, p3, p4 = _r2_patches(r2)
        with p1, p2, p3, p4, self.assertLogs("ryozan.scroll_archive", level=logging.INFO) as captured:
            counts = await scroll_archive.move_public_scroll_pdfs(db)
        self.assertEqual((archive.file_url, artist.file_url), (new_url, new_url))
        self.assertEqual(forged.file_url, old_url, "the forged record is left as it was")
        self.assertNotIn(new_url, forged.data_json)
        self.assertEqual(counts["unproven records skipped"], 1)
        self.assertEqual(counts["public deleted"], 1, "a forged reference never keeps the public PDF alive")
        self.assertNotIn(old_key, r2.objects)
        self.assertNotIn(str(COLLECTOR), "\n".join(r.getMessage() for r in captured.records))


class SubmitRecordsTargetIdsTests(unittest.IsolatedAsyncioTestCase):
    async def test_held_pdf_names_the_records_submit_created(self):
        from scroll_pdf_private_tests import _acceptances, _png, _QueueSession
        from waiver_id_private_tests import _settings

        async def _fail_pdf(*, file_bytes, filename, mime_type, folder_path):
            return None if filename.endswith(".pdf") else f"https://{HOST}/{folder_path}/{filename}"

        artist = SimpleNamespace(
            id=uuid.uuid4(),
            name="Synthetic Artist",
            email="a@example.test",
            role="artist",
            bttcoin_balance=0,
            quest_points=0,
        )
        founders = [SimpleNamespace(id=uuid.uuid4(), name="Owner", email=f"o{i}@example.test") for i in range(2)]
        session = _QueueSession([], None)
        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(documents, "upload_to_blob", _fail_pdf),
            patch.object(documents, "resolve_founders", AsyncMock(return_value=founders)),
        ):
            await documents.submit_scroll(
                request=SimpleNamespace(headers={"user-agent": "unit-test"}),
                artist_signature=base64.b64encode(_png(11, 3)).decode(),
                fields_json=json.dumps(
                    {"terms_version": documents.ARTIST_SCROLL_TERMS_VERSION, "section_acceptances": _acceptances()}
                ),
                bbp_cert=None,
                first_aid_cert=None,
                cpr_cert=None,
                govt_id=None,
                db=session,
                current_user=artist,
            )
        (held,) = [r for r in session.added if type(r).__name__ == "ScrollPdfPendingUpload"]
        created = [r for r in session.added if isinstance(r, DocumentInstance)]
        self.assertEqual(len(created), 3)
        self.assertTrue(all(r.id is not None for r in created))
        self.assertTrue(held.content.startswith(b"%PDF"))
        self.assertEqual(set(json.loads(held.document_ids)), {str(r.id) for r in created})
        self.assertEqual(held.artist_user_id, str(artist.id))
        self.assertTrue(held.folder_path.startswith(f"scrolls/{artist.id}/"))
        # The owner emails never carry the bytes.
        for row in (r for r in session.added if type(r).__name__ == "ScrollOwnerEmailOutbox"):
            self.assertIsNone(row.attachment_content)

    def test_rows_with_no_held_pdf_record_no_targets(self):
        from scroll_pdf_private_tests import _QueueSession

        session = _QueueSession()
        artist = SimpleNamespace(id=uuid.uuid4(), name="Synthetic Artist")
        founder = SimpleNamespace(id=uuid.uuid4(), email="o@example.test")
        scroll_archive.queue_owner_emails(
            session,
            artist,
            [founder],
            scroll_archive.datetime.now(scroll_archive.UTC),
            [],
            True,
            SID,
            document_ids=[uuid.uuid4()],
        )
        (row,) = session.added
        self.assertIsNone(row.document_ids)
        self.assertIsNone(row.artist_user_id)


if __name__ == "__main__":
    unittest.main()
