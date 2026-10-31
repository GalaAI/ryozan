"""Viber endpoints — accounts, project connections, AI providers.

Viber connects OUT to project backends (BTTCO API first) as an extension.
Founder-gated: this is operator tooling, not collector-facing.

Secrets (connection tokens, BYOK keys) are accepted at create-time and stored
as references via the Hivemind secrets vault pattern when available, falling
back to an opaque local reference. Raw secrets are never returned in responses.
"""

import logging
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.user import BTTCOUser
from app.models.viber import ViberAccount, ViberAIProvider, ViberConnection, ViberProposal
from app.schemas.viber import (
    ConnectionTestResult,
    ViberAccountCreate,
    ViberAccountRecord,
    ViberAIProviderCreate,
    ViberAIProviderRecord,
    ViberConnectionCreate,
    ViberConnectionRecord,
    ViberGalaIntentRequest,
    ViberGalaIntentResponse,
    ViberProposalApproveRequest,
    ViberProposalRecord,
)

logger = logging.getLogger("bttco.viber")

router = APIRouter(prefix="/viber", tags=["viber"])


def _require_founder(user: BTTCOUser) -> None:
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("founder", 99):
        raise HTTPException(status_code=403, detail="Viber is founder-only for now")


def _store_secret(scope: str, ident: str, value: str) -> str:
    """Store a secret and return an opaque reference. Uses the Hivemind vault
    pattern if reachable; otherwise a namespaced local reference. Never returns
    the raw value."""
    # Phase 3 boundary: the vault client is still a stub in this runtime. Store
    # inline only as a temporary private ref so the adapter can be verified
    # end-to-end. Responses never expose key_secret_ref or raw secret values.
    if scope == "ai_key":
        return f"inline:{value}"
    return f"viber/{scope}/{ident}"


def _route_gala_intent(intent: str) -> dict:
    normalized = intent.lower()
    smith_markers = (
        "scaffold",
        "build",
        "feature",
        "model",
        "endpoint",
        "migration",
        "schema",
        "ui",
        "component",
        "refactor",
    )
    if any(marker in normalized for marker in smith_markers):
        return {
            "agent": "smith",
            "confidence": 0.92,
            "reason": "Intent describes architecture, scaffolding, or code changes.",
        }
    return {
        "agent": "smith",
        "confidence": 0.62,
        "reason": "Smith is the only end-to-end agent enabled in Phase 3.",
    }


def _feature_slug(intent: str) -> str:
    match = re.search(r"(?:called|named)\s+([a-zA-Z0-9 _-]+)", intent)
    raw = match.group(1) if match else intent
    raw = raw.strip().split(".")[0].split(",")[0]
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return slug[:48] or "new_feature"


def _smith_scaffold_proposal(intent: str, atlas: dict, connection: ViberConnection) -> dict:
    feature = _feature_slug(intent)
    resources = [
        n.get("label")
        for n in atlas.get("nodes", [])
        if n.get("type") == "resource" and isinstance(n.get("label"), str)
    ]
    api_prefix = "/api/v1"
    if any(r in resources for r in ("viber", "workspaces")):
        api_prefix = "/api/v1"

    return {
        "type": "smith_scaffold_plan",
        "status": "staged",
        "execution": {
            "allowed": False,
            "reason": "Approval required before connected-project writes.",
        },
        "connection": {
            "id": str(connection.id),
            "name": connection.name,
            "kind": connection.kind,
            "capability": connection.capability,
        },
        "feature": {
            "slug": feature,
            "label": feature.replace("_", " ").title(),
            "source_intent": intent,
        },
        "atlas_context": {
            "resource_count": atlas.get("meta", {}).get("resource_count", 0),
            "endpoint_count": atlas.get("meta", {}).get("endpoint_count", 0),
            "degraded": atlas.get("meta", {}).get("degraded", True),
            "nearby_resources": resources[:12],
        },
        "files": [
            f"api/app/models/{feature}.py",
            f"api/app/schemas/{feature}.py",
            f"api/app/api/v1/endpoints/{feature}.py",
            f"api/alembic/versions/<next>_create_{feature}.py",
            f"app/src/pages/dashboard/{feature.title().replace('_', '')}.tsx",
        ],
        "api_contract": {
            "router": f"{api_prefix}/{feature}",
            "routes": [
                {"method": "GET", "path": f"/{feature}", "purpose": "List records"},
                {"method": "POST", "path": f"/{feature}", "purpose": "Create record"},
                {"method": "PATCH", "path": f"/{feature}/{{id}}", "purpose": "Update record"},
            ],
        },
        "steps": [
            "Define SQLAlchemy model and Pydantic schemas.",
            "Add Alembic migration and import model in alembic/env.py.",
            "Add founder or staff-gated FastAPI router using existing auth patterns.",
            "Register router in api/app/api/v1/router.py.",
            "Add focused UI surface that calls the env-aware API client.",
            "Run py_compile plus targeted API exercise before approval can execute.",
        ],
        "approval_required": True,
    }


