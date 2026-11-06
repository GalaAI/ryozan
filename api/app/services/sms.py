"""SMS service — Twilio Programmable Messaging.

Mirrors `app.services.email` structurally so the conversation notifier and
other call sites can branch identically. Every send logs to `ryozan_sms_messages`
regardless of outcome, so the founder UI has a real audit trail.

Hard rules:
  * Never send SMS to a number that has opted out (`ryozan_users.sms_opt_out_at IS NOT NULL`)
    or has never opted in (`sms_opt_in = false`). Returns `False` silently so the
    caller falls back to email.
  * Always include a STOP footer on marketing/broadcast messages. Transactional
    messages (booking confirmations) are exempt under CTIA.
  * Normalise to E.164 before sending. Accept US 10-digit/11-digit (+1)
    numbers and pre-normalised international E.164 numbers.
  * Render `{{variable}}` placeholders the same way email templates do.
"""

import contextlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.sms_message import SmsMessage
from app.models.sms_template import SmsTemplate
from app.models.user import User

logger = logging.getLogger("ryozan.sms")


def project_sms_delivery_status(status: str | None) -> str:
    """Normalize provider callbacks without promoting queued work to sent."""
    normalized = str(status or "queued").strip().lower()
    if normalized in {"failed", "undelivered", "canceled"}:
        return "failed"
    if normalized in {"delivered", "read"}:
        return "delivered"
    if normalized in {"sent", "dev_skipped"}:
        return "sent"
    if normalized == "sending":
        return "sending"
    return "queued"


# Maximum body length before Twilio splits into multiple segments. We don't
# hard-cap, but we warn so the founder UI can show segment count.
SEGMENT_LIMIT_GSM7 = 160
SEGMENT_LIMIT_UCS2 = 70

# CTIA-required footer for marketing messages. Transactional messages
# (booking confirmations etc.) are exempt.
STOP_FOOTER = "\nReply STOP to opt out."


# ── Studio contact lines ─────────────────────────────────────────

# Address for a contact known only by phone. example.com is reserved (RFC 2606) and
# accepts no mail, so nothing sent to a placeholder reaches a real inbox.
PLACEHOLDER_EMAIL_DOMAIN = "contacts.example.com"


def placeholder_contact_email(local_part: str) -> str:
    return f"{local_part}@{PLACEHOLDER_EMAIL_DOMAIN}"


def public_site_host() -> str:
    """This deployment's site host for SMS text (no scheme, to save characters)."""
    return get_settings().public_site_url.split("://", 1)[-1]


def sms_help_reply() -> str:
    """The HELP keyword reply, naming the studio's own contact address and site."""
    settings = get_settings()
    contact = (settings.SHOP_EMAIL or "").strip()
    reach = f"email {contact} or visit {public_site_host()}" if contact else f"visit {public_site_host()}"
    return (
        f"{settings.STUDIO_NAME}: For help, {reach}. Msg freq varies. Msg & data rates may apply. Reply STOP to cancel."
    )


# ── Phone normalisation ──────────────────────────────────────────


_DIGITS = re.compile(r"\D")


def normalise_phone(raw: str | None) -> str | None:
    """Convert any reasonable US phone-shaped input into E.164.

    >>> normalise_phone("(555) 555-0123")
    '+15555550123'
    >>> normalise_phone("+15555550123")
    '+15555550123'
    >>> normalise_phone("5555550123")
    '+15555550123'
    >>> normalise_phone("15555550123")
    '+15555550123'
    >>> normalise_phone("not a phone") is None
    True
    """
    if not raw:
        return None
    digits = _DIGITS.sub("", raw)
    if not digits:
        return None
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.startswith("+") and 10 <= len(digits) <= 15:
        return f"+{digits}"
    return None


# ── Template rendering ──────────────────────────────────────────


_VAR_RE = re.compile(r"\{\{(\s*\w+\s*)\}\}")


