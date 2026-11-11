"""
Waiver endpoints — public submit + photo ID upload, authenticated retrieval.

Public (no auth):
  POST /waiver/submit            — submit signed consent waiver
  POST /waiver/upload-id         — upload photo ID to Google Drive

Authenticated (any user):
  GET  /waiver/mine               — list waivers submitted by the current user

Authenticated (artist+):
  GET  /waiver/list               — paginated waiver list with filters
  GET  /waiver/{id}               — full waiver detail
  PATCH /waiver/{id}/status       — approve / flag / reject
  POST /waiver/{id}/countersign   — artist counter-signature
"""

import asyncio
import base64
import json
import logging
import random
import re
import string
import uuid as uuid_mod
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.status import DocStatus
from app.constants.timezones import shop_timezone
from app.core.config import get_settings
from app.core.database import async_session, get_db
from app.core.routes import ROUTES
from app.core.security import ROLE_RANK, get_current_user, require_role
from app.models.document import DocumentInstance, DocumentTemplate
from app.models.notification import Notification
from app.models.user import User, normalize_email
from app.schemas.waiver import (
    CountersignRequest,
    WaiverDetailResponse,
    WaiverListItem,
    WaiverListResponse,
    WaiverResponse,
    WaiverStatusUpdate,
    WaiverSubmit,
)
from app.services.email import FROM_SHOP, send_email
from app.services.email_foundry_waiver import render_aftercare_instructions
from app.services.quest_hooks import check_and_complete_quest
from app.services.scroll_pdf import normalize_embed_image
from app.services.storage import (
    ID_REFERENCE_KEYS,
    UnauthorizedReferenceError,
    authorize_client_references,
    client_reference_key,
    is_own_storage_url,
    private_id_key,
    signed_id_url,
    uploaded_id_reference,
)
from app.services.storage import upload_file as upload_to_drive

logger = logging.getLogger("ryozan.waiver")

router = APIRouter(prefix="/waiver", tags=["waiver"])

# Max photo ID file size: 10 MB
_MAX_ID_SIZE = 10 * 1024 * 1024
_ALLOWED_ID_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"}
# Government ID images are private in R2. Clients only ever see short-lived signed links:
# long enough to load the preview right after upload, or a staff review session.
_ID_PREVIEW_SECONDS = 3600
_ID_VIEW_SECONDS = 900
UPLOAD_ID_AGAIN = "Upload the ID again"
UPLOAD_FILE_AGAIN = "Upload the file again"
SIGN_AGAIN = "Sign again or use your saved signature"


def require_uploaded_id(reference: str | None) -> str:
    """
    The stored reference for an ID a public caller just uploaded, or 422.

    Public write paths accept a private ID only as the unexpired signed link /waiver/upload-id issued for
    that exact key. A bare URL, an expired, tampered or foreign-signed link, or any other key is refused and
    never stored, so nobody can file someone else's ID under their own contact and have it re-signed later.
    """
    stored = uploaded_id_reference(reference)
    if not stored:
        raise HTTPException(status_code=422, detail=UPLOAD_ID_AGAIN)
    return stored


def authorize_client_data(value, *, caller=None, server_allowed=()):
    """
    Client data with every reference to our bucket proven, or 422 and nothing stored.

    See storage.authorize_client_references. A refused ID field says "Upload the ID again", anything else
    "Upload the file again".
    """
    try:
        return authorize_client_references(value, caller=caller, server_allowed=server_allowed)
    except UnauthorizedReferenceError as exc:
        detail = UPLOAD_ID_AGAIN if exc.path and exc.path[0] in ID_REFERENCE_KEYS else UPLOAD_FILE_AGAIN
        raise HTTPException(status_code=422, detail=detail) from None


def authorize_countersignature(signature: str, caller: User) -> str:
    """
    An artist countersignature: an inline image, the caller's own saved signature, or a stored object the
    caller proves is theirs (their signatures/ folder, or an upload link this server just signed).

    Export embeds a stored countersignature in the PDF, so any other stored object, or a URL anywhere else,
    is refused with 422.
    """
    text = (signature or "").strip()
    if text.lower().startswith("data:image/"):
        return signature
    saved = (getattr(caller, "saved_signature_url", None) or "").strip()
    if saved and text == saved:
        return signature
    try:
        authorized = authorize_client_references(text, caller=caller, server_allowed=(saved,) if saved else ())
    except UnauthorizedReferenceError:
        raise HTTPException(status_code=422, detail=SIGN_AGAIN) from None
    if not client_reference_key(authorized):
        raise HTTPException(status_code=422, detail=SIGN_AGAIN)
    return authorized


# Shop-local time. The waiver is signed in person at the studio, and the printed
# record is read there, so dates on the page are rendered in shop time rather than UTC.
# Matches the module-local convention already used in bookings.py and calendar.py.


def _generate_ref_code() -> str:
    """Generate a WVR-XXXXXX reference code (6 uppercase alphanumeric chars)."""
    chars = string.ascii_uppercase + string.digits
    return "WVR-" + "".join(random.choices(chars, k=6))


