"""Government ID photos are private in R2 and reach clients only as short-lived signed links.

Walk-in waiver ID photos (waivers/pending-ids/), exported waiver packets (waivers/**/*.pdf)
and Artist Scroll government IDs (scrolls/.../govt-id.*) used to be handed out as permanent
public URLs on the shop's assets domain. The stored reference is unchanged; every read point
now signs a fresh GET link against the R2 S3 endpoint, and every write point normalizes an
echoed signed link back to the stored reference.

Every install names its own bucket and assets domain in settings, so these tests use a fake
bucket and neutral example.test hosts. All image data is a generated 7x5 PNG.
"""

import base64
import contextlib
import io
import json
import os
import re
import unittest
import uuid
import zlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import reportlab  # noqa: E402

from app.api.v1.endpoints import archive, documents, waiver  # noqa: E402
from app.services import storage  # noqa: E402

FROZEN = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
BUCKET = "fake-shop-bucket"
ASSETS_HOST = "assets.example.test"
ID_KEY = "waivers/pending-ids/0b6f3c1e-1111-4222-8333-944445555666.jpg"
PUBLIC_ID_URL = f"https://{ASSETS_HOST}/{ID_KEY}"
SCROLL_ID_KEY = "scrolls/artist-1/20261110T120000-abcd1234/govt-id.png"


