"""
Google OAuth endpoints -- users connect their personal Google account
for calendar sync, profile info, etc.

Flow:
  1. GET  /auth/google/connect   -- returns the Google OAuth authorization URL
  2. GET  /auth/google/callback  -- handles the redirect from Google, stores refresh_token
  3. GET  /auth/google/status    -- check if current user has Google connected
  4. DELETE /auth/google/disconnect -- removes the stored refresh_token
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.user import User
from app.services.calendar_sync import (
    artist_calendar_connected,
    artist_calendar_id,
    check_calendar_connection,
    workspace_delegation_enabled,
)

logger = logging.getLogger("ryozan.google_auth")

router = APIRouter(prefix="/auth/google", tags=["google-auth"])

IDENTITY_SCOPES = [
    # Identity
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]
LEGACY_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
SCOPES = IDENTITY_SCOPES + LEGACY_CALENDAR_SCOPES


class GoogleConnectResponse(BaseModel):
    authorization_url: str


class GoogleStatusResponse(BaseModel):
    connected: bool
    managed: bool = False
    effective_user_id: str | None = None
    email: str | None = None
    source: str | None = None
    healthy: bool = False
    calendar_accessible: bool = False
    calendar_id: str | None = None
    calendar_summary: str | None = None
    reason: str | None = None
    reconnect_required: bool = False


def _build_flow() -> Flow:
    """Build a google_auth_oauthlib Flow from app settings."""
    settings = get_settings()
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth not configured (missing client ID or secret)",
        )

    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=IDENTITY_SCOPES if workspace_delegation_enabled() else SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )
    return flow


@router.get("/connect", response_model=GoogleConnectResponse)
async def google_connect(
    current_user: User = Depends(get_current_user),
):
    """
    Generate a Google OAuth authorization URL for the current user.
    The state parameter encodes the user_id so the callback can match the token
    back to the correct user record.
    """
    if workspace_delegation_enabled():
        raise HTTPException(
            status_code=409,
            detail="Google Calendar is managed by the Ryozan booking service",
        )

    flow = _build_flow()

    # Signed, short-lived state binds the callback to THIS user. A plaintext
    # user_id would be forgeable (OAuth CSRF: an attacker could attach a victim's
    # id to their own auth code). JWT-sign it with the app secret instead.
    from datetime import timedelta

    from app.core.security import create_access_token

    state = create_access_token(
        {"sub": str(current_user.id), "purpose": "google_oauth"},
        expires_delta=timedelta(minutes=15),
    )

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
        code_challenge=None,
        code_challenge_method=None,
    )

    return GoogleConnectResponse(authorization_url=authorization_url)


@router.get("/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle the OAuth redirect from Google.
    Exchanges the authorization code for tokens and stores the refresh_token
    on the user's record.
    """
    settings = get_settings()

    # Verify the signed state (issued by /connect). Rejects forged/expired state.
    from jose import JWTError, jwt

    try:
        claims = jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if claims.get("purpose") != "google_oauth":
            raise ValueError("wrong purpose")
        user_id = claims["sub"]
    except (JWTError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter") from exc

    # Look up the user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Exchange auth code for tokens
    flow = _build_flow()
    # Google returns all previously-granted scopes in the callback (not just the ones
    # we requested), which google_auth_oauthlib treats as a scope change error.
    # Setting the session scope to None skips that validation.
    flow.oauth2session.scope = None
    try:
        flow.fetch_token(code=code, code_verifier=None)
    except Exception as exc:
        logger.error("Google token exchange failed for user %s: %s", user_id, exc)
        raise HTTPException(status_code=502, detail="Failed to exchange Google auth code") from exc

    credentials = flow.credentials

    if not credentials.refresh_token:
        logger.warning(
            "No refresh_token returned for user %s. User may have previously "
            "authorized without revoking. Keeping existing token if present.",
            user_id,
        )
        if not user.google_refresh_token:
            # Redirect with error since we have no token to store
            return RedirectResponse(
                url=(
                    f"{settings.FRONTEND_URL.rstrip('/')}"
                    "/dashboard?portal=hub&workflow=connections&google=error&reason=no_refresh_token"
                )
            )
    else:
        user.google_refresh_token = credentials.refresh_token

    await db.flush()

    logger.info("Google account connected for user %s", user_id)
    return RedirectResponse(
        url=(f"{settings.FRONTEND_URL.rstrip('/')}/dashboard?portal=hub&workflow=connections&google=connected")
    )


@router.delete("/disconnect", status_code=204)
async def google_disconnect(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove the legacy Google refresh token from the current user's record."""
    if workspace_delegation_enabled():
        raise HTTPException(
            status_code=409,
            detail="Google Calendar is managed by the Ryozan booking service",
        )
    if not current_user.google_refresh_token:
        raise HTTPException(status_code=404, detail="No Google account connected")

    current_user.google_refresh_token = None
    await db.flush()
    logger.info("Google account disconnected for user %s", current_user.id)


@router.get("/status", response_model=GoogleStatusResponse)
async def google_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check the active Calendar authority for the current staff user."""
    if workspace_delegation_enabled():
        calendar_user = current_user
        source = "workspace_delegated"
        if ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3) and current_user.role not in {
            "artist",
            "founder",
        }:
            result = await db.execute(select(User).where(User.role == "founder"))
            founder = result.scalar_one_or_none()
            if founder:
                calendar_user = founder
                source = "workspace_founder_fallback"

        if artist_calendar_connected(calendar_user):
            target_calendar_id = artist_calendar_id(calendar_user)
            health = check_calendar_connection(calendar_id=target_calendar_id)
            return GoogleStatusResponse(
                connected=True,
                managed=True,
                effective_user_id=str(calendar_user.id),
                email=calendar_user.email,
                source=source,
                healthy=health["healthy"],
                calendar_accessible=health["calendar_accessible"],
                calendar_id=health["calendar_id"],
                calendar_summary=health["summary"],
                reason=health["reason"],
                reconnect_required=False,
            )

        return GoogleStatusResponse(
            connected=False,
            managed=True,
            source=source,
            reason="workspace_calendar_not_configured",
            reconnect_required=False,
        )

    if current_user.google_refresh_token:
        health = check_calendar_connection(current_user.google_refresh_token)
        return GoogleStatusResponse(
            connected=True,
            effective_user_id=str(current_user.id),
            email=current_user.email,
            source="self",
            healthy=health["healthy"],
            calendar_accessible=health["calendar_accessible"],
            calendar_id=health["calendar_id"],
            calendar_summary=health["summary"],
            reason=health["reason"],
            reconnect_required=health["reconnect_required"],
        )

    # Admin fallback: check if any founder has Google Calendar
    if ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3):
        result = await db.execute(
            select(User).where(
                User.role == "founder",
                User.google_refresh_token.isnot(None),
            )
        )
        founder = result.scalar_one_or_none()
        if founder:
            health = check_calendar_connection(founder.google_refresh_token)
            return GoogleStatusResponse(
                connected=True,
                effective_user_id=str(founder.id),
                email=founder.email,
                source="founder_fallback",
                healthy=health["healthy"],
                calendar_accessible=health["calendar_accessible"],
                calendar_id=health["calendar_id"],
                calendar_summary=health["summary"],
                reason=health["reason"],
                reconnect_required=health["reconnect_required"],
            )

    return GoogleStatusResponse(
        connected=False,
        source="none",
        reason="not_connected",
        reconnect_required=True,
    )
