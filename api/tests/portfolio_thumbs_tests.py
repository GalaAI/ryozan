"""Portfolio thumbnail contract.

Every portfolio original gets a 640px WebP companion (app.services.portfolio_thumbs) so
gallery tiles stop loading 4096px originals. The mapping original_url -> thumb_url lives
on the artist as portfolio_thumbs; the public ArtistDetail exposes it; removal drops the
entry and queues the thumb object for deletion alongside the original.
"""

import io
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from PIL import Image

from app.api.v1.endpoints import artists
from app.models.user import BTTCOUser
from app.schemas.artist import ArtistDetail
from app.services import portfolio_thumbs


def _jpeg(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), (120, 80, 60))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _black_padded_png(width: int, height: int, *, top: int, bottom: int) -> bytes:
    image = Image.new("RGB", (width, height), (0, 0, 0))
    for y in range(top, height - bottom):
        for x in range(width):
            image.putpixel((x, y), (120, 80, 60))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _artist(images: list[str], thumbs: dict | None = None, focus: dict | None = None) -> BTTCOUser:
    return BTTCOUser(
        id=uuid.uuid4(),
        email="artist@example.test",
        password_hash="disabled",
        name="Artist",
        role="artist",
        slug="artist",
        portfolio_images=list(images),
        portfolio_thumbs=thumbs,
        portfolio_focus=focus,
        availability_source="none",
        booking_mode="mailto",
        booking_slot_interval_minutes=30,
        booking_buffer_minutes=30,
        booking_min_notice_hours=24,
        booking_max_advance_days=180,
        booking_allow_same_day=False,
        allow_prepay_full=False,
    )


class BuildThumbTests(unittest.TestCase):
    def test_large_original_is_capped_at_the_thumb_edge_as_webp(self):
        thumb = portfolio_thumbs.build_portfolio_thumb(_jpeg(3000, 4000))
        self.assertEqual(thumb.mime_type, "image/webp")
        self.assertEqual(thumb.extension, "webp")
        self.assertEqual(max(thumb.width, thumb.height), portfolio_thumbs.PORTFOLIO_THUMB_EDGE_PX)
        self.assertEqual((thumb.width, thumb.height), (480, 640))
        with Image.open(io.BytesIO(thumb.data)) as decoded:
            self.assertEqual(decoded.format, "WEBP")
            self.assertEqual(decoded.size, (480, 640))

    def test_focus_derivative_is_capped_at_the_focus_edge(self):
        focus = portfolio_thumbs.build_portfolio_thumb(_jpeg(3000, 4000), max_edge_px=portfolio_thumbs.PORTFOLIO_FOCUS_EDGE_PX)
        self.assertEqual((focus.width, focus.height), (1050, 1400))
        self.assertEqual(focus.mime_type, "image/webp")

    def test_small_original_is_not_upscaled(self):
        thumb = portfolio_thumbs.build_portfolio_thumb(_jpeg(300, 200))
        self.assertEqual((thumb.width, thumb.height), (300, 200))

    def test_solid_black_edge_bands_are_trimmed_before_resize(self):
        thumb = portfolio_thumbs.build_portfolio_thumb(_black_padded_png(300, 600, top=100, bottom=100))
        self.assertEqual((thumb.width, thumb.height), (300, 400))

    def test_dark_photo_that_reaches_the_edges_is_not_trimmed(self):
        image = Image.new("RGB", (300, 400), (3, 3, 3))
        image.putpixel((0, 0), (60, 40, 30))
        image.putpixel((299, 399), (60, 40, 30))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        thumb = portfolio_thumbs.build_portfolio_thumb(buffer.getvalue())
        self.assertEqual((thumb.width, thumb.height), (300, 400))

    def test_undecodable_bytes_raise_the_thumb_error(self):
        with self.assertRaises(portfolio_thumbs.PortfolioThumbError):
            portfolio_thumbs.build_portfolio_thumb(b"not an image")

    def test_thumb_filename_keeps_the_original_stem(self):
        self.assertEqual(portfolio_thumbs.thumb_filename_for("https://assets.example.test/portfolios/justin/IMG_0212.JPG"), "IMG_0212.webp")
        self.assertEqual(portfolio_thumbs.thumb_filename_for("a1b2c3.png"), "a1b2c3.webp")

    def test_thumbs_for_is_parallel_to_images_with_none_for_missing(self):
        images = ["https://x/a.jpg", "https://x/b.jpg"]
        self.assertEqual(portfolio_thumbs.thumbs_for(images, {"https://x/b.jpg": "https://x/thumbs/b.webp"}), [None, "https://x/thumbs/b.webp"])
        self.assertEqual(portfolio_thumbs.thumbs_for(None, {}), [])


class UploadThumbTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_stores_the_thumb_next_to_the_original(self):
        with patch.object(portfolio_thumbs, "upload_file", new=AsyncMock(return_value="https://assets.example.test/portfolios/artist/thumbs/new.webp")) as store:
            url = await portfolio_thumbs.upload_portfolio_thumb(_jpeg(1200, 900), slug="artist", original_name="new.jpg")
        self.assertEqual(url, "https://assets.example.test/portfolios/artist/thumbs/new.webp")
        kwargs = store.await_args.kwargs
        self.assertEqual(kwargs["folder_path"], "portfolios/artist/thumbs")
        self.assertEqual(kwargs["filename"], "new.webp")
        self.assertEqual(kwargs["mime_type"], "image/webp")

    async def test_focus_upload_stores_under_the_focus_folder(self):
        with patch.object(portfolio_thumbs, "upload_file", new=AsyncMock(return_value="https://assets.example.test/portfolios/artist/focus/new.webp")) as store:
            url = await portfolio_thumbs.upload_portfolio_focus(_jpeg(3000, 2000), slug="artist", original_name="new.jpg")
        self.assertEqual(url, "https://assets.example.test/portfolios/artist/focus/new.webp")
        self.assertEqual(store.await_args.kwargs["folder_path"], "portfolios/artist/focus")
        with Image.open(io.BytesIO(store.await_args.kwargs["file_bytes"])) as decoded:
            self.assertEqual(max(decoded.size), portfolio_thumbs.PORTFOLIO_FOCUS_EDGE_PX)

    async def test_undecodable_original_returns_none_without_uploading(self):
        with patch.object(portfolio_thumbs, "upload_file", new=AsyncMock()) as store:
            url = await portfolio_thumbs.upload_portfolio_thumb(b"garbage", slug="artist", original_name="x.jpg")
        self.assertIsNone(url)
        store.assert_not_awaited()

    async def test_storage_failure_returns_none(self):
        with patch.object(portfolio_thumbs, "upload_file", new=AsyncMock(return_value=None)):
            url = await portfolio_thumbs.upload_portfolio_thumb(_jpeg(800, 800), slug="artist", original_name="x.jpg")
        self.assertIsNone(url)


