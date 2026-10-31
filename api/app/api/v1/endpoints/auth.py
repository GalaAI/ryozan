"""
Auth endpoints — register + login + invite flow.

These map to the existing ryozan_users table.
Passwords are bcrypt-hashed; responses are JWT tokens.

Invite flow:
  POST /auth/invite         — admin generates a signed invite token
  POST /auth/register-invite — public; validates token + creates account
"""

import hmac
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.security import (
    PASSWORD_RESET_EXPIRE_MINUTES,
    ROLE_HIERARCHY,
    create_access_token,
    create_password_reset_token,
    create_user_access_token,
    decode_password_reset_token,
    get_current_user,
    hash_password,
    require_role,
    require_verified_collector,
    reset_token_matches_hash,
    reset_token_proves_email,
    verify_password,
)
from app.core.user_lookup import (
    authenticate_by_email,
    find_user_by_email,
    user_email_matches,
)
from app.models.booking import Booking
from app.models.quote import Quote
from app.models.user import User, normalize_email
from app.schemas.user import (
    ChangePassword,
    Token,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.account_setup import send_account_setup_email
from app.services.account_status import record_password_setup, record_successful_login, set_user_password
from app.services.demo_account import is_demo_account, refuse_if_demo_account
from app.services.email import (
    FROM_ADMIN,
    build_invite_html,
    render_db_template,
    send_email,
    send_password_changed_email,
    send_password_reset_email,
    send_welcome_email,
)

logger = logging.getLogger(__name__)

_settings = get_settings()
INVITE_EXPIRE_HOURS = 72  # invites valid for 3 days

router = APIRouter(prefix="/auth", tags=["auth"])


# Email canonicalization and case-insensitive matching are owned by
# app.models.user / app.core.user_lookup. These aliases keep the local call
# sites (and the existing tests) reading naturally without a second definition.
_normalize_email = normalize_email
_user_email_matches = user_email_matches


class RegistrationPending(BaseModel):
    message: str = "Check your email for a link to verify your address and choose your password."
    verification_required: bool = True
    # Set only by instant sign-up (OPEN_SIGNUP_ENABLED): the new account is
    # already verified and this token signs it in.
    access_token: str | None = None
    token_type: str | None = None


def _queue_account_setup(background_tasks: BackgroundTasks, user: User) -> None:
    background_tasks.add_task(
        send_account_setup_email,
        user_id=user.id,
        email=user.email,
        password_hash=user.password_hash,
        name=user.name or "there",
    )


OPEN_SIGNUP_CODE_MIN_LENGTH = 8


def instant_signup_open(settings) -> bool:
    """Instant sign-up is on, has an invite code, and its OPEN_SIGNUP_UNTIL date (UTC, inclusive) has not passed."""
    # A blank or short code fails closed: instant sign-up never opens without a real one.
    if not settings.OPEN_SIGNUP_ENABLED or len(settings.OPEN_SIGNUP_CODE.strip()) < OPEN_SIGNUP_CODE_MIN_LENGTH:
        return False
    until = settings.OPEN_SIGNUP_UNTIL.strip()
    if not until:
        return True
    try:
        last_day = date.fromisoformat(until)
    except ValueError:
        # A malformed date fails closed rather than leaving sign-up open forever.
        return False
    return datetime.now(UTC).date() <= last_day


async def _instant_signup(db: AsyncSession, body: UserCreate, email: str, existing: User | None) -> RegistrationPending:
    """Create a verified collector with the caller's password and sign it in.

    Only for an install where instant_signup_open() holds. It requires the
    OPEN_SIGNUP_CODE and stops at OPEN_SIGNUP_MAX_ACCOUNTS. An
    unverified collector row left by the email flow is never taken over: its
    mailbox owner set it up.
    """
    settings = get_settings()
    code = settings.OPEN_SIGNUP_CODE.strip()
    if len(code) < OPEN_SIGNUP_CODE_MIN_LENGTH or not hmac.compare_digest(
        (body.signup_code or "").strip().encode(), code.encode()
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="That invite code is not valid.")
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    created = await db.scalar(select(func.count()).select_from(User).where(User.password_change_method == "signup"))
    if (created or 0) >= settings.OPEN_SIGNUP_MAX_ACCOUNTS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sign-up is full on this demo.")
    now = datetime.now(UTC)
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        name=body.name,
        role="collector",
        reasons=body.reasons,
        marketing_opt_in=body.marketing_opt_in,
        email_verified_at=now,
        must_reset_password=False,
        password_state="user_set",
        password_changed_at=now,
        password_change_method="signup",
    )
    db.add(user)
    await db.flush()
    record_successful_login(user, "password")
    return RegistrationPending(
        message="Account created. You are signed in.",
        verification_required=False,
        access_token=create_user_access_token(user),
        token_type="bearer",
    )