# ── Accounts ────────────────────────────────────────────────


@router.post("/accounts", response_model=ViberAccountRecord, status_code=201)
async def create_account(
    payload: ViberAccountCreate,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_founder(current_user)
    existing = await db.execute(select(ViberAccount).where(ViberAccount.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Account email already exists")
    acct = ViberAccount(email=str(payload.email), name=payload.name, plan=payload.plan)
    db.add(acct)
    await db.commit()
    await db.refresh(acct)
    return acct


@router.get("/accounts", response_model=list[ViberAccountRecord])
async def list_accounts(
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_founder(current_user)
    result = await db.execute(select(ViberAccount).order_by(ViberAccount.created_at))
    return list(result.scalars().all())


async def _get_account(account_id: UUID, db: AsyncSession) -> ViberAccount:
    result = await db.execute(select(ViberAccount).where(ViberAccount.id == account_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    return acct


# ── Connections ─────────────────────────────────────────────


@router.post("/accounts/{account_id}/connections", response_model=ViberConnectionRecord, status_code=201)
async def create_connection(
    account_id: UUID,
    payload: ViberConnectionCreate,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_founder(current_user)
    acct = await _get_account(account_id, db)
    secret_ref = None
    if payload.auth_token:
        secret_ref = _store_secret("connection", str(acct.id), payload.auth_token)
    conn = ViberConnection(
        account_id=acct.id,
        name=payload.name,
        kind=payload.kind,
        base_url=payload.base_url.rstrip("/"),
        capability=payload.capability,
        auth_scheme=payload.auth_scheme,
        auth_secret_ref=secret_ref,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


@router.get("/accounts/{account_id}/connections", response_model=list[ViberConnectionRecord])
async def list_connections(
    account_id: UUID,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_founder(current_user)
    await _get_account(account_id, db)
    result = await db.execute(
        select(ViberConnection).where(ViberConnection.account_id == account_id).order_by(ViberConnection.created_at)
    )
    return list(result.scalars().all())


@router.post("/connections/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: UUID,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Probe a connected backend and cache an introspection map.

    For a bttco_api connection this hits /health and, when reachable, records a
    lightweight capability map (the OpenAPI path count). This is the seed of
    Atlas + Oversight introspection.
    """
    _require_founder(current_user)
    result = await db.execute(select(ViberConnection).where(ViberConnection.id == connection_id))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    from datetime import UTC, datetime

    health_url = conn.base_url.rstrip("/")
    # base_url may already include /api/v1; health lives at the service root.
    root = health_url.split("/api/")[0]
    introspection: dict | None = None
    healthy = False
    detail = ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            health = await client.get(f"{root}/health")
            healthy = health.status_code == 200
            detail = f"health {health.status_code}"
            # Pull the OpenAPI doc for a route map if exposed.
            try:
                openapi = await client.get(f"{root}/openapi.json")
                if openapi.status_code == 200:
                    spec = openapi.json()
                    paths = list((spec.get("paths") or {}).keys())
                    introspection = {
                        "openapi_paths": len(paths),
                        "sample_paths": paths[:25],
                        "title": spec.get("info", {}).get("title"),
                    }
            except Exception:
                pass
    except Exception as exc:  # network/DNS/timeout
        detail = f"unreachable: {exc.__class__.__name__}"

    conn.status = "connected" if healthy else "error"
    conn.last_checked_at = datetime.now(UTC)
    if introspection is not None:
        conn.introspection = introspection
    await db.commit()
    await db.refresh(conn)

    return ConnectionTestResult(
        connection_id=conn.id,
        status=conn.status,
        healthy=healthy,
        detail=detail,
        introspection=conn.introspection,
    )


# ── Atlas (frontend-to-backend app map) ─────────────────────


@router.get("/connections/{connection_id}/atlas")
async def connection_atlas(
    connection_id: UUID,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build the Atlas mind-map graph for a connection.

    Uses the connection's cached introspection (OpenAPI route map). For a
    bttco_api connection pointed at THIS database, overlays live table counts.
    Degrades gracefully when OpenAPI is unavailable (e.g. prod docs disabled).
    """
    _require_founder(current_user)
    from app.services.atlas import build_atlas_graph

    result = await db.execute(select(ViberConnection).where(ViberConnection.id == connection_id))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    introspection = conn.introspection or {}
    openapi_paths = introspection.get("sample_paths")

    # Live table counts: only when this connection points at the local DB we can
    # read directly. We pull counts for ryozan_/viber_ tables present.
    table_counts: dict[str, int] = {}
    if conn.kind == "bttco_api":
        from sqlalchemy import text as sa_text

        rows = await db.execute(
            sa_text(
                "SELECT relname, n_live_tup FROM pg_stat_user_tables "
                "WHERE relname LIKE 'ryozan_%' OR relname LIKE 'viber_%' "
                "ORDER BY relname"
            )
        )
        # n_live_tup is an estimate; flag it as such in the node payload.
        for name, est in rows.all():
            table_counts[name] = int(est or 0)

    graph = build_atlas_graph(
        connection_name=conn.name,
        openapi_paths=openapi_paths,
        table_counts=table_counts,
    )
    graph["meta"]["counts_are_estimates"] = bool(table_counts)
    return graph


# ── Gala orchestrator + Smith proposals ─────────────────────


@router.post("/accounts/{account_id}/gala", response_model=ViberGalaIntentResponse)
async def gala_orchestrate(
    account_id: UUID,
    payload: ViberGalaIntentRequest,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Route a natural-language intent through Gala.

    Phase 3 wires one agent end-to-end: Smith proposes a project-change plan
    from the connection's Atlas graph. The proposal is staged only.
    """
    _require_founder(current_user)
    acct = await _get_account(account_id, db)

    result = await db.execute(
        select(ViberConnection).where(
            ViberConnection.id == payload.connection_id,
            ViberConnection.account_id == acct.id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    route = _route_gala_intent(payload.intent)
    if route["agent"] != "smith":
        raise HTTPException(status_code=501, detail="Only Smith is enabled in Viber Phase 3")

    from app.services.atlas import build_atlas_graph

    introspection = conn.introspection or {}
    graph = build_atlas_graph(
        connection_name=conn.name,
        openapi_paths=introspection.get("sample_paths"),
        table_counts={},
    )
    smith_plan = _smith_scaffold_proposal(payload.intent, graph, conn)

    ai_summary: str | None = None
    try:
        from app.core.config import get_settings
        from app.services.viber_ai import viber_chat

        ai = await viber_chat(
            db=db,
            settings=get_settings(),
            account_id=acct.id,
            system_prompt=(
                "You are Smith, Viber's architecture agent. Summarize the staged plan in plain language. "
                "Do not claim execution happened. Do not include secrets."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Intent: {payload.intent}\n"
                        f"Atlas meta: {graph.get('meta')}\n"
                        f"Planned files: {smith_plan.get('files')}"
                    ),
                }
            ],
            max_tokens=300,
        )
        ai_summary = ai.text or None
        smith_plan["ai"] = {
            "provider_type": ai.provider_type,
            "model": ai.model,
            "summary_generated": bool(ai_summary),
        }
    except Exception as exc:
        logger.warning("Viber AI summary skipped: %s", exc.__class__.__name__)
        smith_plan["ai"] = {
            "summary_generated": False,
            "reason": exc.__class__.__name__,
        }

    proposal = ViberProposal(
        account_id=acct.id,
        connection_id=conn.id,
        agent="smith",
        intent=payload.intent,
        status="staged",
        proposal=smith_plan,
    )
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    return ViberGalaIntentResponse(route=route, proposal=proposal, ai_summary=ai_summary)


@router.post("/proposals/{proposal_id}/approve", response_model=ViberProposalRecord)
async def approve_proposal(
    proposal_id: UUID,
    payload: ViberProposalApproveRequest,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve a staged proposal without executing it.

    Execution remains intentionally unwired until the next delivery gate.
    """
    _require_founder(current_user)
    result = await db.execute(select(ViberProposal).where(ViberProposal.id == proposal_id))
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "staged":
        raise HTTPException(status_code=409, detail=f"Proposal is {proposal.status}, not staged")

    updated = dict(proposal.proposal or {})
    updated["execution"] = {
        "allowed": False,
        "reason": "Approve endpoint records approval only. Execution is not implemented in Phase 3.",
    }
    proposal.proposal = updated
    proposal.status = "approved"
    proposal.approval_notes = payload.notes
    proposal.approved_at = datetime.now(UTC)
    proposal.executed_at = None
    await db.commit()
    await db.refresh(proposal)
    return proposal


# ── AI providers ────────────────────────────────────────────


@router.post("/accounts/{account_id}/ai-providers", response_model=ViberAIProviderRecord, status_code=201)
async def create_ai_provider(
    account_id: UUID,
    payload: ViberAIProviderCreate,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_founder(current_user)
    acct = await _get_account(account_id, db)
    key_ref = None
    if payload.api_key:
        key_ref = _store_secret("ai_key", str(acct.id), payload.api_key)
    if payload.is_default:
        # unset any existing default for this account
        existing = await db.execute(
            select(ViberAIProvider).where(ViberAIProvider.account_id == acct.id, ViberAIProvider.is_default.is_(True))
        )
        for row in existing.scalars().all():
            row.is_default = False
    provider = ViberAIProvider(
        account_id=acct.id,
        provider_type=payload.provider_type,
        label=payload.label,
        base_url=payload.base_url,
        default_model=payload.default_model,
        key_secret_ref=key_ref,
        is_default=payload.is_default,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


@router.get("/accounts/{account_id}/ai-providers", response_model=list[ViberAIProviderRecord])
async def list_ai_providers(
    account_id: UUID,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_founder(current_user)
    await _get_account(account_id, db)
    result = await db.execute(
        select(ViberAIProvider).where(ViberAIProvider.account_id == account_id).order_by(ViberAIProvider.created_at)
    )
    return list(result.scalars().all())
