"""
Public artist schemas — no auth or sensitive fields exposed.

These are what the /artists endpoints return to unauthenticated visitors.
Used by the crew page and individual artist profile pages.
"""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .user import _fix_drive_url


class ArtistSummary(BaseModel):
    """Crew listing — minimal info for the grid cards."""

    id: UUID
    slug: str
    name: str
    specialty: list[str] | None = None
    avatar_url: str | None = None
    availability_source: str = "none"
    booking_mode: str | None = "mailto"
    booking_slot_interval_minutes: int = 30
    booking_buffer_minutes: int = 30
    booking_min_notice_hours: int = 24
    booking_max_advance_days: int = 180
    booking_allow_same_day: bool = False

    @field_validator("avatar_url", mode="before")
    @classmethod
    def fix_avatar_url(cls, v: str | None) -> str | None:
        return _fix_drive_url(v)

    model_config = {"from_attributes": True}


class ArtistDetail(BaseModel):
    """Full artist profile — everything the portfolio page needs."""

    id: UUID
    slug: str
    name: str
    title: str | None = None
    specialty: list[str] | None = None
    bio: str | None = None
    avatar_url: str | None = None
    portfolio_images: list[str] | None = None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def fix_avatar_url(cls, v: str | None) -> str | None:
        return _fix_drive_url(v)

    @field_validator("portfolio_images", mode="before")
    @classmethod
    def remove_stale_portfolio_urls(cls, v: list[str] | None) -> list[str] | None:
        if not v:
            return v
        return [url for url in v if isinstance(url, str) and not url.startswith("/uploads/")]

    # original_url -> derivative url for the images above. Grid tiles read the thumb (640px),
    # the enlarged view reads the focus derivative (1400px); an image with no entry falls back
    # to the original.
    portfolio_thumbs: dict[str, str] | None = None
    portfolio_focus: dict[str, str] | None = None

    @field_validator("portfolio_thumbs", "portfolio_focus", mode="before")
    @classmethod
    def clean_portfolio_derivatives(cls, v: dict | None) -> dict[str, str] | None:
        if not v or not isinstance(v, dict):
            return None
        return {k: t for k, t in v.items() if isinstance(k, str) and isinstance(t, str) and t}

    booking_mode: str | None = "mailto"
    booking_url: str | None = None
    availability_source: str = "none"
    booking_slot_interval_minutes: int = 30
    booking_buffer_minutes: int = 30
    booking_min_notice_hours: int = 24
    booking_max_advance_days: int = 180
    booking_allow_same_day: bool = False
    instagram_url: str | None = None
    phone: str | None = None
    facebook_url: str | None = None
    tiktok_url: str | None = None
    website_url: str | None = None
    flash_deposit_amount: float | None = None
    allow_prepay_full: bool = False
    working_hours: dict[str, list[int] | tuple[int, int] | None] | None = None

    model_config = {"from_attributes": True}


class ArtistProfileUpdate(BaseModel):
    """Fields an artist can update on their own artist profile."""

    title: str | None = None
    specialty: list[str] | None = None
    bio: str | None = None
    avatar_url: str | None = None
    booking_mode: str | None = None
    booking_url: str | None = None
    booking_slot_interval_minutes: int | None = Field(default=None, ge=15, le=120)
    booking_buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    booking_min_notice_hours: int | None = Field(default=None, ge=0, le=720)
    booking_max_advance_days: int | None = Field(default=None, ge=1, le=365)
    booking_allow_same_day: bool | None = None
    flash_deposit_amount: float | None = None
    allow_prepay_full: bool | None = None
    working_hours: dict[str, list[int] | tuple[int, int] | None] | None = None
    instagram_url: str | None = None
    phone: str | None = None
    facebook_url: str | None = None
    tiktok_url: str | None = None
    website_url: str | None = None