def _synthetic_id_png() -> bytes:
    """A synthetic 7x5 stand-in for an ID photo. Never real client data."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (7, 5), (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


def _settings(**overrides):
    values = {
        "R2_ACCOUNT_ID": "account-id",
        "R2_ACCESS_KEY_ID": "access-key",
        "R2_SECRET_ACCESS_KEY": "secret-key",
        "R2_BUCKET_NAME": BUCKET,
        "R2_PUBLIC_DOMAIN": ASSETS_HOST,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _assert_signed(test: unittest.TestCase, url: str, key: str, bucket: str = BUCKET) -> dict:
    parts = urlsplit(url)
    test.assertEqual(parts.scheme, "https")
    test.assertEqual(parts.hostname, "account-id.r2.cloudflarestorage.com")
    test.assertEqual(parts.path, f"/{bucket}/{key}")
    query = parse_qs(parts.query)
    test.assertEqual(query["X-Amz-Algorithm"], ["AWS4-HMAC-SHA256"])
    test.assertEqual(query["X-Amz-SignedHeaders"], ["host"])
    test.assertTrue(query["X-Amz-Credential"][0].startswith("access-key/"))
    test.assertTrue(query["X-Amz-Credential"][0].endswith("/auto/s3/aws4_request"))
    test.assertRegex(query["X-Amz-Signature"][0], r"^[0-9a-f]{64}$")
    test.assertNotIn("secret-key", url)
    return query


_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
_SHOW_TEXT_RE = re.compile(rb"\((?:\\.|[^\\()])*\)\s*Tj")


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """The show-text strings of a reportlab PDF, compressed or not (stdlib only)."""
    lines: list[str] = []
    for match in _STREAM_RE.finditer(pdf_bytes):
        payload = match.group(1).strip()
        if payload.endswith(b"~>"):
            with contextlib.suppress(Exception):
                payload = base64.a85decode(payload if payload.startswith(b"<~") else b"<~" + payload, adobe=True)
        with contextlib.suppress(Exception):
            payload = zlib.decompress(payload)
        for show in _SHOW_TEXT_RE.finditer(payload):
            body = show.group(0)
            lines.append(body[body.index(b"(") + 1 : body.rindex(b")")].decode("latin-1"))
    return "\n".join(lines)


def _uncompressed_pdfs(test: unittest.TestCase) -> None:
    """Write PDF streams uncompressed so byte-level assertions see every object."""
    previous = reportlab.rl_config.pageCompression
    reportlab.rl_config.pageCompression = 0
    test.addCleanup(setattr, reportlab.rl_config, "pageCompression", previous)


class PresignTests(unittest.TestCase):
    def test_sigv4_query_signing_matches_the_published_aws_example(self):
        # AWS S3 docs, "Authenticating Requests: Using Query Parameters", GET /test.txt example.
        url = storage._presign(
            host="examplebucket.s3.amazonaws.com",
            canonical_uri="/test.txt",
            region="us-east-1",
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            now=datetime(2013, 5, 24, tzinfo=UTC),
            expires_seconds=86400,
        )
        self.assertEqual(
            url,
            "https://examplebucket.s3.amazonaws.com/test.txt"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request"
            "&X-Amz-Date=20130524T000000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host"
            "&X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404",
        )

    def test_presign_get_url_targets_the_r2_s3_endpoint_with_frozen_time(self):
        with patch.object(storage, "get_settings", return_value=_settings()):
            first = storage.presign_get_url(ID_KEY, 300, now=FROZEN)
            second = storage.presign_get_url(ID_KEY, 300, now=FROZEN)
        query = _assert_signed(self, first, ID_KEY)
        self.assertEqual(query["X-Amz-Date"], ["20261110T120000Z"])
        self.assertEqual(query["X-Amz-Expires"], ["300"])
        self.assertEqual(query["X-Amz-Credential"], ["access-key/20261110/auto/s3/aws4_request"])
        self.assertEqual(first, second)

    def test_presign_uses_the_bucket_named_in_settings(self):
        with patch.object(storage, "get_settings", return_value=_settings(R2_BUCKET_NAME="another-shop")):
            url = storage.presign_get_url(ID_KEY, 300, now=FROZEN)
        _assert_signed(self, url, ID_KEY, bucket="another-shop")

    def test_presign_clamps_expiry_to_the_s3_maximum(self):
        with patch.object(storage, "get_settings", return_value=_settings()):
            url = storage.presign_get_url(ID_KEY, 10**9, now=FROZEN)
        self.assertEqual(parse_qs(urlsplit(url).query)["X-Amz-Expires"], ["604800"])

    def test_presign_returns_none_without_credentials_bucket_or_with_traversal(self):
        with patch.object(storage, "get_settings", return_value=_settings(R2_SECRET_ACCESS_KEY="")):
            self.assertIsNone(storage.presign_get_url(ID_KEY))
        with patch.object(storage, "get_settings", return_value=_settings(R2_BUCKET_NAME="  ")):
            self.assertIsNone(storage.presign_get_url(ID_KEY))
        with patch.object(storage, "get_settings", return_value=_settings()):
            self.assertIsNone(storage.presign_get_url("waivers/../secrets.txt"))


class SignedIdUrlTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(storage, "get_settings", return_value=_settings())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_own_host_form_of_a_pending_id_is_signed(self):
        for reference in (
            PUBLIC_ID_URL,
            f"https://pub-account-id.r2.dev/{BUCKET}/{ID_KEY}",
            f"https://account-id.r2.cloudflarestorage.com/{BUCKET}/{ID_KEY}",
            f"{PUBLIC_ID_URL}?cache=1",
        ):
            with self.subTest(reference=reference):
                _assert_signed(self, storage.signed_id_url(reference), ID_KEY)

    def test_exported_waiver_packet_pdf_is_signed(self):
        key = "waivers/2026-11/Jane_Client_WVR-TEST01/Jane_Client_WVR-TEST01.pdf"
        _assert_signed(self, storage.signed_id_url(f"https://{ASSETS_HOST}/{key}"), key)

    def test_scroll_government_id_is_signed(self):
        _assert_signed(self, storage.signed_id_url(f"https://{ASSETS_HOST}/{SCROLL_ID_KEY}"), SCROLL_ID_KEY)

    def test_non_id_and_foreign_references_pass_through_unchanged(self):
        for reference in (
            f"https://{ASSETS_HOST}/portfolios/artist/image.webp",
            f"https://{ASSETS_HOST}/scrolls/artist-1/20261110T120000-abcd1234/bbp-cert.png",
            f"https://{ASSETS_HOST}/waivers/2026-11/Jane_WVR/signature.png",
            f"https://evil.example.test/{ID_KEY}",
            f"https://pub-account-id.r2.dev/other-bucket/{ID_KEY}",
            f"https://account-id.r2.cloudflarestorage.com/other-bucket/{ID_KEY}",
            f"https://pub-other-account.r2.dev/{BUCKET}/{ID_KEY}",
            f"https://{ASSETS_HOST}/portfolios/artist/menu.pdf",
            f"https://{ASSETS_HOST}/waivers/pending-ids/../2026-11/x.pdf",
            f"ftp://{ASSETS_HOST}/{ID_KEY}",
            "/relative/waivers/pending-ids/a.jpg",
            "",
            None,
        ):
            with self.subTest(reference=reference):
                self.assertEqual(storage.signed_id_url(reference), reference)

    def test_an_unconfigured_bucket_resolves_nothing(self):
        with patch.object(storage, "get_settings", return_value=_settings(R2_BUCKET_NAME="")):
            self.assertIsNone(storage.private_id_key(PUBLIC_ID_URL))
            self.assertEqual(storage.signed_id_url(PUBLIC_ID_URL), PUBLIC_ID_URL)

    def test_signed_link_echoed_back_is_normalized_to_the_stored_reference(self):
        signed = storage.signed_id_url(PUBLIC_ID_URL)
        self.assertEqual(storage.canonical_id_reference(signed), PUBLIC_ID_URL)
        self.assertEqual(storage.canonical_id_reference(PUBLIC_ID_URL), PUBLIC_ID_URL)
        foreign = "https://evil.example.test/x.jpg"
        self.assertEqual(storage.canonical_id_reference(foreign), foreign)

    def test_without_a_public_domain_the_reference_is_the_r2_dev_form(self):
        with patch.object(storage, "get_settings", return_value=_settings(R2_PUBLIC_DOMAIN="")):
            signed = storage.signed_id_url(f"https://pub-account-id.r2.dev/{BUCKET}/{ID_KEY}")
            _assert_signed(self, signed, ID_KEY)
            self.assertEqual(storage.canonical_id_reference(signed), f"https://pub-account-id.r2.dev/{BUCKET}/{ID_KEY}")

    def test_signed_id_fields_signs_every_id_key_and_never_mutates_the_input(self):
        stored_scroll = f"https://{ASSETS_HOST}/{SCROLL_ID_KEY}"
        data = {
            "photo_id_url": PUBLIC_ID_URL,
            "photo_id": PUBLIC_ID_URL,
            "govt_id_url": stored_scroll,
            "signature_url": f"https://{ASSETS_HOST}/waivers/2026-11/x/signature.png",
            "certs_stored": {"govt_id": {"url": stored_scroll}, "bbp_cert": {"url": "https://x.example.test/a"}},
        }
        snapshot = json.loads(json.dumps(data))
        view = storage.signed_id_fields(data, 900)
        _assert_signed(self, view["photo_id_url"], ID_KEY)
        _assert_signed(self, view["photo_id"], ID_KEY)
        _assert_signed(self, view["govt_id_url"], SCROLL_ID_KEY)
        _assert_signed(self, view["certs_stored"]["govt_id"]["url"], SCROLL_ID_KEY)
        self.assertEqual(view["signature_url"], data["signature_url"])
        self.assertEqual(view["certs_stored"]["bbp_cert"], data["certs_stored"]["bbp_cert"])
        self.assertEqual(data, snapshot, "stored data must not be mutated")


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    def __init__(self, instance):
        self._instance = instance

    async def execute(self, _statement):
        return _Result(self._instance)


def _waiver_instance(photo_id_url: str):
    data = {
        "reference_code": "WVR-TEST01",
        "full_name": "Jane Client",
        "date_of_birth": "1990-01-01",
        "phone": "5555550100",
        "email": "jane@example.test",
        "emergency_contact": "John",
        "artist_name": "test-artist",
        "tattoo_description": "Blackwork tiger",
        "tattoo_placement": "forearm",
        "pregnant": "No",
        "blood_disorder": "No",
        "skin_condition": "No",
        "photo_id_url": photo_id_url,
    }
    return SimpleNamespace(
        id=uuid.uuid4(),
        template_id=None,
        category="waiver",
        title="Waiver",
        status="pending_review",
        signed_at=FROZEN,
        created_at=FROZEN,
        artist_signed_at=None,
        artist_signature=None,
        artist_id_reviewed=None,
        guest_name="Jane Client",
        guest_email="jane@example.test",
        recipient_name=None,
        recipient_email=None,
        notes=None,
        data_json=json.dumps(data),
    )


def _recording_client(fetched: list[str], respond):
    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, url):
            fetched.append(url)
            return respond(url)

    return _Client


async def _export(test: unittest.TestCase, inst, respond) -> tuple[list[str], dict, dict]:
    fetched: list[str] = []
    captured: dict = {}

    async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
        captured["pdf"] = file_bytes
        return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

    with (
        patch.object(storage, "get_settings", return_value=_settings()),
        patch.object(waiver, "upload_to_drive", _fake_upload),
        patch("httpx.AsyncClient", _recording_client(fetched, respond)),
    ):
        result = await waiver.export_waiver_pdf(
            waiver_id=inst.id,
            db=_Session(inst),
            current_user=SimpleNamespace(id=uuid.uuid4(), name="Test Artist", role="admin"),
        )
    return fetched, captured, result


def _not_found(_url):
    return SimpleNamespace(status_code=404, content=b"")


class StaffReadPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiver_detail_returns_a_signed_link_not_the_public_url(self):
        inst = _waiver_instance(PUBLIC_ID_URL)
        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(waiver, "_require_waiver_access"),
            patch.object(waiver, "_resolve_artist_slug", AsyncMock(return_value="Test Artist")),
            patch.object(waiver, "_resolve_artist_name", AsyncMock(return_value=None)),
        ):
            detail = await waiver.get_waiver(
                waiver_id=inst.id, db=_Session(inst), current_user=SimpleNamespace(role="artist")
            )
        self.assertNotEqual(detail.photo_id_url, PUBLIC_ID_URL)
        query = _assert_signed(self, detail.photo_id_url, ID_KEY)
        self.assertEqual(query["X-Amz-Expires"], [str(waiver._ID_VIEW_SECONDS)])

    async def test_pdf_export_fetches_the_photo_through_a_signed_link(self):
        fetched, _captured, _result = await _export(self, _waiver_instance(PUBLIC_ID_URL), _not_found)
        self.assertEqual(len(fetched), 1)
        _assert_signed(self, fetched[0], ID_KEY)

    async def test_pdf_export_never_fetches_a_foreign_photo_url_but_still_prints_the_section(self):
        _uncompressed_pdfs(self)
        for foreign in (
            "http://169.254.169.254/latest/meta-data/",
            f"https://evil.example.test/{ID_KEY}",
            f"https://pub-account-id.r2.dev/other-bucket/{ID_KEY}",
        ):
            with self.subTest(reference=foreign):
                fetched, captured, _result = await _export(
                    self,
                    _waiver_instance(foreign),
                    lambda _url: SimpleNamespace(status_code=200, content=_synthetic_id_png()),
                )
                self.assertEqual(fetched, [])
                text = extract_pdf_text(captured["pdf"])
                self.assertIn("Photo ID", text)
                self.assertIn("Photo ID is on file but could not be embedded here.", text)
                self.assertNotIn(b"/Subtype /Image", captured["pdf"])

    async def test_pdf_export_never_fetches_foreign_signature_urls(self):
        inst = _waiver_instance(PUBLIC_ID_URL)
        data = json.loads(inst.data_json)
        data["signature_url"] = "http://169.254.169.254/latest/meta-data/"
        data["artist_countersignature"] = "https://evil.example.test/sig.png"
        inst.data_json = json.dumps(data)
        fetched, _captured, _result = await _export(self, inst, _not_found)
        self.assertTrue(all(urlsplit(url).hostname == "account-id.r2.cloudflarestorage.com" for url in fetched))
        self.assertFalse(any("169.254" in url or "evil.example.test" in url for url in fetched))

    async def test_generated_waiver_pdf_embeds_the_id_image_bytes_not_a_link(self):
        _uncompressed_pdfs(self)

        def _respond(url):
            if urlsplit(url).path == f"/{BUCKET}/{ID_KEY}" and "X-Amz-Signature=" in url:
                return SimpleNamespace(status_code=200, content=_synthetic_id_png())
            return _not_found(url)

        fetched, captured, result = await _export(self, _waiver_instance(PUBLIC_ID_URL), _respond)

        pdf = captured["pdf"]
        self.assertTrue(fetched)
        # Exactly one image XObject, with the synthetic fixture's own pixel dimensions.
        self.assertEqual(len(re.findall(rb"/Subtype\s*/Image", pdf)), 1)
        self.assertRegex(pdf, rb"/Height 5\b")
        self.assertRegex(pdf, rb"/Width 7\b")
        # The ID is embedded, never linked: no R2 host, bucket or object key appears anywhere.
        self.assertNotIn(b"pending-ids", pdf)
        self.assertNotIn(ASSETS_HOST.encode(), pdf)
        self.assertNotIn(BUCKET.encode(), pdf)
        self.assertNotIn(b"r2.cloudflarestorage.com", pdf)
        self.assertNotIn(b"/URI", pdf)
        self.assertNotIn("could not be embedded", extract_pdf_text(pdf))
        # The exported packet itself contains the ID, so staff get a signed link to it.
        key = urlsplit(result["pdf_url"]).path.removeprefix(f"/{BUCKET}/")
        self.assertTrue(key.startswith("waivers/") and key.endswith(".pdf"))
        _assert_signed(self, result["pdf_url"], key)

    async def test_an_older_waiver_regenerates_with_its_id_embedded(self):
        # A waiver signed before this change stores the plain public reference on the assets
        # domain. Every export rebuilds the packet, so re-exporting it embeds the ID the new way.
        _uncompressed_pdfs(self)
        inst = _waiver_instance(PUBLIC_ID_URL)
        data = json.loads(inst.data_json)
        data["photo_id"] = data.pop("photo_id_url")  # the legacy key
        inst.data_json = json.dumps(data)
        fetched, captured, _result = await _export(
            self, inst, lambda _url: SimpleNamespace(status_code=200, content=_synthetic_id_png())
        )
        self.assertEqual(len(fetched), 1)
        _assert_signed(self, fetched[0], ID_KEY)
        self.assertEqual(len(re.findall(rb"/Subtype\s*/Image", captured["pdf"])), 1)
        self.assertNotIn(b"pending-ids", captured["pdf"])

    def test_scroll_fields_view_signs_every_government_id_reference(self):
        stored = f"https://{ASSETS_HOST}/{SCROLL_ID_KEY}"
        data = {
            "govt_id_url": stored,
            "bbp_cert_url": f"https://{ASSETS_HOST}/scrolls/a/b/bbp-cert.png",
            "certs_stored": {"govt_id": {"provided": True, "stored": True, "url": stored, "error": None}},
        }
        with patch.object(storage, "get_settings", return_value=_settings()):
            view = documents._scroll_fields_view(data)
        _assert_signed(self, view["govt_id_url"], SCROLL_ID_KEY)
        _assert_signed(self, view["certs_stored"]["govt_id"]["url"], SCROLL_ID_KEY)
        self.assertEqual(view["bbp_cert_url"], data["bbp_cert_url"])
        self.assertEqual(data["govt_id_url"], stored, "stored data must not be mutated")

    def test_document_responses_sign_ids_in_raw_data_json(self):
        inst = _waiver_instance(PUBLIC_ID_URL)
        with patch.object(storage, "get_settings", return_value=_settings()):
            view = json.loads(documents._signed_data_json(inst.data_json))
            untouched = json.dumps({"full_name": "Jane Client", "notes": "photo_id_url is a key name"})
            self.assertIs(documents._signed_data_json(untouched), untouched)
            self.assertIsNone(documents._signed_data_json(None))
            self.assertEqual(documents._signed_data_json("not json photo_id"), "not json photo_id")
        _assert_signed(self, view["photo_id_url"], ID_KEY)
        self.assertEqual(json.loads(inst.data_json)["photo_id_url"], PUBLIC_ID_URL)

    async def test_archive_detail_signs_the_photo_id(self):
        inst = _waiver_instance(PUBLIC_ID_URL)
        data = json.loads(inst.data_json)
        data["photo_id"] = PUBLIC_ID_URL
        inst.data_json = json.dumps(data)
        with patch.object(storage, "get_settings", return_value=_settings()):
            detail = await archive.get_archive_detail(
                record_id=inst.id, current_user=SimpleNamespace(role="admin"), db=_Session(inst)
            )
        _assert_signed(self, detail["data"]["photo_id"], ID_KEY)
        _assert_signed(self, detail["data"]["photo_id_url"], ID_KEY)


class ScrollPdfIdTests(unittest.TestCase):
    def test_scroll_pdf_embeds_the_government_id_and_never_prints_its_private_url(self):
        from app.services.scroll_pdf import ScrollAsset, ScrollPdfInput, build_scroll_pdf

        _uncompressed_pdfs(self)
        private_url = f"https://{ASSETS_HOST}/{SCROLL_ID_KEY}"
        with patch.object(storage, "get_settings", return_value=_settings()):
            pdf = build_scroll_pdf(
                ScrollPdfInput(
                    artist_name="Synthetic Artist",
                    assets=[
                        ScrollAsset(
                            key="govt_id",
                            label="Government ID",
                            filename="id.png",
                            content_type="image/png",
                            data=_synthetic_id_png(),
                            url=private_url,
                            stored=True,
                        )
                    ],
                )
            )
        self.assertRegex(pdf, rb"/Subtype\s*/Image")
        self.assertRegex(pdf, rb"/Width 7\b")
        self.assertNotIn(SCROLL_ID_KEY.encode(), pdf)
        self.assertNotIn(ASSETS_HOST.encode(), pdf)
        self.assertNotIn(b"/URI", pdf)
        self.assertIn("Private", extract_pdf_text(pdf))


class UploadAndSubmitTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_returns_a_signed_preview_and_submit_stores_the_reference(self):
        upload = SimpleNamespace(content_type="image/jpeg", read=AsyncMock(return_value=b"\xff\xd8jpeg"))
        captured: dict = {}

        async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
            captured["folder_path"] = folder_path
            return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(waiver, "upload_to_drive", _fake_upload),
        ):
            response = await waiver.upload_photo_id(file=upload)
            signed = response["photo_id_url"]
            data = {"photo_id_url": signed}
            documents._verify_public_id_fields(data)
            stored = documents._require_waiver_photo_id(data)
            from_waiver_body = waiver.require_uploaded_id(signed)

        self.assertEqual(captured["folder_path"], "waivers/pending-ids")
        key = urlsplit(stored).path.lstrip("/")
        self.assertTrue(key.startswith("waivers/pending-ids/"))
        query = _assert_signed(self, signed, key)
        self.assertEqual(query["X-Amz-Expires"], [str(waiver._ID_PREVIEW_SECONDS)])
        self.assertEqual(stored, f"https://{ASSETS_HOST}/{key}")
        self.assertEqual(from_waiver_body, stored)


# ── Upload capability: a public caller files only an ID it uploaded itself ──

VICTIM_KEY = "waivers/pending-ids/7c2d9e40-aaaa-4bbb-8ccc-9ddddeeeefff.jpg"
VICTIM_PUBLIC_URL = f"https://{ASSETS_HOST}/{VICTIM_KEY}"


def _frozen_clock(at: datetime):
    """Freeze storage's clock: links are signed and checked at `at`, never the real time."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz else at.replace(tzinfo=None)

    return patch.object(storage, "datetime", _Clock)


