/**
 * Hotbar - MMO-style floating action bar.
 * Persists across all dashboard pages via DashboardLayout.
 * 9 configurable slots + edit mode.
 * Only visible for artist role.
 */

import type { ReactNode } from 'react'
import { useState, useRef, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/auth'
import { ROUTES } from '../lib/routes'

/* ── Types ─────────────────────────── */

interface HotbarAction {
  id: string
  label: string
  icon: ReactNode
  action: string
  desc: string
  type: string
  cooldown: string
  power: number
  school: string
}

/* ── Icons (18x18) ─────────────────── */

const Icons = {
  send: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13"/>
      <polygon points="22 2 15 22 11 13 2 9 22 2"/>
    </svg>
  ),
  heart: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
    </svg>
  ),
  calendar: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
    </svg>
  ),
  clock: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
    </svg>
  ),
  messageSquare: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  ),
  image: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>
    </svg>
  ),
  availability: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/><path d="m9 16 2 2 4-4"/>
    </svg>
  ),
  fileText: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
  ),
  users: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
  ),
  zap: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
    </svg>
  ),
  bot: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="11" width="18" height="10" rx="2"/>
      <circle cx="12" cy="5" r="2"/>
      <path d="M12 7v4"/>
      <path d="M8 16h.01"/>
      <path d="M16 16h.01"/>
    </svg>
  ),
  settings: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
  ),
  check: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12"/>
    </svg>
  ),
}

/* ── All available actions with RPG metadata ── */

/*
 * Power Rating (Impact Scale):
 *   1 = Passive/informational - no direct outcome
 *   2 = Workflow convenience - saves time, no revenue impact
 *   3 = Engagement driver - directly touches a client relationship
 *   4 = Retention multiplier - increases repeat bookings
 *   5 = Revenue generator - directly creates or collects money
 */

const ALL_ACTIONS: HotbarAction[] = [
  {
    id: 'aftercare', label: 'Send', icon: Icons.send, action: 'aftercare',
    desc: 'Send waivers, aftercare instructions, or invoices to clients.',
    type: 'Support', cooldown: 'Per session', power: 4, school: 'Restoration',
  },
  {
    id: 'new-booking', label: 'New Booking', icon: Icons.calendar, action: 'booking',
    desc: 'Create an appointment and notify the client.',
    type: 'Engagement', cooldown: 'Instant', power: 3, school: 'Conjuration',
  },
  {
    id: 'schedule', label: 'View Schedule', icon: Icons.clock, action: 'schedule',
    desc: 'Full calendar view of upcoming sessions and open slots.',
    type: 'Intel', cooldown: 'None', power: 1, school: 'Divination',
  },
  {
    id: 'message', label: 'Quick Message', icon: Icons.messageSquare, action: 'message',
    desc: 'Send a direct message to any client.',
    type: 'Comm', cooldown: 'None', power: 2, school: 'Transmission',
  },
  {
    id: 'portfolio', label: 'Open Hustle', icon: Icons.image, action: 'portfolio',
    desc: 'Manage gallery, flash inventory, and storefront operations.',
    type: 'Creative', cooldown: 'None', power: 4, school: 'Illusion',
  },
  {
    id: 'availability', label: 'Set Availability', icon: Icons.availability, action: 'availability',
    desc: 'Control when clients can book with you.',
    type: 'Utility', cooldown: 'Weekly', power: 3, school: 'Warding',
  },
  {
    id: 'documents', label: 'Open Documents', icon: Icons.fileText, action: 'documents',
    desc: 'Waivers, consent forms, and templates.',
    type: 'Admin', cooldown: 'None', power: 2, school: 'Inscription',
  },
  {
    id: 'clients', label: 'View Clients', icon: Icons.users, action: 'clients',
    desc: 'Client roster with history and contact info.',
    type: 'Intel', cooldown: 'None', power: 2, school: 'Divination',
  },
  {
    id: 'charge', label: 'Charge Client', icon: Icons.zap, action: 'charge',
    desc: 'Generate a QR code for instant Stripe payment.',
    type: 'Finisher', cooldown: 'Per session', power: 5, school: 'Transmutation',
  },
  {
    id: 'bookings', label: 'View Books', icon: Icons.calendar, action: 'bookings',
    desc: 'Full list of all bookings past and upcoming.',
    type: 'Intel', cooldown: 'None', power: 1, school: 'Divination',
  },
]

const DEFAULT_SLOTS = ['aftercare', 'new-booking', 'schedule', 'message', 'clients', 'documents', 'charge', 'availability', 'portfolio']

const STORAGE_KEY = 'ryozan_hotbar'

function loadSlots(): string[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) return JSON.parse(saved)
  } catch { /* ignore */ }
  return DEFAULT_SLOTS
}

function saveSlots(ids: string[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids))
}

/* ── Navigation map for actions ────── */

const NAV_ACTIONS: Record<string, string> = {
  schedule: ROUTES.SCHEDULE,
  portfolio: ROUTES.HUSTLE,
  documents: ROUTES.ASSETS,
  clients: ROUTES.CLIENTS,
  bookings: ROUTES.BOOKINGS,
  message: ROUTES.COURIER,
  booking: ROUTES.BOOKINGS,
  // aftercare: handled as Send flyout — not a direct nav
  availability: ROUTES.SETTINGS,
  // charge: handled via CustomEvent -> POSModal in DashboardLayout
}

