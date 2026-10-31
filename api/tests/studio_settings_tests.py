"""Studio-specific rules sit behind settings: off for a new install, unchanged when on.

Each rule below began as one studio's hardcoded workflow. A new install gets
the neutral behaviour; a studio that sets the variable gets the original
behaviour back exactly.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.v1.endpoints import booking_addons, bookings, conversations, dashboard, email_inbound, gala_chat, users
from app.core.config import Settings, get_settings
from app.services import booky_policy, courier_channels
from app.services.artifact_handlers import quote_proposal
from app.services.artifacts import ArtifactActionError

POLICY_DEFAULTS = {
    "STUDIO_OWNER_NAME": "",
    "DEFAULT_ARTIST_SLUG": "",
    "ADMIN_BOOK_ON_BEHALF_ENABLED": False,
    "STAFF_PROXY_ENABLED": False,
    "STAFF_CONFIRM_FOR_CLIENT_ENABLED": False,
    "EXTERNAL_BOOKING_MODES": "",
    "INBOUND_OWNER_ALIASES": "",
    "DEBRIEF_RECIPIENT_USER_ID": "",
    "BOOKY_CANARY_ARTIST_SLUGS": "",
}

# The values the original studio's production sets to keep its behaviour.
ORIGINAL_STUDIO = {
    "STUDIO_OWNER_NAME": "Justin",
    "DEFAULT_ARTIST_SLUG": "justin",
    "ADMIN_BOOK_ON_BEHALF_ENABLED": True,
    "STAFF_PROXY_ENABLED": True,
    "STAFF_CONFIRM_FOR_CLIENT_ENABLED": True,
    "EXTERNAL_BOOKING_MODES": "venue.ink",
    "INBOUND_OWNER_ALIASES": "justinwalker",
    "DEBRIEF_RECIPIENT_USER_ID": "68a8d9d0-940a-46ec-ae73-803d1a0aa093",
    "BOOKY_CANARY_ARTIST_SLUGS": "justin",
    # Owner inbox and receiving domain, once fixed in source, now set here.
    "SHOP_OWNER_EMAIL": "founder@ryozan.app",
    "COURIER_REPLY_DOMAIN": "ryozan.app",
}


def _off(**overrides):
    return get_settings().model_copy(update={**POLICY_DEFAULTS, **overrides})


def _on(**overrides):
    return get_settings().model_copy(update={**ORIGINAL_STUDIO, **overrides})


def _user(role="admin", **fields):
    return SimpleNamespace(id=fields.pop("id", uuid.uuid4()), role=role, name=fields.pop("name", "Staff"), **fields)


class _ScalarResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = values or []

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.values


class DefaultsTests(unittest.TestCase):
    def test_every_policy_setting_defaults_off(self):
        for name, default in POLICY_DEFAULTS.items():
            self.assertEqual(Settings.model_fields[name].default, default, name)

    def test_debrief_recipient_must_be_a_uuid(self):
        with self.assertRaises(ValueError):
            get_settings().model_validate({**get_settings().model_dump(), "DEBRIEF_RECIPIENT_USER_ID": "founder"})


class DefaultArtistTests(unittest.IsolatedAsyncioTestCase):
    def test_any_artist_needs_a_configured_default(self):
        self.assertIsNone(_off().resolve_artist_slug("any"))
        self.assertEqual(_off().resolve_artist_slug("mira"), "mira")
        self.assertEqual(_on().resolve_artist_slug("any"), "justin")
        self.assertEqual(_on().resolve_artist_slug("mira"), "mira")

    async def test_public_booking_for_any_artist_is_refused_without_default(self):
        db = SimpleNamespace(execute=AsyncMock())
        body = SimpleNamespace(artist_slug="any")
        with patch.object(bookings, "get_settings", return_value=_off()), self.assertRaises(HTTPException) as ctx:
            await bookings._do_submit_public_booking(body, db)
        self.assertEqual(ctx.exception.status_code, 400)
        db.execute.assert_not_awaited()

    async def test_public_booking_for_any_artist_uses_default_when_set(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(None)))
        body = SimpleNamespace(artist_slug="any")
        with patch.object(bookings, "get_settings", return_value=_on()), self.assertRaises(HTTPException) as ctx:
            await bookings._do_submit_public_booking(body, db)
        # The lookup ran for the default artist (no row here, so 404).
        self.assertEqual(ctx.exception.status_code, 404)
        statement = str(db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("'justin'", statement)

    async def test_addons_for_any_artist(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(None)))
        with patch.object(booking_addons, "get_settings", return_value=_off()):
            self.assertEqual(await booking_addons.list_public_booking_addons("any", True, db), [])
        db.execute.assert_not_awaited()
        with patch.object(booking_addons, "get_settings", return_value=_on()), self.assertRaises(HTTPException) as ctx:
            await booking_addons.list_public_booking_addons("any", True, db)
        self.assertEqual(ctx.exception.status_code, 404)
        db.execute.assert_awaited()


class AdminBookOnBehalfTests(unittest.IsolatedAsyncioTestCase):
    async def test_off_refuses_before_touching_data(self):
        db = SimpleNamespace(execute=AsyncMock())
        body = SimpleNamespace(artist_id=uuid.uuid4())
        with patch.object(bookings, "get_settings", return_value=_off()), self.assertRaises(HTTPException) as ctx:
            await bookings.staff_create_booking(body, db, _user("admin"))
        self.assertEqual(ctx.exception.status_code, 403)
        db.execute.assert_not_awaited()

    async def test_on_runs_the_existing_flow(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(None)))
        body = SimpleNamespace(artist_id=uuid.uuid4())
        with patch.object(bookings, "get_settings", return_value=_on()), self.assertRaises(HTTPException) as ctx:
            await bookings.staff_create_booking(body, db, _user("admin"))
        self.assertEqual((ctx.exception.status_code, ctx.exception.detail), (404, "Artist not found"))

    async def test_role_check_still_applies_when_on(self):
        with patch.object(bookings, "get_settings", return_value=_on()), self.assertRaises(HTTPException) as ctx:
            await bookings.staff_create_booking(SimpleNamespace(), SimpleNamespace(), _user("artist"))
        self.assertEqual(ctx.exception.status_code, 403)


class StaffProxyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target_id = uuid.uuid4()
        self.operator = _user("assistant", proxy_for=self.target_id)

    def test_off_ignores_a_stored_proxy(self):
        with patch.object(conversations, "get_settings", return_value=_off()):
            self.assertIsNone(conversations._proxy_target(self.operator))
            self.assertEqual(conversations._visible_user_ids(self.operator), [self.operator.id])

    async def test_off_acts_as_self(self):
        db = SimpleNamespace(execute=AsyncMock())
        with patch.object(conversations, "get_settings", return_value=_off()):
            self.assertIs(await conversations._resolve_effective_user(self.operator, db), self.operator)
        db.execute.assert_not_awaited()

    def test_on_sees_target_threads(self):
        with patch.object(conversations, "get_settings", return_value=_on()):
            self.assertEqual(conversations._proxy_target(self.operator), self.target_id)
            self.assertEqual(conversations._visible_user_ids(self.operator), [self.operator.id, self.target_id])

    async def test_on_acts_as_target(self):
        target = _user("founder", id=self.target_id)
        db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(target)))
        with patch.object(conversations, "get_settings", return_value=_on()):
            self.assertIs(await conversations._resolve_effective_user(self.operator, db), target)

    async def test_setting_a_proxy_needs_the_flag(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
        body = users.UserProxyUpdate(proxy_for=self.target_id)
        with patch.object(users, "get_settings", return_value=_off()), self.assertRaises(HTTPException) as ctx:
            await users.set_user_proxy(self.operator.id, body, db, _user("founder"))
        self.assertEqual(ctx.exception.status_code, 403)
        db.execute.assert_not_awaited()

    async def test_clearing_a_proxy_is_always_allowed(self):
        stored = SimpleNamespace(proxy_for=self.target_id)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ScalarResult(stored)), commit=AsyncMock(), refresh=AsyncMock()
        )
        with patch.object(users, "get_settings", return_value=_off()):
            await users.set_user_proxy(self.operator.id, users.UserProxyUpdate(proxy_for=None), db, _user("founder"))
        self.assertIsNone(stored.proxy_for)

    async def test_on_sets_the_proxy(self):
        stored = SimpleNamespace(proxy_for=None)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[_ScalarResult(stored), _ScalarResult(self.target_id)]),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        body = users.UserProxyUpdate(proxy_for=self.target_id)
        with patch.object(users, "get_settings", return_value=_on()):
            await users.set_user_proxy(self.operator.id, body, db, _user("founder"))
        self.assertEqual(stored.proxy_for, self.target_id)


class StaffConfirmForClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client_id = uuid.uuid4()
        self.booking = SimpleNamespace(client_id=self.client_id)
        self.message = SimpleNamespace(artifact_payload={"booking_id": str(uuid.uuid4())}, artifact_state={})

    async def _confirm(self, settings, actor):
        with (
            patch.object(quote_proposal, "get_settings", return_value=settings),
            patch.object(quote_proposal, "_load_booking", AsyncMock(return_value=self.booking)),
            patch.object(quote_proposal, "_apply_confirm", AsyncMock(return_value={"status": "confirmed"})) as confirm,
        ):
            result = await quote_proposal._apply_action(SimpleNamespace(), self.message, "confirm", actor, {})
        return result, confirm

    async def test_off_staff_cannot_confirm_for_client(self):
        with self.assertRaises(ArtifactActionError):
            await self._confirm(_off(), _user("assistant"))

    async def test_off_client_still_confirms(self):
        result, confirm = await self._confirm(_off(), _user("client", id=self.client_id))
        self.assertEqual(result, {"status": "confirmed"})
        confirm.assert_awaited_once()

    async def test_on_staff_confirms_for_client(self):
        result, confirm = await self._confirm(_on(), _user("assistant"))
        self.assertEqual(result, {"status": "confirmed"})
        confirm.assert_awaited_once()

    async def test_non_staff_never_confirms_for_client(self):
        with self.assertRaises(ArtifactActionError):
            await self._confirm(_on(), _user("client"))


class ExternalBookingModeTests(unittest.TestCase):
    def test_off_sends_booking_emails_for_every_mode(self):
        for mode in ("venue.ink", "mailto", "api", None, ""):
            self.assertFalse(_off().is_external_booking_mode(mode), mode)

    def test_on_skips_listed_modes_only(self):
        settings = _on()
        self.assertTrue(settings.is_external_booking_mode("venue.ink"))
        self.assertTrue(settings.is_external_booking_mode(" Venue.Ink "))
        self.assertFalse(settings.is_external_booking_mode("mailto"))
        self.assertFalse(settings.is_external_booking_mode(None))

    def test_several_modes(self):
        settings = _off(EXTERNAL_BOOKING_MODES="venue.ink, other-system")
        self.assertTrue(settings.is_external_booking_mode("other-system"))

    def test_public_booking_uses_the_setting(self):
        source = bookings._do_submit_public_booking.__code__.co_names
        self.assertIn("is_external_booking_mode", source)


class InboundOwnerAliasTests(unittest.TestCase):
    def test_reserved_addresses_route_regardless(self):
        with patch.object(email_inbound, "get_settings", return_value=_off()):
            self.assertTrue(email_inbound._is_owner_address("info"))

    def test_off_has_no_personal_alias(self):
        with patch.object(email_inbound, "get_settings", return_value=_off()):
            self.assertFalse(email_inbound._is_owner_address("justinwalker"))

    def test_on_routes_alias_to_owner(self):
        with patch.object(email_inbound, "get_settings", return_value=_on()):
            self.assertTrue(email_inbound._is_owner_address("justinwalker"))
            self.assertEqual(_on().owner_inbox_email, "founder@ryozan.app")


class DebriefRecipientTests(unittest.IsolatedAsyncioTestCase):
    async def test_off_sends_to_every_founder(self):
        founders = [uuid.uuid4(), uuid.uuid4()]
        db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(values=founders)))
        self.assertEqual(await dashboard.debrief_recipient_ids(db, _off()), founders)
        statement = str(db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("'founder'", statement)

    async def test_on_sends_to_the_configured_account(self):
        db = SimpleNamespace(execute=AsyncMock())
        self.assertEqual(
            await dashboard.debrief_recipient_ids(db, _on()), [uuid.UUID("68a8d9d0-940a-46ec-ae73-803d1a0aa093")]
        )
        db.execute.assert_not_awaited()


class OwnerNameTests(unittest.TestCase):
    def test_debrief_prompt_names_owner_only_when_set(self):
        self.assertEqual(dashboard.debrief_owner_phrase(_off()), "the shop owner")
        self.assertEqual(dashboard.debrief_owner_phrase(_on()), "the shop owner, Justin")

    def test_reply_guard_catches_promises_in_the_owners_name(self):
        reply = "Justin will confirm your slot tomorrow."
        with patch.object(gala_chat, "get_settings", return_value=_off()):
            self.assertIsNone(gala_chat._unreceipted_action_claim(reply, set()))
            self.assertEqual(gala_chat._unreceipted_action_claim("The owner will confirm it.", set()), "future_action")
        with patch.object(gala_chat, "get_settings", return_value=_on()):
            self.assertEqual(gala_chat._unreceipted_action_claim(reply, set()), "future_action")

    def test_courier_assistant_label(self):
        def label(settings):
            catalog = courier_channels.build_tool_catalog(settings)
            return next(item["label"] for item in catalog if item["id"] == "justin_personal_open_voice")

        self.assertEqual(label(_off()), "Owner personal open-source voice")
        self.assertEqual(label(_on()), "Justin personal open-source voice")


class BookyCanaryTests(unittest.TestCase):
    def test_no_canary_artist_by_default(self):
        artist = SimpleNamespace(slug="justin")
        self.assertFalse(booky_policy.artist_canary_allowed(_off(), artist))
        self.assertTrue(booky_policy.artist_canary_allowed(_on(), artist))


if __name__ == "__main__":
    unittest.main()
