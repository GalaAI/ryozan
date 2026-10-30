"""Workspace collaboration schemas (shared dev-tracker / micro-app surfaces)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ── Workspace ──


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    kind: str
    description: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime


class WorkspaceCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = "tracker"
    description: str | None = None


# ── Notes ──


class NoteRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    author: str
    body: str
    source: str
    created_at: datetime


class NoteCreate(BaseModel):
    body: str = Field(..., min_length=1)
    author: str = "unknown"
    source: str = "web"


# ── Todos ──


class TodoRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    title: str
    status: str
    assignee: str | None
    author: str
    source: str
    created_at: datetime
    done_at: datetime | None


class TodoCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=280)
    assignee: str | None = None
    author: str = "unknown"
    source: str = "web"


class TodoUpdate(BaseModel):
    status: str | None = Field(None, pattern="^(open|done)$")
    title: str | None = Field(None, min_length=1, max_length=280)
    assignee: str | None = None


# ── Log ──


class LogRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    author: str
    entry: str
    source: str
    created_at: datetime


class LogCreate(BaseModel):
    entry: str = Field(..., min_length=1)
    author: str = "unknown"
    source: str = "web"


# ── Feature status (tracker audit matrix) ──


class FeatureStatusRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    domain: str
    live_rows: int
    db_tables: list[str] | None
    api: str | None
    ui: str | None
    status: str
    verdict: str | None
    notes: str | None
    updated_at: datetime


class FeatureStatusUpsert(BaseModel):
    domain: str = Field(..., min_length=1, max_length=120)
    live_rows: int = 0
    db_tables: list[str] | None = None
    api: str | None = None
    ui: str | None = None
    status: str = "empty"
    verdict: str | None = None
    notes: str | None = None


# ── Channel binding (Discord Herald routing) ──


class ChannelBind(BaseModel):
    discord_channel_id: str = Field(..., min_length=1, max_length=32)
    discord_guild_id: str | None = Field(None, max_length=32)
    bound_by: str | None = None


class ChannelRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    discord_channel_id: str
    discord_guild_id: str | None
    bound_by: str | None
    created_at: datetime


class ChannelResolution(BaseModel):
    workspace_slug: str
    workspace_name: str


# ── Aggregate status (the /status digest) ──


class WorkspaceStatus(BaseModel):
    workspace: WorkspaceRecord
    open_todos: int
    done_todos: int
    note_count: int
    log_count: int
    feature_counts: dict[str, int]
    verdict_counts: dict[str, int]
    recent_log: list[LogRecord]
