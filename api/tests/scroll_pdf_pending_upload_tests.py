"""
A scroll PDF whose store fails is always held server-side until a retry stores it privately.

Owner email rows exist only per founder, so they cannot be the holding place: with no founder the
only copy of the PDF used to be lost. The held row is written in the submission transaction whatever
the founders, and its bytes are deleted only after a confirmed private store, in the same commit that
records the URL on the exact records the server created.

Synthetic fixtures only: no real artist, ID or signature data; hosts are *.example.test.
"""

import base64
import json
import logging
import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from scroll_pdf_private_tests import _acceptances, _png, _QueueSession  # noqa: E402
from waiver_id_private_tests import _settings, _synthetic_id_png  # noqa: E402

from app.api.v1.endpoints import documents  # noqa: E402
from app.models.document import DocumentInstance  # noqa: E402
from app.models.scroll_pdf_pending_upload import ScrollPdfPendingUpload  # noqa: E402
from app.services import scroll_archive, storage  # noqa: E402

HOST = "assets.example.test"
FROZEN = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
PDF_BYTES = b"%PDF-1.4 synthetic held scroll"


class _Rows:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Db:
    """Session stand-in. Document queries return every record: the code must do the filtering."""

    def __init__(self, docs, held):
        self.docs, self.held = list(docs), list(held)
        self.deleted: list = []
        self.commits = 0
        self.deleted_at_commit: list[int] = []

    async def execute(self, stmt):
        sql = str(stmt)
        if "ryozan_scroll_pdf_pending_uploads" in sql.split("WHERE")[0]:
            now = datetime.now(UTC)
            return _Rows([r for r in self.held if r not in self.deleted and r.next_attempt_at <= now])
        return _Rows(self.docs)

    async def delete(self, obj):
        self.deleted.append(obj)
        self.deleted_at_commit.append(self.commits)

    async def commit(self):
        self.commits += 1


class _R2:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.objects: dict[str, bytes] = {}

    async def upload(self, *, file_bytes, filename, mime_type, folder_path):
        if self.fail:
            return None
        key = f"{folder_path}/{filename}"
        self.objects[key] = file_bytes
        return f"https://{HOST}/{key}"


def _artist():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Synthetic Artist",
        email="artist@example.test",
        role="artist",
        bttcoin_balance=0,
        quest_points=0,
    )


async def _submit_with_failing_store(artist, founders):
    async def _fail_pdf(*, file_bytes, filename, mime_type, folder_path):
        return None if filename.endswith(".pdf") else f"https://{HOST}/{folder_path}/{filename}"

    session = _QueueSession([], None)
    govt_id = SimpleNamespace(
        filename="id.png", content_type="image/png", read=AsyncMock(return_value=_synthetic_id_png())
    )
    with (
        patch.object(storage, "get_settings", return_value=_settings()),
        patch.object(documents, "upload_to_blob", _fail_pdf),
        patch.object(documents, "resolve_founders", AsyncMock(return_value=founders)),
    ):
        response = await documents.submit_scroll(
            request=SimpleNamespace(headers={"user-agent": "unit-test"}),
            artist_signature=base64.b64encode(_png(11, 3)).decode(),
            fields_json=json.dumps(
                {"terms_version": documents.ARTIST_SCROLL_TERMS_VERSION, "section_acceptances": _acceptances()}
            ),
            bbp_cert=None,
            first_aid_cert=None,
            cpr_cert=None,
            govt_id=govt_id,
            db=session,
            current_user=artist,
        )
    return response, session


def _held(submission_id, folder, document_ids, artist_id, **extra):
    return ScrollPdfPendingUpload(
        id=uuid.uuid4(),
        submission_id=submission_id,
        folder_path=folder,
        filename=storage.SCROLL_ID_PDF_FILENAME,
        content=PDF_BYTES,
        document_ids=json.dumps([str(i) for i in document_ids]),
        artist_user_id=str(artist_id),
        attempts=0,
        next_attempt_at=FROZEN,
        created_at=FROZEN,
        updated_at=FROZEN,
        **extra,
    )


