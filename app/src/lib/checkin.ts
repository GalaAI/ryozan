/**
 * Kiosk check-in rules, kept pure so they can be tested without a browser.
 *
 * The server contract lives in api/app/api/v1/endpoints/documents.py: /documents/checkin/lookup and
 * /verify find a returning client, /documents/checkin opens a walk-in waiver, and
 * /documents/public/{token}/complete files it (_require_waiver_intake_answers enforces the piece, the price,
 * the sober answer and the three consents). Nothing here is shop specific: names, artists and the waiver
 * text all come from the shop's own install at runtime.
 */

export type YesNo = 'Yes' | 'No'

/** The health questions every waiver asks. Keys match WAIVER_HEALTH_KEYS on the server. */
export const HEALTH_QUESTIONS: ReadonlyArray<{ key: string; question: string; hint?: string }> = [
  { key: 'blood_thinners', question: 'On blood thinners?', hint: 'Daily aspirin or a prescribed thinner' },
  { key: 'allergy', question: 'Allergic to anything?', hint: 'Latex, medications, inks or dyes' },
  { key: 'pregnant', question: 'Pregnant or nursing?' },
  { key: 'blood_disorder', question: 'Bleeding or clotting disorder?' },
  { key: 'skin_condition', question: 'Rash, sunburn or keloids where we are tattooing?' },
]

export function digits(value: string | null | undefined): string {
  return String(value ?? '').replace(/\D/g, '')
}

export function validEmail(value: string | null | undefined): boolean {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(value ?? '').trim())
}

/** An email, or a 10 digit number (11 with a leading 1). Mirrors checkin_verify.normalize_contact. */
export function contactOk(value: string): boolean {
  const trimmed = value.trim()
  if (trimmed.includes('@')) return validEmail(trimmed)
  const d = digits(trimmed)
  return d.length === 10 || (d.length === 11 && d.startsWith('1'))
}

/** Local calendar date as YYYY-MM-DD. */
export function isoDate(now: Date = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

/** 18 or older on `now`, and a plausible birth date. */
export function isAdult(dob: string, now: Date = new Date()): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dob)
  if (!match) return false
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  let age = now.getFullYear() - year
  if (now.getMonth() + 1 < month || (now.getMonth() + 1 === month && now.getDate() < day)) age -= 1
  return age >= 18 && age < 120
}

/** "$1,250.00" -> "1250.00"; anything not a positive number -> null. */
export function parsePrice(value: string): string | null {
  const cleaned = value.replace(/[$,\s]/g, '')
  return /^\d+(\.\d{1,2})?$/.test(cleaned) && Number(cleaned) > 0 ? cleaned : null
}

/** "Sam Example 555 010 0199" -> ["Sam Example", "555 010 0199"]; the server stores both in one field. */
export function splitEmergency(value: string | null | undefined): [string, string] {
  const text = String(value ?? '').trim()
  const match = /^(.*?)[\s,]*(\+?1?[\s.(-]*\d{3}[\s.)-]*\d{3}[\s.-]*\d{4})\s*$/.exec(text)
  return match ? [match[1].trim(), match[2].trim()] : [text, '']
}

export interface CheckinDetails {
  fullName: string
  dob: string
  phone: string
  email: string
  address: string
  emergencyName: string
  emergencyPhone: string
  emergencyAddress: string
}

export interface CheckinVisit {
  artistSlug: string
  description: string
  placement: string
  price: string
  date: string
}

/** Field name -> message for every detail that is missing or wrong. Empty means good to go. */
export function detailsErrors(d: CheckinDetails, now: Date = new Date()): Record<string, string> {
  const errors: Record<string, string> = {}
  if (d.fullName.trim().split(/\s+/).filter(Boolean).length < 2) errors.fullName = 'Your full legal name, as it reads on your ID.'
  if (!isAdult(d.dob, now)) errors.dob = '18 and up only.'
  if (digits(d.phone).length < 10) errors.phone = 'A 10 digit number.'
  if (!validEmail(d.email)) errors.email = 'That email does not look right.'
  if (d.address.trim().length <= 5) errors.address = 'Street, city and zip.'
  if (d.emergencyName.trim().length <= 1) errors.emergencyName = 'Who do we call?'
  if (digits(d.emergencyPhone).length < 10) errors.emergencyPhone = 'Their 10 digit number.'
  return errors
}

