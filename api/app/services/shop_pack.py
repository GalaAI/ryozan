"""
Shop brand pack: this install's shop-branded standalone pages, served same-origin.

A pack is a set of versioned folders published outside this repository (see
scripts/publish_shop_pack.py and docs/SHOP_BRAND_PACKS.md), for example
<SHOP_PACK_BASE_URL>/@<scope>/email-foundry/1.0.0/adapter.js.
GET /dependencies/@<scope>/... streams those files from SHOP_PACK_BASE_URL.

Why a proxy and not a redirect: a pack host (today app/src/components/EmailFoundryFrame.tsx)
fetch()es the pack document and adapter, then writes them into a same-origin srcdoc
iframe under <base href="/dependencies/...">. Every font, script, JSON and image the
pack loads after that is relative to /dependencies.
A cross-origin redirect would need CORS on the bucket for fetch(), @font-face and
WebGL textures, and the API's Content-Security-Policy only allows same-origin scripts
and fonts. Proxying keeps the pack on this origin.

Only files under @<SHOP_PACK_SCOPE>/ with an allowlisted extension are served; the
Content-Type comes from the extension, never from the upstream.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

if TYPE_CHECKING:
    from app.core.config import Settings

# The largest pack file today is under 9 MB; anything past this is not a web asset.
PACK_MAX_BYTES = 16 * 1024 * 1024
PACK_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
PACK_CACHE_CONTROL = "public, max-age=300"

PACK_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    # In-browser Babel sources; Babel fetches them as text.
    ".jsx": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".otf": "font/otf",
    ".ttf": "font/ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}

# Forwarded so the browser's revalidation reaches the bucket and a 304 comes back.
_CONDITIONAL_HEADERS = ("if-none-match", "if-modified-since")
_VALIDATOR_HEADERS = ("etag", "last-modified")


def pack_file_path(path: str, scope: str) -> str | None:
    """
    The validated pack path, or None when it must 404.

    Accepts only @<scope>/<segment>/.../<file.ext>: no absolute paths, backslashes,
    control characters, empty, dot or hidden segments, and an allowlisted extension.
    """
    if not scope or not path or path.startswith("/") or "\\" in path:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
        return None
    segments = path.split("/")
    if len(segments) < 2 or segments[0] != f"@{scope}":
        return None
    if any(not segment or segment.startswith(".") for segment in segments[1:]):
        return None
    if posixpath.normpath(path) != path:
        return None
    if posixpath.splitext(segments[-1])[1].lower() not in PACK_CONTENT_TYPES:
        return None
    return path


def pack_upstream_url(path: str, base_url: str) -> str:
    """The upstream URL for a validated pack path: the same path, percent-encoded once."""
    return f"{base_url.rstrip('/')}/{quote(path, safe='/@')}"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


def _bad_gateway() -> HTTPException:
    return HTTPException(status_code=502, detail="Shop pack file could not be loaded")


async def serve_pack_file(path: str, request: Request, settings: Settings) -> Response:
    """Stream one pack file from SHOP_PACK_BASE_URL, or raise 404/502."""
    if not settings.shop_pack_enabled:
        raise _not_found()
    pack_path = pack_file_path(path, settings.SHOP_PACK_SCOPE)
    if pack_path is None:
        raise _not_found()

    extension = posixpath.splitext(pack_path)[1].lower()
    headers = {"Accept-Encoding": "identity"}
    for name in _CONDITIONAL_HEADERS:
        if value := request.headers.get(name):
            headers[name] = value

    client = httpx.AsyncClient(timeout=PACK_TIMEOUT, follow_redirects=False)
    try:
        upstream = await client.send(
            client.build_request("GET", pack_upstream_url(pack_path, settings.SHOP_PACK_BASE_URL), headers=headers),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise _bad_gateway() from exc

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    response_headers = {"Cache-Control": PACK_CACHE_CONTROL}
    for name in _VALIDATOR_HEADERS:
        if value := upstream.headers.get(name):
            response_headers[name] = value

    if upstream.status_code == 304:
        await close()
        return Response(status_code=304, headers=response_headers)
    if upstream.status_code != 200:
        await close()
        # A bucket answers 403 or 404 for a key it does not hold: the pack does not provide the file.
        raise _not_found() if upstream.status_code in (403, 404) else _bad_gateway()

    declared = upstream.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit() or int(declared) > PACK_MAX_BYTES:
            await close()
            raise _bad_gateway()
        if not upstream.headers.get("content-encoding"):
            # A compressed answer despite identity is decoded on the way through, so its length changes.
            response_headers["Content-Length"] = declared
    if extension == ".svg":
        # An SVG opened directly must not run script on this origin.
        response_headers["Content-Security-Policy"] = "sandbox"

    async def body():
        sent = 0
        try:
            async for chunk in upstream.aiter_bytes():
                sent += len(chunk)
                if sent > PACK_MAX_BYTES:
                    # Undeclared length past the cap: end the body short rather than relay it.
                    return
                yield chunk
        finally:
            await close()

    return StreamingResponse(
        body(),
        media_type=PACK_CONTENT_TYPES[extension],
        headers=response_headers,
        background=BackgroundTask(close),
    )
