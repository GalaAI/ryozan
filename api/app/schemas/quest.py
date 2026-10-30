"""Pydantic schemas for quests and user quest progress."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class QuestResponse(BaseModel):
    id: UUID
    slug: str
    title: str
    description: str
    category: str
    xp_reward: int
    coin_reward: int
    requirement: str
    target_count: int
    is_active: bool
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class UserQuestResponse(BaseModel):
    id: UUID
    user_id: UUID
    quest_id: UUID
    progress: int
    completed: bool
    claimed: bool
    completed_at: datetime | None = None
    claimed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class QuestWithProgress(BaseModel):
    """Quest definition merged with user's progress."""

    quest: QuestResponse
    progress: int = 0
    completed: bool = False
    claimed: bool = False
    completed_at: datetime | None = None
    claimed_at: datetime | None = None


class QuestListResponse(BaseModel):
    quests: list[QuestWithProgress]
    total: int


class QuestSummary(BaseModel):
    total_quests: int
    completed_count: int
    claimed_count: int
    total_xp_earned: int
    total_coin_earned: int


class QuestClaimResponse(BaseModel):
    quest: QuestResponse
    xp_awarded: int
    coin_awarded: int
    new_quest_points: int
    new_bttcoin_balance: int


class QuestProgressUpdate(BaseModel):
    progress: int = Field(ge=0)
