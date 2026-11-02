"""
Email template CRUD — founder/admin only.

Templates are stored in the DB with {{variable}} placeholders.
The email service renders them at send-time with real data.
This endpoint provides listing, editing, previewing, and
seeding the initial templates from the hardcoded originals.
"""

import html
import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.email_template import EmailTemplate
from app.models.user import User
from app.services.email_foundry import (
    FOUNDRY_CONNECTORS_KEY,
    FOUNDRY_OUTBOX_KEY,
    apply_foundry_connector_action,
    decode_foundry_unsubscribe_token,
    foundry_connector_inventory,
    foundry_state_for_user,
    merge_foundry_outbox,
    process_foundry_outbox,
    render_foundry_email,
    store_foundry_state,
    validate_foundry_state_value,
)

logger = logging.getLogger("ryozan.email_templates")
router = APIRouter(prefix="/email-templates", tags=["email-templates"])

# ── Schemas ──────────────────────────────────────────


class TemplateResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    subject: str
    category: str
    html_body: str
    variables: list[dict]
    is_active: bool
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TemplateUpdate(BaseModel):
    name: str | None = None
    subject: str | None = None
    html_body: str | None = None
    is_active: bool | None = None
    description: str | None = None


class TemplatePreview(BaseModel):
    html_body: str
    sample_data: dict[str, str] = Field(default_factory=dict)


class FoundryAssistMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class FoundryAssistRequest(BaseModel):
    system: str = Field(min_length=1, max_length=12_000)
    messages: list[FoundryAssistMessage] = Field(min_length=1, max_length=8)
    max_tokens: int = Field(default=2_000, ge=64, le=2_500)


# ── Helpers ──────────────────────────────────────────


def _require_admin(user: User) -> None:
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Admin role or higher required")


def render_template(html: str, data: dict[str, str]) -> str:
    """Replace {{variable}} placeholders with actual values."""

    def replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        return data.get(key, match.group(0))

    return re.sub(r"\{\{(\s*\w+\s*)\}\}", replacer, html)


# ── Endpoints ────────────────────────────────────────


@router.get("/foundry/state")
async def get_foundry_state(
    current_user: User = Depends(get_current_user),
):
    """Admin+: hydrate the locked Email Foundry dependency state."""
    _require_admin(current_user)
    return foundry_state_for_user(current_user)


