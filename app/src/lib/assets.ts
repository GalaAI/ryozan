/**
 * Asset URL helpers for Cloudflare R2 media storage.
 *
 * All portfolio images, shop photos, flash sheets, and graphics
 * are served from assets.ryozan.app (Cloudflare R2).
 *
 * Small branding assets (logo-icon.png, fonts) stay local in /assets/.
 */

export const R2_BASE = 'https://assets.ryozan.app'

/** Build an R2 URL for a portfolio image */
export function portfolioUrl(artist: string, filename: string): string {
  return `${R2_BASE}/portfolios/${artist}/${filename}`
}

/** Build an R2 URL for a portfolio thumbnail */
export function portfolioThumbUrl(artist: string, filename: string): string {
  return `${R2_BASE}/portfolios/${artist}/thumbs/${filename}`
}

/** Build an R2 URL for a flash sheet image */
export function flashUrl(artist: string, filename: string): string {
  return `${R2_BASE}/flash/${artist}/${filename}`
}

/** Build an R2 URL for a shop photo */
export function shopPhotoUrl(filename: string): string {
  return `${R2_BASE}/shop/photos/${filename}`
}

/** Build an R2 URL for a shop photo thumbnail */
export function shopThumbUrl(filename: string): string {
  return `${R2_BASE}/shop/photos/thumbs/${filename}`
}

/** Build an R2 URL for a graphic */
export function graphicUrl(filename: string): string {
  return `${R2_BASE}/graphics/${filename}`
}

/** Build an R2 URL for a branding asset */
export function brandingUrl(filename: string): string {
  return `${R2_BASE}/branding/${filename}`
}
