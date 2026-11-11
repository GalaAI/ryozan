/**
 * Studio settings from GET /studio/settings, read once per page load.
 *
 * One install is one studio, so the app never hardcodes a studio's name,
 * owner, contact, booking days or record ids. Until the settings arrive, or
 * when the read fails, every consumer sees NEUTRAL_STUDIO_SETTINGS: empty
 * values, which the UI renders as neutral copy or hides.
 *
 * This module is pure (type-only imports) so node tests can load it alone;
 * the React hook lives in hooks/useStudioSettings.ts.
 */
import type { DocumentTemplate, PublicSiteMode, StudioSettings, StudioSiteSettings, StudioWeekday, WeeklyHours } from './api'

export const STUDIO_WEEKDAYS: readonly StudioWeekday[] = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

const WEEKDAY_LONG: Record<StudioWeekday, string> = {
  sun: 'Sunday', mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday',
}

/** A new install: the product landing at /, and no studio website content. */
export const NEUTRAL_STUDIO_SITE: StudioSiteSettings = Object.freeze({
  mode: 'product',
  studio_hosts: [],
  tagline: '',
  story_paragraphs: [],
  hours: [],
  hero_image_url: '',
  gallery_image_urls: [],
  social_links: [],
}) as StudioSiteSettings

export const NEUTRAL_STUDIO_SETTINGS: StudioSettings = Object.freeze({
  studio_name: '',
  owner_name: '',
  contact_email: '',
  contact_phone: '',
  address: '',
  timezone: '',
  booking_days: Object.freeze({ closed_weekdays: [], request_only_weekdays: [], request_only_artist_slugs: [] }),
  booking_hours: Object.freeze({ work: Object.freeze({}), consultation: Object.freeze({}) }),
  default_artist_slug: '',
  waiver_template_id: '',
  sample_flash_urls: [],
  email_domain: '',
  shop_pack_scope: '',
  site: NEUTRAL_STUDIO_SITE,
}) as StudioSettings

const text = (value: unknown) => (typeof value === 'string' ? value.trim() : '')
const textList = (value: unknown) => (Array.isArray(value) ? value.map(text).filter(Boolean) : [])
const weekdays = (value: unknown) => textList(value)
  .map((day) => day.toLowerCase())
  .filter((day): day is StudioWeekday => (STUDIO_WEEKDAYS as readonly string[]).includes(day))

const isHourSpan = (value: unknown): value is [number, number] =>
  Array.isArray(value) && value.length === 2 && value.every((hour) => Number.isInteger(hour))

function weeklyHours(value: unknown): WeeklyHours {
  if (!value || typeof value !== 'object') return {}
  const hours: WeeklyHours = {}
  for (const [day, span] of Object.entries(value)) {
    if (/^[0-6]$/.test(day)) hours[day] = isHourSpan(span) ? span : null
  }
  return hours
}

/** Coerce an API body into StudioSettings; anything missing or malformed is neutral. */
export function normalizeStudioSettings(raw: unknown): StudioSettings {
  if (!raw || typeof raw !== 'object') return NEUTRAL_STUDIO_SETTINGS
  const body = raw as Record<string, unknown>
  const days = (body.booking_days && typeof body.booking_days === 'object' ? body.booking_days : {}) as Record<string, unknown>
  const hours = (body.booking_hours && typeof body.booking_hours === 'object' ? body.booking_hours : {}) as Record<string, unknown>
  return {
    studio_name: text(body.studio_name),
    owner_name: text(body.owner_name),
    contact_email: text(body.contact_email),
    contact_phone: text(body.contact_phone),
    address: text(body.address),
    timezone: text(body.timezone),
    booking_days: {
      closed_weekdays: weekdays(days.closed_weekdays),
      request_only_weekdays: weekdays(days.request_only_weekdays),
      request_only_artist_slugs: textList(days.request_only_artist_slugs).map((slug) => slug.toLowerCase()),
    },
    booking_hours: { work: weeklyHours(hours.work), consultation: weeklyHours(hours.consultation) },
    default_artist_slug: text(body.default_artist_slug),
    waiver_template_id: text(body.waiver_template_id),
    sample_flash_urls: textList(body.sample_flash_urls),
    email_domain: text(body.email_domain).toLowerCase(),
    shop_pack_scope: SHOP_PACK_SCOPE.test(text(body.shop_pack_scope)) ? text(body.shop_pack_scope) : '',
    site: normalizeStudioSite(body.site),
  }
}

// A shop brand pack @scope without the @ (SHOP_PACK_SCOPE in the API settings).
const SHOP_PACK_SCOPE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/

