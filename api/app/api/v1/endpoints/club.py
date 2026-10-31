"""
Club endpoints - Community feed for the Club Herald.

POST   /club/posts          Create a new post (any authenticated user)
GET    /club/posts           List posts (paginated, newest first)
POST   /club/posts/{id}/like Toggle like on a post
DELETE /club/posts/{id}      Delete own post (or admin)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.club import ClubPost, ClubPostLike
from app.models.user import User

router = APIRouter(prefix="/club", tags=["club"])

ROLE_RANK = {"collector": 1, "assistant": 2, "artist": 2, "admin": 3, "founder": 4}


# Schemas


class ClubPostCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)
    post_type: str = Field(default="general")
    media_url: str | None = None


class ClubPostResponse(BaseModel):
    id: UUID
    author_id: UUID
    author_name: str | None = None
    author_avatar: str | None = None
    body: str
    media_url: str | None
    post_type: str
    likes_count: int
    is_liked: bool = False
    created_at: str

    class Config:
        from_attributes = True


class ClubPostListResponse(BaseModel):
    posts: list[ClubPostResponse]
    total: int


async def _enrich_post(post: ClubPost, db: AsyncSession, current_user_id: UUID) -> ClubPostResponse:
    """Add author info and like status to a post."""
    author = await db.get(User, post.author_id)

    liked = await db.execute(
        select(ClubPostLike.id).where(and_(ClubPostLike.post_id == post.id, ClubPostLike.user_id == current_user_id))
    )

    return ClubPostResponse(
        id=post.id,
        author_id=post.author_id,
        author_name=author.name if author else None,
        author_avatar=author.avatar_url if author else None,
        body=post.body,
        media_url=post.media_url,
        post_type=post.post_type,
        likes_count=post.likes_count,
        is_liked=liked.scalar_one_or_none() is not None,
        created_at=post.created_at.isoformat(),
    )


@router.post("/posts", response_model=ClubPostResponse, status_code=201)
async def create_post(
    data: ClubPostCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new club post. Any authenticated user."""
    post = ClubPost(
        author_id=current_user.id,
        body=data.body,
        post_type=data.post_type,
        media_url=data.media_url,
    )
    db.add(post)
    await db.flush()

    await db.commit()
    await db.refresh(post)
    return await _enrich_post(post, db, current_user.id)


@router.get("/posts", response_model=ClubPostListResponse)
async def list_posts(
    skip: int = 0,
    limit: int = Query(default=20, le=50),
    post_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List club posts, newest first."""
    # `not ClubPost.is_deleted` looks right and is not. Python's `not` calls
    # __bool__ on the SQLAlchemy column, which returns True, so the whole
    # expression collapses to the literal False and every query becomes
    # `WHERE false`. The feed has been returning zero posts for every user
    # regardless of what was in the table. The column is NOT NULL, so an
    # explicit is_(False) is exact.
    filters = [ClubPost.is_deleted.is_(False)]
    if post_type:
        filters.append(ClubPost.post_type == post_type)

    where = and_(*filters) if filters else True

    count_result = await db.execute(select(func.count()).select_from(ClubPost).where(where))
    total = count_result.scalar()

    result = await db.execute(
        select(ClubPost)
        .where(where)
        .order_by(desc(ClubPost.is_pinned), desc(ClubPost.created_at))
        .offset(skip)
        .limit(limit)
    )
    posts = result.scalars().all()

    enriched = [await _enrich_post(p, db, current_user.id) for p in posts]
    return ClubPostListResponse(posts=enriched, total=total)


@router.post("/posts/{post_id}/like", status_code=200)
async def toggle_like(
    post_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle like on a post. Returns new like count."""
    post = await db.get(ClubPost, post_id)
    if not post or post.is_deleted:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = await db.execute(
        select(ClubPostLike).where(and_(ClubPostLike.post_id == post_id, ClubPostLike.user_id == current_user.id))
    )
    like = existing.scalar_one_or_none()

    if like:
        await db.delete(like)
        post.likes_count = max(0, post.likes_count - 1)
    else:
        db.add(ClubPostLike(post_id=post_id, user_id=current_user.id))
        post.likes_count += 1

    await db.commit()
    return {"likes_count": post.likes_count, "is_liked": like is None}


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(
    post_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-delete a post. Author or admin+."""
    post = await db.get(ClubPost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    is_admin = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)
    if post.author_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    post.is_deleted = True
    await db.commit()
