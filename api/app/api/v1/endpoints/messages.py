"""
Per-message endpoints, primarily the Interactive Message Artifact
action dispatcher (`POST /messages/{id}/artifact-action`).

Conversations + message sending still live in conversations.py; this
module is just the artifact state-machine surface. Separating it
keeps conversations.py focused on conversation CRUD and gives future
artifact features (webhooks, batch actions, etc.) a natural home.
"""

from __future__ import annotations

import logging
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.message import ConversationParticipant, Message
from app.models.user import BTTCOUser  # noqa: TC001
from app.schemas.message import ArtifactActionRequest, ArtifactActionResponse
from app.services import artifacts

logger = logging.getLogger("bttco.messages")
router = APIRouter(prefix="/messages", tags=["messages"])


async def _verify_can_act(db: AsyncSession, message: Message, user: BTTCOUser) -> None:
    """
    Guardrail: the actor must be a participant in the message's
    conversation. Stricter per-action checks (e.g. "only the client
    can confirm a quote_proposal") are enforced inside individual
    artifact handlers.
    """
    result = await db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == message.conversation_id,
            ConversationParticipant.user_id == user.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=403,
            detail="not a participant in this conversation",
        )


@router.post(
    "/{message_id}/artifact-action",
    response_model=ArtifactActionResponse,
)
async def apply_artifact_action(
    message_id: UUID,
    body: ArtifactActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Dispatch an action against a message's artifact state.

    Example: a client confirming a quote_proposal fires action=`confirm`
    on the message id, which the quote_proposal handler translates into
    a Stripe Checkout session + state transition to `awaiting_payment`.

    Returns the new artifact_state so the client can update in place
    without a full message re-fetch.
    """
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")

    await _verify_can_act(db, message, current_user)

    if not artifacts.is_registered(message.message_type):
        raise HTTPException(
            status_code=400,
            detail=f"message type {message.message_type!r} has no artifact handler",
        )

    try:
        new_state = await artifacts.apply_artifact_action(db, message, body.action, current_user, body.data)
    except artifacts.ArtifactActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except artifacts.ArtifactError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await db.flush()
    return ArtifactActionResponse(
        message_id=message.id,
        message_type=message.message_type,
        artifact_state=new_state,
        updated_at=message.updated_at,
    )


@router.get("/types")
async def list_artifact_types(
    current_user: BTTCOUser = Depends(get_current_user),
):
    """
    Introspection endpoint: returns every registered artifact type
    and its allowed actions. Used by the frontend to dynamically
    render composer buttons ("send quote", "send slot proposal", etc)
    without hardcoding the list client-side.
    """
    return {
        "types": [
            {
                "message_type": handler.message_type,
                "allowed_actions": sorted(handler.allowed_actions),
            }
            for handler in (artifacts.get_handler(t) for t in artifacts.registered_types())
        ]
    }