def _safe_name(name: str) -> str:
    """Sanitize a client name for use in folder paths."""
    return re.sub(r"[^a-zA-Z0-9_-]", "", name.replace(" ", "-"))[:40] or "client"


def _artist_can_access_waiver(instance: DocumentInstance, current_user: User) -> bool:
    """Allow managers broadly, but scope artists to their own waiver work."""
    if ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3):
        return True
    if current_user.role != "artist":
        return False
    data = _parse_data_json(instance.data_json)
    return data.get("artist_id") == str(current_user.id) or instance.artist_id_reviewed == current_user.id


def _require_waiver_access(instance: DocumentInstance, current_user: User) -> None:
    if not _artist_can_access_waiver(instance, current_user):
        raise HTTPException(status_code=403, detail="Waiver is not assigned to this artist")


def _format_signed_datetime(value: datetime | None) -> str | None:
    """Render a stored timestamp in shop-local time with the zone spelled out.

    `%Z` resolves to EST or EDT from the date itself, so a summer signing is not
    mislabelled. Naive values are read as UTC, which is how this API writes them.
    Returns None when there is no timestamp to render.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(shop_timezone()).strftime("%B %d, %Y at %I:%M %p %Z")


def _waiver_signed_date_text(instance: DocumentInstance) -> str:
    """The date-of-signing text printed on the waiver PDF.

    `signed_at` is the date of the tattoo session and the legally meaningful field.
    When it is missing we say so outright instead of quietly substituting another
    timestamp: `created_at` is offered only under its own explicit label, so nothing
    on the page can be mistaken for a signing date that was never recorded.
    """
    signed = _format_signed_datetime(instance.signed_at)
    if signed:
        return signed

    created = _format_signed_datetime(instance.created_at)
    if created:
        return f"Not recorded (waiver record created {created})"
    return "Not recorded"


def _yes_no_answer(value: object) -> str:
    """Render a stored waiver answer without treating the string 'No' as truthy, or a missing one as No."""
    if value is None:
        return "Not recorded"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"yes", "true", "1", "on"}:
            return "Yes"
        if normalized in {"no", "false", "0", "off"}:
            return "No"
        if not normalized:
            return "Not recorded"
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "Not recorded"


# ─────────────────────────────────────────────────────────────────────
# PUBLIC: Upload photo ID
# ─────────────────────────────────────────────────────────────────────


@router.post("/upload-id")
async def upload_photo_id(
    file: UploadFile = File(...),
):
    """
    Public: upload a photo ID image before waiver submission.

    Accepts JPEG, PNG, WebP, HEIC/HEIF (iPhone), or PDF up to 10 MB.
    HEIC/HEIF is converted to JPEG so the artist dashboard can render it.
    Uploads to Cloudflare R2 under waivers/pending-ids/, a private prefix.
    Returns a short-lived signed link: the browser shows it as the preview and
    sends it back on submit. That unexpired link is the caller's proof of upload;
    submit verifies its signature and stores the stable reference instead.
    """
    # Validate content type
    if file.content_type not in _ALLOWED_ID_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"File type '{file.content_type}' not allowed. Use JPEG, PNG, WebP, HEIC, or PDF.",
        )

    # Read and validate size
    file_bytes = await file.read()
    if len(file_bytes) > _MAX_ID_SIZE:
        raise HTTPException(status_code=422, detail="File too large. Maximum 10 MB.")

    content_type = file.content_type

    # Convert HEIC/HEIF (iPhone default) to JPEG — browsers can't render HEIC,
    # and artists review the ID in an <img> on the dashboard.
    if content_type in ("image/heic", "image/heif"):
        import io

        try:
            import pillow_heif
            from PIL import Image

            pillow_heif.register_heif_opener()
            img = Image.open(io.BytesIO(file_bytes))
            out = io.BytesIO()
            img.convert("RGB").save(out, format="JPEG", quality=90)
            file_bytes = out.getvalue()
            content_type = "image/jpeg"
        except Exception as exc:
            logger.error("HEIC conversion failed: %s", exc)
            raise HTTPException(
                status_code=422,
                detail="Could not process this photo. Please try again or upload a JPEG/PNG.",
            ) from exc

    # Determine extension from content type
    ext_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "application/pdf": "pdf",
    }
    ext = ext_map.get(content_type, "bin")
    filename = f"{uuid_mod.uuid4()}.{ext}"

    # Upload to R2
    url = await upload_to_drive(
        file_bytes=file_bytes,
        filename=filename,
        mime_type=content_type,
        folder_path="waivers/pending-ids",
    )

    if not url:
        raise HTTPException(status_code=500, detail="Photo upload failed. Please try again.")

    return {"photo_id_url": signed_id_url(url, _ID_PREVIEW_SECONDS)}


# ─────────────────────────────────────────────────────────────────────
# PUBLIC: Submit waiver
# ─────────────────────────────────────────────────────────────────────


@router.post("/submit", response_model=WaiverResponse)
async def submit_waiver(
    body: WaiverSubmit,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Public: submit a signed tattoo consent waiver.

    - Validates all 3 acknowledgment checkboxes are checked
    - Resolves artist slug → artist name
    - Creates a DocumentInstance (user_id=NULL, guest fields populated)
    - Fires background tasks: aftercare email + Drive upload of signature
    - Returns reference code for client confirmation
    """

    # ── Validate acknowledgments ──
    if not (body.health_and_eligibility and body.risks_and_consent and body.accept_terms):
        raise HTTPException(
            status_code=422,
            detail="All acknowledgment checkboxes must be accepted",
        )

    if not body.signature_data:
        raise HTTPException(status_code=422, detail="Signature is required")

    # The ID must be the one this caller uploaded, proven by the signed link upload-id returned.
    photo_id_reference = require_uploaded_id(body.photo_id_url)

    # ── Resolve artist slug → name ──
    result = await db.execute(
        select(User.id, User.name)
        .where(User.slug == body.artist_slug)
        .where(User.role.in_(["artist", "admin", "founder"]))
    )
    artist_row = result.first()
    if not artist_row:
        raise HTTPException(status_code=404, detail="Artist not found")
    artist_id, artist_name = artist_row

    # ── Find the waiver template (optional — links to existing template) ──
    tpl_result = await db.execute(
        select(DocumentTemplate.id)
        .where(DocumentTemplate.name == "Tattoo Consent Waiver")
        .where(DocumentTemplate.is_active)
    )
    template_id = tpl_result.scalar_one_or_none()

    # ── Build the data payload ──
    now = datetime.now(UTC)
    ref_code = _generate_ref_code()

    waiver_data = {
        "full_name": body.full_name,
        "date_of_birth": body.date_of_birth,
        "phone": body.phone,
        "email": body.email,
        "emergency_contact": body.emergency_contact,
        "emergency_contact_address": body.emergency_contact_address,
        "social_handle": body.social_handle,
        "artist_slug": body.artist_slug,
        "artist_name": artist_name,
        "artist_id": str(artist_id),
        "tattoo_description": body.tattoo_description,
        "tattoo_placement": body.tattoo_placement,
        "pregnant": body.pregnant,
        "blood_thinners": body.blood_thinners,
        "blood_disorder": body.blood_disorder,
        "skin_condition": body.skin_condition,
        "allergies": body.allergies,
        "medical_conditions": body.medical_conditions,
        "photo_id_url": photo_id_reference,
        "health_and_eligibility": body.health_and_eligibility,
        "risks_and_consent": body.risks_and_consent,
        "accept_terms": body.accept_terms,
        "signature_data": body.signature_data,
        "reference_code": ref_code,
    }
    # Every other answer is client text too: none may name a stored object (only the ID proven above).
    waiver_data = authorize_client_data(waiver_data, server_allowed=(photo_id_reference,))

    # ── Create document instance ──
    instance = DocumentInstance(
        template_id=template_id,
        user_id=None,
        guest_name=body.full_name,
        guest_email=body.email,
        guest_social=body.social_handle,
        category="waiver",
        title=f"Tattoo Consent — {body.full_name}",
        status=DocStatus.PENDING_REVIEW,
        data_json=json.dumps(waiver_data),
        signed_at=now,
        notes=f"Public waiver | Artist: {artist_name} | Ref: {ref_code}",
    )
    db.add(instance)
    await db.flush()
    await db.refresh(instance)

    # ── Notify assigned artist ──
    notification = Notification(
        user_id=artist_id,
        title="New Waiver Submitted",
        body=f"New waiver from {body.full_name} needs your review",
        notification_type="info",
        action_url=ROUTES.HUDDLE,
    )
    db.add(notification)

    # ── Quest: auto-complete "waiver-signed" if submitter is a registered user ──
    # Case-insensitive so a waiver signed as "Name@Example.com" still credits
    # the account, and .first() so duplicate rows cannot 500 the submission.
    user_result = await db.execute(select(User.id).where(func.lower(User.email) == normalize_email(body.email)))
    submitter_id = user_result.scalars().first()
    if submitter_id:
        await check_and_complete_quest(db, submitter_id, "waiver-signed")

    # ── Background: aftercare email ──
    spawn_background(
        _send_aftercare_email(
            to=body.email,
            client_name=body.full_name,
            artist_name=artist_name,
            tattoo_description=body.tattoo_description,
            tattoo_placement=body.tattoo_placement,
            reference_code=ref_code,
        )
    )

    # ── Background: upload signature to Drive ──
    asyncio.create_task(
        _upload_waiver_files(
            instance_id=instance.id,
            ref_code=ref_code,
            client_name=body.full_name,
            signature_data=waiver_data["signature_data"],
            photo_id_url=photo_id_reference,
        )
    )

    return WaiverResponse(
        id=instance.id,
        reference_code=ref_code,
        client_name=body.full_name,
        artist_name=artist_name,
        tattoo_description=body.tattoo_description,
        signed_at=now,
    )


