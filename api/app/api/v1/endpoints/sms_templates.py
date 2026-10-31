"""SMS template CRUD with role-scoped ownership.

Three classes of templates:

* **Shop templates** (`owner_id IS NULL`) — booking confirmations, STOP/HELP
  responses, reminders. Only founder/admin can edit. Everyone authenticated
  can READ (so artist UIs can call ``send_from_template("booking_confirmation")``
  via shared service code if they need to).
* **Personal templates** (`owner_id = <user_id>`) — artist's own follow-ups,
  custom aftercare, etc. Only the owner + admin+ can read/edit. Slug is
  auto-prefixed ``staff:<owner_uuid>:`` on create so they don't collide with
  shop slugs.
* **Defaults** in ``sms_template_seeder`` — seed-time only; the live DB rows
  are the source of truth once seeded.
"""

import logging
import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.sms_template import SmsTemplate
from app.models.user import User
from app.services.sms import render_template

logger = logging.getLogger("ryozan.sms_templates")
router = APIRouter(prefix="/sms-templates", tags=["sms-templates"])

# Slug rules:
#   shop slug:     ^[a-z0-9_]+$
#   personal slug: ^staff:<uuid>:[a-z0-9_]+$  (server prefixes on POST)
_USER_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_PERSONAL_SLUG_PREFIX = "staff:"


# ── Schemas ──────────────────────────────────────────


class TemplateResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    category: str
    body: str
    variables: list[dict]
    is_active: bool
    description: str | None
    owner_id: UUID | None
    is_shop: bool  # convenience: True when owner_id is None
    is_mine: bool  # convenience: True when owner_id == current_user.id
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TemplateCreate(BaseModel):
    # 57 = 100 (DB column) - 43 ("staff:<uuid>:" prefix for personal templates)
    slug: str = Field(pattern=r"^[a-z0-9_]+$", max_length=57)
    name: str
    body: str
    category: str = "transactional"
    description: str | None = None
    variables: list[dict] = Field(default_factory=list)


class TemplateUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    category: str | None = None
    is_active: bool | None = None
    description: str | None = None


class TemplatePreview(BaseModel):
    body: str
    sample_data: dict[str, str] = Field(default_factory=dict)


# ── Permission helpers ──────────────────────────────────────────


def _is_admin(user: User) -> bool:
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK.get("admin", 3)


def _is_staff(user: User) -> bool:
    """Artist, admin, or founder. Excludes collectors."""
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK.get("artist", 2)


def _require_staff(user: User) -> None:
    if not _is_staff(user):
        raise HTTPException(status_code=403, detail="Staff role or higher required")


def _can_edit(user: User, tpl: SmsTemplate) -> bool:
    """Admin+ can edit anything; otherwise must own the template."""
    if _is_admin(user):
        return True
    return tpl.owner_id is not None and tpl.owner_id == user.id


def _can_read(user: User, tpl: SmsTemplate) -> bool:
    """Admin+ reads all; staff reads shop + own; otherwise nothing.

    Collectors don't have any read path here at all (router guard on _require_staff).
    """
    if _is_admin(user):
        return True
    # Staff: shop templates (owner_id NULL) and their own
    if tpl.owner_id is None:
        return True
    return tpl.owner_id == user.id


def _to_response(tpl: SmsTemplate, user: User) -> dict:
    return {
        "id": tpl.id,
        "slug": tpl.slug,
        "name": tpl.name,
        "category": tpl.category,
        "body": tpl.body,
        "variables": tpl.variables,
        "is_active": tpl.is_active,
        "description": tpl.description,
        "owner_id": tpl.owner_id,
        "is_shop": tpl.owner_id is None,
        "is_mine": tpl.owner_id is not None and tpl.owner_id == user.id,
        "created_at": tpl.created_at,
        "updated_at": tpl.updated_at,
    }


def _segment_count(body: str) -> int:
    """Rough SMS segment estimate (GSM-7 vs UCS-2)."""
    gsm = set(
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà{}[]~^|€\\"
    )
    is_unicode = any(c not in gsm for c in body)
    length = len(body)
    if is_unicode:
        return 1 if length <= 70 else (length + 66) // 67
    return 1 if length <= 160 else (length + 152) // 153


# ── Endpoints ────────────────────────────────────────


