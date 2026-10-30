"""
Archive schemas — unified view of completed records (waivers, bookings, receipts).

Part of the Vault module. Returns a merged, sortable, searchable list
from documents, bookings, and Stripe payment data.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ArchiveRecord(BaseModel):
    """Single archive entry — could be a waiver, booking, or receipt."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    record_type: str  # waiver | booking | receipt
    client_name: str
    client_email: str | None = None
    artist_name: str | None = None
    date: datetime
    status: str
    summary: str
    original_id: uuid.UUID  # links back to the source record


class ArchiveListResponse(BaseModel):
    records: list[ArchiveRecord]
    total: int
