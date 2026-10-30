"""Pydantic schemas for AI memories."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MemoryCreate(BaseModel):
    agent: str = "gala"
    category: str
    key: str
    value: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = "manual"


class MemoryUpdate(BaseModel):
    value: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: str | None = None
    expires_at: datetime | None = None


class MemoryResponse(BaseModel):
    id: UUID
    user_id: UUID
    agent: str
    category: str
    key: str
    value: str
    confidence: float
    source: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None

    class Config:
        from_attributes = True


class MemoryListResponse(BaseModel):
    memories: list[MemoryResponse]
    total: int


class MemoryContext(BaseModel):
    """Formatted memory context for agent system prompts."""

    user_id: UUID
    agent: str
    context_text: str
    memory_count: int
