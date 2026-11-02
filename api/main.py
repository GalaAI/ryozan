"""
Ryozan API — entry point.

Start with:
    alembic upgrade head          # the schema is Alembic's alone; startup issues no DDL
    uvicorn main:app --reload --port 18788

A database built by the pre-2026-10-31 boot path (it has the tables but no
alembic_version) is adopted once with `python scripts/adopt_schema.py --apply`.
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text as sa_text

import app.models  # noqa: F401 — ensure all models are registered with Base.metadata
from app.api.v1.endpoints.bookings import process_booking_email_outbox_once, process_byof_deposit_reviews_once
from app.api.v1.endpoints.conversations import process_courier_notification_outbox_once
from app.api.v1.endpoints.flash import process_apex_training_manifest_once
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import async_session, engine
from app.core.deploy_state import get_deploy_state
from app.core.rate_limit import limiter
from app.services.email import process_booky_courier_outbox_once
from app.services.gallery_storage import process_gallery_deletion_outbox_once
from app.services.pack_bridge import process_pack_bridge_outbox_once, queue_recent_learning_captures_once
from app.services.scroll_archive import process_owner_email_outbox_once
from app.services.session_type_defaults import seed_default_session_types

settings = get_settings()


async def _run_owner_email_outbox() -> None:
    """Continuously drain the existing durable outbound-email authorities."""
    while True:
        workers = (
            ("owner email", process_owner_email_outbox_once),
            ("booking email", process_booking_email_outbox_once),
            ("BYOF deposit review", process_byof_deposit_reviews_once),
            ("Booky Courier", process_booky_courier_outbox_once),
            ("Courier notification", process_courier_notification_outbox_once),
            ("gallery deletion", process_gallery_deletion_outbox_once),
            ("learning capture", queue_recent_learning_captures_once),
            ("Pack bridge", process_pack_bridge_outbox_once),
            ("Apex reviewed dataset", process_apex_training_manifest_once),
        )
        for worker_name, worker in workers:
            try:
                await worker()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[Ryozan] {worker_name} outbox error: {exc}", flush=True)
        await asyncio.sleep(15)


async def _heal_portfolio_derivatives() -> None:
    """One pass, in the background, so a deployment fills its own thumb and focus gaps.

    Bounded: it runs once per process, commits per image, and any failure is logged and
    left for the next start (or scripts/backfill_portfolio_thumbs.py). It never deletes.
    """
    from app.services.portfolio_thumbs import backfill_missing_portfolio_derivatives

    await asyncio.sleep(5)  # let the app come up first; the derivatives are not on the request path
    try:
        async with async_session() as session:
            report = await backfill_missing_portfolio_derivatives(
                session, log=lambda line: print(f"[Ryozan] portfolio derivatives: {line}", flush=True)
            )
        print(
            f"[Ryozan] portfolio derivatives healed: {report.made_thumbs} thumbs, {report.made_focus} focus, "
            f"{report.skipped} complete, {report.failed} failed",
            flush=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[Ryozan] portfolio derivatives error: {exc}", flush=True)


_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        # Google Identity Services (GSI) needs accounts.google.com + apis.google.com for the One-Tap / Sign-in flow.
        # https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid
        "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com https://www.google-analytics.com https://accounts.google.com https://apis.google.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com",
        "img-src 'self' data: blob: https:",
        # Herald repost review previews artists' Reels straight from Instagram's CDN.
        "media-src 'self' blob: https:",
        "font-src 'self' data: https://fonts.gstatic.com",
        # connect-src already wide-open https; explicit for clarity that GSI uses it for token verification.
        "connect-src 'self' https: wss:",
        # GSI embeds its consent flow in an iframe at accounts.google.com.
        "frame-src 'self' https://venue.ink https://www.google.com https://accounts.google.com",
        "upgrade-insecure-requests",
    ]
)


def _apply_security_headers(response: Response) -> None:
    """Add scanner-visible browser hardening headers without overwriting explicit route headers."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
    # GSI uses a popup window that posts the credential back via postMessage.
    # `same-origin` blocks that handshake; `same-origin-allow-popups` keeps process isolation
    # for our own pages while permitting the GSI popup to complete its callback.
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    response.headers.setdefault(
        "Permissions-Policy",
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()",
    )
    if settings.SECURITY_HSTS_ENABLED:
        response.headers.setdefault(
            "Strict-Transport-Security",
            f"max-age={settings.SECURITY_HSTS_MAX_AGE}; includeSubDomains",
        )