class _ScriptedResult:
    def __init__(self, first=None, scalar=None, scalars_first=None):
        self._first, self._scalar, self._scalars_first = first, scalar, scalars_first

    def first(self):
        return self._first

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(first=lambda: self._scalars_first)


class _FakeDb:
    """Answers queries in order and records writes. A query past the scripted ones fails the test."""

    def __init__(self, *results):
        self.results = list(results)
        self.added: list = []

    async def execute(self, _query):
        if not self.results:
            raise AssertionError("unexpected database query")
        return self.results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def refresh(self, obj):
        obj.id = obj.id or uuid.uuid4()
        obj.created_at = obj.created_at or FROZEN


def _discard(coro):
    coro.close()


def _waiver_body(photo_id_url: str):
    from app.schemas.waiver import WaiverSubmit

    # model_construct: EmailStr rejects the reserved example.test domain these synthetic records use.
    return WaiverSubmit.model_construct(
        full_name="Alex Attacker",
        date_of_birth="1990-01-01",
        phone="5555550199",
        email="alex@example.test",
        emergency_contact="Sam 5555550198",
        emergency_contact_address="1 Example Way",
        artist_slug="test-artist",
        tattoo_description="Small star",
        tattoo_placement="Wrist",
        pregnant="No",
        blood_thinners="No",
        blood_disorder="No",
        skin_condition="No",
        photo_id_url=photo_id_url,
        health_and_eligibility=True,
        risks_and_consent=True,
        accept_terms=True,
        signature_data="data:image/png;base64,AAAA",
    )


