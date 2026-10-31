/**
 * Frontend route constants.
 *
 * Single source of truth for all internal navigation URLs.
 * When a route changes, update it here and every reference picks it up.
 *
 * Mirrors backend: api/app/core/routes.py
 *
 * Usage:
 *   import { ROUTES } from '../lib/routes'
 *   navigate(ROUTES.BOOKINGS)
 *   <Link to={ROUTES.COURIER}>Messages</Link>
 */

// ── Dashboard panels ────────────────────────
export const ROUTES = {
  // Main dashboard
  DASHBOARD: '/dashboard',
  WAIVERS: '/dashboard?panel=waivers',
  ASSETS: '/dashboard?panel=assets',
  ARTIST_SCROLL: '/dashboard?portal=hub&workflow=artist-scroll',
  PROFILE: '/dashboard/profile',

  // Modules
  BOOKINGS: '/dashboard/huddle?charm=bookings',
  HUDDLE: '/dashboard/huddle',
  COURIER: '/dashboard/courier',
  GUILD: '/dashboard/guild',
  VAULT: '/dashboard/vault',
  HUSTLE: '/dashboard/hustle',
  HUSTLE_DEPOSITS: '/dashboard/hustle?stash=econ&page=command&focus=deposits',
  QUESTS: '/dashboard/quests',
  SCHEDULE: '/dashboard', // deprecated: schedule now lives in the portal
  CLIENTS: '/dashboard/huddle/clients',
  SETTINGS: '/dashboard/artist-profile',
  SCROLL: '/dashboard?portal=hub&workflow=artist-scroll',
  ARTIST_PROFILE: '/dashboard/artist-profile',
  ADMIN_USERS: '/dashboard/admin/users',
  WALLET: '/dashboard/wallet',

  // Collector pages
  BOOK: '/dashboard/book',
  CLUB: '/dashboard/club',
  LOOT: '/dashboard/loot',
  SHOP: '/dashboard/shop',

  // Standalone bookings manager
  BOOKINGS_MANAGER: '/dashboard/bookings',

  // Herald: guides + parking + aftercare
  HERALD: '/dashboard/herald',

  // Public pages
  PORTAL: '/portal',
  LEGAL: '/legal',
  HOME: '/',
} as const

// ── Dynamic routes (use with template literals) ──
export const dynamicRoutes = {
  form: (token: string) => `/form/${token}`,
  quote: (token: string) => `/quote/${token}`,
  quoteConfirmed: (token: string) => `/quote/${token}/confirmed`,
  bookingRef: (ref: string) => `/dashboard/huddle?charm=bookings&ref=${ref}`,
  collectorBookingRef: (ref: string) => `${ROUTES.BOOKINGS_MANAGER}?ref=${encodeURIComponent(ref)}`,
  checkinArtist: (slug: string) => `/checkin/${slug}`,
  courierConvo: (id: string) => `/dashboard/courier?convo=${id}`,
} as const

export type RouteKey = keyof typeof ROUTES
