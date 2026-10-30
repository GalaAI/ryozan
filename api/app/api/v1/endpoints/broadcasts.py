"""
Broadcast API -- Herald feed + Gala feedback routing.

POST /broadcasts         -- founder/admin creates a broadcast
GET  /broadcasts         -- list broadcasts for current user's audience
POST /broadcasts/feedback -- submit Gala feedback (any role)
GET  /broadcasts/feedback -- list feedback (founder/admin only)
PATCH /broadcasts/feedback/{id} -- update feedback status (founder/admin only)
"""

from uuid import UUID as _UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.broadcast import Broadcast, GalaFeedback
from app.models.user import BTTCOUser

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])

STAFF_ROLES = {"admin", "founder"}


# ── Schemas ──


class BroadcastCreate(BaseModel):
    title: str
    body: str
    broadcast_type: str = "announcement"
    pinned: bool = False
    audience: str = "all"


class BroadcastOut(BaseModel):
    id: str
    author_id: str
    author_name: str | None = None
    title: str
    body: str
    broadcast_type: str
    pinned: bool
    audience: str
    created_at: str


class FeedbackCreate(BaseModel):
    message: str


class FeedbackOut(BaseModel):
    id: str
    user_id: str
    user_name: str | None = None
    user_email: str | None = None
    message: str
    status: str
    priority: int
    created_at: str


class FeedbackUpdate(BaseModel):
    status: str | None = None
    priority: int | None = None


class BroadcastPublishRequest(BaseModel):
    destinations: list[str] = ["facebook"]
    image_url: str | None = None


class BroadcastPublishResult(BaseModel):
    destination: str
    ok: bool
    post_id: str | None = None
    error: str | None = None


class BroadcastPublishResponse(BaseModel):
    broadcast_id: str
    results: list[BroadcastPublishResult]


# ── Broadcasts ──


