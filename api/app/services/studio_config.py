"""
Studio values the owner sets in the app, and the first-run claim of a new install.

Every reader already asks get_settings(), so saved values are laid over that one
Settings object rather than given a second read path. A saved value wins over
the environment, including a saved blank. Only the identity, booking-day and
public-site values in EDITABLE_SETTINGS can be saved: secrets, URLs and provider
keys stay in the environment.

One install runs one API process (see the Dockerfile), so applying to the cached
object reaches every reader. A second process would pick a save up on restart.
"""

import base64
import hashlib
import hmac

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.studio_setting import StudioSetting
from app.models.user import User

EDITABLE_SETTINGS: tuple[str, ...] = (
    "STUDIO_NAME",
    "STUDIO_TIMEZONE",
    "STUDIO_OWNER_NAME",
    "SHOP_EMAIL",
    "STUDIO_PHONE",
    "STUDIO_ADDRESS",
    "STUDIO_ARTIST_ROSTER",
    "BOOKING_CLOSED_WEEKDAYS",
    "BOOKING_REQUEST_ONLY_WEEKDAYS",
    "PUBLIC_SITE_MODE",
    "STUDIO_TAGLINE",
    "STUDIO_STORY",
    "STUDIO_HOURS",
    "STUDIO_HERO_IMAGE_URL",
    "STUDIO_GALLERY_IMAGE_URLS",
    "STUDIO_SOCIAL_LINKS",
)

# Inserted with the first owner. Its primary key lets only one claim commit.
SETUP_CLAIM_KEY = "_setup_claimed"


class StudioSettingsError(ValueError):
    """A submitted value failed the Settings validator for its field."""


def validated_changes(settings: Settings, changes: dict[str, str]) -> dict[str, str]:
    """Run each change through the Settings field validators, without applying it."""
    unknown = set(changes) - set(EDITABLE_SETTINGS)
    if unknown:
        raise StudioSettingsError(f"Not editable: {', '.join(sorted(unknown))}")
    trial = settings.model_copy()
    clean: dict[str, str] = {}
    for key, value in changes.items():
        try:
            Settings.__pydantic_validator__.validate_assignment(trial, key, value)
        except ValidationError as exc:
            raise StudioSettingsError(exc.errors()[0]["msg"].removeprefix("Value error, ")) from exc
        clean[key] = getattr(trial, key)
    if "STUDIO_NAME" in clean and not clean["STUDIO_NAME"].strip():
        raise StudioSettingsError("The studio needs a name.")
    return clean


def apply_values(settings: Settings, values: dict[str, str]) -> None:
    for key, value in values.items():
        if key in EDITABLE_SETTINGS:
            setattr(settings, key, value)


async def load_saved_settings(db: AsyncSession) -> int:
    """Lay the saved values over Settings. Runs once at startup, before the seeds."""
    rows = (await db.execute(select(StudioSetting))).scalars().all()
    settings = get_settings()
    usable: dict[str, str] = {}
    for row in rows:
        if row.key not in EDITABLE_SETTINGS:
            continue
        try:
            usable.update(validated_changes(settings, {row.key: row.value}))
        except StudioSettingsError as exc:
            print(f"[Ryozan] Saved studio setting {row.key} ignored: {exc}", flush=True)
    apply_values(settings, usable)
    return len(usable)


def stage_settings(db: AsyncSession, existing: dict[str, StudioSetting], values: dict[str, str]) -> None:
    for key, value in values.items():
        row = existing.get(key)
        if row is None:
            db.add(StudioSetting(key=key, value=value))
        else:
            row.value = value


async def saved_rows(db: AsyncSession, keys: list[str]) -> dict[str, StudioSetting]:
    rows = (await db.execute(select(StudioSetting).where(StudioSetting.key.in_(keys)))).scalars().all()
    return {row.key: row for row in rows}


async def setup_is_open(db: AsyncSession) -> bool:
    """True until the install has an owner. An install with a founder is never claimable."""
    if await db.get(StudioSetting, SETUP_CLAIM_KEY) is not None:
        return False
    founders = await db.scalar(select(func.count()).select_from(User).where(User.role == "founder"))
    return not founders


def setup_code(settings: Settings) -> str:
    """The code that claims a new install, derived from SECRET_KEY.

    The person who deployed the install can read it in the startup log, and a
    stranger who finds the URL first cannot. It is stable across restarts, and
    worthless once the install has an owner.
    """
    digest = hmac.new(settings.SECRET_KEY.encode(), b"ryozan-first-run-setup", hashlib.sha256).digest()
    raw = base64.b32encode(digest).decode()[:12]
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def setup_code_matches(settings: Settings, submitted: str) -> bool:
    normalize = lambda code: "".join(code.split()).replace("-", "").upper()  # noqa: E731
    return hmac.compare_digest(normalize(submitted).encode(), normalize(setup_code(settings)).encode())
