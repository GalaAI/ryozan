"""The shop's texting line: a Twilio sub-account on the platform, or the shop's own Twilio.

Pins: the platform line opens a sub-account, buys a toll-free number pointed at
the inbound webhook, submits the toll-free verification, and sends only once
Twilio approves it; tokens are sealed and never described; the shop's own
Twilio is checked before it is kept; texts then go out with the shop's
account and number; its webhooks verify with its token; the demo shop never
calls Twilio and takes no credentials; disconnecting releases the number.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import hashlib  # noqa: E402
import hmac  # noqa: E402
import unittest  # noqa: E402
from base64 import b64encode  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from app.api.v1.endpoints import sms as sms_endpoints  # noqa: E402
from app.core import secret_box  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services import shop_texts, sms  # noqa: E402
from app.services.shop_texts import TextsError  # noqa: E402

SUB_TOKEN = "sub-token-0123456789abcdef"
SUB = {"sid": "AC" + "1" * 32, "auth_token": SUB_TOKEN}
NUMBER = {"phone_number": "+18005550123", "sid": "PN" + "2" * 32}
FORM = {
    "business_name": "Moth and Rose Tattoo",
    "business_type": "PRIVATE_PROFIT",
    "ein": "12-3456789",
    "website": "https://moth.example",
    "notification_email": "owner@moth.example",
    "contact_first_name": "Rae",
    "contact_last_name": "Owner",
    "contact_phone": "+15125550100",
    "street": "1 Main St",
    "city": "Austin",
    "state": "TX",
    "postal_code": "78701",
    "use_case": "ACCOUNT_NOTIFICATIONS",
    "use_case_summary": "Booking confirmations and appointment reminders for tattoo clients.",
    "message_sample": "Moth and Rose: your session is confirmed for Friday at 2 PM. Reply STOP to opt out.",
    "opt_in_type": "WEB_FORM",
    "opt_in_image_url": "https://moth.example/sms-opt-in.png",
    "volume": "1,000",
}


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
        "TWILIO_ACCOUNT_SID": "AC" + "9" * 32,
        "TWILIO_AUTH_TOKEN": "platform-token-never-shown",
        "TWILIO_WEBHOOK_AUTH_TOKEN": "",
        "SECRET_KEY": "test-secret-key-long-enough-to-derive",
        "API_PUBLIC_BASE_URL": "https://api.moth.example",
        "STUDIO_NAME": "Moth and Rose",
    }
    return get_settings().model_copy(update={**base, **overrides})


class ShopTextsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        shop_texts._state.clear()
        self.addCleanup(shop_texts._state.clear)
        settings = _settings()
        for target in (shop_texts, secret_box, sms, sms_endpoints):
            patcher = patch.object(target, "get_settings", return_value=settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.demo = patch("app.services.demo_shop.demo_shop_enabled", return_value=False)
        self.demo.start()
        self.addCleanup(self.demo.stop)

    async def _platform_line(self, db, *, approved):
        verification = {"sid": "HH" + "3" * 32, "status": "PENDING_REVIEW"}
        twilio = AsyncMock(
            side_effect=[
                SUB,
                NUMBER,
                verification,
                {**verification, "status": "TWILIO_APPROVED" if approved else "IN_REVIEW"},
            ]
        )
        with patch.object(shop_texts, "_twilio", twilio):
            await shop_texts.start_platform(db)
            await shop_texts.buy_number(db, "+18005550123")
            await shop_texts.submit_verification(db, FORM)
            view = await shop_texts.check(db)
        return twilio, view

    async def test_platform_line_sends_only_once_verification_is_approved(self):
        db = _Db()
        twilio, view = await self._platform_line(db, approved=False)
        create, buy, submit, _ = twilio.await_args_list
        self.assertEqual(create.args[1], "https://api.twilio.com/2010-04-01/Accounts.json")
        self.assertEqual(create.args[3], {"FriendlyName": "Moth and Rose"})
        self.assertEqual(buy.args[3]["SmsUrl"], "https://api.moth.example/api/v1/sms/webhook/inbound")
        self.assertEqual(buy.args[2], (SUB["sid"], SUB_TOKEN))
        self.assertEqual(submit.args[3]["TollfreePhoneNumberSid"], NUMBER["sid"])
        self.assertEqual(submit.args[3]["BusinessRegistrationAuthority"], "EIN")
        self.assertEqual(view["verification_status"], "IN_REVIEW")
        self.assertFalse(view["sending_ready"])
        self.assertIsNone(shop_texts.shop_sender())
        self.assertNotIn(SUB_TOKEN, str(view))
        self.assertNotIn(SUB_TOKEN, db.rows["texts_connection"].value)

    async def test_approved_line_sends_with_the_shop_account_and_number(self):
        await self._platform_line(_Db(), approved=True)
        sender = shop_texts.shop_sender()
        self.assertEqual(
            (sender.account_sid, sender.auth_token, sender.from_number), (SUB["sid"], SUB_TOKEN, "+18005550123")
        )

        post = AsyncMock(return_value=SimpleNamespace(status_code=201, json=lambda: {"sid": "SM1"}))
        client = SimpleNamespace(post=post)
        context = MagicMock()
        context.__aenter__.return_value = client
        with patch.object(sms.httpx, "AsyncClient", return_value=context):
            await sms._twilio_post_message(to="+15125550111", body="Your session is confirmed.")
        self.assertIn(f"/Accounts/{SUB['sid']}/Messages.json", post.await_args.args[0])
        self.assertEqual(post.await_args.kwargs["data"]["From"], "+18005550123")
        self.assertNotIn("MessagingServiceSid", post.await_args.kwargs["data"])
        self.assertEqual(post.await_args.kwargs["auth"], (SUB["sid"], SUB_TOKEN))
        self.assertEqual(sms.resolve_sms_lane_by_number("+18005550123").id, "bella")

    async def test_webhook_signed_with_the_shop_token_is_accepted(self):
        await self._platform_line(_Db(), approved=True)
        form = {"From": "+15125550111", "Body": "hi"}
        url = "https://api.moth.example/api/v1/sms/webhook/inbound"
        payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
        signature = b64encode(hmac.new(SUB_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()
        request = SimpleNamespace(
            headers={"X-Twilio-Signature": signature}, url=SimpleNamespace(path="/api/v1/sms/webhook/inbound", query="")
        )
        self.assertTrue(sms_endpoints._validate_twilio_signature(request, form))
        request.headers = {"X-Twilio-Signature": "forged"}
        self.assertFalse(sms_endpoints._validate_twilio_signature(request, form))

    async def test_own_twilio_is_checked_then_kept_sealed(self):
        db = _Db()
        twilio = AsyncMock(return_value={"sid": "AC" + "4" * 32})
        with patch.object(shop_texts, "_twilio", twilio):
            view = await shop_texts.connect_own(db, "AC" + "4" * 32, "own-token-0123456789", "+15125550123")
        twilio.assert_awaited_once()
        self.assertEqual(twilio.await_args.args[2], ("AC" + "4" * 32, "own-token-0123456789"))
        self.assertTrue(view["sending_ready"])
        self.assertNotIn("own-token-0123456789", db.rows["texts_connection"].value)
        self.assertEqual(shop_texts.shop_sender().auth_token, "own-token-0123456789")
        with self.assertRaises(TextsError):
            await shop_texts.connect_own(db, "AC" + "5" * 32, "another-token-012345", "+15125550124")

    async def test_bad_own_credentials_are_refused_before_twilio(self):
        twilio = AsyncMock()
        with patch.object(shop_texts, "_twilio", twilio), self.assertRaises(TextsError):
            await shop_texts.connect_own(_Db(), "not-a-sid", "short", "5125550123")
        twilio.assert_not_awaited()

    async def test_disconnect_releases_the_platform_number(self):
        db = _Db()
        await self._platform_line(db, approved=True)
        release = AsyncMock(return_value={})
        with patch.object(shop_texts, "_twilio", release):
            view = await shop_texts.disconnect(db)
        self.assertEqual(release.await_args.args[0], "DELETE")
        self.assertTrue(release.await_args.args[1].endswith(f"/IncomingPhoneNumbers/{NUMBER['sid']}.json"))
        self.assertIsNone(view["mode"])
        self.assertEqual(db.rows, {})

    async def test_demo_shop_never_calls_twilio_and_takes_no_credentials(self):
        twilio = AsyncMock()
        with (
            patch("app.services.demo_shop.demo_shop_enabled", return_value=True),
            patch.object(shop_texts, "_twilio", twilio),
        ):
            db = _Db()
            await shop_texts.start_platform(db)
            numbers = await shop_texts.find_numbers()
            await shop_texts.buy_number(db, numbers[0]["phone_number"])
            view = await shop_texts.submit_verification(db, FORM)
            await shop_texts.check(db)
            with self.assertRaises(TextsError) as refused:
                await shop_texts.connect_own(db, "AC" + "4" * 32, "own-token-0123456789", "+15125550123")
            await shop_texts.disconnect(db)
        twilio.assert_not_awaited()
        self.assertEqual(refused.exception.status_code, 403)
        self.assertTrue(view["demo"])
        self.assertEqual(view["verification_status"], "PENDING_REVIEW")
        self.assertFalse(view["sending_ready"])


class SecretBoxTests(unittest.TestCase):
    def test_seal_round_trips_and_a_new_key_cannot_open(self):
        with patch.object(secret_box, "get_settings", return_value=_settings()):
            sealed = secret_box.seal("token-value")
            self.assertNotIn("token-value", sealed)
            self.assertEqual(secret_box.open_sealed(sealed), "token-value")
        with patch.object(secret_box, "get_settings", return_value=_settings(SECRET_KEY="another-secret-key-entirely")):
            self.assertIsNone(secret_box.open_sealed(sealed))


if __name__ == "__main__":
    unittest.main()
