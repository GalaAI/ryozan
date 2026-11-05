/**
 * HuddlLayout: Booking hub using the same layout as Nexus.
 *
 * Structure: .nexus-header toolbar → .nexus-body (.nexus-viewport)
 * Mods render inside the viewport via internal state (no sub-routes).
 *
 * Mods:
 *   - Today: day selector with booking dots and selected-day sessions
 *   - Books: upcoming booking list
 *   - Clients: client rolodex from conversations + bookings
 *   - Availability: set open hours
 *   - Aftercare: post-session client care
 */

import type { ReactNode } from 'react'
import { useState, useEffect, useMemo, lazy, Suspense } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  bookings as bookingsApi,
  calendar as calendarApi,
  contacts as contactsApi,
  googleAuth,
  artists as artistsApi,
  type Booking,
  type CalendarEvent,
  type ContactRecord,
  type SchedulerContract,
} from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { useStudioSettings } from '../../hooks/useStudioSettings'

/* ── SVG Icons ──────────────────────────────────── */

const Icons = {
  calendar: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
    </svg>
  ),
  bookOpen: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
    </svg>
  ),
  users: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
  ),
  heart: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
    </svg>
  ),
  arrowRight: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>
    </svg>
  ),
  chevronDown: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9"/>
    </svg>
  ),
  messageSquare: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  ),
  refresh: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
    </svg>
  ),
  send: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
    </svg>
  ),
  link: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
    </svg>
  ),
  fileText: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>
    </svg>
  ),
  activity: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
    </svg>
  ),
  alert: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
    </svg>
  ),
}

/* ── Huddl panel types ──────────────────────────── */

type HuddlePanel = 'today' | 'inquiries' | 'clients' | 'followup'

interface ToolBeltItem {
  id: HuddlePanel
  label: string
  icon: ReactNode
}

const TOOL_BELT: ToolBeltItem[] = [
  { id: 'today', label: 'Today', icon: Icons.activity },
  { id: 'inquiries', label: 'Bookings', icon: Icons.bookOpen },
  { id: 'clients', label: 'Clients', icon: Icons.users },
  { id: 'followup', label: 'Follow-up', icon: Icons.alert },
]

/* ── Calendar helpers ──────────────────────────────── */

type DayEvent = { time: string; title: string; meta: string | null; isBooking: boolean }
type SlotDiagnostics = Awaited<ReturnType<typeof calendarApi.getArtistSlots>>
type GoogleStatus = Awaited<ReturnType<typeof googleAuth.getStatus>>

const WEEKDAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function toDateStr(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function getTodayDateString(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatTime(time: string | null): string {
  if (!time) return '--:--'
  const parts = time.split(':')
  const h = parseInt(parts[0], 10)
  const m = parts[1] || '00'
  if (!Number.isFinite(h)) return time
  const ampm = h >= 12 ? 'PM' : 'AM'
  const h12 = h % 12 || 12
  return `${h12}:${m} ${ampm}`
}

function slotReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    active_hold: 'held by intake',
    booking_conflict: 'booking conflict',
    closed_day: 'shop closed',
    google_busy: 'Google busy',
    max_advance: 'past booking window',
    minimum_notice: 'inside booking notice',
    past: 'past time',
  }
  return labels[reason] || reason.split('_').join(' ')
}

function getWeekDates(d: Date): Date[] {
  const start = new Date(d)
  start.setDate(start.getDate() - start.getDay())
  return Array.from({ length: 7 }, (_, i) => {
    const day = new Date(start)
    day.setDate(start.getDate() + i)
    return day
  })
}

function bookingsByDate(bookings: Booking[]): Map<string, Booking[]> {
  const map = new Map<string, Booking[]>()
  for (const b of bookings) {
    const key = b.scheduled_date || ''
    if (!key) continue
    const arr = map.get(key) || []
    arr.push(b)
    map.set(key, arr)
  }
  return map
}

/* ── Panel components ─────────────────────────────── */

const BookingsManager = lazy(() => import('./BookingsManager'))

interface ClientEntry {
  name: string
  email: string
  phone: string | null
  bookings: number
  lastBooking: string | null
  status: string
  source: 'booking' | 'jwt' | 'ryozan' | 'manual' | 'api'
}

type ClientTab = 'all' | 'jwt' | 'ryozan'

