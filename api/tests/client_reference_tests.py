"""Every write path that stores client data proves the caller may reference each stored object.

Read paths (document responses, /documents/scroll/my, /scroll/user/{id}, archive views, check-in "on file",
PDF export) sign or fetch the private R2 references they find in stored data. So a client, assistant or
artist must never be able to store a reference to someone else's object: storage.authorize_client_references
accepts a reference to our bucket only as a live upload link this server signed, an object in the caller's
own folder, or a reference the server copied itself in the same request.

Synthetic data only: a fake bucket, example.test hosts and generated PNG bytes.
"""

import json
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException  # noqa: E402
from scroll_owner_archive_tests import _ArtistScrollSession, _data_url, _png_bytes, _R2Stub, _user  # noqa: E402
from starlette.requests import Request  # noqa: E402
from waiver_id_private_tests import (  # noqa: E402
    ASSETS_HOST,
    BUCKET,
    FROZEN,
    PUBLIC_ID_URL,
    VICTIM_KEY,
    VICTIM_PUBLIC_URL,
    _discard,
    _FakeDb,
    _frozen_clock,
    _magic_link_instance,
    _public_waiver_data,
    _ScriptedResult,
    _settings,
    _waiver_body,
)

from app.api.v1.endpoints import documents, waiver  # noqa: E402
from app.models.document import DocumentInstance  # noqa: E402
from app.schemas.document import DocumentInstanceCreate, DocumentStatusUpdate  # noqa: E402
from app.schemas.waiver import CountersignRequest, WaiverStatusUpdate  # noqa: E402
from app.services import storage  # noqa: E402

INLINE_SIG = "data:image/png;base64,iVBORw0KGgo="
OTHER_ID = str(uuid.uuid4())
OTHER_SIGNATURE = f"https://{ASSETS_HOST}/signatures/{OTHER_ID}/artist-sig-{OTHER_ID}.png"
OTHER_SCROLL_ID = f"https://{ASSETS_HOST}/scrolls/{OTHER_ID}/20261110T120000-abcd1234/govt-id.png"
OTHER_DOCUMENT = f"https://{ASSETS_HOST}/documents/{OTHER_ID}/contract.pdf"
WAIVER_PACKET = f"https://{ASSETS_HOST}/waivers/2026-11/Jane_WVR-TEST01/Jane_WVR-TEST01.pdf"
FOREIGN_REFS = (
    VICTIM_PUBLIC_URL,
    f"https://pub-account-id.r2.dev/{BUCKET}/{VICTIM_KEY}",
    f"https://account-id.r2.cloudflarestorage.com/{BUCKET}/{VICTIM_KEY}",
    f"//{ASSETS_HOST}/{VICTIM_KEY}",
    VICTIM_KEY,
    OTHER_SIGNATURE,
    OTHER_SCROLL_ID,
    OTHER_DOCUMENT,
    WAIVER_PACKET,
)