# Nulls are dropped so a pending registration never carries an access_token key.
@router.post(
    "/register",
    response_model=RegistrationPending,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def register(
    request: Request, body: UserCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    """Start collector setup; only the mailbox gets a password-setting link."""
    email = _normalize_email(body.email)

    # Check if email already taken. Case-insensitive, so registering
    # "Meredith@x.com" over an existing "meredith@x.com" is a 409, not a
    # second row.
    existing = await find_user_by_email(db, email)
    if existing and (existing.role != "collector" or existing.email_verified_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    if instant_signup_open(get_settings()):
        return await _instant_signup(db, body, email, existing)
    if existing:
        _queue_account_setup(background_tasks, existing)
        return RegistrationPending()

    user = User(
        email=email,
        # A public caller must never choose the mailbox owner's credential.
        password_hash=hash_password(secrets.token_urlsafe(32)),
        name=body.name,
        role="collector",
        reasons=body.reasons,
        marketing_opt_in=body.marketing_opt_in,
    )
    db.add(user)
    await db.flush()  # get the id before commit

    _queue_account_setup(background_tasks, user)
    return RegistrationPending()


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
async def login(request: Request, body: UserLogin, db: AsyncSession = Depends(get_db)):
    """Authenticate and return a JWT.

    Duplicate-safe: the lookup tries the password against every row matching
    this address rather than demanding exactly one. Before this, two rows
    differing only by capitalization made `scalar_one_or_none()` raise
    `MultipleResultsFound`, which surfaced as an HTTP 500 on every attempt.
    """
    email = _normalize_email(body.email)

    user = await authenticate_by_email(db, email, body.password)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    require_verified_collector(user)
    record_successful_login(user, "password")
    token = create_user_access_token(user)
    return Token(access_token=token)


class DevImpersonateRequest(BaseModel):
    # Mirrored development accounts intentionally use the reserved `.test`
    # domain. This local-only founder route validates syntax without applying
    # deliverability rules that reject the mirror's own account contract.
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/dev-impersonate", response_model=Token)
async def dev_impersonate(
    body: DevImpersonateRequest,
    db: AsyncSession = Depends(get_db),
    _caller: User = Depends(require_role("founder")),
):
    """LOCAL DEV ONLY: founder mints a token for another local account.

    Double-gated: requires LOCAL_DEV_MODE and the founder role. Exists so the
    operator can preview the collector portal as the E2E dummy account with
    real data, without juggling browser profiles. Never active in production
    (LOCAL_DEV_MODE is false there).
    """
    settings = get_settings()
    if not settings.LOCAL_DEV_MODE:
        raise HTTPException(status_code=404, detail="Not found")

    email = _normalize_email(body.email)
    user = await find_user_by_email(db, email)
    if not user:
        raise HTTPException(status_code=404, detail="No such local account")

    token = create_user_access_token(user)
    return Token(access_token=token)


@router.get("/options")
async def auth_options():
    """Public sign-in options this install can complete.

    Google needs a configured OAuth client; self-registration needs an outbound
    email provider for the verification link. The portal hides what is absent.
    """
    settings = get_settings()
    email_ready = bool(
        settings.SENDGRID_API_KEY.strip()
        or settings.RESEND_API_KEY.strip()
        or (settings.SMTP_USER.strip() and settings.SMTP_PASSWORD.strip())
    )
    return {
        "google": bool(settings.GOOGLE_CLIENT_ID.strip() and settings.GOOGLE_CLIENT_SECRET.strip()),
        "registration": email_ready or instant_signup_open(settings),
        "instant_signup": instant_signup_open(settings),
        "signup_code_required": instant_signup_open(settings),
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sign out everywhere: retire every access token issued to this account."""
    current_user.token_version = int(current_user.token_version or 0) + 1
    await db.commit()


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return current_user


@router.patch("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePassword,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change a password, allowing an authenticated first-login reset."""
    refuse_if_demo_account(current_user)
    if not current_user.must_reset_password and not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    set_user_password(current_user, body.new_password, "change")
    await db.commit()


# ── Password reset (forgot password) ───────────────────────────────────────
#
# Token design, in short:
#   * signed with SECRET_KEY / ALGORITHM, same as every other token here
#   * `typ: password_reset` so get_current_user refuses it as a session token,
#     and so an access token can never be redeemed here
#   * `pwf` = fingerprint of the user's CURRENT password hash. Setting a new
#     password changes the hash, so the token stops validating: single use,
#     with no new table or column (production does not run alembic)
#   * expires after PASSWORD_RESET_EXPIRE_MINUTES, which is configuration
#     (Settings.PASSWORD_RESET_EXPIRE_MINUTES, 24 hours by default) rather than
#     a fixed constant. The window can be generous precisely because `pwf`
#     already makes the token single-use and password-bound.
# The token is never logged and never appears in an error message.


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


# One response for every input. Never confirms whether an account exists.
_FORGOT_PASSWORD_MESSAGE = "If an account exists for that email, a reset link is on its way."

# One error for every failure mode: expired, tampered, wrong type, already
# used, unknown user. Distinguishing them would leak account state.
_RESET_TOKEN_ERROR = "This reset link is invalid or has expired. Request a new one."


def _locked_user_by_id(user_id: UUID):
    """Select one user while serializing password-reset redemption."""
    return select(User).where(User.id == user_id).with_for_update()


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Start a password reset.

    Always returns the same 200 body whether or not the account exists. The
    only observable difference is that an email is actually sent when it does.
    Sending happens in a background task so a slow SMTP call cannot hang the
    request (and cannot be timed to probe for accounts).
    """
    email = _normalize_email(body.email)

    # Duplicate-safe: sends to the most authenticatable row rather than 500ing.
    user = await find_user_by_email(db, email)

    # The shared demo account never gets a reset link; the response stays generic.
    if user is not None and user.password_hash and not is_demo_account(user):
        token = create_password_reset_token(user.id, user.password_hash, email=user.email)
        reset_url = f"{_settings.FRONTEND_URL}/reset-password?token={token}"
        background_tasks.add_task(
            send_password_reset_email,
            to_email=user.email,
            name=user.name or "there",
            reset_url=reset_url,
            expires_in_minutes=PASSWORD_RESET_EXPIRE_MINUTES,
        )

    return ForgotPasswordResponse(message=_FORGOT_PASSWORD_MESSAGE)


class ResetTokenStatus(BaseModel):
    usable: bool


@router.get("/reset-password/validate", response_model=ResetTokenStatus)
@limiter.limit("20/minute")
async def validate_reset_token(request: Request, token: str = "", db: AsyncSession = Depends(get_db)):
    """Report whether a reset link is still redeemable, without spending it.

    Exists so the reset page can tell someone their link is dead *before* they
    invent a password and type it twice. Previously the only way to test a
    token was to redeem it, so an expired link presented a perfectly normal
    form and failed at submit with a message that could not say why.

    Leaks nothing. It answers a single boolean to a caller who already holds
    the token, names no account, and distinguishes no failure mode: expired,
    tampered, wrong type, already spent, and unknown user are all just false.
    """
    if not token:
        return ResetTokenStatus(usable=False)
    try:
        payload = decode_password_reset_token(token)
        user_uuid = UUID(str(payload["sub"]))
    except (ValueError, TypeError):
        return ResetTokenStatus(usable=False)

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None or not user.password_hash:
        return ResetTokenStatus(usable=False)

    return ResetTokenStatus(
        usable=reset_token_matches_hash(payload, user.password_hash) and reset_token_proves_email(payload, user)
    )


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Redeem a reset token and set a new password. Clears must_reset_password."""
    try:
        payload = decode_password_reset_token(body.token)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_ERROR) from None

    try:
        user_uuid = UUID(str(payload["sub"]))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_ERROR) from None

    result = await db.execute(_locked_user_by_id(user_uuid))
    user = result.scalar_one_or_none()
    if user is None or not user.password_hash or is_demo_account(user):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_ERROR)

    # Single-use gate: the fingerprint only matches while the password the
    # token was minted against is still the current one.
    if not reset_token_matches_hash(payload, user.password_hash) or not reset_token_proves_email(payload, user):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_RESET_TOKEN_ERROR)

    # A preclaimant could also have linked their own Google identity using a
    # historical unverified session. Mailbox redemption must retire that login.
    if user.role == "collector" and not user.email_verified_at:
        user.google_id = None
    set_user_password(user, body.new_password, "reset")
    user.email_verified_at = datetime.now(UTC)
    await db.commit()

    # Tell the account holder their password changed. This is the only signal
    # that an account takeover through the reset flow would ever produce, so
    # it sends even though nothing in the request needs it. Non-blocking, and
    # a send failure must not fail a reset that already succeeded.
    background_tasks.add_task(
        send_password_changed_email,
        to_email=user.email,
        name=user.name or "there",
    )


