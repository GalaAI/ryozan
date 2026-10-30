/**
 * BookingsManager: Full booking management dashboard.
 *
 * Role-based views:
 *   - Collector: sees own bookings
 *   - Artist: sees bookings assigned to them
 *   - Admin/Founder: sees all bookings with full controls
 *
 * Features: status tabs, search, expandable detail, status actions,
 * empty states, responsive layout.
 */

import { useEffect, useState, useMemo, useCallback, lazy, Suspense } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { bookings as bookingsApi, quotes as quotesApi, calendar as calendarApi, artists as artistsApi, conversations as conversationsApi, crm as crmApi, type Booking, type BookingDraftRecord, type BookingLifecycleEvent, type BookingScheduleSuggestion, type QuoteCreate, type QuoteResponse, type CrmPipelineTaskRecord, type ArtistSummary } from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { ROUTES } from '../../lib/routes'
import { isAdminRole, isArtistPlusRole } from '../../lib/roles'
import { formatTime12h, requestedBookingSlots, formatRequestedSlot } from '../../lib/format'
import '../../styles/courier.css'
// The proxy "Add booking" form (.bkm-proxy-*) and its embedded DayPreference
// calendar (.day-pref-*) are styled in booking.css. Without this import the
// form renders as bare full-width inputs and an unstyled calendar.
import '../../styles/booking.css'
import { useToast } from '../../components/Toast'
import StatusBadge from '../../components/ui/StatusBadge'
import { DayPreference, type DayPreferenceValue } from '../../components/booking/DayPreference'

// Admin-only personal time-blocking panel, code-split so non-admin roles
// never download it (design system rule 5).
const TimeBlockManager = lazy(() => import('./huddle/TimeBlockManager'))

/* ── Image URL helper (handles Blob, Drive, and direct URLs) ── */

function imageThumb(url: string, size = 'w260'): string {
  // R2 and direct CDN URLs are directly embeddable
  if (url.startsWith('https://') && !url.includes('drive.google.com')) return url

  // Drive URLs: use Google's thumbnail service directly
  const m1 = url.match(/\/d\/([a-zA-Z0-9_-]+)/)
  if (m1) return `https://drive.google.com/thumbnail?id=${m1[1]}&sz=${size}`
  const m2 = url.match(/[?&]id=([a-zA-Z0-9_-]+)/)
  if (m2) return `https://drive.google.com/thumbnail?id=${m2[1]}&sz=${size}`

  // lh3 googleusercontent URLs work directly
  if (url.includes('lh3.googleusercontent.com')) return url

  // Other direct URLs
  return url
}

/* ── Style slug → display label mapping ── */

const STYLE_LABELS: Record<string, string> = {
  traditional: 'Traditional',
  japanese: 'Japanese',
  fineline: 'Fine Line',
  blackgrey: 'Black & Grey',
  'black-grey': 'Black & Grey',
  neotraditional: 'Neo-Traditional',
  realism: 'Realism',
  illustrative: 'Illustrative',
  othernotsure: 'Other / Not Sure',
}

function formatStyle(slug: string | null | undefined): string | null {
  if (!slug) return null
  return STYLE_LABELS[slug.toLowerCase()] || slug
}

interface ProxyClientOption {
  key: string
  source: 'inquiry' | 'rolodex'
  name: string
  email: string
  phone: string
  artistSlug?: string | null
  description?: string | null
  placement?: string | null
  meta: string
}

function firstDefined(...values: Array<string | null | undefined>): string {
  return values.find((value) => value && value.trim())?.trim() || ''
}

function inferDurationMinutes(prompt: string): string {
  const lower = prompt.toLowerCase()
  const hourMatch = lower.match(/(\d+(?:\.\d+)?)\s*(h|hr|hour|hours)\b/)
  if (hourMatch) return String(Math.round(Number(hourMatch[1]) * 60))
  const minuteMatch = lower.match(/(\d+)\s*(m|min|minute|minutes)\b/)
  if (minuteMatch) return minuteMatch[1]
  if (lower.includes('half day') || lower.includes('half-day')) return '240'
  if (lower.includes('full day') || lower.includes('full-day')) return '420'
  if (lower.includes('touch up') || lower.includes('touch-up')) return '60'
  return ''
}

function inferDepositAmount(prompt: string): string {
  const match = prompt.match(/\$?\b(\d{2,4})\b(?:\s*(deposit|down))?/i)
  return match?.[2] ? match[1] : ''
}

function inferPlacement(prompt: string): string {
  const lower = prompt.toLowerCase()
  const placements = [
    'forearm', 'upper arm', 'lower arm', 'sleeve', 'hand', 'wrist',
    'chest', 'back', 'ribs', 'stomach', 'thigh', 'calf', 'leg',
    'ankle', 'foot', 'neck', 'shoulder',
  ]
  return placements.find((placement) => lower.includes(placement)) || ''
}

/* ── Status badge mapping (component library) ── */


/* ── Status config ──────────────────────────── */

type BookingCategory = 'all' | 'new' | 'in_progress' | 'booked' | 'past'

const STATUS_TABS: { key: BookingCategory; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'new', label: 'New Requests' },
  { key: 'in_progress', label: 'In Progress' },
  { key: 'booked', label: 'Booked' },
  { key: 'past', label: 'Past' },
]

// Map categories to backend statuses
const CATEGORY_STATUSES: Record<BookingCategory, string[]> = {
  all: [],
  new: ['pending'],
  in_progress: ['quoted'],
  booked: ['confirmed'],
  past: ['completed', 'cancelled', 'no_show', 'expired'],
}

function categoryForStatus(status: string): BookingCategory {
  if (CATEGORY_STATUSES.new.includes(status)) return 'new'
  if (CATEGORY_STATUSES.in_progress.includes(status)) return 'in_progress'
  if (CATEGORY_STATUSES.booked.includes(status)) return 'booked'
  if (CATEGORY_STATUSES.past.includes(status)) return 'past'
  return 'all'
}

/* ── Inline icons ───────────────────────────── */

const Icons = {
  search: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
    </svg>
  ),
  calendar: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>
    </svg>
  ),
  clock: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
    </svg>
  ),
  user: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
    </svg>
  ),
  palette: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10"/><circle cx="12" cy="8" r="1.5" fill="currentColor"/>
      <circle cx="8" cy="12" r="1.5" fill="currentColor"/><circle cx="16" cy="12" r="1.5" fill="currentColor"/>
      <circle cx="12" cy="16" r="1.5" fill="currentColor"/>
    </svg>
  ),
  pin: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>
    </svg>
  ),
  ruler: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 12h5m3 0h4m3 0h5M7 8v8m5-8v8m5-8v8"/>
    </svg>
  ),
  dollar: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 2v20m5-17H9.5a3.5 3.5 0 1 0 0 7h5a3.5 3.5 0 1 1 0 7H7"/>
    </svg>
  ),
  note: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
      <polyline points="14,2 14,8 20,8"/>
    </svg>
  ),
  mail: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
    </svg>
  ),
  phone: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.362 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.338 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/>
    </svg>
  ),
  hash: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/>
    </svg>
  ),
  chevron: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m6 9 6 6 6-6"/>
    </svg>
  ),
  empty: (
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.3">
      <rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/>
    </svg>
  ),
}

/* ── Smart Slot Picker ───────────────────────── */

interface SuggestedSlot {
  date: string   // YYYY-MM-DD
  time: string   // HH:MM
  label: string  // human-readable e.g. "Thu Apr 3 · 12:00 PM"
  duration_minutes: number
  confidence?: string
}

interface BlockedSlot {
  date: string
  reasons: string[]
}

