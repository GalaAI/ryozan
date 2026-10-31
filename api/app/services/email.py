"""
Email service -- SendGrid API primary when configured, with Resend and Gmail SMTP fallback.

Uses httpx for async provider API calls. Falls back to aiosmtplib
Gmail SMTP if hosted API providers are not configured.

Rate limiting:
  - Artists: ARTIST_DAILY_EMAIL_CAP emails/day (default 20)
  - Founder/Admin: unlimited
  - System emails (no sender_id): unlimited (booking confirmations, etc.)

Email types:
  - Aftercare: triggered on waiver submit
  - Document send: magic link to sign/fill a form
  - Reminder: follow-up if document hasn't been signed
  - Booking confirmation: sent to client after public booking submit
  - Shop notification: sent to shop/artist on new booking request
  - Welcome: sent on registration
"""

import base64
import json
import logging
import re as _re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from uuid import UUID

import aiosmtplib
import httpx
from sqlalchemy import or_, select

from app.constants.timezones import SHOP_TIMEZONE
from app.core.config import get_settings
from app.core.routes import ROUTES
from app.services.email_foundry import (
    STARSEED_TILE_URL,
    FoundryEmail,
    render_foundry_email,
    studio_address_html,
    with_current_starseed,
)

# ── Eastern timezone for client-facing times ─────────────────────────────


def _format_time_12hr(time_str: str | None) -> str | None:
    """Convert a time string (e.g. '15:00', '3:00 PM') to 12-hour format."""
    if not time_str:
        return None
    # Already in 12hr format
    if "AM" in time_str.upper() or "PM" in time_str.upper():
        return time_str
    # Parse 24hr "HH:MM" or "H:MM"
    m = _re.match(r"^(\d{1,2}):(\d{2})$", time_str.strip())
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        period = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{mn:02d} {period}"
    return time_str


def _format_datetime_eastern(dt: datetime | None = None) -> str:
    """Format a datetime to 'Month DD, YYYY at H:MM AM/PM EST'."""
    if dt is None:
        dt = datetime.now(UTC)
    eastern = dt.astimezone(SHOP_TIMEZONE)
    return eastern.strftime("%B %d, %Y at %I:%M %p EST")


# ── Per-artist daily email rate limiter (in-memory, resets daily) ──────────
# Key: (user_id, date) → count. No persistence needed -- resets on deploy/restart.
_daily_email_counts: dict[tuple[str, str], int] = defaultdict(int)
ARTIST_DAILY_EMAIL_CAP = 20  # marketing emails per artist per day
UNCAPPED_ROLES = {"founder", "admin"}  # these roles bypass the cap

logger = logging.getLogger("bttco.email")

# ── Brand assets (hosted on ryozan.app) ─────────────────────────
LOGO_URL = "https://ryozan.app/assets/images/logo-round.png"
FONT_URL = "https://ryozan.app/assets/fonts/KCTallboy.otf"
SCROLL_REQUEST_TEMPLATE_MARKER = "BTTCO-EMAIL-FOUNDRY-ARTIST-SCROLL-V1"
FOUNDRY_EMAIL_ASSET_BASE = "https://app.ryozan.app/dependencies/@bttco/email-foundry/1.0.0/assets"

# ── Email sender identities ───────────────────────────────────────────────
# Use these constants as the `from_alias` param in send_email()
FROM_SHOP = "noreply"  # noreply@ryozan.app — booking confirms, waivers, welcome
FROM_ADMIN = "admin"  # admin@ryozan.app — invites, broadcasts, shop announcements
FROM_SERVICE = "service"  # service@ryozan.app — payments, receipts, Stripe notifications

_FROM_MAP = {
    "noreply": ("Ryozan Studio", "noreply@ryozan.app"),
    "admin": ("Ryozan Studio", "admin@ryozan.app"),
    "service": ("Ryozan Studio", "service@ryozan.app"),
}
# Tuple: (display_name, from_email). Replies go to settings.SHOP_EMAIL unless
# the caller passes reply_to; with neither, no Reply-To header is set.


def _email_head() -> str:
    """
    Shared <head> block with @font-face for KC Tallboy.
    Apple Mail / iOS Mail render @font-face.  Gmail/Outlook ignore it
    and fall back to Oswald (via Google Fonts link) or Arial Narrow.
    """
    return f"""<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <!--[if !mso]><!-->
  <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;700&display=swap" rel="stylesheet">
  <style>
    @font-face {{
      font-family: 'KC Tallboy';
      src: url('{FONT_URL}') format('opentype');
      font-weight: normal;
      font-style: normal;
    }}
  </style>
  <!--<![endif]-->
</head>"""


def _build_header(subtitle: str) -> str:
    """
    Shared email header — logo served from ryozan.app (works in all clients),
    shop name in KC Tallboy with Oswald / Arial Narrow fallbacks.
    """
    return f"""
  <!-- ══ HEADER ══════════════════════════════════════════════════ -->
  <tr>
    <td style="background:linear-gradient(180deg,#1a1810 0%,#121312 100%);
               text-align:center;padding:36px 24px 24px;
               border-bottom:1px solid rgba(250,174,21,0.25);">
      <!-- Logo circle -->
      <div style="display:inline-block;width:76px;height:76px;
                  border-radius:50%;border:2px solid #faae15;
                  overflow:hidden;background:#1a1810;margin-bottom:16px;">
        <img src="{LOGO_URL}"
             alt="Ryozan Studio"
             width="76" height="76"
             style="display:block;border-radius:50%;object-fit:cover;">
      </div>
      <!-- Shop name -->
      <h1 style="margin:0 0 6px;font-size:22px;letter-spacing:4px;
                 color:#faae15;font-family:'KC Tallboy','Oswald','Arial Narrow',sans-serif;
                 font-weight:bold;text-transform:uppercase;">
        RYOZAN STUDIO
      </h1>
      <!-- Subtitle tag -->
      <p style="margin:0;font-size:11px;color:#b9ad96;
                letter-spacing:2px;text-transform:uppercase;">
        {subtitle}
      </p>
    </td>
  </tr>"""


@dataclass
class EmailAttachment:
    """A file to attach to an outbound email.

    Added so the owner's Artist Scroll archive can be delivered as an actual document
    rather than a link. Every provider path below encodes this the way that provider
    expects; `send_email`'s existing callers pass no attachments and are unaffected.
    """

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"

    def b64(self) -> str:
        return base64.b64encode(self.content).decode("ascii")


def check_email_rate(sender_id: str | None, sender_role: str | None) -> tuple[bool, int]:
    """
    Check if a sender is within their daily email cap.

    Returns (allowed, remaining). System emails (sender_id=None) always pass.
    Founder/admin roles are uncapped.
    """
    if not sender_id or not sender_role:
        return True, -1  # system email, uncapped
    if sender_role in UNCAPPED_ROLES:
        return True, -1
    today = date.today().isoformat()
    key = (sender_id, today)
    used = _daily_email_counts[key]
    remaining = max(0, ARTIST_DAILY_EMAIL_CAP - used)
    return remaining > 0, remaining


def _increment_email_count(sender_id: str | None) -> None:
    """Increment daily email counter for a sender."""
    if not sender_id:
        return
    today = date.today().isoformat()
    key = (sender_id, today)
    _daily_email_counts[key] += 1


async def render_db_template(slug: str, variables: dict[str, str]) -> str | None:
    """
    Look up an email template by slug in bttco_email_templates.

    If found and active, replace all {{key}} placeholders with values
    from `variables` and return the rendered HTML + subject tuple.
    Returns None if no active template exists (caller falls back to hardcoded).
    """
    try:
        from app.core.database import async_session
        from app.models.email_template import EmailTemplate

        async with async_session() as session:
            result = await session.execute(
                select(EmailTemplate).where(
                    EmailTemplate.slug == slug,
                    EmailTemplate.is_active.is_(True),
                )
            )
            tpl = result.scalar_one_or_none()

        if not tpl:
            return None

        html = tpl.html_body
        if not html or len(html.strip()) < 200:
            logger.warning(
                "render_db_template(%s): stored html_body is empty or trivially short (%d chars) — using hardcoded fallback",
                slug,
                len(html) if html else 0,
            )
            return None

        for key, value in variables.items():
            html = html.replace("{{" + key + "}}", str(value))
        return html
    except Exception as exc:
        logger.warning("render_db_template(%s) failed, using hardcoded: %s", slug, exc)
        return None


async def render_transactional_email(
    slug: str,
    variables: dict[str, object],
    *,
    fallback_subject: str,
    fallback_html: str,
    legacy_slug: str | None = None,
) -> FoundryEmail:
    """Prefer the locked Email Foundry dependency for supported transaction mail."""

    foundry_email = render_foundry_email(slug, variables)
    if foundry_email:
        return foundry_email

    legacy_html = await render_db_template(
        legacy_slug or slug,
        {key: str(value or "") for key, value in variables.items()},
    )
    return FoundryEmail(
        slug=slug,
        subject=fallback_subject,
        html_body=legacy_html or fallback_html,
    )


async def send_email(
    to: str,
    subject: str,
    html_body: str,
    reply_to: str | None = None,
    tags: list[str] | None = None,
    sender_id: str | None = None,
    sender_role: str | None = None,
    from_alias: str | None = None,
    courier_message_id: str | None = None,
    courier_attempt: int | None = None,
    attachments: list[EmailAttachment] | None = None,
) -> bool:
    """
    Send an HTML email via SendGrid, Resend, or Gmail SMTP fallback.

    Returns True on success, False on failure (never raises -- email
    is a side effect, not a critical path).

    Args:
        to: Recipient email address
        subject: Email subject line
        html_body: Full HTML body
        reply_to: Optional reply-to address (overrides alias default)
        tags: Optional provider analytics tags (e.g. ["aftercare", "booking"])
        sender_id: User ID of the person triggering the email (for rate limiting)
        sender_role: Role of the sender (artist, admin, founder -- for cap bypass)
        from_alias: Sender identity key (FROM_SHOP, FROM_ADMIN, FROM_SERVICE).
                    Defaults to FROM_SHOP (noreply@ryozan.app).
        courier_message_id: Optional ID of the Courier Message generating this email.
        courier_attempt: Exact Booky Courier provider attempt for delivery reconciliation.
        attachments: Optional files to attach. Supported on all three provider paths.
    """
    # Every outgoing body gets the current starseed ground, including saved database
    # templates and older renderers that still point at the unversioned or package tile.
    html_body = with_current_starseed(html_body)

    # Rate limit check for artist marketing emails
    allowed, remaining = check_email_rate(sender_id, sender_role)
    if not allowed:
        logger.warning("Email rate limit hit for user %s (cap: %d/day)", sender_id, ARTIST_DAILY_EMAIL_CAP)
        return False

    # Resolve sender identity
    alias_key = from_alias or FROM_SHOP
    display_name, from_email = _FROM_MAP.get(alias_key, _FROM_MAP["noreply"])
    settings = get_settings()
    effective_reply_to = reply_to or settings.SHOP_EMAIL.strip() or None

    # Dev/test redirect: route every outbound email to one readable inbox instead
    # of the real recipient. Hard-guarded to non-production so it can never hijack
    # real client mail. When active, it also overrides LOCAL_DEV_MODE's skip so the
    # email actually sends (the whole point is to receive it).
    test_redirect = (settings.EMAIL_TEST_REDIRECT or "").strip()
    redirect_active = bool(test_redirect) and settings.APP_ENV != "production"
    if redirect_active:
        original_to = to
        to = test_redirect
        subject = f"[TEST -> {original_to}] {subject}"
        logger.info("[EMAIL_TEST_REDIRECT] rewriting recipient %s -> %s", original_to, test_redirect)

    # Infer template name from tags (first tag wins, fallback to "general")
    template_name = (tags[0] if tags else None) or "general"

    if settings.LOCAL_DEV_MODE and not redirect_active:
        logger.info("[LOCAL_DEV_MODE] email send skipped. to=%s subject=%s", to, subject)
        await _log_email_send(
            to=to,
            subject=subject,
            template=template_name,
            from_alias=alias_key,
            resend_id=None,
            status="dev_skipped",
            error=None,
            courier_message_id=courier_message_id,
            courier_attempt=courier_attempt,
        )
        return True

    provider_message_id: str | None = None
    error_detail: str | None = None
    sent = False

    sendgrid_configured = bool(settings.SENDGRID_API_KEY and settings.SENDGRID_API_KEY.strip())
    resend_configured = bool(settings.RESEND_API_KEY and settings.RESEND_API_KEY.strip())
    smtp_configured = bool(settings.SMTP_USER and settings.SMTP_PASSWORD)

    if sendgrid_configured:
        sent, provider_message_id, error_detail = await _send_via_sendgrid(
            to,
            subject,
            html_body,
            effective_reply_to,
            tags,
            from_override=f"{display_name} <{from_email}>",
            attachments=attachments,
        )
        if not sent and resend_configured:
            logger.warning("SendGrid failed to %s (%s); attempting Resend fallback", to, error_detail)
            sent, provider_message_id, resend_error = await _send_via_resend(
                to,
                subject,
                html_body,
                effective_reply_to,
                tags,
                from_override=f"{display_name} <{from_email}>",
                attachments=attachments,
            )
            error_detail = None if sent else f"{error_detail}; {resend_error}" if resend_error else error_detail
        if not sent and smtp_configured:
            logger.warning("Hosted email providers failed to %s (%s); attempting SMTP fallback", to, error_detail)
            smtp_ok = await _send_via_smtp(to, subject, html_body, reply_to=effective_reply_to, attachments=attachments)
            if smtp_ok:
                sent = True
                provider_message_id = None
                error_detail = None
            else:
                join = f"{error_detail}; SMTP fallback failed" if error_detail else "SMTP fallback failed"
                error_detail = join
    elif resend_configured:
        sent, provider_message_id, error_detail = await _send_via_resend(
            to,
            subject,
            html_body,
            effective_reply_to,
            tags,
            from_override=f"{display_name} <{from_email}>",
            attachments=attachments,
        )
        if not sent and smtp_configured:
            logger.warning("Resend failed to %s (%s); attempting SMTP fallback", to, error_detail)
            smtp_ok = await _send_via_smtp(to, subject, html_body, reply_to=effective_reply_to, attachments=attachments)
            if smtp_ok:
                sent = True
                provider_message_id = None
                error_detail = None
            else:
                join = f"{error_detail}; SMTP fallback failed" if error_detail else "SMTP fallback failed"
                error_detail = join
    elif smtp_configured:
        sent = await _send_via_smtp(to, subject, html_body, reply_to=effective_reply_to, attachments=attachments)
        error_detail = None if sent else "SMTP send failed"
    else:
        error_detail = (
            "No email provider configured (set SENDGRID_API_KEY, RESEND_API_KEY, or SMTP_USER and SMTP_PASSWORD)"
        )
        logger.warning("Email skipped for %s: %s", to, error_detail)

    # Write to email log (fire-and-forget, never blocks the caller)
    try:
        await _log_email_send(
            to=to,
            subject=subject,
            template=template_name,
            from_alias=alias_key,
            resend_id=provider_message_id,
            status="sent" if sent else "failed",
            error=error_detail,
            courier_message_id=courier_message_id,
            courier_attempt=courier_attempt,
        )
    except Exception as log_exc:
        logger.warning("Email log write failed: %s", log_exc)

    if sent:
        _increment_email_count(sender_id)
    return sent