@router.post("/", response_model=BroadcastOut)
async def create_broadcast(
    data: BroadcastCreate,
    user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in STAFF_ROLES:
        raise HTTPException(403, "Only admins and founders can create broadcasts")

    broadcast = Broadcast(
        author_id=user.id,
        title=data.title,
        body=data.body,
        broadcast_type=data.broadcast_type,
        pinned=data.pinned,
        audience=data.audience,
    )
    db.add(broadcast)
    await db.commit()
    await db.refresh(broadcast)

    return BroadcastOut(
        id=str(broadcast.id),
        author_id=str(broadcast.author_id),
        author_name=user.name,
        title=broadcast.title,
        body=broadcast.body,
        broadcast_type=broadcast.broadcast_type,
        pinned=broadcast.pinned,
        audience=broadcast.audience,
        created_at=broadcast.created_at.isoformat(),
    )


@router.get("/")
async def list_broadcasts(
    limit: int = 20,
    user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = user.role
    # Filter by audience: "all" or matching role group
    role_audience = "artists" if role in ("artist", "admin", "founder") else "collectors"

    stmt = (
        select(Broadcast, BTTCOUser.name.label("author_name"))
        .join(BTTCOUser, Broadcast.author_id == BTTCOUser.id)
        .where(
            or_(
                Broadcast.audience == "all",
                Broadcast.audience == role_audience,
                Broadcast.audience == role,
            )
        )
        .order_by(Broadcast.pinned.desc(), Broadcast.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    return {
        "broadcasts": [
            BroadcastOut(
                id=str(b.id),
                author_id=str(b.author_id),
                author_name=name,
                title=b.title,
                body=b.body,
                broadcast_type=b.broadcast_type,
                pinned=b.pinned,
                audience=b.audience,
                created_at=b.created_at.isoformat(),
            )
            for b, name in rows
        ]
    }


@router.post("/{broadcast_id}/publish", response_model=BroadcastPublishResponse)
async def publish_broadcast(
    broadcast_id: str,
    data: BroadcastPublishRequest,
    user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in STAFF_ROLES:
        raise HTTPException(403, "Only admins and founders can publish broadcasts")

    stmt = select(Broadcast).where(Broadcast.id == _UUID(broadcast_id))
    result = await db.execute(stmt)
    broadcast = result.scalar_one_or_none()
    if not broadcast:
        raise HTTPException(404, "Broadcast not found")

    message = f"{broadcast.title}\n\n{broadcast.body}".strip()
    settings = get_settings()
    results: list[BroadcastPublishResult] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for raw_destination in data.destinations:
            destination = raw_destination.strip().lower()
            if destination == "facebook":
                results.append(await _publish_facebook(client, settings, message))
            elif destination == "instagram":
                results.append(await _publish_instagram(client, settings, message, data.image_url))
            else:
                results.append(
                    BroadcastPublishResult(destination=destination, ok=False, error="Unsupported destination")
                )

    return BroadcastPublishResponse(
        broadcast_id=str(broadcast.id),
        results=results,
    )


async def _publish_facebook(client: httpx.AsyncClient, settings, message: str) -> BroadcastPublishResult:
    if not settings.META_FACEBOOK_PAGE_ID or not settings.META_FACEBOOK_PAGE_ACCESS_TOKEN:
        return BroadcastPublishResult(destination="facebook", ok=False, error="Facebook publishing is not configured")

    url = f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{settings.META_FACEBOOK_PAGE_ID}/feed"
    try:
        resp = await client.post(
            url,
            data={
                "message": message,
                "access_token": settings.META_FACEBOOK_PAGE_ACCESS_TOKEN,
            },
        )
        if resp.status_code in (200, 201):
            return BroadcastPublishResult(destination="facebook", ok=True, post_id=resp.json().get("id"))
        return BroadcastPublishResult(destination="facebook", ok=False, error=_meta_error(resp))
    except Exception as exc:
        return BroadcastPublishResult(destination="facebook", ok=False, error=str(exc)[:200])


async def _publish_instagram(
    client: httpx.AsyncClient,
    settings,
    caption: str,
    image_url: str | None,
) -> BroadcastPublishResult:
    if not settings.META_INSTAGRAM_ACCOUNT_ID or not settings.META_INSTAGRAM_PAGE_ACCESS_TOKEN:
        return BroadcastPublishResult(destination="instagram", ok=False, error="Instagram publishing is not configured")
    if not image_url:
        return BroadcastPublishResult(destination="instagram", ok=False, error="Instagram requires an image URL")

    base = f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{settings.META_INSTAGRAM_ACCOUNT_ID}"
    try:
        create_resp = await client.post(
            f"{base}/media",
            data={
                "image_url": image_url,
                "caption": caption,
                "access_token": settings.META_INSTAGRAM_PAGE_ACCESS_TOKEN,
            },
        )
        if create_resp.status_code not in (200, 201):
            return BroadcastPublishResult(destination="instagram", ok=False, error=_meta_error(create_resp))

        creation_id = create_resp.json().get("id")
        publish_resp = await client.post(
            f"{base}/media_publish",
            data={
                "creation_id": creation_id,
                "access_token": settings.META_INSTAGRAM_PAGE_ACCESS_TOKEN,
            },
        )
        if publish_resp.status_code in (200, 201):
            return BroadcastPublishResult(destination="instagram", ok=True, post_id=publish_resp.json().get("id"))
        return BroadcastPublishResult(destination="instagram", ok=False, error=_meta_error(publish_resp))
    except Exception as exc:
        return BroadcastPublishResult(destination="instagram", ok=False, error=str(exc)[:200])


def _meta_error(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return f"Meta {resp.status_code}: {resp.text[:200]}"

    message = payload.get("error", {}).get("message") or resp.text[:200]
    return f"Meta {resp.status_code}: {message[:200]}"


# ── Gala Feedback ──


@router.post("/feedback", response_model=FeedbackOut)
async def submit_feedback(
    data: FeedbackCreate,
    user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    feedback = GalaFeedback(
        user_id=user.id,
        message=data.message,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)

    return FeedbackOut(
        id=str(feedback.id),
        user_id=str(feedback.user_id),
        user_name=user.name,
        user_email=user.email,
        message=feedback.message,
        status=feedback.status,
        priority=feedback.priority,
        created_at=feedback.created_at.isoformat(),
    )


@router.get("/feedback")
async def list_feedback(
    status: str | None = None,
    limit: int = 50,
    user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in STAFF_ROLES:
        raise HTTPException(403, "Only admins and founders can view feedback")

    stmt = (
        select(GalaFeedback, BTTCOUser.name.label("user_name"), BTTCOUser.email.label("user_email"))
        .join(BTTCOUser, GalaFeedback.user_id == BTTCOUser.id)
        .order_by(GalaFeedback.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(GalaFeedback.status == status)

    result = await db.execute(stmt)
    rows = result.all()

    return {
        "feedback": [
            FeedbackOut(
                id=str(f.id),
                user_id=str(f.user_id),
                user_name=name,
                user_email=email,
                message=f.message,
                status=f.status,
                priority=f.priority,
                created_at=f.created_at.isoformat(),
            )
            for f, name, email in rows
        ]
    }


@router.patch("/feedback/{feedback_id}")
async def update_feedback(
    feedback_id: str,
    data: FeedbackUpdate,
    user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in STAFF_ROLES:
        raise HTTPException(403, "Only admins and founders can update feedback")

    stmt = select(GalaFeedback).where(GalaFeedback.id == _UUID(feedback_id))
    result = await db.execute(stmt)
    feedback = result.scalar_one_or_none()

    if not feedback:
        raise HTTPException(404, "Feedback not found")

    if data.status is not None:
        feedback.status = data.status
    if data.priority is not None:
        feedback.priority = data.priority

    await db.commit()
    return {"ok": True}
