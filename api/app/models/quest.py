"""
Quest models — ryozan_quests (quest definitions) + ryozan_user_quests (per-user progress).

Quests are the gamification layer of the Collector's Club. Each quest has
XP and RYO rewards. Users track progress and claim rewards when complete.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Quest(Base):
    __tablename__ = "ryozan_quests"
    # Redundant with the slug unique constraint (ryozan_quests_slug_key), but the
    # schema has both; declared so autogenerate does not propose dropping it.
    __table_args__ = (Index("idx_ryozan_quests_slug_unique", "slug", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="onboarding"
    )  # onboarding, loyalty, social, seasonal
    xp_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coin_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requirement: Mapped[str] = mapped_column(String(255), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class UserQuest(Base):
    __tablename__ = "ryozan_user_quests"
    __table_args__ = (UniqueConstraint("user_id", "quest_id", name="uq_user_quest"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_users.id", ondelete="CASCADE"), nullable=False
    )
    quest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_quests.id", ondelete="CASCADE"), nullable=False
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
