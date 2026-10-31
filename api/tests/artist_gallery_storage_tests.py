"""Gallery deletion and R2 object-removal regression tests.

This is a distinct storage-integrity contract from flash ingestion: the public
profile transaction owns visibility, while durable cleanup owns R2 deletion.
"""

import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import artists
from app.models.user import User
from app.services import storage


def _settings():
    return SimpleNamespace(
        R2_ACCOUNT_ID="account-id",
        R2_ACCESS_KEY_ID="access-key",
        R2_SECRET_ACCESS_KEY="secret-key",
        R2_BUCKET_NAME="bttco-assets",
        R2_PUBLIC_DOMAIN="assets.example.test",
    )


class R2DeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_domain_delete_signs_configured_r2_object(self):
        response = SimpleNamespace(status_code=204, text="")
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.delete.return_value = response

        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(storage.httpx, "AsyncClient", return_value=client),
        ):
            deleted = await storage.delete_file(
                "https://assets.example.test/portfolios/artist/image%20one.webp?version=2"
            )

        self.assertTrue(deleted)
        client.delete.assert_awaited_once()
        request_url = client.delete.await_args.args[0]
        request_headers = client.delete.await_args.kwargs["headers"]
        self.assertEqual(
            request_url,
            "https://account-id.r2.cloudflarestorage.com/bttco-assets/portfolios/artist/image%20one.webp",
        )
        self.assertTrue(request_headers["Authorization"].startswith("AWS4-HMAC-SHA256 "))
        self.assertIn("x-amz-date", request_headers)

    async def test_r2_delete_returns_false_when_provider_rejects_request(self):
        response = SimpleNamespace(status_code=503, text="provider details")
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.delete.return_value = response

        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(storage.httpx, "AsyncClient", return_value=client),
        ):
            deleted = await storage.delete_file(
                "https://pub-account-id.r2.dev/bttco-assets/portfolios/artist/image.webp"
            )

        self.assertFalse(deleted)

    async def test_foreign_artist_prefix_is_rejected_before_storage_delete(self):
        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(storage, "_delete_r2", new=AsyncMock(return_value=True)) as delete,
        ):
            deleted = await storage.delete_file(
                "https://assets.example.test/portfolios/another-artist/image.webp",
                allowed_prefix="portfolios/artist/",
            )

        self.assertFalse(deleted)
        delete.assert_not_awaited()