def render_template(body: str, data: dict[str, str]) -> str:
    """Replace {{variable}} placeholders. Identical semantics to email rendering."""

    def replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        return data.get(key, match.group(0))

    return _VAR_RE.sub(replacer, body)


# ── Opt-out helpers ──────────────────────────────────────────


async def is_opted_out(db: AsyncSession, phone_e164: str) -> bool:
    """Return True if any user matching this phone has explicitly opted out.

    Phone may live on multiple user rows (artist + collector etc.); we treat
    *any* opt-out as authoritative for that number.
    """
    has_opt_in, has_opt_out = await _get_opt_state(db, phone_e164)
    return has_opt_out


async def is_opted_in(db: AsyncSession, phone_e164: str) -> bool:
    """Return True if at least one user matching this phone has opted in."""
    has_opt_in, has_opt_out = await _get_opt_state(db, phone_e164)
    return has_opt_in and not has_opt_out


async def _get_opt_state(db: AsyncSession, phone_e164: str) -> tuple[bool, bool]:
    """Return (has_opt_in, has_opt_out) for all rows matching this phone."""
    result = await db.execute(select(User.phone, User.sms_opt_in, User.sms_opt_out_at).where(User.phone.is_not(None)))
    has_opt_in = False
    has_opt_out = False
    for phone, sms_opt_in, sms_opt_out_at in result.all():
        if normalise_phone(phone) != phone_e164:
            continue
        if sms_opt_out_at is not None:
            has_opt_out = True
        elif sms_opt_in:
            has_opt_in = True
    return has_opt_in, has_opt_out


async def set_opt_out(db: AsyncSession, phone_e164: str) -> int:
    """Mark every user with this phone as opted-out. Returns row count."""
    now = datetime.now(UTC)
    result = await db.execute(select(User).where(User.phone.is_not(None)))
    count = 0
    for user in result.scalars():
        if normalise_phone(user.phone) == phone_e164:
            user.sms_opt_out_at = now
            user.sms_opt_in = False
            count += 1
    await db.flush()
    logger.info("sms.opt_out phone=%s users_affected=%d", phone_e164, count)
    return count


async def set_opt_in(db: AsyncSession, phone_e164: str) -> int:
    """Mark every user with this phone as opted-in (clears prior opt-out)."""
    now = datetime.now(UTC)
    result = await db.execute(select(User).where(User.phone.is_not(None)))
    count = 0
    for user in result.scalars():
        if normalise_phone(user.phone) == phone_e164:
            user.sms_opt_in = True
            user.sms_opt_in_at = now
            user.sms_opt_out_at = None
            count += 1
    await db.flush()
    logger.info("sms.opt_in phone=%s users_affected=%d", phone_e164, count)
    return count


# ── Twilio API client ──────────────────────────────────────────


