"""
Public artist endpoints — no auth required (listing/detail).
Protected endpoints for profile management + gallery uploads.

Artists are Users with role='artist' (or 'founder') and a slug set.
Founders inherit all artist capabilities via the role hierarchy.
"""

import uuid as uuid_mod

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.models.user import User
from app.schemas.artist import ArtistDetail, ArtistProfileUpdate, ArtistSummary
from app.services.artist_slugs import unique_artist_slug
from app.services.gallery_storage import process_gallery_deletion_outbox_once, queue_gallery_deletion
from app.services.image_processing import MAX_UPLOAD_BYTES, ImageValidationError, validate_and_normalize_image
from app.services.portfolio_thumbs import upload_portfolio_focus, upload_portfolio_thumb
from app.services.quest_hooks import check_and_complete_quest
from app.services.storage import upload_file as upload_to_drive

router = APIRouter(prefix="/artists", tags=["artists"])


ARTIST_ROLES = ["artist", "founder"]
ARTIST_GALLERY_MAX_IMAGES = 48


async def _read_artist_image(file: UploadFile):
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds the 20 MB limit")
        chunks.append(chunk)
    try:
        return validate_and_normalize_image(b"".join(chunks), file.filename)
    except ImageValidationError as exc:
        status = 413 if exc.code == "FLASH_FILE_TOO_LARGE" else 422
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/", response_model=list[ArtistSummary])
async def list_artists(db: AsyncSession = Depends(get_db, scope="function")):
    """Public: get all artists for the crew page (includes founders)."""
    result = await db.execute(
        select(User).where(User.role.in_(ARTIST_ROLES), User.slug.isnot(None)).order_by(User.name)
    )
    return result.scalars().all()


