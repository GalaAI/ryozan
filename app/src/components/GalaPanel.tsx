/**
 * GalaPanel -- Persistent AI chat panel for the Nexus split terminal.
 *
 * Right-side panel in the Canvas+Gala layout. Handles:
 * - Navigation commands client-side ("show bookings" -> useNavigate)
 * - Freeform chat via POST /api/v1/gala/chat
 * - Prod and local calls route through the API's LiteLLM-backed chat service.
 *
 * Never exposes GalaX internals or HiveMind.
 * Scoped to: tattoo, business, art, marketing, Ryozan operations.
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/auth'
import { getToken, API_BASE, type GalaActionIntent, type GalaChatComponent } from '../lib/api'
import { useSwipeDismiss } from '../hooks/useSwipeDismiss'
import { MessageResponse } from './ai-elements/message'

interface GalaMessage {
  id: string
  role: 'gala' | 'user'
  text: string
  timestamp: number
  components?: GalaChatComponent[]
  receipts?: Array<Record<string, unknown>>
  selectedItems?: string[]
  failure?: {
    retryText: string
    agentId: GalaAgentId
    actionIntent?: GalaActionIntent
  }
}

interface QuickPrompt {
  label: string
  message: string
  actionIntent?: GalaActionIntent
}

type CourierWorkspaceTab = 'thread' | 'notes' | 'links' | 'drafts'

interface CourierWorkspaceState {
  active: boolean
  threadName?: string
  participantCount?: number
  bookingContext?: {
    id?: string
    reference_code?: string | null
    status?: string | null
    artist_name?: string | null
    description?: string | null
    booking_url?: string | null
    client_email?: string | null
    client_phone?: string | null
  } | null
  threadSummary?: string
  threadSignals?: string[]
  threadSuggestions?: string[]
  threadChecklist?: Array<{ label: string; done: boolean }>
  workspaceNote?: string
}

/* Rich Component Renderers */

