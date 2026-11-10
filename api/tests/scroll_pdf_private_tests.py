"""Artist Scroll PDFs are private in R2 and reach clients only as short-lived signed links.

The scroll PDF (scrolls/<user>/<submission>/artist-scroll.pdf) embeds the artist's government ID
image, so it is as sensitive as the ID itself. It is stored as scroll_pdf_url on the artist's record,
as the file_url of that record and of every owner archive copy, and inside the archive copy's data.
Every read point now signs a fresh GET link; the owner email attaches the PDF bytes and links only to
the authenticated app page.

Every install names its own bucket and assets domain in settings, so these tests use a fake bucket and
neutral example.test hosts. All image data is generated; no real client or artist data is used.
"""

import base64
import io
import json
import os
import re
import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from waiver_id_private_tests import (  # noqa: E402
    ASSETS_HOST,
    BUCKET,
    SCROLL_ID_KEY,
    _assert_signed,
    _settings,
    _synthetic_id_png,
    _uncompressed_pdfs,
    extract_pdf_text,
)

from app.api.v1.endpoints import archive, documents, scrolls  # noqa: E402
from app.services import scroll_archive, storage  # noqa: E402

FROZEN = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
SUBMISSION_PREFIX = "scrolls/artist-1/20261110T120000-abcd1234"
SCROLL_PDF_KEY = f"{SUBMISSION_PREFIX}/artist-scroll.pdf"
SCROLL_PDF_URL = f"https://{ASSETS_HOST}/{SCROLL_PDF_KEY}"
SCROLL_ID_URL = f"https://{ASSETS_HOST}/{SCROLL_ID_KEY}"
BBP_CERT_URL = f"https://{ASSETS_HOST}/{SUBMISSION_PREFIX}/bbp-cert.png"
ARTIST_SIG_URL = f"https://{ASSETS_HOST}/{SUBMISSION_PREFIX}/artist-sig.png"


