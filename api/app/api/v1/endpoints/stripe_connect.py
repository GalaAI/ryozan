"""
Stripe Connect endpoints — artist onboarding and account management.

Uses Accounts v2 merchant accounts and direct charges with application fees.
New artists receive full Stripe Dashboard access and Stripe-held fee and loss
responsibility. Existing connected accounts continue in place.
"""

import logging
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.integrations import STRIPE_CONNECT_ROLES
from app.constants.timezones import SHOP_TIMEZONE
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.econ import EconLedgerEntry
from app.models.user import User
from app.services.payments import connect_account_ready, effective_artist_rate
from app.services.stripe_client import get_stripe_client

logger = logging.getLogger("ryozan.stripe")
router = APIRouter(prefix="/stripe", tags=["stripe"])

BOOTH_RENT_ACTIVE_STATUSES = {"active", "trialing", "past_due", "unpaid", "incomplete", "paused"}
CONNECT_PLATFORM_PROFILE_CODE = "stripe_connect_platform_profile_incomplete"


class BoothRentConfigRequest(BaseModel):
    """Founder-controlled artist revenue model and rent terms."""

    enabled: bool = True
    amount_cents: int | None = Field(default=None, ge=0, le=1_000_000)
    interval: Literal["biweekly", "month", "week"] = "month"
    payment_method: Literal["cash", "venmo", "one_time", "autodraft", "stripe"] = "cash"
    commission_rate: int | None = Field(default=None, ge=0, le=99)
    is_guest_artist: bool | None = None
    next_due_date: date | None = None


class BoothRentCashPaymentRequest(BaseModel):
    """One manual booth-rent receipt for a monthly or biweekly period."""

    period: str = Field(pattern=r"^\d{4}-(?:\d{2}-\d{2}|\d{2}|W\d{2})$")


def _init_stripe():
    return get_stripe_client()


def _stripe_configured() -> bool:
    return bool(get_settings().STRIPE_SECRET_KEY.strip())


def _not_connected_status(
    provider_state: Literal["not_connected", "unavailable"] = "not_connected",
) -> dict[str, bool | int | str]:
    return {
        "connected": False,
        "onboarding_complete": False,
        "routing_ready": False,
        "charges_enabled": False,
        "payouts_enabled": False,
        "provider_state": provider_state,
        "requirements_due": 0,
    }


def _create_connected_account(user: User):
    """Create one Accounts v2 identity that can sell and pay booth rent."""
    return _init_stripe().v2.core.accounts.create(
        {
            "display_name": user.name,
            "contact_email": user.email,
            "identity": {"country": "us"},
            "dashboard": "full",
            "defaults": {
                "responsibilities": {
                    "fees_collector": "stripe",
                    "losses_collector": "stripe",
                }
            },
            "configuration": {
                "customer": {},
                "merchant": {
                    "capabilities": {
                        "card_payments": {"requested": True},
                    }
                },
            },
        }
    )


def _retrieve_connected_account(account_id: str):
    """Read live merchant capability and requirements state from Accounts v2."""
    return _init_stripe().v2.core.accounts.retrieve(
        account_id,
        {"include": ["configuration.merchant", "requirements"]},
    )


def _create_account_link(
    account_id: str,
    *,
    configurations: list[str],
    refresh_url: str,
    return_url: str,
):
    """Create hosted onboarding for the Account's applied configurations."""
    return _init_stripe().v2.core.account_links.create(
        {
            "account": account_id,
            "use_case": {
                "type": "account_onboarding",
                "account_onboarding": {
                    "configurations": configurations,
                    "refresh_url": refresh_url,
                    "return_url": return_url,
                },
            },
        }
    )


def _connect_hub_url(base_url: str, state: Literal["refresh", "return"]) -> str:
    """Target the authenticated Hub Connections owner with explicit callback state."""
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(
        {
            "portal": "hub",
            "workflow": "connections",
            "stripe": state,
        }
    )
    return urlunsplit(parts._replace(query=urlencode(query)))


