"""
Generated scroll PDFs stored under scrolls-public/ before every scroll PDF went private are moved to scrolls/.

Every generated scroll PDF prints the artist signature, so none may stay public. Synthetic fixtures
only: no real artist, ID or signature data; hosts are *.example.test.
"""

import json
import logging
import os
import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import documents  # noqa: E402
from app.models.document import DocumentInstance  # noqa: E402
from app.models.scroll_owner_email_outbox import ScrollOwnerEmailOutbox  # noqa: E402
from app.services import scroll_archive, storage  # noqa: E402
from app.services.scroll_archive import OWNER_ARCHIVE_CATEGORY  # noqa: E402

HOST = "assets.example.test"
BUCKET = "fake-shop-bucket"
SID = "synthetic-submission-1"
ARTIST = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
PRIVATE = f"scrolls/{ARTIST}/20261110T120000-abcd1234"
PUBLIC = f"scrolls-public/{ARTIST}/20261110T120000-abcd1234"
OLD_KEY = f"{PUBLIC}/{storage.SCROLL_NOID_PDF_FILENAME}"
NEW_KEY = f"{PRIVATE}/{storage.SCROLL_NOID_PDF_FILENAME}"
OLD_URL = f"https://{HOST}/{OLD_KEY}"
NEW_URL = f"https://{HOST}/{NEW_KEY}"
CERT_URL = f"https://{HOST}/{PUBLIC}/bbp-cert.png"
PDF_BYTES = b"%PDF-1.4 synthetic scroll with a synthetic signature"
FROZEN = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)


def _settings():
    return SimpleNamespace(
        R2_ACCOUNT_ID="account-id",
        R2_ACCESS_KEY_ID="access-key",
        R2_SECRET_ACCESS_KEY="secret-key",
        R2_BUCKET_NAME=BUCKET,
        R2_PUBLIC_DOMAIN=HOST,
    )


class _Result:
    def __init__(self, rows=(), count=None):
        self._rows, self._count = list(rows), count

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._count


def _like_needle(stmt) -> str:
    """The text a LIKE-based count looks for, with SQLAlchemy's autoescape undone."""
    value = next(v for v in stmt.compile().params.values() if isinstance(v, str))
    return value.replace("/%", "%").replace("/_", "_").replace("//", "/")


class _Db:
    """Session stand-in: scroll records, outbox rows, and reference counts computed from them."""

    def __init__(self, docs, *, outbox=(), memory_refs=0):
        self.docs, self.outbox, self.memory_refs = list(docs), list(outbox), memory_refs
        self.added: list = []
        self.commits = 0
        self.committed_at_delete: list[int] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, stmt):
        sql = str(stmt)
        if "count(" in sql:
            if "ryozan_ai_memories" in sql:
                return _Result(count=self.memory_refs)
            needle = _like_needle(stmt)
            hits = sum(
                any(needle in (v or "") for v in (d.file_url, d.data_json, d.artist_signature)) for d in self.docs
            )
            return _Result(count=hits)
        if "ryozan_scroll_owner_email_outbox" in sql.split("WHERE")[0]:
            return _Result([r for r in self.outbox if r.attachment_content is not None])
        return _Result(self.docs)

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


class _R2:
    """Synthetic bucket: read, write and delete by key, with optional failure modes."""

    def __init__(self, objects=None, *, fail_upload=False, corrupt_upload=False, fail_delete=False):
        self.objects: dict[str, bytes] = dict(objects or {})
        self.fail_upload, self.corrupt_upload, self.fail_delete = fail_upload, corrupt_upload, fail_delete
        self.uploads = 0
        self.db: _Db | None = None

    async def fetch(self, object_key):
        return self.objects.get(object_key)

    async def upload(self, file_bytes, filename, mime_type, folder_path):
        self.uploads += 1
        if self.fail_upload:
            return None
        key = f"{folder_path}/{filename}"
        self.objects[key] = file_bytes[:-1] if self.corrupt_upload else file_bytes
        return f"https://{HOST}/{key}"

    async def delete(self, url, *, allowed_prefix=None):
        if self.db is not None:
            self.db.committed_at_delete.append(self.db.commits)
        key = urlsplit(url).path.lstrip("/")
        assert allowed_prefix and key.startswith(allowed_prefix)
        if self.fail_delete:
            return False
        return self.objects.pop(key, None) is not None


