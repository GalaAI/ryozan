"""Ryozan runs one install per shop: shop-specific values come from settings, never code."""

import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ENABLE_API_DOCS", "false")
os.environ.setdefault("ENABLE_CONFIG_HEALTH", "false")

from pydantic import ValidationError  # noqa: E402

from app.api.v1.endpoints import gift_cards  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.services.email_foundry import with_current_email_assets  # noqa: E402


class GiftCardCodePrefixTests(unittest.TestCase):
    def test_default_prefix_is_neutral(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.GIFT_CARD_CODE_PREFIX, "GC")
        with patch.object(gift_cards, "get_settings", return_value=settings):
            code = gift_cards._new_code()
        self.assertRegex(code, r"^GC-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$")

    def test_install_sets_its_own_prefix(self):
        settings = Settings(_env_file=None, GIFT_CARD_CODE_PREFIX=" ex1 ")
        self.assertEqual(settings.GIFT_CARD_CODE_PREFIX, "EX1")
        with patch.object(gift_cards, "get_settings", return_value=settings):
            self.assertTrue(gift_cards._new_code().startswith("EX1-"))

    def test_invalid_prefix_is_rejected(self):
        for bad in ("", "TOO-LONG", "a b", "ABCDEFG"):
            with self.subTest(prefix=bad), self.assertRaises(ValidationError):
                Settings(_env_file=None, GIFT_CARD_CODE_PREFIX=bad)

    def test_no_shop_prefix_is_hard_coded(self):
        source = Path(gift_cards.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'f"BT-', source))


class EmailFoundryAssetScopeTests(unittest.TestCase):
    def test_any_shop_scope_on_any_install_host_is_rewritten(self):
        html = '<img src="https://studio.example.test/dependencies/@exampleshop/email-foundry/1.0.0/assets/starseed-tile.png">'
        out = with_current_email_assets(html)
        self.assertNotIn("@exampleshop", out)
        self.assertIn("starseed-tile.png", out)

    def test_unrelated_images_are_untouched(self):
        html = '<img src="https://studio.example.test/dependencies/@exampleshop/booking-flow/1.0.0/assets/starseed-tile.png">'
        self.assertEqual(with_current_email_assets(html), html)


if __name__ == "__main__":
    unittest.main()
