"""
Document schemas — request/response models for templates and instances.

Matches the frontend api.ts DocumentTemplate / DocumentInstance interfaces.
Includes send/lifecycle schemas for the Document Hub send flow.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ── Templates ──────────────────────────────────────────


class DocumentTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str
    description: str | None = None
    fields_json: str
    is_active: bool
    requires_signature: bool
    created_at: datetime


class DocumentTemplateCreate(BaseModel):
    name: str
    category: str
    description: str | None = None
    fields_json: str = "{}"
    is_active: bool = True
    requires_signature: bool = False


class DocumentTemplateUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    fields_json: str | None = None
    is_active: bool | None = None
    requires_signature: bool | None = None


class DocumentTemplateListResponse(BaseModel):
    templates: list[DocumentTemplateResponse]
    total: int


# ── Instances ──────────────────────────────────────────


class DocumentInstanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_id: uuid.UUID | None = None
    template_name: str | None = None  # joined from template
    user_id: uuid.UUID | None = None
    user_name: str | None = None  # joined from user
    category: str
    title: str
    status: str
    data_json: str | None = None
    signed_at: datetime | None = None
    file_url: str | None = None
    notes: str | None = None
    # Send / lifecycle fields
    send_token: str | None = None
    recipient_email: str | None = None
    recipient_name: str | None = None
    sent_at: datetime | None = None
    sent_by: uuid.UUID | None = None
    viewed_at: datetime | None = None
    reminder_at: datetime | None = None
    reminder_sent: bool = False
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DocumentInstanceCreate(BaseModel):
    template_id: uuid.UUID | None = None
    category: str
    title: str
    data_json: str | None = None
    file_url: str | None = None
    notes: str | None = None


class DocumentStatusUpdate(BaseModel):
    status: str
    notes: str | None = None


class DocumentInstanceListResponse(BaseModel):
    documents: list[DocumentInstanceResponse]
    total: int


# ── Send / Lifecycle ──────────────────────────────────


class DocumentSendRequest(BaseModel):
    """Request to send a document to a recipient via magic link."""

    template_id: uuid.UUID
    recipient_email: EmailStr
    recipient_name: str
    reminder_hours: int = 6  # send reminder after N hours
    expires_hours: int = 72  # expire after N hours


class DocumentSendResponse(BaseModel):
    instance_id: uuid.UUID
    send_token: str
    public_url: str


class DocumentPublicFormResponse(BaseModel):
    """Public-facing form data returned for a magic link token."""

    title: str
    description: str | None = None
    fields: list[dict]
    requires_signature: bool
    recipient_name: str
    expired: bool
    category: str
    initial_values: dict[str, str] = Field(default_factory=dict)
    prefill_notice: str | None = None


class DocumentPublicSubmit(BaseModel):
    """Submitted form data from a public magic link."""

    data: dict
    signature_data: str | None = None  # base64 PNG from SignaturePad
