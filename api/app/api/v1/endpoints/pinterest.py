"""Pinterest API v5 integration (app "GalaX", Trial access).

OAuth connect flow mirrors google_auth.py:
  1. GET  /auth/pinterest/connect     -> returns the authorize URL (state = user_id)
  2. GET  /auth/pinterest/callback    -> exchanges code, stores refresh_token
  3. GET  /auth/pinterest/status      -> connected? + username
  4. DELETE /auth/pinterest/disconnect-> clears the stored token
  5. GET  /auth/pinterest/boards      -> proves the connection (lists boards)

Pinterest specifics (verified against API v5 docs, 2026-06):
  - Token endpoint: POST {base}/oauth/token, Authorization: Basic b64(client:secret),
    Content-Type application/x-www-form-urlencoded.
  - Continuous refresh tokens (60-day, refreshable indefinitely). We store the
    refresh token and mint short-lived access tokens on demand.
  - Scopes requested: PINTEREST_SCOPES below (boards:read, boards:write,
    pins:read, pins:write, user_accounts:read).

Connect, callback, status, disconnect, boards and booking references accept
any signed-in user (collectors build reference boards). Analytics, pin
analytics, flash pinning and board ingest need admin or higher (_require_staff).
Trial access has reduced rate limits; callers should treat read calls as
cacheable and avoid tight loops.
"""

import base64
import logging
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.user import User

logger = logging.getLogger("ryozan.pinterest")

router = APIRouter(prefix="/auth/pinterest", tags=["pinterest"])

# Scopes for content + read use. boards:write/pins:write require Standard access
# (video-demo review); requested here so granting Standard upgrades in place.
PINTEREST_SCOPES = ["boards:read", "boards:write", "pins:read", "pins:write", "user_accounts:read"]
AUTHORIZE_URL = "https://www.pinterest.com/oauth/"


def _require_staff(user: User) -> None:
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("admin", 99):
        raise HTTPException(status_code=403, detail="Requires admin role or higher")


def _api_base() -> str:
    """Host-aware base: sandbox or prod, matching the service module."""
    s = get_settings()
    base = s.PINTEREST_SANDBOX_API_BASE if s.PINTEREST_USE_SANDBOX else s.PINTEREST_API_BASE
    return base.rstrip("/")


