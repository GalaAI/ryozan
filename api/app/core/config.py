"""
Ryozan API Configuration

All settings loaded from environment variables with sensible defaults.
The .env file at the project root is loaded automatically by pydantic-settings.
"""

import contextlib
import json
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings

from app.core.legacy_env import warn_legacy_env

# Weekday names for the booking-day settings, Sunday first like the browser's
# Date.getDay(). Full names are accepted on input and stored as the short form.
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
# Python weekday order, which the slot engine and artist working_hours key on.
ENGINE_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_ALIASES = {
    **{day: day for day in WEEKDAYS},
    "sunday": "sun",
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
}

# Shared secrets that sign tokens or authorize machine callers.
SHARED_SECRET_FIELDS = ("SECRET_KEY", "CRON_SECRET", "DOC_REMINDER_API_KEY", "WORKSPACE_BOT_TOKEN")
# Former in-source defaults. They are public, so they count as unset everywhere.
LEGACY_PUBLIC_SECRETS = frozenset(
    {
        "bttco-dev-secret-change-in-production",
        "bttco-cron-dev-key",
        "bttco-reminders-dev-key",
        "bttco-workspace-dev-key",
        "replace_with_32_byte_hex_secret_key",
    }
)

_HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")
# Characters that would let a URL break out of an HTML attribute or a CSS url().
_URL_FORBIDDEN = re.compile(r"[\s\"'()<>\\`]")


def _is_hostname(value: str) -> bool:
    return len(value) <= 253 and bool(_HOSTNAME.fullmatch(value))


def _is_web_url(value: str) -> bool:
    """An absolute https:// URL with a host, safe to place in an href or src."""
    if not value.startswith("https://") or _URL_FORBIDDEN.search(value):
        return False
    host = value[len("https://") :].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return bool(host)


def _is_public_image_url(value: str) -> bool:
    """https:// URL or a same-site path (/media/a.jpg), never protocol-relative."""
    if value.startswith("/") and not value.startswith("//"):
        return not _URL_FORBIDDEN.search(value)
    return _is_web_url(value)


def _parse_booking_hours(value: str) -> dict[str, tuple[int, int]]:
    """Read day=open-close pairs. Raises ValueError with a message the owner can act on."""
    hours: dict[str, tuple[int, int]] = {}
    for part in (value or "").split(","):
        if not part.strip():
            continue
        name, _, span = part.partition("=")
        day = WEEKDAY_ALIASES.get(name.strip().lower())
        if day is None:
            raise ValueError(f"unknown weekday {name.strip()!r}; use sun, mon, tue, wed, thu, fri or sat")
        opens, _, closes = span.partition("-")
        try:
            open_hour, close_hour = int(opens), int(closes)
        except ValueError:
            raise ValueError(f"hours for {day} must look like {day}=11-18, in whole hours") from None
        if not 0 <= open_hour < close_hour <= 23:
            raise ValueError(
                f"{day}: the opening time must be before the closing time, and closing no later than 11 PM"
            )
        hours[day] = (open_hour, close_hour)
    return hours


