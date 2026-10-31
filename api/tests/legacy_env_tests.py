"""BTTCO-era environment names keep working under their Ryozan replacements."""

import unittest
from unittest.mock import patch

from app.core import legacy_env
from app.core.config import Settings


class LegacyEnvTests(unittest.TestCase):
    def setUp(self):
        legacy_env._warned.clear()

    def test_every_new_name_is_ryozan_and_every_old_name_is_bttco(self):
        for new, old in legacy_env.RENAMED_ENV_VARS.items():
            self.assertIn("RYOZAN", new)
            self.assertIn("BTTCO", old)
            self.assertEqual(new.replace("RYOZAN", "BTTCO"), old)

    def test_getenv_prefers_the_new_name(self):
        env = {"RYOZAN_TEST_DATABASE_URL": "new", "BTTCO_TEST_DATABASE_URL": "old"}
        with patch.dict("os.environ", env, clear=True), self.assertNoLogs("ryozan.config", level="WARNING"):
            self.assertEqual(legacy_env.getenv("RYOZAN_TEST_DATABASE_URL", ""), "new")

    def test_getenv_falls_back_to_the_old_name_and_warns_once(self):
        with (
            patch.dict("os.environ", {"BTTCO_TEST_DATABASE_URL": "old"}, clear=True),
            self.assertLogs("ryozan.config", level="WARNING") as logs,
        ):
            self.assertEqual(legacy_env.getenv("RYOZAN_TEST_DATABASE_URL", ""), "old")
            self.assertEqual(legacy_env.getenv("RYOZAN_TEST_DATABASE_URL", ""), "old")
        self.assertEqual(len(logs.records), 1)
        self.assertIn("BTTCO_TEST_DATABASE_URL is deprecated; set RYOZAN_TEST_DATABASE_URL", logs.output[0])

    def test_getenv_default_when_neither_name_is_set(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(legacy_env.getenv("RYOZAN_E2E_SANDBOX", "0"), "0")

    def test_settings_read_the_legacy_elevenlabs_agent_name_and_warn(self):
        with (
            patch.dict("os.environ", {"ELEVENLABS_BTTCO_AGENT_ID": "agent-old"}, clear=True),
            self.assertLogs("ryozan.config", level="WARNING") as logs,
        ):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.ELEVENLABS_RYOZAN_AGENT_ID, "agent-old")
        self.assertTrue(any("set ELEVENLABS_RYOZAN_AGENT_ID instead" in line for line in logs.output))

    def test_settings_prefer_the_new_elevenlabs_agent_name(self):
        env = {"ELEVENLABS_RYOZAN_AGENT_ID": "agent-new", "ELEVENLABS_BTTCO_AGENT_ID": "agent-old"}
        with patch.dict("os.environ", env, clear=True), self.assertNoLogs("ryozan.config", level="WARNING"):
            settings = Settings(_env_file=None)
        self.assertEqual(settings.ELEVENLABS_RYOZAN_AGENT_ID, "agent-new")


if __name__ == "__main__":
    unittest.main()
