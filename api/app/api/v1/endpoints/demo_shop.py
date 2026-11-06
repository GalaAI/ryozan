"""Reset of the shared judge shop (app.services.demo_shop), for the nightly job and by hand."""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.services.demo_shop import demo_shop_enabled, reset_demo_shop

router = APIRouter(prefix="/demo-shop", tags=["demo-shop"])


@router.post("/reset")
async def reset(
    db: AsyncSession = Depends(get_db, scope="function"),
    x_cron_secret: str = Header(alias="X-Cron-Secret"),
):
    """Remove everything made since the seed and write the seeded shop back."""
    settings = get_settings()
    if not settings.CRON_SECRET or x_cron_secret != settings.CRON_SECRET:
        raise HTTPException(status_code=403, detail="Invalid cron secret")
    if not demo_shop_enabled():
        raise HTTPException(status_code=404, detail="This install is not a demo shop")
    return await reset_demo_shop(db)
