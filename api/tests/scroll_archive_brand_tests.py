"""
Artist Scroll owner email and archive PDF in the Ryozan brand, plus the starseed cache-bust.

Pins: every outgoing body points at the current versioned starseed tile (the R2 object is
cached as immutable, so a replaced tile needs a new URL), the owner "scroll filed" email uses
the Email Foundry shell with a working Codex link and ET timestamp, and the archive PDF carries
the branded header while keeping every missing/failed statement the archive exists to make.
"""

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import reportlab.rl_config

from app.services.email_foundry import (
    STARSEED_TILE_URL,
    render_foundry_email,
    render_gift_card_receipt,
    with_current_starseed,
)
from app.services.email_foundry_waiver import render_checkin_code
from app.services.scroll_archive import OWNER_ARCHIVE_SLUG, build_owner_archive_email_html
from app.services.scroll_pdf import ScrollAsset, ScrollPdfInput, build_scroll_pdf

OLD_R2 = "https://assets.ryozan.app/email/starseed-tile.png"
OLD_PACKAGE = "https://app.ryozan.app/dependencies/@bttco/email-foundry/1.0.0/assets/starseed-tile.png"


class StarseedCacheBustTests(unittest.TestCase):
    def test_current_url_is_versioned(self):
        self.assertTrue(STARSEED_TILE_URL.startswith(OLD_R2 + "?v="))

    def test_rewrites_unversioned_package_and_stale_versions_once(self):
        html = (
            f'<table background="{OLD_R2}" style="background-image:url(\'{OLD_R2}\')">'
            f'<td background="{OLD_PACKAGE}"></td><td background="{OLD_R2}?v=1"></td>'
        )
        out = with_current_starseed(html)
        self.assertEqual(out.count(STARSEED_TILE_URL), 4)
        self.assertNotIn(OLD_PACKAGE, out)
        self.assertNotIn("?v=1", out)
        self.assertEqual(with_current_starseed(out), out, "rewriting must be idempotent")

    def test_leaves_other_assets_alone(self):
        html = '<img src="https://assets.ryozan.app/email/tiger-mark.png">'
        self.assertEqual(with_current_starseed(html), html)
        self.assertEqual(with_current_starseed(""), "")

    def test_catalog_and_side_renderers_use_current_tile(self):
        foundry = render_foundry_email("booking_confirmation", {"client_name": "Rae"})
        self.assertIsNotNone(foundry)
        for html in (
            foundry.html_body,
            render_checkin_code(code="123456").html_body,
            render_gift_card_receipt(
                recipient_name="Rae", purchaser_name="Sam", artist_name="Jade", amount_cents=5000, code="GIFT-1"
            ).html_body,
        ):
            self.assertIn(STARSEED_TILE_URL, html)
            self.assertNotIn(f'"{OLD_R2}"', html)


class OwnerArchiveEmailBrandTests(unittest.TestCase):
    def _html(self, **kwargs):
        settings = SimpleNamespace(FRONTEND_URL="https://app.ryozan.app/", STUDIO_NAME="Ryozan Studio", STUDIO_ADDRESS="")
        with patch("app.core.config.get_settings", return_value=settings):
            return build_owner_archive_email_html(
                kwargs.get("artist", "Sample Artist"),
                datetime(2026, 9, 29, 17, 20, 45, tzinfo=UTC),
                kwargs.get(
                    "assets",
                    [
                        ScrollAsset(key="bbp_cert", label="Bloodborne Pathogens Certificate", stored=True),
                        ScrollAsset(key="cpr_cert", label="CPR Certificate", provided=False),
                        ScrollAsset(key="govt_id", label="Government ID", stored=False, error="timeout"),
                    ],
                ),
                pdf_attached=kwargs.get("pdf_attached", True),
            )

    def test_uses_foundry_shell_and_links_to_codex(self):
        html = self._html()
        self.assertIn(STARSEED_TILE_URL, html)
        self.assertIn("https://assets.ryozan.app/email/tiger-mark.png", html)
        self.assertIn("Artist Scroll // Filed", html)
        self.assertIn("background-color:#d38a68", html)
        self.assertIn(f"category={OWNER_ARCHIVE_SLUG}", html)
        self.assertIn("https://app.ryozan.app/dashboard?portal=hub&amp;panel=codex", html)

    def test_lists_each_document_state_and_shop_time(self):
        html = self._html()
        self.assertIn("Stored", html)
        self.assertIn("Not provided", html)
        self.assertIn("UPLOAD FAILED: timeout", html)
        self.assertIn("September 29, 2026 at 1:20 PM ET", html)
        self.assertIn("A complete PDF copy is attached", html)

    def test_no_dashes_and_missing_pdf_is_stated(self):
        html = self._html(pdf_attached=False, assets=[])
        self.assertIn("no PDF is attached", html)
        self.assertIn("No documents uploaded", html)
        self.assertNotIn("—", html)
        self.assertNotIn("–", html)


class ScrollPdfBrandTests(unittest.TestCase):
    def setUp(self):
        # Uncompressed streams so the text is assertable; restored so later suites
        # that expect reportlab's default compression are unaffected.
        self.addCleanup(setattr, reportlab.rl_config, "pageCompression", reportlab.rl_config.pageCompression)
        reportlab.rl_config.pageCompression = 0

    def test_pdf_has_brand_chrome_and_keeps_archive_statements(self):
        pdf = build_scroll_pdf(
            ScrollPdfInput(
                artist_name="Sample Artist",
                submitted_at=datetime(2026, 9, 29, 17, 20, 45, tzinfo=UTC),
                fields={"license_number": "SC-1"},
                assets=[
                    ScrollAsset(key="cpr_cert", label="CPR Certificate", provided=False),
                    ScrollAsset(key="govt_id", label="Government ID", stored=False, error="timeout"),
                ],
            )
        )
        text = pdf.decode("latin-1")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIn("RYOZAN STUDIO", text)
        self.assertIn("ARTIST SCROLL // OWNER ARCHIVE COPY", text)
        self.assertIn("PAGE 1", text)
        self.assertIn("September 29, 2026 at 1:20 PM ET", text)
        self.assertIn("Not provided", text)
        self.assertIn("UPLOAD FAILED", text)
        self.assertIn("DOCUMENT NOT STORED", text)
        self.assertIn("NOT CAPTURED", text)
        self.assertNotIn("&mdash;", text)
        self.assertIn("/Subtype /Image", text, "the tiger mark is drawn in the header")


if __name__ == "__main__":
    unittest.main()
