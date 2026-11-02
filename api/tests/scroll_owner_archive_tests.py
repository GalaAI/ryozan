"""
Owner archive for submitted Artist Scrolls.

Covers the guarantees the feature exists for: the founder gets their own durable copy in
a dedicated category, that copy survives the artist re-submitting, the copy's underlying
R2 objects are never overwritten, the generated PDF really contains the submitted
evidence, and neither the notification nor the email can cost the artist their
submission.

The endpoint is driven directly against a scripted session, matching
booking_draft_recovery_tests.py. The suite has no database. R2 is stubbed so every
object key written is observable, which is how the no-overwrite guarantee is checked.
"""

import asyncio
import inspect
import io
import json
import os
import re
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import reportlab.rl_config
from fastapi import HTTPException
from PIL import Image
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request

from app.api.v1.endpoints import documents as documents_module
from app.api.v1.endpoints import scrolls as scrolls_module
from app.api.v1.endpoints.documents import get_my_scroll, submit_scroll
from app.models.document import DocumentInstance
from app.models.notification import Notification
from app.models.scroll_owner_email_outbox import ScrollOwnerEmailOutbox
from app.models.user import User
from app.services.email import SCROLL_REQUEST_TEMPLATE_MARKER, build_scroll_request_html
from app.services.email_foundry import STARSEED_TILE_URL, TIGER_MARK_URL
from app.services.email_template_seeder import get_seed_templates
from app.services.scroll_archive import (
    OWNER_ARCHIVE_CATEGORY,
    OWNER_ARCHIVE_SLUG,
    SCROLL_SUBMITTED_NOTIF_TYPE,
    build_owner_archive_email_html,
    build_submission_prefix,
    process_owner_email_outbox_once,
)
from app.services.scroll_pdf import ScrollAsset, ScrollPdfInput, build_scroll_pdf

SCROLLS_PANEL = Path(__file__).resolve().parents[2] / "app" / "src" / "components" / "ScrollsPanel.tsx"
MOBILE_PORTAL_HOME = (
    Path(__file__).resolve().parents[2] / "app" / "src" / "pages" / "dashboard" / "MobilePortalHome.tsx"
)
PROTECTED_ROUTE = Path(__file__).resolve().parents[2] / "app" / "src" / "components" / "layout" / "ProtectedRoute.tsx"
PORTAL_PAGE = Path(__file__).resolve().parents[2] / "app" / "src" / "pages" / "PortalPage.tsx"


class ArtistScrollNotificationRouteTests(unittest.TestCase):
    def test_artist_scroll_requests_open_the_spyder_hub_workflow(self):
        self.assertEqual(
            scrolls_module._ARTIST_SCROLL_ACTION_URL,
            "/dashboard?portal=hub&workflow=artist-scroll",
        )

    def test_assigned_staff_roles_can_open_the_artist_scroll_deep_link(self):
        source = MOBILE_PORTAL_HOME.read_text(encoding="utf-8")

        self.assertIn("['artist', 'admin', 'founder'].includes(role)", source)
        self.assertIn("['artist', 'admin', 'founder'].includes(user?.role ?? '')", source)
        self.assertIn("!requestedArtistScroll || !artistScrollEligible || !user?.id", source)

    def test_logged_out_deep_link_survives_authentication(self):
        protected_route = PROTECTED_ROUTE.read_text(encoding="utf-8")
        portal_page = PORTAL_PAGE.read_text(encoding="utf-8")

        self.assertIn("`${location.pathname}${location.search}${location.hash}`", protected_route)
        self.assertIn("new URLSearchParams({ returnTo })", protected_route)
        self.assertIn("<Navigate to={`/portal?${loginSearch}`} replace />", protected_route)
        self.assertIn("function authReturnDestination", portal_page)
        self.assertIn("requested.startsWith('/dashboard?')", portal_page)
        self.assertIn("requested.includes('\\\\')", portal_page)
        # Registration now waits for mailbox verification; only authenticated
        # arrivals may follow the preserved destination below.
        self.assertNotIn("navigate(authReturnTo)", portal_page)
        self.assertIn("<Navigate to={authReturnTo} replace />", portal_page)


# ── Fixtures ──────────────────────────────────────────


