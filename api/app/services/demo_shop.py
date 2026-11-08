"""Shared judge shop on a hosted demo install.

DEMO_SHOP_ENABLED turns the shared judge login (app.services.demo_account) into
the install's owner and fills the shop with one real artist's public catalog
(app/assets/demo_shop.json) plus sample clients. Judges then run real actions:
a booking lands on the owner calendar, messages go out, the agent works the
enquiries, all inside these limits:

- Writes that reach money, keys, staff or integrations are refused for the
  judge (refuse_demo_owner_writes, attached to those routers).
- Outbound email and SMS never reach a sample address, and a daily cap holds
  for the install and for each recipient (demo_outbound_refusal).
- A live Stripe key is dropped at startup, so checkout only runs in test mode.
- Every night, and on POST /demo-shop/reset, everything created since the seed
  is removed and the seeded rows are written back (reset_demo_shop).

Sample accounts and records use @example.com addresses, so nothing seeded can
receive mail. Records an agent creates, including simulation runs, are
created after the seed and go with the nightly reset like the judge's.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.security import get_optional_current_user, hash_password
from app.models.booking import Booking
from app.models.flash import FlashDesign, FlashSheet
from app.models.session_type import ArtistSessionType
from app.models.studio_setting import StudioSetting
from app.models.user import User, normalize_email
from app.services.demo_account import is_demo_account

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

CATALOG = Path(__file__).resolve().parent.parent / "assets" / "demo_shop.json"
SEEDED_AT_KEY = "demo_shop_seeded_at"
SAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
NPC_EMAILS = ("sample-artist@example.com", "sample-collector@example.com")
PER_RECIPIENT_DAILY_SENDS = 5
_NAMESPACE = uuid.UUID("4f6b2a1e-6d1c-4a8e-9a57-52797a7a6e00")

# Tables cleared of everything created since the seed, children before parents.
RESET_TABLES = (
    "ryozan_quotes",
    "ryozan_pos_transactions",
    "ryozan_gift_card_redemptions",
    "ryozan_booking_slot_holds",
    "ryozan_booking_lifecycle_events",
    "ryozan_booking_addon_selections",
    "ryozan_sms_messages",
    "ryozan_pack_bridge_outbox",
    "ryozan_stash_items",
    "ryozan_stash_canvas_sessions",
    "ryozan_flash_designs",
    "ryozan_bookings",
    "ryozan_messages",
    "ryozan_conversations",
    "ryozan_flash_sheets",
    "ryozan_booking_drafts",
    "ryozan_crm_pipeline_tasks",
    "ryozan_references",
    "ryozan_notifications",
    "ryozan_email_log",
    "ryozan_ai_memories",
    "ryozan_broadcasts",
    "ryozan_time_blocks",
    "ryozan_gift_cards",
    "ryozan_document_instances",
)


# Connection settings a judge can add in demo mode, cleared with the rest.
RESET_SETTING_KEYS = ("email_domain", "texts_connection")


def demo_shop_enabled() -> bool:
    return bool(get_settings().DEMO_SHOP_ENABLED)


def seeded_id(kind: str, key: str) -> uuid.UUID:
    """Seeded rows keep fixed ids, so a reset writes the same rows back over any edit."""
    return uuid.uuid5(_NAMESPACE, f"{kind}:{key}")


def is_sample_address(address: str | None) -> bool:
    domain = (address or "").strip().lower().rpartition("@")[2]
    return domain in SAMPLE_DOMAINS or domain.endswith(".invalid")


def is_demo_npc(user: User | None) -> bool:
    return bool(demo_shop_enabled() and user is not None and normalize_email(user.email or "") in NPC_EMAILS)


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


# ── Write guard ────────────────────────────────────────

# Writes on guarded screens the judge may still make. Stripe Connect onboarding
# only reaches Stripe test mode, since a live key is dropped at startup.
DEMO_OPEN_WRITES = ("/stripe/connect/onboard",)


async def refuse_demo_owner_writes(request: Request, user: User | None = Depends(get_optional_current_user)) -> None:
    """Router dependency: the judge reads these screens but cannot change them."""
    if request.method in ("GET", "HEAD", "OPTIONS") or not demo_shop_enabled():
        return
    if request.url.path.endswith(DEMO_OPEN_WRITES):
        return
    if is_demo_account(user) or is_demo_npc(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The demo shop can view this but not change it.",
        )


# ── Outbound guard ─────────────────────────────────────

_sends: Counter[tuple[str, str]] = Counter()


def demo_outbound_refusal(channel: str, recipient: str | None) -> str | None:
    """Why a send is refused on the demo shop, or None when it may go out (and is counted)."""
    if not demo_shop_enabled():
        return None
    if channel == "email" and is_sample_address(recipient):
        return "sample address"
    today = date.today().isoformat()
    key = (recipient or "").strip().lower()
    if _sends[(today, channel)] >= get_settings().DEMO_SHOP_DAILY_SENDS:
        return "daily demo send cap reached"
    if _sends[(today, f"{channel}:{key}")] >= PER_RECIPIENT_DAILY_SENDS:
        return "daily cap for this recipient reached"
    for stale in [k for k in _sends if k[0] != today]:
        del _sends[stale]
    _sends[(today, channel)] += 1
    _sends[(today, f"{channel}:{key}")] += 1
    return None


def open_owner_booking() -> None:
    """The judge adds bookings from the owner calendar, which books on the shop artist's behalf."""
    if demo_shop_enabled():
        get_settings().ADMIN_BOOK_ON_BEHALF_ENABLED = True