def _account_link_configurations(account) -> list[str]:
    """Return applied configurations accepted by Accounts v2 hosted onboarding."""
    supported = {"customer", "merchant", "recipient"}
    applied = _stripe_dict(account).get("applied_configurations") or []
    return list(dict.fromkeys(configuration for configuration in applied if configuration in supported))


def _ensure_customer_configuration(account_id: str) -> list[str]:
    """Add customer behavior once and return the Account's onboarding configurations."""
    account = _retrieve_connected_account(account_id)
    configurations = _account_link_configurations(account)
    if "customer" not in configurations:
        _init_stripe().v2.core.accounts.update(
            account_id,
            {"configuration": {"customer": {}}},
        )
        account = _retrieve_connected_account(account_id)
        configurations = _account_link_configurations(account)
    return configurations


def _connect_onboarding_error(exc: stripe.error.StripeError) -> HTTPException:
    """Map provider onboarding failures to safe, actionable API errors."""
    message = str(exc).lower()
    if "complete your platform profile" in message:
        return HTTPException(
            status_code=409,
            detail={
                "code": CONNECT_PLATFORM_PROFILE_CODE,
                "message": ("Complete Ryozan's Stripe Connect platform profile before starting artist onboarding."),
            },
        )
    return HTTPException(
        status_code=502,
        detail={
            "code": "stripe_connect_onboarding_unavailable",
            "message": "Stripe Connect could not start onboarding. Try again shortly.",
        },
    )


def _artist_connect_projection(user: User) -> dict:
    projection = {
        "id": str(user.id),
        "name": user.name,
        "role": user.role,
        "slug": user.slug,
        "email": user.email,
        "commission_rate": effective_artist_rate(user),
        "stripe_account_id": user.stripe_account_id,
        "stripe_onboarding_complete": user.stripe_onboarding_complete,
        "routing_ready": user.stripe_onboarding_complete,
        "charges_enabled": False,
        "payouts_enabled": False,
        "provider_state": "not_connected",
        "requirements_due": 0,
        "is_guest_artist": bool(getattr(user, "is_guest_artist", False)),
        "booth_rent": _booth_rent_projection(user),
    }
    if not user.stripe_account_id:
        user.stripe_onboarding_complete = False
        projection["stripe_onboarding_complete"] = False
        projection["routing_ready"] = False
        return projection
    if not _stripe_configured():
        projection["provider_state"] = "unavailable"
        projection["routing_ready"] = False
        return projection

    try:
        account = _retrieve_connected_account(user.stripe_account_id)
        account_state = _connect_account_state(account)
        routing_ready = bool(account_state["routing_ready"])
        user.stripe_onboarding_complete = routing_ready
        projection.update(
            {
                "stripe_onboarding_complete": routing_ready,
                **account_state,
            }
        )
    except stripe.error.StripeError as exc:
        logger.warning("Stripe artist status unavailable for %s: %s", user.id, exc)
        projection["provider_state"] = "unavailable"
        projection["routing_ready"] = False
    return projection


def _stripe_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    converter = getattr(value, "to_dict_recursive", None)
    if callable(converter):
        return converter()
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        return converter()
    return {}


def _connect_account_state(account) -> dict[str, bool | int | str]:
    """Project one Accounts v2 response into the shared artist readiness contract."""
    account_data = _stripe_dict(account)
    requirements = account_data.get("requirements") or {}
    minimum_deadline = (requirements.get("summary") or {}).get("minimum_deadline") or {}
    requirements_status = minimum_deadline.get("status")
    merchant_capabilities = ((account_data.get("configuration") or {}).get("merchant") or {}).get("capabilities") or {}
    card_status = (merchant_capabilities.get("card_payments") or {}).get("status")
    payout_status = ((merchant_capabilities.get("stripe_balance") or {}).get("payouts") or {}).get("status")
    return {
        "routing_ready": connect_account_ready(account),
        "charges_enabled": card_status == "active",
        "payouts_enabled": payout_status == "active",
        "provider_state": "live",
        "requirements_due": 1 if requirements_status in {"currently_due", "past_due"} else 0,
    }


