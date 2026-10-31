"""
Frontend route constants.

Single source of truth for all frontend URLs used in notifications,
emails, and webhooks. When a route changes on the frontend, update
it here and every reference picks it up.

Mirrors frontend: app/src/lib/routes.ts

Usage:
    from app.core.routes import ROUTES
    action_url=ROUTES.WAIVERS
    link=f"{settings.FRONTEND_URL}{ROUTES.QUESTS}"
"""


class _Routes:
    """Frontend route constants."""

    # Dashboard panels
    DASHBOARD = "/dashboard"
    WAIVERS = "/dashboard?panel=waivers"
    CODEX = "/dashboard?panel=codex"
    PROFILE = "/dashboard/profile"

    # Modules
    BOOKINGS = "/dashboard/huddle?charm=bookings"
    HUDDLE = "/dashboard/huddle"
    COURIER = "/dashboard/courier"
    GUILD = "/dashboard/guild"
    VAULT = "/dashboard/vault"
    HUSTLE = "/dashboard/hustle"
    QUESTS = "/dashboard/quests"
    SCHEDULE = "/dashboard/huddle/schedule"
    CLIENTS = "/dashboard/huddle/clients"
    SETTINGS = "/dashboard/settings"
    SCROLL = "/dashboard?portal=hub&workflow=artist-scroll"
    ARTIST_PROFILE = "/dashboard/artist-profile"
    ADMIN_USERS = "/dashboard/admin/users"
    WALLET = "/dashboard/wallet"

    # Collector pages
    BOOK = "/dashboard/book"
    CLUB = "/dashboard/club"
    LOOT = "/dashboard/loot"
    SHOP = "/dashboard/shop"

    # Standalone bookings manager
    BOOKINGS_MANAGER = "/dashboard/bookings"

    # Public pages
    CHECKIN = "/checkin"
    PORTAL = "/portal"
    LEGAL = "/legal"
    HOME = "/"

    # Dynamic (use with .format() or f-string)
    FORM = "/form/{token}"
    QUOTE = "/quote/{token}"
    QUOTE_CONFIRMED = "/quote/{token}/confirmed"
    BOOKING_REF = "/dashboard/huddle?charm=bookings&ref={ref}"
    CHECKIN_ARTIST = "/checkin/{slug}"
    COURIER_CONVO = "/dashboard/courier?convo={id}"
    HUSTLE_DEPOSITS = "/dashboard/hustle?stash=econ&page=command&focus=deposits"


ROUTES = _Routes()
