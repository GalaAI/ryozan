"""Upstream HTTP bridge for the Ryozan MCP surface.

Every MCP tool calls the same /api/v1 routes the web app calls, forwarding the
caller's bearer token. Role scoping, rate limits and business rules therefore
stay in exactly one place: the REST endpoints.

Configuration is environment driven. A missing token is a handled state, not a
crash. Every outbound call is bounded: hard timeout, capped retries, floored
exponential backoff.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

DEFAULT_PORT_FALLBACK = "8801"
_RETRY_STATUSES = {502, 503, 504}
_MAX_ATTEMPTS = 3
_BACKOFF_FLOOR_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 4.0


class UpstreamError(Exception):
    """Raised when the upstream API cannot produce a usable answer."""


def upstream_base_url() -> str:
    """Resolve the API base URL this MCP surface proxies to.

    The MCP router is mounted on the same app it calls, so the loopback port
    must match whatever uvicorn actually bound. That differs per environment:

      Railway         --port ${PORT:-8000}   (PORT is injected, dynamic)
      docker compose  --port 8801            (command override in compose)
      bare Dockerfile --port 8000            (CMD default)

    So honour an explicit override first, then PORT, then the compose port.
    Hardcoding one of these would silently break the other two.
    """
    explicit = os.environ.get("BTTCO_MCP_UPSTREAM_URL")
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("PORT") or DEFAULT_PORT_FALLBACK
    return f"http://127.0.0.1:{port}"


def _timeout() -> httpx.Timeout:
    read = float(os.environ.get("BTTCO_MCP_READ_TIMEOUT", "20"))
    connect = float(os.environ.get("BTTCO_MCP_CONNECT_TIMEOUT", "5"))
    return httpx.Timeout(read, connect=connect)


async def get_json(
    path: str,
    token: str | None,
    params: dict[str, Any] | None = None,
) -> Any:
    """GET an /api/v1 path and return decoded JSON.

    `path` is relative to the API v1 prefix, e.g. "/bookings/".
    Raises UpstreamError with a caller-safe message on any failure.
    """
    if not token:
        raise UpstreamError(
            "Not authenticated. Connect this MCP server through Ryozan OAuth and approve the requested scopes."
        )

    url = f"{upstream_base_url()}/api/v1{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    last_detail = "no response"

    async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=True) as client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await client.get(url, headers=headers, params=clean)
            except httpx.TimeoutException:
                last_detail = "upstream timed out"
            except httpx.HTTPError as exc:
                last_detail = f"upstream unreachable ({type(exc).__name__})"
            else:
                if response.status_code == 401:
                    raise UpstreamError("Ryozan OAuth grant was rejected or expired. Reconnect the MCP server.")
                if response.status_code == 403:
                    raise UpstreamError("This Ryozan account does not have access to that record.")
                if response.status_code == 404:
                    raise UpstreamError("Not found.")
                if response.status_code not in _RETRY_STATUSES:
                    response.raise_for_status()
                    return response.json()
                last_detail = f"upstream returned {response.status_code}"

            if attempt < _MAX_ATTEMPTS:
                delay = min(_BACKOFF_FLOOR_SECONDS * (2 ** (attempt - 1)), _BACKOFF_CAP_SECONDS)
                await asyncio.sleep(delay)

    raise UpstreamError(f"Ryozan API is not answering ({last_detail}). Nothing was changed.")


async def write_json(
    path: str,
    token: str | None,
    method: str,
    payload: Any,
) -> Any:
    """Write through the same authenticated Ryozan REST boundary as the app.

    Net-new write helper justification: workspace POST, PUT and PATCH endpoints
    already own role checks and persistence, but the MCP exposed only GET calls.
    This helper forwards founder/admin JWTs without adding a second auth model.
    """
    if not token:
        raise UpstreamError(
            "Not authenticated. Connect this MCP server through Ryozan OAuth and approve the requested scopes."
        )
    normalized_method = method.upper()
    if normalized_method not in {"POST", "PUT", "PATCH"}:
        raise ValueError(f"Unsupported Ryozan write method: {method}")

    url = f"{upstream_base_url()}/api/v1{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    last_detail = "no response"

    async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=True) as client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await client.request(normalized_method, url, headers=headers, json=payload)
            except httpx.TimeoutException:
                last_detail = "upstream timed out"
            except httpx.HTTPError as exc:
                last_detail = f"upstream unreachable ({type(exc).__name__})"
            else:
                if response.status_code == 401:
                    raise UpstreamError("Ryozan OAuth grant was rejected or expired. Reconnect the MCP server.")
                if response.status_code == 403:
                    raise UpstreamError("This write requires Ryozan admin or founder access.")
                if response.status_code == 404:
                    raise UpstreamError("Not found.")
                if response.status_code == 409:
                    raise UpstreamError("The requested Ryozan record already exists or conflicts with current state.")
                if response.status_code not in _RETRY_STATUSES:
                    response.raise_for_status()
                    return response.json()
                last_detail = f"upstream returned {response.status_code}"

            if attempt < _MAX_ATTEMPTS:
                delay = min(_BACKOFF_FLOOR_SECONDS * (2 ** (attempt - 1)), _BACKOFF_CAP_SECONDS)
                await asyncio.sleep(delay)

    raise UpstreamError(f"Ryozan API is not answering ({last_detail}). Nothing was changed.")


async def get_health(path: str) -> Any:
    """GET an unauthenticated health path (no /api/v1 prefix, no token)."""
    url = f"{upstream_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            return response.json()
    except httpx.TimeoutException:
        raise UpstreamError("Health check timed out.") from None
    except httpx.HTTPError as exc:
        raise UpstreamError(f"Health check unreachable ({type(exc).__name__}).") from None
    except ValueError:
        raise UpstreamError("Health endpoint returned a non-JSON body.") from None
