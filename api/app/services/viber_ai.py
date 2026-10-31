"""Provider-agnostic Viber AI adapter.

Viber agents call this service instead of provider SDKs. This is net-new because
Viber accounts choose their own AI provider, while existing Ryozan Gala chat
resolves AI lanes from Ryozan user membership.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.models.viber import ViberAIProvider

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings

# Default Viber lane routes through the Ark Model Gateway using a gateway slug.
# Explicit BYOK / legacy LiteLLM account rows override this per provider record.
DEFAULT_VIBER_MODEL = "ark/bttco/gemini"


@dataclass(frozen=True)
class ViberChatResult:
    text: str
    model: str
    provider_type: str
    raw: dict


def _inline_secret(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    if secret_ref.startswith("inline:"):
        return secret_ref.removeprefix("inline:")
    return None


async def resolve_viber_ai_provider(
    db: AsyncSession,
    account_id: UUID,
) -> ViberAIProvider | None:
    result = await db.execute(
        select(ViberAIProvider)
        .where(ViberAIProvider.account_id == account_id)
        .order_by(ViberAIProvider.is_default.desc(), ViberAIProvider.created_at.asc())
    )
    return result.scalars().first()


def _provider_endpoint(provider: ViberAIProvider | None, settings: Settings) -> str:
    if provider and provider.provider_type == "byok" and provider.base_url:
        return provider.base_url.rstrip("/")
    if provider and provider.provider_type in {"litellm", "galax3"}:
        # Legacy account rows keep their explicit LiteLLM endpoint.
        base = provider.base_url or settings.LITELLM_API_BASE
        return base.rstrip("/")
    # Default lane: route through the Ark Model Gateway.
    return settings.ARK_MODEL_GATEWAY_BASE_URL.rstrip("/")


def _provider_key(provider: ViberAIProvider | None, settings: Settings) -> str:
    inline = _inline_secret(provider.key_secret_ref if provider else None)
    if inline:
        return inline
    if provider and provider.provider_type == "byok":
        raise HTTPException(status_code=503, detail="Viber BYOK provider has no reachable key")
    if provider and provider.provider_type in {"litellm", "galax3"}:
        # Legacy account rows authenticate against LiteLLM with the master key.
        return settings.LITELLM_MASTER_KEY
    # Default lane authenticates against the Ark Model Gateway.
    return settings.ARK_MODEL_GATEWAY_KEY


def _provider_model(provider: ViberAIProvider | None, model: str | None = None) -> str:
    return model or (provider.default_model if provider else None) or DEFAULT_VIBER_MODEL


async def viber_chat(
    *,
    db: AsyncSession,
    settings: Settings,
    account_id: UUID,
    system_prompt: str,
    messages: list[dict],
    model: str | None = None,
    max_tokens: int = 1200,
) -> ViberChatResult:
    provider = await resolve_viber_ai_provider(db, account_id)
    provider_type = provider.provider_type if provider else "litellm"
    endpoint = _provider_endpoint(provider, settings)
    selected_model = _provider_model(provider, model)
    api_key = _provider_key(provider, settings)

    payload = {
        "model": selected_model,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "max_tokens": max_tokens,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{endpoint}/chat/completions",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Viber AI provider unreachable: {exc.__class__.__name__}") from exc

    if not response.is_success:
        detail = response.text[:300]
        raise HTTPException(status_code=502, detail=f"Viber AI provider failed: {response.status_code} {detail}")

    raw = response.json()
    choice = ((raw.get("choices") or [{}])[0]).get("message") or {}
    return ViberChatResult(
        text=(choice.get("content") or "").strip(),
        model=selected_model,
        provider_type=provider_type,
        raw=raw,
    )
