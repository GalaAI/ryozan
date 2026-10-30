"""
Flash upload, public storefront, normalization, and storage-key tests.

No existing suite exercised the flash upload path, image content verification, EXIF
handling, collision-resistant R2 keys, or the upload endpoint's access control, so this
file owns those regressions. Real image bytes are built with Pillow in-test rather than
mocked, so the checks reflect what Pillow actually accepts and rejects.
"""

import io
import os
import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from PIL import Image

from app.services import image_processing, storage
from app.services.hf_vision import (
    SegmentationUnavailableError,
    classify_detection_role,
    segment_flash_sheet,
)
from app.services.image_processing import (
    ImageValidationError,
    NormalizedImage,
    create_design_cutout_preview,
    create_design_preview,
    validate_and_normalize_image,
)

# ── Image fixtures ────────────────────────────────────


def _image_bytes(fmt: str, size=(64, 48), color=(200, 100, 50), mode="RGB", **save_kw) -> bytes:
    img = Image.new(mode, size, color)
    buffer = io.BytesIO()
    img.save(buffer, format=fmt, **save_kw)
    return buffer.getvalue()


def _jpeg_with_exif_orientation(orientation: int, size=(60, 40)) -> bytes:
    """A JPEG whose EXIF says it should be rotated; used to prove exif_transpose runs."""
    img = Image.new("RGB", size, (10, 120, 200))
    exif = img.getexif()
    exif[0x0112] = orientation  # 0x0112 == Orientation tag
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _mpo_bytes() -> bytes:
    primary = Image.new("RGB", (80, 60), (220, 30, 20))
    auxiliary = Image.new("RGB", (40, 30), (20, 30, 220))
    buffer = io.BytesIO()
    primary.save(buffer, format="MPO", save_all=True, append_images=[auxiliary])
    return buffer.getvalue()


# ── image_processing unit tests ───────────────────────