def _scroll_doc(owner, submission_id, prefix):
    return DocumentInstance(
        id=uuid.uuid4(),
        user_id=owner,
        category="scroll",
        title="t",
        data_json=json.dumps({"submission_id": submission_id, "storage_prefix": prefix, "fields": {}}),
        file_url=None,
    )


async def _retry(test, db, r2):
    with (
        patch.object(storage, "upload_file", r2.upload),
        test.assertLogs("ryozan.scroll_archive", level=logging.INFO) as captured,
    ):
        outcome = await scroll_archive.retry_pending_scroll_pdfs(db)
    return outcome, [r.getMessage() for r in captured.records]


class HeldWithoutFoundersTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_fails_with_zero_founders_then_the_retry_stores_it_privately(self):
        artist = _artist()
        response, session = await _submit_with_failing_store(artist, founders=[])
        self.assertIsNone(response["scroll_pdf_url"])
        self.assertEqual([r for r in session.added if type(r).__name__ == "ScrollOwnerEmailOutbox"], [])
        (held,) = [r for r in session.added if isinstance(r, ScrollPdfPendingUpload)]
        (record,) = [r for r in session.added if isinstance(r, DocumentInstance)]
        self.assertTrue(held.content.startswith(b"%PDF"), "the only copy is held")
        self.assertEqual(json.loads(held.document_ids), [str(record.id)])
        self.assertEqual(held.artist_user_id, str(artist.id))
        self.assertTrue(held.folder_path.startswith(f"scrolls/{artist.id}/"))
        self.assertEqual(held.filename, storage.SCROLL_ID_PDF_FILENAME)

        # A forged record naming the same submission never receives the PDF.
        forged = _scroll_doc(uuid.uuid4(), held.submission_id, f"scrolls/{uuid.uuid4()}/x")
        db, r2 = _Db([forged, record], [held]), _R2()
        (due, stored, failed), logs = await _retry(self, db, r2)
        self.assertEqual((due, stored, failed), (1, 1, 0))
        key = f"{held.folder_path}/{storage.SCROLL_ID_PDF_FILENAME}"
        self.assertEqual(r2.objects, {key: held.content})
        self.assertTrue(storage.is_private_id_key(key))
        url = f"https://{HOST}/{key}"
        self.assertEqual(record.file_url, url)
        self.assertEqual(json.loads(record.data_json)["scroll_pdf_url"], url)
        self.assertIsNone(forged.file_url)
        self.assertEqual(db.deleted, [held])
        self.assertEqual(db.deleted_at_commit, [0], "deleted in the same commit that records the URL")
        self.assertEqual(db.commits, 1)
        joined = "\n".join(logs)
        self.assertIn("Held scroll PDFs stored privately: 1", joined)
        self.assertIn("Held scroll PDF records skipped as unproven: 1", joined)
        for secret in (held.submission_id, str(artist.id), HOST, "scrolls"):
            self.assertNotIn(secret, joined)

        # Staff still open it, through a short-lived signed link.
        with patch.object(storage, "get_settings", return_value=_settings()):
            view = documents._instance_to_response(record, None, None)
        self.assertIn("X-Amz-Signature=", view["file_url"])

    async def test_with_founders_the_bytes_are_held_once_and_never_on_the_emails(self):
        founders = [SimpleNamespace(id=uuid.uuid4(), name="Owner", email=f"o{i}@example.test") for i in range(2)]
        _response, session = await _submit_with_failing_store(_artist(), founders)
        held = [r for r in session.added if isinstance(r, ScrollPdfPendingUpload)]
        self.assertEqual(len(held), 1)
        emails = [r for r in session.added if type(r).__name__ == "ScrollOwnerEmailOutbox"]
        self.assertEqual(len(emails), 2)
        self.assertTrue(all(e.attachment_content is None and e.attachment_filename is None for e in emails))

    async def test_a_successful_store_holds_nothing(self):
        async def _ok(*, file_bytes, filename, mime_type, folder_path):
            return f"https://{HOST}/{folder_path}/{filename}"

        session = _QueueSession([], None)
        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(documents, "upload_to_blob", _ok),
            patch.object(documents, "resolve_founders", AsyncMock(return_value=[])),
        ):
            await documents.submit_scroll(
                request=SimpleNamespace(headers={}),
                artist_signature=base64.b64encode(_png(11, 3)).decode(),
                fields_json=json.dumps(
                    {"terms_version": documents.ARTIST_SCROLL_TERMS_VERSION, "section_acceptances": _acceptances()}
                ),
                bbp_cert=None,
                first_aid_cert=None,
                cpr_cert=None,
                govt_id=None,
                db=session,
                current_user=_artist(),
            )
        self.assertEqual([r for r in session.added if isinstance(r, ScrollPdfPendingUpload)], [])


class PendingRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_retry_keeps_the_bytes_and_backs_off(self):
        artist = uuid.uuid4()
        record = _scroll_doc(artist, "sub-1", f"scrolls/{artist}/sub-1")
        held = _held("sub-1", f"scrolls/{artist}/sub-1", [record.id], artist)
        db = _Db([record], [held])
        (due, stored, failed), _logs = await _retry(self, db, _R2(fail=True))
        self.assertEqual((due, stored, failed), (1, 0, 1))
        self.assertEqual(held.content, PDF_BYTES)
        self.assertEqual(db.deleted, [])
        self.assertEqual(held.attempts, 1)
        self.assertGreater(held.next_attempt_at, datetime.now(UTC))
        self.assertIsNone(record.file_url)

        # Not due yet: nothing happens. Once due, a working store finishes it.
        self.assertEqual((await _retry_quiet(db, _R2())), (0, 0, 0))
        held.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        (due, stored, failed), _logs = await _retry(self, db, _R2())
        self.assertEqual((due, stored, failed), (1, 1, 0))
        self.assertEqual(db.deleted, [held])
        self.assertTrue(record.file_url.endswith(f"/scrolls/{artist}/sub-1/{storage.SCROLL_ID_PDF_FILENAME}"))

    async def test_backoff_is_capped_and_never_gives_up(self):
        artist = uuid.uuid4()
        held = _held("sub-2", f"scrolls/{artist}/sub-2", [], artist)
        held.attempts = 40
        db = _Db([], [held])
        await _retry(self, db, _R2(fail=True))
        self.assertLessEqual(held.next_attempt_at - datetime.now(UTC), timedelta(minutes=60))
        self.assertEqual(held.content, PDF_BYTES)

    async def test_a_row_aimed_at_the_public_prefix_is_stored_privately(self):
        artist = uuid.uuid4()
        record = _scroll_doc(artist, "sub-3", f"scrolls/{artist}/sub-3")
        held = _held("sub-3", f"scrolls-public/{artist}/sub-3", [record.id], artist)
        r2 = _R2()
        await _retry(self, _Db([record], [held]), r2)
        self.assertEqual(list(r2.objects), [f"scrolls/{artist}/sub-3/{storage.SCROLL_ID_PDF_FILENAME}"])

    def test_hold_never_aims_at_the_public_prefix(self):
        session = _QueueSession()
        row = scroll_archive.hold_pdf_for_retry(
            session,
            submission_id="sub-4",
            folder_path="scrolls-public/a-1/sub-4",
            filename=storage.SCROLL_NOID_PDF_FILENAME,
            content=PDF_BYTES,
            document_ids=[],
            artist_user_id="a-1",
        )
        self.assertEqual(row.folder_path, "scrolls/a-1/sub-4")
        self.assertEqual(session.added, [row])


async def _retry_quiet(db, r2):
    with patch.object(storage, "upload_file", r2.upload):
        return await scroll_archive.retry_pending_scroll_pdfs(db)


if __name__ == "__main__":
    unittest.main()