export function visitErrors(v: CheckinVisit): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!v.artistSlug) errors.artist = 'Pick your artist.'
  if (v.description.trim().length <= 2) errors.description = 'Tell us what we are tattooing.'
  if (v.placement.trim().length <= 2) errors.placement = 'Tell us where it goes.'
  if (!parsePrice(v.price)) errors.price = 'The price you and your artist agreed on.'
  if (!v.date) errors.date = 'Pick the date.'
  return errors
}

export interface HealthState {
  answers: Record<string, YesNo | undefined>
  /** Shown only when a prior waiver has health answers on file. */
  sameOffered: boolean
  allergyDetail: string
  medications: string
}

export function sameAsLast(h: HealthState): boolean {
  return h.sameOffered && h.answers.same === 'Yes'
}

/** Keys still unanswered, plus 'allergyDetail' when a yes needs a what. */
export function healthMissing(h: HealthState): string[] {
  const keys = ['intoxicated']
  if (h.sameOffered) keys.push('same')
  if (!sameAsLast(h)) keys.push(...HEALTH_QUESTIONS.map((q) => q.key))
  const missing = keys.filter((k) => !h.answers[k])
  if (!sameAsLast(h) && h.answers.allergy === 'Yes' && h.allergyDetail.trim().length <= 1) missing.push('allergyDetail')
  return missing
}

/** Any yes on the medical questions: the artist goes over it before starting. */
export function healthNeedsReview(h: HealthState): boolean {
  return !sameAsLast(h) && HEALTH_QUESTIONS.some((q) => h.answers[q.key] === 'Yes')
}

/** The `data` body for POST /documents/public/{token}/complete. */
export function buildWaiverData(input: {
  details: CheckinDetails
  visit: CheckinVisit
  health: HealthState
  priorHealth: Record<string, string>
  photoIdUrl: string
  reusePhotoId: boolean
  reuseSignature: boolean
}): Record<string, unknown> {
  const { details, visit, health, priorHealth } = input
  const same = sameAsLast(health)
  const yn = (key: string) => (same ? priorHealth[key] || 'No' : health.answers[key])
  const data: Record<string, unknown> = {
    full_name: details.fullName.trim(),
    date_of_birth: details.dob,
    phone: details.phone.trim(),
    email: details.email.trim(),
    street_address: details.address.trim(),
    emergency_contact: `${details.emergencyName.trim()} ${details.emergencyPhone.trim()}`.trim(),
    emergency_contact_address: details.emergencyAddress.trim(),
    artist_name: visit.artistSlug,
    tattoo_description: visit.description.trim(),
    tattoo_placement: visit.placement.trim(),
    quoted_price: parsePrice(visit.price) ?? '',
    session_date: visit.date,
    intoxicated: health.answers.intoxicated,
    pregnant: yn('pregnant'),
    blood_thinners: yn('blood_thinners'),
    blood_disorder: yn('blood_disorder'),
    skin_condition: yn('skin_condition'),
    allergies: same
      ? priorHealth.allergies || 'None'
      : health.answers.allergy === 'Yes' ? health.allergyDetail.trim() : 'None',
    medical_conditions: same ? priorHealth.medical_conditions || '' : health.medications.trim(),
    health_same_as_last_visit: same,
    health_and_eligibility: true,
    risks_and_consent: true,
    accept_terms: true,
  }
  if (input.reusePhotoId) data.reuse_photo_id = true
  else data.photo_id_url = input.photoIdUrl
  if (input.reuseSignature) data.reuse_signature = true
  return data
}
