"""
Ryozan API — entry point.

Start with:
    uvicorn main:app --reload --port 8801
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

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
from app.core.database import Base, async_session, engine
from app.core.deploy_state import get_deploy_state
from app.core.rate_limit import limiter
from app.core.schema_sync import install_pos_attempt_v1_constraints, reconcile_additive_columns
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


_PERFORMANCE_INDEX_STATEMENTS = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_bookings_artist_id ON bttco_bookings (artist_id)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_bookings_client_id ON bttco_bookings (client_id)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_bookings_status ON bttco_bookings (status)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_bookings_created_at ON bttco_bookings (created_at)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_bookings_scheduled_date ON bttco_bookings (scheduled_date) WHERE scheduled_date IS NOT NULL",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_bookings_artist_status ON bttco_bookings (artist_id, status)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_users_role ON bttco_users (role)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_bookings_conversation_id ON bttco_bookings (conversation_id) WHERE conversation_id IS NOT NULL",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_notifications_user_id ON bttco_notifications (user_id)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_notifications_read ON bttco_notifications (user_id, read) WHERE read = FALSE",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_bttco_sms_messages_courier_message_id ON bttco_sms_messages (courier_message_id) WHERE courier_message_id IS NOT NULL",
)

# The database-level guarantee that one email is one account. Nothing enforced
# this before, which is how two rows differing only by capitalization reached
# production and turned login into an HTTP 500 for the people who owned them.
#
# Applied on boot rather than only via alembic because production has no
# alembic_version table and has never run a migration. Mirrored in
# alembic/versions/20260808_unique_lower_email.py for environments that do.
#
# NOT `CONCURRENTLY`: this must be able to fail loudly and immediately while
# duplicates exist, rather than leaving an INVALID index behind. Its caller
# catches that failure so the rest of the boot block still runs.
_EMAIL_UNIQUE_INDEX_SQL = "CREATE UNIQUE INDEX IF NOT EXISTS ux_bttco_users_email_lower ON bttco_users (lower(email))"

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


async def _run_startup_migrations():
    """
    Run idempotent schema migrations on boot.

    This handles column additions that can't wait for a manual migration
    (e.g. when Railway CLI auth is unavailable). Every statement uses
    IF NOT EXISTS / safe checks so it's harmless to run on every deploy.
    """
    # ── Ensure all tables exist (idempotent — skips existing) ──
    # create_all only creates tables that are missing. It never alters one that
    # already exists, so a new column on an existing table is silently skipped
    # and the first request that selects it raises UndefinedColumn.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[Ryozan] Table creation check: OK")

    # ── Add model columns missing from tables that already exist ──
    # This is what closes the gap create_all leaves. Derived from the ORM
    # metadata, so adding a column to a model is enough -- no hand-written
    # ALTER needed below. Additive only; refuses anything that would fail on a
    # populated table. The hand-written block that follows is kept because it
    # carries historical backfills and index creation this cannot infer.
    async with engine.begin() as conn:
        plan = await reconcile_additive_columns(conn, Base.metadata)
        await conn.run_sync(install_pos_attempt_v1_constraints)
        await conn.execute(sa_text("ALTER TABLE bttco_gift_cards ALTER COLUMN recipient_email DROP NOT NULL"))
        # Lane configuration codes (SENDER_NOT_CONFIGURED) exceed the original varchar(20).
        await conn.execute(sa_text("ALTER TABLE bttco_sms_messages ALTER COLUMN error_code TYPE VARCHAR(40)"))
        default_session_types_seeded = await conn.run_sync(seed_default_session_types)
        await conn.execute(
            sa_text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_bttco_users_booth_rent_subscription_id "
                "ON bttco_users (booth_rent_subscription_id) "
                "WHERE booth_rent_subscription_id IS NOT NULL"
            )
        )
        await conn.execute(
            sa_text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_bttco_scroll_categories_slug ON bttco_scroll_categories (slug)"
            )
        )
        await conn.execute(
            sa_text(
                "UPDATE bttco_pos_transactions "
                "SET commission_base_cents = amount_cents "
                "WHERE attempt_version IS NULL AND commission_base_cents = 0 AND price_adjustment_cents = 0"
            )
        )
    print(f"[Ryozan] Default session types installed: {default_session_types_seeded}")
    # flush=True because container stdout is block-buffered: without it these
    # lines sit in the buffer and never reach the Railway log, which would make
    # the refusal warnings below invisible exactly when they matter.
    if plan.statements:
        print(f"[Ryozan] Additive columns applied: {len(plan.statements)}", flush=True)
        for statement in plan.statements:
            print(f"[Ryozan]   {statement}", flush=True)
    else:
        print("[Ryozan] Additive column check: no drift", flush=True)
    for name, reason in plan.refused:
        # Loud on purpose: a refusal means the model declares a column the
        # database will not get, which is the UndefinedColumn failure waiting
        # to happen. Fix the model, do not ignore this.
        print(f"[Ryozan] REFUSED column {name}: {reason}", flush=True)

    # Convert SQLAlchemy URL to asyncpg format
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    conn = await asyncpg.connect(db_url)
    try:
        # Seed the built-in scroll categories (idempotent via ON CONFLICT).
        # First statement in this block on purpose. Everything here shares one
        # try/except that prints "(non-fatal)" and returns, so any statement
        # that raises silently skips every statement after it. The Scrolls
        # feature is dead without these rows: list_category_docs 404s on a
        # missing slug, so it must not sit behind ~40 statements that could
        # skip it.
        #
        # Three of these rows only ever existed in alembic
        # (20260330_add_scroll_categories seeds artist/shop/misc,
        # 20260426_rename_misc_and_create_response_templates renames misc ->
        # templates). Production has no alembic_version table, so neither
        # migration has ever run there and the table shipped empty.
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
            DELETE FROM bttco_scroll_categories
            WHERE slug = 'misc'
              AND is_builtin = TRUE
              AND EXISTS (
                  SELECT 1 FROM bttco_scroll_categories WHERE slug = 'templates'
              )
        """)
        await conn.execute("""
            UPDATE bttco_scroll_categories
            SET slug = 'templates'
            WHERE slug = 'misc'
              AND is_builtin = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM bttco_scroll_categories WHERE slug = 'templates'
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
            INSERT INTO bttco_scroll_categories (id, slug, label, icon, description, is_builtin, sort_order, ai_digest_enabled, created_at)
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
            "SELECT count(*) FROM bttco_scroll_categories WHERE is_builtin = TRUE"
        )
        print(
            f"[Ryozan] built-in scroll categories reconciled: {builtin_scroll_categories}",
            flush=True,
        )

        # ── must_reset_password column (added 2026-03-09) ──
        await conn.execute("""
            ALTER TABLE bttco_users
            ADD COLUMN IF NOT EXISTS must_reset_password BOOLEAN NOT NULL DEFAULT FALSE
        """)

        # NOTE: One-time forced reset was applied on initial deploy (2026-03-09).
        # Removed the recurring UPDATE that was re-setting must_reset_password = TRUE
        # on every deploy, even after users had already changed their password.

        # ── Artist core columns (may be missing if DB was created without Tauri migrations) ──
        for col_sql in [
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS slug VARCHAR(100) UNIQUE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS specialty TEXT[]",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS bio TEXT",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booking_mode VARCHAR(50)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS instagram_url VARCHAR(500)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS portfolio_images TEXT[]",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS portfolio_thumbs JSONB",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS portfolio_focus JSONB",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS reasons TEXT[]",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS password_state VARCHAR(24)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS password_change_method VARCHAR(16)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS last_login_method VARCHAR(16)",
        ]:
            await conn.execute(col_sql)

        # Stripe event IDs extend the existing Huddl lifecycle ledger. The
        # unique nullable index makes booking and deposit-task transitions
        # atomic with the event that triggered them.
        await conn.execute("""
            ALTER TABLE bttco_booking_lifecycle_events
            ADD COLUMN IF NOT EXISTS provider_event_id VARCHAR(255)
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_bttco_booking_lifecycle_events_provider_event_id
            ON bttco_booking_lifecycle_events (provider_event_id)
        """)

        # ── Stripe + profile columns (added 2026-03-19) ──
        for col_sql in [
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS stripe_account_id VARCHAR(255)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS stripe_onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS commission_rate INTEGER",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_amount_cents INTEGER",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_interval VARCHAR(16)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_payment_method VARCHAR(16) NOT NULL DEFAULT 'cash'",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_status VARCHAR(24) NOT NULL DEFAULT 'not_configured'",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_subscription_id VARCHAR(255)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_checkout_session_id VARCHAR(255)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_current_period_end TIMESTAMPTZ",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_next_due_date DATE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booth_rent_cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS is_guest_artist BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS plaid_access_token VARCHAR(512)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS availability_source VARCHAR(50) NOT NULL DEFAULT 'none'",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booking_slot_interval_minutes INTEGER NOT NULL DEFAULT 30",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booking_buffer_minutes INTEGER NOT NULL DEFAULT 30",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booking_min_notice_hours INTEGER NOT NULL DEFAULT 24",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booking_max_advance_days INTEGER NOT NULL DEFAULT 180",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booking_allow_same_day BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS phone VARCHAR(50)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS facebook_url VARCHAR(500)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS tiktok_url VARCHAR(500)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS website_url VARCHAR(500)",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS title VARCHAR(255)",
        ]:
            await conn.execute(col_sql)
        await conn.execute("""
            UPDATE bttco_users
            SET booth_rent_payment_method = 'autodraft'
            WHERE booth_rent_payment_method = 'stripe'
        """)
        await conn.execute("""
            UPDATE bttco_users
            SET booth_rent_interval = 'biweekly'
            WHERE booth_rent_interval = 'week'
        """)
        await conn.execute("""
            ALTER TABLE bttco_users
            ALTER COLUMN booth_rent_payment_method SET DEFAULT 'cash'
        """)
        # ── Booking URL column (added 2026-03-27) ──
        await conn.execute("ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS booking_url VARCHAR(500)")

        # ── Gala AI personality + Nexus profile columns (added 2026-03-20) ──
        await conn.execute(
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS gala_personality INTEGER NOT NULL DEFAULT 75"
        )
        await conn.execute("ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS nexus_profile JSONB DEFAULT NULL")
        for col_sql in [
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS membership_tier VARCHAR(50) NOT NULL DEFAULT 'freemium'",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS membership_status VARCHAR(50) NOT NULL DEFAULT 'active'",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS ai_monthly_message_cap INTEGER NOT NULL DEFAULT 40",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS early_access_galax3 BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS connected_apps_unlocked BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS product_entitlements JSONB NOT NULL DEFAULT '{}'::jsonb",
        ]:
            await conn.execute(col_sql)
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_bttco_users_membership_tier ON bttco_users(membership_tier)")

        # ── Gala auto-reply mode column (added 2026-04-01) ──
        await conn.execute("ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS gala_auto_reply VARCHAR(50)")

        # ── Saved artist signature URL (added 2026-04-15) ──
        await conn.execute("ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS saved_signature_url VARCHAR(500)")

        # ── Flash artist settings (added 2026-04-18) ──
        await conn.execute("ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS flash_deposit_amount NUMERIC(8,2)")
        await conn.execute(
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS allow_prepay_full BOOLEAN NOT NULL DEFAULT FALSE"
        )

        # ── Hustle Stash / flash canvas metadata (added 2026-05-19) ──
        for col_sql in [
            "ALTER TABLE bttco_flash_designs ADD COLUMN IF NOT EXISTS size_label VARCHAR(100)",
            "ALTER TABLE bttco_flash_designs ADD COLUMN IF NOT EXISTS placement VARCHAR(120)",
            "ALTER TABLE bttco_flash_designs ADD COLUMN IF NOT EXISTS mask_json JSONB",
            "ALTER TABLE bttco_flash_designs ADD COLUMN IF NOT EXISTS canvas_state JSONB",
            "ALTER TABLE bttco_flash_designs ADD COLUMN IF NOT EXISTS ai_metadata JSONB",
            "ALTER TABLE bttco_flash_designs ADD COLUMN IF NOT EXISTS gala_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        ]:
            await conn.execute(col_sql)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_bttco_stash_items_artist_lane ON bttco_stash_items(artist_id, lane)"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_bttco_stash_items_status ON bttco_stash_items(status)")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_bttco_stash_canvas_artist_lane ON bttco_stash_canvas_sessions(artist_id, lane)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_bttco_stash_canvas_sheet_id ON bttco_stash_canvas_sessions(sheet_id)"
        )

        # ── Courier proxy_for (inbox delegation, added 2026-04-30) ──
        await conn.execute(
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS proxy_for UUID REFERENCES bttco_users(id) ON DELETE SET NULL"
        )

        # ── Google OAuth stable account id (added 2026-05-12) ──
        await conn.execute("ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS google_refresh_token VARCHAR(512)")
        await conn.execute("ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS google_calendar_id VARCHAR(255)")
        await conn.execute("ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS google_id VARCHAR(255)")
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_bttco_users_google_id ON bttco_users(google_id) WHERE google_id IS NOT NULL"
        )

        # ── One POS row per Stripe Checkout Session (added 2026-09-27) ──
        # The POS card record is written by the Stripe webhook, and Stripe
        # retries webhooks. The handler checks for an existing row first, but
        # two concurrent retries can both pass that check, so the guarantee is
        # enforced here rather than left to application timing. Partial, because
        # cash rows carry no session id.
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_bttco_pos_transactions_stripe_session_id "
            "ON bttco_pos_transactions(stripe_session_id) WHERE stripe_session_id IS NOT NULL"
        )

        # ── SMS / A2P 10DLC opt-in tracking (added 2026-05-22, PR #31) ──
        # Critical: the SmsMessage/SmsTemplate ORM models + the BTTCOUser model
        # reference these columns at SELECT time. Without them, every
        # `SELECT * FROM bttco_users` (login, register, /me, etc.) raises
        # UndefinedColumnError → 500.
        for col_sql in [
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS sms_opt_in BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS sms_opt_in_at TIMESTAMPTZ",
            "ALTER TABLE bttco_users ADD COLUMN IF NOT EXISTS sms_opt_out_at TIMESTAMPTZ",
        ]:
            await conn.execute(col_sql)

        # SMS message log + templates (mirrors migration 20260522_sms_messaging)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bttco_sms_messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                direction VARCHAR(10) NOT NULL,
                to_phone VARCHAR(20) NOT NULL,
                from_phone VARCHAR(20) NOT NULL,
                body TEXT NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'queued',
                error_code VARCHAR(20),
                error_message TEXT,
                twilio_sid VARCHAR(64) UNIQUE,
                template_slug VARCHAR(100),
                conversation_id UUID,
                user_id UUID,
                sender_id UUID,
                price_cents INTEGER,
                num_segments INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                delivered_at TIMESTAMPTZ
            )
        """)
        for ix_sql in [
            "CREATE INDEX IF NOT EXISTS ix_sms_messages_to_phone ON bttco_sms_messages(to_phone)",
            "CREATE INDEX IF NOT EXISTS ix_sms_messages_created_at ON bttco_sms_messages(created_at)",
            "CREATE INDEX IF NOT EXISTS ix_sms_messages_status ON bttco_sms_messages(status)",
            "CREATE INDEX IF NOT EXISTS ix_sms_messages_direction ON bttco_sms_messages(direction)",
        ]:
            await conn.execute(ix_sql)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bttco_sms_templates (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                slug VARCHAR(100) NOT NULL UNIQUE,
                name VARCHAR(255) NOT NULL,
                category VARCHAR(50) NOT NULL DEFAULT 'transactional',
                body TEXT NOT NULL,
                variables JSONB NOT NULL DEFAULT '[]'::jsonb,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # owner_id from PR #32 (staff scope)
        await conn.execute("ALTER TABLE bttco_sms_templates ADD COLUMN IF NOT EXISTS owner_id UUID")
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_sms_templates_owner_id ON bttco_sms_templates(owner_id)")

        # ── Artist counter-signature columns (added 2026-03-19) ──
        for col_sql in [
            "ALTER TABLE bttco_document_instances ADD COLUMN IF NOT EXISTS artist_signature TEXT",
            "ALTER TABLE bttco_document_instances ADD COLUMN IF NOT EXISTS artist_signed_at TIMESTAMPTZ",
            "ALTER TABLE bttco_document_instances ADD COLUMN IF NOT EXISTS artist_id_reviewed UUID REFERENCES bttco_users(id)",
        ]:
            await conn.execute(col_sql)

        # ── Quest tables (added 2026-03-15) ──
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bttco_quests (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                slug VARCHAR(80) NOT NULL UNIQUE,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                category VARCHAR(50) NOT NULL DEFAULT 'onboarding',
                xp_reward INTEGER NOT NULL DEFAULT 0,
                coin_reward INTEGER NOT NULL DEFAULT 0,
                requirement VARCHAR(255) NOT NULL,
                target_count INTEGER NOT NULL DEFAULT 1,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bttco_user_quests (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES bttco_users(id) ON DELETE CASCADE,
                quest_id UUID NOT NULL REFERENCES bttco_quests(id) ON DELETE CASCADE,
                progress INTEGER NOT NULL DEFAULT 0,
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                claimed BOOLEAN NOT NULL DEFAULT FALSE,
                completed_at TIMESTAMPTZ,
                claimed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(user_id, quest_id)
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bttco_quests_slug_unique
            ON bttco_quests (slug)
        """)

        # ── Email routes table (added 2026-03-19) ──
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bttco_email_routes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                handle VARCHAR(100) NOT NULL UNIQUE,
                forward_to VARCHAR(255) NOT NULL,
                user_id UUID NOT NULL REFERENCES bttco_users(id) ON DELETE CASCADE,
                verified BOOLEAN NOT NULL DEFAULT FALSE,
                verify_token VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                verified_at TIMESTAMPTZ
            )
        """)

        # ── Fix broken password hashes (permanent safety net) ──
        # Detects hashes that aren't valid bcrypt (60 chars, $2b$ or $2a$ prefix)
        # and resets them to a known default so users can log in and change password.
        import os as _os

        import bcrypt as _bcrypt

        _default_pw = _bcrypt.hashpw(b"tattoo", _bcrypt.gensalt()).decode()
        fixed = await conn.execute(
            """
            UPDATE bttco_users
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
                UPDATE bttco_users
                SET password_hash = $1, must_reset_password = TRUE,
                    password_state = 'setup_required', password_changed_at = now(), password_change_method = 'setup'
                WHERE role IN ('artist', 'admin', 'founder', 'assistant')
            """,
                _default_pw,
            )
            print(f"[Ryozan] RESET STAFF PASSWORDS: {reset_result} (remove RESET_ALL_PASSWORDS env var now)")

        # ── Seed starter quests (idempotent; copy refreshes on every boot) ──
        await conn.execute("""
            INSERT INTO bttco_quests (id, slug, title, description, category, xp_reward, coin_reward, requirement, target_count, sort_order, is_active, created_at)
            VALUES
                (gen_random_uuid(), 'first-booking',   'First Session',     'Every master was once a disaster. Book your first session and let the ritual begin. The needle remembers what the mind forgets.',                'onboarding', 50,  10, 'Submit a booking request',    1, 1, TRUE, now()),
                (gen_random_uuid(), 'profile-pic',     'Face Card',         'An artist without a face is just a rumor. Upload your portrait so the mortals know who wields the machine.',                      'onboarding', 25,   5, 'Set a profile avatar',        1, 2, TRUE, now()),
                (gen_random_uuid(), 'waiver-signed',   'Consent King',      'The ancient pact between artist and canvas. Sign the waiver — it protects you both from the chaos of regret and litigation.',                     'onboarding', 30,   5, 'Sign a waiver',               1, 3, TRUE, now()),
                (gen_random_uuid(), 'connect-email',   'Digital Address',   'Claim your @ryozan.app handle. One domain. One identity. The ravens will know where to find you.',                  'onboarding', 35,  10, 'Connect your studio email',    1, 4, TRUE, now()),
                (gen_random_uuid(), 'add-to-homescreen','Go Mobile',        'Bind the portal to your device. Home screen access means you are one tap from the tower at all times. No excuses.', 'onboarding',100,  20, 'Add app to home screen',      1, 5, TRUE, now()),
                (gen_random_uuid(), 'enable-notifications','Signal Flare',  'Light the beacon. Enable push notifications so the tower can reach you when bookings drop, messages land, or quests appear. Silence is not an option.', 'onboarding', 75, 15, 'Enable push notifications', 1, 6, TRUE, now()),
                (gen_random_uuid(), 'three-sessions',  'Repeat Offender',   'Three sessions deep and you are no longer a tourist. You are a regular. The shop knows your coffee order. Probably.',                     'loyalty',   150,  50, 'Complete 3 sessions',          3, 7, TRUE, now()),
                (gen_random_uuid(), 'leave-review',    'Voice of the People','The algorithm feeds on stars. Leave a Google review and help the uninitiated find the tower. Honesty appreciated. Five stars preferred.',                        'social',     40,  15, 'Submit a Google review',       1, 8, TRUE, now()),
                (gen_random_uuid(), 'refer-friend',    'Pack Leader',       'Bring a friend into the fold. One referral who books a session and you earn the right to say you built this. Because you did.',                  'social',     75,  25, 'Refer 1 friend',              1, 9, TRUE, now()),
                (gen_random_uuid(), 'email-alias',     'Shadow Name',       'Every legend needs an alias. Create an email forwarding alias so clients can reach you through the studio domain. One name. One mask. No spam.', 'side', 40, 15, 'Create an email alias', 1, 11, TRUE, now())
            ON CONFLICT (slug) DO UPDATE SET
                title = EXCLUDED.title, description = EXCLUDED.description, requirement = EXCLUDED.requirement
        """)

        # ── Push subscriptions table (added 2026-03-20) ──
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bttco_push_subscriptions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES bttco_users(id) ON DELETE CASCADE,
                endpoint TEXT NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(user_id, endpoint)
            )
        """)

        # ── Notification preferences table (added 2026-03-20) ──
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bttco_notification_preferences (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL UNIQUE REFERENCES bttco_users(id) ON DELETE CASCADE,
                email_bookings BOOLEAN NOT NULL DEFAULT TRUE,
                email_messages BOOLEAN NOT NULL DEFAULT TRUE,
                email_quests BOOLEAN NOT NULL DEFAULT TRUE,
                email_marketing BOOLEAN NOT NULL DEFAULT FALSE,
                push_bookings BOOLEAN NOT NULL DEFAULT TRUE,
                push_messages BOOLEAN NOT NULL DEFAULT TRUE,
                push_quests BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        # ── Booking columns that may be missing on older prod DB (added 2026-03-20) ──
        for col_sql in [
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS guest_name VARCHAR(255)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS guest_email VARCHAR(255)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS guest_phone VARCHAR(50)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS sms_opt_in BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS sms_opt_in_at TIMESTAMPTZ",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS sms_consent_text TEXT",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS reference_code VARCHAR(20) UNIQUE",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS budget VARCHAR(50)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS timeline VARCHAR(50)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS stripe_checkout_session_id VARCHAR(255)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS stripe_payment_intent_id VARCHAR(255)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS google_calendar_event_id VARCHAR(255)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS source VARCHAR(50) NOT NULL DEFAULT 'bttco'",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS booking_kind VARCHAR(50) NOT NULL DEFAULT 'tattoo_session'",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS consultation_method VARCHAR(50)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS conversation_id UUID REFERENCES bttco_conversations(id)",
            "ALTER TABLE bttco_bookings ADD COLUMN IF NOT EXISTS deposit_paid BOOLEAN DEFAULT FALSE",
        ]:
            await conn.execute(col_sql)

        # ── Create index on reference_code if missing ──
        await conn.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_bookings_reference_code
            ON bttco_bookings (reference_code)
            WHERE reference_code IS NOT NULL
        """)

        # ── Booking query performance indexes (added 2026-05-26) ──
        for idx_sql in _PERFORMANCE_INDEX_STATEMENTS:
            await conn.execute(idx_sql)

        # ── Courier artifact message columns (quote/slot proposals, added 2026-05-14) ──
        for col_sql in [
            "ALTER TABLE bttco_messages ADD COLUMN IF NOT EXISTS artifact_payload JSONB",
            "ALTER TABLE bttco_messages ADD COLUMN IF NOT EXISTS artifact_state JSONB",
            "ALTER TABLE bttco_messages ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
        ]:
            await conn.execute(col_sql)

        # ── Artist session type rates + buffer (added 2026-06-22, PR #88) ──
        for col_sql in [
            "ALTER TABLE bttco_artist_session_types ADD COLUMN IF NOT EXISTS buffer_minutes INTEGER",
            "ALTER TABLE bttco_artist_session_types ADD COLUMN IF NOT EXISTS hourly_rate NUMERIC(10, 2)",
        ]:
            await conn.execute(col_sql)

        # ── API usage budget tracker table (added 2026-04-05) ──
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bttco_api_usage (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES bttco_users(id) ON DELETE SET NULL,
                endpoint VARCHAR(100) NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bttco_api_usage_created_at
            ON bttco_api_usage (created_at)
        """)

        # ── Case-insensitive unique email (added 2026-08-08) ──
        # Production has never run alembic and has no alembic_version table, so
        # the matching migration would never reach it; this is the path that
        # actually creates the index in production.
        #
        # Deliberately in its own try/except: the outer handler prints and
        # RETURNS, so an exception here would silently skip every statement
        # after it. This one is EXPECTED to fail until duplicates are merged
        # (run scripts/dedupe_user_emails.py), and a boot must never be held
        # hostage to that.
        try:
            await conn.execute(_EMAIL_UNIQUE_INDEX_SQL)
            print("[Ryozan] Unique index on lower(email): OK", flush=True)
        except Exception as index_error:  # noqa: BLE001 - advisory only, boot must continue
            print(
                "[Ryozan] Unique index on lower(email) NOT created: "
                f"{index_error}. Duplicate emails still exist — run "
                "scripts/dedupe_user_emails.py (dry-run first), then redeploy.",
                flush=True,
            )

        print("[Ryozan] Startup migrations: OK")
    except Exception as e:
        print(f"[Ryozan] Startup migration error (non-fatal): {e}")
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
    await _run_startup_migrations()
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
    if host == "www.ryozan.app":
        redirect_url = request.url.replace(scheme="https", netloc="ryozan.app")
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
from app.mcp.oauth import oauth_router as _bttco_oauth_router  # noqa: E402
from app.mcp.router import mcp_router as _bttco_mcp_router  # noqa: E402

app.include_router(_bttco_mcp_router)
app.include_router(_bttco_oauth_router)

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
    critical_tables = [
        "bttco_users",
        "bttco_bookings",
        "bttco_booking_slot_holds",
        "bttco_booking_lifecycle_events",
        "bttco_conversations",
        "bttco_messages",
        "bttco_crm_pipeline_tasks",
    ]
    checks: dict[str, str] = {}
    failures: list[str] = []

    try:
        async with async_session() as session:
            await session.execute(sa_text("SELECT 1"))
            checks["db"] = "connected"
            for table in critical_tables:
                result = await session.execute(sa_text("SELECT to_regclass(:table_name)"), {"table_name": table})
                if result.scalar():
                    checks[table] = "ok"
                else:
                    checks[table] = "missing_or_unreadable"
                    failures.append(table)
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
    @app.get("/{full_path:path}", include_in_schema=False)
    @limiter.exempt
    async def spa_fallback(full_path: str):
        if not _should_serve_spa_path(full_path):
            raise HTTPException(status_code=404, detail="Not found")
        clean_path = full_path.strip("/")
        if clean_path:
            html_candidate = _static_dir / f"{clean_path}.html"
            if html_candidate.is_file():
                return FileResponse(str(html_candidate))
            direct_candidate = _static_dir / clean_path
            if direct_candidate.is_file():
                return FileResponse(str(direct_candidate))
        index = _static_dir / "index.html"
        return FileResponse(str(index))