_BLOCKED_SPA_PREFIXES = (
    ".git",
    ".svn",
    ".well-known",
    "admin",
    "cgi-bin",
    "debug",
    "phpmyadmin",
    "vendor",
    "wordpress",
    "wp-",
)
_BLOCKED_SPA_FILENAMES = {
    ".env",
    "composer.json",
    "composer.lock",
    "config.php",
    "phpinfo.php",
}
_BLOCKED_SPA_SUFFIXES = (
    ".asp",
    ".aspx",
    ".bak",
    ".cgi",
    ".conf",
    ".env",
    ".ini",
    ".jsp",
    ".log",
    ".old",
    ".php",
    ".sql",
    ".tar",
    ".tgz",
    ".war",
    ".zip",
)


def _should_serve_spa_path(full_path: str) -> bool:
    """Return false for obvious exploit/static-file probes that should be real 404s."""
    path = full_path.strip("/")
    if not path:
        return True

    normalized = path.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return True

    first = parts[0]
    leaf = parts[-1]

    if ".." in parts:
        return False
    if first == "api" or first.startswith(_BLOCKED_SPA_PREFIXES):
        return False
    if leaf in _BLOCKED_SPA_FILENAMES:
        return False
    return not leaf.endswith(_BLOCKED_SPA_SUFFIXES)