// Characters that would let a URL break out of an attribute or a CSS url().
const URL_FORBIDDEN = /[\s"'()<>\\`]/
const HOSTNAME = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/

/** An absolute https:// URL with a host. The API validates too; this keeps a bad body inert. */
export function isWebUrl(value: string): boolean {
  return /^https:\/\/[^/?#]+/.test(value) && !URL_FORBIDDEN.test(value)
}

/** https:// URL or a same-site path (/media/a.jpg), never protocol-relative. */
export function isPublicImageUrl(value: string): boolean {
  if (value.startsWith('/') && !value.startsWith('//')) return !URL_FORBIDDEN.test(value)
  return isWebUrl(value)
}

function normalizeStudioSite(raw: unknown): StudioSiteSettings {
  if (!raw || typeof raw !== 'object') return NEUTRAL_STUDIO_SITE
  const site = raw as Record<string, unknown>
  const hero = text(site.hero_image_url)
  return {
    mode: text(site.mode).toLowerCase() === 'studio' ? 'studio' : 'product',
    studio_hosts: textList(site.studio_hosts).map((host) => host.toLowerCase()).filter((host) => HOSTNAME.test(host)),
    tagline: text(site.tagline),
    story_paragraphs: textList(site.story_paragraphs),
    hours: textList(site.hours),
    hero_image_url: isPublicImageUrl(hero) ? hero : '',
    gallery_image_urls: textList(site.gallery_image_urls).filter(isPublicImageUrl),
    social_links: (Array.isArray(site.social_links) ? site.social_links : [])
      .map((link) => (link && typeof link === 'object' ? link as Record<string, unknown> : {}))
      .map((link) => ({ label: text(link.label), url: text(link.url) }))
      .filter((link) => link.label && isWebUrl(link.url)),
  }
}

/**
 * What `/` shows: the studio website when PUBLIC_SITE_MODE is "studio" or the
 * browser's hostname is one of STUDIO_SITE_HOSTS, otherwise the product landing.
 */
export function resolvePublicSiteMode(settings: StudioSettings, hostname: string): PublicSiteMode {
  if (settings.site.mode === 'studio') return 'studio'
  const host = hostname.trim().toLowerCase().replace(/\.$/, '')
  return host && settings.site.studio_hosts.includes(host) ? 'studio' : 'product'
}

/**
 * A settings read that says whether it succeeded: `error` means the read
 * failed (offline, server error), never that the studio left a value empty.
 */
export type StudioSettingsLoadResult =
  | { status: 'loaded'; settings: StudioSettings }
  | { status: 'error'; error: unknown }

/**
 * One cached read per page load. A failed read is not cached, so the next
 * caller tries again. `load()` resolves neutral on failure; `loadResult()`
 * reports the failure so a caller can tell it from an empty setting.
 */
export function createStudioSettingsLoader(fetchSettings: () => Promise<unknown>) {
  let pending: Promise<StudioSettings> | null = null
  let loaded: StudioSettings | null = null
  const read = (): Promise<StudioSettings> => {
    if (loaded) return Promise.resolve(loaded)
    if (!pending) {
      pending = fetchSettings()
        .then((raw) => { loaded = normalizeStudioSettings(raw); return loaded })
        .finally(() => { pending = null })
    }
    return pending
  }
  return {
    cached: () => loaded,
    /** Replace the cached read after a save; a failed read keeps the old one. */
    async refresh(fetchFresh: () => Promise<unknown>): Promise<void> {
      loaded = normalizeStudioSettings(await fetchFresh())
    },
    load(): Promise<StudioSettings> {
      return read().catch(() => NEUTRAL_STUDIO_SETTINGS)
    },
    loadResult(): Promise<StudioSettingsLoadResult> {
      return read().then(
        (settings) => ({ status: 'loaded' as const, settings }),
        (error: unknown) => ({ status: 'error' as const, error }),
      )
    },
  }
}

/**
 * The install's shop brand pack as a settings read reports it: `error` when
 * the read failed (retry it), `none` when the studio has no pack scope.
 */
export type ShopPackScopeState = { state: 'error' } | { state: 'none' } | { state: 'ready'; scope: string }

export function shopPackScopeState(result: StudioSettingsLoadResult): ShopPackScopeState {
  if (result.status === 'error') return { state: 'error' }
  const scope = result.settings.shop_pack_scope
  return scope ? { state: 'ready', scope } : { state: 'none' }
}

/** The browser's own IANA zone: the starting value on first-run setup. */
export function browserTimezone(): string {
  return new Intl.DateTimeFormat().resolvedOptions().timeZone
}

/** Every IANA zone this browser knows, for the time zone field's suggestions. */
export function studioTimezoneOptions(): string[] {
  return Intl.supportedValuesOf('timeZone')
}

/** Date.getDay() of a YYYY-MM-DD calendar date. */
export function weekdayOf(isoDate: string): StudioWeekday {
  return STUDIO_WEEKDAYS[new Date(`${isoDate}T12:00:00`).getDay()]
}

export function weekdayLongName(day: StudioWeekday): string {
  return WEEKDAY_LONG[day]
}

/** An hour on the 24-hour clock as people say it: 0 is 12 AM, 13 is 1 PM. */
export function hourLabel(hour: number): string {
  return `${hour % 12 || 12} ${hour < 12 ? 'AM' : 'PM'}`
}

/** Read a BOOKING_*_HOURS value (wed=11-18,thu=11-18) for the settings form. */
export function parseHoursSetting(value: string | undefined): Partial<Record<StudioWeekday, [number, number]>> {
  const hours: Partial<Record<StudioWeekday, [number, number]>> = {}
  for (const part of (value ?? '').split(',')) {
    const match = /^(sun|mon|tue|wed|thu|fri|sat)=(\d+)-(\d+)$/.exec(part.trim())
    if (match) hours[match[1] as StudioWeekday] = [Number(match[2]), Number(match[3])]
  }
  return hours
}

export function formatHoursSetting(hours: Partial<Record<StudioWeekday, [number, number]>>): string {
  return STUDIO_WEEKDAYS.flatMap((day) => {
    const span = hours[day]
    return span ? [`${day}=${span[0]}-${span[1]}`] : []
  }).join(',')
}

/** The open days in short form, like TUE-SAT or MON, WED-FRI. Empty when none are known. */
export function openDaysLabel(hours: WeeklyHours): string {
  const names = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
  const runs: Array<[number, number]> = []
  names.forEach((_, day) => {
    if (!hours[String(day)]) return
    const last = runs[runs.length - 1]
    if (last && last[1] === day - 1) last[1] = day
    else runs.push([day, day])
  })
  return runs.map(([first, last]) => (first === last ? names[first] : `${names[first]}-${names[last]}`)).join(', ')
}

export function isClosedDay(settings: StudioSettings, isoDate: string): boolean {
  return settings.booking_days.closed_weekdays.includes(weekdayOf(isoDate))
}

/** Request-only days apply to the listed artists, or to every artist when none are listed. */
export function isRequestOnlyDay(settings: StudioSettings, isoDate: string, artistSlug: string | null | undefined): boolean {
  if (!artistSlug) return false
  const { request_only_weekdays: days, request_only_artist_slugs: slugs } = settings.booking_days
  if (!days.includes(weekdayOf(isoDate))) return false
  return slugs.length === 0 || slugs.includes(artistSlug.toLowerCase())
}

/** Today as YYYY-MM-DD in the studio's zone, or the browser's when none is set or it is invalid. */
export function todayInStudioTimezone(settings: StudioSettings, now: Date = new Date()): string {
  const options: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit' }
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = new Intl.DateTimeFormat('en-US', settings.timezone ? { ...options, timeZone: settings.timezone } : options).formatToParts(now)
  } catch {
    parts = new Intl.DateTimeFormat('en-US', options).formatToParts(now)
  }
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

/** Short zone label for headers, e.g. "NEW YORK"; empty when no zone is set. */
export function timezoneLabel(settings: StudioSettings): string {
  const city = settings.timezone.split('/').pop() ?? ''
  return city.replace(/_/g, ' ').toUpperCase()
}

/** Stand-in flash preview for index, or null when the studio set none. */
export function sampleFlashUrl(settings: StudioSettings, index: number): string | null {
  const urls = settings.sample_flash_urls
  return urls.length ? urls[Math.abs(index) % urls.length] : null
}

/** The waiver the backend's /waiver flow uses when no template id is configured. */
export const DEFAULT_WAIVER_TEMPLATE_NAME = 'Tattoo Consent Waiver'

/**
 * Template for the staff "Send Waiver" action: the configured id, else the
 * active template the backend resolves by name, else the first active waiver.
 */
export function resolveWaiverTemplateId(
  settings: StudioSettings,
  templates: Pick<DocumentTemplate, 'id' | 'name' | 'category' | 'is_active'>[],
): string | null {
  if (settings.waiver_template_id) return settings.waiver_template_id
  const active = templates.filter((template) => template.is_active && template.category === 'waiver')
  return (active.find((template) => template.name === DEFAULT_WAIVER_TEMPLATE_NAME) ?? active[0])?.id ?? null
}