# ─────────────────────────────────────────────────────────────────────
# AUTHENTICATED: List waivers
# ─────────────────────────────────────────────────────────────────────


@router.get("/list", response_model=WaiverListResponse)
async def list_waivers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """
    Artist+: list submitted waivers with optional filtering.

    - status: filter by waiver status (pending_review, completed, signed, approved, flagged, rejected)
    - search: case-insensitive client name search
    """
    base_q = select(DocumentInstance).where(DocumentInstance.category == "waiver")
    if current_user.role == "artist":
        base_q = base_q.where(
            or_(
                DocumentInstance.artist_id_reviewed == current_user.id,
                DocumentInstance.data_json.contains(str(current_user.id)),
            )
        )

    if status:
        base_q = base_q.where(DocumentInstance.status == status)
    else:
        # Exclude approved waivers from the default list — they live in the Vault Archive.
        # The Archive button is the deliberate review gate: signed → approved moves them.
        base_q = base_q.where(DocumentInstance.status != DocStatus.APPROVED)

    if search:
        base_q = base_q.where(DocumentInstance.guest_name.ilike(f"%{search}%"))

    # Count
    count_q = select(func.count()).select_from(base_q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Fetch page
    rows_q = base_q.order_by(DocumentInstance.signed_at.desc()).offset(skip).limit(limit)
    result = await db.execute(rows_q)
    instances = result.scalars().all()

    waivers = []
    for inst in instances:
        data = _parse_data_json(inst.data_json)
        waivers.append(
            WaiverListItem(
                id=inst.id,
                guest_name=inst.guest_name,
                guest_email=inst.guest_email,
                artist_name=data.get("artist_name"),
                reference_code=data.get("reference_code"),
                status=inst.status,
                signed_at=inst.signed_at,
                created_at=inst.created_at,
            )
        )

    return WaiverListResponse(waivers=waivers, total=total)


# ─────────────────────────────────────────────────────────────────────
# AUTHENTICATED: List MY waivers (collector-scoped)
# ─────────────────────────────────────────────────────────────────────


@router.get("/mine", response_model=WaiverListResponse)
async def list_my_waivers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """
    Any authenticated user: list waivers they submitted (matched by email).

    Returns waivers where guest_email matches the current user's email.
    """
    base_q = (
        select(DocumentInstance)
        .where(DocumentInstance.category == "waiver")
        .where(DocumentInstance.guest_email == current_user.email)
    )

    # Count
    count_q = select(func.count()).select_from(base_q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Fetch page
    rows_q = base_q.order_by(DocumentInstance.signed_at.desc()).offset(skip).limit(limit)
    result = await db.execute(rows_q)
    instances = result.scalars().all()

    waivers = []
    for inst in instances:
        data = _parse_data_json(inst.data_json)
        waivers.append(
            WaiverListItem(
                id=inst.id,
                guest_name=inst.guest_name,
                guest_email=inst.guest_email,
                artist_name=data.get("artist_name"),
                reference_code=data.get("reference_code"),
                status=inst.status,
                signed_at=inst.signed_at,
                created_at=inst.created_at,
            )
        )

    return WaiverListResponse(waivers=waivers, total=total)


# ─────────────────────────────────────────────────────────────────────
# AUTHENTICATED: Get waiver detail
# ─────────────────────────────────────────────────────────────────────


@router.get("/{waiver_id}", response_model=WaiverDetailResponse)
async def get_waiver(
    waiver_id: uuid_mod.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: get full waiver details with parsed form data."""
    result = await db.execute(
        select(DocumentInstance).where(DocumentInstance.id == waiver_id).where(DocumentInstance.category == "waiver")
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Waiver not found")
    _require_waiver_access(inst, current_user)

    data = _parse_data_json(inst.data_json)

    return WaiverDetailResponse(
        id=inst.id,
        reference_code=data.get("reference_code", ""),
        status=inst.status,
        signed_at=inst.signed_at,
        full_name=data.get("full_name", ""),
        date_of_birth=data.get("date_of_birth", ""),
        phone=data.get("phone", ""),
        email=data.get("email", ""),
        emergency_contact=data.get("emergency_contact", ""),
        emergency_contact_address=data.get("emergency_contact_address"),
        social_handle=data.get("social_handle"),
        artist_name=await _resolve_artist_slug(db, data.get("artist_name", "")),
        tattoo_description=data.get("tattoo_description", ""),
        tattoo_placement=data.get("tattoo_placement") or data.get("placement", ""),
        pregnant=data.get("pregnant", ""),
        blood_disorder=data.get("blood_disorder", ""),
        skin_condition=data.get("skin_condition", ""),
        allergies=data.get("allergies"),
        medical_conditions=data.get("medical_conditions"),
        quoted_price=data.get("quoted_price"),
        street_address=data.get("street_address"),
        session_date=data.get("session_date"),
        intoxicated=data.get("intoxicated"),
        blood_thinners=data.get("blood_thinners"),
        health_same_as_last_visit=data.get("health_same_as_last_visit")
        if isinstance(data.get("health_same_as_last_visit"), bool)
        else None,
        checkin_source=data.get("checkin_source"),
        photo_id_reused_from=_str_or_none(data.get("photo_id_reused_from")),
        signature_adopted_from=_str_or_none(data.get("signature_adopted_from")),
        photo_id_url=signed_id_url(data.get("photo_id_url") or data.get("photo_id"), _ID_VIEW_SECONDS),
        signature_url=data.get("signature_url"),
        artist_signed_at=inst.artist_signed_at,
        artist_reviewed_by=await _resolve_artist_name(db, inst.artist_id_reviewed),
        notes=inst.notes,
        created_at=inst.created_at,
    )


# ─────────────────────────────────────────────────────────────────────
# AUTHENTICATED: Update waiver status
# ─────────────────────────────────────────────────────────────────────


@router.patch("/{waiver_id}/status")
async def update_waiver_status(
    waiver_id: uuid_mod.UUID,
    body: WaiverStatusUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """Artist+: approve, flag, or reject a waiver."""
    allowed = {
        DocStatus.APPROVED,
        DocStatus.FLAGGED,
        DocStatus.REJECTED,
        DocStatus.SIGNED,
        DocStatus.PENDING_REVIEW,
        "completed",
    }
    if body.status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Status must be one of: {', '.join(sorted(allowed))}",
        )

    result = await db.execute(
        select(DocumentInstance).where(DocumentInstance.id == waiver_id).where(DocumentInstance.category == "waiver")
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Waiver not found")
    _require_waiver_access(inst, current_user)
    notes = authorize_client_data(body.notes, caller=current_user)

    inst.status = body.status
    if notes is not None:
        inst.notes = notes

    await db.flush()
    return {"status": "ok", "new_status": inst.status}


# ─────────────────────────────────────────────────────────────────────
# AUTHENTICATED: Artist counter-sign waiver
# ─────────────────────────────────────────────────────────────────────


@router.post("/{waiver_id}/countersign")
async def countersign_waiver(
    waiver_id: uuid_mod.UUID,
    body: CountersignRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """
    Artist+: counter-sign a waiver that a client has submitted.

    Sets the artist signature, marks the waiver as completed,
    and notifies the client (if they have an account).
    """
    if not body.signature_data:
        raise HTTPException(status_code=422, detail="Signature data is required")

    result = await db.execute(
        select(DocumentInstance).where(DocumentInstance.id == waiver_id).where(DocumentInstance.category == "waiver")
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Waiver not found")
    _require_waiver_access(inst, current_user)

    if inst.status not in (DocStatus.PENDING_REVIEW, DocStatus.SIGNED):
        raise HTTPException(
            status_code=422,
            detail=f"Waiver status is '{inst.status}', must be pending_review to countersign",
        )

    # Export embeds the countersignature and staff views show it: only the artist's own signature is stored.
    signature = authorize_countersignature(body.signature_data, current_user)
    condition_notes = authorize_client_data(body.client_condition_notes, caller=current_user)
    artist_notes = authorize_client_data(body.notes, caller=current_user)

    now = datetime.now(UTC)
    inst.artist_signature = signature
    inst.artist_signed_at = now
    inst.artist_id_reviewed = current_user.id
    inst.status = DocStatus.APPROVED
    inst.updated_at = now

    # Store post-session data in data_json
    existing_data = json.loads(inst.data_json) if inst.data_json else {}
    if body.session_duration_minutes is not None:
        existing_data["session_duration_minutes"] = body.session_duration_minutes
    if condition_notes:
        existing_data["client_condition_notes"] = condition_notes
    if artist_notes:
        existing_data["artist_notes"] = artist_notes
    existing_data["countersigned_by"] = current_user.name
    existing_data["countersigned_at"] = now.isoformat()
    existing_data["artist_countersignature"] = signature
    inst.data_json = json.dumps(existing_data)

    # ── Notify client if they have an account ──
    if inst.user_id:
        notification = Notification(
            user_id=inst.user_id,
            title="Waiver Approved",
            body="Your waiver has been reviewed and approved",
            notification_type="info",
            action_url=ROUTES.DASHBOARD,
        )
        db.add(notification)

        # Auto-complete 'waiver-signed' quest for the client
        await check_and_complete_quest(db, inst.user_id, "waiver-signed")

    await db.flush()

    data = _parse_data_json(inst.data_json)
    return {
        "status": "ok",
        "waiver_id": str(inst.id),
        "new_status": "approved",
        "artist_name": current_user.name,
        "artist_signed_at": now.isoformat(),
        "reference_code": data.get("reference_code"),
    }


# ─────────────────────────────────────────────────────────────────────
# PDF Export
# ─────────────────────────────────────────────────────────────────────


@router.post("/{waiver_id}/export-pdf")
async def export_waiver_pdf(
    waiver_id: uuid_mod.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """
    Generate a printable PDF of the completed waiver.

    Page 1: Client info, tattoo details, medical, photo ID, both signatures.
    Page 2: Terms and conditions from the template.
    Returns the R2 URL of the uploaded PDF.
    """
    import io

    import httpx
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Image,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    result = await db.execute(
        select(DocumentInstance).where(DocumentInstance.id == waiver_id).where(DocumentInstance.category == "waiver")
    )
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Waiver not found")
    _require_waiver_access(inst, current_user)

    data = _parse_data_json(inst.data_json)
    ref_code = data.get("reference_code", str(waiver_id)[:8].upper())

    # Fetch template for terms/conditions
    terms_text = ""
    if inst.template_id:
        tpl_result = await db.execute(select(DocumentTemplate).where(DocumentTemplate.id == inst.template_id))
        tpl = tpl_result.scalar_one_or_none()
        if tpl and tpl.description:
            terms_text = tpl.description

    # Build PDF
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "WaiverTitle", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#1a1a1a")
    )
    heading_style = ParagraphStyle(
        "SectionHead", parent=styles["Heading2"], fontSize=11, textColor=colors.HexColor("#a67d43"), spaceAfter=6
    )
    normal = ParagraphStyle("WaiverNormal", parent=styles["Normal"], fontSize=9, leading=12)
    small = ParagraphStyle("WaiverSmall", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#666666"))
    date_style = ParagraphStyle(
        "WaiverSignedDate",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        spaceBefore=6,
        textColor=colors.HexColor("#1a1a1a"),
    )

    elements = []

    # The date of signing is the legally meaningful field on this record, so it is
    # resolved once up front from stored data only and printed unconditionally. It
    # must never sit behind a network fetch or an `except: pass`.
    signed_date_text = _waiver_signed_date_text(inst)

    # Header
    elements.append(Paragraph(escape(get_settings().STUDIO_NAME), title_style))
    elements.append(Paragraph(f"Tattoo Consent Waiver &mdash; {ref_code}", small))
    elements.append(Paragraph(f"<b>Date Signed:</b> {signed_date_text}", date_style))
    elements.append(Spacer(1, 12))

    # Client Information
    elements.append(Paragraph("Client Information", heading_style))
    client_fields = [
        ("Name", data.get("full_name", "--")),
        ("Date of Birth", data.get("date_of_birth", "--")),
        ("Address", data.get("street_address", "--")),
        ("Phone", data.get("phone", "--")),
        ("Email", data.get("email", "--")),
        ("Emergency Contact", f"{data.get('emergency_contact', '--')} {data.get('emergency_phone', '')}".strip()),
        ("Emergency Contact Address", data.get("emergency_contact_address", "--")),
    ]
    table_data = [
        [Paragraph(f"<b>{k}</b>", small), Paragraph(str(v), normal)] for k, v in client_fields if v and v != "--"
    ]
    if table_data:
        t = Table(table_data, colWidths=[1.8 * inch, 4.5 * inch])
        t.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(t)
    elements.append(Spacer(1, 10))

    # Tattoo Details
    elements.append(Paragraph("Tattoo Details", heading_style))
    tattoo_fields = [
        ("Artist", data.get("artist_name", "--")),
        ("Description", data.get("tattoo_description", "--")),
        ("Placement", data.get("tattoo_placement") or data.get("placement", "--")),
        ("Agreed Price", f"${data.get('quoted_price', 'N/A')}"),
        ("Session Date", data.get("session_date", "--")),
    ]
    table_data = [
        [Paragraph(f"<b>{k}</b>", small), Paragraph(str(v), normal)] for k, v in tattoo_fields if v and v != "--"
    ]
    if table_data:
        t = Table(table_data, colWidths=[1.8 * inch, 4.5 * inch])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        elements.append(t)
    elements.append(Spacer(1, 10))

    # Medical Information
    med_fields = [
        ("Pregnant", _yes_no_answer(data.get("pregnant"))),
        ("Blood Thinners", _yes_no_answer(data.get("blood_thinners"))),
        ("Blood Disorder", _yes_no_answer(data.get("blood_disorder"))),
        ("Skin Condition", _yes_no_answer(data.get("skin_condition"))),
        ("Allergies", data.get("allergies", "None")),
        ("Medical Conditions", data.get("medical_conditions", "None")),
        ("Medications", data.get("medications", "None")),
    ]
    if "blood_thinners" not in data:
        med_fields = [row for row in med_fields if row[0] != "Blood Thinners"]
    # SC DPH Reg. 60-111 (801.C): the sobriety statement is part of every check-in waiver record.
    if data.get("intoxicated"):
        sober = "Not under the influence (client stated)" if str(data["intoxicated"]).lower() == "no" else "YES"
        med_fields.insert(0, ("Alcohol / Drugs Today", sober))
    if data.get("health_same_as_last_visit") is True:
        med_fields.append(("Health Answers", "Client confirmed unchanged since last visit"))
    has_med = bool(data.get("intoxicated")) or any(v and v not in ("No", "None", "--") for _, v in med_fields)
    if has_med:
        elements.append(Paragraph("Medical Information", heading_style))
        table_data = [[Paragraph(f"<b>{k}</b>", small), Paragraph(str(v), normal)] for k, v in med_fields]
        t = Table(table_data, colWidths=[1.8 * inch, 4.5 * inch])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        elements.append(t)
        elements.append(Spacer(1, 10))

    # Photo ID
    # The ID image bytes are embedded in the PDF itself. The private R2 object is fetched through a
    # short-lived signed link and never printed as a URL, so the packet stays complete after the
    # public path is blocked. Every export rebuilds from the stored reference, so re-exporting an
    # older waiver embeds its ID the same way.
    # Only this install's own R2 ID objects are fetched. A client-submitted foreign URL is never
    # requested by the server (no server-side request forgery); the packet just notes the ID is on file.
    photo_id_ref = data.get("photo_id_url") or data.get("photo_id")
    if photo_id_ref:
        photo_id_url = signed_id_url(photo_id_ref) if private_id_key(photo_id_ref) else None
        id_png = None
        if photo_id_url and photo_id_url.startswith("http"):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(photo_id_url)
                    if resp.status_code == 200:
                        id_png = normalize_embed_image(resp.content)
            except Exception:
                logger.warning("Waiver %s: photo ID could not be fetched for the PDF", ref_code)
        elif not photo_id_url:
            logger.warning("Waiver %s: photo ID reference is not a stored ID object; not fetched", ref_code)
        elements.append(Paragraph("Photo ID", heading_style))
        if id_png:
            elements.append(Image(io.BytesIO(id_png), width=2.5 * inch, height=1.6 * inch, kind="proportional"))
        else:
            elements.append(
                Paragraph("Photo ID is on file but could not be embedded here. View it in the waiver record.", small)
            )
        elements.append(Spacer(1, 10))

    # Client Signature. The heading and the signed date always print; the signature
    # image is a best-effort addition layered on top. A slow or broken image fetch
    # degrades the document, it does not silently strip the date off a legal record.
    elements.append(Paragraph("Client Signature", heading_style))
    # Signature images are fetched only from this install's own storage: the public form
    # copies client-supplied fields, so a foreign URL here is never requested by the server.
    sig_url = data.get("signature_url")
    if sig_url and is_own_storage_url(sig_url):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(sig_url)
                if resp.status_code == 200:
                    img_buf = io.BytesIO(resp.content)
                    elements.append(Image(img_buf, width=2.5 * inch, height=0.8 * inch, kind="proportional"))
        except Exception:
            pass
    elements.append(Paragraph(f"Signed: {signed_date_text}", small))
    if data.get("signature_adopted_from"):
        elements.append(Paragraph("Client confirmed their saved signature from a prior waiver at check-in.", small))
    if data.get("photo_id_reused_from"):
        elements.append(Paragraph("Photo ID on file from a prior waiver. Artist verifies it in person.", small))
    if data.get("checkin_source"):
        elements.append(Paragraph("Contact verified by one-time code at check-in.", small))
    elements.append(Spacer(1, 10))

    # Artist Sign-Off
    elements.append(Paragraph("Artist Sign-Off", heading_style))
    signoff_fields = [
        ("Artist", data.get("countersigned_by", current_user.name)),
        ("Client Condition", (data.get("client_condition_notes") or "Good").replace("_", " ").title()),
    ]
    artist_ts = _format_signed_datetime(inst.artist_signed_at)
    if artist_ts:
        signoff_fields.append(("Date/Time", artist_ts))
    table_data = [[Paragraph(f"<b>{k}</b>", small), Paragraph(str(v), normal)] for k, v in signoff_fields]
    t = Table(table_data, colWidths=[1.8 * inch, 4.5 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    elements.append(t)

    # Artist signature image
    artist_sig = data.get("artist_countersignature") or inst.artist_signature
    if artist_sig and is_own_storage_url(artist_sig):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(artist_sig)
                if resp.status_code == 200:
                    img_buf = io.BytesIO(resp.content)
                    elements.append(Image(img_buf, width=2.5 * inch, height=0.8 * inch, kind="proportional"))
        except Exception:
            pass
    elements.append(Spacer(1, 10))

    # Page 2: Terms & Conditions
    if terms_text:
        from reportlab.platypus import PageBreak

        elements.append(PageBreak())
        elements.append(Paragraph("Terms &amp; Conditions", title_style))
        elements.append(Spacer(1, 8))
        for para in terms_text.split("\n\n"):
            if para.strip():
                elements.append(Paragraph(para.strip(), normal))
                elements.append(Spacer(1, 6))

    doc.build(elements)
    pdf_bytes = buf.getvalue()

    # Upload to R2
    from datetime import datetime as dt

    month = dt.now(UTC).strftime("%Y-%m")
    safe_name = re.sub(r"[^a-zA-Z0-9]", "_", data.get("full_name", "unknown"))
    pdf_url = await upload_to_drive(
        file_bytes=pdf_bytes,
        filename=f"{safe_name}_{ref_code}.pdf",
        mime_type="application/pdf",
        folder_path=f"waivers/{month}/{safe_name}_{ref_code}",
    )

    # The packet embeds the client's ID, so staff get a short-lived signed link, never the public path.
    return {"pdf_url": signed_id_url(pdf_url, _ID_VIEW_SECONDS), "reference_code": ref_code}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


async def _resolve_artist_slug(db: AsyncSession, slug_or_name: str) -> str:
    """Resolve an artist slug to display name. Falls back to the raw value."""
    if not slug_or_name:
        return ""
    result = await db.execute(select(User.name).where(User.slug == slug_or_name))
    name = result.scalar_one_or_none()
    return name or slug_or_name


async def _resolve_artist_name(db: AsyncSession, artist_id: uuid_mod.UUID | None) -> str | None:
    """Look up artist name by ID, returning None if not set."""
    if not artist_id:
        return None
    result = await db.execute(select(User.name).where(User.id == artist_id))
    return result.scalar_one_or_none()


def _str_or_none(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _parse_data_json(raw: str | None) -> dict:
    """Safely parse data_json, returning empty dict on failure."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn_background(coro) -> asyncio.Task:
    """Fire-and-forget with a strong reference, so the event loop cannot drop a send mid-flight."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def _send_aftercare_email(
    to: str,
    client_name: str,
    artist_name: str,
    tattoo_description: str,
    tattoo_placement: str,
    reference_code: str,
) -> bool:
    """Background task: send the Email Foundry aftercare (Doc Bot, day 0) for a filed waiver.

    Returns True when the provider accepted the send. Never raises: aftercare must not break a submission.
    """
    try:
        member = False
        try:
            async with async_session() as session:
                role = (
                    (await session.execute(select(User.role).where(func.lower(User.email) == normalize_email(to))))
                    .scalars()
                    .first()
                )
                member = role == "collector"
        except Exception:  # noqa: BLE001 - membership only changes the CTA label
            logger.warning("Aftercare membership lookup failed for %s", to)
        settings = get_settings()
        portal_url = f"{settings.FRONTEND_URL.rstrip('/')}/portal"
        if not member:
            portal_url += "?" + urlencode({"email": to, "name": client_name, "from": "aftercare"})
        rendered = render_aftercare_instructions(
            client_name=client_name,
            artist_name=artist_name,
            tattoo_description=tattoo_description,
            tattoo_placement=tattoo_placement,
            reference_code=reference_code,
            portal_url=portal_url,
            member=member,
        )
        accepted = await send_email(
            to=to,
            subject=rendered.subject,
            html_body=rendered.html_body,
            tags=[rendered.slug, "aftercare"],
            from_alias=FROM_SHOP,
        )
        if not accepted:
            logger.error("Aftercare email was not accepted by the provider for %s (ref %s)", to, reference_code)
        return bool(accepted)
    except Exception as exc:
        logger.error("Aftercare email failed for %s: %s", to, exc)
        return False


async def _upload_waiver_files(
    instance_id: uuid_mod.UUID,
    ref_code: str,
    client_name: str,
    signature_data: str,
    photo_id_url: str | None,
) -> None:
    """
    Background task: upload signature (and optionally move photo ID)
    to a permanent Drive folder for this waiver.

    Updates the DocumentInstance's data_json with the Drive signature URL.
    """
    try:
        now = datetime.now(UTC)
        month = now.strftime("%Y-%m")
        safe_name = _safe_name(client_name)
        folder_path = f"waivers/{month}/{safe_name}_{ref_code}"

        # Decode signature base64 → PNG bytes
        sig_bytes = None
        if signature_data:
            # Strip data URI prefix if present
            if "," in signature_data:
                signature_data = signature_data.split(",", 1)[1]
            sig_bytes = base64.b64decode(signature_data)

        sig_url = None
        if sig_bytes:
            sig_url = await upload_to_drive(
                file_bytes=sig_bytes,
                filename="signature.png",
                mime_type="image/png",
                folder_path=folder_path,
            )

        # Update the document instance with the signature URL
        if sig_url:
            from app.core.database import async_session

            async with async_session() as db:
                result = await db.execute(select(DocumentInstance).where(DocumentInstance.id == instance_id))
                inst = result.scalar_one_or_none()
                if inst:
                    data = _parse_data_json(inst.data_json)
                    data["signature_url"] = sig_url
                    # Remove raw base64 to save space
                    data.pop("signature_data", None)
                    inst.data_json = json.dumps(data)
                    inst.file_url = sig_url
                    await db.commit()
                    logger.info("Waiver files uploaded for %s", ref_code)

    except Exception as exc:
        logger.error("Waiver file upload failed for %s: %s", ref_code, exc)
