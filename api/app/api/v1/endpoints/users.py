"""
User management endpoints.

- Profile viewing / updating (self-service)
- Avatar upload (any authenticated user)
- User listing + role changes (admin/founder only)
"""

import uuid as uuid_mod
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.booking import Booking
from app.models.user import BTTCOUser
from app.schemas.user import (
    UserAccountStatus,
    UserMembershipResponse,
    UserMembershipUpdate,
    UserProfileUpdate,
    UserResponse,
    UserRoleUpdate,
)
from app.services.account_status import password_status
from app.services.ai_routing import resolve_ai_lane
from app.services.demo_account import refuse_if_demo_account
from app.services.quest_hooks import check_and_complete_quest
from app.services.reward_engine import grant_configured_reward, profile_is_complete
from app.services.storage import upload_file as upload_to_drive

router = APIRouter(prefix="/users", tags=["users"])


async def _artist_has_client_relationship(
    db: AsyncSession,
    artist_id: UUID,
    client_id: UUID,
) -> bool:
    """Return whether the artist has a booking assigned to this client."""
    result = await db.execute(
        select(Booking.id)
        .where(
            Booking.artist_id == artist_id,
            Booking.client_id == client_id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


@router.get("/", response_model=list[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = 50,
    role: str | None = Query(default=None, pattern="^(collector|assistant|artist|admin|founder)$"),
    db: AsyncSession = Depends(get_db),
    _caller: BTTCOUser = Depends(require_role("admin")),
):
    """List all users. Admin/founder only."""
    filters = []
    if role:
        filters.append(BTTCOUser.role == role)
    result = await db.execute(select(BTTCOUser).where(*filters).offset(skip).limit(limit).order_by(BTTCOUser.name))
    return result.scalars().all()


@router.get("/artist-account-status", response_model=list[UserAccountStatus])
async def list_artist_account_status(
    response: Response,
    db: AsyncSession = Depends(get_db),
    _caller: BTTCOUser = Depends(require_role("founder")),
):
    """Owner-only artist credential evidence; never select passwords or tokens."""
    response.headers["Cache-Control"] = "no-store"
    result = await db.execute(
        select(
            BTTCOUser.id,
            BTTCOUser.must_reset_password,
            BTTCOUser.password_state,
            BTTCOUser.password_changed_at,
            BTTCOUser.password_change_method,
            BTTCOUser.last_login_at,
            BTTCOUser.last_login_method,
        )
        .where(BTTCOUser.role == "artist")
        .order_by(BTTCOUser.id)
    )
    return [
        UserAccountStatus(
            id=row.id,
            password_status=password_status(row),
            password_changed_at=row.password_changed_at,
            password_change_method=row.password_change_method,
            last_login_at=row.last_login_at,
            last_login_method=row.last_login_method,
        )
        for row in result.all()
    ]


@router.get("/my-clients", response_model=list[UserResponse])
async def list_my_clients(
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(require_role("artist")),
):
    """Artist: list distinct clients from your bookings."""
    # Get unique client_ids from bookings assigned to this artist
    client_ids_q = (
        select(Booking.client_id).where(Booking.artist_id == current_user.id, Booking.client_id.isnot(None)).distinct()
    )
    result = await db.execute(select(BTTCOUser).where(BTTCOUser.id.in_(client_ids_q)).order_by(BTTCOUser.name))
    return result.scalars().all()


@router.get("/me/membership", response_model=UserMembershipResponse)
async def get_my_membership(
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Return the current user's effective AI and portal membership state."""
    decision = await resolve_ai_lane(current_user, db, get_settings(), enforce_limits=False)
    return UserMembershipResponse(
        configured_tier=decision.configured_tier,
        effective_tier=decision.effective_tier,
        membership_status=decision.membership_status,
        ai_monthly_message_cap=decision.ai_monthly_message_cap,
        ai_messages_used=decision.ai_messages_used,
        ai_messages_remaining=decision.ai_messages_remaining,
        primary_model=decision.primary_model,
        fallback_model=decision.fallback_models[0] if decision.fallback_models else None,
        early_access_galax3=decision.early_access_galax3,
        connected_apps_unlocked=decision.connected_apps_unlocked,
        product_entitlements=decision.product_entitlements,
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Get a full profile for self, admin, or an assigned artist relationship."""
    can_access = current_user.id == user_id or current_user.role in {"admin", "founder"}
    if not can_access and current_user.role == "artist":
        can_access = await _artist_has_client_relationship(db, current_user.id, user_id)
    if not can_access:
        raise HTTPException(status_code=403, detail="Not authorized to view this user profile")

    result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/me", response_model=UserResponse)
async def update_my_profile(
    body: UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Update the calling user's own profile fields."""
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(current_user, key, value)
    if current_user.role == "collector" and profile_is_complete(current_user):
        await grant_configured_reward(
            db,
            current_user,
            "profile.completed",
            str(current_user.id),
        )
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/me/avatar", response_model=UserResponse)
async def upload_user_avatar(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Any authenticated user: upload a profile picture."""
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(400, f"File type {file.content_type} not allowed")

    ext = Path(file.filename or "avatar.jpg").suffix or ".jpg"
    filename = f"{uuid_mod.uuid4().hex}{ext}"
    content = await file.read()

    # Upload to Google Drive (persistent storage)
    drive_url = await upload_to_drive(
        file_bytes=content,
        filename=filename,
        mime_type=file.content_type or "image/jpeg",
        folder_path=f"avatars/{current_user.id}",
    )
    if not drive_url:
        raise HTTPException(500, "Failed to upload avatar — storage unavailable")

    current_user.avatar_url = drive_url

    # Auto-complete 'profile-pic' quest
    await check_and_complete_quest(db, current_user.id, "profile-pic")
    if current_user.role == "collector" and profile_is_complete(current_user):
        await grant_configured_reward(
            db,
            current_user,
            "profile.completed",
            str(current_user.id),
        )

    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/{user_id}/avatar", response_model=UserResponse)
async def upload_user_avatar_admin(
    user_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _caller: BTTCOUser = Depends(require_role("founder")),
):
    """Founder only: upload / replace any user's avatar."""
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(400, f"File type {file.content_type} not allowed")

    result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    ext = Path(file.filename or "avatar.jpg").suffix or ".jpg"
    filename = f"{uuid_mod.uuid4().hex}{ext}"
    content = await file.read()

    url = await upload_to_drive(
        file_bytes=content,
        filename=filename,
        mime_type=file.content_type or "image/jpeg",
        folder_path=f"avatars/{user_id}",
    )
    if not url:
        raise HTTPException(500, "Failed to upload avatar — storage unavailable")

    user.avatar_url = url
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/{user_id}/role", response_model=UserResponse)
async def change_user_role(
    user_id: UUID,
    body: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(require_role("admin")),
):
    """Change another user's role without crossing the founder boundary."""
    if current_user.id == user_id:
        raise HTTPException(status_code=403, detail="Users cannot change their own role")

    result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.role != "founder" and (user.role == "founder" or body.role == "founder"):
        raise HTTPException(status_code=403, detail="Only a founder can assign or change founder roles")

    user.role = body.role
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/{user_id}/membership", response_model=UserResponse)
async def update_user_membership(
    user_id: UUID,
    body: UserMembershipUpdate,
    db: AsyncSession = Depends(get_db),
    _caller: BTTCOUser = Depends(require_role("founder")),
):
    """Founder only: assign plan tier, cap, and connected product entitlements."""
    result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    await db.commit()
    await db.refresh(user)
    return user


class UserProxyUpdate(BaseModel):
    proxy_for: UUID | None = None  # null to clear


@router.patch("/{user_id}/proxy", response_model=UserResponse)
async def set_user_proxy(
    user_id: UUID,
    body: UserProxyUpdate,
    db: AsyncSession = Depends(get_db),
    _caller: BTTCOUser = Depends(require_role("founder")),
):
    """Founder only: delegate a user's inbox to another account.

    When proxy_for is set, that user sees the target's conversations
    and sends messages attributed to the target.
    """
    result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.proxy_for is not None:
        # Verify target exists
        target_result = await db.execute(select(BTTCOUser.id).where(BTTCOUser.id == body.proxy_for))
        if not target_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Proxy target user not found")
        # Prevent circular proxy
        if body.proxy_for == user_id:
            raise HTTPException(status_code=400, detail="User cannot proxy themselves")

    user.proxy_for = body.proxy_for
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    _caller: BTTCOUser = Depends(require_role("founder")),
):
    """Founder only: permanently remove a user account."""
    result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "founder":
        raise HTTPException(status_code=403, detail="Cannot delete another founder account")
    refuse_if_demo_account(user)
    await db.execute(delete(BTTCOUser).where(BTTCOUser.id == user_id))
    await db.commit()
    return Response(status_code=204)


# ── Gala Auto-Reply Toggle ────────────────────────


class GalaAutoReplyUpdate(BaseModel):
    mode: str | None = None  # "transparent", "voice", or null to disable


@router.patch("/me/gala-auto-reply", tags=["gala"])
async def toggle_gala_auto_reply(
    body: GalaAutoReplyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(require_role("artist")),
):
    """
    Toggle Gala auto-reply for the calling artist.

    - mode: "transparent" = Gala responds as itself (identifies as AI assistant)
    - mode: "voice" = Gala responds in the artist's voice/tone
    - mode: null = disable auto-reply
    """
    if body.mode is not None and body.mode not in ("transparent", "voice"):
        raise HTTPException(
            status_code=400,
            detail="mode must be 'transparent', 'voice', or null",
        )

    current_user.gala_auto_reply = body.mode
    await db.commit()
    await db.refresh(current_user)

    return {
        "gala_auto_reply": current_user.gala_auto_reply,
        "message": (f"Gala auto-reply set to '{body.mode}'" if body.mode else "Gala auto-reply disabled"),
    }


# ── Saved Artist Signature ──────────────────────────────────────────────


@router.get("/me/signature")
async def get_saved_signature(
    current_user: BTTCOUser = Depends(require_role("artist")),
):
    """Return the saved artist signature URL (or null)."""
    return {"signature_url": current_user.saved_signature_url}


@router.put("/me/signature")
async def save_signature(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(require_role("artist")),
):
    """
    Save an artist's signature (base64 PNG) to R2 for reuse across waivers.
    Expects { "signature_data": "data:image/png;base64,..." }
    """
    import base64
    import logging

    from app.services.storage import upload_file as upload_to_blob

    sig_data = body.get("signature_data", "")
    if not sig_data:
        raise HTTPException(status_code=422, detail="signature_data is required")

    logger = logging.getLogger("bttco.users")
    try:
        raw = sig_data.split(",", 1)[-1] if "," in sig_data else sig_data
        sig_bytes = base64.b64decode(raw)
        sig_url = await upload_to_blob(
            file_bytes=sig_bytes,
            filename=f"artist-sig-{current_user.id}.png",
            mime_type="image/png",
            folder_path=f"signatures/{current_user.id}",
        )
        current_user.saved_signature_url = sig_url
        await db.commit()
        await db.refresh(current_user)
        return {"signature_url": sig_url}
    except Exception as exc:
        logger.error("Failed to save signature for %s: %s", current_user.id, exc)
        raise HTTPException(status_code=500, detail="Failed to upload signature") from exc
