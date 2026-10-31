"""
Notification endpoints — user notification feed.

- List my notifications (with unread count)
- Mark individual as read
- Mark all as read
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationListResponse, NotificationResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = False,
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List notifications for the current user."""
    base_filter = [Notification.user_id == current_user.id]
    if unread_only:
        base_filter.append(Notification.is_read.is_(False))

    where = and_(*base_filter)

    # Total matching
    count_result = await db.execute(select(func.count()).select_from(Notification).where(where))
    total = count_result.scalar()

    # Unread count (always show this regardless of filter)
    unread_result = await db.execute(
        select(func.count()).where(and_(Notification.user_id == current_user.id, Notification.is_read.is_(False)))
    )
    unread_count = unread_result.scalar()

    # Fetch
    result = await db.execute(
        select(Notification).where(where).order_by(desc(Notification.created_at)).offset(skip).limit(limit)
    )
    notifications = result.scalars().all()

    items = [
        NotificationResponse(
            id=n.id,
            user_id=n.user_id,
            title=n.title,
            body=n.body,
            notification_type=n.notification_type,
            is_read=n.is_read,
            action_url=n.action_url,
            created_at=n.created_at,
        )
        for n in notifications
    ]

    return NotificationListResponse(
        notifications=items,
        total=total,
        unread_count=unread_count,
    )


@router.post("/{notification_id}/read", status_code=204)
async def mark_notification_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a single notification as read."""
    result = await db.execute(
        select(Notification).where(and_(Notification.id == notification_id, Notification.user_id == current_user.id))
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    notif.is_read = True
    await db.flush()


@router.post("/read-all", status_code=204)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark all of the current user's notifications as read."""
    await db.execute(
        update(Notification)
        .where(and_(Notification.user_id == current_user.id, Notification.is_read.is_(False)))
        .values(is_read=True)
    )
    await db.flush()


@router.delete("/{notification_id}", status_code=204)
async def dismiss_notification(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a single notification."""
    result = await db.execute(
        select(Notification).where(and_(Notification.id == notification_id, Notification.user_id == current_user.id))
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.delete(notif)
    await db.flush()


@router.delete("/", status_code=204)
async def clear_all_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete all notifications for the current user."""
    await db.execute(delete(Notification).where(Notification.user_id == current_user.id))
    await db.flush()
