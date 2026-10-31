"""Durable reward receipts and earned minigame offers.

Net-new artifact justification:
Quest progress stores milestone state but cannot explain wallet balance changes or
inventory unlocks. Finance ledgers record real money, so reward receipts remain
separate and idempotent while preserving an auditable bridge to future minting.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RewardGrant(Base):
    """Append-only XP, BTTCOIN, and inventory receipt."""

    __tablename__ = "ryozan_reward_grants"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_reward_grant_user_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(340), nullable=False)
    xp_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coin_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unlock_kind: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    unlock_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )


class RewardOffer(Base):
    """Server-issued minigame opportunity with a fixed unlock receipt."""

    __tablename__ = "ryozan_reward_offers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger_grant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ryozan_reward_grants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    offer_type: Mapped[str] = mapped_column(String(40), nullable=False, default="mini_game")
    game_slug: Mapped[str] = mapped_column(String(80), nullable=False)
    reward_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    reward_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="offered", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True
    )
