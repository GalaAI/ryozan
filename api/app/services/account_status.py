"""Nonsecret credential lifecycle evidence, owned by successful auth writes.

Existing security helpers own hashing and verification, not credential provenance.
This service adds explicit lifecycle evidence without inspecting a stored hash or
logging credentials; historical rows remain unknown until an observed event.
"""

from datetime import UTC, datetime
from typing import Literal

from app.core.security import hash_password
from app.models.user import User


def set_user_password(user: User, password: str, method: Literal["change", "reset", "invite"]) -> None:
    user.password_hash = hash_password(password)
    user.must_reset_password = False
    user.password_state = "user_set"
    user.password_changed_at = datetime.now(UTC)
    user.password_change_method = method


def record_password_setup(user: User) -> None:
    """Call when replacing a credential with a generated/temporary credential."""
    user.password_state = "setup_required"
    user.password_changed_at = datetime.now(UTC)
    user.password_change_method = "setup"


def record_successful_login(user: User, method: Literal["password", "google", "invite"]) -> None:
    # Using a temporary credential proves login, never password ownership.
    if user.must_reset_password and not user.password_state:
        user.password_state = "setup_required"
    user.last_login_at = datetime.now(UTC)
    user.last_login_method = method


def password_status(user) -> str:
    if user.must_reset_password or user.password_state == "setup_required":
        return "setup_required"
    if user.password_state == "user_set" and user.password_changed_at:
        return "user_set"
    return "historical_unknown"
