"""
Scrolls endpoints — dynamic scroll-tab categories + per-category document upload/list.

Tabs: artist (built-in), shop (built-in), misc (built-in), + custom (founder-created).

Endpoints:
  GET    /scrolls/categories               — list all categories (ordered)
  POST   /scrolls/categories               — create new category (founder only)
  DELETE /scrolls/categories/{slug}        — delete custom category (founder only)

  GET    /scrolls/{category_slug}          — list documents in a category (founder sees all, others see own)
  POST   /scrolls/{category_slug}/upload   — upload a scroll file to a category
  DELETE /scrolls/docs/{doc_id}            — delete a scroll document (founder only)

  POST   /scrolls/request/{artist_id}      — send artist a scroll completion request (founder only)
  POST   /scrolls/remind/{artist_id}       — send artist a scroll reminder (founder only, debounced 24h)
"""

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.document import DocumentInstance
from app.models.notification import Notification
from app.models.scroll_category import ScrollCategory
from app.models.user import User
from app.services.email import (
    FROM_ADMIN,
    SCROLL_REQUEST_TEMPLATE_MARKER,
    build_scroll_request_html,
    render_db_template,
    send_email,
)
from app.services.push import send_push_notification
from app.services.storage import scroll_id_filename, signed_id_url
from app.services.storage import upload_file as upload_to_blob

logger = logging.getLogger("ryozan.scrolls")
router = APIRouter(prefix="/scrolls", tags=["scrolls"])

# Scroll PDFs that embed the artist's government ID are private in R2; list and upload responses
# carry signed links for those, valid for one review session. Other files keep their public links.
SCROLL_FILE_VIEW_SECONDS = 900

SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
OWNER_ARCHIVE_SLUG = "owner-archive"
OWNER_ARCHIVE_CATEGORY = f"scroll_{OWNER_ARCHIVE_SLUG}"


def _require_owner_archive_access(user: User, category_slug: str) -> None:
    if category_slug == OWNER_ARCHIVE_SLUG and user.role != "founder":
        raise HTTPException(status_code=403, detail="Owner archive access requires founder role")


def _slugify(label: str) -> str:
    return "".join(c if c in SLUG_CHARS else "_" for c in label.lower().strip())[:60]


# ── Categories ────────────────────────────────────────────────────────────────


@router.get("/categories")
async def list_categories(
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("artist")),
):
    """List all scroll categories ordered by sort_order."""
    result = await db.execute(select(ScrollCategory).order_by(ScrollCategory.sort_order, ScrollCategory.label))
    cats = result.scalars().all()
    if current_user.role != "founder":
        cats = [cat for cat in cats if cat.slug != OWNER_ARCHIVE_SLUG]
    return [
        {
            "id": str(c.id),
            "slug": c.slug,
            "label": c.label,
            "icon": c.icon,
            "description": c.description,
            "is_builtin": c.is_builtin,
            "ai_digest_enabled": c.ai_digest_enabled,
            "ai_digest_prompt": c.ai_digest_prompt,
            "sort_order": c.sort_order,
        }
        for c in cats
    ]


@router.post("/categories", status_code=201)
async def create_category(
    label: str = Form(...),
    description: str = Form(""),
    icon: str = Form("📄"),
    ai_digest_enabled: bool = Form(False),
    ai_digest_prompt: str = Form(""),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("founder")),
):
    """
    Founder only — create a new scroll tab category.
    Slug is auto-derived from label. Returns the new category.
    """
    slug = _slugify(label)
    # Ensure uniqueness — append counter if clash
    base_slug = slug
    counter = 1
    while True:
        existing = await db.execute(select(ScrollCategory).where(ScrollCategory.slug == slug))
        if not existing.scalar_one_or_none():
            break
        slug = f"{base_slug}_{counter}"
        counter += 1

    # Sort order = one past the current max
    max_order_result = await db.execute(
        select(ScrollCategory.sort_order).order_by(ScrollCategory.sort_order.desc()).limit(1)
    )
    max_order = max_order_result.scalar_one_or_none() or 0

    cat = ScrollCategory(
        slug=slug,
        label=label.strip(),
        icon=icon or "📄",
        description=description.strip() or None,
        is_builtin=False,
        sort_order=max_order + 1,
        ai_digest_enabled=ai_digest_enabled,
        ai_digest_prompt=ai_digest_prompt.strip() or None,
        created_by=current_user.id,
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)

    return {
        "id": str(cat.id),
        "slug": cat.slug,
        "label": cat.label,
        "icon": cat.icon,
        "description": cat.description,
        "is_builtin": cat.is_builtin,
        "ai_digest_enabled": cat.ai_digest_enabled,
        "ai_digest_prompt": cat.ai_digest_prompt,
        "sort_order": cat.sort_order,
    }


