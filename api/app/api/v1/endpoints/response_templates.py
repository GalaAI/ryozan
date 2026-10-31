"""
Response template endpoints - staff-managed quick-reply library.

GET    /response-templates/        - list all (assistant+)
POST   /response-templates/        - create (assistant+)
PUT    /response-templates/{id}    - edit (assistant+ - own or founder)
DELETE /response-templates/{id}    - delete (assistant+ - own or founder)
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role
from app.models.response_template import ResponseTemplate
from app.models.user import User

router = APIRouter(prefix="/response-templates", tags=["response-templates"])


class TemplateIn(BaseModel):
    title: str
    body: str
    sort_order: int = 0


class TemplateUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    sort_order: int | None = None


def _serialize(t: ResponseTemplate) -> dict:
    return {
        "id": str(t.id),
        "title": t.title,
        "body": t.body,
        "sort_order": t.sort_order,
        "created_by": str(t.created_by) if t.created_by else None,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


@router.get("/")
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("assistant")),
):
    result = await db.execute(
        select(ResponseTemplate).order_by(ResponseTemplate.sort_order, ResponseTemplate.created_at)
    )
    return [_serialize(t) for t in result.scalars().all()]


@router.post("/", status_code=201)
async def create_template(
    body: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("assistant")),
):
    t = ResponseTemplate(
        title=body.title.strip(),
        body=body.body.strip(),
        sort_order=body.sort_order,
        created_by=current_user.id,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _serialize(t)


@router.put("/{template_id}")
async def update_template(
    template_id: uuid.UUID,
    body: TemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("assistant")),
):
    result = await db.execute(select(ResponseTemplate).where(ResponseTemplate.id == template_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")

    # Only founder/admin can edit others' templates
    is_privileged = current_user.role in {"founder", "admin"}
    if not is_privileged and t.created_by != current_user.id:
        raise HTTPException(403, "You can only edit your own templates")

    if body.title is not None:
        t.title = body.title.strip()
    if body.body is not None:
        t.body = body.body.strip()
    if body.sort_order is not None:
        t.sort_order = body.sort_order
    t.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(t)
    return _serialize(t)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("assistant")),
):
    result = await db.execute(select(ResponseTemplate).where(ResponseTemplate.id == template_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")

    is_privileged = current_user.role in {"founder", "admin"}
    if not is_privileged and t.created_by != current_user.id:
        raise HTTPException(403, "You can only delete your own templates")

    await db.delete(t)
    await db.commit()