# ── Invite flow ────────────────────────────────────────────────────────────


class InviteRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern="^(collector|assistant|artist|admin)$")
    slug: str | None = None  # for artist accounts


class InviteResponse(BaseModel):
    token: str
    invite_url: str
    expires_in_hours: int = INVITE_EXPIRE_HOURS


class InviteRegisterRequest(BaseModel):
    invite_token: str
    password: str = Field(min_length=8)


@router.post(
    "/invite",
    response_model=InviteResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def generate_invite(body: InviteRequest, background_tasks: BackgroundTasks):
    """
    Admin-only: generate a signed invite link for an artist or staff member.
    The token embeds email + name + role + slug so the register form is pre-filled.
    Valid for INVITE_EXPIRE_HOURS hours. Fires a branded invite email automatically.
    """
    email = _normalize_email(body.email)
    payload = {
        "invite": True,
        "email": email,
        "name": body.name,
        "role": body.role,
        "slug": body.slug,
    }
    token = create_access_token(payload, expires_delta=timedelta(hours=INVITE_EXPIRE_HOURS))
    frontend_url = _settings.FRONTEND_URL
    invite_url = f"{frontend_url}/join?token={token}"

    # Fire branded invite email — non-blocking
    html = await render_db_template(
        "invite",
        {
            "name": body.name,
            "role": body.role,
            "login_url": invite_url,
            "temp_password": "",
        },
    ) or build_invite_html(
        artist_name=body.name,
        role=body.role,
        invite_url=invite_url,
        expires_in_hours=INVITE_EXPIRE_HOURS,
    )
    background_tasks.add_task(
        send_email,
        to=email,
        subject="You're Invited — Ryozan Studio Artist Portal",
        html_body=html,
        from_alias=FROM_ADMIN,
    )

    return InviteResponse(token=token, invite_url=invite_url)


@router.post("/register-invite", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register_via_invite(
    body: InviteRegisterRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    """
    Public: accept an invite link and set up an account.

    Works for both cases:
    - New account (email not in DB) — creates the user with invite-specified role
    - Existing pre-seeded account (email already in DB) — updates password and returns token

    Token carries pre-validated email, name, and role — no spoofing possible.
    """
    try:
        payload = jwt.decode(body.invite_token, _settings.SECRET_KEY, algorithms=[_settings.ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite link is invalid or has expired",
        ) from None

    if not payload.get("invite"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not an invite token")

    email: str = _normalize_email(payload["email"])
    name: str = payload["name"]
    role: str = payload.get("role", "collector")
    slug: str | None = payload.get("slug")

    if role not in ROLE_HIERARCHY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role in token")

    user = await find_user_by_email(db, email)

    if user:
        # Pre-seeded account — just update the password so they can log in
        if user.role == "collector" and not user.email_verified_at:
            user.google_id = None
        set_user_password(user, body.password, "invite")
        user.email_verified_at = datetime.now(UTC)
    else:
        # Brand new account
        user = User(
            email=email,
            name=name,
            role=role,
            slug=slug,
            booking_mode="mailto" if role == "artist" else None,
            email_verified_at=datetime.now(UTC),
        )
        set_user_password(user, body.password, "invite")
        db.add(user)
        await db.flush()

    # Fire welcome email -- non-blocking
    background_tasks.add_task(
        send_welcome_email,
        to_email=user.email,
        name=user.name or name,
        role=user.role,
    )

    record_successful_login(user, "invite")
    token = create_user_access_token(user)
    return Token(access_token=token)


# ── Register from booking ─────────────────────────────────────────────────


class RegisterFromBookingRequest(BaseModel):
    quote_token: str
    password: str = Field(min_length=8)


@router.post(
    "/register-from-booking",
    response_model=RegistrationPending,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def register_from_booking(
    body: RegisterFromBookingRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    """
    Public: create a collector account using a quote token.

    Looks up the quote -> booking to get the client's email and name,
    then sends mailbox-only setup. A public quote token never opens a session.
    """
    # Find quote by token
    result = await db.execute(select(Quote).where(Quote.token == body.quote_token))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quote not found for the provided token",
        )

    # Find associated booking
    booking_r = await db.execute(select(Booking).where(Booking.id == quote.booking_id))
    booking = booking_r.scalar_one_or_none()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found for this quote",
        )

    if not booking.guest_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email associated with this booking",
        )

    email = _normalize_email(booking.guest_email)
    name = booking.guest_name or "Client"

    # Check if account already exists
    existing = await find_user_by_email(db, email)
    if existing and (existing.role != "collector" or existing.email_verified_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    if existing:
        _queue_account_setup(background_tasks, existing)
        return RegistrationPending()

    # Create collector account
    user = User(
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        name=name,
        role="collector",
    )
    db.add(user)
    await db.flush()

    _queue_account_setup(background_tasks, user)
    return RegistrationPending()


# ── Google OAuth ──────────────────────────────────────────────────────────


class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token from frontend
    quote_token: str | None = None  # optional — link to an existing booking


def _verify_google_credential(credential: str) -> dict:
    """Verify a Google ID token for the Ryozan client and require a verified email."""
    #
    # The audience must be the Ryozan client ID rendered by the browser. An
    # explicit browser override is supported, otherwise the active Ryozan web
    # OAuth client is shared with the server-side Calendar flow.
    google_audience = _settings.GOOGLE_SIGNIN_CLIENT_ID or _settings.GOOGLE_CLIENT_ID
    if not google_audience:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )
    try:
        payload = google_id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            google_audience,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google credential",
        ) from None

    if payload.get("email_verified") is not True:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google credential",
        )
    return payload


async def _resolve_google_user(
    db: AsyncSession,
    credential: str,
    quote_token: str | None,
    background_tasks: BackgroundTasks,
) -> User:
    """Verify a Google ID token and return the account it authenticates.

    Sole owner of "which account does this Google credential belong to".
    Both the JSON entry point and the redirect entry point call this, so the
    popup flow and the installed-PWA flow can never drift apart.

    - Existing user  → that user (login)
    - New user       → a freshly created collector (register)
    - quote_token    → the email must match the booking's guest_email
    """

    payload = _verify_google_credential(credential)

    email_raw: str | None = payload.get("email")
    email = _normalize_email(email_raw) if email_raw else None
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account has no email",
        )

    google_sub: str = payload.get("sub", "")
    name: str = payload.get("name", email.split("@")[0])
    picture: str | None = payload.get("picture")

    # 2. If quote_token provided, verify the email matches the booking
    if quote_token:
        qt_result = await db.execute(select(Quote).where(Quote.token == quote_token))
        quote = qt_result.scalar_one_or_none()
        if quote:
            bk_result = await db.execute(select(Booking).where(Booking.id == quote.booking_id))
            booking = bk_result.scalar_one_or_none()
            if booking and booking.guest_email and _normalize_email(booking.guest_email) != email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Google account email does not match the booking email",
                )

    # 3. Find or create user. Prefer google_id lookup so account stays linked
    # if the user later changes their Google email. google_id carries a real
    # unique index, so that lookup cannot see duplicates; the email fallback
    # can, and goes through the duplicate-safe resolver.
    user = None
    if google_sub:
        from app.core.user_lookup import user_google_matches

        result = await db.execute(select(User).where(user_google_matches(google_sub)))
        user = result.scalar_one_or_none()
    if user is None:
        user = await find_user_by_email(db, email)

    if not user:
        # Create new collector account — random password since they use Google
        user = User(
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            name=name,
            role="collector",
            avatar_url=picture,
            google_id=google_sub or None,
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()
        # Fire welcome email for new Google sign-ups -- non-blocking
        background_tasks.add_task(
            send_welcome_email,
            to_email=user.email,
            name=user.name or "there",
            role=user.role,
        )
    else:
        # Backfill google_id on first Google sign-in for pre-existing accounts.
        if google_sub and not user.google_id:
            user.google_id = google_sub
        # Update avatar if they don't have one yet
        if picture and not user.avatar_url:
            user.avatar_url = picture

    # Google proves only its own email, not a different portal address linked
    # by staff. Retire preclaimant passwords before granting a collector session.
    if _normalize_email(user.email) == email and not user.email_verified_at:
        if user.role == "collector":
            user.password_hash = hash_password(secrets.token_urlsafe(32))
            record_password_setup(user)
            user.google_id = google_sub or None
        user.email_verified_at = datetime.now(UTC)
    require_verified_collector(user)

    record_successful_login(user, "google")
    # Clear forced-reset flag on Google sign-in (no password was set by admin)
    if user.must_reset_password:
        user.must_reset_password = False

    return user


@router.post("/google", response_model=Token)
@limiter.limit("10/minute")
async def google_auth(
    request: Request, body: GoogleAuthRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    """Authenticate (or register) via Google, returning a JWT to the caller.

    Used by Google's rendered button in its default `popup` ux_mode, which is
    what a normal browser tab gets.
    """
    user = await _resolve_google_user(db, body.credential, body.quote_token, background_tasks)
    return Token(access_token=create_user_access_token(user))


# ── Google sign-in by redirect (owns the installed-PWA path) ──────────────
#
# Replaces the previous `ux_mode: "redirect"` endpoint, which relied on
# Google's double-submit `g_csrf_token` cookie. That cookie is set cross-site,
# and Safari on iOS blocks exactly that, with a standalone PWA partitioning the
# cookie jar on top of it. So the check could never pass on the one platform
# the flow existed to serve, and every attempt bounced to ?gsi_error=csrf.
#
# This is the ordinary OAuth 2.0 authorization-code flow instead:
#
#   GET /auth/google/start            302 to Google, carrying a signed state
#   GET /auth/google/callback-signin  Google returns here with ?code&state
#
# Two things fall out of that, and both are the point:
#   * Legitimacy rides in a signed `state` parameter, not a cookie, so there is
#     nothing for Safari to block.
#   * Nothing here renders a button. Google's own button is only mandatory when
#     using google.accounts.id.renderButton, which draws it in an iframe you
#     cannot restyle. A plain navigation to /start can be triggered by any
#     control, so the portal keeps its own designed button.

_GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_GOOGLE_STATE_PURPOSE = "google_signin"
_GOOGLE_LINK_PURPOSE = "google_link"
_GOOGLE_STATE_EXPIRE_MINUTES = 10


def _google_signin_redirect_uri() -> str:
    """Absolute callback URL. Must match the console entry byte for byte."""
    return f"{_settings.API_PUBLIC_BASE_URL}/api/v1/auth/google/callback-signin"


def _portal_url(fragment_or_query: str) -> str:
    return f"{_settings.FRONTEND_URL}/portal{fragment_or_query}"


@router.get("/google/start", include_in_schema=False)
@limiter.limit("10/minute")
async def google_signin_start(request: Request, return_to: str | None = None):
    """Begin Google sign-in. Any control can link here; no Google button needed."""
    client_id = _settings.GOOGLE_SIGNIN_CLIENT_ID or _settings.GOOGLE_CLIENT_ID
    if not client_id or not _settings.GOOGLE_CLIENT_SECRET:
        return RedirectResponse(_portal_url("?gsi_error=unconfigured"), status_code=303)

    now = datetime.now(UTC)
    state = jwt.encode(
        {
            "purpose": _GOOGLE_STATE_PURPOSE,
            "nonce": secrets.token_urlsafe(16),
            "return_to": return_to or "",
            "iat": now,
            "exp": now + timedelta(minutes=_GOOGLE_STATE_EXPIRE_MINUTES),
        },
        _settings.SECRET_KEY,
        algorithm=_settings.ALGORITHM,
    )
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": _google_signin_redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            # Always show the chooser. Without it a shared device silently
            # reuses whichever Google account signed in last.
            "prompt": "select_account",
        }
    )
    return RedirectResponse(f"{_GOOGLE_AUTH_ENDPOINT}?{params}", status_code=303)


