"""
Email log endpoints — Courier module.

Founder/admin-only access. Exposes the ryozan_email_log table for
observability, failure inspection, and manual retry.

Routes:
  GET  /email/log          — paginated log, filterable by status/template
  GET  /email/log/stats    — send counts by template + status (last 30 days)
  POST /email/log/{id}/retry — re-fire a failed send
  POST /webhooks/resend    — Resend delivery webhook (delivered/bounced/opened)
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.email_log import EmailLog
from app.models.email_template import EmailTemplate
from app.models.message import Message
from app.models.user import User
from app.services.courier_channels import CourierRouteContext, build_channel_status
from app.services.email_foundry import foundry_catalog_summary, foundry_source_sha256
from app.services.resend_webhook import verify_resend_webhook

logger = logging.getLogger("ryozan.email_log")

router = APIRouter(tags=["email_log"])

_DELIVERY_SUCCESS_PRECEDENCE = {
    "queued": 0,
    "sent": 1,
    "delivered": 2,
    "opened": 3,
    "clicked": 4,
}
_DELIVERY_FAILURE_TERMINAL = frozenset({"bounced", "complained"})
_MANIFEST_SUCCESS_STATUSES = frozenset({"sent", "delivered", "opened", "clicked"})
_MANIFEST_FAILURE_STATUSES = frozenset({"failed", "bounced", "complained"})
_MANIFEST_RECEIPT_LIMIT = 5000

_FOUNDRY_TRIGGER_MODES: dict[str, Literal["automatic", "manual"]] = {
    "booking_confirmation": "automatic",
    "deposit_request": "automatic",
    "deposit_paid": "automatic",
    "transaction_receipt": "automatic",
    "appointment_reminder": "automatic",
    "aftercare_followup": "automatic",
    "ezine_scroll": "manual",
    "flash_drop": "manual",
    "campaign_broadcast": "manual",
    "herald_news": "manual",
}

_FOUNDRY_CATEGORIES = {
    "booking_confirmation": "booking",
    "deposit_request": "payments",
    "deposit_paid": "payments",
    "transaction_receipt": "payments",
    "appointment_reminder": "booking",
    "aftercare_followup": "aftercare",
    "ezine_scroll": "editorial",
    "flash_drop": "marketing",
    "campaign_broadcast": "marketing",
    "herald_news": "editorial",
}

_FOUNDRY_ALIASES = {
    "booking_confirmation": ("booking-confirmation",),
    "deposit_request": ("pos_invoice",),
    "transaction_receipt": ("transaction_receipt",),
}

# This inventory is the source-backed legacy rendering surface audited in the
# MailHog sandbox. It intentionally records identity and matching evidence only;
# template HTML remains owned by the existing builders and database templates.
_LEGACY_EMAIL_TYPES: tuple[dict[str, object], ...] = (
    {
        "key": "welcome_collector",
        "label": "Collector welcome",
        "category": "account",
        "renderer": "build_welcome_html",
        "subjects": ("welcome to ryozan studio",),
    },
    {
        "key": "welcome_staff",
        "label": "Staff welcome",
        "category": "account",
        "renderer": "build_welcome_html",
        "subjects": ("your ryozan studio portal is ready",),
    },
    {
        "key": "password_reset",
        "label": "Password reset",
        "category": "account",
        "renderer": "build_password_reset_html",
        "subjects": ("reset your ryozan studio",),
    },
    {
        "key": "artist_invite",
        "label": "Artist invite",
        "category": "account",
        "renderer": "build_invite_html",
        "subjects": ("you're invited to join ryozan studio",),
    },
    {
        "key": "artist_login_welcome",
        "label": "Artist login welcome",
        "category": "account",
        "renderer": "build_login_welcome_html",
        "subjects": ("your ryozan studio dashboard login",),
    },
    {
        "key": "booking_request",
        "label": "Legacy booking request",
        "category": "booking",
        "renderer": "build_booking_confirmation_html",
        "subjects": ("we received your tattoo request", "booking request received"),
        "replacement": "foundry.booking_confirmation",
    },
    {
        "key": "consultation_confirmed",
        "label": "Consultation confirmed",
        "category": "booking",
        "renderer": "build_booking_confirmation_html",
        "subjects": ("your consultation is confirmed", "consultation booked"),
        "aliases": ("consultation_confirmation",),
    },
    {
        "key": "booking_lifecycle_confirmed",
        "label": "Booking lifecycle confirmed",
        "category": "booking",
        "renderer": "booking lifecycle inline HTML",
        "subjects": ("booking confirmed:",),
    },
    {
        "key": "booking_lifecycle_rescheduled",
        "label": "Booking lifecycle rescheduled",
        "category": "booking",
        "renderer": "booking lifecycle inline HTML",
        "subjects": ("booking rescheduled:",),
    },
    {
        "key": "booking_lifecycle_cancelled",
        "label": "Booking lifecycle cancelled",
        "category": "booking",
        "renderer": "booking lifecycle inline HTML",
        "subjects": ("booking cancelled:",),
    },
    {
        "key": "request_approved",
        "label": "Request approved, deposit pending",
        "category": "booking",
        "renderer": "render_request_approved",
        "subjects": ("request approved",),
    },
    {
        "key": "artist_booking_alert",
        "label": "Artist booking alert",
        "category": "staff",
        "renderer": "render_artist_booking_alert",
        "subjects": ("new tattoo request:",),
        "aliases": ("booking_artist_alert",),
    },
    {
        "key": "artist_consultation_alert",
        "label": "Artist consultation alert",
        "category": "staff",
        "renderer": "render_artist_booking_alert",
        "subjects": ("new consultation request:",),
        "aliases": ("booking_artist_alert",),
    },
    {
        "key": "quote",
        "label": "Legacy quote",
        "category": "payments",
        "renderer": "build_quote_email_html",
        "subjects": ("your tattoo quote from",),
        "replacement": "foundry.deposit_request",
    },
    {
        "key": "pos_invoice",
        "label": "Legacy POS invoice",
        "category": "payments",
        "renderer": "_build_pos_invoice_email_html",
        "subjects": ("payment request from ryozan studio",),
        "replacement": "foundry.deposit_request",
    },
    {
        "key": "deposit_paid_client",
        "label": "Legacy deposit receipt for client",
        "category": "payments",
        "renderer": "build_deposit_confirmation_html",
        "subjects": ("deposit received - you're booked",),
        "replacement": "foundry.deposit_paid",
    },
    {
        "key": "deposit_paid_artist",
        "label": "Deposit paid artist alert",
        "category": "staff",
        "renderer": "build_deposit_artist_notification_html",
        "subjects": ("deposit paid:", "deposit received -"),
    },
    {
        "key": "pos_receipt",
        "label": "Legacy POS receipt",
        "category": "payments",
        "renderer": "POS receipt inline HTML",
        "subjects": ("payment received - receipt",),
        "replacement": "foundry.transaction_receipt",
    },
    {
        "key": "document_send",
        "label": "Document send",
        "category": "documents",
        "renderer": "build_document_send_html",
        "subjects": ("document ready for your signature",),
    },
    {
        "key": "document_reminder",
        "label": "Document reminder",
        "category": "documents",
        "renderer": "build_reminder_html",
        "subjects": ("reminder: tattoo consent form",),
    },
    {
        "key": "aftercare_immediate",
        "label": "Immediate aftercare",
        "category": "aftercare",
        "renderer": "build_aftercare_html",
        "subjects": ("your tattoo aftercare instructions",),
    },
    {
        "key": "appointment_reminder",
        "label": "Legacy appointment reminder",
        "category": "booking",
        "renderer": "build_appointment_reminder_html",
        "subjects": ("reminder: your appointment is tomorrow",),
        "replacement": "foundry.appointment_reminder",
    },
    {
        "key": "healing_followup_2w",
        "label": "Legacy two-week healing follow-up",
        "category": "aftercare",
        "renderer": "build_healing_followup_html",
        "subjects": ("how is your tattoo healing?",),
        "replacement": "foundry.aftercare_followup",
    },
    {
        "key": "healing_followup_4w",
        "label": "Legacy four-week healing follow-up",
        "category": "aftercare",
        "renderer": "build_healing_followup_html",
        "subjects": ("one month healing check-in",),
        "replacement": "foundry.aftercare_followup",
    },
    {
        "key": "booking_resume",
        "label": "Booking resume reminder",
        "category": "automation",
        "renderer": "build_booking_resume_reminder_html",
        "subjects": ("finish your ryozan booking", "don't forget! finish your booking"),
    },
    {
        "key": "install_reminder_3h",
        "label": "Install reminder",
        "category": "onboarding",
        "renderer": "build_install_reminder_html",
        "subjects": ("finish setting up the ryozan app", "you're one step away"),
    },
    {
        "key": "install_reminder_24h",
        "label": "Final install reminder",
        "category": "onboarding",
        "renderer": "build_install_reminder_html",
        "subjects": ("last reminder to finish app setup", "last chance"),
    },
    {
        "key": "courier_notification",
        "label": "Courier notification",
        "category": "courier",
        "renderer": "Courier inline HTML",
        "subjects": ("message from ",),
        "aliases": ("courier_notification",),
    },
    {
        "key": "scroll_request",
        "label": "Artist Scroll request",
        "category": "compliance",
        "renderer": "build_scroll_request_html",
        "subjects": ("action required: complete your artist scroll",),
    },
    {
        "key": "scroll_reminder",
        "label": "Artist Scroll reminder",
        "category": "compliance",
        "renderer": "build_scroll_request_html",
        "subjects": ("reminder: complete your artist scroll", "reminder: your artist scroll"),
    },
    {
        "key": "owner_scroll_archive",
        "label": "Owner Scroll archive",
        "category": "compliance",
        "renderer": "build_owner_archive_email_html",
        "subjects": ("artist scroll submitted:",),
        "aliases": ("scroll_submitted",),
    },
    {
        "key": "email_route_verification",
        "label": "Email route verification",
        "category": "account",
        "renderer": "email_connect inline HTML",
        "subjects": ("verify your ryozan email",),
    },
    {
        "key": "inbound_forward",
        "label": "Inbound email forward",
        "category": "courier",
        "renderer": "email_inbound Resend wrapper",
        "subjects": ("[bttco]",),
    },
)


def _resolve_delivery_webhook_status(current: str | None, incoming: str) -> str:
    """Apply provider delivery evidence without regressing terminal state."""
    current_status = str(current or "queued")
    if current_status in _DELIVERY_FAILURE_TERMINAL:
        return current_status
    if incoming in _DELIVERY_FAILURE_TERMINAL:
        return incoming
    current_rank = _DELIVERY_SUCCESS_PRECEDENCE.get(current_status)
    incoming_rank = _DELIVERY_SUCCESS_PRECEDENCE.get(incoming)
    if incoming_rank is None:
        return current_status
    if current_rank is None or incoming_rank >= current_rank:
        return incoming
    return current_status


# ── Auth helpers ────────────────────────────────────────────────────────────


def _require_founder(user: User):
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("founder", 99):
        raise HTTPException(status_code=403, detail="Founder access required")


# ── Schemas ─────────────────────────────────────────────────────────────────


class EmailLogRow(BaseModel):
    id: UUID
    to_email: str
    subject: str
    template: str
    from_alias: str | None
    status: str
    resend_id: str | None
    error: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailLogListResponse(BaseModel):
    items: list[EmailLogRow]
    total: int
    skip: int
    limit: int


class EmailStatRow(BaseModel):
    template: str
    status: str
    count: int


class EmailStatsResponse(BaseModel):
    by_template_status: list[EmailStatRow]
    total_sent: int
    total_failed: int
    window_days: int


class RetryResponse(BaseModel):
    success: bool
    message: str


class MessagingReceiptSummary(BaseModel):
    attempts: int
    successes: int
    failures: int
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    statuses: list[str]


class MessagingEmailType(BaseModel):
    id: str
    template_key: str
    label: str
    family: Literal["foundry", "legacy", "database", "observed"]
    category: str
    trigger_mode: Literal["automatic", "manual", "unknown"]
    runtime_status: Literal["live", "legacy-live", "available", "not-live", "unknown"]
    is_live: bool
    manual_available: bool
    renderer: str
    replacement_id: str | None
    subject_template: str | None
    receipt: MessagingReceiptSummary
    evidence: list[str]


class MessagingProviderCapability(BaseModel):
    id: str
    channel: Literal["email", "sms", "whatsapp", "assets"]
    state: Literal["configured", "not-configured", "local-capture", "advertised"]
    selected_by_policy: bool
    evidence: str


class MessagingAsset(BaseModel):
    id: str
    label: str
    kind: Literal["logo", "background", "image"]
    url: str
    source: Literal["r2"]


class MessagingBoundary(BaseModel):
    id: str
    owner: str
    note: str


class MessagingManifestResponse(BaseModel):
    version: Literal["1.0"]
    tenant_id: Literal["bttco"]
    tenant_name: Literal["Ryozan Studio"]
    observed_at: datetime
    receipt_window_days: int
    foundry_source_sha256: str
    email_types: list[MessagingEmailType]
    providers: list[MessagingProviderCapability]
    assets: list[MessagingAsset]
    boundaries: list[MessagingBoundary]


def _normalize_template_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_") or "general"


def _subject_template_matches(template: str, subject: str) -> bool:
    if re.fullmatch(r"\s*\{\{[^{}]+\}\}\s*", template):
        return False
    parts = re.split(r"\{\{[^{}]+\}\}", template)
    pattern = "^" + ".+?".join(re.escape(part) for part in parts) + "$"
    return re.match(pattern, subject, flags=re.IGNORECASE) is not None


def _source_email_definitions() -> list[dict[str, object]]:
    catalog = foundry_catalog_summary()
    definitions: list[dict[str, object]] = []
    for slug, template in catalog.items():
        definitions.append(
            {
                "id": f"foundry.{slug}",
                "key": slug,
                "label": slug.replace("_", " ").title(),
                "family": "foundry",
                "category": _FOUNDRY_CATEGORIES.get(slug, "email"),
                "trigger_mode": _FOUNDRY_TRIGGER_MODES.get(slug, "manual"),
                "manual_available": True,
                "renderer": f"Email Foundry 1.0.0/{slug}",
                "replacement": None,
                "subject_template": str(template["subject"]),
                "subjects": (),
                "aliases": (slug, *_FOUNDRY_ALIASES.get(slug, ())),
            }
        )
    for definition in _LEGACY_EMAIL_TYPES:
        definitions.append(
            {
                "id": f"legacy.{definition['key']}",
                "key": str(definition["key"]),
                "label": str(definition["label"]),
                "family": "legacy",
                "category": str(definition["category"]),
                "trigger_mode": "automatic",
                "manual_available": False,
                "renderer": str(definition["renderer"]),
                "replacement": definition.get("replacement"),
                "subject_template": None,
                "subjects": tuple(definition.get("subjects", ())),
                "aliases": tuple(definition.get("aliases", ())),
            }
        )
    return definitions


def _empty_receipt() -> dict[str, object]:
    return {
        "attempts": 0,
        "successes": 0,
        "failures": 0,
        "last_attempt_at": None,
        "last_success_at": None,
        "statuses": set(),
    }


def _record_receipt(accumulator: dict[str, object], receipt: dict[str, object]) -> None:
    status = str(receipt["status"])
    created_at = receipt["created_at"]
    accumulator["attempts"] = int(accumulator["attempts"]) + 1
    statuses = accumulator["statuses"]
    assert isinstance(statuses, set)
    statuses.add(status)
    last_attempt = accumulator["last_attempt_at"]
    if last_attempt is None or created_at > last_attempt:
        accumulator["last_attempt_at"] = created_at
    if status in _MANIFEST_SUCCESS_STATUSES:
        accumulator["successes"] = int(accumulator["successes"]) + 1
        last_success = accumulator["last_success_at"]
        if last_success is None or created_at > last_success:
            accumulator["last_success_at"] = created_at
    if status in _MANIFEST_FAILURE_STATUSES:
        accumulator["failures"] = int(accumulator["failures"]) + 1


def _match_receipt_definition(
    receipt: dict[str, object],
    definitions: list[dict[str, object]],
) -> str | None:
    subject = str(receipt.get("subject") or "").strip()
    for definition in definitions:
        template = definition.get("subject_template")
        if template and _subject_template_matches(str(template), subject):
            return str(definition["id"])
    lowered_subject = subject.lower()
    for definition in definitions:
        if any(lowered_subject.startswith(str(prefix).lower()) for prefix in definition.get("subjects", ())):
            return str(definition["id"])

    normalized = _normalize_template_key(str(receipt.get("template") or ""))
    alias_matches = [
        definition
        for definition in definitions
        if normalized in {_normalize_template_key(str(alias)) for alias in definition.get("aliases", ())}
    ]
    return str(alias_matches[0]["id"]) if len(alias_matches) == 1 else None


def _runtime_status(
    *,
    family: str,
    trigger_mode: str,
    manual_available: bool,
    database_active: bool,
    receipt: dict[str, object],
) -> Literal["live", "legacy-live", "available", "not-live", "unknown"]:
    if int(receipt["successes"]) > 0:
        if family == "foundry":
            return "live"
        if family in {"legacy", "database"}:
            return "legacy-live"
        return "unknown"
    if int(receipt["attempts"]) > 0:
        return "unknown"
    if manual_available and (family == "foundry" and trigger_mode == "manual" or database_active):
        return "available"
    return "not-live"


def _receipt_model(receipt: dict[str, object]) -> MessagingReceiptSummary:
    statuses = receipt["statuses"]
    assert isinstance(statuses, set)
    return MessagingReceiptSummary(
        attempts=int(receipt["attempts"]),
        successes=int(receipt["successes"]),
        failures=int(receipt["failures"]),
        last_attempt_at=receipt["last_attempt_at"],
        last_success_at=receipt["last_success_at"],
        statuses=sorted(str(status) for status in statuses),
    )


def _build_email_type_manifest(
    receipts: list[dict[str, object]],
    database_templates: list[dict[str, object]],
) -> list[MessagingEmailType]:
    definitions = _source_email_definitions()
    by_id = {str(definition["id"]): definition for definition in definitions}
    database_by_key = {_normalize_template_key(str(row["slug"])): row for row in database_templates}

    for row in database_templates:
        normalized = _normalize_template_key(str(row["slug"]))
        already_owned = any(normalized == _normalize_template_key(str(item["key"])) for item in definitions)
        if already_owned:
            continue
        definition = {
            "id": f"database.{normalized}",
            "key": str(row["slug"]),
            "label": str(row["name"]),
            "family": "database",
            "category": str(row["category"]),
            "trigger_mode": "manual",
            "manual_available": bool(row["is_active"]),
            "renderer": "ryozan_email_templates",
            "replacement": None,
            "subject_template": str(row["subject"]),
            "subjects": (),
            "aliases": (str(row["slug"]),),
        }
        definitions.append(definition)
        by_id[str(definition["id"])] = definition

    receipt_by_id = {str(definition["id"]): _empty_receipt() for definition in definitions}
    unmatched: dict[str, dict[str, object]] = {}
    for receipt in receipts:
        matched_id = _match_receipt_definition(receipt, definitions)
        if matched_id:
            _record_receipt(receipt_by_id[matched_id], receipt)
            continue
        observed_key = _normalize_template_key(str(receipt.get("template") or ""))
        _record_receipt(unmatched.setdefault(observed_key, _empty_receipt()), receipt)

    result: list[MessagingEmailType] = []
    for definition in definitions:
        receipt = receipt_by_id[str(definition["id"])]
        db_row = database_by_key.get(_normalize_template_key(str(definition["key"])))
        database_active = bool(db_row and db_row["is_active"])
        status = _runtime_status(
            family=str(definition["family"]),
            trigger_mode=str(definition["trigger_mode"]),
            manual_available=bool(definition["manual_available"]),
            database_active=database_active,
            receipt=receipt,
        )
        evidence = []
        if int(receipt["successes"]) > 0:
            evidence.append("Recent successful receipt attributed by template tag or subject contract.")
        elif int(receipt["attempts"]) > 0:
            evidence.append("Recent attempt exists, but no successful receipt is attributable.")
        elif status == "available":
            evidence.append("Manual send route is available with no recent attributed success receipt.")
        else:
            evidence.append("Source or catalog entry exists with no recent attributed success receipt.")
        if definition.get("replacement"):
            evidence.append(f"Replacement: {definition['replacement']}.")
        if database_active:
            evidence.append("An active database template row exists.")
        result.append(
            MessagingEmailType(
                id=str(definition["id"]),
                template_key=str(definition["key"]),
                label=str(definition["label"]),
                family=definition["family"],
                category=str(definition["category"]),
                trigger_mode=definition["trigger_mode"],
                runtime_status=status,
                is_live=status in {"live", "legacy-live"},
                manual_available=bool(definition["manual_available"]),
                renderer=str(definition["renderer"]),
                replacement_id=str(definition["replacement"]) if definition.get("replacement") else None,
                subject_template=str(definition["subject_template"]) if definition.get("subject_template") else None,
                receipt=_receipt_model(receipt),
                evidence=evidence,
            )
        )

    for key, receipt in unmatched.items():
        status = _runtime_status(
            family="observed",
            trigger_mode="unknown",
            manual_available=False,
            database_active=False,
            receipt=receipt,
        )
        result.append(
            MessagingEmailType(
                id=f"observed.{key}",
                template_key=key,
                label=f"Observed receipt: {key.replace('_', ' ')}",
                family="observed",
                category="unattributed",
                trigger_mode="unknown",
                runtime_status=status,
                is_live=False,
                manual_available=False,
                renderer="unknown",
                replacement_id=None,
                subject_template=None,
                receipt=_receipt_model(receipt),
                evidence=["Receipt exists, but the send path did not persist a unique renderer identity."],
            )
        )

    family_order = {"foundry": 0, "legacy": 1, "database": 2, "observed": 3}
    return sorted(result, key=lambda item: (family_order[item.family], item.category, item.label))


def _provider_manifest() -> list[MessagingProviderCapability]:
    settings = get_settings()
    sendgrid = bool(settings.SENDGRID_API_KEY.strip())
    resend = bool(settings.RESEND_API_KEY.strip())
    smtp = bool(settings.SMTP_USER.strip() and settings.SMTP_PASSWORD.strip())
    mailhog = settings.SMTP_HOST.lower() in {"localhost", "127.0.0.1", "mailhog"} and settings.SMTP_PORT == 1025
    selected = "sendgrid" if sendgrid else "resend" if resend else "smtp" if smtp else None
    courier = {
        channel["id"]: channel
        for channel in build_channel_status(settings, CourierRouteContext())["channels"]
        if channel["id"] in {"sms", "whatsapp"}
    }
    return [
        MessagingProviderCapability(
            id="sendgrid",
            channel="email",
            state="configured" if sendgrid else "not-configured",
            selected_by_policy=selected == "sendgrid",
            evidence="Configuration presence only; the current receipt row does not persist provider identity.",
        ),
        MessagingProviderCapability(
            id="resend",
            channel="email",
            state="configured" if resend else "not-configured",
            selected_by_policy=selected == "resend",
            evidence="Configuration presence only; Resend webhook receipts enrich delivery status when a matching ID exists.",
        ),
        MessagingProviderCapability(
            id="smtp",
            channel="email",
            state="configured" if smtp else "not-configured",
            selected_by_policy=selected == "smtp",
            evidence="Configured as the final email fallback when hosted providers are unavailable.",
        ),
        MessagingProviderCapability(
            id="mailhog",
            channel="email",
            state="local-capture" if mailhog else "not-configured",
            selected_by_policy=selected == "smtp" and mailhog,
            evidence="Local SMTP capture only. It is not a production delivery provider.",
        ),
        # Twilio is the only SMS and WhatsApp transport, so Courier's own channel
        # owner decides whether each lane is ready and therefore selected.
        MessagingProviderCapability(
            id="twilio",
            channel="sms",
            state="configured" if courier["sms"]["configured"] else "not-configured",
            selected_by_policy=bool(courier["sms"]["connected"]),
            evidence="Courier SMS lane readiness: Twilio credentials, messaging service, and a ready sender lane.",
        ),
        MessagingProviderCapability(
            id="twilio-whatsapp",
            channel="whatsapp",
            state="configured" if courier["whatsapp"]["configured"] else "not-configured",
            selected_by_policy=bool(courier["whatsapp"]["connected"]),
            evidence="Courier WhatsApp lane readiness: Twilio credentials and the shop WhatsApp sender.",
        ),
        MessagingProviderCapability(
            id="cloudflare-r2",
            channel="assets",
            state="advertised" if settings.R2_PUBLIC_DOMAIN.strip() else "not-configured",
            selected_by_policy=bool(settings.R2_PUBLIC_DOMAIN.strip()),
            evidence="Public asset domain is advertised separately from private R2 write credentials.",
        ),
    ]


def _asset_manifest() -> list[MessagingAsset]:
    urls = sorted({url for template in foundry_catalog_summary().values() for url in template.get("asset_urls", [])})
    assets = []
    for url in urls:
        filename = str(url).rsplit("/", 1)[-1]
        kind: Literal["logo", "background", "image"] = (
            "logo" if "tiger-mark" in filename else "background" if "tile" in filename else "image"
        )
        assets.append(
            MessagingAsset(
                id=filename.rsplit(".", 1)[0],
                label=filename.rsplit(".", 1)[0].replace("-", " ").title(),
                kind=kind,
                url=str(url),
                source="r2",
            )
        )
    return assets


# ── GET /email/log ───────────────────────────────────────────────────────────


@router.get("/email/log", response_model=EmailLogListResponse)
async def list_email_log(
    status_filter: str | None = Query(None, alias="status"),
    template: str | None = Query(None),
    search: str | None = Query(None, max_length=200),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Paginated email log. Filterable by status, template, or recipient search.
    Founder/admin only.
    """
    _require_founder(current_user)

    q = select(EmailLog)

    if status_filter:
        q = q.where(EmailLog.status == status_filter)
    if template:
        q = q.where(EmailLog.template == template)
    if search:
        q = q.where(EmailLog.to_email.ilike(f"%{search}%"))

    # Count
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    # Page
    q = q.order_by(desc(EmailLog.created_at)).offset(skip).limit(limit)
    rows = (await db.execute(q)).scalars().all()

    return EmailLogListResponse(
        items=[EmailLogRow.model_validate(r) for r in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


# ── GET /email/log/stats ────────────────────────────────────────────────────


@router.get("/email/log/stats", response_model=EmailStatsResponse)
async def email_log_stats(
    days: int = Query(30, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate send counts grouped by template + status for the last N days."""
    _require_founder(current_user)

    since = datetime.now(UTC) - timedelta(days=days)

    rows = (
        await db.execute(
            select(EmailLog.template, EmailLog.status, func.count().label("cnt"))
            .where(EmailLog.created_at >= since)
            .group_by(EmailLog.template, EmailLog.status)
            .order_by(EmailLog.template, EmailLog.status)
        )
    ).all()

    stats = [EmailStatRow(template=r.template, status=r.status, count=r.cnt) for r in rows]
    total_sent = sum(s.count for s in stats if s.status == "sent")
    total_failed = sum(s.count for s in stats if s.status == "failed")

    return EmailStatsResponse(
        by_template_status=stats,
        total_sent=total_sent,
        total_failed=total_failed,
        window_days=days,
    )


@router.get("/email/manifest", response_model=MessagingManifestResponse)
async def email_messaging_manifest(
    days: int = Query(90, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Founder-only tenant projection for Ark Courier and Email Foundry."""
    _require_founder(current_user)

    observed_at = datetime.now(UTC)
    since = observed_at - timedelta(days=days)
    receipt_rows = (
        await db.execute(
            select(EmailLog.template, EmailLog.status, EmailLog.subject, EmailLog.created_at)
            .where(EmailLog.created_at >= since)
            .order_by(desc(EmailLog.created_at))
            .limit(_MANIFEST_RECEIPT_LIMIT)
        )
    ).all()
    template_rows = (
        (await db.execute(select(EmailTemplate).order_by(EmailTemplate.category, EmailTemplate.name))).scalars().all()
    )

    receipts = [
        {
            "template": row.template,
            "status": row.status,
            "subject": row.subject,
            "created_at": row.created_at,
        }
        for row in receipt_rows
    ]
    database_templates = [
        {
            "slug": row.slug,
            "name": row.name,
            "category": row.category,
            "subject": row.subject,
            "is_active": row.is_active,
        }
        for row in template_rows
    ]

    return MessagingManifestResponse(
        version="1.0",
        tenant_id="bttco",
        tenant_name="Ryozan Studio",
        observed_at=observed_at,
        receipt_window_days=days,
        foundry_source_sha256=foundry_source_sha256(),
        email_types=_build_email_type_manifest(receipts, database_templates),
        providers=_provider_manifest(),
        assets=_asset_manifest(),
        boundaries=[
            MessagingBoundary(
                id="manual-template-send",
                owner="Ryozan email template control surface",
                note="Sends whichever saved template is selected. It is a control surface, not a distinct renderer.",
            ),
            MessagingBoundary(
                id="stripe-provider-receipt",
                owner="Stripe",
                note="Provider-owned receipt configured outside Ryozan rendering code.",
            ),
            MessagingBoundary(
                id="private-reference-assets",
                owner="Ryozan booking authorization",
                note="Private booking reference URLs are excluded from the general tenant manifest.",
            ),
        ],
    )


# ── POST /email/log/{id}/retry ──────────────────────────────────────────────


@router.post("/email/log/{log_id}/retry", response_model=RetryResponse)
async def retry_email(
    log_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Re-send a failed email via Resend using the stored resend_id subject/to.
    Increments retry_count and updates last_retry_at regardless of outcome.
    """
    _require_founder(current_user)

    row = await db.get(EmailLog, log_id)
    if not row:
        raise HTTPException(status_code=404, detail="Email log entry not found")

    if row.status not in ("failed", "bounced", "complained"):
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry failed/bounced/complained emails (current status: {row.status})",
        )

    settings = get_settings()
    if not settings.RESEND_API_KEY:
        raise HTTPException(status_code=503, detail="Resend API key not configured")

    # Stamp retry attempt before send (so it's recorded even if send raises)
    await db.execute(
        update(EmailLog)
        .where(EmailLog.id == log_id)
        .values(
            retry_count=EmailLog.retry_count + 1,
            last_retry_at=datetime.now(UTC),
            status="queued",
        )
    )
    await db.commit()

    # Trigger Resend re-send using their /resend API
    # (Resend can replay a message by its ID — no HTML needed)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.resend.com/emails/{row.resend_id}/cancel",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            )
        # Resend doesn't have a native "resend by ID" — we mark it for manual follow-up
        # and update status back to failed so it stays visible
        # TODO: when Resend adds replay API, switch to that endpoint
        await db.execute(
            update(EmailLog)
            .where(EmailLog.id == log_id)
            .values(status="failed", error="Retry queued — manual resend required (Resend replay API pending)")
        )
        await db.commit()
        return RetryResponse(
            success=False,
            message="Retry recorded. Resend replay API not yet available — contact Resend support or trigger manually.",
        )
    except Exception as exc:
        logger.error("Retry attempt failed for log %s: %s", log_id, exc)
        await db.execute(
            update(EmailLog).where(EmailLog.id == log_id).values(status="failed", error=f"Retry error: {exc}")
        )
        await db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── POST /webhooks/resend ────────────────────────────────────────────────────


@router.post("/webhooks/resend", status_code=200)
async def resend_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Resend delivery webhook. Updates EmailLog status on:
      email.delivered, email.bounced, email.opened, email.clicked

    Resend signs webhooks with a svix-signature header, verified with
    RESEND_WEBHOOK_SECRET. Without the secret the webhook is refused (503)
    outside local, development and test environments.
    """
    settings = get_settings()
    body = await request.body()

    # Signed with RESEND_WEBHOOK_SECRET. Outside local/dev/test a blank secret answers 503.
    verify_resend_webhook(
        body, request.headers, settings.RESEND_WEBHOOK_SECRET, settings, label="Resend delivery webhook"
    )

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    event_type = payload.get("type", "")
    email_id = payload.get("data", {}).get("email_id") or payload.get("data", {}).get("id")

    if not email_id:
        logger.warning("Resend webhook missing email_id: %s", payload)
        return {"ok": True}

    # Map Resend event type → our status
    status_map = {
        "email.sent": "sent",
        "email.delivered": "delivered",
        "email.bounced": "bounced",
        "email.complained": "complained",
        "email.delivery_delayed": "queued",  # still in flight, not failed
        "email.opened": "opened",
        "email.clicked": "clicked",
    }
    new_status = status_map.get(event_type)
    if not new_status:
        return {"ok": True}  # Unknown event, ignore

    result = await db.execute(
        select(EmailLog)
        .where(EmailLog.resend_id == email_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    updated_log = result.scalar_one_or_none()
    if not updated_log:
        logger.info("Resend webhook: %s had no matching EmailLog row", email_id)
        return {"ok": True, "updated": 0}

    resolved_status = _resolve_delivery_webhook_status(updated_log.status, new_status)
    if resolved_status != updated_log.status:
        updated_log.status = resolved_status
        updated_log.updated_at = datetime.now(UTC)

    if updated_log.courier_message_id:
        courier_message_id = updated_log.courier_message_id
        message_result = await db.execute(
            select(Message)
            .where(Message.id == courier_message_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        msg = message_result.scalar_one_or_none()
        if msg:
            meta = json.loads(msg.metadata_json or "{}")
            current_attempt = int(meta.get("delivery_attempts") or 0)
            attempt_matches = (
                updated_log.courier_attempt == current_attempt
                if updated_log.courier_attempt is not None
                else current_attempt == 0
            )
            # The outbox worker owns a nonstale sending lease through its
            # attempt-bound terminal receipt. Webhooks may enrich that attempt
            # only after the worker has made Message state terminal.
            if attempt_matches and meta.get("delivery_status") != "sending":
                message_status = _resolve_delivery_webhook_status(
                    str(meta.get("delivery_status") or "queued"),
                    resolved_status,
                )
                if message_status != meta.get("delivery_status"):
                    meta["delivery_status"] = message_status
                    meta["delivery_updated_at"] = updated_log.updated_at.isoformat()
                    msg.metadata_json = json.dumps(meta)

    await db.commit()

    logger.info(
        "Resend webhook: %s received %s and resolved to %s",
        email_id,
        new_status,
        resolved_status,
    )
    return {"ok": True, "updated": 1}
