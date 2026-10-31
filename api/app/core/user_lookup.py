"""Duplicate-tolerant email lookups for `ryozan_users`.

Why this module exists
----------------------
`ryozan_users` has never had a unique index on email, and several account
creation paths stored the address exactly as the client typed it. Two rows
that differ only by capitalization are therefore possible, and production has
two such pairs.

Every case-insensitive lookup used to end in `scalar_one_or_none()`, which
raises `MultipleResultsFound` on more than one row. That escaped FastAPI as an
HTTP 500, so the affected people could not log in at all — not a wrong-password
401, a hard server error on every attempt.

This module is the single owner of "find the user for this email". It never
raises on duplicates. It picks a deterministic winner instead.

Winner selection rule
---------------------
Never "oldest row wins". Picking the wrong row of a pair converts a fixable
500 into a permanent, silent 401 — the person stays locked out and the error
now looks like their fault.

So authentication comes first:

1. `authenticate_by_email` tries the supplied password against **every**
   candidate and returns the row whose hash actually verifies. If a duplicate
   pair holds one live password and one placeholder, the live one wins by
   definition.
   If every candidate is an unclaimed artist (`must_reset_password=True`),
   the documented first-login credential is accepted. A mixed duplicate set
   can never enter through that fallback.
2. When no password is in play (Google sign-in, forgot-password, registration
   conflict checks), candidates are ranked by how usable the account is:
     a. has `google_id`            — can authenticate via Google
     b. has a real bcrypt hash     — can authenticate with a password
     c. is not `must_reset_password` — a fully activated account
     d. oldest `created_at`        — the original row, which owns the history
     e. lowest `id` as a text tie-break — total order, so the choice is stable
        across processes and reruns

The ranking is a total order, so the same duplicate set always resolves to the
same row no matter which replica serves the request.

This is a safety net, not a fix. The duplicates still need merging with
`scripts/dedupe_user_emails.py` before the unique index can be created.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.core.security import password_hash_fingerprint, verify_password
from app.models.user import User, UserIdentityAlias, normalize_email

logger = logging.getLogger(__name__)

# Rows created by SMS/WhatsApp intake carry literal placeholder strings rather
# than a bcrypt hash. bcrypt hashes always start with the "$2" version marker.
_BCRYPT_PREFIX = "$2"

# Invited artists retain must_reset_password=True until they claim the account.
# This temporary credential is intentionally narrow: it is never accepted for
# an established account, a non-artist, or a mixed duplicate set.
FIRST_LOGIN_ARTIST_PASSWORD = "tattoo"

# Sorts after every real timestamp, so a row with no created_at never wins the
# tie-break by accident.
_MAX_TIME = datetime.max.replace(tzinfo=UTC)


def user_email_matches(email: str):
    """Case-insensitive WHERE clause against the normalized address."""
    normalized = normalize_email(email)
    return or_(
        func.lower(User.email) == normalized,
        select(UserIdentityAlias.id)
        .where(UserIdentityAlias.user_id == User.id, UserIdentityAlias.email == normalized)
        .exists(),
    )


def user_google_matches(subject: str):
    """Resolve a verified provider subject, including explicitly merged identities."""
    return or_(
        User.google_id == subject,
        select(UserIdentityAlias.id)
        .where(UserIdentityAlias.user_id == User.id, UserIdentityAlias.google_id == subject)
        .exists(),
    )


def has_usable_password(user: User) -> bool:
    return bool(user.password_hash) and user.password_hash.startswith(_BCRYPT_PREFIX)


def _rank_key(user: User) -> tuple:
    """Sort key implementing the documented winner rule. Lower sorts first."""
    return (
        0 if user.google_id else 1,
        0 if has_usable_password(user) else 1,
        1 if user.must_reset_password else 0,
        user.created_at or _MAX_TIME,
        str(user.id),
    )


def rank_candidates(users: list[User]) -> list[User]:
    """Most-authenticatable first, deterministically."""
    return sorted(users, key=_rank_key)


def _warn_on_duplicates(email: str, users: list[User]) -> None:
    if len(users) > 1:
        correlation = hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()[:12]
        logger.warning(
            "duplicate ryozan_users rows: count=%d correlation=%s; run scripts/dedupe_user_emails.py",
            len(users),
            correlation,
        )


async def find_users_by_email(db: AsyncSession, email: str) -> list[User]:
    """Every row matching this email, case-insensitively. Never raises."""
    result = await db.execute(select(User).where(user_email_matches(email)))
    return list(result.scalars().all())


async def find_user_by_email(db: AsyncSession, email: str) -> User | None:
    """The best row for this email, or None. Never raises on duplicates."""
    users = await find_users_by_email(db, email)
    if not users:
        return None
    _warn_on_duplicates(email, users)
    return rank_candidates(users)[0]


async def authenticate_by_email(db: AsyncSession, email: str, password: str) -> User | None:
    """Return the row this password actually authenticates, else None.

    Checks every candidate rather than the first, so a duplicate pair where
    only the second row holds the person's real password still logs in.
    """
    users = await find_users_by_email(db, email)
    if not users:
        return None
    _warn_on_duplicates(email, users)
    ranked = rank_candidates(users)
    for user in ranked:
        if verify_password(password, user.password_hash):
            return user

    # A password change revokes every rescued password without deleting the
    # verified Google/email aliases. Old duplicate session tokens remain invalid.
    aliases = (
        (
            await db.execute(
                select(UserIdentityAlias)
                .options(
                    load_only(
                        UserIdentityAlias.user_id, UserIdentityAlias.password_hash, UserIdentityAlias.password_binding
                    )
                )
                .where(
                    UserIdentityAlias.user_id.in_([user.id for user in ranked]),
                    UserIdentityAlias.email == normalize_email(email),
                )
            )
        )
        .scalars()
        .all()
    )
    for user in ranked:
        for alias in aliases:
            if (
                alias.user_id == user.id
                and secrets.compare_digest(alias.password_binding, password_hash_fingerprint(user.password_hash))
                and verify_password(password, alias.password_hash)
            ):
                return user

    first_login_only = all(user.role == "artist" and user.must_reset_password for user in users)
    if first_login_only and secrets.compare_digest(password, FIRST_LOGIN_ARTIST_PASSWORD):
        return ranked[0]
    return None
