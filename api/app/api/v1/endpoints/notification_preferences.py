"""
Notification preferences endpoints — per-user opt-in/out controls.

- Get current user's preferences (auto-creates defaults if none exist)
- Partial update preferences
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.notification_preferences import NotificationPreferences
from app.models.user import BTTCOUser
from app.schemas.notification_preferences import (
    NotificationPreferencesResponse,
    NotificationPreferencesUpdate,
)

router = APIRouter(prefix="/notification-preferences", tags=["notification-preferences"])


async def _get_or_create_prefs(user_id, db: AsyncSession) -> NotificationPreferences:
    """Return existing prefs or create a default row."""
    result = await db.execute(select(NotificationPreferences).where(NotificationPreferences.user_id == user_id))
    prefs = result.scalar_one_or_none()
    if prefs:
        return prefs

    prefs = NotificationPreferences(user_id=user_id)
    db.add(prefs)
    await db.flush()
    return prefs


@router.get("/", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Get current user's notification preferences (creates defaults if none)."""
    prefs = await _get_or_create_prefs(current_user.id, db)
    return prefs


@router.patch("/", response_model=NotificationPreferencesResponse)
async def update_notification_preferences(
    body: NotificationPreferencesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Update notification preferences (partial — only send fields you want to change)."""
    prefs = await _get_or_create_prefs(current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(prefs, field, value)

    prefs.updated_at = datetime.now(UTC)
    await db.flush()
    return prefs
