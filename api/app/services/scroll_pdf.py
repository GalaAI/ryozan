"""
Artist Scroll PDF builder: renders a submitted scroll into a self-contained legal record.

Why this is a new module rather than a helper factored out of waiver.py:
`export_waiver_pdf` builds its document inline in the endpoint body. Its styles, field
tables, and image fetches are interleaved with waiver-specific lookups (DocumentTemplate
terms, `_require_waiver_access`, waiver `data_json` keys) and it re-downloads its images
over HTTP with a bare `except: pass`. There is no seam to lift out without rewriting that
endpoint, and 148 waivers in production depend on its byte-for-byte behaviour. So the
scroll gets its own builder, which differs from the waiver's in ways that matter anyway:

  - it renders *every* submitted field generically, since a scroll's fields are dynamic
  - it embeds certificate/ID uploads as full pages, which the waiver never does
  - it takes image bytes directly instead of re-fetching them from R2, so the archive is
    built from exactly what was uploaded and cannot silently differ from it
  - it records storage outcome per asset, so the archive never claims a document that was
    not actually stored

Accepts HEIC (via pillow_heif) and corrects EXIF orientation. Non-image uploads (a cert
may legitimately be a PDF) are referenced on their own page rather than embedded, since
the API has no PDF-merge dependency; the page states plainly that the file is stored
separately and gives its URL.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image as RLImage,
)
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.constants.timezones import shop_timezone
from app.services.storage import private_id_key

logger = logging.getLogger("ryozan.scroll_pdf")

# HEIC/HEIF support. iPhone photos of a certificate arrive as HEIC, and without this
# Pillow cannot open them at all. Registered lazily and defensively: a missing or broken
# pillow_heif must degrade the archive to a reference page, never fail the whole PDF.
try:  # pragma: no cover - exercised implicitly by any HEIC upload
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception as exc:  # pragma: no cover - defensive
    logger.warning("pillow_heif unavailable, HEIC certs will be referenced not embedded: %s", exc)

# Longest edge kept when embedding a cert page. Keeps the archive PDF a sane size
# without throwing away legibility of a certificate number.
MAX_EMBED_EDGE_PX = 2000

_PAGE_WIDTH, _PAGE_HEIGHT = letter
_SIDE_MARGIN = 0.75 * inch
_TOP_MARGIN = 1.45 * inch  # clears the branded header band
_BOTTOM_MARGIN = 0.85 * inch  # clears the footer rule
_CONTENT_WIDTH = _PAGE_WIDTH - 2 * _SIDE_MARGIN
_CONTENT_HEIGHT = _PAGE_HEIGHT - _TOP_MARGIN - _BOTTOM_MARGIN - 1.6 * inch

# Ryozan print palette. Email Foundry's colors, adapted for paper: the dark ground is kept for
# the header band only, body copy stays ink-on-white so the archive prints cleanly.
_INK = colors.HexColor("#161512")
_GROUND = colors.HexColor("#121312")
_CREAM = colors.HexColor("#e2dac2")
_GOLD = colors.HexColor("#c8a15e")
_GOLD_DEEP = colors.HexColor("#8a6a2e")
_TEAL = colors.HexColor("#3f7f6d")
_RED = colors.HexColor("#a03d33")
_MUTED = colors.HexColor("#7a7263")
_RULE = colors.HexColor("#ddd3bd")
_WELL = colors.HexColor("#f6f1e6")

# Standard PDF fonts stand in for the brand faces (Poppins/Space Grotesk/JetBrains Mono) so the
# archive text stays searchable and extractable without embedding font files.
_DISPLAY = "Helvetica-Bold"
_BODY = "Helvetica"
_BODY_BOLD = "Helvetica-Bold"
_MONO = "Courier-Bold"

_STUDIO_MARK = Path(__file__).resolve().parent.parent / "assets" / "brand" / "ryozan-mark.png"


def _studio_name() -> str:
    from app.core.config import get_settings

    return get_settings().STUDIO_NAME


def _studio_footer() -> str:
    from app.core.config import get_settings

    settings = get_settings()
    return (settings.STUDIO_ADDRESS.strip() or settings.STUDIO_NAME).upper()


@dataclass
class ScrollAsset:
    """One uploaded certificate or ID, with the outcome of its storage attempt.

    `stored` is the load-bearing field: it is False when the R2 upload failed, and the
    PDF then says so explicitly instead of quietly omitting the document. `data` is the
    bytes as received, so the archive is rendered from the upload itself rather than a
    re-download that could 404 or return something else.
    """

    key: str
    label: str
    filename: str | None = None
    content_type: str | None = None
    data: bytes | None = None
    url: str | None = None
    stored: bool = False
    error: str | None = None
    provided: bool = True


@dataclass
class ScrollPdfInput:
    artist_name: str
    artist_email: str | None = None
    artist_user_id: str | None = None
    submission_id: str | None = None
    submitted_at: datetime | None = None
    fields: dict = field(default_factory=dict)
    artist_signature_png: bytes | None = None
    owner_signature_png: bytes | None = None
    artist_signature_url: str | None = None
    owner_signature_url: str | None = None
    terms_version: str | None = None
    acceptance_metadata: dict = field(default_factory=dict)
    assets: list[ScrollAsset] = field(default_factory=list)


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "display": ParagraphStyle(
            "ScrollDisplay", parent=base["Normal"], fontName=_DISPLAY, fontSize=26, leading=27, textColor=_TEAL
        ),
        "display2": ParagraphStyle(
            "ScrollDisplay2", parent=base["Normal"], fontName=_DISPLAY, fontSize=26, leading=28, textColor=_INK
        ),
        "title": ParagraphStyle(
            "ScrollTitle",
            parent=base["Normal"],
            fontName=_DISPLAY,
            fontSize=15,
            leading=19,
            textColor=_INK,
            spaceAfter=8,
        ),
        "heading": ParagraphStyle(
            "ScrollHead",
            parent=base["Normal"],
            fontName=_MONO,
            fontSize=8.5,
            leading=11,
            textColor=_GOLD_DEEP,
            spaceBefore=6,
            spaceAfter=6,
        ),
        "label": ParagraphStyle(
            "ScrollLabel", parent=base["Normal"], fontName=_MONO, fontSize=7, leading=10, textColor=_MUTED
        ),
        "normal": ParagraphStyle(
            "ScrollNormal", parent=base["Normal"], fontName=_BODY, fontSize=9.5, leading=13, textColor=_INK
        ),
        "small": ParagraphStyle(
            "ScrollSmall", parent=base["Normal"], fontName=_BODY, fontSize=8, leading=11, textColor=_MUTED
        ),
        "warn": ParagraphStyle(
            "ScrollWarn", parent=base["Normal"], fontName=_BODY_BOLD, fontSize=9.5, leading=13, textColor=_RED
        ),
    }


def _section(title: str, styles: dict) -> list:
    """Mono uppercase section label over a hairline, the Foundry spec-card rhythm."""
    rule = Table([[""]], colWidths=[_CONTENT_WIDTH], rowHeights=[1])
    rule.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, _RULE)]))
    return [Spacer(1, 6), rule, Paragraph(_escape(title), styles["heading"])]


def _draw_page_chrome(canvas, doc) -> None:
    """Dark header band with the tiger mark, and an address/page footer, on every page."""
    canvas.saveState()
    band_h = 0.95 * inch
    top = _PAGE_HEIGHT
    canvas.setFillColor(_GROUND)
    canvas.rect(0, top - band_h, _PAGE_WIDTH, band_h, stroke=0, fill=1)
    canvas.setFillColor(_GOLD)
    canvas.rect(0, top - band_h - 2, _PAGE_WIDTH, 2, stroke=0, fill=1)

    text_x = _SIDE_MARGIN
    if _STUDIO_MARK.exists():
        try:
            mark_h = 0.62 * inch
            mark_w = mark_h
            canvas.drawImage(
                ImageReader(str(_STUDIO_MARK)),
                _SIDE_MARGIN,
                top - band_h + (band_h - mark_h) / 2,
                width=mark_w,
                height=mark_h,
                mask="auto",
            )
            text_x = _SIDE_MARGIN + mark_w + 0.16 * inch
        except Exception as exc:  # pragma: no cover - defensive, the mark is decoration
            logger.warning("scroll PDF header mark unavailable: %s", exc)

    canvas.setFillColor(_CREAM)
    canvas.setFont(_DISPLAY, 15)
    canvas.drawString(text_x, top - 0.47 * inch, _studio_name().upper(), charSpace=2.2)
    canvas.setFillColor(_GOLD)
    canvas.setFont(_MONO, 7.5)
    canvas.drawString(text_x, top - 0.66 * inch, "ARTIST SCROLL // OWNER ARCHIVE COPY", charSpace=1.4)
    canvas.drawRightString(_PAGE_WIDTH - _SIDE_MARGIN, top - 0.66 * inch, "CONFIDENTIAL // SHOP RECORD")

    canvas.setStrokeColor(_RULE)
    canvas.setLineWidth(0.6)
    canvas.line(_SIDE_MARGIN, 0.62 * inch, _PAGE_WIDTH - _SIDE_MARGIN, 0.62 * inch)
    canvas.setFillColor(_MUTED)
    canvas.setFont(_MONO, 6.5)
    canvas.drawString(_SIDE_MARGIN, 0.45 * inch, _studio_footer())
    canvas.drawRightString(_PAGE_WIDTH - _SIDE_MARGIN, 0.45 * inch, f"PAGE {doc.page}")
    canvas.restoreState()


def _escape(value: object) -> str:
    """Escape for reportlab's mini-HTML paragraph markup.

    Field values are artist-supplied free text. An unescaped '&' or '<' makes
    reportlab raise mid-build and would take the whole archive down with it.
    """
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _humanize(key: str) -> str:
    return key.replace("_", " ").replace("-", " ").strip().title()


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None or value == "":
        return "--"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "--"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items()) if value else "--"
    return str(value)


# Keys already rendered elsewhere in the document (as signatures or embedded cert pages).
# Repeating their raw URLs in the field table adds noise, not evidence.
_RENDERED_ELSEWHERE = {
    "bbp_cert_url",
    "first_aid_cert_url",
    "cpr_cert_url",
    "govt_id_url",
    "owner_sig_url",
    "artist_sig_url",
    "scroll_pdf_url",
    "certs_stored",
    "submission_id",
    "storage_prefix",
    "terms_version",
    "section_acceptances",
    "acceptance_record",
}


def normalize_embed_image(data: bytes) -> bytes | None:
    """Decode any Pillow-readable image to an upright PNG, or return None.

    exif_transpose is the point of this function: a phone photo of a certificate carries
    its rotation in EXIF, and reportlab honours pixels only. Without baking the rotation
    in, half the archived IDs would be sideways.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((MAX_EMBED_EDGE_PX, MAX_EMBED_EDGE_PX))
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.info("scroll asset is not a decodable image, will be referenced instead: %s", exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("unexpected failure normalizing scroll asset image: %s", exc)
        return None


def _fitted_image(png_bytes: bytes, max_w: float, max_h: float) -> RLImage | None:
    try:
        with Image.open(io.BytesIO(png_bytes)) as probe:
            width, height = probe.size
        if not width or not height:
            return None
        scale = min(max_w / width, max_h / height)
        return RLImage(io.BytesIO(png_bytes), width=width * scale, height=height * scale)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("failed to place scroll image into PDF: %s", exc)
        return None


_STATUS_COLORS = {"stored": _TEAL, "not provided": _MUTED, "upload failed": _RED}


def _value_paragraph(value: str, styles: dict) -> Paragraph:
    """Status words get the Foundry DO/DON'T colors; everything else is plain ink."""
    text = _escape(value)
    lowered = value.lower()
    for prefix, color in _STATUS_COLORS.items():
        if lowered.startswith(prefix):
            return Paragraph(f'<font color="{color.hexval()}"><b>{text}</b></font>', styles["normal"])
    return Paragraph(text, styles["normal"])


def _kv_table(rows: list[tuple[str, str]], styles: dict) -> Table:
    data = [[Paragraph(_escape(k), styles["label"]), _value_paragraph(v, styles)] for k, v in rows]
    label_w = 1.9 * inch
    table = Table(data, colWidths=[label_w, _CONTENT_WIDTH - label_w])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, -1), _WELL),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, _RULE, None, (2, 2)),
                ("BOX", (0, 0), (-1, -1), 0.6, _RULE),
            ]
        )
    )
    return table


