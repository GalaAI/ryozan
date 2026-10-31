"""Idempotent reward rules for verified client activity."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models.reward import RewardGrant, RewardOffer
from app.models.user import User

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RewardRule:
    xp: int
    coin: int
    description: str
    minigame_eligible: bool = False


REWARD_RULES: dict[str, RewardRule] = {
    "portal.daily_visit": RewardRule(5, 1, "Used the Ryozan portal today", True),
    "profile.completed": RewardRule(40, 10, "Completed a collector profile", True),
    "booking.inquiry_submitted": RewardRule(20, 5, "Sent a tattoo inquiry", True),
    "booking.deposit_paid": RewardRule(50, 20, "Paid a booking deposit", True),
    "appointment.completed": RewardRule(150, 50, "Completed a tattoo appointment", True),
    "conversation.artist_interaction": RewardRule(5, 1, "Connected with an artist", False),
}

MINIGAME_INTERVAL = 4
MINIGAME_UNLOCKS = (
    ("art", "arcade-art-drop"),
    ("digital_merch", "collector-digital-drop"),
    ("loot_pack", "bttco-booster-pack"),
)


def profile_is_complete(user: User) -> bool:
    """Return the minimal collector profile completion contract."""
    return bool((user.name or "").strip() and user.avatar_url and (user.reasons or []))


def reward_source_key(event_type: str, source_id: str) -> str:
    return f"{event_type}:{source_id}"


async def _maybe_attach_unlock(db: AsyncSession, grant: RewardGrant) -> None:
    if grant.event_type != "appointment.completed":
        return
    completed_count = await db.scalar(
        select(func.count())
        .select_from(RewardGrant)
        .where(
            RewardGrant.user_id == grant.user_id,
            RewardGrant.event_type == "appointment.completed",
        )
    )
    if completed_count and completed_count % 3 == 0:
        grant.unlock_kind = "loot_pack"
        grant.unlock_slug = "bttco-booster-pack"


async def _maybe_issue_minigame_offer(db: AsyncSession, grant: RewardGrant) -> RewardOffer | None:
    rule = REWARD_RULES.get(grant.event_type)
    if not rule or not rule.minigame_eligible:
        return None

    eligible_types = [event_type for event_type, item in REWARD_RULES.items() if item.minigame_eligible]
    eligible_count = await db.scalar(
        select(func.count())
        .select_from(RewardGrant)
        .where(
            RewardGrant.user_id == grant.user_id,
            RewardGrant.event_type.in_(eligible_types),
        )
    )
    if not eligible_count or eligible_count % MINIGAME_INTERVAL:
        return None

    reward_kind, reward_slug = MINIGAME_UNLOCKS[(eligible_count // MINIGAME_INTERVAL - 1) % len(MINIGAME_UNLOCKS)]
    offer = RewardOffer(
        user_id=grant.user_id,
        trigger_grant_id=grant.id,
        offer_type="mini_game",
        game_slug="tiger-draw",
        reward_kind=reward_kind,
        reward_slug=reward_slug,
        status="offered",
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )
    db.add(offer)
    return offer


async def grant_reward(
    db: AsyncSession,
    user: User,
    *,
    event_type: str,
    source_id: str,
    xp: int,
    coin: int,
    description: str,
    unlock_kind: str | None = None,
    unlock_slug: str | None = None,
    metadata: dict | None = None,
) -> tuple[RewardGrant, bool]:
    """Write exactly one reward receipt and update balances in the same transaction."""
    key = reward_source_key(event_type, source_id)
    await db.flush()
    grant_id = uuid.uuid4()
    inserted_id = await db.scalar(
        insert(RewardGrant)
        .values(
            id=grant_id,
            user_id=user.id,
            event_type=event_type,
            source_id=source_id,
            idempotency_key=key,
            xp_delta=xp,
            coin_delta=coin,
            description=description,
            unlock_kind=unlock_kind,
            unlock_slug=unlock_slug,
            metadata_json=metadata or {},
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(constraint="uq_reward_grant_user_key")
        .returning(RewardGrant.id)
    )
    if not inserted_id:
        existing = await db.scalar(
            select(RewardGrant).where(and_(RewardGrant.user_id == user.id, RewardGrant.idempotency_key == key))
        )
        if not existing:
            raise RuntimeError("Reward idempotency conflict could not be resolved")
        return existing, False

    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(
            quest_points=func.coalesce(User.quest_points, 0) + xp,
            bttcoin_balance=func.coalesce(User.bttcoin_balance, 0) + coin,
        )
    )
    await db.refresh(user, attribute_names=["quest_points", "bttcoin_balance"])
    grant = await db.get(RewardGrant, inserted_id)
    if not grant:
        raise RuntimeError("Inserted reward receipt could not be loaded")
    await _maybe_attach_unlock(db, grant)
    await _maybe_issue_minigame_offer(db, grant)
    await db.flush()
    return grant, True


async def grant_configured_reward(
    db: AsyncSession,
    user: User,
    event_type: str,
    source_id: str,
    *,
    metadata: dict | None = None,
) -> tuple[RewardGrant, bool]:
    rule = REWARD_RULES[event_type]
    return await grant_reward(
        db,
        user,
        event_type=event_type,
        source_id=source_id,
        xp=rule.xp,
        coin=rule.coin,
        description=rule.description,
        metadata=metadata,
    )


async def reward_user_by_id(
    db: AsyncSession,
    user_id: UUID | None,
    event_type: str,
    source_id: str,
    *,
    metadata: dict | None = None,
) -> tuple[RewardGrant | None, bool]:
    if not user_id:
        return None, False
    user = await db.get(User, user_id)
    if not user or user.role != "collector":
        return None, False
    return await grant_configured_reward(db, user, event_type, source_id, metadata=metadata)


async def complete_reward_offer(
    db: AsyncSession,
    user: User,
    offer_id: UUID,
) -> tuple[RewardOffer, RewardGrant]:
    """Complete one earned minigame offer and issue its unlock exactly once."""
    offer = await db.scalar(
        select(RewardOffer).where(RewardOffer.id == offer_id, RewardOffer.user_id == user.id).with_for_update()
    )
    if not offer:
        raise LookupError("Reward offer not found")
    if offer.expires_at and offer.expires_at < datetime.now(UTC):
        offer.status = "expired"
        raise ValueError("Reward offer expired")

    grant, _ = await grant_reward(
        db,
        user,
        event_type="minigame.completed",
        source_id=str(offer.id),
        xp=25,
        coin=8,
        description=f"Completed minigame: {offer.game_slug}",
        unlock_kind=offer.reward_kind,
        unlock_slug=offer.reward_slug,
        metadata={"offer_id": str(offer.id)},
    )
    offer.status = "completed"
    offer.completed_at = offer.completed_at or datetime.now(UTC)
    await db.flush()
    return offer, grant
