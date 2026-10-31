"""Deterministic Booky release policy and UI projection inputs.

Net-new artifact justification:
The repository already owns booking, Calendar, Courier, and lifecycle behavior,
but it has no authority for per-artist Booky modes or autonomous-consultation
gates. This module adds only that missing policy decision and delegates every
mutation to the existing service owners.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.models.user import User

BookyMode = Literal["manual", "shadow", "approval", "consult_auto", "session_auto"]
ACTIVE_BOOKY_MODES = {"manual", "shadow", "approval", "consult_auto"}
ALL_BOOKY_MODES = {*ACTIVE_BOOKY_MODES, "session_auto"}


def _csv_set(value: str) -> set[str]:
    return {item.strip().lower() for item in (value or "").split(",") if item.strip()}


def normalize_booky_mode(value: str | None) -> BookyMode:
    mode = (value or "manual").strip().lower()
    return mode if mode in ALL_BOOKY_MODES else "manual"  # type: ignore[return-value]


def booky_action_id(
    *,
    artist_id: object,
    inquiry_id: str | None,
    hold_token: str | None,
    policy_version: str,
) -> str:
    seed = f"{artist_id}:{inquiry_id or ''}:{hold_token or ''}:{policy_version}"
    return "booky_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def artist_canary_allowed(settings: Settings, artist: User) -> bool:
    allowed = _csv_set(settings.BOOKY_CANARY_ARTIST_SLUGS)
    return "*" in allowed or (artist.slug or "").lower() in allowed


def collector_canary_allowed(settings: Settings, collector_id: object | None) -> bool:
    """Require one explicitly configured authenticated account UUID."""
    try:
        resolved_id = UUID(str(collector_id))
    except (TypeError, ValueError):
        return False
    allowed: set[UUID] = set()
    for raw_id in (settings.BOOKY_CANARY_COLLECTOR_IDS or "").split(","):
        try:
            allowed.add(UUID(raw_id.strip()))
        except ValueError:
            return False
    return resolved_id in allowed


@dataclass(frozen=True)
class BookyPolicyDecision:
    configured_mode: BookyMode
    effective_mode: BookyMode
    truth_state: str
    recommendation: str
    explanation: str
    confidence: str
    policy_version: str
    risk: str
    required_approval: bool
    blocked_reasons: tuple[str, ...]
    available_actions: tuple[str, ...]

    @property
    def can_auto_confirm(self) -> bool:
        return self.effective_mode == "consult_auto" and self.truth_state == "ready"


def resolve_booky_policy(
    *,
    settings: Settings,
    artist: User,
    booking_kind: str,
    collector_id: object | None,
    collector_identity_verified: bool,
    consent_granted: bool,
    consent_policy_version: str | None,
    slot_hold_verified: bool,
    calendar_connected: bool,
) -> BookyPolicyDecision:
    configured = normalize_booky_mode(artist.booky_mode)
    policy_version = settings.BOOKY_POLICY_VERSION

    if configured == "manual":
        return BookyPolicyDecision(
            configured_mode=configured,
            effective_mode="manual",
            truth_state="empty",
            recommendation="",
            explanation="This artist uses the manual booking workflow.",
            confidence="high",
            policy_version=policy_version,
            risk="low",
            required_approval=True,
            blocked_reasons=(),
            available_actions=("open_inquiry", "message_client", "propose_slot"),
        )

    blocked: list[str] = []
    if configured == "session_auto":
        blocked.append("session_auto_disabled")
    if not settings.BOOKY_AUTOMATION_ENABLED:
        blocked.append("automation_disabled")
    if artist.booky_opted_in_at is None:
        blocked.append("artist_opt_in_required")
    if not artist_canary_allowed(settings, artist):
        blocked.append("artist_not_in_canary")
    if not collector_canary_allowed(settings, collector_id):
        blocked.append("collector_not_in_canary")
    if not collector_identity_verified:
        blocked.append("collector_identity_required")

    if configured == "consult_auto":
        if booking_kind != "consultation":
            blocked.append("consultations_only")
        if not consent_granted:
            blocked.append("collector_consent_required")
        elif consent_policy_version != policy_version:
            blocked.append("consent_policy_mismatch")
        if not slot_hold_verified:
            blocked.append("verified_slot_hold_required")
        if not calendar_connected:
            blocked.append("calendar_unavailable")

    if blocked:
        return BookyPolicyDecision(
            configured_mode=configured,
            effective_mode="manual",
            truth_state="blocked",
            recommendation="Keep the inquiry pending for staff review.",
            explanation="Booky did not pass every release-policy prerequisite.",
            confidence="high",
            policy_version=policy_version,
            risk="controlled",
            required_approval=True,
            blocked_reasons=tuple(blocked),
            available_actions=("open_inquiry", "review_blockers"),
        )

    if configured == "shadow":
        return BookyPolicyDecision(
            configured_mode=configured,
            effective_mode="shadow",
            truth_state="ready",
            recommendation="Review the consultation slot and prepare a confirmation.",
            explanation="Shadow mode records a recommendation without client-visible Booky actions.",
            confidence="high" if slot_hold_verified else "medium",
            policy_version=policy_version,
            risk="none",
            required_approval=True,
            blocked_reasons=(),
            available_actions=("open_inquiry", "review_recommendation"),
        )

    if configured == "approval":
        return BookyPolicyDecision(
            configured_mode=configured,
            effective_mode="approval",
            truth_state="approval_needed",
            recommendation="Approve or reject the held consultation slot.",
            explanation="Approval mode requires a staff decision before confirmation or delivery.",
            confidence="high" if slot_hold_verified else "medium",
            policy_version=policy_version,
            risk="low",
            required_approval=True,
            blocked_reasons=(),
            available_actions=("approve_consultation", "reject_recommendation", "message_client"),
        )

    return BookyPolicyDecision(
        configured_mode=configured,
        effective_mode="consult_auto",
        truth_state="ready",
        recommendation="Confirm the held consultation and queue Courier delivery.",
        explanation="Every consultation-auto prerequisite passed.",
        confidence="high",
        policy_version=policy_version,
        risk="low",
        required_approval=False,
        blocked_reasons=(),
        available_actions=("open_booking", "cancel_booking", "retry_delivery"),
    )
