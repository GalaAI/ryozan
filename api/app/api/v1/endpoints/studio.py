"""
Public studio settings: the display and behaviour values a browser needs.

  GET /studio/settings   no auth, cacheable

One install is one studio. Everything here is read from Settings, so a new
install shows neutral copy until it sets its own values. Only non-secret
values belong here: anything added to this response is world-readable.
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.constants.timezones import SHOP_TIMEZONE
from app.core.config import Settings, get_settings

router = APIRouter(prefix="/studio", tags=["studio"])

# Settings change only on redeploy; five minutes keeps a stale tab honest.
STUDIO_SETTINGS_CACHE_CONTROL = "public, max-age=300"


class StudioBookingDays(BaseModel):
    # Three-letter weekday names, Sunday first: sun mon tue wed thu fri sat.
    closed_weekdays: list[str]
    request_only_weekdays: list[str]
    # Artists the request-only days apply to; empty means every artist.
    request_only_artist_slugs: list[str]


class StudioSettingsResponse(BaseModel):
    studio_name: str
    owner_name: str
    contact_email: str
    contact_phone: str
    address: str
    # IANA zone the booking engine trades in (app.constants.timezones).
    timezone: str
    booking_days: StudioBookingDays
    default_artist_slug: str
    waiver_template_id: str
    sample_flash_urls: list[str]
    # COURIER_REPLY_DOMAIN: where studio email handles live. Empty: handles are off.
    email_domain: str


def build_studio_settings(settings: Settings) -> StudioSettingsResponse:
    return StudioSettingsResponse(
        studio_name=settings.STUDIO_NAME.strip(),
        owner_name=settings.STUDIO_OWNER_NAME.strip(),
        contact_email=settings.SHOP_EMAIL.strip(),
        contact_phone=settings.STUDIO_PHONE.strip(),
        address=settings.STUDIO_ADDRESS.strip(),
        timezone=SHOP_TIMEZONE.key,
        booking_days=StudioBookingDays(
            closed_weekdays=settings.booking_closed_weekdays,
            request_only_weekdays=settings.booking_request_only_weekdays,
            request_only_artist_slugs=settings.booking_request_only_artist_slugs,
        ),
        default_artist_slug=settings.DEFAULT_ARTIST_SLUG.strip(),
        waiver_template_id=settings.WAIVER_TEMPLATE_ID,
        sample_flash_urls=settings.sample_flash_urls,
        email_domain=settings.COURIER_REPLY_DOMAIN,
    )


@router.get("/settings", response_model=StudioSettingsResponse)
async def get_studio_settings(response: Response) -> StudioSettingsResponse:
    """Public, non-secret studio display and booking settings. No auth required."""
    response.headers["Cache-Control"] = STUDIO_SETTINGS_CACHE_CONTROL
    return build_studio_settings(get_settings())
