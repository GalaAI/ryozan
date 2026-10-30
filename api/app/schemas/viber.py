"""Viber account + connection schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ── Account ──


class ViberAccountRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    plan: str
    is_active: bool
    created_at: datetime


class ViberAccountCreate(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=120)
    plan: str = "founder"


# ── Connection ──


class ViberConnectionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    name: str
    kind: str
    base_url: str
    capability: str
    auth_scheme: str
    status: str
    last_checked_at: datetime | None
    introspection: dict | None
    created_at: datetime


class ViberConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = "bttco_api"
    base_url: str = Field(..., min_length=1, max_length=512)
    capability: str = Field("read", pattern="^(read|read_write)$")
    auth_scheme: str = "workspace_token"
    # The raw secret is accepted at create-time, stored as a reference, never returned.
    auth_token: str | None = None


class ConnectionTestResult(BaseModel):
    connection_id: UUID
    status: str
    healthy: bool
    detail: str
    introspection: dict | None = None


# ── AI provider ──


class ViberAIProviderRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    provider_type: str
    label: str
    base_url: str | None
    default_model: str | None
    is_default: bool
    created_at: datetime


class ViberAIProviderCreate(BaseModel):
    provider_type: str = Field("byok", pattern="^(byok|litellm|galax3)$")
    label: str = "Default"
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = None  # stored as a secret reference, never returned
    is_default: bool = False


# ── Gala + proposals ──


class ViberGalaIntentRequest(BaseModel):
    connection_id: UUID
    intent: str = Field(..., min_length=1, max_length=2000)


class ViberProposalRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    connection_id: UUID
    agent: str
    intent: str
    status: str
    proposal: dict
    approval_notes: str | None
    created_at: datetime
    approved_at: datetime | None
    executed_at: datetime | None


class ViberGalaIntentResponse(BaseModel):
    route: dict
    proposal: ViberProposalRecord
    ai_summary: str | None = None


class ViberProposalApproveRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)