def refuse_live_stripe_key() -> bool:
    """Drop a live Stripe key so checkout on the demo shop only runs in test mode."""
    settings = get_settings()
    key = (settings.STRIPE_SECRET_KEY or "").strip()
    if not demo_shop_enabled() or not key.startswith(("sk_live", "rk_live")):
        return False
    settings.STRIPE_SECRET_KEY = ""
    return True


# ── Seed ───────────────────────────────────────────────


def _unusable_password() -> str:
    return hash_password(secrets.token_urlsafe(32))


async def _upsert(db: AsyncSession, model, row_id: uuid.UUID, **fields):
    row = await db.get(model, row_id)
    if row is None:
        row = model(id=row_id, **fields)
        db.add(row)
    else:
        for name, value in fields.items():
            setattr(row, name, value)
    return row


async def _seed_user(db: AsyncSession, email: str, password_hash: str | None, **fields) -> User:
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(id=seeded_id("user", email), email=email, password_hash=password_hash or _unusable_password())
        db.add(user)
    elif password_hash:
        user.password_hash = password_hash
    for name, value in fields.items():
        setattr(user, name, value)
    user.email_verified_at = user.email_verified_at or datetime.now(UTC)
    user.must_reset_password = False
    user.password_state = "user_set"
    return user


async def seed_demo_shop(db: AsyncSession, *, restart_window: bool = False) -> str:
    """Write the catalog and sample records; safe to run on every start.

    The seed time marks where the reset window starts. Only the first seed and a
    reset move it, so a restart does not shelter the day's records from the reset.
    """
    catalog = load_catalog()
    settings = get_settings()
    # The owner screens show the signed-in owner's own book, so the catalog goes on
    # the judge's owner account: the shop the judge runs is the artist's shop.
    owner_email = normalize_email(settings.DEMO_ACCOUNT_EMAIL or "")
    artist = (
        await db.execute(select(User).where(User.email == owner_email, User.role == "founder"))
    ).scalar_one_or_none()
    if artist is None:
        return "skipped: the demo account is not the shop owner"
    artist_data = catalog["artist"]
    for name in artist_data["profile_fields"]:
        setattr(artist, name, artist_data[name])
    if restart_window:
        # Each day's judges start Stripe Connect test onboarding from scratch.
        artist.stripe_account_id = None
        artist.stripe_onboarding_complete = False
    npc_password = settings.DEMO_NPC_PASSWORD
    npc_hash = hash_password(npc_password) if len(npc_password) >= 12 else None
    await _seed_user(db, NPC_EMAILS[0], npc_hash, role="artist", name="Sample Artist", slug="sample-artist")
    await _seed_user(db, NPC_EMAILS[1], npc_hash, role="collector", name="Sample Collector")
    await db.flush()

    for index, session in enumerate(catalog["session_types"]):
        await _upsert(
            db,
            ArtistSessionType,
            seeded_id("session", session["label"]),
            artist_id=artist.id,
            is_active=True,
            sort_order=(index + 1) * 10,
            **session,
        )

    sheet = catalog["flash_sheet"]
    sheet_id = seeded_id("sheet", sheet["title"])
    await _upsert(
        db,
        FlashSheet,
        sheet_id,
        artist_id=artist.id,
        image_url=sheet["image_url"],
        title=sheet["title"],
        status="published",
        design_count=len(catalog["flash"]),
    )
    await db.flush()
    for design in catalog["flash"]:
        await _upsert(
            db,
            FlashDesign,
            seeded_id("flash", design["name"]),
            sheet_id=sheet_id,
            artist_id=artist.id,
            status="published",
            claimed_by=None,
            claimed_at=None,
            booking_id=None,
            gala_sync_enabled=False,
            **design,
        )

    today = date.today()
    for sample in catalog["sample_clients"]:
        fields = {
            "artist_id": artist.id,
            "guest_name": sample["name"],
            "guest_email": sample["email"],
            "description": sample["description"],
            "placement": sample["placement"],
            "size_estimate": sample["size"],
            "status": sample["status"],
            "reference_code": sample["reference_code"],
            "source": "demo_seed",
            "scheduled_date": None,
            "scheduled_time": None,
            "duration_minutes": None,
            "deposit_amount": None,
            "estimated_total": None,
            "deposit_paid": False,
        }
        if sample.get("days_out") is not None:
            fields.update(
                scheduled_date=today + timedelta(days=sample["days_out"]),
                scheduled_time=sample["time"],
                duration_minutes=sample["minutes"],
                deposit_amount=sample["deposit"],
                estimated_total=sample["total"],
                deposit_paid=bool(sample.get("deposit_paid")),
            )
        await _upsert(db, Booking, seeded_id("booking", sample["reference_code"]), **fields)

    await db.flush()
    marker = await db.get(StudioSetting, SEEDED_AT_KEY)
    first = marker is None
    if first:
        marker = StudioSetting(key=SEEDED_AT_KEY)
        db.add(marker)
    if first or restart_window:
        marker.value = datetime.now(UTC).isoformat()
    await db.commit()
    return "seeded" if first else "refreshed"


