"""
Studio settings and first-run setup.

  GET  /studio/settings        no auth, cacheable: the values a browser needs
  GET  /studio/settings/edit   founder: the values the owner can change
  PUT  /studio/settings/edit   founder: save some of them
  GET  /studio/setup           no auth: does this install still need an owner?
  POST /studio/setup           no auth, needs the setup code: claim a new install

One install is one studio. Everything in GET /studio/settings is read from
Settings, so a new install shows neutral copy until it sets its own values. Only
non-secret values belong there: anything added to that response is world-readable.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.timezones import shop_timezone
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.security import create_user_access_token, hash_password, require_role
from app.models.studio_setting import StudioSetting
from app.models.user import User, normalize_email
from app.schemas.user import Token
from app.services.account_status import record_successful_login
from app.services.artist_slugs import unique_artist_slug
from app.services.studio_config import (
    EDITABLE_SETTINGS,
    SETUP_CLAIM_KEY,
    StudioSettingsError,
    apply_values,
    saved_rows,
    setup_code_matches,
    setup_is_open,
    stage_settings,
    validated_changes,
)

router = APIRouter(prefix="/studio", tags=["studio"])

# An owner's save reaches a stale tab within five minutes.
STUDIO_SETTINGS_CACHE_CONTROL = "public, max-age=300"


class StudioBookingDays(BaseModel):
    # Three-letter weekday names, Sunday first: sun mon tue wed thu fri sat.
    closed_weekdays: list[str]
    request_only_weekdays: list[str]
    # Artists the request-only days apply to; empty means every artist.
    request_only_artist_slugs: list[str]


class StudioBookingHours(BaseModel):
    # Keyed "0" (Monday) to "6" (Sunday), the shape of an artist's working_hours.
    # A closed day is null.
    work: dict[str, list[int] | None]
    consultation: dict[str, list[int] | None]


class StudioSocialLink(BaseModel):
    label: str
    url: str


class StudioSiteSettings(BaseModel):
    """The studio's public website. Blank values hide their section."""

    # PUBLIC_SITE_MODE: "product" shows the Ryozan landing at /, "studio" this studio's site.
    mode: Literal["product", "studio"]
    # STUDIO_SITE_HOSTS: hostnames that get the studio site even in product mode.
    studio_hosts: list[str]
    tagline: str
    story_paragraphs: list[str]
    hours: list[str]
    hero_image_url: str
    gallery_image_urls: list[str]
    social_links: list[StudioSocialLink]


class StudioSettingsResponse(BaseModel):
    studio_name: str
    owner_name: str
    contact_email: str
    contact_phone: str
    address: str
    # IANA zone the booking engine trades in (app.constants.timezones).
    timezone: str
    booking_days: StudioBookingDays
    booking_hours: StudioBookingHours
    default_artist_slug: str
    waiver_template_id: str
    sample_flash_urls: list[str]
    # COURIER_REPLY_DOMAIN: where studio email handles live. Empty: handles are off.
    email_domain: str
    site: StudioSiteSettings


def _public_hours(schedule: dict[int, tuple[int, int] | None]) -> dict[str, list[int] | None]:
    return {str(day): list(hours) if hours else None for day, hours in schedule.items()}


def build_studio_settings(settings: Settings) -> StudioSettingsResponse:
    return StudioSettingsResponse(
        studio_name=settings.STUDIO_NAME.strip(),
        owner_name=settings.STUDIO_OWNER_NAME.strip(),
        contact_email=settings.SHOP_EMAIL.strip(),
        contact_phone=settings.STUDIO_PHONE.strip(),
        address=settings.STUDIO_ADDRESS.strip(),
        timezone=shop_timezone().key,
        booking_days=StudioBookingDays(
            closed_weekdays=settings.booking_closed_weekdays,
            request_only_weekdays=settings.booking_request_only_weekdays,
            request_only_artist_slugs=settings.booking_request_only_artist_slugs,
        ),
        booking_hours=StudioBookingHours(
            work=_public_hours(settings.booking_work_schedule),
            consultation=_public_hours(settings.booking_consultation_schedule),
        ),
        default_artist_slug=settings.DEFAULT_ARTIST_SLUG.strip(),
        waiver_template_id=settings.WAIVER_TEMPLATE_ID,
        sample_flash_urls=settings.sample_flash_urls,
        email_domain=settings.COURIER_REPLY_DOMAIN,
        site=StudioSiteSettings(
            mode=settings.public_site_mode,
            studio_hosts=settings.studio_site_hosts,
            tagline=settings.STUDIO_TAGLINE.strip(),
            story_paragraphs=settings.studio_story_paragraphs,
            hours=settings.studio_hours_lines,
            hero_image_url=settings.studio_hero_image_url,
            gallery_image_urls=settings.studio_gallery_image_urls,
            social_links=[StudioSocialLink(**link) for link in settings.studio_social_links],
        ),
    )


@router.get("/settings", response_model=StudioSettingsResponse)
async def get_studio_settings(response: Response) -> StudioSettingsResponse:
    """Public, non-secret studio display and booking settings. No auth required."""
    response.headers["Cache-Control"] = STUDIO_SETTINGS_CACHE_CONTROL
    return build_studio_settings(get_settings())