async def _log_email_send(
    to: str,
    subject: str,
    template: str,
    from_alias: str,
    resend_id: str | None,
    status: str,
    error: str | None,
    courier_message_id: str | None = None,
    courier_attempt: int | None = None,
) -> None:
    """Write a record to bttco_email_log. Uses its own short-lived session."""
    try:
        from app.core.database import async_session
        from app.models.email_log import EmailLog

        async with async_session() as session:
            log = EmailLog(
                to_email=to,
                subject=subject,
                template=template,
                from_alias=from_alias,
                resend_id=resend_id,
                status=status,
                error=error,
                courier_message_id=courier_message_id,
                courier_attempt=courier_attempt,
            )
            session.add(log)
            await session.commit()
    except Exception as exc:
        logger.warning("_log_email_send db error: %s", exc)


BOOKY_COURIER_STALE_MINUTES = 10
BOOKY_COURIER_MAX_ATTEMPTS = 5
BOOKY_COURIER_STAFF_RECOVERY_MAX_ATTEMPTS = 3


def _booky_courier_metadata(message) -> dict:
    try:
        payload = json.loads(message.metadata_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _booky_courier_timestamp(metadata: dict, key: str, fallback: datetime | None = None) -> datetime | None:
    raw = metadata.get(key)
    try:
        value = datetime.fromisoformat(str(raw)) if raw else fallback
    except (TypeError, ValueError):
        value = fallback
    if value and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


async def _booky_attempt_email_log(session, message_id, attempt: int):
    from app.models.email_log import EmailLog

    if attempt <= 0:
        return None
    result = await session.execute(
        select(EmailLog)
        .where(
            EmailLog.courier_message_id == message_id,
            EmailLog.courier_attempt == attempt,
        )
        .order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _record_booky_delivery_terminal_receipt(
    session,
    *,
    message,
    metadata: dict,
    delivery_status: str,
    error: str | None,
) -> None:
    from app.models.booking_lifecycle_event import BookingLifecycleEvent

    action_id = str(metadata.get("action_id") or "")
    attempt = int(metadata.get("delivery_attempts") or 0)
    try:
        booking_id = UUID(str(metadata.get("booking_id")))
    except (TypeError, ValueError):
        return
    if not action_id or attempt <= 0:
        return
    provider_event_id = f"booky:{action_id}:courier-attempt:{attempt}:terminal"
    existing = await session.execute(
        select(BookingLifecycleEvent.id).where(BookingLifecycleEvent.provider_event_id == provider_event_id)
    )
    if existing.scalar_one_or_none():
        return
    succeeded = delivery_status in {"sent", "delivered", "opened", "clicked"}
    staff_recovery_attempt = int(metadata.get("delivery_staff_recovery_attempts") or 0)
    session.add(
        BookingLifecycleEvent(
            booking_id=booking_id,
            event_type=(
                "booky_delivery_retry_completed"
                if succeeded and staff_recovery_attempt
                else "booky_delivery_retry_failed"
                if staff_recovery_attempt
                else "booky_delivery_completed"
                if succeeded
                else "booky_delivery_failed"
            ),
            source="booky",
            provider_event_id=provider_event_id,
            reference_code=metadata.get("reference_code"),
            summary=(
                "Booky Courier provider attempt completed." if succeeded else "Booky Courier provider attempt failed."
            ),
            details={
                "action_id": action_id,
                "courier_message_id": str(message.id),
                "delivery_attempt": attempt,
                "delivery_attempt_id": metadata.get("delivery_attempt_id"),
                "staff_recovery_attempt": staff_recovery_attempt,
                "delivery_status": delivery_status,
                "delivery_started_at": metadata.get("delivery_attempt_started_at"),
                "delivery_finished_at": metadata.get("delivery_attempt_finished_at"),
                "error": error,
            },
        )
    )


async def process_booky_courier_outbox_once(
    limit: int = 20,
    *,
    message_id=None,
) -> int:
    """Claim and deliver durable Booky Courier messages.

    Courier Message owns the durable intent and EmailLog owns provider evidence.
    A stale `sending` row is reconciled against EmailLog before any resend, so a
    process restart cannot silently lose or blindly duplicate the delivery.
    """
    from app.core.database import async_session
    from app.models.message import Message

    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=BOOKY_COURIER_STALE_MINUTES)
    async with async_session() as session:
        query = select(Message).where(
            Message.message_type == "booky_confirmation",
            or_(
                Message.metadata_json.like('%"delivery_status": "queued"%'),
                Message.metadata_json.like('%"delivery_status": "sending"%'),
                Message.metadata_json.like('%"delivery_status": "failed"%'),
            ),
        )
        if message_id is not None:
            query = query.where(Message.id == message_id)
        result = await session.execute(
            query.order_by(Message.created_at).with_for_update(skip_locked=True).limit(limit)
        )
        candidates = list(result.scalars().all())
        claimed: list[tuple[object, dict]] = []
        for message in candidates:
            metadata = _booky_courier_metadata(message)
            status = str(metadata.get("delivery_status") or "queued")
            attempts = int(metadata.get("delivery_attempts") or 0)
            staff_override = metadata.get("delivery_staff_override") is True
            updated_at = _booky_courier_timestamp(metadata, "delivery_updated_at", message.created_at)
            next_attempt_at = _booky_courier_timestamp(metadata, "delivery_next_attempt_at", now) or now

            if status in {"sent", "delivered", "opened", "clicked"}:
                continue

            if status == "sending":
                nonstale = bool(updated_at and updated_at > stale_before)
                if nonstale:
                    continue
                email_log = await _booky_attempt_email_log(session, message.id, attempts)
                if email_log and email_log.status in {
                    "sent",
                    "delivered",
                    "opened",
                    "clicked",
                    "dev_skipped",
                    "failed",
                    "bounced",
                    "complained",
                }:
                    reconciled_status = (
                        "sent"
                        if email_log.status in {"sent", "dev_skipped"}
                        else "failed"
                        if email_log.status in {"failed", "bounced", "complained"}
                        else email_log.status
                    )
                    metadata["delivery_status"] = reconciled_status
                    metadata["delivery_updated_at"] = now.isoformat()
                    metadata["delivery_attempt_finished_at"] = now.isoformat()
                    metadata["delivery_last_error"] = email_log.error
                    if reconciled_status == "failed":
                        metadata["delivery_next_attempt_at"] = (
                            now + timedelta(minutes=min(2**attempts, 60))
                        ).isoformat()
                    message.metadata_json = json.dumps(metadata)
                    await _record_booky_delivery_terminal_receipt(
                        session,
                        message=message,
                        metadata=metadata,
                        delivery_status=reconciled_status,
                        error=email_log.error,
                    )
                    continue

            eligible = (
                staff_override
                or status == "queued"
                or (status == "failed" and attempts < BOOKY_COURIER_MAX_ATTEMPTS and next_attempt_at <= now)
                or (status == "sending" and updated_at and updated_at <= stale_before)
            )
            if not eligible or (attempts >= BOOKY_COURIER_MAX_ATTEMPTS and not staff_override):
                continue

            metadata["delivery_status"] = "sending"
            metadata["delivery_attempts"] = attempts + 1
            metadata["delivery_staff_override"] = False
            metadata["delivery_updated_at"] = now.isoformat()
            metadata["delivery_attempt_started_at"] = now.isoformat()
            metadata["delivery_attempt_finished_at"] = None
            metadata["delivery_attempt_id"] = f"booky:{metadata.get('action_id')}:courier-attempt:{attempts + 1}"
            metadata["delivery_last_error"] = None
            message.metadata_json = json.dumps(metadata)
            claimed.append((message.id, metadata))
        await session.commit()

        delivered = 0
        for claimed_id, claimed_metadata in claimed:
            try:
                html = build_booking_confirmation_html(
                    client_name=str(claimed_metadata["client_name"]),
                    artist_name=str(claimed_metadata["artist_name"]),
                    description=str(claimed_metadata.get("description") or ""),
                    placement=claimed_metadata.get("placement"),
                    reference_code=str(claimed_metadata["reference_code"]),
                    booking_kind="consultation",
                    consultation_method=claimed_metadata.get("consultation_method"),
                    confirmed=True,
                    scheduled_date=claimed_metadata.get("scheduled_date"),
                    scheduled_time=claimed_metadata.get("scheduled_time"),
                    conference_url=claimed_metadata.get("conference_url"),
                )
                sent = await send_email(
                    to=str(claimed_metadata["delivery_to"]),
                    subject="Consultation Booked - Ryozan Studio",
                    html_body=html,
                    tags=["booking-confirmation"],
                    from_alias=FROM_SHOP,
                    courier_message_id=str(claimed_id),
                    courier_attempt=int(claimed_metadata["delivery_attempts"]),
                )
                error = None if sent else "send_email returned False"
            except Exception as exc:  # noqa: BLE001 - durable state records provider failure
                sent = False
                error = str(exc)[:1000]
                logger.exception("Booky Courier delivery failed for message %s", claimed_id)

            message = await session.get(
                Message,
                claimed_id,
                with_for_update=True,
                populate_existing=True,
            )
            if not message:
                continue
            metadata = _booky_courier_metadata(message)
            attempt = int(claimed_metadata.get("delivery_attempts") or 1)
            if (
                int(metadata.get("delivery_attempts") or 0) != attempt
                or metadata.get("delivery_attempt_id") != claimed_metadata.get("delivery_attempt_id")
                or metadata.get("delivery_status") != "sending"
            ):
                continue
            finished_at = datetime.now(UTC)
            metadata["delivery_status"] = "sent" if sent else "failed"
            metadata["delivery_updated_at"] = finished_at.isoformat()
            metadata["delivery_attempt_finished_at"] = finished_at.isoformat()
            metadata["delivery_last_error"] = error
            if not sent:
                metadata["delivery_next_attempt_at"] = (
                    finished_at + timedelta(minutes=min(2**attempt, 60))
                ).isoformat()
            message.metadata_json = json.dumps(metadata)
            await _record_booky_delivery_terminal_receipt(
                session,
                message=message,
                metadata=metadata,
                delivery_status=str(metadata["delivery_status"]),
                error=error,
            )
            await session.commit()
            if sent:
                delivered += 1
        return delivered


def _parse_email_identity(identity: str) -> tuple[str | None, str]:
    """Parse 'Display Name <email@example.com>' into provider-friendly pieces."""
    match = _re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", identity)
    if match:
        name = match.group(1).strip().strip('"') or None
        return name, match.group(2).strip()
    return None, identity.strip()


async def _send_via_sendgrid(
    to: str,
    subject: str,
    html_body: str,
    reply_to: str | None = None,
    tags: list[str] | None = None,
    from_override: str | None = None,
    attachments: list[EmailAttachment] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Send email via SendGrid REST API. Returns (success, message_id, error)."""
    settings = get_settings()
    configured_from = f"{settings.SENDGRID_FROM_NAME} <{settings.SENDGRID_FROM_EMAIL}>"
    from_identity = from_override if settings.SENDGRID_USE_ALIAS_FROM and from_override else configured_from
    from_name, from_email = _parse_email_identity(from_identity)

    payload: dict = {
        "personalizations": [
            {
                "to": [{"email": to}],
                "subject": subject,
            }
        ],
        "from": {"email": from_email},
        "content": [{"type": "text/html", "value": html_body}],
    }
    if from_name:
        payload["from"]["name"] = from_name
    if reply_to:
        payload["reply_to"] = {"email": reply_to}
    if tags:
        payload["categories"] = tags[:10]
    if attachments:
        payload["attachments"] = [
            {
                "content": a.b64(),
                "filename": a.filename,
                "type": a.content_type,
                "disposition": "attachment",
            }
            for a in attachments
        ]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code == 202:
            message_id = resp.headers.get("X-Message-Id")
            logger.info("Email sent via SendGrid to %s (id=%s)", to, message_id)
            return True, message_id, None

        err = f"SendGrid {resp.status_code}: {resp.text[:200]}"
        logger.error("SendGrid API error %s: %s", resp.status_code, resp.text)
        return False, None, err
    except Exception as exc:
        logger.error("SendGrid send failed to %s: %s", to, exc)
        return False, None, str(exc)[:200]


async def _send_via_resend(
    to: str,
    subject: str,
    html_body: str,
    reply_to: str | None = None,
    tags: list[str] | None = None,
    from_override: str | None = None,
    attachments: list[EmailAttachment] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Send email via Resend REST API. Returns (success, resend_id, error)."""
    settings = get_settings()
    from_addr = from_override or f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_EMAIL}>"

    payload: dict = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    if reply_to:
        payload["reply_to"] = [reply_to]
    if tags:
        payload["tags"] = [{"name": t, "value": "true"} for t in tags[:5]]
    if attachments:
        payload["attachments"] = [
            {"content": a.b64(), "filename": a.filename, "content_type": a.content_type} for a in attachments
        ]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code in (200, 201):
            data = resp.json()
            resend_id = data.get("id")
            logger.info("Email sent via Resend to %s (id=%s)", to, resend_id)
            return True, resend_id, None
        else:
            err = f"Resend {resp.status_code}: {resp.text[:200]}"
            logger.error("Resend API error %s: %s", resp.status_code, resp.text)
            return False, None, err
    except Exception as exc:
        logger.error("Resend send failed to %s: %s", to, exc)
        return False, None, str(exc)[:200]


async def _send_via_smtp(
    to: str,
    subject: str,
    html_body: str,
    reply_to: str | None = None,
    attachments: list[EmailAttachment] | None = None,
) -> bool:
    """Legacy Gmail SMTP fallback.

    reply_to is honored when set so that fallback delivery preserves the same
    reply-routing semantics as the primary Resend path. Without this, replies
    to courier notifications would go to the From address (noreply@) when
    SMTP fallback fires, silently reintroducing the lost-reply bug.
    """
    settings = get_settings()

    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("No email provider configured -- skipping email to %s", to)
        return False

    plain = "Thank you for visiting Ryozan Studio Please view this email in an HTML-capable client."

    if attachments:
        # An attachment cannot live directly in a multipart/alternative -- clients would
        # treat it as another rendering of the same body and hide it. Nest the existing
        # alternative part inside a multipart/mixed instead. The no-attachment path below
        # is left exactly as it was so every existing caller's message is unchanged.
        msg = MIMEMultipart("mixed")
        body = MIMEMultipart("alternative")
        body.attach(MIMEText(plain, "plain"))
        body.attach(MIMEText(html_body, "html"))
        msg.attach(body)
        for att in attachments:
            subtype = att.content_type.split("/", 1)[-1] if "/" in att.content_type else "octet-stream"
            part = MIMEApplication(att.content, _subtype=subtype)
            part.add_header("Content-Disposition", "attachment", filename=att.filename)
            msg.attach(part)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html_body, "html"))

    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL or settings.SMTP_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    try:
        local_capture = settings.SMTP_PORT == 1025 and settings.SMTP_HOST.strip().lower() in {
            "127.0.0.1",
            "localhost",
            "mailhog",
        }
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            start_tls=not local_capture,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
        )
        logger.info("Email sent via SMTP to %s", to)
        return True
    except Exception as exc:
        logger.error("SMTP send failed to %s: %s", to, exc)
        return False