def _alembic_head() -> str | None:
    """The head revision of this checkout's migration chain."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


async def _check_schema_revision() -> None:
    """Say loudly when the database is not at the Alembic head. Read-only.

    The schema is owned by Alembic alone. In the container `alembic upgrade head`
    runs before uvicorn (Dockerfile CMD, railway.toml startCommand) and a failed
    migration stops the deploy, so this only fires for a process started some
    other way, typically a local `uvicorn main:app` against a database nobody
    migrated.
    """
    try:
        head = _alembic_head()
        async with engine.connect() as conn:
            has_table = await conn.scalar(sa_text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
            current = await conn.scalar(sa_text("SELECT version_num FROM alembic_version")) if has_table else None
    except Exception as exc:  # noqa: BLE001 - diagnostics must not stop the API
        print(f"[Ryozan] Schema revision check failed: {type(exc).__name__}: {exc}", flush=True)
        return
    if current == head:
        print(f"[Ryozan] Schema at Alembic head {head}", flush=True)
        return
    print(
        f"[Ryozan] SCHEMA NOT AT HEAD: database is at {current or 'no revision'}, code expects {head}. "
        "Run `alembic upgrade head` (a database built by the old boot path needs "
        "`python scripts/adopt_schema.py` first).",
        flush=True,
    )


async def _seed_step(name: str, step) -> None:
    """Run one seed step; a failure is logged and never skips the steps after it."""
    try:
        await step()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one broken seed must not take the others down
        print(f"[Ryozan] Startup seed FAILED ({name}): {type(exc).__name__}: {exc}", flush=True)


async def _run_startup_seeds() -> None:
    """Idempotent data seeds and repairs that run on every boot. No schema DDL.

    Schema changes are Alembic revisions and nothing else (see alembic/versions).
    What runs here is data: rows that are meant to be refreshed on every deploy
    (built-in scroll categories, starter-quest copy), per-account defaults, and
    cheap idempotent repairs of historical values. Each step is isolated by
    _seed_step, so one failure is reported and the rest still run.
    """

    async def default_session_types() -> None:
        async with engine.begin() as conn:
            installed = await conn.run_sync(seed_default_session_types)
        print(f"[Ryozan] Default session types installed: {installed}", flush=True)

    async def pos_commission_base() -> None:
        # Rows written before POS attempt v1 carried no commission base; it is the amount.
        async with engine.begin() as conn:
            await conn.execute(
                sa_text(
                    "UPDATE ryozan_pos_transactions "
                    "SET commission_base_cents = amount_cents "
                    "WHERE attempt_version IS NULL AND commission_base_cents = 0 AND price_adjustment_cents = 0"
                )
            )

    await _seed_step("default session types", default_session_types)
    await _seed_step("POS commission base", pos_commission_base)

    # Convert SQLAlchemy URL to asyncpg format
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    conn = await asyncpg.connect(db_url)
    try:

        async def scroll_categories() -> None:
            # Seed the built-in scroll categories (idempotent via ON CONFLICT).
            # The Scrolls feature is dead without these rows: list_category_docs
            # 404s on a missing slug. Its own _seed_step, so no other seed can
            # skip it.
            #
            # Three of these rows once lived only in Alembic revisions
            # (20260330_add_scroll_categories seeded artist/shop/misc,
            # 20260426_rename_misc_and_create_response_templates renamed misc ->
            # templates) that production never ran, so the table shipped empty.
            # The 20261031 baseline is schema only; the rows are seeded here.
            #
            # All SIX rows are seeded, not just the three the migrations know about.
            # ScrollsPanel.tsx replaces its BUILTIN_CATEGORIES fallback wholesale as
            # soon as the API returns anything non-empty:
            #     .then(cats => { if (cats.length > 0) setCategories(cats) })
            # so seeding only the migrations' three would delete the Waivers, Upkeep
            # and Quest Log tabs from the UI. Those three are special-cased render
            # branches (activeSlug === 'upkeep' etc.) that still need a category row
            # to appear as a tab. slug / label / icon / sort_order mirror
            # BUILTIN_CATEGORIES verbatim so nothing shifts or renames.
            #
            # is_builtin is TRUE on all six so DELETE /scrolls/categories/{slug}
            # refuses them. create_category always writes is_builtin=False, and
            # _slugify('Quest Log') is 'quest_log', so a deleted builtin could not be
            # recreated with the slug its hardcoded render branch matches on.
            #
            # Reconcile the historical built-in misc row before the canonical
            # upsert. If templates already exists, the stale built-in row is simply
            # removed. A custom misc row is left alone.
            await conn.execute("""
                DELETE FROM ryozan_scroll_categories
                WHERE slug = 'misc'
                  AND is_builtin = TRUE
                  AND EXISTS (
                      SELECT 1 FROM ryozan_scroll_categories WHERE slug = 'templates'
                  )
            """)
            await conn.execute("""
                UPDATE ryozan_scroll_categories
                SET slug = 'templates'
                WHERE slug = 'misc'
                  AND is_builtin = TRUE
                  AND NOT EXISTS (
                      SELECT 1 FROM ryozan_scroll_categories WHERE slug = 'templates'
                  )
            """)

            # Every NOT NULL column is supplied explicitly. create_all builds this
            # table from the ORM model, whose id / is_builtin / sort_order /
            # ai_digest_enabled / created_at defaults are Python-side only, so the
            # live columns carry NO server default -- the same omission that broke
            # the starter-quest seed.
            #
            # The seventh row, 'owner-archive' (label "Artist Scrolls"), is the only
            # backend-owned one: it is NOT in ScrollsPanel's BUILTIN_CATEGORIES,
            # because it is not a frontend fallback tab. It is the owner's copy of
            # every submitted Artist Scroll -- see app/services/scroll_archive.py --
            # and maps to document category 'scroll_owner-archive' under the
            # scrolls.py `scroll_{slug}` convention. It deliberately does NOT reuse
            # 'scroll': that category is the artist's own scroll, which
            # GET /documents/scroll/my returns for the caller and which submit_scroll
            # deletes on every upsert, so filing the owner's archive there would both
            # leak an artist's scroll to the founder as their own and delete the
            # archive on the founder's next submission.
            #
            # It is is_builtin=TRUE for a stronger reason than the other six. Their
            # failure mode on deletion is a missing tab. This one's slug is hardcoded
            # in backend service code (OWNER_ARCHIVE_CATEGORY), so deleting the row
            # makes list_category_docs 404 while submit_scroll keeps filing archive
            # rows into it -- the owner would lose access to compliance records that
            # are still accumulating. _slugify('Artist Scrolls') is 'artist_scrolls',
            # so it could not be recreated with the slug the backend matches on
            # either.
            await conn.execute("""
                INSERT INTO ryozan_scroll_categories (id, slug, label, icon, description, is_builtin, sort_order, ai_digest_enabled, created_at)
                VALUES
                    (gen_random_uuid(), 'artist',    'Artist',    '📜',             'Artist compliance scrolls: BBP cert, employment agreements, license records.', TRUE, 0, FALSE, now()),
                    (gen_random_uuid(), 'shop',      'Shop',      '🏪',             'Shop-level documents: vendor agreements, insurance, lease summaries.',         TRUE, 1, FALSE, now()),
                    (gen_random_uuid(), 'templates', 'Templates', 'message-square', 'Reusable auto-message response templates for staff quick-replies.',            TRUE, 2, FALSE, now()),
                    (gen_random_uuid(), 'waivers',   'Waivers',   '📋',             'Send consent forms and track waiver status.',                                  TRUE, 3, FALSE, now()),
                    (gen_random_uuid(), 'upkeep',    'Upkeep',    '🔧',             'Daily, weekly, and monthly station maintenance quests.',                       TRUE, 4, FALSE, now()),
                    (gen_random_uuid(), 'quests',    'Quest Log', 'quest',          'Active, completed, paused, and archived quests for everyone.',                 TRUE, 5, FALSE, now()),
                    (gen_random_uuid(), 'owner-archive', 'Artist Scrolls', 'archive', 'Durable copies of Artist Scrolls submitted by artists, filed to the owner.',  TRUE, 6, FALSE, now())
                ON CONFLICT (slug) DO UPDATE SET
                    label = EXCLUDED.label,
                    icon = EXCLUDED.icon,
                    description = EXCLUDED.description,
                    is_builtin = TRUE,
                    sort_order = EXCLUDED.sort_order,
                    ai_digest_enabled = FALSE
            """)
            builtin_scroll_categories = await conn.fetchval(
                "SELECT count(*) FROM ryozan_scroll_categories WHERE is_builtin = TRUE"
            )
            print(
                f"[Ryozan] built-in scroll categories reconciled: {builtin_scroll_categories}",
                flush=True,
            )

        async def booth_rent_values() -> None:
            await conn.execute("""
                UPDATE ryozan_users
                SET booth_rent_payment_method = 'autodraft'
                WHERE booth_rent_payment_method = 'stripe'
            """)
            await conn.execute("""
                UPDATE ryozan_users
                SET booth_rent_interval = 'biweekly'
                WHERE booth_rent_interval = 'week'
            """)

        async def password_hashes_and_quests() -> None:
            # ── Fix broken password hashes (permanent safety net) ──
            # Detects hashes that aren't valid bcrypt (60 chars, $2b$ or $2a$ prefix)
            # and resets them to a known default so users can log in and change password.
            import os as _os

            import bcrypt as _bcrypt

            _default_pw = _bcrypt.hashpw(b"tattoo", _bcrypt.gensalt()).decode()
            fixed = await conn.execute(
                """
                UPDATE ryozan_users
                SET password_hash = $1, must_reset_password = TRUE,
                    password_state = 'setup_required', password_changed_at = now(), password_change_method = 'setup'
                WHERE length(password_hash) != 60
                   OR (password_hash NOT LIKE '$2b$%' AND password_hash NOT LIKE '$2a$%')
            """,
                _default_pw,
            )
            if fixed and fixed != "UPDATE 0":
                print(f"[Ryozan] Fixed broken password hashes: {fixed}")

            # ── One-time staff password reset (triggered by RESET_ALL_PASSWORDS env var) ──
            if _os.environ.get("RESET_ALL_PASSWORDS") == "1":
                reset_result = await conn.execute(
                    """
                    UPDATE ryozan_users
                    SET password_hash = $1, must_reset_password = TRUE,
                        password_state = 'setup_required', password_changed_at = now(), password_change_method = 'setup'
                    WHERE role IN ('artist', 'admin', 'founder', 'assistant')
                """,
                    _default_pw,
                )
                print(f"[Ryozan] RESET STAFF PASSWORDS: {reset_result} (remove RESET_ALL_PASSWORDS env var now)")

            # ── Seed starter quests (idempotent; copy refreshes on every boot) ──
            await conn.execute("""
                INSERT INTO ryozan_quests (id, slug, title, description, category, xp_reward, coin_reward, requirement, target_count, sort_order, is_active, created_at)
                VALUES
                    (gen_random_uuid(), 'first-booking',   'First Session',     'Every master was once a disaster. Book your first session and let the ritual begin. The needle remembers what the mind forgets.',                'onboarding', 50,  10, 'Submit a booking request',    1, 1, TRUE, now()),
                    (gen_random_uuid(), 'profile-pic',     'Face Card',         'An artist without a face is just a rumor. Upload your portrait so the mortals know who wields the machine.',                      'onboarding', 25,   5, 'Set a profile avatar',        1, 2, TRUE, now()),
                    (gen_random_uuid(), 'waiver-signed',   'Consent King',      'The ancient pact between artist and canvas. Sign the waiver — it protects you both from the chaos of regret and litigation.',                     'onboarding', 30,   5, 'Sign a waiver',               1, 3, TRUE, now()),
                    (gen_random_uuid(), 'connect-email',   'Digital Address',   'Claim your studio email handle. One domain. One identity. The ravens will know where to find you.',                  'onboarding', 35,  10, 'Connect your studio email',    1, 4, TRUE, now()),
                    (gen_random_uuid(), 'add-to-homescreen','Go Mobile',        'Bind the portal to your device. Home screen access means you are one tap from the tower at all times. No excuses.', 'onboarding',100,  20, 'Add app to home screen',      1, 5, TRUE, now()),
                    (gen_random_uuid(), 'enable-notifications','Signal Flare',  'Light the beacon. Enable push notifications so the tower can reach you when bookings drop, messages land, or quests appear. Silence is not an option.', 'onboarding', 75, 15, 'Enable push notifications', 1, 6, TRUE, now()),
                    (gen_random_uuid(), 'three-sessions',  'Repeat Offender',   'Three sessions deep and you are no longer a tourist. You are a regular. The shop knows your coffee order. Probably.',                     'loyalty',   150,  50, 'Complete 3 sessions',          3, 7, TRUE, now()),
                    (gen_random_uuid(), 'leave-review',    'Voice of the People','The algorithm feeds on stars. Leave a Google review and help the uninitiated find the tower. Honesty appreciated. Five stars preferred.',                        'social',     40,  15, 'Submit a Google review',       1, 8, TRUE, now()),
                    (gen_random_uuid(), 'refer-friend',    'Pack Leader',       'Bring a friend into the fold. One referral who books a session and you earn the right to say you built this. Because you did.',                  'social',     75,  25, 'Refer 1 friend',              1, 9, TRUE, now()),
                    (gen_random_uuid(), 'email-alias',     'Shadow Name',       'Every legend needs an alias. Create an email forwarding alias so clients can reach you through the studio domain. One name. One mask. No spam.', 'side', 40, 15, 'Create an email alias', 1, 11, TRUE, now())
                ON CONFLICT (slug) DO UPDATE SET
                    title = EXCLUDED.title, description = EXCLUDED.description, requirement = EXCLUDED.requirement
            """)

        await _seed_step("built-in scroll categories", scroll_categories)
        await _seed_step("booth rent values", booth_rent_values)
        await _seed_step("password hashes and starter quests", password_hashes_and_quests)
        print("[Ryozan] Startup seeds: done", flush=True)
    finally:
        await conn.close()


async def _ensure_demo_account() -> None:
    """Create or repair the shared judge demo account when one is configured."""
    from app.services.demo_account import ensure_demo_account

    try:
        async with async_session() as session:
            outcome = await ensure_demo_account(session)
        print(f"[Ryozan] Demo account: {outcome}")
    except Exception as exc:  # a broken demo account must not stop the API
        print(f"[Ryozan] Demo account failed: {type(exc).__name__}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    # ── Startup ───────────────────────────────
    print(f"[Ryozan] Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    print(f"[Ryozan] Database: {settings.DATABASE_URL.split('@')[1]}")  # hide creds
    await _check_schema_revision()
    await _run_startup_seeds()
    await _ensure_demo_account()
    outbox_task = asyncio.create_task(_run_owner_email_outbox())
    derivatives_task = (
        asyncio.create_task(_heal_portfolio_derivatives()) if settings.PORTFOLIO_DERIVATIVES_ON_STARTUP else None
    )
    try:
        yield
    finally:
        outbox_task.cancel()
        with suppress(asyncio.CancelledError):
            await outbox_task
        if derivatives_task:
            derivatives_task.cancel()
            with suppress(asyncio.CancelledError):
                await derivatives_task
        # ── Shutdown ──────────────────────────────
        print("[Ryozan] Shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_API_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_API_DOCS else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RequestValidationError)
async def _validation_error_without_input(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return FastAPI's 422 shape without echoing the rejected values.

    The default handler copies each value into detail[].input, so a form-encoded
    login returned the submitted password. Keep loc, msg and type; drop input.
    """
    errors = [{key: value for key, value in error.items() if key != "input"} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    site_host = (urlsplit(settings.public_site_url).hostname or "").lower()
    if site_host and host == f"www.{site_host}":
        redirect_url = request.url.replace(scheme="https", netloc=site_host)
        response = RedirectResponse(str(redirect_url), status_code=308)
        if settings.SECURITY_HEADERS_ENABLED:
            _apply_security_headers(response)
        return response

    response = await call_next(request)
    if settings.SECURITY_HEADERS_ENABLED:
        _apply_security_headers(response)
    return response


# CORS — allow frontends to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# MCP surface (Streamable HTTP at /mcp). Registered here, before the SPA
# catch-all below, so GET /mcp returns a real 405 instead of index.html.
# Resolve the router from its module at include time: the top-level name bound
# at import can miss the decorator-registered routes under some import orders,
# so read the fully-built router object here.
from app.mcp.oauth import oauth_router as _mcp_oauth_router  # noqa: E402
from app.mcp.router import mcp_router as _mcp_router  # noqa: E402

app.include_router(_mcp_router)
app.include_router(_mcp_oauth_router)

# Serve uploaded files (gallery images, etc.)
# NOTE: Local /uploads/ static mount removed (2026-03-24).
# All file storage now uses Cloudflare R2.
# Legacy /uploads/ avatar URLs in the DB need re-upload to work.


@app.get("/health")
async def health_check():
    """
    Deep health check — verifies API + database connectivity.

    Railway pings this every 60s. If the DB is unreachable, this returns 503
    so Railway knows to restart the service instead of serving dead requests.
    """
    try:
        async with async_session() as session:
            result = await session.execute(sa_text("SELECT 1"))
            result.scalar()
        return {"status": "ok", "version": settings.APP_VERSION, "db": "connected", "deploy": get_deploy_state()}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "version": settings.APP_VERSION,
                "db": "unreachable",
                "deploy": get_deploy_state(),
            },
        )


