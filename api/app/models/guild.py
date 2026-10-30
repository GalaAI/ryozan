"""
Guild models — Leaderboard, Classes, Inspirations, Gauntlet.

These extend the Quest system to form the full Guild gamification layer.
All tables use bttco_ prefix for multi-tenant readiness.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# ── Leaderboard ────────────────────────────────


class LeaderboardEntry(Base):
    """Pre-computed leaderboard snapshots. Regenerated periodically (cron/n8n)."""

    __tablename__ = "bttco_leaderboard_entries"
    __table_args__ = (UniqueConstraint("user_id", "period", "board_type", name="uq_leaderboard_user_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="CASCADE"), nullable=False
    )
    board_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="xp"
    )  # xp, bttcoin, quests_completed, sessions
    period: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "2026-03" (monthly), "2026-W12" (weekly), "all-time"
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


# ── Classes (Group Learning) ──────────────────


class GuildClass(Base):
    """Learning module definitions — workshops, tutorials, skill sessions."""

    __tablename__ = "bttco_classes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="technique"
    )  # technique, business, safety, design, culture
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False, default="beginner"
    )  # beginner, intermediate, advanced
    xp_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coin_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # structured lesson content
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ClassEnrollment(Base):
    """User enrollment and progress in a class."""

    __tablename__ = "bttco_class_enrollments"
    __table_args__ = (UniqueConstraint("user_id", "class_id", name="uq_class_enrollment"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="CASCADE"), nullable=False
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_classes.id", ondelete="CASCADE"), nullable=False
    )
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0-100
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


# ── Inspire (Shared References) ───────────────


class Inspiration(Base):
    """Shared reference images, links, mood boards."""

    __tablename__ = "bttco_inspirations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # ["traditional", "color", "japanese"]
    likes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


# ── Gauntlet (Challenges) ─────────────────────


class GauntletChallenge(Base):
    """Challenge definitions — mini games, drills, timed trials."""

    __tablename__ = "bttco_gauntlet_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    challenge_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="drill"
    )  # drill, timed, quiz, creative
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False, default="beginner"
    )  # beginner, intermediate, advanced, master
    xp_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coin_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # challenge-specific settings
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class GauntletAttempt(Base):
    """User attempts at a gauntlet challenge."""

    __tablename__ = "bttco_gauntlet_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_users.id", ondelete="CASCADE"), nullable=False
    )
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bttco_gauntlet_challenges.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    time_taken_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # attempt-specific results
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
