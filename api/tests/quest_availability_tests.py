"""Clients see only quests this install can complete, and every hook names a seeded slug."""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.api.v1.endpoints import quests

API = Path(__file__).resolve().parents[1]
_MAIN = (API / "main.py").read_text(encoding="utf-8")
_SEED = _MAIN[_MAIN.index("INSERT INTO bttco_quests") : _MAIN.index("ON CONFLICT (slug)", _MAIN.index("INSERT INTO bttco_quests"))]
SEEDED = set(re.findall(r"gen_random_uuid\(\), '([a-z-]+)',", _SEED))


def _settings(**overrides):
    base = {"VAPID_PUBLIC_KEY": "", "RESEND_API_KEY": "", "SENDGRID_API_KEY": "", "SMTP_PASSWORD": ""}
    return SimpleNamespace(**{**base, **overrides})


class QuestAvailabilityTests(unittest.TestCase):
    def test_every_completion_hook_names_a_seeded_quest(self):
        hooked = set()
        for path in (API / "app").rglob("*.py"):
            hooked |= set(re.findall(r'check_and_complete_quest\([^)]*?"([a-z-]+)"\)', path.read_text(encoding="utf-8")))
        self.assertTrue(hooked)
        self.assertEqual(hooked - SEEDED, set())

    def test_demo_without_integrations_lists_only_completable_quests(self):
        with patch.object(quests, "get_settings", return_value=_settings()):
            listed = {slug for slug in SEEDED if quests.quest_available(slug)}
        self.assertEqual(listed, {"first-booking", "profile-pic", "waiver-signed"})

    def test_integration_quests_return_when_configured(self):
        with patch.object(quests, "get_settings", return_value=_settings(VAPID_PUBLIC_KEY="k", RESEND_API_KEY="r")):
            self.assertTrue(quests.quest_available("enable-notifications"))
            self.assertTrue(quests.quest_available("connect-email"))
            self.assertTrue(quests.quest_available("email-alias"))
            self.assertFalse(quests.quest_available("leave-review"))


if __name__ == "__main__":
    unittest.main()
