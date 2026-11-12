"""Founders and admins can always open and print a complete waiver record.

Waiver ID photos and signature images are private in R2. That must never cost the shop its records:
the printable waiver PDF exported by a founder or an admin still embeds the client's ID image, the
client's signature and the artist's countersignature as images, and prints every filled form value
as text, with no storage URL anywhere in the file. A founder opening the waiver detail gets
short-lived signed links for the ID and the signature.

Synthetic data only: generated PNGs with distinct pixel sizes, a fake bucket and example.test hosts.
"""

import json
import os
import re
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from waiver_id_private_tests import (  # noqa: E402
    ASSETS_HOST,
    BUCKET,
    ID_KEY,
    PUBLIC_ID_URL,
    _assert_signed,
    _recording_client,
    _Session,
    _settings,
    _uncompressed_pdfs,
    _waiver_instance,
    extract_pdf_text,
)

from app.api.v1.endpoints import waiver  # noqa: E402
from app.services import storage  # noqa: E402

CLIENT_SIG_KEY = "waivers/2026-11/Jane_Client_WVR-TEST01/signature.png"
ARTIST_SIG_KEY = "signatures/00000000-0000-4000-8000-0000000000aa/artist-sig-fixture.png"
# Distinct fixture sizes, so each embedded image is identified by its own dimensions.
ID_SIZE, CLIENT_SIG_SIZE, ARTIST_SIG_SIZE = (7, 5), (13, 4), (9, 3)
FORM_VALUES = ("Jane Client", "1990-01-01", "5555550100", "jane@example.test", "Blackwork tiger", "forearm")


def _png(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


FIXTURES = {
    ID_KEY: _png(ID_SIZE, (200, 30, 30)),
    CLIENT_SIG_KEY: _png(CLIENT_SIG_SIZE, (20, 20, 20)),
    ARTIST_SIG_KEY: _png(ARTIST_SIG_SIZE, (40, 40, 160)),
}


def _record():
    inst = _waiver_instance(PUBLIC_ID_URL)
    data = json.loads(inst.data_json)
    data["signature_url"] = f"https://{ASSETS_HOST}/{CLIENT_SIG_KEY}"
    data["artist_countersignature"] = f"https://{ASSETS_HOST}/{ARTIST_SIG_KEY}"
    data["countersigned_by"] = "Synthetic Artist"
    inst.data_json = json.dumps(data)
    inst.status = "approved"
    return inst


def _respond(url: str):
    """Serve the fixtures only through signed links on the R2 S3 endpoint, like a private bucket."""
    parts = urlsplit(url)
    key = parts.path.removeprefix(f"/{BUCKET}/")
    if parts.hostname == "account-id.r2.cloudflarestorage.com" and "X-Amz-Signature=" in url and key in FIXTURES:
        return SimpleNamespace(status_code=200, content=FIXTURES[key])
    return SimpleNamespace(status_code=403, content=b"")


def _image_sizes(pdf: bytes) -> list[tuple[int, int]]:
    sizes = []
    for header in re.findall(rb"<<(?:(?!>>).)*?/Subtype\s*/Image(?:(?!>>).)*>>", pdf, re.S):
        width = re.search(rb"/Width (\d+)", header)
        height = re.search(rb"/Height (\d+)", header)
        if width and height:
            sizes.append((int(width.group(1)), int(height.group(1))))
    return sizes


class WaiverPdfExportForOwnersTests(unittest.IsolatedAsyncioTestCase):
    async def _export_as(self, role: str):
        _uncompressed_pdfs(self)
        inst = _record()
        fetched: list[str] = []
        captured: dict = {}

        async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
            captured["pdf"] = file_bytes
            return f"https://{ASSETS_HOST}/{folder_path}/{filename}"

        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(waiver, "upload_to_drive", _fake_upload),
            patch("httpx.AsyncClient", _recording_client(fetched, _respond)),
        ):
            result = await waiver.export_waiver_pdf(
                waiver_id=inst.id,
                db=_Session(inst),
                current_user=SimpleNamespace(id=uuid.uuid4(), name=f"Synthetic {role}", role=role),
            )
        return captured["pdf"], fetched, result

    async def _assert_complete_record(self, role: str):
        pdf, fetched, result = await self._export_as(role)

        # Every image came from our own private storage through a signed link.
        self.assertEqual(len(fetched), 3)
        for url in fetched:
            self.assertEqual(urlsplit(url).hostname, "account-id.r2.cloudflarestorage.com")
            self.assertIn("X-Amz-Signature=", url)

        # The ID image, the client signature and the artist countersignature are embedded images.
        sizes = _image_sizes(pdf)
        for size in (ID_SIZE, CLIENT_SIG_SIZE, ARTIST_SIG_SIZE):
            self.assertIn(size, sizes, f"missing embedded image {size} for {role}")

        # The filled form values print as text.
        text = extract_pdf_text(pdf)
        for value in FORM_VALUES:
            self.assertIn(value, text)
        self.assertIn("Client Signature", text)
        self.assertIn("Photo ID", text)
        self.assertNotIn("could not be embedded", text)

        # No storage URL, host, bucket or key anywhere in the file.
        for needle in (
            ASSETS_HOST.encode(),
            BUCKET.encode(),
            b"r2.cloudflarestorage.com",
            b"r2.dev",
            b"pending-ids",
            b"signature.png",
            b"artist-sig-fixture",
            b"/URI",
        ):
            self.assertNotIn(needle, pdf)

        # The exported packet itself is a private object: the caller gets a signed link to download it.
        key = urlsplit(result["pdf_url"]).path.removeprefix(f"/{BUCKET}/")
        self.assertTrue(key.startswith("waivers/") and key.endswith(".pdf"))
        _assert_signed(self, result["pdf_url"], key)

    async def test_founder_exports_a_complete_waiver_pdf(self):
        await self._assert_complete_record("founder")

    async def test_admin_exports_a_complete_waiver_pdf(self):
        await self._assert_complete_record("admin")


