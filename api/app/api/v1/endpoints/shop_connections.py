"""The shop's own connections for email and texts (Connections screen in the hub).

Email: the shop sends from the platform sender by default, or from its own
domain once it is verified (app.services.shop_email_domain).
Texts: a Twilio sub-account and toll-free number for the shop on the platform,
or the shop's own Twilio account (app.services.shop_texts).
Owners and admins manage both. On the demo shop nothing reaches the providers.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.services import shop_email_domain, shop_texts
from app.services.shop_email_domain import EmailDomainError
from app.services.shop_texts import TextsError

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


# ── Texts ──────────────────────────────────────────────


class TextsNumber(BaseModel):
    phone_number: str = Field(min_length=8, max_length=16)


class TextsOwn(BaseModel):
    account_sid: str = Field(min_length=34, max_length=34)
    auth_token: str = Field(min_length=16, max_length=128)
    phone_number: str = Field(min_length=8, max_length=16)


class TextsVerification(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    business_type: str = Field(pattern="^(PRIVATE_PROFIT|SOLE_PROPRIETOR|NON_PROFIT)$")
    ein: str | None = Field(default=None, max_length=20)
    website: str = Field(min_length=8, max_length=300, pattern="^https://")
    notification_email: str = Field(min_length=5, max_length=200)
    contact_first_name: str = Field(min_length=1, max_length=60)
    contact_last_name: str = Field(min_length=1, max_length=60)
    contact_phone: str = Field(min_length=8, max_length=16)
    street: str = Field(min_length=3, max_length=200)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=50)
    postal_code: str = Field(min_length=3, max_length=12)
    use_case: str
    use_case_summary: str = Field(min_length=20, max_length=500)
    message_sample: str = Field(min_length=10, max_length=1000)
    opt_in_type: str
    opt_in_image_url: str = Field(min_length=8, max_length=500, pattern="^https://")
    volume: str


async def _texts(work):
    try:
        return await work
    except TextsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/texts", dependencies=[OWNERS])
async def texts_connection():
    """The shop's texting line: how it is set up, its number and whether it may send."""
    return shop_texts.describe()


@router.post("/texts/platform", dependencies=[OWNERS])
async def start_platform_texts(db: AsyncSession = Depends(get_db, scope="function")):
    return await _texts(shop_texts.start_platform(db))


@router.get("/texts/numbers", dependencies=[OWNERS])
async def find_texts_numbers(area_code: str | None = None):
    return {"numbers": await _texts(shop_texts.find_numbers(area_code))}


@router.post("/texts/number", dependencies=[OWNERS])
async def buy_texts_number(body: TextsNumber, db: AsyncSession = Depends(get_db, scope="function")):
    return await _texts(shop_texts.buy_number(db, body.phone_number))


@router.post("/texts/verification", dependencies=[OWNERS])
async def submit_texts_verification(body: TextsVerification, db: AsyncSession = Depends(get_db, scope="function")):
    if body.business_type != "SOLE_PROPRIETOR" and not (body.ein or "").strip():
        raise HTTPException(status_code=400, detail="A registered business needs its EIN.")
    return await _texts(shop_texts.submit_verification(db, body.model_dump()))


@router.post("/texts/check", dependencies=[OWNERS])
async def check_texts(db: AsyncSession = Depends(get_db, scope="function")):
    return await _texts(shop_texts.check(db))


@router.post("/texts/own", dependencies=[OWNERS])
async def connect_own_texts(body: TextsOwn, db: AsyncSession = Depends(get_db, scope="function")):
    return await _texts(shop_texts.connect_own(db, body.account_sid, body.auth_token, body.phone_number))


@router.delete("/texts", dependencies=[OWNERS])
async def disconnect_texts(db: AsyncSession = Depends(get_db, scope="function")):
    return await _texts(shop_texts.disconnect(db))
