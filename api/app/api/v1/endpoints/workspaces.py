"""Workspace collaboration endpoints.

Shared dev-collab surfaces (dev tracker, landing prep, signup, countdown).
Backs both the web tracker and the Discord dev-collab bot.

Auth is dual-path:
  - Web users: a founder/admin JWT (get_current_user + role gate).
  - The Discord bot: an X-Workspace-Token header matching WORKSPACE_BOT_TOKEN.
Either path yields an "actor" label used as the author on writes.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.workspace import (
    Workspace,
    WorkspaceChannel,
    WorkspaceFeatureStatus,
    WorkspaceLog,
    WorkspaceNote,
    WorkspaceTodo,
)
from app.schemas.workspace import (
    ChannelBind,
    ChannelRecord,
    ChannelResolution,
    FeatureStatusRecord,
    FeatureStatusUpsert,
    LogCreate,
    LogRecord,
    NoteCreate,
    NoteRecord,
    TodoCreate,
    TodoRecord,
    TodoUpdate,
    WorkspaceCreate,
    WorkspaceRecord,
    WorkspaceStatus,
)

logger = logging.getLogger("ryozan.workspaces")
settings = get_settings()

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# ── Dual auth ───────────────────────────────────────────────


async def authorize_actor(
    x_workspace_token: str | None = Header(default=None, alias="X-Workspace-Token"),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Return an actor label. Bot token OR founder/admin JWT. Raises 401/403."""
    # Bot path: shared-secret header.
    if x_workspace_token:
        if x_workspace_token != settings.WORKSPACE_BOT_TOKEN:
            raise HTTPException(status_code=401, detail="Invalid workspace token")
        return "bot"

    # Web path: founder/admin JWT.
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing credentials")
    token = authorization.split(" ", 1)[1]
    user = await get_current_user(token=token, db=db)
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("admin", 99):
        raise HTTPException(status_code=403, detail="Requires admin role or higher")
    return user.name or user.email or "staff"


async def _get_workspace(slug: str, db: AsyncSession) -> Workspace:
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail=f"Workspace '{slug}' not found")
    return ws


# ── Discord channel routing (global Herald) ─────────────────