def _subscription_period_end(subscription: dict) -> datetime | None:
    timestamp = subscription.get("current_period_end")
    if not timestamp:
        items = (subscription.get("items") or {}).get("data") or []
        if items:
            timestamp = items[0].get("current_period_end")
    if not timestamp:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=UTC)


#: Booth-rent methods that never touch a card rail, so nothing reports them and
#: they only exist in the books once a human records them. Cash lands in the safe,
#: Venmo lands in a Venmo balance; both are booth rent income for tax.
MANUALLY_RECORDED_BOOTH_METHODS = frozenset({"cash", "venmo"})


def _booth_payment_method(user: User) -> str:
    method = str(getattr(user, "booth_rent_payment_method", "cash") or "cash")
    return "autodraft" if method == "stripe" else method


def _normalize_booth_interval(interval: str | None) -> str | None:
    return "biweekly" if interval == "week" else interval


def _next_month_first(current: date | None = None) -> date:
    value = current or datetime.now(SHOP_TIMEZONE).date()
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _advance_booth_due_date(due_date: date, interval: str) -> date:
    if _normalize_booth_interval(interval) == "biweekly":
        return due_date + timedelta(days=14)
    month = 1 if due_date.month == 12 else due_date.month + 1
    year = due_date.year + 1 if due_date.month == 12 else due_date.year
    return date(year, month, min(due_date.day, monthrange(year, month)[1]))


def _booth_installment_amount(amount_cents: int, interval: str) -> int:
    if _normalize_booth_interval(interval) == "biweekly":
        return (amount_cents * 12 + 13) // 26
    return amount_cents


def _due_datetime(due_date: date) -> datetime:
    return datetime.combine(due_date, time.min, tzinfo=SHOP_TIMEZONE).astimezone(UTC)


def _booth_rent_period(interval: str, next_due_date: date | None = None) -> tuple[str, datetime]:
    normalized = _normalize_booth_interval(interval) or "month"
    period_start = next_due_date or _next_month_first()
    period_end = _advance_booth_due_date(period_start, normalized)
    period = period_start.isoformat() if normalized == "biweekly" else period_start.strftime("%Y-%m")
    return period, _due_datetime(period_end)


def _sync_booth_rent_subscription(user: User, subscription) -> None:
    data = _stripe_dict(subscription)
    status = str(data.get("status") or "pending")
    user.booth_rent_status = status
    user.booth_rent_cancel_at_period_end = bool(data.get("cancel_at_period_end"))
    user.booth_rent_current_period_end = _subscription_period_end(data)
    if user.booth_rent_current_period_end:
        user.booth_rent_next_due_date = user.booth_rent_current_period_end.astimezone(SHOP_TIMEZONE).date()
    if data.get("id"):
        user.booth_rent_subscription_id = str(data["id"])
    user.updated_at = datetime.now(UTC)


def _booth_rent_projection(user: User) -> dict:
    amount_cents = getattr(user, "booth_rent_amount_cents", None)
    commission_rate = getattr(user, "commission_rate", None)
    current_period_end = getattr(user, "booth_rent_current_period_end", None)
    next_due_date = getattr(user, "booth_rent_next_due_date", None)
    configured = bool(amount_cents and amount_cents > 0 and commission_rate == 100)
    payment_method = _booth_payment_method(user)
    status = getattr(user, "booth_rent_status", "not_configured")
    today = datetime.now(SHOP_TIMEZONE).date()
    if payment_method in {"cash", "one_time"} and next_due_date:
        if next_due_date > today and status in {"due", "pending", "scheduled"}:
            status = "scheduled"
        elif next_due_date <= today and status in {"paid", "scheduled"}:
            status = "due"
    interval = _normalize_booth_interval(getattr(user, "booth_rent_interval", None))
    return {
        "configured": configured,
        "amount_cents": amount_cents,
        "installment_amount_cents": _booth_installment_amount(amount_cents, interval)
        if amount_cents and interval
        else None,
        "interval": interval,
        "payment_method": payment_method,
        "status": status,
        "subscription_id": getattr(user, "booth_rent_subscription_id", None),
        "current_period_end": current_period_end.isoformat() if current_period_end else None,
        "next_due_date": next_due_date.isoformat() if next_due_date else None,
        "cancel_at_period_end": getattr(user, "booth_rent_cancel_at_period_end", False),
        "commission_rate": commission_rate,
    }


