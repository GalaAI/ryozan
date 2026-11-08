"""Mods: the shop's Mods inventory and the Ark Matrix Mod.

The inventory follows Ark's Mold contract (see services/mods_inventory.py).
The Matrix view reads Ark's Matrix Mod: its catalog of Eden Matrices with
their engines, scopes and tools, and, when this install holds an Ark
operator token, the recent scenario runs. Ryozan never starts an Ark run
from here; runs start in Ark or from an agent with that Matrix's scope.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.security import require_role
from app.services.mods_inventory import MOLD_INSTANCE, build_mods_feed

router = APIRouter(prefix="/mods", tags=["mods"])

MATRIX_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


@router.get("")
async def mods_inventory(_user=Depends(require_role("admin"))):
    """The Mods inventory: the Mold instance and its feed."""
    return {"mold": MOLD_INSTANCE, "feed": build_mods_feed()}


@router.get("/matrix")
async def matrix_mod(_user=Depends(require_role("admin"))):
    """Ark's Matrix Mod catalog, and the recent scenario runs when connected."""
    settings = get_settings()
    base = (settings.ARK_MATRIX_MOD_URL or "").strip().rstrip("/")
    if not base:
        return {
            "connected": False,
            "reason": "This install is not connected to Ark. Set ARK_MATRIX_MOD_URL to the Ark API's Matrix Mod route.",
            "matrices": [],
            "runs": None,
        }
    try:
        async with httpx.AsyncClient(timeout=MATRIX_TIMEOUT) as client:
            catalog = await client.get(base)
            catalog.raise_for_status()
            matrices = catalog.json().get("matrices", [])
            runs = None
            runs_note = "Runs need ARK_MATRIX_MOD_TOKEN, an Ark operator token, on this install."
            token = (settings.ARK_MATRIX_MOD_TOKEN or "").strip()
            if token:
                reply = await client.post(
                    f"{base}/call",
                    headers={"authorization": f"Bearer {token}"},
                    json={"capabilityId": "matrix.eden-scenarios.list_runs", "arguments": {}},
                )
                body = reply.json()
                if reply.is_success and body.get("ok"):
                    runs = body.get("data", {}).get("runs", [])
                    runs_note = None
                else:
                    runs_note = f"Ark refused the runs read: {body.get('error') or reply.status_code}."
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "connected": False,
            "reason": f"Ark's Matrix Mod did not answer ({type(exc).__name__}).",
            "matrices": [],
            "runs": None,
        }
    return {"connected": True, "matrices": matrices, "runs": runs, "runsNote": runs_note}
