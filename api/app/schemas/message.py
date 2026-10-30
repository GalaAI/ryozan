"""
Pydantic schemas for the messaging system.

Covers conversation CRUD, participant management, and message payloads
for both REST and WebSocket transports.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

# ── Conversations ─────────────────────────────────────


class ConversationCreate(BaseModel):
    """Start a new conversation."""

    name: str | None = None  # null = 1:1, set = group
    is_group: bool = False
    participant_ids: list[UUID] = Field(min_length=1)


class ConversationResponse(BaseModel):
    id: UUID
    name: str | None
    is_group: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    participant_count: int = 0
    last_message: Optional["MessageResponse"] = None
    unread_count: int = 0
    is_unread: bool = False
    last_read_at: datetime | None = None
    booking_context: dict[str, Any] | None = None
    channel_context: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int


# ── Participants ──────────────────────────────────────


class ParticipantResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str | None = None
    role: str
    joined_at: datetime
    last_read_at: datetime | None = None

    model_config = {"from_attributes": True}


class ParticipantAdd(BaseModel):
    user_id: UUID
    role: str = "member"


# ── Messages ─────────────────────────────────────────


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    message_type: str = "text"
    metadata_json: str | None = None
    # Optional structured payload for artifact message types. Plain text
    # messages leave this null. Validated against a type-specific schema
    # by app.services.artifacts.validate_payload() on create.
    artifact_payload: dict[str, Any] | None = None


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_id: UUID
    sender_name: str | None = None
    content: str
    message_type: str
    metadata_json: str | None = None
    # Artifact extensions — null for plain text messages, populated for
    # typed artifacts like quote_proposal / slot_proposal.
    artifact_payload: dict[str, Any] | None = None
    artifact_state: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ArtifactActionRequest(BaseModel):
    """
    Body for POST /messages/{id}/artifact-action.
    Mutates the message's artifact_state according to its type handler.
    """

    action: str = Field(min_length=1, max_length=64)
    data: dict[str, Any] | None = None


class ArtifactActionResponse(BaseModel):
    message_id: UUID
    message_type: str
    artifact_state: dict[str, Any]
    updated_at: datetime


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    total: int
    has_more: bool = False


# ── WebSocket payloads ────────────────────────────────


class WSIncoming(BaseModel):
    """What the client sends over WebSocket."""

    action: str  # "send_message", "typing", "mark_read"
    conversation_id: UUID | None = None
    content: str | None = None
    message_type: str = "text"
    metadata_json: str | None = None


class WSOutgoing(BaseModel):
    """What the server pushes to clients."""

    event: str  # "new_message", "typing", "user_joined", "user_left", "error"
    data: dict


# Resolve forward reference
ConversationResponse.model_rebuild()
