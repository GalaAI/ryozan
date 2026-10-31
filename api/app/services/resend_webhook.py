"""Svix signature check shared by the two Resend webhooks.

Resend signs each webhook endpoint with its own secret (Standard Webhooks; the
svix SDK is wire-compatible). Both /webhooks/resend (delivery events) and
/webhooks/email-inbound (received mail) call verify_resend_webhook before they
read the body, so an unsigned request never reaches the database or a provider.

A blank secret is accepted only in a local, development or test environment.
Everywhere else the webhook answers 503 until its secret is set.
"""

import logging

from fastapi import HTTPException

logger = logging.getLogger("ryozan.resend_webhook")

LOCAL_ENVIRONMENTS = frozenset({"development", "dev", "local", "test"})


def is_local_environment(settings) -> bool:
    return bool(settings.DEBUG) or (settings.APP_ENV or "").strip().lower() in LOCAL_ENVIRONMENTS


def verify_resend_webhook(body: bytes, headers, secret: str, settings, *, label: str) -> None:
    """Raise unless the request carries a valid Svix signature for `secret`.

    `label` names the webhook in the 503 detail and the logs.
    """
    if not (secret or "").strip():
        if is_local_environment(settings):
            logger.warning("%s: no signing secret set; accepting an unsigned webhook in local/dev/test", label)
            return
        logger.error("%s: no signing secret set; rejecting the webhook outside local/dev/test", label)
        raise HTTPException(status_code=503, detail=f"{label} signing secret not configured")

    svix_id = headers.get("svix-id", "")
    svix_ts = headers.get("svix-timestamp", "")
    svix_sig = headers.get("svix-signature", "")
    if not all([svix_id, svix_ts, svix_sig]):
        raise HTTPException(status_code=400, detail="Missing Svix signature headers")
    try:
        from svix.webhooks import Webhook

        Webhook(secret).verify(
            body,
            {
                "svix-id": svix_id,
                "svix-timestamp": svix_ts,
                "svix-signature": svix_sig,
            },
        )
    except Exception as exc:
        logger.warning("%s: signature verification failed: %s", label, exc)
        raise HTTPException(status_code=401, detail="Invalid webhook signature") from exc
