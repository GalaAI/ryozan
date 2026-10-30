"""
Interactive Message Artifacts — service layer.

A message in BTTCO's Courier can either be plain text or a typed "artifact"
with a structured JSON payload and mutable state. This module is the central
registry + dispatcher for artifact behavior. Each artifact type declares:

  * A Pydantic payload schema validated at create-time
  * An initial state (the starting point of its state machine)
  * A set of actions the client or artist can invoke on it
  * A transition function mapping (current_state, action) → new_state

The idea is that every new structured interaction (quote proposal, slot
proposal, waiver, aftercare ack, etc.) drops in as a new artifact type
without touching the endpoint code. The endpoint just looks up the type
in the registry and delegates to its handler.

Phase 2 ships the plumbing with only the `text` no-op handler registered
so we can validate the dispatch path end-to-end. Phase 3 adds
`quote_proposal` on top.
"""

from __future__ import annotations

import logging
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.models.message import Message  # noqa: TC001
from app.models.user import BTTCOUser  # noqa: TC001

logger = logging.getLogger("bttco.artifacts")


class ArtifactError(Exception):
    """Raised for any artifact validation / state-transition failure."""


class ArtifactActionError(ArtifactError):
    """The requested action is not allowed on the current state."""


class ArtifactNotFoundError(ArtifactError):
    """The artifact type is not registered."""


# ── Registry primitives ──────────────────────────────────────────


@dataclass
class ArtifactHandler:
    """
    Describes a single artifact type's behavior.

    payload_schema:  Pydantic model used to validate + normalize the
                     payload at create-time.
    initial_state:   The state dict a brand-new artifact starts in.
    allowed_actions: Set of action names this type understands.
    apply_action:    Async callable(db, message, action, actor, data)
                     -> dict (the new state). May also mutate related
                     rows (e.g. booking, quote, stripe session).
    """

    message_type: str
    payload_schema: type[BaseModel] | None
    initial_state: dict[str, Any]
    allowed_actions: frozenset[str]
    apply_action: Callable[
        [AsyncSession, Message, str, BTTCOUser | None, dict[str, Any]],
        Any,
    ]


_REGISTRY: dict[str, ArtifactHandler] = {}


def register(handler: ArtifactHandler) -> None:
    """Register an artifact handler. Called once per type at import time."""
    if handler.message_type in _REGISTRY:
        raise RuntimeError(f"duplicate artifact registration: {handler.message_type}")
    _REGISTRY[handler.message_type] = handler
    logger.info("registered artifact handler: %s", handler.message_type)


def get_handler(message_type: str) -> ArtifactHandler:
    try:
        return _REGISTRY[message_type]
    except KeyError as exc:
        raise ArtifactNotFoundError(f"no artifact handler for type={message_type!r}") from exc


def is_registered(message_type: str) -> bool:
    return message_type in _REGISTRY


def registered_types() -> list[str]:
    """Used by /messages/types for frontend type introspection."""
    return sorted(_REGISTRY.keys())


# ── Generic helpers ──────────────────────────────────────────────


def validate_payload(message_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """
    Validate a raw payload against the handler's schema and return the
    JSON-serializable normalized dict (UUIDs become strings, datetimes
    become ISO strings, etc.) so it can be stored directly in the
    Postgres jsonb column. Raises ArtifactError on failure.
    """
    handler = get_handler(message_type)
    if handler.payload_schema is None:
        return payload or {}
    try:
        return handler.payload_schema.model_validate(payload or {}).model_dump(mode="json")
    except Exception as exc:
        raise ArtifactError(f"invalid {message_type} payload: {exc}") from exc


def initial_state(message_type: str) -> dict[str, Any]:
    return dict(get_handler(message_type).initial_state)


async def apply_artifact_action(
    db: AsyncSession,
    message: Message,
    action: str,
    actor: BTTCOUser | None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Dispatch an action against a message's artifact. Returns the new
    state after the transition. The caller is responsible for flushing
    and committing the session.
    """
    handler = get_handler(message.message_type)
    if action not in handler.allowed_actions:
        raise ArtifactActionError(
            f"action={action!r} not allowed on {message.message_type!r} (allowed: {sorted(handler.allowed_actions)})"
        )

    new_state = await handler.apply_action(db, message, action, actor, data or {})
    message.artifact_state = new_state
    message.updated_at = datetime.now(UTC)
    return new_state


# ── Built-in handler: text ───────────────────────────────────────
#
# Plain-text messages are implemented as a no-op artifact handler so the
# dispatch path is exercised by the existing `text` message_type. The
# only "action" a plain text message understands is `mark_read`, which
# currently just records the timestamp in state. Future features like
# read receipts and reactions can extend this without a schema change.


class TextMarkReadPayload(BaseModel):
    """No fields — mark-read takes no data."""


async def _text_apply_action(
    db: AsyncSession,
    message: Message,
    action: str,
    actor: BTTCOUser | None,
    data: dict[str, Any],
) -> dict[str, Any]:
    state = dict(message.artifact_state or {})
    if action == "mark_read":
        reads = dict(state.get("reads", {}))
        if actor:
            reads[str(actor.id)] = datetime.now(UTC).isoformat()
        state["reads"] = reads
    return state


register(
    ArtifactHandler(
        message_type="text",
        payload_schema=None,  # plain text has no structured payload
        initial_state={},
        allowed_actions=frozenset({"mark_read"}),
        apply_action=_text_apply_action,
    )
)


# ── Auto-register all concrete artifact handlers ─────────────────
# Importing this package at the END of artifacts.py means every
# handler module runs its own `register(...)` call once the core
# primitives above are in place. Order matters: the imports below
# depend on `register` already being defined and the `text` handler
# already in _REGISTRY.
from app.services import artifact_handlers  # noqa: E402,F401
