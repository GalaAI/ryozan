"""Shared collaboration workspaces.

A workspace is a small, project-scoped shared surface (dev tracker, landing
page prep, signup capture, countdown, etc.) that the owner and staff collaborate
on, surfaced in Discord and a Vercel micro-app, backed by this database.

This is the reusable "Ryozan micro-app" core: the dev tracker is workspace #1.
Notes, todos, and log entries are all scoped by workspace_id so the next
micro-app is just another workspace row, not a new schema.

feature_status is intentionally NOT generic — it holds the dev-audit matrix
(DB/API/UI/verdict per feature) which only the dev-tracker workspace uses.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Workspace(Base):
    """A shared project surface. dev-tracker is the first instance."""

    __tablename__ = "ryozan_workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="tracker")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class WorkspaceNote(Base):
    """Free-form shared note in a workspace."""

    __tablename__ = "ryozan_workspace_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    author: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="web")  # web | discord | api
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class WorkspaceTodo(Base):
    """Shared punch-list item."""

    __tablename__ = "ryozan_workspace_todos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(280), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open | done
    assignee: Mapped[str | None] = mapped_column(String(80), nullable=True)
    author: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceLog(Base):
    """Chronological dev journal entry — who did what, when."""

    __tablename__ = "ryozan_workspace_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    author: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    entry: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class WorkspaceChannel(Base):
    """Maps a Discord channel to a workspace, enabling the global Herald bot.

    One bot installed across many servers routes each command to the right
    workspace by the channel it was issued in. Bind once with /bind <slug>.
    """

    __tablename__ = "ryozan_workspace_channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    discord_channel_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    discord_guild_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bound_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class WorkspaceFeatureStatus(Base):
    """Dev-audit matrix row (DB/API/UI/verdict per feature).

    Tracker-specific. Persists the audit so /status reflects DB truth and
    changes can be tracked over time rather than living only in a JSON file.
    """

    __tablename__ = "ryozan_workspace_feature_status"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(120), nullable=False)
    live_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    db_tables: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    api: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ui: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="empty")
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