@router.get("/resolve/{discord_channel_id}", response_model=ChannelResolution)
async def resolve_channel(
    discord_channel_id: str,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    """Return which workspace a Discord channel is bound to. Herald uses this
    to route every command to the right workspace."""
    result = await db.execute(
        select(Workspace.slug, Workspace.name)
        .join(WorkspaceChannel, WorkspaceChannel.workspace_id == Workspace.id)
        .where(WorkspaceChannel.discord_channel_id == discord_channel_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not bound to any workspace")
    return ChannelResolution(workspace_slug=row[0], workspace_name=row[1])


@router.post("/{slug}/bind", response_model=ChannelRecord, status_code=201)
async def bind_channel(
    slug: str,
    payload: ChannelBind,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    """Bind a Discord channel to this workspace (re-bind moves it)."""
    ws = await _get_workspace(slug, db)
    result = await db.execute(
        select(WorkspaceChannel).where(WorkspaceChannel.discord_channel_id == payload.discord_channel_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.workspace_id = ws.id
        existing.discord_guild_id = payload.discord_guild_id
        existing.bound_by = payload.bound_by or actor
        await db.commit()
        await db.refresh(existing)
        return existing
    channel = WorkspaceChannel(
        workspace_id=ws.id,
        discord_channel_id=payload.discord_channel_id,
        discord_guild_id=payload.discord_guild_id,
        bound_by=payload.bound_by or actor,
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return channel


# ── Workspaces ──────────────────────────────────────────────


@router.get("", response_model=list[WorkspaceRecord])
async def list_workspaces(
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Workspace).where(Workspace.archived.is_(False)).order_by(Workspace.created_at))
    return list(result.scalars().all())


@router.post("", response_model=WorkspaceRecord, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(Workspace).where(Workspace.slug == payload.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Workspace slug already exists")
    ws = Workspace(**payload.model_dump())
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return ws


@router.get("/{slug}/status", response_model=WorkspaceStatus)
async def workspace_status(
    slug: str,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)

    open_todos = await db.scalar(
        select(func.count(WorkspaceTodo.id)).where(WorkspaceTodo.workspace_id == ws.id, WorkspaceTodo.status == "open")
    )
    done_todos = await db.scalar(
        select(func.count(WorkspaceTodo.id)).where(WorkspaceTodo.workspace_id == ws.id, WorkspaceTodo.status == "done")
    )
    note_count = await db.scalar(select(func.count(WorkspaceNote.id)).where(WorkspaceNote.workspace_id == ws.id))
    log_count = await db.scalar(select(func.count(WorkspaceLog.id)).where(WorkspaceLog.workspace_id == ws.id))

    feat_rows = await db.execute(
        select(WorkspaceFeatureStatus.status, WorkspaceFeatureStatus.verdict).where(
            WorkspaceFeatureStatus.workspace_id == ws.id
        )
    )
    feature_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    for status_val, verdict_val in feat_rows.all():
        feature_counts[status_val] = feature_counts.get(status_val, 0) + 1
        if verdict_val:
            verdict_counts[verdict_val] = verdict_counts.get(verdict_val, 0) + 1

    recent = await db.execute(
        select(WorkspaceLog).where(WorkspaceLog.workspace_id == ws.id).order_by(desc(WorkspaceLog.created_at)).limit(5)
    )

    return WorkspaceStatus(
        workspace=WorkspaceRecord.model_validate(ws),
        open_todos=int(open_todos or 0),
        done_todos=int(done_todos or 0),
        note_count=int(note_count or 0),
        log_count=int(log_count or 0),
        feature_counts=feature_counts,
        verdict_counts=verdict_counts,
        recent_log=[LogRecord.model_validate(r) for r in recent.scalars().all()],
    )


# ── Notes ───────────────────────────────────────────────────


@router.get("/{slug}/notes", response_model=list[NoteRecord])
async def list_notes(
    slug: str,
    limit: int = Query(default=50, ge=1, le=200),
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    result = await db.execute(
        select(WorkspaceNote)
        .where(WorkspaceNote.workspace_id == ws.id)
        .order_by(desc(WorkspaceNote.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post("/{slug}/notes", response_model=NoteRecord, status_code=201)
async def add_note(
    slug: str,
    payload: NoteCreate,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    author = payload.author if payload.author != "unknown" else actor
    note = WorkspaceNote(workspace_id=ws.id, body=payload.body, author=author, source=payload.source)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


# ── Todos ───────────────────────────────────────────────────


@router.get("/{slug}/todos", response_model=list[TodoRecord])
async def list_todos(
    slug: str,
    status: str | None = Query(default=None, pattern="^(open|done)$"),
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    stmt = select(WorkspaceTodo).where(WorkspaceTodo.workspace_id == ws.id)
    if status:
        stmt = stmt.where(WorkspaceTodo.status == status)
    stmt = stmt.order_by(WorkspaceTodo.status, desc(WorkspaceTodo.created_at))
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/{slug}/todos", response_model=TodoRecord, status_code=201)
async def add_todo(
    slug: str,
    payload: TodoCreate,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    author = payload.author if payload.author != "unknown" else actor
    todo = WorkspaceTodo(
        workspace_id=ws.id,
        title=payload.title,
        assignee=payload.assignee,
        author=author,
        source=payload.source,
    )
    db.add(todo)
    await db.commit()
    await db.refresh(todo)
    return todo


@router.patch("/{slug}/todos/{todo_id}", response_model=TodoRecord)
async def update_todo(
    slug: str,
    todo_id: UUID,
    payload: TodoUpdate,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    result = await db.execute(
        select(WorkspaceTodo).where(WorkspaceTodo.id == todo_id, WorkspaceTodo.workspace_id == ws.id)
    )
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")

    from datetime import UTC, datetime

    if payload.title is not None:
        todo.title = payload.title
    if payload.assignee is not None:
        todo.assignee = payload.assignee
    if payload.status is not None:
        todo.status = payload.status
        todo.done_at = datetime.now(UTC) if payload.status == "done" else None

    await db.commit()
    await db.refresh(todo)
    return todo


# ── Log ─────────────────────────────────────────────────────


@router.get("/{slug}/log", response_model=list[LogRecord])
async def list_log(
    slug: str,
    limit: int = Query(default=50, ge=1, le=200),
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    result = await db.execute(
        select(WorkspaceLog)
        .where(WorkspaceLog.workspace_id == ws.id)
        .order_by(desc(WorkspaceLog.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post("/{slug}/log", response_model=LogRecord, status_code=201)
async def add_log(
    slug: str,
    payload: LogCreate,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    author = payload.author if payload.author != "unknown" else actor
    entry = WorkspaceLog(workspace_id=ws.id, entry=payload.entry, author=author, source=payload.source)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


# ── Feature status (tracker audit matrix) ───────────────────


@router.get("/{slug}/features", response_model=list[FeatureStatusRecord])
async def list_features(
    slug: str,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    result = await db.execute(
        select(WorkspaceFeatureStatus)
        .where(WorkspaceFeatureStatus.workspace_id == ws.id)
        .order_by(WorkspaceFeatureStatus.domain)
    )
    return list(result.scalars().all())


@router.put("/{slug}/features", response_model=FeatureStatusRecord)
async def upsert_feature(
    slug: str,
    payload: FeatureStatusUpsert,
    actor: str = Depends(authorize_actor),
    db: AsyncSession = Depends(get_db),
):
    ws = await _get_workspace(slug, db)
    result = await db.execute(
        select(WorkspaceFeatureStatus).where(
            WorkspaceFeatureStatus.workspace_id == ws.id,
            WorkspaceFeatureStatus.domain == payload.domain,
        )
    )
    row = result.scalar_one_or_none()
    from datetime import UTC, datetime

    if row:
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
    else:
        row = WorkspaceFeatureStatus(workspace_id=ws.id, **payload.model_dump())
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