def _magic_link_instance(email: str = "alex@example.test", data: dict | None = None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        category="waiver",
        status="sent",
        send_token="token-1",
        expires_at=None,
        guest_name="Alex Attacker",
        guest_email=email,
        recipient_name=None,
        recipient_email=email,
        guest_social=None,
        data_json=json.dumps(data or {"checkin_verified": True, "checkin_source": "lookup:email"}),
        signed_at=None,
        updated_at=None,
        reminder_sent=False,
    )


def _public_waiver_data(**extra) -> dict:
    return {
        "full_name": "Alex Attacker",
        "email": "alex@example.test",
        "tattoo_description": "Small star",
        "tattoo_placement": "Wrist",
        "quoted_price": "120",
        "intoxicated": "No",
        "pregnant": "No",
        "blood_thinners": "No",
        "blood_disorder": "No",
        "skin_condition": "No",
        "health_and_eligibility": True,
        "risks_and_consent": True,
        "accept_terms": True,
        **extra,
    }


class VerifiedUploadKeyTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(storage, "get_settings", return_value=_settings())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _link(self, key: str = ID_KEY, expires: int = 600, **settings) -> str:
        with patch.object(storage, "get_settings", return_value=_settings(**settings)):
            return storage.presign_get_url(key, expires, now=FROZEN)

    def test_an_unexpired_link_this_server_signed_resolves_to_its_key(self):
        link = self._link()
        self.assertEqual(storage.verified_upload_key(link, now=FROZEN), ID_KEY)
        self.assertEqual(storage.verified_upload_key(link, now=FROZEN.replace(minute=9, second=59)), ID_KEY)
        self.assertEqual(storage.uploaded_id_reference(link, now=FROZEN), PUBLIC_ID_URL)

    def test_a_bare_url_to_a_private_id_key_is_refused(self):
        for reference in (
            VICTIM_PUBLIC_URL,
            f"https://pub-account-id.r2.dev/{BUCKET}/{VICTIM_KEY}",
            f"https://account-id.r2.cloudflarestorage.com/{BUCKET}/{VICTIM_KEY}",
        ):
            with self.subTest(reference=reference):
                self.assertIsNone(storage.verified_upload_key(reference, now=FROZEN))

    def test_an_expired_link_is_refused(self):
        link = self._link(expires=600)
        self.assertIsNone(storage.verified_upload_key(link, now=FROZEN.replace(minute=10)))
        self.assertIsNone(storage.verified_upload_key(link, now=datetime(2026, 11, 11, tzinfo=UTC)))

    def test_a_signature_for_a_different_key_is_refused(self):
        link = self._link(ID_KEY)
        swapped = link.replace(f"/{ID_KEY}?", f"/{VICTIM_KEY}?")
        self.assertNotEqual(swapped, link)
        self.assertIsNone(storage.verified_upload_key(swapped, now=FROZEN))

    def test_a_signature_made_with_a_different_secret_is_refused(self):
        foreign = self._link(VICTIM_KEY, R2_SECRET_ACCESS_KEY="another-secret")
        self.assertIsNone(storage.verified_upload_key(foreign, now=FROZEN))
        other_key_id = self._link(VICTIM_KEY, R2_ACCESS_KEY_ID="another-access-key")
        self.assertIsNone(storage.verified_upload_key(other_key_id, now=FROZEN))

    def test_tampered_expiry_date_host_or_extra_parameters_are_refused(self):
        link = self._link()
        for tampered in (
            link.replace("X-Amz-Expires=600", "X-Amz-Expires=604800"),
            link.replace("X-Amz-Date=20261110T120000Z", "X-Amz-Date=20261110T130000Z"),
            link.replace("account-id.r2.cloudflarestorage.com", "evil.example.test"),
            link.replace(f"/{BUCKET}/", "/other-bucket/"),
            link.replace("https://", "http://"),
            link + "&X-Amz-Expires=600",
            link + "&extra=1",
            link[:-1] + ("0" if link[-1] != "0" else "1"),
            link.split("&X-Amz-Signature=")[0],
        ):
            with self.subTest(tampered=tampered):
                self.assertIsNone(storage.verified_upload_key(tampered, now=FROZEN))

    def test_a_correctly_signed_link_outside_pending_ids_is_refused(self):
        packet = "waivers/2026-11/Jane_Client_WVR-TEST01/Jane_Client_WVR-TEST01.pdf"
        for key in (packet, SCROLL_ID_KEY, "portfolios/artist/image.webp", "waivers/pending-ids/../2026-11/x.pdf"):
            with self.subTest(key=key):
                link = storage._presign(
                    host="account-id.r2.cloudflarestorage.com",
                    canonical_uri=f"/{BUCKET}/{key}",
                    region="auto",
                    access_key="access-key",
                    secret_key="secret-key",
                    now=FROZEN,
                    expires_seconds=600,
                )
                self.assertIsNone(storage.verified_upload_key(link, now=FROZEN))

    def test_nothing_resolves_without_signing_credentials(self):
        link = self._link()
        with patch.object(storage, "get_settings", return_value=_settings(R2_SECRET_ACCESS_KEY="")):
            self.assertIsNone(storage.verified_upload_key(link, now=FROZEN))
        for reference in ("", None, "not a url", "https://[bad", 42):
            with self.subTest(reference=reference):
                self.assertIsNone(storage.verified_upload_key(reference, now=FROZEN))


class PublicIdWriteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for patcher in (
            patch.object(storage, "get_settings", return_value=_settings()),
            _frozen_clock(FROZEN),
            patch.object(waiver, "spawn_background", _discard),
            patch.object(documents, "spawn_background", _discard),
            patch.object(waiver, "_upload_waiver_files", AsyncMock()),
            patch.object(documents, "_upload_magic_link_files", AsyncMock()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _upload(self) -> str:
        upload = SimpleNamespace(content_type="image/jpeg", read=AsyncMock(return_value=b"\xff\xd8jpeg"))

        async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
            return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

        with patch.object(waiver, "upload_to_drive", _fake_upload):
            return (await waiver.upload_photo_id(file=upload))["photo_id_url"]

    async def test_upload_then_waiver_submit_stores_the_uploaded_reference(self):
        signed = await self._upload()
        key = urlsplit(signed).path.split(f"/{BUCKET}/", 1)[1]
        db = _FakeDb(
            _ScriptedResult(first=(uuid.uuid4(), "Test Artist")),
            _ScriptedResult(scalar=None),
            _ScriptedResult(scalars_first=None),
        )
        response = await waiver.submit_waiver(_waiver_body(signed), db=db)
        instance = next(obj for obj in db.added if isinstance(obj, waiver.DocumentInstance))
        self.assertEqual(json.loads(instance.data_json)["photo_id_url"], f"https://{ASSETS_HOST}/{key}")
        self.assertEqual(response.reference_code, json.loads(instance.data_json)["reference_code"])
        waiver._upload_waiver_files.assert_called_once()
        self.assertEqual(waiver._upload_waiver_files.call_args.kwargs["photo_id_url"], f"https://{ASSETS_HOST}/{key}")

    async def test_waiver_submit_refuses_bare_expired_and_foreign_links_and_stores_nothing(self):
        signed = await self._upload()
        foreign = storage._presign(
            host="account-id.r2.cloudflarestorage.com",
            canonical_uri=f"/{BUCKET}/{VICTIM_KEY}",
            region="auto",
            access_key="access-key",
            secret_key="another-secret",
            now=FROZEN,
            expires_seconds=600,
        )
        for reference, clock in (
            (VICTIM_PUBLIC_URL, FROZEN),
            (signed, FROZEN.replace(hour=13)),
            (foreign, FROZEN),
            ("https://evil.example.test/id.jpg", FROZEN),
        ):
            with self.subTest(reference=reference), _frozen_clock(clock):
                db = _FakeDb()
                with self.assertRaises(waiver.HTTPException) as caught:
                    await waiver.submit_waiver(_waiver_body(reference), db=db)
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(caught.exception.detail, "Upload the ID again")
                self.assertEqual(db.added, [])

    async def test_magic_link_complete_stores_the_uploaded_reference(self):
        signed = await self._upload()
        key = urlsplit(signed).path.split(f"/{BUCKET}/", 1)[1]
        instance = _magic_link_instance()
        db = _FakeDb(_ScriptedResult(scalar=instance), _ScriptedResult(scalars_first=None))
        body = documents.DocumentPublicSubmit(
            data=_public_waiver_data(photo_id_url=signed), signature_data="data:image/png;base64,AAAA"
        )
        response = await documents.complete_public_form("token-1", body, db=db)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(json.loads(instance.data_json)["photo_id_url"], f"https://{ASSETS_HOST}/{key}")

    async def test_checkin_attack_cannot_file_or_resign_the_victims_id(self):
        # The attacker verified their own contact, then tries to file the victim's ID object (bare, or an
        # old link that has since expired) so a later check-in lookup would sign it as theirs.
        with _frozen_clock(FROZEN.replace(hour=8)):
            stale_link = storage.presign_get_url(VICTIM_KEY, 600)
        before = _magic_link_instance().data_json
        for reference in (VICTIM_PUBLIC_URL, stale_link):
            for field in ("photo_id_url", "photo_id", "govt_id_url"):
                with self.subTest(reference=reference, field=field):
                    instance = _magic_link_instance()
                    db = _FakeDb(_ScriptedResult(scalar=instance))
                    body = documents.DocumentPublicSubmit(
                        data=_public_waiver_data(**{field: reference}), signature_data="data:image/png;base64,AAAA"
                    )
                    with self.assertRaises(documents.HTTPException) as caught:
                        await documents.complete_public_form("token-1", body, db=db)
                    self.assertEqual(caught.exception.status_code, 422)
                    self.assertEqual(caught.exception.detail, "Upload the ID again")
                    self.assertEqual(instance.data_json, before)
                    self.assertEqual(instance.status, "sent")
                    self.assertEqual(db.added, [])
        with self.assertRaises(waiver.HTTPException):
            await waiver.submit_waiver(_waiver_body(stale_link), db=_FakeDb())

        # Nothing was filed, so the attacker's check-in has no ID on file to sign.
        lookup_db = _FakeDb(_ScriptedResult(scalar=None))
        self.assertIsNone(await documents._latest_prior_waiver(lookup_db, {"alex@example.test"}))

    async def test_public_form_drops_a_client_supplied_certs_stored(self):
        signed = await self._upload()
        instance = _magic_link_instance()
        db = _FakeDb(_ScriptedResult(scalar=instance), _ScriptedResult(scalars_first=None))
        body = documents.DocumentPublicSubmit(
            data=_public_waiver_data(photo_id_url=signed, certs_stored={"govt_id": {"url": VICTIM_PUBLIC_URL}}),
            signature_data="data:image/png;base64,AAAA",
        )
        await documents.complete_public_form("token-1", body, db=db)
        self.assertNotIn("certs_stored", json.loads(instance.data_json))
        self.assertNotIn(VICTIM_KEY, instance.data_json)

    async def test_verified_reuse_copies_the_id_on_file_server_side(self):
        prior = {"id": str(uuid.uuid4()), "signed_at": FROZEN, "data": {"photo_id_url": PUBLIC_ID_URL}}
        instance = _magic_link_instance()
        db = _FakeDb(_ScriptedResult(scalar=instance), _ScriptedResult(scalars_first=None))
        body = documents.DocumentPublicSubmit(
            data=_public_waiver_data(reuse_photo_id=True), signature_data="data:image/png;base64,AAAA"
        )
        with patch.object(documents, "_latest_prior_waiver", AsyncMock(return_value=prior)):
            await documents.complete_public_form("token-1", body, db=db)
        stored = json.loads(instance.data_json)
        self.assertEqual(stored["photo_id_url"], PUBLIC_ID_URL)
        self.assertEqual(stored["photo_id_reused_from"], prior["id"])

    async def test_a_client_account_cannot_store_another_persons_id_reference(self):
        from app.schemas.document import DocumentInstanceCreate

        body = DocumentInstanceCreate(
            category="waiver", title="Waiver", data_json=json.dumps({"photo_id_url": VICTIM_PUBLIC_URL})
        )
        collector = SimpleNamespace(id=uuid.uuid4(), role="collector", name="Alex Attacker")
        db = _FakeDb()
        with self.assertRaises(documents.HTTPException) as caught:
            await documents.submit_document(body, db=db, current_user=collector)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(db.added, [])

        plain = DocumentInstanceCreate(category="form", title="Intake", data_json='{"note": "hello"}')
        db = _FakeDb()
        await documents.submit_document(plain, db=db, current_user=collector)
        self.assertEqual(db.added[0].data_json, '{"note": "hello"}')

    async def test_assistants_and_artists_cannot_store_another_persons_id_reference(self):
        from app.schemas.document import DocumentInstanceCreate

        raw = json.dumps({"photo_id_url": VICTIM_PUBLIC_URL})
        for role in ("assistant", "artist"):
            with self.subTest(role=role):
                user = SimpleNamespace(id=uuid.uuid4(), role=role, name="Test Staff")
                db = _FakeDb()
                with self.assertRaises(documents.HTTPException) as caught:
                    await documents.submit_document(
                        DocumentInstanceCreate(category="waiver", title="Waiver", data_json=raw),
                        db=db,
                        current_user=user,
                    )
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(db.added, [])

    async def test_admin_document_submit_keeps_current_behavior(self):
        from app.schemas.document import DocumentInstanceCreate

        raw = json.dumps({"photo_id_url": PUBLIC_ID_URL})
        admin = SimpleNamespace(id=uuid.uuid4(), role="admin", name="Test Admin")
        db = _FakeDb()
        await documents.submit_document(
            DocumentInstanceCreate(category="waiver", title="Waiver", data_json=raw), db=db, current_user=admin
        )
        self.assertEqual(db.added[0].data_json, raw)


if __name__ == "__main__":
    unittest.main()
