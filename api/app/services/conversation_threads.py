"""
Single owner of "which Courier thread do these two people share".

Why this module exists
----------------------
Four call sites needed the same answer and three of them answered it
themselves. `quotes.py` implemented the lookup inline twice, and the two
booking-creation paths in `bookings.py` skipped the lookup entirely and built a
fresh `Conversation` on every call. That is a duplicate thread per booking, and
production already shows the result: five client/artist pairs hold more than
one one-to-one thread, one of them three.

One of the inline copies also carried the `not Conversation.is_group` defect,
which collapses to the literal `False`, so its lookup could never match and it
created a new thread every single time. That is repaired separately, but the
deeper problem is that the rule lived in four places at all.

Thread identity here is *participant identity*, not a name. A thread is the one
non-group conversation whose participant set is exactly the people given. Names
are display text, they change, and two people do not get a second thread just
because a booking used a different label.
"""

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.message import Conversation, ConversationParticipant

logger = logging.getLogger(__name__)


def conversation_reply_to(conversation_id: UUID | None) -> str | None:
    """Shared reply alias for Courier notifications and booking-linked emails.

    The configured domain must be admitted separately for receiving. The helper
    generates an address; it does not establish provider or DNS readiness.
    """
    if conversation_id is None:
        return None
    return f"convo-{conversation_id}@{get_settings().COURIER_REPLY_DOMAIN}"


async def find_direct_conversation(
    db: AsyncSession,
    participant_ids: set[UUID],
) -> Conversation | None:
    """Return the non-group thread whose participants are exactly this set.

    Exactly, not merely containing. A two-person lookup must not return a
    thread that happens to include those two plus a third party.
    """
    if len(participant_ids) < 2:
        return None

    # Conversations that contain every requested participant, and whose total
    # participant count equals the requested size. The count comparison is what
    # makes the match exact.
    subq = (
        select(ConversationParticipant.conversation_id)
        .where(ConversationParticipant.user_id.in_(participant_ids))
        .group_by(ConversationParticipant.conversation_id)
        .having(func.count(func.distinct(ConversationParticipant.user_id)) == len(participant_ids))
        .subquery()
    )
    total = (
        select(ConversationParticipant.conversation_id)
        .group_by(ConversationParticipant.conversation_id)
        .having(func.count(ConversationParticipant.id) == len(participant_ids))
        .subquery()
    )

    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.is_group.is_(False),
            Conversation.id.in_(select(subq.c.conversation_id)),
            Conversation.id.in_(select(total.c.conversation_id)),
        )
        .order_by(Conversation.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_or_create_direct_conversation(
    db: AsyncSession,
    participant_ids: set[UUID],
    *,
    name: str,
    created_by: UUID,
) -> tuple[Conversation, bool]:
    """Get the shared thread for these people, creating it only if absent.

    Returns (conversation, created). Callers that need to know whether they
    just started a thread — to post an opening message, say — can branch on the
    flag instead of inferring it.

    Ordered oldest-first when several already exist, so the historical thread
    wins rather than an accidental duplicate. That matters while the existing
    duplicates are still in the data.
    """
    existing = await find_direct_conversation(db, participant_ids)
    if existing is not None:
        return existing, False

    conv = Conversation(name=name, is_group=False, created_by=created_by)
    db.add(conv)
    await db.flush()
    for uid in participant_ids:
        db.add(ConversationParticipant(conversation_id=conv.id, user_id=uid, role="member"))
    await db.flush()
    logger.info("opened Courier thread %s for %d participants", conv.id, len(participant_ids))
    return conv, True