@router.put("/foundry/state/{key}")
async def put_foundry_state(
    key: str,
    value: Any = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin+: persist one exact STORE_KEYS value from Email Foundry."""
    _require_admin(current_user)
    try:
        value = validate_foundry_state_value(key, value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    foundry_state = foundry_state_for_user(current_user)
    if key == FOUNDRY_OUTBOX_KEY:
        value = merge_foundry_outbox(foundry_state[FOUNDRY_OUTBOX_KEY], value)
    foundry_state[key] = value
    store_foundry_state(current_user, foundry_state)
    await db.flush()

    if key == FOUNDRY_OUTBOX_KEY:
        await process_foundry_outbox(db, current_user, foundry_state)
        store_foundry_state(current_user, foundry_state)
        await db.flush()

    return {"key": key, "saved": True, "value": foundry_state[key], "state": foundry_state}


# Provider credentials already belong to Ryozan, but no founder-scoped control surface existed for
# attaching those providers to Courier. These routes extend the existing Foundry state boundary and
# expose only booleans plus probe receipts, never the underlying credential values.
@router.get("/foundry/connectors")
async def get_foundry_connectors(
    current_user: User = Depends(get_current_user),
):
    """Founder: project provider configuration and Courier binding state."""
    if current_user.role != "founder":
        raise HTTPException(status_code=403, detail="Founder role required")
    return {"connectors": foundry_connector_inventory(foundry_state_for_user(current_user))}


@router.post("/foundry/connectors/{connector_id}/{action}")
async def update_foundry_connector(
    connector_id: str,
    action: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Founder: connect, reconnect, disconnect, or test one Ryozan-owned provider binding."""
    if current_user.role != "founder":
        raise HTTPException(status_code=403, detail="Founder role required")
    state = foundry_state_for_user(current_user)
    try:
        result = await apply_foundry_connector_action(state, connector_id, action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state[FOUNDRY_CONNECTORS_KEY] = state.get(FOUNDRY_CONNECTORS_KEY, {})
    store_foundry_state(current_user, state)
    await db.flush()
    return result


@router.post("/foundry/outbox/{entry_id}/approve")
async def approve_foundry_outbox(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Founder: approve one held list send, execute it, and return its receipt."""
    if current_user.role != "founder":
        raise HTTPException(status_code=403, detail="Founder approval required")

    state = foundry_state_for_user(current_user)
    row = next((entry for entry in state[FOUNDRY_OUTBOX_KEY] if str(entry.get("id")) == entry_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Foundry outbox entry not found")
    if row.get("kind") != "manual" or row.get("status") != "held for sign-off":
        raise HTTPException(status_code=409, detail="Only a held manual list send can be approved")

    row["status"] = "approved"
    row["approvedAt"] = datetime.now(UTC).isoformat()
    row["approvedBy"] = str(current_user.id)
    store_foundry_state(current_user, state)
    await db.flush()
    await process_foundry_outbox(db, current_user, state, only_entry_ids={entry_id})
    store_foundry_state(current_user, state)
    await db.flush()
    return {"entry": row, "state": state}


@router.post("/foundry/assist")
async def assist_foundry(
    body: FoundryAssistRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin+: execute the locked Herald drafting control through Ark's model gateway."""
    _require_admin(current_user)

    from app.core.config import get_settings
    from app.services.ai_routing import call_ark_gateway_chat_completion, require_ai_access
    from app.services.api_budget import check_budget, log_usage

    settings = get_settings()
    require_ai_access(settings, current_user)
    if not settings.ARK_MODEL_GATEWAY_KEY:
        raise HTTPException(status_code=503, detail="AI generation is not configured")
    await check_budget(db, settings.ANTHROPIC_MONTHLY_BUDGET_CAP)
    completion = await call_ark_gateway_chat_completion(
        settings=settings,
        system_prompt=body.system,
        messages=[message.model_dump() for message in body.messages],
        tools=[],
        primary_model=settings.ARK_GATEWAY_UTILITY_MODEL,
        fallback_models=[settings.ARK_GATEWAY_UTILITY_FALLBACK_MODEL],
        max_tokens=body.max_tokens,
    )
    choice = ((completion.get("choices") or [{}])[0]).get("message") or {}
    text = str(choice.get("content") or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="Herald returned no usable draft")
    usage = completion.get("usage") or {}
    await log_usage(
        db,
        "email-foundry/herald",
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        current_user.id,
    )
    return {"text": text}


def _unsubscribe_page(*, body: str, token: str | None = None, status_code: int = 200) -> HTMLResponse:
    action = ""
    if token:
        action = f"""
        <form method="post" action="/api/v1/email-templates/foundry/unsubscribe">
          <input type="hidden" name="token" value="{html.escape(token, quote=True)}">
          <button type="submit">Unsubscribe me</button>
        </form>"""
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Email preferences</title><style>body{{margin:0;background:#0b0c0b;color:#e8dfcf;font:16px/1.6 system-ui,sans-serif}}main{{max-width:560px;margin:12vh auto;padding:32px;border:1px solid #34322e;border-radius:16px;background:#151715}}h1{{font-size:28px}}button{{padding:12px 18px;border:0;border-radius:999px;background:#d38a68;color:#29140e;font-weight:700;cursor:pointer}}</style></head><body><main><h1>Email preferences</h1><p>{html.escape(body)}</p>{action}</main></body></html>"""
    return HTMLResponse(document, status_code=status_code, headers={"cache-control": "no-store"})


@router.get("/foundry/unsubscribe", response_class=HTMLResponse)
async def confirm_foundry_unsubscribe(token: str = Query(...)):
    """Show a confirmation step so email link scanners cannot unsubscribe a client."""
    try:
        decode_foundry_unsubscribe_token(token)
    except ValueError:
        return _unsubscribe_page(body="This unsubscribe link is invalid.", status_code=400)
    from app.core.config import get_settings

    return _unsubscribe_page(body=f"Stop marketing emails from {get_settings().STUDIO_NAME}?", token=token)


@router.post("/foundry/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_foundry(
    token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Apply the signed marketing opt-out without accepting a login token."""
    try:
        user_id = decode_foundry_unsubscribe_token(token)
    except ValueError:
        return _unsubscribe_page(body="This unsubscribe link is invalid.", status_code=400)
    user = await db.get(User, user_id)
    if user:
        user.marketing_opt_in = False
        await db.flush()
    from app.core.config import get_settings

    return _unsubscribe_page(body=f"You are unsubscribed from {get_settings().STUDIO_NAME} marketing emails.")


@router.get("/", response_model=list[TemplateResponse])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin+: list all email templates."""
    _require_admin(current_user)
    result = await db.execute(select(EmailTemplate).order_by(EmailTemplate.category, EmailTemplate.name))
    return result.scalars().all()


@router.get("/{slug}", response_model=TemplateResponse)
async def get_template(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin+: get a single template by slug."""
    _require_admin(current_user)
    result = await db.execute(select(EmailTemplate).where(EmailTemplate.slug == slug))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.patch("/{slug}", response_model=TemplateResponse)
async def update_template(
    slug: str,
    body: TemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin+: update a template's content, subject, or status."""
    _require_admin(current_user)
    result = await db.execute(select(EmailTemplate).where(EmailTemplate.slug == slug))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(template, field, value)
    template.updated_at = datetime.now(UTC)
    await db.flush()
    return template


@router.post("/preview")
async def preview_template(
    body: TemplatePreview,
    current_user: User = Depends(get_current_user),
):
    """Admin+: render a template with sample data and return the HTML."""
    _require_admin(current_user)
    rendered = render_template(body.html_body, body.sample_data)
    return {"html": rendered}


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
    """Admin+: manually send the locked Foundry template when available."""
    _require_admin(current_user)
    foundry_template = render_foundry_email(slug, {})
    template = None
    if not foundry_template:
        result = await db.execute(select(EmailTemplate).where(EmailTemplate.slug == slug))
        template = result.scalar_one_or_none()
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

    from app.services.email import send_email

    results = []
    for recipient in body.recipients:
        to_email = recipient.get("email", "")
        name = recipient.get("name", "")
        if not to_email:
            continue
        vars_with_name = {**body.variables, "client_name": name, "name": name}
        rendered = render_foundry_email(slug, vars_with_name)
        if rendered:
            html = rendered.html_body
            subject = rendered.subject
        else:
            html = render_template(template.html_body, vars_with_name)
            subject = render_template(template.subject, vars_with_name)
        ok = await send_email(
            to=to_email,
            subject=subject,
            html_body=html,
            tags=[slug],
            sender_id=str(current_user.id),
            sender_role=current_user.role,
        )
        results.append({"email": to_email, "sent": ok})

    sent_count = sum(1 for r in results if r["sent"])
    return {"results": results, "sent": sent_count, "failed": len(results) - sent_count}


@router.post("/seed", status_code=200)
async def seed_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Founder only: seed all hardcoded email templates into the DB.
    Updates existing templates in-place (upsert). Safe to re-run.
    """
    if current_user.role != "founder":
        raise HTTPException(status_code=403, detail="Founder only")

    from app.services.email_template_seeder import get_seed_templates

    templates = get_seed_templates()
    created = 0
    updated = 0

    for t in templates:
        existing_result = await db.execute(select(EmailTemplate).where(EmailTemplate.slug == t["slug"]))
        existing_tpl = existing_result.scalar_one_or_none()
        if existing_tpl:
            existing_tpl.name = t["name"]
            existing_tpl.subject = t["subject"]
            existing_tpl.category = t["category"]
            existing_tpl.html_body = t["html_body"]
            existing_tpl.variables = t["variables"]
            existing_tpl.description = t["description"]
            updated += 1
        else:
            db.add(
                EmailTemplate(
                    slug=t["slug"],
                    name=t["name"],
                    subject=t["subject"],
                    category=t["category"],
                    html_body=t["html_body"],
                    variables=t["variables"],
                    description=t["description"],
                )
            )
            created += 1

    await db.flush()
    return {"created": created, "updated": updated, "total": len(templates)}
