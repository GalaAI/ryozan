"""
Per-artist session type CRUD.

Artists configure their own size/duration/pricing matrix through their
portal profile. The public booking form + Courier quote composer read
these to auto-suggest pricing and duration based on the client's
selected size and placement.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.session_type import ArtistSessionType
from app.models.user import User

logger = logging.getLogger("ryozan.session_types")
router = APIRouter(prefix="/session-types", tags=["session-types"])


# ── Schemas ──────────────────────────────────────────


class SessionTypeCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    size: str = Field(min_length=1, max_length=50)
    placements: list[str] | None = None
    duration_minutes: int = Field(default=120, ge=15, le=720)
    buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    deposit_amount: float = Field(default=100, ge=0)
    hourly_rate: float | None = Field(default=None, ge=0)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    sort_order: int = 0


class SessionTypeUpdate(BaseModel):
    label: str | None = None
    size: str | None = None
    placements: list[str] | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=720)
    buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    deposit_amount: float | None = Field(default=None, ge=0)
    hourly_rate: float | None = Field(default=None, ge=0)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    is_active: bool | None = None
    sort_order: int | None = None


class SessionTypeResponse(BaseModel):
    id: UUID
    artist_id: UUID
    label: str
    size: str
    placements: list[str] | None
    duration_minutes: int
    buffer_minutes: int | None
    deposit_amount: float
    hourly_rate: float | None
    price_min: float | None
    price_max: float | None
    is_active: bool
    sort_order: int

    model_config = {"from_attributes": True}


# ── Helpers ──────────────────────────────────────────


def _require_staff(user: User) -> None:
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=403, detail="Artist role or higher required")


# ── Endpoints ────────────────────────────────────────


@router.get("/artist/{artist_slug}", response_model=list[SessionTypeResponse])
async def list_session_types_for_artist(
    artist_slug: str,
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
):
    """
    Public: list session types for an artist by slug.
    Used by the booking form to show available session options
    and auto-fill pricing based on client's size/placement selection.
    """
    artist_result = await db.execute(
        select(User).where(
            User.slug == artist_slug,
            User.role.in_(["artist", "founder"]),
        )
    )
    artist = artist_result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    query = select(ArtistSessionType).where(ArtistSessionType.artist_id == artist.id)
    if active_only:
        query = query.where(ArtistSessionType.is_active.is_(True))
    query = query.order_by(ArtistSessionType.sort_order, ArtistSessionType.label)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/mine", response_model=list[SessionTypeResponse])
async def list_my_session_types(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Artist+: list your own session types (including inactive)."""
    _require_staff(current_user)
    result = await db.execute(
        select(ArtistSessionType)
        .where(ArtistSessionType.artist_id == current_user.id)
        .order_by(ArtistSessionType.sort_order, ArtistSessionType.label)
    )
    return result.scalars().all()


@router.post("/", response_model=SessionTypeResponse, status_code=201)
async def create_session_type(
    body: SessionTypeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Artist+: create a new session type."""
    _require_staff(current_user)
    st = ArtistSessionType(
        artist_id=current_user.id,
        label=body.label,
        size=body.size,
        placements=body.placements,
        duration_minutes=body.duration_minutes,
        buffer_minutes=body.buffer_minutes,
        deposit_amount=body.deposit_amount,
        hourly_rate=body.hourly_rate,
        price_min=body.price_min,
        price_max=body.price_max,
        sort_order=body.sort_order,
    )
    db.add(st)
    await db.flush()
    return st


@router.patch("/{session_type_id}", response_model=SessionTypeResponse)
async def update_session_type(
    session_type_id: UUID,
    body: SessionTypeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Artist+: update one of your session types."""
    _require_staff(current_user)
    result = await db.execute(select(ArtistSessionType).where(ArtistSessionType.id == session_type_id))
    st = result.scalar_one_or_none()
    if not st:
        raise HTTPException(status_code=404, detail="Session type not found")

    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    if st.artist_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Not your session type")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(st, field, value)
    st.updated_at = datetime.now(UTC)
    await db.flush()
    return st


@router.delete("/{session_type_id}", status_code=204)
async def delete_session_type(
    session_type_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Artist+: delete one of your session types."""
    _require_staff(current_user)
    result = await db.execute(select(ArtistSessionType).where(ArtistSessionType.id == session_type_id))
    st = result.scalar_one_or_none()
    if not st:
        raise HTTPException(status_code=404, detail="Session type not found")

    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    if st.artist_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Not your session type")

    await db.delete(st)
    await db.flush()


@router.get("/lookup", response_model=SessionTypeResponse | None)
async def lookup_session_type(
    artist_slug: str = Query(...),
    size: str = Query(...),
    placement: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Public: find the best-matching session type for an artist given
    a size and optional placement. Used by the booking form to
    auto-fill duration and pricing when the client picks a size.

    Returns the most specific match (placement + size), falling back
    to size-only if no placement match exists.
    """
    artist_result = await db.execute(
        select(User.id).where(
            User.slug == artist_slug,
            User.role.in_(["artist", "founder"]),
        )
    )
    artist_id = artist_result.scalar_one_or_none()
    if not artist_id:
        raise HTTPException(status_code=404, detail="Artist not found")

    # Try placement-specific match first
    if placement:
        result = await db.execute(
            select(ArtistSessionType)
            .where(
                ArtistSessionType.artist_id == artist_id,
                ArtistSessionType.size == size,
                ArtistSessionType.is_active.is_(True),
                ArtistSessionType.placements.contains([placement]),
            )
            .limit(1)
        )
        match = result.scalar_one_or_none()
        if match:
            return match

    # Fall back to size-only match (empty placements = applies to all)
    result = await db.execute(
        select(ArtistSessionType)
        .where(
            ArtistSessionType.artist_id == artist_id,
            ArtistSessionType.size == size,
            ArtistSessionType.is_active.is_(True),
        )
        .order_by(ArtistSessionType.sort_order)
        .limit(1)
    )
    return result.scalar_one_or_none()
