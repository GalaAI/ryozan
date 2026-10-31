"""Viber — the vibe-coder dev cockpit.

Viber is its own product (separate identity) that connects OUT to project
backends as an extension. The first connector is the BTTCO API. These tables
live in nexus alongside BTTCO but model Viber's own accounts and connections,
keeping the standalone-product story clean.

Connections carry a capability gate (read | read_write). Per the Axiom Delivery
Flow canon, write operations must pass approval gates before touching anything
real — the model stores the capability; the API/agent layer enforces the gate.

The original product spec lived with the BTTCO dev tracker and is not part of Ryozan.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ViberAccount(Base):
    """A Viber user. Separate from ryozan_users — Viber is its own product."""

    __tablename__ = "viber_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    plan: Mapped[str] = mapped_column(String(24), nullable=False, default="founder")  # founder | free | pro | team
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ViberConnection(Base):
    """A connection from a Viber account to a project backend (e.g. BTTCO API).

    capability gates what Viber may do through this connection:
      - 'read'        : introspection only (Atlas, Oversight)
      - 'read_write'  : may trigger changes, but every write passes an approval
                        gate before execution (Axiom Delivery Flow canon).
    auth_secret_ref is a vault/secret reference, NOT a raw token, so credentials
    never sit in plaintext in this table.
    """

    __tablename__ = "viber_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="bttco_api")  # bttco_api | rest | fastapi
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    capability: Mapped[str] = mapped_column(String(16), nullable=False, default="read")
    auth_scheme: Mapped[str] = mapped_column(String(32), nullable=False, default="workspace_token")
    auth_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")  # pending | connected | error
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    introspection: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # cached route/model/table map
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ViberAIProvider(Base):
    """How a Viber account's agents reach an LLM.

    provider_type:
      - 'byok'      : user-supplied key (stored as a secret reference)
      - 'litellm'   : route through a LiteLLM proxy at base_url
      - 'galax3'    : the managed GalaX3 shared infra (premium; no key needed)
    """

    __tablename__ = "viber_ai_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    provider_type: Mapped[str] = mapped_column(String(16), nullable=False, default="byok")
    label: Mapped[str] = mapped_column(String(80), nullable=False, default="Default")
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # litellm endpoint
    default_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    key_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ViberProposal(Base):
    """A staged agent proposal that requires founder approval before execution.

    This is separate from Courier message artifacts because Viber proposals target
    connected project code/API changes. The approval gate intentionally stores
    the proposed write plan without executing it.
    """

    __tablename__ = "viber_proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    agent: Mapped[str] = mapped_column(String(32), nullable=False, default="smith")
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="staged")
    proposal: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approval_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
