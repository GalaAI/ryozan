"""Shared judge shop on a demo install.

Pins: the judge owns only an install no one else owns and goes back to a client
when the shop is off; sends never reach a sample address and stop at the daily
caps; the judge cannot write to the guarded screens; a live Stripe key is
dropped; the reset runs once a day at the set hour; the catalog holds one
artist's work and sample clients with sample addresses only.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.services import demo_account, demo_shop  # noqa: E402

SHOP = {
    "DEMO_ACCOUNT_EMAIL": "judge@ryozan.app",
    "DEMO_ACCOUNT_PASSWORD": "shared-demo-password-1",
    "DEMO_SHOP_ENABLED": True,
    "DEMO_SHOP_DAILY_SENDS": 3,
    "DEMO_SHOP_RESET_HOUR_UTC": 9,
}


def _settings(**overrides):
    return get_settings().model_copy(update={**SHOP, **overrides})


class _Db:
    def __init__(self, existing=None, other_owner=None):
        self.existing = existing
        self.other_owner = other_owner
        self.added = []

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.existing)

    async def scalar(self, _statement):
        return self.other_owner

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        pass


class DemoOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def _ensure(self, db, **overrides):
        with patch.object(demo_account, "get_settings", return_value=_settings(**overrides)):
            return await demo_account.ensure_demo_account(db)

    async def test_judge_owns_an_install_no_one_else_owns(self):
        db = _Db()
        self.assertEqual(await self._ensure(db), "created")
        self.assertEqual(db.added[0].role, "founder")

    async def test_another_owner_keeps_the_judge_a_client(self):
        judge = SimpleNamespace(email="judge@ryozan.app", role="collector", password_hash=None, email_verified_at=None)
        outcome = await self._ensure(_Db(judge, other_owner="someone"))
        self.assertIn("no other owner", outcome)
        self.assertEqual(judge.role, "collector")

    async def test_shop_off_returns_the_judge_to_a_client(self):
        judge = SimpleNamespace(
            email="judge@ryozan.app",
            role="founder",
            password_change_method="signup",
            password_hash=hash_password("shared-demo-password-1"),
            email_verified_at=None,
        )
        self.assertEqual(await self._ensure(_Db(judge), DEMO_SHOP_ENABLED=False), "verified")
        self.assertEqual(judge.role, "collector")

    async def test_a_real_owner_at_the_demo_address_is_left_alone(self):
        owner = SimpleNamespace(email="judge@ryozan.app", role="founder", password_change_method="setup")
        self.assertIn("founder", await self._ensure(_Db(owner)))
        self.assertEqual(owner.role, "founder")


class OutboundTests(unittest.TestCase):
    def setUp(self):
        demo_shop._sends.clear()
        patcher = patch.object(demo_shop, "get_settings", return_value=_settings())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sample_addresses_never_receive_mail(self):
        self.assertEqual(demo_shop.demo_outbound_refusal("email", "noor.sample@example.com"), "sample address")
        self.assertEqual(demo_shop.demo_outbound_refusal("email", "x@shop.invalid"), "sample address")

    def test_daily_caps_hold_per_install_and_per_recipient(self):
        with patch.object(demo_shop, "PER_RECIPIENT_DAILY_SENDS", 2):
            self.assertIsNone(demo_shop.demo_outbound_refusal("email", "judge@gmail.com"))
            self.assertIsNone(demo_shop.demo_outbound_refusal("email", "Judge@gmail.com"))
            self.assertIn("recipient", demo_shop.demo_outbound_refusal("email", "judge@gmail.com"))
            self.assertIsNone(demo_shop.demo_outbound_refusal("email", "other@gmail.com"))
            self.assertIn("daily demo send cap", demo_shop.demo_outbound_refusal("email", "third@gmail.com"))
            self.assertIsNone(demo_shop.demo_outbound_refusal("sms", "+15125550100"))

    def test_shop_off_sends_normally(self):
        with patch.object(demo_shop, "get_settings", return_value=_settings(DEMO_SHOP_ENABLED=False)):
            self.assertIsNone(demo_shop.demo_outbound_refusal("email", "noor.sample@example.com"))


class WriteGuardTests(unittest.IsolatedAsyncioTestCase):
    async def _guard(self, method, user, **overrides):
        settings = _settings(**overrides)
        with (
            patch.object(demo_shop, "get_settings", return_value=settings),
            patch.object(demo_account, "get_settings", return_value=settings),
        ):
            await demo_shop.refuse_demo_owner_writes(SimpleNamespace(method=method), user)

    async def test_judge_reads_but_cannot_write(self):
        judge = SimpleNamespace(email="judge@ryozan.app")
        await self._guard("GET", judge)
        with self.assertRaises(HTTPException) as refused:
            await self._guard("PATCH", judge)
        self.assertEqual(refused.exception.status_code, 403)

    async def test_sample_accounts_cannot_write_either(self):
        with self.assertRaises(HTTPException):
            await self._guard("POST", SimpleNamespace(email="sample-collector@example.com"))

    async def test_other_people_and_a_shop_that_is_off_pass(self):
        await self._guard("POST", SimpleNamespace(email="owner@studio.example"))
        await self._guard("POST", None)
        await self._guard("POST", SimpleNamespace(email="judge@ryozan.app"), DEMO_SHOP_ENABLED=False)


class StripeAndScheduleTests(unittest.TestCase):
    def test_live_stripe_key_is_dropped_and_a_test_key_kept(self):
        live = _settings(STRIPE_SECRET_KEY="sk_live_abc")
        with patch.object(demo_shop, "get_settings", return_value=live):
            self.assertTrue(demo_shop.refuse_live_stripe_key())
        self.assertEqual(live.STRIPE_SECRET_KEY, "")
        test = _settings(STRIPE_SECRET_KEY="sk_test_abc")
        with patch.object(demo_shop, "get_settings", return_value=test):
            self.assertFalse(demo_shop.refuse_live_stripe_key())
        self.assertEqual(test.STRIPE_SECRET_KEY, "sk_test_abc")

    def test_reset_runs_at_the_set_hour(self):
        with patch.object(demo_shop, "get_settings", return_value=_settings()):
            before = datetime(2026, 11, 6, 8, 30, tzinfo=UTC)
            after = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
            self.assertEqual(demo_shop.seconds_until_next_reset(before), 30 * 60)
            self.assertEqual(demo_shop.seconds_until_next_reset(after), 24 * 3600)


class CatalogTests(unittest.TestCase):
    def test_one_artist_and_sample_addresses_only(self):
        catalog = demo_shop.load_catalog()
        self.assertEqual(catalog["artist"]["name"], "Justin Walker")
        self.assertTrue(all(demo_shop.is_sample_address(c["email"]) for c in catalog["sample_clients"]))
        self.assertTrue(all("/justin/" in f["image_url"] for f in catalog["flash"]))
        self.assertTrue(all("/justin/" in url for url in catalog["artist"]["portfolio_images"]))
        self.assertNotIn("phone", catalog["artist"])
        codes = [c["reference_code"] for c in catalog["sample_clients"]]
        self.assertEqual(len(codes), len(set(codes)))


if __name__ == "__main__":
    unittest.main()
