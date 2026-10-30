"""Pydantic schemas for quote operations."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TimeSlot(BaseModel):
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    duration_minutes: int = Field(ge=15, le=720)


class QuoteCreate(BaseModel):
    """Artist creates a quote for a booking.

    All pricing and scheduling fields are optional so artists (or staff
    on their behalf) can start with a conversational reply before
    committing to numbers.
    """

    booking_id: UUID
    estimated_total_min: float | None = Field(None, ge=0)
    estimated_total_max: float | None = Field(None, ge=0)
    deposit_amount: float = Field(default=0, ge=0)
    available_slots: list[TimeSlot] = Field(default_factory=list)
    artist_message: str | None = None
    expires_in_days: int = Field(default=7, ge=1, le=30)


class QuoteResponse(BaseModel):
    id: UUID
    booking_id: UUID
    token: str
    estimated_total_min: float | None = None
    estimated_total_max: float | None = None
    deposit_amount: float
    available_slots: list[TimeSlot] = []
    artist_message: str | None = None
    status: str
    sent_at: datetime
    viewed_at: datetime | None = None
    accepted_at: datetime | None = None
    expires_at: datetime | None = None
    selected_date: datetime | None = None
    selected_time: str | None = None
    selected_duration: int | None = None

    model_config = {"from_attributes": True}


class QuotePublicView(BaseModel):
    """What the client sees on the public quote page (no internal IDs)."""

    artist_name: str
    artist_avatar: str | None = None
    client_name: str
    description: str
    placement: str | None = None
    size_estimate: str | None = None
    estimated_total_min: float | None = None
    estimated_total_max: float | None = None
    deposit_amount: float
    available_slots: list[TimeSlot]
    artist_message: str | None = None
    status: str
    expired: bool = False
    reference_code: str | None = None
    selected_date: datetime | None = None
    selected_time: str | None = None
    selected_duration: int | None = None


class QuoteAccept(BaseModel):
    """Client accepts a quote by picking a slot."""

    selected_slot_index: int = Field(ge=0)
