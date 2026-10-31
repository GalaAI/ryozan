"""
Web Push notification service.

Sends push notifications to all of a user's registered devices.
Automatically cleans up stale subscriptions (410 Gone).
"""

import json
import logging
from uuid import UUID

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)
settings = get_settings()


async def send_push_notification(
    db: AsyncSession,
    user_id: UUID,
    title: str,
    body: str,
    url: str | None = None,
) -> int:
    """
    Send a web push notification to all of a user's subscribed devices.

    Returns the number of successful deliveries.
    On 410 Gone, the stale subscription is deleted automatically.
    """
    if settings.LOCAL_DEV_MODE:
        logger.info("[LOCAL_DEV_MODE] push skipped for user %s title=%s", user_id, title)
        return 0

    result = await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))
    subscriptions = result.scalars().all()

    if not subscriptions:
        return 0

    if not settings.VAPID_PRIVATE_KEY or not settings.VAPID_PUBLIC_KEY:
        logger.warning("VAPID keys not configured — skipping push notifications")
        return 0

    if not settings.vapid_subject:
        # Push services reject a VAPID token without a `sub` contact, and
        # py_vapid raises before sending. Say which setting fixes it.
        logger.error("Push skipped: set VAPID_SUBJECT (mailto: or https://) or SHOP_EMAIL to enable Web Push")
        return 0

    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "icon": "/assets/images/logo-icon.png",
            "data": {"url": url or "/"},
        }
    )

    vapid_claims = {
        "sub": settings.vapid_subject,
    }

    sent = 0
    stale_ids = []

    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh,
                "auth": sub.auth,
            },
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=vapid_claims,
            )
            sent += 1
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                # Subscription expired — mark for cleanup
                stale_ids.append(sub.id)
                logger.info(f"Push subscription expired for user {user_id}, cleaning up")
            else:
                logger.error(f"Push failed for user {user_id}: {e}")

    # Clean up stale subscriptions
    if stale_ids:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(stale_ids)))

    return sent
