"""
Messaging models — NEW tables for Phase 3.

Conversations can be:
  - 1:1 (two participants)
  - Group (multiple participants, named)
  - Gala-assisted (Gala AI is a participant)

These tables will be created via Alembic migration.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Conversation(Base):
    """A chat thread — 1:1, group, or Gala-assisted."""

    __tablename__ = "ryozan_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # null for 1:1
    is_group: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ryozan_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    participants = relationship("ConversationParticipant", back_populates="conversation")
    messages = relationship("Message", back_populates="conversation")


class ConversationParticipant(Base):
    """Links users to conversations they belong to."""

    __tablename__ = "ryozan_conversation_participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_users.id", ondelete="CASCADE"), nullable=False
    )
    # "gala" as a virtual participant uses a well-known UUID (set in config)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation = relationship("Conversation", back_populates="participants")


class Message(Base):
    """
    A single message in a conversation.

    Messages are either plain text (`message_type='text'`, content-only) or
    typed "artifacts" with a structured JSON payload and mutable state.
    Examples of artifact types handled by app.services.artifacts:

      - quote_proposal: artist-side deposit + total + slot offer
      - slot_proposal:  whitelisted bookable time slots
      - waiver_request: inline waiver sign link
      - aftercare_ack:  "acknowledge you received aftercare" card

    Artifact payloads live in `artifact_payload` (immutable after creation).
    Mutable fields like confirmation status and timestamps live in
    `artifact_state` so the message row is updated in place as the
    client and artist interact with the card.
    """

    __tablename__ = "ryozan_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_conversations.id", ondelete="CASCADE"), nullable=False
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ryozan_users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(50), nullable=False, default="text")
    # Legacy rich-content column — kept for backward compatibility with
    # earlier message types. New artifact types use artifact_payload /
    # artifact_state below.
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Immutable typed payload for artifact messages. Schema depends on
    # message_type and is enforced by app.services.artifacts.
    artifact_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Mutable state for artifact messages (status, timestamps, Stripe
    # session id, etc). Updated in place via the artifact-action endpoint.
    artifact_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation = relationship("Conversation", back_populates="messages")