def _signature_block(
    label: str,
    png: bytes | None,
    url: str | None,
    styles: dict,
    elements: list,
) -> None:
    normalized = normalize_embed_image(png) if png else None
    flowable = _fitted_image(normalized, 2.6 * inch, 0.9 * inch) if normalized else None
    if flowable is not None:
        body = flowable
    elif url:
        body = Paragraph(f"Signature image stored at: {_escape(url)}", styles["small"])
    else:
        body = Paragraph("NOT CAPTURED: no signature image was stored.", styles["warn"])
    # Signature sits on a line inside a paper well, like a signed form.
    box = Table([[body]], colWidths=[3.4 * inch], rowHeights=[1.1 * inch])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _WELL),
                ("BOX", (0, 0), (-1, -1), 0.6, _RULE),
                ("LINEBELOW", (0, 0), (-1, -1), 1.2, _INK),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    box.hAlign = "LEFT"
    elements.append(KeepTogether([*_section(label, styles), box]))
    elements.append(Spacer(1, 10))


def _asset_pages(assets: list[ScrollAsset], styles: dict, elements: list) -> None:
    for asset in assets:
        if not asset.provided:
            continue
        elements.append(PageBreak())
        elements.append(Paragraph(_escape(asset.label), styles["title"]))
        meta: list[tuple[str, str]] = []
        if asset.filename:
            meta.append(("Original filename", asset.filename))
        if asset.content_type:
            meta.append(("Content type", asset.content_type))
        meta.append(("Stored", "Yes" if asset.stored else "No"))
        private_id = bool(asset.url and private_id_key(asset.url))
        if asset.url and private_id:
            # Government IDs are private objects: the bytes are embedded below, never a link.
            meta.append(("Storage", "Private (staff open it through the app)"))
        elif asset.url:
            meta.append(("Storage URL", asset.url))
        elements.append(_kv_table(meta, styles))
        elements.append(Spacer(1, 10))

        if not asset.stored:
            reason = asset.error or "the upload did not complete"
            elements.append(
                Paragraph(
                    f"DOCUMENT NOT STORED: {_escape(reason)}. This archive does not contain this document.",
                    styles["warn"],
                )
            )
            continue

        normalized = normalize_embed_image(asset.data) if asset.data else None
        flowable = _fitted_image(normalized, _CONTENT_WIDTH, _CONTENT_HEIGHT) if normalized else None
        if flowable is not None:
            elements.append(flowable)
        else:
            elements.append(
                Paragraph(
                    "This upload is not an embeddable image (for example a PDF). "
                    + (
                        "It is stored privately and forms part of this record."
                        if private_id
                        else "It is stored separately at the storage URL above and forms part of this record."
                    ),
                    styles["normal"],
                )
            )