def _basic_auth_header() -> str:
    settings = get_settings()
    raw = f"{settings.PINTEREST_CLIENT_ID}:{settings.PINTEREST_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def _exchange_code(code: str) -> dict:
    settings = get_settings()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.PINTEREST_REDIRECT_URI,
        "continuous_refresh": "true",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_api_base()}/oauth/token",
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
        )
    if resp.status_code != 200:
        logger.error("Pinterest token exchange failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=502, detail="Pinterest token exchange failed")
    return resp.json()


async def get_access_token(user: User) -> str:
    """Return a usable access token.

    Prefers a directly-stored access token (e.g. a dashboard-issued token), and
    falls back to minting one from the stored OAuth refresh token. Either path
    yields a Bearer token for the Pinterest v5 API.
    """
    if user.pinterest_access_token:
        return user.pinterest_access_token
    if not user.pinterest_refresh_token:
        raise HTTPException(status_code=400, detail="Pinterest not connected")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": user.pinterest_refresh_token,
        "scope": " ".join(PINTEREST_SCOPES),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_api_base()}/oauth/token",
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
        )
    if resp.status_code != 200:
        logger.error("Pinterest token refresh failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=502, detail="Pinterest token refresh failed")
    return resp.json()["access_token"]


@router.get("/connect")
async def pinterest_connect(
    current_user: User = Depends(get_current_user),
):
    """Return the Pinterest authorize URL. state encodes the user_id.

    Open to any authenticated user: collectors connect their OWN Pinterest to
    build a reference board for their booking. The token is scoped to their own
    user row; shop-level actions (boards/ingest/pin-flash) remain staff-only.
    """
    settings = get_settings()
    if not settings.PINTEREST_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Pinterest client id not configured")
    # Signed, short-lived state binds the callback to THIS user. A plaintext
    # user_id would be forgeable (OAuth CSRF: an attacker could attach a victim's
    # id to their own auth code). JWT-sign it with the app secret instead.
    from datetime import timedelta

    from app.core.security import create_access_token

    state = create_access_token(
        {"sub": str(current_user.id), "purpose": "pinterest_oauth"},
        expires_delta=timedelta(minutes=15),
    )
    params = {
        "client_id": settings.PINTEREST_CLIENT_ID,
        "redirect_uri": settings.PINTEREST_REDIRECT_URI,
        "response_type": "code",
        "scope": ",".join(PINTEREST_SCOPES),
        "state": state,
    }
    return {"authorize_url": f"{AUTHORIZE_URL}?{urlencode(params)}"}


@router.get("/callback")
async def pinterest_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Handle the redirect from Pinterest. Exchange code, store refresh token."""
    settings = get_settings()
    # Verify the signed state (issued by /connect). Rejects forged/expired state.
    from jose import JWTError, jwt

    try:
        claims = jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if claims.get("purpose") != "pinterest_oauth":
            raise ValueError("wrong purpose")
        user_id = claims["sub"]
    except (JWTError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter") from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    tokens = await _exchange_code(code)
    refresh = tokens.get("refresh_token")
    if not refresh:
        return RedirectResponse(
            url=(
                f"{settings.FRONTEND_URL.rstrip('/')}"
                "/dashboard?portal=hub&workflow=connections&pinterest=error&reason=no_refresh_token"
            )
        )
    user.pinterest_refresh_token = refresh

    # Best-effort: capture the username for display.
    try:
        access = tokens.get("access_token")
        if access:
            async with httpx.AsyncClient(timeout=10.0) as client:
                me = await client.get(
                    f"{_api_base()}/user_account",
                    headers={"Authorization": f"Bearer {access}"},
                )
                if me.status_code == 200:
                    user.pinterest_username = me.json().get("username")
    except Exception:
        pass

    await db.commit()
    return RedirectResponse(
        url=(f"{settings.FRONTEND_URL.rstrip('/')}/dashboard?portal=hub&workflow=connections&pinterest=connected")
    )


@router.get("/status")
async def pinterest_status(
    current_user: User = Depends(get_current_user),
):
    # Any user can see their own connection state.
    return {
        "connected": bool(current_user.pinterest_refresh_token or current_user.pinterest_access_token),
        "username": current_user.pinterest_username,
    }


@router.delete("/disconnect", status_code=204)
async def pinterest_disconnect(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    # Any user can disconnect their own account.
    current_user.pinterest_refresh_token = None
    current_user.pinterest_access_token = None
    current_user.pinterest_username = None
    await db.commit()


@router.get("/boards")
async def pinterest_boards(
    current_user: User = Depends(get_current_user),
):
    """List the caller's own connected boards (uses their own token)."""
    from app.services import pinterest as pin

    access = await get_access_token(current_user)
    try:
        return await pin.list_boards(access, page_size=50)
    except pin.PinterestError as e:
        raise HTTPException(status_code=502, detail=f"Pinterest: {e.detail}") from e


@router.get("/analytics")
async def pinterest_account_analytics(
    days: int = Query(default=30, ge=1, le=90),
    start_date: str | None = Query(default=None, description="YYYY-MM-DD (UTC); overrides days"),
    end_date: str | None = Query(default=None, description="YYYY-MM-DD (UTC); overrides days"),
    current_user: User = Depends(get_current_user),
):
    """Account analytics for Axiom SEO/product reporting.

    Defaults to the last `days` (max 90, per Pinterest's window). Returns the
    metric set (impressions, pin clicks, outbound clicks, saves, engagement).
    Staff-only. Verified against Pinterest OpenAPI v5.23.0.
    """
    _require_staff(current_user)
    from datetime import UTC, datetime, timedelta

    from app.services import pinterest as pin

    if not (start_date and end_date):
        today = datetime.now(UTC).date()
        end_date = today.isoformat()
        start_date = (today - timedelta(days=days)).isoformat()

    access = await get_access_token(current_user)
    try:
        return await pin.get_user_analytics(access, start_date=start_date, end_date=end_date)
    except pin.PinterestError as e:
        raise HTTPException(status_code=502, detail=f"Pinterest: {e.detail}") from e


@router.get("/pins/{pin_id}/analytics")
async def pinterest_pin_analytics(
    pin_id: str,
    days: int = Query(default=30, ge=1, le=90),
    current_user: User = Depends(get_current_user),
):
    """Per-pin analytics (which designs/pins drive clicks). Staff-only."""
    _require_staff(current_user)
    from datetime import UTC, datetime, timedelta

    from app.services import pinterest as pin

    today = datetime.now(UTC).date()
    access = await get_access_token(current_user)
    try:
        return await pin.get_pin_analytics(
            access,
            pin_id,
            start_date=(today - timedelta(days=days)).isoformat(),
            end_date=today.isoformat(),
        )
    except pin.PinterestError as e:
        raise HTTPException(status_code=502, detail=f"Pinterest: {e.detail}") from e


@router.post("/booking-references/{booking_id}/{board_id}")
async def pinterest_booking_references(
    booking_id: str,
    board_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Collector reference board: pull pins from the collector's own Pinterest
    board into their booking's reference_urls, so the artist sees the visual
    references. Read-only on Pinterest (works on Trial/sandbox). The caller must
    own the booking (be its client) or be staff.
    """
    import json as _json
    from uuid import UUID as _UUID

    from app.models.booking import Booking
    from app.services import pinterest as pin

    try:
        booking_uuid = _UUID(booking_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid booking id") from exc

    result = await db.execute(select(Booking).where(Booking.id == booking_uuid))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    is_staff = ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 99)
    if not is_staff and booking.client_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your booking")

    # References belong to the booking's client, so pull from the client's
    # Pinterest token, not the staff caller's. Collector-self keeps using their own.
    token_owner = current_user
    if is_staff and booking.client_id:
        client_result = await db.execute(select(User).where(User.id == booking.client_id))
        client = client_result.scalar_one_or_none()
        if not client or not (client.pinterest_refresh_token or client.pinterest_access_token):
            raise HTTPException(status_code=400, detail="Booking client has not connected Pinterest")
        token_owner = client

    access = await get_access_token(token_owner)
    try:
        data = await pin.list_board_pins(access, board_id, page_size=50)
    except pin.PinterestError as e:
        raise HTTPException(status_code=502, detail=f"Pinterest: {e.detail}") from e

    # Extract image URLs from the pins.
    urls: list[str] = []
    for p in data.get("items", []):
        media = p.get("media") or {}
        images = media.get("images") or {}
        url = None
        if images:
            pref = images.get("1200x") or images.get("600x") or next(iter(images.values()), None)
            if isinstance(pref, dict):
                url = pref.get("url")
        url = url or media.get("image_cover_url")
        if url:
            urls.append(url)

    # Merge into the booking's reference_urls (dedup), preserving any existing.
    existing: list[str] = []
    if booking.reference_urls:
        try:
            existing = _json.loads(booking.reference_urls)
            if not isinstance(existing, list):
                existing = []
        except (ValueError, TypeError):
            existing = []
    merged = list(dict.fromkeys([*existing, *urls]))
    # 50 is the per-booking reference ceiling; a large board cannot flood it.
    merged = merged[:50]
    booking.reference_urls = _json.dumps(merged)
    await db.commit()

    return {
        "booking_id": booking_id,
        "board_id": board_id,
        "pins_pulled": len(urls),
        "total_references": len(merged),
    }


async def _find_or_create_board(access: str, name: str, description: str = "") -> str:
    """Return the board id for `name`, creating it if absent. Requires boards:write
    to create (read-only tokens will 401 on create)."""
    from app.services import pinterest as pin

    boards = (await pin.list_boards(access, page_size=50)).get("items", [])
    match = next((b for b in boards if (b.get("name") or "").lower() == name.lower()), None)
    if match:
        return match["id"]
    created = await pin.create_board(access, name=name, description=description)
    return created["id"]


@router.post("/pin-flash/{design_id}")
async def pinterest_pin_flash(
    design_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Create a Pin for a published flash design, linking to the shop.

    Requires boards:write + pins:write on the connected token. Idempotency is
    the caller's concern for now (one pin per call). Pins to a 'Flash' board,
    created on first use.
    """
    _require_staff(current_user)
    from uuid import UUID as _UUID

    from app.models.flash import FlashDesign
    from app.services import pinterest as pin

    result = await db.execute(select(FlashDesign).where(FlashDesign.id == _UUID(design_id)))
    design = result.scalar_one_or_none()
    if not design:
        raise HTTPException(status_code=404, detail="Flash design not found")
    if not design.image_url:
        raise HTTPException(status_code=400, detail="Flash design has no image")

    settings = get_settings()
    access = await get_access_token(current_user)
    shop_url = f"{settings.FRONTEND_URL}/dashboard/shop"
    name = design.name or "Flash design"
    price = f" - ${int(design.price)}" if design.price else ""
    try:
        board_id = await _find_or_create_board(
            access, "Flash", f"Available flash designs at {settings.STUDIO_NAME}. Tap to book."
        )
        created = await pin.create_pin(
            access,
            board_id=board_id,
            image_url=design.image_url,
            title=f"{name}{price}",
            description=f"{name} flash design{price}. Available to book at {settings.STUDIO_NAME}",
            link=shop_url,
            alt_text=f"{name} tattoo flash design",
        )
    except pin.PinterestError as e:
        # Surface the scope problem clearly when the token lacks write access.
        detail = e.detail
        if "boards:write" in detail or "pins:write" in detail:
            detail = "Token missing write scopes (boards:write, pins:write). Regenerate the Pinterest token with write access."
        raise HTTPException(status_code=502, detail=detail) from e

    return {
        "design_id": design_id,
        "board_id": board_id,
        "pin_id": created.get("id"),
        "pin_url": f"https://www.pinterest.com/pin/{created.get('id')}/",
    }


@router.post("/ingest/{board_id}")
async def pinterest_ingest_board(
    board_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Ingest pins from a board into the References library.

    Read-only on Pinterest (pins:read + boards:read). Creates one Reference row
    per pin image, tagged source:pinterest and pin:<id> for dedup. Skips pins
    already ingested. Powers the reference library from saved Pinterest content.
    """
    _require_staff(current_user)
    from app.models.reference import Reference
    from app.services import pinterest as pin

    access = await get_access_token(current_user)
    try:
        data = await pin.list_board_pins(access, board_id, page_size=50)
    except pin.PinterestError as e:
        raise HTTPException(status_code=502, detail=f"Pinterest: {e.detail}") from e

    items = data.get("items", [])
    created = 0
    skipped = 0
    for p in items:
        pin_id = p.get("id")
        media = p.get("media") or {}
        # Pull the best image URL Pinterest returns.
        image_url = None
        images = media.get("images") or {}
        if images:
            # pick the largest by key like "600x", "1200x", "originals"
            preferred = images.get("1200x") or images.get("600x") or next(iter(images.values()), None)
            if isinstance(preferred, dict):
                image_url = preferred.get("url")
        image_url = image_url or media.get("image_cover_url")
        if not pin_id or not image_url:
            skipped += 1
            continue

        tag = f"pin:{pin_id}"
        existing = await db.execute(select(Reference.id).where(Reference.tags.contains([tag])))
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        title = (p.get("title") or p.get("description") or "Pinterest pin").strip()[:200] or "Pinterest pin"
        ref = Reference(
            filename=f"pinterest_{pin_id}.jpg",
            original_filename=title,
            mime_type="image/jpeg",
            file_size=0,  # remote-hosted; size unknown
            file_url=image_url,
            uploaded_by=current_user.id,
            tags=["source:pinterest", tag],
        )
        db.add(ref)
        created += 1

    await db.commit()
    return {"board_id": board_id, "pins_seen": len(items), "created": created, "skipped": skipped}