def _weekly_schedule(value: str) -> dict[int, tuple[int, int] | None]:
    hours = _parse_booking_hours(value)
    return {index: hours.get(day) for index, day in enumerate(ENGINE_WEEKDAYS)}


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Ryozan API"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOCAL_DEV_MODE: bool = False
    # Dev/test only: when set, ALL outbound email is redirected to this address
    # instead of the real recipient (so test bookings land in one readable inbox).
    # Must be empty in production. Ignored when APP_ENV == "production" as a guard.
    EMAIL_TEST_REDIRECT: str = ""
    ENABLE_API_DOCS: bool = False
    ENABLE_CONFIG_HEALTH: bool = False
    SECURITY_HEADERS_ENABLED: bool = True
    SECURITY_HSTS_ENABLED: bool = True
    SECURITY_HSTS_MAX_AGE: int = 31536000
    API_V1_PREFIX: str = "/api/v1"

    # Database — Railway provides DATABASE_URL as postgresql://
    # We derive the asyncpg variant automatically.
    # Fall back to the docker dev mirror on :5433 (docker-compose.yml `db`),
    # never to a shared host database. Production supplies both via Railway.
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/nexus"
    DATABASE_SYNC_URL: str = "postgresql://postgres:postgres@localhost:5433/nexus"

    @model_validator(mode="after")
    def fix_database_urls(self):
        """Ensure DATABASE_URL uses asyncpg driver and DATABASE_SYNC_URL is plain psycopg."""
        if self.DATABASE_URL.startswith("postgresql://"):
            self.DATABASE_SYNC_URL = self.DATABASE_URL
            self.DATABASE_URL = self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif self.DATABASE_URL.startswith("postgresql+asyncpg://"):
            self.DATABASE_SYNC_URL = self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
        return self

    # Auth. Shared secrets have no public default: development generates and
    # persists random values (see ensure_shared_secrets), every other APP_ENV
    # refuses to start until they are set.
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    # Password reset links travel by email, and people open email hours or days
    # after it lands. A window shorter than a day fails ordinary users and every
    # recipient of a bulk "reset your password" migration notice. 24 hours is
    # safe here because the token is single-use and bound to the user's current
    # password hash (see app.core.security), so it dies the moment the password
    # changes. Override with PASSWORD_RESET_EXPIRE_MINUTES in the environment.
    PASSWORD_RESET_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # CORS — origins that can call this API
    CORS_ORIGINS: list[str] = [
        "http://localhost:15300",  # Vite dev server (judge and local build)
        "http://127.0.0.1:15300",
        "http://localhost:5173",  # legacy Vite default
    ]
    # Optional preview-deploy origin pattern; env-only.
    CORS_ORIGIN_REGEX: str = ""

    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = 30  # seconds

    # Email -- SendGrid API (primary when configured), Resend API, or SMTP fallback.
    # Every sender identity comes from these settings; nothing in source names a
    # mail domain. A blank *_FROM_EMAIL falls back to SHOP_EMAIL and a blank
    # *_FROM_NAME falls back to STUDIO_NAME (app.services.email.sender_identity).
    SENDGRID_API_KEY: str = ""  # SG.xxxxx from SendGrid
    SENDGRID_FROM_EMAIL: str = ""
    SENDGRID_FROM_NAME: str = ""
    # Send from <alias>@<domain of the SendGrid from address> (noreply, admin,
    # service) instead of the from address itself.
    SENDGRID_USE_ALIAS_FROM: bool = False

    # Email -- Resend API (sending, and the inbound mail owner)
    RESEND_API_KEY: str = ""  # re_xxxxx from resend.com
    RESEND_FROM_EMAIL: str = ""
    RESEND_FROM_NAME: str = ""
    RESEND_WEBHOOK_SECRET: str = ""  # svix signing secret for the delivery webhook (Resend → /webhooks/resend)
    # svix signing secret for the inbound webhook (Resend → /webhooks/email-inbound)
    RESEND_INBOUND_WEBHOOK_SECRET: str = ""
    # The domain Resend receives mail on for this install, for example
    # in.studio.example.com. Courier reply addresses (convo-<id>@) and artist
    # email handles (<handle>@) live on it. Blank turns both off.
    COURIER_REPLY_DOMAIN: str = ""

    @field_validator("COURIER_REPLY_DOMAIN")
    @classmethod
    def validate_courier_reply_domain(cls, value: str) -> str:
        import re

        value = (value or "").strip().lower()
        if not value:
            return value
        if len(value) > 253 or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", value
        ):
            raise ValueError("COURIER_REPLY_DOMAIN must be a DNS hostname")
        return value

    # SMTP fallback. Sending needs both SMTP_USER and SMTP_PASSWORD.
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""  # Gmail App Password (16-char)
    SMTP_FROM_NAME: str = ""
    SMTP_FROM_EMAIL: str = ""  # blank sends from SMTP_USER

    # Studio identity printed on client email, SMS and PDF output. The postal
    # address is env-only: a deployment sets its own, and nothing prints when blank.
    STUDIO_NAME: str = "Ryozan Studio"
    # IANA zone the studio trades in: every "which day" question, client-facing
    # time and calendar entry. The owner sets it during first-run setup.
    STUDIO_TIMEZONE: str = "America/New_York"
    # Free text the agents read as the studio's artist roster (one artist per line). Empty: no roster in prompts.
    STUDIO_ARTIST_ROSTER: str = ""
    STUDIO_ADDRESS: str = ""
    # Public studio phone shown in the app (e.g. staff emergency contacts).
    # Blank hides it.
    STUDIO_PHONE: str = ""

    # Shop-level notification recipients (always CC'd on booking alerts).
    # SHOP_EMAIL is also the public contact address in GET /studio/settings, the
    # fallback sender address and the fallback VAPID subject. Blank means no
    # shop inbox copy is sent.
    SHOP_EMAIL: str = ""
    # The owner's private inbox: booking alerts, and inbound mail to reserved
    # addresses (info@, bookings@ ...) and the catch-all. Blank falls back to
    # SHOP_EMAIL; with neither, inbound mail stays in the app inbox only.
    SHOP_OWNER_EMAIL: str = ""

    # ── Studio policy ─────────────────────────────────────────────────────
    # Workflows some studios run and others do not. Every one is off for a new
    # install; a studio turns on the ones it uses in its own environment.
    #
    # Owner's display name for AI prompts, assistant labels and the reply
    # guard that stops AI promising actions on the owner's behalf. Blank
    # means generic wording ("the shop owner").
    STUDIO_OWNER_NAME: str = ""
    # Slug of the artist who receives "any artist" requests. Blank means the
    # client must choose an artist.
    DEFAULT_ARTIST_SLUG: str = ""
    # Admins and founders may create bookings on behalf of an artist
    # (POST /bookings/staff), e.g. a front desk booking for an artist.
    ADMIN_BOOK_ON_BEHALF_ENABLED: bool = False
    # A staff account may be set to act as another user in Courier: it sees
    # that user's threads and its messages are sent as that user
    # (User.proxy_for, PATCH /users/{id}/proxy).
    STAFF_PROXY_ENABLED: bool = False
    # Staff may confirm a quote proposal on the client's behalf (e.g. after the
    # client says yes in a DM). Off means only the booking client confirms.
    STAFF_CONFIRM_FOR_CLIENT_ENABLED: bool = False
    # Comma-separated artist booking_mode values that mean "this artist books
    # through an external system". Booking emails are not sent for them, since
    # that system handles the client conversation.
    EXTERNAL_BOOKING_MODES: str = ""
    # Comma-separated extra inbound email local parts (before the @) routed to
    # the owner inbox, in addition to info/bookings/admin/noreply/support.
    INBOUND_OWNER_ALIASES: str = ""
    # User id that receives the cron daily debrief. Blank sends it to every
    # founder account.
    DEBRIEF_RECIPIENT_USER_ID: str = ""
    # Booking calendar days, as comma-separated weekday names (sun, mon, tue,
    # wed, thu, fri, sat; full names also accepted). Closed days cannot be
    # picked on the public booking calendar. Request-only days can be picked,
    # but no time slot is offered: the client sends a request and the shop
    # confirms. BOOKING_REQUEST_ONLY_ARTIST_SLUGS limits request-only days to
    # those artists; blank applies them to every artist. All blank: every day
    # is bookable and the live slot engine decides.
    BOOKING_CLOSED_WEEKDAYS: str = ""
    BOOKING_REQUEST_ONLY_WEEKDAYS: str = ""
    BOOKING_REQUEST_ONLY_ARTIST_SLUGS: str = ""
    # When the studio books, as comma-separated day=open-close in whole hours on
    # the 24-hour clock (wed=11-18). A day left out is closed; blank closes every
    # day. An artist's own working hours replace BOOKING_WORK_HOURS for that artist.
    BOOKING_WORK_HOURS: str = "wed=11-18,thu=11-18,fri=11-20,sat=11-20"
    BOOKING_CONSULTATION_HOURS: str = "tue=11-17,wed=11-17,thu=11-17,fri=11-17,sat=11-17"
    # Document template id the staff "Send Waiver" action uses. Blank falls back
    # to the active "Tattoo Consent Waiver" template, the same one /waiver uses.
    WAIVER_TEMPLATE_ID: str = ""
    # Comma-separated image URLs shown as stand-in flash previews in local
    # development when a real flash image fails to load. Blank shows the
    # neutral "Preview unavailable" placeholder instead.
    SAMPLE_FLASH_URLS: str = ""

    # ── Public website ────────────────────────────────────────────────────
    # What the browser shows at `/`. "product" (the default) is the Ryozan
    # product landing, which the hosted demo serves. "studio" is this studio's
    # own website: home, our story, artist pages and the booking entry, built
    # only from the values below and the artists in the database. Every value
    # here is public (GET /studio/settings); blank values hide their section.
    PUBLIC_SITE_MODE: str = "product"
    # Optional, comma-separated hostnames that get the studio site even when
    # PUBLIC_SITE_MODE is "product". Only for one install that answers on a
    # product domain and a studio domain at once; a studio-only install leaves
    # it blank and sets PUBLIC_SITE_MODE=studio.
    STUDIO_SITE_HOSTS: str = ""
    # One line under the studio name on the home page.
    STUDIO_TAGLINE: str = ""
    # The "Our story" text. Separate paragraphs with a blank line.
    STUDIO_STORY: str = ""
    # Opening hours as free text, one line per row (e.g. "Tue-Sat 11am-7pm").
    STUDIO_HOURS: str = ""
    # Image URLs (https:// or a site path such as /media/front.jpg).
    STUDIO_HERO_IMAGE_URL: str = ""
    STUDIO_GALLERY_IMAGE_URLS: str = ""  # comma-separated
    # Comma-separated Label=https://url pairs, e.g. Instagram=https://instagram.com/studio
    STUDIO_SOCIAL_LINKS: str = ""

    @staticmethod
    def _csv_set(raw: str) -> frozenset[str]:
        return frozenset(part.strip().lower() for part in (raw or "").split(",") if part.strip())

    @property
    def external_booking_modes(self) -> frozenset[str]:
        return self._csv_set(self.EXTERNAL_BOOKING_MODES)

    def is_external_booking_mode(self, booking_mode: str | None) -> bool:
        """True when this artist's booking emails are owned by an external system."""
        return bool(booking_mode) and booking_mode.strip().lower() in self.external_booking_modes

    @property
    def inbound_owner_aliases(self) -> frozenset[str]:
        return self._csv_set(self.INBOUND_OWNER_ALIASES)

    @property
    def owner_inbox_email(self) -> str:
        """Where owner mail goes: SHOP_OWNER_EMAIL, else SHOP_EMAIL, else blank."""
        return (self.SHOP_OWNER_EMAIL or "").strip() or (self.SHOP_EMAIL or "").strip()

    @property
    def public_site_url(self) -> str:
        """FRONTEND_URL without a trailing slash, the base for links in client messages."""
        return (self.FRONTEND_URL or "").strip().rstrip("/")

    def email_asset_url(self, name: str) -> str:
        """URL of a brand image for client email, under EMAIL_ASSET_BASE_URL or <site>/email."""
        base = (self.EMAIL_ASSET_BASE_URL or "").strip().rstrip("/") or f"{self.public_site_url}/email"
        return f"{base}/{name}"

    def inbound_address(self, local_part: str) -> str | None:
        """<local_part>@COURIER_REPLY_DOMAIN, or None when no receiving domain is set."""
        domain = (self.COURIER_REPLY_DOMAIN or "").strip()
        return f"{local_part}@{domain}" if domain else None

    def resolve_artist_slug(self, slug: str) -> str | None:
        """Map the public "any" artist choice to the configured default artist.

        Returns None when "any" is requested and no default artist is set.
        """
        if slug != "any":
            return slug
        return self.DEFAULT_ARTIST_SLUG.strip() or None

    @property
    def owner_label(self) -> str:
        """Owner name for prompts and labels, or a neutral fallback."""
        return self.STUDIO_OWNER_NAME.strip() or "the shop owner"

    @staticmethod
    def _csv_list(raw: str) -> list[str]:
        return [part.strip() for part in (raw or "").split(",") if part.strip()]

    @property
    def booking_closed_weekdays(self) -> list[str]:
        return self._csv_list(self.BOOKING_CLOSED_WEEKDAYS)

    @property
    def booking_request_only_weekdays(self) -> list[str]:
        return self._csv_list(self.BOOKING_REQUEST_ONLY_WEEKDAYS)

    @property
    def booking_request_only_artist_slugs(self) -> list[str]:
        return sorted(self._csv_set(self.BOOKING_REQUEST_ONLY_ARTIST_SLUGS))

    @property
    def booking_work_schedule(self) -> dict[int, tuple[int, int] | None]:
        return _weekly_schedule(self.BOOKING_WORK_HOURS)

    @property
    def booking_consultation_schedule(self) -> dict[int, tuple[int, int] | None]:
        return _weekly_schedule(self.BOOKING_CONSULTATION_HOURS)

    @property
    def sample_flash_urls(self) -> list[str]:
        return self._csv_list(self.SAMPLE_FLASH_URLS)

    @property
    def public_site_mode(self) -> str:
        """Return "studio" or "product"; anything else is the product default."""
        return "studio" if (self.PUBLIC_SITE_MODE or "").strip().lower() == "studio" else "product"

    @property
    def studio_site_hosts(self) -> list[str]:
        return sorted(host for host in self._csv_set(self.STUDIO_SITE_HOSTS) if _is_hostname(host))

    @property
    def studio_story_paragraphs(self) -> list[str]:
        text = (self.STUDIO_STORY or "").replace("\r\n", "\n").replace("\\n", "\n")
        return [" ".join(block.split()) for block in re.split(r"\n\s*\n", text) if block.strip()]

    @property
    def studio_hours_lines(self) -> list[str]:
        text = (self.STUDIO_HOURS or "").replace("\r\n", "\n").replace("\\n", "\n")
        return [line.strip() for line in text.split("\n") if line.strip()]

    @property
    def studio_hero_image_url(self) -> str:
        value = (self.STUDIO_HERO_IMAGE_URL or "").strip()
        return value if _is_public_image_url(value) else ""

    @property
    def studio_gallery_image_urls(self) -> list[str]:
        return [url for url in self._csv_list(self.STUDIO_GALLERY_IMAGE_URLS) if _is_public_image_url(url)]

    @property
    def studio_social_links(self) -> list[dict[str, str]]:
        """[{label, url}] from Label=https://url pairs; malformed pairs are left out."""
        links = []
        for part in self._csv_list(self.STUDIO_SOCIAL_LINKS):
            label, sep, url = part.partition("=")
            label, url = label.strip(), url.strip()
            if sep and label and _is_web_url(url):
                links.append({"label": label, "url": url})
        return links

    @property
    def vapid_subject(self) -> str:
        """VAPID `sub` claim: VAPID_SUBJECT, else mailto:SHOP_EMAIL, else blank.

        Push services require it, so push stays off while it is blank.
        """
        subject = self.VAPID_SUBJECT.strip()
        if subject:
            return subject
        email = self.SHOP_EMAIL.strip()
        return f"mailto:{email}" if email else ""

    @property
    def push_configured(self) -> bool:
        return bool(self.VAPID_PUBLIC_KEY and self.VAPID_PRIVATE_KEY and self.vapid_subject)

    @field_validator("BOOKING_CLOSED_WEEKDAYS", "BOOKING_REQUEST_ONLY_WEEKDAYS")
    @classmethod
    def normalize_weekdays(cls, value: str) -> str:
        """Canonical form: unique three-letter names in Sunday-first order."""
        picked: set[str] = set()
        for part in (value or "").split(","):
            name = part.strip().lower()
            if not name:
                continue
            short = WEEKDAY_ALIASES.get(name)
            if short is None:
                raise ValueError(f"unknown weekday {part.strip()!r}; use sun, mon, tue, wed, thu, fri or sat")
            picked.add(short)
        return ",".join(day for day in WEEKDAYS if day in picked)

    @field_validator("VAPID_SUBJECT")
    @classmethod
    def validate_vapid_subject(cls, value: str) -> str:
        value = (value or "").strip()
        if value and not (value.startswith("mailto:") or value.startswith("https://")):
            raise ValueError("VAPID_SUBJECT must be a mailto: or https:// URI")
        return value

    @field_validator("DEBRIEF_RECIPIENT_USER_ID", "WAIVER_TEMPLATE_ID")
    @classmethod
    def validate_optional_uuid(cls, value: str, info: ValidationInfo) -> str:
        value = (value or "").strip()
        if value:
            try:
                UUID(value)
            except ValueError as exc:
                raise ValueError(f"{info.field_name} must be a UUID") from exc
        return value

    @field_validator("BOOKING_WORK_HOURS", "BOOKING_CONSULTATION_HOURS")
    @classmethod
    def normalize_booking_hours(cls, value: str) -> str:
        """Canonical form: one day=open-close per open day, Sunday first."""
        hours = _parse_booking_hours(value)
        return ",".join(f"{day}={hours[day][0]}-{hours[day][1]}" for day in WEEKDAYS if day in hours)

    @field_validator("STUDIO_TIMEZONE")
    @classmethod
    def _valid_studio_timezone(cls, value: str) -> str:
        zone = (value or "").strip()
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(f"STUDIO_TIMEZONE {zone!r} is not an IANA time zone") from exc
        return zone

    @field_validator("PUBLIC_SITE_MODE")
    @classmethod
    def validate_public_site_mode(cls, value: str) -> str:
        value = (value or "").strip().lower() or "product"
        if value not in ("product", "studio"):
            raise ValueError('PUBLIC_SITE_MODE must be "product" or "studio"')
        return value

    @field_validator("STUDIO_SITE_HOSTS")
    @classmethod
    def validate_studio_site_hosts(cls, value: str) -> str:
        hosts = [part.strip().lower() for part in (value or "").split(",") if part.strip()]
        for host in hosts:
            if not _is_hostname(host):
                raise ValueError(f"STUDIO_SITE_HOSTS entry {host!r} must be a bare hostname, without scheme or port")
        return ",".join(hosts)

    @field_validator("STUDIO_HERO_IMAGE_URL", "STUDIO_GALLERY_IMAGE_URLS")
    @classmethod
    def validate_studio_image_urls(cls, value: str, info: ValidationInfo) -> str:
        urls = [part.strip() for part in (value or "").split(",") if part.strip()]
        if info.field_name == "STUDIO_HERO_IMAGE_URL" and len(urls) > 1:
            raise ValueError("STUDIO_HERO_IMAGE_URL takes one URL")
        for url in urls:
            if not _is_public_image_url(url):
                raise ValueError(
                    f"{info.field_name} entry {url!r} must be an https:// URL or a site path starting with /"
                )
        return ",".join(urls)

    @field_validator("STUDIO_SOCIAL_LINKS")
    @classmethod
    def validate_studio_social_links(cls, value: str) -> str:
        pairs = [part.strip() for part in (value or "").split(",") if part.strip()]
        for pair in pairs:
            label, sep, url = pair.partition("=")
            if not (sep and label.strip() and _is_web_url(url.strip())):
                raise ValueError(f"STUDIO_SOCIAL_LINKS entry {pair!r} must be Label=https://url")
        return ",".join(pairs)

    # Google OAuth for the active Ryozan Cloud project (628400721124). The web
    # client handles server-side Calendar authorization and browser GSI for the
    # same Ryozan web application. The id and secret must stay a matched pair.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REFRESH_TOKEN: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:18788/api/v1/auth/google/callback"

    # Calendar authority. `oauth` preserves the per-artist refresh-token path
    # during migration. `workspace_delegated` uses one Ryozan Workspace identity
    # and calendars shared with that identity, so clients never grant Calendar.
    GOOGLE_CALENDAR_AUTH_MODE: str = "oauth"
    GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON: str = ""
    GOOGLE_CALENDAR_DELEGATED_USER: str = ""
    GOOGLE_CALENDAR_DEFAULT_ID: str = ""

    # Optional separate OAuth client for Google sign-in. Blank means sign-in uses
    # GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, the same client as Calendar.
    # A sign-in client id that differs from GOOGLE_CLIENT_ID needs its own
    # GOOGLE_SIGNIN_CLIENT_SECRET; without it sign-in reports itself unconfigured.
    # VITE_GOOGLE_CLIENT_ID must render the same client id as the server uses.
    GOOGLE_SIGNIN_CLIENT_ID: str = ""
    GOOGLE_SIGNIN_CLIENT_SECRET: str = ""

    @property
    def google_signin_client(self) -> tuple[str, str]:
        """(client id, client secret) for Google sign-in, or ("", "") when incomplete.

        The token exchange must send the secret that belongs to the client id,
        so a separate sign-in client never borrows GOOGLE_CLIENT_SECRET.
        """
        shared_id = (self.GOOGLE_CLIENT_ID or "").strip()
        signin_id = (self.GOOGLE_SIGNIN_CLIENT_ID or "").strip()
        signin_secret = (self.GOOGLE_SIGNIN_CLIENT_SECRET or "").strip()
        if signin_id and signin_id != shared_id:
            client_id, secret = signin_id, signin_secret
        else:
            client_id, secret = shared_id, signin_secret or (self.GOOGLE_CLIENT_SECRET or "").strip()
        return (client_id, secret) if client_id and secret else ("", "")

    @property
    def google_signin_audience(self) -> str:
        """Client id a Google ID token must be issued to."""
        return (self.GOOGLE_SIGNIN_CLIENT_ID or "").strip() or (self.GOOGLE_CLIENT_ID or "").strip()

    # Pinterest API v5 (Trial access; app "GalaX"). Credentials live in the
    # developer dashboard at developers.pinterest.com/apps. Base: https://api.pinterest.com/v5
    PINTEREST_CLIENT_ID: str = ""
    PINTEREST_CLIENT_SECRET: str = ""
    PINTEREST_REDIRECT_URI: str = "http://localhost:18788/api/v1/auth/pinterest/callback"
    PINTEREST_API_BASE: str = "https://api.pinterest.com/v5"
    # Sandbox host (Trial access, full read+write but isolated). Standard access
    # unlocks writes on the production host and requires a video-demo review.
    PINTEREST_SANDBOX_API_BASE: str = "https://api-sandbox.pinterest.com/v5"
    # When true, Pinterest calls target the sandbox host. Flip to false once
    # Standard access is granted to write against the real account.
    PINTEREST_USE_SANDBOX: bool = True

    # Frontend URL (for magic links in emails). Local default; hosted values
    # are set only in the deployed environment.
    FRONTEND_URL: str = "http://localhost:15300"

    # API public base URL (for verification links, webhooks, etc.). Local default.
    API_PUBLIC_BASE_URL: str = "http://localhost:18788"

    # Where client email loads its brand images (tiger-mark.png, starseed-tile.png).
    # Blank serves the copies shipped in app/public/email from FRONTEND_URL.
    EMAIL_ASSET_BASE_URL: str = ""

    # Document lifecycle
    DOC_REMINDER_API_KEY: str = ""  # n8n uses this to call /reminders/process

    # Cron / scheduled job auth
    CRON_SECRET: str = ""

    # Workspace dev-collab bot (Discord) shared-secret auth
    WORKSPACE_BOT_TOKEN: str = ""
    # Where development keeps its generated shared secrets (gitignored).
    DEV_SECRETS_FILE: str = str(Path(__file__).resolve().parents[2] / ".dev-secrets.json")

    @model_validator(mode="after")
    def ensure_shared_secrets(self):
        """Refuse public or blank shared secrets outside development and test.

        Development gets random values generated once and persisted, so JWTs and
        cron calls stay valid across reloads and workers on one machine. Test
        gets throwaway in-memory values.
        """
        missing = [
            name
            for name in SHARED_SECRET_FIELDS
            if not getattr(self, name).strip() or getattr(self, name) in LEGACY_PUBLIC_SECRETS
        ]
        if not missing:
            return self
        app_env = self.APP_ENV.strip().lower()
        if app_env == "test":
            # Test processes get throwaway values and never touch the dev file.
            for name in missing:
                setattr(self, name, secrets.token_urlsafe(48))
            return self
        if app_env != "development":
            raise ValueError(
                f"Refusing to start with APP_ENV={self.APP_ENV}: set {', '.join(missing)} to private random values."
            )
        path = Path(self.DEV_SECRETS_FILE)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        changed = False
        for name in missing:
            if not isinstance(stored.get(name), str) or len(stored[name]) < 32:
                stored[name] = secrets.token_urlsafe(48)
                changed = True
            setattr(self, name, stored[name])
        if changed:
            # A read-only checkout keeps the in-memory values for this process.
            with contextlib.suppress(OSError):
                path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
        return self

    # External services (wired up incrementally)
    N8N_WEBHOOK_URL: str = ""

    # AI model access. Every model call goes through one OpenAI-compatible
    # /v1 client (app.services.ai_routing.call_ark_gateway_chat_completion).
    # The default target is Nebius Token Factory serving NVIDIA Nemotron, so a
    # fresh install only needs NEBIUS_API_KEY. An Ark Model Gateway is an
    # optional override: set ARK_MODEL_GATEWAY_BASE_URL and ARK_MODEL_GATEWAY_KEY
    # (and ark/* slugs in the AI_* model settings) to route through it instead.
    #
    # Accepted spellings, canonical first so it wins when several are set:
    #   base URL: ARK_MODEL_GATEWAY_BASE_URL, ARK_MODEL_GATEWAY_API_BASE, NEBIUS_BASE_URL
    #   key:      ARK_MODEL_GATEWAY_KEY, ARK_MODEL_GATEWAY_API_KEY, NEBIUS_API_KEY
    # The legacy _API_ spellings stay because deployed environments carry them;
    # model_config ignores unknown names, so dropping one would silently blank
    # the key and disable AI with no error anywhere.
    ARK_MODEL_GATEWAY_BASE_URL: str = Field(
        default="https://api.tokenfactory.nebius.com/v1",
        validation_alias=AliasChoices("ARK_MODEL_GATEWAY_BASE_URL", "ARK_MODEL_GATEWAY_API_BASE", "NEBIUS_BASE_URL"),
    )
    # Sent as `Authorization: Bearer <key>`. Blank disables model calls and each
    # caller reports that AI is not configured or uses its deterministic path.
    ARK_MODEL_GATEWAY_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("ARK_MODEL_GATEWAY_KEY", "ARK_MODEL_GATEWAY_API_KEY", "NEBIUS_API_KEY"),
    )

    # Instant sign-up for a public demo install (hackathon judging): POST
    # /auth/register creates a verified collector with the chosen password and
    # signs it in, without the email round trip. Each account sees only its own
    # bookings, messages and memory. Keep false on a real shop.
    OPEN_SIGNUP_ENABLED: bool = False
    # Guards for instant sign-up. OPEN_SIGNUP_CODE is required (blank keeps
    # instant sign-up closed) and must be entered on the form;
    # after OPEN_SIGNUP_UNTIL (ISO date, UTC, inclusive) instant sign-up closes;
    # at most OPEN_SIGNUP_MAX_ACCOUNTS accounts are ever created through it.
    # Each account is still a freemium client: 40 AI turns a month, and every
    # AI call counts toward ANTHROPIC_MONTHLY_BUDGET_CAP for the whole install.
    OPEN_SIGNUP_CODE: str = ""
    OPEN_SIGNUP_UNTIL: str = ""
    OPEN_SIGNUP_MAX_ACCOUNTS: int = 200
    # Accounts created by instant sign-up lose AI access after
    # OPEN_SIGNUP_UNTIL as well, so a closed demo stops spending credits.

    # Per-account cap on Tavily searches per calendar month (UTC). Staff
    # accounts are not capped. Each search is one basic-depth credit.
    TAVILY_MONTHLY_SEARCHES_PER_ACCOUNT: int = 25

    # One shared judge demo account (app.services.demo_account), created or
    # repaired at startup when both values are set. Its password and existence
    # are fixed; it ends with OPEN_SIGNUP_UNTIL like instant sign-up accounts.
    # Every judge shares it, so its monthly caps are set here.
    DEMO_ACCOUNT_EMAIL: str = ""
    DEMO_ACCOUNT_PASSWORD: str = ""
    DEMO_ACCOUNT_MONTHLY_AI_TURNS: int = 300
    DEMO_ACCOUNT_MONTHLY_SEARCHES: int = 150
    # Shared judge shop (app.services.demo_shop). On, the demo account owns the
    # install, the shop is seeded with one artist's public catalog and sample
    # clients, outbound sends are capped and never reach sample addresses, a
    # live Stripe key is dropped, and the shop resets daily at the hour below.
    DEMO_SHOP_ENABLED: bool = False
    DEMO_SHOP_DAILY_SENDS: int = 40
    DEMO_SHOP_RESET_HOUR_UTC: int = 9
    # Password for the sample artist and sample collector accounts that test
    # agents sign in as. Blank or under 12 characters leaves them unusable.
    DEMO_NPC_PASSWORD: str = ""

    # Live web search for agent turns (app.services.web_search, Tavily Search
    # API). Blank hides the web_search tool from every agent.
    TAVILY_API_KEY: str = ""

    # Unattended client replies are a separate external-effect boundary from
    # authenticated Portal AI. This explicit switch keeps those replies
    # fail-closed without disabling role-scoped AI for signed-in accounts.
    GALA_AUTO_REPLY_ENABLED: bool = False

    # Booky is an independent, narrower release lane. Missing configuration is
    # fail-closed and never relaxes unrelated client AI.
    BOOKY_AUTOMATION_ENABLED: bool = False
    BOOKY_POLICY_VERSION: str = "2026-08-20.v1"
    BOOKY_CANARY_ARTIST_SLUGS: str = ""
    BOOKY_CANARY_COLLECTOR_IDS: str = ""

    @model_validator(mode="after")
    def validate_booky_canary_collector_ids(self):
        """Reject ambiguous Booky collector canaries at configuration load."""
        for raw_id in self.BOOKY_CANARY_COLLECTOR_IDS.split(","):
            candidate = raw_id.strip()
            if not candidate:
                continue
            try:
                UUID(candidate)
            except ValueError as exc:
                raise ValueError("BOOKY_CANARY_COLLECTOR_IDS must contain only UUIDs") from exc
        return self

    # Bella WhatsApp is a separate production lane from the legacy Gala release
    # gate. A dedicated default-off flag lets us verify Twilio, Courier, and the
    # Ark gateway together without opening every older client-facing AI path.
    BELLA_WHATSAPP_AUTO_REPLY_ENABLED: bool = False
    # SMS stays independently default-off so A2P and reply receipts can be
    # proven on an opted-in test contact before customer traffic is enabled.
    BELLA_SMS_AUTO_REPLY_ENABLED: bool = False

    # AI monthly spend cap (USD). Enforced by app.services.api_budget before every
    # gateway call. Named ANTHROPIC_* for historical continuity; covers all lanes.
    ANTHROPIC_MONTHLY_BUDGET_CAP: float = 40.00

    # Chat lanes. Defaults are Nebius Token Factory model ids for NVIDIA
    # Nemotron 3. Per-agent choice comes from AGENT_PROFILES[agent].nemotron_model
    # in gala_chat (see ai_routing.agent_model_lane); these lane values are the
    # tier default and the fallback chain. On an Ark Model Gateway set them to
    # ark/* slugs such as ark/nemotron-3-super.
    AI_FREEMIUM_PRIMARY_MODEL: str = "nvidia/nemotron-3-super-120b-a12b"
    AI_FREEMIUM_FALLBACK_MODEL: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    AI_PAID_PRIMARY_MODEL: str = "nvidia/nemotron-3-super-120b-a12b"
    AI_PAID_FALLBACK_MODEL: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    AI_PREMIUM_PRIMARY_MODEL: str = "nvidia/nemotron-3-super-120b-a12b"
    AI_PREMIUM_FALLBACK_MODEL: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"

    # Utility lane: one-shot generation, extraction, and classification
    # (booking templates, memory extraction, scroll digests, upload routing).
    ARK_GATEWAY_UTILITY_MODEL: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    ARK_GATEWAY_UTILITY_FALLBACK_MODEL: str = "nvidia/nemotron-3-super-120b-a12b"

    # Legacy LiteLLM — retained ONLY for explicit Viber BYOK / legacy account rows
    # (app.services.viber_ai). NOT the Ryozan default path; Gala routes through the
    # gateway above.
    LITELLM_API_BASE: str = "http://127.0.0.1:4000/v1"
    LITELLM_MASTER_KEY: str = ""

    # Cloudflare R2 (S3-compatible object storage — all media)
    # Heal missing portfolio thumb and focus derivatives in the background at startup
    # (app.services.portfolio_thumbs.backfill_missing_portfolio_derivatives). Idempotent
    # and additive; off for tests and for hosts that must not touch storage.
    PORTFOLIO_DERIVATIVES_ON_STARTUP: bool = True

    R2_ACCOUNT_ID: str = "00000000000000000000000000000000"
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = ""  # blank: uploads stay off until the deployment names its bucket
    R2_PUBLIC_DOMAIN: str = ""  # custom domain for R2 public access; env-only

    # Hugging Face (SAM 2 image segmentation for flash sheets)
    HF_API_TOKEN: str = ""

    # Cloudflare (DNS management — used by deploy scripts, not the API itself)
    CF_API_KEY: str = ""
    CF_EMAIL: str = ""
    CF_ZONE_ID: str = ""

    # Meta publishing for Herald.
    # These env vars extend the existing Broadcast surface instead of creating
    # a separate content service. Courier owns messages; Herald owns deliberate
    # outbound releases to public social channels.
    META_GRAPH_API_VERSION: str = "v20.0"
    META_FACEBOOK_PAGE_ID: str = ""
    META_FACEBOOK_PAGE_ACCESS_TOKEN: str = ""
    META_INSTAGRAM_ACCOUNT_ID: str = ""
    META_INSTAGRAM_PAGE_ACCESS_TOKEN: str = ""
    # WhatsApp Cloud API uses a separate phone-number id and WABA token even
    # when it lives under the same Meta business app as Facebook/Instagram.
    META_WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""
    META_WHATSAPP_PHONE_NUMBER_ID: str = ""
    META_WHATSAPP_ACCESS_TOKEN: str = ""
    META_WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = ""
    META_WHATSAPP_APP_SECRET: str = ""

    # Web Push (VAPID)
    # Generate with: python -c "from pywebpush import webpush; from py_vapid import Vapid; v=Vapid(); v.generate_keys(); from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat; import base64; b=lambda d: base64.urlsafe_b64encode(d).rstrip(b'=').decode(); print('PRIVATE:', b(v.private_key.private_numbers().private_value.to_bytes(32,'big'))); print('PUBLIC:', b(v.private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)))"
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    # Contact URI push services require ("mailto:..." or "https://...").
    # Blank falls back to mailto:SHOP_EMAIL; with neither, push stays off.
    VAPID_SUBJECT: str = ""

    # Plaid (bank account linking)
    PLAID_CLIENT_ID: str = ""
    PLAID_SECRET: str = ""
    # sandbox or production. Plaid shut down its development environment; any
    # other value makes every Plaid call answer 503 naming this setting.
    PLAID_ENVIRONMENT: str = "sandbox"

    # Clover POS connector
    CLOVER_CLIENT_ID: str = ""
    CLOVER_CLIENT_SECRET: str = ""
    CLOVER_MERCHANT_ID: str = ""
    CLOVER_ACCESS_TOKEN: str = ""
    CLOVER_ENVIRONMENT: str = "sandbox"  # sandbox | production
    CLOVER_REDIRECT_URI: str = "http://localhost:18788/api/v1/econ/clover/callback"

    # Mercury banking connector
    MERCURY_API_TOKEN: str = ""
    MERCURY_API_BASE: str = "https://api.mercury.com/api/v1"
    MERCURY_ALLOWED_EGRESS_IP: str = "152.55.180.240"

    # Stripe Connect
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_CONNECT_WEBHOOK_SECRET: str = ""
    # Accounts v2 thin-event destinations issue their own signing secret. It
    # remains separate from the snapshot webhook so either destination can be
    # rotated or disabled without weakening the other verification boundary.
    STRIPE_CONNECT_THIN_WEBHOOK_SECRET: str = ""
    # Account Link callbacks stay on the single authenticated artist-managed
    # Connections workflow. The endpoint adds the return or refresh state so
    # an expired Stripe link can be replaced through the authenticated owner.
    STRIPE_CONNECT_RETURN_URL: str = "http://localhost:15300/dashboard?portal=hub&workflow=connections"
    # Country of the artists' connected accounts (ISO 3166-1 alpha-2), for
    # example US, GB or CA. Stripe Connect must support it for the platform.
    STRIPE_CONNECT_COUNTRY: str = "US"

    @field_validator("STRIPE_CONNECT_COUNTRY")
    @classmethod
    def validate_stripe_connect_country(cls, value: str) -> str:
        from app.constants.countries import ISO_3166_ALPHA2

        value = (value or "").strip().upper()
        if value not in ISO_3166_ALPHA2:
            raise ValueError("STRIPE_CONNECT_COUNTRY must be an ISO 3166-1 alpha-2 country code, such as US or GB")
        return value

    # Twilio (SMS / A2P 10DLC)
    # Get Account SID + Auth Token from console.twilio.com.
    # TWILIO_FROM_NUMBER remains the WhatsApp sender during the phased multi-number
    # cutover. SMS always resolves one explicit lane sender below and never falls
    # back to this legacy value.
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""
    TWILIO_MESSAGING_SERVICE_SID: str = ""
    TWILIO_BELLA_FROM_NUMBER: str = ""
    TWILIO_GALA_FROM_NUMBER: str = ""
    TWILIO_RYOZAN_COLLECTOR_FROM_NUMBER: str = ""
    # X-Twilio-Signature validation secret; same as TWILIO_AUTH_TOKEN by default
    # but exposed separately in case Twilio rotates webhook signing.
    TWILIO_WEBHOOK_AUTH_TOKEN: str = ""

    # Courier voice lanes. Business voice should stay on Twilio first; the
    # personal lane can move through Jambonz/SIP once the open-source stack is
    # stood up and tested away from customer traffic.
    ELEVENLABS_API_KEY: str = ""
    # Renamed from ELEVENLABS_BTTCO_AGENT_ID; the old name still works and
    # logs a deprecation warning (see app.core.legacy_env).
    ELEVENLABS_RYOZAN_AGENT_ID: str = Field(
        default="",
        validation_alias=AliasChoices("ELEVENLABS_RYOZAN_AGENT_ID", "ELEVENLABS_BTTCO_AGENT_ID"),
    )
    ELEVENLABS_JUSTIN_AGENT_ID: str = ""
    # HMAC secret generated by the ElevenLabs workspace webhook. The value is
    # stored only in the production secret backend and never in source.
    ELEVENLABS_WEBHOOK_SECRET: str = ""
    # Shared bearer for live agent webhook tools such as take-message. It is a
    # separate credential from the workspace post-call signing secret.
    ELEVENLABS_TOOL_SECRET: str = ""
    # Scoped Ryozan to Ark Pack bridge. Blank values fail closed and keep the
    # provider receipt in Ryozan until KeyMaster provisions the runtime binding.
    ARK_PACK_BRIDGE_URL: str = ""
    # Model-assignment previews use a local Ark API without redirecting the
    # existing hosted profiles, intake, or phone-agent bridge.
    ARK_ORCHESTRA_CONFIG_URL: str = ""
    ARK_PACK_BRIDGE_KEY: str = ""
    # Optional mirror of public general inquiries into the operator's ArkStation
    # AInbox. Unset means off. It is never a hard dependency: the Courier thread
    # in Ryozan is the record, and a mirror failure is logged and never surfaced.
    ARK_AINBOX_MIRROR_URL: str | None = None
    ARK_AINBOX_MIRROR_TOKEN: str | None = None
    JAMBONZ_ACCOUNT_SID: str = ""
    JAMBONZ_API_KEY: str = ""
    JAMBONZ_PERSONAL_WEBHOOK_SECRET: str = ""

    @model_validator(mode="after")
    def warn_renamed_env_vars(self):
        """Log once when a renamed setting is supplied only under its BTTCO-era name."""
        env_file = Path(".env")
        values = {**(dotenv_values(env_file) if env_file.is_file() else {}), **os.environ}
        warn_legacy_env("ELEVENLABS_RYOZAN_AGENT_ID", values)
        return self

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
