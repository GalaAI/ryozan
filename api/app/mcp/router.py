"""MCP Streamable HTTP transport for Ryozan, implemented on FastAPI directly.

Stateless and JSON-response only, which the Streamable HTTP spec permits:
no session id is issued and GET (server-initiated SSE) is answered 405.
That keeps the transport dependency-free so the Railway image is unchanged.

Auth: remote clients use BTTCO OAuth. The MCP grant is verified and translated
to a short-lived internal site token, so REST role checks remain authoritative
without accepting an MCP audience token on ordinary API routes.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.mcp.oauth import McpActor, authenticate_mcp_token, mcp_auth_challenge
from app.mcp.tools import TOOLS_BY_NAME, public_tool_list, tool_allowed
from app.mcp.upstream import UpstreamError

logger = logging.getLogger("ryozan.mcp")

mcp_router = APIRouter(tags=["mcp"])

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2025-06-18"

SERVER_INFO = {"name": "ryozan", "title": "Ryozan Studio", "version": "1.3.0"}
ANONYMOUS_SERVICE_ACTOR = McpActor(
    user=None,
    role="collector",
    scopes={"ryozan.service.read"},
    site_token="",
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    return None


def _text_result(body: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": body}], "isError": is_error}


async def _handle_initialize(message: dict) -> dict:
    requested = (message.get("params") or {}).get("protocolVersion")
    negotiated = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
    return _result(
        message.get("id"),
        {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Ryozan is the authenticated shop-operations boundary. Tool discovery is filtered "
                "by the signed-in Ryozan role and granted OAuth scopes. Client delivery is exposed "
                "only through the explicit ryozan.shop.send scope and Courier channel gates. "
                "Booking reschedule and cancel need ryozan.shop.write and the REST staff gates; this "
                "server never moves money. Axiom routes Ark and development "
                "operations through Hivemind; Ryozan development coordination tools appear only for "
                "admin or founder grants that explicitly request development scopes."
            ),
        },
    )


async def _handle_tools_call(message: dict, actor: McpActor) -> dict:
    params = message.get("params") or {}
    name = params.get("name")
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        return _error(message.get("id"), INVALID_PARAMS, f"Unknown tool: {name}")
    if not tool_allowed(name, actor.role, actor.scopes):
        return _result(
            message.get("id"),
            _text_result("This Ryozan account or OAuth grant cannot use that tool.", is_error=True),
        )

    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(message.get("id"), INVALID_PARAMS, "arguments must be an object")

    try:
        body = await spec["handler"](actor.site_token, arguments)
    except UpstreamError as exc:
        return _result(message.get("id"), _text_result(str(exc), is_error=True))
    except (TypeError, ValueError) as exc:
        return _result(message.get("id"), _text_result(f"Bad argument: {exc}", is_error=True))
    except Exception:
        logger.exception("MCP tool %s failed", name)
        return _result(message.get("id"), _text_result("Tool failed unexpectedly. Nothing was changed.", True))

    return _result(message.get("id"), _text_result(body))


def _can_dispatch_without_auth(message: dict) -> bool:
    method = message.get("method")
    if method in {"initialize", "ping"}:
        return True
    if isinstance(method, str) and method.startswith("notifications/"):
        return True
    if method != "tools/call":
        return False
    params = message.get("params") or {}
    return params.get("name") == "ryozan_service_health"


async def _dispatch(message: dict, actor: McpActor) -> dict | None:
    """Return a JSON-RPC response, or None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "Expected a JSON-RPC 2.0 message")

    method = message.get("method")
    is_notification = "id" not in message

    if method == "initialize":
        return await _handle_initialize(message)
    if method == "ping":
        return _result(message.get("id"), {})
    if method == "tools/list":
        return _result(message.get("id"), {"tools": public_tool_list(actor.role, actor.scopes)})
    if method == "tools/call":
        return await _handle_tools_call(message, actor)
    if method and method.startswith("notifications/"):
        return None
    if is_notification:
        return None
    return _error(message.get("id"), METHOD_NOT_FOUND, f"Method not supported: {method}")


@mcp_router.post("/mcp", include_in_schema=False)
async def mcp_endpoint(request: Request) -> Response:
    """Single Streamable HTTP entry point. JSON in, JSON out."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(_error(None, PARSE_ERROR, "Body is not valid JSON"), status_code=400)

    token = _bearer(request)
    actor = await authenticate_mcp_token(token) if token else None
    if actor is None:
        if isinstance(payload, list):
            if not all(isinstance(m, dict) and _can_dispatch_without_auth(m) for m in payload):
                return JSONResponse(
                    {"error": "unauthorized", "error_description": "Ryozan account authorization is required."},
                    status_code=401,
                    headers={"WWW-Authenticate": mcp_auth_challenge()},
                )
        elif not isinstance(payload, dict) or not _can_dispatch_without_auth(payload):
            return JSONResponse(
                {"error": "unauthorized", "error_description": "Ryozan account authorization is required."},
                status_code=401,
                headers={"WWW-Authenticate": mcp_auth_challenge()},
            )
        actor = ANONYMOUS_SERVICE_ACTOR

    if isinstance(payload, list):
        responses = [r for r in [await _dispatch(m, actor) for m in payload] if r is not None]
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)

    response = await _dispatch(payload, actor)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)


@mcp_router.get("/mcp", include_in_schema=False)
async def mcp_sse_not_supported() -> Response:
    """Server-initiated SSE is not offered. Declaring it beats falling through
    to the SPA catch-all, which would return index.html and confuse clients."""
    return JSONResponse(
        _error(None, INTERNAL_ERROR, "This MCP server is JSON-response only. Use POST."),
        status_code=405,
        headers={"Allow": "POST"},
    )