class GoogleLinkResponse(BaseModel):
    authorization_url: str


class GoogleLinkStatus(BaseModel):
    linked: bool


def _link_result(outcome: str) -> RedirectResponse:
    """Send a link attempt back to Connections, where it started."""
    return RedirectResponse(
        f"{_settings.FRONTEND_URL}/dashboard?portal=hub&workflow=connections&google_signin={outcome}",
        status_code=303,
    )


@router.get("/google/link", response_model=GoogleLinkResponse)
@limiter.limit("10/minute")
async def google_link_start(request: Request, current_user: User = Depends(require_role("assistant"))):
    """Begin linking a Google login to the signed-in account.

    Google sign-in finds an account by its linked Google ID first, then by email,
    and creates a collector when neither matches. Staff whose Google address
    differs from their portal address therefore landed on a fresh collector
    account. Linking from inside their real session attaches the Google ID here,
    so every later Google sign-in resolves to this account.
    """
    client_id = _settings.GOOGLE_SIGNIN_CLIENT_ID or _settings.GOOGLE_CLIENT_ID
    if not client_id or not _settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google sign-in is not configured")

    now = datetime.now(UTC)
    state = jwt.encode(
        {
            "purpose": _GOOGLE_LINK_PURPOSE,
            "sub": str(current_user.id),
            "nonce": secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + timedelta(minutes=_GOOGLE_STATE_EXPIRE_MINUTES),
        },
        _settings.SECRET_KEY,
        algorithm=_settings.ALGORITHM,
    )
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": _google_signin_redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return GoogleLinkResponse(authorization_url=f"{_GOOGLE_AUTH_ENDPOINT}?{params}")