def build_aftercare_html(
    client_name: str,
    artist_name: str,
    tattoo_description: str,
    tattoo_placement: str,
    reference_code: str,
) -> str:
    """
    Branded aftercare email — personalized with client info and
    tattoo-site-specific instructions (SC 44-34-60 compliance).
    """

    # Site-specific aftercare tips based on placement area
    placement_lower = tattoo_placement.lower()
    site_tips = _get_placement_tips(placement_lower)

    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  {_build_header("TATTOO AFTERCARE INSTRUCTIONS")}

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:15px;line-height:1.6;">
    <p style="margin:0 0 16px;">Hey {client_name},</p>
    <p style="margin:0 0 16px;">
      Thanks for getting tattooed with <strong style="color:#faae15;">{artist_name}</strong> at Ryozan Studio
      Here's everything you need to know to keep your new piece looking sharp.
    </p>
  </td></tr>

  <!-- Tattoo Details Card -->
  <tr><td style="padding:0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(255,248,236,0.04);border:1px solid rgba(250,174,21,0.2);border-radius:8px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">Your Tattoo</p>
        <p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;"><strong>Design:</strong> {tattoo_description}</p>
        <p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;"><strong>Placement:</strong> {tattoo_placement}</p>
        <p style="margin:0;font-size:15px;color:#e8dfcf;"><strong>Artist:</strong> {artist_name}</p>
      </td></tr>
    </table>
  </td></tr>

  <!-- General Aftercare -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:15px;line-height:1.7;">
    <h2 style="margin:0 0 16px;font-size:18px;color:#faae15;">General Aftercare</h2>

    <p style="margin:0 0 12px;"><strong style="color:#faae15;">First 2-4 Hours</strong></p>
    <ul style="margin:0 0 16px;padding-left:20px;color:#e8dfcf;">
      <li style="margin-bottom:6px;">Leave the bandage on for the time your artist recommended (typically 2-4 hours, or overnight for Saniderm/second skin).</li>
      <li style="margin-bottom:6px;">Wash your hands thoroughly before touching the tattoo.</li>
      <li style="margin-bottom:6px;">Gently remove the bandage and rinse with lukewarm water and fragrance-free soap.</li>
      <li>Pat dry with a clean paper towel — never use cloth towels.</li>
    </ul>

    <p style="margin:0 0 12px;"><strong style="color:#faae15;">Days 1-14 (Healing Phase)</strong></p>
    <ul style="margin:0 0 16px;padding-left:20px;color:#e8dfcf;">
      <li style="margin-bottom:6px;">Wash 2-3 times daily with fragrance-free soap and pat dry.</li>
      <li style="margin-bottom:6px;">Apply a thin layer of unscented moisturizer (Aquaphor for the first 3 days, then unscented lotion).</li>
      <li style="margin-bottom:6px;">Do NOT pick, scratch, or peel any flaking skin — let it shed naturally.</li>
      <li style="margin-bottom:6px;">Avoid submerging in water (no pools, hot tubs, baths, or ocean).</li>
      <li>Avoid direct sunlight on the tattoo.</li>
    </ul>

    <p style="margin:0 0 12px;"><strong style="color:#faae15;">Long-Term Care</strong></p>
    <ul style="margin:0 0 16px;padding-left:20px;color:#e8dfcf;">
      <li style="margin-bottom:6px;">Always apply SPF 30+ sunscreen to healed tattoos when exposed to sun.</li>
      <li style="margin-bottom:6px;">Keep the area moisturized for vibrant color retention.</li>
      <li>Contact your artist if you notice signs of infection: excessive redness, swelling, pus, or fever.</li>
    </ul>
  </td></tr>

  <!-- Site-Specific Tips -->
  <tr><td style="padding:0 24px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(250,174,21,0.06);border:1px solid rgba(250,174,21,0.2);border-radius:8px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 12px;font-size:14px;color:#faae15;text-transform:uppercase;letter-spacing:1px;font-weight:bold;">
          Tips for Your {tattoo_placement} Tattoo
        </p>
        {site_tips}
      </td></tr>
    </table>
  </td></tr>

  <!-- Blood Donor Notice (SC Legal Requirement) -->
  <tr><td style="padding:0 24px 24px;">
    <p style="margin:0;font-size:13px;color:#b9ad96;line-height:1.5;font-style:italic;">
      Note: Getting a tattoo may temporarily disqualify you from donating blood. Per AABB standards,
      most blood banks require a waiting period after tattooing. Check with your local blood bank for
      their specific policy.
    </p>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Collector's Club CTA -->
  <tr><td style="padding:24px;text-align:center;">
    <p style="margin:0 0 16px;font-size:15px;color:#e8dfcf;">
      Want faster check-ins, rewards, and exclusive drops?
    </p>
    <a href="{get_settings().FRONTEND_URL}/portal"
       style="display:inline-block;padding:14px 32px;background:#faae15;color:#121312;
              text-decoration:none;border-radius:999px;font-weight:bold;font-size:15px;
              letter-spacing:0.5px;">
      Join Collector's Club
    </a>
  </td></tr>

  <!-- Reference Code -->
  <tr><td style="padding:0 24px 24px;text-align:center;">
    <p style="margin:0;font-size:13px;color:#b9ad96;">
      Your waiver reference: <strong style="color:#e8dfcf;font-family:'Courier New',monospace;">{reference_code}</strong>
    </p>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 32px;text-align:center;">
    <p style="margin:0 0 8px;font-size:12px;color:#b9ad96;">
      Ryozan Studio
    </p>
    <p style="margin:0;font-size:11px;color:#666;">
      Questions about healing? Reply to this email or DM your artist on Instagram.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _get_placement_tips(placement: str) -> str:
    """
    Return HTML <ul> with aftercare tips specific to the tattoo placement.

    Covers the most common placement areas. Falls back to general
    movement/clothing advice for anything unrecognized.
    """

    items = "".join(
        f'<li style="margin-bottom:6px;font-size:14px;color:#e8dfcf;">{tip}</li>' for tip in placement_tips(placement)
    )
    return f'<ul style="margin:0;padding-left:20px;">{items}</ul>'


def placement_tips(placement: str) -> list[str]:
    """Plain-text aftercare tips for a placement. Shared by the legacy and Email Foundry aftercare renderers."""

    placement = (placement or "").lower()
    tips: list[str] = []

    # Joint / high-movement areas
    if any(kw in placement for kw in ["elbow", "knee", "ankle", "wrist", "finger", "hand", "foot", "toe", "knuckle"]):
        tips.append(
            "This area bends and flexes frequently — expect more scabbing and longer healing time (up to 3-4 weeks)."
        )
        tips.append("Avoid tight clothing, gloves, or shoes that rub directly on the tattoo during healing.")
        tips.append("Moisturize more often since joints dry out faster from movement.")

    # Inner arm / forearm
    elif any(kw in placement for kw in ["forearm", "inner arm", "bicep", "upper arm", "arm"]):
        tips.append("Avoid resting your arm on dirty surfaces (desks, counters) during the first week.")
        tips.append("Wear loose sleeves or roll them up — tight fabrics can stick to the healing tattoo.")
        tips.append("If you work out, avoid exercises that create heavy friction on the area.")

    # Chest / sternum / ribs
    elif any(kw in placement for kw in ["chest", "sternum", "rib", "ribs", "torso", "stomach", "abdomen"]):
        tips.append(
            "Wear soft, breathable fabrics — avoid bras, tight undershirts, or compression wear for at least 2 weeks."
        )
        tips.append("Sleep on your back if possible to avoid pressing the tattoo into sheets.")
        tips.append("Be careful with seat belts rubbing against chest/rib tattoos.")

    # Back / spine / shoulder blade
    elif any(kw in placement for kw in ["back", "spine", "shoulder blade", "shoulder", "upper back", "lower back"]):
        tips.append("Avoid lying directly on the tattoo while sleeping — try sleeping on your side or stomach.")
        tips.append("Skip backpacks and crossbody bags that rub the area.")
        tips.append("Wear loose shirts and avoid leaning against rough surfaces.")

    # Thigh / leg
    elif any(kw in placement for kw in ["thigh", "leg", "calf", "shin", "quad"]):
        tips.append("Avoid tight jeans or pants that rub — opt for loose shorts or sweats during healing.")
        tips.append("If you shave your legs, avoid the tattooed area for at least 3 weeks.")
        tips.append("Be mindful when sitting for long periods — crossed legs can irritate inner thigh tattoos.")

    # Neck / behind ear
    elif any(kw in placement for kw in ["neck", "behind ear", "ear", "throat"]):
        tips.append("Keep hair products, perfume, and cologne away from the tattoo during healing.")
        tips.append("Be careful with collars, necklaces, and headphones that may rub the area.")
        tips.append("This area is highly visible — consistent sunscreen use after healing is critical for longevity.")

    # Hip / pelvis
    elif any(kw in placement for kw in ["hip", "pelvis", "waist"]):
        tips.append("Avoid tight waistbands — low-rise or loose-fitting pants are best during healing.")
        tips.append("Be mindful of seat belts and bag straps crossing the area.")

    # Catch-all: general movement & clothing advice
    else:
        tips.append("Wear loose, breathable clothing over the tattooed area to prevent friction.")
        tips.append("Avoid activities that cause heavy sweating or rubbing on the tattoo for the first 2 weeks.")
        tips.append("If you're unsure about anything specific to your placement, reach out to your artist.")

    return tips


# ── Document Send Email ───────────────────────────────