@app.get("/health/deploy")
async def deploy_state_check():
    """Return the branch and commit currently serving this process."""
    return get_deploy_state()


@app.get("/health/ready")
async def readiness_check():
    """
    Strict deploy readiness check for Railway and Axiom smoke tests.

    Unlike /health, this fails when critical tables are missing or production
    is running with development defaults.
    """
    # Response key -> table checked. The keys are part of this endpoint's JSON
    # contract (scripts/verify-huddl-scheduler.ps1 reads checks.bttco_*), so they
    # keep the pre-20261031_ryozan_prefix table names until the API-naming PR
    # renames them; only the database object names changed.
    critical_tables = {
        "bttco_users": "ryozan_users",
        "bttco_bookings": "ryozan_bookings",
        "bttco_booking_slot_holds": "ryozan_booking_slot_holds",
        "bttco_booking_lifecycle_events": "ryozan_booking_lifecycle_events",
        "bttco_conversations": "ryozan_conversations",
        "bttco_messages": "ryozan_messages",
        "bttco_crm_pipeline_tasks": "ryozan_crm_pipeline_tasks",
    }
    checks: dict[str, str] = {}
    failures: list[str] = []

    try:
        async with async_session() as session:
            await session.execute(sa_text("SELECT 1"))
            checks["db"] = "connected"
            for key, table in critical_tables.items():
                result = await session.execute(sa_text("SELECT to_regclass(:table_name)"), {"table_name": table})
                if result.scalar():
                    checks[key] = "ok"
                else:
                    checks[key] = "missing_or_unreadable"
                    failures.append(key)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "version": settings.APP_VERSION,
                "checks": {"db": "unreachable"},
                "failures": ["db"],
                "deploy": get_deploy_state(),
            },
        )

    status_code = 503 if failures else 200
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if not failures else "not_ready",
            "version": settings.APP_VERSION,
            "checks": checks,
            "failures": failures,
            "deploy": get_deploy_state(),
        },
    )