class InterpretCategoryRequest(BaseModel):
    intent: str


_VALID_ICON_SLUGS = ["file", "dollar", "health", "legal", "chart", "lock", "clipboard", "folder", "mail", "pen"]


@router.post("/categories/interpret", status_code=201)
async def gala_interpret_category(
    body: InterpretCategoryRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("founder")),
):
    """
    Founder only — Gala (Claude Haiku) interprets natural language intent and creates a scroll category.
    Returns the new category with AI-chosen label, description, icon, and digest settings.
    """
    from app.core.config import get_settings
    from app.services.ai_routing import call_ark_gateway_text, require_ai_access

    s = get_settings()

    require_ai_access(s, current_user)
    if not s.ARK_MODEL_GATEWAY_KEY:
        raise HTTPException(503, "AI not configured. Set NEBIUS_API_KEY.")

    prompt = (
        f"You are configuring a document management system for a tattoo studio called {s.STUDIO_NAME}\n"
        f'A founder wants a new document tab with this intent:\n"{body.intent.strip()}"\n\n'
        "Return ONLY a JSON object with these exact fields:\n"
        "{\n"
        '  "label": "Short tab name (2-3 words, title-case)",\n'
        '  "description": "One sentence describing what documents belong here.",\n'
        f'  "icon": "ONE of: {", ".join(_VALID_ICON_SLUGS)}",\n'
        '  "ai_digest_enabled": true or false,\n'
        '  "ai_digest_prompt": "What to extract (empty string if ai_digest_enabled is false)"\n'
        "}\n"
        "Rules: no emojis, return ONLY the JSON, no markdown fences, no explanation."
    )

    raw, _usage = await call_ark_gateway_text(
        settings=s,
        system_prompt="You configure a document management system and return only valid JSON.",
        user_prompt=prompt,
        primary_model=s.ARK_GATEWAY_UTILITY_MODEL,
        fallback_models=[s.ARK_GATEWAY_UTILITY_FALLBACK_MODEL],
        max_tokens=256,
    )
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"Gala returned invalid JSON: {exc}") from exc

    label = str(data.get("label", "")).strip() or "New Tab"
    description = str(data.get("description", "")).strip()
    icon = str(data.get("icon", "file"))
    if icon not in _VALID_ICON_SLUGS:
        icon = "file"
    ai_digest_enabled = bool(data.get("ai_digest_enabled", False))
    ai_digest_prompt = str(data.get("ai_digest_prompt", "")).strip()

    # Slug uniqueness (same logic as create_category)
    slug = _slugify(label)
    base_slug = slug
    counter = 1
    while True:
        existing = await db.execute(select(ScrollCategory).where(ScrollCategory.slug == slug))
        if not existing.scalar_one_or_none():
            break
        slug = f"{base_slug}_{counter}"
        counter += 1

    max_order_result = await db.execute(
        select(ScrollCategory.sort_order).order_by(ScrollCategory.sort_order.desc()).limit(1)
    )
    max_order = max_order_result.scalar_one_or_none() or 0

    cat = ScrollCategory(
        slug=slug,
        label=label,
        icon=icon,
        description=description or None,
        is_builtin=False,
        sort_order=max_order + 1,
        ai_digest_enabled=ai_digest_enabled,
        ai_digest_prompt=ai_digest_prompt or None,
        created_by=current_user.id,
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)

    return {
        "id": str(cat.id),
        "slug": cat.slug,
        "label": cat.label,
        "icon": cat.icon,
        "description": cat.description,
        "is_builtin": cat.is_builtin,
        "ai_digest_enabled": cat.ai_digest_enabled,
        "ai_digest_prompt": cat.ai_digest_prompt,
        "sort_order": cat.sort_order,
    }