/** Mini calendar + duration → smart slot suggestions */
function SlotPicker({
  bookingId: _bookingId,
  artistSlug,
  availabilitySource,
  preferredDate,
  onSelect,
  duration,
  onDurationChange,
}: {
  bookingId: string
  artistSlug: string | null
  availabilitySource: string
  preferredDate?: string | null
  onSelect: (date: string, time: string, duration: number) => void
  duration: number
  onDurationChange: (d: number) => void
}) {
  void _bookingId

  const isVenue = availabilitySource === 'venue'

  const [calMonth, setCalMonth]     = useState(() => {
    const d = preferredDate ? new Date(preferredDate + 'T00:00:00') : new Date()
    d.setDate(1)
    return d
  })
  const [selectedDate, setSelectedDate] = useState<string | null>(preferredDate || null)
  const [calendarConnected, setCalendarConnected] = useState<boolean>(true)
  const [loadingSlots, setLoadingSlots] = useState(false)
  const [slots, setSlots]               = useState<SuggestedSlot[]>([])
  const [blockedSlots, setBlockedSlots] = useState<BlockedSlot[]>([])
  const [selectedSlot, setSelectedSlot] = useState<SuggestedSlot | null>(null)
  const [slotError, setSlotError] = useState<string | null>(null)

  useEffect(() => {
    if (isVenue) return
    if (!artistSlug) {
      const timer = window.setTimeout(() => {
        setCalendarConnected(false)
        setSlots([])
        setBlockedSlots([])
        setSlotError('Artist calendar profile is missing')
      }, 0)
      return () => window.clearTimeout(timer)
    }
    const start = new Date(calMonth)
    const end   = new Date(calMonth)
    end.setMonth(end.getMonth() + 2)
    const startStr = start.toISOString().slice(0, 10)
    const endStr = end.toISOString().slice(0, 10)
    let cancelled = false
    Promise.resolve()
      .then(() => {
        setLoadingSlots(true)
        setSlotError(null)
        return calendarApi.getArtistSlots(artistSlug, {
          start: startStr,
          end: endStr,
          durationMinutes: duration,
          limit: 120,
          includeBlocked: true,
          blockedLimit: 120,
        })
      })
      .then(res => {
        if (cancelled) return
        setSlots(res.slots)
        setBlockedSlots(res.blocked_slots || [])
        setCalendarConnected(res.calendar_connected !== false)
        setSelectedSlot(null)
      })
      .catch(() => {
        if (cancelled) return
        setSlots([])
        setBlockedSlots([])
        setCalendarConnected(false)
        setSlotError('Could not load server availability')
      })
      .finally(() => {
        if (!cancelled) setLoadingSlots(false)
      })
    return () => { cancelled = true }
  }, [calMonth, artistSlug, isVenue, duration])

  // Calendar grid helpers
  const daysInMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() + 1, 0).getDate()
  const firstDow    = new Date(calMonth.getFullYear(), calMonth.getMonth(), 1).getDay()
  const today       = new Date().toISOString().slice(0, 10)

  // Busy day set for calendar shading
  const openDays = useMemo(() => new Set(slots.map((slot) => slot.date)), [slots])
  const blockedDays = useMemo(() => new Set(blockedSlots.map((slot) => slot.date)), [blockedSlots])
  const visibleSlots = useMemo(
    () => (selectedDate ? slots.filter((slot) => slot.date === selectedDate) : slots).slice(0, 12),
    [selectedDate, slots],
  )
  // Venue artists don't use Ryozan's calendar — their scheduling is
  // handled entirely through venue.ink.
  if (isVenue) {
    return (
      <div className="bkm-slot-picker" style={{ textAlign: 'center', padding: '20px 12px' }}>
        <p className="qp-card-note" style={{ margin: 0 }}>
          This artist books through Venue. Use the Slots composer to propose times manually, or manage scheduling in Venue.
        </p>
      </div>
    )
  }

  const preferredDayStr = preferredDate?.slice(0, 10)

  return (
    <div className="bkm-slot-picker">
      {/* Mini calendar */}
      <div className="bkm-slot-cal">
        <div className="bkm-slot-cal-nav">
          <button type="button" onClick={() => setCalMonth(m => { const n = new Date(m); n.setMonth(n.getMonth() - 1); return n })}>‹</button>
          <span>{calMonth.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}</span>
          <button type="button" onClick={() => setCalMonth(m => { const n = new Date(m); n.setMonth(n.getMonth() + 1); return n })}>›</button>
        </div>
        <div className="bkm-slot-cal-grid">
          {['Su','Mo','Tu','We','Th','Fr','Sa'].map(d => (
            <div key={d} className="bkm-slot-cal-dow">{d}</div>
          ))}
          {Array.from({ length: firstDow }).map((_, i) => (
            <div key={`pad-${i}`} />
          ))}
          {Array.from({ length: daysInMonth }).map((_, i) => {
            const day     = i + 1
            const dateStr = `${calMonth.getFullYear()}-${String(calMonth.getMonth() + 1).padStart(2,'0')}-${String(day).padStart(2,'0')}`
            const isPast    = dateStr < today
            const hasOpenSlot = openDays.has(dateStr)
            const hasBlockedSlot = blockedDays.has(dateStr)
            const isSelected = dateStr === selectedDate
            const isPref    = dateStr === preferredDayStr

            return (
              <button
                key={dateStr}
                type="button"
                disabled={isPast || (!loadingSlots && !hasOpenSlot && !hasBlockedSlot)}
                className={[
                  'bkm-slot-cal-day',
                  isPast || (!loadingSlots && !hasOpenSlot && !hasBlockedSlot) ? 'disabled' : '',
                  hasOpenSlot      ? 'busy'     : '',
                  !hasOpenSlot && hasBlockedSlot ? 'blocked' : '',
                  isPref           ? 'preferred': '',
                  isSelected       ? 'selected' : '',
                ].filter(Boolean).join(' ')}
                onClick={() => setSelectedDate(prev => prev === dateStr ? null : dateStr)}
                title={isPref ? 'Client preferred date' : undefined}
              >
                {day}
                {isPref && <span className="bkm-slot-cal-pref-dot" />}
              </button>
            )
          })}
        </div>
      </div>

      {/* Suggested slots */}
      <div className="bkm-slot-suggestions">
        <div className="bkm-edit-label">
          {loadingSlots ? 'Finding openings...' : `${visibleSlots.length || 0} server-verified slot${visibleSlots.length !== 1 ? 's' : ''} · ${duration >= 60 ? `${duration/60}h` : `${duration}m`} session`}
        </div>
        {!calendarConnected && !loadingSlots && (
          <p className="bkm-slot-empty" style={{ color: 'var(--gold)', opacity: 0.85 }}>
            Google Calendar not connected for this artist. Slots below use Ryozan booking rules and local booking conflicts only.
          </p>
        )}
        {slotError && !loadingSlots && (
          <p className="bkm-slot-empty" style={{ color: 'var(--danger)', opacity: 0.9 }}>{slotError}</p>
        )}
        {visibleSlots.length === 0 && !loadingSlots && !slotError && (
          <p className="bkm-slot-empty">please select a different date</p>
        )}
        <div className="bkm-slot-options">
          {visibleSlots.map(slot => (
            <button
              key={`${slot.date}-${slot.time}`}
              type="button"
              className={`bkm-slot-option${selectedSlot?.date === slot.date && selectedSlot?.time === slot.time ? ' selected' : ''}`}
              onClick={() => {
                setSelectedSlot(slot)
                onDurationChange(slot.duration_minutes)
                onSelect(slot.date, slot.time, slot.duration_minutes)
              }}
            >
              <span className="bkm-slot-option-label">{slot.label}</span>
              <span className="bkm-slot-option-dur">{slot.duration_minutes >= 60 ? `${slot.duration_minutes/60}h` : `${slot.duration_minutes}m`}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

/* ── Helpers ─────────────────────────────────── */

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days}d ago`
  return formatDate(iso)
}

function formatEventLabel(type: string): string {
  return type
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function isSparseGoogleImport(b: Booking): boolean {
  return (
    !b.reference_code &&
    b.status === 'confirmed' &&
    (b.artist_notes || '').startsWith('Imported from Google Calendar')
  )
}

function dateKey(date: Date): string {
  return date.toISOString().slice(0, 10)
}

function startOfLocalDay(date: Date): Date {
  const copy = new Date(date)
  copy.setHours(0, 0, 0, 0)
  return copy
}

function bookingScheduleDate(b: Booking): string | null {
  return b.scheduled_date || b.preferred_date
}

function bookingScheduleTime(b: Booking): string {
  return (b.scheduled_time || b.preferred_time || '').slice(0, 5)
}

function bookingDurationLabel(minutes: number | null): string {
  const value = minutes || 120
  return value >= 60 ? `${value / 60}h` : `${value}m`
}

function scheduleSortValue(b: Booking): string {
  return `${bookingScheduleDate(b) || '9999-99-99'}T${bookingScheduleTime(b) || '99:99'}`
}

function hasScheduleOverlap(a: Booking, b: Booking): boolean {
  const aDate = bookingScheduleDate(a)
  const bDate = bookingScheduleDate(b)
  const aTime = bookingScheduleTime(a)
  const bTime = bookingScheduleTime(b)
  if (!aDate || !bDate || aDate !== bDate || !aTime || !bTime) return false
  const aStart = new Date(`${aDate}T${aTime}:00`).getTime()
  const bStart = new Date(`${bDate}T${bTime}:00`).getTime()
  const aEnd = aStart + (a.duration_minutes || 120) * 60000
  const bEnd = bStart + (b.duration_minutes || 120) * 60000
  return aStart < bEnd && bStart < aEnd
}

/* ── Component ──────────────────────────────── */

export default function BookingsManager() {
  const user = useAuthStore((s) => s.user)
  const effectiveRole = useAuthStore((s) => s.effectiveRole)
  const canManage = ['assistant', 'artist', 'admin', 'founder'].includes(effectiveRole)
  const navigate = useNavigate()
  const [openingThreadId, setOpeningThreadId] = useState<string | null>(null)

  // Approval happens in the booking's thread: open it with the chosen time (or
  // none, to message first) and the approval card ready to confirm.
  const openApprovalThread = async (
    booking: Booking,
    slot: { date: string; time: string } | null,
    custom = false,
    duration?: number,
  ) => {
    setOpeningThreadId(booking.id)
    try {
      let convId = booking.conversation_id
      if (!convId) {
        convId = (await bookingsApi.ensureConversation(booking.id)).conversation_id
        setAllBookings((prev) => prev.map((bk) => (bk.id === booking.id ? { ...bk, conversation_id: convId } : bk)))
      }
      navigate(`${ROUTES.COURIER}?convo=${encodeURIComponent(convId)}`, {
        state: {
          conversationId: convId,
          approveBookingId: booking.id,
          approveSlot: slot,
          approveCustom: custom,
          approveDuration: duration ?? null,
        },
      })
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Could not open the booking thread')
      setOpeningThreadId(null)
    }
  }
  const canOverrideSchedule = ['admin', 'founder'].includes(effectiveRole)
  const canReassign = ['admin', 'founder'].includes(effectiveRole)
  const [searchParams, setSearchParams] = useSearchParams()

  const [allBookings, setAllBookings] = useState<Booking[]>([])
  const [bookingDrafts, setBookingDrafts] = useState<BookingDraftRecord[]>([])
  const [pipelineTasks, setPipelineTasks] = useState<CrmPipelineTaskRecord[]>([])
  const [draftsTotal, setDraftsTotal] = useState(0)
  const [draftsError, setDraftsError] = useState<string | null>(null)
  const [pipelineError, setPipelineError] = useState<string | null>(null)
  // Maps artist UUID -> slug and availability_source, so SlotPicker can
  // read the right artist's calendar and adapt its UI per source.
  const [artistSlugById, setArtistSlugById] = useState<Record<string, string>>({})
  const [artistSourceById, setArtistSourceById] = useState<Record<string, string>>({})
  // Full artist roster for the admin act-as selector, venue artists excluded
  // (they schedule in venue.ink, not Ryozan's calendar).
  const [actAsArtists, setActAsArtists] = useState<ArtistSummary[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<BookingCategory>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [updatingId, setUpdatingId] = useState<string | null>(null)

  // Inline editing state (keyed by booking id)
  const [editNotes, setEditNotes] = useState<Record<string, string>>({})
  const [editSchedDate, setEditSchedDate] = useState<Record<string, string>>({})
  const [editSchedTime, setEditSchedTime] = useState<Record<string, string>>({})
  const [editDuration, setEditDuration] = useState<Record<string, string>>({})
  const [overrideConflict, setOverrideConflict] = useState<Record<string, boolean>>({})
  const [overrideBounds, setOverrideBounds] = useState<Record<string, boolean>>({})
  const [overrideReason, setOverrideReason] = useState<Record<string, string>>({})
  const [scheduleSuggestions, setScheduleSuggestions] = useState<Record<string, BookingScheduleSuggestion[]>>({})
  const [scheduleSuggestionLoading, setScheduleSuggestionLoading] = useState<string | null>(null)
  const [editInlineDeposit, setEditInlineDeposit] = useState<Record<string, string>>({})
  const [editInlineTotal, setEditInlineTotal] = useState<Record<string, string>>({})
  const [quoteComposerOpen, setQuoteComposerOpen] = useState<Record<string, boolean>>({})
  const [slotComposerOpen, setSlotComposerOpen] = useState<Record<string, boolean>>({})
  // Whitelisted slots the artist picks for the client (keyed by booking id)
  const [proposedSlots, setProposedSlots] = useState<Record<string, { date: string; time: string; duration_minutes: number; label: string }[]>>({})
  const [slotDuration, setSlotDuration] = useState<Record<string, number>>({})
  const [galaLoading, setGalaLoading] = useState<string | null>(null)

  // Schedule-on-confirm flow
  const [, setSchedulingId] = useState<string | null>(null)
  const [, setSchedDate] = useState('')
  const [, setSchedTime] = useState('')
  const [, setSchedDuration] = useState('60')

  // Loaded quotes per booking
  const [bookingQuotes, setBookingQuotes] = useState<Record<string, QuoteResponse[]>>({})
  const [bookingHistory, setBookingHistory] = useState<Record<string, BookingLifecycleEvent[]>>({})
  const [bookingHistoryError, setBookingHistoryError] = useState<Record<string, string>>({})

  // Lightbox for reference images
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null)

  // Admin/founder act-as proxy booking form
  const canProxyBook = isAdminRole(effectiveRole)
  // Time blocks are self-service for artists and above; the backend
  // (_resolve_block_artist_id) lets an artist block only their own calendar
  // while admins may target any artist. Assistants are excluded (backend
  // requires artist rank).
  const canBlockTime = isArtistPlusRole(effectiveRole)
  const [proxyFormOpen, setProxyFormOpen] = useState(false)
  // Personal time-blocking panel. Mutually exclusive with the proxy form so
  // only one composer occupies the header area at a time.
  const [blockTimeOpen, setBlockTimeOpen] = useState(false)
  const [proxySubmitting, setProxySubmitting] = useState(false)
  const [proxyError, setProxyError] = useState<string | null>(null)
  const [proxyBookyPrompt, setProxyBookyPrompt] = useState('')
  const [proxyBookySearchOpen, setProxyBookySearchOpen] = useState(false)
  const [proxyForm, setProxyForm] = useState({
    artist_id: '',
    guest_name: '',
    guest_email: '',
    guest_phone: '',
    description: '',
    placement: '',
    scheduled_date: '',
    scheduled_time: '',
    duration_minutes: '',
    deposit_amount: '',
    status: 'confirmed',
    confirm_calendar: true,
  })
  // Calendar-tile date/time selection for the proxy form. Drives
  // scheduled_date/scheduled_time off the artist's TRUE availability
  // (Google Calendar + personal time blocks + shop hours) via the same
  // DayPreference component the public booking flow uses, so staff can
  // only pick real openings instead of typing a date that may conflict.
  const [proxyDayPref, setProxyDayPref] = useState<DayPreferenceValue | null>(null)
  // Stable inquiry id so slot holds from this form group/release together.
  const [proxyInquiryId] = useState(() => `staff-${crypto.randomUUID()}`)

  const toast = useToast()

  /* ── Fetch ──────────────────────── */

  const fetchBookings = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      // Scope by role:
      //   assistant/admin/founder → full shop pipeline (?all=true)
      //   artist                  → bookings assigned to them (server default)
      //   collector               → their own bookings (server default)
      // Assistants are intentionally given the shop-wide view because they
      // manage bookings on behalf of every artist in the shop.
      const needsAllView = effectiveRole === 'assistant'
        || effectiveRole === 'admin'
        || effectiveRole === 'founder'
      const bookParams: Parameters<typeof bookingsApi.list>[0] = needsAllView
        ? { limit: 100, all: true }
        : { limit: 100 }
      const data = await bookingsApi.list(bookParams)
      setAllBookings(data.bookings)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load bookings')
    } finally {
      setIsLoading(false)
    }
  }, [effectiveRole])

  // Pre-select the proxy target if it is an eligible artist, else the first.
  useEffect(() => {
    if (proxyForm.artist_id || actAsArtists.length === 0) return
    const proxyId = user?.proxy_for ?? null
    const preferred = actAsArtists.find((a) => a.id === proxyId) ?? actAsArtists[0]
    if (preferred) setProxyForm((f) => ({ ...f, artist_id: preferred.id }))
  }, [actAsArtists, user?.proxy_for, proxyForm.artist_id])

  // The artist the proxy form is currently booking for; gives DayPreference
  // the slug + availability source to query true openings.
  const selectedProxyArtist = useMemo(
    () => actAsArtists.find((a) => a.id === proxyForm.artist_id) ?? null,
    [actAsArtists, proxyForm.artist_id],
  )

  const proxyClientOptions = useMemo<ProxyClientOption[]>(() => {
    const options = new Map<string, ProxyClientOption>()

    for (const draft of bookingDrafts.filter((record) => record.status !== 'submitted')) {
      const form = draft.form_data ?? {}
      const name = firstDefined(draft.guest_name, [form.first_name, form.last_name].filter(Boolean).join(' '))
      const email = firstDefined(draft.guest_email, form.email)
      const phone = firstDefined(draft.guest_phone, form.phone)
      if (!name && !email && !phone) continue
      const key = `inquiry:${draft.id}`
      options.set(key, {
        key,
        source: 'inquiry',
        name: name || email || phone || 'New inquiry',
        email,
        phone,
        artistSlug: draft.artist_slug,
        description: form.description || form.client_notes || '',
        placement: form.placement || '',
        meta: draft.updated_at ? `Inquiry updated ${new Date(draft.updated_at).toLocaleDateString()}` : 'Current inquiry',
      })
    }

    for (const booking of allBookings) {
      const name = firstDefined(booking.resolved_name, booking.client_name, booking.guest_name)
      const email = firstDefined(booking.resolved_email, booking.guest_email)
      const phone = firstDefined(booking.resolved_phone, booking.guest_phone)
      if (!name && !email && !phone) continue
      const key = `rolodex:${(email || phone || name).toLowerCase()}`
      if (options.has(key)) continue
      options.set(key, {
        key,
        source: 'rolodex',
        name: name || email || phone || 'Past client',
        email,
        phone,
        description: booking.description,
        placement: booking.placement,
        meta: booking.artist_name ? `Past booking with ${booking.artist_name}` : 'Past booking',
      })
    }

    return [...options.values()].slice(0, 16)
  }, [bookingDrafts, allBookings])

  const proxyPromptMatches = useMemo(() => {
    const query = `${proxyBookyPrompt} ${proxyForm.guest_name}`.trim().toLowerCase()
    const pool = query ? proxyClientOptions.filter((option) => (
      option.name.toLowerCase().includes(query)
      || option.email.toLowerCase().includes(query)
      || option.phone.toLowerCase().includes(query)
      || query.includes(option.name.toLowerCase().split(' ')[0] || '__no_match__')
    )) : proxyClientOptions

    return pool.slice(0, 3)
  }, [proxyBookyPrompt, proxyClientOptions, proxyForm.guest_name])
  const showProxyBookySearch = proxyBookySearchOpen
    && proxyBookyPrompt.trim().length >= 2
    && proxyPromptMatches.length > 0

  // Keep scheduled_date/scheduled_time in sync with the calendar-tile pick.
  const handleProxyDayPref = useCallback((value: DayPreferenceValue | null) => {
    setProxyDayPref(value)
    setProxyForm((f) => ({
      ...f,
      scheduled_date: value?.date || '',
      scheduled_time: value?.time || '',
    }))
  }, [])

  const clearProxySlot = useCallback(() => {
    setProxyDayPref(null)
    setProxyForm((f) => ({ ...f, scheduled_date: '', scheduled_time: '' }))
  }, [])

  const handleProxyArtistChange = useCallback((artistId: string) => {
    setProxyDayPref(null)
    setProxyForm((f) => ({
      ...f,
      artist_id: artistId,
      scheduled_date: '',
      scheduled_time: '',
    }))
  }, [])

  const applyProxyClient = useCallback((option: ProxyClientOption) => {
    const matchedArtist = option.artistSlug
      ? actAsArtists.find((artist) => artist.slug === option.artistSlug)
      : null

    setProxyForm((f) => ({
      ...f,
      artist_id: matchedArtist?.id || f.artist_id,
      guest_name: option.name || f.guest_name,
      guest_email: option.email || f.guest_email,
      guest_phone: option.phone || f.guest_phone,
      description: f.description || option.description || '',
      placement: f.placement || option.placement || '',
      scheduled_date: matchedArtist ? '' : f.scheduled_date,
      scheduled_time: matchedArtist ? '' : f.scheduled_time,
    }))
    if (matchedArtist) setProxyDayPref(null)
  }, [actAsArtists])

  const applyBookyPrompt = useCallback(() => {
    const prompt = proxyBookyPrompt.trim()
    if (!prompt) return

    const lower = prompt.toLowerCase()
    const promptDigits = lower.replace(/\D/g, '')
    const matchedArtist = actAsArtists.find((artist) => lower.includes(artist.name.toLowerCase()))
      ?? actAsArtists.find((artist) => lower.includes(artist.name.split(' ')[0].toLowerCase()))
    const matchedClient = proxyClientOptions.find((option) => (
      lower.includes(option.name.toLowerCase())
      || lower.includes(option.name.toLowerCase().split(' ')[0] || '__no_match__')
      || (option.email && lower.includes(option.email.toLowerCase()))
      || (option.phone && promptDigits.includes(option.phone.replace(/\D/g, '')))
    ))
    const duration = inferDurationMinutes(prompt)
    const deposit = inferDepositAmount(prompt)
    const placement = inferPlacement(prompt)

    setProxyForm((f) => ({
      ...f,
      artist_id: matchedArtist?.id || f.artist_id,
      guest_name: matchedClient?.name || f.guest_name,
      guest_email: matchedClient?.email || f.guest_email,
      guest_phone: matchedClient?.phone || f.guest_phone,
      description: f.description || matchedClient?.description || prompt,
      placement: placement || f.placement || matchedClient?.placement || '',
      duration_minutes: duration || f.duration_minutes,
      deposit_amount: deposit || f.deposit_amount,
      status: lower.includes('draft') || lower.includes('pending') ? 'pending' : f.status,
      confirm_calendar: lower.includes('no notify') || lower.includes('do not notify') ? false : f.confirm_calendar,
      scheduled_date: matchedArtist ? '' : f.scheduled_date,
      scheduled_time: matchedArtist ? '' : f.scheduled_time,
    }))
    if (matchedArtist) setProxyDayPref(null)
  }, [actAsArtists, proxyBookyPrompt, proxyClientOptions])

  const submitProxyBooking = async () => {
    setProxyError(null)
    if (!proxyForm.artist_id) { setProxyError('Pick an artist.'); return }
    if (!proxyForm.description.trim()) { setProxyError('Description is required.'); return }
    if (!proxyForm.guest_name.trim() || !(proxyForm.guest_email.trim() || proxyForm.guest_phone.trim())) {
      setProxyError('Provide a client name and an email or phone.'); return
    }
    setProxySubmitting(true)
    try {
      await bookingsApi.staffCreate({
        artist_id: proxyForm.artist_id,
        guest_name: proxyForm.guest_name.trim(),
        guest_email: proxyForm.guest_email.trim() || undefined,
        guest_phone: proxyForm.guest_phone.trim() || undefined,
        description: proxyForm.description.trim(),
        placement: proxyForm.placement.trim() || undefined,
        scheduled_date: proxyForm.scheduled_date || undefined,
        scheduled_time: proxyForm.scheduled_time || undefined,
        duration_minutes: proxyForm.duration_minutes ? Number(proxyForm.duration_minutes) : undefined,
        deposit_amount: proxyForm.deposit_amount ? Number(proxyForm.deposit_amount) : undefined,
        status: proxyForm.status,
        confirm_calendar: proxyForm.confirm_calendar,
      })
      setProxyFormOpen(false)
      setProxyForm((f) => ({
        ...f,
        guest_name: '', guest_email: '', guest_phone: '', description: '',
        placement: '', scheduled_date: '', scheduled_time: '',
        duration_minutes: '', deposit_amount: '',
      }))
      setProxyDayPref(null)
      toast.success('Booking created.')
      await fetchBookings()
    } catch (e) {
      setProxyError(e instanceof Error ? e.message : 'Failed to create booking.')
    } finally {
      setProxySubmitting(false)
    }
  }

  const fetchBookingDrafts = useCallback(async () => {
    if (!canManage) return
    setDraftsError(null)
    try {
      const data = await bookingsApi.listDrafts({ limit: 25 })
      setBookingDrafts(data.drafts)
      setDraftsTotal(data.total)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load recovery drafts'
      if (message.includes('valid UUID') || message.includes('Not Found')) {
        setBookingDrafts([])
        setDraftsTotal(0)
        return
      }
      setDraftsError(message)
    }
  }, [canManage])

  const fetchPipelineTasks = useCallback(async () => {
    if (!canManage) return
    setPipelineError(null)
    try {
      const data = await crmApi.listPipelineTasks({ status: 'open', limit: 25 })
      setPipelineTasks(data.tasks)
    } catch (err: unknown) {
      setPipelineError(err instanceof Error ? err.message : 'Failed to load CRM queue')
    }
  }, [canManage])

  useEffect(() => { fetchBookings() }, [fetchBookings])
  useEffect(() => { fetchBookingDrafts() }, [fetchBookingDrafts])
  useEffect(() => { fetchPipelineTasks() }, [fetchPipelineTasks])

  // Build a UUID -> slug lookup so SlotPicker can fetch the *assigned*
  // artist's calendar instead of the current user's.
  useEffect(() => {
    let cancelled = false
    artistsApi.list()
      .then((list) => {
        if (cancelled) return
        const slugMap: Record<string, string> = {}
        const sourceMap: Record<string, string> = {}
        for (const a of list) {
          if (a.id && a.slug) slugMap[a.id] = a.slug
          if (a.id) sourceMap[a.id] = a.availability_source || 'none'
        }
        const eligible = list.filter((a) => a.availability_source !== 'venue')
        setActAsArtists(eligible)
        setArtistSlugById(slugMap)
        setArtistSourceById(sourceMap)
      })
      .catch(() => {
        /* non-fatal — SlotPicker falls back to work-schedule defaults */
      })
    return () => { cancelled = true }
  }, [])

  /* ── Filter & search ────────────── */

  const visibleBookingBase = useMemo(
    () => allBookings.filter((b) => !isSparseGoogleImport(b)),
    [allBookings],
  )

  const filtered = useMemo(() => {
    let result = visibleBookingBase

    // Category filter
    if (activeTab !== 'all') {
      const statuses = CATEGORY_STATUSES[activeTab]
      result = result.filter((b) => statuses.includes(b.status))
    }

    // Search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      result = result.filter((b) =>
        (b.client_name || '').toLowerCase().includes(q) ||
        (b.artist_name || '').toLowerCase().includes(q) ||
        (b.description || '').toLowerCase().includes(q) ||
        (b.style_preference || '').toLowerCase().includes(q) ||
        (b.placement || '').toLowerCase().includes(q)
      )
    }

    return result
  }, [visibleBookingBase, activeTab, searchQuery])

  /* ── Status counts ──────────────── */

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: visibleBookingBase.length }
    for (const [cat, statuses] of Object.entries(CATEGORY_STATUSES)) {
      if (cat === 'all') continue
      c[cat] = visibleBookingBase.filter((b) => statuses.includes(b.status)).length
    }
    return c
  }, [visibleBookingBase])

  const scheduleBoard = useMemo(() => {
    const today = startOfLocalDay(new Date())
    const days = Array.from({ length: 7 }, (_, index) => {
      const day = new Date(today)
      day.setDate(today.getDate() + index)
      const key = dateKey(day)
      const items = visibleBookingBase
        .filter((b) => bookingScheduleDate(b) === key && !['cancelled', 'no_show', 'expired'].includes(b.status))
        .sort((a, b) => scheduleSortValue(a).localeCompare(scheduleSortValue(b)))
      const conflicts = items.filter((item, itemIndex) =>
        items.some((candidate, candidateIndex) => candidateIndex !== itemIndex && hasScheduleOverlap(item, candidate)),
      )
      const totalMinutes = items.reduce((sum, item) => sum + (item.duration_minutes || 120), 0)
      return { key, day, items, conflicts, totalMinutes }
    })
    const unscheduled = visibleBookingBase.filter((b) =>
      ['pending', 'quoted'].includes(b.status) && !b.scheduled_date && !b.preferred_date,
    )
    const conflictCount = days.reduce((sum, day) => sum + day.conflicts.length, 0)
    const bookedCount = days.reduce((sum, day) => sum + day.items.filter((item) => item.status === 'confirmed').length, 0)
    return { days, unscheduled, conflictCount, bookedCount }
  }, [visibleBookingBase])

  const recoveryDrafts = useMemo(
    () => bookingDrafts.filter((draft) => draft.status !== 'submitted'),
    [bookingDrafts],
  )

  const openPipelineTasks = useMemo(
    () => pipelineTasks.filter((task) => task.status === 'open'),
    [pipelineTasks],
  )

  const completePipelineTaskForBooking = useCallback(async (bookingId: string) => {
    const task = pipelineTasks.find((item) => item.booking_id === bookingId && item.status === 'open')
    if (!task) return
    try {
      const updated = await crmApi.setPipelineTaskStatus(task.id, 'done')
      setPipelineTasks((prev) => prev.map((item) => item.id === task.id ? updated : item))
    } catch {
      /* task completion is supplemental */
    }
  }, [pipelineTasks])

  const openTaskBooking = async (task: CrmPipelineTaskRecord) => {
    if (!task.booking_id) return
    setSearchQuery('')
    const cached = allBookings.find((item) => item.id === task.booking_id)
    if (cached) {
      setActiveTab(categoryForStatus(cached.status))
      setExpandedId(task.booking_id)
      return
    }
    // Not in local cache (e.g. artist clicked task for booking assigned to another
    // artist, or list was paginated and this booking fell outside). Fetch on demand
    // and inject into allBookings so the card can render + expand.
    try {
      const fresh = await bookingsApi.get(task.booking_id)
      setAllBookings((prev) => {
        const exists = prev.some((b) => b.id === fresh.id)
        return exists ? prev.map((b) => (b.id === fresh.id ? fresh : b)) : [fresh, ...prev]
      })
      setActiveTab(categoryForStatus(fresh.status))
      setExpandedId(task.booking_id)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to open booking'
      toast.error(msg)
    }
  }

  const openScheduleBooking = (booking: Booking) => {
    setSearchQuery('')
    setActiveTab(categoryForStatus(booking.status))
    setExpandedId(booking.id)
    loadQuotes(booking.id)
    loadHistory(booking.id)
  }

  /* ── Status update ──────────────── */

  const handleStatusUpdate = async (bookingId: string, newStatus: string) => {
    if (newStatus === 'quote') {
      const booking = allBookings.find((b) => b.id === bookingId)
      if (!booking) return

      const message = (editNotes[bookingId] ?? '').trim()
      const depositRaw = editInlineDeposit[bookingId] ?? ''
      const totalRaw = editInlineTotal[bookingId] ?? ''
      const depositNum = depositRaw ? Number(depositRaw) : 0
      const totalNum = totalRaw ? Number(totalRaw) : undefined
      const composerActive = quoteComposerOpen[bookingId]

      // ── New path: quote_proposal artifact message ──
      // Fires when the $ Quote composer panel is open AND has pricing.
      // Sends a text message with an attached quote_proposal artifact
      // in the booking's conversation thread.
      if (composerActive && (depositNum > 0 || totalNum)) {
        if (!message) {
          toast.error('Add a message to go with your quote proposal')
          return
        }
        setUpdatingId(bookingId)
        try {
          // Ensure the booking has a conversation thread (creates one
          // on demand for guest bookings that lack a client account).
          let convId = booking.conversation_id
          if (!convId) {
            const res = await bookingsApi.ensureConversation(bookingId)
            convId = res.conversation_id
          }

          // Send the message with the quote_proposal artifact attached.
          await conversationsApi.sendMessage(
            convId,
            message,
            'quote_proposal',
            undefined,
            {
              booking_id: bookingId,
              deposit_amount: depositNum,
              estimated_total_max: totalNum ?? null,
              expires_in_days: 7,
            },
          )

          // Update local booking state
          setAllBookings((prev) =>
            prev.map((b) =>
              b.id === bookingId
                ? {
                    ...b,
                    status: 'quoted',
                    conversation_id: convId,
                    deposit_amount: depositNum || null,
                    estimated_total: totalNum ?? b.estimated_total,
                  }
                : b
            )
          )

          // Close the composer + clear fields
          setQuoteComposerOpen(p => ({ ...p, [bookingId]: false }))
          setEditInlineDeposit(p => { const n = { ...p }; delete n[bookingId]; return n })
          setEditInlineTotal(p => { const n = { ...p }; delete n[bookingId]; return n })
          setEditNotes(p => { const n = { ...p }; delete n[bookingId]; return n })
          await completePipelineTaskForBooking(bookingId)

          toast.success('Quote proposal sent in conversation!')
        } catch (err: unknown) {
          toast.error(err instanceof Error ? err.message : 'Failed to send quote proposal')
        } finally {
          setUpdatingId(null)
        }
        return
      }

      // ── Slot proposal path ──
      // Fires when the 📅 Slot composer has whitelisted slots.
      const slotsReady = proposedSlots[bookingId]?.length > 0 && slotComposerOpen[bookingId]
      if (slotsReady) {
        if (!message) {
          toast.error('Add a message to go with your slot proposal')
          return
        }
        setUpdatingId(bookingId)
        try {
          let convId = booking.conversation_id
          if (!convId) {
            const res = await bookingsApi.ensureConversation(bookingId)
            convId = res.conversation_id
          }
          await conversationsApi.sendMessage(
            convId,
            message,
            'slot_proposal',
            undefined,
            {
              booking_id: bookingId,
              slots: proposedSlots[bookingId],
            },
          )
          setAllBookings(prev =>
            prev.map(bk => bk.id === bookingId ? { ...bk, conversation_id: convId } : bk)
          )
          setSlotComposerOpen(p => ({ ...p, [bookingId]: false }))
          setProposedSlots(p => { const n = { ...p }; delete n[bookingId]; return n })
          setEditNotes(p => { const n = { ...p }; delete n[bookingId]; return n })
          await completePipelineTaskForBooking(bookingId)
          toast.success('Slot proposal sent! Client will pick a time.')
        } catch (err: unknown) {
          toast.error(err instanceof Error ? err.message : 'Failed to send slot proposal')
        } finally {
          setUpdatingId(null)
        }
        return
      }

      // ── Legacy path: uses the /quotes/ endpoint ──
      // Fires when neither composer is active or has content.
      // Sends via the legacy create-quote flow which fires an email +
      // creates a bttco_quotes row. Kept for backward compatibility
      // until the client-side quote page is migrated to artifact cards.
      const date = editSchedDate[bookingId] ?? booking.scheduled_date ?? ''
      const time = editSchedTime[bookingId] ?? booking.scheduled_time ?? ''
      const dur = editDuration[bookingId] ?? booking.duration_minutes ?? 60
      const hasSlot = Boolean(date && time)

      if (!hasSlot && !depositNum && !totalNum && !message) {
        toast.error('Add a message, a slot, or pricing before sending')
        return
      }

      setUpdatingId(bookingId)
      try {
        const payload: QuoteCreate = {
          booking_id: bookingId,
          deposit_amount: depositNum,
          available_slots: hasSlot
            ? [{ date, time, duration_minutes: Number(dur) || 60 }]
            : [],
          estimated_total_max: totalNum,
          artist_message: message || undefined,
          expires_in_days: 7,
        }
        const quote = await quotesApi.create(payload)
        setAllBookings((prev) =>
          prev.map((b) =>
            b.id === bookingId
              ? {
                  ...b,
                  status: 'quoted',
                  deposit_amount: depositNum || null,
                  estimated_total: totalNum ?? b.estimated_total,
                }
              : b
          )
        )
        setBookingQuotes((prev) => ({ ...prev, [bookingId]: [quote, ...(prev[bookingId] || [])] }))
        await completePipelineTaskForBooking(bookingId)
        toast.success('Sent! Client will receive an email.')
      } catch (err: unknown) {
        toast.error(err instanceof Error ? err.message : 'Failed to send')
      } finally {
        setUpdatingId(null)
      }
      return
    }
    // If confirming, open scheduling flow instead of immediate update
    if (newStatus === 'confirmed') {
      setSchedulingId(bookingId)
      const booking = allBookings.find((b) => b.id === bookingId)
      setSchedDate(booking?.scheduled_date || booking?.preferred_date || '')
      setSchedTime(booking?.scheduled_time || booking?.preferred_time || '')
      setSchedDuration(String(booking?.duration_minutes || 60))
      return
    }
    setUpdatingId(bookingId)
    try {
      const updated = newStatus === 'cancelled'
        ? await bookingsApi.cancel(bookingId, {
            reason: 'Cancelled from Huddl',
            notify_client: false,
            delete_calendar_event: true,
          })
        : await bookingsApi.update(bookingId, { status: newStatus })
      setAllBookings((prev) => prev.map((b) => (b.id === bookingId ? updated : b)))
      if (newStatus !== 'pending') await completePipelineTaskForBooking(bookingId)
      toast.success(`Booking ${newStatus}`)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update booking')
    } finally {
      setUpdatingId(null)
    }
  }

  const handleRescheduleBooking = async (
    booking: Booking,
    options: { notifyClient?: boolean; reason?: string } = {},
  ) => {
    const date = editSchedDate[booking.id] || booking.scheduled_date || booking.preferred_date || ''
    const time = editSchedTime[booking.id] || booking.scheduled_time || booking.preferred_time || ''
    const duration = Number(editDuration[booking.id] || booking.duration_minutes || 120)

    if (!date || !time) {
      toast.error('Choose a date and time before saving')
      return
    }

    setUpdatingId(booking.id)
    try {
      const allowConflict = canOverrideSchedule && Boolean(overrideConflict[booking.id])
      const allowOutOfBounds = canOverrideSchedule && Boolean(overrideBounds[booking.id])
      const cleanOverrideReason = (overrideReason[booking.id] || '').trim()
      if ((allowConflict || allowOutOfBounds) && !cleanOverrideReason) {
        toast.error('Add an override reason before saving')
        setUpdatingId(null)
        return
      }
      const updated = await bookingsApi.reschedule(booking.id, {
        scheduled_date: date,
        scheduled_time: time,
        duration_minutes: Number.isFinite(duration) ? duration : 120,
        reason: options.reason || 'Updated from Huddl',
        notify_client: options.notifyClient ?? false,
        allow_conflict: allowConflict || undefined,
        allow_out_of_bounds: allowOutOfBounds || undefined,
        override_reason: cleanOverrideReason || undefined,
      })
      setAllBookings((prev) => prev.map((b) => (b.id === booking.id ? updated : b)))
      setEditSchedDate((prev) => { const next = { ...prev }; delete next[booking.id]; return next })
      setEditSchedTime((prev) => { const next = { ...prev }; delete next[booking.id]; return next })
      setEditDuration((prev) => { const next = { ...prev }; delete next[booking.id]; return next })
      setOverrideConflict((prev) => { const next = { ...prev }; delete next[booking.id]; return next })
      setOverrideBounds((prev) => { const next = { ...prev }; delete next[booking.id]; return next })
      setOverrideReason((prev) => { const next = { ...prev }; delete next[booking.id]; return next })
      await completePipelineTaskForBooking(booking.id)
      toast.success(options.notifyClient ? 'Appointment confirmed and collector notified' : 'Booking time saved')
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to save booking time')
    } finally {
      setUpdatingId(null)
    }
  }

  const handleCancelBooking = async (booking: Booking) => {
    setUpdatingId(booking.id)
    try {
      const updated = await bookingsApi.cancel(booking.id, {
        reason: 'Cancelled from Huddl',
        notify_client: false,
        delete_calendar_event: true,
      })
      setAllBookings((prev) => prev.map((b) => (b.id === booking.id ? updated : b)))
      await completePipelineTaskForBooking(booking.id)
      toast.success('Booking cancelled')
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to cancel booking')
    } finally {
      setUpdatingId(null)
    }
  }

  const handleReassignBooking = async (booking: Booking, newArtistId: string) => {
    if (!newArtistId || newArtistId === booking.artist_id) return
    const targetArtist = actAsArtists.find((a) => a.id === newArtistId)
    const targetName = targetArtist?.name || 'the selected artist'

    const hasHold =
      booking.deposit_authorization_status === 'authorized' ||
      booking.deposit_authorization_status === 'pending_checkout'
    const message = hasHold
      ? `Reassign this booking to ${targetName}?\n\nThis will release the existing deposit hold and notify the collector that a new authorization may be required.`
      : `Reassign this booking to ${targetName}?`

    if (!confirm(message)) return

    setUpdatingId(booking.id)
    try {
      const updated = await bookingsApi.update(booking.id, {
        artist_id: newArtistId,
        notify_client: true,
      })
      setAllBookings((prev) =>
        prev.map((b) => (b.id === booking.id ? { ...b, ...updated, artist_id: newArtistId, artist_name: targetName } : b))
      )
      toast.success(`Booking reassigned to ${targetName}`)
      try {
        const history = await bookingsApi.history(booking.id)
        setBookingHistory((prev) => ({ ...prev, [booking.id]: history }))
      } catch {
        // history refresh is best-effort
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to reassign booking')
    } finally {
      setUpdatingId(null)
    }
  }

  const loadScheduleSuggestions = async (booking: Booking) => {
    setScheduleSuggestionLoading(booking.id)
    try {
      const response = await bookingsApi.scheduleSuggestions(booking.id, { days: 60, limit: 5 })
      setScheduleSuggestions((prev) => ({ ...prev, [booking.id]: response.suggestions }))
      if (response.suggestions.length === 0) {
        toast.error('No verified openings found')
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to load schedule suggestions')
    } finally {
      setScheduleSuggestionLoading(null)
    }
  }

  // Deep-link from email: ?ref=BT-XXXX&action=accept|reject
  useEffect(() => {
    const ref = searchParams.get('ref')
    const action = searchParams.get('action')
    if (!ref || isLoading) return

    const booking = allBookings.find((b) => b.reference_code === ref)
    if (!booking) {
      // Booking not in local cache. Fall through silently for now; the on-demand
      // fetch happens via openTaskBooking when triggered from the CRM card. For
      // email deep-links we can't search-by-ref via the API yet (only by id), so
      // logging is the best signal. TODO: add bookings.getByRef API for this path.
      if (allBookings.length > 0) {
        console.warn(`[BookingsManager] Deep-link ?ref=${ref} did not match any cached booking. ` +
          `User may not have access OR list was paginated.`)
      }
      return
    }

    // Auto-expand the booking
    setSearchQuery('')
    setExpandedId(booking.id)
    setActiveTab(action ? 'all' : categoryForStatus(booking.status))

    // Auto-trigger action if provided
    const nextStatus = action === 'accept' ? 'quoted' : action === 'reject' ? 'cancelled' : null
    const nextTab = nextStatus === 'quoted' ? 'in_progress' : nextStatus === 'cancelled' ? 'past' : null
    if (nextStatus && booking.status === 'pending' && canManage) {
      setUpdatingId(booking.id)
      const request = nextStatus === 'cancelled'
        ? bookingsApi.cancel(booking.id, {
            reason: 'Rejected from Huddl deep link',
            notify_client: false,
            delete_calendar_event: true,
          })
        : bookingsApi.update(booking.id, { status: nextStatus })
      request
        .then((updated) => {
          setAllBookings((prev) => prev.map((b) => (b.id === booking.id ? updated : b)))
          if (nextTab) setActiveTab(nextTab)
          completePipelineTaskForBooking(booking.id)
          toast.success(`Booking ${nextStatus}`)
        })
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : 'Failed to update booking')
        })
        .finally(() => setUpdatingId(null))
    }

    // Clear action params so it doesn't re-trigger on refresh, but keep charm=bookings.
    const nextParams = new URLSearchParams(searchParams)
    nextParams.delete('ref')
    nextParams.delete('action')
    nextParams.delete('focus')
    setSearchParams(nextParams, { replace: true })
  }, [allBookings, isLoading, searchParams, canManage, setSearchParams, toast, completePipelineTaskForBooking])

  /* ── Load quotes for expanded booking ── */

  const loadQuotes = useCallback(async (bookingId: string) => {
    if (bookingQuotes[bookingId]) return // already loaded
    try {
      const quotes = await quotesApi.forBooking(bookingId)
      setBookingQuotes((prev) => ({ ...prev, [bookingId]: quotes }))
    } catch {
      // silent, quotes are supplementary info
    }
  }, [bookingQuotes])

  const loadHistory = useCallback(async (bookingId: string) => {
    if (bookingHistory[bookingId]) return
    try {
      setBookingHistoryError((prev) => {
        const next = { ...prev }
        delete next[bookingId]
        return next
      })
      const history = await bookingsApi.history(bookingId)
      setBookingHistory((prev) => ({ ...prev, [bookingId]: history }))
    } catch (err: unknown) {
      setBookingHistoryError((prev) => ({
        ...prev,
        [bookingId]: err instanceof Error ? err.message : 'Failed to load booking history',
      }))
    }
  }, [bookingHistory])

  /* ── Inline field save ──────────── */

  /* ── Toggle expand ──────────────── */

  const toggleExpand = (id: string) => {
    const opening = expandedId !== id
    setExpandedId((prev) => (prev === id ? null : id))
    if (opening) {
      loadQuotes(id)
      loadHistory(id)
    }
  }

  /* ── Render ─────────────────────── */

  return (
    <div className="dashboard-panel bkm-panel">
      {/* Header */}
      <header className="bkm-header">
        <div className="bkm-title-row">
          <div>
            <span className="bkm-eyebrow">Huddl queue</span>
            <h1 className="panel-title">Bookings</h1>
          </div>
        </div>

        <div className="bkm-command-row">
          <div className="bkm-search">
            <span className="bkm-search-icon">{Icons.search}</span>
            <input
              type="text"
              className="bkm-search-input"
              placeholder="Search client, artist, style, placement..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <div className="bkm-tabs" aria-label="Booking status filters">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.key}
                className={`bkm-tab ${activeTab === tab.key ? 'active' : ''}`}
                onClick={() => setActiveTab(tab.key)}
              >
                <span>{tab.label}</span>
                {counts[tab.key] !== undefined && (
                  <span className="bkm-tab-count">{counts[tab.key] || 0}</span>
                )}
              </button>
            ))}
          </div>

          {!proxyFormOpen && !blockTimeOpen && (
            <>
              {canProxyBook && (
                <button
                  type="button"
                  className="bkm-add-booking-btn"
                  onClick={() => { setBlockTimeOpen(false); setProxyFormOpen(true) }}
                >
                  + Add booking
                </button>
              )}
              {canBlockTime && (
                <button
                  type="button"
                  className="bkm-block-time-btn"
                  onClick={() => { setProxyFormOpen(false); setBlockTimeOpen(true) }}
                >
                  Block time
                </button>
              )}
            </>
          )}
        </div>
      </header>

      {canBlockTime && blockTimeOpen && (
        <Suspense fallback={<div className="bkm-proxy-form"><p className="bkm-proxy-empty">Loading time blocks...</p></div>}>
          <TimeBlockManager
            artists={actAsArtists}
            canTargetOthers={canProxyBook}
            onClose={() => setBlockTimeOpen(false)}
          />
        </Suspense>
      )}

      {canProxyBook && proxyFormOpen && (
        <div className="bkm-proxy-form">
          {actAsArtists.length === 0 ? (
            <p className="bkm-proxy-empty">
              No Ryozan-scheduling artists available; venue artists book in venue.ink.
            </p>
          ) : (
            <>
              <div className="bkm-proxy-head">
                <div>
                  <h3>Booking composer</h3>
                  <p>Booky can draft the setup. Staff reviews the client, artist, slot, and notification before saving.</p>
                </div>
                <button type="button" onClick={() => setProxyFormOpen(false)}>Close</button>
              </div>

              <div className="bkm-booky-bar">
                <div className="bkm-booky-command">
                  <span>Booky</span>
                  <input
                    value={proxyBookyPrompt}
                    onChange={(e) => setProxyBookyPrompt(e.target.value)}
                    onFocus={() => setProxyBookySearchOpen(true)}
                    onBlur={() => window.setTimeout(() => setProxyBookySearchOpen(false), 120)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        applyBookyPrompt()
                      }
                    }}
                    placeholder="Book Erin with Alex, 2h forearm, $100 deposit"
                  />
                  <button type="button" onClick={applyBookyPrompt}>Fill</button>
                </div>
                {showProxyBookySearch && (
                  <div className="bkm-booky-sources" aria-label="Booky source suggestions">
                    {proxyPromptMatches.map((option) => (
                    <button
                      type="button"
                      key={option.key}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => {
                        applyProxyClient(option)
                        setProxyBookySearchOpen(false)
                      }}
                    >
                      <strong>{option.name}</strong>
                      <small>{option.source === 'inquiry' ? 'Inquiry' : 'Rolodex'}</small>
                    </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="bkm-proxy-body">
                <div className="bkm-proxy-grid">
                  <section className="bkm-proxy-card bkm-proxy-card-client">
                    <div className="bkm-proxy-card-head">
                      <span>Client</span>
                      <small>{proxyForm.guest_email || proxyForm.guest_phone ? 'Ready' : 'Needs contact'}</small>
                    </div>
                    <div className="bkm-proxy-client-row">
                      <label className="bkm-proxy-field"><span>Name</span>
                        <input value={proxyForm.guest_name} onChange={(e) => setProxyForm((f) => ({ ...f, guest_name: e.target.value }))} /></label>
                      <label className="bkm-proxy-field"><span>Email</span>
                        <input type="email" value={proxyForm.guest_email} onChange={(e) => setProxyForm((f) => ({ ...f, guest_email: e.target.value }))} /></label>
                      <label className="bkm-proxy-field"><span>Phone</span>
                        <input value={proxyForm.guest_phone} onChange={(e) => setProxyForm((f) => ({ ...f, guest_phone: e.target.value }))} /></label>
                    </div>
                  </section>

                  <section className="bkm-proxy-card">
                    <div className="bkm-proxy-card-head">
                      <span>Artist</span>
                      <small>{selectedProxyArtist?.availability_source === 'google_calendar' ? 'Calendar linked' : 'Shop hours fallback'}</small>
                    </div>
                    <div className="bkm-artist-picker">
                      {actAsArtists.map((artist) => (
                        <button
                          type="button"
                          key={artist.id}
                          className={proxyForm.artist_id === artist.id ? 'is-active' : ''}
                          onClick={() => handleProxyArtistChange(artist.id)}
                        >
                          <strong>{artist.name}</strong>
                          <small>{artist.availability_source === 'venue' ? 'Venue managed' : 'Ryozan calendar'}</small>
                        </button>
                      ))}
                    </div>
                  </section>

                  <section className="bkm-proxy-card bkm-proxy-card-details">
                    <div className="bkm-proxy-card-head">
                      <span>Tattoo details</span>
                      <small>{proxyForm.description ? 'Drafted' : 'Required'}</small>
                    </div>
                    <div className="bkm-placement-chips" aria-label="Quick placement options">
                      {['Forearm', 'Upper arm', 'Thigh', 'Calf', 'Chest', 'Back'].map((placement) => (
                        <button
                          type="button"
                          key={placement}
                          className={proxyForm.placement.toLowerCase() === placement.toLowerCase() ? 'is-active' : ''}
                          onClick={() => setProxyForm((f) => ({ ...f, placement }))}
                        >
                          {placement}
                        </button>
                      ))}
                    </div>
                    <label className="bkm-proxy-field"><span>Placement</span>
                      <input value={proxyForm.placement} onChange={(e) => setProxyForm((f) => ({ ...f, placement: e.target.value }))} /></label>
                    <label className="bkm-proxy-field"><span>Description</span>
                      <textarea value={proxyForm.description} onChange={(e) => setProxyForm((f) => ({ ...f, description: e.target.value }))} /></label>
                  </section>

                  <section className="bkm-proxy-card">
                    <div className="bkm-proxy-card-head">
                      <span>Session</span>
                      <small>{proxyForm.status === 'confirmed' ? 'Confirmed' : 'Pending'}</small>
                    </div>
                    <div className="bkm-duration-chips" aria-label="Duration presets">
                      {[
                        ['60', '1h'],
                        ['120', '2h'],
                        ['180', '3h'],
                        ['240', 'Half day'],
                        ['420', 'Full day'],
                      ].map(([value, label]) => (
                        <button
                          type="button"
                          key={value}
                          className={proxyForm.duration_minutes === value ? 'is-active' : ''}
                          onClick={() => {
                            setProxyForm((f) => ({ ...f, duration_minutes: value }))
                            clearProxySlot()
                          }}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <div className="bkm-proxy-inline-fields">
                      <label className="bkm-proxy-field"><span>Minutes</span>
                        <input
                          type="number"
                          value={proxyForm.duration_minutes}
                          onChange={(e) => {
                            setProxyForm((f) => ({ ...f, duration_minutes: e.target.value }))
                            clearProxySlot()
                          }}
                        /></label>
                      <label className="bkm-proxy-field"><span>Deposit</span>
                        <input type="number" value={proxyForm.deposit_amount} onChange={(e) => setProxyForm((f) => ({ ...f, deposit_amount: e.target.value }))} /></label>
                    </div>
                    <div className="bkm-status-segments" aria-label="Booking status">
                      {[
                        ['pending', 'Pending'],
                        ['confirmed', 'Confirmed'],
                      ].map(([value, label]) => (
                        <button
                          type="button"
                          key={value}
                          className={proxyForm.status === value ? 'is-active' : ''}
                          onClick={() => setProxyForm((f) => ({ ...f, status: value }))}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </section>
                </div>
                {/* Date + time off the artist's TRUE availability (calendar +
                    time blocks + shop hours), reusing the public-flow calendar
                    tile. Staff can only pick real openings; the chosen slot is
                    held so it cannot be double-booked mid-entry. Venue-managed
                    artists never reach this form (excluded from actAsArtists). */}
                <div className="bkm-proxy-field bkm-proxy-field-full bkm-proxy-calendar">
                  <div className="bkm-proxy-card-head">
                    <span>Calendar and time</span>
                    <small>{proxyForm.scheduled_date && proxyForm.scheduled_time ? `${proxyForm.scheduled_date} ${proxyForm.scheduled_time}` : 'Pick from openings'}</small>
                  </div>
                  {selectedProxyArtist?.slug ? (
                    <DayPreference
                      artistSlug={selectedProxyArtist.slug}
                      availabilitySource={selectedProxyArtist.availability_source}
                      selectedSize={null}
                      selectedPlacement={proxyForm.placement || null}
                      selectedDurationMinutes={proxyForm.duration_minutes ? Number(proxyForm.duration_minutes) : null}
                      inquiryId={proxyInquiryId}
                      guestEmail={proxyForm.guest_email}
                      value={proxyDayPref}
                      onChange={handleProxyDayPref}
                    />
                  ) : (
                    <p className="bkm-proxy-empty">Pick an artist to load their open times.</p>
                  )}
                </div>
              </div>
              <div className="bkm-proxy-actions">
                <label className="bkm-proxy-checkbox">
                  <input type="checkbox" checked={proxyForm.confirm_calendar}
                    onChange={(e) => setProxyForm((f) => ({ ...f, confirm_calendar: e.target.checked }))} />
                  <span>Post to calendar and notify client when confirmed</span>
                </label>
                <div className="bkm-proxy-submit-group">
                  {proxyError && <p className="bkm-proxy-error">{proxyError}</p>}
                  <button type="button" className="bkm-proxy-submit" disabled={proxySubmitting} onClick={submitProxyBooking}>
                    {proxySubmitting ? 'Creating...' : 'Create booking'}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {!isLoading && canManage && (
        <section className="bkm-schedule-board" aria-label="Huddl schedule board">
          <div className="bkm-schedule-board-head">
            <div>
              <p className="bkm-recovery-kicker">Schedule board</p>
              <h2>Next 7 days</h2>
            </div>
            <div className="bkm-schedule-board-stats">
              <span>{scheduleBoard.bookedCount} booked</span>
              <span>{scheduleBoard.unscheduled.length} unscheduled</span>
              {scheduleBoard.conflictCount > 0 && <span className="bkm-schedule-alert">{scheduleBoard.conflictCount} conflict flags</span>}
            </div>
          </div>

          <div className="bkm-schedule-days">
            {scheduleBoard.days.map((day) => (
              <div key={day.key} className={`bkm-schedule-day${day.conflicts.length > 0 ? ' has-conflict' : ''}`}>
                <div className="bkm-schedule-day-head">
                  <span>{day.day.toLocaleDateString('en-US', { weekday: 'short' })}</span>
                  <strong>{day.day.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</strong>
                </div>
                <div className="bkm-schedule-load">
                  <span>{day.items.length} session{day.items.length === 1 ? '' : 's'}</span>
                  {day.totalMinutes > 0 && <span>{bookingDurationLabel(day.totalMinutes)}</span>}
                </div>
                <div className="bkm-schedule-day-list">
                  {day.items.length === 0 && <span className="bkm-schedule-empty">Open</span>}
                  {day.items.slice(0, 4).map((booking) => {
                    const conflict = day.conflicts.some((item) => item.id === booking.id)
                    return (
                      <button
                        key={booking.id}
                        type="button"
                        className={`bkm-schedule-pill status-${booking.status}${conflict ? ' conflict' : ''}`}
                        onClick={() => openScheduleBooking(booking)}
                        title={conflict ? 'Overlaps another scheduled booking' : undefined}
                      >
                        <span>{bookingScheduleTime(booking) || 'Time TBD'}</span>
                        <strong>{booking.resolved_name || booking.client_name || booking.guest_name || 'Guest Client'}</strong>
                      </button>
                    )
                  })}
                  {day.items.length > 4 && <span className="bkm-schedule-more">+{day.items.length - 4} more</span>}
                </div>
              </div>
            ))}
          </div>

          {scheduleBoard.unscheduled.length > 0 && (
            <div className="bkm-unscheduled-strip">
              <span>Needs time</span>
              {scheduleBoard.unscheduled.slice(0, 6).map((booking) => (
                <button key={booking.id} type="button" onClick={() => openScheduleBooking(booking)}>
                  {booking.resolved_name || booking.client_name || booking.guest_name || 'Guest Client'}
                </button>
              ))}
              {scheduleBoard.unscheduled.length > 6 && <span>+{scheduleBoard.unscheduled.length - 6} more</span>}
            </div>
          )}
        </section>
      )}

      {/* CRM queue: native booking handoff tasks from public submissions */}
      {canManage && (openPipelineTasks.length > 0 || pipelineError) && (
        <section className="bkm-crm-queue">
          <div className="bkm-crm-head">
            <div>
              <p className="bkm-recovery-kicker">CRM Queue</p>
              <h2>Booking handoff</h2>
            </div>
            <div className="bkm-recovery-actions">
              <span>{openPipelineTasks.length} open</span>
              <button type="button" className="bkm-recovery-refresh" onClick={fetchPipelineTasks}>Refresh</button>
            </div>
          </div>

          {pipelineError && (
            <div className="bkm-error">
              {pipelineError}
              <button className="bkm-error-dismiss" onClick={() => setPipelineError(null)}>×</button>
            </div>
          )}

          {openPipelineTasks.length > 0 && (
            <div className="bkm-crm-list">
              {openPipelineTasks.slice(0, 6).map((task) => {
                const booking = task.booking_id ? allBookings.find((item) => item.id === task.booking_id) : null
                const clientName = task.booking_client_name || booking?.resolved_name || booking?.client_name || 'New inquiry'
                const reference = task.booking_reference_code || booking?.reference_code
                const conversationUrl = task.courier_url || (task.conversation_id ? `/dashboard/courier?convo=${task.conversation_id}` : null)
                return (
                  <article key={task.id} className="bkm-crm-card">
                    <div className="bkm-crm-main">
                      <div className="bkm-recovery-row">
                        <strong>{clientName}</strong>
                        <span className="bkm-recovery-status status-open">{task.priority || 'open'}</span>
                      </div>
                      <div className="bkm-recovery-meta">
                        {reference && <span>{reference}</span>}
                        {task.booking_artist_name && <span>{task.booking_artist_name}</span>}
                        <span>{timeAgo(task.created_at)}</span>
                      </div>
                      {task.body && (
                        <p className="bkm-crm-desc">{task.body.split('\n').find((line) => line.startsWith('Project: '))?.replace('Project: ', '') || task.title}</p>
                      )}
                    </div>
                    <div className="bkm-crm-actions">
                      {task.booking_id && (
                        <button type="button" className="btn btn-outline btn-xs" onClick={() => openTaskBooking(task)}>
                          Open booking
                        </button>
                      )}
                      {conversationUrl && (
                        <Link to={conversationUrl} className="btn btn-outline btn-xs">
                          Courier
                        </Link>
                      )}
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs"
                        onClick={async () => {
                          const updated = await crmApi.setPipelineTaskStatus(task.id, 'done')
                          setPipelineTasks((prev) => prev.map((item) => item.id === task.id ? updated : item))
                        }}
                      >
                        Done
                      </button>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
        </section>
      )}

      {/* Recovery queue: partial public submissions saved before final submit */}
      {canManage && recoveryDrafts.length > 0 && (
        <section className="bkm-recovery">
          <div className="bkm-recovery-head">
            <div>
              <p className="bkm-recovery-kicker">Recovery Queue</p>
              <h2>Partial booking inquiries</h2>
            </div>
            <div className="bkm-recovery-actions">
              <span>{recoveryDrafts.length} open{draftsTotal > recoveryDrafts.length ? ` / ${draftsTotal} total` : ''}</span>
              <button type="button" className="bkm-recovery-refresh" onClick={fetchBookingDrafts}>Refresh</button>
            </div>
          </div>

          {draftsError && (
            <div className="bkm-error">
              {draftsError}
              <button className="bkm-error-dismiss" onClick={() => setDraftsError(null)}>×</button>
            </div>
          )}

          {recoveryDrafts.length > 0 && (
            <div className="bkm-recovery-list">
              {recoveryDrafts.slice(0, 5).map((draft) => {
                const data = draft.form_data || {}
                const description = data.description || ''
                return (
                  <article key={draft.id} className="bkm-recovery-card">
                    <div className="bkm-recovery-main">
                      <div className="bkm-recovery-row">
                        <strong>{draft.guest_name || [data.first_name, data.last_name].filter(Boolean).join(' ') || 'Unknown client'}</strong>
                        <span className={`bkm-recovery-status status-${draft.status}`}>{draft.status.replace(/_/g, ' ')}</span>
                      </div>
                      <div className="bkm-recovery-meta">
                        {draft.guest_email && <a href={`mailto:${draft.guest_email}`}>{draft.guest_email}</a>}
                        {draft.guest_phone && <a href={`tel:${draft.guest_phone}`}>{draft.guest_phone}</a>}
                        {draft.artist_slug && <span>artist: {draft.artist_slug}</span>}
                        <span>{timeAgo(draft.updated_at)}</span>
                      </div>
                      {description && <p className="bkm-recovery-desc">{description}</p>}
                    </div>
                    {draft.image_urls && draft.image_urls.length > 0 && (
                      <div className="bkm-recovery-images">
                        {draft.image_urls.slice(0, 3).map((url, i) => (
                          <button key={url} type="button" className="bkm-ref-thumb" onClick={() => setLightboxUrl(imageThumb(url, 'w800'))}>
                            <img src={imageThumb(url)} alt={`Draft reference ${i + 1}`} />
                          </button>
                        ))}
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </section>
      )}

      {/* Error */}
      {error && (
        <div className="bkm-error">
          {error}
          <button className="bkm-error-dismiss" onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)', padding: 'var(--space-4)' }}>
          <div className="skeleton skeleton-card" style={{ height: '80px' }} />
          <div className="skeleton skeleton-card" style={{ height: '80px' }} />
          <div className="skeleton skeleton-card" style={{ height: '80px' }} />
        </div>
      )}

      {/* Empty state */}
      {!isLoading && filtered.length === 0 && (
        <div className="bkm-empty">
          {Icons.empty}
          <p className="bkm-empty-title">
            {searchQuery ? 'No matches found' : activeTab === 'all' ? 'No bookings yet' : `No ${activeTab} bookings`}
          </p>
          <p className="bkm-empty-sub">
            {searchQuery
              ? 'Try a different search term'
              : 'Bookings will appear here when clients submit requests'}
          </p>
        </div>
      )}

      {/* Bookings list */}
      {!isLoading && filtered.length > 0 && (
        <div className="bkm-list">
          {filtered.map((b) => {
            const isExpanded = expandedId === b.id
            return (
              <div
                key={b.id}
                className={`bkm-card ${isExpanded ? 'expanded' : ''}`}
              >
                {/* Summary row */}
                <button
                  className="bkm-card-summary"
                  onClick={() => toggleExpand(b.id)}
                >
                  <div className={`bkm-indicator status-${b.status}`} />
                  <div className="bkm-card-main">
                    <div className="bkm-card-top">
                      <span className="bkm-client">{b.resolved_name || b.client_name || 'Guest Client'}</span>
                      <StatusBadge status={b.status} />
                    </div>
                    <div className="bkm-card-meta">
                      {b.artist_name && <span className="bkm-meta-item">{Icons.user} {b.artist_name}</span>}
                      {b.style_preference && <span className="bkm-meta-item">{Icons.palette} {formatStyle(b.style_preference)}</span>}
                      {b.placement && <span className="bkm-meta-item">{Icons.pin} {b.placement}</span>}
                      {requestedBookingSlots(b).map((slot, index) => (
                        <span key={`${slot.date}-${slot.time}-${index}`} className="bkm-meta-item">
                          {Icons.calendar} Requested: {formatRequestedSlot(slot.date, slot.time)}
                        </span>
                      ))}
                      <span className="bkm-meta-item">{Icons.clock} {timeAgo(b.created_at)}</span>
                    </div>
                    {b.description && (
                      <p className="bkm-desc">{b.description}</p>
                    )}
                  </div>
                  <span className={`bkm-chevron ${isExpanded ? 'open' : ''}`}>
                    {Icons.chevron}
                  </span>
                </button>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="bkm-detail">

                    {/* ── Row 1: Chips + ref images (dense header) ── */}
                    <div className="bkm-detail-header">
                      <div className="bkm-chips">
                        {canReassign && (
                          <div className="bkm-reassign-control" title="Reassign artist for this booking">
                            <span className="bkm-edit-label">Artist:</span>
                            <select
                              className="bkm-reassign-select"
                              value={b.artist_id}
                              disabled={updatingId === b.id}
                              onChange={(e) => handleReassignBooking(b, e.target.value)}
                              aria-label="Reassign booking to artist"
                            >
                              {!actAsArtists.some((a) => a.id === b.artist_id) && b.artist_name && (
                                <option value={b.artist_id}>{b.artist_name} (Current)</option>
                              )}
                              {actAsArtists.map((a) => (
                                <option key={a.id} value={a.id}>
                                  {a.name}
                                </option>
                              ))}
                            </select>
                          </div>
                        )}
                        {b.deposit_authorization_status && (
                          <span
                            className={`bkm-chip bkm-chip-auth status-${b.deposit_authorization_status}`}
                            title="BYOF deposit authorization status"
                          >
                            {b.deposit_authorization_status === 'authorized'
                              ? 'Deposit hold: Active'
                              : b.deposit_authorization_status === 'captured'
                              ? 'Deposit captured'
                              : b.deposit_authorization_status === 'reauthorization_required'
                              ? 'Reauthorization needed'
                              : b.deposit_authorization_status === 'released'
                              ? 'Deposit hold released'
                              : `Hold: ${b.deposit_authorization_status}`}
                          </span>
                        )}
                        {(b.resolved_email || b.guest_email) && (
                          <a className="bkm-chip" href={`mailto:${b.resolved_email || b.guest_email}`}>
                            {Icons.mail} {b.resolved_email || b.guest_email}
                          </a>
                        )}
                        {(b.resolved_phone || b.guest_phone) && (
                          <a className="bkm-chip" href={`tel:${b.resolved_phone || b.guest_phone}`}>
                            {Icons.phone} {b.resolved_phone || b.guest_phone}
                          </a>
                        )}
                        {(b.resolved_phone || b.guest_phone) && (
                          <span className="bkm-chip" title={b.sms_consent_text || 'No SMS appointment reminder consent recorded'}>
                            {b.sms_opt_in ? 'SMS reminders on' : 'SMS reminders off'}
                          </span>
                        )}
                        {b.reference_code && <span className="bkm-chip bkm-chip-mono">{Icons.hash} {b.reference_code}</span>}
                        {b.size_estimate && <span className="bkm-chip">{Icons.ruler} {b.size_estimate}</span>}
                        {b.budget && <span className="bkm-chip">{Icons.dollar} {b.budget}</span>}
                        {b.timeline && <span className="bkm-chip">{Icons.clock} {b.timeline}</span>}
                      </div>
                      {b.reference_urls && b.reference_urls.length > 0 && (
                        <div className="bkm-ref-images">
                          {b.reference_urls.map((url, i) => (
                            <button key={i} className="bkm-ref-thumb" onClick={() => setLightboxUrl(imageThumb(url, 'w800'))}>
                              <img src={imageThumb(url)} alt={`Reference ${i + 1}`} />
                            </button>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* ── Row 2: Two-column (calendar left, pricing/duration/message right) ── */}
                    {canManage && b.status === 'pending' && (
                      <div className="bkm-schedule-inline">
                        {/* Left: calendar + slot chip */}
                        <div className="bkm-schedule-left">
                          {b.preferred_slots && b.preferred_slots.length > 0 && (
                            <div className="bkm-collector-slots" aria-label="Collector offered times">
                              <div className="bkm-collector-slots-head">
                                <span className="bkm-edit-label">Collector offered</span>
                                <span>Pick one to approve in the thread</span>
                              </div>
                              <div className="bkm-collector-slot-grid">
                                {b.preferred_slots.map((slot) => (
                                  <button
                                    key={`${slot.date}-${slot.time}`}
                                    type="button"
                                    className="bkm-collector-slot"
                                    disabled={openingThreadId === b.id}
                                    onClick={() => void openApprovalThread(b, slot)}
                                  >
                                    {Icons.calendar}
                                    <span>{formatRequestedSlot(slot.date, slot.time)}</span>
                                  </button>
                                ))}
                              </div>
                            </div>
                          )}
                          <div className="bkm-slot-confirm-row">
                            <button
                              type="button"
                              className="btn btn-sm btn-ghost"
                              disabled={openingThreadId === b.id}
                              onClick={() => void openApprovalThread(b, null, true)}
                            >
                              {openingThreadId === b.id ? 'Opening thread...' : 'Message to coordinate a time'}
                            </button>
                          </div>
                          <SlotPicker
                            bookingId={b.id}
                            artistSlug={artistSlugById[b.artist_id] ?? null}
                            availabilitySource={artistSourceById[b.artist_id] ?? 'none'}
                            preferredDate={b.preferred_date}
                            duration={slotDuration[b.id] ?? 120}
                            onDurationChange={(d) => setSlotDuration(p => ({ ...p, [b.id]: d }))}
                            onSelect={(date, time, dur) => {
                              setEditSchedDate(p => ({ ...p, [b.id]: date }))
                              setEditSchedTime(p => ({ ...p, [b.id]: time }))
                              setEditDuration(p => ({ ...p, [b.id]: String(dur) }))
                            }}
                          />
                          {editSchedDate[b.id] && editSchedTime[b.id] && (
                            <div className="bkm-slot-confirm-row">
                              <div className="bkm-slot-selected-chip">
                                {Icons.calendar}&nbsp;
                                {new Date(`${editSchedDate[b.id]}T${editSchedTime[b.id]}`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
                                {' · '}
                                {formatTime12h(editSchedTime[b.id])}
                                {editDuration[b.id] && ` · ${Number(editDuration[b.id]) >= 60 ? `${Number(editDuration[b.id])/60}h` : `${editDuration[b.id]}m`}`}
                                <button type="button" className="bkm-slot-clear" aria-label="Clear selected time" onClick={() => {
                                  setEditSchedDate(p => { const n = {...p}; delete n[b.id]; return n })
                                  setEditSchedTime(p => { const n = {...p}; delete n[b.id]; return n })
                                  setEditDuration(p => { const n = {...p}; delete n[b.id]; return n })
                                }}>×</button>
                              </div>
                              <button
                                type="button"
                                className="btn btn-sm btn-primary bkm-confirm-slot"
                                disabled={openingThreadId === b.id}
                                onClick={() => void openApprovalThread(
                                  b,
                                  { date: editSchedDate[b.id], time: editSchedTime[b.id] },
                                  false,
                                  editDuration[b.id] ? Number(editDuration[b.id]) : undefined,
                                )}
                              >
                                {openingThreadId === b.id ? 'Opening thread...' : 'Approve this time'}
                              </button>
                            </div>
                          )}
                        </div>

                        {/* Right: Courier-style response bar */}
                        <div className="bkm-schedule-right">
                          {/* Duration pills */}
                          <div className="bkm-slot-duration-row">
                            <div className="bkm-slot-duration-pills">
                              {[60, 90, 120, 180, 240].map(m => (
                                <button key={m} type="button"
                                  className={`bkm-slot-duration-pill${(slotDuration[b.id] ?? 120) === m ? ' active' : ''}`}
                                  onClick={() => setSlotDuration(p => ({ ...p, [b.id]: m }))}
                                >
                                  {m >= 60 ? `${m / 60}h` : `${m}m`}
                                </button>
                              ))}
                            </div>
                          </div>

                          {/* Courier-style input bar */}
                          <div className="bkm-courier-bar">
                            {/* Tool buttons (icon-only) */}
                            <div className="courier-input-tools">
                              {/* Gala AI */}
                              <button
                                type="button"
                                className={`courier-tool-btn courier-tool-btn--gala${galaLoading === b.id ? ' loading' : ''}`}
                                disabled={galaLoading === b.id}
                                title="Gala AI draft"
                                onClick={async () => {
                                  setGalaLoading(b.id)
                                  try {
                                    const res = await bookingsApi.galaAssist(b.id)
                                    setEditNotes((p) => ({ ...p, [b.id]: res.draft }))
                                  } catch { toast.error('Gala could not generate a response') }
                                  finally { setGalaLoading(null) }
                                }}
                              >
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                              </button>
                              {/* Template */}
                              <button
                                type="button"
                                className="courier-tool-btn"
                                title="Insert template"
                                onClick={() => {
                                  const template = user?.nexus_profile?.response_templates?.new_inquiry
                                    || "Hey {name}, thanks for reaching out! I'd love to work on this piece with you. Let me put together a quote and we'll get you scheduled."
                                  const clientFirst = (b.resolved_name || b.client_name || 'there').split(' ')[0]
                                  setEditNotes((p) => ({ ...p, [b.id]: template.replace(/\{name\}/g, clientFirst) }))
                                }}
                              >
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                              </button>
                              {/* Quote */}
                              <button
                                type="button"
                                className={`courier-tool-btn${quoteComposerOpen[b.id] ? ' courier-tool-btn--active' : ''}`}
                                title="Attach quote"
                                onClick={() => setQuoteComposerOpen(p => ({ ...p, [b.id]: !p[b.id] }))}
                              >
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16"><path d="M12 2v20m5-17H9.5a3.5 3.5 0 1 0 0 7h5a3.5 3.5 0 1 1 0 7H7"/></svg>
                              </button>
                              {/* Calendar / Slots */}
                              <button
                                type="button"
                                className={`courier-tool-btn${slotComposerOpen[b.id] ? ' courier-tool-btn--active' : ''}`}
                                title="Propose time slots"
                                onClick={() => setSlotComposerOpen(p => ({ ...p, [b.id]: !p[b.id] }))}
                              >
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
                              </button>
                            </div>

                            {/* Inline quote composer — slides in when $ Quote is toggled */}
                            {quoteComposerOpen[b.id] && (
                              <div className="qp-composer">
                                <div className="qp-composer-header">
                                  <span className="qp-composer-label">Quote Proposal</span>
                                  <button
                                    type="button"
                                    className="qp-composer-close"
                                    onClick={() => setQuoteComposerOpen(p => ({ ...p, [b.id]: false }))}
                                  >&times;</button>
                                </div>
                                <div className="qp-composer-fields">
                                  <div className="qp-composer-field">
                                    <label>Deposit ($)</label>
                                    <input
                                      type="number" min={0} step={25} placeholder="0"
                                      value={editInlineDeposit[b.id] ?? ''}
                                      onChange={(e) => setEditInlineDeposit(p => ({ ...p, [b.id]: e.target.value }))}
                                    />
                                  </div>
                                  <div className="qp-composer-field">
                                    <label>Est. Total ($)</label>
                                    <input
                                      type="number" min={0} step={50} placeholder="Optional"
                                      value={editInlineTotal[b.id] ?? ''}
                                      onChange={(e) => setEditInlineTotal(p => ({ ...p, [b.id]: e.target.value }))}
                                    />
                                  </div>
                                </div>
                                <div className="qp-composer-hint">
                                  Attached to your message. Client will see a Confirm/Decline card.
                                </div>
                              </div>
                            )}

                            {/* Inline slot composer — whitelist slots for client */}
                            {slotComposerOpen[b.id] && (
                              <div className="qp-composer">
                                <div className="qp-composer-header">
                                  <span className="qp-composer-label">Slot Proposal</span>
                                  <button
                                    type="button"
                                    className="qp-composer-close"
                                    onClick={() => {
                                      setSlotComposerOpen(p => ({ ...p, [b.id]: false }))
                                      setProposedSlots(p => { const n = { ...p }; delete n[b.id]; return n })
                                    }}
                                  >&times;</button>
                                </div>

                                {/* Added slots */}
                                {(proposedSlots[b.id] || []).map((s, i) => (
                                  <div key={i} className="sp-composer-slot">
                                    <span>{s.label || `${s.date} ${s.time}`}</span>
                                    <button
                                      type="button"
                                      className="qp-composer-close"
                                      onClick={() => setProposedSlots(p => ({
                                        ...p,
                                        [b.id]: (p[b.id] || []).filter((_, j) => j !== i),
                                      }))}
                                    >&times;</button>
                                  </div>
                                ))}

                                {/* Quick-add row */}
                                <div className="qp-composer-fields">
                                  <div className="qp-composer-field">
                                    <label>Date</label>
                                    <input
                                      type="date"
                                      id={`sp-date-${b.id}`}
                                      min={new Date().toISOString().slice(0, 10)}
                                    />
                                  </div>
                                  <div className="qp-composer-field">
                                    <label>Time</label>
                                    <input
                                      type="time"
                                      id={`sp-time-${b.id}`}
                                      defaultValue="11:00"
                                    />
                                  </div>
                                  <div className="qp-composer-field" style={{ flex: '0 0 auto' }}>
                                    <label>&nbsp;</label>
                                    <button
                                      type="button"
                                      className="bkm-msg-toolbar-btn"
                                      onClick={() => {
                                        const dateEl = document.getElementById(`sp-date-${b.id}`) as HTMLInputElement | null
                                        const timeEl = document.getElementById(`sp-time-${b.id}`) as HTMLInputElement | null
                                        if (!dateEl?.value || !timeEl?.value) return
                                        const d = new Date(`${dateEl.value}T${timeEl.value}`)
                                        const dur = slotDuration[b.id] ?? 120
                                        const label = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
                                          + ' · ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' })
                                        setProposedSlots(p => ({
                                          ...p,
                                          [b.id]: [...(p[b.id] || []), { date: dateEl.value, time: timeEl.value, duration_minutes: dur, label }],
                                        }))
                                      }}
                                    >+ Add</button>
                                  </div>
                                </div>

                                <div className="qp-composer-hint">
                                  {(proposedSlots[b.id]?.length || 0) === 0
                                    ? 'Add at least one slot for the client to pick from.'
                                    : `${proposedSlots[b.id]!.length} slot${proposedSlots[b.id]!.length > 1 ? 's' : ''} ready. Client picks one.`
                                  }
                                </div>
                              </div>
                            )}

                            {/* Input + send */}
                            <input
                              className="courier-input"
                              placeholder="Type your response..."
                              value={editNotes[b.id] ?? ''}
                              onChange={(e) => setEditNotes((p) => ({ ...p, [b.id]: e.target.value }))}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' && !e.shiftKey && (editNotes[b.id] ?? '').trim()) {
                                  e.preventDefault()
                                  handleStatusUpdate(b.id, 'quote')
                                }
                              }}
                            />
                            <button
                              type="button"
                              className="courier-send-btn"
                              disabled={updatingId === b.id || !(editNotes[b.id] ?? '').trim()}
                              onClick={() => handleStatusUpdate(b.id, 'quote')}
                            >
                              <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                            </button>
                          </div>

                          {/* Delete action — minimal, below the bar */}
                          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '4px' }}>
                            <button
                              type="button"
                              className="btn btn-sm btn-danger"
                              style={{ fontSize: '0.65rem', padding: '2px 8px', opacity: 0.6 }}
                              disabled={updatingId === b.id}
                              onClick={() => {
                                if (confirm('Cancel this inquiry?')) handleCancelBooking(b)
                              }}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* ── Scheduling row (for quoted/confirmed only) ── */}
                    {b.status !== 'pending' && (b.scheduled_date || b.preferred_date) && (
                      <div className="bkm-sched-row">
                        {b.scheduled_date ? (
                          <>{Icons.calendar}<span className="bkm-sched-confirmed">{formatDate(b.scheduled_date)}{b.scheduled_time ? ` · ${formatTime12h(b.scheduled_time)}` : ''}</span></>
                        ) : (
                          <>{Icons.calendar}<span className="bkm-sched-preferred">Prefers {formatDate(b.preferred_date)}{b.preferred_time ? ` · ${b.preferred_time}` : ''}</span></>
                        )}
                      </div>
                    )}

                    {canManage && (b.status === 'quoted' || b.status === 'confirmed') && (
                      <div className="bkm-schedule-inline">
                        <div className="qp-composer-fields">
                          <div className="qp-composer-field">
                            <label>Date</label>
                            <input
                              type="date"
                              min={new Date().toISOString().slice(0, 10)}
                              value={editSchedDate[b.id] ?? b.scheduled_date ?? b.preferred_date ?? ''}
                              onChange={(e) => setEditSchedDate((p) => ({ ...p, [b.id]: e.target.value }))}
                            />
                          </div>
                          <div className="qp-composer-field">
                            <label>Time</label>
                            <input
                              type="time"
                              value={editSchedTime[b.id] ?? b.scheduled_time ?? b.preferred_time ?? ''}
                              onChange={(e) => setEditSchedTime((p) => ({ ...p, [b.id]: e.target.value }))}
                            />
                          </div>
                          <div className="qp-composer-field">
                            <label>Duration</label>
                            <select
                              value={editDuration[b.id] ?? String(b.duration_minutes || 120)}
                              onChange={(e) => setEditDuration((p) => ({ ...p, [b.id]: e.target.value }))}
                            >
                              {[60, 90, 120, 180, 240, 360].map((minutes) => (
                                <option key={minutes} value={minutes}>
                                  {minutes >= 60 ? `${minutes / 60}h` : `${minutes}m`}
                                </option>
                              ))}
                            </select>
                          </div>
                        </div>
                        {canOverrideSchedule && (
                          <div className="qp-composer-fields" style={{ marginTop: '8px' }}>
                            <label className="bkm-chip" style={{ cursor: 'pointer' }}>
                              <input
                                type="checkbox"
                                checked={Boolean(overrideConflict[b.id])}
                                onChange={(e) => setOverrideConflict((p) => ({ ...p, [b.id]: e.target.checked }))}
                              />
                              Override conflicts
                            </label>
                            <label className="bkm-chip" style={{ cursor: 'pointer' }}>
                              <input
                                type="checkbox"
                                checked={Boolean(overrideBounds[b.id])}
                                onChange={(e) => setOverrideBounds((p) => ({ ...p, [b.id]: e.target.checked }))}
                              />
                              Override bounds
                            </label>
                            {(overrideConflict[b.id] || overrideBounds[b.id]) && (
                              <div className="qp-composer-field" style={{ minWidth: '260px', flex: '1 1 260px' }}>
                                <label>Override reason</label>
                                <input
                                  type="text"
                                  value={overrideReason[b.id] ?? ''}
                                  onChange={(e) => setOverrideReason((p) => ({ ...p, [b.id]: e.target.value }))}
                                  placeholder="Required for audited override"
                                />
                              </div>
                            )}
                          </div>
                        )}
                        <div style={{ display: 'grid', gap: '6px', marginTop: '8px' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                            <span className="bkm-edit-label">Smoku schedule suggestions</span>
                            <button
                              type="button"
                              className="btn btn-sm btn-outline"
                              disabled={scheduleSuggestionLoading === b.id}
                              onClick={() => loadScheduleSuggestions(b)}
                            >
                              {scheduleSuggestionLoading === b.id ? 'Finding...' : 'Suggest times'}
                            </button>
                          </div>
                          {scheduleSuggestions[b.id]?.length > 0 && (
                            <div className="bkm-slot-options">
                              {scheduleSuggestions[b.id].map((slot) => (
                                <button
                                  key={`${slot.date}-${slot.time}-${slot.duration_minutes}`}
                                  type="button"
                                  className="bkm-slot-option"
                                  onClick={() => {
                                    setEditSchedDate((p) => ({ ...p, [b.id]: slot.date }))
                                    setEditSchedTime((p) => ({ ...p, [b.id]: slot.time }))
                                    setEditDuration((p) => ({ ...p, [b.id]: String(slot.duration_minutes) }))
                                  }}
                                  title={slot.rationale}
                                >
                                  <span className="bkm-slot-option-label">{slot.label}</span>
                                  <span className="bkm-slot-option-dur">{slot.duration_minutes >= 60 ? `${slot.duration_minutes / 60}h` : `${slot.duration_minutes}m`}</span>
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '8px' }}>
                          <button
                            type="button"
                            className="btn btn-sm btn-primary"
                            disabled={updatingId === b.id}
                            onClick={() => handleRescheduleBooking(b)}
                          >
                            Save time
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            disabled={updatingId === b.id}
                            onClick={() => {
                              if (confirm('Cancel this booking and remove the calendar event?')) handleCancelBooking(b)
                            }}
                          >
                            Cancel booking
                          </button>
                        </div>
                      </div>
                    )}

                    {/* ── Client notes ── */}
                    {b.client_notes && (
                      <p className="bkm-client-notes">{Icons.note} {b.client_notes}</p>
                    )}

                    {/* ── Quote history (quoted/confirmed) ── */}
                    {(b.status === 'quoted' || b.status === 'confirmed') && bookingQuotes[b.id] && bookingQuotes[b.id].length > 0 && (
                      <div className="bkm-quote-status">
                        <h4 className="bkm-schedule-title">Quote History</h4>
                        {bookingQuotes[b.id].map((q) => (
                          <div key={q.id} className="bkm-quote-item">
                            <div className="bkm-quote-header">
                              <span className={`badge ${q.status === 'accepted' ? 'badge-success' : q.status === 'viewed' ? 'badge-info' : q.status === 'expired' ? 'badge-muted' : 'badge-warning'}`}>
                                {q.status}
                              </span>
                              <span className="bkm-meta-item">${q.deposit_amount} deposit</span>
                              {q.estimated_total_min && (
                                <span className="bkm-meta-item">
                                  Est: ${q.estimated_total_min}{q.estimated_total_max && q.estimated_total_max !== q.estimated_total_min ? `-$${q.estimated_total_max}` : ''}
                                </span>
                              )}
                              <span className="bkm-meta-item">{Icons.clock} Sent {timeAgo(q.sent_at)}</span>
                            </div>
                            {q.selected_date && (
                              <div className="bkm-meta-item" style={{ marginTop: '4px', color: 'var(--gold)' }}>
                                {Icons.calendar} Booked: {formatDate(q.selected_date)} at {formatTime12h(q.selected_time)}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Booking lifecycle history */}
                    {(bookingHistory[b.id] || bookingHistoryError[b.id]) && (
                      <div className="bkm-quote-status">
                        <h4 className="bkm-schedule-title">Booking History</h4>
                        {bookingHistoryError[b.id] && (
                          <div className="bkm-quote-item">
                            <div className="bkm-quote-header">
                              <span className="badge badge-warning">Unavailable</span>
                              <span className="bkm-meta-item">{bookingHistoryError[b.id]}</span>
                            </div>
                          </div>
                        )}
                        {bookingHistory[b.id]?.length === 0 && (
                          <div className="bkm-quote-item">
                            <div className="bkm-quote-header">
                              <span className="badge badge-muted">No events</span>
                              <span className="bkm-meta-item">No lifecycle events recorded yet</span>
                            </div>
                          </div>
                        )}
                        {bookingHistory[b.id]?.map((event) => (
                          <div key={event.id} className="bkm-quote-item">
                            <div className="bkm-quote-header">
                              <span className="badge badge-info">{formatEventLabel(event.event_type)}</span>
                              <span className="bkm-meta-item">{event.summary}</span>
                              <span className="bkm-meta-item">{Icons.clock} {timeAgo(event.created_at)}</span>
                            </div>
                            <div className="bkm-meta-item" style={{ marginTop: '4px' }}>
                              Source: {event.source}
                              {event.reference_code ? ` · Ref ${event.reference_code}` : ''}
                              {event.hold_token ? ` · Hold ${event.hold_token.slice(0, 8)}` : ''}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Footer removed — actions live inline in the courier bar above */}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* ── Reference Image Lightbox ── */}
      {lightboxUrl && (
        <div className="bkm-lightbox" onClick={() => setLightboxUrl(null)}>
          <div className="bkm-lightbox-content" onClick={(e) => e.stopPropagation()}>
            <button className="bkm-lightbox-close" onClick={() => setLightboxUrl(null)}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
            <img src={lightboxUrl} alt="Reference" className="bkm-lightbox-img" />
          </div>
        </div>
      )}
    </div>
  )
}



