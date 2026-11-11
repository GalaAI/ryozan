"""A check-in waiver linked to a booking is assigned to that booking's artist.

Net-new because the kiosk can be opened on one artist's /checkin link while the verified booking belongs to
another artist. The browser's artist pick must not decide who the waiver is assigned to or who is notified.
Synthetic data only.
"""

import json
import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import documents  # noqa: E402
from app.constants.status import DocStatus  # noqa: E402
from app.models.booking import Booking  # noqa: E402
from app.models.notification import Notification  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.document import DocumentPublicSubmit  # noqa: E402
from app.services import storage  # noqa: E402

# Fake storage settings so the photo ID is a real upload link this server signed (no network, no real bucket).
FAKE_STORAGE = SimpleNamespace(
    R2_ACCOUNT_ID="account-id",
    R2_ACCESS_KEY_ID="access-key",
    R2_SECRET_ACCESS_KEY="secret-key",
    R2_BUCKET_NAME="fake-shop-bucket",
    R2_PUBLIC_DOMAIN="assets.example.test",
)
ID_KEY = "waivers/pending-ids/0b6f3c1e-1111-4222-8333-944445555666.jpg"

ARTIST_A = SimpleNamespace(id=uuid.uuid4(), slug="artist-a", name="Artist A")
ARTIST_B = SimpleNamespace(id=uuid.uuid4(), slug="artist-b", name="Artist B")
BOOKING = SimpleNamespace(id=uuid.uuid4(), artist_id=ARTIST_B.id)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(first=lambda: None, all=lambda: [])


class _Db:
    """Answers the few reads complete_public_form makes; records what it adds."""

    def __init__(self, instance=None):
        self.instance = instance
        self.added: list = []

    async def get(self, model, key):
        if model is Booking and key == BOOKING.id:
            return BOOKING
        if model is User:
            return {ARTIST_A.id: ARTIST_A, ARTIST_B.id: ARTIST_B}.get(key)
        return None

    async def execute(self, statement):
        text = str(statement)
        if "document_instances" in text:
            return _Result(self.instance)
        params = statement.compile().params
        for artist in (ARTIST_A, ARTIST_B):
            if artist.slug in params.values() or artist.name in params.values():
                return _Result(artist)
        return _Result(None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _waiver_instance(stored: dict):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        category="waiver",
        status=DocStatus.SENT,
        expires_at=now + timedelta(hours=1),
        data_json=json.dumps(stored),
        guest_name="Test Client",
        guest_email="client@example.test",
        guest_social=None,
        recipient_name="Test Client",
        recipient_email="client@example.test",
        signed_at=None,
        updated_at=None,
        reminder_sent=False,
    )


def _signed_upload_link() -> str:
    with patch.object(storage, "get_settings", return_value=FAKE_STORAGE):
        return storage.presign_upload_proof_url(ID_KEY)


def _submission(**overrides) -> DocumentPublicSubmit:
    data = {
        "full_name": "Test Client",
        "email": "client@example.test",
        "artist_name": "artist-a",  # the route artist the tablet was opened on
        "artist_id": str(ARTIST_A.id),  # a client-sent id is never trusted
        "tattoo_description": "Small line flower",
        "tattoo_placement": "Left forearm",
        "quoted_price": "250",
        "intoxicated": "No",
        "pregnant": "No",
        "blood_thinners": "No",
        "blood_disorder": "No",
        "skin_condition": "No",
        "health_same_as_last_visit": False,
        "photo_id_url": _signed_upload_link(),
        "health_and_eligibility": True,
        "risks_and_consent": True,
        "accept_terms": True,
    }
    data.update(overrides)
    return DocumentPublicSubmit(data=data, signature_data="data:image/png;base64,AAAA")


class ResolveWaiverArtistTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_booking_artist_wins_over_the_client_pick(self):
        artist = await documents._resolve_waiver_artist(
            _Db(), {"booking_id": str(BOOKING.id), "artist_name": "artist-a"}
        )
        self.assertIs(artist, ARTIST_B)

    async def test_without_a_booking_the_client_pick_is_matched(self):
        self.assertIs(await documents._resolve_waiver_artist(_Db(), {"artist_name": "artist-a"}), ARTIST_A)
        self.assertIsNone(await documents._resolve_waiver_artist(_Db(), {"artist_name": ""}))

    async def test_a_bad_booking_id_falls_back_to_the_pick(self):
        artist = await documents._resolve_waiver_artist(_Db(), {"booking_id": "not-a-uuid", "artist_name": "artist-a"})
        self.assertIs(artist, ARTIST_A)


class CompleteWaiverAssignmentTests(unittest.IsolatedAsyncioTestCase):
    async def _complete(self, stored: dict, body: DocumentPublicSubmit):
        instance = _waiver_instance(stored)
        db = _Db(instance)
        with (
            patch.object(storage, "get_settings", return_value=FAKE_STORAGE),
            patch.object(documents.asyncio, "create_task", lambda coro: coro.close()),
            patch.object(documents, "spawn_background", lambda coro: coro.close()),
        ):
            await documents.complete_public_form("tok", body, db)
        return json.loads(instance.data_json), db

    async def test_a_mismatched_route_artist_is_replaced_by_the_booking_artist(self):
        stored = {"checkin_verified": True, "booking_id": str(BOOKING.id), "artist_name": "artist-b"}
        data, db = await self._complete(stored, _submission())
        self.assertEqual(data["artist_name"], "artist-b")
        self.assertEqual(data["artist_id"], str(ARTIST_B.id))
        self.assertEqual(data["booking_id"], str(BOOKING.id))
        notified = [n.user_id for n in db.added if isinstance(n, Notification)]
        self.assertEqual(notified, [ARTIST_B.id], "only the booking's artist is notified")

    async def test_a_walk_in_is_assigned_to_the_artist_they_picked(self):
        data, db = await self._complete({"checkin_verified": True}, _submission(artist_id=str(ARTIST_B.id)))
        self.assertEqual(data["artist_name"], "artist-a")
        self.assertEqual(data["artist_id"], str(ARTIST_A.id), "the id comes from the matched artist, not the body")
        self.assertEqual([n.user_id for n in db.added if isinstance(n, Notification)], [ARTIST_A.id])


if __name__ == "__main__":
    unittest.main()