def build_document_send_html(
    recipient_name: str,
    document_title: str,
    public_url: str,
    sender_name: str,
) -> str:
    """
    Branded email notifying a recipient they have a document to sign.

    Contains a CTA button linking to the magic link form page.
    Same dark theme + gold accents as aftercare email.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  {_build_header("DOCUMENT FOR YOUR SIGNATURE")}

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:15px;line-height:1.6;">
    <p style="margin:0 0 16px;">Hey {recipient_name},</p>
    <p style="margin:0 0 16px;">
      <strong style="color:#faae15;">{sender_name}</strong> from Ryozan Studio
      has sent you a document that needs your attention.
    </p>
  </td></tr>

  <!-- Document Card -->
  <tr><td style="padding:0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(255,248,236,0.04);border:1px solid rgba(250,174,21,0.2);border-radius:8px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 4px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">Document</p>
        <p style="margin:0;font-size:17px;color:#e8dfcf;font-weight:bold;">{document_title}</p>
      </td></tr>
    </table>
  </td></tr>

  <!-- CTA Button -->
  <tr><td style="padding:24px;text-align:center;">
    <a href="{public_url}"
       style="display:inline-block;padding:14px 40px;background:#faae15;color:#121312;
              text-decoration:none;border-radius:999px;font-weight:bold;font-size:15px;
              letter-spacing:0.5px;">
      Open &amp; Sign Document
    </a>
  </td></tr>

  <!-- Fine Print -->
  <tr><td style="padding:0 24px 24px;">
    <p style="margin:0;font-size:13px;color:#b9ad96;line-height:1.5;text-align:center;">
      This link is unique to you. If you didn't expect this, you can safely ignore it.
    </p>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 32px;text-align:center;">
    <p style="margin:0 0 8px;font-size:12px;color:#b9ad96;">
      Ryozan Studio
    </p>
    <p style="margin:0;font-size:11px;color:#666;">
      Questions? Reply to this email or contact the shop directly.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def build_reminder_html(
    recipient_name: str,
    document_title: str,
    public_url: str,
    hours_remaining: int,
) -> str:
    """
    Branded reminder email for an unsigned document.

    Shows urgency with hours remaining. Same visual style.
    """
    urgency = f"about {hours_remaining} hours" if hours_remaining > 1 else "less than an hour"

    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  {_build_header("FRIENDLY REMINDER")}

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:15px;line-height:1.6;">
    <p style="margin:0 0 16px;">Hey {recipient_name},</p>
    <p style="margin:0 0 16px;">
      Just a quick reminder — you still have a document waiting for your signature.
      You have {urgency} before it expires.
    </p>
  </td></tr>

  <!-- Document Card -->
  <tr><td style="padding:0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(255,248,236,0.04);border:1px solid rgba(250,174,21,0.2);border-radius:8px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 4px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">Document</p>
        <p style="margin:0;font-size:17px;color:#e8dfcf;font-weight:bold;">{document_title}</p>
      </td></tr>
    </table>
  </td></tr>

  <!-- CTA Button -->
  <tr><td style="padding:24px;text-align:center;">
    <a href="{public_url}"
       style="display:inline-block;padding:14px 40px;background:#faae15;color:#121312;
              text-decoration:none;border-radius:999px;font-weight:bold;font-size:15px;
              letter-spacing:0.5px;">
      Complete Your Document
    </a>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 32px;text-align:center;">
    <p style="margin:0 0 8px;font-size:12px;color:#b9ad96;">
      Ryozan Studio
    </p>
    <p style="margin:0;font-size:11px;color:#666;">
      If you've already completed this, you can ignore this email.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# -- Booking Confirmation (client) ---------------------------------


def build_booking_confirmation_html(
    client_name: str,
    artist_name: str,
    description: str,
    placement: str | None,
    reference_code: str,
    booking_kind: str = "tattoo_session",
    consultation_method: str | None = None,
    confirmed: bool = False,
    scheduled_date: str | None = None,
    scheduled_time: str | None = None,
    conference_url: str | None = None,
) -> str:
    """
    Branded confirmation email sent to the client after submitting
    a public booking request.  Shows project summary, reference code,
    and a Collector's Club CTA.
    """
    placement_line = (
        f'<p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;"><strong>Placement:</strong> {placement}</p>'
        if placement
        else ""
    )
    method_labels = {
        "whatsapp": "WhatsApp",
        "meet": "Google Meet",
        "in_person": "In person",
    }
    consultation_line = (
        (
            '<p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;">'
            f"<strong>Consultation method:</strong> {method_labels.get(consultation_method, consultation_method)}</p>"
        )
        if booking_kind == "consultation" and consultation_method
        else ""
    )
    schedule_line = (
        (
            '<p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;">'
            f"<strong>Booked for:</strong> {scheduled_date} at {scheduled_time}</p>"
        )
        if confirmed and scheduled_date and scheduled_time
        else ""
    )
    conference_button = (
        (
            '<p style="margin:18px 0 0;text-align:center;">'
            f'<a href="{conference_url}" style="display:inline-block;padding:12px 24px;'
            'background:#df8a67;color:#121312;text-decoration:none;border-radius:8px;font-weight:bold;">'
            "Open Google Meet</a></p>"
        )
        if conference_url
        else ""
    )
    payment_step = (
        "<li>No deposit is required for this consultation.</li>"
        if booking_kind == "consultation"
        else "<li>A deposit may be required to lock in your appointment date.</li>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  {_build_header("CONSULTATION BOOKED" if confirmed else "BOOKING REQUEST RECEIVED")}

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:15px;line-height:1.6;">
    <p style="margin:0 0 16px;">Hey {client_name},</p>
    <p style="margin:0 0 16px;">
      {f'Your consultation with <strong style="color:#df8a67;">{artist_name}</strong> is booked.' if confirmed else f'Thanks for reaching out to <strong style="color:#faae15;">{artist_name}</strong> at Ryozan Studio Your booking request has been received and your artist will review it within <strong>24-48 hours</strong>.'}
    </p>
  </td></tr>

  <!-- Project Details Card -->
  <tr><td style="padding:0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(255,248,236,0.04);border:1px solid rgba(250,174,21,0.2);border-radius:8px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">
          Your Project
        </p>
        <p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;">
          <strong>Description:</strong> {description[:200]}{"..." if len(description) > 200 else ""}
        </p>
        {placement_line}
        {consultation_line}
        {schedule_line}
        <p style="margin:0;font-size:15px;color:#e8dfcf;">
          <strong>Artist:</strong> {artist_name}
        </p>
      </td></tr>
    </table>
  </td></tr>

  <!-- What's Next -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:15px;line-height:1.6;">
    <h2 style="margin:0 0 12px;font-size:18px;color:#faae15;">{("You Are Booked" if confirmed else "What Happens Next?")}</h2>
    <ul style="margin:0;padding-left:20px;">
      {('<li style="margin-bottom:6px;">A calendar invitation has been sent to your email.</li><li style="margin-bottom:6px;">Use the selected consultation method at your booked time.</li>' if confirmed else '<li style="margin-bottom:6px;">Your artist reviews your request and any reference images.</li><li style="margin-bottom:6px;">You will receive a follow-up email to discuss details and schedule your session.</li>')}
      {payment_step}
    </ul>
    {conference_button}
  </td></tr>

  <!-- Reference Code -->
  <tr><td style="padding:0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(250,174,21,0.06);border:1px solid rgba(250,174,21,0.2);border-radius:8px;">
      <tr><td style="padding:16px 20px;text-align:center;">
        <p style="margin:0 0 4px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">
          Your Reference Code
        </p>
        <p style="margin:0;font-size:22px;color:#faae15;font-family:'Courier New',monospace;font-weight:bold;letter-spacing:2px;">
          {reference_code}
        </p>
        <p style="margin:8px 0 0;font-size:12px;color:#b9ad96;">
          Save this code to check on your booking status.
        </p>
      </td></tr>
    </table>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:24px 24px 0;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Collector's Club CTA -->
  <tr><td style="padding:24px;text-align:center;">
    <p style="margin:0 0 16px;font-size:15px;color:#e8dfcf;">
      Want faster check-ins, rewards, and exclusive drops?
    </p>
    <a href="{get_settings().FRONTEND_URL}/portal"
       style="display:inline-block;padding:14px 32px;background:#faae15;color:#121312;
              text-decoration:none;border-radius:999px;font-weight:bold;font-size:15px;
              letter-spacing:0.5px;">
      Join Collector's Club
    </a>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 32px;text-align:center;">
    <p style="margin:0 0 8px;font-size:12px;color:#b9ad96;">
      Ryozan Studio
    </p>
    <p style="margin:0;font-size:11px;color:#666;">
      Questions? Reply to this email or DM your artist on Instagram.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# -- Deposit Confirmation (post-payment) ----------------------------


def build_deposit_confirmation_html(
    client_name: str,
    artist_name: str,
    description: str,
    placement: str | None,
    deposit_amount: float | str,
    scheduled_date: str | None,
    scheduled_time: str | None,
    duration_minutes: int | None,
    reference_code: str,
) -> str:
    """
    Branded confirmation email sent after deposit payment via Stripe Checkout.
    Includes appointment details, parking info, and Collector's Club CTA.
    """
    # deposit_amount is normally a float (real send), but the template seeder
    # passes a "{{deposit_amount}}" placeholder so the DB template stays fillable.
    # Format numbers as $1,234; pass a placeholder/string through untouched.
    deposit_display = f"${deposit_amount:,.0f}" if isinstance(deposit_amount, (int, float)) else str(deposit_amount)

    placement_line = (
        f'<p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;"><strong>Placement:</strong> {placement}</p>'
        if placement
        else ""
    )

    schedule_line = ""
    if scheduled_date:
        schedule_line = (
            f'<p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;"><strong>Date:</strong> {scheduled_date}'
        )
        if scheduled_time:
            schedule_line += f" at {_format_time_12hr(scheduled_time) or scheduled_time}"
        if duration_minutes:
            schedule_line += f" ({duration_minutes} min)"
        schedule_line += "</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  {_build_header("YOU'RE BOOKED!")}

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:15px;line-height:1.6;">
    <p style="margin:0 0 16px;">Hey {client_name},</p>
    <p style="margin:0 0 16px;">
      Your deposit of <strong style="color:#faae15;">{deposit_display}</strong> has been received
      and your appointment with <strong style="color:#faae15;">{artist_name}</strong> is confirmed!
    </p>
  </td></tr>

  <!-- Appointment Details Card -->
  <tr><td style="padding:0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(255,248,236,0.04);border:1px solid rgba(250,174,21,0.2);border-radius:8px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">
          Appointment Details
        </p>
        <p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;">
          <strong>Artist:</strong> {artist_name}
        </p>
        <p style="margin:0 0 4px;font-size:15px;color:#e8dfcf;">
          <strong>Description:</strong> {description[:200]}{"..." if len(description) > 200 else ""}
        </p>
        {placement_line}
        {schedule_line}
        <p style="margin:4px 0 0;font-size:15px;color:#faae15;">
          <strong>Deposit Paid:</strong> {deposit_display}
        </p>
      </td></tr>
    </table>
  </td></tr>

  <!-- Reference Code -->
  <tr><td style="padding:20px 24px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(250,174,21,0.06);border:1px solid rgba(250,174,21,0.2);border-radius:8px;">
      <tr><td style="padding:12px 20px;text-align:center;">
        <p style="margin:0 0 4px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">
          Your Reference Code
        </p>
        <p style="margin:0;font-size:22px;color:#faae15;font-family:'Courier New',monospace;font-weight:bold;letter-spacing:2px;">
          {reference_code}
        </p>
      </td></tr>
    </table>
  </td></tr>

  <!-- What to Expect -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:15px;line-height:1.6;">
    <h2 style="margin:0 0 12px;font-size:18px;color:#faae15;">What to Expect</h2>
    <ul style="margin:0;padding-left:20px;">
      <li style="margin-bottom:6px;">A reminder will be sent before your session.</li>
      <li style="margin-bottom:6px;">Your deposit is applied toward your final session price.</li>
      <li>All payments are final and non-refundable except where required by law or for duplicate or unauthorized charges. An artist may approve transferring a deposit to a rescheduled appointment.</li>
    </ul>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Collector's Club CTA -->
  <tr><td style="padding:24px;text-align:center;">
    <p style="margin:0 0 16px;font-size:15px;color:#e8dfcf;">
      Want faster check-ins, rewards, and exclusive drops?
    </p>
    <a href="{get_settings().FRONTEND_URL}/portal"
       style="display:inline-block;padding:14px 32px;background:#faae15;color:#121312;
              text-decoration:none;border-radius:999px;font-weight:bold;font-size:15px;
              letter-spacing:0.5px;">
      Join Collector's Club
    </a>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 32px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
    <p style="margin:0 0 8px;font-size:12px;color:#b9ad96;">
      Ryozan Studio
    </p>
    <p style="margin:0;font-size:11px;color:#666;">
      Questions? Reply to this email or DM your artist on Instagram.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# -- Deposit Artist Notification ------------------------------------


def build_deposit_artist_notification_html(
    client_name: str,
    client_email: str,
    artist_name: str,
    description: str,
    placement: str | None,
    deposit_amount: float,
    scheduled_date: str | None = None,
    scheduled_time: str | None = None,
    duration_minutes: int | None = None,
    reference_code: str = "",
) -> str:
    """
    Internal notification sent to the artist when a client pays their deposit.
    Concise — who paid, how much, session details.
    """

    # Helper to render a label/value row — skips if value is None/empty
    def row(label: str, value: str | None) -> str:
        if not value:
            return ""
        return f"""
        <tr>
          <td style="padding:5px 16px 5px 0;font-size:13px;color:#b9ad96;
                     white-space:nowrap;vertical-align:top;width:130px;">{label}</td>
          <td style="padding:5px 0;font-size:14px;color:#e8dfcf;vertical-align:top;">{value}</td>
        </tr>"""

    # Build schedule line
    schedule_parts = []
    if scheduled_date:
        schedule_parts.append(scheduled_date)
    if scheduled_time:
        schedule_parts.append(_format_time_12hr(scheduled_time) or scheduled_time)
    schedule_val = " at ".join(schedule_parts) if schedule_parts else None

    duration_val = f"{duration_minutes} min" if duration_minutes else None

    detail_rows = (
        row("CLIENT", client_name)
        + row("EMAIL", client_email)
        + row("DESCRIPTION", f"{description[:200]}{'...' if len(description) > 200 else ''}")
        + row("PLACEMENT", placement)
        + row("SCHEDULED", schedule_val)
        + row("DURATION", duration_val)
        + row("DEPOSIT", f"${deposit_amount:,.0f}")
        + row("REFERENCE", reference_code)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;border:1px solid rgba(250,174,21,0.15);border-radius:10px;overflow:hidden;">

  {_build_header("DEPOSIT RECEIVED")}

  <!-- ── Alert bar ──────────────────────────────────────────── -->
  <tr>
    <td style="background:rgba(250,174,21,0.12);padding:12px 24px;
               border-top:1px solid rgba(250,174,21,0.25);
               border-bottom:1px solid rgba(250,174,21,0.25);">
      <p style="margin:0;font-size:14px;color:#faae15;letter-spacing:0.5px;">
        {client_name} just paid a <strong>${deposit_amount:,.0f}</strong> deposit for their session with you!
      </p>
    </td>
  </tr>

  <!-- ── Session Details ─────────────────────────────────────── -->
  <tr>
    <td style="padding:20px 24px;">
      <p style="margin:0 0 10px;font-size:11px;color:#b9ad96;
                text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">
        Session Details
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        {detail_rows}
      </table>
    </td>
  </tr>

  <!-- ── CTA ─────────────────────────────────────────────────── -->
  <tr>
    <td style="padding:0 24px 24px;text-align:center;">
      <a href="{get_settings().FRONTEND_URL}{ROUTES.HUDDLE}"
         style="display:inline-block;padding:13px 36px;background:#faae15;color:#121312;
                text-decoration:none;border-radius:6px;font-weight:bold;font-size:14px;
                letter-spacing:0.5px;">
        Open Dashboard
      </a>
    </td>
  </tr>

  <!-- ── Footer ──────────────────────────────────────────────── -->
  <tr>
    <td style="padding:12px 24px 20px;text-align:center;
               border-top:1px solid rgba(250,174,21,0.1);">
      <p style="margin:0;font-size:11px;color:#555;">
        Ryozan Studio
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# -- Artist Notification (new booking alert) ------------------------


def _build_reference_images_section(urls: list[str] | None) -> str:
    """Build the reference images HTML section for the artist alert email.
    Extracts Google Drive file IDs and renders clickable thumbnail previews.
    Returns empty string if no images — section is omitted entirely."""
    import re

    if not urls:
        return ""

    # Build thumbnail/link pairs (max 4)
    # Supports R2/direct URLs and legacy Drive URLs
    images = []
    for url in urls[:4]:
        if not url:
            continue
        # Direct image URLs (R2, any CDN)
        if url.startswith("https://"):
            images.append({"thumb": url, "full": url})
            continue
        else:
            # Legacy: extract Drive file ID for thumbnail
            match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
            if match:
                fid = match.group(1)
                images.append(
                    {
                        "thumb": f"https://drive.google.com/thumbnail?id={fid}&sz=w260",
                        "full": url,
                    }
                )

    if not images:
        return ""

    # Build rows of 2 images each
    rows = []
    for i in range(0, len(images), 2):
        pair = images[i : i + 2]
        row_cells = ""
        for img in pair:
            row_cells += f"""
              <td style="padding:4px;width:50%;vertical-align:top;">
                <a href="{img["full"]}" target="_blank" style="text-decoration:none;">
                  <img src="{img["thumb"]}" alt="Reference image"
                       style="width:100%;max-width:260px;height:auto;border-radius:6px;
                              border:1px solid rgba(250,174,21,0.2);display:block;" />
                </a>
              </td>"""
        # Pad with empty cell if odd number
        if len(pair) == 1:
            row_cells += '<td style="padding:4px;width:50%;"></td>'
        rows.append(f"<tr>{row_cells}</tr>")

    return f"""
  <!-- ── Reference Images ────────────────────────────────── -->
  <tr>
    <td style="padding:16px 24px 0;">
      <p style="margin:0 0 10px;font-size:11px;color:#b9ad96;
                text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">
        Reference Images
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        {"".join(rows)}
      </table>
    </td>
  </tr>"""


def build_artist_notification_html(
    client_name: str,
    client_email: str,
    client_phone: str | None,
    artist_name: str,
    description: str,
    placement: str | None,
    size: str | None,
    budget: str | None,
    timeline: str | None,
    reference_code: str,
    style_preference: str | None = None,
    submitted_at: str | None = None,
    reference_image_urls: list[str] | None = None,
    conversation_id: str | None = None,
    accept_url: str | None = None,
    message_url: str | None = None,
    decline_url: str | None = None,
) -> str:
    """
    Internal shop notification — sent to artist + settings.SHOP_EMAIL
    when a new public booking request arrives.

    Designed for quick scanning on mobile: who, contact info, who they want,
    when they submitted, what they want. No marketing fluff.
    """

    description = description or ""
    placement = placement or ""

    # Timestamp in Eastern
    ts = submitted_at or _format_datetime_eastern()

    # Helper to render a label/value row — skips if value is None/empty
    def row(label: str, value: str | None, href: str | None = None) -> str:
        if not value:
            return ""
        val_html = (
            f'<a href="{href}" style="color:#faae15;text-decoration:none;">{value}</a>'
            if href
            else f'<span style="color:#e8dfcf;">{value}</span>'
        )
        return f"""
        <tr>
          <td style="padding:5px 16px 5px 0;font-size:13px;color:#b9ad96;
                     white-space:nowrap;vertical-align:top;width:110px;">{label}</td>
          <td style="padding:5px 0;font-size:14px;vertical-align:top;">{val_html}</td>
        </tr>"""

    contact_rows = (
        row("NAME", client_name)
        + row("EMAIL", client_email, href=f"mailto:{client_email}")
        + row("PHONE", client_phone, href=f"tel:{client_phone}" if client_phone else None)
        + row("SUBMITTED", ts)
    )

    project_rows = (
        row("ARTIST", artist_name)
        + row("STYLE", style_preference)
        + row("PLACEMENT", placement)
        + row("SIZE", size)
        + row("BUDGET", budget)
        + row("TIMELINE", timeline)
    )

    base_url = get_settings().FRONTEND_URL.rstrip("/")
    booking_url = f"{base_url}{ROUTES.BOOKING_REF.format(ref=reference_code)}"
    accept_href = accept_url or f"{booking_url}&action=accept"
    message_href = message_url or (
        f"{base_url}{ROUTES.COURIER_CONVO.format(id=conversation_id)}"
        if conversation_id
        else f"{booking_url}&focus=message"
    )
    decline_href = decline_url or f"{booking_url}&action=reject"

    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;border:1px solid rgba(250,174,21,0.15);border-radius:10px;overflow:hidden;">

  {_build_header("NEW BOOKING INQUIRY")}

  <!-- ── Alert bar ──────────────────────────────────────────── -->
  <tr>
    <td style="background:rgba(250,174,21,0.12);padding:12px 24px;
               border-top:1px solid rgba(250,174,21,0.25);
               border-bottom:1px solid rgba(250,174,21,0.25);">
      <p style="margin:0;font-size:13px;color:#faae15;letter-spacing:0.5px;">
        &#9679; New booking request &mdash; ref
        <strong style="font-family:'Courier New',monospace;">{reference_code}</strong>
      </p>
    </td>
  </tr>

  <!-- ── Contact info ───────────────────────────────────────── -->
  <tr>
    <td style="padding:20px 24px 0;">
      <p style="margin:0 0 10px;font-size:11px;color:#b9ad96;
                text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">
        Contact
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        {contact_rows}
      </table>
    </td>
  </tr>

  <!-- Divider -->
  <tr><td style="padding:16px 24px 0;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.15);margin:0;">
  </td></tr>

  <!-- ── Project info ───────────────────────────────────────── -->
  <tr>
    <td style="padding:16px 24px 0;">
      <p style="margin:0 0 10px;font-size:11px;color:#b9ad96;
                text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">
        Project
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        {project_rows}
      </table>
    </td>
  </tr>

  <!-- ── Description ───────────────────────────────────────── -->
  <tr>
    <td style="padding:16px 24px 0;">
      <p style="margin:0 0 8px;font-size:11px;color:#b9ad96;
                text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;">
        Description
      </p>
      <p style="margin:0;font-size:14px;color:#e8dfcf;line-height:1.65;
                background:rgba(255,248,236,0.03);border-left:3px solid rgba(250,174,21,0.4);
                padding:10px 14px;border-radius:0 4px 4px 0;">
        {description[:500]}{"..." if len(description) > 500 else ""}
      </p>
    </td>
  </tr>

  {_build_reference_images_section(reference_image_urls)}

  <!-- ── Action Buttons ─────────────────────────────────────── -->
  <tr>
    <td style="padding:24px;text-align:center;">
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;">
        <tr>
          <td style="padding:0 6px;">
            <a href="{accept_href}"
               style="display:inline-block;padding:12px 24px;background:#faae15;color:#121312;
                      text-decoration:none;border-radius:6px;font-weight:bold;font-size:13px;
                      letter-spacing:0.3px;">
              Accept
            </a>
          </td>
          <td style="padding:0 6px;">
            <a href="{message_href}"
               style="display:inline-block;padding:12px 24px;background:rgba(250,174,21,0.15);
                      color:#faae15;text-decoration:none;border-radius:6px;font-weight:bold;
                      font-size:13px;letter-spacing:0.3px;border:1px solid rgba(250,174,21,0.3);">
              Message
            </a>
          </td>
          <td style="padding:0 6px;">
            <a href="{decline_href}"
               style="display:inline-block;padding:12px 24px;background:rgba(255,255,255,0.06);
                      color:#b9ad96;text-decoration:none;border-radius:6px;font-weight:bold;
                      font-size:13px;letter-spacing:0.3px;border:1px solid rgba(255,255,255,0.1);">
              Decline
            </a>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- ── Footer ────────────────────────────────────────────── -->
  <tr>
    <td style="padding:12px 24px 20px;text-align:center;
               border-top:1px solid rgba(250,174,21,0.1);">
      <p style="margin:0;font-size:11px;color:#555;">
        Ryozan Studio &bull; Automated booking notification
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# -- Quote Email ---------------------------------------------------


def build_quote_email_html(
    client_name: str,
    artist_name: str,
    deposit_amount: float,
    estimated_min: float | None,
    estimated_max: float | None,
    slots: list,
    artist_message: str | None,
    quote_url: str,
    reference_code: str | None,
) -> str:
    """Build branded HTML email for a booking quote."""
    # Format price range
    if estimated_min and estimated_max and estimated_min != estimated_max:
        price_range = f"${estimated_min:,.0f} – ${estimated_max:,.0f}"
    elif estimated_max:
        price_range = f"${estimated_max:,.0f}"
    elif estimated_min:
        price_range = f"${estimated_min:,.0f}"
    else:
        price_range = "To be discussed"

    # Format slots
    slot_html = ""
    for s in slots:
        # Support both dict and Pydantic model
        date = s.date if hasattr(s, "date") else s["date"]
        time = s.time if hasattr(s, "time") else s["time"]
        dur = s.duration_minutes if hasattr(s, "duration_minutes") else s["duration_minutes"]
        slot_html += f'<div style="padding:8px 12px;background:rgba(250,174,21,0.08);border-radius:6px;margin-bottom:6px;font-family:Arial,sans-serif;font-size:14px;color:#e8dfcf;">{date} at {time} ({dur} min)</div>'

    # Artist message block
    msg_block = ""
    if artist_message:
        msg_block = f'''
        <div style="padding:12px 16px;background:rgba(255,255,255,0.04);border-left:3px solid #faae15;border-radius:0 6px 6px 0;margin:16px 0;">
            <p style="margin:0;font-family:Arial,sans-serif;font-size:14px;color:#e8dfcf;font-style:italic;">"{artist_message}"</p>
            <p style="margin:6px 0 0;font-family:Arial,sans-serif;font-size:12px;color:#b9ad96;">— {artist_name}</p>
        </div>'''

    ref_line = (
        f'<p style="font-family:Arial,sans-serif;font-size:12px;color:#b9ad96;margin:0 0 16px;">Reference: {reference_code}</p>'
        if reference_code
        else ""
    )

    return f"""<!DOCTYPE html>
