"""
Tier-aware AI routing and membership helpers.

Centralizes entitlement resolution, freemium caps, and model fallback so billing
policy and runtime model choice stay aligned.

All model calls go through one OpenAI-compatible /v1 client. The default target
is Nebius Token Factory serving NVIDIA Nemotron (nvidia/* model ids); an Ark
Model Gateway with ark/* slugs is an optional override configured in settings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.api_usage import APIUsage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.models.user import User

STAFF_ROLES = {"assistant", "artist", "admin", "founder"}

# Nemotron 3 models stream reasoning_content before the answer. A small token
# budget can be spent entirely on reasoning and end with finish_reason=length and
# empty content (observed with Nano at 40 tokens), so every Nemotron call gets at
# least this many completion tokens. The empty-content guard below still applies.
NEMOTRON_MIN_MAX_TOKENS = 1024


def _is_nemotron(model_name: str) -> bool:
    return "nemotron" in (model_name or "").lower()


def _is_token_factory_model(model_name: str) -> bool:
    return (model_name or "").startswith("nvidia/")


ACTIVE_STATUSES = {"active", "trialing"}


def ai_access_allowed(_settings: Settings, user: User | None) -> bool:
    """Allow model calls only for a real authenticated Ryozan account.

    Endpoint authorization, agent ``allowed_roles``, tool scopes, and the
    membership lane remain the owners of what that account may do. Background
    or client-facing automation has no authenticated caller and stays closed.
    """
    return bool(user and getattr(user, "role", None))


def require_ai_access(settings: Settings, user: User | None) -> None:
    """Require an authenticated account before any user-initiated model call."""
    if not ai_access_allowed(settings, user):
        raise HTTPException(
            status_code=403,
            detail="An authenticated Ryozan account is required for AI access.",
        )
    if demo_account_expired(settings, user):
        raise HTTPException(status_code=403, detail="This demo account's AI access has ended.")


def demo_account_expired(settings: Settings, user: User | None) -> bool:
    """An instant sign-up account past OPEN_SIGNUP_UNTIL (UTC, inclusive) has no AI access."""
    if getattr(user, "password_change_method", None) != "signup":
        return False
    until = (settings.OPEN_SIGNUP_UNTIL or "").strip()
    if not until:
        return False
    try:
        return datetime.now(UTC).date() > date.fromisoformat(until)
    except ValueError:
        # A malformed date fails closed, as it does for sign-up itself.
        return True


@dataclass
class AILaneDecision:
    configured_tier: str
    effective_tier: str
    membership_status: str
    primary_model: str
    fallback_models: list[str]
    ai_monthly_message_cap: int | None
    ai_messages_used: int
    ai_messages_remaining: int | None
    early_access_galax3: bool
    connected_apps_unlocked: bool
    product_entitlements: dict


def _default_entitlements(tier: str) -> dict:
    if tier == "staff":
        return {
            "courier": "advanced",
            "guild": "advanced",
            "huddle": "advanced",
            "hustle": "advanced",
            "nexus": "advanced",
            "axiom": "advanced",
        }
    if tier == "premium":
        return {
            "courier": "advanced",
            "guild": "advanced",
            "huddle": "advanced",
            "hustle": "advanced",
            "nexus": "advanced",
            "axiom": "preview",
        }
    if tier in {"business", "pro"}:
        return {
            "courier": "standard",
            "guild": "standard",
            "huddle": "standard",
            "hustle": "standard",
            "nexus": "standard",
            "axiom": "waitlist",
        }
    return {
        "courier": "lite",
        "guild": "lite",
        "huddle": "lite",
        "hustle": "lite",
        "nexus": "lite",
        "axiom": "locked",
    }


def _normalize_tier(user: User) -> str:
    if (user.role or "") in STAFF_ROLES:
        return "staff"
    raw = (user.membership_tier or "freemium").strip().lower()
    if raw in {"freemium", "pro", "premium", "business"}:
        return raw
    return "freemium"


async def _monthly_ai_messages_used(db: AsyncSession, user_id) -> int:
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count(APIUsage.id)).where(
            APIUsage.user_id == user_id,
            APIUsage.created_at >= month_start,
            APIUsage.endpoint.like("gala/chat:%"),
        )
    )
    return int(result.scalar_one() or 0)


async def resolve_ai_lane(
    user: User,
    db: AsyncSession,
    settings: Settings,
    *,
    enforce_limits: bool = True,
) -> AILaneDecision:
    configured_tier = _normalize_tier(user)
    membership_status = (user.membership_status or "active").strip().lower()
    effective_tier = configured_tier

    if configured_tier not in {"freemium", "staff"} and membership_status not in ACTIVE_STATUSES:
        effective_tier = "freemium"

    if effective_tier == "staff":
        primary_model = settings.AI_PREMIUM_PRIMARY_MODEL
        fallback_models = [settings.AI_PREMIUM_FALLBACK_MODEL, settings.AI_PAID_FALLBACK_MODEL]
        cap = None
    elif effective_tier == "premium":
        primary_model = settings.AI_PREMIUM_PRIMARY_MODEL
        fallback_models = [settings.AI_PREMIUM_FALLBACK_MODEL, settings.AI_PAID_FALLBACK_MODEL]
        cap = user.ai_monthly_message_cap or 1500
    elif effective_tier in {"business", "pro"}:
        primary_model = settings.AI_PAID_PRIMARY_MODEL
        fallback_models = [settings.AI_PAID_FALLBACK_MODEL]
        cap = user.ai_monthly_message_cap or 400
    else:
        primary_model = settings.AI_FREEMIUM_PRIMARY_MODEL
        fallback_models = [settings.AI_FREEMIUM_FALLBACK_MODEL]
        cap = user.ai_monthly_message_cap or 40

    fallback_models = [model for model in fallback_models if model and model != primary_model]
    used = await _monthly_ai_messages_used(db, user.id)
    remaining = None if cap is None else max(cap - used, 0)

    if enforce_limits and cap is not None and used >= cap:
        raise HTTPException(
            status_code=402,
            detail=(
                "This AI tier has reached its monthly cap. Upgrade to Pro or Premium to keep using "
                "Smoku and Gala without interruption."
            ),
        )

    product_entitlements = {
        **_default_entitlements(effective_tier),
        **(user.product_entitlements or {}),
    }

    return AILaneDecision(
        configured_tier=configured_tier,
        effective_tier=effective_tier,
        membership_status=membership_status,
        primary_model=primary_model,
        fallback_models=fallback_models,
        ai_monthly_message_cap=cap,
        ai_messages_used=used,
        ai_messages_remaining=remaining,
        early_access_galax3=bool(user.early_access_galax3 or effective_tier in {"premium", "staff"}),
        connected_apps_unlocked=bool(
            user.connected_apps_unlocked or effective_tier in {"business", "premium", "staff"}
        ),
        product_entitlements=product_entitlements,
    )


def agent_model_lane(lane: AILaneDecision, agent_model: str | None) -> AILaneDecision:
    """Put the agent's own Nemotron model first when the lane is on Token Factory.

    The tier lane stays the fallback chain. On an Ark gateway (ark/* slugs) the
    agent's nvidia/* id would not resolve, so the lane is returned unchanged.
    """
    if not agent_model or not _is_token_factory_model(lane.primary_model):
        return lane
    chain = [lane.primary_model, *lane.fallback_models]
    fallbacks = list(dict.fromkeys(model for model in chain if model and model != agent_model))
    return replace(lane, primary_model=agent_model, fallback_models=fallbacks)


async def call_ark_gateway_chat_completion(
    *,
    settings: Settings,
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    primary_model: str,
    fallback_models: list[str],
    max_tokens: int = 1024,
) -> dict:
    """Shared model client. POSTs an OpenAI chat completion to the configured
    OpenAI-compatible endpoint (Nebius Token Factory by default), walking
    primary -> fallbacks until one returns a usable turn. Raises
    HTTPException(502) if every candidate fails.
    """
    models = [primary_model, *fallback_models]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.ARK_MODEL_GATEWAY_KEY}",
    }
    base_url = settings.ARK_MODEL_GATEWAY_BASE_URL.rstrip("/")
    last_error = "Unknown model provider error"

    async with httpx.AsyncClient(timeout=60.0) as client:
        for model_name in models:
            payload: dict = {
                "model": model_name,
                "messages": [{"role": "system", "content": system_prompt}, *messages],
                "max_tokens": max(max_tokens, NEMOTRON_MIN_MAX_TOKENS) if _is_nemotron(model_name) else max_tokens,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            try:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            except httpx.HTTPError as exc:
                last_error = f"{model_name}: {exc}"
                continue

            if response.is_success:
                data = response.json()
                finish_reason = (data.get("choices") or [{}])[0].get("finish_reason") or "unknown"
                choice = ((data.get("choices") or [{}])[0]).get("message") or {}
                content = (choice.get("content") or "").strip()
                tool_calls = choice.get("tool_calls") or []
                # A tool-call turn legitimately carries empty content; accept it so the
                # tool-calling consumers (gala_chat) can execute the requested tools.
                if tool_calls or (content and finish_reason != "length"):
                    return data
                if content:
                    last_error = f"{model_name}: incomplete assistant content (finish_reason=length)"
                else:
                    last_error = f"{model_name}: empty assistant content (finish_reason={finish_reason})"
                continue

            detail = response.text[:300]
            last_error = f"{model_name}: {response.status_code} {detail}"
            # 401 means a bad/absent gateway key: the same key is used for every
            # model, so retrying down the fallback chain just repeats the auth
            # failure. Fail fast. 403 can vary by per-model policy, so it stays
            # retryable.
            if response.status_code not in {400, 403, 404, 422, 429, 500, 502, 503, 504}:
                break

    raise HTTPException(status_code=502, detail=f"Model routing failed. {last_error}")


async def call_ark_gateway_text(
    *,
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    primary_model: str,
    fallback_models: list[str] | None = None,
    max_tokens: int = 512,
) -> tuple[str, dict]:
    """One-shot text generation through the gateway for utility endpoints
    (templates, extraction, digests, classification).

    Returns (text, usage) where usage is the OpenAI-style token dict (may be
    empty). Raises HTTPException(502) if the gateway is unreachable or returns no
    usable text.
    """
    data = await call_ark_gateway_chat_completion(
        settings=settings,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[],
        primary_model=primary_model,
        fallback_models=fallback_models or [],
        max_tokens=max_tokens,
    )
    choice = ((data.get("choices") or [{}])[0]).get("message") or {}
    text = (choice.get("content") or "").strip()
    usage = data.get("usage") or {}
    return text, usage
