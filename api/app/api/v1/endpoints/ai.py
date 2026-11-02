"""
AI-powered content generation endpoints.

Uses Anthropic Claude to generate:
- Response templates (booking replies, follow-ups, etc.)
- Courier messages (client communications)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_role
from app.models.user import User

router = APIRouter(prefix="/ai", tags=["ai"])


class GenerateTemplateRequest(BaseModel):
    template_type: str  # e.g. "new_inquiry", "quote_followup", "decline"
    tone: str = "professional"  # professional, friendly, casual
    context: str = ""  # optional extra context from the artist


class GenerateMessageRequest(BaseModel):
    purpose: str  # e.g. "booking_reply", "follow_up", "reschedule"
    client_name: str = ""
    booking_desc: str = ""  # e.g. "neo-traditional wolf half-sleeve"
    tone: str = "professional"
    context: str = ""


class GenerateResponse(BaseModel):
    text: str


# Template type metadata for prompt construction
TEMPLATE_INFO = {
    "new_inquiry": {
        "label": "New Inquiry",
        "purpose": "First reply when a booking request comes in",
    },
    "quote_followup": {
        "label": "Quote Follow-up",
        "purpose": "Nudge a client who hasn't responded to a quote",
    },
    "booking_confirmed": {
        "label": "Booking Confirmed",
        "purpose": "Personal message when a session is locked in",
    },
    "day_before": {
        "label": "Day Before Reminder",
        "purpose": "Custom touch the day before the appointment",
    },
    "post_session": {
        "label": "Post Session Thanks",
        "purpose": "Personal thank-you after the tattoo session",
    },
    "healing_checkin": {
        "label": "Healing Check-in",
        "purpose": "2-week follow-up about healing progress",
    },
    "reschedule": {
        "label": "Reschedule",
        "purpose": "When a client needs to move their appointment date",
    },
    "waitlist": {
        "label": "Waitlist",
        "purpose": "When the artist is booked out but wants to keep the lead warm",
    },
    "decline": {
        "label": "Decline",
        "purpose": "Polite decline (not their style, schedule full, etc.)",
    },
}


def _require_gateway(s, current_user=None):
    """Guard: limited-release access, then the Ark Model Gateway key.

    Raises 403 for non-staff while AI is in limited release, 503 when unconfigured.
    """
    from app.services.ai_routing import require_ai_access

    require_ai_access(s, current_user)
    if not s.ARK_MODEL_GATEWAY_KEY:
        raise HTTPException(
            status_code=503,
            detail="AI generation not configured. Set NEBIUS_API_KEY.",
        )


@router.post("/generate-template", response_model=GenerateResponse)
async def generate_template(
    body: GenerateTemplateRequest,
    current_user: User = Depends(require_role("artist")),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Generate an AI-powered response template for an artist."""
    info = TEMPLATE_INFO.get(body.template_type)
    if not info:
        raise HTTPException(400, f"Unknown template type: {body.template_type}")

    from app.services.ai_routing import call_ark_gateway_text
    from app.services.api_budget import check_budget, log_usage

    s = get_settings()
    _require_gateway(s, current_user)
    await check_budget(db, s.ANTHROPIC_MONTHLY_BUDGET_CAP)

    # Build artist context from their profile
    artist_name = current_user.name or "the artist"
    specialties = ""
    if current_user.nexus_profile and current_user.nexus_profile.get("specialties"):
        specialties = f" who specializes in {', '.join(current_user.nexus_profile['specialties'])}"

    prompt = f"""Write a short, {body.tone} response template for a tattoo artist named {artist_name}{specialties} at {s.STUDIO_NAME}.

Template type: {info["label"]}
Purpose: {info["purpose"]}

Requirements:
- Use {{name}} as a placeholder for the client's first name
- Keep it concise (2-4 sentences max)
- Sound human and warm, not corporate or robotic
- Match the tone: {body.tone}
- Don't use emojis
- Don't include a subject line, just the message body
{f"- Additional context from the artist: {body.context}" if body.context else ""}

Write only the template text, nothing else."""

    text, usage = await call_ark_gateway_text(
        settings=s,
        system_prompt="You draft concise response templates for a tattoo studio. Write only the template text.",
        user_prompt=prompt,
        primary_model=s.ARK_GATEWAY_UTILITY_MODEL,
        fallback_models=[s.ARK_GATEWAY_UTILITY_FALLBACK_MODEL],
        max_tokens=300,
    )
    if not text:
        raise HTTPException(status_code=502, detail="AI returned an empty template")
    await log_usage(
        db,
        "ai/generate-template",
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        current_user.id,
    )

    return GenerateResponse(text=text)


@router.post("/generate-message", response_model=GenerateResponse)
async def generate_message(
    body: GenerateMessageRequest,
    current_user: User = Depends(require_role("artist")),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Generate an AI-powered message for client communication (Courier, booking replies)."""
    from app.services.ai_routing import call_ark_gateway_text
    from app.services.api_budget import check_budget, log_usage

    s = get_settings()
    _require_gateway(s, current_user)
    await check_budget(db, s.ANTHROPIC_MONTHLY_BUDGET_CAP)

    artist_name = current_user.name or "the artist"

    prompt = f"""Write a short, {body.tone} message from tattoo artist {artist_name} at {s.STUDIO_NAME}

Purpose: {body.purpose}
{f"Client name: {body.client_name}" if body.client_name else ""}
{f"Project: {body.booking_desc}" if body.booking_desc else ""}

Requirements:
- Keep it concise (2-4 sentences)
- Sound human and personal, not templated
- Match the tone: {body.tone}
- Don't use emojis
- Address the client by name if provided
{f"- Additional context: {body.context}" if body.context else ""}

Write only the message text, nothing else."""

    text, usage = await call_ark_gateway_text(
        settings=s,
        system_prompt="You draft concise client messages for a tattoo studio. Write only the message text.",
        user_prompt=prompt,
        primary_model=s.ARK_GATEWAY_UTILITY_MODEL,
        fallback_models=[s.ARK_GATEWAY_UTILITY_FALLBACK_MODEL],
        max_tokens=300,
    )
    if not text:
        raise HTTPException(status_code=502, detail="AI returned an empty message")
    await log_usage(
        db,
        "ai/generate-message",
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        current_user.id,
    )

    return GenerateResponse(text=text)
