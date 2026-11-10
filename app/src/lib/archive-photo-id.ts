/**
 * The client photo ID image on an archived waiver record.
 *
 * Waivers store it as `photo_id_url` (current form) or `photo_id` (older records).
 * The server returns either as a short-lived signed link, so this only picks the
 * field; it never builds or rewrites a URL. Only http(s) links are returned.
 */
export function archivePhotoIdUrl(data: Record<string, unknown> | null | undefined): string | null {
  if (!data) return null
  for (const key of ['photo_id_url', 'photo_id'] as const) {
    const value = data[key]
    if (typeof value === 'string') {
      const url = value.trim()
      if (/^https?:\/\//i.test(url)) return url
    }
  }
  return null
}
