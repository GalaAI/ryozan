"""
Admin endpoint — API usage / budget tracking.

GET /api/v1/admin/api-usage   — usage summary + log (admin+ only)
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_role
from app.models.api_usage import APIUsage
from app.models.user import BTTCOUser

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/api-usage")
async def get_api_usage(
    db: AsyncSession = Depends(get_db),
    _: BTTCOUser = Depends(require_role("admin")),
):
    """Return this month's API usage summary and the last 100 log entries."""
    settings = get_settings()
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Monthly totals
    totals_result = await db.execute(
        select(
            func.coalesce(func.sum(APIUsage.input_tokens), 0).label("total_input"),
            func.coalesce(func.sum(APIUsage.output_tokens), 0).label("total_output"),
            func.coalesce(func.sum(APIUsage.estimated_cost), 0.0).label("total_cost"),
            func.count(APIUsage.id).label("call_count"),
        ).where(APIUsage.created_at >= month_start)
    )
    totals = totals_result.one()

    # Per-endpoint breakdown
    breakdown_result = await db.execute(
        select(
            APIUsage.endpoint,
            func.count(APIUsage.id).label("calls"),
            func.sum(APIUsage.estimated_cost).label("cost"),
        )
        .where(APIUsage.created_at >= month_start)
        .group_by(APIUsage.endpoint)
        .order_by(func.sum(APIUsage.estimated_cost).desc())
    )
    breakdown = [
        {"endpoint": row.endpoint, "calls": row.calls, "cost": round(float(row.cost or 0), 6)}
        for row in breakdown_result.all()
    ]

    # Recent log (last 100)
    recent_result = await db.execute(select(APIUsage).order_by(APIUsage.created_at.desc()).limit(100))
    recent = [
        {
            "id": str(r.id),
            "user_id": str(r.user_id) if r.user_id else None,
            "endpoint": r.endpoint,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "estimated_cost": round(r.estimated_cost, 6),
            "created_at": r.created_at.isoformat(),
        }
        for r in recent_result.scalars().all()
    ]

    return {
        "period": {"start": month_start.isoformat(), "end": now.isoformat()},
        "budget_cap": settings.ANTHROPIC_MONTHLY_BUDGET_CAP,
        "totals": {
            "calls": totals.call_count,
            "input_tokens": totals.total_input,
            "output_tokens": totals.total_output,
            "estimated_cost": round(float(totals.total_cost), 4),
            "remaining_budget": round(settings.ANTHROPIC_MONTHLY_BUDGET_CAP - float(totals.total_cost), 4),
        },
        "by_endpoint": breakdown,
        "recent_calls": recent,
    }
