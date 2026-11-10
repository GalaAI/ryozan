"""
Document endpoints — templates, form submissions, file uploads, signing, send lifecycle.

Endpoints (auth required unless noted):
  GET    /documents/templates           — list templates (optional ?category=)
  GET    /documents/templates/{id}      — get single template
  POST   /documents/templates           — create template (artist+)
  PATCH  /documents/templates/{id}      — update template (artist+)

  GET    /documents/                    — list instances (?category=, ?user_id=, ?status=)
  POST   /documents/                    — submit a filled form / create instance
  POST   /documents/{id}/sign           — sign a document
  PATCH  /documents/{id}/status         — update status (artist+ for review)
  POST   /documents/upload              — upload a file (multipart/form-data)

  POST   /documents/send               — send a doc to a recipient via magic link
  GET    /documents/public/{token}      — [NO AUTH] get form for magic link
  POST   /documents/public/{token}/complete — [NO AUTH] submit completed form
  POST   /documents/reminders/process   — [API KEY] fire pending reminders
  POST   /documents/{id}/remind         — manual resend reminder
"""

import asyncio
import base64
import contextlib
import json
import logging
import random
import re
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.waiver import _artist_can_access_waiver, _send_aftercare_email, spawn_background
from app.constants.status import DocStatus
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.core.routes import ROUTES
from app.core.security import ROLE_RANK, get_current_user, require_role
from app.models.booking import Booking
from app.models.document import DocumentInstance, DocumentTemplate
from app.models.notification import Notification
from app.models.user import User, normalize_email
from app.schemas.document import (
    DocumentInstanceCreate,
    DocumentInstanceListResponse,
    DocumentInstanceResponse,
    DocumentPublicFormResponse,
    DocumentPublicSubmit,
    DocumentSendRequest,
    DocumentSendResponse,
    DocumentStatusUpdate,
    DocumentTemplateCreate,
    DocumentTemplateListResponse,
    DocumentTemplateResponse,
    DocumentTemplateUpdate,
)
from app.services import checkin_verify
from app.services.email import (
    FROM_SHOP,
    build_document_send_html,
    build_reminder_html,
    render_db_template,
    send_email,
)
from app.services.email_foundry_waiver import render_checkin_code, render_waiver_link
from app.services.quest_hooks import check_and_complete_quest
from app.services.scroll_archive import (
    OWNER_ARCHIVE_CATEGORY,
    build_submission_prefix,
    file_owner_archive,
    notify_owners,
    queue_owner_emails,
    resolve_founders,
)
from app.services.scroll_pdf import ScrollAsset, ScrollPdfInput, build_scroll_pdf_report
from app.services.sms import send_sms
from app.services.storage import (
    PRIVATE_REFERENCE_HINTS,
    SCROLL_ID_PDF_FILENAME,
    SCROLL_NOID_PDF_FILENAME,
    canonical_id_reference,
    is_own_storage_url,
    private_id_key,
    public_scroll_prefix,
    signed_id_fields,
    signed_id_url,
)
from app.services.storage import upload_file as upload_to_blob

logger = logging.getLogger("ryozan.documents")

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_SCROLL_FILE_BYTES = 10 * 1024 * 1024
MAX_SCROLL_SIGNATURE_BYTES = 2 * 1024 * 1024
MAX_SCROLL_TOTAL_BYTES = 25 * 1024 * 1024
ARTIST_SCROLL_TERMS_VERSION = "artist-scroll-2026-08-10-v1"
REQUIRED_SCROLL_ACCEPTANCE_SECTIONS = ("application", "agreement", "bloodborne", "fire", "dhec")
# Keys of the stored scroll record that only the server writes: the references to objects it
# stored for this submission and the bookkeeping around them. A browser-supplied value for any of
# them is discarded, so an artist can never point the record (and the signed links handed back for
# it) at another object in our bucket.
SERVER_OWNED_SCROLL_KEYS = frozenset(
    {
        "bbp_cert_url",
        "first_aid_cert_url",
        "cpr_cert_url",
        "govt_id_url",
        "govt_id",
        "photo_id_url",
        "photo_id",
        "owner_sig_url",
        "artist_sig_url",
        "artist_signature",
        "signature_url",
        "scroll_pdf_url",
        "file_url",
        "certs_stored",
        "submission_id",
        "storage_prefix",
        "public_storage_prefix",
        "acceptance_record",
    }
)


def _scrub_client_storage_refs(value, *, top_level: bool = True):
    """Copy of browser-supplied scroll fields with no reference into our own storage.

    Drops the server-owned keys at the top level, and anywhere in the payload drops any string that
    resolves to an object on our R2 bucket (a bare key URL or an echoed signed link). Those values are
    never typed by an artist; keeping one would let the read endpoints sign a link to someone else's
    waiver, signature or scroll. Returns (scrubbed, dropped_count). Never logs values.
    """
    dropped = 0
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if top_level and key in SERVER_OWNED_SCROLL_KEYS:
                dropped += 1
                continue
            if isinstance(item, str) and is_own_storage_url(item):
                dropped += 1
                continue
            cleaned, n = _scrub_client_storage_refs(item, top_level=False)
            dropped += n
            out[key] = cleaned
        return out, dropped
    if isinstance(value, list):
        out_list = []
        for item in value:
            if isinstance(item, str) and is_own_storage_url(item):
                dropped += 1
                continue
            cleaned, n = _scrub_client_storage_refs(item, top_level=False)
            dropped += n
            out_list.append(cleaned)
        return out_list, dropped
    return value, 0


WAIVER_CONTACT_PREFILL_FIELDS = (
    "full_name",
    "date_of_birth",
    "phone",
    "email",
    "emergency_contact",
    "emergency_contact_address",
    "street_address",
    "social_handle",
)


def _require_owner_archive_access(user: User, category: str | None) -> None:
    """Keep owner compliance archives founder-only across generic document routes."""
    if category == OWNER_ARCHIVE_CATEGORY and user.role != "founder":
        raise HTTPException(status_code=403, detail="Owner archive access requires founder role")


# A caller below staff may only store a private reference it can prove it owns.
CALLER_REUPLOAD_MESSAGE = "Upload the file again"

# Categories only the server creates for a caller below admin: the artist scroll is created by
# submit_scroll (and the scrolls artist upload), never through the generic document routes. Artists and
# assistants are held to this too: they are peers of the artists whose submissions they could forge.
SERVER_CREATED_CATEGORIES = frozenset({"scroll", OWNER_ARCHIVE_CATEGORY})
# data_json keys only submit_scroll writes. Scroll PDF recovery never trusts them, but a caller below
# admin has no reason to send them through the generic routes, so a payload carrying one is refused.
RESERVED_SCROLL_MARKERS = frozenset(
    {
        "submission_id",
        "scroll_pdf_url",
        "acceptance_record",
        "storage_prefix",
        "public_storage_prefix",
        "certs_stored",
        "artist_user_id",
    }
)
RESERVED_MARKER_MESSAGE = "This document cannot carry server-reserved fields"
SERVER_CATEGORY_MESSAGE = "This document category is created by the server only"


def _is_admin(user: User) -> bool:
    return ROLE_RANK.get(getattr(user, "role", None) or "", -1) >= ROLE_RANK["admin"]


def _is_staff(user: User) -> bool:
    """Assistant and above. Staff already read stored records, so their writes are not checked here."""
    return ROLE_RANK.get(getattr(user, "role", None) or "", -1) >= ROLE_RANK["assistant"]


def _require_caller_category(category: str | None) -> None:
    """Below admin, the generic document routes never create a server-only category."""
    if category in SERVER_CREATED_CATEGORIES:
        raise HTTPException(status_code=403, detail=SERVER_CATEGORY_MESSAGE)


