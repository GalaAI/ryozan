"""One-time code check for the waiver check-in lookup.

The check-in lookup is public (a QR at the counter, a link in a booking email), so anyone can type any
email or phone. A match only proves the contact exists on file; it must never reveal the booking or the
contact facts on file until the person proves they hold that inbox or phone. That proof is a six digit
code sent to the contact itself.

The challenge is stateless: a signed token carries the normalized contact, a hash of the code and an
expiry, so no table or migration is needed. Wrong guesses are counted in process per challenge nonce;
with a single API instance that caps guessing at MAX_ATTEMPTS per issued code, and the route rate limit
caps how many codes one client can request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

CODE_TTL_SECONDS = 10 * 60
MAX_ATTEMPTS = 5
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# nonce -> wrong guesses (and a used marker). Pruned as challenges expire.
_attempts: dict[str, tuple[int, float]] = {}
_used: dict[str, float] = {}


@dataclass(frozen=True)
class Contact:
    kind: str  # "email" | "phone"
    value: str  # lowercased email, or the last ten digits of a US number


class ChallengeError(ValueError):
    """The code or the challenge is not valid. The message is safe to show the client."""


def normalize_contact(raw: str) -> Contact | None:
    text = (raw or "").strip()
    if "@" in text:
        email = text.lower()
        return Contact("email", email) if _EMAIL_RE.match(email) else None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return Contact("phone", digits) if len(digits) == 10 else None


def phone_digits(raw: str | None) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def mask_contact(contact: Contact) -> str:
    dot = "•"
    if contact.kind == "phone":
        return f"({dot * 3}) {dot * 3} {contact.value[-4:]}"
    name, _, domain = contact.value.partition("@")
    return f"{name[:1]}{dot * max(len(name) - 1, 2)}@{domain}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _code_hash(secret: str, nonce: str, code: str) -> str:
    return hmac.new(secret.encode(), f"checkin-code:{nonce}:{code}".encode(), hashlib.sha256).hexdigest()


def _sign(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode(), f"checkin-challenge:{body}".encode(), hashlib.sha256).digest())


def _prune(now: float) -> None:
    for store in (_attempts, _used):
        for key in [k for k, v in store.items() if (v[1] if isinstance(v, tuple) else v) < now]:
            store.pop(key, None)


def issue_challenge(secret: str, contact: Contact, *, now: float | None = None) -> tuple[str, str]:
    """Return (challenge_token, code). The caller sends the code to the contact and never returns it."""
    now = time.time() if now is None else now
    code = f"{secrets.randbelow(1_000_000):06d}"
    nonce = secrets.token_urlsafe(12)
    body = _b64(
        json.dumps(
            {
                "k": contact.kind,
                "v": contact.value,
                "n": nonce,
                "h": _code_hash(secret, nonce, code),
                "e": int(now + CODE_TTL_SECONDS),
            },
            separators=(",", ":"),
        ).encode()
    )
    return f"{body}.{_sign(secret, body)}", code


def verify_challenge(secret: str, token: str, code: str, *, now: float | None = None) -> Contact:
    """Return the verified contact or raise ChallengeError. Each challenge verifies at most once."""
    now = time.time() if now is None else now
    _prune(now)
    body, _, signature = (token or "").partition(".")
    if not body or not hmac.compare_digest(signature, _sign(secret, body)):
        raise ChallengeError("That code has expired. Ask for a new one.")
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, TypeError) as exc:
        raise ChallengeError("That code has expired. Ask for a new one.") from exc
    nonce, expires = str(claims.get("n", "")), float(claims.get("e", 0))
    if expires < now or nonce in _used:
        raise ChallengeError("That code has expired. Ask for a new one.")
    tries, _ = _attempts.get(nonce, (0, expires))
    if tries >= MAX_ATTEMPTS:
        raise ChallengeError("Too many tries. Ask for a new code.")
    clean = re.sub(r"\D", "", code or "")
    if not hmac.compare_digest(_code_hash(secret, nonce, clean), str(claims.get("h", ""))):
        _attempts[nonce] = (tries + 1, expires)
        raise ChallengeError("That code doesn't match.")
    _used[nonce] = expires
    _attempts.pop(nonce, None)
    return Contact(str(claims["k"]), str(claims["v"]))
