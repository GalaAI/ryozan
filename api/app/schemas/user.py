"""
Pydantic schemas for user operations.

Separation of concerns:
  - *Create  = what the client sends to register
  - *Login   = what the client sends to authenticate
  - *Response = what the API returns (never exposes password_hash)
  - *Update  = partial updates to profile
"""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserAccountStatus(BaseModel):
    """Founder-only evidence, deliberately separate from all profile schemas."""

    id: UUID
    password_status: Literal["setup_required", "user_set", "historical_unknown"]
    password_changed_at: datetime | None
    password_change_method: Literal["setup", "change", "reset", "invite"] | None
    last_login_at: datetime | None
    last_login_method: Literal["password", "google", "invite"] | None


def _fix_drive_url(url: str | None) -> str | None:
    """Convert stale uc?export=view URLs to lh3 direct links."""
    if not url:
        return url
    if url.startswith("/uploads/"):
        return None
    m = re.search(r"drive\.google\.com/uc\?.*?id=([A-Za-z0-9_-]+)", url)
    if m:
        return f"https://lh3.googleusercontent.com/d/{m.group(1)}"
    return url


# ── Auth ──────────────────────────────────────────────


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=255)
    reasons: list[str] | None = None
    marketing_opt_in: bool = False
    # Invite code for instant sign-up on a demo install (OPEN_SIGNUP_CODE).
    signup_code: str | None = Field(default=None, max_length=128)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str  # user id
    role: str
    exp: datetime


# ── User responses ────────────────────────────────────


class UserResponse(BaseModel):
    id: UUID
    email: str
    name: str
    role: str
    avatar_url: str | None = None
    bttcoin_balance: int = 0
    quest_points: int = 0
    marketing_opt_in: bool = False
    must_reset_password: bool = False
    reasons: list[str] | None = None
    membership_tier: str = "freemium"
    membership_status: str = "active"
    stripe_customer_id: str | None = None
    ai_monthly_message_cap: int = 40
    early_access_galax3: bool = False
    connected_apps_unlocked: bool = False
    product_entitlements: dict | None = None
    gala_personality: int = 75
    nexus_profile: dict | None = None
    proxy_for: UUID | None = None
    created_at: datetime

    @field_validator("avatar_url", mode="before")
    @classmethod
    def fix_avatar_url(cls, v: str | None) -> str | None:
        return _fix_drive_url(v)

    model_config = {"from_attributes": True}


class UserProfileUpdate(BaseModel):
    """Fields an artist/user can update on their own profile."""

    name: str | None = None
    avatar_url: str | None = None
    marketing_opt_in: bool | None = None
    reasons: list[str] | None = None
    gala_personality: int | None = Field(None, ge=0, le=100)
    nexus_profile: dict | None = None  # JSONB: response_templates, booking_prefs


class ChangePassword(BaseModel):
    """Verify current password, set a new one."""

    current_password: str
    new_password: str = Field(min_length=8)


class UserRoleUpdate(BaseModel):
    """Admin-only: change a user's role."""

    role: str = Field(pattern="^(collector|assistant|artist|admin|founder)$")


class UserMembershipResponse(BaseModel):
    configured_tier: str
    effective_tier: str
    membership_status: str
    ai_monthly_message_cap: int | None = None
    ai_messages_used: int = 0
    ai_messages_remaining: int | None = None
    primary_model: str
    fallback_model: str | None = None
    early_access_galax3: bool = False
    connected_apps_unlocked: bool = False
    product_entitlements: dict = Field(default_factory=dict)


class UserMembershipUpdate(BaseModel):
    membership_tier: str | None = Field(default=None, pattern="^(freemium|pro|premium|business)$")
    membership_status: str | None = Field(default=None, pattern="^(active|trialing|past_due|canceled|inactive)$")
    stripe_customer_id: str | None = None
    ai_monthly_message_cap: int | None = Field(default=None, ge=0, le=50000)
    early_access_galax3: bool | None = None
    connected_apps_unlocked: bool | None = None
    product_entitlements: dict | None = None
