/**
 * MobileActionSheet -- swipe-up quick cast grid for mobile.
 *
 * Replaces the cramped horizontal hotbar on screens < 768px.
 * Shows a half-sheet with a 3x3 grid of actions + labels.
 * Tap grip handle or swipe up from bottom to open.
 * Swipe down or tap overlay to dismiss.
 */

import type { ReactNode } from 'react'
import { useState, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/auth'

/* ── Types ─────────────────────────── */

interface ActionItem {
  id: string
  label: string
  icon: ReactNode
  action: string
}

/* ── Icons (reused from Hotbar) ────── */

const Icons = {
  heart: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
    </svg>
  ),
  calendar: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
    </svg>
  ),
  clock: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
    </svg>
  ),
  messageSquare: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  ),
  users: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
  ),
  fileText: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
  ),
  zap: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
    </svg>
  ),
  image: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>
    </svg>
  ),
  availability: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/><path d="m9 16 2 2 4-4"/>
    </svg>
  ),
  chevronUp: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="18 15 12 9 6 15"/>
    </svg>
  ),
}

/* ── Default quick cast actions ────── */

const ACTIONS: ActionItem[] = [
  { id: 'aftercare', label: 'Aftercare', icon: Icons.heart, action: 'aftercare' },
  { id: 'new-booking', label: 'New Booking', icon: Icons.calendar, action: 'booking' },
  { id: 'schedule', label: 'Schedule', icon: Icons.clock, action: 'schedule' },
  { id: 'message', label: 'Message', icon: Icons.messageSquare, action: 'message' },
  { id: 'clients', label: 'Clients', icon: Icons.users, action: 'clients' },
  { id: 'documents', label: 'Docs', icon: Icons.fileText, action: 'documents' },
  { id: 'charge', label: 'Charge', icon: Icons.zap, action: 'charge' },
  { id: 'bookings', label: 'Bookings', icon: Icons.calendar, action: 'bookings' },
  { id: 'portfolio', label: 'Hustle', icon: Icons.image, action: 'portfolio' },
]

const NAV_ACTIONS: Record<string, string> = {
  schedule: '/dashboard',
  portfolio: '/dashboard/hustle',
  documents: '/dashboard?panel=codex',
  clients: '/dashboard/huddle/clients',
  bookings: '/dashboard/huddle?charm=bookings',
}

/* ── Component ─────────────────────── */

interface MobileActionSheetProps {
  onGalaToggle: () => void
  galaOpen?: boolean
  galaEnabled?: boolean
}

export default function MobileActionSheet({ onGalaToggle, galaOpen, galaEnabled = false }: MobileActionSheetProps) {
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const sheetRef = useRef<HTMLDivElement>(null)
  const touchStartY = useRef<number | null>(null)
  const gripTouchStartY = useRef<number | null>(null)

  // Show for artists, admins, and founders.
  if (!user || !['artist', 'admin', 'founder'].includes(user.role)) return null

  const go = (to: string) => {
    setOpen(false)
    navigate(to)
  }

  const isActive = (path: string) => {
    if (path === '/dashboard') return location.pathname === '/dashboard'
    return location.pathname.startsWith(path)
  }

  const handleAction = (action: string) => {
    setOpen(false)
    if (NAV_ACTIONS[action]) {
      navigate(NAV_ACTIONS[action])
    }
    window.dispatchEvent(new CustomEvent('hotbar-action', { detail: action }))
  }

  /* ── Swipe down to dismiss the sheet ── */
  const onSheetTouchStart = (e: React.TouchEvent) => {
    touchStartY.current = e.touches[0].clientY
  }
  const onSheetTouchEnd = (e: React.TouchEvent) => {
    if (touchStartY.current === null) return
    const dy = e.changedTouches[0].clientY - touchStartY.current
    if (dy > 60) setOpen(false) // swipe down
    touchStartY.current = null
  }

  /* ── Swipe up on grip to open ── */
  const onGripTouchStart = (e: React.TouchEvent) => {
    gripTouchStartY.current = e.touches[0].clientY
  }
  const onGripTouchEnd = (e: React.TouchEvent) => {
    if (gripTouchStartY.current === null) return
    const dy = e.changedTouches[0].clientY - gripTouchStartY.current
    if (dy < -40) setOpen(true) // swipe up
    gripTouchStartY.current = null
  }

  return (
    <>
      <nav className={`mobile-bottom-nav${galaOpen ? ' hidden' : ''}`} aria-label="Mobile portal navigation">
        <button className={`mobile-nav-item${isActive('/dashboard') ? ' active' : ''}`} onClick={() => go('/dashboard')} type="button">
          {Icons.calendar}
          <span>Home</span>
        </button>
        <button className={`mobile-nav-item${isActive('/dashboard/huddle') ? ' active' : ''}`} onClick={() => go('/dashboard/huddle')} type="button">
          {Icons.users}
          <span>Huddle</span>
        </button>
        <button className={`mobile-nav-item${isActive('/dashboard/courier') ? ' active' : ''}`} onClick={() => go('/dashboard/courier')} type="button">
          {Icons.messageSquare}
          <span>Courier</span>
        </button>
        <button className={`mobile-nav-item${isActive('/dashboard/hustle') ? ' active' : ''}`} onClick={() => go('/dashboard/hustle')} type="button">
          {Icons.image}
          <span>Hustle</span>
        </button>
        <button
          className={`mobile-nav-item mobile-nav-tools${open ? ' active' : ''}`}
          onClick={() => setOpen(prev => !prev)}
          onTouchStart={onGripTouchStart}
          onTouchEnd={onGripTouchEnd}
          type="button"
        >
          {Icons.chevronUp}
          <span>Tools</span>
        </button>
        {galaEnabled && (
          <button className="mobile-nav-item mobile-nav-gala" onClick={onGalaToggle} type="button">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/>
            </svg>
            <span>Gala</span>
          </button>
        )}
      </nav>

      {/* Overlay + full action sheet */}
      {['artist', 'admin', 'founder'].includes(user.role) && (
        <>
          {open && <div className="mobile-sheet-overlay" onClick={() => setOpen(false)} />}

          <div
            ref={sheetRef}
            className={`mobile-action-sheet${open ? ' open' : ''}`}
            onTouchStart={onSheetTouchStart}
            onTouchEnd={onSheetTouchEnd}
          >
            <div className="mobile-sheet-handle" />
            <div className="mobile-sheet-label">Quick Casts</div>
            <div className="mobile-sheet-grid">
              {ACTIONS.map((item) => (
                <button
                  key={item.id}
                  className="mobile-sheet-action"
                  onClick={() => handleAction(item.action)}
                >
                  <span className="mobile-sheet-action-icon">{item.icon}</span>
                  <span className="mobile-sheet-action-label">{item.label}</span>
                </button>
              ))}
            </div>

            {/* Gala shortcut inside the sheet */}
            {galaEnabled && (
              <button
                className="mobile-sheet-gala"
                onClick={() => { setOpen(false); onGalaToggle() }}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/>
                </svg>
                <span>Ask Gala</span>
              </button>
            )}
          </div>
        </>
      )}
    </>
  )
}