@app.get("/health/config")
async def health_config():
    """Config diagnostic — shows which services are configured (no secrets)."""
    if not settings.ENABLE_CONFIG_HEALTH:
        raise HTTPException(status_code=404, detail="Not found")

    resend_on = bool(settings.RESEND_API_KEY and settings.RESEND_API_KEY.strip())
    smtp_on = bool(settings.SMTP_USER and settings.SMTP_PASSWORD)
    return {
        "resend_configured": resend_on,
        "smtp_configured": smtp_on,
        "email_ready": resend_on or smtp_on,
        "email_primary": "resend" if resend_on else ("smtp" if smtp_on else "none"),
        "smtp_host": settings.SMTP_HOST,
        "smtp_from": settings.SMTP_FROM_EMAIL or settings.SMTP_USER or "(not set)",
        "database_configured": bool(settings.DATABASE_URL),
        "cors_origins": settings.CORS_ORIGINS[:3]
        if isinstance(settings.CORS_ORIGINS, list)
        else str(settings.CORS_ORIGINS)[:100],
        "app_env": getattr(settings, "APP_ENV", "not set"),
        "frontend_url": settings.FRONTEND_URL,
    }


# ── SPA static file serving (production only) ────────────────────────────────
# The Docker build copies the React build output to api/static/.
# In local dev this directory won't exist, so this block is a no-op.
_static_dir = Path(__file__).parent / "static"

