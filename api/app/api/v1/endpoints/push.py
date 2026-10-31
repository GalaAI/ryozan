"""
Push notification endpoints — Web Push subscription management.

- Subscribe a device to push notifications
- Unsubscribe a device
- Get VAPID public key (no auth required)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services.quest_hooks import check_and_complete_quest

router = APIRouter(prefix="/push", tags=["push"])
settings = get_settings()


# ── Schemas ─────────────────────────────────────────────


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: PushKeys


class VapidKeyResponse(BaseModel):
    public_key: str


# ── Endpoints ───────────────────────────────────────────


@router.get("/vapid-key", response_model=VapidKeyResponse)
async def get_vapid_key():
    """Return the public VAPID key for client-side push subscription. No auth required."""
    if not settings.VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="Push notifications not configured")
    if not settings.vapid_subject:
        # A browser that subscribes now would never receive a push: sends need a
        # VAPID subject. Refuse here so the client does not offer push at all.
        raise HTTPException(
            status_code=503,
            detail="Push notifications not configured: set VAPID_SUBJECT or SHOP_EMAIL",
        )
    return VapidKeyResponse(public_key=settings.VAPID_PUBLIC_KEY)


@router.post("/subscribe", status_code=201)
async def subscribe_push(
    body: PushSubscribeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save a push subscription for the current user.

    If the same endpoint already exists for this user, it's updated in place.
    """
    # Check for existing subscription with same endpoint
    result = await db.execute(
        select(PushSubscription).where(
            and_(
                PushSubscription.user_id == current_user.id,
                PushSubscription.endpoint == body.endpoint,
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Update keys in case they rotated
        existing.p256dh = body.keys.p256dh
        existing.auth = body.keys.auth
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
        )
        db.add(sub)

    # Auto-complete the "enable-notifications" (Signal Flare) quest
    await check_and_complete_quest(db, current_user.id, "enable-notifications")

    await db.commit()
    return {"status": "subscribed"}


@router.delete("/subscribe", status_code=204)
async def unsubscribe_push(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove all push subscriptions for the current user."""
    await db.execute(delete(PushSubscription).where(PushSubscription.user_id == current_user.id))