def _png_bytes(size=(40, 20), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(50, 30), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _data_url(raw: bytes) -> str:
    import base64

    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _upload(name: str, data: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=name,
        headers=Headers({"content-type": content_type}),
    )


def _user(role: str, name: str, email: str) -> User:
    user = User(email=email, password_hash="x", name=name, role=role)
    user.id = uuid.uuid4()
    user.bttcoin_balance = 0
    user.quest_points = 0
    return user


class _Scalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Routes queries by the table they hit, and records everything written.

    Deliberately stateful across calls so a second submission sees the rows the first
    one added. That is what makes the "re-submission does not delete the founder's
    copy" assertion meaningful rather than a tautology.
    """

    def __init__(self, founders, instances=None):
        self.founders = list(founders)
        self.instances = list(instances or [])
        self.added = []
        self.deleted = []
        self.commits = 0

    async def execute(self, stmt):
        sql = str(stmt)
        if sql.strip().upper().startswith("UPDATE"):
            return _Result([])
        if "ryozan_users" in sql:
            return _Result(self.founders)
        if "ryozan_document_instances" in sql:
            rows = self.instances
            if "category" in sql and "scroll" in sql.lower():
                pass
            return _Result(rows)
        return _Result([])

    def add(self, row):
        self.added.append(row)
        if isinstance(row, DocumentInstance):
            self.instances.append(row)

    async def delete(self, row):
        self.deleted.append(row)
        if row in self.instances:
            self.instances.remove(row)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class ArtistScrollRequestEmailTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self):
        return SimpleNamespace(FRONTEND_URL="https://app.ryozan.app/")

    def test_builder_uses_foundry_type_assets_and_live_scroll_route(self):
        with patch("app.services.email.get_settings", return_value=self._settings()):
            html = build_scroll_request_html("Jade")

        self.assertIn(SCROLL_REQUEST_TEMPLATE_MARKER, html)
        self.assertIn("family=Poppins", html)
        self.assertIn("Space+Grotesk", html)
        self.assertIn("JetBrains+Mono", html)
        self.assertIn(TIGER_MARK_URL, html)
        self.assertIn(STARSEED_TILE_URL, html)
        self.assertNotIn("/assets/starseed-tile.png", html)
        self.assertNotIn('width="600"', html)
        self.assertIn("background-color:#d38a68", html)
        self.assertIn(
            'href="https://app.ryozan.app/dashboard?portal=hub&workflow=artist-scroll"',
            html,
        )
        self.assertNotIn("/dashboard?panel=codex", html)
        self.assertNotIn("logo-round.png", html)

    def test_seed_template_keeps_runtime_values_as_merge_tags(self):
        template = next(item for item in get_seed_templates() if item["slug"] == "scroll_request")

        self.assertIn(SCROLL_REQUEST_TEMPLATE_MARKER, template["html_body"])
        self.assertIn("{{artist_name}}", template["html_body"])
        self.assertIn("{{eyebrow}}", template["html_body"])
        self.assertIn("{{body_intro}}", template["html_body"])
        self.assertIn('href="{{action_url}}"', template["html_body"])

    async def test_stale_database_template_falls_back_to_current_foundry_email(self):
        artist = _user("artist", "Jade", "jade@ryozan.test")
        session = FakeSession([])
        sent = []

        async def capture_email(**kwargs):
            sent.append(kwargs)
            return True

        with (
            patch.object(scrolls_module, "get_settings", return_value=self._settings()),
            patch("app.services.email.get_settings", return_value=self._settings()),
            patch.object(
                scrolls_module,
                "render_db_template",
                return_value='<html><a href="https://app.ryozan.app/dashboard?panel=codex">old</a></html>',
            ),
            patch.object(scrolls_module, "send_push_notification", return_value=True),
            patch.object(scrolls_module, "send_email", capture_email),
        ):
            await scrolls_module._send_scroll_notification(session, artist, is_reminder=False)

        self.assertEqual(len(sent), 1)
        html = sent[0]["html_body"]
        self.assertIn(SCROLL_REQUEST_TEMPLATE_MARKER, html)
        self.assertIn(
            'href="https://app.ryozan.app/dashboard?portal=hub&workflow=artist-scroll"',
            html,
        )
        self.assertNotIn("/dashboard?panel=codex", html)


class _ArtistScrollSession(FakeSession):
    """Session whose document query returns only the caller's own category='scroll' rows,
    the way the real upsert query filters."""

    def __init__(self, founders, instances=None, caller=None):
        super().__init__(founders, instances)
        self.caller = caller

    async def execute(self, stmt):
        sql = str(stmt)
        if sql.strip().upper().startswith("UPDATE"):
            return _Result([])
        if "ryozan_users" in sql:
            return _Result(self.founders)
        if "ryozan_document_instances" in sql:
            rows = [
                i
                for i in self.instances
                if i.category == "scroll" and (self.caller is None or i.user_id == self.caller.id)
            ]
            return _Result(rows)
        return _Result([])


class _R2Stub:
    """Stands in for R2, recording every object key written."""

    def __init__(self, fail_keys=()):
        self.keys = []
        self.fail_keys = set(fail_keys)

    async def __call__(self, file_bytes, filename, mime_type, folder_path):
        key = f"{folder_path}/{filename}"
        self.keys.append(key)
        if filename in self.fail_keys:
            return None
        return f"https://cdn.test/{key}"


class _OutboxSession:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _stmt):
        return _Result(self.rows)

    async def commit(self):
        self.commits += 1


class _OutboxSessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self.session


async def _submit(
    session,
    artist,
    r2,
    *,
    with_certs=True,
    fail_keys=(),
    cert_data: bytes | None = None,
):
    r2.fail_keys = set(fail_keys)
    kwargs = dict(
        request=Request({"type": "http", "headers": [(b"user-agent", b"owner-archive-test")]}),
        artist_signature=_data_url(_png_bytes(color=(5, 5, 200))),
        fields_json=json.dumps(
            {
                "license_number": "SC-TATTOO-9931",
                "bbp_expires": "2027-04-01",
                "tax_opt_in": True,
                "terms_version": "artist-scroll-2026-08-10-v1",
                "section_acceptances": {
                    key: {"label": key.title(), "accepted": True, "legal_name": "Rae Vance"}
                    for key in ("application", "agreement", "bloodborne", "fire", "dhec")
                },
            }
        ),
        db=session,
        current_user=artist,
    )
    if with_certs:
        cert_bytes = cert_data if cert_data is not None else _jpeg_bytes()
        kwargs.update(
            bbp_cert=_upload("bbp.jpg", cert_bytes, "image/jpeg"),
            first_aid_cert=_upload("aid.jpg", cert_bytes, "image/jpeg"),
            cpr_cert=None,
            govt_id=_upload("id.pdf", b"%PDF-1.4 not really an image", "application/pdf"),
        )
    else:
        kwargs.update(bbp_cert=None, first_aid_cert=None, cpr_cert=None, govt_id=None)

    with patch.object(documents_module, "upload_to_blob", r2):
        return await submit_scroll(**kwargs)


# ── Tests ─────────────────────────────────────────────


class OwnerArchiveFilingTests(unittest.IsolatedAsyncioTestCase):
    async def test_submission_creates_artist_and_founder_instances(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin Walker", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)
        r2 = _R2Stub()

        with patch("app.services.scroll_archive.send_email", return_value=True):
            await _submit(session, artist, r2)

        instances = [r for r in session.added if isinstance(r, DocumentInstance)]
        self.assertEqual(len(instances), 2, "expected one artist instance and one founder archive copy")

        artist_rows = [i for i in instances if i.user_id == artist.id]
        founder_rows = [i for i in instances if i.user_id == founder.id]
        self.assertEqual(len(artist_rows), 1)
        self.assertEqual(len(founder_rows), 1)

        self.assertEqual(artist_rows[0].category, "scroll")
        self.assertEqual(
            founder_rows[0].category,
            OWNER_ARCHIVE_CATEGORY,
            "the owner copy must use the dedicated category, never 'scroll'",
        )
        self.assertNotEqual(founder_rows[0].category, "scroll")
        self.assertIn("Rae Vance", founder_rows[0].title)

        data = json.loads(founder_rows[0].data_json)
        self.assertEqual(data["artist_user_id"], str(artist.id))
        self.assertEqual(data["artist_name"], "Rae Vance")
        self.assertTrue(data["submitted_at"])

    async def test_founder_copy_is_filed_for_every_founder(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founders = [_user("founder", "A", "a@ryozan.test"), _user("founder", "B", "b@ryozan.test")]
        session = _ArtistScrollSession(founders, caller=artist)

        with patch("app.services.scroll_archive.send_email", return_value=True):
            result = await _submit(session, artist, _R2Stub())

        self.assertEqual(result["owner_copies_filed"], 2)

    async def test_no_founder_skips_archive_without_failing_submission(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        session = _ArtistScrollSession([], caller=artist)

        result = await _submit(session, artist, _R2Stub())

        self.assertEqual(result["status"], "signed")
        self.assertEqual(result["owner_copies_filed"], 0)
        instances = [r for r in session.added if isinstance(r, DocumentInstance)]
        self.assertEqual(len(instances), 1)


class OwnerArchiveInputAndAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_artist_form_submits_without_an_owner_signature(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)
        r2 = _R2Stub()

        result = await _submit(session, artist, r2)

        self.assertEqual(result["status"], "signed")
        self.assertIsNone(result["owner_sig_url"])
        self.assertFalse(any(key.endswith("/owner-sig.png") for key in r2.keys))
        self.assertEqual(result["owner_copies_filed"], 1)
        self.assertNotIn("owner_signature", inspect.signature(submit_scroll).parameters)

    async def test_per_file_limit_rejects_before_storage_or_upsert(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)
        r2 = _R2Stub()

        with self.assertRaises(HTTPException) as raised:
            await _submit(
                session,
                artist,
                r2,
                cert_data=b"x" * (documents_module.MAX_SCROLL_FILE_BYTES + 1),
            )

        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(r2.keys, [])
        self.assertEqual(session.deleted, [])

    def test_owner_archive_is_founder_only_in_both_api_surfaces(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")

        for guard, category in (
            (documents_module._require_owner_archive_access, OWNER_ARCHIVE_CATEGORY),
            (scrolls_module._require_owner_archive_access, OWNER_ARCHIVE_SLUG),
        ):
            with self.subTest(guard=guard.__module__):
                with self.assertRaises(HTTPException) as raised:
                    guard(artist, category)
                self.assertEqual(raised.exception.status_code, 403)
                guard(founder, category)

    async def test_artist_file_url_is_the_scroll_pdf_not_the_bbp_cert(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)
        r2 = _R2Stub()

        with patch("app.services.scroll_archive.send_email", return_value=True):
            result = await _submit(session, artist, r2)

        artist_row = next(r for r in session.added if isinstance(r, DocumentInstance) and r.user_id == artist.id)
        self.assertTrue(
            artist_row.file_url.endswith("artist-scroll.pdf"),
            f"artist file_url should be the generated scroll PDF, got {artist_row.file_url}",
        )
        self.assertNotIn("bbp-cert", artist_row.file_url)
        self.assertEqual(result["scroll_pdf_url"], artist_row.file_url)


class OwnerScrollIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_my_scroll_as_founder_does_not_return_an_artist_scroll(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)

        with patch("app.services.scroll_archive.send_email", return_value=True):
            await _submit(session, artist, _R2Stub())

        # The founder now owns an archive copy of the artist's scroll. Their own
        # /scroll/my must still be empty: the archive is not their scroll.
        founder_session = _ArtistScrollSession([founder], instances=session.instances, caller=founder)
        result = await get_my_scroll(db=founder_session, current_user=founder)

        self.assertIsNone(
            result["scroll"],
            "the founder's own scroll endpoint must not surface an artist's scroll",
        )

    async def test_archive_category_differs_from_artist_scroll_category(self):
        self.assertEqual(OWNER_ARCHIVE_CATEGORY, f"scroll_{OWNER_ARCHIVE_SLUG}")
        self.assertNotEqual(OWNER_ARCHIVE_CATEGORY, "scroll")


class ResubmissionImmutabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_submission_preserves_founder_archive_and_objects(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)

        r2_first = _R2Stub()
        with patch("app.services.scroll_archive.send_email", return_value=True):
            await _submit(session, artist, r2_first)

        first_archive = next(
            r for r in session.added if isinstance(r, DocumentInstance) and r.category == OWNER_ARCHIVE_CATEGORY
        )
        first_data = json.loads(first_archive.data_json)

        r2_second = _R2Stub()
        with patch("app.services.scroll_archive.send_email", return_value=True):
            await _submit(session, artist, r2_second)

        # The founder's first archive row must not have been deleted by the upsert.
        self.assertNotIn(
            first_archive,
            session.deleted,
            "the artist's re-submission deleted the founder's archived copy",
        )
        archives = [
            r for r in session.added if isinstance(r, DocumentInstance) and r.category == OWNER_ARCHIVE_CATEGORY
        ]
        self.assertEqual(len(archives), 2, "each submission should file its own archive copy")

        # Only the artist's own category='scroll' row is replaced.
        for deleted in session.deleted:
            self.assertEqual(deleted.category, "scroll")
            self.assertEqual(deleted.user_id, artist.id)

        # No R2 object key is reused between submissions, so the first archive's
        # signatures, certificates, and PDF are still exactly what was filed.
        self.assertTrue(r2_first.keys, "first submission wrote no objects")
        overlap = set(r2_first.keys) & set(r2_second.keys)
        self.assertEqual(
            overlap,
            set(),
            f"re-submission overwrote objects the earlier archive points at: {sorted(overlap)}",
        )

        second_data = json.loads(archives[1].data_json)
        self.assertNotEqual(first_data["storage_prefix"], second_data["storage_prefix"])
        self.assertNotEqual(first_data["submission_id"], second_data["submission_id"])

    def test_submission_prefixes_are_unique(self):
        artist_id = str(uuid.uuid4())
        prefixes = {build_submission_prefix(artist_id) for _ in range(50)}
        self.assertEqual(len(prefixes), 50)
        for prefix in prefixes:
            self.assertTrue(prefix.startswith(f"scrolls/{artist_id}/"))


class ScrollPdfContentTests(unittest.TestCase):
    def setUp(self):
        self._page_compression = reportlab.rl_config.pageCompression
        reportlab.rl_config.pageCompression = 0
        self.addCleanup(
            setattr,
            reportlab.rl_config,
            "pageCompression",
            self._page_compression,
        )

    def _build(self):
        return build_scroll_pdf(
            ScrollPdfInput(
                artist_name="Rae Vance",
                artist_email="rae@ryozan.test",
                artist_user_id="artist-123",
                submission_id="sub-abc",
                fields={"license_number": "SC-TATTOO-9931", "bbp_expires": "2027-04-01"},
                artist_signature_png=_png_bytes(color=(5, 5, 200)),
                owner_signature_png=_png_bytes(color=(200, 5, 5)),
                assets=[
                    ScrollAsset(
                        key="bbp_cert",
                        label="Bloodborne Pathogens Certificate",
                        filename="bbp.jpg",
                        content_type="image/jpeg",
                        data=_jpeg_bytes(),
                        url="https://cdn.test/bbp.jpg",
                        stored=True,
                    ),
                    ScrollAsset(
                        key="first_aid_cert",
                        label="First Aid Certificate",
                        filename="aid.jpg",
                        content_type="image/jpeg",
                        data=_jpeg_bytes(color=(20, 200, 20)),
                        url="https://cdn.test/aid.jpg",
                        stored=True,
                    ),
                    ScrollAsset(key="cpr_cert", label="CPR Certificate", provided=False),
                    ScrollAsset(
                        key="govt_id",
                        label="Government ID",
                        filename="id.pdf",
                        content_type="application/pdf",
                        data=b"%PDF-1.4 not an image",
                        url="https://cdn.test/id.pdf",
                        stored=True,
                    ),
                ],
            )
        )

    def test_pdf_contains_fields_signatures_and_embedded_certs(self):
        pdf = self._build()
        self.assertTrue(pdf.startswith(b"%PDF"))
        text = pdf.decode("latin-1")

        # Submitted form fields are rendered.
        self.assertIn("SC-TATTOO-9931", text)
        self.assertIn("2027-04-01", text)
        self.assertIn("Rae Vance", text)

        # Both signatures have their own labelled block.
        self.assertIn("Artist Signature", text)
        self.assertIn("Owner Countersignature", text)

        # Certificate pages exist and the missing one is named as not provided.
        self.assertIn("Bloodborne Pathogens Certificate", text)
        self.assertIn("First Aid Certificate", text)
        self.assertIn("Government ID", text)
        self.assertIn("Not provided", text)

        # Two signatures + two embeddable certs are actually embedded as images.
        # The govt ID is a PDF, so it is referenced rather than embedded.
        self.assertGreaterEqual(
            text.count("/Subtype /Image"),
            4,
            "expected both signatures and both image certs embedded as image XObjects",
        )

    def test_non_image_upload_is_referenced_not_silently_dropped(self):
        text = self._build().decode("latin-1")
        self.assertIn("not an embeddable image", text)
        self.assertIn("https://cdn.test/id.pdf", text)

    def test_artist_only_submission_does_not_report_a_missing_owner_signature(self):
        pdf = build_scroll_pdf(
            ScrollPdfInput(
                artist_name="Rae Vance",
                artist_signature_png=_png_bytes(color=(5, 5, 200)),
            )
        )
        text = pdf.decode("latin-1")

        self.assertIn("Artist Signature", text)
        self.assertNotIn("Owner Countersignature", text)

    def test_failed_upload_is_declared_missing_not_omitted(self):
        pdf = build_scroll_pdf(
            ScrollPdfInput(
                artist_name="Rae Vance",
                fields={"license_number": "X"},
                assets=[
                    ScrollAsset(
                        key="bbp_cert",
                        label="Bloodborne Pathogens Certificate",
                        filename="bbp.jpg",
                        content_type="image/jpeg",
                        data=_jpeg_bytes(),
                        stored=False,
                        error="storage returned no URL",
                    )
                ],
            )
        )
        text = pdf.decode("latin-1")
        self.assertIn("UPLOAD FAILED", text)
        self.assertIn("DOCUMENT NOT STORED", text)

    def test_free_text_with_markup_characters_does_not_break_the_build(self):
        pdf = build_scroll_pdf(
            ScrollPdfInput(
                artist_name="Rae & <b>Vance</b>",
                fields={"notes": "A < B & C > D", "shop": "<script>alert(1)</script>"},
            )
        )
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_versioned_acceptance_metadata_is_printed(self):
        pdf = build_scroll_pdf(
            ScrollPdfInput(
                artist_name="Rae Vance",
                terms_version="artist-scroll-2026-08-10-v1",
                acceptance_metadata={
                    "accepted_at": "2026-08-10T12:00:00+00:00",
                    "accepted_by_user_id": "user-123",
                    "accepted_by_email": "rae@example.test",
                    "sections": {
                        "agreement": {"label": "Agreement", "legal_name": "Rae Vance"},
                    },
                },
            )
        ).decode("latin-1")

        self.assertIn("artist-scroll-2026-08-10-v1", pdf)
        self.assertIn("2026-08-10T12:00:00+00:00", pdf)
        self.assertIn("Agreement: accepted by Rae Vance", pdf)

    def test_owner_email_html_escapes_artist_and_asset_values(self):
        html = build_owner_archive_email_html(
            "Rae <script>alert(1)</script>",
            documents_module.datetime.now(documents_module.UTC),
            [
                ScrollAsset(
                    key="bbp",
                    label="<img src=x onerror=alert(1)>",
                    error="<b>failed</b>",
                    stored=False,
                )
            ],
            pdf_attached=False,
        )

        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("no PDF is attached", html)


class OwnerNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_row_created_for_founder(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)

        with patch("app.services.scroll_archive.send_email", return_value=True):
            await _submit(session, artist, _R2Stub())

        notifs = [
            r
            for r in session.added
            if isinstance(r, Notification) and r.notification_type == SCROLL_SUBMITTED_NOTIF_TYPE
        ]
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0].user_id, founder.id)
        self.assertIn("Rae Vance", notifs[0].body)
        self.assertEqual(
            notifs[0].action_url,
            "/dashboard?portal=hub&panel=codex&category=owner-archive",
        )

    async def test_email_is_durably_queued_with_the_pdf_attached(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)

        result = await _submit(session, artist, _R2Stub())

        rows = [r for r in session.added if isinstance(r, ScrollOwnerEmailOutbox)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].to_email, "justin@ryozan.test")
        self.assertIn("Rae Vance", rows[0].subject)
        self.assertTrue(rows[0].attachment_content.startswith(b"%PDF"))
        self.assertEqual(rows[0].status, "queued")
        self.assertEqual(result["owner_emails_queued"], 1)
        self.assertEqual(result["owner_emails_sent"], 0)

    async def test_email_provider_is_not_called_on_the_request_path(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)

        with patch("app.services.scroll_archive.send_email") as sender:
            result = await _submit(session, artist, _R2Stub())

        sender.assert_not_called()
        self.assertEqual(result["status"], "signed")
        self.assertEqual(result["bttcoin_awarded"], 500)
        self.assertEqual(result["owner_emails_sent"], 0)
        self.assertEqual(result["owner_emails_queued"], 1)
        self.assertEqual(result["owner_copies_filed"], 1)
        self.assertEqual(session.commits, 1)

    async def test_notification_failure_still_returns_success_to_the_artist(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)

        with patch("app.services.scroll_archive.Notification", side_effect=RuntimeError("notif exploded")):
            result = await _submit(session, artist, _R2Stub())

        self.assertEqual(result["status"], "signed")
        self.assertEqual(result["bttcoin_awarded"], 500)


class OwnerEmailOutboxProcessorTests(unittest.IsolatedAsyncioTestCase):
    def _row(self):
        now = documents_module.datetime.now(documents_module.UTC)
        row = ScrollOwnerEmailOutbox(
            founder_id=uuid.uuid4(),
            submission_id="submission-1",
            to_email="justin@ryozan.test",
            subject="Artist Scroll Submitted: Rae Vance",
            html_body="<p>ready</p>",
            attachment_filename="artist-scroll-Rae_Vance.pdf",
            attachment_content=b"%PDF-1.4",
            status="queued",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        row.id = uuid.uuid4()
        return row

    async def test_processor_marks_claimed_email_sent(self):
        row = self._row()
        session = _OutboxSession([row])
        calls = []

        async def _capture(**kwargs):
            calls.append(kwargs)
            return True

        with (
            patch("app.core.database.async_session", _OutboxSessionFactory(session)),
            patch("app.services.scroll_archive.send_email", _capture),
        ):
            delivered = await process_owner_email_outbox_once()

        self.assertEqual(delivered, 1)
        self.assertEqual(row.status, "sent")
        self.assertEqual(row.attempts, 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["attachments"][0].content.startswith(b"%PDF"))

    async def test_processor_retains_failed_email_for_retry(self):
        row = self._row()
        session = _OutboxSession([row])

        with (
            patch("app.core.database.async_session", _OutboxSessionFactory(session)),
            patch("app.services.scroll_archive.send_email", return_value=False),
        ):
            delivered = await process_owner_email_outbox_once()

        self.assertEqual(delivered, 0)
        self.assertEqual(row.status, "failed")
        self.assertEqual(row.attempts, 1)
        self.assertIn("False", row.last_error)


class PartialUploadVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_cert_upload_is_recorded_in_both_copies(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        founder = _user("founder", "Justin", "justin@ryozan.test")
        session = _ArtistScrollSession([founder], caller=artist)

        with patch("app.services.scroll_archive.send_email", return_value=True):
            await _submit(session, artist, _R2Stub(), fail_keys={"bbp-cert.jpg"})

        rows = [r for r in session.added if isinstance(r, DocumentInstance)]
        artist_row = next(r for r in rows if r.category == "scroll")
        archive_row = next(r for r in rows if r.category == OWNER_ARCHIVE_CATEGORY)

        artist_certs = json.loads(artist_row.data_json)["certs_stored"]
        self.assertFalse(artist_certs["bbp_cert"]["stored"])
        self.assertTrue(artist_certs["first_aid_cert"]["stored"])

        archive_certs = json.loads(archive_row.data_json)["certs_stored"]
        self.assertFalse(archive_certs["bbp_cert"]["stored"])
        self.assertIsNone(archive_certs["bbp_cert"]["url"])


class EmailAttachmentTransportTests(unittest.TestCase):
    """send_email had no attachment support; these pin the encoding per provider and
    prove the no-attachment SMTP message is unchanged."""

    def test_sendgrid_payload_carries_base64_attachment(self):
        import base64

        from app.services.email import EmailAttachment, _send_via_sendgrid

        captured = {}

        class _Resp:
            status_code = 202
            headers = {"X-Message-Id": "mid"}
            text = ""

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                captured.update(json)
                return _Resp()

        from app.core.config import get_settings

        with (
            patch("app.services.email.httpx.AsyncClient", lambda **kw: _Client()),
            patch.object(get_settings(), "SENDGRID_FROM_EMAIL", "noreply@studio.example.test"),
        ):
            ok, _, _ = asyncio.run(
                _send_via_sendgrid(
                    "a@b.test",
                    "subj",
                    "<p>hi</p>",
                    attachments=[EmailAttachment(filename="s.pdf", content=b"%PDF-1", content_type="application/pdf")],
                )
            )

        self.assertTrue(ok)
        att = captured["attachments"][0]
        self.assertEqual(att["filename"], "s.pdf")
        self.assertEqual(att["type"], "application/pdf")
        self.assertEqual(att["disposition"], "attachment")
        self.assertEqual(base64.b64decode(att["content"]), b"%PDF-1")

    def test_smtp_message_without_attachments_stays_multipart_alternative(self):
        from app.services.email import _send_via_smtp

        sent = {}

        async def _fake_send(msg, **kw):
            sent["msg"] = msg

        with (
            patch("app.services.email.aiosmtplib.send", _fake_send),
            patch("app.services.email.get_settings") as gs,
        ):
            gs.return_value.SMTP_USER = "u"
            gs.return_value.SMTP_PASSWORD = "p"
            gs.return_value.SMTP_FROM_NAME = "N"
            gs.return_value.SMTP_FROM_EMAIL = "f@b.test"
            gs.return_value.SMTP_HOST = "h"
            gs.return_value.SMTP_PORT = 587
            asyncio.run(_send_via_smtp("a@b.test", "s", "<p>hi</p>"))

        self.assertEqual(sent["msg"].get_content_subtype(), "alternative")

    def test_smtp_message_with_attachment_nests_body_in_multipart_mixed(self):
        from app.services.email import EmailAttachment, _send_via_smtp

        sent = {}

        async def _fake_send(msg, **kw):
            sent["msg"] = msg

        with (
            patch("app.services.email.aiosmtplib.send", _fake_send),
            patch("app.services.email.get_settings") as gs,
        ):
            gs.return_value.SMTP_USER = "u"
            gs.return_value.SMTP_PASSWORD = "p"
            gs.return_value.SMTP_FROM_NAME = "N"
            gs.return_value.SMTP_FROM_EMAIL = "f@b.test"
            gs.return_value.SMTP_HOST = "h"
            gs.return_value.SMTP_PORT = 587
            asyncio.run(
                _send_via_smtp(
                    "a@b.test",
                    "s",
                    "<p>hi</p>",
                    attachments=[EmailAttachment(filename="s.pdf", content=b"%PDF-1", content_type="application/pdf")],
                )
            )

        msg = sent["msg"]
        self.assertEqual(msg.get_content_subtype(), "mixed")
        parts = msg.get_payload()
        self.assertEqual(parts[0].get_content_subtype(), "alternative")
        self.assertEqual(parts[1].get_filename(), "s.pdf")
        self.assertEqual(parts[1].get_payload(decode=True), b"%PDF-1")

    def test_smtp_disables_starttls_for_local_mailhog_capture(self):
        from app.services.email import _send_via_smtp

        sent = {}

        async def _fake_send(_msg, **kwargs):
            sent.update(kwargs)

        with (
            patch("app.services.email.aiosmtplib.send", _fake_send),
            patch("app.services.email.get_settings") as gs,
        ):
            gs.return_value.SMTP_USER = "mailhog"
            gs.return_value.SMTP_PASSWORD = "mailhog"
            gs.return_value.SMTP_FROM_NAME = "Ryozan Dev"
            gs.return_value.SMTP_FROM_EMAIL = "dev@ryozan-studio.local"
            gs.return_value.SMTP_HOST = "127.0.0.1"
            gs.return_value.SMTP_PORT = 1025
            asyncio.run(_send_via_smtp("client@example.test", "subject", "<p>body</p>"))

        self.assertFalse(sent["start_tls"])


class OwnerArchiveTabVisibilityTests(unittest.TestCase):
    """The archive tab must render for founders only.

    Seeding owner-archive as a real category is required -- the backend hardcodes
    its slug and GET /scrolls/owner-archive 404s without the row -- but that also
    makes it appear in the category list every dashboard user fetches.
    GET /scrolls/{slug} already scopes non-founders to their own rows, so there is
    no data leak, but an artist would otherwise get an empty "Artist Scrolls" tab
    with an "Add Scroll" button that files junk into the owner's archive category.

    These are static guards on ScrollsPanel.tsx, matching the approach
    scroll_category_seed_tests.py already uses for the seed/frontend drift check.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = SCROLLS_PANEL.read_text(encoding="utf-8")

    def test_scrolls_panel_exists(self):
        self.assertTrue(
            SCROLLS_PANEL.is_file(),
            f"{SCROLLS_PANEL} not found. If ScrollsPanel.tsx moved, update this path -- "
            "the owner archive tab must stay founder-gated.",
        )

    def test_owner_archive_is_not_a_frontend_fallback_tab(self):
        """BUILTIN_CATEGORIES renders before the API responds and is not role-aware.

        Putting owner-archive in it would flash the tab for every user on load.
        """
        block = re.search(r"const BUILTIN_CATEGORIES[^=]*=\s*\[(.*?)\n\]", self.source, re.DOTALL)
        self.assertIsNotNone(block, "BUILTIN_CATEGORIES array not found in ScrollsPanel.tsx")
        self.assertNotIn(
            OWNER_ARCHIVE_SLUG,
            block.group(1),
            "owner-archive must not be in BUILTIN_CATEGORIES -- that fallback is not role-aware, "
            "so the archive tab would render for every user before the API responds.",
        )

    def test_owner_archive_is_visible_to_founders_in_codex(self):
        self.assertIn(
            "c.slug !== OWNER_ARCHIVE_SLUG || user?.role === 'founder'",
            self.source,
            "owner-archive must remain hidden from non-founders and visible to founders.",
        )
        self.assertNotIn("if (requested === OWNER_ARCHIVE_SLUG) return 'artist'", self.source)
        self.assertRegex(
            self.source,
            r"\{visibleCategories\.map\(",
            "the subtab bar must render visibleCategories, not the unfiltered categories list.",
        )

    def test_owner_archive_is_read_only_in_codex(self):
        self.assertIn("onDelete={isOwnerArchive ? undefined", self.source)
        self.assertIn("{!isOwnerArchive && (", self.source)


if __name__ == "__main__":
    unittest.main()
