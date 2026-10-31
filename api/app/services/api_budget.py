"""
API budget tracking service.

Provides two functions consumed by AI-calling endpoints:
  check_budget(db)      - raises 503 if monthly spend >= cap
  log_usage(db, ...)    - inserts a usage row after a successful API call
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_usage import COST_PER_INPUT_TOKEN, COST_PER_OUTPUT_TOKEN, APIUsage

logger = logging.getLogger("ryozan.api_budget")


async def check_budget(db: AsyncSession, cap: float) -> None:
    """
    Raise a 503 HTTPException if the current month's estimated spend >= cap.
    Call this before making an AI API request.
    """
    from fastapi import HTTPException

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(func.coalesce(func.sum(APIUsage.estimated_cost), 0.0)).where(APIUsage.created_at >= month_start)
    )
    spent: float = result.scalar_one()

    if spent >= cap:
        logger.warning(
            "API budget cap reached: $%.4f spent of $%.2f cap this month",
            spent,
            cap,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                f"AI features are temporarily unavailable - monthly budget cap "
                f"(${cap:.2f}) has been reached. Resets on the 1st."
            ),
        )


async def log_usage(
    db: AsyncSession,
    endpoint: str,
    input_tokens: int,
    output_tokens: int,
    user_id: UUID | None = None,
) -> None:
    """Insert a usage record after a successful AI call. Non-fatal on error."""
    try:
        cost = (input_tokens * COST_PER_INPUT_TOKEN) + (output_tokens * COST_PER_OUTPUT_TOKEN)
        row = APIUsage(
            user_id=user_id,
            endpoint=endpoint,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=cost,
        )
        db.add(row)
        await db.flush()  # caller owns the transaction
    except Exception as exc:
        logger.error("Failed to log API usage for %s: %s", endpoint, exc)
