/**
 * Display helpers for the studio website (PUBLIC_SITE_MODE=studio).
 *
 * Every studio-specific word or image on those pages comes from
 * GET /studio/settings or the artists API through these helpers. Nothing here
 * names a studio, a person, a place or a domain: an install with empty
 * settings renders neutral copy and hides the sections it has no data for.
 *
 * No React and no network: node tests bundle it with the page views.
 */
import type { ArtistDetail, ArtistSummary, StudioSettings } from './api'
import { isPublicImageUrl as isImageUrl, isWebUrl } from './studio-settings'

/** Shown when STUDIO_NAME is blank. */
export const NEUTRAL_STUDIO_NAME = 'Tattoo studio'

export function studioName(settings: StudioSettings): string {
  return settings.studio_name || NEUTRAL_STUDIO_NAME
}

export interface StudioContact {
  address: string
  phone: string
  phoneHref: string
  email: string
  hours: string[]
}

export function studioContact(settings: StudioSettings): StudioContact {
  const phone = settings.contact_phone
  const dialable = phone.replace(/[^\d+]/g, '')
  return {
    address: settings.address,
    phone,
    phoneHref: dialable ? `tel:${dialable}` : '',
    email: settings.contact_email,
    hours: settings.site.hours,
  }
}

export function hasContact(contact: StudioContact): boolean {
  return Boolean(contact.address || contact.phone || contact.email || contact.hours.length)
}

/**
 * Inline style for the hero: the photo as a CSS variable the stylesheet
 * layers under its own gradient. Undefined when no photo is set.
 */
export function heroImageStyle(url: string): Record<string, string> | undefined {
  return url && isImageUrl(url) ? { '--studio-hero-image': `url("${url}")` } : undefined
}

/** Artists the public site lists: a slug and a name, nothing else assumed. */
export function listedArtists(artists: ArtistSummary[] | null | undefined): ArtistSummary[] {
  return (artists ?? []).filter((artist) => artist.slug && artist.name)
}

export function artistSpecialties(artist: Pick<ArtistSummary, 'specialty'>): string {
  return (artist.specialty ?? []).map((style) => style.trim()).filter(Boolean).join(' · ')
}

export function artistAvatar(artist: Pick<ArtistSummary, 'avatar_url'>): string {
  const url = (artist.avatar_url ?? '').trim()
  return isImageUrl(url) ? url : ''
}

/** Portfolio images, as the 640px thumb when the API has one. */
export function artistPortfolio(artist: Pick<ArtistDetail, 'portfolio_images' | 'portfolio_thumbs'>): string[] {
  const thumbs = artist.portfolio_thumbs ?? {}
  return (artist.portfolio_images ?? [])
    .map((original) => thumbs[original] || original)
    .filter((url) => isImageUrl(url))
}

const ARTIST_LINK_FIELDS = [
  ['instagram_url', 'Instagram'],
  ['tiktok_url', 'TikTok'],
  ['facebook_url', 'Facebook'],
  ['website_url', 'Website'],
] as const

export function artistLinks(artist: Partial<Pick<ArtistDetail, 'instagram_url' | 'tiktok_url' | 'facebook_url' | 'website_url'>>): { label: string; url: string }[] {
  return ARTIST_LINK_FIELDS
    .map(([field, label]) => ({ label, url: (artist[field] ?? '').trim() }))
    .filter((link) => isWebUrl(link.url))
}

/** Bio as paragraphs, split on blank lines. */
export function paragraphs(text: string | null | undefined): string[] {
  return (text ?? '').split(/\n\s*\n/).map((block) => block.replace(/\s+/g, ' ').trim()).filter(Boolean)
}