function parseCSV(text: string): ClientEntry[] {
  const lines = text.trim().split('\n')
  if (lines.length < 2) return []
  const headers = lines[0].split(',').map(h => h.trim().toLowerCase().replace(/['"]/g, ''))
  const emailIdx = headers.findIndex(h => h.includes('email'))
  const nameIdx = headers.findIndex(h => h.includes('first') || h === 'name' || h.includes('full'))
  const lastIdx = headers.findIndex(h => h.includes('last'))
  const phoneIdx = headers.findIndex(h => h.includes('phone'))

  return lines.slice(1).filter(l => l.trim()).map(line => {
    const cols = line.split(',').map(c => c.trim().replace(/^["']|["']$/g, ''))
    const first = nameIdx >= 0 ? cols[nameIdx] || '' : ''
    const last = lastIdx >= 0 ? cols[lastIdx] || '' : ''
    const name = (first + ' ' + last).trim() || 'Unknown'
    return {
      name,
      email: emailIdx >= 0 ? cols[emailIdx] || '' : '',
      phone: phoneIdx >= 0 ? cols[phoneIdx] || null : null,
      bookings: 0,
      lastBooking: null,
      status: 'imported',
      source: 'manual' as const,
    }
  }).filter(c => c.email)
}

function contactToClientEntry(contact: ContactRecord): ClientEntry {
  return {
    name: contact.name || contact.email || 'Unknown',
    email: contact.email || '',
    phone: contact.phone,
    bookings: contact.booking_count,
    lastBooking: contact.last_booking_at,
    status: contact.last_booking_status || contact.lifecycle_stage,
    source: 'api',
  }
}

function ClientsPanel({ bookings }: { bookings: Booking[] }) {
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState<ClientTab>('all')
  const [apiContacts, setApiContacts] = useState<ClientEntry[]>([])
  const [contactsLoading, setContactsLoading] = useState(true)
  const [contactsError, setContactsError] = useState<string | null>(null)
  const { settings: studioSettings } = useStudioSettings()
  const [jwtContacts, setJwtContacts] = useState<ClientEntry[]>([])
  const [ryozanContacts, setRyozanContacts] = useState<ClientEntry[]>([])
  const [selectedClientKey, setSelectedClientKey] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    contactsApi.list({ limit: 500 })
      .then((response) => {
        if (cancelled) return
        setApiContacts(response.contacts.map(contactToClientEntry))
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setContactsError(error instanceof Error ? error.message : 'Unable to load contacts')
      })
      .finally(() => {
        if (!cancelled) setContactsLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const bookingClients = useMemo(() => {
    const map = new Map<string, ClientEntry>()
    for (const b of bookings) {
      const name = b.resolved_name || b.client_name || 'Unknown'
      const email = b.resolved_email || b.guest_email || ''
      const key = email || name
      const existing = map.get(key)
      if (existing) {
        existing.bookings++
        if (b.created_at && (!existing.lastBooking || b.created_at > existing.lastBooking)) {
          existing.lastBooking = b.created_at
          existing.status = b.status
        }
      } else {
        map.set(key, {
          name, email, phone: b.guest_phone || null,
          bookings: 1, lastBooking: b.created_at || null,
          status: b.status, source: 'booking',
        })
      }
    }
    return Array.from(map.values()).sort((a, b) => b.bookings - a.bookings)
  }, [bookings])

  const handleUpload = (audience: 'jwt' | 'ryozan') => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.csv,.txt'
    input.onchange = (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = () => {
        const contacts = parseCSV(reader.result as string).map(c => ({ ...c, source: audience }))
        if (audience === 'jwt') {
          const merged = [...jwtContacts, ...contacts]
          setJwtContacts(merged)
        } else {
          const merged = [...ryozanContacts, ...contacts]
          setRyozanContacts(merged)
        }
      }
      reader.readAsText(file)
    }
    input.click()
  }

  const allClients = useMemo(() => {
    const alphaSort = (a: ClientEntry, b: ClientEntry) => {
      const aHasName = a.name && !a.name.includes('@') && a.name !== a.email.split('@')[0]
      const bHasName = b.name && !b.name.includes('@') && b.name !== b.email.split('@')[0]
      if (aHasName && !bHasName) return -1
      if (!aHasName && bHasName) return 1
      return a.name.localeCompare(b.name)
    }
    if (tab === 'jwt') return [...jwtContacts].sort(alphaSort)
    if (tab === 'ryozan') return [...apiContacts, ...ryozanContacts].sort(alphaSort)
    // All: merge API contacts, booking clients, and imported contacts, dedupe by email/name.
    const map = new Map<string, ClientEntry>()
    for (const c of apiContacts) map.set(c.email || c.name, c)
    for (const c of bookingClients) map.set(c.email || c.name, c)
    for (const c of jwtContacts) if (!map.has(c.email)) map.set(c.email, c)
    for (const c of ryozanContacts) if (!map.has(c.email)) map.set(c.email, c)
    return Array.from(map.values()).sort(alphaSort)
  }, [tab, apiContacts, bookingClients, jwtContacts, ryozanContacts])

  const [visibleCount, setVisibleCount] = useState(50)

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setVisibleCount(50) }, [search, tab])

  const filtered = search
    ? allClients.filter(c =>
        c.name.toLowerCase().includes(search.toLowerCase()) ||
        c.email.toLowerCase().includes(search.toLowerCase()) ||
        (c.phone || '').toLowerCase().includes(search.toLowerCase()) ||
        c.source.toLowerCase().includes(search.toLowerCase())
      )
    : allClients

  const visibleClients = filtered.slice(0, visibleCount)
  const hasMore = filtered.length > visibleCount
  const clientKey = (client: ClientEntry) => client.email || client.name
  const selectedClient = filtered.find((client) => clientKey(client) === selectedClientKey) || visibleClients[0] || null
  const importedCount = jwtContacts.length + ryozanContacts.length
  const activeProducts = [
    { label: 'Courier', detail: selectedClient?.email ? 'message routing ready' : 'needs email' },
    { label: 'Huddl', detail: selectedClient?.bookings ? `${selectedClient.bookings} booking touchpoints` : 'no booking history' },
    { label: 'Hustl', detail: 'campaign audience candidate' },
    { label: 'Herald', detail: selectedClient?.email ? 'email eligible' : 'missing email' },
    { label: 'Guild', detail: selectedClient?.source === 'jwt' ? 'JWT audience' : 'Ryozan audience' },
  ]
  const intelligenceNotes = selectedClient
    ? [
        selectedClient.bookings > 1 ? 'Repeat client. Prioritize continuity and prior-session context.' : 'Single known booking. Build profile from next interaction.',
        selectedClient.phone ? 'Phone channel is available for time-sensitive follow-up.' : 'Phone missing. Use email or Courier until profile is enriched.',
        selectedClient.lastBooking ? `Last booking signal: ${new Date(selectedClient.lastBooking).toLocaleDateString('en-US')}.` : 'No dated booking signal yet.',
      ]
    : ['Select a contact to generate context.']

  const TABS: { key: ClientTab; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: allClients.length },
    { key: 'jwt', label: studioSettings.owner_name || 'Owner', count: jwtContacts.length },
    { key: 'ryozan', label: 'Ryozan', count: apiContacts.length + ryozanContacts.length },
  ]

  return (
    <div className="nexus-clients rolodex-shell">
      <header className="rolodex-hero">
        <div>
          <span className="rolodex-kicker">Universal contacts</span>
          <h1>Rolodex</h1>
          <p>Client memory for Courier, Huddl, Hustl, Herald, Guild, and future Ark products.</p>
        </div>
        <div className="rolodex-actions">
          <button className="btn btn-outline btn-sm" onClick={() => handleUpload(tab === 'ryozan' ? 'ryozan' : 'jwt')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            Import
          </button>
        </div>
      </header>

      <section className="rolodex-metrics" aria-label="Rolodex coverage">
        <div><span>Total</span><strong>{allClients.length}</strong></div>
        <div><span>Ryozan</span><strong>{apiContacts.length + ryozanContacts.length}</strong></div>
        <div><span>Booking-linked</span><strong>{bookingClients.length}</strong></div>
        <div><span>Imported</span><strong>{importedCount}</strong></div>
      </section>

      <div className="rolodex-command">
        <label className="rolodex-search">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input
            placeholder="Search name, email, phone, source..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </label>
        <div className="rolodex-tabs" aria-label="Rolodex source filters">
          {TABS.map(t => (
            <button
              key={t.key}
              className={tab === t.key ? 'active' : ''}
              onClick={() => setTab(t.key)}
            >
              {t.label}<span>{t.count}</span>
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state rolodex-empty">
          <div className="empty-state-icon">{Icons.users}</div>
          <p className="empty-state-text">
            {contactsLoading
              ? 'Loading Rolodex contacts...'
              : contactsError
                ? contactsError
                : tab === 'all' && allClients.length === 0
                  ? 'No private Ryozan contacts found yet'
                  : 'No matching contacts'}
          </p>
        </div>
      ) : (
        <section className="rolodex-workspace">
          <aside className="rolodex-list" aria-label="Rolodex contacts">
            {visibleClients.map((client) => {
              const key = clientKey(client)
              const isSelected = selectedClient ? clientKey(selectedClient) === key : false
              return (
                <button
                  key={key}
                  className={`rolodex-contact${isSelected ? ' selected' : ''}`}
                  onClick={() => setSelectedClientKey(key)}
                >
                  <span className="rolodex-avatar">{client.name.slice(0, 1).toUpperCase()}</span>
                  <span className="rolodex-contact-main">
                    <strong>{client.name}</strong>
                    <small>{client.email || client.phone || 'No channel on file'}</small>
                  </span>
                  <em>{client.bookings}</em>
                </button>
              )
            })}
            {hasMore && (
              <button className="rolodex-more" onClick={() => setVisibleCount(v => v + 50)}>
                Show more ({filtered.length - visibleCount} remaining)
              </button>
            )}
          </aside>

          <article className="rolodex-profile">
            {selectedClient && (
              <>
                <div className="rolodex-profile-head">
                  <span className="rolodex-avatar rolodex-avatar--large">{selectedClient.name.slice(0, 1).toUpperCase()}</span>
                  <div>
                    <span className="rolodex-kicker">Contact card</span>
                    <h2>{selectedClient.name}</h2>
                    <p>{selectedClient.status || 'known contact'} · {selectedClient.source}</p>
                  </div>
                </div>

                <div className="rolodex-field-grid">
                  <div><span>Email</span><strong>{selectedClient.email || 'Missing'}</strong></div>
                  <div><span>Phone</span><strong>{selectedClient.phone || 'Missing'}</strong></div>
                  <div><span>Bookings</span><strong>{selectedClient.bookings}</strong></div>
                  <div><span>Last signal</span><strong>{selectedClient.lastBooking ? new Date(selectedClient.lastBooking).toLocaleDateString('en-US') : 'None'}</strong></div>
                </div>

                <div className="rolodex-product-map">
                  <div className="rolodex-section-head">
                    <span>Connected products</span>
                    <b>Ark-ready</b>
                  </div>
                  {activeProducts.map((product) => (
                    <div key={product.label} className="rolodex-product-row">
                      <strong>{product.label}</strong>
                      <span>{product.detail}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </article>

          <aside className="rolodex-ai">
            <div className="rolodex-section-head">
              <span>Gala intelligence</span>
              <b>Augmented</b>
            </div>
            <p>Prepared for routed AI summaries, dedupe, next-best action, audience scoring, and cross-product memory.</p>
            <div className="rolodex-ai-list">
              {intelligenceNotes.map((note) => <span key={note}>{note}</span>)}
            </div>
          </aside>
        </section>
      )}
    </div>
  )
}

function HuddlTodayPanel({
  bookings,
  selectedDay,
  selectedDayEvents,
  weekDates,
  bookingMap,
  eventMap,
  calConnected,
  googleStatus,
  slotDiagnostics,
  schedulerContract,
  onSelectDay,
  onShiftWeek,
  onGoToday,
  onOpenBookings,
  onOpenFollowUp,
  onConnectGoogle,
  onOpenAppointmentTypes,
}: {
  bookings: Booking[]
  selectedDay: string
  selectedDayEvents: DayEvent[]
  weekDates: Date[]
  bookingMap: Map<string, Booking[]>
  eventMap: Map<string, CalendarEvent[]>
  calConnected: boolean
  googleStatus: GoogleStatus | null
  slotDiagnostics: SlotDiagnostics | null
  schedulerContract: SchedulerContract | null
  onSelectDay: (day: string, date: Date) => void
  onShiftWeek: (dir: -1 | 1) => void
  onGoToday: () => void
  onOpenBookings: () => void
  onOpenFollowUp: () => void
  onConnectGoogle: () => void
  onOpenAppointmentTypes: () => void
}) {
  const today = getTodayDateString()
  const selectedDate = new Date(`${selectedDay}T12:00:00`)
  const selectedDayBookings = bookings.filter((booking) => booking.scheduled_date === selectedDay)
  const pending = bookings.filter((booking) => booking.status === 'pending')
  const quoted = bookings.filter((booking) => booking.status === 'quoted')
  const confirmed = selectedDayBookings.filter((booking) => booking.status === 'confirmed')
  const unpaidDeposits = bookings.filter((booking) => booking.deposit_amount != null && !booking.deposit_paid)
  const unsignedReadiness = bookings.filter((booking) => booking.status === 'confirmed' && !booking.aftercare_ack)

  const nextSession = selectedDayBookings
    .filter((booking) => booking.scheduled_time)
    .sort((a, b) => (a.scheduled_time || '').localeCompare(b.scheduled_time || ''))[0]
  const selectedDayLabel = selectedDate.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })
  const weekLabel = `${weekDates[0].toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} - ${weekDates[6].toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
  const selectedDayBlocked = (slotDiagnostics?.blocked_slots || []).filter((slot) => slot.date === selectedDay)
  const blockedReasonCounts = selectedDayBlocked.reduce<Record<string, number>>((counts, slot) => {
    for (const reason of slot.reasons) counts[reason] = (counts[reason] || 0) + 1
    return counts
  }, {})
  const nextOpenSlots = (slotDiagnostics?.slots || []).slice(0, 4)
  const topBlockedReasons = Object.entries(blockedReasonCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
  const schedulerActions = new Set((schedulerContract?.actions || []).map((action) => action.name))
  const requiredSchedulerActions = ['list_slots', 'hold_slot', 'release_hold', 'submit_booking']
  const missingSchedulerActions = requiredSchedulerActions.filter((action) => !schedulerActions.has(action))
  const appointmentTypes = schedulerContract?.appointment_types || []
  const schedulerRules = schedulerContract?.rules || {}
  const readinessChecks = [
    {
      label: 'Google calendar',
      ok: Boolean(googleStatus?.connected && googleStatus.healthy && googleStatus.calendar_accessible),
      detail: googleStatus?.connected
        ? googleStatus.healthy
          ? googleStatus.calendar_summary || 'Calendar verified'
          : googleStatus.reason || 'Reconnect required'
        : 'Not connected',
    },
    {
      label: 'Open slot engine',
      ok: Boolean(slotDiagnostics && nextOpenSlots.length > 0),
      detail: slotDiagnostics
        ? nextOpenSlots.length > 0
          ? `${nextOpenSlots.length} sample openings`
          : 'No openings in diagnostic window'
        : 'Checking slots',
    },
    {
      label: 'Appointment types',
      ok: appointmentTypes.length > 0,
      detail: appointmentTypes.length > 0 ? `${appointmentTypes.length} active options` : 'No active options',
    },
    {
      label: 'Hold and submit flow',
      ok: missingSchedulerActions.length === 0,
      detail: missingSchedulerActions.length === 0 ? 'Hold, release, submit ready' : `Missing ${missingSchedulerActions.join(', ')}`,
    },
  ]
  const readyCheckCount = readinessChecks.filter((check) => check.ok).length
  const readinessScore = Math.round((readyCheckCount / readinessChecks.length) * 1000) / 10
  const firstOpenSlot = nextOpenSlots[0]
  const strongestAppointmentType = appointmentTypes[0]

  return (
    <div className="huddl-shell">
      <section className="huddl-hero">
        <div>
          <span className="huddl-kicker">Huddl today</span>
          <h1>Appointment command</h1>
          <p>Bookings, client readiness, calendar signal, and follow-up pressure in one room.</p>
        </div>
        <div className="huddl-hero__actions">
          <button className="btn btn-primary btn-sm" onClick={onOpenBookings}>{Icons.bookOpen} Booking queue</button>
        </div>
      </section>

      <section className="huddl-command-grid" aria-label="Huddl command board">
        <section className="huddl-panel huddl-dayboard huddl-command-calendar" aria-label="Huddl day selector">
          <div className="huddl-dayboard__head">
            <div>
              <span className="huddl-panel__eyebrow">Calendar signal</span>
              <strong>{weekLabel}</strong>
            </div>
            <div className="huddl-dayboard__controls">
              <button className="huddl-icon-btn" onClick={() => onShiftWeek(-1)} aria-label="Previous week">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>
              </button>
              <button className="huddl-today-btn" onClick={onGoToday}>Today</button>
              <button className="huddl-icon-btn" onClick={() => onShiftWeek(1)} aria-label="Next week">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>
              </button>
            </div>
          </div>

          <div className="huddl-date-strip">
            {weekDates.map((date) => {
              const dateStr = toDateStr(date)
              const bookingCount = (bookingMap.get(dateStr) || []).length
              const googleCount = (eventMap.get(dateStr) || []).length
              const isSelected = dateStr === selectedDay
              const isToday = dateStr === today
              return (
                <button
                  key={dateStr}
                  className={[
                    'huddl-date-tile',
                    isSelected ? 'selected' : '',
                    isToday ? 'today' : '',
                  ].filter(Boolean).join(' ')}
                  onClick={() => onSelectDay(dateStr, date)}
                >
                  <span>{WEEKDAY_LABELS[date.getDay()]}</span>
                  <strong>{date.getDate()}</strong>
                  <small>{date.toLocaleDateString('en-US', { month: 'short' })}</small>
                  {(bookingCount > 0 || googleCount > 0) && (
                    <em>
                      {bookingCount > 0 && <i className="huddl-date-dot huddl-date-dot--booking" />}
                      {googleCount > 0 && <i className="huddl-date-dot huddl-date-dot--google" />}
                    </em>
                  )}
                </button>
              )
            })}
          </div>
        </section>

        <aside className="huddl-command-sidebar" aria-label="Huddl status">
          <section className="huddl-stat-grid" aria-label="Huddl status counts">
            <button className="huddl-stat-card" onClick={onGoToday}>
              <span className="huddl-stat-label">{selectedDay === today ? 'Today' : 'Selected day'}</span>
              <strong>{selectedDayEvents.length}</strong>
              <small>{confirmed.length} confirmed</small>
            </button>
            <button className="huddl-stat-card" onClick={onOpenBookings}>
              <span className="huddl-stat-label">Booking inbox</span>
              <strong>{pending.length}</strong>
              <small>{quoted.length} quoted</small>
            </button>
            <button className="huddl-stat-card" onClick={onOpenFollowUp}>
              <span className="huddl-stat-label">Follow-up</span>
              <strong>{unpaidDeposits.length + unsignedReadiness.length}</strong>
              <small>deposits and readiness</small>
            </button>
          </section>

          <div className="huddl-panel huddl-command-next">
            <div className="huddl-panel__head">
              <span>Next session</span>
              <button className="huddl-link" onClick={onOpenBookings}>Review bookings</button>
            </div>
            {nextSession ? (
              <div className="huddl-feature-card">
                <span className="huddl-feature-time">{formatTime(nextSession.scheduled_time)}</span>
                <strong>{nextSession.resolved_name || nextSession.client_name || 'Walk-in'}</strong>
                <p>{nextSession.description || 'No design description yet.'}</p>
                <div className="huddl-chip-row">
                  {nextSession.placement && <span>{nextSession.placement}</span>}
                  {nextSession.deposit_amount != null && <span>{nextSession.deposit_paid ? 'Deposit paid' : 'Deposit due'}</span>}
                  <span>{nextSession.status}</span>
                </div>
              </div>
            ) : (
              <div className="huddl-empty">No next session for this day.</div>
            )}
          </div>
        </aside>

        <div className="huddl-panel huddl-readiness-panel huddl-command-readiness">
          <div className="huddl-panel__head">
            <span>Booking readiness</span>
            <b>{readinessScore.toFixed(1)}%</b>
          </div>
          <div className="huddl-readiness-score">
            <strong>{readinessScore >= 96.7 ? 'Ready for live booking' : 'Needs attention'}</strong>
            <small>
              {firstOpenSlot
                ? `Next public slot: ${firstOpenSlot.label}`
                : 'No public slot available in the current diagnostic window.'}
            </small>
          </div>
          <div className="huddl-readiness-checks">
            {readinessChecks.map((check) => (
              <div key={check.label} className={`huddl-readiness-check${check.ok ? ' ok' : ' warn'}`}>
                <span>{check.ok ? 'OK' : 'Fix'}</span>
                <div>
                  <strong>{check.label}</strong>
                  <small>{check.detail}</small>
                </div>
              </div>
            ))}
          </div>
          <div className="huddl-chip-row huddl-rule-row">
            <span>{Number(schedulerRules.slot_interval_minutes ?? slotDiagnostics?.slot_interval_minutes ?? 30)} min grid</span>
            <span>{Number(schedulerRules.buffer_minutes ?? slotDiagnostics?.buffer_minutes ?? 0)} min buffer</span>
            <span>{Number(schedulerRules.minimum_notice_hours ?? slotDiagnostics?.minimum_notice_hours ?? 0)}h notice</span>
            {strongestAppointmentType && <span>{strongestAppointmentType.label}</span>}
          </div>
          <div className="huddl-readiness-actions">
            {(!googleStatus?.connected || googleStatus.reconnect_required || !googleStatus.healthy) && (
              <button type="button" className="huddl-readiness-action" onClick={onConnectGoogle}>
                {googleStatus?.connected ? 'Reconnect Google' : 'Connect Google'}
              </button>
            )}
            {appointmentTypes.length === 0 && (
              <button type="button" className="huddl-readiness-action" onClick={onOpenAppointmentTypes}>
                Set Appointment Types
              </button>
            )}
          </div>
        </div>

        <div className="huddl-panel huddl-command-availability">
          <div className="huddl-panel__head">
            <span>Availability diagnostics</span>
            <b>{slotDiagnostics?.calendar_connected ? 'Calendar verified' : 'Shop fallback'}</b>
          </div>
          {!slotDiagnostics ? (
            <div className="huddl-empty">Checking slot health...</div>
          ) : (
            <div className="huddl-list">
              {nextOpenSlots.length === 0 ? (
                <div className="huddl-empty">No open slots found in the diagnostic window.</div>
              ) : nextOpenSlots.map((slot) => (
                <div key={`${slot.date}-${slot.time}`} className="huddl-row">
                  <span className="huddl-row__pip booking" />
                  <span className="huddl-row__time">{formatTime(slot.time)}</span>
                  <div className="huddl-row__main">
                    <strong>{slot.label}</strong>
                    <small>{slot.duration_minutes} min - {slot.confidence}</small>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="huddl-panel huddl-command-blocked">
          <div className="huddl-panel__head">
            <span>Blocked on {selectedDayLabel}</span>
            <b>{selectedDayBlocked.length}</b>
          </div>
          {!slotDiagnostics ? (
            <div className="huddl-empty">Waiting for diagnostics.</div>
          ) : selectedDayBlocked.length === 0 ? (
            <div className="huddl-empty">No blocked slots on this day.</div>
          ) : (
            <div className="huddl-list">
              {topBlockedReasons.map(([reason, count]) => (
                <div key={reason} className="huddl-row">
                  <span className="huddl-row__pip" />
                  <span className="huddl-row__time">{count}</span>
                  <div className="huddl-row__main">
                    <strong>{slotReasonLabel(reason)}</strong>
                    <small>{selectedDayBlocked.filter((slot) => slot.reasons.includes(reason)).slice(0, 2).map((slot) => formatTime(slot.time)).join(', ')}</small>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="huddl-panel huddl-command-sessions">
          <div className="huddl-panel__head">
            <span>Sessions for {selectedDayLabel}</span>
            {calConnected && <b>Google linked</b>}
          </div>
          {selectedDayEvents.length === 0 ? (
            <div className="huddl-empty">Nothing scheduled for this day.</div>
          ) : (
            <div className="huddl-list">
              {selectedDayEvents.slice(0, 6).map((event, index) => (
                <div key={`${event.time}-${event.title}-${index}`} className="huddl-row">
                  <span className={`huddl-row__pip${event.isBooking ? ' booking' : ''}`} />
                  <span className="huddl-row__time">{event.time}</span>
                  <div className="huddl-row__main">
                    <strong>{event.title}</strong>
                    {event.meta && <small>{event.meta}</small>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

function HuddlFollowUpPanel({
  bookings,
  onOpenBookings,
  onOpenDeposits,
}: {
  bookings: Booking[]
  onOpenBookings: () => void
  onOpenDeposits: () => void
}) {
  const closedDepositStatuses = new Set(['cancelled', 'canceled', 'declined', 'expired', 'completed', 'no_show'])
  const needsDeposit = bookings.filter((booking) => (
    booking.deposit_amount != null
    && !booking.deposit_paid
    && !closedDepositStatuses.has(booking.status.toLowerCase())
  ))
  const stalePending = bookings.filter((booking) => booking.status === 'pending')
  const readiness = bookings.filter((booking) => booking.status === 'confirmed' && !booking.aftercare_ack)
  const completedAftercare = bookings.filter((booking) => booking.status === 'completed' && !booking.aftercare_ack)

  const lanes = [
    { id: 'deposits', title: 'Deposits', items: needsDeposit, empty: 'No unpaid deposits.' },
    { id: 'pending', title: 'Stale leads', items: stalePending, empty: 'No pending leads waiting.' },
    { id: 'readiness', title: 'Readiness', items: readiness, empty: 'Confirmed sessions look ready.' },
    { id: 'aftercare', title: 'Aftercare', items: completedAftercare, empty: 'No aftercare follow-up queued.' },
  ]

  return (
    <div className="huddl-shell">
      <section className="huddl-hero huddl-hero--compact">
        <div>
          <span className="huddl-kicker">Huddl follow-up</span>
          <h1>Nothing falls through</h1>
          <p>Deposits, client readiness, unsigned acknowledgements, and aftercare touchpoints.</p>
        </div>
        <div className="huddl-hero__actions">
          <button className="btn btn-primary btn-sm" onClick={onOpenBookings}>{Icons.bookOpen} Booking queue</button>
          <button className="btn btn-outline btn-sm" onClick={onOpenDeposits}>{Icons.arrowRight} Deposit activity</button>
        </div>
      </section>

      <section className="huddl-follow-grid">
        {lanes.map((lane) => (
          <div key={lane.id} className="huddl-panel huddl-follow-lane">
            <div className="huddl-panel__head">
              <span>{lane.title}</span>
              <b>{lane.items.length}</b>
            </div>
            {lane.items.length === 0 ? (
              <div className="huddl-empty">{lane.empty}</div>
            ) : (
              <div className="huddl-list">
                {lane.items.slice(0, 8).map((booking) => (
                <button
                  key={booking.id}
                  className="huddl-row huddl-row--button"
                  onClick={lane.id === 'deposits' ? onOpenDeposits : onOpenBookings}
                >
                  <span className="huddl-row__pip booking" />
                  <span className="huddl-row__time">{booking.scheduled_date || 'Unscheduled'}</span>
                  <div className="huddl-row__main">
                      <strong>{booking.resolved_name || booking.client_name || booking.guest_name || 'Guest'}</strong>
                      <small>{booking.description || booking.status}</small>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </section>
    </div>
  )
}


/* ── Main Component ────────────────────────────────── */

export default function HuddleLayout() {
  const navigate = useNavigate()
  const userRole = useAuthStore((s) => s.user?.role)
  const effectiveRole = useAuthStore((s) => s.effectiveRole)
  const isManager = ['assistant', 'admin', 'founder'].includes(effectiveRole)
  // Read ?charm= query param to auto-select a panel (e.g. from notification clicks)
  const charmFromUrl = useMemo(() => {
    const params = new URLSearchParams(window.location.search)
    const raw = params.get('charm')
    if (!raw) return null
    // Normalize aliases to valid panel IDs
    const CHARM_MAP: Record<string, HuddlePanel> = { bookings: 'inquiries', booking: 'inquiries', calendar: 'today' }
    return (CHARM_MAP[raw] || raw) as HuddlePanel
  }, [])
  const [activePanel, setActivePanel] = useState<HuddlePanel>(charmFromUrl || 'today')
  const [bookings, setBookings] = useState<Booking[]>([])

  // Calendar state
  const [calDate, setCalDate] = useState(() => new Date())
  const [selectedDay, setSelectedDay] = useState(() => getTodayDateString())

  // Google Calendar
  const [calConnected, setCalConnected] = useState(false)
  const [googleStatus, setGoogleStatus] = useState<GoogleStatus | null>(null)
  const [calEvents, setCalEvents] = useState<CalendarEvent[]>([])
  const [slotDiagnostics, setSlotDiagnostics] = useState<SlotDiagnostics | null>(null)
  const [schedulerContract, setSchedulerContract] = useState<SchedulerContract | null>(null)

  const connectGoogle = async () => {
    const { authorization_url } = await googleAuth.getConnectUrl()
    window.location.href = authorization_url
  }

  useEffect(() => {
    window.dispatchEvent(new CustomEvent('ryozan:nexus-toolbar-register', {
      detail: {
        items: TOOL_BELT.map((tool) => ({
          id: tool.id,
          label: tool.label,
          icon: tool.icon,
          active: activePanel === tool.id,
          onClick: () => setActivePanel(tool.id),
        })),
      },
    }))

    return () => {
      window.dispatchEvent(new CustomEvent('ryozan:nexus-toolbar-clear'))
    }
  }, [activePanel])

  // The studio's default artist (DEFAULT_ARTIST_SLUG) anchors the manager view:
  // their bookings, scheduler contract and slot diagnostics. None configured
  // means the manager sees their own bookings and no single-artist diagnostics.
  const { settings: studioSettings, loaded: studioSettingsLoaded } = useStudioSettings()
  const leadArtistSlug = studioSettings.default_artist_slug

  // Load bookings + Google Calendar status
  useEffect(() => {
    if (!studioSettingsLoaded) return
    const load = async () => {
      try {
        // Admin/assistant see the lead artist's bookings
        let bookParams: Parameters<typeof bookingsApi.list>[0] = { limit: 100 }
        if (leadArtistSlug && isManager && effectiveRole !== 'founder' && effectiveRole !== 'artist') {
          try {
            const artistList = await artistsApi.list()
            const leadArtist = artistList.find((a: { slug: string }) => a.slug === leadArtistSlug)
            if (leadArtist) bookParams = { limit: 100, for_artist: leadArtist.id }
          } catch { /* fallback to default */ }
        }
        const [bookingData, gcalStatus, contractData] = await Promise.allSettled([
          bookingsApi.list(bookParams),
          googleAuth.getStatus(),
          leadArtistSlug ? calendarApi.getSchedulerContract(leadArtistSlug) : Promise.resolve(null),
        ])
        if (bookingData.status === 'fulfilled') setBookings(bookingData.value.bookings || [])
        if (gcalStatus.status === 'fulfilled') setGoogleStatus(gcalStatus.value)
        if (contractData.status === 'fulfilled') setSchedulerContract(contractData.value)
        if (leadArtistSlug) {
          const diagnosticStart = new Date()
          const diagnosticEnd = new Date()
          diagnosticEnd.setDate(diagnosticStart.getDate() + 14)
          calendarApi.getArtistSlots(leadArtistSlug, {
            start: toDateStr(diagnosticStart),
            end: toDateStr(diagnosticEnd),
            limit: 12,
            includeBlocked: true,
            blockedLimit: 180,
          })
            .then((diagnostics) => setSlotDiagnostics(diagnostics))
            .catch(() => setSlotDiagnostics(null))
        } else {
          setSlotDiagnostics(null)
        }
        if (gcalStatus.status === 'fulfilled' && gcalStatus.value.connected) {
          setCalConnected(true)
          try {
            const res = await calendarApi.getUpcoming(60)
            setCalEvents(res.events || [])
          } catch { /* gcal fetch failed silently */ }
        } else {
          setCalConnected(false)
        }
      } catch {
        // silent
      }
    }
    load()
  }, [effectiveRole, isManager, leadArtistSlug, studioSettingsLoaded])

  // Calendar data
  const calBookingMap = useMemo(() => bookingsByDate(bookings), [bookings])

  const navigateWeek = (dir: -1 | 1) => {
    setCalDate((prev) => {
      const d = new Date(prev)
      d.setDate(d.getDate() + dir * 7)
      return d
    })
  }
  const goToday = () => {
    const now = new Date()
    setCalDate(now)
    setSelectedDay(getTodayDateString())
  }

  // Google Calendar events indexed by date string
  const calEventsByDate = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>()
    for (const ev of calEvents) {
      const ds = ev.start.slice(0, 10)
      const arr = map.get(ds) || []
      arr.push(ev)
      map.set(ds, arr)
    }
    return map
  }, [calEvents])

  // Combined events for selected day (bookings + gcal)
  const selectedDayEvents = useMemo((): DayEvent[] => {
    const bookingEvs: DayEvent[] = (calBookingMap.get(selectedDay) || [])
      .sort((a, b) => (a.scheduled_time || '').localeCompare(b.scheduled_time || ''))
      .map(b => ({
        time: formatTime(b.scheduled_time),
        title: b.resolved_name || b.client_name || 'Walk-in',
        meta: b.description || null,
        isBooking: true,
      }))
    const gcalEvs: DayEvent[] = (calEventsByDate.get(selectedDay) || [])
      .map(ev => ({
        time: ev.start.length > 10
          ? new Date(ev.start).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
          : 'All day',
        title: ev.summary,
        meta: ev.location || null,
        isBooking: false,
      }))
    return [...bookingEvs, ...gcalEvs].sort((a, b) => a.time.localeCompare(b.time))
  }, [selectedDay, calBookingMap, calEventsByDate])
  const weekDates = useMemo(() => getWeekDates(calDate), [calDate])

  return (
    <div className="dashboard-panel home-panel nexus">
      {/* ── Body: Viewport + Tool Belt (same as Nexus) ── */}
      <div className="nexus-body">
        <div className="nexus-viewport card">
          {/* Today Panel */}
          {activePanel === 'today' && (
            <HuddlTodayPanel
              bookings={bookings}
              selectedDay={selectedDay}
              selectedDayEvents={selectedDayEvents}
              weekDates={weekDates}
              bookingMap={calBookingMap}
              eventMap={calEventsByDate}
              calConnected={calConnected}
              googleStatus={googleStatus}
              slotDiagnostics={slotDiagnostics}
              schedulerContract={schedulerContract}
              onSelectDay={(day, date) => {
                setSelectedDay(day)
                setCalDate(date)
              }}
              onShiftWeek={navigateWeek}
              onGoToday={goToday}
              onOpenBookings={() => setActivePanel('inquiries')}
              onOpenFollowUp={() => setActivePanel('followup')}
              onConnectGoogle={() => { void connectGoogle() }}
              onOpenAppointmentTypes={() => navigate('/dashboard/artist-profile?tab=account&section=appointment-types')}
            />
          )}

          {/* Inquiries Panel (BookingsManager) */}
          {activePanel === 'inquiries' && (
            <Suspense fallback={<div className="nexus-panel-loading">Loading inquiries...</div>}>
              <BookingsManager />
            </Suspense>
          )}

          {/* Clients Panel */}
          {activePanel === 'clients' && <ClientsPanel bookings={bookings} />}

          {/* Follow-up Panel */}
          {activePanel === 'followup' && (
            <HuddlFollowUpPanel
              bookings={bookings}
              onOpenBookings={() => setActivePanel('inquiries')}
              onOpenDeposits={() => {
                window.location.href = userRole === 'artist'
                  ? '/dashboard?portal=hub&workflow=deposits'
                  : '/dashboard/hustle?stash=econ&page=command&focus=deposits'
              }}
            />
          )}
        </div>

      </div>
    </div>
  )
}