def _has_reserved_marker(value) -> bool:
    if isinstance(value, dict):
        return any(k in RESERVED_SCROLL_MARKERS or _has_reserved_marker(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_reserved_marker(v) for v in value)
    return False


def _require_no_reserved_markers(raw: str | None) -> None:
    """Refuse caller data_json carrying a key only submit_scroll writes, at any depth. Never logs values."""
    if not raw:
        return
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        # Kept as text and never parsed for recovery, but the marker must not appear in it either.
        if any(f'"{marker}"' in raw for marker in RESERVED_SCROLL_MARKERS):
            raise HTTPException(status_code=422, detail=RESERVED_MARKER_MESSAGE) from None
        return
    if _has_reserved_marker(data):
        raise HTTPException(status_code=422, detail=RESERVED_MARKER_MESSAGE)


def _is_uuid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return True


def _is_callers_own_private_key(key: str, user_id: uuid.UUID) -> bool:
    """
    True when a private key sits under a folder the server writes only for this user.

    signatures/<user>/ (saved signature), scrolls/<user>/ (Gala document upload, and the scroll submission
    folder scrolls/<user>/<submission>/) and scrolls/<category>/<user>/ (category upload marked as an ID).
    """
    parts = key.split("/")
    uid = str(user_id)
    if parts[0] == "signatures":
        return len(parts) > 2 and parts[1] == uid
    if parts[0] == "scrolls":
        if len(parts) > 2 and parts[1] == uid:
            return True
        return len(parts) > 3 and parts[2] == uid and not _is_uuid(parts[1])
    return False


def _caller_private_reference(value: str, user: User) -> str:
    """
    The stored form of a reference a non-staff caller submits, or 422.

    Anything that is not a private key on our bucket passes through. A private key is accepted only when it
    lies under the caller's own upload folders; otherwise the caller could plant another person's ID, waiver
    PDF or signature and read it back as a freshly signed link. This install has no server-verified upload
    link check, so a waivers/ key (including a pending ID upload) is always refused here. Never logs the value.
    """
    key = private_id_key(value)
    if not key:
        return value
    if _is_callers_own_private_key(key, user.id):
        return canonical_id_reference(value) or value
    raise HTTPException(status_code=422, detail=CALLER_REUPLOAD_MESSAGE)


def _caller_private_references(value, user: User):
    """Copy of a JSON-like value with every string checked by _caller_private_reference."""
    if isinstance(value, str):
        return _caller_private_reference(value, user)
    if isinstance(value, dict):
        return {k: _caller_private_references(v, user) for k, v in value.items()}
    if isinstance(value, list):
        return [_caller_private_references(v, user) for v in value]
    return value


def _caller_data_json(raw: str | None, user: User) -> str | None:
    """
    data_json as a non-staff caller may store it. Unparseable JSON is kept as is: it is never signed on read
    (_signed_data_json returns it untouched). The raw text is kept unless a reference had to be rewritten.
    """
    if not raw:
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    checked = _caller_private_references(data, user)
    return raw if checked == data else json.dumps(checked)


def _require_document_instance_access(instance: DocumentInstance, user: User) -> None:
    """Apply the same owner and artist assignment boundary to generic document actions."""
    _require_owner_archive_access(user, instance.category)
    if user.role in {"admin", "founder"}:
        return
    if instance.user_id == user.id:
        return
    if instance.category == "waiver" and user.role == "artist" and _artist_can_access_waiver(instance, user):
        return
    raise HTTPException(status_code=403, detail="Document is not assigned to this account")


def _build_waiver_contact_prefill(
    prior_data: dict,
    current_data: dict,
    *,
    recipient_name: str | None,
    recipient_email: str | None,
) -> dict[str, str]:
    """Reuse identity and contact facts without carrying consent into a new waiver.

    Appointment details, health answers, photo IDs, acknowledgements, initials,
    and signatures are intentionally excluded so every visit gets a fresh review.
    """
    prefill: dict[str, str] = {}
    for source in (prior_data, current_data):
        for field in WAIVER_CONTACT_PREFILL_FIELDS:
            value = source.get(field)
            if isinstance(value, str) and value.strip():
                prefill[field] = value.strip()

    if recipient_name and recipient_name.strip():
        prefill["full_name"] = recipient_name.strip()
    if recipient_email and recipient_email.strip():
        prefill["email"] = recipient_email.strip()
    artist_name = current_data.get("artist_name")
    if isinstance(artist_name, str) and artist_name.strip():
        prefill["artist_name"] = artist_name.strip()
    return prefill


def _prior_prefill_allowed(instance: DocumentInstance, current_data: dict) -> bool:
    """Prior contact facts may prefill only a staff-sent link or a check-in verified by code."""
    return bool(instance.sent_by) or current_data.get("checkin_verified") is True


# Server-set waiver facts the public form never sends back; kept when the client submits.
WAIVER_SERVER_KEYS = ("booking_id", "booking_reference", "checkin_verified", "checkin_source")


def _generate_ref_code() -> str:
    """Generate a WVR-XXXXXX reference code (6 uppercase alphanumeric chars)."""
    chars = string.ascii_uppercase + string.digits
    return "WVR-" + "".join(random.choices(chars, k=6))


def _require_waiver_placement(data: dict) -> str:
    placement = str(data.get("tattoo_placement") or data.get("placement") or "").strip()
    if not placement:
        # Older live waiver templates used one combined description field labeled
        # "design, placement, size" and did not render a dedicated placement input.
        # Keep those active links submittable while normalized forms add the body map.
        placement = str(data.get("tattoo_description") or "").strip()
    if not placement:
        raise HTTPException(status_code=422, detail="Tattoo placement is required")
    return placement


def _require_waiver_photo_id(data: dict) -> str:
    photo_id = str(data.get("photo_id_url") or data.get("photo_id") or "").strip()
    if not photo_id:
        raise HTTPException(status_code=422, detail="Photo ID is required")
    # The browser may echo back the signed preview link; store the stable reference instead.
    return canonical_id_reference(photo_id) or photo_id


def _require_waiver_intake_answers(data: dict) -> None:
    """Every waiver carries the piece, the agreed price, a sober answer and the three consents."""
    if not str(data.get("tattoo_description") or "").strip():
        raise HTTPException(status_code=422, detail="Tell us what you're getting tattooed")
    price = str(data.get("quoted_price") or "").strip().lstrip("$").replace(",", "")
    try:
        if float(price) <= 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter the price you agreed on") from exc
    data["quoted_price"] = price
    if str(data.get("intoxicated") or "").strip().lower() != "no":
        raise HTTPException(status_code=422, detail="We can't tattoo anyone under the influence today")
    for key in ("health_and_eligibility", "risks_and_consent", "accept_terms"):
        if data.get(key) is not True:
            raise HTTPException(status_code=422, detail="Confirm all three statements before you sign")


def _require_waiver_signature(signature: str | None) -> str:
    signature_data = str(signature or "").strip()
    if not signature_data:
        raise HTTPException(status_code=422, detail="Signature is required")
    return signature_data


def _normalize_waiver_fields(fields: list[dict]) -> list[dict]:
    normalized = []
    has_placement = False
    for field in fields:
        if not isinstance(field, dict):
            normalized.append(field)
            continue

        field_name = field.get("name")
        field_type = field.get("type")
        if field_type == "signature" or field_name in {"client_signature", "signature", "artist_signature"}:
            continue

        if field_name in {"tattoo_placement", "placement"}:
            has_placement = True
            normalized.append(
                {
                    **field,
                    "name": "tattoo_placement",
                    "label": field.get("label") or "Tattoo Placement",
                    "type": "body_map",
                    "required": True,
                }
            )
        elif field_name == "tattoo_description":
            normalized.append(
                {
                    **field,
                    "label": "Tattoo Description",
                    "type": "text",
                    "required": True,
                    "placeholder": "Describe the tattoo in one sentence.",
                    "maxLength": field.get("maxLength") or 180,
                }
            )
        elif field_name in {"photo_id_url", "photo_id"}:
            normalized.append({**field, "required": True})
        else:
            normalized.append(field)

    if not has_placement:
        placement_field = {
            "name": "tattoo_placement",
            "label": "Tattoo Placement",
            "type": "body_map",
            "required": True,
        }
        insert_at = next(
            (
                index + 1
                for index, field in enumerate(normalized)
                if isinstance(field, dict) and field.get("name") == "tattoo_description"
            ),
            len(normalized),
        )
        normalized.insert(insert_at, placement_field)
    return normalized


# Upload directory — sibling to the API project root
UPLOAD_DIR = Path(__file__).resolve().parents[4] / "uploads" / "documents"


# Government ID images are private in R2; responses carry short-lived signed links only.
ID_VIEW_SECONDS = 900


def _signed_data_json(raw: str | None) -> str | None:
    """
    The stored data_json with any private government ID reference swapped for a signed link.

    Documents with no private ID reference come back byte for byte unchanged. Stored data is
    never mutated; this only shapes the response.
    """
    if not raw or not any(hint in raw for hint in PRIVATE_REFERENCE_HINTS):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(data, dict):
        return raw
    view = signed_id_fields(data, ID_VIEW_SECONDS)
    return raw if view == data else json.dumps(view)


def _instance_to_response(inst: DocumentInstance, template_name: str | None, user_name: str | None) -> dict:
    """Convert a DocumentInstance row + joined names into a response dict."""
    return {
        "id": inst.id,
        "template_id": inst.template_id,
        "template_name": template_name,
        "user_id": inst.user_id,
        "user_name": user_name,
        "category": inst.category,
        "title": inst.title,
        "status": inst.status,
        "data_json": _signed_data_json(inst.data_json),
        "signed_at": inst.signed_at,
        # Private ID objects, and scroll PDFs that embed the ID, reach clients as short-lived signed links only.
        "file_url": signed_id_url(inst.file_url, ID_VIEW_SECONDS),
        "notes": inst.notes,
        # Send / lifecycle fields
        "send_token": inst.send_token,
        "recipient_email": inst.recipient_email,
        "recipient_name": inst.recipient_name,
        "sent_at": inst.sent_at,
        "sent_by": inst.sent_by,
        "viewed_at": inst.viewed_at,
        "reminder_at": inst.reminder_at,
        "reminder_sent": inst.reminder_sent,
        "expires_at": inst.expires_at,
        "created_at": inst.created_at,
        "updated_at": inst.updated_at,
    }


# ── Templates ──────────────────────────────────────────


@router.get("/templates", response_model=DocumentTemplateListResponse)
async def list_templates(
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """List all active document templates, optionally filtered by category."""
    query = select(DocumentTemplate).where(DocumentTemplate.is_active)
    if category:
        query = query.where(DocumentTemplate.category == category)
    query = query.order_by(DocumentTemplate.name)

    result = await db.execute(query)
    templates = result.scalars().all()

    return {"templates": templates, "total": len(templates)}


@router.get("/templates/{template_id}", response_model=DocumentTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Get a single document template by ID."""
    result = await db.execute(select(DocumentTemplate).where(DocumentTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("/templates", response_model=DocumentTemplateResponse)
async def create_template(
    body: DocumentTemplateCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("admin")),
):
    """Admin/founder: create a new global document template."""
    template = DocumentTemplate(
        **body.model_dump(),
        created_by=current_user.id,
    )
    db.add(template)
    await db.flush()
    await db.refresh(template)
    return template


@router.patch("/templates/{template_id}", response_model=DocumentTemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    body: DocumentTemplateUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("admin")),
):
    """Admin/founder: update a global document template."""
    result = await db.execute(select(DocumentTemplate).where(DocumentTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(template, key, value)
    template.updated_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(template)
    return template


# ── Instances ──────────────────────────────────────────


@router.get("/", response_model=DocumentInstanceListResponse)
async def list_documents(
    category: str | None = Query(None),
    user_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """
    List document instances.

    Artists+ see all documents. Collectors/assistants see only their own.
    """
    from app.core.security import ROLE_HIERARCHY

    _require_owner_archive_access(current_user, category)

    query = (
        select(
            DocumentInstance,
            DocumentTemplate.name.label("template_name"),
            User.name.label("user_name"),
        )
        .outerjoin(DocumentTemplate, DocumentInstance.template_id == DocumentTemplate.id)
        .outerjoin(User, DocumentInstance.user_id == User.id)
    )

    # Role-based filtering: founders/admins see all; artists see their own
    # documents plus waivers explicitly assigned to them; lower roles see own.
    user_role_index = ROLE_HIERARCHY.index(current_user.role)
    artist_index = ROLE_HIERARCHY.index("artist")
    if current_user.role != "founder":
        query = query.where(DocumentInstance.category != OWNER_ARCHIVE_CATEGORY)

    if user_role_index < artist_index:
        query = query.where(DocumentInstance.user_id == current_user.id)
    elif current_user.role == "artist":
        query = query.where(
            or_(
                DocumentInstance.user_id == current_user.id,
                and_(
                    DocumentInstance.category == "waiver",
                    or_(
                        DocumentInstance.artist_id_reviewed == current_user.id,
                        DocumentInstance.data_json.contains(str(current_user.id)),
                    ),
                ),
            )
        )
    elif user_id:
        query = query.where(DocumentInstance.user_id == user_id)

    if category:
        query = query.where(DocumentInstance.category == category)
    if status:
        query = query.where(DocumentInstance.status == status)
    else:
        # Exclude archived docs from active list (they live in Vault)
        query = query.where(DocumentInstance.status != DocStatus.ARCHIVED)

    query = query.order_by(DocumentInstance.created_at.desc())
    result = await db.execute(query)
    rows = result.all()

    documents = [_instance_to_response(inst, tpl_name, u_name) for inst, tpl_name, u_name in rows]
    return {"documents": documents, "total": len(documents)}


@router.post("/", response_model=DocumentInstanceResponse)
async def submit_document(
    body: DocumentInstanceCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Submit a filled form or create a document instance."""
    _require_owner_archive_access(current_user, body.category)
    if not _is_admin(current_user):
        # No server-only category and no server-reserved scroll marker below admin (checked before any write).
        _require_caller_category(body.category)
        _require_no_reserved_markers(body.data_json)
    if _is_staff(current_user):
        data_json = body.data_json
        # A client echoing a signed private link back stores the stable reference, never an expiring URL.
        file_url = canonical_id_reference(body.file_url)
    else:
        # Below staff, a private reference is stored only with proof the caller owns it (checked before any write).
        data_json = _caller_data_json(body.data_json, current_user)
        file_url = _caller_private_reference(body.file_url, current_user) if body.file_url else body.file_url
    instance = DocumentInstance(
        template_id=body.template_id,
        user_id=current_user.id,
        category=body.category,
        title=body.title,
        status="submitted",
        data_json=data_json,
        file_url=file_url,
        notes=body.notes,
    )
    db.add(instance)
    await db.flush()
    await db.refresh(instance)

    # Fetch joined names for the response
    template_name = None
    if instance.template_id:
        tpl_result = await db.execute(select(DocumentTemplate.name).where(DocumentTemplate.id == instance.template_id))
        template_name = tpl_result.scalar_one_or_none()

    return _instance_to_response(instance, template_name, current_user.name)


@router.post("/{document_id}/sign", response_model=DocumentInstanceResponse)
async def sign_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Sign a document (marks it as signed with current timestamp)."""
    result = await db.execute(select(DocumentInstance).where(DocumentInstance.id == document_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Document not found")
    _require_document_instance_access(instance, current_user)

    # Only the owner can sign their own document
    if instance.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only sign your own documents")

    instance.signed_at = datetime.now(UTC)
    instance.status = DocStatus.SIGNED
    instance.updated_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(instance)

    # Fetch joined names
    template_name = None
    if instance.template_id:
        tpl_result = await db.execute(select(DocumentTemplate.name).where(DocumentTemplate.id == instance.template_id))
        template_name = tpl_result.scalar_one_or_none()

    return _instance_to_response(instance, template_name, current_user.name)


@router.patch("/{document_id}/status", response_model=DocumentInstanceResponse)
async def update_document_status(
    document_id: uuid.UUID,
    body: DocumentStatusUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: update a document's status (approve, reject, etc.)."""
    result = await db.execute(select(DocumentInstance).where(DocumentInstance.id == document_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Document not found")
    _require_document_instance_access(instance, current_user)

    instance.status = body.status
    if body.notes is not None:
        instance.notes = body.notes
    instance.updated_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(instance)

    # Fetch joined names
    template_name = None
    user_name = current_user.name
    if instance.template_id:
        tpl_result = await db.execute(select(DocumentTemplate.name).where(DocumentTemplate.id == instance.template_id))
        template_name = tpl_result.scalar_one_or_none()
    if instance.user_id != current_user.id:
        user_result = await db.execute(select(User.name).where(User.id == instance.user_id))
        user_name = user_result.scalar_one_or_none()

    return _instance_to_response(instance, template_name, user_name)


@router.post("/upload", response_model=DocumentInstanceResponse)
async def upload_document(
    file: UploadFile = File(...),
    category: str = Form(...),
    title: str = Form(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Upload a file and create a document instance for it."""
    _require_owner_archive_access(current_user, category)
    if not _is_admin(current_user):
        _require_caller_category(category)

    from app.services.storage import upload_file as upload_to_drive

    ext = Path(file.filename).suffix if file.filename else ""
    stored_name = f"{uuid.uuid4().hex}{ext}"
    content = await file.read()

    # Upload to Google Drive (persistent storage)
    drive_url = await upload_to_drive(
        file_bytes=content,
        filename=stored_name,
        mime_type=file.content_type or "application/octet-stream",
        folder_path=f"documents/{current_user.id}",
    )
    if not drive_url:
        raise HTTPException(500, "Failed to upload document — storage unavailable")

    relative_url = drive_url

    instance = DocumentInstance(
        user_id=current_user.id,
        category=category,
        title=title,
        status="submitted",
        file_url=relative_url,
    )
    db.add(instance)
    await db.flush()
    await db.refresh(instance)

    return _instance_to_response(instance, None, current_user.name)


# ── Delete ────────────────────────────────────────────


@router.delete("/{instance_id}")
async def delete_document(
    instance_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: Delete a document instance."""
    result = await db.execute(select(DocumentInstance).where(DocumentInstance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Document not found")
    _require_document_instance_access(instance, current_user)
    if instance.category == "waiver" and current_user.role not in {"founder", "admin"}:
        raise HTTPException(status_code=403, detail="Waiver deletion requires founder or admin role")

    await db.delete(instance)
    await db.flush()
    return {"status": "deleted", "id": instance_id}


# ── Artist Scroll ─────────────────────────────────────
# Dedicated endpoints for the 6-section compliance scroll.
# Uses DocumentInstance with category='scroll'.
# BBP cert image and both signature PNGs are stored in Cloudflare R2.


@router.post("/scroll/submit")
async def submit_scroll(
    request: Request,
    # Signature images as base64 strings
    artist_signature: str = Form(...),
    # All form field values serialized as JSON
    fields_json: str = Form("{}"),
    # Required compliance document uploads
    bbp_cert: UploadFile | None = File(None),
    first_aid_cert: UploadFile | None = File(None),
    cpr_cert: UploadFile | None = File(None),
    govt_id: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """
    Submit a completed Artist Scroll. Stores:
    - The artist signature as a Blob image
    - BBP cert file (if provided) as Blob upload
    - All form field values in data_json
    - Creates/updates a DocumentInstance (category='scroll')

    Upserts: if a scroll already exists for this user it is replaced.

    The artist's DocumentInstance is still upserted, but the underlying R2 objects are
    not: every submission writes under its own unique prefix (see
    scroll_archive.build_submission_prefix) so a re-submission can never overwrite the
    objects an earlier owner archive copy points at.

    A durable copy of the submission is filed to every founder as a generated PDF in the
    dedicated `scroll_owner-archive` category, with an in-app notification and an email
    carrying the PDF. Those three are best-effort and never fail the artist's submission.
    """
    import base64

    submitted_at = datetime.now(UTC)
    submission_id = uuid.uuid4().hex
    # One unique prefix per submission. Previously every object used a fixed key
    # (artist-sig-{user_id}.png), so re-submitting overwrote the signature and
    # certificate objects in place and silently rewrote what earlier archive copies
    # pointed at.
    storage_prefix = build_submission_prefix(str(current_user.id))
    # The government ID, the signature images and the generated PDF (which prints the signatures whether
    # or not it embeds the ID) stay under the private "scrolls/" prefix; only the certificates go under
    # the public "scrolls-public/" twin, since everything under "scrolls/" is private by key.
    public_prefix = public_scroll_prefix(storage_prefix)

    def _decode_signature(raw_b64: str, label: str) -> bytes:
        try:
            raw = raw_b64.split(",", 1)[-1]
            decoded = base64.b64decode(raw, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail=f"{label} signature is not valid base64") from None
        if len(decoded) > MAX_SCROLL_SIGNATURE_BYTES:
            raise HTTPException(status_code=413, detail=f"{label} signature exceeds the 2 MB limit")
        return decoded

    artist_sig_bytes = _decode_signature(artist_signature, "Artist")
    owner_sig_bytes: bytes | None = None
    total_upload_bytes = len(artist_sig_bytes)

    upload_specs = (
        (bbp_cert, "bbp_cert"),
        (first_aid_cert, "first_aid_cert"),
        (cpr_cert, "cpr_cert"),
        (govt_id, "govt_id"),
    )
    preloaded_uploads: dict[str, bytes] = {}
    for upload, key in upload_specs:
        if not upload or not upload.filename:
            continue
        content = await upload.read(MAX_SCROLL_FILE_BYTES + 1)
        if len(content) > MAX_SCROLL_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{upload.filename} exceeds the 10 MB file limit")
        total_upload_bytes += len(content)
        if total_upload_bytes > MAX_SCROLL_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="Artist Scroll uploads exceed the 25 MB total limit")
        preloaded_uploads[key] = content

    try:
        fields = json.loads(fields_json)
    except Exception:
        raise HTTPException(status_code=422, detail="Artist Scroll fields_json must be valid JSON") from None
    if not isinstance(fields, dict):
        raise HTTPException(status_code=422, detail="Artist Scroll fields_json must be an object")
    # Never trust a browser-supplied storage reference: the ID, certificates and signatures are
    # only ever the files uploaded in this request, stored by the server under its own keys below.
    fields, dropped_refs = _scrub_client_storage_refs(fields)
    if dropped_refs:
        logger.warning(
            "Scroll %s: discarded %d client-supplied storage reference(s) for user %s",
            submission_id,
            dropped_refs,
            current_user.id,
        )

    terms_version = fields.get("terms_version")
    section_acceptances = fields.get("section_acceptances")
    if terms_version != ARTIST_SCROLL_TERMS_VERSION or not isinstance(section_acceptances, dict):
        raise HTTPException(status_code=422, detail="Artist Scroll acceptance record is missing or outdated")
    for section in REQUIRED_SCROLL_ACCEPTANCE_SECTIONS:
        acceptance = section_acceptances.get(section)
        if not isinstance(acceptance, dict) or acceptance.get("accepted") is not True:
            raise HTTPException(status_code=422, detail=f"Artist Scroll section '{section}' was not accepted")
        if len(str(acceptance.get("legal_name") or "").strip()) < 2:
            raise HTTPException(status_code=422, detail=f"Artist Scroll section '{section}' needs a legal name")

    acceptance_record = {
        "terms_version": ARTIST_SCROLL_TERMS_VERSION,
        "accepted_at": submitted_at.isoformat(),
        "accepted_by_user_id": str(current_user.id),
        "accepted_by_email": current_user.email,
        "user_agent": request.headers.get("user-agent", "")[:300],
        "sections": section_acceptances,
    }
    fields["acceptance_record"] = acceptance_record

    # Upsert: delete existing scroll for this user. Only touches category='scroll',
    # so founders' `scroll_owner-archive` rows are never affected.
    existing = await db.execute(
        select(DocumentInstance).where(
            DocumentInstance.user_id == current_user.id,
            DocumentInstance.category == "scroll",
        )
    )
    for old in existing.scalars().all():
        await db.delete(old)
    await db.flush()

    async def _upload_signature(sig_bytes: bytes, name: str, label: str) -> tuple[bytes, str | None]:
        """Store one prevalidated signature PNG."""
        try:
            url = await upload_to_blob(
                file_bytes=sig_bytes,
                filename=name,
                mime_type="image/png",
                folder_path=storage_prefix,
            )
        except Exception:
            logger.error(
                "Scroll %s: %s signature upload raised for user %s",
                submission_id,
                label,
                current_user.id,
                exc_info=True,
            )
            return sig_bytes, None
        if not url:
            logger.error(
                "Scroll %s: %s signature upload returned no URL for user %s (R2 unavailable?)",
                submission_id,
                label,
                current_user.id,
            )
        return sig_bytes, url

    artist_sig_url: str | None = None
    artist_sig_bytes, artist_sig_url = await _upload_signature(artist_sig_bytes, "artist-sig.png", "artist")

    owner_sig_url: str | None = None

    async def _upload_cert(upload: UploadFile | None, key: str, label: str, stem: str) -> ScrollAsset:
        """Store one optional certificate/ID upload, recording the outcome.

        Failures are still non-fatal, matching the previous behaviour, but they are now
        recorded on the asset (and logged at error, not warning) so the archive states
        plainly that the document is missing instead of quietly omitting it.
        """
        if not upload or not upload.filename:
            return ScrollAsset(key=key, label=label, provided=False)
        asset = ScrollAsset(
            key=key,
            label=label,
            filename=upload.filename,
            content_type=upload.content_type,
            provided=True,
        )
        try:
            content = preloaded_uploads[key]
            asset.data = content
            ext = Path(upload.filename).suffix or ".jpg"
            url = await upload_to_blob(
                file_bytes=content,
                filename=f"{stem}{ext}",
                mime_type=upload.content_type or "image/jpeg",
                folder_path=storage_prefix if key == "govt_id" else public_prefix,
            )
            if url:
                asset.url = url
                asset.stored = True
            else:
                asset.error = "storage returned no URL"
                logger.error(
                    "Scroll %s: %s upload for user %s returned no URL (R2 unavailable?); "
                    "the owner archive will record it as not stored.",
                    submission_id,
                    label,
                    current_user.id,
                )
        except Exception as exc:
            asset.error = str(exc)[:200]
            logger.error(
                "Scroll %s: %s upload failed for user %s",
                submission_id,
                label,
                current_user.id,
                exc_info=True,
            )
        return asset

    assets = [
        await _upload_cert(bbp_cert, "bbp_cert", "Bloodborne Pathogens Certificate", "bbp-cert"),
        await _upload_cert(first_aid_cert, "first_aid_cert", "First Aid Certificate", "first-aid-cert"),
        await _upload_cert(cpr_cert, "cpr_cert", "CPR Certificate", "cpr-cert"),
        await _upload_cert(govt_id, "govt_id", "Government ID", "govt-id"),
    ]
    by_key = {a.key: a for a in assets}
    bbp_cert_url = by_key["bbp_cert"].url
    first_aid_cert_url = by_key["first_aid_cert"].url
    cpr_cert_url = by_key["cpr_cert"].url
    govt_id_url = by_key["govt_id"].url

    # Merge stored data.
    submitted_fields = dict(fields)

    if bbp_cert_url:
        fields["bbp_cert_url"] = bbp_cert_url
    if first_aid_cert_url:
        fields["first_aid_cert_url"] = first_aid_cert_url
    if cpr_cert_url:
        fields["cpr_cert_url"] = cpr_cert_url
    if govt_id_url:
        fields["govt_id_url"] = govt_id_url
    if owner_sig_url:
        fields["owner_sig_url"] = owner_sig_url
    if artist_sig_url:
        fields["artist_sig_url"] = artist_sig_url

    # Which documents the record actually holds. Written to both copies so neither can
    # imply a certificate is on file when its upload failed.
    fields["submission_id"] = submission_id
    fields["storage_prefix"] = storage_prefix
    fields["certs_stored"] = {
        a.key: {"provided": a.provided, "stored": a.stored, "url": a.url, "error": a.error} for a in assets
    }

    # Generate the full scroll PDF. This is the record's real "file" -- file_url used to
    # point at bbp_cert_url, i.e. the bloodborne-pathogens certificate rather than the
    # scroll itself.
    scroll_pdf_url: str | None = None
    pdf_bytes: bytes | None = None
    pdf_filename: str | None = None
    try:
        report = build_scroll_pdf_report(
            ScrollPdfInput(
                artist_name=current_user.name or "Unknown Artist",
                artist_email=current_user.email,
                artist_user_id=str(current_user.id),
                submission_id=submission_id,
                submitted_at=submitted_at,
                fields=submitted_fields,
                artist_signature_png=artist_sig_bytes,
                owner_signature_png=owner_sig_bytes,
                artist_signature_url=artist_sig_url,
                owner_signature_url=owner_sig_url,
                terms_version=ARTIST_SCROLL_TERMS_VERSION,
                acceptance_metadata=acceptance_record,
                assets=assets,
            )
        )
        pdf_bytes = report.pdf
        # The name records whether the PDF embeds the government ID image ("artist-scroll.id.pdf", else
        # "artist-scroll.noid.pdf"). Privacy comes from the prefix: every generated PDF prints the artist
        # signature (and any owner countersignature), so it is always private and reaches clients only as
        # a short-lived signed link.
        pdf_filename = SCROLL_ID_PDF_FILENAME if report.embeds_government_id else SCROLL_NOID_PDF_FILENAME
        scroll_pdf_url = await upload_to_blob(
            file_bytes=pdf_bytes,
            filename=pdf_filename,
            mime_type="application/pdf",
            folder_path=storage_prefix,
        )
        if not scroll_pdf_url:
            logger.error(
                "Scroll %s: PDF generated but upload returned no URL for user %s (R2 unavailable?)",
                submission_id,
                current_user.id,
            )
    except Exception:
        logger.error(
            "Scroll %s: failed to build or store the scroll PDF for user %s; "
            "the submission still succeeds but the owner archive has no PDF.",
            submission_id,
            current_user.id,
            exc_info=True,
        )

    if scroll_pdf_url:
        fields["scroll_pdf_url"] = scroll_pdf_url

    instance = DocumentInstance(
        # Set here so a held PDF can name exactly the records this submission created.
        id=uuid.uuid4(),
        user_id=current_user.id,
        category="scroll",
        title=f"Artist Scroll — {current_user.name}",
        status="signed",
        data_json=json.dumps(fields),
        file_url=scroll_pdf_url,
        artist_signature=artist_sig_url or artist_signature[:500] if artist_signature else None,
        artist_signed_at=submitted_at,
        signed_at=submitted_at,
    )
    db.add(instance)

    # ── Owner (founder) archive copy + notification + email ──────────────
    # All best-effort: a failure here must not cost the artist their submission, their
    # coin, or their XP. Logged at error level with the submission id so a missing
    # archive is diagnosable rather than a silent 200.
    founders: list[User] = []
    archive_rows: list[DocumentInstance] = []
    owner_emails_queued = 0
    try:
        founders = await resolve_founders(db)
        archive_rows = file_owner_archive(
            db,
            artist=current_user,
            founders=founders,
            fields=fields,
            assets=assets,
            submission_id=submission_id,
            storage_prefix=storage_prefix,
            submitted_at=submitted_at,
            pdf_url=scroll_pdf_url,
        )
        notify_owners(db, current_user, founders, submitted_at, scroll_pdf_url)
        owner_emails_queued = queue_owner_emails(
            db,
            current_user,
            founders,
            submitted_at,
            assets,
            bool(scroll_pdf_url),
            submission_id,
            # Storing the PDF failed: keep its bytes in the DB (never attached) so it can be retried.
            unstored_pdf=(pdf_bytes, pdf_filename) if pdf_bytes and pdf_filename and not scroll_pdf_url else None,
            # The retry records the stored URL on exactly these server-created records.
            document_ids=[instance.id, *(row.id for row in archive_rows)],
        )
    except Exception:
        logger.error(
            "Scroll %s: failed to file the owner archive copy for artist %s",
            submission_id,
            current_user.id,
            exc_info=True,
        )

    # Award RYO + XP for scroll completion
    scroll_coin_reward = 500
    scroll_xp = 150
    current_user.bttcoin_balance = (current_user.bttcoin_balance or 0) + scroll_coin_reward
    current_user.quest_points = (current_user.quest_points or 0) + scroll_xp

    # Mark all scroll_request notifications as read for this user
    from sqlalchemy import update as sa_update

    await db.execute(
        sa_update(Notification)
        .where(
            Notification.user_id == current_user.id,
            Notification.notification_type.in_(("scroll_request", "scroll_remind")),
        )
        .values(is_read=True)
    )

    await db.commit()
    await db.refresh(instance)

    return {
        "id": str(instance.id),
        "status": "signed",
        "signed_at": instance.signed_at.isoformat(),
        "artist_sig_url": artist_sig_url,
        "owner_sig_url": owner_sig_url,
        "bbp_cert_url": bbp_cert_url,
        # Every scroll PDF prints the artist signature: a short-lived signed link, never the public URL.
        "scroll_pdf_url": signed_id_url(scroll_pdf_url, ID_VIEW_SECONDS),
        "submission_id": submission_id,
        "owner_copies_filed": len(archive_rows),
        "owner_emails_queued": owner_emails_queued,
        "owner_emails_sent": 0,
        "bttcoin_awarded": scroll_coin_reward,
        "xp_awarded": scroll_xp,
    }


def _scroll_fields_view(data: dict) -> dict:
    """The stored scroll fields with every government ID reference swapped for a signed link."""
    return signed_id_fields(data, ID_VIEW_SECONDS)


@router.get("/scroll/my")
async def get_my_scroll(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Get the calling user's own completed scroll (if any)."""
    result = await db.execute(
        select(DocumentInstance)
        .where(
            DocumentInstance.user_id == current_user.id,
            DocumentInstance.category == "scroll",
        )
        .order_by(DocumentInstance.created_at.desc())
        .limit(1)
    )
    inst = result.scalar_one_or_none()
    if not inst:
        return {"scroll": None}

    data = {}
    if inst.data_json:
        with contextlib.suppress(Exception):
            data = json.loads(inst.data_json)

    return {
        "scroll": {
            "id": str(inst.id),
            "status": inst.status,
            "signed_at": inst.signed_at.isoformat() if inst.signed_at else None,
            "artist_signature": signed_id_url(inst.artist_signature, ID_VIEW_SECONDS),
            "bbp_cert_url": signed_id_url(data.get("bbp_cert_url"), ID_VIEW_SECONDS),
            "first_aid_cert_url": signed_id_url(data.get("first_aid_cert_url"), ID_VIEW_SECONDS),
            "cpr_cert_url": signed_id_url(data.get("cpr_cert_url"), ID_VIEW_SECONDS),
            "govt_id_url": signed_id_url(data.get("govt_id_url"), ID_VIEW_SECONDS),
            "owner_sig_url": signed_id_url(data.get("owner_sig_url"), ID_VIEW_SECONDS),
            "tax_opt_in": data.get("tax_opt_in"),
            "fields": _scroll_fields_view(data),
            "created_at": inst.created_at.isoformat(),
        }
    }


@router.get("/scroll/user/{user_id}")
async def get_user_scroll(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    caller: User = Depends(require_role("artist")),
):
    """Fetch the caller's scroll, or any scroll for founder/admin review."""
    if caller.role not in {"founder", "admin"} and caller.id != user_id:
        raise HTTPException(status_code=403, detail="Artist Scroll is not assigned to this account")
    result = await db.execute(
        select(DocumentInstance, User.name.label("user_name"))
        .outerjoin(User, DocumentInstance.user_id == User.id)
        .where(
            DocumentInstance.user_id == user_id,
            DocumentInstance.category == "scroll",
        )
        .order_by(DocumentInstance.created_at.desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return {"scroll": None}

    inst, user_name = row
    data = {}
    if inst.data_json:
        with contextlib.suppress(Exception):
            data = json.loads(inst.data_json)

    return {
        "scroll": {
            "id": str(inst.id),
            "user_name": user_name,
            "user_id": str(user_id),
            "status": inst.status,
            "signed_at": inst.signed_at.isoformat() if inst.signed_at else None,
            "artist_signature": signed_id_url(inst.artist_signature, ID_VIEW_SECONDS),
            "bbp_cert_url": signed_id_url(data.get("bbp_cert_url"), ID_VIEW_SECONDS),
            "first_aid_cert_url": signed_id_url(data.get("first_aid_cert_url"), ID_VIEW_SECONDS),
            "cpr_cert_url": signed_id_url(data.get("cpr_cert_url"), ID_VIEW_SECONDS),
            "govt_id_url": signed_id_url(data.get("govt_id_url"), ID_VIEW_SECONDS),
            "owner_sig_url": signed_id_url(data.get("owner_sig_url"), ID_VIEW_SECONDS),
            "tax_opt_in": data.get("tax_opt_in"),
            "fields": _scroll_fields_view(data),
            "created_at": inst.created_at.isoformat(),
        }
    }


# ── Send / Lifecycle ──────────────────────────────────


@router.post("/send", response_model=DocumentSendResponse)
async def send_document(
    body: DocumentSendRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """
    Artist+: Send a document to a recipient via magic link email.

    Creates a DocumentInstance with status='sent', generates a unique token,
    and emails the recipient a link to complete the form.
    """
    # Fetch the template
    result = await db.execute(select(DocumentTemplate).where(DocumentTemplate.id == body.template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    settings = get_settings()
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)

    instance = DocumentInstance(
        template_id=template.id,
        category=template.category,
        title=template.name,
        status=DocStatus.SENT,
        send_token=token,
        recipient_email=body.recipient_email,
        recipient_name=body.recipient_name,
        # Populate guest fields so waiver list/search works for sent waivers
        guest_name=body.recipient_name,
        guest_email=body.recipient_email,
        sent_at=now,
        sent_by=current_user.id,
        reminder_at=now + timedelta(hours=body.reminder_hours),
        expires_at=now + timedelta(hours=body.expires_hours),
    )
    db.add(instance)
    await db.flush()
    await db.refresh(instance)

    public_url = f"{settings.FRONTEND_URL}{ROUTES.FORM.format(token=token)}"

    # Send the email (non-blocking, never raises). Waivers use the Email Foundry waiver link into /checkin.
    if template.category == "waiver":
        public_url = _waiver_form_url(settings, token)
        rendered = render_waiver_link(
            client_name=body.recipient_name,
            artist_name=current_user.name or "",
            document_title="Tattoo waiver",
            form_url=public_url,
        )
        await send_email(
            to=body.recipient_email,
            subject=rendered.subject,
            html_body=rendered.html_body,
            tags=[rendered.slug],
            from_alias=FROM_SHOP,
        )
    else:
        html = await render_db_template(
            "document_send",
            {
                "client_name": body.recipient_name,
                "doc_title": template.name,
                "form_url": public_url,
                "artist_name": current_user.name or settings.STUDIO_NAME,
            },
        ) or build_document_send_html(
            recipient_name=body.recipient_name,
            document_title=template.name,
            public_url=public_url,
            sender_name=current_user.name or settings.STUDIO_NAME,
        )
        await send_email(
            to=body.recipient_email,
            subject=f"Document to sign: {template.name}",
            html_body=html,
            from_alias=FROM_SHOP,
        )

    logger.info(
        "Document sent: %s → %s (token=%s)",
        template.name,
        body.recipient_email,
        token[:8],
    )

    return {
        "instance_id": instance.id,
        "send_token": token,
        "public_url": public_url,
    }


@router.post("/checkin")
async def public_checkin(
    body: dict,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    [NO AUTH] Walk-in check-in: creates a waiver instance and returns
    the magic link token. No email sent — client fills it out on-site.
    """
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    artist_slug = (body.get("artist_slug") or "").strip()

    if not name or not email:
        raise HTTPException(status_code=422, detail="Name and email are required")

    # Find the waiver template
    result = await db.execute(
        select(DocumentTemplate).where(
            DocumentTemplate.name == "Tattoo Consent Waiver",
            DocumentTemplate.category == "waiver",
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=500, detail="Waiver template not found")

    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)

    instance = DocumentInstance(
        template_id=template.id,
        category="waiver",
        title=template.name,
        status=DocStatus.SENT,
        send_token=token,
        recipient_email=email,
        recipient_name=name,
        guest_name=name,
        guest_email=email,
        sent_at=now,
        expires_at=now + timedelta(hours=72),
    )

    # Pre-fill artist slug in data_json if provided
    if artist_slug:
        instance.data_json = json.dumps({"artist_name": artist_slug})

    db.add(instance)
    await db.flush()

    settings = get_settings()
    return {
        "token": token,
        "form_url": f"{settings.FRONTEND_URL}{ROUTES.FORM.format(token=token)}",
    }


# ── Check-in lookup: find a returning client or a booking, prove the contact, then prefill ──

CHECKIN_BOOKING_STATUSES = ("pending", "quoted", "confirmed")
CHECKIN_VISIT_STATUSES = (DocStatus.PENDING_REVIEW, DocStatus.SIGNED, DocStatus.APPROVED, "completed")


async def _checkin_matches(db: AsyncSession, contact: checkin_verify.Contact) -> dict:
    """Everything on file for one contact: portal account, the next booking, and prior waivers."""
    users: list[User] = []
    if contact.kind == "email":
        users = list((await db.execute(select(User).where(func.lower(User.email) == contact.value))).scalars())
        booking_filter = or_(
            func.lower(Booking.guest_email) == contact.value,
            Booking.client_id.in_([u.id for u in users]) if users else false(),
        )
    else:
        digits = func.regexp_replace(func.coalesce(User.phone, ""), r"\D", "", "g")
        users = list((await db.execute(select(User).where(digits.like(f"%{contact.value}")))).scalars())
        booking_digits = func.regexp_replace(func.coalesce(Booking.guest_phone, ""), r"\D", "", "g")
        booking_filter = or_(
            booking_digits.like(f"%{contact.value}"),
            Booking.client_id.in_([u.id for u in users]) if users else false(),
        )

    today = datetime.now(UTC).date()
    window_start = today - timedelta(days=1)
    booking = (
        await db.execute(
            select(Booking)
            .where(booking_filter, Booking.status.in_(CHECKIN_BOOKING_STATUSES))
            .where(
                or_(
                    Booking.scheduled_date >= window_start,
                    and_(Booking.scheduled_date.is_(None), Booking.preferred_date >= window_start),
                    and_(
                        Booking.scheduled_date.is_(None),
                        Booking.preferred_date.is_(None),
                        Booking.created_at >= datetime.now(UTC) - timedelta(days=60),
                    ),
                )
            )
            .order_by(Booking.scheduled_date.asc().nulls_last(), Booking.preferred_date.asc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()

    emails = {contact.value} if contact.kind == "email" else set()
    emails |= {u.email.strip().lower() for u in users if u.email}
    if booking and booking.guest_email:
        emails.add(booking.guest_email.strip().lower())
    if contact.kind == "phone":
        # A walk-in has no account and may have no booking: the only place their number lives is the
        # waiver they signed. The check-in code goes to this number, so holding it is what is proven.
        waiver_email = await _waiver_email_for_phone(db, contact.value)
        if waiver_email:
            emails.add(waiver_email)
    visits, last_visit = 0, None
    if emails:
        row = (
            await db.execute(
                select(func.count(DocumentInstance.id), func.max(DocumentInstance.signed_at))
                .where(DocumentInstance.category == "waiver")
                .where(func.lower(DocumentInstance.guest_email).in_(emails))
                .where(DocumentInstance.status.in_(CHECKIN_VISIT_STATUSES))
            )
        ).one()
        visits, last_visit = int(row[0] or 0), row[1]

    member = next((u for u in users if u.role == "collector"), None)
    prior = await _latest_prior_waiver(db, emails)
    return {
        "users": users,
        "member": member,
        "booking": booking,
        "emails": emails,
        "visits": visits,
        "last_visit": last_visit,
        "prior": prior,
    }


PHONE_WAIVER_SCAN_LIMIT = 100


def _waiver_phone_matches(data: object, digits: str) -> bool:
    """True when a stored waiver's own phone answer is this ten digit number."""
    if not isinstance(data, dict) or len(digits) != 10:
        return False
    return checkin_verify.phone_digits(str(data.get("phone") or "")) == digits


async def _waiver_email_for_phone(db: AsyncSession, digits: str) -> str | None:
    """The email on the newest completed waiver signed with this phone number, if any.

    Waiver answers are stored as JSON text, so the query narrows on the last four digits and the exact
    match is decided here on the parsed answer. Only the newest match is used: one number, one identity.
    Never called for an email contact, where the typed number would be unproven.
    """
    if len(digits) != 10:
        return None
    rows = (
        await db.execute(
            select(DocumentInstance.guest_email, DocumentInstance.data_json)
            .where(DocumentInstance.category == "waiver")
            .where(DocumentInstance.status.in_(CHECKIN_VISIT_STATUSES))
            .where(DocumentInstance.guest_email.is_not(None))
            .where(DocumentInstance.data_json.like(f"%{digits[-4:]}%"))
            .order_by(DocumentInstance.signed_at.desc().nulls_last(), DocumentInstance.created_at.desc())
            .limit(PHONE_WAIVER_SCAN_LIMIT)
        )
    ).all()
    for guest_email, data_json in rows:
        try:
            data = json.loads(data_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        email = (guest_email or "").strip().lower()
        if email and _waiver_phone_matches(data, digits):
            return email
    return None


async def _latest_prior_waiver(db: AsyncSession, emails: set[str], exclude_id: uuid.UUID | None = None) -> dict | None:
    """The newest completed waiver for these emails, as its stored data (with its id and signed date)."""
    if not emails:
        return None
    query = (
        select(DocumentInstance)
        .where(DocumentInstance.category == "waiver")
        .where(func.lower(DocumentInstance.guest_email).in_(emails))
        .where(DocumentInstance.status.in_(CHECKIN_VISIT_STATUSES))
        .where(DocumentInstance.data_json.is_not(None))
        .order_by(DocumentInstance.signed_at.desc().nulls_last(), DocumentInstance.created_at.desc())
        .limit(1)
    )
    if exclude_id:
        query = query.where(DocumentInstance.id != exclude_id)
    instance = (await db.execute(query)).scalar_one_or_none()
    if not instance:
        return None
    try:
        data = json.loads(instance.data_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return {"id": str(instance.id), "signed_at": instance.signed_at, "data": data}


# Health answers a returning client may confirm as unchanged. Sobriety is never carried over: it is asked
# fresh on every visit (SC Reg. 60-111 801.C).
WAIVER_HEALTH_KEYS = (
    "pregnant",
    "blood_thinners",
    "blood_disorder",
    "skin_condition",
    "allergies",
    "medical_conditions",
)


def _stored_signature_url(data: dict) -> str | None:
    url = str(data.get("signature_url") or "").strip()
    return url if url.startswith("https://") else None


def _stored_photo_id_url(data: dict) -> str | None:
    url = str(data.get("photo_id_url") or data.get("photo_id") or "").strip()
    return url if url.startswith("https://") else None


def _first_name(*names: str | None) -> str:
    for name in names:
        if name and name.strip():
            return name.strip().split()[0]
    return ""


@router.post("/checkin/lookup")
@limiter.limit("6/minute")
async def checkin_lookup(request: Request, body: dict, db: AsyncSession = Depends(get_db, scope="function")):
    """
    [NO AUTH] Step one of check-in. Says only whether the contact is on file. When it is, a six digit code
    goes to that email or phone and nothing on file is returned until the code comes back to /checkin/verify.
    """
    contact = checkin_verify.normalize_contact(str(body.get("contact") or ""))
    if not contact:
        raise HTTPException(status_code=422, detail="Enter an email or a 10 digit mobile number")

    matches = await _checkin_matches(db, contact)
    if not (matches["users"] or matches["booking"] or matches["visits"]):
        return {"found": False}

    settings = get_settings()
    challenge, code = checkin_verify.issue_challenge(settings.SECRET_KEY, contact)
    message = f"{code} is your Ryozan check-in code. It expires in 10 minutes."
    sent_to, channel = checkin_verify.mask_contact(contact), contact.kind

    delivered = False
    if contact.kind == "phone":
        try:
            sms = await send_sms(db=db, to=contact.value, body=message, is_transactional=True, skip_opt_in_check=True)
            delivered = sms.status not in {"failed", "undelivered", "skipped", "blocked"}
        except Exception:  # noqa: BLE001 — a failed SMS falls back to email below, never a 500
            logger.exception("Check-in code SMS failed")
        fallback = next(iter(sorted(matches["emails"])), None)
        if not delivered and fallback:
            channel, sent_to = "email", checkin_verify.mask_contact(checkin_verify.Contact("email", fallback))
            delivered = await _send_checkin_code_email(fallback, code)
    else:
        delivered = await _send_checkin_code_email(contact.value, code)

    if not delivered:
        raise HTTPException(
            status_code=503, detail="We couldn't send your code. Check in as a walk-in or ask the front desk."
        )
    return {"found": True, "challenge": challenge, "channel": channel, "sent_to": sent_to}


async def _send_checkin_code_email(to: str, code: str) -> bool:
    rendered = render_checkin_code(code=code)
    return await send_email(
        to=to, subject=rendered.subject, html_body=rendered.html_body, tags=[rendered.slug], from_alias=FROM_SHOP
    )


def _waiver_form_url(settings, token: str) -> str:
    """Waivers open in the check-in flow; the same token carries over."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/checkin?t={token}"


async def _send_document_reminder(settings, instance: DocumentInstance, hours_remaining: int) -> bool:
    """Reminder for an unsigned document. Waivers use the Email Foundry waiver reminder."""
    if instance.category == "waiver":
        rendered = render_waiver_link(
            client_name=instance.recipient_name or "",
            artist_name="",
            document_title="Tattoo waiver",
            form_url=_waiver_form_url(settings, instance.send_token),
            hours_remaining=hours_remaining,
        )
        return await send_email(
            to=instance.recipient_email,
            subject=rendered.subject,
            html_body=rendered.html_body,
            tags=[rendered.slug],
            from_alias=FROM_SHOP,
        )
    public_url = f"{settings.FRONTEND_URL}{ROUTES.FORM.format(token=instance.send_token)}"
    html = await render_db_template(
        "document_reminder",
        {
            "client_name": instance.recipient_name or "there",
            "doc_title": instance.title,
            "form_url": public_url,
            "artist_name": "",
        },
    ) or build_reminder_html(
        recipient_name=instance.recipient_name or "there",
        document_title=instance.title,
        public_url=public_url,
        hours_remaining=hours_remaining,
    )
    return await send_email(
        to=instance.recipient_email,
        subject=f"Reminder: {instance.title} still needs your signature",
        html_body=html,
        from_alias=FROM_SHOP,
    )


@router.post("/checkin/verify")
@limiter.limit("10/minute")
async def checkin_verify_code(request: Request, body: dict, db: AsyncSession = Depends(get_db, scope="function")):
    """
    [NO AUTH] Step two of check-in. A correct code opens a waiver linked to the booking on file and returns
    the facts that prefill the form: first name, the booking, visit count, and portal membership.
    """
    settings = get_settings()
    try:
        contact = checkin_verify.verify_challenge(
            settings.SECRET_KEY, str(body.get("challenge") or ""), str(body.get("code") or "")
        )
    except checkin_verify.ChallengeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    matches = await _checkin_matches(db, contact)
    booking: Booking | None = matches["booking"]
    member: User | None = matches["member"]
    account = member or (matches["users"][0] if matches["users"] else None)

    artist: User | None = await db.get(User, booking.artist_id) if booking else None
    # A returning walk-in has no booking and no account: their last waiver is the only record of who they are.
    prior_answers: dict = matches["prior"]["data"] if matches["prior"] else {}
    prior_name = prior_answers.get("full_name") if isinstance(prior_answers.get("full_name"), str) else None
    prior_phone = prior_answers.get("phone") if isinstance(prior_answers.get("phone"), str) else None
    name = (booking.guest_name if booking else None) or (account.name if account else None) or prior_name or ""
    email = (
        (contact.value if contact.kind == "email" else None)
        or (booking.guest_email if booking else None)
        or (account.email if account else None)
        or next(iter(sorted(matches["emails"])), "")
    )
    phone = (
        (booking.guest_phone if booking else None)
        or (account.phone if account else None)
        or (contact.value if contact.kind == "phone" else None)
        or prior_phone
        or ""
    )

    template = (
        await db.execute(
            select(DocumentTemplate).where(
                DocumentTemplate.name == "Tattoo Consent Waiver", DocumentTemplate.category == "waiver"
            )
        )
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=500, detail="Waiver template not found")

    data: dict = {"checkin_verified": True, "checkin_source": f"lookup:{contact.kind}"}
    for key, value in (("full_name", name), ("email", email), ("phone", phone)):
        if value:
            data[key] = value
    if booking:
        data["booking_id"] = str(booking.id)
        if booking.reference_code:
            data["booking_reference"] = booking.reference_code
        if artist and (artist.slug or artist.name):
            data["artist_name"] = artist.slug or artist.name

    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    db.add(
        DocumentInstance(
            template_id=template.id,
            category="waiver",
            title=template.name,
            status=DocStatus.SENT,
            send_token=token,
            recipient_email=email or None,
            recipient_name=name or None,
            guest_name=name or None,
            guest_email=email or None,
            sent_at=now,
            expires_at=now + timedelta(hours=72),
            data_json=json.dumps(data),
        )
    )
    await db.flush()

    booking_view = None
    if booking:
        booking_view = {
            "reference": booking.reference_code,
            "artist_name": artist.name if artist else None,
            "artist_slug": artist.slug if artist else None,
            "description": booking.description,
            "placement": booking.placement,
            "size": booking.size_estimate,
            "date": (booking.scheduled_date or booking.preferred_date).isoformat()
            if (booking.scheduled_date or booking.preferred_date)
            else None,
            "time": booking.scheduled_time or booking.preferred_time,
            "status": booking.status,
        }
    # What a returning client already has on file. Shown only after the code proved the contact; the
    # signature and ID are copied server side at submit, never trusted from the browser.
    on_file = None
    prior = matches["prior"]
    if prior:
        prior_data = prior["data"]
        on_file = {
            "signed_at": prior["signed_at"].isoformat() if prior["signed_at"] else None,
            "photo_id_url": signed_id_url(_stored_photo_id_url(prior_data), ID_VIEW_SECONDS),
            "signature_url": signed_id_url(_stored_signature_url(prior_data), ID_VIEW_SECONDS),
            "health": {key: prior_data[key] for key in WAIVER_HEALTH_KEYS if prior_data.get(key) not in (None, "")},
        }
    return {
        "token": token,
        "first_name": _first_name(name, account.name if account else None),
        "booking": booking_view,
        "visits": matches["visits"],
        "last_visit": matches["last_visit"].isoformat() if matches["last_visit"] else None,
        "member": {"tier": member.membership_tier} if member else None,
        "on_file": on_file,
    }


@router.get("/public/{token}", response_model=DocumentPublicFormResponse)
async def get_public_form(
    token: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    [NO AUTH] Get the form data for a magic link token.

    Also marks the document as 'viewed' on first access.
    """
    result = await db.execute(select(DocumentInstance).where(DocumentInstance.send_token == token))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Form not found or link is invalid")

    now = datetime.now(UTC)
    expired = instance.expires_at is not None and instance.expires_at < now

    # If already signed/approved/rejected, treat as completed
    if instance.status in (DocStatus.SIGNED, DocStatus.APPROVED, DocStatus.REJECTED):
        expired = True

    # Mark as viewed on first access
    if not instance.viewed_at and not expired:
        instance.viewed_at = now
        instance.updated_at = now
        await db.flush()

    # Fetch template for form fields
    fields = []
    description = None
    requires_signature = False
    if instance.template_id:
        tpl_result = await db.execute(select(DocumentTemplate).where(DocumentTemplate.id == instance.template_id))
        template = tpl_result.scalar_one_or_none()
        if template:
            try:
                raw = json.loads(template.fields_json) if template.fields_json else []
                # fields_json may be {"fields": [...]} or a bare list
                fields = raw.get("fields", raw) if isinstance(raw, dict) else raw
            except json.JSONDecodeError:
                fields = []
            if instance.category == "waiver":
                fields = _normalize_waiver_fields(fields)
            description = template.description
            requires_signature = template.requires_signature
    if instance.category == "waiver":
        requires_signature = True

    current_data: dict = {}
    if instance.data_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(instance.data_json)
            if isinstance(parsed, dict):
                current_data = parsed

    initial_values: dict[str, str] = {}
    if instance.category == "waiver":
        prior_data: dict = {}
        normalized_email = (instance.guest_email or instance.recipient_email or "").strip().lower()
        # A prior visit's contact facts are only offered when this link was sent by staff to that inbox, or the
        # client proved they hold the contact through the check-in code. A walk-in who typed an email into the
        # public check-in has proven nothing, so their form starts from what they typed.
        if normalized_email and _prior_prefill_allowed(instance, current_data):
            prior_result = await db.execute(
                select(DocumentInstance)
                .where(DocumentInstance.id != instance.id)
                .where(DocumentInstance.category == "waiver")
                .where(func.lower(DocumentInstance.guest_email) == normalized_email)
                .where(DocumentInstance.data_json.is_not(None))
                .where(
                    DocumentInstance.status.in_(
                        (DocStatus.PENDING_REVIEW, DocStatus.SIGNED, DocStatus.APPROVED, "completed")
                    )
                )
                .order_by(DocumentInstance.created_at.desc())
                .limit(1)
            )
            prior = prior_result.scalar_one_or_none()
            if prior and prior.data_json:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    parsed = json.loads(prior.data_json)
                    if isinstance(parsed, dict):
                        prior_data = parsed

        initial_values = _build_waiver_contact_prefill(
            prior_data,
            current_data,
            recipient_name=instance.recipient_name or instance.guest_name,
            recipient_email=instance.recipient_email or instance.guest_email,
        )

    return {
        "title": instance.title,
        "description": description,
        "fields": fields,
        "requires_signature": requires_signature,
        "recipient_name": instance.recipient_name or "",
        "expired": expired,
        "category": instance.category or "",
        "initial_values": initial_values,
        "prefill_notice": (
            "Contact details were filled from the latest matching Ryozan record. Review them before signing."
            if initial_values
            else None
        ),
    }


@router.post("/public/{token}/complete")
async def complete_public_form(
    token: str,
    body: DocumentPublicSubmit,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    [NO AUTH] Submit a completed form from a magic link.

    Saves form data, optional signature, and updates status to 'signed'.
    """
    result = await db.execute(select(DocumentInstance).where(DocumentInstance.send_token == token))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Form not found or link is invalid")

    now = datetime.now(UTC)

    # Check expiry
    if instance.expires_at and instance.expires_at < now:
        raise HTTPException(status_code=410, detail="This form has expired")

    # Check if already completed
    if instance.status in (DocStatus.SIGNED, DocStatus.APPROVED, DocStatus.REJECTED):
        raise HTTPException(status_code=409, detail="This form has already been completed")

    # Build data payload with form data
    data = body.data.copy()

    # For waiver documents, generate a reference code and ensure guest fields are set
    is_waiver = instance.category == "waiver"
    if is_waiver and instance.data_json:
        # Keep what the server set at check-in (the linked booking, the verified flag); the client never
        # owns these, so a submitted value is ignored in favour of the stored one.
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            stored = json.loads(instance.data_json)
            if isinstance(stored, dict):
                for key in WAIVER_SERVER_KEYS:
                    data.pop(key, None)
                    if key in stored:
                        data[key] = stored[key]

    # A returning client can adopt the ID and signature from their last waiver instead of redoing them. The
    # browser only asks; the files are copied here from the stored record, and only after a verified check-in.
    reuse_signature = bool(data.pop("reuse_signature", False))
    reuse_photo_id = bool(data.pop("reuse_photo_id", False))
    adopted_signature_url: str | None = None
    if is_waiver and (reuse_signature or reuse_photo_id):
        if data.get("checkin_verified") is not True:
            raise HTTPException(status_code=422, detail="Saved details need a verified check-in")
        email = (instance.guest_email or instance.recipient_email or "").strip().lower()
        prior = await _latest_prior_waiver(db, {email} if email else set(), exclude_id=instance.id)
        prior_data = prior["data"] if prior else {}
        if reuse_photo_id:
            photo_url = _stored_photo_id_url(prior_data)
            if not photo_url or not prior:
                raise HTTPException(status_code=422, detail="No ID on file. Upload your ID.")
            data["photo_id_url"] = photo_url
            data["photo_id_reused_from"] = prior["id"]
        if reuse_signature:
            adopted_signature_url = _stored_signature_url(prior_data)
            if not adopted_signature_url or not prior:
                raise HTTPException(status_code=422, detail="No saved signature. Sign to finish.")
            data["signature_url"] = adopted_signature_url
            data["signature_adopted_from"] = prior["id"]
            data["signature_adopted_at"] = now.isoformat()
    ref_code = None
    if is_waiver:
        data["tattoo_placement"] = _require_waiver_placement(data)
        data["placement"] = data["tattoo_placement"]
        ref_code = _generate_ref_code()
        data["reference_code"] = ref_code
        # Populate guest fields from form data if not already set
        if not instance.guest_name:
            instance.guest_name = data.get("full_name") or instance.recipient_name or "Unknown"
        if not instance.guest_email:
            instance.guest_email = data.get("email") or instance.recipient_email
        if not instance.guest_social and data.get("social_handle"):
            instance.guest_social = data.get("social_handle")

    # Store signature under consistent key (matches walk-in path)
    signature_raw = None
    if body.signature_data:
        signature_raw = body.signature_data
        data["signature_data"] = body.signature_data
    # Also check for client_signature from template-driven forms
    if not signature_raw and data.get("client_signature"):
        signature_raw = data["client_signature"]

    if is_waiver:
        data["photo_id_url"] = _require_waiver_photo_id(data)
        _require_waiver_intake_answers(data)
        if adopted_signature_url and not signature_raw:
            # Adopted signature: already stored as an image, nothing to upload.
            data.pop("signature_data", None)
        else:
            signature_raw = _require_waiver_signature(signature_raw)
            data["signature_data"] = signature_raw

    instance.data_json = json.dumps(data)
    instance.status = DocStatus.PENDING_REVIEW if is_waiver else DocStatus.SIGNED
    instance.signed_at = now
    instance.updated_at = now

    # Clear reminder since it's now signed
    instance.reminder_sent = True  # prevent future reminders

    await db.flush()

    # ── Background: upload signature to Blob storage ──
    if is_waiver and signature_raw and ref_code:
        asyncio.create_task(
            _upload_magic_link_files(
                instance_id=instance.id,
                ref_code=ref_code,
                client_name=instance.guest_name or "unknown",
                signature_data=signature_raw,
            )
        )

    # ── Notify the assigned artist ──
    artist_display = ""
    if is_waiver:
        artist_slug = data.get("artist_name")
        if artist_slug:
            # Try slug match first, fall back to name match
            artist_result = await db.execute(select(User).where(User.slug == artist_slug))
            artist = artist_result.scalar_one_or_none()
            if not artist:
                artist_result = await db.execute(select(User).where(User.name == artist_slug))
                artist = artist_result.scalar_one_or_none()
            if artist:
                artist_display = artist.name or ""
                guest = instance.guest_name or instance.recipient_name or "A client"
                db.add(
                    Notification(
                        user_id=artist.id,
                        title="Waiver Ready for Review",
                        body=f"{guest} completed their consent waiver. Review and countersign.",
                        notification_type="info",
                        action_url=ROUTES.WAIVERS,
                    )
                )
                await db.flush()

    # ── Quest: a signed-in client's "waiver-signed" quest, same as the legacy /waiver/submit path ──
    if is_waiver:
        waiver_email = normalize_email(instance.guest_email or data.get("email") or "")
        if waiver_email:
            submitter_id = (
                (await db.execute(select(User.id).where(func.lower(User.email) == waiver_email))).scalars().first()
            )
            if submitter_id:
                try:
                    async with db.begin_nested():
                        await check_and_complete_quest(db, submitter_id, "waiver-signed")
                except Exception:  # noqa: BLE001 - a quest hiccup must never fail a filed waiver
                    logger.exception("waiver-signed quest failed for %s", ref_code)

    # ── Background: aftercare email for waiver completions ──
    if is_waiver:
        client_email = instance.guest_email or data.get("email") or ""
        if client_email:
            spawn_background(
                _send_aftercare_email(
                    to=client_email,
                    client_name=data.get("full_name") or instance.guest_name or "there",
                    artist_name=artist_display or "your artist",
                    tattoo_description=data.get("tattoo_description") or "",
                    tattoo_placement=data.get("tattoo_placement") or "",
                    reference_code=ref_code or "",
                )
            )

    logger.info("Public form completed: token=%s ref=%s", token[:8], ref_code or "n/a")

    response = {"status": "ok", "message": "Form submitted successfully"}
    if ref_code:
        response["reference_code"] = ref_code
        response["instance_id"] = str(instance.id)
    return response


@router.post("/reminders/process")
async def process_reminders(
    db: AsyncSession = Depends(get_db, scope="function"),
    x_api_key: str = Header(alias="X-Api-Key"),
):
    """
    [API KEY] Process pending reminders and expire old documents.

    Called by n8n every 15 minutes. Requires X-Api-Key header.
    """
    settings = get_settings()
    if x_api_key != settings.DOC_REMINDER_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    now = datetime.now(UTC)
    reminders_sent = 0
    expired_count = 0

    # 1. Send pending reminders
    reminder_query = select(DocumentInstance).where(
        and_(
            DocumentInstance.status == DocStatus.SENT,
            DocumentInstance.reminder_at <= now,
            DocumentInstance.reminder_sent.is_(False),
            DocumentInstance.expires_at > now,
        )
    )
    result = await db.execute(reminder_query)
    pending = result.scalars().all()

    for inst in pending:
        if inst.recipient_email and inst.send_token:
            hours_remaining = max(0, int((inst.expires_at - now).total_seconds() / 3600)) if inst.expires_at else 0
            await _send_document_reminder(settings, inst, hours_remaining)
            reminders_sent += 1

        inst.reminder_sent = True
        inst.updated_at = now

    # 2. Expire old documents
    expire_query = select(DocumentInstance).where(
        and_(
            DocumentInstance.status == DocStatus.SENT,
            DocumentInstance.expires_at <= now,
        )
    )
    result = await db.execute(expire_query)
    expired_docs = result.scalars().all()

    for inst in expired_docs:
        inst.status = DocStatus.EXPIRED
        inst.updated_at = now
        expired_count += 1

    await db.flush()

    logger.info("Reminders processed: %d sent, %d expired", reminders_sent, expired_count)

    return {
        "reminders_sent": reminders_sent,
        "expired": expired_count,
    }


@router.post("/{document_id}/remind", response_model=DocumentInstanceResponse)
async def manual_remind(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: Manually resend a reminder email for a sent document."""
    result = await db.execute(select(DocumentInstance).where(DocumentInstance.id == document_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Document not found")
    _require_document_instance_access(instance, current_user)

    if instance.status != DocStatus.SENT:
        raise HTTPException(status_code=400, detail="Can only remind for documents with status 'sent'")

    if not instance.recipient_email or not instance.send_token:
        raise HTTPException(status_code=400, detail="Document has no recipient email or send token")

    settings = get_settings()
    now = datetime.now(UTC)
    hours_remaining = max(0, int((instance.expires_at - now).total_seconds() / 3600)) if instance.expires_at else 48
    await _send_document_reminder(settings, instance, hours_remaining)

    instance.updated_at = now
    await db.flush()
    await db.refresh(instance)

    # Fetch joined names
    template_name = None
    if instance.template_id:
        tpl_result = await db.execute(select(DocumentTemplate.name).where(DocumentTemplate.id == instance.template_id))
        template_name = tpl_result.scalar_one_or_none()

    return _instance_to_response(instance, template_name, current_user.name)


# ── Background task: upload signature for magic-link waivers ──


def _safe_name(name: str) -> str:
    """Sanitize a name for use in file paths."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:40]


async def _upload_magic_link_files(
    instance_id: uuid.UUID,
    ref_code: str,
    client_name: str,
    signature_data: str,
) -> None:
    """
    Background task: upload signature PNG to Cloudflare R2.
    Updates data_json with the URL and removes the raw base64.
    """
    try:
        now = datetime.now(UTC)
        month = now.strftime("%Y-%m")
        safe = _safe_name(client_name)
        folder_path = f"waivers/{month}/{safe}_{ref_code}"

        # Decode base64 signature
        sig_raw = signature_data
        if "," in sig_raw:
            sig_raw = sig_raw.split(",", 1)[1]
        sig_bytes = base64.b64decode(sig_raw)

        sig_url = await upload_to_blob(
            file_bytes=sig_bytes,
            filename="signature.png",
            mime_type="image/png",
            folder_path=folder_path,
        )

        if sig_url:
            from app.core.database import async_session

            async with async_session() as db:
                result = await db.execute(select(DocumentInstance).where(DocumentInstance.id == instance_id))
                inst = result.scalar_one_or_none()
                if inst and inst.data_json:
                    data = json.loads(inst.data_json)
                    data["signature_url"] = sig_url
                    # Remove raw base64 to save DB space
                    data.pop("signature_data", None)
                    data.pop("client_signature", None)
                    data.pop("_signature", None)
                    inst.data_json = json.dumps(data)
                    inst.file_url = sig_url
                    await db.commit()
                    logger.info("Magic-link waiver files uploaded: %s", ref_code)

    except Exception as exc:
        logger.error("Magic-link file upload failed for %s: %s", ref_code, exc)