<html>
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:32px 16px;">
<table width="600" cellpadding="0" cellspacing="0" style="background-color:#1a1b1a;border-radius:12px;overflow:hidden;">

<!-- Logo -->
<tr><td style="padding:24px 32px;text-align:center;border-bottom:1px solid rgba(250,174,21,0.15);">
    <img src="{LOGO_URL}" alt="Ryozan Studio" width="60" style="border-radius:50%;">
</td></tr>

<!-- Content -->
<tr><td style="padding:32px;">
    <h1 style="font-family:'KC Tallboy',Oswald,'Arial Narrow',sans-serif;font-size:24px;color:#faae15;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px;">Your Quote is Ready</h1>
    {ref_line}

    <p style="font-family:Arial,sans-serif;font-size:15px;color:#e8dfcf;line-height:1.6;margin:0 0 20px;">
        Hey {client_name},<br><br>
        <strong>{artist_name}</strong> has reviewed your request and put together a quote for you.
    </p>

    {msg_block}

    <!-- Pricing -->
    <div style="padding:16px;background:rgba(250,174,21,0.06);border:1px solid rgba(250,174,21,0.15);border-radius:8px;margin:20px 0;">
        <table width="100%" cellpadding="4" style="font-family:Arial,sans-serif;font-size:14px;color:#e8dfcf;">
            <tr>
                <td style="color:#b9ad96;">Estimated Total</td>
                <td style="text-align:right;font-weight:bold;color:#faae15;">{price_range}</td>
            </tr>
            <tr>
                <td style="color:#b9ad96;">Deposit Required</td>
                <td style="text-align:right;font-weight:bold;color:#e8dfcf;">${deposit_amount:,.0f}</td>
            </tr>
        </table>
    </div>

    <!-- Slots -->
    <p style="font-family:Arial,sans-serif;font-size:13px;color:#b9ad96;margin:20px 0 8px;text-transform:uppercase;letter-spacing:0.5px;">Available Time Slots</p>
    {slot_html}

    <!-- CTA -->
    <div style="text-align:center;margin:28px 0 12px;">
        <a href="{quote_url}" style="display:inline-block;padding:14px 36px;background-color:#faae15;color:#121312;font-family:'KC Tallboy',Oswald,'Arial Narrow',sans-serif;font-size:16px;font-weight:bold;text-decoration:none;border-radius:6px;text-transform:uppercase;letter-spacing:1px;">View Quote & Book</a>
    </div>

    <p style="font-family:Arial,sans-serif;font-size:12px;color:#b9ad96;text-align:center;margin:8px 0 0;">
        All payments are final and non-refundable except where required by law or for duplicate or unauthorized charges. A deposit may be transferred only when the artist approves a reschedule.
    </p>
