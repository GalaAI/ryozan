"""WhatsApp Cloud API transport helpers for Courier.

Net-new because the existing WhatsApp endpoint only archives inbound Meta
webhooks. Courier needs outbound transport behind the same provider boundary,
but the endpoint should remain focused on HTTP webhook handling.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.sms import normalise_phone

logger = logging.getLogger("ryozan.whatsapp")


class WhatsAppSendError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


async def send_whatsapp_text(*, to: str, body: str) -> dict[str, Any]:
    """Send a WhatsApp free-form text message through Meta Cloud API."""

    settings = get_settings()
    phone_number_id = settings.META_WHATSAPP_PHONE_NUMBER_ID
    token = settings.META_WHATSAPP_ACCESS_TOKEN
    if not phone_number_id or not token:
        raise WhatsAppSendError("CONFIG", "Meta WhatsApp phone number id or access token is not configured")

    e164 = normalise_phone(to)
    if not e164:
        raise WhatsAppSendError("INVALID_PHONE", f"Could not normalise {to!r} to E.164")

    recipient = e164.lstrip("+")
    url = f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 300:
        try:
            error = response.json().get("error") or {}
            raise WhatsAppSendError(str(error.get("code", response.status_code)), error.get("message", response.text))
        except ValueError:
            raise WhatsAppSendError(str(response.status_code), response.text[:300]) from None

    result = response.json()
    logger.info("whatsapp.send.ok to=%s message_id=%s", e164, ((result.get("messages") or [{}])[0]).get("id"))
    return result