# ── Reset ──────────────────────────────────────────────


async def reset_demo_shop(db: AsyncSession) -> dict:
    """Remove everything made since the seed, then write the seeded rows back."""
    marker = await db.get(StudioSetting, SEEDED_AT_KEY)
    if marker is None:
        return {"removed": {}, "seed": await seed_demo_shop(db)}
    since = datetime.fromisoformat(marker.value)
    removed: dict[str, int] = {}
    for table in RESET_TABLES:
        result = await db.execute(text(f"DELETE FROM {table} WHERE created_at > :since"), {"since": since})  # noqa: S608
        if result.rowcount:
            removed[table] = result.rowcount
    keep = [normalize_email(get_settings().DEMO_ACCOUNT_EMAIL or ""), *NPC_EMAILS]
    newcomers = (await db.execute(select(User).where(User.created_at > since, User.email.not_in(keep)))).scalars().all()
    users = 0
    for user in newcomers:
        try:
            async with db.begin_nested():
                await db.delete(user)
            users += 1
        except Exception:  # still referenced somewhere; it goes on a later night
            logger.warning("demo shop reset kept user %s", user.id)
    if users:
        removed["ryozan_users"] = users
    # A connection the judge set up in demo mode goes too.
    for key in RESET_SETTING_KEYS:
        row = await db.get(StudioSetting, key)
        if row is not None:
            await db.delete(row)
            removed[f"setting:{key}"] = 1
    await db.commit()
    from app.services import shop_email_domain, shop_texts

    await shop_email_domain.load(db)
    await shop_texts.load(db)
    return {"removed": removed, "seed": await seed_demo_shop(db, restart_window=True)}


def seconds_until_next_reset(now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    target = now.replace(hour=get_settings().DEMO_SHOP_RESET_HOUR_UTC % 24, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_nightly_resets(session_factory) -> None:
    """Background task: reset the demo shop once a day at DEMO_SHOP_RESET_HOUR_UTC."""
    while True:
        await asyncio.sleep(seconds_until_next_reset())
        try:
            async with session_factory() as db:
                outcome = await reset_demo_shop(db)
            print(f"[Ryozan] Demo shop reset: {outcome}", flush=True)
        except Exception as exc:  # one failed night must not end the loop
            print(f"[Ryozan] Demo shop reset failed: {type(exc).__name__}", flush=True)