class GalleryEndpointThumbTests(unittest.IsolatedAsyncioTestCase):
    async def test_gallery_upload_records_the_thumb_mapping_and_detail_exposes_it(self):
        existing = "https://assets.example.test/portfolios/artist/existing.webp"
        uploaded = "https://assets.example.test/portfolios/artist/uploaded.jpg"
        thumb = "https://assets.example.test/portfolios/artist/thumbs/uploaded.webp"
        current_user = _artist([existing], {existing: "https://assets.example.test/portfolios/artist/thumbs/existing.webp"})
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        upload = SimpleNamespace(filename="uploaded.jpg", content_type="image/jpeg", read=AsyncMock(side_effect=[b"bytes", b""]))
        normalized = SimpleNamespace(data=_jpeg(2000, 1500), extension="jpg", mime_type="image/jpeg")

        with (
            patch.object(artists, "_read_artist_image", new=AsyncMock(return_value=normalized)),
            patch.object(artists, "upload_to_drive", new=AsyncMock(return_value=uploaded)),
            patch.object(artists, "upload_portfolio_thumb", new=AsyncMock(return_value=thumb)) as make_thumb,
            patch.object(artists, "upload_portfolio_focus", new=AsyncMock(return_value=thumb.replace("/thumbs/", "/focus/"))) as make_focus,
        ):
            result = await artists.upload_gallery_image(file=upload, db=db, current_user=current_user)

        make_thumb.assert_awaited_once()
        make_focus.assert_awaited_once()
        self.assertEqual(make_thumb.await_args.kwargs["slug"], "artist")
        self.assertEqual(result.portfolio_images, [existing, uploaded])
        self.assertEqual(result.portfolio_thumbs[uploaded], thumb)
        self.assertEqual(result.portfolio_focus[uploaded], thumb.replace("/thumbs/", "/focus/"))
        self.assertEqual(result.portfolio_thumbs[existing], "https://assets.example.test/portfolios/artist/thumbs/existing.webp")
        detail = ArtistDetail.model_validate(result)
        self.assertEqual(detail.portfolio_thumbs[uploaded], thumb)
        self.assertEqual(detail.portfolio_focus[uploaded], thumb.replace("/thumbs/", "/focus/"))

    async def test_gallery_upload_survives_a_thumb_failure(self):
        uploaded = "https://assets.example.test/portfolios/artist/uploaded.jpg"
        current_user = _artist([])
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        upload = SimpleNamespace(filename="uploaded.jpg", content_type="image/jpeg", read=AsyncMock(side_effect=[b"bytes", b""]))
        normalized = SimpleNamespace(data=_jpeg(640, 640), extension="jpg", mime_type="image/jpeg")

        with (
            patch.object(artists, "_read_artist_image", new=AsyncMock(return_value=normalized)),
            patch.object(artists, "upload_to_drive", new=AsyncMock(return_value=uploaded)),
            patch.object(artists, "upload_portfolio_thumb", new=AsyncMock(return_value=None)),
            patch.object(artists, "upload_portfolio_focus", new=AsyncMock(return_value=None)),
        ):
            result = await artists.upload_gallery_image(file=upload, db=db, current_user=current_user)

        self.assertEqual(result.portfolio_images, [uploaded])
        self.assertIsNone(result.portfolio_thumbs)
        self.assertIsNone(result.portfolio_focus)
        db.commit.assert_awaited_once()

    async def test_gallery_removal_drops_the_thumb_and_queues_its_deletion(self):
        original = "https://assets.example.test/portfolios/artist/one.jpg"
        thumb = "https://assets.example.test/portfolios/artist/thumbs/one.webp"
        keep = "https://assets.example.test/portfolios/artist/two.jpg"
        keep_thumb = "https://assets.example.test/portfolios/artist/thumbs/two.webp"
        focus = "https://assets.example.test/portfolios/artist/focus/one.webp"
        current_user = _artist([original, keep], {original: thumb, keep: keep_thumb}, {original: focus})
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        outbox = SimpleNamespace(id=uuid.uuid4())

        with (
            patch.object(artists, "queue_gallery_deletion", new=AsyncMock(return_value=outbox)) as queue,
            patch.object(artists, "process_gallery_deletion_outbox_once", new=AsyncMock(return_value=1)),
        ):
            result = await artists.remove_gallery_image(body=artists.GalleryRemoveRequest(url=original), db=db, current_user=current_user)

        queued_urls = sorted(call.kwargs["url"] for call in queue.await_args_list)
        self.assertEqual(queued_urls, sorted([original, thumb, focus]))
        for call in queue.await_args_list:
            self.assertEqual(call.kwargs["allowed_prefix"], "portfolios/artist/")
        self.assertEqual(result.portfolio_images, [keep])
        self.assertEqual(result.portfolio_thumbs, {keep: keep_thumb})
        self.assertIsNone(result.portfolio_focus)

    def test_detail_drops_malformed_thumb_entries(self):
        detail = ArtistDetail.model_validate(
            {
                "id": uuid.uuid4(),
                "slug": "artist",
                "name": "Artist",
                "portfolio_images": ["https://x/a.jpg"],
                "portfolio_thumbs": {"https://x/a.jpg": "https://x/thumbs/a.webp", "https://x/b.jpg": None, 3: "x"},
            }
        )
        self.assertEqual(detail.portfolio_thumbs, {"https://x/a.jpg": "https://x/thumbs/a.webp"})


class BackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_backfill_makes_only_what_is_missing_and_records_per_image(self):
        a, b, c = ("https://x/portfolios/artist/a.jpg", "https://x/portfolios/artist/b.jpg", "https://x/portfolios/artist/c.jpg")
        artist = _artist([a, b, c], {a: "https://x/portfolios/artist/thumbs/a.webp", b: "https://x/portfolios/artist/thumbs/b.webp"}, {a: "https://x/portfolios/artist/focus/a.webp"})
        rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [artist]))
        db = SimpleNamespace(execute=AsyncMock(return_value=rows), commit=AsyncMock())
        fetched: list[str] = []

        async def fetch(url: str) -> bytes | None:
            fetched.append(url)
            return _jpeg(1600, 1200) if url != c else None

        async def store(*, file_bytes, filename, mime_type, folder_path):
            return f"https://x/{folder_path}/{filename}"

        with patch.object(portfolio_thumbs, "upload_file", new=store):
            report = await portfolio_thumbs.backfill_missing_portfolio_derivatives(db, fetch_original=fetch, log=lambda line: None)

        self.assertEqual(fetched, [b, c])  # a is complete; b needs focus only; c needs both
        self.assertEqual((report.made_thumbs, report.made_focus, report.skipped, report.failed), (0, 1, 1, 1))
        self.assertEqual(artist.portfolio_focus[b], "https://x/portfolios/artist/focus/b.webp")
        self.assertEqual(artist.portfolio_thumbs[b], "https://x/portfolios/artist/thumbs/b.webp")
        self.assertNotIn(c, artist.portfolio_focus)
        db.commit.assert_awaited()

    async def test_dry_run_fetches_and_writes_nothing(self):
        a = "https://x/portfolios/artist/a.jpg"
        artist = _artist([a])
        rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [artist]))
        db = SimpleNamespace(execute=AsyncMock(return_value=rows), commit=AsyncMock())
        fetch = AsyncMock()
        with patch.object(portfolio_thumbs, "upload_file", new=AsyncMock()) as store:
            report = await portfolio_thumbs.backfill_missing_portfolio_derivatives(db, apply=False, fetch_original=fetch, log=lambda line: None)
        fetch.assert_not_awaited()
        store.assert_not_awaited()
        db.commit.assert_not_awaited()
        self.assertEqual(report.made, 0)
        self.assertIsNone(artist.portfolio_thumbs)

    async def test_rebuild_regenerates_existing_derivatives_in_place(self):
        original = "https://x/portfolios/artist/a.png"
        thumb = "https://x/portfolios/artist/thumbs/a.webp"
        focus = "https://x/portfolios/artist/focus/a.webp"
        artist = _artist([original], {original: thumb}, {original: focus})
        rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [artist]))
        db = SimpleNamespace(execute=AsyncMock(return_value=rows), commit=AsyncMock())
        fetch = AsyncMock(return_value=_black_padded_png(300, 600, top=100, bottom=100))

        async def store(*, file_bytes, filename, mime_type, folder_path):
            return f"https://x/{folder_path}/{filename}"

        with patch.object(portfolio_thumbs, "upload_file", new=store):
            report = await portfolio_thumbs.backfill_missing_portfolio_derivatives(
                db, rebuild=True, fetch_original=fetch, log=lambda line: None
            )

        fetch.assert_awaited_once_with(original)
        self.assertEqual((report.made_thumbs, report.made_focus, report.skipped, report.failed), (1, 1, 0, 0))
        self.assertEqual(artist.portfolio_thumbs[original], thumb)
        self.assertEqual(artist.portfolio_focus[original], focus)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