class ImageValidationTests(unittest.TestCase):
    def test_valid_jpeg_normalizes(self):
        result = validate_and_normalize_image(_image_bytes("JPEG"), "sheet.jpg")
        self.assertIsInstance(result, NormalizedImage)
        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertEqual(result.extension, "jpg")
        self.assertEqual(result.source_format, "JPEG")
        # Re-decodes cleanly.
        Image.open(io.BytesIO(result.data)).verify()

    def test_valid_png_normalizes(self):
        result = validate_and_normalize_image(_image_bytes("PNG"), "sheet.png")
        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual(result.extension, "png")

    def test_valid_webp_normalizes(self):
        result = validate_and_normalize_image(_image_bytes("WEBP"), "sheet.webp")
        self.assertEqual(result.mime_type, "image/webp")
        self.assertEqual(result.extension, "webp")

    def test_mpo_primary_frame_becomes_ordinary_jpeg(self):
        result = validate_and_normalize_image(_mpo_bytes(), "portrait.jpg")
        self.assertEqual(result.source_format, "MPO")
        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertIn("selected_mpo_primary_frame", result.recovery_actions)
        output = Image.open(io.BytesIO(result.data))
        self.assertEqual(output.format, "JPEG")
        self.assertEqual(output.size, (80, 60))
        self.assertEqual(getattr(output, "n_frames", 1), 1)

    def test_utf8_latin1_binary_recode_is_recovered(self):
        original = _image_bytes("JPEG")
        corrupted = original.decode("latin-1").encode("utf-8")
        result = validate_and_normalize_image(corrupted, "damaged.jpg")
        self.assertEqual(result.source_format, "JPEG")
        self.assertIn("reversed_utf8_latin1_recode", result.recovery_actions)
        Image.open(io.BytesIO(result.data)).verify()

    def test_missing_mime_is_irrelevant_bytes_decide(self):
        # The browser MIME is never passed in; only bytes + filename hint. A valid JPEG
        # with a misleading .png name still validates because bytes are authoritative.
        result = validate_and_normalize_image(_image_bytes("JPEG"), "actually-a-jpeg.png")
        self.assertEqual(result.source_format, "JPEG")

    def test_fake_image_with_image_extension_rejected(self):
        with self.assertRaises(ImageValidationError) as ctx:
            validate_and_normalize_image(b"this is not an image at all", "evil.jpg")
        self.assertEqual(ctx.exception.code, "FLASH_UNREADABLE_IMAGE")

    def test_empty_file_rejected(self):
        with self.assertRaises(ImageValidationError) as ctx:
            validate_and_normalize_image(b"", "empty.jpg")
        self.assertEqual(ctx.exception.code, "FLASH_EMPTY_FILE")

    def test_unsupported_gif_rejected(self):
        with self.assertRaises(ImageValidationError) as ctx:
            validate_and_normalize_image(_image_bytes("GIF"), "anim.gif")
        self.assertEqual(ctx.exception.code, "FLASH_UNSUPPORTED_FORMAT")

    def test_unsupported_bmp_rejected(self):
        with self.assertRaises(ImageValidationError) as ctx:
            validate_and_normalize_image(_image_bytes("BMP"), "pic.bmp")
        self.assertEqual(ctx.exception.code, "FLASH_UNSUPPORTED_FORMAT")

    def test_heic_extension_gets_specific_message(self):
        # Pillow can't decode HEIC without a plugin; the .heic hint should produce a
        # HEIC-specific unsupported message rather than the generic unreadable one.
        with self.assertRaises(ImageValidationError) as ctx:
            validate_and_normalize_image(b"\x00\x00\x00\x18ftypheic not-real", "photo.heic")
        self.assertEqual(ctx.exception.code, "FLASH_UNSUPPORTED_FORMAT")
        self.assertIn("HEIC", str(ctx.exception))

    def test_file_at_size_boundary_ok(self):
        # A file exactly at MAX_UPLOAD_BYTES must pass the size gate. Build a small valid
        # image, then pad conceptually by asserting the boundary comparison is inclusive.
        small = _image_bytes("JPEG")
        self.assertLessEqual(len(small), image_processing.MAX_UPLOAD_BYTES)
        # Directly exercise the boundary: a byte string exactly at the cap is not rejected
        # for size (it will fail later as unreadable, proving size passed first).
        at_cap = b"x" * image_processing.MAX_UPLOAD_BYTES
        with self.assertRaises(ImageValidationError) as ctx:
            validate_and_normalize_image(at_cap, "boundary.jpg")
        self.assertNotEqual(ctx.exception.code, "FLASH_FILE_TOO_LARGE")

    def test_file_above_size_boundary_rejected(self):
        over = b"x" * (image_processing.MAX_UPLOAD_BYTES + 1)
        with self.assertRaises(ImageValidationError) as ctx:
            validate_and_normalize_image(over, "toobig.jpg")
        self.assertEqual(ctx.exception.code, "FLASH_FILE_TOO_LARGE")

    def test_oversized_dimensions_are_downscaled_not_rejected(self):
        big = _image_bytes("JPEG", size=(6000, 200))
        result = validate_and_normalize_image(big, "wide.jpg")
        self.assertLessEqual(max(result.width, result.height), image_processing.MAX_EDGE_PX)

    def test_exif_orientation_is_applied_and_stripped(self):
        # Orientation 6 = rotate 90 CW; after transpose the tag must be gone and the
        # width/height swapped relative to the stored pixel grid.
        data = _jpeg_with_exif_orientation(6, size=(60, 40))
        result = validate_and_normalize_image(data, "rotated.jpg")
        out = Image.open(io.BytesIO(result.data))
        # exif_transpose swaps dimensions for orientation 6/8.
        self.assertEqual((out.width, out.height), (40, 60))
        # Metadata stripped: no orientation tag survives.
        self.assertNotIn(0x0112, out.getexif())

    def test_metadata_is_stripped(self):
        data = _jpeg_with_exif_orientation(1, size=(50, 50))
        result = validate_and_normalize_image(data, "meta.jpg")
        out = Image.open(io.BytesIO(result.data))
        self.assertEqual(len(dict(out.getexif())), 0)

    def test_design_preview_is_webp_with_percentage_geometry(self):
        source = _image_bytes("PNG", size=(200, 100))
        preview = create_design_preview(
            source,
            {"xmin": 20, "ymin": 10, "xmax": 120, "ymax": 60},
        )
        self.assertEqual(preview.mime_type, "image/webp")
        self.assertEqual(preview.extension, "webp")
        self.assertEqual(preview.pixel_box, {"xmin": 20, "ymin": 10, "xmax": 120, "ymax": 60})
        self.assertEqual(preview.percent_box, {"x": 10.0, "y": 10.0, "w": 50.0, "h": 50.0})
        output = Image.open(io.BytesIO(preview.data))
        self.assertEqual(output.format, "WEBP")
        self.assertEqual(output.size, (100, 50))

    def test_design_cutout_preserves_png_alpha_and_source_geometry(self):
        cutout = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
        for x in range(10, 90):
            for y in range(5, 45):
                cutout.putpixel((x, y), (180, 90, 40, 255))
        buffer = io.BytesIO()
        cutout.save(buffer, format="PNG")

        preview = create_design_cutout_preview(
            buffer.getvalue(),
            {"xmin": 20, "ymin": 10, "xmax": 120, "ymax": 60},
            source_width=200,
            source_height=100,
        )

        self.assertEqual(preview.mime_type, "image/png")
        self.assertEqual(preview.percent_box, {"x": 10.0, "y": 10.0, "w": 50.0, "h": 50.0})
        output = Image.open(io.BytesIO(preview.data)).convert("RGBA")
        self.assertEqual(output.getchannel("A").getextrema(), (0, 255))