class TwilioError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class SmsLaneConfigurationError(Exception):
    """Raised when a configured SMS identity cannot be resolved unambiguously."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SmsLane:
    id: str
    label: str
    from_number: str
    assigned_agent_id: str
    assigned_agent_label: str
    reply_mode: str
    voice_status: str


_SMS_LANE_FIELDS = {
    "bella": (
        "Bella",
        "TWILIO_BELLA_FROM_NUMBER",
        "bella",
        "Bella",
        "gated_auto_reply",
        "ELEVENLABS_RYOZAN_AGENT_ID",
    ),
    "gala": (
        "Gala",
        "TWILIO_GALA_FROM_NUMBER",
        "gala",
        "Gala",
        "human_operated",
        "ELEVENLABS_JUSTIN_AGENT_ID",
    ),
    "ryozan-collector": (
        "ryozan-collector",
        "TWILIO_RYOZAN_COLLECTOR_FROM_NUMBER",
        "sigma",
        "Sigma",
        "human_operated",
        None,
    ),
}


def _voice_status(settings: Any, provider_agent_field: str | None) -> str:
    """Report provider configuration only, never runtime or call readiness."""
    if not provider_agent_field:
        return "not_configured"
    configured = bool(getattr(settings, "ELEVENLABS_API_KEY", "") and getattr(settings, provider_agent_field, ""))
    return "provider_configured" if configured else "not_configured"


def _configured_sms_lanes(settings: Any | None = None) -> dict[str, SmsLane]:
    settings = settings or get_settings()
    lanes: dict[str, SmsLane] = {}
    claimed_numbers: dict[str, str] = {}
    for lane_id, (
        label,
        field_name,
        assigned_agent_id,
        assigned_agent_label,
        reply_mode,
        provider_agent_field,
    ) in _SMS_LANE_FIELDS.items():
        raw_number = str(getattr(settings, field_name, "") or "").strip()
        if not raw_number:
            continue
        from_number = normalise_phone(raw_number)
        if not from_number:
            raise SmsLaneConfigurationError("INVALID_SENDER", f"{field_name} must be a valid E.164 phone number")
        claimed_by = claimed_numbers.get(from_number)
        if claimed_by:
            raise SmsLaneConfigurationError(
                "DUPLICATE_SENDER",
                f"SMS sender identity is assigned to both {claimed_by} and {lane_id}",
            )
        claimed_numbers[from_number] = lane_id
        lanes[lane_id] = SmsLane(
            id=lane_id,
            label=label,
            from_number=from_number,
            assigned_agent_id=assigned_agent_id,
            assigned_agent_label=assigned_agent_label,
            reply_mode=reply_mode,
            voice_status=_voice_status(settings, provider_agent_field),
        )
    return lanes


SMS_ERROR_CODE_MAX = 40


def _clip_error_code(code: object) -> str | None:
    """Fit a provider or lane error code into ryozan_sms_messages.error_code.

    A code longer than the column would fail the flush and roll back the whole
    request session, turning a logged SMS failure into a failed booking.
    """
    if code is None:
        return None
    text = str(code)
    return text[:SMS_ERROR_CODE_MAX] if len(text) > SMS_ERROR_CODE_MAX else text


def resolve_sms_lane_by_id(lane_id: str, settings: Any | None = None) -> SmsLane:
    normalized_id = str(lane_id or "").strip().lower()
    if normalized_id not in _SMS_LANE_FIELDS:
        raise SmsLaneConfigurationError("UNKNOWN_LANE", f"Unknown SMS sender lane: {normalized_id or '(blank)'}")
    lane = _configured_sms_lanes(settings).get(normalized_id)
    if not lane:
        field_name = _SMS_LANE_FIELDS[normalized_id][1]
        raise SmsLaneConfigurationError("SENDER_NOT_CONFIGURED", f"{field_name} is not configured")
    return lane


def resolve_sms_lane_by_number(to_number: str | None, settings: Any | None = None) -> SmsLane:
    normalized_number = normalise_phone(to_number)
    if not normalized_number:
        raise SmsLaneConfigurationError("INVALID_DESTINATION", "Inbound Twilio To number is not valid E.164")
    lanes = _configured_sms_lanes(settings)
    matches = [lane for lane in lanes.values() if lane.from_number == normalized_number]
    if len(matches) != 1:
        raise SmsLaneConfigurationError(
            "UNKNOWN_DESTINATION" if not matches else "DUPLICATE_SENDER",
            "Inbound Twilio To number does not resolve to exactly one configured SMS lane",
        )
    return matches[0]


def mask_sms_sender(number: str) -> str:
    normalized = normalise_phone(number)
    return f"***-***-{normalized[-4:]}" if normalized else "Not configured"


def public_sms_lane_status(settings: Any | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    try:
        lanes = _configured_sms_lanes(settings)
        configuration_error = None
    except SmsLaneConfigurationError as exc:
        lanes = {}
        configuration_error = exc.code
    statuses: list[dict[str, Any]] = []
    for lane_id, (
        label,
        _field_name,
        assigned_agent_id,
        assigned_agent_label,
        reply_mode,
        provider_agent_field,
    ) in _SMS_LANE_FIELDS.items():
        lane = lanes.get(lane_id)
        statuses.append(
            {
                "id": lane_id,
                "label": label,
                "configured": lane is not None,
                "masked_number": mask_sms_sender(lane.from_number) if lane else None,
                "status": "ready" if lane and not configuration_error else "blocked",
                "configuration_error": configuration_error,
                "assigned_agent_id": assigned_agent_id,
                "assigned_agent_label": assigned_agent_label,
                "assignment_status": "assigned",
                "reply_mode": reply_mode,
                "auto_reply_enabled": bool(
                    lane_id == "bella" and lane is not None and getattr(settings, "BELLA_SMS_AUTO_REPLY_ENABLED", False)
                ),
                "voice_status": _voice_status(settings, provider_agent_field),
            }
        )
    return statuses


async def _twilio_post_message(
    *,
    to: str,
    body: str,
    channel: str = "sms",
    sms_lane: SmsLane | None = None,
) -> dict:
    """Raw Twilio Messages API POST. Raises TwilioError on non-2xx.

    SMS requires both the Messaging Service SID and one explicit configured
    sender identity. WhatsApp retains its existing dedicated From behavior.
    """
    settings = get_settings()
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        raise TwilioError("CONFIG", "Twilio credentials not configured")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    status_callback = f"{settings.API_PUBLIC_BASE_URL.rstrip('/')}/api/v1/sms/webhook/status"
    is_whatsapp = channel == "whatsapp"
    destination = f"whatsapp:{to}" if is_whatsapp else to
    data = {"To": destination, "Body": body, "StatusCallback": status_callback}
    if is_whatsapp and settings.TWILIO_FROM_NUMBER:
        data["From"] = f"whatsapp:{settings.TWILIO_FROM_NUMBER}"
    elif is_whatsapp:
        raise TwilioError("CONFIG", "TWILIO_FROM_NUMBER is not configured for WhatsApp")
    else:
        try:
            lane = sms_lane or resolve_sms_lane_by_id("bella", settings)
        except SmsLaneConfigurationError as exc:
            raise TwilioError(exc.code, exc.message) from exc
        if not settings.TWILIO_MESSAGING_SERVICE_SID:
            raise TwilioError("CONFIG", "TWILIO_MESSAGING_SERVICE_SID is not configured")
        data["MessagingServiceSid"] = settings.TWILIO_MESSAGING_SERVICE_SID
        data["From"] = lane.from_number

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url,
            data=data,
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
        )
    if resp.status_code >= 300:
        try:
            err = resp.json()
            raise TwilioError(str(err.get("code", resp.status_code)), err.get("message", resp.text))
        except ValueError:
            raise TwilioError(str(resp.status_code), resp.text[:200]) from None
    return resp.json()


# ── Public API ──────────────────────────────────────────


async def send_sms(
    *,
    db: AsyncSession,
    to: str,
    body: str,
    template_slug: str | None = None,
    conversation_id: UUID | None = None,
    courier_message_id: UUID | None = None,
    user_id: UUID | None = None,
    sender_id: UUID | None = None,
    is_transactional: bool = True,
    skip_opt_in_check: bool = False,
    lane_id: str = "bella",
) -> SmsMessage:
    """Send an SMS via Twilio. Always returns the log row (even on failure).

    Args:
        to: Phone number in any reasonable format; normalised to E.164 here.
        body: Message body. `{{vars}}` already rendered by caller.
        template_slug: Slug if this was rendered from a template (for logging).
        conversation_id: Optional link to a `ryozan_conversations` row.
        courier_message_id: Optional owning Courier Message for callback projection.
        user_id: The recipient user, if known.
        sender_id: The staff user initiating the send (None for system/automation).
        is_transactional: When False, appends "Reply STOP to opt out." footer.
        skip_opt_in_check: Set True ONLY for the registration-confirmation message
            itself (one-time double opt-in confirmation). Everything else MUST
            respect opt-in state.

    Returns:
        The persisted `SmsMessage` row. Check `.status` for outcome.
    """
    settings = get_settings()
    lane: SmsLane | None = None
    lane_error: SmsLaneConfigurationError | None = None
    try:
        lane = resolve_sms_lane_by_id(lane_id, settings)
    except SmsLaneConfigurationError as exc:
        lane_error = exc
    from_phone = lane.from_number if lane else "(unset)"
    e164 = normalise_phone(to)

    log = SmsMessage(
        direction="outbound",
        to_phone=e164 or to,
        from_phone=from_phone,
        body=body,
        status="queued",
        template_slug=template_slug,
        conversation_id=conversation_id,
        courier_message_id=courier_message_id,
        user_id=user_id,
        sender_id=sender_id,
    )
    db.add(log)

    if lane_error:
        log.status = "failed"
        log.error_code = _clip_error_code(lane_error.code)
        log.error_message = lane_error.message
        await db.flush()
        logger.error("sms.send.sender_lane_blocked lane=%s code=%s", lane_id, lane_error.code)
        return log

    from app.services.demo_shop import demo_outbound_refusal

    demo_refusal = demo_outbound_refusal("sms", e164 or to)
    if demo_refusal:
        log.status = "failed"
        log.error_code = "DEMO_SHOP"
        log.error_message = f"Demo shop: {demo_refusal}"
        await db.flush()
        logger.info("sms.send.demo_refused to=%s reason=%s", e164 or to, demo_refusal)
        return log

    if settings.LOCAL_DEV_MODE:
        log.status = "dev_skipped"
        log.twilio_sid = f"dev-mock-{int(datetime.now(UTC).timestamp())}"
        await db.flush()
        logger.info("[LOCAL_DEV_MODE] SMS skipped. to=%s template=%s", e164 or to, template_slug)
        return log

    # Validation ↓
    if not e164:
        log.status = "failed"
        log.error_code = "INVALID_PHONE"
        log.error_message = f"Could not normalise {to!r} to E.164"
        await db.flush()
        logger.warning("sms.send.invalid_phone raw=%r", to)
        return log

    has_opt_in, has_opt_out = await _get_opt_state(db, e164)

    if has_opt_out:
        log.status = "failed"
        log.error_code = "OPTED_OUT"
        log.error_message = "Recipient has opted out of SMS"
        await db.flush()
        logger.info("sms.send.skip_opted_out to=%s", e164)
        return log

    if not skip_opt_in_check and not has_opt_in:
        log.status = "failed"
        log.error_code = "NO_OPT_IN"
        log.error_message = "Recipient has not opted into SMS"
        await db.flush()
        logger.info("sms.send.skip_no_opt_in to=%s", e164)
        return log

    final_body = body if is_transactional else body + STOP_FOOTER
    log.body = final_body

    # Send ↓
    try:
        result = await _twilio_post_message(to=e164, body=final_body, sms_lane=lane)
        log.status = result.get("status", "sent")  # twilio returns "queued"/"sending"
        log.twilio_sid = result.get("sid")
        log.num_segments = int(result.get("num_segments", 1) or 1)
        price = result.get("price")
        if price is not None:
            with contextlib.suppress(TypeError, ValueError):
                # Twilio prices are negative (debits); store absolute cents.
                log.price_cents = int(round(float(price) * -100))
        logger.info(
            "sms.send.ok to=%s sid=%s segments=%s",
            e164,
            log.twilio_sid,
            log.num_segments,
        )
    except TwilioError as e:
        log.status = "failed"
        log.error_code = _clip_error_code(e.code)
        log.error_message = e.message
        logger.error("sms.send.fail to=%s code=%s msg=%s", e164, e.code, e.message)
    except Exception as e:  # network, etc.
        log.status = "failed"
        log.error_code = "EXCEPTION"
        log.error_message = str(e)[:500]
        logger.exception("sms.send.exception to=%s", e164)

    await db.flush()
    return log


async def send_twilio_whatsapp_text(
    *,
    db: AsyncSession,
    to: str,
    body: str,
    conversation_id: UUID | None = None,
    courier_message_id: UUID | None = None,
    user_id: UUID | None = None,
    sender_id: UUID | None = None,
) -> SmsMessage:
    """Send a freeform WhatsApp reply through the Twilio-owned shop sender.

    This transport is intentionally separate from SMS consent checks. Twilio and
    WhatsApp enforce the customer-service window, while STOP handling remains in
    the shared inbound webhook for conservative opt-out behavior.
    """
    settings = get_settings()
    e164 = normalise_phone(to)
    from_phone = settings.TWILIO_FROM_NUMBER or "(unset)"
    log = SmsMessage(
        direction="outbound",
        to_phone=e164 or to,
        from_phone=from_phone,
        body=body,
        status="queued",
        conversation_id=conversation_id,
        courier_message_id=courier_message_id,
        user_id=user_id,
        sender_id=sender_id,
    )
    db.add(log)

    if settings.LOCAL_DEV_MODE:
        log.status = "dev_skipped"
        log.twilio_sid = f"dev-mock-{int(datetime.now(UTC).timestamp())}"
        await db.flush()
        return log

    if not e164:
        log.status = "failed"
        log.error_code = "INVALID_PHONE"
        log.error_message = f"Could not normalise {to!r} to E.164"
        await db.flush()
        return log

    try:
        result = await _twilio_post_message(to=e164, body=body, channel="whatsapp")
        log.status = result.get("status", "sent")
        log.twilio_sid = result.get("sid")
        log.num_segments = int(result.get("num_segments", 1) or 1)
        logger.info("whatsapp.send.ok to=%s sid=%s", e164, log.twilio_sid)
    except TwilioError as exc:
        log.status = "failed"
        log.error_code = _clip_error_code(exc.code)
        log.error_message = exc.message
        logger.error("whatsapp.send.fail to=%s code=%s msg=%s", e164, exc.code, exc.message)
    except Exception as exc:
        log.status = "failed"
        log.error_code = "EXCEPTION"
        log.error_message = str(exc)[:500]
        logger.exception("whatsapp.send.exception to=%s", e164)

    await db.flush()
    return log


async def send_from_template(
    *,
    db: AsyncSession,
    slug: str,
    to: str,
    variables: dict[str, str],
    conversation_id: UUID | None = None,
    courier_message_id: UUID | None = None,
    user_id: UUID | None = None,
    sender_id: UUID | None = None,
    is_transactional: bool = True,
    skip_opt_in_check: bool = False,
    lane_id: str = "bella",
) -> SmsMessage | None:
    """Render and send a stored template. Returns None if template missing/inactive."""
    result = await db.execute(select(SmsTemplate).where(SmsTemplate.slug == slug))
    tpl = result.scalar_one_or_none()
    if tpl is None or not tpl.is_active:
        fallback = _get_seed_template(slug)
        if fallback is None:
            logger.warning("sms.send_from_template.missing slug=%s", slug)
            return None
        logger.warning("sms.send_from_template.using_seed_fallback slug=%s", slug)
        rendered = render_template(str(fallback["body"]), variables)
        fallback_category = str(fallback.get("category", "transactional"))
        resolved_is_transactional = fallback_category != "marketing"
    else:
        rendered = render_template(tpl.body, variables)
        resolved_is_transactional = is_transactional
    return await send_sms(
        db=db,
        to=to,
        body=rendered,
        template_slug=slug,
        conversation_id=conversation_id,
        courier_message_id=courier_message_id,
        user_id=user_id,
        sender_id=sender_id,
        is_transactional=resolved_is_transactional,
        skip_opt_in_check=skip_opt_in_check,
        lane_id=lane_id,
    )


def _get_seed_template(slug: str) -> dict[str, Any] | None:
    from app.services.sms_template_seeder import get_seed_templates

    for template in get_seed_templates():
        if template.get("slug") == slug:
            return template
    return None