class GalleryDeleteEndpointTests(unittest.IsolatedAsyncioTestCase):
    def _artist(self, url: str) -> User:
        return User(
            id=uuid.uuid4(),
            email="artist@example.test",
            password_hash="disabled",
            name="Artist",
            role="artist",
            slug="artist",
            portfolio_images=[url],
            availability_source="none",
            booking_mode="mailto",
            booking_slot_interval_minutes=30,
            booking_buffer_minutes=30,
            booking_min_notice_hours=24,
            booking_max_advance_days=180,
            booking_allow_same_day=False,
            allow_prepay_full=False,
        )

    async def test_public_artist_detail_returns_portfolio_images(self):
        url = "https://assets.example.test/portfolios/artist/image.webp"
        current_user = self._artist(url)
        result = SimpleNamespace(scalar_one_or_none=lambda: current_user)
        db = SimpleNamespace(execute=AsyncMock(return_value=result))

        artist = await artists.get_artist("artist", db=db)
        public_contract = artists.ArtistDetail.model_validate(artist)

        self.assertEqual(public_contract.portfolio_images, [url])

    async def test_gallery_upload_appends_url_and_public_detail_exposes_it(self):
        existing_url = "https://assets.example.test/portfolios/artist/existing.webp"
        uploaded_url = "https://assets.example.test/portfolios/artist/uploaded.webp"
        current_user = self._artist(existing_url)
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        upload = SimpleNamespace(
            filename="new-work.webp",
            content_type="image/webp",
            read=AsyncMock(side_effect=[b"image-bytes", b""]),
        )

        normalized = SimpleNamespace(
            data=b"normalized-image",
            extension="webp",
            mime_type="image/webp",
        )
        with (
            patch.object(artists, "_read_artist_image", new=AsyncMock(return_value=normalized)),
            patch.object(
                artists,
                "upload_to_drive",
                new=AsyncMock(return_value=uploaded_url),
            ) as store,
        ):
            result = await artists.upload_gallery_image(
                file=upload,
                db=db,
                current_user=current_user,
            )

        store.assert_awaited_once()
        self.assertEqual(
            result.portfolio_images,
            [existing_url, uploaded_url],
        )
        public_contract = artists.ArtistDetail.model_validate(result)
        self.assertEqual(public_contract.portfolio_images, [existing_url, uploaded_url])
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(current_user)

    async def test_failed_object_delete_remains_durable_after_public_gallery_removal(self):
        url = "https://assets.example.test/portfolios/artist/image.webp"
        current_user = self._artist(url)
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        outbox = SimpleNamespace(id=uuid.uuid4())

        with (
            patch.object(artists, "queue_gallery_deletion", new=AsyncMock(return_value=outbox)) as queue,
            patch.object(artists, "process_gallery_deletion_outbox_once", new=AsyncMock(return_value=0)) as process,
        ):
            result = await artists.remove_gallery_image(
                artists.GalleryRemoveRequest(url=url),
                db=db,
                current_user=current_user,
            )

        queue.assert_awaited_once_with(
            db,
            artist_id=current_user.id,
            url=url,
            allowed_prefix="portfolios/artist/",
        )
        process.assert_awaited_once_with(outbox_id=outbox.id)
        self.assertEqual(result.portfolio_images, [])
        db.commit.assert_awaited_once()

    async def test_confirmed_object_delete_commits_gallery_removal(self):
        url = "https://assets.example.test/portfolios/artist/image.webp"
        current_user = self._artist(url)
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        outbox = SimpleNamespace(id=uuid.uuid4())

        with (
            patch.object(artists, "queue_gallery_deletion", new=AsyncMock(return_value=outbox)) as queue,
            patch.object(artists, "process_gallery_deletion_outbox_once", new=AsyncMock(return_value=1)) as process,
        ):
            result = await artists.remove_gallery_image(
                artists.GalleryRemoveRequest(url=url),
                db=db,
                current_user=current_user,
            )

        queue.assert_awaited_once()
        process.assert_awaited_once_with(outbox_id=outbox.id)
        self.assertEqual(result.portfolio_images, [])
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(current_user)

    async def test_database_commit_failure_never_starts_irreversible_storage_delete(self):
        url = "https://assets.example.test/portfolios/artist/image.webp"
        current_user = self._artist(url)
        db = SimpleNamespace(commit=AsyncMock(side_effect=RuntimeError("database unavailable")), refresh=AsyncMock())
        outbox = SimpleNamespace(id=uuid.uuid4())

        with (
            patch.object(artists, "queue_gallery_deletion", new=AsyncMock(return_value=outbox)),
            patch.object(artists, "process_gallery_deletion_outbox_once", new=AsyncMock()) as process,
            self.assertRaisesRegex(RuntimeError, "database unavailable"),
        ):
            await artists.remove_gallery_image(
                artists.GalleryRemoveRequest(url=url),
                db=db,
                current_user=current_user,
            )

        process.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class StaffGalleryEndpointTests(unittest.IsolatedAsyncioTestCase):
    """The owner fills another artist's gallery; the /me routes only ever touch the caller."""

    def _user(self, *, slug: str, role: str = "artist", images: list[str] | None = None) -> User:
        return User(
            id=uuid.uuid4(),
            email=f"{slug}@example.test",
            password_hash="disabled",
            name=slug.title(),
            role=role,
            slug=slug,
            portfolio_images=images,
            availability_source="none",
            booking_mode="mailto",
            booking_slot_interval_minutes=30,
            booking_buffer_minutes=30,
            booking_min_notice_hours=24,
            booking_max_advance_days=180,
            booking_allow_same_day=False,
            allow_prepay_full=False,
        )

    def _db_returning(self, artist):
        result = SimpleNamespace(scalar_one_or_none=lambda: artist)
        return SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock(), refresh=AsyncMock())

    async def test_staff_upload_lands_in_named_artist_gallery_not_the_callers(self):
        founder = self._user(
            slug="owner", role="founder", images=["https://assets.example.test/portfolios/owner/a.webp"]
        )
        target = self._user(slug="devin", images=None)
        uploaded_url = "https://assets.example.test/portfolios/devin/new.webp"
        db = self._db_returning(target)
        upload = SimpleNamespace(filename="work.jpg", content_type="image/jpeg")
        normalized = SimpleNamespace(data=b"normalized-image", extension="webp", mime_type="image/webp")

        with (
            patch.object(artists, "_read_artist_image", new=AsyncMock(return_value=normalized)),
            patch.object(artists, "upload_to_drive", new=AsyncMock(return_value=uploaded_url)) as store,
            patch.object(artists, "upload_portfolio_thumb", new=AsyncMock(return_value=None)),
            patch.object(artists, "upload_portfolio_focus", new=AsyncMock(return_value=None)),
        ):
            result = await artists.upload_gallery_image_for_artist(slug="devin", file=upload, db=db, _caller=founder)

        self.assertEqual(store.await_args.kwargs["folder_path"], "portfolios/devin")
        self.assertEqual(result.portfolio_images, [uploaded_url])
        self.assertEqual(artists.ArtistDetail.model_validate(result).portfolio_images, [uploaded_url])
        self.assertEqual(founder.portfolio_images, ["https://assets.example.test/portfolios/owner/a.webp"])
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(target)

    async def test_staff_upload_for_unknown_artist_is_404_and_stores_nothing(self):
        db = self._db_returning(None)
        caller = self._user(slug="owner", role="founder")
        with (
            patch.object(artists, "upload_to_drive", new=AsyncMock()) as store,
            self.assertRaises(artists.HTTPException) as caught,
        ):
            await artists.upload_gallery_image_for_artist(
                slug="nobody", file=SimpleNamespace(filename="x.jpg"), db=db, _caller=caller
            )
        self.assertEqual(caught.exception.status_code, 404)
        store.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_staff_routes_require_founder(self):
        from fastapi.routing import APIRoute

        guarded = {
            (route.path, method): route
            for route in artists.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }
        for key in (("/artists/{slug}/gallery/upload", "POST"), ("/artists/{slug}/gallery", "DELETE")):
            route = guarded[key]
            check = next(d.call for d in route.dependant.dependencies if d.call.__qualname__.startswith("require_role"))
            for role, allowed in (("collector", False), ("artist", False), ("admin", False), ("founder", True)):
                caller = self._user(slug="caller", role=role)
                if allowed:
                    self.assertIs(await check(current_user=caller), caller)
                else:
                    with self.assertRaises(artists.HTTPException) as denied:
                        await check(current_user=caller)
                    self.assertEqual(denied.exception.status_code, 403)

    async def test_me_routes_stay_ahead_of_slug_routes(self):
        from fastapi.routing import APIRoute

        posts = [r.path for r in artists.router.routes if isinstance(r, APIRoute) and "POST" in r.methods]
        self.assertLess(posts.index("/artists/me/gallery/upload"), posts.index("/artists/{slug}/gallery/upload"))
