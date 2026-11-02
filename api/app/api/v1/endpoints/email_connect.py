"""
Email connect endpoints -- artists claim a handle on the studio's receiving
domain (COURIER_REPLY_DOMAIN) and link it to their personal email.

Flow:
  1. POST   /email/connect  -- artist picks handle + provides personal email
  2. Verification email sent to personal email with confirm link
  3. GET    /email/verify?token=xxx -- confirms the route, marks quest complete
  4. GET    /email/status -- check current email route status
  5. DELETE /email/release -- release claimed handle (irreversible)
"""

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.email_route import EmailRoute
from app.models.user import User
from app.services.email import FROM_ADMIN, send_email
from app.services.quest_hooks import check_and_complete_quest

router = APIRouter(prefix="/email", tags=["email"])

NO_DOMAIN_DETAIL = "Studio email handles are off: set COURIER_REPLY_DOMAIN to the domain Resend receives mail on"


def _handle_address(handle: str) -> str | None:
    """handle@COURIER_REPLY_DOMAIN, or None when no receiving domain is set."""
    return get_settings().inbound_address(handle)


def _require_handle_address(handle: str) -> str:
    address = _handle_address(handle)
    if address is None:
        raise HTTPException(503, NO_DOMAIN_DETAIL)
    return address


# Reserved handles that can't be claimed by artists
RESERVED_HANDLES = {
    "info",
    "admin",
    "support",
    "help",
    "noreply",
    "no-reply",
    "bookings",
    "billing",
    "legal",
    "press",
    "marketing",
    "hello",
    "contact",
    "team",
    "hr",
    "jobs",
    "careers",
    "postmaster",
    "abuse",
    "webmaster",
    "mail",
}


class EmailConnectRequest(BaseModel):
    handle: str  # desired handle, e.g. "sarah"
    forward_to: EmailStr  # personal email


class EmailStatusResponse(BaseModel):
    handle: str | None = None
    bttco_email: str | None = None
    forward_to: str | None = None
    verified: bool = False
    connected: bool = False


class HandleCheckResponse(BaseModel):
    handle: str
    available: bool
    bttco_email: str | None


def _sanitize_handle(handle: str) -> str:
    """Lowercase, strip whitespace, remove @ and domain if pasted."""
    handle = handle.lower().strip()
    if "@" in handle:
        handle = handle.split("@")[0]
    # Only allow alphanumeric, dots, hyphens, underscores
    cleaned = "".join(c for c in handle if c.isalnum() or c in ".-_")
    return cleaned