class FlashVisionContractTests(unittest.IsolatedAsyncioTestCase):
    def test_context_labels_never_become_design_inventory(self):
        for label in ("context:signature", "$350 price", "BTTCO logo", "artist stamp"):
            role, _context_type = classify_detection_role(label)
            self.assertEqual(role, "context", label)

    def test_design_labels_remain_reviewable_candidates(self):
        for label in ("design:tengu mask", "cyclops", "red crab illustration"):
            role, context_type = classify_detection_role(label)
            self.assertEqual(role, "design", label)
            self.assertIsNone(context_type)

    async def test_missing_gateway_is_explicitly_unavailable(self):
        settings = type(
            "Settings",
            (),
            {"ARK_MODEL_GATEWAY_KEY": "", "ARK_MODEL_GATEWAY_BASE_URL": "http://gateway.test/v1"},
        )()
        with (
            patch("app.services.hf_vision.get_settings", return_value=settings),
            self.assertRaises(SegmentationUnavailableError),
        ):
            await segment_flash_sheet(_image_bytes("JPEG"), image_width=64, image_height=48)

    async def test_gateway_request_includes_exact_image_dimensions(self):
        class _Response:
            status_code = 200
            text = '{"detections": []}'

            @staticmethod
            def json():
                return {"detections": [], "request_id": "vision-test"}

        class _Client:
            def __init__(self):
                self.json = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, *, headers, json):
                self.json = json
                return _Response()

        settings = type(
            "Settings",
            (),
            {
                "ARK_MODEL_GATEWAY_KEY": "test-key",
                "ARK_MODEL_GATEWAY_BASE_URL": "http://gateway.test/v1",
            },
        )()
        client = _Client()
        with (
            patch("app.services.hf_vision.get_settings", return_value=settings),
            patch("app.services.hf_vision.httpx.AsyncClient", return_value=client),
        ):
            detections = await segment_flash_sheet(
                _image_bytes("PNG", size=(400, 200)),
                image_width=400,
                image_height=200,
            )

        self.assertEqual(detections, [])
        self.assertEqual(client.json["image_width"], 400)
        self.assertEqual(client.json["image_height"], 200)


# ── storage key tests ─────────────────────────────────


class StorageKeyTests(unittest.TestCase):
    def test_keys_are_unique_for_same_filename(self):
        a = storage.build_object_key("flash/justin", "jpg")
        b = storage.build_object_key("flash/justin", "jpg")
        self.assertNotEqual(a, b)

    def test_key_uses_folder_prefix_and_extension(self):
        key = storage.build_object_key("flash/justin", "png")
        self.assertTrue(key.startswith("flash/justin/"))
        self.assertTrue(key.endswith(".png"))

    def test_key_strips_leading_dot_from_extension(self):
        key = storage.build_object_key("flash/justin", ".webp")
        self.assertTrue(key.endswith(".webp"))
        self.assertNotIn("..webp", key)

    def test_key_handles_empty_folder(self):
        key = storage.build_object_key("", "jpg")
        # No folder prefix means no leading slash and no path separators.
        self.assertNotIn("/", key)
        self.assertTrue(key.endswith(".jpg"))


# ── Endpoint access-control + flow tests ──────────────


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RowsResult:
    def __init__(self, *, rows=None, scalar_values=None):
        self.rows = list(rows or [])
        self.scalar_values = list(scalar_values or [])

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return _RowsResult(rows=self.scalar_values)


class _FakeSession:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.statements = []
        self.added = []
        self.flushes = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        if self.results:
            return self.results.pop(0)
        return _ScalarResult(None)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()
            # SQLAlchemy applies column defaults on real flush; the fake must too so
            # response serialization mirrors production.
            if hasattr(value, "created_at") and getattr(value, "created_at", None) is None:
                from datetime import UTC, datetime

                value.created_at = datetime.now(UTC)
            if hasattr(value, "design_count") and getattr(value, "design_count", None) is None:
                value.design_count = 0


class _User:
    def __init__(self, role="artist", slug="justin", uid=None):
        self.id = uid or uuid.uuid4()
        self.role = role
        self.slug = slug


class _UploadFile:
    def __init__(self, data: bytes, filename="sheet.jpg", content_type="image/jpeg"):
        self._data = data
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._data


class _ImageResponse:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content
        self.headers = {"content-type": "image/png", "content-length": str(len(content))}


class _ImageClient:
    def __init__(self, content: bytes):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url: str):
        return _ImageResponse(self.content)


class FlashPublicStorefrontTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _design(*, artist_id, sheet_id):
        now = datetime.now(UTC)
        return SimpleNamespace(
            id=uuid.uuid4(),
            sheet_id=sheet_id,
            artist_id=artist_id,
            crop_x=0,
            crop_y=0,
            crop_w=100,
            crop_h=100,
            image_url="https://assets.example/design.webp",
            name="Tiger Head",
            ai_suggested_name=None,
            price=250,
            estimated_minutes=120,
            size_label="palm",
            placement="forearm",
            style="traditional",
            mask_json=None,
            canvas_state=None,
            ai_metadata={},
            gala_sync_enabled=False,
            status="published",
            claimed_by=None,
            claimed_at=None,
            created_at=now,
        )

    async def test_store_designs_include_artist_identity_for_booking_handoff(self):
        from app.api.v1.endpoints import flash

        artist_id = uuid.uuid4()
        sheet_id = uuid.uuid4()
        design = self._design(artist_id=artist_id, sheet_id=sheet_id)
        db = _FakeSession(results=[
            _RowsResult(rows=[(sheet_id, "https://assets.example/sheet.webp")]),
            _RowsResult(rows=[(artist_id, "justin", "Justin Walker")]),
        ])

        responses = await flash._design_responses_with_sources(db, [design])

        self.assertEqual(responses[0].artist_slug, "justin")
        self.assertEqual(responses[0].artist_name, "Justin Walker")
        self.assertEqual(responses[0].source_image_url, "https://assets.example/sheet.webp")

    async def test_sheet_wall_uses_published_designs_not_sheet_status(self):
        from app.api.v1.endpoints import flash

        artist_id = uuid.uuid4()
        sheet_id = uuid.uuid4()
        design = self._design(artist_id=artist_id, sheet_id=sheet_id)
        sheet = SimpleNamespace(
            id=sheet_id,
            artist_id=artist_id,
            image_url="https://assets.example/sheet.webp",
            title="Available flash",
            status="segmented",
            created_at=datetime.now(UTC),
        )
        db = _FakeSession(results=[
            _RowsResult(rows=[(artist_id, "Justin Walker", 100, True)]),
            _RowsResult(scalar_values=[sheet]),
            _RowsResult(scalar_values=[design]),
        ])

        response = await flash.get_public_sheet_wall("justin", db=db)

        sheet_query = str(db.statements[1])
        where_clause = sheet_query.split("WHERE", 1)[1]
        self.assertIn("JOIN bttco_flash_designs", sheet_query)
        self.assertIn("bttco_flash_designs.status", where_clause)
        self.assertNotIn("bttco_flash_sheets.status", where_clause)
        self.assertEqual(response.sheets[0].design_count, 1)
        self.assertEqual(response.sheets[0].designs[0].id, str(design.id))

    async def test_sheet_wall_preserves_artist_deposit_disabled_setting(self):
        from app.api.v1.endpoints import flash

        artist_id = uuid.uuid4()
        db = _FakeSession(results=[
            _RowsResult(rows=[(artist_id, "Justin Walker", 0, False)]),
            _RowsResult(scalar_values=[]),
        ])

        response = await flash.get_public_sheet_wall("justin", db=db)

        self.assertEqual(response.flash_deposit_amount, 0)
        self.assertFalse(response.allow_prepay_full)

    async def test_unknown_artist_slug_never_falls_back_to_global_inventory(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import flash

        db = _FakeSession(results=[_ScalarResult(None)])

        with self.assertRaises(HTTPException) as raised:
            await flash.browse_store_public(artist_slug="missing-artist", db=db)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(len(db.statements), 1)

    async def test_claim_only_endpoints_are_retired_without_inventory_writes(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import flash

        db = _FakeSession()
        user = _User(role="collector")
        design_id = uuid.uuid4()

        with self.assertRaises(HTTPException) as single_claim:
            await flash.claim_design(design_id, db=db, current_user=user)
        with self.assertRaises(HTTPException) as multi_claim:
            await flash.flash_checkout(
                flash.FlashCheckoutRequest(design_ids=[design_id]),
                db=db,
                current_user=user,
            )

        self.assertEqual(single_claim.exception.status_code, 410)
        self.assertEqual(multi_claim.exception.status_code, 410)
        self.assertEqual(db.statements, [])
        self.assertEqual(db.added, [])

    async def test_published_design_image_is_served_through_same_origin_boundary(self):
        from app.api.v1.endpoints import flash

        artist_id = uuid.uuid4()
        sheet_id = uuid.uuid4()
        design = self._design(artist_id=artist_id, sheet_id=sheet_id)
        design.image_url = "https://assets.ryozan.app/flash/test.png"
        image_bytes = _image_bytes("PNG")
        db = _FakeSession(results=[_RowsResult(rows=[(design, design.image_url)])])

        with patch.object(flash.httpx, "AsyncClient", return_value=_ImageClient(image_bytes)):
            response = await flash.get_public_design_image(design.id, db=db)

        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(response.body, image_bytes)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_public_design_image_rejects_non_r2_asset_hosts(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import flash

        artist_id = uuid.uuid4()
        sheet_id = uuid.uuid4()
        design = self._design(artist_id=artist_id, sheet_id=sheet_id)
        design.image_url = "https://untrusted.example/flash.png"
        db = _FakeSession(results=[_RowsResult(rows=[(design, design.image_url)])])

        with self.assertRaises(HTTPException) as raised:
            await flash.get_public_design_image(design.id, db=db)

        self.assertEqual(raised.exception.status_code, 502)


class FlashSegmentationEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_sheet_crop_materializes_active_asset_and_preserves_original(self):
        from app.api.v1.endpoints import flash
        from app.schemas.flash import FlashSheetCropRequest

        user = _User()
        source_url = "https://assets.example/sheet.png"
        sheet = SimpleNamespace(
            id=uuid.uuid4(),
            artist_id=user.id,
            image_url=source_url,
            source_image_url=source_url,
            crop_applied=False,
            crop_x=0,
            crop_y=0,
            crop_w=100,
            crop_h=100,
            asset_version=1,
            title="Sheet",
            status="uploaded",
            design_count=0,
            created_at=datetime.now(UTC),
        )
        db = _FakeSession(results=[_ScalarResult(sheet)])
        source = _image_bytes("PNG", size=(200, 100))
        upload = AsyncMock(return_value="https://assets.example/sheets/cropped.webp")

        with (
            patch("httpx.AsyncClient", return_value=_ImageClient(source)),
            patch.object(flash, "upload_to_drive", upload),
        ):
            result = await flash.save_sheet_crop(
                sheet.id,
                FlashSheetCropRequest(crop_x=25, crop_y=20, crop_w=50, crop_h=60),
                db=db,
                current_user=user,
            )

        cropped = Image.open(io.BytesIO(upload.call_args.kwargs["file_bytes"]))
        self.assertEqual(cropped.size, (100, 60))
        self.assertEqual(upload.call_args.kwargs["mime_type"], "image/webp")
        self.assertEqual(result.image_url, "https://assets.example/sheets/cropped.webp")
        self.assertEqual(result.source_image_url, source_url)
        self.assertTrue(result.crop_applied)
        self.assertEqual(result.crop_x, 25)
        self.assertEqual(result.crop_y, 20)
        self.assertEqual(result.crop_w, 50)
        self.assertEqual(result.crop_h, 60)
        self.assertEqual(result.asset_version, 2)

    async def test_full_sheet_restores_original_without_new_upload(self):
        from app.api.v1.endpoints import flash
        from app.schemas.flash import FlashSheetCropRequest

        user = _User()
        source_url = "https://assets.example/original.png"
        sheet = SimpleNamespace(
            id=uuid.uuid4(),
            artist_id=user.id,
            image_url="https://assets.example/cropped.webp",
            source_image_url=source_url,
            crop_applied=True,
            crop_x=10,
            crop_y=15,
            crop_w=80,
            crop_h=70,
            asset_version=2,
            title="Sheet",
            status="uploaded",
            design_count=0,
            created_at=datetime.now(UTC),
        )
        db = _FakeSession(results=[_ScalarResult(sheet)])

        with patch.object(flash, "upload_to_drive", AsyncMock()) as upload:
            result = await flash.save_sheet_crop(
                sheet.id,
                FlashSheetCropRequest(crop_x=0, crop_y=0, crop_w=100, crop_h=100),
                db=db,
                current_user=user,
            )

        upload.assert_not_awaited()
        self.assertEqual(result.image_url, source_url)
        self.assertFalse(result.crop_applied)
        self.assertEqual((result.crop_x, result.crop_y, result.crop_w, result.crop_h), (0, 0, 100, 100))
        self.assertEqual(result.asset_version, 3)

    def test_legacy_pixel_row_is_not_relabelled_as_v2_percent_geometry(self):
        from app.api.v1.endpoints.flash import _design_to_response

        now = datetime.now(UTC)
        design = SimpleNamespace(
            id=uuid.uuid4(),
            sheet_id=uuid.uuid4(),
            artist_id=uuid.uuid4(),
            crop_x=840,
            crop_y=610,
            crop_w=1200,
            crop_h=900,
            image_url="https://assets.example/legacy-sheet.jpg",
            ai_metadata=None,
            name="Legacy",
            ai_suggested_name=None,
            price=None,
            estimated_minutes=None,
            size_label=None,
            placement=None,
            style=None,
            mask_json=None,
            canvas_state=None,
            gala_sync_enabled=False,
            status="published",
            claimed_by=None,
            claimed_at=None,
            created_at=now,
        )

        response = _design_to_response(design, design.image_url)

        self.assertEqual(response.crop_units, "pixel")
        self.assertEqual(response.asset_version, 1)
        self.assertIsNone(response.preview_url)
        self.assertEqual(response.source_image_url, design.image_url)

    async def test_segment_materializes_design_preview_and_excludes_context(self):
        from app.api.v1.endpoints import flash
        from app.models.flash import FlashDesign

        user = _User()
        sheet = type(
            "Sheet",
            (),
            {
                "id": uuid.uuid4(),
                "artist_id": user.id,
                "image_url": "https://assets.example/sheet.png",
                "status": "uploaded",
                "design_count": 0,
            },
        )()
        db = _FakeSession(results=[_ScalarResult(sheet), _ScalarResult(None)])
        source = _image_bytes("PNG", size=(200, 100))
        cutout = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
        cutout.paste((180, 90, 40, 255), (10, 5, 90, 45))
        cutout_buffer = io.BytesIO()
        cutout.save(cutout_buffer, format="PNG")
        detections = [
            {
                "label": "design:tengu mask",
                "score": 0.97,
                "box": {"xmin": 20, "ymin": 10, "xmax": 120, "ymax": 60},
                "role": "design",
                "context_type": None,
                "cutout_png_bytes": cutout_buffer.getvalue(),
            },
            {
                "label": "context:signature",
                "score": 0.93,
                "box": {"xmin": 150, "ymin": 5, "xmax": 195, "ymax": 25},
                "role": "context",
                "context_type": "signature",
            },
        ]
        upload = AsyncMock(return_value="https://assets.example/previews/tengu.webp")

        with (
            patch("httpx.AsyncClient", return_value=_ImageClient(source)),
            patch.object(flash, "segment_flash_sheet", AsyncMock(return_value=detections)),
            patch.object(flash, "upload_to_drive", upload),
        ):
            result = await flash.segment_sheet(sheet.id, db=db, current_user=user)

        created = [item for item in db.added if isinstance(item, FlashDesign)]
        self.assertEqual(len(created), 1)
        self.assertEqual(float(created[0].crop_x), 10.0)
        self.assertEqual(float(created[0].crop_y), 10.0)
        self.assertEqual(float(created[0].crop_w), 50.0)
        self.assertEqual(float(created[0].crop_h), 50.0)
        self.assertEqual(created[0].image_url, "https://assets.example/previews/tengu.webp")
        self.assertTrue(created[0].mask_json["transparent_cutout"])
        self.assertEqual(created[0].mask_json["source"], "sam2_alpha_cutout")
        self.assertEqual(created[0].ai_metadata["asset_version"], 3)
        self.assertEqual(upload.call_args.kwargs["mime_type"], "image/png")
        self.assertEqual(created[0].ai_metadata["context_regions"][0]["context_type"], "signature")
        self.assertEqual(result.design_count, 1)
        self.assertEqual(len(result.context_regions), 1)
        self.assertEqual(sheet.status, "segmented")
        self.assertEqual(sheet.design_count, 1)

    async def test_sheet_crop_limits_model_input_and_maps_results_to_original(self):
        from app.api.v1.endpoints import flash
        from app.models.flash import FlashDesign
        from app.schemas.flash import FlashSegmentationRequest

        user = _User()
        sheet = SimpleNamespace(
            id=uuid.uuid4(),
            artist_id=user.id,
            image_url="https://assets.example/sheet.png",
            status="uploaded",
            design_count=0,
        )
        db = _FakeSession(results=[_ScalarResult(sheet)])
        source = _image_bytes("PNG", size=(200, 100))

        async def segment_cropped_input(data: bytes, *, image_width: int, image_height: int):
            cropped = Image.open(io.BytesIO(data))
            self.assertEqual(cropped.size, (100, 60))
            self.assertEqual((image_width, image_height), (100, 60))
            return [
                {
                    "label": "design:rose",
                    "score": 0.95,
                    "box": {"xmin": 10, "ymin": 10, "xmax": 50, "ymax": 40},
                    "role": "design",
                    "context_type": None,
                },
                {
                    "label": "context:signature",
                    "score": 0.9,
                    "box": {"xmin": 70, "ymin": 5, "xmax": 90, "ymax": 15},
                    "role": "context",
                    "context_type": "signature",
                },
            ]

        with (
            patch("httpx.AsyncClient", return_value=_ImageClient(source)),
            patch.object(flash, "segment_flash_sheet", side_effect=segment_cropped_input),
            patch.object(
                flash,
                "upload_to_drive",
                AsyncMock(return_value="https://assets.example/previews/rose.webp"),
            ),
        ):
            result = await flash.segment_sheet(
                sheet.id,
                body=FlashSegmentationRequest(crop_x=25, crop_y=20, crop_w=50, crop_h=60),
                db=db,
                current_user=user,
            )

        created = [item for item in db.added if isinstance(item, FlashDesign)]
        self.assertEqual(len(created), 1)
        self.assertEqual(float(created[0].crop_x), 30.0)
        self.assertEqual(float(created[0].crop_y), 30.0)
        self.assertEqual(float(created[0].crop_w), 20.0)
        self.assertEqual(float(created[0].crop_h), 30.0)
        self.assertEqual(
            created[0].ai_metadata["segmentation_crop"],
            {
                "xmin": 50,
                "ymin": 20,
                "xmax": 150,
                "ymax": 80,
            },
        )
        self.assertEqual(result.designs[0].box, {"xmin": 60.0, "ymin": 30.0, "xmax": 100.0, "ymax": 60.0})
        self.assertEqual(result.context_regions[0].box, {"xmin": 120.0, "ymin": 25.0, "xmax": 140.0, "ymax": 35.0})

    async def test_delete_design_removes_unclaimed_entry_and_updates_sheet_count(self):
        from app.api.v1.endpoints import flash
        from app.models.flash import ModelFeedbackSample

        user = _User()
        sheet = SimpleNamespace(
            id=uuid.uuid4(),
            design_count=2,
            status="published",
            image_url="https://assets.example/sheet.jpg",
        )
        design = SimpleNamespace(
            id=uuid.uuid4(),
            sheet_id=sheet.id,
            artist_id=user.id,
            status="published",
            claimed_by=None,
            booking_id=None,
            image_url="https://assets.example/cutout.png",
            crop_x=10,
            crop_y=10,
            crop_w=30,
            crop_h=40,
            mask_json={"source": "sam2_alpha_cutout", "box": {"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4}},
            ai_metadata={"model_id": "sam2-bttco", "model_version": "candidate-1", "asset_version": 3},
        )

        class _DeleteSession(_FakeSession):
            def __init__(self):
                super().__init__(results=[_ScalarResult(design)])
                self.deleted = []

            async def get(self, _model, _id):
                return sheet

            async def delete(self, value):
                self.deleted.append(value)

            async def scalar(self, _stmt):
                return 1

        db = _DeleteSession()
        response = await flash.delete_design(design.id, db=db, current_user=user)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(db.deleted, [design])
        self.assertEqual(sheet.design_count, 1)
        feedback = [item for item in db.added if isinstance(item, ModelFeedbackSample)]
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0].outcome, "rejected")
        self.assertEqual(feedback[0].training_status, "excluded")

    async def test_publishing_sam2_cutout_records_accepted_training_evidence(self):
        from app.api.v1.endpoints import flash
        from app.models.flash import ModelFeedbackSample
        from app.schemas.flash import FlashDesignUpdate

        now = datetime.now(UTC)
        user = _User()
        sheet = SimpleNamespace(id=uuid.uuid4(), status="segmented", image_url="https://assets.example/sheet.jpg")
        design = SimpleNamespace(
            id=uuid.uuid4(),
            sheet_id=sheet.id,
            artist_id=user.id,
            crop_x=10,
            crop_y=10,
            crop_w=30,
            crop_h=40,
            image_url="https://assets.example/cutout.png",
            name="Kappa",
            ai_suggested_name="Design 1",
            price=300,
            estimated_minutes=120,
            size_label=None,
            placement=None,
            style="japanese",
            mask_json={"source": "sam2_alpha_cutout", "box": {"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4}},
            canvas_state=None,
            ai_metadata={"model_id": "sam2-bttco", "model_version": "candidate-1", "asset_version": 3},
            gala_sync_enabled=False,
            status="draft",
            claimed_by=None,
            claimed_at=None,
            booking_id=None,
            created_at=now,
            updated_at=now,
        )

        class _UpdateSession(_FakeSession):
            async def get(self, _model, _id):
                return sheet

        db = _UpdateSession(results=[_ScalarResult(design)])
        response = await flash.update_design(
            design.id,
            FlashDesignUpdate(status="published"),
            db=db,
            current_user=user,
        )

        feedback = [item for item in db.added if isinstance(item, ModelFeedbackSample)]
        self.assertEqual(response.status, "published")
        self.assertEqual(sheet.status, "published")
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0].outcome, "accepted")
        self.assertTrue(feedback[0].accepted)

    async def test_training_manifest_uses_latest_review_outcome(self):
        from datetime import timedelta

        from app.api.v1.endpoints import flash
        from app.models.flash import ModelFeedbackSample

        now = datetime.now(UTC)
        deleted_subject = uuid.uuid4()
        usable_subject = uuid.uuid4()

        def sample(subject_id, outcome, created_at, artifact_ref):
            return ModelFeedbackSample(
                id=uuid.uuid4(),
                model_id="sam2-bttco",
                model_version="candidate-1",
                input_type="image",
                input_ref="https://assets.example/sheet.jpg",
                artifact_ref=artifact_ref,
                subject_id=subject_id,
                prediction={"mask": {"box": {"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4}}},
                correction=None,
                accepted=outcome != "rejected",
                outcome=outcome,
                source="test",
                module="bttco",
                training_status="pending",
                created_at=created_at,
            )

        rows = [
            sample(deleted_subject, "rejected", now, "https://assets.example/deleted.png"),
            sample(usable_subject, "accepted", now - timedelta(seconds=1), "https://assets.example/good.png"),
            sample(deleted_subject, "accepted", now - timedelta(seconds=2), "https://assets.example/old.png"),
        ]

        class _Rows:
            def scalars(self):
                return self

            def all(self):
                return rows

        class _ManifestSession(_FakeSession):
            async def execute(self, _stmt):
                return _Rows()

        manifest = await flash.get_training_manifest(
            db=_ManifestSession(),
            current_user=_User(role="admin"),
        )

        self.assertEqual(manifest["sample_count"], 1)
        self.assertEqual(manifest["samples"][0]["subject_id"], str(usable_subject))

    async def test_push_training_manifest_snapshots_dataset_without_starting_training(self):
        from app.api.v1.endpoints import flash

        manifest = {
            "schema_version": "model-feedback/v1",
            "model_id": "sam2-bttco",
            "sample_count": 24,
            "samples": [],
        }
        response = SimpleNamespace(
            status_code=201,
            json=lambda: {
                "ok": True,
                "data": {
                    "id": "apex.dataset.bttco.abc",
                    "sampleCount": 24,
                    "artifactUri": "gs://bucket/datasets/abc.json",
                },
            },
        )
        client = AsyncMock()
        client.post.return_value = response

        class _ClientContext:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(flash, "_build_training_manifest", AsyncMock(return_value=manifest)),
            patch.object(
                flash,
                "get_settings",
                return_value=SimpleNamespace(
                    ARK_MODEL_GATEWAY_KEY="scoped-gateway-key",
                    ARK_MODEL_GATEWAY_BASE_URL="https://ark.example/v1",
                ),
            ),
            patch.object(flash.httpx, "AsyncClient", return_value=_ClientContext()),
        ):
            receipt = await flash.push_training_manifest_to_apex(
                db=_FakeSession(),
                current_user=_User(role="founder"),
            )

        self.assertEqual(receipt["status"], "snapshot_created")
        self.assertEqual(receipt["sample_count"], 24)
        self.assertFalse(receipt["training_started"])
        self.assertEqual(client.post.await_count, 1)
        url = client.post.await_args.args[0]
        self.assertEqual(url, "https://ark.example/api/apex/datasets/bttco")
        self.assertNotIn("gauntlet", url)
        self.assertEqual(
            client.post.await_args.kwargs["headers"]["Authorization"],
            "Bearer scoped-gateway-key",
        )

    async def test_delete_design_blocks_claimed_inventory(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import flash

        user = _User()
        design = SimpleNamespace(
            id=uuid.uuid4(),
            sheet_id=uuid.uuid4(),
            artist_id=user.id,
            status="claimed",
            claimed_by=uuid.uuid4(),
            booking_id=uuid.uuid4(),
        )
        db = _FakeSession(results=[_ScalarResult(design)])

        with self.assertRaises(HTTPException) as ctx:
            await flash.delete_design(design.id, db=db, current_user=user)
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_manual_crop_materializes_preview_without_context(self):
        from app.api.v1.endpoints import flash
        from app.models.flash import FlashDesign
        from app.schemas.flash import FlashDesignCreate

        user = _User()
        sheet = type(
            "Sheet",
            (),
            {
                "id": uuid.uuid4(),
                "artist_id": user.id,
                "image_url": "https://assets.example/sheet.png",
                "status": "uploaded",
                "design_count": 0,
            },
        )()
        db = _FakeSession(results=[_ScalarResult(sheet)])
        source = _image_bytes("PNG", size=(200, 100))

        with (
            patch("httpx.AsyncClient", return_value=_ImageClient(source)),
            patch.object(
                flash,
                "upload_to_drive",
                AsyncMock(return_value="https://assets.example/previews/manual.webp"),
            ),
        ):
            result = await flash.create_design_crop(
                sheet.id,
                FlashDesignCreate(crop_x=10, crop_y=20, crop_w=50, crop_h=40),
                db=db,
                current_user=user,
            )

        created = [item for item in db.added if isinstance(item, FlashDesign)]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].image_url, "https://assets.example/previews/manual.webp")
        self.assertEqual(created[0].mask_json["source"], "artist_crop")
        self.assertEqual(created[0].ai_metadata["context_regions"], [])
        self.assertAlmostEqual(float(created[0].crop_x), 10.0)
        self.assertAlmostEqual(float(created[0].crop_y), 20.0)
        self.assertAlmostEqual(float(created[0].crop_w), 50.0)
        self.assertAlmostEqual(float(created[0].crop_h), 40.0)
        self.assertEqual(result.preview_url, "https://assets.example/previews/manual.webp")
        self.assertEqual(result.source_image_url, sheet.image_url)
        self.assertEqual(sheet.status, "segmented")
        self.assertEqual(sheet.design_count, 1)


class FlashUploadEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def _call_upload(self, *, user, upload, storage_url="https://assets.example/flash/justin/x.jpg"):
        from app.api.v1.endpoints import flash

        db = _FakeSession()
        with patch.object(flash, "upload_to_drive", return_value=storage_url) as mock_up:
            result = await flash.upload_flash_sheet(
                file=upload,
                title=None,
                artist_id=None,
                db=db,
                current_user=user,
            )
        return result, db, mock_up

    async def test_valid_jpeg_upload_creates_sheet(self):
        user = _User()
        upload = _UploadFile(_image_bytes("JPEG"), "my sheet.jpg")
        result, db, mock_up = await self._call_upload(user=user, upload=upload)
        self.assertEqual(len(db.added), 1)
        # Storage received normalized JPEG bytes and a collision-resistant key.
        _, kwargs = mock_up.call_args
        self.assertEqual(kwargs["mime_type"], "image/jpeg")
        self.assertTrue(kwargs["folder_path"].startswith("flash/justin"))
        self.assertNotEqual(kwargs["filename"], "my sheet.jpg")

    async def test_unsupported_format_returns_422_code(self):
        from fastapi import HTTPException

        user = _User()
        upload = _UploadFile(_image_bytes("GIF"), "anim.gif")
        with self.assertRaises(HTTPException) as ctx:
            await self._call_upload(user=user, upload=upload)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["code"], "FLASH_UNSUPPORTED_FORMAT")

    async def test_oversize_returns_413_code(self):
        from fastapi import HTTPException

        user = _User()
        upload = _UploadFile(b"x" * (image_processing.MAX_UPLOAD_BYTES + 1), "big.jpg")
        with self.assertRaises(HTTPException) as ctx:
            await self._call_upload(user=user, upload=upload)
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertEqual(ctx.exception.detail["code"], "FLASH_FILE_TOO_LARGE")

    async def test_storage_unavailable_returns_503_code(self):
        from fastapi import HTTPException

        user = _User()
        upload = _UploadFile(_image_bytes("PNG"), "sheet.png")
        with self.assertRaises(HTTPException) as ctx:
            await self._call_upload(user=user, upload=upload, storage_url=None)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail["code"], "FLASH_STORAGE_UNAVAILABLE")

    async def test_original_filename_kept_as_title_not_key(self):
        user = _User()
        upload = _UploadFile(_image_bytes("JPEG"), "Dragon Sleeve.jpg")
        result, db, mock_up = await self._call_upload(user=user, upload=upload)
        sheet = db.added[0]
        self.assertEqual(sheet.title, "Dragon Sleeve")


class FlashUploadAccessControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_artist_cannot_upload_for_another_artist(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import flash

        artist = _User(role="artist", uid=uuid.uuid4())
        other_artist_id = uuid.uuid4()
        db = _FakeSession()
        upload = _UploadFile(_image_bytes("JPEG"))
        with self.assertRaises(HTTPException) as ctx:
            await flash.upload_flash_sheet(
                file=upload,
                title=None,
                artist_id=other_artist_id,
                db=db,
                current_user=artist,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_admin_can_upload_for_selected_artist(self):
        from app.api.v1.endpoints import flash

        admin = _User(role="admin", uid=uuid.uuid4())
        target = _User(role="artist", slug="target", uid=uuid.uuid4())
        # _resolve_flash_owner queries for the target artist; return it.
        db = _FakeSession(results=[_ScalarResult(target)])
        upload = _UploadFile(_image_bytes("JPEG"))
        with patch.object(flash, "upload_to_drive", return_value="https://assets.example/x.jpg"):
            await flash.upload_flash_sheet(
                file=upload,
                title=None,
                artist_id=target.id,
                db=db,
                current_user=admin,
            )
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].artist_id, target.id)


if __name__ == "__main__":
    unittest.main()