const CONTEXT_TITLES: Array<[string, string]> = [
  [ROUTES.COURIER, 'Courier'],
  [ROUTES.GUILD, 'Guild'],
  [ROUTES.HUSTLE, 'Hustle'],
  [ROUTES.HUDDLE, 'Huddl'],
  [ROUTES.VAULT, 'Vault'],
  [ROUTES.HERALD, 'Herald'],
  [ROUTES.QUESTS, 'Quests'],
  [ROUTES.WALLET, 'Wallet'],
  [ROUTES.PROFILE, 'Profile'],
  [ROUTES.ARTIST_PROFILE, 'Profile'],
  [ROUTES.ADMIN_USERS, 'Admin'],
  [ROUTES.DASHBOARD, 'Nexus'],
]

function getHotbarContextTitle(pathname: string) {
  const match = CONTEXT_TITLES.find(([path]) => pathname === path || pathname.startsWith(`${path}/`))
  return match?.[1] ?? 'Nexus'
}

/* ── Send flyout options ── */
const SEND_OPTIONS = [
  { label: 'Waivers',   icon: 'fileText' as const, route: ROUTES.ASSETS },
  { label: 'Aftercare', icon: 'heart'    as const, route: ROUTES.ASSETS },
  { label: 'Invoices',  icon: 'zap'      as const, route: ROUTES.ASSETS },
]

/* Nav tiles merged into ALL_ACTIONS as quick casts */

/* ── Power pips (filled/empty diamonds) ── */

function PowerPips({ level }: { level: number }) {
  return (
    <span className="cast-card-pips">
      {Array.from({ length: 5 }, (_, i) => (
        <span key={i} className={`cast-pip${i < level ? ' filled' : ''}`} />
      ))}
    </span>
  )
}

/* ── Component ─────────────────────── */