@router.get("/check/{handle}", response_model=HandleCheckResponse)
async def check_handle_availability(
    handle: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Check if a handle is available on the studio's receiving domain."""
    handle = _sanitize_handle(handle)
    if not handle or len(handle) < 2:
        raise HTTPException(400, "Handle must be at least 2 characters")
    address = _require_handle_address(handle)

    available = True
    if handle in RESERVED_HANDLES:
        available = False
    else:
        existing = await db.execute(select(func.count()).select_from(EmailRoute).where(EmailRoute.handle == handle))
        if existing.scalar() > 0:
            available = False

    return HandleCheckResponse(
        handle=handle,
        available=available,
        bttco_email=address,
    )


@router.post("/connect", response_model=EmailStatusResponse)
async def connect_email(
    body: EmailConnectRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """
    Artist claims a handle on the receiving domain and links their personal email.
    Sends a verification email to confirm ownership of the personal address.
    """
    settings = get_settings()
    handle = _sanitize_handle(body.handle)

    if not handle or len(handle) < 2:
        raise HTTPException(400, "Handle must be at least 2 characters")
    if len(handle) > 30:
        raise HTTPException(400, "Handle must be 30 characters or less")
    if handle in RESERVED_HANDLES:
        raise HTTPException(400, f"'{handle}' is reserved")
    address = _require_handle_address(handle)

    # Check if handle is taken by someone else
    existing = await db.execute(select(EmailRoute).where(EmailRoute.handle == handle))
    route = existing.scalar_one_or_none()
    if route and route.user_id != current_user.id:
        raise HTTPException(409, f"'{handle}' is already taken")

    # Check if user already has a route
    user_route = await db.execute(select(EmailRoute).where(EmailRoute.user_id == current_user.id))
    existing_route = user_route.scalar_one_or_none()

    token = secrets.token_urlsafe(32)

    if existing_route:
        # Update existing route
        existing_route.handle = handle
        existing_route.forward_to = body.forward_to
        existing_route.verified = False
        existing_route.verify_token = token
        existing_route.verified_at = None
    else:
        # Create new route
        new_route = EmailRoute(
            handle=handle,
            forward_to=body.forward_to,
            user_id=current_user.id,
            verify_token=token,
        )
        db.add(new_route)

    await db.flush()

    # Send verification email
    base_url = getattr(settings, "API_PUBLIC_BASE_URL", None) or "http://127.0.0.1:18788"
    verify_url = f"{base_url.rstrip('/')}/api/v1/email/verify?token={token}"

    await send_email(
        to=body.forward_to,
        subject="Verify your Ryozan email",
        html_body=f"""
        <div style="font-family: sans-serif; max-width: 500px; margin: 0 auto; padding: 24px;">
            <h2 style="color: #d4a853;">Confirm Your Ryozan Email</h2>
            <p>Hey {current_user.name},</p>
            <p>You're claiming <strong>{address}</strong> as your Ryozan email.</p>
            <p>Click below to verify this forwards to your personal inbox:</p>
            <a href="{verify_url}"
               style="display: inline-block; padding: 12px 24px; background: #d4a853; color: #000;
                      text-decoration: none; font-weight: bold; border-radius: 6px; margin: 16px 0;">
                Verify My Email
            </a>
            <p style="color: #888; font-size: 13px;">
                If you didn't request this, just ignore this email.
            </p>
        </div>
        """,
        from_alias=FROM_ADMIN,
    )

    return EmailStatusResponse(
        handle=handle,
        bttco_email=address,
        forward_to=body.forward_to,
        verified=False,
        connected=True,
    )


@router.get("/verify")
async def verify_email(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Verify email route via token from confirmation email.
    Auto-completes the 'connect-email' onboarding quest if it exists.
    """
    result = await db.execute(select(EmailRoute).where(EmailRoute.verify_token == token))
    route = result.scalar_one_or_none()
    if not route:
        raise HTTPException(404, "Invalid or expired verification token")

    if route.verified:
        return {"status": "already_verified", "email": _handle_address(route.handle)}

    route.verified = True
    route.verified_at = datetime.now(UTC)
    route.verify_token = None  # single-use token

    # Auto-complete quests for email verification
    await check_and_complete_quest(db, route.user_id, "connect-email")
    await check_and_complete_quest(db, route.user_id, "email-alias")

    await db.flush()

    # Redirect to the app with a success message
    return {
        "status": "verified",
        "email": _handle_address(route.handle),
        "message": "Your Ryozan email is now active! Incoming mail will forward to your personal inbox.",
    }


@router.get("/status", response_model=EmailStatusResponse)
async def email_status(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Get the current user's email route status."""
    result = await db.execute(select(EmailRoute).where(EmailRoute.user_id == current_user.id))
    route = result.scalar_one_or_none()
    if not route:
        return EmailStatusResponse()

    return EmailStatusResponse(
        handle=route.handle,
        bttco_email=_handle_address(route.handle),
        forward_to=route.forward_to,
        verified=route.verified,
        connected=True,
    )


@router.delete("/release", status_code=204)
async def release_email(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """
    Release the current user's studio email handle.

    Deletes the email route entirely so the handle becomes available again.
    This action is irreversible — the user would need to re-claim and re-verify.
    """
    result = await db.execute(select(EmailRoute).where(EmailRoute.user_id == current_user.id))
    route = result.scalar_one_or_none()
    if not route:
        raise HTTPException(404, "No email route found")

    await db.delete(route)
    await db.flush()