function BookingListComponent({ data, onSelect }: { data: Record<string, unknown>; onSelect?: (ids: string[]) => void }) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const bookings = (data.bookings || []) as Record<string, unknown>[]

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      onSelect?.(Array.from(next))
      return next
    })
  }

  if (!bookings.length) return null

  return (
    <div className="gala-component gala-booking-list">
      {bookings.map((b) => (
        <div
          key={String(b.id)}
          className={`gala-booking-card${selected.has(String(b.id)) ? ' selected' : ''}`}
          onClick={() => data.selectable && toggle(String(b.id))}
          style={{ cursor: data.selectable ? 'pointer' : 'default' }}
        >
          <div className="gala-booking-card-top">
            <span className="gala-booking-name">{String(b.client_name || b.guest_name || '')}</span>
            <span className={`gala-booking-status gala-status-${String(b.status)}`}>{String(b.status)}</span>
          </div>
          <span className="gala-booking-desc">{String(b.description || '')}</span>
          <div className="gala-booking-meta">
            <span>{String(b.reference_code || '')}</span>
            {Boolean(b.created_at) && <span>{new Date(String(b.created_at)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</span>}
          </div>
          {Boolean(data.selectable) && selected.has(String(b.id)) && (
            <span className="gala-booking-check">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

function StatCardComponent({ data }: { data: Record<string, unknown> }) {
  const stats = Object.entries(data).filter(([k]) => k !== 'type')
  return (
    <div className="gala-component gala-stat-grid">
      {stats.map(([key, value]) => (
        <div key={key} className="gala-stat-item">
          <span className="gala-stat-value">{String(value)}</span>
          <span className="gala-stat-label">{key.replace(/_/g, ' ')}</span>
        </div>
      ))}
    </div>
  )
}

function ScheduleComponent({ data }: { data: Record<string, unknown> }) {
  const events = (data.events || []) as Record<string, unknown>[]
  if (!events.length) return null
  return (
    <div className="gala-component gala-schedule">
      {events.map((e, i: number) => (
        <div key={i} className="gala-schedule-item">
          <span className="gala-schedule-date">{String(e.date || '')} {String(e.time || '')}</span>
          <span className="gala-schedule-client">{String(e.client || '')}</span>
          <span className="gala-schedule-desc">{String(e.description || '')}</span>
        </div>
      ))}
    </div>
  )
}

function WaiverStatusComponent({ data }: { data: Record<string, unknown> }) {
  const waivers = (data.waivers || []) as Record<string, unknown>[]
  if (!waivers.length) return null
  return (
    <div className="gala-component gala-waiver-list">
      {waivers.map((w, i: number) => (
        <div key={i} className="gala-waiver-item">
          <span className="gala-waiver-title">{String(w.title || '')}</span>
          <span className={`gala-waiver-status gala-status-${String(w.status)}`}>{String(w.status || '')}</span>
        </div>
      ))}
    </div>
  )
}

function WebResultsComponent({ data }: { data: Record<string, unknown> }) {
  const results = (data.results || []) as Record<string, unknown>[]
  if (!results.length) return null
  return (
    <div className="gala-component gala-web-results">
      <span className="gala-web-query">Tavily web search: {String(data.query || '')}</span>
      {results.map((r, i: number) => (
        <a key={i} className="gala-web-item" href={String(r.url || '')} target="_blank" rel="noopener noreferrer">
          <span className="gala-web-title">[{i + 1}] {String(r.title || r.url || '')}</span>
          <span className="gala-web-url">{String(r.url || '')}</span>
        </a>
      ))}
    </div>
  )
}

function ClientCardComponent({ data }: { data: Record<string, unknown> }) {
  const name = String(data.name || data.client_name || 'Client')
  const details = Object.entries(data).filter(([key]) => !['name', 'client_name', 'id'].includes(key))
  return (
    <div className="gala-component gala-client-card">
      <strong>{name}</strong>
      {details.slice(0, 6).map(([key, value]) => (
        <span key={key}>{key.replace(/_/g, ' ')}: {String(value ?? '')}</span>
      ))}
    </div>
  )
}

function UnavailableActionsComponent({ data }: { data: Record<string, unknown> }) {
  const actions = Array.isArray(data.actions) ? data.actions : []
  return (
    <div className="gala-component gala-action-buttons">
      {actions.map((action, index) => {
        const record = typeof action === 'object' && action !== null ? action as Record<string, unknown> : {}
        return <button key={index} type="button" disabled>{String(record.label || record.name || action || 'Action')} unavailable</button>
      })}
      <small>Structured actions require a verified callback before they can run.</small>
    </div>
  )
}

export function GalaComponentRenderer({ component, onSelect }: { component: GalaChatComponent; onSelect?: (ids: string[]) => void }) {
  switch (component.type) {
    case 'booking-list': return <BookingListComponent data={component.data} onSelect={onSelect} />
    case 'client-card': return <ClientCardComponent data={component.data} />
    case 'stat-card': return <StatCardComponent data={component.data} />
    case 'schedule-view': return <ScheduleComponent data={component.data} />
    case 'waiver-status': return <WaiverStatusComponent data={component.data} />
    case 'action-buttons': return <UnavailableActionsComponent data={component.data} />
    case 'web-results': return <WebResultsComponent data={component.data} />
    default: return null
  }
}

const Icons = {
  send: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
    </svg>
  ),
  bot: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/>
    </svg>
  ),
  close: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>
  ),
}

/* Navigation command map */

import { ROUTES } from '../lib/routes'

const NAV_COMMANDS: Record<string, string> = {
  bookings: ROUTES.BOOKINGS,
  books: ROUTES.BOOKINGS,
  schedule: ROUTES.SCHEDULE,
  calendar: ROUTES.SCHEDULE,
  clients: ROUTES.CLIENTS,
  contacts: ROUTES.CLIENTS,
  documents: ROUTES.ASSETS,
  docs: ROUTES.ASSETS,
  messages: ROUTES.COURIER,
  inbox: ROUTES.COURIER,
  profile: ROUTES.PROFILE,
  portfolio: ROUTES.HUSTLE,
  gallery: `${ROUTES.ARTIST_PROFILE}?tab=gallery`,
  hustle: ROUTES.HUSTLE,
  flash: ROUTES.HUSTLE,
  shop: ROUTES.HUSTLE,
  huddle: ROUTES.HUDDLE,
  courier: ROUTES.COURIER,
  guild: ROUTES.GUILD,
  vault: ROUTES.VAULT,
  home: ROUTES.DASHBOARD,
  nexus: ROUTES.DASHBOARD,
}

const NAV_PREFIXES = ['show', 'open', 'go to', 'navigate to', 'take me to', 'pull up', 'load']

function matchNavCommand(text: string): { path: string; label: string } | null {
  const lower = text.toLowerCase().trim()

  // Direct keyword match
  for (const [keyword, path] of Object.entries(NAV_COMMANDS)) {
    if (lower === keyword) return { path, label: keyword }
  }

  // Prefix + keyword match: "show bookings", "open my schedule"
  for (const prefix of NAV_PREFIXES) {
    if (!lower.startsWith(prefix)) continue
    const rest = lower.slice(prefix.length).replace(/^(\s+my\s+|\s+the\s+|\s+)/, '')
    for (const [keyword, path] of Object.entries(NAV_COMMANDS)) {
      if (rest.includes(keyword)) return { path, label: keyword }
    }
  }

  // Fuzzy natural language
  if (lower.includes('appointment') || lower.includes('my book')) return { path: NAV_COMMANDS.bookings, label: 'bookings' }
  if (lower.includes('my schedule') || lower.includes('when am i')) return { path: NAV_COMMANDS.schedule, label: 'schedule' }
  if (lower.includes('my client') || lower.includes('contact')) return { path: NAV_COMMANDS.clients, label: 'clients' }
  if (lower.includes('my message') || lower.includes('unread')) return { path: NAV_COMMANDS.messages, label: 'messages' }
  if (lower.includes('my portfolio') || lower.includes('my gallery')) return { path: NAV_COMMANDS.portfolio, label: 'portfolio' }

  return null
}

function makeId() {
  return Math.random().toString(36).slice(2, 9)
}

function CourierWorkspaceModule({
  data,
  activeTab,
  onTabChange,
  onNavigate,
}: {
  data: CourierWorkspaceState
  activeTab: CourierWorkspaceTab
  onTabChange: (tab: CourierWorkspaceTab) => void
  onNavigate: (path: string) => void
}) {
  const booking = data.bookingContext
  const tabs: Array<{ id: CourierWorkspaceTab; label: string }> = [
    { id: 'thread', label: 'Thread' },
    { id: 'notes', label: 'Notes' },
    { id: 'links', label: 'Links' },
    { id: 'drafts', label: 'Drafts' },
  ]

  const sendSuggestion = (text: string) => {
    window.dispatchEvent(new CustomEvent('ryozan:courier-use-suggestion', { detail: { text } }))
  }

  const updateNote = (note: string) => {
    window.dispatchEvent(new CustomEvent('ryozan:courier-workspace-note-change', { detail: { note } }))
  }

  return (
    <section className="gala-courier-module" aria-label="Courier workspace">
      <div className="gala-courier-tabs" role="tablist" aria-label="Courier context tabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`gala-courier-tab${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="gala-courier-content">
        {activeTab === 'thread' && (
          <>
            <div className="gala-courier-brief">
              <div>
                <span className="gala-courier-kicker">{booking?.reference_code || 'Courier thread'}</span>
                <h3>{data.threadName || 'Active thread'}</h3>
              </div>
              {booking?.status && <span className={`gala-booking-status gala-status-${booking.status}`}>{booking.status}</span>}
            </div>
            <p className="gala-courier-summary">{data.threadSummary || 'No active Courier context yet.'}</p>
            <div className="gala-courier-list">
              {(data.threadSignals?.length ? data.threadSignals : ['No extra thread signals yet.']).map((signal) => (
                <div key={signal} className="gala-courier-row">{signal}</div>
              ))}
            </div>
          </>
        )}

        {activeTab === 'notes' && (
          <>
            <textarea
              className="gala-courier-note"
              value={data.workspaceNote || ''}
              onChange={(event) => updateNote(event.target.value)}
              placeholder="Capture internal notes, summary bullets, or next-step reminders."
              rows={7}
            />
            <button
              type="button"
              className="gala-courier-action"
              onClick={() => window.dispatchEvent(new CustomEvent('ryozan:courier-save-note-context'))}
            >
              Save note context
            </button>
            <div className="gala-courier-checklist">
              {(data.threadChecklist || []).map((item) => (
                <div key={item.label} className={`gala-courier-check${item.done ? ' is-done' : ''}`}>
                  <span />
                  {item.label}
                </div>
              ))}
            </div>
          </>
        )}

        {activeTab === 'links' && (
          <>
            <div className="gala-courier-brief">
              <div>
                <span className="gala-courier-kicker">Linked context</span>
                <h3>Thread Links</h3>
              </div>
            </div>
            <p className="gala-courier-summary">
              Linked booking, client, and channel context for the active Courier thread.
            </p>
            <div className="gala-courier-actions">
              {booking?.booking_url && (
                <button type="button" className="gala-courier-action" onClick={() => onNavigate(booking.booking_url || '')}>
                  Open booking workspace
                </button>
              )}
              {booking?.client_email && (
                <a href={`mailto:${booking.client_email}`} className="gala-courier-action">Open email channel</a>
              )}
              {booking?.client_phone && (
                <a href={`tel:${booking.client_phone}`} className="gala-courier-action">Open call channel</a>
              )}
              {!booking?.booking_url && !booking?.client_email && !booking?.client_phone && (
                <div className="gala-courier-row">No linked surfaces are available for this thread yet.</div>
              )}
            </div>
          </>
        )}

        {activeTab === 'drafts' && (
          <div className="gala-courier-actions">
            {(data.threadSuggestions || []).map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="gala-courier-action"
                onClick={() => sendSuggestion(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

/* Component */

interface GalaPanelProps {
  className?: string
  onClose?: () => void
  onCollapse?: () => void
  composeRequest?: {
    id: number
    agentId?: GalaAgentId
    message: string
  } | null
}

type GalaAgentId = 'gala' | 'spyder' | 'smoku' | 'xar'

interface GalaAgentProfile {
  id: GalaAgentId
  name: string
  title: string
  scope: string
  instruction: string
  researchPlan: string[]
}

const AGENT_PROFILES: Record<GalaAgentId, GalaAgentProfile> = {
  gala: {
    id: 'gala',
    name: 'Gala',
    title: 'Ryozan operations agent',
    scope: 'General Ryozan dashboard operations across Huddle, Courier, Guild, Vault, Herald, and admin workflows.',
    instruction: 'Operate as the Ryozan operations agent. Keep context across dashboard pages, route work to the correct app, and ask for missing data before recommending write actions.',
    researchPlan: [
      'Audit current workflow intent against Ryozan role, route, and available API contracts.',
      'Check whether the task belongs in Huddle, Courier, Hustle, Vault, Herald, Guild, or admin settings.',
      'Prefer safe drafts, checklists, and staff approval before mutation.',
    ],
  },
  spyder: {
    id: 'spyder',
    name: 'Sōkō',
    title: 'Ryozan staff operations agent',
    scope: 'The signed-in artist or assistant account, its clients, schedule, follow-up, and shop workflow.',
    instruction: 'Operate as Spyder for the authenticated staff account. Keep every lookup and action inside that account role and use current Ryozan records instead of assumed client or schedule state.',
    researchPlan: [
      'Identify the signed-in staff role and current page before choosing a workflow.',
      'Use current bookings, schedule, waivers, and client records when the available tools support them.',
      'State the exact blocker when a requested write or external action lacks its implemented gate.',
    ],
  },
  smoku: {
    id: 'smoku',
    name: 'Rochishin',
    title: 'Ryozan collector guide',
    scope: 'The signed-in collector account, its bookings, preparation, aftercare, waivers, and tattoo education.',
    instruction: 'Operate as Smoku for the authenticated collector account. Use only that collector\'s authorized records, give a concrete next step, and never invent booking, artist, or aftercare state.',
    researchPlan: [
      'Ground the response in the signed-in collector account and current portal page.',
      'Use current booking, schedule, waiver, and aftercare state when the available tools support it.',
      'Route shop decisions or unavailable actions to the correct human or booking workflow without claiming they already happened.',
    ],
  },
  xar: {
    id: 'xar',
    name: 'Shishin',
    title: 'Hustle commerce agent',
    scope: 'Hustle only: tattoo flash inventory, creator storefronts, ecommerce, pricing, listings, conversion, marketing, SEO, AISO, advertising, and sales performance.',
    instruction: 'Operate as Xar, the Hustle agent. Only answer Hustle tasks or closely related tattoo commerce work. Do not make up missing product, pricing, legal, payment, or artist data. Ask for exact missing fields before write actions.',
    researchPlan: [
      'Review current tattoo, ecommerce, creator commerce, SEO, AISO, advertising, and conversion best practices before strategy recommendations.',
      'Benchmark pricing logic against size, placement, complexity, artist demand, deposit policy, and local market fit.',
      'Audit each listing for claim accuracy, clear buying intent, booking handoff, payment readiness, image quality, and publish blockers.',
      'Recommend experiments that can be measured through views, saves, claims, bookings, conversion rate, revenue, and repeat client behavior.',
    ],
  },
}

function agentForContext(pathname: string, role?: string): GalaAgentId {
  if (pathname === ROUTES.HUSTLE || pathname === '/dashboard/flash') return 'xar'
  if (role === 'artist' || role === 'assistant') return 'spyder'
  if (role === 'collector') return 'smoku'
  return 'gala'
}

const QUICK_PROMPTS: QuickPrompt[] = [
  { label: "Today's schedule", message: "What's on my schedule today?", actionIntent: 'schedule.read' },
  { label: "Pending requests", message: "Show me all pending booking requests.", actionIntent: 'bookings.pending.read' },
  { label: "Needs follow-up", message: "What bookings need follow-up?", actionIntent: 'bookings.stale.read' },
  { label: "Business stats", message: "Give me a quick business overview.", actionIntent: 'business_stats.read' },
]

const HUSTLE_QUICK_PROMPTS: QuickPrompt[] = [
  { label: 'Price flash', message: 'Help me price the current flash inventory for this creator. Ask for size, placement, complexity, and deposit rules if needed.' },
  { label: 'Listing copy', message: 'Draft polished storefront listing copy for the selected flash or product. Keep it accurate and ask for missing details.' },
  { label: 'Segment sheet', message: 'Walk me through segmenting this flash sheet and identifying what needs review before publishing.' },
  { label: 'Publish check', message: 'Audit this Hustle creator page for missing profile, inventory, booking, payment, and storage readiness.' },
]

const STAFF_QUICK_PROMPTS: QuickPrompt[] = [
  { label: "Today's schedule", message: "What's on my schedule today?", actionIntent: 'schedule.read' },
  { label: 'Pending requests', message: 'Show me my pending booking requests.', actionIntent: 'bookings.pending.read' },
  { label: 'Needs follow-up', message: 'Which of my bookings need follow-up?', actionIntent: 'bookings.stale.read' },
  { label: 'Waiver check', message: 'Which upcoming appointments still need waiver attention?' },
]

const COLLECTOR_QUICK_PROMPTS: QuickPrompt[] = [
  { label: 'My appointments', message: 'Show me the current status of my appointments.', actionIntent: 'bookings.read' },
  { label: 'Prepare', message: 'What is the next verified step for preparing for my appointment?' },
  { label: 'Aftercare', message: 'Give me aftercare guidance grounded in my current appointment status.' },
  { label: 'Book next', message: 'Help me start the next booking step without claiming a time is available.' },
]

const GALA_ACTION_PROMPTS = [
  { label: 'Plan', desc: 'Next safe step', message: 'Gala, help me plan the next safe action for this page.' },
  { label: 'Audit', desc: 'Find workflow gaps', message: 'Gala, audit the current page for workflow gaps and likely operational risks.' },
  { label: 'Draft', desc: 'Write the next asset', message: 'Gala, draft the next message or content needed for this workflow.' },
  { label: 'Route', desc: 'Choose the owner', message: 'Gala, tell me which app or workflow should handle this task.' },
]

const HUSTLE_ACTION_PROMPTS = [
  { label: 'Price', desc: 'Price the offer', message: 'Xar, help me price the current flash or listing. Ask for size, placement, complexity, deposit rules, and artist constraints before suggesting a price.' },
  { label: 'Listing', desc: 'Write listing copy', message: 'Xar, draft a high-converting Hustle listing for the current creator inventory. Keep claims accurate and ask for missing product details.' },
  { label: 'Segment', desc: 'Map the sheet', message: 'Xar, guide flash sheet segmentation and review what needs cleanup before publishing.' },
  { label: 'Audit', desc: 'Check publish risk', message: 'Xar, audit this Hustle creator surface for pricing, ecommerce readiness, SEO, AISO, conversion, marketing, and publish blockers.' },
]

const STAFF_ACTION_PROMPTS = [
  { label: 'Plan', desc: 'Next safe step', message: 'Spyder, help me plan the next safe action for this page.' },
  { label: 'Schedule', desc: 'Check current work', message: 'Spyder, check my current schedule and identify the next operational action.' },
  { label: 'Follow-up', desc: 'Find client gaps', message: 'Spyder, identify which of my current bookings need follow-up.' },
  { label: 'Route', desc: 'Choose the owner', message: 'Spyder, tell me which Ryozan workflow should handle this task.' },
]

const COLLECTOR_ACTION_PROMPTS = [
  { label: 'Prepare', desc: 'Next verified step', message: 'Smoku, give me the next verified preparation step for my appointment.' },
  { label: 'Aftercare', desc: 'Grounded guidance', message: 'Smoku, give me aftercare guidance based on my current appointment status.' },
  { label: 'Booking', desc: 'Request next step', message: 'Smoku, help me take the next booking step without inventing availability.' },
  { label: 'Route', desc: 'Get shop help', message: 'Smoku, tell me the correct Ryozan contact or workflow for this request.' },
]

export default function GalaPanel({ className, onClose, onCollapse, composeRequest }: GalaPanelProps) {
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()
  const location = useLocation()
  const [view, setView] = useState<'home' | 'chat'>('home')
  const [messages, setMessages] = useState<GalaMessage[]>([])
  const [input, setInput] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [activeAgentId, setActiveAgentId] = useState<GalaAgentId>(() => agentForContext(window.location.pathname, user?.role))
  const [courierWorkspace, setCourierWorkspace] = useState<CourierWorkspaceState | null>(null)
  const [courierWorkspaceTab, setCourierWorkspaceTab] = useState<CourierWorkspaceTab>('thread')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const handledComposeRequestRef = useRef<number | null>(null)
  const activeAgent = AGENT_PROFILES[activeAgentId]
  const showCourierWorkspace = location.pathname === ROUTES.COURIER && Boolean(courierWorkspace?.active)

  const resetChat = () => {
    setMessages([])
    setInput('')
    setView('home')
  }

  // Swipe right to dismiss on mobile
  const swipe = useSwipeDismiss({ direction: 'right', onDismiss: () => onClose?.() })

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    setActiveAgentId(agentForContext(location.pathname, user?.role))
  }, [location.pathname, user?.role])

  useEffect(() => {
    if (location.pathname !== ROUTES.COURIER) setCourierWorkspace(null)
  }, [location.pathname])

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<CourierWorkspaceState>).detail
      setCourierWorkspace(detail?.active ? detail : null)
    }

    window.addEventListener('ryozan:courier-workspace', handler)
    return () => window.removeEventListener('ryozan:courier-workspace', handler)
  }, [])

  const addGalaMessage = useCallback((text: string) => {
    setMessages(prev => [...prev, {
      id: makeId(),
      role: 'gala',
      text,
      timestamp: Date.now(),
    }])
  }, [])

  const sendMessage = useCallback(async (text: string, requestedAgentId?: GalaAgentId, actionIntent?: GalaActionIntent) => {
    if (!text || isTyping) return
    const requestAgent = requestedAgentId ? AGENT_PROFILES[requestedAgentId] : activeAgent
    setView('chat')
    setMessages(prev => [...prev, {
      id: makeId(),
      role: 'user',
      text,
      timestamp: Date.now(),
    }])
    setInput('')

    // Check for navigation commands (client-side, no API call)
    const navMatch = actionIntent ? null : matchNavCommand(text)
    if (navMatch) {
      navigate(navMatch.path)
      setTimeout(() => addGalaMessage(`Loading ${navMatch.label}.`), 300)
      return
    }

    // Freeform chat -> Gala endpoint
    setIsTyping(true)
    try {
      const token = getToken()

      // Build conversation history for context (cap at last 10 turns)
      const currentMessages = messages  // snapshot before the new user msg is appended
      const history = currentMessages.slice(-20).map(m => ({
        role: m.role === 'gala' ? 'assistant' : 'user',
        content: m.text,
      }))

      // If any recent Gala message has selected items, append them to the user message
      // so the model knows exactly what the user is referring to
      const recentGalaMsg = [...currentMessages].reverse().find(m => m.role === 'gala' && m.selectedItems?.length)
      const selectedContext = recentGalaMsg?.selectedItems?.length
        ? `\n\n[User has selected ${recentGalaMsg.selectedItems.length} item(s) from the list above. IDs: ${recentGalaMsg.selectedItems.join(', ')}]`
        : ''

      const res = await fetch(`${API_BASE}/gala/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: text + selectedContext,
          action_intent: actionIntent,
          history,
          context: {
            user_name: user?.name,
            user_role: user?.role,
            current_path: window.location.pathname,
            agent_id: requestAgent.id,
            agent_name: requestAgent.name,
            agent_title: requestAgent.title,
            agent_scope: requestAgent.scope,
            agent_instruction: requestAgent.instruction,
            agent_research_plan: requestAgent.researchPlan,
          },
        }),
      })

      if (!res.ok) {
        let detail = `${requestAgent.name} is unavailable right now. Try again in a moment.`
        try {
          const errorBody = await res.json() as { detail?: unknown }
          if (typeof errorBody.detail === 'string' && errorBody.detail.trim()) {
            detail = errorBody.detail.trim()
          } else if (
            errorBody.detail &&
            typeof errorBody.detail === 'object' &&
            'message' in errorBody.detail &&
            typeof errorBody.detail.message === 'string'
          ) {
            detail = errorBody.detail.message
          }
        } catch {
          // Keep the safe client message when the failure body is not JSON.
        }
        throw new Error(detail)
      }

      const data = await res.json()
      const components = (data.components || []) as GalaChatComponent[]
      setMessages(prev => [...prev, {
        id: makeId(),
        role: 'gala',
        text: data.response || data.message || 'I heard you, but I could not process that.',
        timestamp: Date.now(),
        components: components.length > 0 ? components : undefined,
        receipts: Array.isArray(data.tool_calls) && data.tool_calls.length > 0 ? data.tool_calls : undefined,
      }])
    } catch (error) {
      setMessages(prev => [...prev, {
        id: makeId(),
        role: 'gala',
        text: error instanceof Error && error.message
          ? error.message
          : `${requestAgent.name} is unavailable right now. Try again in a moment.`,
        timestamp: Date.now(),
        failure: {
          retryText: text,
          agentId: requestAgent.id,
          actionIntent,
        },
      }])
    } finally {
      setIsTyping(false)
    }
  }, [activeAgent, addGalaMessage, isTyping, messages, navigate, user?.name, user?.role])

  const handleSend = () => sendMessage(input.trim())

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ agentId?: GalaAgentId; message?: string }>).detail
      if (detail?.agentId && AGENT_PROFILES[detail.agentId]) setActiveAgentId(detail.agentId)
      const message = detail?.message?.trim()
      if (!message) return
      setView('home')
      setInput(message)
      window.setTimeout(() => inputRef.current?.focus(), 50)
    }
    window.addEventListener('gala-compose', handler)
    return () => window.removeEventListener('gala-compose', handler)
  }, [])

  useEffect(() => {
    if (!composeRequest || handledComposeRequestRef.current === composeRequest.id) return
    if (isTyping) return

    handledComposeRequestRef.current = composeRequest.id
    const message = composeRequest.message.trim()
    if (!message) return

    if (composeRequest.agentId && AGENT_PROFILES[composeRequest.agentId]) {
      setActiveAgentId(composeRequest.agentId)
    }
    void sendMessage(message, composeRequest.agentId)
  }, [composeRequest, isTyping, sendMessage])

  const quickPrompts = activeAgentId === 'xar'
    ? HUSTLE_QUICK_PROMPTS
    : activeAgentId === 'spyder'
      ? STAFF_QUICK_PROMPTS
      : activeAgentId === 'smoku'
        ? COLLECTOR_QUICK_PROMPTS
        : QUICK_PROMPTS
  const agentActionPrompts = activeAgentId === 'xar'
    ? HUSTLE_ACTION_PROMPTS
    : activeAgentId === 'spyder'
      ? STAFF_ACTION_PROMPTS
      : activeAgentId === 'smoku'
        ? COLLECTOR_ACTION_PROMPTS
        : GALA_ACTION_PROMPTS
  const agentActions = (
    <div className="gala-agent-actions" aria-label={`${activeAgent.name} action shortcuts`}>
      {agentActionPrompts.map((action) => (
        <button
          key={action.label}
          type="button"
          className="gala-agent-action-card"
          onClick={() => sendMessage(action.message)}
        >
          <span className="gala-agent-action-label">{action.label}</span>
          <span className="gala-agent-action-desc">{action.desc}</span>
        </button>
      ))}
    </div>
  )

  return (
    <div className={`gala-panel${className ? ` ${className}` : ''}`} {...swipe}>
      <div className="gala-header">
        {view === 'chat' ? (
          <button className="gala-back-btn" onClick={resetChat} aria-label="Back to home">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
        ) : (
          Icons.bot
        )}
        <span>{activeAgent.name}</span>
        <div className="gala-header-actions">
          {view === 'chat' && (
            <button className="gala-reset-btn" onClick={resetChat}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            </button>
          )}
          {onCollapse && (
            <button className="gala-collapse-btn" onClick={onCollapse} aria-label="Collapse Gala" title="Collapse">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
            </button>
          )}
          {onClose && (
            <button className="gala-close-btn" onClick={onClose} aria-label="Close Gala">
              {Icons.close}
            </button>
          )}
        </div>
      </div>

      {view === 'home' ? (
        <div className={`gala-home${showCourierWorkspace ? ' gala-home--courier' : ''}`}>
          {showCourierWorkspace && courierWorkspace ? (
            <>
              <CourierWorkspaceModule
                data={courierWorkspace}
                activeTab={courierWorkspaceTab}
                onTabChange={setCourierWorkspaceTab}
                onNavigate={navigate}
              />
              {agentActions}
            </>
          ) : (
            <>
              <div className="gala-home-hero">
                <div className="gala-home-icon">{Icons.bot}</div>
                <div className="gala-home-name">{activeAgent.name}</div>
                <div className="gala-home-sub">{activeAgent.title}</div>
                <div className="gala-agent-scope">{activeAgent.scope}</div>
              </div>

              {agentActions}

              <div className="gala-quick-prompts">
                {quickPrompts.map(p => (
                  <button
                    key={p.label}
                    className="gala-quick-btn"
                    onClick={() => sendMessage(p.message, undefined, p.actionIntent)}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </>
          )}

          <div className="gala-home-input-wrap">
            <input
              ref={inputRef}
              className="gala-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={`Ask ${activeAgent.name}...`}
            />
            <button
              className="gala-send-btn"
              onClick={handleSend}
              disabled={!input.trim() || isTyping}
            >
              {Icons.send}
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="gala-messages">
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`gala-msg from-${msg.role}${msg.failure ? ' gala-msg-error' : ''}`}
                role={msg.failure ? 'alert' : undefined}
                style={msg.failure ? { borderColor: 'var(--error, #ef5350)' } : undefined}
              >
                {msg.role === 'gala' ? (
                  <MessageResponse className="gala-msg-response">{msg.text}</MessageResponse>
                ) : (
                  msg.text
                )}
                {msg.failure && (
                  <button
                    type="button"
                    className="gala-courier-action"
                    onClick={() => void sendMessage(msg.failure!.retryText, msg.failure!.agentId, msg.failure!.actionIntent)}
                    disabled={isTyping}
                    style={{ marginTop: '0.65rem' }}
                  >
                    Try again
                  </button>
                )}
                {msg.components?.map((comp, i) => (
                  <GalaComponentRenderer
                    key={`${msg.id}-comp-${i}`}
                    component={comp}
                    onSelect={(ids) => {
                      setMessages(prev => prev.map(m =>
                        m.id === msg.id ? { ...m, selectedItems: ids } : m
                      ))
                    }}
                  />
                ))}
                {msg.receipts?.length ? (
                  <details className="gala-receipts">
                    <summary>Execution receipt</summary>
                    {msg.receipts.map((receipt, index) => (
                      <div key={`${String(receipt.correlation_id || receipt.tool || 'receipt')}-${index}`}>
                        {String(receipt.tool || 'Action')}: {String(receipt.status || 'unknown')}
                      </div>
                    ))}
                  </details>
                ) : null}
              </div>
            ))}
            {isTyping && (
              <div className="gala-msg from-gala gala-typing">
                <span className="gala-dot" /><span className="gala-dot" /><span className="gala-dot" />
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <div className="gala-input-wrap">
            <input
              ref={inputRef}
              className="gala-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={`Ask ${activeAgent.name}...`}
            />
            <button
              className="gala-send-btn"
              onClick={handleSend}
              disabled={!input.trim() || isTyping}
            >
              {Icons.send}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