</td></tr>

<!-- Footer -->
<tr><td style="padding:20px 32px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
    <p style="font-family:Arial,sans-serif;font-size:11px;color:#b9ad96;margin:0;">
        Ryozan Studio
    </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# -- Artist Invite Email -------------------------------------------


def build_invite_html(
    artist_name: str,
    role: str,
    invite_url: str,
    expires_in_hours: int = 72,
) -> str:
    """
    Branded invite email sent to a new artist or staff member.

    Contains their name, role confirmation, expiry notice, and a
    single prominent CTA button linking to the /join page.
    """
    role_display = role.capitalize()

    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  {_build_header("YOU'VE BEEN INVITED")}

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:28px 24px 20px;color:#e8dfcf;font-size:15px;line-height:1.7;">
    <p style="margin:0 0 16px;">Hey {artist_name},</p>
    <p style="margin:0 0 16px;">
      You've been invited to join the <strong style="color:#faae15;">Ryozan Studio</strong>
      artist portal — your personal dashboard for managing bookings, client documents,
      and your public profile.
    </p>
    <p style="margin:0;">
      Click the button below to set your password and activate your account.
    </p>
  </td></tr>

  <!-- Identity Card -->
  <tr><td style="padding:0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(255,248,236,0.04);border:1px solid rgba(250,174,21,0.2);border-radius:8px;overflow:hidden;">
      <tr>
        <td style="padding:10px 20px;border-bottom:1px solid rgba(250,174,21,0.08);">
          <span style="font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">Name</span>
          <span style="float:right;font-size:14px;color:#e8dfcf;font-weight:500;">{artist_name}</span>
        </td>
      </tr>
      <tr>
        <td style="padding:10px 20px;">
          <span style="font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">Role</span>
          <span style="float:right;font-size:14px;color:#faae15;font-weight:600;">{role_display}</span>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- CTA Button -->
  <tr><td style="padding:28px 24px;text-align:center;">
    <a href="{invite_url}"
       style="display:inline-block;padding:16px 44px;background:#faae15;color:#121312;
              text-decoration:none;border-radius:8px;font-weight:bold;font-size:16px;
              letter-spacing:0.5px;">
      Activate My Account
    </a>
  </td></tr>

  <!-- Expiry Notice -->
  <tr><td style="padding:0 24px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(250,174,21,0.06);border:1px solid rgba(250,174,21,0.15);border-radius:6px;">
      <tr><td style="padding:12px 16px;text-align:center;">
        <p style="margin:0;font-size:13px;color:#b9ad96;">
          &#9201; This invite expires in <strong style="color:#e8dfcf;">{expires_in_hours} hours</strong>.
          If it expires, contact the shop owner for a new link.
        </p>
      </td></tr>
    </table>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- What's Inside -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:14px;line-height:1.7;">
    <p style="margin:0 0 12px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">
      Your Portal Includes
    </p>
    <ul style="margin:0;padding-left:20px;color:#b9ad96;">
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Booking Requests</strong> — review and manage client inquiries</li>
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Documents</strong> — send waivers and custom forms digitally</li>
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Artist Profile</strong> — control how you appear on the public site</li>
      <li><strong style="color:#e8dfcf;">Client Messaging</strong> — communicate with clients in one place</li>
    </ul>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 32px;text-align:center;
                 border-top:1px solid rgba(250,174,21,0.1);">
    <p style="margin:0 0 6px;font-size:12px;color:#b9ad96;">
      Ryozan Studio
    </p>
    <p style="margin:0;font-size:11px;color:#555;">
      Didn't expect this? You can safely ignore this email.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def build_login_welcome_html(
    artist_name: str,
    email: str,
    login_url: str,
    password: str,
) -> str:
    """
    Branded welcome email sent to existing artist accounts with their
    login credentials.  Dark/gold theme matching all other BTTCO emails.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  {_build_header("YOUR DASHBOARD IS READY")}

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:28px 24px 20px;color:#e8dfcf;font-size:15px;line-height:1.7;">
    <p style="margin:0 0 16px;">Hey {artist_name},</p>
    <p style="margin:0 0 16px;">
      Your <strong style="color:#faae15;">Ryozan Studio</strong> artist portal
      is live and ready for you. Log in to manage your bookings, documents, portfolio,
      and connect with clients &mdash; all in one place.
    </p>
  </td></tr>

  <!-- Credentials Card -->
  <tr><td style="padding:0 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(255,248,236,0.04);border:1px solid rgba(250,174,21,0.2);border-radius:8px;overflow:hidden;">
      <tr>
        <td style="padding:10px 20px;border-bottom:1px solid rgba(250,174,21,0.08);">
          <span style="font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">Email</span>
          <span style="float:right;font-size:14px;color:#e8dfcf;font-weight:500;">{email}</span>
        </td>
      </tr>
      <tr>
        <td style="padding:10px 20px;">
          <span style="font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">Password</span>
          <span style="float:right;font-size:14px;color:#faae15;font-weight:600;font-family:'Courier New',Courier,monospace;">{password}</span>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- Security Notice -->
  <tr><td style="padding:16px 24px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:rgba(250,174,21,0.06);border:1px solid rgba(250,174,21,0.15);border-radius:6px;">
      <tr><td style="padding:10px 16px;text-align:center;">
        <p style="margin:0;font-size:13px;color:#b9ad96;">
          &#128274; Please change your password after your first login.
        </p>
      </td></tr>
    </table>
  </td></tr>

  <!-- CTA Button -->
  <tr><td style="padding:28px 24px;text-align:center;">
    <a href="{login_url}"
       style="display:inline-block;padding:16px 44px;background:#faae15;color:#121312;
              text-decoration:none;border-radius:8px;font-weight:bold;font-size:16px;
              letter-spacing:0.5px;">
      Log In Now
    </a>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- What's Inside -->
  <tr><td style="padding:24px;color:#e8dfcf;font-size:14px;line-height:1.7;">
    <p style="margin:0 0 12px;font-size:12px;color:#b9ad96;text-transform:uppercase;letter-spacing:1px;">
      Your Portal Includes
    </p>
    <ul style="margin:0;padding-left:20px;color:#b9ad96;">
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Booking Requests</strong> &mdash; review and manage client inquiries</li>
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Documents</strong> &mdash; send waivers and custom forms digitally</li>
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Artist Profile</strong> &mdash; control how you appear on the public site</li>
      <li><strong style="color:#e8dfcf;">Client Messaging</strong> &mdash; communicate with clients in one place</li>
    </ul>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 32px;text-align:center;
                 border-top:1px solid rgba(250,174,21,0.1);">
    <p style="margin:0 0 6px;font-size:12px;color:#b9ad96;">
      Ryozan Studio
    </p>
    <p style="margin:0;font-size:11px;color:#555;">
      Questions? Reply to this email or reach out to your shop admin.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# ── Welcome Email (post-registration) ────────────────────────────────────


def build_welcome_html(name: str, role: str) -> str:
    """
    Branded welcome email fired after a new user registers.

    Content varies by role:
      - collector: mentions booking, aftercare, Collector's Club
      - artist/admin/etc: mentions portal, profile setup, getting started
    """
    settings = get_settings()
    portal_url = f"{settings.FRONTEND_URL}/portal"

    if role == "collector":
        subtitle = "WELCOME TO THE CLUB"
        body_copy = """
    <p style="margin:0 0 16px;">
      You're officially part of the <strong style="color:#faae15;">Ryozan Studio</strong>
      family. Your collector account is set up and ready to go.
    </p>
    <p style="margin:0 0 16px;">Here's what you can do now:</p>"""
        features = """
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Book a Session</strong> -- browse our artists and request your next tattoo</li>
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Aftercare Tracking</strong> -- get personalized healing instructions for every piece</li>
      <li><strong style="color:#e8dfcf;">Collector's Club</strong> -- earn rewards, unlock exclusive drops, and level up</li>"""
        cta_text = "Start Exploring"
    else:
        subtitle = "WELCOME TO THE TEAM"
        body_copy = f"""
    <p style="margin:0 0 16px;">
      Welcome to <strong style="color:#faae15;">Ryozan Studio</strong>.
      Your <strong style="color:#faae15;">{role.capitalize()}</strong> account is live
      and your portal is ready.
    </p>
    <p style="margin:0 0 16px;">Here's how to get started:</p>"""
        features = """
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Artist Portal</strong> -- your dashboard for bookings, documents, and clients</li>
      <li style="margin-bottom:6px;"><strong style="color:#e8dfcf;">Profile Setup</strong> -- add your bio, portfolio links, and booking preferences</li>
      <li><strong style="color:#e8dfcf;">Getting Started</strong> -- check your inbox for booking requests and start managing</li>"""
        cta_text = "Open My Portal"

    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  {_build_header(subtitle)}

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:28px 24px 20px;color:#e8dfcf;font-size:15px;line-height:1.7;">
    <p style="margin:0 0 16px;">Hey {name},</p>
    {body_copy}
  </td></tr>

  <!-- Features List -->
  <tr><td style="padding:0 24px 20px;">
    <ul style="margin:0;padding-left:20px;color:#b9ad96;font-size:14px;line-height:1.7;">
      {features}
    </ul>
  </td></tr>

  <!-- CTA Button -->
  <tr><td style="padding:20px 24px 28px;text-align:center;">
    <a href="{portal_url}"
       style="display:inline-block;padding:16px 44px;background:#faae15;color:#121312;
              text-decoration:none;border-radius:8px;font-weight:bold;font-size:16px;
              letter-spacing:0.5px;">
      {cta_text}
    </a>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <hr style="border:none;border-top:1px solid rgba(250,174,21,0.3);margin:0;">
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 32px;text-align:center;
                 border-top:1px solid rgba(250,174,21,0.1);">
    <p style="margin:0 0 6px;font-size:12px;color:#b9ad96;">
      Ryozan Studio
    </p>
    <p style="margin:0;font-size:11px;color:#555;">
      Questions? Reply to this email or reach out on Instagram.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


async def send_welcome_email(to_email: str, name: str, role: str) -> bool:
    """
    Fire a branded welcome email after registration.

    Never raises -- email is a side effect.  Returns True/False.
    """
    try:
        html = await render_db_template(
            "welcome",
            {
                "name": name,
                "role": role,
            },
        ) or build_welcome_html(name=name, role=role)
        return await send_email(
            to=to_email,
            subject="Welcome to Ryozan Studio",
            html_body=html,
            from_alias=FROM_SHOP,
        )
    except Exception as exc:
        logger.error("Welcome email failed for %s: %s", to_email, exc)
        return False


# ── Notification preference check ──────────────────────────────────────────

# Column mapping: (channel, category) → model field name
_PREF_FIELD_MAP = {
    ("email", "bookings"): "email_bookings",
    ("email", "messages"): "email_messages",
    ("email", "quests"): "email_quests",
    ("email", "marketing"): "email_marketing",
    ("push", "bookings"): "push_bookings",
    ("push", "messages"): "push_messages",
    ("push", "quests"): "push_quests",
}


async def should_notify(db, user_id, channel: str, category: str) -> bool:
    """
    Check if a user's notification preferences allow this notification.

    Args:
        db: AsyncSession
        user_id: UUID of the user to check
        channel: 'email' or 'push'
        category: 'bookings', 'messages', 'quests', 'marketing'

    Returns True if the preference allows it. Defaults to True if no
    preferences row exists (user hasn't configured prefs yet).
    """
    field = _PREF_FIELD_MAP.get((channel, category))
    if not field:
        # Unknown channel/category combo — allow by default
        return True

    from sqlalchemy import select

    from app.models.notification_preferences import NotificationPreferences

    result = await db.execute(select(NotificationPreferences).where(NotificationPreferences.user_id == user_id))
    prefs = result.scalar_one_or_none()
    if not prefs:
        # No prefs row = defaults (True for everything except marketing)
        return category != "marketing"

    return getattr(prefs, field, True)