def _require_role(user: User, min_role: str):
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")


def _require_artist(user: User) -> None:
    if user.role != "artist":
        raise HTTPException(status_code=403, detail="Requires an artist account")


def _require_connect_artist(user: User) -> None:
    if user.role not in STRIPE_CONNECT_ROLES:
        raise HTTPException(status_code=403, detail="Requires an artist-capable account")


@router.post("/connect/onboard")
async def create_connect_account(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a Stripe Connected Account for the current artist and return
    an onboarding link. Artist completes identity verification on Stripe's
    hosted onboarding page.
    """
    _require_connect_artist(current_user)

    if not _stripe_configured():
        logger.info("Stripe Connect onboarding unavailable: STRIPE_SECRET_KEY is not configured")
        raise HTTPException(status_code=503, detail="Stripe Connect is not configured")

    settings = get_settings()
    refresh_url = _connect_hub_url(settings.STRIPE_CONNECT_RETURN_URL, "refresh")
    return_url = _connect_hub_url(settings.STRIPE_CONNECT_RETURN_URL, "return")

    try:
        # If they already have an account, return a fresh onboarding link.
        if current_user.stripe_account_id:
            configurations = _ensure_customer_configuration(current_user.stripe_account_id)
            link = _create_account_link(
                current_user.stripe_account_id,
                configurations=configurations,
                refresh_url=refresh_url,
                return_url=return_url,
            )
            return {"url": link.url, "account_id": current_user.stripe_account_id}

        account = _create_connected_account(current_user)
        configurations = _ensure_customer_configuration(account.id)
        link = _create_account_link(
            account.id,
            configurations=configurations,
            refresh_url=refresh_url,
            return_url=return_url,
        )
    except stripe.error.StripeError as exc:
        logger.warning(
            "Stripe Connect onboarding failed for user %s: type=%s code=%s param=%s request_id=%s",
            current_user.id,
            type(exc).__name__,
            getattr(exc, "code", None),
            getattr(exc, "param", None),
            getattr(exc, "request_id", None),
        )
        raise _connect_onboarding_error(exc) from exc

    current_user.stripe_account_id = account.id
    await db.flush()

    return {"url": link.url, "account_id": account.id}


@router.get("/connect/status")
async def get_connect_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check current artist's Stripe Connect onboarding status."""
    _require_connect_artist(current_user)

    if not _stripe_configured():
        return _not_connected_status("unavailable")

    if not current_user.stripe_account_id:
        return _not_connected_status()

    try:
        account = _retrieve_connected_account(current_user.stripe_account_id)
    except stripe.error.StripeError as e:
        logger.warning("Stripe Connect status fetch failed for %s: %s", current_user.id, e)
        raise HTTPException(status_code=502, detail="Stripe Connect status unavailable") from e

    account_state = _connect_account_state(account)
    routing_ready = bool(account_state["routing_ready"])
    if current_user.stripe_onboarding_complete != routing_ready:
        current_user.stripe_onboarding_complete = routing_ready
        await db.flush()

    return {
        "connected": True,
        "onboarding_complete": routing_ready,
        **account_state,
        "account_id": current_user.stripe_account_id,
    }


@router.post("/connect/onboard/{user_id}")
async def admin_create_connect_account(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin: create a Connect account for a specific artist and return onboarding link."""
    _require_role(current_user, "admin")

    if not _stripe_configured():
        logger.info("Stripe Connect admin onboarding unavailable: STRIPE_SECRET_KEY is not configured")
        raise HTTPException(status_code=503, detail="Stripe Connect is not configured")

    settings = get_settings()
    refresh_url = _connect_hub_url(settings.STRIPE_CONNECT_RETURN_URL, "refresh")
    return_url = _connect_hub_url(settings.STRIPE_CONNECT_RETURN_URL, "return")

    result = await db.execute(select(User).where(User.id == user_id))
    artist = result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="User not found")
    if artist.role not in STRIPE_CONNECT_ROLES:
        raise HTTPException(status_code=400, detail="User is not artist-capable")

    try:
        if artist.stripe_account_id:
            configurations = _ensure_customer_configuration(artist.stripe_account_id)
            link = _create_account_link(
                artist.stripe_account_id,
                configurations=configurations,
                refresh_url=refresh_url,
                return_url=return_url,
            )
            return {"url": link.url, "account_id": artist.stripe_account_id}

        account = _create_connected_account(artist)
        configurations = _ensure_customer_configuration(account.id)
        link = _create_account_link(
            account.id,
            configurations=configurations,
            refresh_url=refresh_url,
            return_url=return_url,
        )
    except stripe.error.StripeError as exc:
        logger.warning(
            "Stripe Connect admin onboarding failed for user %s: type=%s code=%s param=%s request_id=%s",
            artist.id,
            type(exc).__name__,
            getattr(exc, "code", None),
            getattr(exc, "param", None),
            getattr(exc, "request_id", None),
        )
        raise _connect_onboarding_error(exc) from exc

    artist.stripe_account_id = account.id
    await db.flush()

    return {"url": link.url, "account_id": account.id}


@router.get("/connect/artists")
async def list_artist_stripe_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin: list all artists with their Stripe Connect status."""
    _require_role(current_user, "admin")

    result = await db.execute(select(User).where(User.role.in_(STRIPE_CONNECT_ROLES)))
    artists = result.scalars().all()

    projections = [_artist_connect_projection(artist) for artist in artists]
    await db.flush()
    return projections


@router.put("/booth-rent/artists/{user_id}")
async def configure_artist_booth_rent(
    user_id: UUID,
    payload: BoothRentConfigRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Founder/admin: configure booth rent or return an artist to commission."""
    _require_role(current_user, "admin")
    result = await db.execute(select(User).where(User.id == user_id).with_for_update())
    artist = result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="User not found")
    if artist.role != "artist":
        raise HTTPException(status_code=400, detail="User is not an artist")

    active_subscription = bool(
        artist.booth_rent_subscription_id and artist.booth_rent_status in BOOTH_RENT_ACTIVE_STATUSES
    )
    if payload.is_guest_artist is not None:
        artist.is_guest_artist = payload.is_guest_artist
    if payload.enabled:
        payment_method = "autodraft" if payload.payment_method == "stripe" else payload.payment_method
        interval = _normalize_booth_interval(payload.interval) or "month"
        next_due_date = payload.next_due_date or artist.booth_rent_next_due_date or _next_month_first()
        if payload.amount_cents is None or payload.amount_cents < 100:
            raise HTTPException(status_code=422, detail="Booth rent must be at least $1.00")
        terms_changed = (
            artist.booth_rent_amount_cents != payload.amount_cents
            or _normalize_booth_interval(artist.booth_rent_interval) != interval
            or _booth_payment_method(artist) != payment_method
            or artist.booth_rent_next_due_date != next_due_date
        )
        if active_subscription and terms_changed:
            raise HTTPException(
                status_code=409,
                detail="Cancel the active booth-rent subscription before changing its amount or schedule",
            )
        artist.commission_rate = 100
        artist.booth_rent_amount_cents = payload.amount_cents
        artist.booth_rent_interval = interval
        artist.booth_rent_payment_method = payment_method
        artist.booth_rent_next_due_date = next_due_date
        if not active_subscription:
            if next_due_date > datetime.now(SHOP_TIMEZONE).date():
                artist.booth_rent_status = "scheduled"
            else:
                artist.booth_rent_status = "pending" if payment_method == "autodraft" else "due"
            artist.booth_rent_subscription_id = None
            artist.booth_rent_checkout_session_id = None
            artist.booth_rent_current_period_end = None
            artist.booth_rent_cancel_at_period_end = False
    else:
        if active_subscription:
            raise HTTPException(
                status_code=409,
                detail="Cancel the active booth-rent subscription before returning this artist to commission",
            )
        if payload.commission_rate is None:
            raise HTTPException(status_code=422, detail="A commission rate is required when booth rent is disabled")
        artist.commission_rate = payload.commission_rate
        artist.booth_rent_amount_cents = None
        artist.booth_rent_interval = None
        artist.booth_rent_payment_method = "cash"
        artist.booth_rent_status = "not_configured"
        artist.booth_rent_subscription_id = None
        artist.booth_rent_checkout_session_id = None
        artist.booth_rent_current_period_end = None
        artist.booth_rent_next_due_date = None
        artist.booth_rent_cancel_at_period_end = False

    artist.updated_at = datetime.now(UTC)
    projection = _artist_connect_projection(artist)
    await db.flush()
    return projection


@router.post("/booth-rent/artists/{user_id}/cash-payment")
async def record_artist_booth_rent_cash_payment(
    user_id: UUID,
    payload: BoothRentCashPaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Founder/admin: record one cash booth-rent contractor contribution."""
    _require_role(current_user, "admin")
    result = await db.execute(select(User).where(User.id == user_id).with_for_update())
    artist = result.scalar_one_or_none()
    if not artist:
        raise HTTPException(status_code=404, detail="User not found")
    if artist.role != "artist":
        raise HTTPException(status_code=400, detail="User is not an artist")
    if (
        artist.commission_rate != 100
        or _booth_payment_method(artist) not in MANUALLY_RECORDED_BOOTH_METHODS
        or not artist.booth_rent_amount_cents
        or _normalize_booth_interval(artist.booth_rent_interval) not in {"biweekly", "month"}
    ):
        raise HTTPException(
            status_code=409,
            detail="Manually recorded booth rent (cash or Venmo) is not configured for this artist",
        )
    try:
        interval = _normalize_booth_interval(artist.booth_rent_interval) or "month"
        if interval == "biweekly":
            if "-W" in payload.period:
                period_start = datetime.strptime(f"{payload.period}-1", "%G-W%V-%u").date()
            else:
                period_start = date.fromisoformat(payload.period)
            period_end = _advance_booth_due_date(period_start, interval)
        else:
            period_start = datetime.strptime(f"{payload.period}-01", "%Y-%m-%d").date()
            period_end = _advance_booth_due_date(period_start, interval)
    except ValueError as exc:
        expected = "YYYY-MM-DD" if interval == "biweekly" else "YYYY-MM"
        raise HTTPException(status_code=422, detail=f"Booth-rent period must be a valid {expected} value") from exc

    method = _booth_payment_method(artist)
    # Keyed on the period rather than the method: a period is paid once, whichever
    # way the money arrived, so recording it again under the other method must be
    # a no-op rather than double income.
    external_id = f"booth-rent:{artist.id}:{payload.period}"
    existing_result = await db.execute(
        select(EconLedgerEntry).where(
            EconLedgerEntry.provider == "manual",
            EconLedgerEntry.external_id == external_id,
        )
    )
    entry = existing_result.scalar_one_or_none()
    already_recorded = entry is not None
    if entry is None:
        entry = EconLedgerEntry(
            provider="manual",
            external_id=external_id,
            entry_type="payment",
            status="posted",
            amount_cents=_booth_installment_amount(artist.booth_rent_amount_cents, interval),
            currency="USD",
            occurred_at=datetime.now(UTC),
            description=f"Booth rent - {artist.name} - {payload.period}",
            counterparty=artist.name,
            category="contractor_contribution",
            payroll_tax_bucket="income",
            artist_id=artist.id,
            raw={
                "contribution_type": "booth_rent",
                "payment_method": method,
                "booth_rent_period": payload.period,
                "recorded_by": str(current_user.id),
            },
        )
        db.add(entry)

    artist.booth_rent_status = "paid"
    artist.booth_rent_current_period_end = _due_datetime(period_end)
    artist.booth_rent_next_due_date = period_end
    artist.updated_at = datetime.now(UTC)
    await db.flush()
    return {
        "already_recorded": already_recorded,
        "payment": {
            "id": str(entry.id),
            "amount_cents": entry.amount_cents,
            "period": payload.period,
            "category": entry.category,
            "payment_method": method,
        },
        "artist": _artist_connect_projection(artist),
    }


@router.get("/booth-rent/status")
async def get_booth_rent_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Artist: inspect configured rent and synchronize the Stripe subscription."""
    _require_artist(current_user)
    if current_user.booth_rent_subscription_id and _stripe_configured():
        try:
            subscription = _init_stripe().v1.subscriptions.retrieve(current_user.booth_rent_subscription_id)
            _sync_booth_rent_subscription(current_user, subscription)
            await db.flush()
        except stripe.error.InvalidRequestError:
            current_user.booth_rent_status = "canceled"
            current_user.booth_rent_cancel_at_period_end = False
            current_user.booth_rent_current_period_end = None
            await db.flush()
        except stripe.error.StripeError as exc:
            logger.warning("Stripe booth-rent status unavailable for %s: %s", current_user.id, exc)
    return _booth_rent_projection(current_user)


@router.post("/booth-rent/checkout")
async def create_booth_rent_checkout(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Artist: start one-time or automatic Stripe booth-rent payment."""
    _require_artist(current_user)
    if not _stripe_configured():
        raise HTTPException(status_code=503, detail="Stripe Billing is not configured")

    result = await db.execute(select(User).where(User.id == current_user.id).with_for_update())
    artist = result.scalar_one()
    amount_cents = artist.booth_rent_amount_cents
    interval = _normalize_booth_interval(artist.booth_rent_interval)
    if artist.role != "artist" or artist.commission_rate != 100 or not amount_cents or not interval:
        raise HTTPException(status_code=409, detail="Booth rent has not been configured for this artist")
    payment_method = _booth_payment_method(artist)
    if payment_method == "cash":
        raise HTTPException(status_code=409, detail="Booth rent is configured for cash payment")
    if payment_method == "autodraft" and (
        artist.booth_rent_subscription_id and artist.booth_rent_status in BOOTH_RENT_ACTIVE_STATUSES
    ):
        raise HTTPException(status_code=409, detail="Booth rent is already active; use Manage payment instead")
    if (
        payment_method == "one_time"
        and artist.booth_rent_status == "paid"
        and artist.booth_rent_current_period_end
        and artist.booth_rent_current_period_end > datetime.now(UTC)
    ):
        raise HTTPException(status_code=409, detail="Booth rent is already paid for the current period")

    stripe_client = _init_stripe()
    if artist.booth_rent_checkout_session_id:
        try:
            existing = stripe_client.v1.checkout.sessions.retrieve(artist.booth_rent_checkout_session_id)
            existing_data = _stripe_dict(existing)
            if existing_data.get("status") == "open" and existing_data.get("url"):
                return {
                    "url": existing_data["url"],
                    "session_id": existing_data.get("id"),
                    "payment_method": payment_method,
                }
        except stripe.error.StripeError:
            pass

    next_due_date = artist.booth_rent_next_due_date or _next_month_first()
    period, period_end = _booth_rent_period(interval, next_due_date)
    installment_amount_cents = _booth_installment_amount(amount_cents, interval)
    metadata = {
        "bttco_billing_kind": "booth_rent",
        "bttco_user_id": str(artist.id),
        "artist_id": str(artist.id),
        "interval": interval,
        "monthly_obligation_cents": str(amount_cents),
        "installment_amount_cents": str(installment_amount_cents),
        "booth_rent_due_date": next_due_date.isoformat(),
        "booth_rent_payment_method": payment_method,
        "booth_rent_period": period,
        "booth_rent_period_end": str(int(period_end.timestamp())),
    }
    frontend_url = get_settings().FRONTEND_URL.rstrip("/")
    price_data = {
        "currency": "usd",
        "unit_amount": installment_amount_cents,
        "product_data": {
            "name": "Ryozan Studio booth rent",
            "metadata": {"bttco_billing_kind": "booth_rent"},
        },
    }
    if payment_method == "autodraft":
        price_data["recurring"] = (
            {"interval": "week", "interval_count": 2} if interval == "biweekly" else {"interval": "month"}
        )
    params = {
        "mode": "subscription" if payment_method == "autodraft" else "payment",
        "client_reference_id": str(artist.id),
        "line_items": [
            {
                "price_data": price_data,
                "quantity": 1,
            }
        ],
        "metadata": metadata,
        "success_url": f"{frontend_url}/dashboard/vault?booth_rent=success",
        "cancel_url": f"{frontend_url}/dashboard/vault?booth_rent=canceled",
    }
    if payment_method == "autodraft":
        params["subscription_data"] = {"metadata": metadata}
        if next_due_date > datetime.now(SHOP_TIMEZONE).date():
            params["subscription_data"].update(
                {
                    "billing_cycle_anchor": int(_due_datetime(next_due_date).timestamp()),
                    "proration_behavior": "none",
                }
            )
    else:
        params["payment_intent_data"] = {"metadata": metadata}
    if artist.stripe_account_id:
        _ensure_customer_configuration(artist.stripe_account_id)
        params["customer_account"] = artist.stripe_account_id
    elif artist.stripe_customer_id:
        params["customer"] = artist.stripe_customer_id
    else:
        params["customer_email"] = artist.email

    try:
        session = stripe_client.v1.checkout.sessions.create(params)
    except stripe.error.StripeError as exc:
        logger.error("Stripe booth-rent checkout failed for %s: %s", artist.id, exc)
        raise HTTPException(status_code=502, detail="Could not create booth-rent checkout") from exc
    if not session.url:
        raise HTTPException(status_code=502, detail="Stripe checkout did not return a redirect URL")

    artist.booth_rent_checkout_session_id = session.id
    artist.booth_rent_status = "checkout_pending"
    artist.updated_at = datetime.now(UTC)
    await db.flush()
    return {"url": session.url, "session_id": session.id, "payment_method": payment_method}


@router.post("/booth-rent/portal")
async def create_booth_rent_portal(
    current_user: User = Depends(get_current_user),
):
    """Artist: open Stripe-hosted payment method and cancellation management."""
    _require_artist(current_user)
    if not _stripe_configured():
        raise HTTPException(status_code=503, detail="Stripe Billing is not configured")
    if _booth_payment_method(current_user) != "autodraft":
        raise HTTPException(status_code=409, detail="Billing management is only available for automatic draft")
    if not current_user.stripe_account_id and not current_user.stripe_customer_id:
        raise HTTPException(status_code=409, detail="Start booth-rent payment before opening billing management")

    stripe_client = _init_stripe()
    try:
        portal_params = {
            "return_url": f"{get_settings().FRONTEND_URL.rstrip('/')}/dashboard/vault",
        }
        if current_user.stripe_account_id:
            portal_params["customer_account"] = current_user.stripe_account_id
        else:
            portal_params["customer"] = current_user.stripe_customer_id
        portal = stripe_client.v1.billing_portal.sessions.create(
            portal_params,
        )
    except stripe.error.StripeError as exc:
        logger.error("Stripe billing portal failed for %s: %s", current_user.id, exc)
        raise HTTPException(status_code=502, detail="Could not open Stripe billing management") from exc
    return {"url": portal.url}
