/**
 * The public booking flow (/book, /book/<artist>, /book/sesh, /book/confirm, /ask):
 * routes, the per-tab draft and the request payloads. No React and no network,
 * so node tests bundle it with the page views.
 *
 * Nothing here names a studio or an artist: the roster comes from GET /artists/
 * and the shop's name and contact lines from GET /studio/settings.
 */
import type { PublicBookingSubmit, PublicCheckoutStatus, PublicInquirySubmit } from './api'

export type BookingStep = 'artist' | 'sesh' | 'confirm' | 'ask'

export const BOOK_ROUTE: Record<BookingStep, string> = {
  artist: '/book',
  sesh: '/book/sesh',
  confirm: '/book/confirm',
  ask: '/ask',
}

/** The artist step's own deep link: /book/<slug>. */
export function artistBookingPath(slug: string): string {
  return `${BOOK_ROUTE.artist}/${encodeURIComponent(slug)}`
}

const STEP_SEGMENTS = new Set(['sesh', 'confirm'])

/** The artist slug a /book/<slug> URL carries, or null for /book and the step routes. */
export function artistSlugFromPath(pathname: string): string | null {
  const match = /^\/book\/([^/?#]+)\/?$/.exec(pathname)
  if (!match || STEP_SEGMENTS.has(match[1])) return null
  try {
    return decodeURIComponent(match[1])
  } catch {
    return null
  }
}

export interface PreferredSlotDraft {
  date: string
  time: string
}

export interface BookingDraft {
  inquiryId: string
  artistSlug: string
  artistName: string
  description: string
  placement: string
  size: string
  slots: PreferredSlotDraft[]
  firstName: string
  lastName: string
  email: string
  phone: string
  smsOptIn: boolean
}

export const DRAFT_STORAGE_KEY = 'ryozan.booking.draft.v1'
export const MAX_PREFERRED_SLOTS = 3
export const MIN_DESCRIPTION_LENGTH = 10

function newInquiryId(): string {
  try {
    return globalThis.crypto.randomUUID()
  } catch {
    return `inq-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  }
}

export function emptyDraft(): BookingDraft {
  return {
    inquiryId: newInquiryId(),
    artistSlug: '',
    artistName: '',
    description: '',
    placement: '',
    size: '',
    slots: Array.from({ length: MAX_PREFERRED_SLOTS }, () => ({ date: '', time: '' })),
    firstName: '',
    lastName: '',
    email: '',
    phone: '',
    smsOptIn: false,
  }
}

/**
 * One booking request: the draft the collector is filling and, once sent, the
 * server's answer. The page keeps a single component instance across the step
 * routes, so the flow carries both together and resets them here, not by key.
 */
export interface BookingRequest<R> {
  draft: BookingDraft
  submitted: R | null
}

/** A request went out: keep its answer for the confirmation, drop its draft and idempotency id. */
export function completeRequest<R>(submitted: R): BookingRequest<R> {
  return { draft: emptyDraft(), submitted }
}

/**
 * Entering the artist step after a sent request starts the next one: the old
 * confirmation is cleared and the draft starts over with a new idempotency id.
 * Anything else leaves the request as it is (the same object, so no re-render).
 */
export function beginRequest<R>(request: BookingRequest<R>, step: BookingStep): BookingRequest<R> {
  if (step !== 'artist' || request.submitted === null) return request
  return { draft: emptyDraft(), submitted: null }
}

/** The draft after a question went out: same fields, a new id so the next send is its own request. */
export function withFreshInquiryId(draft: BookingDraft): BookingDraft {
  return { ...draft, inquiryId: newInquiryId() }
}

/** Reads the tab's draft. Storage can be missing or blocked; the flow still works in memory. */
export function readDraft(storage: Pick<Storage, 'getItem'> | null | undefined): BookingDraft {
  const base = emptyDraft()
  try {
    const raw = storage?.getItem(DRAFT_STORAGE_KEY)
    if (!raw) return base
    const parsed = JSON.parse(raw) as Partial<BookingDraft>
    const slots = Array.isArray(parsed.slots) ? parsed.slots : []
    return {
      ...base,
      ...Object.fromEntries(Object.entries(parsed).filter(([key, value]) => key in base && typeof value === typeof base[key as keyof BookingDraft])),
      slots: base.slots.map((empty, index) => ({
        date: typeof slots[index]?.date === 'string' ? slots[index].date : empty.date,
        time: typeof slots[index]?.time === 'string' ? slots[index].time : empty.time,
      })),
    }
  } catch {
    return base
  }
}

export function writeDraft(storage: Pick<Storage, 'setItem'> | null | undefined, draft: BookingDraft): void {
  try {
    storage?.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft))
  } catch {
    /* storage unavailable: the in-memory draft still carries the step */
  }
}

export function clearDraft(storage: Pick<Storage, 'removeItem'> | null | undefined): void {
  try {
    storage?.removeItem(DRAFT_STORAGE_KEY)
  } catch {
    /* nothing to clear */
  }
}

/** Filled, distinct proposed times in the order the collector gave them. */
export function filledSlots(slots: PreferredSlotDraft[]): PreferredSlotDraft[] {
  const seen = new Set<string>()
  const out: PreferredSlotDraft[] = []
  for (const slot of slots) {
    const date = slot.date.trim()
    const time = slot.time.trim().slice(0, 5)
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) continue
    const key = `${date} ${time}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ date, time })
  }
  return out.slice(0, MAX_PREFERRED_SLOTS)
}