@router.get("/google/link/status", response_model=GoogleLinkStatus)
async def google_link_status(current_user: User = Depends(get_current_user)):
    """Whether a Google login is linked to the signed-in account."""
    return GoogleLinkStatus(linked=bool(current_user.google_id))


async def _finish_google_link(db: AsyncSession, user_id: object, id_token_str: str) -> RedirectResponse:
    """Attach the verified Google ID to the account named in the signed state."""
    try:
        payload = _verify_google_credential(id_token_str)
        google_sub = str(payload.get("sub") or "")
        target_id = UUID(str(user_id))
    except (HTTPException, ValueError, TypeError):
        return _link_result("error")
    if not google_sub:
        return _link_result("error")

    target = (await db.execute(select(User).where(User.id == target_id))).scalar_one_or_none()
    # This is the staff identity-repair flow. Historical public collector
    # sessions could mint link state before this mailbox protection existed;
    # never let that state reattach a preclaimant after mailbox redemption.
    if target is None or target.role == "collector":
        return _link_result("error")
    if target.google_id == google_sub:
        return _link_result("linked")

    # google_id is unique: release it from whichever account holds it (usually the
    # collector a mismatched Google sign-in created) before attaching it here.
    from app.core.user_lookup import user_google_matches

    holder = (await db.execute(select(User).where(user_google_matches(google_sub)))).scalar_one_or_none()
    if holder is not None and holder.id == target.id:
        return _link_result("linked")
    if holder is not None and holder.role != "collector":
        return _link_result("error")
    if holder is not None:
        holder.google_id = None
        await db.flush()
        logger.info("google link: moved Google login from user %s to user %s", holder.id, target.id)

    target.google_id = google_sub
    await db.flush()
    logger.info("google link: linked Google login to user %s", target.id)
    return _link_result("linked")


