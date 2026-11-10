"""
Waiver schemas — public (no-auth) tattoo consent waiver submission.

WaiverSubmit captures the 3-step form data from the public /waiver page.
WaiverResponse returns a reference code + confirmation data.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator, model_validator


class WaiverSubmit(BaseModel):
    """All fields from the 3-step public waiver form."""

    # Step 0 — Your Info
    full_name: str
    date_of_birth: str  # YYYY-MM-DD
    phone: str
    email: EmailStr
    emergency_contact: str  # "Name and Phone" combined
    emergency_contact_address: str  # street address of emergency contact
    social_handle: str | None = None  # @instagram etc
    artist_slug: str  # slug from artist select dropdown

    # Step 1 — Tattoo & Health
    tattoo_description: str
    tattoo_placement: str
    pregnant: str  # "Yes" | "No"
    blood_thinners: str | None = None  # "Yes" | "No"; None only from a form that never asked it
    blood_disorder: str  # "Yes" | "No"
    skin_condition: str  # "Yes" | "No"
    allergies: str | None = None
    medical_conditions: str | None = None  # includes current meds
    photo_id_url: str | None = None  # file path after upload

    # Step 2 — Review, Agree & Sign
    health_and_eligibility: bool  # consolidated checkbox
    risks_and_consent: bool  # consolidated checkbox
    accept_terms: bool  # covers T&C + media release
    signature_data: str  # base64 PNG from canvas

    @field_validator("tattoo_placement", "signature_data")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("This field is required")
        return value.strip()

    @model_validator(mode="after")
    def photo_id_required(self):
        if not self.photo_id_url or not self.photo_id_url.strip():
            raise ValueError("Photo ID is required")
        self.photo_id_url = self.photo_id_url.strip()
        return self


class WaiverResponse(BaseModel):
    """Returned after successful waiver submission."""

    id: uuid.UUID
    reference_code: str  # WVR-XXXXXX
    client_name: str
    artist_name: str
    tattoo_description: str
    signed_at: datetime


# ── Retrieval schemas (dashboard, auth required) ──


class WaiverListItem(BaseModel):
    """Summary row for the waivers list view."""

    id: uuid.UUID
    guest_name: str | None = None
    guest_email: str | None = None
    artist_name: str | None = None
    reference_code: str | None = None
    status: str
    signed_at: datetime | None = None
    created_at: datetime


class WaiverListResponse(BaseModel):
    """Paginated list of waivers."""

    waivers: list[WaiverListItem]
    total: int


class WaiverDetailResponse(BaseModel):
    """Full waiver detail with parsed data_json fields."""

    id: uuid.UUID
    reference_code: str
    status: str
    signed_at: datetime | None = None
    # Personal info
    full_name: str
    date_of_birth: str
    phone: str
    email: str
    emergency_contact: str
    emergency_contact_address: str | None = None
    social_handle: str | None = None
    # Tattoo / health
    artist_name: str
    tattoo_description: str
    tattoo_placement: str
    pregnant: str
    blood_disorder: str
    skin_condition: str
    allergies: str | None = None
    medical_conditions: str | None = None
    quoted_price: str | None = None
    # Check-in intake (3 step) additions
    street_address: str | None = None
    session_date: str | None = None
    intoxicated: str | None = None  # "No" is required to file
    blood_thinners: str | None = None
    health_same_as_last_visit: bool | None = None
    checkin_source: str | None = None  # lookup:email | lookup:phone when the contact was code-verified
    photo_id_reused_from: str | None = None  # prior waiver id when the ID on file was reused
    signature_adopted_from: str | None = None  # prior waiver id when the saved signature was adopted
    # Files (Drive URLs)
    photo_id_url: str | None = None
    signature_url: str | None = None
    # Artist counter-signature
    artist_signed_at: datetime | None = None
    artist_reviewed_by: str | None = None  # artist name who countersigned
    # Meta
    notes: str | None = None
    created_at: datetime


class WaiverStatusUpdate(BaseModel):
    """Body for PATCH /waiver/{id}/status."""

    status: str  # approved | flagged | rejected
    notes: str | None = None


class CountersignRequest(BaseModel):
    """Body for POST /waiver/{id}/countersign."""

    signature_data: str  # base64 PNG from canvas
    session_duration_minutes: int | None = None  # how long the session lasted
    client_condition_notes: str | None = None  # post-session skin/client condition
    notes: str | None = None  # general artist notes
