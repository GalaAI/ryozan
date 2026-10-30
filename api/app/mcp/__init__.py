"""Ryozan MCP surface — Streamable HTTP transport over the existing v1 REST API.

No third-party MCP dependency. The transport is implemented directly on
FastAPI so the Railway image is unchanged.
"""

from app.mcp.router import mcp_router

__all__ = ["mcp_router"]