export default function Hotbar() {
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [slotIds, setSlotIds] = useState<string[]>(loadSlots)
  const [editing, setEditing] = useState(false)
  const [hoveredCast, setHoveredCast] = useState<HotbarAction | null>(null)
  const [cardPos, setCardPos] = useState<{ x: number; bottom: number } | null>(null)
  const touchStart = useRef<number | null>(null)
  const hoverTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Send flyout state
  const [sendFlyoutOpen, setSendFlyoutOpen] = useState(false)
  const [sendFlyoutPos, setSendFlyoutPos] = useState<{ x: number; bottom: number } | null>(null)

  useEffect(() => {
    if (!sendFlyoutOpen) return
    const close = (e: MouseEvent) => {
      const target = e.target as Element
      if (!target.closest('.hotbar-send-flyout') && !target.closest('[data-action="aftercare"]')) {
        setSendFlyoutOpen(false)
      }
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [sendFlyoutOpen])

  // Show for staff (assistant, artist, admin, founder)
  const staffRoles = ['assistant', 'artist', 'admin', 'founder']
  if (!user?.role || !staffRoles.includes(user.role)) return null

  const activeSlots = slotIds
    .map(id => ALL_ACTIONS.find(a => a.id === id))
    .filter(Boolean) as HotbarAction[]
  const contextTitle = getHotbarContextTitle(location.pathname)

  // Pad to 9 empty slots for visual consistency
  const displaySlots: (HotbarAction | null)[] = [...activeSlots]
  while (displaySlots.length < 9) displaySlots.push(null)

  const handleAction = (action: string, el?: HTMLElement) => {
    if (action === 'aftercare') {
      if (el) {
        const rect = el.getBoundingClientRect()
        setSendFlyoutPos({ x: rect.left + rect.width / 2, bottom: window.innerHeight - rect.top + 8 })
      }
      setSendFlyoutOpen(prev => !prev)
      return
    }
    if (NAV_ACTIONS[action]) {
      navigate(NAV_ACTIONS[action])
    }
    window.dispatchEvent(new CustomEvent('hotbar-action', { detail: action }))
  }

  const toggleSlot = (id: string) => {
    let next: string[]
    if (slotIds.includes(id)) {
      next = slotIds.filter(s => s !== id)
    } else if (slotIds.length < 9) {
      next = [...slotIds, id]
    } else {
      return
    }
    setSlotIds(next)
    saveSlots(next)
  }

  const showCard = (action: HotbarAction, el: HTMLElement) => {
    if (hoverTimeout.current) clearTimeout(hoverTimeout.current)
    hoverTimeout.current = setTimeout(() => {
      const rect = el.getBoundingClientRect()
      setCardPos({
        x: rect.left + rect.width / 2,
        bottom: window.innerHeight - rect.top + 12,
      })
      setHoveredCast(action)
    }, 300)
  }

  const hideCard = () => {
    if (hoverTimeout.current) clearTimeout(hoverTimeout.current)
    setHoveredCast(null)
    setCardPos(null)
  }

  const handleTouchStart = (e: React.TouchEvent) => { touchStart.current = e.touches[0].clientX }
  const handleTouchEnd = (e: React.TouchEvent) => {
    if (touchStart.current !== null) {
      const diff = e.changedTouches[0].clientX - touchStart.current
      if (Math.abs(diff) > 80) {
        const bar = e.currentTarget as HTMLElement
        bar.scrollBy({ left: diff > 0 ? -120 : 120, behavior: 'smooth' })
      }
    }
    touchStart.current = null
  }

  if (editing) {
    return (
      <div className="hotbar">
        <span className="hotbar-title">Edit Quick Casts</span>
        <div className="hotbar-inner hotbar-edit-mode" onTouchStart={handleTouchStart} onTouchEnd={handleTouchEnd}>
          {ALL_ACTIONS.map((action) => (
            <button
              type="button"
              key={action.id}
              className={`hotbar-slot${slotIds.includes(action.id) ? ' active' : ''}`}
              onClick={() => toggleSlot(action.id)}
              aria-label={`${slotIds.includes(action.id) ? 'Remove' : 'Add'} ${action.label} quick cast`}
            >
              <span className="hotbar-icon">{action.icon}</span>
              <span className="hotbar-key">{action.label.split(' ').map(w => w[0]).join('')}</span>
            </button>
          ))}
          <div className="hotbar-divider" />
          <button
            type="button"
            className="hotbar-slot hotbar-confirm"
            onClick={() => setEditing(false)}
            aria-label="Finish editing quick casts"
          >
            <span className="hotbar-icon">{Icons.check}</span>
            <span className="hotbar-key">OK</span>
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="hotbar">
      <div className="hotbar-inner" onTouchStart={handleTouchStart} onTouchEnd={handleTouchEnd}>
        <span className="hotbar-context-label" aria-label={`${contextTitle} context`}>
          <span className="hotbar-context-text" aria-hidden="true">
            {contextTitle.split('').map((char, index) => (
              <span key={`${char}-${index}`} className="hotbar-context-char">{char}</span>
            ))}
          </span>
        </span>

        {displaySlots.map((slot, i) => (
          slot ? (
            <button
              type="button"
              key={slot.id}
              className={`hotbar-slot${slot.action === 'aftercare' && sendFlyoutOpen ? ' active' : ''}`}
              data-action={slot.action}
              onClick={(e) => handleAction(slot.action, e.currentTarget)}
              onMouseEnter={(e) => showCard(slot, e.currentTarget)}
              onMouseLeave={hideCard}
              aria-label={slot.action === 'message' ? 'Open Courier' : slot.label}
            >
              <span className="hotbar-icon">{slot.icon}</span>
            </button>
          ) : (
            <button
              key={`empty-${i}`}
              type="button"
              className="hotbar-slot hotbar-empty"
              onClick={() => setEditing(true)}
              aria-label={`Configure quick cast slot ${i + 1}`}
            >
              <span className="hotbar-add">+</span>
            </button>
          )
        ))}
        <div className="hotbar-divider" />
        <button
          type="button"
          className="hotbar-slot hotbar-edit-btn"
          onClick={() => setEditing(true)}
          aria-label="Edit quick casts"
        >
          <span className="hotbar-icon">{Icons.settings}</span>
        </button>
      </div>

      {/* Send flyout */}
      {sendFlyoutOpen && sendFlyoutPos && (
        <div
          className="hotbar-send-flyout"
          style={{
            position: 'fixed',
            left: `${sendFlyoutPos.x}px`,
            bottom: `${sendFlyoutPos.bottom}px`,
            transform: 'translateX(-50%)',
            zIndex: 101,
          }}
        >
          <div className="hsf-label-row">Send</div>
          {SEND_OPTIONS.map(opt => (
            <button
              type="button"
              key={opt.label}
              className="hsf-item"
              onClick={() => { setSendFlyoutOpen(false); navigate(opt.route) }}
            >
              <span className="hsf-icon">{Icons[opt.icon]}</span>
              <span className="hsf-name">{opt.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* RPG Cast Card tooltip */}
      {hoveredCast && cardPos && (
        <div
          className="cast-card"
          style={{
            left: `${cardPos.x}px`,
            bottom: `${cardPos.bottom}px`,
            transform: 'translateX(-50%)',
          }}
        >
          <div className="cast-card-header">
            <span className="cast-card-school">{hoveredCast.school}</span>
            <span className="cast-card-type">{hoveredCast.type}</span>
          </div>
          <div className="cast-card-icon-wrap">
            {hoveredCast.icon}
          </div>
          <div className="cast-card-name">{hoveredCast.label}</div>
          <div className="cast-card-desc">{hoveredCast.desc}</div>
          <div className="cast-card-stats">
            <div className="cast-card-stat">
              <span className="cast-card-stat-label">Power</span>
              <PowerPips level={hoveredCast.power} />
            </div>
            <div className="cast-card-stat">
              <span className="cast-card-stat-label">Cooldown</span>
              <span className="cast-card-stat-value">{hoveredCast.cooldown}</span>
            </div>
          </div>
          <div className="cast-card-footer">
            Quick Cast
          </div>
        </div>
      )}
    </div>
  )
}
