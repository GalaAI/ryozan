"""Schemas for private contact and intake exports."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ContactRecord(BaseModel):
    id: str
    name: str
    email: str | None = None
    phone: str | None = None
    source: str
    lifecycle_stage: str
    booking_count: int = 0
    last_booking_at: datetime | None = None
    last_booking_status: str | None = None
    marketing_opt_in: bool = False
    tags: list[str] = []


class ContactListResponse(BaseModel):
    contacts: list[ContactRecord]
    total: int


class ContactIntakeRecord(BaseModel):
    source_system: str
    source_type: str
    source_ref: str
    contact_id: str
    occurred_at: datetime
    payload: dict[str, Any]


class ContactExportResponse(BaseModel):
    contacts: list[ContactRecord]
    intake_items: list[ContactIntakeRecord]
    total_contacts: int
    total_intake_items: int
