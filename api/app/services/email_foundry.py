"""Server-side projection of the locked @bttco/email-foundry dependency."""

import asyncio
import json
import re
from calendar import monthrange
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import aiosmtplib
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.constants.timezones import SHOP_TIMEZONE
from app.models.user import BTTCOUser

_CATALOG_PATH = Path(__file__).with_name("email_foundry_catalog.json")

# The starseed ground tile. The R2 object is served with a one-year immutable cache, so a
# replaced tile only reaches inboxes under a new URL. Bump STARSEED_TILE_VERSION whenever
# email/starseed-tile.png is replaced (scripts/email/starseed_tile_generator.py).
STARSEED_TILE_VERSION = "4"
STARSEED_TILE_URL = f"https://assets.ryozan.app/email/starseed-tile.png?v={STARSEED_TILE_VERSION}"
# Any starseed reference an older body may carry: the unversioned R2 URL (locked catalog,
# saved database templates) or the Foundry package's local copy.
_STARSEED_ANY_RE = re.compile(
    r"https://(?:assets\.ryozan\.app/email"
    r"|app\.ryozan\.app/dependencies/@bttco/email-foundry/[^\"'()\s]*?/assets)"
    r"/starseed-tile\.png(?:\?v=[\w.-]+)?"
)


def with_current_starseed(html: str) -> str:
    """Point every starseed ground reference at the current, cache-busted tile."""
    return _STARSEED_ANY_RE.sub(STARSEED_TILE_URL, html) if html else html


FOUNDRY_FIELDS_KEY = "bttco-foundry-fields-v1"
FOUNDRY_SCHEDULE_KEY = "bttco-foundry-schedule-v1"
FOUNDRY_CUSTOM_KEY = "bttco-foundry-custom-v1"
FOUNDRY_OUTBOX_KEY = "bttco-foundry-outbox-v1"
FOUNDRY_CONNECTORS_KEY = "bttco-foundry-connectors-v1"
FOUNDRY_BINDINGS_KEY = "__courier_bindings"
FOUNDRY_DELIVERY_KEY = "__courier_delivery"
FOUNDRY_ELEMENTS_KEY = "__courier_elements"
FOUNDRY_STATE_DEFAULTS: dict[str, Any] = {
    FOUNDRY_FIELDS_KEY: {},
    FOUNDRY_SCHEDULE_KEY: {},
    FOUNDRY_CUSTOM_KEY: [],
    FOUNDRY_OUTBOX_KEY: [],
    FOUNDRY_CONNECTORS_KEY: {},
}
FOUNDRY_OUTBOX_STATUSES = {"queued", "held for sign-off", "approved", "sent", "failed"}
_TERMINAL_OUTBOX_STATUSES = {"sent", "failed"}
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
_MAX_OUTBOX_ROWS = 30
_FOUNDRY_ELEMENT_PATH_RE = re.compile(r"^body(?:/[a-z][a-z0-9]*\[[1-9]\d*\])*$")
_FOUNDRY_UNSAFE_STYLE_RE = re.compile(r"[<>;]|expression\s*\(|javascript\s*:|url\s*\(", re.IGNORECASE)
_FOUNDRY_REFERENCE_IMAGES_RE = re.compile(
    r"<!--foundry:reference-images:start-->.*?<!--foundry:reference-images:end-->",
    re.DOTALL,
)
_FOUNDRY_STYLE_PROPERTIES = {
    "color": "color",
    "backgroundColor": "background-color",
    "fontFamily": "font-family",
    "fontSize": "font-size",
    "fontWeight": "font-weight",
    "fontStyle": "font-style",
    "lineHeight": "line-height",
    "letterSpacing": "letter-spacing",
    "textAlign": "text-align",
    "textDecoration": "text-decoration",
    "textTransform": "text-transform",
    "verticalAlign": "vertical-align",
    "opacity": "opacity",
    "borderColor": "border-color",
    "borderWidth": "border-width",
    "borderStyle": "border-style",
    "borderRadius": "border-radius",
    "paddingTop": "padding-top",
    "paddingRight": "padding-right",
    "paddingBottom": "padding-bottom",
    "paddingLeft": "padding-left",
    "marginTop": "margin-top",
    "marginRight": "margin-right",
    "marginBottom": "margin-bottom",
    "marginLeft": "margin-left",
    "width": "width",
    "maxWidth": "max-width",
    "height": "height",
    "maxHeight": "max-height",
}
_FOUNDRY_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
FOUNDRY_TRIGGERS = {
    "manual",
    "booking_filed",
    "quote_dealt",
    "deposit_paid",
    "session_booked",
    "session_minus_24h",
    "session_done",
    "heal_day_3",
    "heal_day_7",
    "flash_published",
}

_FOUNDRY_CONNECTORS = {
    "sendgrid": ("SendGrid", "email"),
    "resend": ("Resend", "email"),
    "smtp": ("SMTP", "email"),
    "twilio": ("Twilio", "sms + voice"),
    "cloudflare-r2": ("Cloudflare R2", "assets"),
    # Shop-operations bindings Ryozan reads through Hivemind. Each probe is a
    # read-only provider GET; none of them can send, book, or move money.
    "google-calendar": ("Google Calendar", "booking calendar"),
    "stripe": ("Stripe", "payments (read-only)"),
    "meta-whatsapp": ("WhatsApp Cloud API", "whatsapp"),
    "meta-instagram": ("Instagram", "instagram"),
    "meta-facebook": ("Facebook Page", "facebook"),
}


@dataclass(frozen=True)
class FoundryEmail:
    """A fully merged subject and HTML body ready for the shared mail sender."""

    slug: str
    subject: str
    html_body: str


@dataclass
class _FoundryOverlayFrame:
    tag: str
    path: str
    child_counts: dict[str, int]
    replaces_content: bool = False


