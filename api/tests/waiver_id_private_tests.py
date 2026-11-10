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
            f"https://{ASSETS_HOST}/scrolls-public/artist-1/20261110T120000-abcd1234/bbp-cert.png",
            f"https://{ASSETS_HOST}/avatars/u-1/signature.png",
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
        # Waiver signature images are private too (waivers/ is private by key).
        _assert_signed(self, view["signature_url"], "waivers/2026-11/x/signature.png")
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
            "bbp_cert_url": f"https://{ASSETS_HOST}/scrolls-public/a/b/bbp-cert.png",
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
            stored = documents._require_waiver_photo_id({"photo_id_url": signed})
            from_waiver_body = storage.canonical_id_reference(signed)

        self.assertEqual(captured["folder_path"], "waivers/pending-ids")
        key = urlsplit(stored).path.lstrip("/")
        self.assertTrue(key.startswith("waivers/pending-ids/"))
        query = _assert_signed(self, signed, key)
        self.assertEqual(query["X-Amz-Expires"], [str(waiver._ID_PREVIEW_SECONDS)])
        self.assertEqual(stored, f"https://{ASSETS_HOST}/{key}")
        self.assertEqual(from_waiver_body, stored)


if __name__ == "__main__":
    unittest.main()
