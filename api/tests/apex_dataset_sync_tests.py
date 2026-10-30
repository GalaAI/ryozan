"""Automatic Apex dataset reconciliation tests.

Net-new justification: the existing flash tests cover the founder-triggered
handoff, but no test owned the lifespan worker's changed-manifest and no-cost
behavior. This file stays scoped to that scheduler boundary.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.v1.endpoints import flash


class ApexDatasetSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        flash._last_apex_dataset_digest = None

    async def test_posts_a_changed_manifest_once_without_starting_training(self):
        manifest = {
            "schema_version": "model-feedback/v1",
            "model_id": "sam2-bttco",
            "sample_count": 1,
            "samples": [{"id": "sample-1"}],
        }
        response = SimpleNamespace(
            status_code=201,
            json=lambda: {"data": {"id": "apex.dataset.bttco.fixture"}},
        )
        client = SimpleNamespace(post=AsyncMock(return_value=response))
        context = AsyncMock()
        context.__aenter__.return_value = client
        context.__aexit__.return_value = None
        settings = SimpleNamespace(
            ARK_MODEL_GATEWAY_KEY="gateway-key",
            ARK_MODEL_GATEWAY_BASE_URL="http://ark.test/v1",
        )

        with (
            patch.object(flash, "get_settings", return_value=settings),
            patch.object(flash, "_build_training_manifest", new=AsyncMock(return_value=manifest)),
            patch.object(flash.httpx, "AsyncClient", return_value=context),
        ):
            first = await flash._deliver_training_manifest_to_apex(SimpleNamespace(), force=False)
            second = await flash._deliver_training_manifest_to_apex(SimpleNamespace(), force=False)

        self.assertEqual(first["status"], "snapshot_created")
        self.assertFalse(first["training_started"])
        self.assertEqual(second["status"], "unchanged")
        self.assertFalse(second["training_started"])
        client.post.assert_awaited_once()

    async def test_empty_review_set_never_calls_apex(self):
        settings = SimpleNamespace(
            ARK_MODEL_GATEWAY_KEY="gateway-key",
            ARK_MODEL_GATEWAY_BASE_URL="http://ark.test/v1",
        )
        with (
            patch.object(flash, "get_settings", return_value=settings),
            patch.object(
                flash,
                "_build_training_manifest",
                new=AsyncMock(
                    return_value={
                        "schema_version": "model-feedback/v1",
                        "model_id": "sam2-bttco",
                        "sample_count": 0,
                        "samples": [],
                    }
                ),
            ),
            patch.object(flash.httpx, "AsyncClient") as client,
        ):
            result = await flash._deliver_training_manifest_to_apex(SimpleNamespace(), force=False)

        self.assertEqual(result["status"], "empty")
        self.assertFalse(result["training_started"])
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
