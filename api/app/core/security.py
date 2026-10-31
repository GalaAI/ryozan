"""
Authentication and authorization.

JWT-based auth matching the existing ryozan_users table.
Role hierarchy: founder > admin > artist > assistant > collector (client)

Uses bcrypt directly (passlib is abandoned and incompatible with bcrypt 4.1+).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")
# Same scheme for routes that also serve anonymous callers: no token yields None.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login", auto_error=False)

# Role hierarchy — higher index = more privileges
ROLE_HIERARCHY = ["collector", "assistant", "artist", "admin", "founder"]
ROLE_RANK = {role: i for i, role in enumerate(ROLE_HIERARCHY)}

# bcrypt only ever consults the first 72 bytes of a password. bcrypt 4.x
# truncated silently; bcrypt 5.0 raises ValueError instead, which surfaced in
# production as an HTTP 500 on login and registration for any password longer
# than 72 bytes. Slicing the encoded bytes here reproduces the 4.x behaviour
# byte for byte, so every hash already stored keeps verifying.
#
# Do NOT "fix" this by pre-hashing (e.g. SHA-256 then bcrypt): that changes the
# input bcrypt sees and would invalidate every credential in the database.
BCRYPT_MAX_BYTES = 72

# Token type discriminator. Access tokens predate this claim, so an absent
# `typ` is treated as an access token; anything else is refused by
# get_current_user. A reset token therefore can never act as a session token,
# and an access token can never be redeemed as a reset token.
ACCESS_TOKEN_TYPE = "access"
PASSWORD_RESET_TOKEN_TYPE = "password_reset"
# Reset-link lifetime is configuration, not a constant: it is owned by
# Settings.PASSWORD_RESET_EXPIRE_MINUTES (24 hours by default) and overridable
# by environment. The name stays importable here because callers already read
# it from this module.
PASSWORD_RESET_EXPIRE_MINUTES = settings.PASSWORD_RESET_EXPIRE_MINUTES
BOOKY_CAPABILITY_TOKEN_TYPE = "booky_capability"
BOOKY_CAPABILITY_EXPIRE_MINUTES = 10


def _bcrypt_bytes(password: str) -> bytes:
    """Encode a password to exactly the bytes bcrypt will consider.

    Slicing happens on the encoded bytes, never on the str, and the result is
    handed straight to bcrypt, so a cut through the middle of a multi-byte
    character is harmless (nothing is ever decoded back).
    """
    return password.encode("utf-8")[:BCRYPT_MAX_BYTES]


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_bytes(plain), (hashed or "").encode("utf-8"))
    except ValueError:
        # Malformed or empty stored hash: treat as a failed login, not a 500.
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_user_access_token(user, expires_delta: timedelta | None = None) -> str:
    """Create a session token bound to the user's current password state."""
    return create_access_token(
        {
            "sub": str(user.id),
            "role": user.role,
            "typ": ACCESS_TOKEN_TYPE,
            "pwf": password_hash_fingerprint(user.password_hash),
            "tv": int(getattr(user, "token_version", 0) or 0),
        },
        expires_delta=expires_delta,
    )


def create_booky_capability_token(user_id, inquiry_id: str) -> str:
    """Mint a short-lived Booky capability bound to one account and inquiry."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "typ": BOOKY_CAPABILITY_TOKEN_TYPE,
        "inquiry": inquiry_id,
        "jti": secrets.token_urlsafe(18),
        "iat": now,
        "exp": now + timedelta(minutes=BOOKY_CAPABILITY_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_booky_capability_token(token: str, inquiry_id: str) -> UUID:
    """Validate a Booky capability without accepting it as a login token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise ValueError("Booky capability failed signature or expiry validation") from None
    if payload.get("typ") != BOOKY_CAPABILITY_TOKEN_TYPE:
        raise ValueError("token is not a Booky capability")
    if payload.get("inquiry") != inquiry_id or not payload.get("sub"):
        raise ValueError("Booky capability is not bound to this inquiry")
    try:
        return UUID(str(payload["sub"]))
    except (TypeError, ValueError):
        raise ValueError("Booky capability account is invalid") from None