@router.get("/", response_model=list[TemplateResponse])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Staff+: list every template the caller is allowed to see.

    * Admin+ → all templates
    * Staff (artist) → shop templates + their own personal templates
    * Collector → 403
    """
    _require_staff(current_user)
    if _is_admin(current_user):
        query = select(SmsTemplate).order_by(SmsTemplate.category, SmsTemplate.name)
    else:
        query = (
            select(SmsTemplate)
            .where(or_(SmsTemplate.owner_id.is_(None), SmsTemplate.owner_id == current_user.id))
            .order_by(SmsTemplate.category, SmsTemplate.name)
        )
    rows = (await db.execute(query)).scalars().all()
    return [_to_response(t, current_user) for t in rows]


@router.get("/{slug}", response_model=TemplateResponse)
async def get_template(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_staff(current_user)
    result = await db.execute(select(SmsTemplate).where(SmsTemplate.slug == slug))
    tpl = result.scalar_one_or_none()
    if not tpl or not _can_read(current_user, tpl):
        raise HTTPException(status_code=404, detail="Template not found")
    return _to_response(tpl, current_user)


@router.post("/", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Staff+: create a new template.

    * Staff (artist) → creates a personal template owned by them. Slug is
      auto-prefixed ``staff:<user_uuid>:<your_slug>``.
    * Admin+ → creates a shop-level template (``owner_id=None``, slug stored
      as-is). Admin cannot create personal templates via this endpoint.
    """
    _require_staff(current_user)
    if not _USER_SLUG_RE.match(body.slug):
        raise HTTPException(status_code=400, detail="Slug must be a-z 0-9 underscore only")

    if _is_admin(current_user):
        # Admin creates a shop-level template by default.
        slug = body.slug
        owner_id = None
    else:
        # Artist gets prefixed slug + owns the row.
        slug = f"{_PERSONAL_SLUG_PREFIX}{current_user.id}:{body.slug}"
        owner_id = current_user.id

    # Reject duplicate slugs.
    existing = await db.execute(select(SmsTemplate).where(SmsTemplate.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Template with slug {slug!r} already exists")

    tpl = SmsTemplate(
        slug=slug,
        name=body.name,
        category=body.category,
        body=body.body,
        variables=body.variables,
        description=body.description,
        owner_id=owner_id,
    )
    db.add(tpl)
    await db.flush()
    logger.info(
        "sms_template.create slug=%s owner=%s by=%s",
        slug,
        owner_id,
        current_user.id,
    )
    return _to_response(tpl, current_user)


@router.patch("/{slug}", response_model=TemplateResponse)
async def update_template(
    slug: str,
    body: TemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_staff(current_user)
    result = await db.execute(select(SmsTemplate).where(SmsTemplate.slug == slug))
    tpl = result.scalar_one_or_none()
    if not tpl or not _can_read(current_user, tpl):
        raise HTTPException(status_code=404, detail="Template not found")
    if not _can_edit(current_user, tpl):
        raise HTTPException(
            status_code=403,
            detail="You can only edit your own templates. Shop templates require admin.",
        )
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tpl, field, value)
    tpl.updated_at = datetime.now(UTC)
    await db.flush()
    return _to_response(tpl, current_user)


@router.delete("/{slug}", status_code=204)
async def delete_template(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a template. Same edit gate as PATCH."""
    _require_staff(current_user)
    result = await db.execute(select(SmsTemplate).where(SmsTemplate.slug == slug))
    tpl = result.scalar_one_or_none()
    if not tpl or not _can_read(current_user, tpl):
        raise HTTPException(status_code=404, detail="Template not found")
    if not _can_edit(current_user, tpl):
        raise HTTPException(status_code=403, detail="You can only delete your own templates")
    await db.delete(tpl)
    await db.flush()
    logger.info("sms_template.delete slug=%s by=%s", slug, current_user.id)


@router.post("/preview")
async def preview_template(
    body: TemplatePreview,
    current_user: User = Depends(get_current_user),
):
    """Render a body string with sample data. Doesn't require ownership —
    artists can preview shop templates without modifying them."""
    _require_staff(current_user)
    rendered = render_template(body.body, body.sample_data)
    return {
        "body": rendered,
        "length": len(rendered),
        "segments": _segment_count(rendered),
    }


class TemplateSendRequest(BaseModel):
    recipients: list[dict]
    variables: dict[str, str] = Field(default_factory=dict)


@router.post("/{slug}/send")
async def send_template_manual(
    slug: str,
    body: TemplateSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Staff+: send a template. Staff can only send their own templates or
    shop templates. Recipient validation (whether the artist can text this
    number) is enforced in the SMS service layer via opt-in checks."""
    _require_staff(current_user)
    result = await db.execute(select(SmsTemplate).where(SmsTemplate.slug == slug))
    tpl = result.scalar_one_or_none()
    if not tpl or not _can_read(current_user, tpl):
        raise HTTPException(status_code=404, detail="Template not found")
    if not tpl.is_active:
        raise HTTPException(status_code=400, detail="Template is disabled")

    from app.services.sms import send_sms

    results = []
    for recipient in body.recipients:
        phone = (recipient.get("phone") or "").strip()
        name = recipient.get("name", "")
        if not phone:
            continue
        vars_with_name = {**body.variables, "client_name": name, "name": name}
        rendered = render_template(tpl.body, vars_with_name)
        log = await send_sms(
            db=db,
            to=phone,
            body=rendered,
            template_slug=slug,
            sender_id=current_user.id,
            is_transactional=tpl.category != "marketing",
        )
        results.append(
            {
                "phone": phone,
                "sent": log.status != "failed",
                "status": log.status,
                "error": log.error_message,
            }
        )

    sent_count = sum(1 for r in results if r["sent"])
    return {"results": results, "sent": sent_count, "failed": len(results) - sent_count}


@router.post("/seed", status_code=200)
async def seed_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Founder only: upsert all hardcoded shop-level SMS templates."""
    if current_user.role != "founder":
        raise HTTPException(status_code=403, detail="Founder only")
    from app.services.sms_template_seeder import get_seed_templates

    templates = get_seed_templates()
    created = 0
    updated = 0
    for t in templates:
        existing_result = await db.execute(select(SmsTemplate).where(SmsTemplate.slug == t["slug"]))
        existing_tpl = existing_result.scalar_one_or_none()
        if existing_tpl:
            existing_tpl.name = t["name"]
            existing_tpl.category = t["category"]
            existing_tpl.body = t["body"]
            existing_tpl.variables = t["variables"]
            existing_tpl.description = t["description"]
            existing_tpl.updated_at = datetime.now(UTC)
            # Seeded templates are always shop-level (owner_id=None).
            existing_tpl.owner_id = None
            updated += 1
        else:
            db.add(
                SmsTemplate(
                    slug=t["slug"],
                    name=t["name"],
                    category=t["category"],
                    body=t["body"],
                    variables=t["variables"],
                    description=t["description"],
                    owner_id=None,
                )
            )
            created += 1
    await db.flush()
    return {"created": created, "updated": updated, "total": len(templates)}
