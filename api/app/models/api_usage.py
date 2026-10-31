"""
API usage tracking model — logs every Anthropic call for budget management.

Table: bttco_api_usage
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Approximate cost per token in USD (claude-sonnet-4-20250514 as of 2026-04)
# Input:  $3.00 / 1M tokens  →  0.000003 per token
# Output: $15.00 / 1M tokens →  0.000015 per token
COST_PER_INPUT_TOKEN = 0.000003
COST_PER_OUTPUT_TOKEN = 0.000015


class APIUsage(Base):
    """One row per Anthropic API call made by any BTTCO endpoint."""

    __tablename__ = "bttco_api_usage"
    __table_args__ = (Index("idx_bttco_api_usage_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
