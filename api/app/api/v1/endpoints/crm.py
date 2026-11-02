"""CRM pipeline tasks — minimal v1 for shop ops follow-ups."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.routes import ROUTES
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking import Booking
from app.models.crm_pipeline_task import CrmPipelineTask
from app.models.user import User
from app.schemas.crm import CrmPipelineTaskCreate, CrmPipelineTaskListResponse, CrmPipelineTaskRecord

logger = logging.getLogger("ryozan.crm")

router = APIRouter(prefix="/crm", tags=["crm"])


def _require_role(user: User, min_role: str) -> None:
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")


async def _task_record(row: CrmPipelineTask, db: AsyncSession) -> CrmPipelineTaskRecord:
    record = CrmPipelineTaskRecord.model_validate(row)
    if not row.booking_id:
        return record

    result = await db.execute(
        select(
            Booking.reference_code,
            Booking.guest_name,
            Booking.client_id,
            Booking.artist_id,
            Booking.conversation_id,
            User.name.label("artist_name"),
        )
        .join(User, User.id == Booking.artist_id)
        .where(Booking.id == row.booking_id)
    )
    booking = result.one_or_none()
    if not booking:
        return record

    reference_code, guest_name, _client_id, _artist_id, conversation_id, artist_name = booking
    return record.model_copy(
        update={
            "booking_reference_code": reference_code,
            "booking_client_name": guest_name,
            "booking_artist_name": artist_name,
            "conversation_id": conversation_id,
            "booking_url": ROUTES.BOOKING_REF.format(ref=reference_code) if reference_code else None,
            "courier_url": ROUTES.COURIER_CONVO.format(id=conversation_id) if conversation_id else None,
        }
    )


def _booking_task_body(booking: Booking, artist_name: str | None) -> str:
    reference_code = booking.reference_code or str(booking.id)
    lines = [
        f"Client: {booking.guest_name or 'Client'}",
        f"Email: {booking.guest_email or 'not provided'}",
        f"Phone: {booking.guest_phone or 'not provided'}",
        f"Artist: {artist_name or 'Artist'}",
        f"Reference: {reference_code}",
        f"Booking: {ROUTES.BOOKING_REF.format(ref=reference_code)}",
    ]
    if booking.conversation_id:
        lines.append(f"Courier: {ROUTES.COURIER_CONVO.format(id=booking.conversation_id)}")
    lines.extend(["", f"Project: {booking.description}"])
    if booking.placement:
        lines.append(f"Placement: {booking.placement}")
    if booking.size_estimate:
        lines.append(f"Size: {booking.size_estimate}")
    if booking.style_preference:
        lines.append(f"Style: {booking.style_preference}")
    if booking.budget:
        lines.append(f"Budget: {booking.budget}")
    if booking.timeline:
        lines.append(f"Timeline: {booking.timeline}")
    return "\n".join(lines)


async def _backfill_pending_booking_tasks(db: AsyncSession) -> None:
    result = await db.execute(
        select(Booking, User.name)
        .join(User, User.id == Booking.artist_id)
        .where(Booking.status == "pending")
        .order_by(desc(Booking.created_at))
        .limit(100)
    )
    for booking, artist_name in result.all():
        existing = await db.execute(
            select(CrmPipelineTask.id).where(
                CrmPipelineTask.booking_id == booking.id,
                CrmPipelineTask.status == "open",
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            CrmPipelineTask(
                title=f"Reply to booking {booking.reference_code or booking.id}",
                body=_booking_task_body(booking, artist_name),
                status="open",
                priority="high",
                booking_id=booking.id,
                assignee_id=booking.artist_id,
            )
        )
    await db.flush()


@router.get("/pipeline-tasks", response_model=CrmPipelineTaskListResponse)
async def list_pipeline_tasks(
    status_filter: str | None = Query(None, alias="status"),
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Staff: list CRM pipeline tasks (open shop follow-ups)."""
    _require_role(current_user, "assistant")
    if status_filter in (None, "open"):
        await _backfill_pending_booking_tasks(db)

    stmt = select(CrmPipelineTask)
    count_base = select(func.count()).select_from(CrmPipelineTask)
    if status_filter:
        stmt = stmt.where(CrmPipelineTask.status == status_filter)
        count_base = count_base.where(CrmPipelineTask.status == status_filter)

    total_result = await db.execute(count_base)
    total = int(total_result.scalar() or 0)

    stmt = stmt.order_by(desc(CrmPipelineTask.updated_at)).offset(skip).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return CrmPipelineTaskListResponse(tasks=[await _task_record(r, db) for r in rows], total=total)


@router.post("/pipeline-tasks", response_model=CrmPipelineTaskRecord)
async def create_pipeline_task(
    body: CrmPipelineTaskCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Staff: create a pipeline task."""
    _require_role(current_user, "assistant")
    row = CrmPipelineTask(
        title=body.title.strip(),
        body=body.body,
        status=body.status or "open",
        priority=body.priority,
        booking_id=body.booking_id,
        assignee_id=body.assignee_id or current_user.id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    logger.info("CRM pipeline task created %s by %s", row.id, current_user.id)
    return await _task_record(row, db)


@router.patch("/pipeline-tasks/{task_id}/status", response_model=CrmPipelineTaskRecord)
async def update_pipeline_task_status(
    task_id: UUID,
    status: str = Query(..., min_length=2, max_length=32),
    db: AsyncSession = Depends(get_db, scope="function"),
    current_user: User = Depends(get_current_user),
):
    """Staff: update task status (e.g. open -> done)."""
    _require_role(current_user, "assistant")
    result = await db.execute(select(CrmPipelineTask).where(CrmPipelineTask.id == task_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    row.status = status
    await db.flush()
    await db.refresh(row)
    return await _task_record(row, db)
