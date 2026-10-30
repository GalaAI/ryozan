"""
Integration options -- which third-party connections this install offers.

- GET /integrations/options -- per integration: configured on this server, and
  whether the caller's role may start the connect flow (null when anonymous).

Only booleans leave this module; no client id, key or secret value is returned.

The role sets below are the single source for who may start each connect flow.
Endpoints that enforce a role list import it from here (stripe_connect); the
rest accept any signed-in user through get_current_user, which ALL_SIGNED_IN
mirrors so the UI never offers a button the API then refuses.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.v1.endpoints.quests import outbound_email_configured
from app.core.config import get_settings
from app.core.security import ROLE_HIERARCHY, get_optional_current_user
from app.services.calendar_sync import workspace_delegation_enabled

router = APIRouter(prefix="/integrations", tags=["integrations"])

ALL_SIGNED_IN = frozenset(ROLE_HIERARCHY)

# GET /auth/google/connect: any signed-in user.
GOOGLE_CALENDAR_ROLES = ALL_SIGNED_IN
# GET /pinterest/connect: any signed-in user (collectors build reference boards).
PINTEREST_ROLES = ALL_SIGNED_IN
# POST /plaid/link-token: any signed-in user.
PLAID_ROLES = ALL_SIGNED_IN
# POST /stripe/connect/onboard: artist-capable accounts only.
STRIPE_CONNECT_ROLES = frozenset({"artist", "founder"})
# POST /push/subscribe: any signed-in user.
PUSH_ROLES = ALL_SIGNED_IN
# POST /email/connect: any signed-in user.
EMAIL_FORWARDING_ROLES = ALL_SIGNED_IN


def _set(*values: str) -> bool:
    return all((value or "").strip() for value in values)


def _google_calendar_configured() -> bool:
    settings = get_settings()
    # Service-account (workspace delegated) mode refuses per-user connect with 409.
    return _set(settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET) and not workspace_delegation_enabled()


def _pinterest_configured() -> bool:
    settings = get_settings()
    return _set(settings.PINTEREST_CLIENT_ID, settings.PINTEREST_CLIENT_SECRET)


def _plaid_configured() -> bool:
    settings = get_settings()
    return _set(settings.PLAID_CLIENT_ID, settings.PLAID_SECRET)


def _stripe_connect_configured() -> bool:
    # Deferred: stripe_connect imports STRIPE_CONNECT_ROLES from this module.
    from app.api.v1.endpoints.stripe_connect import _stripe_configured

    return _stripe_configured()


def _push_configured() -> bool:
    settings = get_settings()
    return _set(settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY)


INTEGRATIONS = (
    ("google_calendar", _google_calendar_configured, GOOGLE_CALENDAR_ROLES),
    ("pinterest", _pinterest_configured, PINTEREST_ROLES),
    ("plaid", _plaid_configured, PLAID_ROLES),
    ("stripe_connect", _stripe_connect_configured, STRIPE_CONNECT_ROLES),
    ("push", _push_configured, PUSH_ROLES),
    ("email_forwarding", outbound_email_configured, EMAIL_FORWARDING_ROLES),
)


class IntegrationOption(BaseModel):
    id: str
    configured: bool
    allowed: bool | None


class IntegrationOptionsResponse(BaseModel):
    integrations: list[IntegrationOption]


def integration_options(role: str | None) -> IntegrationOptionsResponse:
    return IntegrationOptionsResponse(
        integrations=[
            IntegrationOption(
                id=integration_id,
                configured=bool(configured()),
                allowed=None if role is None else role in roles,
            )
            for integration_id, configured, roles in INTEGRATIONS
        ]
    )


@router.get("/options", response_model=IntegrationOptionsResponse)
async def get_integration_options(current_user=Depends(get_optional_current_user)):
    """Which connections this server can offer, and which the caller may start."""
    return integration_options(getattr(current_user, "role", None) if current_user else None)
