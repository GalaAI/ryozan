// The SPA's inline redaction did not cover the standalone booking documents.
// One public-route policy now governs both entry points; no client identifiers,
// reference codes, query strings, or private routes belong in web analytics.
const PUBLIC_PATHS = new Set([
  '/', '/crew', '/our-story', '/parking', '/contact', '/gift-cards', '/legal',
  '/privacy', '/terms', '/book', '/book/sesh', '/book/confirm', '/ask',
])

export function publicAnalyticsUrl(raw: string): string | null {
  try {
    const url = new URL(raw)
    if (!['https:', 'http:'].includes(url.protocol)) return null
    const path = url.pathname.replace(/\/+$/, '') || '/'
    // Dynamic artist values are never sent. A public URL can still contain an
    // accidentally pasted credential or an unknown slug.
    const route = /^\/artist\/[^/]+$/.test(path) ? '/artist'
      : /^\/book\/(?!sesh$|confirm$)[^/]+$/.test(path) ? '/book'
      : PUBLIC_PATHS.has(path) ? path : null
    return route === null ? null : url.origin + route
  } catch {
    return null
  }
}

export function beforePublicAnalytics<T extends { url: string }>(event: T): T | null {
  const url = publicAnalyticsUrl(event.url)
  return url ? { ...event, url } : null
}

export function isProductionAnalytics(): boolean {
  return ['ryozan.app', 'www.ryozan.app'].includes(window.location.hostname)
}

export function bookingOutcome(result: Record<string, unknown>): string | null {
  if (result.idempotent_acknowledgement === true) return null
  if (result.kind === 'inquiry' && result.booking_status === 'inquiry') return 'inquiry_accepted'
  if (typeof result.reference_code !== 'string' || !result.reference_code) return null
  if (result.checkout_url) return 'checkout_required'
  return result.booking_status === 'confirmed' ? 'booking_confirmed' : 'request_accepted'
}