@router.get("/google/callback-signin", include_in_schema=False)
@limiter.limit("10/minute")
async def google_signin_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Google returns here. Exchange the code, then hand back a session.

    Every failure answers with a redirect rather than JSON: the browser is
    mid-navigation, so an error body would leave the user on a blank page with
    no way back. The reason travels as ?gsi_error= so the gate can say what
    happened.
    """
    if error or not code or not state:
        return RedirectResponse(_portal_url("?gsi_error=cancelled"), status_code=303)

    try:
        claims = jwt.decode(state, _settings.SECRET_KEY, algorithms=[_settings.ALGORITHM])
        if claims.get("purpose") not in {_GOOGLE_STATE_PURPOSE, _GOOGLE_LINK_PURPOSE}:
            raise JWTError("wrong purpose")
    except JWTError:
        return RedirectResponse(_portal_url("?gsi_error=state"), status_code=303)
    linking = claims.get("purpose") == _GOOGLE_LINK_PURPOSE

    client_id = _settings.GOOGLE_SIGNIN_CLIENT_ID or _settings.GOOGLE_CLIENT_ID
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                _GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": _settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": _google_signin_redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
        token_response.raise_for_status()
        id_token_str = token_response.json().get("id_token")
    except (httpx.HTTPError, ValueError):
        logger.warning("google signin: token exchange failed")
        if linking:
            return _link_result("error")
        return RedirectResponse(_portal_url("?gsi_error=exchange"), status_code=303)

    if not id_token_str:
        logger.warning("google signin: token response carried no id_token")
        if linking:
            return _link_result("error")
        return RedirectResponse(_portal_url("?gsi_error=exchange"), status_code=303)

    if linking:
        return await _finish_google_link(db, claims.get("sub"), id_token_str)

    try:
        user = await _resolve_google_user(db, id_token_str, None, background_tasks)
    except HTTPException:
        return RedirectResponse(_portal_url("?gsi_error=invalid"), status_code=303)

    # The session rides in the fragment, which browsers never transmit to a
    # server. The app consumes it and strips it from history immediately.
    token = create_user_access_token(user)
    return RedirectResponse(_portal_url(f"#gsi_token={token}"), status_code=303)
