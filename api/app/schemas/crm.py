"""CRM pipeline task schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CrmPipelineTaskRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    body: str | None
    status: str
    priority: str | None
    booking_id: UUID | None
    assignee_id: UUID | None
    created_at: datetime
    updated_at: datetime
    booking_reference_code: str | None = None
    booking_client_name: str | None = None
    booking_artist_name: str | None = None
    conversation_id: UUID | None = None
    booking_url: str | None = None
    courier_url: str | None = None


class CrmPipelineTaskListResponse(BaseModel):
    tasks: list[CrmPipelineTaskRecord]
    total: int


class CrmPipelineTaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    body: str | None = None
    status: str = "open"
    priority: str | None = None
    booking_id: UUID | None = None
    assignee_id: UUID | None = None
