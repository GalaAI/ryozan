"""Shared judge demo account for a hosted demo install.

Configured by DEMO_ACCOUNT_EMAIL and DEMO_ACCOUNT_PASSWORD. ensure_demo_account
runs at startup and keeps one verified client account with that password, so
the password lives only in the deploy environment. The account's password and
existence are fixed: change-password, password reset and account deletion
refuse it. It is created like an instant sign-up account, so OPEN_SIGNUP_UNTIL
ends its AI access, and its monthly AI turns and Tavily searches have their own
caps because every judge shares them.

It is a client account, unless DEMO_SHOP_ENABLED makes it the owner of the
shared judge shop (app.services.demo_shop) on an install no one else owns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.models.user import User, normalize_email

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEMO_ACCOUNT_NAME = "Hackathon Judge"


def _demo_email() -> str:
    raw = get_settings().DEMO_ACCOUNT_EMAIL.strip()
    return normalize_email(raw) if raw else ""


def is_demo_account(user: User | None) -> bool:
    email = _demo_email()
    return bool(email and user is not None and normalize_email(user.email or "") == email)


def refuse_if_demo_account(user: User | None) -> None:
    from app.services.demo_shop import is_demo_npc

    if is_demo_account(user) or is_demo_npc(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The shared demo account cannot change its password or be removed.",
        )


async def ensure_demo_account(db: AsyncSession) -> str:
    """Create or repair the demo account; returns what happened, never the password."""
    settings = get_settings()
    email = _demo_email()
    password = settings.DEMO_ACCOUNT_PASSWORD
    if not email or not password:
        return "not configured"
    if len(password) < 12:
        # A short shared password is refused rather than published to judges.
        return "skipped: DEMO_ACCOUNT_PASSWORD must be 12 or more characters"
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    other_owner = None
    if settings.DEMO_SHOP_ENABLED:
        # Asked before a new row is added, so the query cannot flush it half filled.
        other_owner = await db.scalar(select(User.id).where(User.role == "founder", User.email != email).limit(1))
    now = datetime.now(UTC)
    if user is None:
        user = User(email=email, name=DEMO_ACCOUNT_NAME, role="collector", password_change_method="signup")
        db.add(user)
        outcome = "created"
    else:
        outcome = "verified"
    ours = user.role == "collector" or (
        user.role == "founder" and getattr(user, "password_change_method", None) == "signup"
    )
    if not ours:
        # The demo address must never take over a real staff account.
        return f"skipped: {email} belongs to a {user.role} account"
    if settings.DEMO_SHOP_ENABLED and user.role != "founder":
        if other_owner is not None:
            return "skipped: the demo shop needs an install with no other owner"
        user.role = "founder"
    elif not settings.DEMO_SHOP_ENABLED and user.role == "founder":
        user.role = "collector"
    if not user.password_hash or not verify_password(password, user.password_hash):
        user.password_hash = hash_password(password)
        user.password_changed_at = now
        outcome = "created" if outcome == "created" else "password updated"
    user.email_verified_at = user.email_verified_at or now
    user.must_reset_password = False
    user.password_state = "user_set"
    user.password_change_method = "signup"
    user.ai_monthly_message_cap = settings.DEMO_ACCOUNT_MONTHLY_AI_TURNS
    await db.commit()
    return outcome