/** Why the details step cannot continue yet, or null. */
export function seshProblem(draft: BookingDraft): string | null {
  if (!draft.artistSlug) return 'Choose an artist first.'
  if (draft.description.trim().length < MIN_DESCRIPTION_LENGTH) {
    return `Describe the tattoo in at least ${MIN_DESCRIPTION_LENGTH} characters.`
  }
  if (filledSlots(draft.slots).length === 0) return 'Propose at least one date and time.'
  return null
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function contactProblem(fields: Pick<BookingDraft, 'firstName' | 'lastName' | 'email'>): string | null {
  if (!fields.firstName.trim() || !fields.lastName.trim()) return 'Add your first and last name.'
  if (!EMAIL.test(fields.email.trim())) return 'Add an email address the shop can reply to.'
  return null
}

export function bookingPayload(draft: BookingDraft): PublicBookingSubmit {
  const phone = draft.phone.trim()
  return {
    inquiry_id: draft.inquiryId,
    artist_slug: draft.artistSlug,
    first_name: draft.firstName.trim(),
    last_name: draft.lastName.trim(),
    email: draft.email.trim(),
    ...(phone ? { phone } : {}),
    sms_opt_in: Boolean(phone) && draft.smsOptIn,
    description: draft.description.trim(),
    booking_kind: 'tattoo_session',
    ...(draft.placement.trim() ? { placement: draft.placement.trim() } : {}),
    ...(draft.size.trim() ? { size_estimate: draft.size.trim() } : {}),
    preferred_slots: filledSlots(draft.slots),
  }
}

export interface InquiryFields {
  artistSlug: string
  firstName: string
  lastName: string
  email: string
  phone: string
  smsOptIn: boolean
  message: string
}

export function inquiryProblem(fields: InquiryFields): string | null {
  const contact = contactProblem(fields)
  if (contact) return contact
  if (fields.message.trim().length < MIN_DESCRIPTION_LENGTH) {
    return `Write your question in at least ${MIN_DESCRIPTION_LENGTH} characters.`
  }
  return null
}

export function inquiryPayload(fields: InquiryFields, inquiryId: string): PublicInquirySubmit {
  const phone = fields.phone.trim()
  return {
    inquiry_id: inquiryId,
    artist_slug: fields.artistSlug || 'any',
    first_name: fields.firstName.trim(),
    last_name: fields.lastName.trim(),
    email: fields.email.trim(),
    ...(phone ? { phone } : {}),
    sms_opt_in: Boolean(phone) && fields.smsOptIn,
    message: fields.message.trim(),
  }
}

export interface CheckoutReturn {
  outcome: 'success' | 'cancelled'
  reference: string
  sessionId: string | null
}

/** The Stripe redirect's query on /book/confirm, or null for a plain visit. */
export function checkoutReturn(search: string): CheckoutReturn | null {
  const params = new URLSearchParams(search)
  const outcome = params.get('checkout')
  const sessionId = params.get('session_id')
  const validSession = sessionId && /^cs_[A-Za-z0-9_]{8,250}$/.test(sessionId) ? sessionId : null
  if (outcome === 'success' || outcome === 'cancelled') {
    return { outcome, reference: params.get('ref') || '', sessionId: validSession }
  }
  // A bare ?session_id= is a success return whose outcome marker was dropped.
  if (validSession) return { outcome: 'success', reference: params.get('ref') || '', sessionId: validSession }
  return null
}

/** What the deposit line says after the server answers; the redirect alone is never a receipt. */
export function depositLine(status: Pick<PublicCheckoutStatus, 'deposit_state'>): string {
  switch (status.deposit_state) {
    case 'paid':
      return 'Deposit received.'
    case 'authorized':
      return 'Deposit held on your card while your artist reviews the request. You are only charged if they accept it.'
    case 'pending':
      return 'Payment is still processing. This page does not need to stay open; you will get an email when it clears.'
    default:
      return 'No deposit is due for this request.'
  }
}

/** "2030-01-02" and "13:00" as the collector reads them, in the browser's locale. */
export function slotLabel(slot: PreferredSlotDraft): string {
  const [year, month, day] = slot.date.split('-').map(Number)
  const [hour, minute] = slot.time.split(':').map(Number)
  if (!year || !month || !day || Number.isNaN(hour) || Number.isNaN(minute)) return `${slot.date} ${slot.time}`
  const when = new Date(year, month - 1, day, hour, minute)
  return when.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