def _png(width: int, height: int, color=(30, 30, 200)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def _settings_patch():
    return patch.object(storage, "get_settings", return_value=_settings())


def _scroll_fields() -> dict:
    return {
        "legal_name": "Synthetic Artist",
        "tax_opt_in": "false",
        "govt_id_url": SCROLL_ID_URL,
        "bbp_cert_url": BBP_CERT_URL,
        "artist_sig_url": ARTIST_SIG_URL,
        "scroll_pdf_url": SCROLL_PDF_URL,
        "storage_prefix": SUBMISSION_PREFIX,
        "certs_stored": {
            "govt_id": {"provided": True, "stored": True, "url": SCROLL_ID_URL, "error": None},
            "bbp_cert": {"provided": True, "stored": True, "url": BBP_CERT_URL, "error": None},
        },
    }


def _scroll_instance(*, category: str = "scroll", data: dict | None = None, file_url: str | None = SCROLL_PDF_URL):
    return SimpleNamespace(
        id=uuid.uuid4(),
        template_id=None,
        user_id=uuid.uuid4(),
        recipient_name=None,
        recipient_email=None,
        guest_name=None,
        guest_email=None,
        category=category,
        title="Artist Scroll",
        status="signed",
        data_json=json.dumps(data if data is not None else _scroll_fields()),
        signed_at=FROZEN,
        file_url=file_url,
        notes=None,
        artist_signature=None,
        artist_signed_at=FROZEN,
        artist_id_reviewed=None,
        send_token=None,
        sent_at=None,
        sent_by=None,
        viewed_at=None,
        reminder_at=None,
        reminder_sent=False,
        expires_at=None,
        created_at=FROZEN,
        updated_at=FROZEN,
    )


def _owner_archive_data() -> dict:
    fields = _scroll_fields()
    return {
        "source": "artist_scroll_submission",
        "artist_name": "Synthetic Artist",
        "submission_id": "sub-1",
        "storage_prefix": SUBMISSION_PREFIX,
        "scroll_pdf_url": SCROLL_PDF_URL,
        "certs_stored": fields["certs_stored"],
        "fields": fields,
    }


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value

    def all(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: self._value)


class _QueueSession:
    """Returns the queued results in order, one per execute()."""

    def __init__(self, *results):
        self._results = list(results)
        self.added: list = []

    async def execute(self, _statement):
        return _Result(self._results.pop(0) if self._results else [])

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, _obj):
        return None

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = FROZEN
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class StorageRuleTests(unittest.TestCase):
    def setUp(self):
        patcher = _settings_patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_own_host_form_of_a_scroll_pdf_is_signed(self):
        for reference in (
            SCROLL_PDF_URL,
            f"https://pub-account-id.r2.dev/{BUCKET}/{SCROLL_PDF_KEY}",
            f"https://account-id.r2.cloudflarestorage.com/{BUCKET}/{SCROLL_PDF_KEY}",
        ):
            with self.subTest(reference=reference):
                self.assertTrue(storage.private_id_key(reference))
                _assert_signed(self, storage.signed_id_url(reference), SCROLL_PDF_KEY)

    def test_any_pdf_under_scrolls_is_private_whatever_its_case(self):
        for key in (
            "scrolls/artist-1/20261110T120000-abcd1234/ARTIST-SCROLL.PDF",
            "scrolls/shop/00000000-0000-4000-8000-000000000001/abc123.pdf",
        ):
            with self.subTest(key=key):
                self.assertTrue(storage.is_private_id_key(key))
                _assert_signed(self, storage.signed_id_url(f"https://{ASSETS_HOST}/{key}"), key)

    def test_non_id_scroll_assets_and_foreign_scroll_pdfs_pass_through(self):
        for reference in (
            BBP_CERT_URL,
            ARTIST_SIG_URL,
            f"https://{ASSETS_HOST}/{SUBMISSION_PREFIX}/cpr-cert.jpg",
            f"https://{ASSETS_HOST}/scrolls/shop/u-1/notes.docx",
            f"https://{ASSETS_HOST}/scrolls/shop/u-1/pdf-guide.txt",
            f"https://evil.example.test/{SCROLL_PDF_KEY}",
            f"https://pub-account-id.r2.dev/other-bucket/{SCROLL_PDF_KEY}",
            f"https://{ASSETS_HOST}/scrolls/../waivers/x.png",
            None,
        ):
            with self.subTest(reference=reference):
                self.assertEqual(storage.signed_id_url(reference), reference)

    def test_a_signed_scroll_pdf_link_normalizes_to_the_stored_reference(self):
        signed = storage.signed_id_url(SCROLL_PDF_URL)
        self.assertEqual(storage.canonical_id_reference(signed), SCROLL_PDF_URL)

    def test_signed_id_fields_signs_scroll_pdf_and_nested_archive_fields_without_mutating(self):
        data = _owner_archive_data()
        snapshot = json.loads(json.dumps(data))
        view = storage.signed_id_fields(data, 900)
        _assert_signed(self, view["scroll_pdf_url"], SCROLL_PDF_KEY)
        _assert_signed(self, view["certs_stored"]["govt_id"]["url"], SCROLL_ID_KEY)
        _assert_signed(self, view["fields"]["scroll_pdf_url"], SCROLL_PDF_KEY)
        _assert_signed(self, view["fields"]["govt_id_url"], SCROLL_ID_KEY)
        _assert_signed(self, view["fields"]["certs_stored"]["govt_id"]["url"], SCROLL_ID_KEY)
        self.assertEqual(view["certs_stored"]["bbp_cert"]["url"], BBP_CERT_URL)
        self.assertEqual(view["fields"]["bbp_cert_url"], BBP_CERT_URL)
        self.assertEqual(view["fields"]["artist_sig_url"], ARTIST_SIG_URL)
        self.assertEqual(data, snapshot, "stored data must not be mutated")