@router.get("/{slug}", response_model=ArtistDetail)
async def get_artist(slug: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Public: get a single artist's full profile by slug."""
    result = await db.execute(select(User).where(User.slug == slug, User.role.in_(ARTIST_ROLES)))
    artist = result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    return artist


@router.patch("/me", response_model=ArtistDetail)
async def update_artist_profile(
    body: ArtistProfileUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: update your own artist-specific profile fields."""
    update_data = body.model_dump(exclude_unset=True)
    if not current_user.slug:
        current_user.slug = await unique_artist_slug(db, current_user.name or "artist")
    if "working_hours" in update_data:
        wh = update_data.pop("working_hours")
        profile = dict(current_user.nexus_profile or {})
        profile["working_hours"] = wh
        current_user.nexus_profile = profile
    for key, value in update_data.items():
        setattr(current_user, key, value)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/me/avatar", response_model=ArtistDetail)
async def upload_avatar(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: upload a profile picture."""
    normalized = await _read_artist_image(file)
    filename = f"{uuid_mod.uuid4().hex}.{normalized.extension}"

    # Upload to Cloudflare R2 (persistent storage)
    r2_url = await upload_to_drive(
        file_bytes=normalized.data,
        filename=filename,
        mime_type=normalized.mime_type,
        folder_path=f"avatars/{current_user.id}",
    )
    if not r2_url:
        raise HTTPException(500, "Failed to upload avatar — storage unavailable")

    current_user.avatar_url = r2_url

    # Auto-complete 'profile-pic' quest
    await check_and_complete_quest(db, current_user.id, "profile-pic")

    await db.commit()
    await db.refresh(current_user)
    return current_user


# ── Gallery management ─────────────────────────────────────────


async def _append_gallery_image(db: AsyncSession, artist: User, file: UploadFile) -> User:
    """Store one image and append it to the given artist's public gallery."""
    images = list(artist.portfolio_images or [])
    if len(images) >= ARTIST_GALLERY_MAX_IMAGES:
        raise HTTPException(status_code=409, detail=f"Gallery limit reached ({ARTIST_GALLERY_MAX_IMAGES} images)")

    normalized = await _read_artist_image(file)
    filename = f"{uuid_mod.uuid4().hex}.{normalized.extension}"

    slug = artist.slug or str(artist.id)
    url = await upload_to_drive(
        file_bytes=normalized.data,
        filename=filename,
        mime_type=normalized.mime_type,
        folder_path=f"portfolios/{slug}",
    )
    if not url:
        raise HTTPException(500, "Failed to upload image — storage unavailable")
    images.append(url)
    artist.portfolio_images = images

    # Tile and enlarged-view companions. Best effort: the original is already stored, a
    # derivative failure only means this image is served full size until the startup
    # backfill (or scripts/backfill_portfolio_thumbs.py) heals it.
    thumb_url = await upload_portfolio_thumb(normalized.data, slug=slug, original_name=filename)
    if thumb_url:
        thumbs = dict(artist.portfolio_thumbs or {})
        thumbs[url] = thumb_url
        artist.portfolio_thumbs = thumbs
    focus_url = await upload_portfolio_focus(normalized.data, slug=slug, original_name=filename)
    if focus_url:
        focus = dict(artist.portfolio_focus or {})
        focus[url] = focus_url
        artist.portfolio_focus = focus

    await db.commit()
    await db.refresh(artist)
    return artist


async def _remove_gallery_image(db: AsyncSession, artist: User, url: str) -> User:
    """Remove one image from the given artist's public gallery and queue its R2 cleanup."""
    images = list(artist.portfolio_images or [])
    if url not in images:
        raise HTTPException(404, "Image URL not found in gallery")

    slug = artist.slug or str(artist.id)
    deletion = await queue_gallery_deletion(
        db,
        artist_id=artist.id,
        url=url,
        allowed_prefix=f"portfolios/{slug}/",
    )

    thumbs = dict(artist.portfolio_thumbs or {})
    focus = dict(artist.portfolio_focus or {})
    for derivative_url in (thumbs.pop(url, None), focus.pop(url, None)):
        if derivative_url:
            await queue_gallery_deletion(
                db,
                artist_id=artist.id,
                url=derivative_url,
                allowed_prefix=f"portfolios/{slug}/",
            )

    images.remove(url)
    artist.portfolio_images = images
    artist.portfolio_thumbs = thumbs or None
    artist.portfolio_focus = focus or None

    await db.commit()
    # The public profile is now authoritative. R2 cleanup may finish here or
    # through the durable worker after a provider/process interruption.
    await process_gallery_deletion_outbox_once(outbox_id=deletion.id)
    await db.refresh(artist)
    return artist


async def _artist_by_slug(db: AsyncSession, slug: str) -> User:
    result = await db.execute(select(User).where(User.slug == slug, User.role.in_(ARTIST_ROLES)))
    artist = result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    return artist


@router.post("/me/gallery/upload", response_model=ArtistDetail)
async def upload_gallery_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: upload an image to your portfolio gallery."""
    return await _append_gallery_image(db, current_user, file)


class GalleryRemoveRequest(BaseModel):
    url: str


@router.delete("/me/gallery", response_model=ArtistDetail)
async def remove_gallery_image(
    body: GalleryRemoveRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: remove an image from your portfolio gallery."""
    return await _remove_gallery_image(db, current_user, body.url)


# Staff gallery management. The /me routes above only ever write to the signed-in account, so
# without these an owner has no way to fill the gallery of an artist who has not logged in yet.
# Declared after the /me routes so "me" is never read as a slug.


@router.post("/{slug}/gallery/upload", response_model=ArtistDetail)
async def upload_gallery_image_for_artist(
    slug: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    _caller: User = Depends(require_role("founder")),
):
    """Founder only: upload an image to the named artist's portfolio gallery."""
    return await _append_gallery_image(db, await _artist_by_slug(db, slug), file)


@router.delete("/{slug}/gallery", response_model=ArtistDetail)
async def remove_gallery_image_for_artist(
    slug: str,
    body: GalleryRemoveRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    _caller: User = Depends(require_role("founder")),
):
    """Founder only: remove an image from the named artist's portfolio gallery."""
    return await _remove_gallery_image(db, await _artist_by_slug(db, slug), body.url)
