"""OAuth 2.1 authorization server and token verifier for the remote Ryozan MCP.

The BTTCO account remains the only user identity. This module adds the remote
client grant layer required by ChatGPT, Claude, Gemini, and other MCP hosts.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session, get_db
from app.core.security import (
    create_user_access_token,
    get_current_user,
    password_hash_fingerprint,
    require_verified_collector,
    reset_token_matches_hash,
)
from app.models.mcp_oauth import McpOAuthAuthorizationCode, McpOAuthClient, McpOAuthRefreshToken
from app.models.user import BTTCOUser

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()
oauth_router = APIRouter(tags=["mcp-oauth"])

MCP_ACCESS_TOKEN_TYPE = "mcp_access"
MCP_AUTH_REQUEST_TYPE = "mcp_auth_request"
MCP_RESOURCE = f"{settings.API_PUBLIC_BASE_URL.rstrip('/')}/mcp"
MCP_ISSUER = settings.API_PUBLIC_BASE_URL.rstrip("/")
MCP_RESOURCE_METADATA = f"{MCP_ISSUER}/.well-known/oauth-protected-resource"

SHOP_READ_SCOPE = "ryozan.shop.read"
SHOP_SEND_SCOPE = "ryozan.shop.send"
SHOP_WRITE_SCOPE = "ryozan.shop.write"
DEV_READ_SCOPE = "ryozan.dev.read"
DEV_WRITE_SCOPE = "ryozan.dev.write"
SERVICE_READ_SCOPE = "ryozan.service.read"
DEV_EXECUTE_SCOPE = "ryozan.dev.execute"
DEV_BUILD_SCOPE = "ryozan.dev.build"
DEV_TEST_SCOPE = "ryozan.dev.test"
DEV_GIT_SCOPE = "ryozan.dev.git"
DEV_MIGRATE_SCOPE = "ryozan.dev.migrate"
DEV_DEPLOY_SCOPE = "ryozan.dev.deploy"
CLOUD_READ_SCOPE = "ryozan.cloud.read"
CLOUD_WRITE_SCOPE = "ryozan.cloud.write"
SCOPE_READ_SCOPE = "ryozan.scope.read"
SCOPE_WRITE_SCOPE = "ryozan.scope.write"
ADMIN_SCOPE = "ryozan.admin"
SUPPORTED_SCOPES = (
    SHOP_READ_SCOPE,
    SHOP_SEND_SCOPE,
    SHOP_WRITE_SCOPE,
    DEV_READ_SCOPE,
    DEV_WRITE_SCOPE,
    DEV_EXECUTE_SCOPE,
    DEV_BUILD_SCOPE,
    DEV_TEST_SCOPE,
    DEV_GIT_SCOPE,
    DEV_MIGRATE_SCOPE,
    DEV_DEPLOY_SCOPE,
    CLOUD_READ_SCOPE,
    CLOUD_WRITE_SCOPE,
    SCOPE_READ_SCOPE,
    SCOPE_WRITE_SCOPE,
    ADMIN_SCOPE,
    SERVICE_READ_SCOPE,
)

ROLE_SCOPES: dict[str, set[str]] = {
    "collector": {SHOP_READ_SCOPE, SERVICE_READ_SCOPE},
    # Booking changes still pass the REST assigned-artist and shop-manager gate.
    "assistant": {SHOP_READ_SCOPE, SHOP_WRITE_SCOPE, SERVICE_READ_SCOPE},
    "artist": {SHOP_READ_SCOPE, SHOP_WRITE_SCOPE, SERVICE_READ_SCOPE},
    "admin": {
        SHOP_READ_SCOPE,
        SHOP_SEND_SCOPE,
        SHOP_WRITE_SCOPE,
        DEV_READ_SCOPE,
        DEV_WRITE_SCOPE,
        SERVICE_READ_SCOPE,
    },
    "founder": set(SUPPORTED_SCOPES),
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _scope_set(value: str | None) -> set[str]:
    return {part for part in (value or "").split() if part}


def _grantable_scopes(requested: set[str], role: str) -> set[str] | None:
    """Narrow a request to what this role may grant (RFC 6749 section 3.3).

    One client asks every user for the same scopes, so an artist connecting
    their own account receives read and write but not send. A grant without
    shop read is useless and is refused.
    """
    granted = requested & ROLE_SCOPES.get(role, set())
    return granted if SHOP_READ_SCOPE in granted else None


def _canonical_scope(value: set[str]) -> str:
    return " ".join(scope for scope in SUPPORTED_SCOPES if scope in value)


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _redirect_uri_allowed(uri: str) -> bool:
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.fragment or not parsed.scheme or not parsed.netloc:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _redirect_with(uri: str, **params: str | None) -> str:
    supplied = [(key, value) for key, value in params.items() if value is not None]
    joiner = "&" if "?" in uri else "?"
    return f"{uri}{joiner}{urlencode(supplied)}"


class ClientRegistration(BaseModel):
    client_name: str = Field(min_length=1, max_length=255)
    redirect_uris: list[str] = Field(min_length=1, max_length=12)
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    token_endpoint_auth_method: str = "none"


class AuthorizationDecision(BaseModel):
    request_token: str
    approve: bool = True


def _decode_auth_request(value: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(value, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=400, detail="Authorization request is invalid or expired") from None
    if payload.get("typ") != MCP_AUTH_REQUEST_TYPE:
        raise HTTPException(status_code=400, detail="Authorization request type is invalid")
    return payload


async def _load_client(db: AsyncSession, client_id: str) -> McpOAuthClient | None:
    result = await db.execute(select(McpOAuthClient).where(McpOAuthClient.client_id == client_id))
    return result.scalar_one_or_none()


@oauth_router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@oauth_router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
async def protected_resource_metadata() -> dict[str, Any]:
    return {
        "resource": MCP_RESOURCE,
        "authorization_servers": [MCP_ISSUER],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{settings.FRONTEND_URL.rstrip('/')}/portal",
    }


@oauth_router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def authorization_server_metadata() -> dict[str, Any]:
    return {
        "issuer": MCP_ISSUER,
        "authorization_endpoint": f"{MCP_ISSUER}/oauth/authorize",
        "token_endpoint": f"{MCP_ISSUER}/oauth/token",
        "registration_endpoint": f"{MCP_ISSUER}/oauth/register",
        "revocation_endpoint": f"{MCP_ISSUER}/oauth/revoke",
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": False,
    }


@oauth_router.post("/oauth/register", include_in_schema=False, status_code=201)
async def register_client(body: ClientRegistration, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if body.token_endpoint_auth_method != "none":
        raise HTTPException(status_code=400, detail="Only public PKCE clients are supported")
    if set(body.grant_types) - {"authorization_code", "refresh_token"}:
        raise HTTPException(status_code=400, detail="Unsupported grant type")
    if body.response_types != ["code"]:
        raise HTTPException(status_code=400, detail="Only the code response type is supported")
    if any(not _redirect_uri_allowed(uri) for uri in body.redirect_uris):
        raise HTTPException(status_code=400, detail="A redirect URI is invalid")

    client_id = f"ryozan_{secrets.token_urlsafe(32)}"
    client = McpOAuthClient(
        client_id=client_id,
        client_name=body.client_name.strip(),
        redirect_uris=list(dict.fromkeys(body.redirect_uris)),
        grant_types=list(dict.fromkeys(body.grant_types)),
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    db.add(client)
    await db.flush()
    return {
        "client_id": client_id,
        "client_id_issued_at": int(client.created_at.timestamp()),
        "client_secret_expires_at": 0,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": client.grant_types,
        "response_types": client.response_types,
        "token_endpoint_auth_method": "none",
    }


@oauth_router.get("/oauth/authorize", include_in_schema=False)
async def begin_authorization(
    client_id: str,
    redirect_uri: str,
    response_type: str,
    code_challenge: str,
    code_challenge_method: str,
    resource: str = MCP_RESOURCE,
    scope: str = SHOP_READ_SCOPE,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    client = await _load_client(db, client_id)
    if not client or redirect_uri not in client.redirect_uris:
        return _oauth_error("invalid_request", "Client or redirect URI is invalid")
    if response_type != "code" or code_challenge_method != "S256" or len(code_challenge) < 43:
        return RedirectResponse(
            _redirect_with(redirect_uri, error="invalid_request", state=state, iss=MCP_ISSUER), status_code=302
        )
    if resource != MCP_RESOURCE:
        return RedirectResponse(
            _redirect_with(redirect_uri, error="invalid_target", state=state, iss=MCP_ISSUER), status_code=302
        )
    requested = _scope_set(scope)
    if not requested or requested - set(SUPPORTED_SCOPES):
        return RedirectResponse(
            _redirect_with(redirect_uri, error="invalid_scope", state=state, iss=MCP_ISSUER), status_code=302
        )

    now = _now()
    request_token = jwt.encode(
        {
            "typ": MCP_AUTH_REQUEST_TYPE,
            "sub": client.client_id,
            "client_name": client.client_name,
            "redirect_uri": redirect_uri,
            "scope": _canonical_scope(requested),
            "resource": resource,
            "state": state,
            "code_challenge": code_challenge,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    portal = f"{settings.FRONTEND_URL.rstrip('/')}/portal"
    return RedirectResponse(_redirect_with(portal, mcp_oauth_request=request_token), status_code=302)


@oauth_router.get("/api/v1/auth/mcp/authorize/preview", include_in_schema=False)
async def authorization_preview(
    request_token: str,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    payload = _decode_auth_request(request_token)
    requested = _scope_set(payload.get("scope"))
    allowed = ROLE_SCOPES.get(user.role, set())
    return {
        "client_name": payload.get("client_name") or "AI client",
        "requested_scopes": sorted(requested),
        "role": user.role,
        "can_approve": _grantable_scopes(requested, user.role) is not None,
        "blocked_scopes": sorted(requested - allowed),
    }


@oauth_router.post("/api/v1/auth/mcp/authorize", include_in_schema=False)
async def complete_authorization(
    body: AuthorizationDecision,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    payload = _decode_auth_request(body.request_token)
    client = await _load_client(db, str(payload["sub"]))
    redirect_uri = str(payload.get("redirect_uri") or "")
    if not client or redirect_uri not in client.redirect_uris:
        raise HTTPException(status_code=400, detail="OAuth client no longer exists")

    common = {"state": payload.get("state"), "iss": MCP_ISSUER}
    if not body.approve:
        return {"redirect_url": _redirect_with(redirect_uri, error="access_denied", **common)}

    requested = _grantable_scopes(_scope_set(payload.get("scope")), user.role)
    if requested is None:
        return {"redirect_url": _redirect_with(redirect_uri, error="invalid_scope", **common)}

    code = secrets.token_urlsafe(48)
    db.add(
        McpOAuthAuthorizationCode(
            code_hash=_hash(code),
            client_id=client.client_id,
            user_id=user.id,
            redirect_uri=redirect_uri,
            scope=_canonical_scope(requested),
            resource=str(payload["resource"]),
            code_challenge=str(payload["code_challenge"]),
            expires_at=_now() + timedelta(minutes=5),
        )
    )
    await db.flush()
    return {"redirect_url": _redirect_with(redirect_uri, code=code, **common)}


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _create_mcp_access_token(user: BTTCOUser, client_id: str, scope: str, resource: str) -> str:
    now = _now()
    return jwt.encode(
        {
            "sub": str(user.id),
            "typ": MCP_ACCESS_TOKEN_TYPE,
            "role": user.role,
            "client_id": client_id,
            "scope": scope,
            "aud": resource,
            "iss": MCP_ISSUER,
            "pwf": password_hash_fingerprint(user.password_hash),
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


async def _issue_tokens(
    db: AsyncSession,
    user: BTTCOUser,
    client_id: str,
    scope: str,
    resource: str,
) -> dict[str, Any]:
    refresh = secrets.token_urlsafe(64)
    db.add(
        McpOAuthRefreshToken(
            token_hash=_hash(refresh),
            client_id=client_id,
            user_id=user.id,
            scope=scope,
            resource=resource,
            expires_at=_now() + timedelta(days=30),
        )
    )
    await db.flush()
    return {
        "access_token": _create_mcp_access_token(user, client_id, scope, resource),
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": refresh,
        "scope": scope,
    }


@oauth_router.post("/oauth/token", include_in_schema=False)
async def token_endpoint(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    grant_type = str(form.get("grant_type") or "")
    client_id = str(form.get("client_id") or "")
    supplied_resource = str(form.get("resource") or "")
    client = await _load_client(db, client_id)
    if not client:
        return _oauth_error("invalid_client", "Unknown OAuth client", 401)

    if grant_type == "authorization_code":
        code = str(form.get("code") or "")
        verifier = str(form.get("code_verifier") or "")
        redirect_uri = str(form.get("redirect_uri") or "")
        result = await db.execute(
            select(McpOAuthAuthorizationCode)
            .where(McpOAuthAuthorizationCode.code_hash == _hash(code))
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        resource = supplied_resource or (record.resource if record else "")
        if (
            not record
            or record.used_at is not None
            or record.expires_at <= _now()
            or record.client_id != client_id
            or record.redirect_uri != redirect_uri
            or record.resource != resource
            or not secrets.compare_digest(_pkce_challenge(verifier), record.code_challenge)
        ):
            return _oauth_error("invalid_grant", "Authorization code is invalid or expired")
        record.used_at = _now()
        user = await db.get(BTTCOUser, record.user_id)
        if not user:
            return _oauth_error("invalid_grant", "User account no longer exists")
        if not _grant_matches_mailbox_ownership(user, record):
            return _oauth_error("invalid_grant", "Verify your mailbox and authorize this client again")
        if not _scope_set(record.scope) <= ROLE_SCOPES.get(user.role, set()):
            return _oauth_error("invalid_scope", "Stored grant exceeds the current Ryozan role")
        return await _issue_tokens(db, user, client_id, record.scope, resource)

    if grant_type == "refresh_token":
        refresh = str(form.get("refresh_token") or "")
        result = await db.execute(
            select(McpOAuthRefreshToken).where(McpOAuthRefreshToken.token_hash == _hash(refresh)).with_for_update()
        )
        record = result.scalar_one_or_none()
        resource = supplied_resource or (record.resource if record else "")
        if (
            not record
            or record.revoked_at is not None
            or record.expires_at <= _now()
            or record.client_id != client_id
            or record.resource != resource
        ):
            return _oauth_error("invalid_grant", "Refresh token is invalid or expired")
        record.revoked_at = _now()
        user = await db.get(BTTCOUser, record.user_id)
        if not user:
            return _oauth_error("invalid_grant", "User account no longer exists")
        if not _grant_matches_mailbox_ownership(user, record):
            return _oauth_error("invalid_grant", "Verify your mailbox and authorize this client again")
        if not _scope_set(record.scope) <= ROLE_SCOPES.get(user.role, set()):
            record.revoked_at = _now()
            return _oauth_error("invalid_scope", "Stored grant exceeds the current Ryozan role")
        return await _issue_tokens(db, user, client_id, record.scope, resource)

    return _oauth_error("unsupported_grant_type", "Grant type is not supported")


@oauth_router.post("/oauth/revoke", include_in_schema=False)
async def revoke_endpoint(request: Request, db: AsyncSession = Depends(get_db)):
    """RFC 7009 revocation for refresh tokens, so a disconnect ends the grant.

    Access tokens are short-lived signed tokens and expire on their own. Per the
    RFC the response is 200 for unknown or already revoked tokens; only the
    issuing client may revoke its own token.
    """
    form = await request.form()
    client_id = str(form.get("client_id") or "")
    token = str(form.get("token") or "")
    if not await _load_client(db, client_id):
        return _oauth_error("invalid_client", "Unknown OAuth client", 401)
    record = await db.get(McpOAuthRefreshToken, _hash(token)) if token else None
    if record and record.client_id == client_id and record.revoked_at is None:
        record.revoked_at = _now()
    return JSONResponse({}, status_code=200)


def _grant_matches_mailbox_ownership(user: BTTCOUser, record) -> bool:
    if user.role != "collector":
        return True
    # Historical grants carry neither email nor password proof. Even after
    # the owner verifies, a preclaimant must not exchange an outstanding grant
    # for fresh credentials. The existing durable timestamp retires all grants
    # from before the current mailbox proof without a historical backfill.
    verified_at = user.email_verified_at
    created_at = getattr(record, "created_at", None)
    return bool(verified_at and created_at and created_at >= verified_at)


@dataclass(frozen=True)
class McpActor:
    user: BTTCOUser | None
    role: str
    scopes: set[str]
    site_token: str


async def authenticate_mcp_token(token: str) -> McpActor | None:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=MCP_RESOURCE,
            issuer=MCP_ISSUER,
        )
    except JWTError:
        return None
    if payload.get("typ") != MCP_ACCESS_TOKEN_TYPE or not payload.get("sub") or not payload.get("pwf"):
        return None
    scopes = _scope_set(payload.get("scope"))
    if not scopes or scopes - set(SUPPORTED_SCOPES):
        return None
    async with async_session() as db:
        try:
            user = await db.get(BTTCOUser, UUID(str(payload["sub"])))
        except (TypeError, ValueError):
            return None
        if not user or not reset_token_matches_hash(payload, user.password_hash):
            return None
        try:
            require_verified_collector(user)
        except HTTPException:
            return None
        if payload.get("role") != user.role or not scopes <= ROLE_SCOPES.get(user.role, set()):
            return None
        return McpActor(
            user=user,
            role=user.role,
            scopes=scopes,
            site_token=create_user_access_token(user, expires_delta=timedelta(minutes=5)),
        )


def mcp_auth_challenge() -> str:
    return f'Bearer resource_metadata="{MCP_RESOURCE_METADATA}", scope="{SHOP_READ_SCOPE}"'