@router.delete("/categories/{slug}", status_code=204)
async def delete_category(
    slug: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(require_role("founder")),
):
    """Founder only — delete a custom (non-builtin) category."""
    result = await db.execute(select(ScrollCategory).where(ScrollCategory.slug == slug))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(404, "Category not found")
    if cat.is_builtin:
        raise HTTPException(403, "Cannot delete built-in scroll categories")
    await db.delete(cat)
    await db.commit()


# ── Documents in a category ───────────────────────────────────────────────────


@router.get("/{category_slug}")
async def list_category_docs(
    category_slug: str,
    user_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """
    List scroll documents in a category.
    - Founders/artists see all docs (or filter by user_id).
    - Others see only their own.
    """
    _require_owner_archive_access(current_user, category_slug)

    # Validate category exists
    cat_result = await db.execute(select(ScrollCategory).where(ScrollCategory.slug == category_slug))
    cat = cat_result.scalar_one_or_none()
    if not cat:
        raise HTTPException(404, "Category not found")

    # For artist scrolls specifically, delegate to existing pattern
    # For all categories: category stored as "scroll_{slug}" except built-in "artist" = "scroll"
    doc_category = "scroll" if category_slug == "artist" else f"scroll_{category_slug}"

    query = (
        select(DocumentInstance, User.name.label("user_name"))
        .outerjoin(User, DocumentInstance.user_id == User.id)
        .where(DocumentInstance.category == doc_category)
    )

    # Role scoping: founders/admins see all (or filter by user_id), artists see only their own
    role = getattr(current_user, "role", "collector")
    if role in ("founder", "admin"):
        if user_id:
            pass  # will be filtered below
        # else: no filter, sees all
    else:
        # Artists and collectors only see their own scrolls
        query = query.where(DocumentInstance.user_id == current_user.id)
    if role in ("founder", "admin") and user_id:
        try:
            query = query.where(DocumentInstance.user_id == uuid.UUID(user_id))
        except ValueError:
            raise HTTPException(400, "Invalid user_id") from None

    query = query.order_by(DocumentInstance.created_at.desc())
    result = await db.execute(query)
    rows = result.all()

    docs = []
    for inst, user_name in rows:
        data = {}
        if inst.data_json:
            with contextlib.suppress(Exception):
                data = json.loads(inst.data_json)
        docs.append(
            {
                "id": str(inst.id),
                "user_id": str(inst.user_id) if inst.user_id else None,
                "user_name": user_name,
                "title": inst.title,
                "status": inst.status,
                # A scroll PDF that embeds the artist's government ID: short-lived signed links only.
                "file_url": signed_id_url(inst.file_url, SCROLL_FILE_VIEW_SECONDS),
                "signed_at": inst.signed_at.isoformat() if inst.signed_at else None,
                "created_at": inst.created_at.isoformat(),
                "ai_digest": data.get("ai_digest"),
                "notes": inst.notes,
            }
        )

    return {
        "category": {
            "slug": cat.slug,
            "label": cat.label,
            "icon": cat.icon,
            "ai_digest_enabled": cat.ai_digest_enabled,
        },
        "docs": docs,
        "total": len(docs),
    }


@router.post("/{category_slug}/upload", status_code=201)
async def upload_scroll_doc(
    category_slug: str,
    file: UploadFile = File(...),
    title: str = Form(""),
    notes: str = Form(""),
    ai_digest_enabled: bool = Form(False),
    is_id: bool = Form(False),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a document to a scroll category.
    When the uploader marks the file as a government ID (is_id), it is stored under the private
    ".id.<ext>" marker name, only ever served through short-lived signed links, and never sent to
    the Gala AI digest.
    Any authenticated user can upload to their own category.
    If the category has ai_digest_enabled OR the caller passes ai_digest_enabled=true,
    Gala extracts structured intel and enriches current_user.nexus_profile.
    """
    _require_owner_archive_access(current_user, category_slug)

    # Validate category
    cat_result = await db.execute(select(ScrollCategory).where(ScrollCategory.slug == category_slug))
    cat = cat_result.scalar_one_or_none()
    if not cat:
        raise HTTPException(404, "Category not found")

    allowed_mime = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
        "text/plain",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if file.content_type not in allowed_mime:
        raise HTTPException(400, f"File type '{file.content_type}' not accepted for scroll uploads")

    doc_title = title.strip() or (file.filename or "Untitled Scroll")
    doc_category = "scroll" if category_slug == "artist" else f"scroll_{category_slug}"
    ext = Path(file.filename or "doc").suffix or ""
    token = uuid.uuid4().hex
    filename = scroll_id_filename(token, ext) if is_id else f"{token}{ext}"

    content = await file.read()

    blob_url = await upload_to_blob(
        file_bytes=content,
        filename=filename,
        mime_type=file.content_type or "application/octet-stream",
        folder_path=f"scrolls/{category_slug}/{current_user.id}",
    )
    if not blob_url:
        raise HTTPException(500, "File storage unavailable — upload failed")

    # Gala AI digest (optional, async, non-blocking)
    ai_digest_result: dict | None = None
    # An ID never goes to the AI digest, whatever the category or caller asks for.
    should_digest = (ai_digest_enabled or cat.ai_digest_enabled) and not is_id
    if should_digest and cat.ai_digest_prompt:
        try:
            ai_digest_result = await _run_gala_digest(
                content=content,
                mime_type=file.content_type or "",
                filename=file.filename or "",
                prompt=cat.ai_digest_prompt,
                user=current_user,
                db=db,
            )
        except Exception as exc:
            logger.warning("Gala digest failed for %s/%s: %s", category_slug, current_user.id, exc)

    data_json_payload: dict = {}
    if ai_digest_result:
        data_json_payload["ai_digest"] = ai_digest_result

    instance = DocumentInstance(
        user_id=current_user.id,
        category=doc_category,
        title=doc_title,
        status="submitted",
        file_url=blob_url,
        notes=notes.strip() or None,
        data_json=json.dumps(data_json_payload) if data_json_payload else None,
        signed_at=datetime.now(UTC),
    )
    db.add(instance)
    await db.commit()
    await db.refresh(instance)

    return {
        "id": str(instance.id),
        "title": instance.title,
        # Category uploads stay public unless marked as an ID (the ".id.<ext>" key).
        "file_url": signed_id_url(blob_url, SCROLL_FILE_VIEW_SECONDS),
        "status": "submitted",
        "ai_digest": ai_digest_result,
        "created_at": instance.created_at.isoformat(),
    }


@router.delete("/docs/{doc_id}", status_code=204)
async def delete_scroll_doc(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Delete a scroll document. Founders can delete any; others delete only their own."""
    result = await db.execute(select(DocumentInstance).where(DocumentInstance.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    role = getattr(current_user, "role", "collector")
    if doc.category == OWNER_ARCHIVE_CATEGORY and role != "founder":
        raise HTTPException(403, "Owner archive access requires founder role")
    if role not in ("founder", "admin") and doc.user_id != current_user.id:
        raise HTTPException(403, "Not authorised to delete this document")

    await db.delete(doc)
    await db.commit()


# ── Gala AI Digest ─────────────────────────────────────────────────────────────


async def _run_gala_digest(
    content: bytes,
    mime_type: str,
    filename: str,
    prompt: str,
    user: User,
    db: AsyncSession,
) -> dict:
    """
    Send document content through the Ark Model Gateway for structured extraction.
    Updates user.nexus_profile with extracted intel.
    Returns the extracted digest dict.

    Only supports text/CSV/plain content for now (binary PDFs need OCR pipeline).
    """
    from app.core.config import get_settings
    from app.services.ai_routing import ai_access_allowed, call_ark_gateway_text

    settings = get_settings()

    # Optional enrichment of a non-AI upload: skip the model call for non-staff
    # during limited release rather than failing the upload itself.
    if not ai_access_allowed(settings, user):
        return {"error": "Gala AI is in limited release (founder/admin only)"}

    if not settings.ARK_MODEL_GATEWAY_KEY:
        return {"error": "Gala AI not configured"}

    # Decode readable content (text/CSV only; skip binary PDFs for now)
    content_text: str | None = None
    if mime_type in ("text/plain", "text/csv"):
        with contextlib.suppress(Exception):
            content_text = content.decode("utf-8", errors="replace")[:8000]

    if not content_text:
        return {"note": "Binary file — text extraction pending"}

    system_msg = (
        "You are Gala, Ryozan's AI intelligence layer. "
        "Extract structured business intel from the following document. "
        "Return a compact JSON object with the most valuable insights. "
        "Keep keys snake_case. Be concise. No fluff."
    )
    user_msg = f"{prompt}\n\n---DOCUMENT---\n{content_text}"

    try:
        raw, _usage = await call_ark_gateway_text(
            settings=settings,
            system_prompt=system_msg,
            user_prompt=user_msg,
            primary_model=settings.ARK_GATEWAY_UTILITY_MODEL,
            fallback_models=[settings.ARK_GATEWAY_UTILITY_FALLBACK_MODEL],
            max_tokens=512,
        )
    except Exception as exc:
        logger.warning("Gala digest gateway call failed for %s: %s", filename, exc)
        return {"error": "Gala AI unavailable"}

    # Parse JSON from response
    try:
        digest = json.loads(raw)
    except Exception:
        # Try to extract JSON block if wrapped in markdown
        import re

        m = re.search(r"```(?:json)?\s*(\{[\s\S]+?\})\s*```", raw)
        digest = json.loads(m.group(1)) if m else {"raw": raw[:500]}

    # Enrich nexus_profile
    try:
        profile = dict(user.nexus_profile or {})
        scroll_intel = profile.get("scroll_intel", {})
        # Merge by filename key so re-uploads overwrite cleanly
        scroll_intel[filename] = {"digest": digest, "updated_at": datetime.now(UTC).isoformat()}
        profile["scroll_intel"] = scroll_intel
        user.nexus_profile = profile
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to update nexus_profile for %s: %s", user.id, exc)

    return digest


# ── Scroll Request / Remind ────────────────────────────────────────────────────

_SCROLL_REQUEST_NOTIF_TYPE = "scroll_request"
_SCROLL_REMIND_NOTIF_TYPE = "scroll_remind"
_REMIND_DEBOUNCE_HOURS = 24
_ARTIST_SCROLL_ACTION_URL = "/dashboard?portal=hub&workflow=artist-scroll"


async def _get_latest_scroll(db: AsyncSession, artist_id: UUID) -> DocumentInstance | None:
    result = await db.execute(
        select(DocumentInstance)
        .where(
            DocumentInstance.user_id == artist_id,
            DocumentInstance.category == "scroll",
        )
        .order_by(DocumentInstance.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_latest_scroll_notification(
    db: AsyncSession,
    artist_id: UUID,
    notification_type: str | tuple[str, ...],
) -> Notification | None:
    types = (notification_type,) if isinstance(notification_type, str) else notification_type
    result = await db.execute(
        select(Notification)
        .where(
            and_(
                Notification.user_id == artist_id,
                Notification.notification_type.in_(types),
            )
        )
        .order_by(desc(Notification.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _send_scroll_notification(db: AsyncSession, artist: User, *, is_reminder: bool) -> Notification:
    if is_reminder:
        title = "Reminder: Your Artist Scroll is Still Incomplete"
        body = (
            "Your Artist Scroll is still incomplete and needs to be filled out immediately. "
            "Open the portal, complete each section, and sign it for facility records."
        )
        notification_type = _SCROLL_REMIND_NOTIF_TYPE
        push_body = "Your Artist Scroll still needs to be filled out. Open the portal and sign it today."
        subject = "Reminder: Your Artist Scroll is Still Incomplete"
        email_eyebrow = "REMINDER // ARTIST SCROLL"
        email_body_intro = "Your Artist Scroll is still incomplete and needs your signature."
    else:
        title = "Action Required: Complete Your Artist Scroll"
        body = (
            "Fill out your Artist Scroll. It is required for health department inspections and facility records. "
            "Complete it to earn 500 BTTCoin and 150 XP."
        )
        notification_type = _SCROLL_REQUEST_NOTIF_TYPE
        push_body = "Fill out your Artist Scroll for health department records. Tap to open it with Spyder."
        subject = "Action Required: Complete Your Artist Scroll"
        email_eyebrow = "ACTION REQUIRED // ARTIST SCROLL"
        email_body_intro = "Your Artist Scroll is ready to be filled out and signed."

    notif = Notification(
        user_id=artist.id,
        title=title,
        body=body,
        notification_type=notification_type,
        action_url=_ARTIST_SCROLL_ACTION_URL,
        is_read=False,
    )
    db.add(notif)
    await db.commit()

    await send_push_notification(
        db,
        artist.id,
        title=title,
        body=push_body,
        url=_ARTIST_SCROLL_ACTION_URL,
    )

    if artist.email:
        action_url = f"{get_settings().FRONTEND_URL.rstrip('/')}{_ARTIST_SCROLL_ACTION_URL}"
        stored_html = await render_db_template(
            "scroll_request",
            {
                "artist_name": artist.name or "there",
                "eyebrow": email_eyebrow,
                "body_intro": email_body_intro,
                "action_url": action_url,
            },
        )
        # The database can still contain the pre-Foundry seed, including its
        # obsolete /dashboard?panel=codex CTA. Only a current Foundry-marked
        # template is safe to send; otherwise the source builder is authoritative.
        html = (
            stored_html
            if stored_html and SCROLL_REQUEST_TEMPLATE_MARKER in stored_html
            else build_scroll_request_html(
                artist.name or "there",
                is_reminder=is_reminder,
                action_url=action_url,
                eyebrow=email_eyebrow,
                body_intro=email_body_intro,
            )
        )
        await send_email(
            to=artist.email,
            subject=subject,
            html_body=html,
            from_alias=FROM_ADMIN,
        )

    return notif


async def _send_scroll_reminder_if_due(db: AsyncSession, artist: User) -> bool:
    latest_scroll = await _get_latest_scroll(db, artist.id)
    if latest_scroll and latest_scroll.status == "signed":
        return False

    latest_request = await _get_latest_scroll_notification(
        db,
        artist.id,
        (_SCROLL_REQUEST_NOTIF_TYPE, _SCROLL_REMIND_NOTIF_TYPE),
    )
    if not latest_request:
        return False

    cutoff = datetime.now(UTC) - timedelta(hours=_REMIND_DEBOUNCE_HOURS)
    latest_reminder = await _get_latest_scroll_notification(db, artist.id, _SCROLL_REMIND_NOTIF_TYPE)
    if latest_reminder and latest_reminder.created_at >= cutoff:
        return False

    await _send_scroll_notification(db, artist, is_reminder=True)
    return True


@router.post("/request/{artist_id}", status_code=204)
async def request_artist_scroll(
    artist_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("founder")),
):
    """
    Send the artist a first-time notification asking them to complete their Artist Scroll.
    Founder only. Safe to call multiple times — each call creates a new notification.
    The notification links directly to the Spyder Artist Scroll workflow in the Hub.
    """
    artist_result = await db.execute(select(User).where(User.id == artist_id))
    artist = artist_result.scalar_one_or_none()
    if not artist:
        raise HTTPException(404, "Artist not found")
    if artist.role not in ("artist", "admin", "founder"):
        raise HTTPException(400, "Target user is not an artist")

    await _send_scroll_notification(db, artist, is_reminder=False)

    logger.info("Scroll request sent to artist %s by founder %s", artist_id, current_user.id)


@router.post("/remind/{artist_id}", status_code=204)
async def remind_artist_scroll(
    artist_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(require_role("founder")),
):
    """
    Send the artist a reminder to complete their Artist Scroll.
    Debounced: if a remind was already sent within the last 24 hours, returns 429.
    """
    artist_result = await db.execute(select(User).where(User.id == artist_id))
    artist = artist_result.scalar_one_or_none()
    if not artist:
        raise HTTPException(404, "Artist not found")

    cutoff = datetime.now(UTC) - timedelta(hours=_REMIND_DEBOUNCE_HOURS)
    recent = await _get_latest_scroll_notification(db, artist.id, _SCROLL_REMIND_NOTIF_TYPE)
    if recent and recent.created_at >= cutoff:
        raise HTTPException(
            429,
            detail=f"A reminder was already sent within the last {_REMIND_DEBOUNCE_HOURS} hours. Wait before sending another.",
        )

    await _send_scroll_notification(db, artist, is_reminder=True)

    logger.info("Scroll reminder sent to artist %s by founder %s", artist_id, current_user.id)


@router.get("/status/{artist_id}")
async def get_artist_scroll_request_status(
    artist_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    _: User = Depends(require_role("founder")),
):
    """Founder: review whether an artist has been requested or reminded to complete their scroll."""
    artist_result = await db.execute(select(User).where(User.id == artist_id))
    artist = artist_result.scalar_one_or_none()
    if not artist:
        raise HTTPException(404, "Artist not found")

    latest_scroll = await _get_latest_scroll(db, artist.id)
    latest_request = await _get_latest_scroll_notification(db, artist.id, _SCROLL_REQUEST_NOTIF_TYPE)
    latest_reminder = await _get_latest_scroll_notification(db, artist.id, _SCROLL_REMIND_NOTIF_TYPE)
    last_touch = latest_reminder or latest_request
    next_reminder_at = None
    if last_touch and not (latest_scroll and latest_scroll.status == "signed"):
        next_reminder_at = last_touch.created_at + timedelta(hours=_REMIND_DEBOUNCE_HOURS)

    return {
        "artist_id": str(artist.id),
        "has_completed_scroll": bool(latest_scroll and latest_scroll.status == "signed"),
        "request_sent": bool(latest_request or latest_reminder),
        "last_request_at": latest_request.created_at.isoformat() if latest_request else None,
        "last_reminder_at": latest_reminder.created_at.isoformat() if latest_reminder else None,
        "next_reminder_at": next_reminder_at.isoformat() if next_reminder_at else None,
        "daily_reminders_active": bool(
            (latest_request or latest_reminder) and not (latest_scroll and latest_scroll.status == "signed")
        ),
    }


@router.post("/reminders/daily")
async def send_daily_scroll_reminders(
    db: AsyncSession = Depends(get_db, scope="function"),
    x_cron_secret: str = Header(alias="X-Cron-Secret"),
):
    """
    Cron-triggered Artist Scroll reminder sweep.

    Sends one reminder per day to artists who already received a scroll request
    and still have no signed Artist Scroll on file.
    """
    settings = get_settings()
    if x_cron_secret != settings.CRON_SECRET:
        raise HTTPException(status_code=403, detail="Invalid cron secret")

    artists_result = await db.execute(select(User).where(User.role.in_(("artist", "admin", "founder"))))
    artists = artists_result.scalars().all()
    sent: list[str] = []
    skipped = 0
    for artist in artists:
        if await _send_scroll_reminder_if_due(db, artist):
            sent.append(str(artist.id))
        else:
            skipped += 1

    return {
        "status": "ok",
        "sent_count": len(sent),
        "skipped_count": skipped,
        "sent_artist_ids": sent,
    }
