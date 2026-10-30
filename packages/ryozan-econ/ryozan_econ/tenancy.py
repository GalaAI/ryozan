"""Who a set of books belongs to.

Ryozan is itself a vessel in the Ark ontology -- ``vessel:ryozan``, "Ryozan
(Tattoo Workflow & AI)", domain ``ryozan.ark`` -- and ArkStation manages it as a
peer of the studio vessels that use it, not as a container for them. They are
siblings in the vessel graph.

A *tenant* of these books is therefore not a vessel. It is the business whose
money is being counted, which Ark already names with an entity ref of the form
``entity:<slug>``, reported alongside a vessel ref like
``vessel:<slug>:platform``. So the canonical tenant id is the entity ref, and
Ryozan the product is the thing keeping that entity's books.

Ark owns that naming, so this module reuses it rather than inventing a parallel
slug scheme. It stays a *convention* rather than a hard requirement: a studio
running Ryozan standalone, outside Ark, still needs books, and refusing their
plain identifier would make the package unsellable off-platform.
"""

from __future__ import annotations

import re

#: Ark entity refs, e.g. ``entity:example-studio``. The business whose books these are.
ARK_ENTITY_REF = re.compile(r"^entity:[a-z0-9][a-z0-9:._-]*$")

#: Ark vessel refs, e.g. ``vessel:ryozan`` or ``vessel:example-studio:platform``. Software,
#: not a taxpayer -- never a tenant of a ledger.
ARK_VESSEL_REF = re.compile(r"^vessel:[a-z0-9][a-z0-9:._-]*$")

#: Ryozan's own vessel ref, for anything that needs to name the product.
RYOZAN_VESSEL = "vessel:ryozan"

MAX_TENANT_ID = 160


class TenantIdError(ValueError):
    """The tenant id is unusable as a key for a set of books."""


def normalize_tenant_id(tenant_id: str) -> str:
    """Validate and canonicalize a tenant id.

    Accepts an Ark entity ref or a plain identifier. Rejects a vessel ref
    outright: a vessel is software, and software does not owe taxes. Pointing
    books at ``vessel:example-studio:platform`` instead of ``entity:example-studio`` is the kind of
    mistake that is invisible until two deployments disagree about whose money
    they are counting.
    """
    if not isinstance(tenant_id, str):
        raise TenantIdError("tenant_id must be a string")
    value = tenant_id.strip()
    if not value:
        raise TenantIdError("tenant_id is required")
    if len(value) > MAX_TENANT_ID:
        raise TenantIdError(f"tenant_id is longer than {MAX_TENANT_ID} characters")
    if ARK_VESSEL_REF.match(value):
        raise TenantIdError(
            f"{value!r} is a vessel ref. Books belong to a business, not to software -- "
            "use the entity ref instead, e.g. entity:your-studio"
        )
    return value


def is_ark_entity(tenant_id: str) -> bool:
    """True when the tenant is named the way Ark names businesses."""
    return bool(ARK_ENTITY_REF.match(tenant_id))


def ark_entity_tenant(slug: str) -> str:
    """Build the canonical tenant id for an Ark entity slug.

    ``ark_entity_tenant("your-studio") == "entity:your-studio"``
    """
    clean = slug.strip().lower()
    if not clean:
        raise TenantIdError("an entity slug is required")
    if clean.startswith("entity:"):
        clean = clean[len("entity:") :]
    candidate = f"entity:{clean}"
    if not ARK_ENTITY_REF.match(candidate):
        raise TenantIdError(f"{slug!r} is not a usable Ark entity slug")
    return candidate
