"""Optional mirror of public general inquiries into the operator's ArkStation AInbox.

Net-new justification: `pack_bridge.py` already delivers typed Courier and
booking captures to Ark through a durable outbox keyed on
ARK_PACK_BRIDGE_URL/KEY. That lane is a learning capture stream with its own
payload contract. This module is the narrower, owner-requested mirror of one
general inquiry to a plain AInbox endpoint (ARK_AINBOX_MIRROR_URL): off by
default, one bounded POST, no outbox, and never a hard dependency. The Courier
thread in BTTCO stays the record; a mirror failure is logged and never
surfaced to the collector.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("bttco.inquiry_mirror")

INQUIRY_MIRROR_TIMEOUT_SECONDS = 5.0


async def mirror_general_inquiry(payload: dict[str, Any]) -> None:
    """POST one general inquiry to the AInbox mirror when it is configured.

    Returns immediately when ARK_AINBOX_MIRROR_URL is unset. Every failure,
    including a non-2xx response, is caught and logged at warning level.
    """
    settings = get_settings()
    url = (settings.ARK_AINBOX_MIRROR_URL or "").strip()
    if not url:
        return
    token = (settings.ARK_AINBOX_MIRROR_TOKEN or "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"source": "bttco", "kind": "general_inquiry", **payload}
    try:
        async with httpx.AsyncClient(timeout=INQUIRY_MIRROR_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body, headers=headers)
        if response.status_code >= 300:
            logger.warning(
                "AInbox mirror rejected inquiry %s: status=%s",
                payload.get("reference_code"),
                response.status_code,
            )
            return
        logger.info("AInbox mirror accepted inquiry %s", payload.get("reference_code"))
    except Exception as exc:
        logger.warning(
            "AInbox mirror failed for inquiry %s: %s",
            payload.get("reference_code"),
            type(exc).__name__,
        )
