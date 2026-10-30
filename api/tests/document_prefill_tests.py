"""Safe document prefill contract tests.

This is a net-new contract because the existing waiver PDF tests cover printed
records, not which prior values may enter a new consent form. Keeping this as a
small pure-function suite prevents signatures, health answers, IDs, and consent
acknowledgements from being silently reused while contact facts remain useful.
"""

import os
import unittest
import uuid
from types import SimpleNamespace

from fastapi import HTTPException

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints.documents import (  # noqa: E402
    OWNER_ARCHIVE_CATEGORY,
    _build_waiver_contact_prefill,
    _require_document_instance_access,
)


class DocumentPrefillTests(unittest.TestCase):
    def test_reuses_contact_facts_but_not_consent_or_sensitive_visit_data(self) -> None:
        result = _build_waiver_contact_prefill(
            {
                "full_name": "Prior Name",
                "date_of_birth": "1990-01-02",
                "phone": "555-0100",
                "email": "prior@example.test",
                "emergency_contact": "Pat 555-0101",
                "emergency_contact_address": "10 Example Ave",
                "social_handle": "@prior",
                "pregnant": "No",
                "medical_conditions": "Never reuse this",
                "photo_id_url": "https://private.example.test/id.png",
                "signature_data": "data:image/png;base64,secret",
                "risks_and_consent": True,
            },
            {"artist_name": "justin"},
            recipient_name="Current Name",
            recipient_email="current@example.test",
        )

        self.assertEqual(result["full_name"], "Current Name")
        self.assertEqual(result["email"], "current@example.test")
        self.assertEqual(result["phone"], "555-0100")
        self.assertEqual(result["artist_name"], "justin")
        for forbidden in (
            "pregnant",
            "medical_conditions",
            "photo_id_url",
            "signature_data",
            "risks_and_consent",
        ):
            self.assertNotIn(forbidden, result)

    def test_current_contact_values_override_prior_values(self) -> None:
        result = _build_waiver_contact_prefill(
            {"phone": "555-0100", "social_handle": "@old"},
            {"phone": "555-0199", "social_handle": "@new"},
            recipient_name=None,
            recipient_email=None,
        )

        self.assertEqual(result, {"phone": "555-0199", "social_handle": "@new"})


class DocumentAccessTests(unittest.TestCase):
    def test_artist_can_open_an_assigned_waiver_but_not_an_unrelated_one(self) -> None:
        artist_id = uuid.uuid4()
        artist = SimpleNamespace(id=artist_id, role="artist")
        assigned = SimpleNamespace(
            category="waiver",
            user_id=uuid.uuid4(),
            artist_id_reviewed=artist_id,
            data_json="{}",
        )
        unrelated = SimpleNamespace(
            category="waiver",
            user_id=uuid.uuid4(),
            artist_id_reviewed=uuid.uuid4(),
            data_json="{}",
        )

        _require_document_instance_access(assigned, artist)
        with self.assertRaises(HTTPException) as denied:
            _require_document_instance_access(unrelated, artist)
        self.assertEqual(denied.exception.status_code, 403)

    def test_owner_archive_is_founder_only(self) -> None:
        owner_archive = SimpleNamespace(
            category=OWNER_ARCHIVE_CATEGORY,
            user_id=uuid.uuid4(),
            artist_id_reviewed=None,
            data_json="{}",
        )

        _require_document_instance_access(owner_archive, SimpleNamespace(id=uuid.uuid4(), role="founder"))
        with self.assertRaises(HTTPException) as denied:
            _require_document_instance_access(owner_archive, SimpleNamespace(id=owner_archive.user_id, role="artist"))
        self.assertEqual(denied.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
