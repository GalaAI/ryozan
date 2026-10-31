"""
User model - maps to existing ryozan_users table in nexus.

7 users already seeded. Schema matches what Tauri/Rust backend created.

Artist-specific fields (slug, specialty, bio, etc.) are on the same table
since artists ARE users with role='artist'. No join table needed.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import ARRAY, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.database import Base


def normalize_email(email: str | None) -> str | None:
    """The single definition of a canonical BTTCO email address.

    Stripped and lowercased. Every read comparison and every write goes
    through this, so `Meredith@x.com` and `meredith@x.com` can never become
    two different accounts again.
    """
    if email is None:
        return None
    return str(email).strip().lower()


class UserIdentityAlias(Base):
    """Operator-confirmed identities retained when duplicate user rows are merged.

    The existing user resolver and dedupe command own authentication and merging.
    This table adds the missing many-identities-to-one-account contract, not a
    second account system. No public profile or signup API can create aliases.
    """

    __tablename__ = "ryozan_user_identity_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ryozan_users.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_binding: Mapped[str] = mapped_column(String(32))
    source_snapshot: Mapped[dict] = mapped_column(JSONB)
    reference_snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class BTTCOUser(Base):
    __tablename__ = "ryozan_users"
    __table_args__ = (
        Index("idx_ryozan_users_role", "role"),
        Index("ix_ryozan_users_membership_tier", "membership_tier"),
        # Alongside the plain google_id unique constraint (ryozan_users_google_id_key).
        Index(
            "ix_ryozan_users_google_id",
            "google_id",
            unique=True,
            postgresql_where=text("google_id IS NOT NULL"),
        ),
        Index(
            "ux_ryozan_users_booth_rent_subscription_id",
            "booth_rent_subscription_id",
            unique=True,
            postgresql_where=text("booth_rent_subscription_id IS NOT NULL"),
        ),
        # Case-insensitive email uniqueness; normalize_email() keeps writes lowercase.
        Index("ux_ryozan_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Session generation. Every access token carries it as `tv`; sign-out bumps
    # it so tokens minted before the sign-out stop authenticating.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="collector")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    bttcoin_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quest_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_reset_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Defaults describe future generated credentials; migrations leave old rows unknown.
    password_state: Mapped[str | None] = mapped_column(String(24), nullable=True, default="setup_required")
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=lambda: datetime.now(UTC)
    )
    password_change_method: Mapped[str | None] = mapped_column(String(16), nullable=True, default="setup")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reasons: Mapped[list | None] = mapped_column(ARRAY(String), nullable=True)
    membership_tier: Mapped[str] = mapped_column(String(50), nullable=False, default="freemium")
    membership_status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    booth_rent_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    booth_rent_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
    booth_rent_payment_method: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'cash'"), default="cash"
    )
    booth_rent_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'not_configured'"), default="not_configured"
    )
    booth_rent_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    booth_rent_checkout_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    booth_rent_current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booth_rent_next_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    booth_rent_cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    ai_monthly_message_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    early_access_galax3: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connected_apps_unlocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    product_entitlements: Mapped[dict | None] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Artist-specific fields (null for non-artists) ────
    slug: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "Founder / 2nd Gen Tattoo Artist"
    specialty: Mapped[list | None] = mapped_column(ARRAY(String), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    booking_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 'mailto' | 'api' | an external mode
    booking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    portfolio_images: Mapped[list | None] = mapped_column(ARRAY(String), nullable=True)
    # original_url -> 640px WebP thumb url (app.services.portfolio_thumbs). A dict, not a
    # parallel list, so removing or reordering an original never desyncs its thumb.
    portfolio_thumbs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # original_url -> 1400px WebP focus url for the enlarged view (same service, same rules).
    portfolio_focus: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Stripe Connect ────
    stripe_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_onboarding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    commission_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)  # artist's percentage (70 = 70%)
    is_guest_artist: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)

    # ── Plaid (bank account linking) ────
    plaid_access_token: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── Availability source (how this artist's calendar is managed) ────
    # Values: 'google_calendar', 'venue', 'manual', 'none'
    availability_source: Mapped[str] = mapped_column(String(50), nullable=False, default="none")
    booking_slot_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    booking_buffer_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # Whether booking_buffer_minutes is applied at all. server_default=true
    # preserves the existing behaviour for every row already in production,
    # where the buffer has always been in force.
    booking_buffer_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    booking_min_notice_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    booking_max_advance_days: Mapped[int] = mapped_column(Integer, nullable=False, default=180)
    booking_allow_same_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Booky is opt-in per artist. session_auto is reserved at the contract
    # layer but cannot be persisted through the settings API.
    booky_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'manual'"), default="manual"
    )
    booky_opted_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Google OAuth (per-user calendar/profile connection) ────
    google_refresh_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Calendar shared with the delegated BTTCO booking identity. Defaults to
    # the artist account email when blank, but supports secondary calendars.
    google_calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)

    # ── Pinterest OAuth (per-user content connection, API v5) ──
    pinterest_refresh_token: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pinterest_access_token: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pinterest_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Gala AI personality ────
    gala_personality: Mapped[int] = mapped_column(
        Integer, nullable=False, default=75
    )  # 0 = professional/quiet, 100 = full wizard mode

    # ── Gala Auto-Reply (null=disabled, "transparent"=as Gala, "voice"=as artist) ──
    gala_auto_reply: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── Nexus Memory Profile (response templates, preferences, AI context) ──
    nexus_profile: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=dict
    )  # JSONB: response_templates, booking_prefs, etc.

    # ── Saved artist signature (R2 URL, persists across waivers) ──
    saved_signature_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Flash artist settings ────
    flash_deposit_amount: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    allow_prepay_full: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    @property
    def working_hours(self) -> dict | None:
        if isinstance(self.nexus_profile, dict):
            return self.nexus_profile.get("working_hours")
        return None

    # ── Inbox delegation — when set, this user shares the proxy target's
    #    inbox and sends messages as that user ────
    proxy_for: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # ── Portfolio / contact fields ────
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── SMS / A2P 10DLC opt-in tracking ────
    # sms_opt_in defaults to False; flipped true when user explicitly opts in
    # (booking form checkbox, "START" reply, or admin tool). Timestamps preserved
    # for audit / CTIA compliance.
    sms_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sms_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sms_opt_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    facebook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tiktok_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    @validates("email")
    def _canonicalize_email(self, _key: str, value: str | None) -> str | None:
        """Normalize on every write — construction and later assignment alike.

        This is the owner of email casing. Call sites do not need to remember
        to `.lower()`, and the ones that forgot (quotes, flash, booking repair,
        the sync scripts) are fixed by this alone. Paired with the
        `ux_ryozan_users_email_lower` unique index so the invariant is enforced
        in the database too, not only in Python.
        """
        normalized = normalize_email(value)
        if self.email is not None and self.email != normalized:
            self.email_verified_at = None
        return normalized