class _Patched(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for patcher in (
            patch.object(storage, "get_settings", return_value=_settings()),
            _frozen_clock(FROZEN),
            patch.object(waiver, "spawn_background", _discard),
            patch.object(documents, "spawn_background", _discard),
            patch.object(waiver, "_upload_waiver_files", AsyncMock()),
            patch.object(documents, "_upload_magic_link_files", AsyncMock()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def upload_link(self, key: str = "waivers/pending-ids/11111111-2222-4333-8444-555555555555.jpg") -> str:
        return storage.presign_get_url(key, 3600)


class AuthorizeClientReferencesTests(_Patched):
    def test_a_foreign_reference_at_any_depth_is_refused_with_its_path(self):
        for ref in FOREIGN_REFS:
            with self.subTest(ref=ref):
                with self.assertRaises(storage.UnauthorizedReferenceError) as caught:
                    storage.authorize_client_references({"a": [{"b": ref}]}, caller=uuid.uuid4())
                self.assertEqual(caught.exception.path, ("a", 0, "b"))

    def test_text_inline_images_and_foreign_hosts_pass_unchanged(self):
        data = {
            "note": "see waivers/ on the desk",
            "sig": INLINE_SIG,
            "site": "https://evil.example.test/waivers/pending-ids/x.jpg",
            "other_bucket": f"https://pub-account-id.r2.dev/other-bucket/{VICTIM_KEY}",
            "n": 3,
            "flag": True,
            "none": None,
        }
        self.assertEqual(storage.authorize_client_references(data), data)

    def test_upload_link_own_folders_and_server_copies_are_accepted(self):
        me = uuid.uuid4()
        link = self.upload_link()
        own = {
            "sig": f"https://{ASSETS_HOST}/signatures/{me}/artist-sig.png",
            "scroll": f"https://{ASSETS_HOST}/scrolls/{me}/20261110T120000-abcd1234/govt-id.png",
            "category_scroll": f"https://{ASSETS_HOST}/scrolls/fire/{me}/doc.pdf",
            "doc": f"https://{ASSETS_HOST}/documents/{me}/contract.pdf",
        }
        out = storage.authorize_client_references(
            {"upload": link, "copied": PUBLIC_ID_URL, **own},
            caller=SimpleNamespace(id=me),
            server_allowed=[PUBLIC_ID_URL],
        )
        self.assertEqual(
            out["upload"], f"https://{ASSETS_HOST}/waivers/pending-ids/11111111-2222-4333-8444-555555555555.jpg"
        )
        self.assertEqual(out["copied"], PUBLIC_ID_URL)
        for name, ref in own.items():
            self.assertEqual(out[name], ref)
        # A signed view link to one's own object is stored as the stable reference.
        signed_own = storage.presign_get_url(f"documents/{me}/contract.pdf", 900)
        self.assertEqual(storage.authorize_client_references(signed_own, caller=me), own["doc"])

    def test_a_public_caller_has_no_folder(self):
        me = uuid.uuid4()
        with self.assertRaises(storage.UnauthorizedReferenceError):
            storage.authorize_client_references(f"https://{ASSETS_HOST}/documents/{me}/x.pdf")


class PublicCompleteTests(_Patched):
    async def _complete(self, data: dict, signature_data: str | None = INLINE_SIG):
        instance = _magic_link_instance()
        before = instance.data_json
        db = _FakeDb(_ScriptedResult(scalar=instance), _ScriptedResult(scalars_first=None))
        body = documents.DocumentPublicSubmit(data=data, signature_data=signature_data)
        try:
            return await documents.complete_public_form("token-1", body, db=db), instance
        except HTTPException:
            self.assertEqual(instance.data_json, before)
            self.assertEqual(instance.status, "sent")
            self.assertEqual(db.added, [])
            raise

    async def test_a_nested_foreign_reference_is_refused_and_nothing_stored(self):
        link = self.upload_link()
        for ref in FOREIGN_REFS:
            for extra in (
                {"answers": {"files": [ref]}},
                {"signature_url": ref},
                {"client_signature": ref},
                {"attachment": ref},
            ):
                with self.subTest(ref=ref, extra=list(extra)):
                    with self.assertRaises(HTTPException) as caught:
                        await self._complete(_public_waiver_data(photo_id_url=link, **extra))
                    self.assertEqual(caught.exception.status_code, 422)
                    self.assertEqual(caught.exception.detail, "Upload the file again")

    async def test_a_stored_object_as_signature_data_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            await self._complete(_public_waiver_data(photo_id_url=self.upload_link()), signature_data=OTHER_SIGNATURE)
        self.assertEqual(caught.exception.status_code, 422)

    async def test_any_uploaded_file_field_is_stored_as_its_reference(self):
        link = self.upload_link()
        _response, instance = await self._complete(_public_waiver_data(photo_id_url=link, extra={"files": [link]}))
        stored = json.loads(instance.data_json)
        expected = f"https://{ASSETS_HOST}/waivers/pending-ids/11111111-2222-4333-8444-555555555555.jpg"
        self.assertEqual(stored["photo_id_url"], expected)
        self.assertEqual(stored["extra"]["files"], [expected])

    async def test_id_reuse_and_adopted_signature_are_copied_server_side(self):
        prior_sig = f"https://{ASSETS_HOST}/waivers/2026-10/Alex_WVR-OLD001/signature.png"
        prior = {
            "id": str(uuid.uuid4()),
            "signed_at": FROZEN,
            "data": {"photo_id_url": PUBLIC_ID_URL, "signature_url": prior_sig},
        }
        with patch.object(documents, "_latest_prior_waiver", AsyncMock(return_value=prior)):
            _response, instance = await self._complete(
                _public_waiver_data(reuse_photo_id=True, reuse_signature=True), signature_data=None
            )
        stored = json.loads(instance.data_json)
        self.assertEqual(stored["photo_id_url"], PUBLIC_ID_URL)
        self.assertEqual(stored["signature_url"], prior_sig)
        self.assertEqual(stored["signature_adopted_from"], prior["id"])

    async def test_waiver_submit_refuses_a_stored_object_in_any_answer(self):
        body = _waiver_body(self.upload_link())
        body.medical_conditions = OTHER_SCROLL_ID
        db = _FakeDb(
            _ScriptedResult(first=(uuid.uuid4(), "Test Artist")),
            _ScriptedResult(scalar=None),
        )
        with self.assertRaises(HTTPException) as caught:
            await waiver.submit_waiver(body, db=db)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(db.added, [])

    async def test_public_checkin_refuses_a_stored_object_as_the_artist(self):
        with self.assertRaises(HTTPException):
            await documents.public_checkin(
                {"name": "A", "email": "a@example.test", "artist_slug": WAIVER_PACKET}, db=_FakeDb()
            )


def _scroll_fields(**extra) -> str:
    return json.dumps(
        {
            "license_number": "SC-TATTOO-0001",
            "terms_version": documents.ARTIST_SCROLL_TERMS_VERSION,
            "section_acceptances": {
                key: {"accepted": True, "legal_name": "Rae Vance"}
                for key in documents.REQUIRED_SCROLL_ACCEPTANCE_SECTIONS
            },
            **extra,
        }
    )


class ScrollSubmitTests(_Patched):
    async def _submit(self, artist, session, fields_json: str):
        with patch.object(documents, "upload_to_blob", _R2Stub()):
            return await documents.submit_scroll(
                request=Request({"type": "http", "headers": []}),
                artist_signature=_data_url(_png_bytes()),
                fields_json=fields_json,
                bbp_cert=None,
                first_aid_cert=None,
                cpr_cert=None,
                govt_id=None,
                db=session,
                current_user=artist,
            )

    async def test_server_owned_keys_are_stripped_and_never_signed_back(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        session = _ArtistScrollSession([], caller=artist)
        planted = {
            "photo_id_url": VICTIM_PUBLIC_URL,
            "govt_id_url": OTHER_SCROLL_ID,
            "photo_id": VICTIM_PUBLIC_URL,
            "bbp_cert_url": OTHER_DOCUMENT,
            "scroll_pdf_url": WAIVER_PACKET,
            "storage_prefix": f"scrolls/{OTHER_ID}/x",
            "certs_stored": {"govt_id": {"url": OTHER_SCROLL_ID}},
        }
        await self._submit(artist, session, _scroll_fields(**planted))
        row = next(r for r in session.added if isinstance(r, DocumentInstance) and r.user_id == artist.id)
        for ref in (VICTIM_KEY, OTHER_ID, "Jane_WVR-TEST01"):
            self.assertNotIn(ref, row.data_json)
        row.created_at = FROZEN
        mine = await documents.get_my_scroll(db=session, current_user=artist)
        dumped = json.dumps(mine)
        self.assertNotIn(VICTIM_KEY, dumped)
        self.assertNotIn(OTHER_ID, dumped)
        self.assertIsNone(mine["scroll"]["govt_id_url"])

    async def test_a_foreign_reference_in_any_other_field_is_refused_before_anything_is_written(self):
        artist = _user("artist", "Rae Vance", "rae@ryozan.test")
        session = _ArtistScrollSession([], caller=artist)
        for ref in FOREIGN_REFS:
            with self.subTest(ref=ref):
                with self.assertRaises(HTTPException) as caught:
                    await self._submit(artist, session, _scroll_fields(notes={"extra": [ref]}))
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(session.added, [])
                self.assertEqual(session.deleted, [])


def _countersign_instance():
    return SimpleNamespace(
        id=uuid.uuid4(),
        category="waiver",
        status="pending_review",
        data_json=json.dumps({"reference_code": "WVR-TEST01"}),
        user_id=None,
        artist_signature=None,
        artist_signed_at=None,
        artist_id_reviewed=None,
        notes=None,
        updated_at=None,
    )


class StaffWritePathTests(_Patched):
    def setUp(self):
        super().setUp()
        for patcher in (
            patch.object(waiver, "_require_waiver_access"),
            patch.object(documents, "_require_document_instance_access"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def staff(self, role: str, **extra):
        return SimpleNamespace(id=uuid.uuid4(), role=role, name="Test Staff", saved_signature_url=None, **extra)

    async def countersign(self, user, signature: str, **extra):
        inst = _countersign_instance()
        db = _FakeDb(_ScriptedResult(scalar=inst))
        result = await waiver.countersign_waiver(
            inst.id, CountersignRequest(signature_data=signature, **extra), db=db, current_user=user
        )
        return result, inst

    async def test_countersign_refuses_any_stored_object_not_the_artists_own(self):
        for role in ("assistant", "artist", "admin", "founder"):
            for ref in (*FOREIGN_REFS, "https://evil.example.test/sig.png", "not a signature"):
                with self.subTest(role=role, ref=ref):
                    inst = _countersign_instance()
                    before = inst.data_json
                    with self.assertRaises(HTTPException) as caught:
                        await waiver.countersign_waiver(
                            inst.id,
                            CountersignRequest(signature_data=ref),
                            db=_FakeDb(_ScriptedResult(scalar=inst)),
                            current_user=self.staff(role),
                        )
                    self.assertEqual(caught.exception.status_code, 422)
                    self.assertEqual(inst.data_json, before)
                    self.assertEqual(inst.status, "pending_review")

    async def test_countersign_accepts_inline_and_the_artists_own_saved_signature(self):
        artist = self.staff("artist")
        saved = f"https://{ASSETS_HOST}/signatures/{artist.id}/artist-sig-{artist.id}.png"
        artist.saved_signature_url = saved
        _result, inst = await self.countersign(artist, INLINE_SIG)
        self.assertEqual(json.loads(inst.data_json)["artist_countersignature"], INLINE_SIG)
        _result, inst = await self.countersign(artist, saved)
        self.assertEqual(inst.artist_signature, saved)
        self.assertEqual(inst.status, "approved")

    async def test_countersign_notes_cannot_carry_a_stored_object(self):
        with self.assertRaises(HTTPException):
            await self.countersign(self.staff("artist"), INLINE_SIG, notes=WAIVER_PACKET)
        with self.assertRaises(HTTPException):
            await self.countersign(self.staff("artist"), INLINE_SIG, client_condition_notes=OTHER_DOCUMENT)

    async def test_assistants_and_artists_are_refused_on_document_create_and_both_status_routes(self):
        for role in ("assistant", "artist"):
            user = self.staff(role)
            for ref in FOREIGN_REFS:
                for body in (
                    DocumentInstanceCreate(category="form", title="T", data_json=json.dumps({"deep": {"x": [ref]}})),
                    DocumentInstanceCreate(category="form", title="T", file_url=ref),
                    DocumentInstanceCreate(category="form", title="T", notes=ref),
                ):
                    with self.subTest(role=role, ref=ref, body=body.model_dump(exclude_none=True)):
                        db = _FakeDb()
                        with self.assertRaises(HTTPException) as caught:
                            await documents.submit_document(body, db=db, current_user=user)
                        self.assertEqual(caught.exception.status_code, 422)
                        self.assertEqual(db.added, [])
                inst = SimpleNamespace(id=uuid.uuid4(), status="submitted", notes=None, category="form")
                with self.subTest(role=role, ref=ref, route="documents status"), self.assertRaises(HTTPException):
                    await documents.update_document_status(
                        inst.id,
                        DocumentStatusUpdate(status="approved", notes=ref),
                        db=_FakeDb(_ScriptedResult(scalar=inst)),
                        current_user=user,
                    )
                self.assertEqual(inst.status, "submitted")
                winst = _countersign_instance()
                with self.subTest(role=role, ref=ref, route="waiver status"), self.assertRaises(HTTPException):
                    await waiver.update_waiver_status(
                        winst.id,
                        WaiverStatusUpdate(status="flagged", notes=ref),
                        db=_FakeDb(_ScriptedResult(scalar=winst)),
                        current_user=user,
                    )
                self.assertEqual(winst.status, "pending_review")

    async def test_an_artist_may_reference_their_own_uploads(self):
        artist = self.staff("artist")
        own = f"https://{ASSETS_HOST}/documents/{artist.id}/contract.pdf"
        db = _FakeDb()
        await documents.submit_document(
            DocumentInstanceCreate(category="form", title="T", file_url=own, data_json=json.dumps({"files": [own]})),
            db=db,
            current_user=artist,
        )
        self.assertEqual(db.added[0].file_url, own)
        self.assertEqual(json.loads(db.added[0].data_json), {"files": [own]})

    async def test_founder_and_admin_keep_storing_any_reference_on_document_create_only(self):
        for role in ("admin", "founder"):
            raw = json.dumps({"photo_id_url": VICTIM_PUBLIC_URL, "deep": [OTHER_SCROLL_ID]})
            db = _FakeDb()
            await documents.submit_document(
                DocumentInstanceCreate(category="form", title="T", data_json=raw, file_url=WAIVER_PACKET),
                db=db,
                current_user=self.staff(role),
            )
            self.assertEqual(db.added[0].data_json, raw)
            self.assertEqual(db.added[0].file_url, WAIVER_PACKET)
            # The skip does not extend to status notes.
            inst = SimpleNamespace(id=uuid.uuid4(), status="submitted", notes=None, category="form")
            with self.assertRaises(HTTPException):
                await documents.update_document_status(
                    inst.id,
                    DocumentStatusUpdate(status="approved", notes=WAIVER_PACKET),
                    db=_FakeDb(_ScriptedResult(scalar=inst)),
                    current_user=self.staff(role),
                )


if __name__ == "__main__":
    unittest.main()
