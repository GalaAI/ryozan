"""
Quest endpoints — gamification layer for the Collector's Club.

- GET  /quests/          — list all active quests with user progress
- GET  /quests/summary   — completion stats + totals
- POST /quests/{id}/claim — claim reward for a completed quest
- POST /quests/{id}/progress — update progress (staff only)
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.quest import Quest, UserQuest
from app.models.user import BTTCOUser
from app.schemas.quest import (
    QuestClaimResponse,
    QuestListResponse,
    QuestProgressUpdate,
    QuestResponse,
    QuestSummary,
    QuestWithProgress,
)
from app.services.quest_hooks import check_and_complete_quest
from app.services.reward_engine import grant_reward

router = APIRouter(prefix="/quests", tags=["quests"])


# A quest is listed only while this install can complete it. These have no
# completion trigger anywhere in the app yet.
_NO_COMPLETION_PATH = {"add-to-homescreen", "three-sessions", "leave-review", "refer-friend"}


def outbound_email_configured() -> bool:
    """Whether this install can send mail (verification links, forwarding confirmations)."""
    settings = get_settings()
    return bool(settings.RESEND_API_KEY or settings.SENDGRID_API_KEY or settings.SMTP_PASSWORD)


def quest_available(slug: str) -> bool:
    """Whether a client could finish this quest on this install today."""
    if slug in _NO_COMPLETION_PATH:
        return False
    settings = get_settings()
    if slug == "enable-notifications":
        return bool(settings.VAPID_PUBLIC_KEY)
    if slug in {"connect-email", "email-alias"}:
        # The handle is confirmed by a verification email.
        return outbound_email_configured()
    return True


def _require_staff(user: BTTCOUser):
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("artist", 99):
        raise HTTPException(status_code=403, detail="Staff access required")


@router.get("/", response_model=QuestListResponse)
async def list_quests(
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """List all active quests with the current user's progress."""
    query = select(Quest).where(Quest.is_active).order_by(Quest.sort_order, Quest.created_at)
    if category:
        query = query.where(Quest.category == category)

    result = await db.execute(query)
    quests = [q for q in result.scalars().all() if quest_available(q.slug)]

    # Fetch user progress for all quests in one query
    uq_result = await db.execute(
        select(UserQuest).where(
            and_(
                UserQuest.user_id == current_user.id,
                UserQuest.quest_id.in_([q.id for q in quests]),
            )
        )
    )
    progress_map = {uq.quest_id: uq for uq in uq_result.scalars().all()}

    items = []
    for q in quests:
        uq = progress_map.get(q.id)
        items.append(
            QuestWithProgress(
                quest=QuestResponse.model_validate(q),
                progress=uq.progress if uq else 0,
                completed=uq.completed if uq else False,
                claimed=uq.claimed if uq else False,
                completed_at=uq.completed_at if uq else None,
                claimed_at=uq.claimed_at if uq else None,
            )
        )

    return QuestListResponse(quests=items, total=len(items))