if _static_dir.is_dir():
    # Serve hashed assets (JS, CSS, images, fonts) under /assets directly.
    # These are cache-busted by Vite so they can be served with long TTLs.
    _assets_dir = _static_dir / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    # Mount the rest of the static directory (favicon, manifest, etc.)
    # html=False so unknown paths fall through to the catch-all below.
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static-root")

    # SPA catch-all: check for an exact HTML file or direct asset first (e.g. ask.html,
    # book.html, book/sesh.html, book/confirm.html) so multi-page entries load directly,
    # then fall back to index.html so client-side routing (React Router) works.
    # The catch-all also matches /assets/* for the rate limiter, so exempting it keeps
    # static files and SPA routes out of the per-IP API budget.
    # Public pages that name the studio: their {{studio_name}}, {{contact_link}} and
    # {{site_url}} placeholders are filled from Settings, so each deployment shows its
    # own name, contact address and canonical URLs.
    _STUDIO_PAGE_TYPES = {
        "privacy.html": "text/html; charset=utf-8",
        "sms-terms.html": "text/html; charset=utf-8",
        "sitemap.xml": "application/xml",
        "robots.txt": "text/plain; charset=utf-8",
    }

    def _studio_page(path: Path) -> Response | FileResponse:
        media_type = _STUDIO_PAGE_TYPES.get(path.name)
        if media_type is None or path.parent != _static_dir:
            return FileResponse(str(path))
        contact = (settings.SHOP_EMAIL or "").strip()
        contact_link = f'<a href="mailto:{escape(contact)}">{escape(contact)}</a>' if contact else "the studio directly"
        body = (
            path.read_text(encoding="utf-8")
            .replace("{{studio_name}}", escape(settings.STUDIO_NAME))
            .replace("{{contact_link}}", contact_link)
            .replace("{{site_url}}", escape(settings.public_site_url))
        )
        return Response(content=body, media_type=media_type)

    @app.get("/{full_path:path}", include_in_schema=False)
    @limiter.exempt
    async def spa_fallback(full_path: str):
        if not _should_serve_spa_path(full_path):
            raise HTTPException(status_code=404, detail="Not found")
        clean_path = full_path.strip("/")
        if clean_path:
            html_candidate = _static_dir / f"{clean_path}.html"
            if html_candidate.is_file():
                return _studio_page(html_candidate)
            direct_candidate = _static_dir / clean_path
            if direct_candidate.is_file():
                return _studio_page(direct_candidate)
        index = _static_dir / "index.html"
        return FileResponse(str(index))
