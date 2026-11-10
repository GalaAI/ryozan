"""Only Artist Scroll PDFs that contain the artist's government ID image are private in R2.

Generated scroll PDFs are named by content: one that embeds the ID image is
scrolls/<user>/<submission>/artist-scroll.id.pdf (private, signed links only), one without an embedded
ID is artist-scroll.noid.pdf (public). Legacy artist-scroll.pdf objects stay private by key, because the
app form requires a government ID. PDFs uploaded to scroll categories stay public, except an upload
that is itself an ID (govt-id.*). The owner email is link only: no attachment and no storage link.

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
ID_PDF_KEY = f"{SUBMISSION_PREFIX}/artist-scroll.id.pdf"
ID_PDF_URL = f"https://{ASSETS_HOST}/{ID_PDF_KEY}"
LEGACY_PDF_KEY = f"{SUBMISSION_PREFIX}/artist-scroll.pdf"
LEGACY_PDF_URL = f"https://{ASSETS_HOST}/{LEGACY_PDF_KEY}"
# New files that are safe to serve publicly live under the separate scrolls-public/ prefix.
PUBLIC_PREFIX = "scrolls-public/artist-1/20261110T120000-abcd1234"
NOID_PDF_URL = f"https://{ASSETS_HOST}/{PUBLIC_PREFIX}/artist-scroll.noid.pdf"
CATEGORY_PDF_URL = f"https://{ASSETS_HOST}/scrolls-public/shop/00000000-0000-4000-8000-000000000001/abc123.pdf"
SCROLL_ID_URL = f"https://{ASSETS_HOST}/{SCROLL_ID_KEY}"
ID_AS_PDF_KEY = f"{SUBMISSION_PREFIX}/govt-id.pdf"
BBP_CERT_URL = f"https://{ASSETS_HOST}/{PUBLIC_PREFIX}/bbp-cert.png"
ARTIST_SIG_URL = f"https://{ASSETS_HOST}/{PUBLIC_PREFIX}/artist-sig.png"
# Older files under scrolls/ were never inspected, so every one of them is private by key.
LEGACY_CATEGORY_PDF_KEY = "scrolls/shop/00000000-0000-4000-8000-000000000001/abc123.pdf"
LEGACY_CATEGORY_PDF_URL = f"https://{ASSETS_HOST}/{LEGACY_CATEGORY_PDF_KEY}"
LEGACY_BBP_CERT_KEY = f"{SUBMISSION_PREFIX}/bbp-cert.png"
LEGACY_BBP_CERT_URL = f"https://{ASSETS_HOST}/{LEGACY_BBP_CERT_KEY}"


def _png(width: int, height: int, color=(30, 30, 200)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def _settings_patch():
    return patch.object(storage, "get_settings", return_value=_settings())


def _scroll_fields(pdf_url: str = ID_PDF_URL, *, with_id: bool = True) -> dict:
    fields = {
        "legal_name": "Synthetic Artist",
        "tax_opt_in": "false",
        "bbp_cert_url": BBP_CERT_URL,
        "artist_sig_url": ARTIST_SIG_URL,
        "scroll_pdf_url": pdf_url,
        "storage_prefix": SUBMISSION_PREFIX,
        "certs_stored": {
            "govt_id": (
                {"provided": True, "stored": True, "url": SCROLL_ID_URL, "error": None}
                if with_id
                else {"provided": False, "stored": False, "url": None, "error": None}
            ),
            "bbp_cert": {"provided": True, "stored": True, "url": BBP_CERT_URL, "error": None},
        },
    }
    if with_id:
        fields["govt_id_url"] = SCROLL_ID_URL
    return fields


def _owner_archive_data(fields: dict | None = None) -> dict:
    fields = fields or _scroll_fields()
    return {
        "source": "artist_scroll_submission",
        "artist_name": "Synthetic Artist",
        "submission_id": "sub-1",
        "storage_prefix": SUBMISSION_PREFIX,
        "scroll_pdf_url": fields["scroll_pdf_url"],
        "certs_stored": fields["certs_stored"],
        "fields": fields,
    }


def _instance(*, category: str = "scroll", data: dict | None = None, file_url: str | None = ID_PDF_URL):
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

    def test_every_own_host_form_of_an_id_bearing_scroll_pdf_is_signed(self):
        for key in (ID_PDF_KEY, LEGACY_PDF_KEY):
            for reference in (
                f"https://{ASSETS_HOST}/{key}",
                f"https://pub-account-id.r2.dev/{BUCKET}/{key}",
                f"https://account-id.r2.cloudflarestorage.com/{BUCKET}/{key}",
            ):
                with self.subTest(reference=reference):
                    self.assertTrue(storage.private_id_key(reference))
                    _assert_signed(self, storage.signed_id_url(reference), key)

    def test_marker_legacy_and_id_upload_keys_are_private_whatever_the_case(self):
        for key in (
            f"{SUBMISSION_PREFIX}/ARTIST-SCROLL.ID.PDF",
            f"{SUBMISSION_PREFIX}/Artist-Scroll.pdf",
            ID_AS_PDF_KEY,
        ):
            with self.subTest(key=key):
                self.assertTrue(storage.is_private_id_key(key))
                _assert_signed(self, storage.signed_id_url(f"https://{ASSETS_HOST}/{key}"), key)

    def test_every_scrolls_key_is_private_and_scrolls_public_is_not(self):
        self.assertEqual(storage.scroll_id_filename("abc123", ".PNG"), "abc123.id.png")
        for key in (
            "scrolls/shop/u-1/abc123.id.pdf",
            "scrolls/shop/u-1/ABC123.ID.WEBP",
            "scrolls/shop/u-1/abc123.pdf",
            "scrolls/shop/u-1/abc123.jpg",
            f"{SUBMISSION_PREFIX}/artist-scroll.noid.pdf",
            LEGACY_BBP_CERT_KEY,
            f"{SUBMISSION_PREFIX}/artist-sig.png",
        ):
            with self.subTest(key=key):
                self.assertTrue(storage.is_private_id_key(key))
        for key in (
            "scrolls-public/shop/u-1/abc123.pdf",
            f"{PUBLIC_PREFIX}/artist-scroll.noid.pdf",
            f"{PUBLIC_PREFIX}/bbp-cert.png",
            "scrollsfoo/shop/abc.pdf",
            "portfolios/a/abc123.id.jpg",
        ):
            with self.subTest(key=key):
                self.assertFalse(storage.is_private_id_key(key))

    def test_public_scroll_prefix_maps_only_the_scrolls_root(self):
        self.assertEqual(storage.public_scroll_prefix(SUBMISSION_PREFIX), PUBLIC_PREFIX)
        self.assertEqual(storage.public_scroll_prefix("scrolls/shop/u-1/"), "scrolls-public/shop/u-1")
        self.assertEqual(storage.public_scroll_prefix("documents/u-1"), "documents/u-1")

    def test_legacy_scrolls_files_get_signed_links(self):
        for key in (LEGACY_CATEGORY_PDF_KEY, LEGACY_BBP_CERT_KEY):
            with self.subTest(key=key):
                _assert_signed(self, storage.signed_id_url(f"https://{ASSETS_HOST}/{key}"), key)

    def test_noid_pdf_category_pdfs_and_other_scroll_assets_stay_public(self):
        for reference in (
            NOID_PDF_URL,
            CATEGORY_PDF_URL,
            BBP_CERT_URL,
            ARTIST_SIG_URL,
            f"https://{ASSETS_HOST}/scrolls-public/shop/u-1/notes.docx",
            f"https://{ASSETS_HOST}/scrolls-public/shop/u-1/my-artist-scroll.id.pdf",
            f"https://evil.example.test/{ID_PDF_KEY}",
            f"https://pub-account-id.r2.dev/other-bucket/{ID_PDF_KEY}",
            f"https://{ASSETS_HOST}/scrolls/../waivers/x.id.pdf",
            None,
        ):
            with self.subTest(reference=reference):
                self.assertEqual(storage.signed_id_url(reference), reference)

    def test_signed_links_echoed_back_normalize_to_the_stored_reference(self):
        for reference in (ID_PDF_URL, LEGACY_PDF_URL):
            with self.subTest(reference=reference):
                signed = storage.signed_id_url(reference)
                self.assertIn("X-Amz-Signature=", signed)
                self.assertEqual(storage.canonical_id_reference(signed), reference)
        self.assertEqual(storage.canonical_id_reference(CATEGORY_PDF_URL), CATEGORY_PDF_URL)

    def test_signed_id_fields_follow_the_pdf_key(self):
        data = _owner_archive_data()
        snapshot = json.loads(json.dumps(data))
        view = storage.signed_id_fields(data, 900)
        _assert_signed(self, view["scroll_pdf_url"], ID_PDF_KEY)
        _assert_signed(self, view["certs_stored"]["govt_id"]["url"], SCROLL_ID_KEY)
        _assert_signed(self, view["fields"]["scroll_pdf_url"], ID_PDF_KEY)
        _assert_signed(self, view["fields"]["govt_id_url"], SCROLL_ID_KEY)
        self.assertEqual(view["certs_stored"]["bbp_cert"]["url"], BBP_CERT_URL)
        self.assertEqual(view["fields"]["artist_sig_url"], ARTIST_SIG_URL)
        self.assertEqual(data, snapshot, "stored data must not be mutated")

        no_id = _owner_archive_data(_scroll_fields(NOID_PDF_URL, with_id=False))
        self.assertEqual(storage.signed_id_fields(no_id, 900), no_id)

        legacy_view = storage.signed_id_fields(_owner_archive_data(_scroll_fields(LEGACY_PDF_URL)), 900)
        _assert_signed(self, legacy_view["scroll_pdf_url"], LEGACY_PDF_KEY)
        _assert_signed(self, legacy_view["fields"]["scroll_pdf_url"], LEGACY_PDF_KEY)


class ScrollReadPointTests(unittest.IsolatedAsyncioTestCase):
    def test_document_response_signs_id_bearing_pdfs(self):
        for inst, key in (
            (_instance(), ID_PDF_KEY),
            (_instance(file_url=LEGACY_PDF_URL, data=_scroll_fields(LEGACY_PDF_URL)), LEGACY_PDF_KEY),
            (
                _instance(
                    category=scroll_archive.OWNER_ARCHIVE_CATEGORY,
                    data=_owner_archive_data(_scroll_fields(LEGACY_PDF_URL)),
                    file_url=LEGACY_PDF_URL,
                ),
                LEGACY_PDF_KEY,
            ),
        ):
            with self.subTest(key=key, category=inst.category), _settings_patch():
                response = documents._instance_to_response(inst, None, "Synthetic Artist")
                query = _assert_signed(self, response["file_url"], key)
                self.assertEqual(query["X-Amz-Expires"], [str(documents.ID_VIEW_SECONDS)])
                _assert_signed(self, json.loads(response["data_json"])["scroll_pdf_url"], key)
                self.assertNotIn(SCROLL_ID_URL, response["data_json"])
                self.assertNotIn(inst.file_url, response["data_json"])

    def test_document_response_passes_pdfs_without_an_id_through(self):
        no_id = _scroll_fields(NOID_PDF_URL, with_id=False)
        for inst in (
            _instance(file_url=NOID_PDF_URL, data=no_id),
            _instance(category="scroll_shop", file_url=CATEGORY_PDF_URL, data={"note": "synthetic"}),
            _instance(file_url=BBP_CERT_URL, data={}),
        ):
            with self.subTest(file_url=inst.file_url), _settings_patch():
                response = documents._instance_to_response(inst, None, None)
                self.assertEqual(response["file_url"], inst.file_url)
                self.assertEqual(response["data_json"], inst.data_json)

    async def test_my_scroll_and_user_scroll_sign_the_pdf_in_fields(self):
        inst = _instance()
        with _settings_patch():
            mine = await documents.get_my_scroll(db=_QueueSession(inst), current_user=SimpleNamespace(id=inst.user_id))
            theirs = await documents.get_user_scroll(
                user_id=inst.user_id,
                db=_QueueSession((inst, "Synthetic Artist")),
                caller=SimpleNamespace(id=uuid.uuid4(), role="founder"),
            )
        for scroll in (mine["scroll"], theirs["scroll"]):
            _assert_signed(self, scroll["fields"]["scroll_pdf_url"], ID_PDF_KEY)
            _assert_signed(self, scroll["govt_id_url"], SCROLL_ID_KEY)
            self.assertNotIn(ID_PDF_URL, json.dumps(scroll))

    async def test_legacy_scrolls_files_are_signed_at_every_read_point(self):
        legacy_fields = {
            **_scroll_fields(LEGACY_PDF_URL),
            "bbp_cert_url": LEGACY_BBP_CERT_URL,
            "owner_sig_url": f"https://{ASSETS_HOST}/{SUBMISSION_PREFIX}/owner-sig.png",
        }
        inst = _instance(file_url=LEGACY_PDF_URL, data=legacy_fields)
        inst.artist_signature = f"https://{ASSETS_HOST}/{SUBMISSION_PREFIX}/artist-sig.png"
        category_row = _instance(category="scroll_shop", file_url=LEGACY_CATEGORY_PDF_URL, data={})
        with _settings_patch():
            mine = (
                await documents.get_my_scroll(db=_QueueSession(inst), current_user=SimpleNamespace(id=inst.user_id))
            )["scroll"]
            listing = await scrolls.list_category_docs(
                category_slug="shop",
                user_id=None,
                db=_QueueSession(
                    SimpleNamespace(slug="shop", label="Shop", icon="x", ai_digest_enabled=False), [(category_row, "A")]
                ),
                current_user=SimpleNamespace(id=uuid.uuid4(), role="founder"),
            )
            response = documents._instance_to_response(category_row, None, None)
        _assert_signed(self, mine["bbp_cert_url"], LEGACY_BBP_CERT_KEY)
        _assert_signed(self, mine["fields"]["bbp_cert_url"], LEGACY_BBP_CERT_KEY)
        _assert_signed(self, mine["owner_sig_url"], f"{SUBMISSION_PREFIX}/owner-sig.png")
        _assert_signed(self, mine["artist_signature"], f"{SUBMISSION_PREFIX}/artist-sig.png")
        _assert_signed(self, mine["fields"]["certs_stored"]["govt_id"]["url"], SCROLL_ID_KEY)
        _assert_signed(self, listing["docs"][0]["file_url"], LEGACY_CATEGORY_PDF_KEY)
        _assert_signed(self, response["file_url"], LEGACY_CATEGORY_PDF_KEY)
        self.assertNotIn(f"https://{ASSETS_HOST}/scrolls/", json.dumps(mine))

    async def test_scroll_category_list_signs_only_id_bearing_pdfs(self):
        category = SimpleNamespace(
            slug="owner-archive", label="Artist Scrolls", icon="archive", ai_digest_enabled=False
        )
        rows = [
            (_instance(category=scroll_archive.OWNER_ARCHIVE_CATEGORY, data=_owner_archive_data()), "Owner"),
            (
                _instance(
                    category=scroll_archive.OWNER_ARCHIVE_CATEGORY,
                    data=_owner_archive_data(_scroll_fields(NOID_PDF_URL, with_id=False)),
                    file_url=NOID_PDF_URL,
                ),
                "Owner",
            ),
            (_instance(file_url=CATEGORY_PDF_URL, data={}), "Owner"),
        ]
        with _settings_patch():
            listing = await scrolls.list_category_docs(
                category_slug="owner-archive",
                user_id=None,
                db=_QueueSession(category, rows),
                current_user=SimpleNamespace(id=uuid.uuid4(), role="founder"),
            )
        query = _assert_signed(self, listing["docs"][0]["file_url"], ID_PDF_KEY)
        self.assertEqual(query["X-Amz-Expires"], [str(scrolls.SCROLL_FILE_VIEW_SECONDS)])
        self.assertEqual(listing["docs"][1]["file_url"], NOID_PDF_URL)
        self.assertEqual(listing["docs"][2]["file_url"], CATEGORY_PDF_URL)

    async def _upload(self, *, is_id: bool, filename: str, content_type: str, ai_digest: bool = False):
        category = SimpleNamespace(slug="shop", ai_digest_enabled=ai_digest, ai_digest_prompt="Extract intel")
        session = _QueueSession(category)
        upload = SimpleNamespace(
            filename=filename, content_type=content_type, read=AsyncMock(return_value=b"synthetic")
        )
        digest = AsyncMock(return_value={"summary": "synthetic"})

        async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
            return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

        with (
            _settings_patch(),
            patch.object(scrolls, "upload_to_blob", _fake_upload),
            patch.object(scrolls, "_run_gala_digest", digest),
        ):
            response = await scrolls.upload_scroll_doc(
                category_slug="shop",
                file=upload,
                title="",
                notes="",
                ai_digest_enabled=ai_digest,
                is_id=is_id,
                db=session,
                current_user=SimpleNamespace(id=uuid.uuid4(), role="artist"),
            )
        return response, session.added[0].file_url, digest

    async def test_scroll_category_pdf_upload_keeps_its_public_link(self):
        response, stored, _digest = await self._upload(
            is_id=False, filename="synthetic.pdf", content_type="application/pdf"
        )
        key = urlsplit(stored).path.lstrip("/")
        self.assertTrue(key.startswith("scrolls-public/shop/") and key.endswith(".pdf"), key)
        self.assertNotIn(".id.", key)
        self.assertEqual(response["file_url"], stored)

    async def test_an_upload_marked_as_an_id_is_private_and_skips_the_ai_digest(self):
        for filename, content_type, ext in (
            ("synthetic.pdf", "application/pdf", "pdf"),
            ("synthetic.JPG", "image/jpeg", "jpg"),
        ):
            with self.subTest(filename=filename):
                response, stored, digest = await self._upload(
                    is_id=True, filename=filename, content_type=content_type, ai_digest=True
                )
                key = urlsplit(stored).path.lstrip("/")
                self.assertTrue(key.startswith("scrolls/shop/") and key.endswith(f".id.{ext}"), key)
                self.assertEqual(stored, f"https://{ASSETS_HOST}/{key}", "the stored reference stays stable")
                _assert_signed(self, response["file_url"], key)
                digest.assert_not_called()
                self.assertIsNone(response["ai_digest"])

    async def test_an_unmarked_upload_still_gets_the_ai_digest(self):
        response, _stored, digest = await self._upload(
            is_id=False, filename="synthetic.pdf", content_type="application/pdf", ai_digest=True
        )
        digest.assert_awaited_once()
        self.assertEqual(response["ai_digest"], {"summary": "synthetic"})

    async def test_archive_detail_signs_the_owner_copy_pdf_and_nested_ids(self):
        inst = _instance(category=scroll_archive.OWNER_ARCHIVE_CATEGORY, data=_owner_archive_data())
        with _settings_patch():
            detail = await archive.get_archive_detail(
                record_id=inst.id, current_user=SimpleNamespace(role="founder"), db=_QueueSession(inst)
            )
        data = detail["data"]
        _assert_signed(self, data["scroll_pdf_url"], ID_PDF_KEY)
        _assert_signed(self, data["fields"]["scroll_pdf_url"], ID_PDF_KEY)
        _assert_signed(self, data["fields"]["govt_id_url"], SCROLL_ID_KEY)
        self.assertNotIn(ID_PDF_URL, json.dumps(detail))
        self.assertNotIn(SCROLL_ID_URL, json.dumps(detail))


def _acceptances() -> dict:
    return {
        section: {"label": section, "accepted": True, "legal_name": "Synthetic Artist"}
        for section in documents.REQUIRED_SCROLL_ACCEPTANCE_SECTIONS
    }


async def _submit(
    test: unittest.TestCase, govt_id, *, fail_pdf: bool = False, folders: dict | None = None
) -> tuple[dict, dict[str, bytes], _QueueSession]:
    uploads: dict[str, bytes] = {}

    async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
        if fail_pdf and filename.endswith(".pdf"):
            return None
        uploads[filename] = file_bytes
        if folders is not None:
            folders[filename] = folder_path
        return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

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
            # Client-sent values the server alone owns must never be stored.
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
    return response, uploads, session


def _docs(session: _QueueSession) -> list:
    return [obj for obj in session.added if type(obj).__name__ == "DocumentInstance"]


class SubmitScrollTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_embedded_id_gets_the_private_marker_name_and_a_signed_link(self):
        _uncompressed_pdfs(self)
        govt_id = SimpleNamespace(
            filename="id.png", content_type="image/png", read=AsyncMock(return_value=_synthetic_id_png())
        )
        response, uploads, session = await _submit(self, govt_id)

        self.assertEqual(set(uploads) & {"artist-scroll.pdf", "artist-scroll.noid.pdf"}, set())
        pdf = uploads["artist-scroll.id.pdf"]
        # The PDF embeds the synthetic 7x5 ID image and carries no ID or PDF URL.
        self.assertRegex(pdf, rb"/Width 7\b")
        self.assertRegex(pdf, rb"/Height 5\b")
        for needle in (b"govt-id", b"artist-scroll.", b"scrolls/", ASSETS_HOST.encode(), BUCKET.encode()):
            self.assertNotIn(needle, pdf)
        self.assertNotIn(b"r2.cloudflarestorage.com", pdf)
        self.assertNotIn(b"/URI", pdf)
        self.assertNotIn("evil.example.test", extract_pdf_text(pdf))

        key = urlsplit(response["scroll_pdf_url"]).path.removeprefix(f"/{BUCKET}/")
        self.assertTrue(key.endswith("/artist-scroll.id.pdf"))
        _assert_signed(self, response["scroll_pdf_url"], key)
        stored = f"https://{ASSETS_HOST}/{key}"
        docs = _docs(session)
        self.assertEqual({d.category for d in docs}, {"scroll", scroll_archive.OWNER_ARCHIVE_CATEGORY})
        for doc in docs:
            data = json.loads(doc.data_json)
            self.assertEqual(doc.file_url, stored)
            self.assertEqual(data["scroll_pdf_url"], stored)
            self.assertNotIn("X-Amz-Signature", doc.data_json)
            self.assertNotIn("evil.example.test", doc.data_json)

    async def test_without_an_embedded_id_the_pdf_is_the_public_noid_name(self):
        id_as_pdf = SimpleNamespace(
            filename="id.pdf", content_type="application/pdf", read=AsyncMock(return_value=b"%PDF-1.4 synthetic")
        )
        for label, govt_id in (("id uploaded as a pdf", id_as_pdf), ("no id", None)):
            with self.subTest(case=label):
                response, uploads, session = await _submit(self, govt_id)
                self.assertEqual(set(uploads) & {"artist-scroll.pdf", "artist-scroll.id.pdf"}, set())
                pdf = uploads["artist-scroll.noid.pdf"]
                self.assertNotIn(b"/Width 7", pdf)
                self.assertNotIn(b"govt-id", pdf)
                public = response["scroll_pdf_url"]
                self.assertNotIn("X-Amz-Signature", public)
                self.assertEqual(urlsplit(public).hostname, ASSETS_HOST)
                for doc in _docs(session):
                    data = json.loads(doc.data_json)
                    self.assertEqual(doc.file_url, public)
                    self.assertEqual(data["scroll_pdf_url"], public)
                    with _settings_patch():
                        self.assertEqual(documents._instance_to_response(doc, None, None)["file_url"], public)
                if govt_id is not None:
                    # The ID upload itself (govt-id.pdf) stays private.
                    stored_id = json.loads(_docs(session)[0].data_json)["govt_id_url"]
                    self.assertTrue(stored_id.endswith("/govt-id.pdf"))
                    with _settings_patch():
                        self.assertIn("X-Amz-Signature=", storage.signed_id_url(stored_id))

    async def test_signatures_and_ids_stay_private_and_certificates_go_public(self):
        govt_id = SimpleNamespace(
            filename="id.png", content_type="image/png", read=AsyncMock(return_value=_synthetic_id_png())
        )
        bbp = SimpleNamespace(filename="bbp.png", content_type="image/png", read=AsyncMock(return_value=_png(3, 3)))
        folders: dict[str, str] = {}
        uploads: dict[str, bytes] = {}

        async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
            uploads[filename] = file_bytes
            folders[filename] = folder_path
            return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

        with (
            _settings_patch(),
            patch.object(documents, "upload_to_blob", _fake_upload),
            patch.object(documents, "resolve_founders", AsyncMock(return_value=[])),
        ):
            await documents.submit_scroll(
                request=SimpleNamespace(headers={"user-agent": "unit-test"}),
                artist_signature=base64.b64encode(_png(11, 3)).decode(),
                fields_json=json.dumps(
                    {
                        "terms_version": documents.ARTIST_SCROLL_TERMS_VERSION,
                        "section_acceptances": _acceptances(),
                    }
                ),
                bbp_cert=bbp,
                first_aid_cert=None,
                cpr_cert=None,
                govt_id=govt_id,
                db=_QueueSession([], None),
                current_user=SimpleNamespace(
                    id=uuid.uuid4(),
                    name="Synthetic Artist",
                    email="a@example.test",
                    role="artist",
                    bttcoin_balance=0,
                    quest_points=0,
                ),
            )
        self.assertTrue(folders["artist-sig.png"].startswith("scrolls/"))
        self.assertTrue(folders["govt-id.png"].startswith("scrolls/"))
        self.assertTrue(folders["artist-scroll.id.pdf"].startswith("scrolls/"))
        self.assertTrue(folders["bbp-cert.png"].startswith("scrolls-public/"))

    async def test_a_pdf_that_fails_to_store_keeps_its_bytes_for_retry_never_attached(self):
        govt_id = SimpleNamespace(
            filename="id.png", content_type="image/png", read=AsyncMock(return_value=_synthetic_id_png())
        )
        response, uploads, session = await _submit(self, govt_id, fail_pdf=True)
        self.assertIsNone(response["scroll_pdf_url"])
        self.assertNotIn("artist-scroll.id.pdf", uploads)
        outbox = [obj for obj in session.added if type(obj).__name__ == "ScrollOwnerEmailOutbox"]
        self.assertEqual(len(outbox), 1)
        # The bytes are kept with the private target name, only so the store can be retried.
        self.assertTrue(outbox[0].attachment_content.startswith(b"%PDF"))
        self.assertEqual(outbox[0].attachment_filename, "artist-scroll.id.pdf")
        self.assertIn("the archive has no PDF", outbox[0].html_body)
        for doc in _docs(session):
            self.assertIsNone(doc.file_url)

    async def test_owner_email_is_link_only(self):
        govt_id = SimpleNamespace(
            filename="id.png", content_type="image/png", read=AsyncMock(return_value=_synthetic_id_png())
        )
        _response, _uploads, session = await _submit(self, govt_id)
        outbox = [obj for obj in session.added if type(obj).__name__ == "ScrollOwnerEmailOutbox"]
        self.assertEqual(len(outbox), 1)
        self.assertIsNone(outbox[0].attachment_content)
        self.assertIsNone(outbox[0].attachment_filename)
        html = outbox[0].html_body
        for needle in (ASSETS_HOST, BUCKET, "r2.cloudflarestorage.com", "r2.dev", "scrolls/", ".pdf", "X-Amz"):
            self.assertNotIn(needle, html)
        links = [link for link in re.findall(r'href="([^"]+)"', html) if "fonts.googleapis.com" not in link]
        self.assertEqual(len(links), 1)
        self.assertIn("/dashboard?portal=hub", links[0])
        self.assertIn(f"category={scroll_archive.OWNER_ARCHIVE_SLUG}", links[0])
        self.assertIn("not attached to this email", html)
        for notice in (obj for obj in session.added if type(obj).__name__ == "Notification"):
            self.assertNotIn(ASSETS_HOST, notice.body)
            self.assertTrue(notice.action_url.startswith("/dashboard"))

    def test_signed_links_expire_within_the_sigv4_cap(self):
        with _settings_patch():
            for seconds in (documents.ID_VIEW_SECONDS, scrolls.SCROLL_FILE_VIEW_SECONDS, 10**9):
                query = parse_qs(urlsplit(storage.signed_id_url(ID_PDF_URL, seconds)).query)
                self.assertLessEqual(int(query["X-Amz-Expires"][0]), 7 * 24 * 3600)


if __name__ == "__main__":
    unittest.main()
