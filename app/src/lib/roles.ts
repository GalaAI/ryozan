/**
 * Role constants: single source of truth for frontend role checks.
 *
 * Mirrors backend hierarchy: founder > admin > artist > assistant > collector
 * Assistant has staff-level dashboard access (manages bookings, messages, etc.)
 */

export const STAFF_ROLES = ['assistant', 'artist', 'admin', 'founder'] as const
export const ADMIN_ROLES = ['admin', 'founder'] as const
export const ARTIST_PLUS_ROLES = ['artist', 'admin', 'founder'] as const

export function isStaffRole(role: string | undefined | null): boolean {
  return STAFF_ROLES.includes(role as typeof STAFF_ROLES[number])
}

export function isAdminRole(role: string | undefined | null): boolean {
  return ADMIN_ROLES.includes(role as typeof ADMIN_ROLES[number])
}

export function isArtistPlusRole(role: string | undefined | null): boolean {
  return ARTIST_PLUS_ROLES.includes(role as typeof ARTIST_PLUS_ROLES[number])
}
