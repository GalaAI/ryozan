"""Configurable booking add-ons.

Public booking reads active add-ons by artist slug. Staff manage their own
add-ons, while admin+ roles can target another artist for shop setup.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking_addon import BookingAddOn
from app.models.user import BTTCOUser

router = APIRouter(prefix="/booking-addons", tags=["booking-addons"])


class BookingAddOnCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    price_amount: float | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, ge=0, le=720)
    sort_order: int = 0
    artist_id: UUID | None = None


class BookingAddOnUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    price_amount: float | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, ge=0, le=720)
    is_active: bool | None = None
    sort_order: int | None = None


class BookingAddOnResponse(BaseModel):
    id: UUID
    artist_id: UUID
    label: str
    description: str | None
    price_amount: float | None
    duration_minutes: int | None
    is_active: bool
    sort_order: int

    model_config = {"from_attributes": True}


def _require_staff(user: BTTCOUser) -> None:
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=403, detail="Artist role or higher required")


def _is_admin(user: BTTCOUser) -> bool:
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK.get("admin", 3)


async def _resolve_artist_id(
    db: AsyncSession,
    current_user: BTTCOUser,
    requested_artist_id: UUID | None,
) -> UUID:
    if requested_artist_id is None or requested_artist_id == current_user.id:
        return current_user.id
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Cannot manage another artist's add-ons")
    result = await db.execute(
        select(BTTCOUser.id).where(
            BTTCOUser.id == requested_artist_id,
            BTTCOUser.role.in_(["artist", "admin", "founder"]),
        )
    )
    artist_id = result.scalar_one_or_none()
    if not artist_id:
        raise HTTPException(status_code=404, detail="Artist not found")
    return artist_id


@router.get("/artist/{artist_slug}", response_model=list[BookingAddOnResponse])
async def list_public_booking_addons(
    artist_slug: str,
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
):
    slug = get_settings().resolve_artist_slug(artist_slug)
    if slug is None:
        return []
    artist_result = await db.execute(
        select(BTTCOUser.id).where(
            BTTCOUser.slug == slug,
            BTTCOUser.role.in_(["artist", "admin", "founder"]),
        )
    )
    artist_id = artist_result.scalar_one_or_none()
    if not artist_id:
        raise HTTPException(status_code=404, detail="Artist not found")

    query = select(BookingAddOn).where(BookingAddOn.artist_id == artist_id)
    if active_only:
        query = query.where(BookingAddOn.is_active.is_(True))
    query = query.order_by(BookingAddOn.sort_order, BookingAddOn.label)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/mine", response_model=list[BookingAddOnResponse])
async def list_my_booking_addons(
    artist_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    _require_staff(current_user)
    resolved_artist_id = await _resolve_artist_id(db, current_user, artist_id)
    result = await db.execute(
        select(BookingAddOn)
        .where(BookingAddOn.artist_id == resolved_artist_id)
        .order_by(BookingAddOn.sort_order, BookingAddOn.label)
    )
    return result.scalars().all()


@router.post("/", response_model=BookingAddOnResponse, status_code=201)
async def create_booking_addon(
    body: BookingAddOnCreate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    _require_staff(current_user)
    artist_id = await _resolve_artist_id(db, current_user, body.artist_id)
    add_on = BookingAddOn(
        artist_id=artist_id,
        label=body.label.strip(),
        description=(body.description or "").strip() or None,
        price_amount=body.price_amount,
        duration_minutes=body.duration_minutes,
        sort_order=body.sort_order,
    )
    db.add(add_on)
    await db.flush()
    return add_on


@router.patch("/{add_on_id}", response_model=BookingAddOnResponse)
async def update_booking_addon(
    add_on_id: UUID,
    body: BookingAddOnUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    _require_staff(current_user)
    result = await db.execute(select(BookingAddOn).where(BookingAddOn.id == add_on_id))
    add_on = result.scalar_one_or_none()
    if not add_on:
        raise HTTPException(status_code=404, detail="Add-on not found")
    if add_on.artist_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Cannot update another artist's add-on")

    for field, value in body.model_dump(exclude_unset=True).items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(add_on, field, value)
    add_on.updated_at = datetime.now(UTC)
    await db.flush()
    return add_on


@router.delete("/{add_on_id}", status_code=204)
async def delete_booking_addon(
    add_on_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    _require_staff(current_user)
    result = await db.execute(select(BookingAddOn).where(BookingAddOn.id == add_on_id))
    add_on = result.scalar_one_or_none()
    if not add_on:
        raise HTTPException(status_code=404, detail="Add-on not found")
    if add_on.artist_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Cannot delete another artist's add-on")

    await db.delete(add_on)
    await db.flush()
