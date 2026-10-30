"""Shared Stripe SDK client and request-option helpers.

This module is intentionally net-new because the repository previously set the
process-global ``stripe.api_key`` independently in each endpoint. A single
``StripeClient`` owner is required for Accounts v2 and direct-charge request
scoping, and keeps platform and connected-account calls auditable in one place.
"""

from typing import Any

from stripe import StripeClient

from app.core.config import get_settings


def get_stripe_client() -> StripeClient:
    """Return a configured Stripe client or fail with an actionable error."""
    api_key = get_settings().STRIPE_SECRET_KEY.strip()
    if not api_key:
        raise RuntimeError("STRIPE_SECRET_KEY is required for Stripe operations")
    return StripeClient(api_key)


def stripe_request_options(
    *,
    account_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any] | None:
    """Build Stripe SDK request options without mixing them into API params."""
    options: dict[str, Any] = {}
    if account_id:
        options["stripe_account"] = account_id
    if idempotency_key:
        options["idempotency_key"] = idempotency_key
    return options or None