def _safe_foundry_attribute(tag: str, name: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()[:2048]
    if name in {"alt", "title"}:
        return candidate if name == "title" or tag == "img" else None
    if name == "href" and tag == "a":
        if not candidate:
            return candidate
        parsed = urlparse(candidate)
        if candidate.startswith(("/", "#")) or parsed.scheme.lower() in {"http", "https", "mailto", "tel"}:
            return candidate
    if name == "src" and tag == "img":
        if not candidate:
            return candidate
        parsed = urlparse(candidate)
        if parsed.scheme.lower() in {"http", "https", "cid"}:
            return candidate
    return None


def _normalize_foundry_element_overlays(value: object) -> dict[str, dict[str, Any]]:
    """Validate Courier's bounded visual overlay without altering package source."""
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_path, raw_patch in list(value.items())[:100]:
        path = str(raw_path)
        if len(path) > 600 or not _FOUNDRY_ELEMENT_PATH_RE.fullmatch(path) or not isinstance(raw_patch, Mapping):
            continue
        raw_tag = raw_patch.get("tag")
        tag = str(raw_tag).strip().lower() if isinstance(raw_tag, str) else path.rsplit("/", 1)[-1].split("[", 1)[0]
        if not re.fullmatch(r"[a-z][a-z0-9]*", tag):
            continue

        styles: dict[str, str] = {}
        raw_styles = raw_patch.get("styles")
        if isinstance(raw_styles, Mapping):
            for property_name, css_name in _FOUNDRY_STYLE_PROPERTIES.items():
                raw_value = raw_styles.get(property_name)
                if not isinstance(raw_value, str):
                    continue
                style_value = raw_value.strip()[:240]
                if style_value and not _FOUNDRY_UNSAFE_STYLE_RE.search(style_value):
                    styles[css_name] = style_value

        attributes: dict[str, str] = {}
        raw_attributes = raw_patch.get("attributes")
        if isinstance(raw_attributes, Mapping):
            for attribute_name in ("href", "src", "alt", "title"):
                safe_value = _safe_foundry_attribute(tag, attribute_name, raw_attributes.get(attribute_name))
                if safe_value is not None:
                    attributes[attribute_name] = safe_value

        patch: dict[str, Any] = {"tag": tag, "styles": styles, "attributes": attributes}
        raw_text = raw_patch.get("text")
        if isinstance(raw_text, str) and tag not in _FOUNDRY_VOID_TAGS:
            patch["text"] = raw_text[:20_000]
        normalized[path] = patch
    return normalized


class _FoundryOverlayParser(HTMLParser):
    """Apply path-addressed overrides while preserving every untouched source tag."""

    def __init__(self, overlays: Mapping[str, Mapping[str, Any]]):
        super().__init__(convert_charrefs=False)
        self.overlays = overlays
        self.frames: list[_FoundryOverlayFrame] = []
        self.output: list[str] = []

    def _suppressed(self) -> bool:
        return any(frame.replaces_content for frame in self.frames)

    def _next_path(self, tag: str) -> str:
        if tag == "body":
            return "body"
        if not self.frames or not self.frames[-1].path:
            return ""
        parent = self.frames[-1]
        index = parent.child_counts.get(tag, 0) + 1
        parent.child_counts[tag] = index
        return f"{parent.path}/{tag}[{index}]"

    @staticmethod
    def _patched_start_tag(tag: str, attrs: list[tuple[str, str | None]], patch: Mapping[str, Any]) -> str:
        attributes = {name: value for name, value in attrs}
        styles: dict[str, str] = {}
        raw_style = attributes.get("style") or ""
        for declaration in raw_style.split(";"):
            name, separator, value = declaration.partition(":")
            if separator and name.strip() and value.strip():
                styles[name.strip().lower()] = value.strip()
        patch_styles = patch.get("styles")
        if isinstance(patch_styles, Mapping):
            styles.update({str(name): str(value) for name, value in patch_styles.items()})
        if styles:
            attributes["style"] = ";".join(f"{name}:{value}" for name, value in styles.items())
        patch_attributes = patch.get("attributes")
        if isinstance(patch_attributes, Mapping):
            attributes.update({str(name): str(value) for name, value in patch_attributes.items()})

        rendered_attributes = "".join(
            f" {name}" if value is None else f' {name}="{escape(str(value), quote=True)}"'
            for name, value in attributes.items()
        )
        return f"<{tag}{rendered_attributes}>"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        path = self._next_path(tag)
        patch = self.overlays.get(path) if path else None
        if patch and patch.get("tag") != tag:
            patch = None
        ancestor_suppressed = self._suppressed()
        replaces_content = bool(patch and isinstance(patch.get("text"), str) and tag not in _FOUNDRY_VOID_TAGS)
        if not ancestor_suppressed:
            self.output.append(
                self._patched_start_tag(tag, attrs, patch) if patch else (self.get_starttag_text() or f"<{tag}>")
            )
            if replaces_content:
                self.output.append(escape(str(patch["text"])))
        if tag not in _FOUNDRY_VOID_TAGS:
            self.frames.append(
                _FoundryOverlayFrame(tag=tag, path=path, child_counts={}, replaces_content=replaces_content)
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        path = self._next_path(tag)
        patch = self.overlays.get(path) if path else None
        if patch and patch.get("tag") != tag:
            patch = None
        if self._suppressed():
            return
        rendered = self._patched_start_tag(tag, attrs, patch) if patch else (self.get_starttag_text() or f"<{tag}/>")
        self.output.append(rendered[:-1] + "/>" if patch and rendered.endswith(">") else rendered)

    def handle_endtag(self, tag: str) -> None:
        frame = self.frames.pop() if self.frames else None
        if not self._suppressed():
            self.output.append(f"</{tag}>")
        if frame and frame.tag != tag:
            self.frames.clear()

    def handle_data(self, data: str) -> None:
        if not self._suppressed():
            self.output.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self._suppressed():
            self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._suppressed():
            self.output.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if not self._suppressed():
            self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        if not self._suppressed():
            self.output.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        if not self._suppressed():
            self.output.append(f"<?{data}>")


def apply_foundry_element_overlays(source: str, overlays: object) -> str:
    normalized = _normalize_foundry_element_overlays(overlays)
    if not normalized:
        return source
    parser = _FoundryOverlayParser(normalized)
    parser.feed(source)
    parser.close()
    return "".join(parser.output)


@lru_cache(maxsize=1)
def _catalog() -> dict:
    with _CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)


def foundry_source_sha256() -> str:
    return str(_catalog()["source_sha256"])


def foundry_catalog_summary() -> dict[str, dict[str, object]]:
    """Return metadata needed by audit clients without exposing template HTML."""

    summary: dict[str, dict[str, object]] = {}
    for slug, template in _catalog()["templates"].items():
        html = str(template["html"])
        asset_urls = sorted(set(re.findall(r"https://assets\.ryozan\.app/[^\"'()\s]+", html)))
        summary[str(slug)] = {
            "subject": str(template["subject"]),
            "variables": [str(value) for value in template.get("variables", [])],
            "asset_urls": asset_urls,
        }
    return summary


def studio_identity_fields() -> dict[str, str]:
    """Studio name and postal address merge fields, owned by Settings."""

    from app.core.config import get_settings

    settings = get_settings()
    return {"studio_name": settings.STUDIO_NAME, "studio_address": settings.STUDIO_ADDRESS}


def studio_address_html(style: str) -> str:
    """One footer paragraph with the configured postal address, or nothing."""

    from app.core.config import get_settings

    address = get_settings().STUDIO_ADDRESS.strip()
    return f'<p style="{style}">{escape(address)}</p>' if address else ""


def render_foundry_email(
    slug: str,
    variables: Mapping[str, object],
    *,
    element_overlays: object | None = None,
) -> FoundryEmail | None:
    """Render a locked Foundry template without changing its authored markup."""

    template = _catalog()["templates"].get(slug)
    if not template:
        return None

    merged = {key: "" for key in template.get("variables", [])}
    merged.update(studio_identity_fields())
    merged.update({key: "" if value is None else str(value) for key, value in variables.items()})

    subject = str(template["subject"])
    html_body = str(template["html"])
    subject = fill_foundry_merge_tags(subject, merged)
    html_body = fill_foundry_merge_tags(html_body, merged)
    html_body = apply_foundry_element_overlays(html_body, element_overlays)
    html_body = with_current_starseed(html_body)

    return FoundryEmail(slug=slug, subject=subject, html_body=html_body)


def render_gift_card_receipt(
    *,
    recipient_name: str,
    purchaser_name: str,
    artist_name: str,
    amount_cents: int,
    code: str,
    message: str | None = None,
    gift_recipient_name: str | None = None,
    sent_to_purchaser: bool = False,
) -> FoundryEmail:
    """Render the automatic paid gift-card receipt in Email Foundry's transactional language.

    This lives beside the locked Foundry catalog so transactional gift-card
    delivery can use the shared email system without modifying package bytes.
    """

    safe_recipient = escape(recipient_name)
    safe_purchaser = escape(purchaser_name)
    safe_gift_recipient = escape(gift_recipient_name or recipient_name)
    safe_artist = escape(artist_name)
    safe_code = escape(code)
    safe_message = escape(message).replace("\n", "<br>") if message else ""
    message_block = (
        (
            f"<tr><td style=\"padding:0 26px 18px;font-family:'Space Grotesk',Arial,sans-serif;"
            f'font-size:14px;line-height:1.65;color:#c9c0b0;font-style:italic;">&ldquo;{safe_message}&rdquo;'
            f"<div style=\"padding-top:8px;font-family:'JetBrains Mono',monospace;font-size:9px;"
            f'letter-spacing:.16em;color:#9d9686;text-transform:uppercase;">{safe_purchaser}</div></td></tr>'
        )
        if safe_message
        else ""
    )
    amount = f"${amount_cents / 100:,.2f}"
    if sent_to_purchaser:
        subject = f"Your {amount} tattoo gift card for {gift_recipient_name or artist_name}"
        preheader = f"Your {amount} gift card for {safe_gift_recipient} is ready to share."
        intro = (
            f'Your tattoo gift for <strong style="color:#d38a68;">{safe_gift_recipient}</strong> is ready. '
            f'It includes tattoo credit with <strong style="color:#d38a68;">{safe_artist}</strong>.'
        )
    else:
        subject = f"Your {amount} tattoo gift for {artist_name}"
        preheader = f"{safe_purchaser} sent you {amount} toward your next tattoo with {safe_artist}."
        intro = f'{safe_purchaser} sent you tattoo credit for <strong style="color:#d38a68;">{safe_artist}</strong>.'
    footer_address = studio_address_html(
        "margin:0;font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.15em;color:#6f695d;text-transform:uppercase;"
    )
    html_body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark"><title>Tattoo Gift Card</title>
<!--[if !mso]><!--><link href="https://fonts.googleapis.com/css2?family=Poppins:wght@700;800;900&family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet"><!--<![endif]-->
<style>:root{{color-scheme:dark;supported-color-schemes:dark}}body{{margin:0;padding:0;background-color:#0b0c0b!important}}img{{border:0}}u+.bttco-email-body .bttco-email-panel{{background-image:linear-gradient(#191a19,#191a19)!important}}u+.bttco-email-body .bttco-email-well{{background-image:linear-gradient(#101110,#101110)!important}}</style></head>
<body class="bttco-email-body" style="margin:0;padding:0;background-color:#0b0c0b;">
<span style="display:none;font-size:1px;color:#0b0c0b;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{preheader}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0b0c0b" background="{STARSEED_TILE_URL}" style="background-color:#0b0c0b;background-image:url('{STARSEED_TILE_URL}');background-repeat:repeat;"><tr><td align="center" style="padding:26px 14px;">
<table class="bttco-email-panel" role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#191a19" style="max-width:600px;width:100%;background-color:#191a19;background-image:linear-gradient(#191a19,#191a19);border:1px solid rgba(226,218,194,.14);border-radius:24px;overflow:hidden;">
<tr><td align="center" style="padding:34px 24px 22px;border-bottom:1px solid rgba(226,218,194,.12);"><img src="https://assets.ryozan.app/email/tiger-mark.png" alt="Ryozan Studio" width="72" style="display:block;margin:0 auto 14px;"><div style="font-family:'Poppins','Arial Black',Arial,sans-serif;font-weight:900;font-size:19px;letter-spacing:.18em;color:#e2dac2;text-transform:uppercase;">Ryozan Studio</div><div style="padding-top:8px;font-family:'JetBrains Mono',monospace;font-size:9.5px;font-weight:700;letter-spacing:.22em;color:#d38a68;text-transform:uppercase;">Gift card // paid</div></td></tr>
<tr><td style="padding:26px 26px 18px;font-family:'Space Grotesk',Arial,sans-serif;font-size:14px;line-height:1.65;color:#e2dac2;">Hey {safe_recipient},<br><br>{intro}</td></tr>
{message_block}
<tr><td style="padding:0 26px 24px;"><table class="bttco-email-well" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#101110" style="background-color:#101110;background-image:linear-gradient(#101110,#101110);border:1px solid rgba(211,138,104,.55);border-radius:20px;"><tr><td align="center" style="padding:26px 18px 14px;"><div style="font-family:'Poppins','Arial Black',Arial,sans-serif;font-weight:800;font-size:40px;line-height:1;color:#d38a68;">{amount}</div><div style="padding-top:8px;font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.2em;color:#9d9686;text-transform:uppercase;">Tattoo credit with {safe_artist}</div></td></tr><tr><td align="center" style="padding:0 18px 26px;"><div style="display:inline-block;padding:11px 18px;border:1px solid rgba(226,218,194,.22);border-radius:16px;font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;letter-spacing:.12em;color:#e2dac2;">{safe_code}</div></td></tr></table></td></tr>
<tr><td style="padding:0 26px 28px;font-family:'Space Grotesk',Arial,sans-serif;font-size:13px;line-height:1.65;color:#9d9686;">Bring this code when paying for tattoo services with {safe_artist}. Gift cards are artist-specific, non-refundable, and do not expire.</td></tr>
<tr><td align="center" style="padding:24px 24px 34px;border-top:1px solid rgba(226,218,194,.12);"><p style="margin:0 0 8px;font-family:'Space Grotesk',Arial,sans-serif;font-size:12.5px;color:#9d9686;">Questions? Just reply. A human reads this inbox.</p>{footer_address}</td></tr>
</table></td></tr></table></body></html>"""
    return FoundryEmail(slug="gift_card_receipt", subject=subject, html_body=html_body)


def fill_foundry_merge_tags(source: str, variables: Mapping[str, object]) -> str:
    """Fill nested Foundry merge tags without changing authored markup."""
    rendered = source
    values = {key: "" if value is None else str(value) for key, value in variables.items()}
    for index in (1, 2):
        if not values.get(f"ref_image_{index}", "").strip():
            rendered = re.sub(
                rf"<!--foundry:reference-{index}:start-->.*?<!--foundry:reference-{index}:end-->",
                "",
                rendered,
                flags=re.DOTALL,
            )
    if not any(values.get(f"ref_image_{index}", "").strip() for index in (1, 2)):
        rendered = _FOUNDRY_REFERENCE_IMAGES_RE.sub("", rendered)
    for _ in range(4):
        previous = rendered
        for key, value in values.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        if rendered == previous:
            break
    return rendered


def _foundry_template_field_state(
    state: Mapping[str, Any], slug: str
) -> tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    all_fields = state.get(FOUNDRY_FIELDS_KEY)
    template_state = all_fields.get(slug) if isinstance(all_fields, Mapping) else None
    if not isinstance(template_state, Mapping):
        return {}, {}, {}
    saved_fields = {
        str(key): value for key, value in template_state.items() if isinstance(key, str) and not key.startswith("__")
    }
    bindings = template_state.get(FOUNDRY_BINDINGS_KEY)
    delivery = template_state.get(FOUNDRY_DELIVERY_KEY)
    return (
        saved_fields,
        bindings if isinstance(bindings, Mapping) else {},
        delivery if isinstance(delivery, Mapping) else {},
    )


def _foundry_element_overlays(state: Mapping[str, Any], slug: str) -> Mapping[str, Any]:
    all_fields = state.get(FOUNDRY_FIELDS_KEY)
    template_state = all_fields.get(slug) if isinstance(all_fields, Mapping) else None
    if not isinstance(template_state, Mapping):
        return {}
    overlays = template_state.get(FOUNDRY_ELEMENTS_KEY)
    return overlays if isinstance(overlays, Mapping) else {}


def _foundry_field_is_fixed(bindings: Mapping[str, Any], key: str) -> bool:
    binding = bindings.get(key)
    if isinstance(binding, Mapping):
        binding = binding.get("mode")
    return str(binding or "automatic").strip().lower() == "fixed"


def merge_foundry_variables(state: Mapping[str, Any], slug: str, *sources: Mapping[str, Any]) -> dict[str, Any]:
    """Apply Courier field bindings to saved values and current event data."""
    merged, bindings, _ = _foundry_template_field_state(state, slug)
    for source in sources:
        for key, value in source.items():
            normalized_key = str(key)
            if normalized_key.startswith("__") or _foundry_field_is_fixed(bindings, normalized_key):
                continue
            merged[normalized_key] = value
    return merged


def resolve_foundry_recipient(state: Mapping[str, Any], slug: str, event_recipient: str) -> str | None:
    """Resolve the transactional recipient from current tenant delivery policy."""
    _, _, delivery = _foundry_template_field_state(state, slug)
    mode = str(delivery.get("recipientMode") or "automatic").strip().lower()
    candidate = delivery.get("fixedRecipient") if mode == "fixed" else event_recipient
    normalized = str(candidate or "").strip().lower()
    return normalized if _EMAIL_RE.fullmatch(normalized) else None


def foundry_state_for_user(user: BTTCOUser) -> dict[str, Any]:
    """Return one complete adapter snapshot from the founder's durable profile.

    The snapshot is a deep copy. Callers mutate it freely and hand it back to
    ``store_foundry_state``; it must never alias the ORM-tracked JSONB value,
    otherwise in-place edits make the old and new values compare equal and
    SQLAlchemy silently skips the UPDATE.
    """
    profile = user.nexus_profile or {}
    stored = profile.get("email_foundry_state") or {}
    return {
        key: deepcopy(stored[key]) if key in stored else deepcopy(default)
        for key, default in FOUNDRY_STATE_DEFAULTS.items()
    }


def validate_foundry_state_value(key: str, value: Any) -> Any:
    """Reject malformed adapter writes before they become executable state."""
    if key not in FOUNDRY_STATE_DEFAULTS:
        raise ValueError("Unsupported Email Foundry state key")
    if key in {FOUNDRY_FIELDS_KEY, FOUNDRY_SCHEDULE_KEY, FOUNDRY_CONNECTORS_KEY} and not isinstance(value, dict):
        raise ValueError("Email Foundry fields, schedules, and connectors must be objects")
    if key in {FOUNDRY_CUSTOM_KEY, FOUNDRY_OUTBOX_KEY} and not isinstance(value, list):
        raise ValueError("Email Foundry custom templates and outbox must be arrays")
    if key == FOUNDRY_OUTBOX_KEY:
        return [row for row in value if isinstance(row, dict)][:_MAX_OUTBOX_ROWS]
    return value


def _foundry_connector_configured(connector_id: str) -> bool:
    from app.core.config import get_settings
    from app.services.calendar_sync import calendar_connection_configured

    settings = get_settings()
    checks = {
        "sendgrid": bool(settings.SENDGRID_API_KEY.strip()),
        "resend": bool(settings.RESEND_API_KEY.strip()),
        "smtp": bool(settings.SMTP_USER.strip() and settings.SMTP_PASSWORD.strip()),
        "twilio": bool(
            settings.TWILIO_ACCOUNT_SID.strip()
            and settings.TWILIO_AUTH_TOKEN.strip()
            and (settings.TWILIO_FROM_NUMBER.strip() or settings.TWILIO_MESSAGING_SERVICE_SID.strip())
        ),
        "cloudflare-r2": bool(
            settings.R2_ACCOUNT_ID.strip()
            and settings.R2_ACCESS_KEY_ID.strip()
            and settings.R2_SECRET_ACCESS_KEY.strip()
            and settings.R2_BUCKET_NAME.strip()
        ),
        "google-calendar": calendar_connection_configured(),
        "stripe": bool(settings.STRIPE_SECRET_KEY.strip()),
        "meta-whatsapp": bool(
            settings.META_WHATSAPP_PHONE_NUMBER_ID.strip() and settings.META_WHATSAPP_ACCESS_TOKEN.strip()
        ),
        "meta-instagram": bool(
            settings.META_INSTAGRAM_ACCOUNT_ID.strip() and settings.META_INSTAGRAM_PAGE_ACCESS_TOKEN.strip()
        ),
        "meta-facebook": bool(
            settings.META_FACEBOOK_PAGE_ID.strip() and settings.META_FACEBOOK_PAGE_ACCESS_TOKEN.strip()
        ),
    }
    if connector_id not in checks:
        raise ValueError("Unsupported Foundry connector")
    return checks[connector_id]


def foundry_connector_inventory(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project Ryozan-owned provider bindings without exposing provider credentials."""
    stored = state.get(FOUNDRY_CONNECTORS_KEY)
    rows = stored if isinstance(stored, dict) else {}
    inventory: list[dict[str, Any]] = []
    for connector_id, (label, channel) in _FOUNDRY_CONNECTORS.items():
        configured = _foundry_connector_configured(connector_id)
        saved = rows.get(connector_id) if isinstance(rows.get(connector_id), dict) else {}
        enabled = bool(saved.get("enabled"))
        last_test_passed = saved.get("lastTestPassed") is True
        connected = configured and enabled and last_test_passed
        state_label = "connected" if connected else "configured" if configured else "not-configured"
        if connected:
            evidence = "Ryozan credentials passed a live provider probe and this Courier binding is enabled."
        elif configured:
            evidence = "Ryozan owns configured credentials. Connect to enable and verify this Courier binding."
        else:
            evidence = "No Ryozan provider credential is configured. Add it in api/.env, then connect."
        inventory.append(
            {
                "id": connector_id,
                "label": label,
                "channel": channel,
                "configured": configured,
                "connected": connected,
                "selected": connected,
                "state": state_label,
                "evidence": evidence,
                "actions": ["test", "reconnect", "disconnect"] if connected else ["connect"],
                "lastTestAt": saved.get("lastTestAt"),
                "lastTestPassed": saved.get("lastTestPassed"),
            }
        )
    return inventory


async def _probe_foundry_connector(connector_id: str) -> tuple[bool, str]:
    from app.core.config import get_settings

    settings = get_settings()
    if not _foundry_connector_configured(connector_id):
        return False, "Ryozan credentials are not configured"

    try:
        if connector_id == "sendgrid":
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    "https://api.sendgrid.com/v3/scopes",
                    headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"},
                )
            return response.status_code == 200, f"SendGrid credential probe returned HTTP {response.status_code}"

        if connector_id == "resend":
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    "https://api.resend.com/domains?limit=1",
                    headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                )
            return response.status_code == 200, f"Resend credential probe returned HTTP {response.status_code}"

        if connector_id == "smtp":
            local_capture = settings.SMTP_PORT == 1025 and settings.SMTP_HOST.strip().lower() in {
                "127.0.0.1",
                "localhost",
                "mailhog",
            }
            smtp = aiosmtplib.SMTP(
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                start_tls=not local_capture,
                timeout=8,
            )
            await smtp.connect()
            if not local_capture:
                await smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            await smtp.quit()
            return True, "SMTP accepted a live connection and authentication probe"

        if connector_id == "twilio":
            account_url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}.json"
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(account_url, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
            return response.status_code == 200, f"Twilio account probe returned HTTP {response.status_code}"

        if connector_id == "google-calendar":
            from app.services.calendar_sync import check_calendar_connection

            status = await asyncio.to_thread(check_calendar_connection)
            if status.get("healthy"):
                return True, "Google Calendar refreshed its credential and read the shop calendar"
            return False, f"Google Calendar probe failed: {status.get('reason') or 'unavailable'}"

        if connector_id == "stripe":
            from app.services.stripe_client import get_stripe_client

            balance = await asyncio.to_thread(get_stripe_client().v1.balance.retrieve)
            livemode = "live" if getattr(balance, "livemode", False) else "test"
            return True, f"Stripe returned the {livemode}-mode account balance"

        if connector_id.startswith("meta-"):
            node_id, token = {
                "meta-whatsapp": (settings.META_WHATSAPP_PHONE_NUMBER_ID, settings.META_WHATSAPP_ACCESS_TOKEN),
                "meta-instagram": (settings.META_INSTAGRAM_ACCOUNT_ID, settings.META_INSTAGRAM_PAGE_ACCESS_TOKEN),
                "meta-facebook": (settings.META_FACEBOOK_PAGE_ID, settings.META_FACEBOOK_PAGE_ACCESS_TOKEN),
            }[connector_id]
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{node_id.strip()}",
                    params={"fields": "id"},
                    headers={"Authorization": f"Bearer {token.strip()}"},
                )
            return response.status_code == 200, f"Meta Graph node probe returned HTTP {response.status_code}"

        from app.services.storage import _r2_sign

        host = f"{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        path = f"/{settings.R2_BUCKET_NAME}"
        headers = {"host": host}
        headers["Authorization"] = _r2_sign("HEAD", path, headers, settings.R2_SECRET_ACCESS_KEY)
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.head(f"https://{host}{path}", headers=headers)
        return response.status_code in {200, 204}, f"Cloudflare R2 bucket probe returned HTTP {response.status_code}"
    except Exception as exc:
        return False, f"Provider probe failed with {type(exc).__name__}"