@router.get("/summary", response_model=QuestSummary)
async def quest_summary(
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Get quest completion stats for the current user."""
    active_result = await db.execute(select(Quest.slug).where(Quest.is_active))
    total_quests = sum(1 for slug in active_result.scalars().all() if quest_available(slug))

    uq_result = await db.execute(select(UserQuest).where(UserQuest.user_id == current_user.id))
    user_quests = uq_result.scalars().all()

    completed_count = sum(1 for uq in user_quests if uq.completed)
    claimed_count = sum(1 for uq in user_quests if uq.claimed)

    # Calculate earned rewards from claimed quests
    if claimed_count > 0:
        claimed_ids = [uq.quest_id for uq in user_quests if uq.claimed]
        quest_result = await db.execute(select(Quest).where(Quest.id.in_(claimed_ids)))
        claimed_quests = quest_result.scalars().all()
        total_xp = sum(q.xp_reward for q in claimed_quests)
        total_coin = sum(q.coin_reward for q in claimed_quests)
    else:
        total_xp = 0
        total_coin = 0

    return QuestSummary(
        total_quests=total_quests,
        completed_count=completed_count,
        claimed_count=claimed_count,
        total_xp_earned=total_xp,
        total_coin_earned=total_coin,
    )


@router.post("/{quest_id}/claim", response_model=QuestClaimResponse)
async def claim_quest(
    quest_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Claim the reward for a completed quest."""
    # Fetch quest
    quest_result = await db.execute(select(Quest).where(Quest.id == quest_id))
    quest = quest_result.scalar_one_or_none()
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    # Fetch or create user quest record
    uq_result = await db.execute(
        select(UserQuest).where(and_(UserQuest.user_id == current_user.id, UserQuest.quest_id == quest_id))
    )
    uq = uq_result.scalar_one_or_none()

    if not uq or not uq.completed:
        raise HTTPException(status_code=400, detail="Quest not completed yet")
    if uq.claimed:
        raise HTTPException(status_code=400, detail="Quest reward already claimed")

    # Mark claimed
    now = datetime.now(UTC)
    uq.claimed = True
    uq.claimed_at = now

    # Award through the durable reward ledger. UserQuest remains the milestone
    # owner while RewardGrant owns the balance receipt and idempotency key.
    await grant_reward(
        db,
        current_user,
        event_type="quest.claimed",
        source_id=str(quest.id),
        xp=quest.xp_reward,
        coin=quest.coin_reward,
        description=f"Claimed quest: {quest.title}",
        metadata={"quest_slug": quest.slug},
    )

    await db.flush()

    return QuestClaimResponse(
        quest=QuestResponse.model_validate(quest),
        xp_awarded=quest.xp_reward,
        coin_awarded=quest.coin_reward,
        new_quest_points=current_user.quest_points,
        new_bttcoin_balance=current_user.bttcoin_balance,
    )


@router.post("/{quest_id}/progress", response_model=QuestWithProgress)
async def update_quest_progress(
    quest_id: UUID,
    body: QuestProgressUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Staff-only manual override of quest progress.

    Legitimate progress does not come through here. It comes from
    `check_and_complete_quest`, which fires on the real action across ten
    triggers: bookings, waivers, profile pictures, email connect, push
    registration, club posts and Stripe payments. This endpoint is the manual
    escape hatch for staff fixing a stuck record.

    It was previously ungated. `_require_staff` was defined and never called,
    so any collector could POST their own progress straight to the target
    count, auto-complete the quest, and claim the XP and BTTCOIN without ever
    performing the action. That is harmless only while BTTCOIN has no
    redemption path, and stops being harmless the day a member store ships.

    Nothing in the app called it: `quests.updateProgress` exists in
    `app/src/lib/api.ts` with no caller anywhere, so gating it breaks no
    client.
    """
    _require_staff(current_user)
    # Fetch quest
    quest_result = await db.execute(select(Quest).where(Quest.id == quest_id))
    quest = quest_result.scalar_one_or_none()
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    # Get or create user quest record
    uq_result = await db.execute(
        select(UserQuest).where(and_(UserQuest.user_id == current_user.id, UserQuest.quest_id == quest_id))
    )
    uq = uq_result.scalar_one_or_none()

    if not uq:
        uq = UserQuest(user_id=current_user.id, quest_id=quest_id, progress=0)
        db.add(uq)
        await db.flush()

    # Update progress
    uq.progress = min(body.progress, quest.target_count)

    # Auto-complete if target met
    if uq.progress >= quest.target_count and not uq.completed:
        uq.completed = True
        uq.completed_at = datetime.now(UTC)

    await db.flush()

    return QuestWithProgress(
        quest=QuestResponse.model_validate(quest),
        progress=uq.progress,
        completed=uq.completed,
        claimed=uq.claimed,
        completed_at=uq.completed_at,
        claimed_at=uq.claimed_at,
    )


@router.post("/check-mobile")
async def check_mobile_quest(
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Frontend calls this when it detects it's running as a PWA
    (display-mode: standalone). Auto-completes the 'add-to-homescreen' quest.
    """
    completed = await check_and_complete_quest(db, current_user.id, "add-to-homescreen")
    return {"completed": completed}


@router.post("/push-subscription")
async def save_push_subscription(
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Save the user's push notification subscription and auto-complete
    the 'enable-notifications' quest. The frontend sends this after the user
    grants notification permission and the service worker registers.
    """
    # TODO: persist subscription payload to a push_subscriptions table
    # when that feature is built out. For now, just complete the quest.

    completed = await check_and_complete_quest(db, current_user.id, "enable-notifications")
    return {"completed": completed}