class ScrollReadPointTests(unittest.IsolatedAsyncioTestCase):
    def test_document_response_signs_file_url_and_data_json_scroll_pdf(self):
        for inst in (
            _scroll_instance(),
            _scroll_instance(category=scroll_archive.OWNER_ARCHIVE_CATEGORY, data=_owner_archive_data()),
        ):
            with self.subTest(category=inst.category), _settings_patch():
                response = documents._instance_to_response(inst, None, "Synthetic Artist")
            query = _assert_signed(self, response["file_url"], SCROLL_PDF_KEY)
            self.assertEqual(query["X-Amz-Expires"], [str(documents.ID_VIEW_SECONDS)])
            view = json.loads(response["data_json"])
            _assert_signed(self, view["scroll_pdf_url"], SCROLL_PDF_KEY)
            self.assertNotIn(SCROLL_PDF_URL, response["data_json"])
            self.assertNotIn(SCROLL_ID_URL, response["data_json"])
            self.assertEqual(inst.file_url, SCROLL_PDF_URL, "stored reference must not change")

    def test_document_response_passes_a_non_id_file_through(self):
        cert = _scroll_instance(file_url=BBP_CERT_URL, data={"note": "synthetic"})
        with _settings_patch():
            response = documents._instance_to_response(cert, None, None)
        self.assertEqual(response["file_url"], BBP_CERT_URL)

    async def test_my_scroll_and_user_scroll_sign_the_pdf_in_fields(self):
        inst = _scroll_instance()
        user_id = inst.user_id
        with _settings_patch():
            mine = await documents.get_my_scroll(db=_QueueSession(inst), current_user=SimpleNamespace(id=user_id))
            theirs = await documents.get_user_scroll(
                user_id=user_id,
                db=_QueueSession((inst, "Synthetic Artist")),
                caller=SimpleNamespace(id=uuid.uuid4(), role="founder"),
            )
        for scroll in (mine["scroll"], theirs["scroll"]):
            _assert_signed(self, scroll["fields"]["scroll_pdf_url"], SCROLL_PDF_KEY)
            _assert_signed(self, scroll["govt_id_url"], SCROLL_ID_KEY)
            self.assertNotIn(SCROLL_PDF_URL, json.dumps(scroll))

    async def test_scroll_category_list_signs_the_pdf_file_url(self):
        category = SimpleNamespace(
            slug="owner-archive", label="Artist Scrolls", icon="archive", ai_digest_enabled=False
        )
        archive_row = _scroll_instance(category=scroll_archive.OWNER_ARCHIVE_CATEGORY, data=_owner_archive_data())
        cert_row = _scroll_instance(file_url=BBP_CERT_URL, data={})
        session = _QueueSession(category, [(archive_row, "Owner"), (cert_row, "Owner")])
        with _settings_patch():
            listing = await scrolls.list_category_docs(
                category_slug="owner-archive",
                user_id=None,
                db=session,
                current_user=SimpleNamespace(id=uuid.uuid4(), role="founder"),
            )
        query = _assert_signed(self, listing["docs"][0]["file_url"], SCROLL_PDF_KEY)
        self.assertEqual(query["X-Amz-Expires"], [str(scrolls.SCROLL_FILE_VIEW_SECONDS)])
        self.assertEqual(listing["docs"][1]["file_url"], BBP_CERT_URL)

    async def test_scroll_category_upload_returns_a_signed_link_and_stores_the_reference(self):
        category = SimpleNamespace(slug="shop", ai_digest_enabled=False, ai_digest_prompt=None)
        session = _QueueSession(category)
        upload = SimpleNamespace(
            filename="synthetic.pdf", content_type="application/pdf", read=AsyncMock(return_value=b"%PDF-1.4 test")
        )

        async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
            return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

        with _settings_patch(), patch.object(scrolls, "upload_to_blob", _fake_upload):
            response = await scrolls.upload_scroll_doc(
                category_slug="shop",
                file=upload,
                title="",
                notes="",
                ai_digest_enabled=False,
                db=session,
                current_user=SimpleNamespace(id=uuid.uuid4(), role="artist"),
            )
        stored = session.added[0].file_url
        key = urlsplit(stored).path.lstrip("/")
        self.assertTrue(key.startswith("scrolls/shop/") and key.endswith(".pdf"))
        _assert_signed(self, response["file_url"], key)
        self.assertEqual(stored, f"https://{ASSETS_HOST}/{key}")

    async def test_archive_detail_signs_the_owner_copy_pdf_and_nested_ids(self):
        inst = _scroll_instance(category=scroll_archive.OWNER_ARCHIVE_CATEGORY, data=_owner_archive_data())
        with _settings_patch():
            detail = await archive.get_archive_detail(
                record_id=inst.id, current_user=SimpleNamespace(role="founder"), db=_QueueSession(inst)
            )
        data = detail["data"]
        _assert_signed(self, data["scroll_pdf_url"], SCROLL_PDF_KEY)
        _assert_signed(self, data["fields"]["scroll_pdf_url"], SCROLL_PDF_KEY)
        _assert_signed(self, data["fields"]["govt_id_url"], SCROLL_ID_KEY)
        self.assertNotIn(SCROLL_PDF_URL, json.dumps(detail))
        self.assertNotIn(SCROLL_ID_URL, json.dumps(detail))


def _acceptances() -> dict:
    return {
        section: {"label": section, "accepted": True, "legal_name": "Synthetic Artist"}
        for section in documents.REQUIRED_SCROLL_ACCEPTANCE_SECTIONS
    }


class SubmitScrollTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_embeds_the_id_returns_signed_links_and_emails_no_url(self):
        _uncompressed_pdfs(self)
        uploads: dict[str, bytes] = {}

        async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
            uploads[filename] = file_bytes
            return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

        govt_id = SimpleNamespace(
            filename="id.png", content_type="image/png", read=AsyncMock(return_value=_synthetic_id_png())
        )
        artist = SimpleNamespace(
            id=uuid.uuid4(),
            name="Synthetic Artist",
            email="artist@example.test",
            role="artist",
            bttcoin_balance=0,
            quest_points=0,
        )
        founder = SimpleNamespace(id=uuid.uuid4(), name="Synthetic Owner", email="owner@example.test")
        fields_json = json.dumps(
            {
                "legal_name": "Synthetic Artist",
                "terms_version": documents.ARTIST_SCROLL_TERMS_VERSION,
                "section_acceptances": _acceptances(),
                # A client echoing an older value must never be stored.
                "scroll_pdf_url": "https://evil.example.test/old.pdf",
            }
        )
        session = _QueueSession([], None)
        with (
            _settings_patch(),
            patch.object(documents, "upload_to_blob", _fake_upload),
            patch.object(documents, "resolve_founders", AsyncMock(return_value=[founder])),
        ):
            response = await documents.submit_scroll(
                request=SimpleNamespace(headers={"user-agent": "unit-test"}),
                artist_signature=base64.b64encode(_png(11, 3)).decode(),
                fields_json=fields_json,
                bbp_cert=None,
                first_aid_cert=None,
                cpr_cert=None,
                govt_id=govt_id,
                db=session,
                current_user=artist,
            )

        # The generated PDF embeds the synthetic 7x5 ID image and carries no ID or PDF URL.
        pdf = uploads["artist-scroll.pdf"]
        self.assertRegex(pdf, rb"/Width 7\b")
        self.assertRegex(pdf, rb"/Height 5\b")
        for needle in (b"govt-id", b"artist-scroll.pdf", b"scrolls/", ASSETS_HOST.encode(), BUCKET.encode()):
            self.assertNotIn(needle, pdf)
        self.assertNotIn(b"r2.cloudflarestorage.com", pdf)
        self.assertNotIn(b"/URI", pdf)
        self.assertNotIn("evil.example.test", extract_pdf_text(pdf))

        # The response hands back a signed link; the records keep the stable reference.
        signed_key = urlsplit(response["scroll_pdf_url"]).path.removeprefix(f"/{BUCKET}/")
        self.assertTrue(signed_key.startswith(f"scrolls/{artist.id}/") and signed_key.endswith("/artist-scroll.pdf"))
        _assert_signed(self, response["scroll_pdf_url"], signed_key)
        stored_reference = f"https://{ASSETS_HOST}/{signed_key}"
        docs = [obj for obj in session.added if type(obj).__name__ == "DocumentInstance"]
        self.assertEqual({d.category for d in docs}, {"scroll", scroll_archive.OWNER_ARCHIVE_CATEGORY})
        for doc in docs:
            self.assertEqual(doc.file_url, stored_reference)
            self.assertEqual(json.loads(doc.data_json)["scroll_pdf_url"], stored_reference)
            self.assertNotIn("X-Amz-Signature", doc.data_json)

        # The owner email attaches the PDF bytes and links only to the authenticated app page.
        outbox = [obj for obj in session.added if type(obj).__name__ == "ScrollOwnerEmailOutbox"]
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].attachment_content, pdf)
        html = outbox[0].html_body
        for needle in (ASSETS_HOST, BUCKET, "r2.cloudflarestorage.com", "r2.dev", "scrolls/", '.pdf"'):
            self.assertNotIn(needle, html)
        links = [link for link in re.findall(r'href="([^"]+)"', html) if "fonts.googleapis.com" not in link]
        self.assertEqual(len(links), 1)
        self.assertIn("/dashboard?portal=hub", links[0])
        self.assertIn(f"category={scroll_archive.OWNER_ARCHIVE_SLUG}", links[0])
        notices = [obj for obj in session.added if type(obj).__name__ == "Notification"]
        self.assertTrue(notices)
        for notice in notices:
            self.assertNotIn(ASSETS_HOST, notice.body)
            self.assertTrue(notice.action_url.startswith("/dashboard"))

    def test_signed_links_expire_within_the_sigv4_cap(self):
        with _settings_patch():
            for seconds in (documents.ID_VIEW_SECONDS, scrolls.SCROLL_FILE_VIEW_SECONDS, 10**9):
                query = parse_qs(urlsplit(storage.signed_id_url(SCROLL_PDF_URL, seconds)).query)
                self.assertLessEqual(int(query["X-Amz-Expires"][0]), 7 * 24 * 3600)


if __name__ == "__main__":
    unittest.main()
