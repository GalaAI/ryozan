"""
Reference Library — shared file library for shop staff.

Artists and above can upload, browse, pin, and delete reference
images and PDFs. Per-user recently viewed tracking is included.
"""

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.reference import Reference, ReferenceView
from app.models.user import User
from app.services.storage import upload_file

logger = logging.getLogger("ryozan.references")
router = APIRouter(prefix="/references", tags=["references"])

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB
ALLOWED_PREFIXES = ("image/", "application/pdf")


# -- Schemas -------------------------------------------------------


class ReferenceResponse(BaseModel):
    id: uuid.UUID
    filename: str
    original_filename: str
    mime_type: str
    file_size: int
    file_url: str
    uploaded_by: uuid.UUID
    is_pinned: bool
    pin_order: int | None
    tags: list
    view_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReferenceListResponse(BaseModel):
    items: list[ReferenceResponse]
    recently_viewed: list[ReferenceResponse]


class PinBody(BaseModel):
    is_pinned: bool


# -- Helpers -------------------------------------------------------


def _require_artist(user: User) -> None:
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("artist", 2):
        raise HTTPException(status_code=403, detail="Artist role or higher required")


# -- Endpoints -----------------------------------------------------


@router.get("/", response_model=ReferenceListResponse)
async def list_references(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all references: pinned first (by pin_order), then unpinned alphabetically."""
    _require_artist(current_user)

    # All references, pinned first sorted by pin_order, then unpinned alpha
    result = await db.execute(
        select(Reference).order_by(
            Reference.is_pinned.desc(),
            Reference.pin_order.asc().nullslast(),
            Reference.original_filename.asc(),
        )
    )
    items = result.scalars().all()

    # Recently viewed by this user (last 10)
    rv_result = await db.execute(
        select(Reference)
        .join(ReferenceView, ReferenceView.reference_id == Reference.id)
        .where(ReferenceView.user_id == current_user.id)
        .order_by(ReferenceView.viewed_at.desc())
        .limit(10)
    )
    recently_viewed = rv_result.scalars().all()

    return ReferenceListResponse(items=items, recently_viewed=recently_viewed)


@router.post("/upload", response_model=ReferenceResponse, status_code=201)
async def upload_reference(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a reference file (image or PDF, max 25 MB)."""
    _require_artist(current_user)

    # Validate mime type
    content_type = file.content_type or ""
    if not any(content_type.startswith(p) for p in ALLOWED_PREFIXES):
        raise HTTPException(
            status_code=400,
            detail=f"File type '{content_type}' not allowed. Use image/* or application/pdf.",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File exceeds 25 MB limit")

    original = file.filename or "unnamed"
    ext = Path(original).suffix
    stored_name = f"{uuid.uuid4().hex}{ext}"

    url = await upload_file(content, stored_name, content_type, "references")
    if not url:
        raise HTTPException(status_code=502, detail="File upload failed")

    ref = Reference(
        filename=stored_name,
        original_filename=original,
        mime_type=content_type,
        file_size=len(content),
        file_url=url,
        uploaded_by=current_user.id,
    )
    db.add(ref)
    await db.flush()
    return ref


@router.post("/{reference_id}/view", status_code=204)
async def record_view(
    reference_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record a view on a reference (upsert per-user, increment view_count)."""
    _require_artist(current_user)

    # Verify reference exists
    result = await db.execute(select(Reference).where(Reference.id == reference_id))
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="Reference not found")

    # Increment global view count
    ref.view_count = ref.view_count + 1

    # Upsert into reference_views for this user
    existing = await db.execute(
        select(ReferenceView).where(
            and_(
                ReferenceView.reference_id == reference_id,
                ReferenceView.user_id == current_user.id,
            )
        )
    )
    view = existing.scalar_one_or_none()
    if view:
        view.viewed_at = datetime.now(UTC)
    else:
        db.add(
            ReferenceView(
                reference_id=reference_id,
                user_id=current_user.id,
            )
        )

    await db.flush()


@router.patch("/{reference_id}/pin", response_model=ReferenceResponse)
async def toggle_pin(
    reference_id: uuid.UUID,
    body: PinBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pin or unpin a reference."""
    _require_artist(current_user)

    result = await db.execute(select(Reference).where(Reference.id == reference_id))
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="Reference not found")

    if body.is_pinned:
        # Get next pin_order
        max_result = await db.execute(select(func.coalesce(func.max(Reference.pin_order), 0)))
        max_order = max_result.scalar()
        ref.is_pinned = True
        ref.pin_order = max_order + 1
    else:
        ref.is_pinned = False
        ref.pin_order = None

    ref.updated_at = datetime.now(UTC)
    await db.flush()
    return ref


@router.delete("/{reference_id}", status_code=204)
async def delete_reference(
    reference_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a reference. Only the uploader or founder/admin can delete."""
    _require_artist(current_user)

    result = await db.execute(select(Reference).where(Reference.id == reference_id))
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="Reference not found")

    is_owner = ref.uploaded_by == current_user.id
    is_privileged = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    if not is_owner and not is_privileged:
        raise HTTPException(status_code=403, detail="Only the uploader or admin/founder can delete")

    # Clean up views, then delete the reference
    await db.execute(delete(ReferenceView).where(ReferenceView.reference_id == reference_id))
    await db.delete(ref)
    await db.flush()
