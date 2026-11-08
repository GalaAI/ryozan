"""The shop's own connections for email and texts (Connections screen in the hub).

Email: the shop sends from the platform sender by default, or from its own
domain once it is verified (app.services.shop_email_domain). Owners and admins
manage it. On the demo shop nothing reaches the provider.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.services import shop_email_domain
from app.services.shop_email_domain import EmailDomainError

router = APIRouter(prefix="/connections", tags=["connections"])
OWNERS = Depends(require_role("admin"))


class EmailDomainCreate(BaseModel):
    domain: str = Field(min_length=4, max_length=253)
    from_local: str = Field(default="hello", min_length=1, max_length=64)


def _refused(exc: EmailDomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/email", dependencies=[OWNERS])
async def email_connection():
    """Where the shop's mail goes out from, and its own domain if it added one."""
    return shop_email_domain.describe()


@router.post("/email/domain", dependencies=[OWNERS])
async def add_email_domain(body: EmailDomainCreate, db: AsyncSession = Depends(get_db, scope="function")):
    try:
        return await shop_email_domain.add_domain(db, body.domain, body.from_local)
    except EmailDomainError as exc:
        raise _refused(exc) from exc


@router.post("/email/domain/check", dependencies=[OWNERS])
async def check_email_domain(db: AsyncSession = Depends(get_db, scope="function")):
    try:
        return await shop_email_domain.check_domain(db)
    except EmailDomainError as exc:
        raise _refused(exc) from exc


@router.delete("/email/domain", dependencies=[OWNERS])
async def remove_email_domain(db: AsyncSession = Depends(get_db, scope="function")):
    try:
        return await shop_email_domain.remove_domain(db)
    except EmailDomainError as exc:
        raise _refused(exc) from exc