class StudioSettingsEdit(BaseModel):
    """The values the owner can edit, by their Settings names. Omitted means unchanged."""

    STUDIO_NAME: str | None = Field(default=None, max_length=120)
    STUDIO_TIMEZONE: str | None = Field(default=None, max_length=64)
    STUDIO_OWNER_NAME: str | None = Field(default=None, max_length=120)
    SHOP_EMAIL: EmailStr | Literal[""] | None = None
    STUDIO_PHONE: str | None = Field(default=None, max_length=40)
    STUDIO_ADDRESS: str | None = Field(default=None, max_length=300)
    STUDIO_ARTIST_ROSTER: str | None = Field(default=None, max_length=4000)
    BOOKING_CLOSED_WEEKDAYS: str | None = Field(default=None, max_length=60)
    BOOKING_REQUEST_ONLY_WEEKDAYS: str | None = Field(default=None, max_length=60)
    BOOKING_WORK_HOURS: str | None = Field(default=None, max_length=120)
    BOOKING_CONSULTATION_HOURS: str | None = Field(default=None, max_length=120)
    PUBLIC_SITE_MODE: Literal["product", "studio"] | None = None
    STUDIO_TAGLINE: str | None = Field(default=None, max_length=200)
    STUDIO_STORY: str | None = Field(default=None, max_length=8000)
    STUDIO_HOURS: str | None = Field(default=None, max_length=1000)
    STUDIO_HERO_IMAGE_URL: str | None = Field(default=None, max_length=1000)
    STUDIO_GALLERY_IMAGE_URLS: str | None = Field(default=None, max_length=8000)
    STUDIO_SOCIAL_LINKS: str | None = Field(default=None, max_length=2000)


def _editable_values(settings: Settings) -> dict[str, str]:
    return {key: getattr(settings, key) for key in EDITABLE_SETTINGS}


@router.get("/settings/edit", response_model=StudioSettingsEdit)
async def get_editable_settings(_founder: User = Depends(require_role("founder"))) -> dict[str, str]:
    """The current value of everything the owner can edit."""
    return _editable_values(get_settings())


@router.put("/settings/edit", response_model=StudioSettingsEdit)
async def save_editable_settings(
    body: StudioSettingsEdit,
    _founder: User = Depends(require_role("founder")),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict[str, str]:
    """Save the submitted values. They apply at once, with no redeploy."""
    settings = get_settings()
    changes = {key: value for key, value in body.model_dump(exclude_unset=True).items() if value is not None}
    try:
        clean = validated_changes(settings, changes)
    except StudioSettingsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    stage_settings(db, await saved_rows(db, list(clean)), clean)
    await db.commit()
    apply_values(settings, clean)
    return _editable_values(settings)


class StudioSetupStatus(BaseModel):
    needs_setup: bool
    # An install with accounts but no founder is claimable, but its people still sign in.
    has_accounts: bool


class StudioSetupRequest(BaseModel):
    setup_code: str = Field(min_length=1, max_length=40)
    studio_name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(min_length=1, max_length=64)
    owner_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8)


@router.get("/setup", response_model=StudioSetupStatus)
async def get_setup_status(
    response: Response, db: AsyncSession = Depends(get_db, scope="function")
) -> StudioSetupStatus:
    response.headers["Cache-Control"] = "no-store"
    accounts = await db.scalar(select(func.count()).select_from(User))
    return StudioSetupStatus(needs_setup=await setup_is_open(db), has_accounts=bool(accounts))


@router.post("/setup", response_model=Token, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def claim_install(
    request: Request, body: StudioSetupRequest, db: AsyncSession = Depends(get_db, scope="function")
) -> Token:
    """Claim a new install: create its owner, save its identity, and sign the owner in."""
    settings = get_settings()
    if not await setup_is_open(db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This studio is already set up.")
    if not setup_code_matches(settings, body.setup_code):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="That setup code is not right.")

    email = normalize_email(body.email)
    owner_name = body.owner_name.strip()
    taken = await db.scalar(select(func.count()).select_from(User).where(func.lower(User.email) == email))
    if taken:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That email already has an account here.")
    try:
        clean = validated_changes(
            settings,
            {
                "STUDIO_NAME": body.studio_name.strip(),
                "STUDIO_TIMEZONE": body.timezone,
                "STUDIO_OWNER_NAME": owner_name,
                "SHOP_EMAIL": settings.SHOP_EMAIL.strip() or email,
            },
        )
    except StudioSettingsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    now = datetime.now(UTC)
    owner = User(
        email=email,
        password_hash=hash_password(body.password),
        name=owner_name,
        slug=await unique_artist_slug(db, owner_name),
        role="founder",
        email_verified_at=now,
        must_reset_password=False,
        password_state="user_set",
        password_changed_at=now,
        password_change_method="setup",
    )
    db.add(owner)
    db.add(StudioSetting(key=SETUP_CLAIM_KEY, value=email))
    stage_settings(db, await saved_rows(db, list(clean)), clean)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This studio is already set up.") from exc
    record_successful_login(owner, "password")
    token = create_user_access_token(owner)
    await db.commit()
    apply_values(settings, clean)
    return Token(access_token=token)