async def apply_foundry_connector_action(state: dict[str, Any], connector_id: str, action: str) -> dict[str, Any]:
    """Apply one founder-scoped Courier binding action and retain a test receipt."""
    if connector_id not in _FOUNDRY_CONNECTORS:
        raise ValueError("Unsupported Foundry connector")
    if action not in {"connect", "reconnect", "disconnect", "test"}:
        raise ValueError("Unsupported Foundry connector action")

    saved = state.setdefault(FOUNDRY_CONNECTORS_KEY, {})
    if not isinstance(saved, dict):
        saved = {}
        state[FOUNDRY_CONNECTORS_KEY] = saved
    row = dict(saved.get(connector_id) or {})
    observed_at = datetime.now(UTC).isoformat()

    if action == "disconnect":
        row.update({"enabled": False, "updatedAt": observed_at})
        saved[connector_id] = row
        probe_ok = True
        detail = "Courier binding disabled. Ryozan retained the tenant-owned provider credential."
    else:
        probe_ok, detail = await _probe_foundry_connector(connector_id)
        row.update({"lastTestAt": observed_at, "lastTestPassed": probe_ok, "updatedAt": observed_at})
        if action in {"connect", "reconnect"}:
            row["enabled"] = probe_ok
        saved[connector_id] = row

    connector = next(item for item in foundry_connector_inventory(state) if item["id"] == connector_id)
    return {
        "ok": probe_ok,
        "action": action,
        "connector": connector,
        "receipt": {
            "connectorId": connector_id,
            "action": action,
            "observedAt": observed_at,
            "result": "passed" if probe_ok else "failed",
            "detail": detail,
        },
    }


