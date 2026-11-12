"""Vault archive access follows the waiver rule: admins and founders see all, artists only their own.

Synthetic data only. No real client data, names, emails or IDs.
"""

import json
import os
import unittest
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

os.environ.setdefault("ENABLE_API_DOCS", "false")
os.environ.setdefault("ENABLE_CONFIG_HEALTH", "false")

from fastapi import HTTPException  # noqa: E402

from app.api.v1.endpoints import archive  # noqa: E402
from app.constants.status import DocStatus  # noqa: E402
from app.services.scroll_archive import OWNER_ARCHIVE_CATEGORY  # noqa: E402

ASSIGNED_ARTIST = uuid.uuid4()
OTHER_ARTIST = uuid.uuid4()
PHOTO_ID = "https://assets.example.test/waivers/pending-ids/synthetic.jpg"


def _user(role: str, user_id: uuid.UUID | None = None):
    return SimpleNamespace(id=user_id or uuid.uuid4(), role=role, name=f"Test {role}", email=f"{role}@example.test")


def _waiver(**overrides):
    data = {
        "full_name": "Test Client",
        "artist_id": str(ASSIGNED_ARTIST),
        "photo_id_url": PHOTO_ID,
        "tattoo_placement": "forearm",
    }
    values = {
        "id": uuid.uuid4(),
        "category": "waiver",
        "title": "Waiver",
        "status": DocStatus.APPROVED,
        "guest_name": "Test Client",
        "guest_email": "client@example.test",
        "recipient_name": None,
        "recipient_email": None,
        "artist_id_reviewed": None,
        "user_id": None,
        "signed_at": datetime(2026, 11, 10, tzinfo=UTC),
        "created_at": datetime(2026, 11, 10, tzinfo=UTC),
        "data_json": json.dumps(data),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Result:
    def __init__(self, items):
        self._items = items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._items))

    def __iter__(self):
        return iter([])


class _DetailSession:
    """First query returns the document; any other query returns nothing."""

    def __init__(self, doc):
        self._doc = doc
        self._calls = 0

    async def execute(self, _query):
        self._calls += 1
        return _Result([self._doc] if self._calls == 1 else [])


class _ListSession:
    """Returns the given waivers for the waiver query and nothing for the rest."""

    def __init__(self, waivers):
        self._waivers = waivers
        self._calls = 0

    async def execute(self, _query):
        self._calls += 1
        return _Result(self._waivers if self._calls == 1 else [])


class ArchiveDetailAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_unassigned_artist_gets_403_on_a_waiver(self):
        doc = _waiver()
        with self.assertRaises(HTTPException) as ctx:
            await archive.get_archive_detail(
                record_id=doc.id, current_user=_user("artist", OTHER_ARTIST), db=_DetailSession(doc)
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_assigned_artist_still_opens_their_waiver(self):
        doc = _waiver()
        record = await archive.get_archive_detail(
            record_id=doc.id, current_user=_user("artist", ASSIGNED_ARTIST), db=_DetailSession(doc)
        )
        self.assertEqual(record["data"]["photo_id_url"], PHOTO_ID)

    async def test_reviewing_artist_still_opens_the_waiver(self):
        doc = _waiver(artist_id_reviewed=OTHER_ARTIST)
        record = await archive.get_archive_detail(
            record_id=doc.id, current_user=_user("artist", OTHER_ARTIST), db=_DetailSession(doc)
        )
        self.assertEqual(record["type"], "waiver")

    async def test_founder_and_admin_get_the_full_record_with_the_id(self):
        for role in ("founder", "admin"):
            with self.subTest(role=role):
                doc = _waiver()
                record = await archive.get_archive_detail(
                    record_id=doc.id, current_user=_user(role), db=_DetailSession(doc)
                )
                self.assertEqual(record["data"]["photo_id_url"], PHOTO_ID)
                self.assertEqual(record["data"]["full_name"], "Test Client")
                self.assertEqual(record["client_email"], "client@example.test")

    async def test_assistant_and_client_stay_refused(self):
        for role in ("assistant", "collector"):
            with self.subTest(role=role):
                doc = _waiver()
                with self.assertRaises(HTTPException) as ctx:
                    await archive.get_archive_detail(record_id=doc.id, current_user=_user(role), db=_DetailSession(doc))
                self.assertEqual(ctx.exception.status_code, 403)

    async def test_owner_archive_copy_is_founder_only(self):
        doc = _waiver(category=OWNER_ARCHIVE_CATEGORY, status=DocStatus.ARCHIVED)
        for role in ("admin", "artist"):
            with self.subTest(role=role), self.assertRaises(HTTPException) as ctx:
                await archive.get_archive_detail(
                    record_id=doc.id, current_user=_user(role, ASSIGNED_ARTIST), db=_DetailSession(doc)
                )
            self.assertEqual(ctx.exception.status_code, 403)
        record = await archive.get_archive_detail(
            record_id=doc.id, current_user=_user("founder"), db=_DetailSession(doc)
        )
        self.assertEqual(record["type"], OWNER_ARCHIVE_CATEGORY)


class ArchiveListAccessTests(unittest.IsolatedAsyncioTestCase):
    async def _list(self, user, waivers):
        return await archive.list_archive(
            type="waiver",
            search="",
            skip=0,
            limit=25,
            sort_by="date",
            sort_dir="desc",
            current_user=user,
            db=_ListSession(waivers),
        )

    async def test_unassigned_artist_does_not_see_other_artists_waivers(self):
        response = await self._list(_user("artist", OTHER_ARTIST), [_waiver()])
        self.assertEqual(response.total, 0)
        self.assertEqual(response.records, [])

    async def test_assigned_artist_and_founder_see_the_waiver(self):
        for user in (_user("artist", ASSIGNED_ARTIST), _user("founder"), _user("admin")):
            with self.subTest(role=user.role):
                response = await self._list(user, [_waiver()])
                self.assertEqual(response.total, 1)


if __name__ == "__main__":
    unittest.main()
