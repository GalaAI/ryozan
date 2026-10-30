"""
Quest auto-completion hooks — called from various endpoints when
trigger actions occur (booking confirmed, avatar uploaded, etc.).

Usage:
    from app.services.quest_hooks import check_and_complete_quest
    await check_and_complete_quest(db, user_id, "first-booking")
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.routes import ROUTES
from app.models.notification import Notification
from app.models.quest import Quest, UserQuest

logger = logging.getLogger("bttco.quest_hooks")


async def check_and_complete_quest(
    db: AsyncSession,
    user_id: UUID,
    quest_slug: str,
) -> bool:
    """
    Auto-complete a quest when its trigger action happens.

    Returns True if the quest was newly completed, False otherwise
    (already complete, quest not found, quest inactive, etc.).
    """
    # Find quest by slug
    result = await db.execute(select(Quest).where(and_(Quest.slug == quest_slug, Quest.is_active)))
    quest = result.scalar_one_or_none()
    if not quest:
        logger.debug("Quest '%s' not found or inactive, skipping", quest_slug)
        return False

    # Check if UserQuest exists
    uq_result = await db.execute(
        select(UserQuest).where(and_(UserQuest.user_id == user_id, UserQuest.quest_id == quest.id))
    )
    uq = uq_result.scalar_one_or_none()

    now = datetime.now(UTC)

    if uq and uq.completed:
        # Already done
        return False

    if not uq:
        # Create completed UserQuest
        uq = UserQuest(
            user_id=user_id,
            quest_id=quest.id,
            progress=quest.target_count,
            completed=True,
            completed_at=now,
        )
        db.add(uq)
    else:
        # Exists but not complete — mark it done
        uq.progress = quest.target_count
        uq.completed = True
        uq.completed_at = now

    # Create achievement notification
    notification = Notification(
        user_id=user_id,
        title=f"Quest Complete: {quest.title}",
        body=f'You completed the "{quest.title}" quest! Claim your reward.',
        notification_type="achievement",
        action_url=ROUTES.QUESTS,
    )
    db.add(notification)

    await db.flush()
    logger.info("Quest '%s' auto-completed for user %s", quest_slug, user_id)
    return True
