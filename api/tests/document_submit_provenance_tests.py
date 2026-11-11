"""
POST /documents/ never lets a caller below admin store (and read back signed) a private object it does not own,
and below admin never creates an artist scroll record or carries a key only submit_scroll writes.

Below admin a reference into our bucket is accepted only under the caller's own folders or as a live upload
link this server issued for waivers/pending-ids/ (storage.authorize_client_references, from #32).

Synthetic fixtures only: no real client, ID or signature data.
"""

import json
import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi import HTTPException

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import documents  # noqa: E402
from app.schemas.document import DocumentInstanceCreate  # noqa: E402
from app.services import storage  # noqa: E402

BUCKET = "fake-shop-bucket"
HOST = "assets.example.test"
NOW = datetime.now(UTC).replace(microsecond=0)
OTHER_USER = uuid.UUID("11111111-2222-4333-8444-555555555555")
VICTIM_KEYS = (
    f"signatures/{OTHER_USER}/artist-sig-{OTHER_USER}.png",
    "waivers/2026-11/Synthetic_WVR-TEST01/Synthetic_WVR-TEST01.pdf",
    "waivers/2026-11/Synthetic_WVR-TEST01/signature.png",
    "waivers/pending-ids/9c1d2e3f-aaaa-4bbb-8ccc-ddddeeeeffff.jpg",
    f"scrolls/{OTHER_USER}/20261110T120000-abcd1234/govt-id.png",
    f"scrolls/shop/{OTHER_USER}/abc123.id.png",
)


def _settings():
    return SimpleNamespace(
        R2_ACCOUNT_ID="account-id",
        R2_ACCESS_KEY_ID="access-key",
        R2_SECRET_ACCESS_KEY="secret-key",
        R2_BUCKET_NAME=BUCKET,
        R2_PUBLIC_DOMAIN=HOST,
    )


def _public(key: str) -> str:
    return f"https://{HOST}/{key}"


def _signed(key: str) -> str:
    """The signed link a caller could hold: the upload link for a pending ID, a viewing link otherwise."""
    with patch.object(storage, "get_settings", return_value=_settings()):
        if key.startswith(storage.UPLOAD_ID_PREFIX):
            return storage.presign_upload_proof_url(key, now=NOW - timedelta(minutes=1))
        return _viewing(key)


def _viewing(key: str) -> str:
    """A staff viewing link (never the upload lifetime, which presign_get_url refuses)."""
    with patch.object(storage, "get_settings", return_value=_settings()):
        return storage.presign_get_url(key, 900, now=NOW - timedelta(minutes=1))


class _Session:
    def __init__(self):
        self.added: list = []

    def add(self, obj):
        obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        return None

    async def refresh(self, _obj):
        return None

    async def execute(self, _statement):
        raise AssertionError("no query expected")


def _user(role: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, name="Synthetic User")


class SubmitDocumentProvenanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(storage, "get_settings", return_value=_settings())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _submit(self, user, *, file_url=None, data=None):
        db = _Session()
        body = DocumentInstanceCreate(
            category="general",
            title="Synthetic form",
            file_url=file_url,
            data_json=json.dumps(data) if data is not None else None,
        )
        response = await documents.submit_document(body=body, db=db, current_user=user)
        return response, db

    async def test_collector_cannot_store_another_persons_private_reference(self):
        collector = _user("collector")
        for key in VICTIM_KEYS:
            for reference in (_public(key), _signed(key)):
                for where in ("file_url", "data_json", "nested data_json"):
                    with self.subTest(key=key, where=where, signed="X-Amz" in reference):
                        db = _Session()
                        kwargs = {
                            "file_url": {"file_url": reference},
                            "data_json": {"data": {"photo": reference}},
                            "nested data_json": {"data": {"answers": [{"files": [reference]}]}},
                        }[where]
                        body = DocumentInstanceCreate(
                            category="general",
                            title="Synthetic form",
                            file_url=kwargs.get("file_url"),
                            data_json=json.dumps(kwargs["data"]) if "data" in kwargs else None,
                        )
                        if key.startswith("waivers/pending-ids/") and "X-Amz" in reference:
                            # A live upload link this server issued is the caller's own upload capability.
                            await documents.submit_document(body=body, db=db, current_user=collector)
                            self.assertNotIn("X-Amz", json.dumps([db.added[0].file_url, db.added[0].data_json]))
                            continue
                        with self.assertRaises(HTTPException) as raised:
                            await documents.submit_document(body=body, db=db, current_user=collector)
                        self.assertEqual(raised.exception.status_code, 422)
                        self.assertIn(raised.exception.detail, ("Upload the file again", "Upload the ID again"))
                        self.assertEqual(db.added, [])

    async def test_collector_own_uploads_and_ordinary_values_still_work(self):
        collector = _user("collector")
        own_key = f"scrolls/{collector.id}/govt-id-license.png"
        public_key = f"documents/{collector.id}/abc.pdf"
        response, db = await self._submit(
            collector,
            file_url=_signed(own_key),
            data={"note": "hello", "doc": _public(public_key), "own": _public(own_key)},
        )
        stored = db.added[0]
        self.assertEqual(stored.file_url, _public(own_key))
        self.assertEqual(json.loads(stored.data_json)["doc"], _public(public_key))
        self.assertEqual(urlsplit(response["file_url"]).path, f"/{BUCKET}/{own_key}")
        self.assertIn("X-Amz-Signature", response["file_url"])

    async def test_collector_own_signature_and_category_id_uploads_are_accepted(self):
        collector = _user("collector")
        for key in (
            f"signatures/{collector.id}/sig.png",
            f"scrolls/shop/{collector.id}/abc123.id.png",
        ):
            with self.subTest(key=key):
                _response, db = await self._submit(collector, file_url=_signed(key), data={"f": _public(key)})
                self.assertEqual(db.added[0].file_url, _public(key))

    async def test_a_live_pending_id_upload_link_is_stored_as_the_stable_reference_a_bare_one_is_not(self):
        key = "waivers/pending-ids/0b6f3c1e-1111-4222-8333-944445555666.jpg"
        _response, db = await self._submit(_user("collector"), file_url=_signed(key))
        self.assertEqual(db.added[0].file_url, _public(key))
        for reference in (_public(key), _viewing(key)):
            with self.subTest(signed="X-Amz" in reference), self.assertRaises(HTTPException) as raised:
                await self._submit(_user("collector"), file_url=reference)
            self.assertEqual(raised.exception.status_code, 422)

    async def test_unparseable_data_json_is_stored_untouched(self):
        db = _Session()
        body = DocumentInstanceCreate(category="general", title="t", data_json="not json")
        await documents.submit_document(body=body, db=db, current_user=_user("collector"))
        self.assertEqual(db.added[0].data_json, "not json")

    async def test_assistants_and_artists_are_held_to_ownership_too(self):
        for role in ("assistant", "artist"):
            for key in VICTIM_KEYS:
                for where in ("file_url", "data_json"):
                    if where == "file_url" and key.startswith("waivers/pending-ids/"):
                        continue  # a live upload link is the caller's own upload capability
                    with self.subTest(role=role, key=key, where=where), self.assertRaises(HTTPException) as raised:
                        if where == "file_url":
                            await self._submit(_user(role), file_url=_signed(key))
                        else:
                            await self._submit(_user(role), data={"nested": [{"doc": _public(key)}]})
                    self.assertEqual(raised.exception.status_code, 422)
        artist = _user("artist")
        own = f"signatures/{artist.id}/sig.png"
        _response, db = await self._submit(artist, file_url=_signed(own))
        self.assertEqual(db.added[0].file_url, _public(own))

    async def test_admin_and_founder_behavior_is_unchanged(self):
        for role in ("admin", "founder"):
            key = VICTIM_KEYS[1]
            with self.subTest(role=role):
                raw = json.dumps({"packet": _public(key)})
                db = _Session()
                body = DocumentInstanceCreate(category="general", title="t", file_url=_signed(key), data_json=raw)
                response = await documents.submit_document(body=body, db=db, current_user=_user(role))
                self.assertEqual(db.added[0].file_url, _public(key))
                self.assertEqual(db.added[0].data_json, raw)
                self.assertIn("X-Amz-Signature", response["file_url"])


SID = "synthetic-victim-submission"
PREFIX = f"scrolls/{OTHER_USER}/20261110T120000-abcd1234"


class CallerReservedMarkerTests(unittest.IsolatedAsyncioTestCase):
    """Below admin, the generic routes never create an artist scroll or carry submit-only markers."""

    async def _post(self, role, **body):
        session = _Session()
        body.setdefault("title", "t")
        with patch.object(storage, "get_settings", return_value=_settings()):
            await documents.submit_document(DocumentInstanceCreate(**body), db=session, current_user=_user(role))
        return session

    async def test_below_admin_cannot_create_a_scroll_record(self):
        for role in ("collector", "assistant", "artist"):
            with self.subTest(role=role):
                with self.assertRaises(HTTPException) as ctx:
                    await self._post(role, category="scroll", data_json="{}")
                self.assertEqual(ctx.exception.status_code, 403)

    async def test_below_admin_cannot_send_reserved_markers(self):
        payloads = (
            {"submission_id": SID},
            {"fields": {"scroll_pdf_url": "https://example.test/x.pdf"}},
            {"items": [{"acceptance_record": {}}]},
            {"storage_prefix": PREFIX},
            {"public_storage_prefix": PREFIX},
            {"certs_stored": {}},
            {"artist_user_id": str(OTHER_USER)},
        )
        for role in ("collector", "assistant", "artist"):
            for data in payloads:
                with self.subTest(role=role, data=data):
                    with self.assertRaises(HTTPException) as ctx:
                        await self._post(role, category="contracts", data_json=json.dumps(data))
                    self.assertEqual(ctx.exception.status_code, 422)
        with self.assertRaises(HTTPException):
            await self._post("collector", category="contracts", data_json='{"submission_id": "x" broken')

    async def test_ordinary_documents_and_admin_writes_still_work(self):
        session = await self._post("collector", category="contracts", data_json=json.dumps({"note": "synthetic"}))
        self.assertEqual(len(session.added), 1)
        session = await self._post("artist", category="contracts", data_json=json.dumps({"note": "synthetic"}))
        self.assertEqual(len(session.added), 1)
        for role in ("admin", "founder"):
            session = await self._post(role, category="scroll", data_json=json.dumps({"submission_id": SID}))
            self.assertEqual(session.added[0].category, "scroll")

    async def test_upload_route_refuses_a_scroll_record_below_admin(self):
        for role in ("collector", "artist"):
            with self.subTest(role=role), self.assertRaises(HTTPException) as ctx:
                await documents.upload_document(
                    file=None, category="scroll", title="t", db=_Session(), current_user=_user(role)
                )
            self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
