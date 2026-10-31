"""AI Memory model - persistent memory for Gala/Smoku agents."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class AIMemory(Base):
    __tablename__ = "ryozan_ai_memories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ryozan_users.id", ondelete="CASCADE"), nullable=False)
    agent = Column(String(20), nullable=False, default="gala")
    category = Column(String(30), nullable=False)
    key = Column(String(100), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=0.5)
    source = Column(String(30), nullable=False, default="inferred")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "agent", "category", "key", name="uq_memory_user_agent_cat_key"),)
