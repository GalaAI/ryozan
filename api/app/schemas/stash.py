"""Schemas for Hustle Stash inventory endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StashItemBase(BaseModel):
    lane: str = Field(default="flash", pattern="^(flash|crafts|digital)$")
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: str = Field(default="draft", pattern="^(draft|published|archived|sold)$")
    sku: str | None = Field(default=None, max_length=120)
    asset_url: str | None = None
    price: float | None = Field(default=None, ge=0)
    inventory_count: int | None = Field(default=None, ge=0)
    size_label: str | None = Field(default=None, max_length=100)
    estimated_minutes: int | None = Field(default=None, ge=0)
    delivery_kind: str | None = Field(default=None, max_length=50)
    storage_ref: str | None = None
    tags: list[str] | None = None
    metadata: dict | None = None
    ai_metadata: dict | None = None
    gala_sync_enabled: bool = False
    source_flash_design_id: UUID | None = None


class StashItemCreate(StashItemBase):
    artist_id: UUID | None = None


class StashItemUpdate(BaseModel):
    lane: str | None = Field(default=None, pattern="^(flash|crafts|digital)$")
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|published|archived|sold)$")
    sku: str | None = Field(default=None, max_length=120)
    asset_url: str | None = None
    price: float | None = Field(default=None, ge=0)
    inventory_count: int | None = Field(default=None, ge=0)
    size_label: str | None = Field(default=None, max_length=100)
    estimated_minutes: int | None = Field(default=None, ge=0)
    delivery_kind: str | None = Field(default=None, max_length=50)
    storage_ref: str | None = None
    tags: list[str] | None = None
    metadata: dict | None = None
    ai_metadata: dict | None = None
    gala_sync_enabled: bool | None = None
    source_flash_design_id: UUID | None = None


class StashItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    artist_id: UUID
    created_by_id: UUID | None
    source_flash_design_id: UUID | None
    lane: str
    title: str
    description: str | None
    status: str
    sku: str | None
    asset_url: str | None
    price: float | None
    inventory_count: int | None
    size_label: str | None
    estimated_minutes: int | None
    delivery_kind: str | None
    storage_ref: str | None
    tags: list[str] | None
    metadata: dict | None
    ai_metadata: dict | None
    gala_sync_enabled: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StashItemListResponse(BaseModel):
    items: list[StashItemResponse]
    total: int


class CanvasSessionCreate(BaseModel):
    artist_id: UUID | None = None
    sheet_id: UUID | None = None
    lane: str = Field(default="flash", pattern="^(flash|crafts|digital)$")
    status: str = Field(default="draft", pattern="^(draft|segmenting|review|enriched|published|archived)$")
    canvas_state: dict | None = None
    ai_suggestions: dict | None = None
    source_context: dict | None = None
    gala_sync_enabled: bool = False


class CanvasSessionUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(draft|segmenting|review|enriched|published|archived)$")
    canvas_state: dict | None = None
    ai_suggestions: dict | None = None
    source_context: dict | None = None
    gala_sync_enabled: bool | None = None


class CanvasSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    artist_id: UUID
    sheet_id: UUID | None
    created_by_id: UUID | None
    lane: str
    status: str
    canvas_state: dict | None
    ai_suggestions: dict | None
    source_context: dict | None
    gala_sync_enabled: bool
    created_at: datetime
    updated_at: datetime