# ── Automated Lifecycle Emails ─────────────────────────────────────────


def build_appointment_reminder_html(
    client_name: str,
    artist_name: str,
    scheduled_date: str,
    scheduled_time: str | None,
    description: str,
    reference_code: str,
) -> str:
    """24-hour appointment reminder email to the client."""
    time_str = f" at {_format_time_12hr(scheduled_time) or scheduled_time}" if scheduled_time else ""
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;border:1px solid rgba(250,174,21,0.15);border-radius:10px;overflow:hidden;">

  {_build_header("APPOINTMENT REMINDER")}

  <tr>
    <td style="padding:24px;">
      <p style="color:#e8dfcf;font-size:15px;line-height:1.6;margin:0 0 16px;">
        Hey <strong>{client_name}</strong>,
      </p>
      <p style="color:#e8dfcf;font-size:15px;line-height:1.6;margin:0 0 16px;">
        Just a reminder that your tattoo session with <strong style="color:#faae15;">{artist_name}</strong>
        is tomorrow, <strong>{scheduled_date}{time_str}</strong>.
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="background:rgba(250,174,21,0.06);border-radius:8px;border:1px solid rgba(250,174,21,0.15);margin:16px 0;">
        <tr><td style="padding:16px;">
          <p style="margin:0 0 8px;font-size:13px;color:#faae15;font-weight:bold;letter-spacing:0.5px;">SESSION DETAILS</p>
          <p style="margin:0;font-size:14px;color:#e8dfcf;line-height:1.5;">
            {description}<br>
            <span style="color:#b9ad96;">Ref: {reference_code}</span>
          </p>
        </td></tr>
      </table>
      <p style="color:#b9ad96;font-size:13px;line-height:1.5;margin:16px 0 0;">
        <strong>Before your session:</strong><br>
        &bull; Stay hydrated and eat a good meal<br>
        &bull; Avoid alcohol and blood thinners 24 hours before<br>
        &bull; Wear comfortable clothing that gives easy access to the tattoo area<br>
        &bull; Bring a valid photo ID
      </p>
    </td>
  </tr>

  <tr>
    <td style="padding:0 24px 24px;text-align:center;">
      <a href="{get_settings().FRONTEND_URL}{ROUTES.DASHBOARD}"
         style="display:inline-block;padding:12px 28px;background:#faae15;color:#121312;
                text-decoration:none;border-radius:6px;font-weight:bold;font-size:14px;">
        View My Appointment
      </a>
    </td>
  </tr>

  <tr>
    <td style="padding:12px 24px 20px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
      <p style="margin:0;font-size:11px;color:#555;">
        Ryozan Studio
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def build_healing_followup_html(
    client_name: str,
    artist_name: str,
    tattoo_description: str,
    weeks_since: int,
    reference_code: str,
) -> str:
    """Post-healing follow-up email (sent 2-4 weeks after session)."""
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;border:1px solid rgba(250,174,21,0.15);border-radius:10px;overflow:hidden;">

  {_build_header("HEALING CHECK-IN")}

  <tr>
    <td style="padding:24px;">
      <p style="color:#e8dfcf;font-size:15px;line-height:1.6;margin:0 0 16px;">
        Hey <strong>{client_name}</strong>,
      </p>
      <p style="color:#e8dfcf;font-size:15px;line-height:1.6;margin:0 0 16px;">
        It's been about {weeks_since} weeks since your session with {artist_name}
        for your <strong style="color:#faae15;">{tattoo_description}</strong>.
        How's it healing?
      </p>
      <p style="color:#b9ad96;font-size:14px;line-height:1.6;margin:0 0 16px;">
        By now your tattoo should be mostly healed. The ink has settled into the dermis
        and the surface should feel smooth. If you're still seeing any issues, don't
        hesitate to reach out.
      </p>
      <p style="color:#b9ad96;font-size:13px;line-height:1.5;margin:0 0 16px;">
        <strong>Long-term care:</strong><br>
        &bull; Apply sunscreen (SPF 30+) when exposed to sun<br>
        &bull; Keep the area moisturized<br>
        &bull; Avoid soaking in pools or hot tubs until fully healed
      </p>
    </td>
  </tr>

  <tr>
    <td style="padding:0 24px 24px;text-align:center;">
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;">
        <tr>
          <td style="padding:0 6px;">
            <a href="{get_settings().FRONTEND_URL}{ROUTES.COURIER}"
               style="display:inline-block;padding:12px 24px;background:#faae15;color:#121312;
                      text-decoration:none;border-radius:6px;font-weight:bold;font-size:13px;">
              Message {artist_name.split(" ")[0]}
            </a>
          </td>
          <td style="padding:0 6px;">
            <a href="{get_settings().FRONTEND_URL}{ROUTES.BOOK}"
               style="display:inline-block;padding:12px 24px;background:rgba(250,174,21,0.15);
                      color:#faae15;text-decoration:none;border-radius:6px;font-weight:bold;
                      font-size:13px;border:1px solid rgba(250,174,21,0.3);">
              Book Next Session
            </a>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <tr>
    <td style="padding:12px 24px 20px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
      <p style="margin:0;font-size:11px;color:#555;">
        Ryozan Studio
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def build_install_reminder_html(client_name: str, is_final: bool = False) -> str:
    """Install + notifications onboarding reminder (3h and 24h variants)."""
    settings = get_settings()
    app_url = f"{settings.FRONTEND_URL}/dashboard/quests"
    heading = "ONE LAST NUDGE" if is_final else "YOU'RE ALMOST SET UP"
    intro = (
        "This is your last reminder: install the Ryozan app and enable notifications "
        "to earn your <strong style='color:#faae15;'>Signal Flare</strong> quest reward "
        "before it disappears."
        if is_final
        else (
            "You're one step away from unlocking your "
            "<strong style='color:#faae15;'>Signal Flare</strong> quest reward. "
            "Install the app and enable notifications to claim your BTTCoin and XP."
        )
    )
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background-color:#121312;font-family:'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#121312;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;border:1px solid rgba(250,174,21,0.15);border-radius:10px;overflow:hidden;">

  {_build_header(heading)}

  <tr>
    <td style="padding:24px;">
      <p style="color:#e8dfcf;font-size:15px;line-height:1.6;margin:0 0 16px;">
        Hey <strong>{client_name}</strong>,
      </p>
      <p style="color:#e8dfcf;font-size:15px;line-height:1.6;margin:0 0 20px;">
        {intro}
      </p>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="background:rgba(250,174,21,0.06);border-radius:8px;border:1px solid rgba(250,174,21,0.15);margin:0 0 20px;">
        <tr><td style="padding:16px;">
          <p style="margin:0 0 10px;font-size:13px;color:#faae15;font-weight:bold;letter-spacing:0.5px;">WHY IT MATTERS</p>
          <p style="margin:0 0 8px;font-size:14px;color:#e8dfcf;line-height:1.5;">
            &bull; Instant alerts for flash drops and booking updates
          </p>
          <p style="margin:0 0 8px;font-size:14px;color:#e8dfcf;line-height:1.5;">
            &bull; One-tap access from your home screen
          </p>
          <p style="margin:0;font-size:14px;color:#e8dfcf;line-height:1.5;">
            &bull; Earn BTTCoin + XP for completing the quest
          </p>
        </td></tr>
      </table>

      <p style="color:#b9ad96;font-size:13px;line-height:1.5;margin:0 0 4px;">
        Takes less than 30 seconds. Open the app in your browser, then tap
        <strong style="color:#e8dfcf;">Quests</strong> to get started.
      </p>
    </td>
  </tr>

  <tr>
    <td style="padding:0 24px 24px;text-align:center;">
      <a href="{app_url}"
         style="display:inline-block;padding:12px 28px;background:#faae15;color:#121312;
                text-decoration:none;border-radius:6px;font-weight:bold;font-size:14px;">
        Install App &amp; Enable Notifications
      </a>
    </td>
  </tr>

  <tr>
    <td style="padding:12px 24px 20px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
      <p style="margin:0;font-size:11px;color:#555;">
        Ryozan Studio
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def build_booking_resume_reminder_html(client_name: str, book_url: str, join_url: str) -> str:
    """Single follow-up for unfinished public booking intake drafts."""
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background:#0d0d0d;font-family:Arial,sans-serif;color:#e8dfcf;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:24px 16px;">
<table role="presentation" width="100%" max-width="580px" cellpadding="0" cellspacing="0"
       style="max-width:580px;margin:0 auto;background:#121312;border:1px solid rgba(250,174,21,0.2);border-radius:12px;overflow:hidden;">

  {_build_header("DON'T FORGET")}

  <tr><td style="padding:32px 24px 24px;">
    <p style="margin:0 0 16px;font-size:16px;color:#e8dfcf;">Hey {client_name},</p>
    <p style="margin:0 0 16px;font-size:15px;color:#b9ad96;line-height:1.6;">
      You started a booking request at Ryozan Studio but did not finish it.
      If you still want to lock your session, finish your booking below.
    </p>
    <p style="margin:0 0 18px;font-size:14px;color:#a7a7a7;line-height:1.6;">
      We will not send additional reminders unless you explicitly opt in.
    </p>
    <div style="text-align:center;margin:26px 0 14px;">
      <a href="{book_url}"
         style="display:inline-block;padding:13px 32px;background:#faae15;color:#121312;
                text-decoration:none;border-radius:999px;font-weight:bold;font-size:14px;letter-spacing:0.4px;">
        Finish Booking
      </a>
    </div>
    <p style="margin:0;font-size:13px;color:#8f8f8f;line-height:1.6;text-align:center;">
      Want updates, drops, and member perks?
      <a href="{join_url}" style="color:#faae15;text-decoration:none;">Join the Club</a>.
    </p>
  </td></tr>

  <tr><td style="padding:12px 24px 20px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
    <p style="margin:0;font-size:11px;color:#555;">
      Ryozan Studio
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def build_scroll_request_html(
    artist_name: str,
    is_reminder: bool = False,
    *,
    action_url: str | None = None,
    eyebrow: str | None = None,
    body_intro: str | None = None,
) -> str:
    """Build the Artist Scroll request in the locked Email Foundry visual language."""
    action_url = action_url or f"{get_settings().FRONTEND_URL.rstrip('/')}{ROUTES.SCROLL}"
    eyebrow = eyebrow or ("REMINDER // ARTIST SCROLL" if is_reminder else "ACTION REQUIRED // ARTIST SCROLL")
    body_intro = body_intro or (
        "Your Artist Scroll is still incomplete and needs your signature."
        if is_reminder
        else "Your Artist Scroll is ready to be filled out and signed."
    )
    footer_address = studio_address_html(
        "margin:0 0 4px;font-family:'JetBrains Mono','Courier New',Courier,monospace;font-size:9.5px;"
        "letter-spacing:.16em;color:#756b5c;text-transform:uppercase;"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>Artist Scroll</title>
  <!--[if !mso]><!-->
  <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@700;800;900&amp;family=Space+Grotesk:wght@400;600;700&amp;family=JetBrains+Mono:wght@400;700&amp;display=swap" rel="stylesheet">
  <!--<![endif]-->
  <style>
    body{{margin:0;padding:0;}}
    a{{color:#c8a15e;}}
    img{{border:0;}}
    @media only screen and (max-width:480px){{
      .foundry-shop-name{{font-size:17px!important;letter-spacing:.12em!important;line-height:1.3!important;}}
      .foundry-spec-value{{font-size:10.5px!important;}}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#0b0c0b;">
<!-- {SCROLL_REQUEST_TEMPLATE_MARKER} -->
<span style="display:none;font-size:1px;color:#0b0c0b;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{body_intro}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0b0c0b" background="{STARSEED_TILE_URL}" style="width:100%;table-layout:fixed;background-color:#0b0c0b;background-image:url('{STARSEED_TILE_URL}');background-repeat:repeat;">
<tr><td align="center" style="padding:26px 14px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#191a19" style="max-width:600px;width:100%;table-layout:fixed;background-color:#191a19;border:1px solid #3a301e;border-radius:16px;">

  <tr><td align="center" style="padding:34px 24px 22px;border-bottom:1px solid #2a2316;">
    <img src="{FOUNDRY_EMAIL_ASSET_BASE}/tiger-mark-trim-crop.png" alt="Ryozan Studio" width="72" style="display:block;margin:0 auto 14px;">
    <div class="foundry-shop-name" style="font-family:'Poppins','Arial Black','Helvetica Neue',Arial,sans-serif;font-weight:900;font-size:19px;line-height:1.25;letter-spacing:.18em;color:#c8a15e;text-transform:uppercase;overflow-wrap:anywhere;">RYOZAN STUDIO</div>
    <div style="font-family:'JetBrains Mono','Courier New',Courier,monospace;font-size:9.5px;letter-spacing:.22em;color:rgba(232,223,207,.5);text-transform:uppercase;padding-top:8px;">{eyebrow}</div>
  </td></tr>

  <tr><td align="center" style="padding:22px 26px 0;">
    <span style="display:inline-block;padding:5px 12px;border:1px solid #e8dfcf;border-radius:99px;font-family:'JetBrains Mono','Courier New',Courier,monospace;font-size:9.5px;line-height:1;font-weight:700;letter-spacing:.16em;color:#e8dfcf;text-transform:uppercase;box-shadow:0 0 14px 1px rgba(236,205,143,.22);"><span style="font-size:7px;line-height:1;vertical-align:middle;">&#9679;</span><span style="vertical-align:middle;">&nbsp;&nbsp;SIGNATURE REQUIRED</span></span>
  </td></tr>

  <tr><td style="padding:24px 26px 0;font-family:'Space Grotesk','Helvetica Neue',Arial,sans-serif;font-size:14px;line-height:1.65;color:#e3dbcb;">
    Hey {artist_name},<br><br>{body_intro} It is required for health department inspections and facility records.
  </td></tr>

  <tr><td style="padding:20px 26px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#101110" style="background-color:#101110;border:1px solid #314641;border-radius:14px;">
      <tr><td style="padding:18px 18px 0;">
        <div style="font-family:'JetBrains Mono','Courier New',Courier,monospace;font-size:9.5px;font-weight:700;letter-spacing:.18em;color:#8a8577;text-transform:uppercase;padding-bottom:12px;">ARTIST RECORD &middot; AWAITING SIGNATURE</div>
        <div style="font-family:'Poppins','Arial Black','Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:23px;line-height:1.08;letter-spacing:.01em;color:#7fc3b1;text-transform:uppercase;">ARTIST</div>
        <div style="font-family:'Poppins','Arial Black','Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:23px;line-height:1.08;letter-spacing:.01em;color:#e8dfcf;text-transform:uppercase;">SCROLL</div>
        <div style="height:1px;background-color:#2c2d2b;margin-top:15px;font-size:0;line-height:0;">&nbsp;</div>
      </td></tr>
      <tr><td style="padding:15px 18px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="1" border="0" bgcolor="#33342f" style="width:100%;table-layout:fixed;background-color:#33342f;border-radius:8px;">
          <tr>
            <td width="50%" bgcolor="#141514" style="background-color:#141514;padding:11px 12px;"><div style="font-family:'JetBrains Mono','Courier New',Courier,monospace;font-size:8.5px;font-weight:700;letter-spacing:.16em;color:#8a8577;text-transform:uppercase;">REQUIRED FOR</div><div style="font-family:'Poppins','Arial Black','Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:12.5px;letter-spacing:.02em;color:#e8dfcf;text-transform:uppercase;padding-top:4px;">SHOP RECORDS</div></td>
            <td width="50%" bgcolor="#141514" style="background-color:#141514;padding:11px 12px;"><div style="font-family:'JetBrains Mono','Courier New',Courier,monospace;font-size:8.5px;font-weight:700;letter-spacing:.16em;color:#8a8577;text-transform:uppercase;">REWARD</div><div class="foundry-spec-value" style="font-family:'Poppins','Arial Black','Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:12.5px;line-height:1.25;letter-spacing:.02em;color:#e8dfcf;text-transform:uppercase;padding-top:4px;overflow-wrap:anywhere;">500 BTTCOIN + 150 XP</div></td>
          </tr>
        </table>
      </td></tr>
      <tr><td style="padding:16px 18px 16px;font-family:'Space Grotesk','Helvetica Neue',Arial,sans-serif;font-size:12.5px;line-height:1.65;color:#a89d8a;">Read each section, upload the required records, accept the terms, and add your signatures.</td></tr>
    </table>
  </td></tr>

  <tr><td style="padding:20px 26px 0;font-family:'Space Grotesk','Helvetica Neue',Arial,sans-serif;font-size:14px;line-height:1.65;color:#a89d8a;">
    If you have a question about any section, highlight the text, tap <strong style="color:#c8a15e;">Ask the studio</strong>, and send it through Courier.
  </td></tr>

  <tr><td align="center" style="padding:24px 26px 10px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
      <td bgcolor="#d38a68" style="background-color:#d38a68;border-radius:99px;box-shadow:0 0 22px 2px rgba(211,138,104,.45);">
        <a href="{action_url}" style="display:block;padding:13px 34px;font-family:'JetBrains Mono','Courier New',Courier,monospace;font-size:11px;font-weight:700;letter-spacing:.14em;text-indent:.14em;text-align:center;text-transform:uppercase;color:#29140e;text-decoration:none;">COMPLETE ARTIST SCROLL</a>
      </td>
    </tr></table>
  </td></tr>
  <tr><td align="center" style="padding:0 26px 30px;font-family:'JetBrains Mono','Courier New',Courier,monospace;font-size:9.5px;letter-spacing:.14em;color:#756b5c;text-transform:uppercase;">Complete every section before signing.</td></tr>

  <tr><td align="center" style="padding:26px 24px 40px;border-top:1px solid #2a2316;">
    <p style="margin:0 0 10px;font-family:'Space Grotesk','Helvetica Neue',Arial,sans-serif;font-size:12.5px;color:#a89d8a;">Questions? Reply here or ask the studio in Courier.</p>
    {footer_address}
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# ── Password reset ─────────────────────────────────────────────────────────


def humanize_minutes(minutes: int) -> str:
    """Render a token lifetime the way a person would say it out loud.

    A 24 hour window is 1440 minutes, and "expires in 1440 minutes" reads like
    a bug to the recipient. Minutes stay minutes below an hour; whole hours and
    whole days are promoted.
    """
    minutes = int(minutes)
    if minutes < 60 or minutes % 60:
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    hours = minutes // 60
    if hours <= 48 or hours % 24:
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    days = hours // 24
    return f"{days} day" if days == 1 else f"{days} days"


def build_password_reset_html(name: str, reset_url: str, expires_in_minutes: int) -> str:
    """Branded password-reset email. The link is the only place the token appears."""
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background:#0d0d0d;font-family:Arial,sans-serif;color:#e8dfcf;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:24px 16px;">
<table role="presentation" width="100%" max-width="580px" cellpadding="0" cellspacing="0"
       style="max-width:580px;margin:0 auto;background:#121312;border:1px solid rgba(250,174,21,0.2);border-radius:12px;overflow:hidden;">

  {_build_header("RESET YOUR PASSWORD")}

  <tr><td style="padding:32px 24px 24px;">
    <p style="margin:0 0 16px;font-size:16px;color:#e8dfcf;">Hey {name},</p>
    <p style="margin:0 0 16px;font-size:15px;color:#b9ad96;line-height:1.6;">
      Here is your link to set a new password for your Ryozan Studio
      account.
    </p>
    <div style="text-align:center;margin:28px 0;">
      <a href="{reset_url}"
         style="display:inline-block;padding:14px 36px;background:#faae15;color:#121312;
                text-decoration:none;border-radius:999px;font-weight:bold;font-size:15px;
                letter-spacing:0.5px;">
        Reset Password
      </a>
    </div>
    <p style="margin:0 0 12px;font-size:13px;color:#a7a7a7;line-height:1.6;">
      This link expires in {humanize_minutes(expires_in_minutes)} and can only be used once.
    </p>
    <p style="margin:0;font-size:13px;color:#8f8f8f;line-height:1.6;">
      Not expecting this? You can ignore it. Nothing changes until you open the
      link and set a new password.
    </p>
  </td></tr>

  <tr><td style="padding:12px 24px 20px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
    <p style="margin:0;font-size:11px;color:#555;">
      Ryozan Studio
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


async def send_password_reset_email(to_email: str, name: str, reset_url: str, expires_in_minutes: int) -> bool:
    """
    Fire a branded password-reset email.

    Never raises -- email is a side effect. Returns True/False. The reset URL
    (and therefore the token) is never logged, including on failure.
    """
    try:
        html = await render_db_template(
            "password_reset",
            {
                "name": name,
                "reset_url": reset_url,
                # {{expires_in}} is the phrase to render ("24 hours").
                # {{expires_in_minutes}} stays for templates stored before the
                # window became configurable; it is a bare number, so any
                # template using it must supply its own "minutes" wording.
                "expires_in": humanize_minutes(expires_in_minutes),
                "expires_in_minutes": str(expires_in_minutes),
            },
        ) or build_password_reset_html(
            name=name,
            reset_url=reset_url,
            expires_in_minutes=expires_in_minutes,
        )
        return await send_email(
            to=to_email,
            subject="Reset your Ryozan Studio password",
            html_body=html,
            from_alias=FROM_ADMIN,
        )
    except Exception as exc:
        logger.error("Password reset email failed for %s: %s", to_email, type(exc).__name__)
        return False


def build_password_changed_html(name: str, recover_url: str) -> str:
    """Plain confirmation that a password was updated.

    Sent after every successful reset. It still carries the recovery link,
    because this is the only notice an account holder would get if someone else
    redeemed a reset link for them. But it reads as a confirmation, not an
    alarm: the overwhelmingly common case is someone who just set their own
    password on purpose, and greeting them with an intrusion warning is both
    frightening and wrong.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background:#0d0d0d;font-family:Arial,sans-serif;color:#e8dfcf;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:24px 16px;">
<table role="presentation" width="100%" max-width="580px" cellpadding="0" cellspacing="0"
       style="max-width:580px;margin:0 auto;background:#121312;border:1px solid rgba(250,174,21,0.2);border-radius:12px;overflow:hidden;">

  {_build_header("PASSWORD UPDATED")}

  <tr><td style="padding:32px 24px 24px;">
    <p style="margin:0 0 16px;font-size:16px;color:#e8dfcf;">Hey {name},</p>
    <p style="margin:0 0 16px;font-size:15px;color:#b9ad96;line-height:1.6;">
      Your Ryozan Studio password has been updated. You are all set, and
      you can sign in with it now.
    </p>
    <p style="margin:0;font-size:13px;color:#8f8f8f;line-height:1.6;">
      Did not make this change?
      <a href="{recover_url}" style="color:#faae15;text-decoration:underline;">Reset your password</a>.
    </p>
  </td></tr>

  <tr><td style="padding:12px 24px 20px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
    <p style="margin:0;font-size:11px;color:#555;">
      Ryozan Studio
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


async def send_password_changed_email(to_email: str, name: str, recover_url: str = "") -> bool:
    """Fire the password-changed notice. Never raises: a reset already succeeded."""
    try:
        settings = get_settings()
        url = recover_url or f"{settings.FRONTEND_URL}/portal?recover=1"
        html = await render_db_template(
            "password_changed", {"name": name, "recover_url": url}
        ) or build_password_changed_html(name=name, recover_url=url)
        return await send_email(
            to=to_email,
            subject="Your Ryozan Studio password was changed",
            html_body=html,
            from_alias=FROM_ADMIN,
        )
    except Exception as exc:
        logger.error("Password changed email failed for %s: %s", to_email, type(exc).__name__)
        return False


def build_portal_migration_html(name: str, google_url: str, claim_url: str) -> str:
    """Migration notice for accounts that have never been claimed.

    Deliberately carries no token. A reset link in a bulk send is a standing
    key to that account sitting in an inbox nobody controls, and this goes to
    every unclaimed account at once. Both buttons start a fresh, short-lived
    flow at the moment the person actually acts.

    Google is the primary path because most of these addresses are Gmail, and
    that route needs no password and cannot expire.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
{_email_head()}
<body style="margin:0;padding:0;background:#0d0d0d;font-family:Arial,sans-serif;color:#e8dfcf;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:24px 16px;">
<table role="presentation" width="100%" max-width="580px" cellpadding="0" cellspacing="0"
       style="max-width:580px;margin:0 auto;background:#121312;border:1px solid rgba(250,174,21,0.2);border-radius:12px;overflow:hidden;">

  {_build_header("YOUR PORTAL IS READY")}

  <tr><td style="padding:32px 24px 24px;">
    <p style="margin:0 0 16px;font-size:16px;color:#e8dfcf;">Hey {name},</p>
    <p style="margin:0 0 16px;font-size:15px;color:#b9ad96;line-height:1.6;">
      We rebuilt the Ryozan portal. Your bookings and history are already in
      there waiting for you, but the move means your account needs a way to sign in.
    </p>
    <p style="margin:0 0 8px;font-size:15px;color:#b9ad96;line-height:1.6;">
      The quickest way is Google. One tap, no password to remember.
    </p>
    <div style="text-align:center;margin:24px 0 16px;">
      <a href="{google_url}"
         style="display:inline-block;padding:14px 36px;background:#faae15;color:#121312;
                text-decoration:none;border-radius:999px;font-weight:bold;font-size:15px;
                letter-spacing:0.5px;">
        Continue with Google
      </a>
    </div>
    <p style="margin:0 0 20px;font-size:13px;color:#a7a7a7;line-height:1.6;text-align:center;">
      Rather use a password?
      <a href="{claim_url}" style="color:#faae15;text-decoration:underline;">Set one here</a>.
    </p>
    <p style="margin:0;font-size:13px;color:#8f8f8f;line-height:1.6;">
      Nothing about your bookings changed, and you do not need to do anything today.
      This is here whenever you want it.
    </p>
  </td></tr>

  <tr><td style="padding:12px 24px 20px;text-align:center;border-top:1px solid rgba(250,174,21,0.1);">
    <p style="margin:0;font-size:11px;color:#555;">
      Ryozan Studio
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


async def send_portal_migration_email(to_email: str, name: str) -> bool:
    """Fire the migration notice. Carries no credential of any kind."""
    try:
        settings = get_settings()
        google_url = f"{settings.API_PUBLIC_BASE_URL}/api/v1/auth/google/start"
        claim_url = f"{settings.FRONTEND_URL}/portal?recover=1"
        html = await render_db_template(
            "portal_migration",
            {"name": name, "google_url": google_url, "claim_url": claim_url},
        ) or build_portal_migration_html(name=name, google_url=google_url, claim_url=claim_url)
        return await send_email(
            to=to_email,
            subject="Your Ryozan Studio portal is ready",
            html_body=html,
            from_alias=FROM_ADMIN,
        )
    except Exception as exc:
        logger.error("Portal migration email failed for %s: %s", to_email, type(exc).__name__)
        return False
