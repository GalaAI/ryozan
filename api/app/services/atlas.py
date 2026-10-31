"""Atlas — frontend-to-backend app map builder.

Atlas turns a connected backend into a navigation graph: route groups -> endpoints
-> (inferred) data tables, plus live row counts where reachable. It is the visual
orientation layer of Viber (the app-applied form of the Atlas scroll).

v1 is intentionally introspection-driven and degrades gracefully:
  - If the backend exposes OpenAPI, we group its paths into resource clusters.
  - Row counts come from a provided table-count map (the connector supplies them;
    for BTTCO that is the bttco_query path). Absent counts -> node marked unknown.

The output is a typed graph (nodes + edges) the web UI renders as a mind-map.
No assumptions are baked as fact: a node only claims a table/count when the data
is present; otherwise it is flagged inferred/unknown.
"""

from __future__ import annotations

import re
from typing import Any


def _resource_from_path(path: str) -> str:
    """Group an OpenAPI path into a resource cluster.

    /api/v1/workspaces/{slug}/notes -> 'workspaces'
    /api/v1/bookings                -> 'bookings'
    """
    # strip the version prefix and leading slash
    cleaned = re.sub(r"^/?api/v\d+/?", "", path.lstrip("/"))
    first = cleaned.split("/")[0] if cleaned else ""
    # drop path params like {slug}
    if first.startswith("{") or not first:
        return "root"
    return first


def build_atlas_graph(
    *,
    connection_name: str,
    openapi_paths: list[str] | None,
    table_counts: dict[str, int] | None = None,
    verdicts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce a node/edge graph for the Atlas mind-map.

    Args:
      openapi_paths: list of route strings from the backend's OpenAPI doc.
      table_counts: optional {table_name: live_row_count}. When present, a
        resource cluster whose name matches (singular/plural) gets the count.
      verdicts: optional {resource: {status, verdict}} from Oversight to overlay.

    Returns a dict: { nodes: [...], edges: [...], meta: {...} }.
    """
    paths = openapi_paths or []
    table_counts = table_counts or {}
    verdicts = verdicts or {}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Root node = the connected app.
    root_id = "app"
    nodes.append({"id": root_id, "type": "app", "label": connection_name})

    # Group paths into resource clusters.
    clusters: dict[str, list[str]] = {}
    for p in paths:
        res = _resource_from_path(p)
        clusters.setdefault(res, []).append(p)

    def match_table(resource: str) -> tuple[str | None, int | None]:
        # try exact, plural, and ryozan_-prefixed variants
        candidates = [
            resource,
            f"ryozan_{resource}",
            f"{resource}s",
            f"ryozan_{resource}s",
            resource.rstrip("s"),
            f"ryozan_{resource.rstrip('s')}",
        ]
        for c in candidates:
            if c in table_counts:
                return c, table_counts[c]
        return None, None

    for resource, res_paths in sorted(clusters.items()):
        res_node_id = f"res:{resource}"
        v = verdicts.get(resource, {})
        table_name, count = match_table(resource)
        nodes.append(
            {
                "id": res_node_id,
                "type": "resource",
                "label": resource,
                "endpoint_count": len(res_paths),
                "table": table_name,
                "live_rows": count,
                "rows_known": count is not None,
                "status": v.get("status"),
                "verdict": v.get("verdict"),
            }
        )
        edges.append({"from": root_id, "to": res_node_id})

        # Endpoint leaf nodes (cap to keep the map legible; note if truncated).
        shown = res_paths[:8]
        for i, p in enumerate(shown):
            ep_id = f"ep:{resource}:{i}"
            nodes.append({"id": ep_id, "type": "endpoint", "label": p})
            edges.append({"from": res_node_id, "to": ep_id})
        if len(res_paths) > len(shown):
            more_id = f"ep:{resource}:more"
            nodes.append(
                {
                    "id": more_id,
                    "type": "endpoint-more",
                    "label": f"+{len(res_paths) - len(shown)} more endpoints",
                }
            )
            edges.append({"from": res_node_id, "to": more_id})

        # Table leaf if we matched one.
        if table_name:
            tbl_id = f"tbl:{table_name}"
            nodes.append(
                {
                    "id": tbl_id,
                    "type": "table",
                    "label": table_name,
                    "live_rows": count,
                    "rows_known": count is not None,
                }
            )
            edges.append({"from": res_node_id, "to": tbl_id})

    meta = {
        "connection_name": connection_name,
        "resource_count": len(clusters),
        "endpoint_count": len(paths),
        "tables_matched": sum(1 for r in clusters if match_table(r)[0]),
        "has_counts": bool(table_counts),
        "degraded": not paths,  # no OpenAPI -> limited map
    }
    return {"nodes": nodes, "edges": edges, "meta": meta}
