"""Herald artist reposts, managed from the portal and owned by Ark.

Net-new justification: Ark owns the repost drafts, review, and the Social Modz
publish (GalaAI/ark#1181). The portal needs the same checkpoint for founders and
admins, so this router only forwards over the existing signed Pack bridge with
the signed-in operator as the approval. It stores nothing and posts nothing
itself.
"""

from __future__ import annotations

import re
from datetime import datetime  # noqa: TC003
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.security import get_current_user
from app.models.user import BTTCOUser  # noqa: TC001
from app.services.agent_evolution import AgentEvolutionBridgeError, request_ark_bridge

router = APIRouter(prefix="/herald/reposts", tags=["herald"])

_ARK_REPOSTS = "/api/social/reposts"
_DRAFT_ID = re.compile(r"^repost_[a-z0-9_]+$")
_STATUSES = {"pending", "approved", "rejected", "published", "failed"}
# Scan reads seven accounts; publish waits for Meta to finish processing video.
_SCAN_TIMEOUT = 120.0
_PUBLISH_TIMEOUT = 300.0


class RepostScanRequest(BaseModel):
    handles: list[str] | None = Field(default=None, max_length=20)
    max_per_artist: int | None = Field(default=None, ge=1, le=10)


class RepostPatchRequest(BaseModel):
    caption: str | None = Field(default=None, max_length=2000)
    hashtags: list[str] | None = Field(default=None, max_length=30)
    scheduled_for: datetime | None = None
    clear_schedule: bool = False


class RepostReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


class RepostPublishRequest(BaseModel):
    approval_reason: str | None = Field(default=None, min_length=1, max_length=400)


def _require_herald_operator(user: BTTCOUser) -> None:
    if user.role not in {"founder", "admin"}:
        raise HTTPException(403, "Artist reposts are limited to founder and admin accounts.")


def _draft_path(draft_id: str) -> str:
    if not _DRAFT_ID.match(draft_id):
        raise HTTPException(400, "Invalid repost draft id.")
    return f"{_ARK_REPOSTS}/{draft_id}"


def _gate(user: BTTCOUser, **payload: Any) -> dict[str, Any]:
    """Carry the signed-in operator as the approval across the signed Ark bridge."""
    return {
        **{key: value for key, value in payload.items() if value is not None},
        "operatorGate": {"confirmed": True, "approvedBy": str(user.name or user.email)},
    }


async def _ark(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    try:
        return await request_ark_bridge(method, path, payload, timeout=timeout, service="Ark Social Modz")
    except AgentEvolutionBridgeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("")
async def list_reposts(
    status: str | None = Query(default=None),
    user: BTTCOUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_herald_operator(user)
    query = f"?status={status}" if status in _STATUSES else ""
    return await _ark("GET", f"{_ARK_REPOSTS}{query}")


@router.get("/location")
async def repost_location(user: BTTCOUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_herald_operator(user)
    return await _ark("GET", f"{_ARK_REPOSTS}/location", timeout=30.0)


@router.post("/scan")
async def scan_reposts(
    request: RepostScanRequest,
    user: BTTCOUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_herald_operator(user)
    return await _ark(
        "POST",
        f"{_ARK_REPOSTS}/scan",
        _gate(user, handles=request.handles, maxPerArtist=request.max_per_artist),
        timeout=_SCAN_TIMEOUT,
    )


@router.get("/{draft_id}")
async def get_repost(draft_id: str, user: BTTCOUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_herald_operator(user)
    return await _ark("GET", _draft_path(draft_id))


@router.patch("/{draft_id}")
async def edit_repost(
    draft_id: str,
    request: RepostPatchRequest,
    user: BTTCOUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_herald_operator(user)
    payload = _gate(
        user,
        caption=request.caption,
        hashtags=[tag.lstrip("#") for tag in request.hashtags] if request.hashtags is not None else None,
        scheduledFor=request.scheduled_for.isoformat().replace("+00:00", "Z") if request.scheduled_for else None,
    )
    if request.clear_schedule:
        payload["scheduledFor"] = None
    return await _ark("PATCH", _draft_path(draft_id), payload)


@router.post("/{draft_id}/review")
async def review_repost(
    draft_id: str,
    request: RepostReviewRequest,
    user: BTTCOUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_herald_operator(user)
    return await _ark(
        "POST",
        f"{_draft_path(draft_id)}/review",
        _gate(user, decision=request.decision, note=request.note),
    )


@router.post("/{draft_id}/publish")
async def publish_repost(
    draft_id: str,
    request: RepostPublishRequest,
    user: BTTCOUser = Depends(get_current_user),
) -> dict[str, Any]:
    _require_herald_operator(user)
    # A failed publish comes back as an error here; the draft keeps Meta's reason for the card.
    return await _ark(
        "POST",
        f"{_draft_path(draft_id)}/publish",
        _gate(user, approvalReason=request.approval_reason),
        timeout=_PUBLISH_TIMEOUT,
    )
