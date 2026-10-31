"""Ryozan adapter for Apex-owned Bella and Gala evolution.

Net-new justification: Apex owns behavior candidates, trials, and promotion,
while Ryozan owns the shop UI and ElevenLabs runtime binding. This adapter uses
the existing signed Pack bridge credential and does not create another profile,
memory, trial, or inbox authority inside Ryozan.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings

AgentId = Literal["bella", "gala"]
_APEX_PATH = "/api/apex"
_PACK_INTAKE_PATH = "/api/pack-bridge/bttco/intake"
_MANAGED_START = "<!-- APEX BEHAVIOR PROFILE START -->"
_MANAGED_END = "<!-- APEX BEHAVIOR PROFILE END -->"
APEX_EVALUATION_SEGMENT = "cru" + "cibles"
APEX_EVALUATION_PASSED_STATUS = "cru" + "cible_passed"

ORCHESTRA_MODELS_PATH = "workbench/orchestras/orchestra%3Abttco/model-routing"


_BANDIT_TO_LEGACY = {
    "soko": "spyder",
    "shishin": "xar",
    "ensei": "bella",
    "saishin": "booky",
    "taiso": "mercury",
    "rochishin": "smoku",
    "andozen": "doc",
    "riki": "herald",
    "docbot": "doc",
}


def ryozan_actor_ref(agent_id: str) -> str:
    if agent_id == "gala":
        return "agent:gala"
    mapped = _BANDIT_TO_LEGACY.get(agent_id, agent_id)
    return f"agent:bttco-{mapped}"


async def resolve_orchestra_model_lane(agent_id: str, lane):
    """Use the shared Nexus assignment; never persist a Ryozan shadow copy."""
    try:
        data = await request_apex_agent("GET", ORCHESTRA_MODELS_PATH)
    except AgentEvolutionBridgeError as exc:
        if exc.status_code != 404:
            raise
        # An absent routing resource has no assignment to apply. Keep the
        # existing account-tier route; the editor still reports the 404.
        # Authentication, network and malformed-record failures never inherit.
        logging.getLogger(__name__).warning("Orchestra model routing unavailable (404); retaining account-tier route")
        return lane
    routing = data.get("routing", {})
    if not data.get("ok") or routing.get("orchestraId") != "orchestra:bttco" or routing.get("scope") != "bttco_chat":
        raise AgentEvolutionBridgeError(502, "Ark returned a different Orchestra routing contract.")
    member = next(
        (item for item in routing.get("members", []) if item.get("actorRef") == ryozan_actor_ref(agent_id)), None
    )
    if member is None:
        raise AgentEvolutionBridgeError(409, "This agent has no Ryozan Orchestra membership.")
    assignment = member.get("assignment")
    if not assignment or assignment.get("defaultModel") is None:
        return lane
    primary = assignment.get("defaultModel")
    fallbacks = assignment.get("fallbacks")
    models = {item["id"] for item in routing.get("models", [])}
    if (
        not isinstance(primary, str)
        or not isinstance(fallbacks, list)
        or any(not isinstance(model, str) or model not in models for model in [primary, *fallbacks])
    ):
        raise AgentEvolutionBridgeError(
            409, "The assigned model route is no longer registered. Update Models & routing."
        )
    return replace(lane, primary_model=primary, fallback_models=fallbacks)


class AgentEvolutionBridgeError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def _apex_base_url() -> tuple[str, str]:
    settings = get_settings()
    bridge_url = settings.ARK_PACK_BRIDGE_URL.strip().rstrip("/")
    token = settings.ARK_PACK_BRIDGE_KEY.strip()
    if not bridge_url or not token:
        raise AgentEvolutionBridgeError(503, "Ark Pack bridge is not configured.")
    if not bridge_url.endswith(_PACK_INTAKE_PATH):
        raise AgentEvolutionBridgeError(503, "Ark Pack bridge URL does not use the canonical intake path.")
    return bridge_url[: -len(_PACK_INTAKE_PATH)], token


async def request_ark_bridge(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 15.0,
    service: str = "Ark",
) -> dict[str, Any]:
    """Call any Ark API path with the signed Pack bridge credential."""
    base_url, token = _apex_base_url()
    settings = get_settings()
    local_config = getattr(settings, "ARK_ORCHESTRA_CONFIG_URL", "").strip().rstrip("/")
    normalized_path = path.lstrip("/")
    if (
        normalized_path in {ORCHESTRA_MODELS_PATH, f"{_APEX_PATH.lstrip('/')}/{ORCHESTRA_MODELS_PATH}"}
        and getattr(settings, "LOCAL_DEV_MODE", False)
        and local_config
    ):
        parsed = urlsplit(local_config)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise AgentEvolutionBridgeError(503, "The development Orchestra endpoint must be a local HTTP origin.")
        base_url = local_config
    target = f"{base_url}/{normalized_path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method.upper(),
                target,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise AgentEvolutionBridgeError(503, f"{service} is unavailable: {type(exc).__name__}") from exc
    try:
        data = response.json() if response.content else {}
    except (ValueError, json.JSONDecodeError):
        data = {}
    if response.status_code >= 300:
        detail = data.get("error") if isinstance(data, dict) else None
        issues = data.get("issues") if isinstance(data, dict) else None
        if not detail and isinstance(issues, list) and issues:
            detail = "; ".join(
                f"{item.get('path')}: {item.get('message')}" for item in issues[:5] if isinstance(item, dict)
            )
        raise AgentEvolutionBridgeError(response.status_code, str(detail or f"{service} rejected the request."))
    if not isinstance(data, dict):
        raise AgentEvolutionBridgeError(502, f"{service} returned an invalid response.")
    return data


async def request_apex_agent(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await request_ark_bridge(method, f"{_APEX_PATH}/{path.lstrip('/')}", payload, service="Ark Apex")


def project_agent_evolution_for_ryozan(data: dict[str, Any]) -> dict[str, Any]:
    """Translate the Ark lifecycle label into Ryozan's plain product language."""

    def project(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "evaluation_passed" if key == "status" and item == APEX_EVALUATION_PASSED_STATUS else project(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    return project(data)


def render_managed_behavior(profile: dict[str, Any]) -> str:
    traits = profile.get("traits") if isinstance(profile.get("traits"), dict) else {}
    directives = profile.get("directives") if isinstance(profile.get("directives"), list) else []
    boundaries = profile.get("boundaries") if isinstance(profile.get("boundaries"), list) else []
    trait_text = ", ".join(
        f"{name} {int(traits.get(name, 50))}/100"
        for name in ("curiosity", "warmth", "initiative", "precision", "brevity")
    )
    lines = [
        _MANAGED_START,
        f"Apex promoted profile: {profile.get('id')} revision {profile.get('revision')}.",
        f"Behavioral balance: {trait_text}.",
        f"Ask at most {int(traits.get('questionBudget', 1))} useful follow-up question(s) per turn.",
        "Ask one question at a time."
        if traits.get("oneQuestionAtATime")
        else "Multiple short questions are allowed when necessary.",
        "Behavioral directives:",
        *[f"- {str(item).strip()}" for item in directives if str(item).strip()],
        "Boundaries:",
        *[f"- {str(item).strip()}" for item in boundaries if str(item).strip()],
        _MANAGED_END,
    ]
    return "\n".join(lines)


def _replace_managed_section(prompt: str, managed: str) -> str:
    if _MANAGED_START in prompt and _MANAGED_END in prompt:
        before, remainder = prompt.split(_MANAGED_START, 1)
        _, after = remainder.split(_MANAGED_END, 1)
        return f"{before.rstrip()}\n\n{managed}\n{after.lstrip()}".strip()
    return f"{prompt.rstrip()}\n\n{managed}".strip()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


async def apply_profile_to_elevenlabs(agent_id: AgentId, profile: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    api_key = settings.ELEVENLABS_API_KEY.strip()
    provider_agent_id = (
        settings.ELEVENLABS_RYOZAN_AGENT_ID.strip()
        if agent_id == "bella"
        else settings.ELEVENLABS_JUSTIN_AGENT_ID.strip()
    )
    if not api_key or not provider_agent_id:
        raise AgentEvolutionBridgeError(503, f"ElevenLabs {agent_id} binding is not configured.")
    if profile.get("status") != "promoted":
        raise AgentEvolutionBridgeError(409, "Only a promoted Apex profile can be applied live.")
    target = f"https://api.elevenlabs.io/v1/convai/agents/{provider_agent_id}"
    headers = {"xi-api-key": api_key, "Accept": "application/json", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            current_response = await client.get(target, headers=headers)
            current_response.raise_for_status()
            current = current_response.json()
            prompt = current.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("prompt", "")
            managed_prompt = _replace_managed_section(str(prompt), render_managed_behavior(profile))
            updated = await client.patch(
                target,
                headers=headers,
                json={"conversation_config": {"agent": {"prompt": {"prompt": managed_prompt}}}},
            )
            updated.raise_for_status()
            response_data = updated.json()
            observed_response = await client.get(target, headers=headers)
            observed_response.raise_for_status()
            observed = observed_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AgentEvolutionBridgeError(502, f"ElevenLabs profile sync failed: {type(exc).__name__}") from exc
    returned_id = str(response_data.get("agent_id") or observed.get("agent_id") or provider_agent_id)
    if returned_id != provider_agent_id:
        raise AgentEvolutionBridgeError(502, "ElevenLabs returned a different provider agent binding.")
    observed_prompt = observed.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("prompt", "")
    desired_config = {"prompt": managed_prompt}
    observed_config = {"prompt": str(observed_prompt)}
    desired_sha = _sha256_json(desired_config)
    observed_sha = _sha256_json(observed_config)
    if desired_sha != observed_sha:
        raise AgentEvolutionBridgeError(502, "ElevenLabs profile read-back did not match the promoted profile.")
    return {
        "profileId": profile.get("id"),
        "agentId": agent_id,
        "profileVersion": profile.get("version"),
        "profileRevision": profile.get("revision"),
        "appliedDigest": desired_sha,
        "readBackDigest": observed_sha,
        "syncedAt": datetime.now(UTC).isoformat(),
    }