def merge_foundry_outbox(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve authoritative receipts when a stale iframe writes its local copy."""
    by_id = {str(row.get("id")): row for row in existing if row.get("id")}
    merged: list[dict[str, Any]] = []
    for raw in incoming:
        row = dict(raw)
        row_id = str(row.get("id") or "")
        previous = by_id.get(row_id)
        if previous and (
            previous.get("status") in _TERMINAL_OUTBOX_STATUSES
            or (previous.get("status") == "held for sign-off" and row.get("status") != "approved")
        ):
            row = dict(previous)
        if row.get("status") not in FOUNDRY_OUTBOX_STATUSES:
            row["status"] = "failed"
            row["error"] = "Unsupported outbox status"
        merged.append(row)
    return merged[:_MAX_OUTBOX_ROWS]


def store_foundry_state(user: BTTCOUser, state: Mapping[str, Any]) -> None:
    """Write the adapter snapshot back and force the JSONB column dirty.

    ``nexus_profile`` is a plain JSONB column without mutation tracking. When a
    caller mutated nested state in place, the reassigned dict compares equal to
    the committed value and the UPDATE is dropped, which is how a provider
    connect could return ``connected: true`` and read back as ``configured``
    on the next request. ``flag_modified`` makes the write unconditional.
    """
    profile = dict(user.nexus_profile or {})
    profile["email_foundry_state"] = deepcopy(dict(state))
    user.nexus_profile = profile
    flag_modified(user, "nexus_profile")


def create_foundry_unsubscribe_token(user_id: UUID) -> str:
    """Create a signed, non-session token for one marketing preference change."""
    from jose import jwt

    from app.core.config import get_settings

    settings = get_settings()
    return jwt.encode(
        {
            "sub": str(user_id),
            "typ": "foundry_unsubscribe",
            "iat": datetime.now(UTC),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_foundry_unsubscribe_token(token: str) -> UUID:
    from jose import JWTError, jwt

    from app.core.config import get_settings

    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("typ") != "foundry_unsubscribe":
            raise ValueError("Invalid unsubscribe token")
        return UUID(str(payload.get("sub") or ""))
    except (JWTError, TypeError, ValueError):
        raise ValueError("Invalid unsubscribe token") from None


def foundry_unsubscribe_url(user_id: UUID) -> str:
    from app.core.config import get_settings

    settings = get_settings()
    token = create_foundry_unsubscribe_token(user_id)
    return f"{settings.API_PUBLIC_BASE_URL.rstrip('/')}/api/v1/email-templates/foundry/unsubscribe?token={token}"


def _render_outbox_email(
    state: Mapping[str, Any], entry: Mapping[str, Any], variables: Mapping[str, Any]
) -> FoundryEmail | None:
    slug = str(entry.get("slug") or "")
    entry_variables = entry.get("variables")
    merged_variables = merge_foundry_variables(
        state,
        slug,
        entry_variables if isinstance(entry_variables, Mapping) else {},
        variables,
    )
    element_overlays = _foundry_element_overlays(state, slug)
    rendered = render_foundry_email(slug, merged_variables, element_overlays=element_overlays)
    if rendered:
        return rendered

    # Custom Forge rows are compiled by the locked adapter using forgeHtml().
    # The BTTCO sender only fills per-recipient merge tags in that exact output.
    template_html = entry.get("templateHtml")
    template_subject = entry.get("templateSubject")
    if slug.startswith("forge:") and isinstance(template_html, str) and isinstance(template_subject, str):
        return FoundryEmail(
            slug=slug,
            subject=fill_foundry_merge_tags(template_subject, merged_variables),
            html_body=apply_foundry_element_overlays(
                fill_foundry_merge_tags(template_html, merged_variables),
                element_overlays,
            ),
        )
    return None


def _receipt(*, accepted: bool, recipients: int, failures: int, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "providerAccepted": accepted,
        "recipients": recipients,
        "failures": failures,
        "processedAt": datetime.now(UTC).isoformat(),
    }
    if error:
        payload["error"] = error
    return payload


def parse_foundry_delay_hours(value: Any) -> float | None:
    if value is None or not str(value).strip():
        return 0
    match = re.fullmatch(
        r"\+?(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours|d|day|days|m|min|mins|minutes)?", str(value).strip(), re.I
    )
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "h")[0].lower()
    return amount * 24 if unit == "d" else amount / 60 if unit == "m" else amount


async def _send_foundry_single(
    state: Mapping[str, Any],
    schedule: Mapping[str, Any],
    actor: BTTCOUser,
    *,
    slug: str,
    recipient: str,
    variables: Mapping[str, Any],
    delivery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.services.email import send_email

    entry: dict[str, Any] = {"slug": slug}
    for key in ("templateHtml", "templateSubject"):
        if isinstance(schedule.get(key), str):
            entry[key] = schedule[key]
    settings = get_settings()
    merged = {
        **variables,
        "unsubscribe_url": f"{settings.FRONTEND_URL.rstrip('/')}/dashboard",
    }
    rendered = _render_outbox_email(state, entry, merged)
    if not rendered:
        return _receipt(
            accepted=False,
            recipients=0,
            failures=1,
            error="No authoritative Foundry renderer exists for this template",
        )
    delivery_options = {
        key: str(value) for key in ("from_alias", "courier_message_id") if (value := (delivery or {}).get(key))
    }
    accepted = await send_email(
        to=recipient,
        subject=rendered.subject,
        html_body=rendered.html_body,
        tags=[slug],
        sender_id=str(actor.id),
        sender_role=actor.role,
        **delivery_options,
    )
    return _receipt(
        accepted=accepted,
        recipients=1 if accepted else 0,
        failures=0 if accepted else 1,
        error=None if accepted else "The configured email provider rejected the event send",
    )


async def dispatch_foundry_trigger(
    db: AsyncSession,
    trigger: str,
    *,
    recipient: str,
    variables: Mapping[str, Any],
    event_id: str | None = None,
    occurred_at: datetime | None = None,
    owners: list[BTTCOUser] | None = None,
    delivery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind one Ryozan domain event to every matching Foundry schedule."""
    if trigger not in FOUNDRY_TRIGGERS:
        raise ValueError(f"Unsupported Foundry trigger: {trigger}")
    normalized_event_recipient = recipient.strip().lower()
    observed_at = (occurred_at or datetime.now(UTC)).astimezone(UTC)
    stable_event_id = event_id or f"{trigger}:{uuid4()}"
    if owners is None:
        owner_result = await db.execute(select(BTTCOUser).where(BTTCOUser.role == "founder"))
        owners = list(owner_result.scalars().all())

    matched: list[str] = []
    sent = 0
    queued = 0
    failed = 0
    for owner in owners:
        state = foundry_state_for_user(owner)
        schedules = state.get(FOUNDRY_SCHEDULE_KEY) or {}
        for slug, raw_schedule in schedules.items():
            if not isinstance(raw_schedule, dict):
                continue
            if raw_schedule.get("mode") != "trigger" or raw_schedule.get("trigger") != trigger:
                continue
            receipts = [row for row in raw_schedule.get("receipts", []) if isinstance(row, dict)]
            pending = [row for row in raw_schedule.get("pending", []) if isinstance(row, dict)]
            existing_pending = next(
                (row for row in pending if str(row.get("eventId")) == stable_event_id),
                None,
            )
            existing_receipt = next(
                (row for row in receipts if str(row.get("eventId")) == stable_event_id),
                None,
            )
            if existing_pending is not None:
                matched.append(slug)
                queued += 1
                continue
            if existing_receipt is not None and existing_receipt.get("status") == "sent":
                matched.append(slug)
                sent += 1
                continue
            if existing_receipt is not None:
                # A failed receipt is retryable. Remove only that failed
                # projection and let the normal send path create the new
                # authoritative receipt for the stable event ID.
                receipts = [row for row in receipts if str(row.get("eventId")) != stable_event_id]
            delay = parse_foundry_delay_hours(raw_schedule.get("delay"))
            resolved_recipient = resolve_foundry_recipient(state, slug, normalized_event_recipient)
            matched.append(slug)
            if delay is None:
                failed += 1
                receipts.insert(
                    0,
                    {
                        "eventId": stable_event_id,
                        "trigger": trigger,
                        "status": "failed",
                        "receipt": _receipt(
                            accepted=False,
                            recipients=0,
                            failures=1,
                            error="Invalid Foundry trigger delay",
                        ),
                    },
                )
            elif resolved_recipient is None:
                failed += 1
                receipts.insert(
                    0,
                    {
                        "eventId": stable_event_id,
                        "trigger": trigger,
                        "status": "failed",
                        "receipt": _receipt(
                            accepted=False,
                            recipients=0,
                            failures=1,
                            error="The configured Foundry recipient is missing or invalid",
                        ),
                    },
                )
            elif delay > 0:
                pending.append(
                    {
                        "eventId": stable_event_id,
                        "trigger": trigger,
                        "to": resolved_recipient,
                        "eventRecipient": normalized_event_recipient,
                        "variables": dict(variables),
                        "delivery": dict(delivery or {}),
                        "occurredAt": observed_at.isoformat(),
                        "dueAt": (observed_at + timedelta(hours=delay)).isoformat(),
                    }
                )
                queued += 1
            else:
                receipt = await _send_foundry_single(
                    state,
                    raw_schedule,
                    owner,
                    slug=slug,
                    recipient=resolved_recipient,
                    variables=variables,
                    delivery=delivery,
                )
                accepted = bool(receipt["providerAccepted"])
                sent += int(accepted)
                failed += int(not accepted)
                receipts.insert(
                    0,
                    {
                        "eventId": stable_event_id,
                        "trigger": trigger,
                        "status": "sent" if accepted else "failed",
                        "receipt": receipt,
                    },
                )
            raw_schedule["receipts"] = receipts[:20]
            raw_schedule["pending"] = pending[:100]
        store_foundry_state(owner, state)
    await db.flush()
    return {"matched": sorted(set(matched)), "sent": sent, "queued": queued, "failed": failed}


async def _execute_foundry_audience_trigger(
    db: AsyncSession,
    state: dict[str, Any],
    schedule: Mapping[str, Any],
    owner: BTTCOUser,
    *,
    slug: str,
    event_id: str,
    trigger: str,
    variables: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    entry_id = f"trigger:{event_id}:{slug}"
    entry: dict[str, Any] = {
        "id": entry_id,
        "at": observed_at.isoformat(),
        "slug": slug,
        "name": schedule.get("name") or slug,
        "to": "full send list",
        "kind": "manual",
        "status": "approved",
        "trigger": trigger,
        "variables": dict(variables),
    }
    for key in ("templateHtml", "templateSubject"):
        if isinstance(schedule.get(key), str):
            entry[key] = schedule[key]
    current = [row for row in state[FOUNDRY_OUTBOX_KEY] if str(row.get("id")) != entry_id]
    state[FOUNDRY_OUTBOX_KEY] = [entry, *current][:_MAX_OUTBOX_ROWS]
    await process_foundry_outbox(db, owner, state, only_entry_ids={entry_id})
    completed = next(row for row in state[FOUNDRY_OUTBOX_KEY] if str(row.get("id")) == entry_id)
    return completed.get("receipt") or _receipt(
        accepted=False,
        recipients=0,
        failures=1,
        error="Foundry audience trigger produced no receipt",
    )


async def dispatch_foundry_audience_trigger(
    db: AsyncSession,
    trigger: str,
    *,
    variables: Mapping[str, Any],
    event_id: str,
    occurred_at: datetime | None = None,
    owners: list[BTTCOUser] | None = None,
) -> dict[str, Any]:
    """Bind a shop-wide event such as flash_published to the consent list."""
    if trigger not in FOUNDRY_TRIGGERS:
        raise ValueError(f"Unsupported Foundry trigger: {trigger}")
    observed_at = (occurred_at or datetime.now(UTC)).astimezone(UTC)
    if owners is None:
        owner_result = await db.execute(select(BTTCOUser).where(BTTCOUser.role == "founder"))
        owners = list(owner_result.scalars().all())
    matched: list[str] = []
    sent = 0
    queued = 0
    failed = 0
    for owner in owners:
        state = foundry_state_for_user(owner)
        schedules = state.get(FOUNDRY_SCHEDULE_KEY) or {}
        for slug, raw_schedule in schedules.items():
            if not isinstance(raw_schedule, dict):
                continue
            if raw_schedule.get("mode") != "trigger" or raw_schedule.get("trigger") != trigger:
                continue
            receipts = [row for row in raw_schedule.get("receipts", []) if isinstance(row, dict)]
            pending = [row for row in raw_schedule.get("pending", []) if isinstance(row, dict)]
            if any(str(row.get("eventId")) == event_id for row in [*receipts, *pending]):
                matched.append(slug)
                continue
            matched.append(slug)
            delay = parse_foundry_delay_hours(raw_schedule.get("delay"))
            if delay is None:
                receipt = _receipt(
                    accepted=False,
                    recipients=0,
                    failures=1,
                    error="Invalid Foundry trigger delay",
                )
                failed += 1
                receipts.insert(0, {"eventId": event_id, "trigger": trigger, "status": "failed", "receipt": receipt})
            elif delay > 0:
                pending.append(
                    {
                        "eventId": event_id,
                        "trigger": trigger,
                        "audience": "marketing",
                        "variables": dict(variables),
                        "occurredAt": observed_at.isoformat(),
                        "dueAt": (observed_at + timedelta(hours=delay)).isoformat(),
                    }
                )
                queued += 1
            else:
                receipt = await _execute_foundry_audience_trigger(
                    db,
                    state,
                    raw_schedule,
                    owner,
                    slug=slug,
                    event_id=event_id,
                    trigger=trigger,
                    variables=variables,
                    observed_at=observed_at,
                )
                accepted = bool(receipt["providerAccepted"])
                sent += int(accepted)
                failed += int(not accepted)
                receipts.insert(
                    0,
                    {
                        "eventId": event_id,
                        "trigger": trigger,
                        "status": "sent" if accepted else "failed",
                        "receipt": receipt,
                    },
                )
            raw_schedule["pending"] = pending[:100]
            raw_schedule["receipts"] = receipts[:20]
        store_foundry_state(owner, state)
    await db.flush()
    return {"matched": sorted(set(matched)), "sent": sent, "queued": queued, "failed": failed}


async def process_foundry_trigger_queue(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    owners: list[BTTCOUser] | None = None,
) -> dict[str, int]:
    """Execute delayed domain-event sends from the existing hourly worker."""
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    if owners is None:
        owner_result = await db.execute(select(BTTCOUser).where(BTTCOUser.role == "founder"))
        owners = list(owner_result.scalars().all())
    due = 0
    sent = 0
    failed = 0
    for owner in owners:
        state = foundry_state_for_user(owner)
        schedules = state.get(FOUNDRY_SCHEDULE_KEY) or {}
        for slug, raw_schedule in schedules.items():
            if not isinstance(raw_schedule, dict):
                continue
            future: list[dict[str, Any]] = []
            receipts = [row for row in raw_schedule.get("receipts", []) if isinstance(row, dict)]
            for row in [entry for entry in raw_schedule.get("pending", []) if isinstance(entry, dict)]:
                try:
                    due_at = datetime.fromisoformat(str(row.get("dueAt")).replace("Z", "+00:00"))
                    if due_at.tzinfo is None:
                        due_at = due_at.replace(tzinfo=UTC)
                except ValueError:
                    due_at = observed_at
                if due_at.astimezone(UTC) > observed_at:
                    future.append(row)
                    continue
                due += 1
                if row.get("audience") == "marketing":
                    receipt = await _execute_foundry_audience_trigger(
                        db,
                        state,
                        raw_schedule,
                        owner,
                        slug=slug,
                        event_id=str(row.get("eventId") or uuid4()),
                        trigger=str(row.get("trigger") or "manual"),
                        variables=row.get("variables") or {},
                        observed_at=observed_at,
                    )
                else:
                    resolved_recipient = resolve_foundry_recipient(
                        state,
                        slug,
                        str(row.get("eventRecipient") or row.get("to") or ""),
                    )
                    if resolved_recipient is None:
                        receipt = _receipt(
                            accepted=False,
                            recipients=0,
                            failures=1,
                            error="The configured Foundry recipient is missing or invalid",
                        )
                    else:
                        receipt = await _send_foundry_single(
                            state,
                            raw_schedule,
                            owner,
                            slug=slug,
                            recipient=resolved_recipient,
                            variables=row.get("variables") or {},
                            delivery=row.get("delivery") or {},
                        )
                accepted = bool(receipt["providerAccepted"])
                sent += int(accepted)
                failed += int(not accepted)
                receipts.insert(
                    0,
                    {
                        "eventId": row.get("eventId"),
                        "trigger": row.get("trigger"),
                        "status": "sent" if accepted else "failed",
                        "receipt": receipt,
                    },
                )
            raw_schedule["pending"] = future[:100]
            raw_schedule["receipts"] = receipts[:20]
        store_foundry_state(owner, state)
    await db.flush()
    return {"due": due, "sent": sent, "failed": failed}


async def process_foundry_outbox(
    db: AsyncSession,
    actor: BTTCOUser,
    state: dict[str, Any],
    *,
    only_entry_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Drain executable rows and return the authoritative receipt-backed outbox."""
    from app.core.config import get_settings
    from app.services.email import send_email

    outbox = [dict(row) for row in state.get(FOUNDRY_OUTBOX_KEY, []) if isinstance(row, dict)]
    settings = get_settings()
    for entry in outbox:
        entry_id = str(entry.get("id") or "")
        if only_entry_ids is not None and entry_id not in only_entry_ids:
            continue
        kind = entry.get("kind")
        status = entry.get("status")
        if not ((kind == "test" and status == "queued") or (kind == "manual" and status == "approved")):
            continue

        slug = str(entry.get("slug") or "")
        if kind == "test":
            recipient = str(entry.get("to") or "").strip().lower()
            if not _EMAIL_RE.fullmatch(recipient):
                entry["status"] = "failed"
                entry["receipt"] = _receipt(
                    accepted=False,
                    recipients=0,
                    failures=1,
                    error="A valid single test address is required",
                )
                continue
            rendered = _render_outbox_email(
                state,
                entry,
                {
                    "client_name": settings.STUDIO_OWNER_NAME.strip() or "Test Recipient",
                    "name": settings.STUDIO_OWNER_NAME.strip() or "Test Recipient",
                    "unsubscribe_url": f"{settings.FRONTEND_URL.rstrip('/')}/dashboard",
                },
            )
            if not rendered:
                entry["status"] = "failed"
                entry["receipt"] = _receipt(
                    accepted=False,
                    recipients=0,
                    failures=1,
                    error="No authoritative Foundry renderer exists for this template",
                )
                continue
            accepted = await send_email(
                to=recipient,
                subject=rendered.subject,
                html_body=rendered.html_body,
                tags=[slug],
                sender_id=str(actor.id),
                sender_role=actor.role,
            )
            entry["status"] = "sent" if accepted else "failed"
            entry["receipt"] = _receipt(
                accepted=accepted,
                recipients=1 if accepted else 0,
                failures=0 if accepted else 1,
                error=None if accepted else "The configured email provider rejected the test send",
            )
            continue

        audience_result = await db.execute(
            select(BTTCOUser).where(BTTCOUser.marketing_opt_in.is_(True)).order_by(BTTCOUser.created_at)
        )
        audience = [user for user in audience_result.scalars().all() if _EMAIL_RE.fullmatch(user.email or "")]
        sent = 0
        failed = 0
        for recipient in audience:
            rendered = _render_outbox_email(
                state,
                entry,
                {
                    "client_name": (recipient.name or "there").split(" ")[0],
                    "name": recipient.name or "there",
                    "unsubscribe_url": foundry_unsubscribe_url(recipient.id),
                },
            )
            if not rendered:
                failed += 1
                continue
            accepted = await send_email(
                to=recipient.email,
                subject=rendered.subject,
                html_body=rendered.html_body,
                tags=[slug],
                sender_id=str(actor.id),
                sender_role=actor.role,
            )
            sent += int(accepted)
            failed += int(not accepted)

        accepted = bool(audience) and failed == 0
        entry["status"] = "sent" if accepted else "failed"
        entry["receipt"] = _receipt(
            accepted=accepted,
            recipients=sent,
            failures=failed,
            error="No opted-in recipients exist" if not audience else None,
        )

    state[FOUNDRY_OUTBOX_KEY] = outbox[:_MAX_OUTBOX_ROWS]
    return state[FOUNDRY_OUTBOX_KEY]


def _next_month(value: datetime) -> datetime:
    month = value.month + 1
    year = value.year
    if month == 13:
        month = 1
        year += 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def campaign_occurrence_due(schedule: Mapping[str, Any], now: datetime) -> datetime | None:
    """Return the newest unprocessed campaign occurrence using locked nextRun semantics."""
    if schedule.get("mode") != "campaign" or not schedule.get("date"):
        return None
    try:
        local_base = datetime.fromisoformat(f"{schedule['date']}T{schedule.get('time') or '12:00'}")
        occurrence = local_base.replace(tzinfo=SHOP_TIMEZONE).astimezone(UTC)
    except (TypeError, ValueError):
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    if occurrence > now:
        return None

    repeat = schedule.get("repeat") or "once"
    if repeat not in {"once", "weekly", "monthly"}:
        return None
    if repeat != "once":
        guard = 0
        while guard < 600:
            candidate = occurrence + timedelta(days=7) if repeat == "weekly" else _next_month(occurrence)
            if candidate > now:
                break
            occurrence = candidate
            guard += 1

    last_run_raw = schedule.get("lastRunAt")
    if last_run_raw:
        try:
            last_run = datetime.fromisoformat(str(last_run_raw).replace("Z", "+00:00"))
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=UTC)
            if last_run.astimezone(UTC) >= occurrence:
                return None
        except ValueError:
            pass
    return occurrence


async def process_foundry_campaigns(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    owners: list[BTTCOUser] | None = None,
) -> dict[str, int]:
    """Execute due Foundry campaigns for opted-in Ryozan recipients exactly once."""
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    if owners is None:
        owner_result = await db.execute(select(BTTCOUser).where(BTTCOUser.role == "founder"))
        owners = list(owner_result.scalars().all())

    due = 0
    sent = 0
    failed = 0
    for owner in owners:
        state = foundry_state_for_user(owner)
        schedules = state.get(FOUNDRY_SCHEDULE_KEY) or {}
        for slug, raw_schedule in list(schedules.items()):
            if not isinstance(raw_schedule, dict):
                continue
            occurrence = campaign_occurrence_due(raw_schedule, observed_at)
            if not occurrence:
                continue
            due += 1
            entry_id = f"campaign:{slug}:{int(occurrence.timestamp())}"
            entry = {
                "id": entry_id,
                "at": observed_at.isoformat(),
                "scheduledFor": occurrence.isoformat(),
                "slug": slug,
                "name": raw_schedule.get("name") or slug,
                "to": "full send list",
                "kind": "manual",
                "status": "approved",
            }
            for key in ("templateHtml", "templateSubject"):
                if isinstance(raw_schedule.get(key), str):
                    entry[key] = raw_schedule[key]
            current_outbox = [row for row in state[FOUNDRY_OUTBOX_KEY] if str(row.get("id")) != entry_id]
            state[FOUNDRY_OUTBOX_KEY] = [entry, *current_outbox][:_MAX_OUTBOX_ROWS]
            await process_foundry_outbox(db, owner, state, only_entry_ids={entry_id})
            completed = next(row for row in state[FOUNDRY_OUTBOX_KEY] if str(row.get("id")) == entry_id)
            raw_schedule["lastRunAt"] = occurrence.isoformat()
            raw_schedule["lastStatus"] = completed.get("status")
            raw_schedule["lastReceipt"] = completed.get("receipt")
            sent += int(completed.get("status") == "sent")
            failed += int(completed.get("status") == "failed")
        store_foundry_state(owner, state)
    await db.flush()
    return {"due": due, "sent": sent, "failed": failed}
