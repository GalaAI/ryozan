"""
The studio's booking hours are settings the owner edits, read by the slot engine,
the booking checks, the public settings and the agents' wording.
"""

import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from pydantic import ValidationError  # noqa: E402

from app.api.v1.endpoints import bookings as bookings_endpoint  # noqa: E402
from app.api.v1.endpoints import calendar as calendar_endpoint  # noqa: E402
from app.api.v1.endpoints.studio import build_studio_settings  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.services.artist_slugs import unique_artist_slug  # noqa: E402
from app.services.studio_config import describe_hours, validated_changes  # noqa: E402

# 2030-01-07 is a Monday.
MONDAY = date(2030, 1, 7)
TUESDAY = date(2030, 1, 8)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


class ParsingTests(unittest.TestCase):
    def test_the_defaults_are_the_schedule_the_engine_always_had(self):
        settings = _settings()
        self.assertEqual(
            settings.booking_work_schedule,
            {0: None, 1: None, 2: (11, 18), 3: (11, 18), 4: (11, 20), 5: (11, 20), 6: None},
        )
        self.assertEqual(
            settings.booking_consultation_schedule,
            {0: None, 1: (11, 17), 2: (11, 17), 3: (11, 17), 4: (11, 17), 5: (11, 17), 6: None},
        )

    def test_values_are_stored_in_one_form(self):
        settings = _settings(BOOKING_WORK_HOURS=" Monday=9-17 , sun=10-14 ")
        self.assertEqual(settings.BOOKING_WORK_HOURS, "sun=10-14,mon=9-17")
        self.assertEqual(settings.booking_work_schedule[0], (9, 17))
        self.assertEqual(settings.booking_work_schedule[6], (10, 14))
        self.assertIsNone(settings.booking_work_schedule[1])

    def test_blank_closes_every_day(self):
        self.assertEqual(set(_settings(BOOKING_WORK_HOURS="").booking_work_schedule.values()), {None})

    def test_bad_hours_are_refused(self):
        for value in ("someday=11-18", "mon=18-11", "mon=11-11", "mon=11-24", "mon=eleven-six", "mon", "mon=-1-5"):
            with self.subTest(value), self.assertRaises(ValidationError):
                _settings(BOOKING_WORK_HOURS=value)

    def test_a_save_runs_the_same_checks(self):
        settings = _settings()
        self.assertEqual(
            validated_changes(settings, {"BOOKING_CONSULTATION_HOURS": "fri=10-12,mon=9-10"}),
            {"BOOKING_CONSULTATION_HOURS": "mon=9-10,fri=10-12"},
        )


class EngineTests(unittest.TestCase):
    def setUp(self):
        settings = get_settings()
        saved = (settings.BOOKING_WORK_HOURS, settings.BOOKING_CONSULTATION_HOURS)
        self.addCleanup(setattr, settings, "BOOKING_WORK_HOURS", saved[0])
        self.addCleanup(setattr, settings, "BOOKING_CONSULTATION_HOURS", saved[1])
        self.settings = settings

    def test_a_saved_change_reaches_the_slot_engine(self):
        self.settings.BOOKING_WORK_HOURS = "mon=9-11"
        self.settings.BOOKING_CONSULTATION_HOURS = "mon=12-13"
        self.assertEqual(calendar_endpoint._resolve_artist_work_schedule(None)[0], (9, 11))
        self.assertEqual(calendar_endpoint._resolve_artist_work_schedule(None, True)[0], (12, 13))
        slots = calendar_endpoint._generate_available_slots(
            start_date=MONDAY,
            end_date=TUESDAY,
            duration_minutes=60,
            slot_interval_minutes=60,
            buffer_minutes=0,
            minimum_notice_hours=0,
            max_advance_days=100000,
            allow_same_day=True,
            busy_blocks=[],
            confirmed_bookings=[],
            calendar_connected=True,
            limit=20,
        )
        self.assertEqual([slot.time for slot in slots], ["09:00", "10:00"])
        self.assertEqual({slot.date for slot in slots}, {MONDAY.isoformat()})

    def test_an_artists_own_hours_still_win_for_tattoos_only(self):
        self.settings.BOOKING_WORK_HOURS = "mon=9-11"
        self.settings.BOOKING_CONSULTATION_HOURS = "mon=12-13"
        artist = SimpleNamespace(nexus_profile={"working_hours": {"1": [14, 16]}})
        self.assertEqual(calendar_endpoint._resolve_artist_work_schedule(artist)[1], (14, 16))
        self.assertIsNone(calendar_endpoint._resolve_artist_work_schedule(artist)[0])
        self.assertEqual(calendar_endpoint._resolve_artist_work_schedule(artist, True)[0], (12, 13))
        follower = SimpleNamespace(nexus_profile={"working_hours": None})
        self.assertEqual(calendar_endpoint._resolve_artist_work_schedule(follower)[0], (9, 11))

    def test_public_settings_and_the_schedule_route_follow_the_setting(self):
        self.settings.BOOKING_WORK_HOURS = "mon=9-17"
        self.settings.BOOKING_CONSULTATION_HOURS = ""
        hours = build_studio_settings(self.settings).booking_hours
        self.assertEqual(hours.work["0"], [9, 17])
        self.assertIsNone(hours.work["1"])
        self.assertEqual(set(hours.consultation.values()), {None})


class ScheduleRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_schedule_route_lists_the_saved_days(self):
        settings = get_settings()
        self.addCleanup(setattr, settings, "BOOKING_WORK_HOURS", settings.BOOKING_WORK_HOURS)
        settings.BOOKING_WORK_HOURS = "mon=9-17"
        body = await calendar_endpoint.get_schedule(current_user=MagicMock())
        self.assertEqual(body["working_days"], {"monday": {"open": "09:00", "close": "17:00"}})
        self.assertEqual(len(body["off_days"]), 6)

    async def test_the_booking_check_reads_the_schedule_it_is_given(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))
        with (
            patch.object(bookings_endpoint, "_confirmed_bookings_for_range", AsyncMock(return_value=[])),
            patch.object(bookings_endpoint, "_active_holds_for_range", AsyncMock(return_value=[])),
            patch.object(bookings_endpoint, "_personal_blocks_for_range", AsyncMock(return_value=[])),
        ):
            common = {
                "artist_id": MagicMock(),
                "scheduled_date": TUESDAY,
                "scheduled_time": "12:00",
                "duration_minutes": 30,
                "buffer_minutes": 0,
                "minimum_notice_hours": 0,
                "max_advance_days": 100000,
                "allow_same_day": True,
                "busy_blocks": [],
            }
            closed = await bookings_endpoint._schedule_blocking_reasons(db, work_schedule={1: None}, **common)
            opened = await bookings_endpoint._schedule_blocking_reasons(db, work_schedule={1: (11, 17)}, **common)
        self.assertIn("closed_day", closed)
        self.assertNotIn("closed_day", opened)
        self.assertNotIn("outside_work_hours", opened)


class ArtistSlugTests(unittest.IsolatedAsyncioTestCase):
    async def _slug(self, name, taken=()):
        taken = list(taken)
        db = MagicMock()
        db.scalar = AsyncMock(side_effect=lambda query: taken.pop(0) if taken else None)
        return await unique_artist_slug(db, name)

    async def test_a_name_becomes_a_slug(self):
        self.assertEqual(await self._slug("Sam O'Neil-Ray"), "sam-o-neil-ray")

    async def test_a_taken_slug_gets_a_number(self):
        self.assertEqual(await self._slug("Sam", taken=["some-id", "other-id"]), "sam-3")

    async def test_a_name_with_nothing_usable_still_gets_one(self):
        self.assertEqual(await self._slug("***"), "artist")


class WordingTests(unittest.TestCase):
    def test_hours_read_as_plain_words(self):
        self.assertEqual(
            describe_hours(_settings().booking_consultation_schedule),
            "Tuesday through Saturday, 11 AM to 5 PM",
        )
        self.assertEqual(
            describe_hours(_settings().booking_work_schedule),
            "Wednesday through Thursday, 11 AM to 6 PM; Friday through Saturday, 11 AM to 8 PM",
        )
        self.assertEqual(
            describe_hours(_settings(BOOKING_WORK_HOURS="sun=9-12").booking_work_schedule), "Sunday, 9 AM to 12 PM"
        )
        self.assertEqual(describe_hours(_settings(BOOKING_WORK_HOURS="").booking_work_schedule), "closed every day")


if __name__ == "__main__":
    unittest.main()
