"""
AI Memory endpoints - CRUD + context injection for Gala/Smoku agents.

Provides persistent memory storage so agents remember user preferences,
history, and context across conversations.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.ai_memory import AIMemory
from app.models.booking import Booking
from app.models.user import BTTCOUser
from app.schemas.ai_memory import (
    MemoryContext,
    MemoryCreate,
    MemoryListResponse,
    MemoryResponse,
    MemoryUpdate,
)

router = APIRouter(prefix="/ai/memories", tags=["ai-memories"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_to_response(m: AIMemory) -> MemoryResponse:
    return MemoryResponse(
        id=m.id,
        user_id=m.user_id,
        agent=m.agent,
        category=m.category,
        key=m.key,
        value=m.value,
        confidence=m.confidence,
        source=m.source,
        created_at=m.created_at,
        updated_at=m.updated_at,
        expires_at=m.expires_at,
    )


async def _artist_has_client_relationship(
    db: AsyncSession,
    artist_id: UUID,
    client_id: UUID,
) -> bool:
    """Return whether the artist has a booking assigned to this client."""
    result = await db.execute(
        select(Booking.id)
        .where(
            Booking.artist_id == artist_id,
            Booking.client_id == client_id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# LIST — current user's memories
# ---------------------------------------------------------------------------


@router.get("/", response_model=MemoryListResponse)
async def list_memories(
    agent: str | None = None,
    category: str | None = None,
    skip: int = 0,
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """List memories for the current user, filterable by agent and category."""
    filters = [AIMemory.user_id == current_user.id]
    if agent:
        filters.append(AIMemory.agent == agent)
    if category:
        filters.append(AIMemory.category == category)

    where = and_(*filters)

    count_result = await db.execute(select(func.count()).select_from(AIMemory).where(where))
    total = count_result.scalar()

    result = await db.execute(
        select(AIMemory).where(where).order_by(desc(AIMemory.updated_at)).offset(skip).limit(limit)
    )
    memories = result.scalars().all()

    return MemoryListResponse(
        memories=[_memory_to_response(m) for m in memories],
        total=total,
    )


# ---------------------------------------------------------------------------
# CONTEXT — formatted memory block for agent system prompts (internal use)
# ---------------------------------------------------------------------------


@router.get("/context/{user_id}", response_model=MemoryContext)
async def get_memory_context(
    user_id: UUID,
    agent: str = "gala",
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Return a formatted text block of memories for a user, suitable for
    injection into an agent system prompt.
    """
    can_access = current_user.id == user_id or current_user.role in {"admin", "founder"}
    if not can_access and current_user.role == "artist":
        can_access = await _artist_has_client_relationship(db, current_user.id, user_id)
    if not can_access:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view this user's AI memory context",
        )

    # Look up the target user's name
    user_result = await db.execute(select(BTTCOUser).where(BTTCOUser.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Fetch non-expired memories
    now = datetime.now(UTC)
    result = await db.execute(
        select(AIMemory)
        .where(
            and_(
                AIMemory.user_id == user_id,
                AIMemory.agent == agent,
            )
        )
        .order_by(AIMemory.category, desc(AIMemory.confidence))
    )
    all_memories = result.scalars().all()

    # Filter out expired
    memories = [m for m in all_memories if m.expires_at is None or m.expires_at > now]

    if not memories:
        return MemoryContext(
            user_id=user_id,
            agent=agent,
            context_text=f"## Known Information About {target_user.name}\nNo stored memories yet.",
            memory_count=0,
        )

    # Group by category
    grouped: dict[str, list[AIMemory]] = {}
    for m in memories:
        grouped.setdefault(m.category, []).append(m)

    lines = [f"## Known Information About {target_user.name}"]
    for cat, items in grouped.items():
        for item in items:
            lines.append(f"- {cat.title()}: {item.value} (confidence: {item.confidence}, source: {item.source})")

    return MemoryContext(
        user_id=user_id,
        agent=agent,
        context_text="\n".join(lines),
        memory_count=len(memories),
    )


# ---------------------------------------------------------------------------
# CREATE / UPSERT
# ---------------------------------------------------------------------------


@router.post("/", response_model=MemoryResponse, status_code=201)
async def create_or_upsert_memory(
    body: MemoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Create a new memory or update an existing one if the
    (user_id, agent, category, key) combination already exists.
    """
    # Check for existing
    result = await db.execute(
        select(AIMemory).where(
            and_(
                AIMemory.user_id == current_user.id,
                AIMemory.agent == body.agent,
                AIMemory.category == body.category,
                AIMemory.key == body.key,
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = body.value
        existing.confidence = body.confidence
        existing.source = body.source
        existing.updated_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(existing)
        return _memory_to_response(existing)

    memory = AIMemory(
        user_id=current_user.id,
        agent=body.agent,
        category=body.category,
        key=body.key,
        value=body.value,
        confidence=body.confidence,
        source=body.source,
    )
    db.add(memory)
    await db.flush()
    await db.refresh(memory)
    return _memory_to_response(memory)


# ---------------------------------------------------------------------------
# EXTRACT — use Anthropic to pull facts from a text blob
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    text: str
    agent: str = "gala"


class ExtractResponse(BaseModel):
    extracted: list[MemoryResponse]
    count: int


@router.post("/extract", response_model=ExtractResponse)
async def extract_memories(
    body: ExtractRequest,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(require_role("artist")),
):
    """
    Extract factual memories from a text blob using Anthropic Claude.
    Automatically upserts extracted facts into the current user's memory store.
    """
    from app.services.ai_routing import call_ark_gateway_text, require_ai_access
    from app.services.api_budget import check_budget, log_usage

    s = get_settings()
    require_ai_access(s, current_user)
    if not s.ARK_MODEL_GATEWAY_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI extraction not configured. Set NEBIUS_API_KEY.",
        )
    await check_budget(db, s.ANTHROPIC_MONTHLY_BUDGET_CAP)

    prompt = f"""Analyze the following text about a tattoo client and extract discrete facts.
Return ONLY a JSON array of objects, each with these fields:
- "category": one of "style", "health", "history", "preference", "personal", "contact"
- "key": short identifier (e.g. "preferred_style", "allergy", "last_session")
- "value": the fact as a concise sentence
- "confidence": float 0.0-1.0 based on how explicit the fact is

Text:
{body.text}

Return ONLY valid JSON, no markdown fencing, no explanation."""

    raw_text, usage = await call_ark_gateway_text(
        settings=s,
        system_prompt="You extract structured client facts and return only valid JSON.",
        user_prompt=prompt,
        primary_model=s.ARK_GATEWAY_UTILITY_MODEL,
        fallback_models=[s.ARK_GATEWAY_UTILITY_FALLBACK_MODEL],
        max_tokens=1000,
    )
    await log_usage(
        db,
        "ai_memories/extract",
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        current_user.id,
    )

    import json

    try:
        facts = json.loads(raw_text)
    except json.JSONDecodeError:
        raise HTTPException(400, "AI returned invalid JSON. Try with clearer text.") from None

    if not isinstance(facts, list):
        raise HTTPException(400, "AI returned non-array response.")

    extracted = []
    for fact in facts:
        cat = fact.get("category", "general")
        key = fact.get("key", "unknown")
        value = fact.get("value", "")
        confidence = min(max(float(fact.get("confidence", 0.5)), 0.0), 1.0)

        if not value:
            continue

        # Upsert
        result = await db.execute(
            select(AIMemory).where(
                and_(
                    AIMemory.user_id == current_user.id,
                    AIMemory.agent == body.agent,
                    AIMemory.category == cat,
                    AIMemory.key == key,
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = value
            existing.confidence = confidence
            existing.source = "extracted"
            existing.updated_at = datetime.now(UTC)
            await db.flush()
            await db.refresh(existing)
            extracted.append(_memory_to_response(existing))
        else:
            memory = AIMemory(
                user_id=current_user.id,
                agent=body.agent,
                category=cat,
                key=key,
                value=value,
                confidence=confidence,
                source="extracted",
            )
            db.add(memory)
            await db.flush()
            await db.refresh(memory)
            extracted.append(_memory_to_response(memory))

    return ExtractResponse(extracted=extracted, count=len(extracted))


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: UUID,
    body: MemoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Update a specific memory belonging to the current user."""
    result = await db.execute(
        select(AIMemory).where(and_(AIMemory.id == memory_id, AIMemory.user_id == current_user.id))
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    if body.value is not None:
        memory.value = body.value
    if body.confidence is not None:
        memory.confidence = body.confidence
    if body.source is not None:
        memory.source = body.source
    if body.expires_at is not None:
        memory.expires_at = body.expires_at

    memory.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(memory)
    return _memory_to_response(memory)


# ---------------------------------------------------------------------------
# DELETE — single memory
# ---------------------------------------------------------------------------


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """Delete a single memory belonging to the current user."""
    result = await db.execute(
        select(AIMemory).where(and_(AIMemory.id == memory_id, AIMemory.user_id == current_user.id))
    )
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    await db.delete(memory)
    await db.flush()


# ---------------------------------------------------------------------------
# DELETE — clear all memories for a user (admin only)
# ---------------------------------------------------------------------------


@router.delete("/user/{user_id}", status_code=204)
async def clear_user_memories(
    user_id: UUID,
    agent: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(require_role("admin")),
):
    """Clear all memories for a specific user. Admin only."""
    filters = [AIMemory.user_id == user_id]
    if agent:
        filters.append(AIMemory.agent == agent)

    await db.execute(delete(AIMemory).where(and_(*filters)))
    await db.flush()