def _doc(category, url=OLD_URL, *, owner=None):
    data = {
        "submission_id": SID,
        "storage_prefix": PRIVATE,
        "scroll_pdf_url": url,
        "bbp_cert_url": CERT_URL,
        "certs_stored": {"bbp_cert": {"provided": True, "stored": True, "url": CERT_URL}},
        "fields": {"scroll_pdf_url": url, "bbp_cert_url": CERT_URL},
    }
    if category == OWNER_ARCHIVE_CATEGORY:
        data["artist_user_id"] = str(ARTIST)
    return DocumentInstance(
        id=uuid.uuid4(),
        user_id=owner or (ARTIST if category == "scroll" else uuid.uuid4()),
        category=category,
        title="Artist Scroll: Synthetic Artist",
        status="signed",
        data_json=json.dumps(data),
        file_url=url,
        signed_at=FROZEN,
        created_at=FROZEN,
        updated_at=FROZEN,
    )


def _r2_patches(r2):
    return (
        patch.object(storage, "get_settings", return_value=_settings()),
        patch.object(storage, "fetch_object", r2.fetch),
        patch.object(storage, "upload_file", r2.upload),
        patch.object(storage, "delete_file", r2.delete),
    )


async def _run(test, db, r2):
    r2.db = db
    p1, p2, p3, p4 = _r2_patches(r2)
    with p1, p2, p3, p4, test.assertLogs("ryozan.scroll_archive", level=logging.INFO) as captured:
        counts = await scroll_archive.move_public_scroll_pdfs(db)
    return counts, [r.getMessage() for r in captured.records]


class MovePublicScrollPdfTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_public_pdf_is_copied_verified_repointed_then_deleted(self):
        docs = [_doc("scroll"), _doc(OWNER_ARCHIVE_CATEGORY), _doc(OWNER_ARCHIVE_CATEGORY)]
        db, r2 = _Db(docs), _R2({OLD_KEY: PDF_BYTES, f"{PUBLIC}/bbp-cert.png": b"cert"})
        counts, logs = await _run(self, db, r2)

        self.assertEqual((counts["pdfs"], counts["moved"], counts["failed"]), (1, 1, 0))
        self.assertEqual((counts["public deleted"], counts["public kept"]), (1, 0))
        self.assertEqual(r2.objects[NEW_KEY], PDF_BYTES, "same bytes, same .noid name, private prefix")
        self.assertNotIn(OLD_KEY, r2.objects, "the public copy is gone")
        self.assertIn(f"{PUBLIC}/bbp-cert.png", r2.objects, "certificates stay public")
        self.assertEqual(db.committed_at_delete, [1], "deleted only after the new references were committed")
        for doc in docs:
            data = json.loads(doc.data_json)
            self.assertEqual(doc.file_url, NEW_URL)
            self.assertEqual((data["scroll_pdf_url"], data["fields"]["scroll_pdf_url"]), (NEW_URL, NEW_URL))
            self.assertEqual(data["bbp_cert_url"], CERT_URL)
            self.assertNotIn(OLD_KEY, doc.data_json)
            # Staff and founders still open it from the app, now through a short-lived signed link.
            with patch.object(storage, "get_settings", return_value=_settings()):
                view = documents._instance_to_response(doc, None, None)
            parts = urlsplit(view["file_url"])
            self.assertEqual(
                (parts.hostname, parts.path), ("account-id.r2.cloudflarestorage.com", f"/{BUCKET}/{NEW_KEY}")
            )
            self.assertIn("X-Amz-Signature", parse_qs(parts.query))
        joined = "\n".join(logs)
        self.assertIn("1 pdfs, 1 moved, 0 failed, 1 public deleted, 0 public kept", joined)
        for secret in (SID, str(ARTIST), HOST, "scrolls"):
            self.assertNotIn(secret, joined)

    async def test_second_pass_changes_nothing(self):
        docs = [_doc("scroll"), _doc(OWNER_ARCHIVE_CATEGORY)]
        db, r2 = _Db(docs), _R2({OLD_KEY: PDF_BYTES})
        await _run(self, db, r2)
        snapshot = ([d.file_url for d in docs], [d.data_json for d in docs], dict(r2.objects), r2.uploads)

        counts, _logs = await _run(self, db, r2)
        self.assertEqual((counts["pdfs"], counts["moved"], counts["failed"]), (0, 0, 0))
        self.assertEqual(
            ([d.file_url for d in docs], [d.data_json for d in docs], dict(r2.objects), r2.uploads), snapshot
        )

    async def test_private_and_id_pdfs_are_left_alone(self):
        docs = [_doc("scroll", NEW_URL), _doc(OWNER_ARCHIVE_CATEGORY, f"https://{HOST}/{PRIVATE}/artist-scroll.id.pdf")]
        before = [d.data_json for d in docs]
        db, r2 = _Db(docs), _R2()
        counts, _logs = await _run(self, db, r2)
        self.assertEqual((counts["pdfs"], r2.uploads), (0, 0))
        self.assertEqual([d.data_json for d in docs], before)

    async def test_storage_failure_keeps_the_original_reference_and_the_public_object(self):
        for label, r2 in (
            ("upload fails", _R2({OLD_KEY: PDF_BYTES}, fail_upload=True)),
            ("read-back differs", _R2({OLD_KEY: PDF_BYTES}, corrupt_upload=True)),
            ("source unreadable", _R2()),
        ):
            with self.subTest(label=label):
                docs = [_doc("scroll"), _doc(OWNER_ARCHIVE_CATEGORY)]
                before = [(d.file_url, d.data_json) for d in docs]
                counts, logs = await _run(self, _Db(docs), r2)
                self.assertEqual((counts["moved"], counts["failed"], counts["public deleted"]), (0, 1, 0))
                self.assertEqual([(d.file_url, d.data_json) for d in docs], before)
                if label != "source unreadable":
                    self.assertEqual(r2.objects[OLD_KEY], PDF_BYTES, "the public original is kept")
                self.assertIn("0 moved, 1 failed", "\n".join(logs))

    async def test_public_object_is_kept_while_another_record_still_mentions_it(self):
        docs = [_doc("scroll"), _doc(OWNER_ARCHIVE_CATEGORY)]
        db, r2 = _Db(docs, memory_refs=1), _R2({OLD_KEY: PDF_BYTES})
        counts, _logs = await _run(self, db, r2)
        self.assertEqual((counts["moved"], counts["public deleted"], counts["public kept"]), (1, 0, 1))
        self.assertEqual(r2.objects[OLD_KEY], PDF_BYTES)
        self.assertTrue(all(d.file_url == NEW_URL for d in docs))


def _outbox_row(filename=storage.SCROLL_NOID_PDF_FILENAME, **extra):
    return ScrollOwnerEmailOutbox(
        id=uuid.uuid4(),
        founder_id=uuid.uuid4(),
        submission_id=SID,
        to_email="owner@example.test",
        subject="Artist Scroll Submitted: Synthetic Artist",
        html_body="<p>synthetic</p>",
        attachment_filename=filename,
        attachment_content=PDF_BYTES,
        status="sent",
        attempts=1,
        next_attempt_at=FROZEN,
        created_at=FROZEN,
        updated_at=FROZEN,
        **extra,
    )


class RetryPrivateFolderTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_of_a_noid_pdf_stores_it_privately_under_the_submission_folder(self):
        archive, artist = _doc(OWNER_ARCHIVE_CATEGORY, None), _doc("scroll", None)
        row = _outbox_row()
        r2 = _R2()
        with patch.object(storage, "upload_file", r2.upload), self.assertLogs("ryozan.scroll_archive", level="INFO"):
            pending, stored, failed = await scroll_archive.retry_unstored_scroll_pdfs(
                _Db([archive, artist], outbox=[row])
            )
        self.assertEqual((pending, stored, failed), (1, 1, 0))
        self.assertEqual(r2.objects, {NEW_KEY: PDF_BYTES})
        self.assertEqual((archive.file_url, artist.file_url), (NEW_URL, NEW_URL))
        self.assertIsNone(row.attachment_content)


if __name__ == "__main__":
    unittest.main()
