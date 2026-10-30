"""Pydantic schemas for notification preferences."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class NotificationPreferencesResponse(BaseModel):
    id: UUID
    user_id: UUID
    email_bookings: bool
    email_messages: bool
    email_quests: bool
    email_marketing: bool
    push_bookings: bool
    push_messages: bool
    push_quests: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class NotificationPreferencesUpdate(BaseModel):
    email_bookings: bool | None = None
    email_messages: bool | None = None
    email_quests: bool | None = None
    email_marketing: bool | None = None
    push_bookings: bool | None = None
    push_messages: bool | None = None
    push_quests: bool | None = None
