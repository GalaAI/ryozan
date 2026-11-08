"""Mods inventory (Ark Mold contract) and the Ark Matrix Mod view.

Net-new file: no test covered a Mods index or the Matrix Mod connection.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api.v1.endpoints import mods as mods_endpoint
from app.services import mods_inventory


class ModsInventoryTests(unittest.TestCase):
    def test_every_tab_has_mods_with_unique_prefixed_codes_in_name_order(self):
        feed = mods_inventory.build_mods_feed()
        self.assertEqual(feed["status"], "live")
        codes = [entry["code"] for entry in feed["entries"]]
        self.assertEqual(len(codes), len(set(codes)))
        for tab in mods_inventory.MOLD_INSTANCE["tabs"]:
            rows = [entry for entry in feed["entries"] if entry["tab"] == tab["id"]]
            self.assertTrue(rows, tab["id"])
            self.assertTrue(all(entry["code"].startswith(f"{tab['prefix']}-") for entry in rows))
            names = [entry["name"].lower() for entry in rows]
            self.assertEqual(names, sorted(names))

    def test_tiers_follow_the_ark_ladder_and_planned_mods_are_marked_planned(self):
        feed = mods_inventory.build_mods_feed()
        self.assertEqual(feed["warnings"], [])
        tiers = {entry["name"]: entry["tier"] for entry in feed["entries"]}
        self.assertTrue(set(tiers.values()) <= set(mods_inventory.TIERS))
        self.assertEqual(tiers["Finance writes"], "planned")
        self.assertEqual(tiers["Ledger"], "scaffolded")
        self.assertIn("Matrix Mod", tiers)

    def test_connection_is_unknown_without_a_setting_and_follows_the_setting_otherwise(self):
        settings = SimpleNamespace(ARK_MATRIX_MOD_URL="http://127.0.0.1:8788/api/eden/matrix-mod")
        with patch.object(mods_inventory, "get_settings", return_value=settings):
            entries = {entry["name"]: entry for entry in mods_inventory.build_mods_feed()["entries"]}
        self.assertIsNone(entries["Viber"]["connected"])
        self.assertTrue(entries["Matrix Mod"]["connected"])

    def test_a_broken_check_reads_as_unknown_never_connected(self):
        broken = mods_inventory.ModDefinition("ark", "Broken", "x", "implemented", "x", connected=lambda: 1 / 0)
        with patch.object(mods_inventory, "MODS", (broken,)):
            feed = mods_inventory.build_mods_feed()
        self.assertIsNone(feed["entries"][0]["connected"])
        self.assertIn("Could not check whether Broken is connected.", feed["warnings"])


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.is_success = 200 <= status < 300

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.is_success:
            raise mods_endpoint.httpx.HTTPStatusError("bad", request=None, response=None)


class FakeClient:
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        FakeClient.calls.append(("GET", url, None))
        return FakeResponse({"matrices": [{"slug": "eden-scenarios"}]})

    async def post(self, url, headers=None, json=None):
        FakeClient.calls.append(("POST", url, json))
        return FakeResponse({"ok": True, "data": {"runs": [{"runId": "run-20261108-a1b2c3", "state": "passed"}]}})


class MatrixModViewTests(unittest.TestCase):
    def _call(self, **settings):
        values = {"ARK_MATRIX_MOD_URL": "", "ARK_MATRIX_MOD_TOKEN": "", **settings}
        FakeClient.calls = []
        with (
            patch.object(mods_endpoint, "get_settings", return_value=SimpleNamespace(**values)),
            patch.object(mods_endpoint.httpx, "AsyncClient", FakeClient),
        ):
            return asyncio.run(mods_endpoint.matrix_mod(None))

    def test_not_connected_without_a_url(self):
        body = self._call()
        self.assertFalse(body["connected"])
        self.assertEqual(FakeClient.calls, [])

    def test_catalog_without_runs_until_a_token_is_set(self):
        body = self._call(ARK_MATRIX_MOD_URL="http://127.0.0.1:8788/api/eden/matrix-mod/")
        self.assertTrue(body["connected"])
        self.assertEqual(body["matrices"], [{"slug": "eden-scenarios"}])
        self.assertIsNone(body["runs"])
        self.assertEqual(FakeClient.calls, [("GET", "http://127.0.0.1:8788/api/eden/matrix-mod", None)])

    def test_runs_are_read_with_the_operator_token(self):
        body = self._call(
            ARK_MATRIX_MOD_URL="http://127.0.0.1:8788/api/eden/matrix-mod", ARK_MATRIX_MOD_TOKEN="test-token"
        )
        self.assertEqual(body["runs"][0]["state"], "passed")
        self.assertEqual(FakeClient.calls[1][2], {"capabilityId": "matrix.eden-scenarios.list_runs", "arguments": {}})


if __name__ == "__main__":
    unittest.main()
