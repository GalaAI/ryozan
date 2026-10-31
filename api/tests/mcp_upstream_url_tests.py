"""Upstream URL resolution across the three environments the API runs in.

The MCP router loops back to the same app it is mounted on, so the port must
track whatever uvicorn actually bound. Railway injects PORT, compose overrides
the command to 8801, and the bare Dockerfile CMD uses 8000. A hardcoded port
breaks two of the three silently, with no error until a tool is called.
"""

import unittest
from unittest.mock import patch

from app.core import legacy_env
from app.mcp.upstream import upstream_base_url


class UpstreamUrlTests(unittest.TestCase):
    def test_railway_dynamic_port_is_honoured(self):
        with patch.dict("os.environ", {"PORT": "39471"}, clear=True):
            self.assertEqual(upstream_base_url(), "http://127.0.0.1:39471")

    def test_bare_dockerfile_port_is_honoured(self):
        with patch.dict("os.environ", {"PORT": "8000"}, clear=True):
            self.assertEqual(upstream_base_url(), "http://127.0.0.1:8000")

    def test_compose_falls_back_to_8801(self):
        """docker compose overrides the command and does not set PORT."""
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(upstream_base_url(), "http://127.0.0.1:8801")

    def test_explicit_override_wins_over_port(self):
        env = {"RYOZAN_MCP_UPSTREAM_URL": "http://ryozan-api:9000", "PORT": "8000"}
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(upstream_base_url(), "http://ryozan-api:9000")

    def test_trailing_slash_is_stripped(self):
        with patch.dict("os.environ", {"RYOZAN_MCP_UPSTREAM_URL": "http://x:1/"}, clear=True):
            self.assertEqual(upstream_base_url(), "http://x:1")

    def test_legacy_name_still_works_and_warns_once(self):
        legacy_env._warned.discard("BTTCO_MCP_UPSTREAM_URL")
        with (
            patch.dict("os.environ", {"BTTCO_MCP_UPSTREAM_URL": "http://legacy:9000"}, clear=True),
            self.assertLogs("ryozan.config", level="WARNING") as logs,
        ):
            self.assertEqual(upstream_base_url(), "http://legacy:9000")
            self.assertEqual(upstream_base_url(), "http://legacy:9000")
        self.assertEqual(len(logs.records), 1)
        self.assertIn("set RYOZAN_MCP_UPSTREAM_URL instead", logs.output[0])

    def test_new_name_wins_over_legacy_name(self):
        env = {"RYOZAN_MCP_UPSTREAM_URL": "http://new:1", "BTTCO_MCP_UPSTREAM_URL": "http://old:1"}
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(upstream_base_url(), "http://new:1")


if __name__ == "__main__":
    unittest.main()
