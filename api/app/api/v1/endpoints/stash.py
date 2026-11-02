"""Hustle Stash inventory endpoints."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_RANK, require_role
from app.models.flash import FlashDesign, FlashSheet
from app.models.stash import StashCanvasSession, StashItem
from app.models.user import User
from app.schemas.stash import (
    CanvasSessionCreate,
    CanvasSessionResponse,
    CanvasSessionUpdate,
    StashItemCreate,
    StashItemListResponse,
    StashItemResponse,
    StashItemUpdate,
)
from app.schemas.user import UserResponse

router = APIRouter(prefix="/stash", tags=["stash"])


def _can_manage_artist(current_user: User, artist_id: UUID) -> bool:
    return current_user.id == artist_id or ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)


async def _resolve_artist_owner(
    db: AsyncSession,
    current_user: User,
    artist_id: UUID | None,
) -> User:
    if artist_id is None or artist_id == current_user.id:
        return current_user
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Cannot manage another creator's Stash")
    result = await db.execute(
        select(User).where(
            User.id == artist_id,
            User.role.in_(["artist", "admin", "founder"]),
        )
    )
    owner = result.scalar_one_or_none()
    if not owner:
        raise HTTPException(status_code=404, detail="Creator not found")
    return owner


@router.get("/creators", response_model=list[UserResponse])
async def list_hustle_creators(
    db: AsyncSession = Depends(get_db, scope="function"),
    _current_user: User = Depends(require_role("admin")),
):
    """Admin+: list creator accounts that Hustle can target."""
    result = await db.execute(
        select(User).where(User.role.in_(["artist", "admin", "founder"])).order_by(User.name, User.email)
    )
    return result.scalars().all()


def _stash_item_response(item: StashItem) -> StashItemResponse:
    return StashItemResponse(
        id=item.id,
        artist_id=item.artist_id,
        created_by_id=item.created_by_id,
        source_flash_design_id=item.source_flash_design_id,
        lane=item.lane,
        title=item.title,
        description=item.description,
        status=item.status,
        sku=item.sku,
        asset_url=item.asset_url,
        price=float(item.price) if item.price is not None else None,
        inventory_count=item.inventory_count,
        size_label=item.size_label,
        estimated_minutes=item.estimated_minutes,
        delivery_kind=item.delivery_kind,
        storage_ref=item.storage_ref,
        tags=item.tags,
        metadata=item.metadata_json,
        ai_metadata=item.ai_metadata,
        gala_sync_enabled=item.gala_sync_enabled,
        published_at=item.published_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("/items", response_model=StashItemListResponse)
async def list_stash_items(
    lane: str | None = Query(default=None, pattern="^(flash|crafts|digital)$"),
    status: str | None = Query(default=None),
    artist_id: UUID | None = Query(default=None),
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    owner = await _resolve_artist_owner(db, current_user, artist_id)
    filters = [StashItem.artist_id == owner.id]
    if lane:
        filters.append(StashItem.lane == lane)
    if status:
        filters.append(StashItem.status == status)

    count_result = await db.execute(select(func.count()).select_from(StashItem).where(*filters))
    total = count_result.scalar() or 0
    result = await db.execute(
        select(StashItem).where(*filters).order_by(StashItem.updated_at.desc()).offset(skip).limit(limit)
    )
    items = result.scalars().all()
    return StashItemListResponse(items=[_stash_item_response(item) for item in items], total=total)


@router.post("/items", response_model=StashItemResponse, status_code=201)
async def create_stash_item(
    body: StashItemCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    owner = await _resolve_artist_owner(db, current_user, body.artist_id)
    if body.source_flash_design_id:
        result = await db.execute(select(FlashDesign).where(FlashDesign.id == body.source_flash_design_id))
        design = result.scalar_one_or_none()
        if not design:
            raise HTTPException(status_code=404, detail="Source flash design not found")
        if not _can_manage_artist(current_user, design.artist_id) or design.artist_id != owner.id:
            raise HTTPException(status_code=403, detail="Cannot attach that flash design")

    now = datetime.now(UTC)
    item = StashItem(
        artist_id=owner.id,
        created_by_id=current_user.id,
        source_flash_design_id=body.source_flash_design_id,
        lane=body.lane,
        title=body.title,
        description=body.description,
        status=body.status,
        sku=body.sku,
        asset_url=body.asset_url,
        price=body.price,
        inventory_count=body.inventory_count,
        size_label=body.size_label,
        estimated_minutes=body.estimated_minutes,
        delivery_kind=body.delivery_kind,
        storage_ref=body.storage_ref,
        tags=body.tags,
        metadata_json=body.metadata,
        ai_metadata=body.ai_metadata,
        gala_sync_enabled=body.gala_sync_enabled,
        published_at=now if body.status == "published" else None,
    )
    db.add(item)
    await db.flush()
    return _stash_item_response(item)


@router.patch("/items/{item_id}", response_model=StashItemResponse)
async def update_stash_item(
    item_id: UUID,
    body: StashItemUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    result = await db.execute(select(StashItem).where(StashItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Stash item not found")
    if not _can_manage_artist(current_user, item.artist_id):
        raise HTTPException(status_code=403, detail="Cannot manage this Stash item")

    updates = body.model_dump(exclude_unset=True)
    if "source_flash_design_id" in updates and updates["source_flash_design_id"]:
        design_result = await db.execute(select(FlashDesign).where(FlashDesign.id == updates["source_flash_design_id"]))
        design = design_result.scalar_one_or_none()
        if not design or design.artist_id != item.artist_id:
            raise HTTPException(status_code=422, detail="Source flash design must belong to this creator")

    if "metadata" in updates:
        item.metadata_json = updates.pop("metadata")
    for field, value in updates.items():
        setattr(item, field, value)
    if body.status == "published" and item.published_at is None:
        item.published_at = datetime.now(UTC)
    item.updated_at = datetime.now(UTC)
    await db.flush()
    return _stash_item_response(item)


@router.post("/items/from-flash/{design_id}", response_model=StashItemResponse, status_code=201)
async def create_stash_item_from_flash(
    design_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    result = await db.execute(select(FlashDesign).where(FlashDesign.id == design_id))
    design = result.scalar_one_or_none()
    if not design:
        raise HTTPException(status_code=404, detail="Flash design not found")
    if not _can_manage_artist(current_user, design.artist_id):
        raise HTTPException(status_code=403, detail="Cannot manage this flash design")

    item = StashItem(
        artist_id=design.artist_id,
        created_by_id=current_user.id,
        source_flash_design_id=design.id,
        lane="flash",
        title=design.name or design.ai_suggested_name or "Flash design",
        status="published" if design.status == "published" else "draft",
        asset_url=design.image_url,
        price=float(design.price) if design.price is not None else None,
        inventory_count=1,
        size_label=design.size_label,
        estimated_minutes=design.estimated_minutes,
        tags=[design.style] if design.style else None,
        metadata_json={
            "placement": design.placement,
            "style": design.style,
            "sheet_id": str(design.sheet_id),
        },
        ai_metadata=design.ai_metadata,
        gala_sync_enabled=design.gala_sync_enabled,
        published_at=datetime.now(UTC) if design.status == "published" else None,
    )
    db.add(item)
    await db.flush()
    return _stash_item_response(item)


@router.post("/canvas-sessions", response_model=CanvasSessionResponse, status_code=201)
async def create_canvas_session(
    body: CanvasSessionCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    owner = await _resolve_artist_owner(db, current_user, body.artist_id)
    if body.sheet_id:
        result = await db.execute(select(FlashSheet).where(FlashSheet.id == body.sheet_id))
        sheet = result.scalar_one_or_none()
        if not sheet:
            raise HTTPException(status_code=404, detail="Flash sheet not found")
        if sheet.artist_id != owner.id:
            raise HTTPException(status_code=403, detail="Flash sheet belongs to another creator")

    session = StashCanvasSession(
        artist_id=owner.id,
        created_by_id=current_user.id,
        sheet_id=body.sheet_id,
        lane=body.lane,
        status=body.status,
        canvas_state=body.canvas_state,
        ai_suggestions=body.ai_suggestions,
        source_context=body.source_context,
        gala_sync_enabled=body.gala_sync_enabled,
    )
    db.add(session)
    await db.flush()
    return session


@router.patch("/canvas-sessions/{session_id}", response_model=CanvasSessionResponse)
async def update_canvas_session(
    session_id: UUID,
    body: CanvasSessionUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    result = await db.execute(select(StashCanvasSession).where(StashCanvasSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Canvas session not found")
    if not _can_manage_artist(current_user, session.artist_id):
        raise HTTPException(status_code=403, detail="Cannot manage this canvas session")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(session, field, value)
    session.updated_at = datetime.now(UTC)
    await db.flush()
    return session
