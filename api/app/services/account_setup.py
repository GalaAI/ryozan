"""Mailbox-only collector setup using the existing single-use recovery flow."""

from app.core.config import get_settings
from app.core.security import PASSWORD_RESET_EXPIRE_MINUTES, create_password_reset_token
from app.services.email import send_password_reset_email


async def send_account_setup_email(*, user_id, email: str, password_hash: str, name: str) -> bool:
    # Never return this token to a public submitter or persist it in an outbox.
    token = create_password_reset_token(user_id, password_hash, email=email)
    return await send_password_reset_email(
        to_email=email,
        name=name or "there",
        reset_url=f"{get_settings().FRONTEND_URL}/reset-password?token={token}",
        expires_in_minutes=PASSWORD_RESET_EXPIRE_MINUTES,
    )
