"""The shop's own email domain on the platform's Resend account.

Pins: a domain is created through Resend's Domains API and its records kept;
mail moves to the shop's domain only once Resend reports it verified; bad
domains and a second domain are refused; removing deletes it at Resend; the
demo shop never calls Resend; nothing returned carries the platform key.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services import email as email_service  # noqa: E402
from app.services import shop_email_domain  # noqa: E402
from app.services.shop_email_domain import EmailDomainError  # noqa: E402

KEY = "re_platform_key_never_shown"


class _Db:
    def __init__(self):
        self.rows = {}

    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def delete(self, row):
        self.rows.pop(row.key, None)

    async def commit(self):
        pass


def _settings(**overrides):
    base = {
        "RESEND_API_KEY": KEY,
        "RESEND_FROM_EMAIL": "mail@ryozan.app",
        "RESEND_FROM_NAME": "",
        "STUDIO_NAME": "Moth",
    }
    return get_settings().model_copy(update={**base, **overrides})


CREATED = {
    "id": "dom_1",
    "status": "not_started",
    "records": [
        {"record": "DKIM", "type": "TXT", "name": "resend._domainkey", "value": "p=abc", "status": "not_started"}
    ],
}


class ShopEmailDomainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        shop_email_domain._state.clear()
        self.addCleanup(shop_email_domain._state.clear)
        settings = _settings()
        for target in (shop_email_domain, email_service):
            patcher = patch.object(target, "get_settings", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        demo = patch("app.services.demo_shop.demo_shop_enabled", return_value=False)
        demo.start()
        self.addCleanup(demo.stop)

    async def test_add_creates_at_resend_and_waits_for_verification(self):
        resend = AsyncMock(return_value=CREATED)
        with patch.object(shop_email_domain, "_resend", resend):
            view = await shop_email_domain.add_domain(_Db(), "Moth.Studio", "hello")
        resend.assert_awaited_once_with("POST", "/domains", {"name": "moth.studio"})
        self.assertEqual(view["records"][0]["name"], "resend._domainkey")
        self.assertEqual(view["sending_from"], "mail@ryozan.app")
        self.assertIsNone(shop_email_domain.own_sender())
        self.assertNotIn(KEY, str(view))

    async def test_verified_domain_becomes_the_sender(self):
        db = _Db()
        resend = AsyncMock(side_effect=[CREATED, {}, {**CREATED, "status": "verified"}])
        with patch.object(shop_email_domain, "_resend", resend):
            await shop_email_domain.add_domain(db, "moth.studio", "bookings")
            view = await shop_email_domain.check_domain(db)
        self.assertEqual(view["sending_from"], "bookings@moth.studio")
        self.assertEqual(email_service.sender_identity("resend"), ("Moth", "bookings@moth.studio"))
        await shop_email_domain.load(db)
        self.assertEqual(shop_email_domain.own_sender(), "bookings@moth.studio")

    async def test_bad_domain_and_a_second_domain_are_refused(self):
        with self.assertRaises(EmailDomainError):
            await shop_email_domain.add_domain(_Db(), "not a domain")
        with patch.object(shop_email_domain, "_resend", AsyncMock(return_value=CREATED)):
            db = _Db()
            await shop_email_domain.add_domain(db, "moth.studio")
            with self.assertRaises(EmailDomainError) as refused:
                await shop_email_domain.add_domain(db, "other.studio")
        self.assertEqual(refused.exception.status_code, 409)

    async def test_remove_deletes_at_resend_and_falls_back(self):
        db = _Db()
        resend = AsyncMock(side_effect=[CREATED, {}])
        with patch.object(shop_email_domain, "_resend", resend):
            await shop_email_domain.add_domain(db, "moth.studio")
            view = await shop_email_domain.remove_domain(db)
        resend.assert_awaited_with("DELETE", "/domains/dom_1")
        self.assertIsNone(view["domain"])
        self.assertEqual(db.rows, {})

    async def test_demo_shop_never_calls_resend(self):
        resend = AsyncMock()
        with (
            patch("app.services.demo_shop.demo_shop_enabled", return_value=True),
            patch.object(shop_email_domain, "_resend", resend),
        ):
            db = _Db()
            view = await shop_email_domain.add_domain(db, "moth.studio")
            await shop_email_domain.check_domain(db)
            await shop_email_domain.remove_domain(db)
        resend.assert_not_awaited()
        self.assertTrue(view["demo"])
        self.assertTrue(view["records"])
        self.assertIsNone(shop_email_domain.own_sender())


if __name__ == "__main__":
    unittest.main()
