"""Evidence-backed Ryozan email manifest classification tests.

The existing catalog tests prove individual renderers. This separate contract
proves the cross-renderer inventory and receipt classification consumed by Ark.
"""

import asyncio
import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from app.api.v1.endpoints.email_log import (
    _asset_manifest,
    _build_email_type_manifest,
    _provider_manifest,
    _source_email_definitions,
)
from app.mcp import tools


class EmailManifestTests(unittest.TestCase):
    def test_source_inventory_contains_eleven_foundry_and_thirty_two_legacy_types(self):
        # The eleventh Foundry design is deposit_artist_notification. The shop's
        # own deposit notice previously had no Foundry design at all and fell
        # through to a legacy database row, which is where its amount was lost.
        definitions = _source_email_definitions()

        self.assertEqual(len(definitions), 44)
        self.assertEqual(sum(row["family"] == "foundry" for row in definitions), 11)
        self.assertEqual(sum(row["family"] == "legacy" for row in definitions), 33)
        self.assertIn(
            "deposit_artist_notification",
            {row["key"] for row in definitions if row["family"] == "foundry"},
        )

    def test_receipts_distinguish_foundry_live_legacy_live_and_unattributed(self):
        now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
        manifest = _build_email_type_manifest(
            [
                {
                    "template": "general",
                    "status": "delivered",
                    "subject": "We got your request — Ryozan Studio",
                    "created_at": now,
                },
                {
                    "template": "general",
                    "status": "sent",
                    "subject": "Reset your Ryozan Studio password",
                    "created_at": now,
                },
                {
                    "template": "unmapped-provider-tag",
                    "status": "sent",
                    "subject": "A subject with no renderer contract",
                    "created_at": now,
                },
            ],
            [],
        )
        by_id = {row.id: row for row in manifest}

        self.assertEqual(by_id["foundry.booking_confirmation"].runtime_status, "live")
        self.assertTrue(by_id["foundry.booking_confirmation"].is_live)
        self.assertEqual(by_id["legacy.password_reset"].runtime_status, "legacy-live")
        self.assertTrue(by_id["legacy.password_reset"].is_live)
        self.assertEqual(by_id["observed.unmapped_provider_tag"].runtime_status, "unknown")
        self.assertFalse(by_id["observed.unmapped_provider_tag"].is_live)

    def test_manual_foundry_is_available_while_unproven_automatic_is_not_live(self):
        by_id = {row.id: row for row in _build_email_type_manifest([], [])}

        self.assertEqual(by_id["foundry.flash_drop"].runtime_status, "available")
        self.assertTrue(by_id["foundry.flash_drop"].manual_available)
        self.assertEqual(by_id["foundry.appointment_reminder"].runtime_status, "not-live")
        self.assertEqual(by_id["legacy.booking_request"].replacement_id, "foundry.booking_confirmation")
        self.assertEqual(by_id["legacy.booking_request"].runtime_status, "not-live")

    def test_active_database_template_without_receipt_is_available(self):
        manifest = _build_email_type_manifest(
            [],
            [
                {
                    "slug": "re_engagement_v4",
                    "name": "Re-engagement v4",
                    "category": "marketing",
                    "subject": "We saved your place",
                    "is_active": True,
                }
            ],
        )
        by_id = {row.id: row for row in manifest}

        self.assertEqual(by_id["database.re_engagement_v4"].runtime_status, "available")
        self.assertFalse(by_id["database.re_engagement_v4"].is_live)

    def test_manifest_assets_are_stable_r2_urls(self):
        assets = _asset_manifest()

        self.assertGreaterEqual(len(assets), 2)
        self.assertTrue(all(asset.url.startswith("https://assets.ryozan.app/email/") for asset in assets))
        self.assertEqual({asset.kind for asset in assets}, {"logo", "background"})

    def test_sms_and_whatsapp_rows_follow_courier_lane_readiness(self):
        channels = {
            "channels": [
                {"id": "sms", "configured": True, "connected": True},
                {"id": "whatsapp", "configured": True, "connected": False},
                {"id": "email", "configured": True, "connected": True},
            ]
        }
        with patch("app.api.v1.endpoints.email_log.build_channel_status", return_value=channels):
            rows = {row.id: row for row in _provider_manifest()}

        self.assertEqual((rows["twilio"].channel, rows["twilio"].state), ("sms", "configured"))
        self.assertTrue(rows["twilio"].selected_by_policy)
        self.assertEqual(rows["twilio-whatsapp"].channel, "whatsapp")
        self.assertEqual(rows["twilio-whatsapp"].state, "configured")
        self.assertFalse(rows["twilio-whatsapp"].selected_by_policy)

    def test_mcp_tool_returns_structured_manifest_without_write(self):
        payload = {"version": "1.0", "tenant_id": "bttco", "email_types": []}

        async def fake_get_json(path, token, params=None):
            self.assertEqual(path, "/email/manifest")
            self.assertEqual(token, "site-token")
            self.assertEqual(params, {"days": 45})
            return payload

        with patch.object(tools, "get_json", fake_get_json):
            body = asyncio.run(tools.tool_messaging_manifest("site-token", {"days": 45}))

        self.assertEqual(json.loads(body), payload)


if __name__ == "__main__":
    unittest.main()