# ── Password reset tokens ──────────────────────────────────────────────────
#
# Single-use without a new table or column: the token carries a fingerprint of
# the user's *current* password hash. Setting a new password changes the hash,
# which changes the fingerprint, which retires every token minted against the
# old one. Redeeming a token, or changing the password by any other route,
# therefore invalidates outstanding reset links.


def password_hash_fingerprint(password_hash: str) -> str:
    """Non-reversible fingerprint of a stored bcrypt hash.

    The bcrypt hash itself never leaves the server: only this digest is
    embedded in the token, and it is compared server-side on redemption.
    """
    return hashlib.sha256((password_hash or "").encode("utf-8")).hexdigest()[:32]


def create_password_reset_token(user_id, password_hash: str, *, email: str | None = None) -> str:
    """Mint a short-lived, single-use, reset-only token signed with SECRET_KEY."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "typ": PASSWORD_RESET_TOKEN_TYPE,
        "pwf": password_hash_fingerprint(password_hash),
        "iat": now,
        "exp": now + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
    }
    if email is not None:
        from app.models.user import normalize_email

        payload["email"] = normalize_email(email)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_password_reset_token(token: str) -> dict:
    """Validate a reset token's signature, expiry, type and shape.

    Raises ValueError for anything wrong. The message never contains the token
    and is never surfaced verbatim to the caller.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise ValueError("reset token failed signature or expiry validation") from None
    if payload.get("typ") != PASSWORD_RESET_TOKEN_TYPE:
        raise ValueError("token is not a password reset token")
    if not payload.get("sub") or not payload.get("pwf"):
        raise ValueError("reset token is missing required claims")
    return payload


def reset_token_matches_hash(payload: dict, password_hash: str) -> bool:
    """Constant-time check that the token was minted against the current hash."""
    return secrets.compare_digest(
        str(payload.get("pwf", "")),
        password_hash_fingerprint(password_hash),
    )


def reset_token_proves_email(payload: dict, user) -> bool:
    """Reject legacy public setup tokens and tokens for a previous address."""
    from app.models.user import normalize_email

    return bool(payload.get("email")) and normalize_email(payload["email"]) == normalize_email(user.email)


def require_verified_collector(user) -> None:
    if user.role == "collector" and not getattr(user, "email_verified_at", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email using a password recovery link or Continue with Google before signing in.",
        )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    """Decode JWT and return the authenticated user row."""
    from app.models.user import BTTCOUser  # deferred to avoid circular import

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        # Only password-bound session tokens open a session. Requiring both
        # claims intentionally expires legacy tokens once when this ships.
        if payload.get("typ") != ACCESS_TOKEN_TYPE or not payload.get("pwf"):
            raise credentials_exception
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        # Convert string user_id to UUID for asyncpg compatibility
        try:
            user_uuid = UUID(user_id)
        except (ValueError, TypeError):
            raise credentials_exception from None
    except JWTError:
        raise credentials_exception from None

    result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    if not reset_token_matches_hash(payload, user.password_hash):
        raise credentials_exception
    # Signed out: the token predates the user's current session generation.
    if int(payload.get("tv", 0) or 0) != int(user.token_version or 0):
        raise credentials_exception
    require_verified_collector(user)
    return user


async def get_optional_current_user(
    token: str | None = Depends(oauth2_scheme_optional),
    db: AsyncSession = Depends(get_db),
):
    """Return the signed-in user, or None for anonymous or unusable sessions.

    Runs the full get_current_user validation; any token it would reject
    (expired, signed out, unverified collector) is treated as anonymous.
    """
    if not token:
        return None
    try:
        return await get_current_user(token=token, db=db)
    except HTTPException:
        return None


def require_role(minimum_role: str):
    """
    Dependency factory: ensures the current user has at least the given role.

    Usage:
        @router.get("/admin-only", dependencies=[Depends(require_role("admin"))])
    """
    min_index = ROLE_HIERARCHY.index(minimum_role)

    async def _check(current_user=Depends(get_current_user)):
        user_index = ROLE_HIERARCHY.index(current_user.role)
        if user_index < min_index:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum_role} role or higher",
            )
        return current_user

    return _check