def build_scroll_pdf(data: ScrollPdfInput) -> bytes:
    """Render a complete Artist Scroll archive PDF and return its bytes.

    Never raises for content reasons: an undecodable image, a missing signature, or a
    failed upload each degrade to an explicit statement in the document. The archive is
    allowed to say "this is missing"; it is not allowed to imply something is present
    when it is not.
    """
    styles = _styles()
    submitted_at = data.submitted_at or datetime.now(UTC)
    elements: list = []

    # Foundry two-line display lockup: the record type in teal over its noun in ink.
    elements.append(Paragraph("ARTIST", styles["display"]))
    elements.append(Paragraph("SCROLL", styles["display2"]))
    elements.append(Spacer(1, 4))
    elements.append(
        Paragraph(f"{_escape(data.artist_name or 'Unnamed artist')} // Owner Archive Copy", styles["small"])
    )
    elements.append(Spacer(1, 10))

    local = submitted_at.astimezone(shop_timezone())
    submitted_label = (
        f"{local.strftime('%B')} {local.day}, {local.year} at {local.strftime('%I:%M %p').lstrip('0')} {local.strftime('%Z')}"
        f" ({submitted_at.astimezone(UTC).strftime('%H:%M UTC')})"
    )
    submission_rows = [
        ("Artist", data.artist_name or "--"),
        ("Artist email", data.artist_email or "--"),
        ("Artist user ID", data.artist_user_id or "--"),
        ("Submitted at", submitted_label),
    ]
    if data.submission_id:
        submission_rows.append(("Submission ID", data.submission_id))
    elements.extend(_section("Submission", styles))
    elements.append(_kv_table(submission_rows, styles))
    elements.append(Spacer(1, 10))

    elements.extend(_section("Terms Acceptance", styles))
    acceptance = data.acceptance_metadata or {}
    acceptance_rows = [
        ("Terms version", data.terms_version or str(acceptance.get("terms_version") or "Not recorded")),
        ("Accepted at", str(acceptance.get("accepted_at") or "Not recorded")),
        ("Accepted by user", str(acceptance.get("accepted_by_user_id") or "Not recorded")),
        ("Accepted by email", str(acceptance.get("accepted_by_email") or "Not recorded")),
    ]
    elements.append(_kv_table(acceptance_rows, styles))
    sections = acceptance.get("sections") if isinstance(acceptance.get("sections"), dict) else {}
    for key, value in sections.items():
        if not isinstance(value, dict):
            continue
        elements.append(
            Paragraph(
                f"{_escape(value.get('label') or _humanize(key))}: accepted by "
                f"{_escape(value.get('legal_name') or 'Not recorded')}",
                styles["small"],
            )
        )
    elements.append(Spacer(1, 10))

    # Every submitted field is rendered generically. A scroll's fields are dynamic, so an
    # allow-list would silently drop whatever the form gains next.
    field_rows = [
        (_humanize(key), _format_value(value))
        for key, value in sorted((data.fields or {}).items())
        if key not in _RENDERED_ELSEWHERE
    ]
    elements.extend(_section("Submitted Form Fields", styles))
    if field_rows:
        elements.append(_kv_table(field_rows, styles))
    else:
        elements.append(Paragraph("No form fields were submitted.", styles["warn"]))
    elements.append(Spacer(1, 10))

    # Document inventory: what the archive claims to hold, and what it does not.
    inventory: list[tuple[str, str]] = []
    for asset in data.assets:
        if not asset.provided:
            inventory.append((asset.label, "Not provided"))
        elif asset.stored:
            inventory.append((asset.label, "Stored; embedded below when the format is supported"))
        else:
            inventory.append((asset.label, f"UPLOAD FAILED: not stored ({asset.error or 'unknown error'})"))
    elements.append(
        KeepTogether(
            [*_section("Document Inventory", styles), _kv_table(inventory or [("Documents", "None provided")], styles)]
        )
    )
    elements.append(Spacer(1, 12))

    _signature_block("Artist Signature", data.artist_signature_png, data.artist_signature_url, styles, elements)
    if data.owner_signature_png or data.owner_signature_url:
        _signature_block("Owner Countersignature", data.owner_signature_png, data.owner_signature_url, styles, elements)

    _asset_pages(data.assets, styles, elements)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=_TOP_MARGIN,
        bottomMargin=_BOTTOM_MARGIN,
        leftMargin=_SIDE_MARGIN,
        rightMargin=_SIDE_MARGIN,
        title=f"Artist Scroll: {data.artist_name}",
        author=_studio_name(),
    )
    doc.build(elements, onFirstPage=_draw_page_chrome, onLaterPages=_draw_page_chrome)
    return buf.getvalue()
