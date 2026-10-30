"""Pinterest API v5 client service.

Shared by every Pinterest feature (flash auto-pin, portfolio boards, board
ingest into References, analytics, collector connect). One place that knows how
to talk to https://api.pinterest.com/v5 with a Bearer token.

All calls take an explicit access_token so the same client serves both the shop
account (founder token) and collector-connected accounts. Trial access has
reduced rate limits; callers should avoid tight loops and cache reads.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("bttco.pinterest.service")


class PinterestError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"Pinterest API {status}: {detail}")


def _base() -> str:
    s = get_settings()
    base = s.PINTEREST_SANDBOX_API_BASE if s.PINTEREST_USE_SANDBOX else s.PINTEREST_API_BASE
    return base.rstrip("/")


async def _request(method: str, path: str, token: str, **kwargs) -> Any:
    url = f"{_base()}{path}"
    headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(method, url, headers=headers, **kwargs)
    if resp.status_code >= 400:
        logger.warning("Pinterest %s %s -> %s %s", method, path, resp.status_code, resp.text[:200])
        raise PinterestError(resp.status_code, resp.text[:300])
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


# ── Account ──


async def get_user_account(token: str) -> dict:
    return await _request("GET", "/user_account", token)


# ── Boards ──


async def list_boards(token: str, page_size: int = 25, bookmark: str | None = None) -> dict:
    params = {"page_size": page_size}
    if bookmark:
        params["bookmark"] = bookmark
    return await _request("GET", "/boards", token, params=params)


async def create_board(token: str, name: str, description: str = "", privacy: str = "PUBLIC") -> dict:
    """Create a board. privacy: PUBLIC | PROTECTED | SECRET."""
    body = {"name": name[:180], "description": description[:500], "privacy": privacy}
    return await _request("POST", "/boards", token, json=body)


async def list_board_pins(token: str, board_id: str, page_size: int = 25, bookmark: str | None = None) -> dict:
    params = {"page_size": page_size}
    if bookmark:
        params["bookmark"] = bookmark
    return await _request("GET", f"/boards/{board_id}/pins", token, params=params)


# ── Pins ──


async def create_pin(
    token: str,
    *,
    board_id: str,
    image_url: str,
    title: str | None = None,
    description: str | None = None,
    link: str | None = None,
    alt_text: str | None = None,
) -> dict:
    """Create a Pin from a hosted image URL on the given board."""
    body: dict[str, Any] = {
        "board_id": board_id,
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    if title:
        body["title"] = title[:100]
    if description:
        body["description"] = description[:800]
    if link:
        body["link"] = link
    if alt_text:
        body["alt_text"] = alt_text[:500]
    return await _request("POST", "/pins", token, json=body)


async def get_pin(token: str, pin_id: str) -> dict:
    return await _request("GET", f"/pins/{pin_id}", token)


# ── Analytics ──

# Verified against Pinterest OpenAPI v5.23.0 (api-description repo).
# user_account/analytics: metric_types optional (default all). YYYY-MM-DD, <=90 day range.
# pins/{pin_id}/analytics: metric_types REQUIRED. start_date/end_date required.
ANALYTICS_METRICS = [
    "ENGAGEMENT",
    "ENGAGEMENT_RATE",
    "IMPRESSION",
    "OUTBOUND_CLICK",
    "OUTBOUND_CLICK_RATE",
    "PIN_CLICK",
    "PIN_CLICK_RATE",
    "SAVE",
    "SAVE_RATE",
]


async def get_user_analytics(
    token: str,
    *,
    start_date: str,
    end_date: str,
    metric_types: list[str] | None = None,
) -> dict:
    """User account analytics. Dates YYYY-MM-DD (UTC), <=90 days back, range <=90 days.
    metric_types optional (default all). Needs user_accounts:read."""
    params: dict[str, Any] = {"start_date": start_date, "end_date": end_date}
    if metric_types:
        params["metric_types"] = ",".join(metric_types)
    return await _request("GET", "/user_account/analytics", token, params=params)


async def get_pin_analytics(
    token: str,
    pin_id: str,
    *,
    start_date: str,
    end_date: str,
    metric_types: list[str] | None = None,
) -> dict:
    """Pin analytics. Dates YYYY-MM-DD. metric_types is REQUIRED per the spec, so
    defaults to the full set when not supplied."""
    params: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "metric_types": ",".join(metric_types or ANALYTICS_METRICS),
    }
    return await _request("GET", f"/pins/{pin_id}/analytics", token, params=params)