class WaiverDetailForOwnersTests(unittest.IsolatedAsyncioTestCase):
    async def test_founder_opening_a_waiver_gets_signed_id_and_signature_links(self):
        inst = _record()
        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(waiver, "_resolve_artist_slug", AsyncMock(return_value="Synthetic Artist")),
            patch.object(waiver, "_resolve_artist_name", AsyncMock(return_value=None)),
        ):
            detail = await waiver.get_waiver(
                waiver_id=inst.id,
                db=_Session(inst),
                current_user=SimpleNamespace(id=uuid.uuid4(), role="founder"),
            )
        _assert_signed(self, detail.photo_id_url, ID_KEY)
        _assert_signed(self, detail.signature_url, CLIENT_SIG_KEY)
        self.assertEqual(detail.full_name, "Jane Client")


class SignatureStorageTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(storage, "get_settings", return_value=_settings())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_waiver_and_saved_signatures_are_private_by_key(self):
        for key in (CLIENT_SIG_KEY, ARTIST_SIG_KEY, "scrolls/artist-1/20261110T120000-abcd1234/artist-sig.png"):
            with self.subTest(key=key):
                self.assertTrue(storage.is_private_id_key(key))
                _assert_signed(self, storage.signed_id_url(f"https://{ASSETS_HOST}/{key}"), key)

    def test_a_countersign_echoing_the_signed_saved_signature_stores_the_stable_reference(self):
        stored = f"https://{ASSETS_HOST}/{ARTIST_SIG_KEY}"
        signed = storage.signed_id_url(stored)
        self.assertEqual(storage.canonical_id_reference(signed), stored)
        self.assertEqual(storage.canonical_id_reference("data:image/png;base64,AAAA"), "data:image/png;base64,AAAA")

    def test_archive_and_document_views_sign_waiver_signatures(self):
        data = json.loads(_record().data_json)
        view = storage.signed_id_fields(data, 900)
        _assert_signed(self, view["signature_url"], CLIENT_SIG_KEY)
        _assert_signed(self, view["artist_countersignature"], ARTIST_SIG_KEY)
        _assert_signed(self, view["photo_id_url"], ID_KEY)
        self.assertEqual(view["full_name"], "Jane Client")


class InlineSignatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_inline_data_uri_signature_is_decoded_and_a_foreign_url_is_never_fetched(self):
        import base64

        inline = "data:image/png;base64," + base64.b64encode(FIXTURES[CLIENT_SIG_KEY]).decode()
        fetched: list[str] = []
        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch("httpx.AsyncClient", _recording_client(fetched, _respond)),
        ):
            self.assertIsNotNone(await waiver._signature_image_bytes(inline))
            self.assertIsNone(await waiver._signature_image_bytes("https://evil.example.test/sig.png"))
            self.assertIsNone(await waiver._signature_image_bytes("not base64 !!"))
        self.assertEqual(fetched, [])


if __name__ == "__main__":
    unittest.main()
