/**
 * DashboardLayout: Authenticated area layout.
 *
 * Structure matches original artist-portal.html exactly:
 *   main.portal-shell > section.app-view > aside.sidebar + div.main-area
 *
 * The .portal-shell has padding (for login view centering).
 * The .app-view is the flex container that holds sidebar + main.
 * The .sidebar is position:fixed, 80px wide.
 * The .main-area has margin-left: var(--sidebar-width).
 */

import { useState, useRef, useEffect, useCallback, lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { ROUTES } from '../../lib/routes'
import { useAuthStore } from '../../stores/auth'
import { auth as authApi, notifications as notifApi, artists as artistsApi, conversations as convsApi } from '../../lib/api'
import type { Notification, ArtistDetail } from '../../lib/api'
import { useSwipeDismiss } from '../../hooks/useSwipeDismiss'
import NexusSearch from '../NexusSearch'
import '../../styles/artist-portal.css'
import '../../styles/dashboard.css'

const GalaPanel = lazy(() => import('../GalaPanel'))
const MobileActionSheet = lazy(() => import('../MobileActionSheet'))
const POSModal = lazy(() => import('../POSModal'))
const DevModeBar = lazy(() => import('../DevModeBar'))
const DevModeBarTabs = lazy(() => import('../DevModeBar').then((module) => ({ default: module.DevModeBarTabs })))

interface NexusToolbarItem {
  id: string
  label: string
  icon: ReactNode
  active?: boolean
  onClick: () => void
}

interface NexusToolbarRegisterDetail {
  items: NexusToolbarItem[]
}

interface GalaComposeRequest {
  id: number
  agentId?: 'gala' | 'xar'
  message: string
}

/* ── SVG Icons (from original SECTION_ICONS in artist-portal.js) ──── */

const icons = {
  dashboard: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="3" y="3" width="7" height="7" rx="1"/>
      <rect x="14" y="3" width="7" height="7" rx="1"/>
      <rect x="3" y="14" width="7" height="7" rx="1"/>
      <rect x="14" y="14" width="7" height="7" rx="1"/>
    </svg>
  ),
  bookings: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="3" y="4" width="18" height="18" rx="2"/>
      <line x1="16" y1="2" x2="16" y2="6"/>
      <line x1="8" y1="2" x2="8" y2="6"/>
      <line x1="3" y1="10" x2="21" y2="10"/>
      <path d="M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/>
    </svg>
  ),
  clients: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/>
      <circle cx="9" cy="7" r="4"/>
      <path d="M23 21v-2a4 4 0 00-3-3.87"/>
      <path d="M16 3.13a4 4 0 010 7.75"/>
    </svg>
  ),
  schedule: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="12" cy="12" r="10"/>
      <polyline points="12 6 12 12 16 14"/>
    </svg>
  ),
  quests: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
    </svg>
  ),
  documents: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
      <line x1="16" y1="13" x2="8" y2="13"/>
      <line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
  ),
  waivers: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/>
      <rect x="9" y="3" width="6" height="4" rx="1"/>
      <path d="M9 14l2 2 4-4"/>
    </svg>
  ),
  users: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M16 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/>
      <circle cx="8.5" cy="7" r="4"/>
      <line x1="20" y1="8" x2="20" y2="14"/>
      <line x1="23" y1="11" x2="17" y2="11"/>
    </svg>
  ),
  profile: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/>
      <circle cx="12" cy="7" r="4"/>
    </svg>
  ),
  artist: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M12 19l7-7 3 3-7 7-3-3z"/>
      <path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/>
      <path d="M2 2l7.586 7.586"/>
      <circle cx="11" cy="11" r="2"/>
    </svg>
  ),
  logout: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/>
      <polyline points="16 17 21 12 16 7"/>
      <line x1="21" y1="12" x2="9" y2="12"/>
    </svg>
  ),
  back: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="19" y1="12" x2="5" y2="12"/>
      <polyline points="12 19 5 12 12 5"/>
    </svg>
  ),
  bell: (
    <svg className="topbar-svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/>
      <path d="M13.73 21a2 2 0 01-3.46 0"/>
    </svg>
  ),
  menu: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <line x1="3" y1="6" x2="21" y2="6"/>
      <line x1="3" y1="12" x2="21" y2="12"/>
      <line x1="3" y1="18" x2="21" y2="18"/>
    </svg>
  ),
  messages: (
    <svg className="topbar-svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
      <polyline points="22,6 12,13 2,6"/>
    </svg>
  ),
  chevron: (
    <svg className="topbar-user-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9"/>
    </svg>
  ),
  settings: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>
    </svg>
  ),
  huddle: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="20" height="14" rx="2"/>
      <line x1="8" y1="21" x2="16" y2="21"/>
      <line x1="12" y1="17" x2="12" y2="21"/>
      <path d="M6 8h.01M9 8h.01"/>
    </svg>
  ),
  courier: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 2L11 13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
    </svg>
  ),
  wallet: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><path d="M12 6v12"/><path d="M15.5 9.5a3 3 0 0 0-3-1.5H11a3 3 0 0 0 0 4h2a3 3 0 0 1 0 4h-1.5a3 3 0 0 1-3-1.5"/>
    </svg>
  ),
  guild: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>
    </svg>
  ),
  vault: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="6" width="20" height="14" rx="2"/><path d="M2 10h20"/><circle cx="12" cy="16" r="2"/><path d="M6 6V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/>
    </svg>
  ),
  herald: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
    </svg>
  ),
  /* ── Collector-specific icons ── */
  book: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
    </svg>
  ),
  club: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
  ),
  loot: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a4 4 0 0 0-8 0v2"/><circle cx="12" cy="15" r="2"/>
    </svg>
  ),
  shop: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/>
    </svg>
  ),
  flash: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
    </svg>
  ),
  mods: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M17.5 14v7M14 17.5h7"/>
    </svg>
  ),
  matrix: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2 21 7v10l-9 5-9-5V7z"/><path d="M12 22V12M21 7l-9 5-9-5"/>
    </svg>
  ),
}

/* ── Nav item configs by role (matches original SECTION arrays) ───── */

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
  end?: boolean
}

/**
 * Original role → sections mapping from artist-portal.js:
 *   FOUNDER:   dashboard, clients, schedule, bookings, artist, sidequests, documents, users, profile
 *   ADMIN:     dashboard, clients, schedule, sidequests, documents, users, profile
 *   ARTIST:    dashboard, clients, schedule, artist, sidequests, documents, profile
 *   ASSISTANT: dashboard, clients, sidequests, documents, profile
 *   COLLECTOR: dashboard, bookings, sidequests, documents, profile
 */

interface NavSection {
  label: string | null  // null = no section header
  items: NavItem[]
}

interface DevModeMeta {
  label: string
  detail: string
  tone: 'local' | 'proxy' | 'custom'
}

/** Build nav sections matching role-based access */
function getNavSections(role?: string): NavSection[] {
  const r = role || 'collector'

  // Collector: Home | Book | Club | Loot | Shop
  if (r === 'collector') {
    return [{ label: null, items: [
      { to: '/dashboard', label: 'Home', icon: icons.dashboard, end: true },
      { to: '/dashboard/book', label: 'Book', icon: icons.book },
      { to: '/dashboard/club', label: 'Club', icon: icons.club },
      { to: '/dashboard/loot', label: 'Loot', icon: icons.loot },
      { to: '/dashboard/shop', label: 'Shop', icon: icons.shop },
    ]}]
  }

  // Artist / Admin / Founder: clean minimal sidebar. Extras live in Toolkit + Axiom.
  // Owners and admins also get the Mods rail: the inventory and the Ark Matrix Mod.
  const modsRail: NavSection[] = r === 'admin' || r === 'founder'
    ? [{ label: 'Mods', items: [
      { to: '/dashboard/mods', label: 'Mods', icon: icons.mods, end: true },
      { to: '/dashboard/mods?view=matrix', label: 'Matrix', icon: icons.matrix },
    ]}]
    : []
  return [{ label: null, items: [
    { to: '/dashboard', label: 'Home', icon: icons.dashboard, end: true },
    { to: '/dashboard/huddle', label: 'Huddle', icon: icons.huddle },
    ...(r === 'assistant' ? [] : [{ to: '/dashboard/hustle', label: 'Hustle', icon: icons.flash }]),
    { to: '/dashboard/courier', label: 'Courier', icon: icons.courier },
    { to: '/dashboard/guild', label: 'Guild', icon: icons.guild },
    { to: '/dashboard/vault', label: 'Vault', icon: icons.vault },
    { to: '/dashboard/herald', label: 'Herald', icon: icons.herald },
  ]}, ...modsRail]
}

function getDevModeMeta(): DevModeMeta | null {
  if (!import.meta.env.DEV) return null

  const env = (import.meta.env.VITE_APP_ENV ?? import.meta.env.MODE ?? 'unknown').toLowerCase()
  const apiUrl = (import.meta.env.VITE_API_URL ?? '').toLowerCase()

  if (env === 'prod-proxy') {
    return {
      label: 'prod-proxy',
      detail: 'production api',
      tone: 'proxy',
    }
  }

  if (apiUrl.includes('localhost') || apiUrl.includes('127.0.0.1') || env === 'development') {
    return {
      label: 'local-full',
      detail: 'local api',
      tone: 'local',
    }
  }

  return {
    label: env,
    detail: import.meta.env.VITE_API_URL ? 'custom api' : 'same-origin api',
    tone: 'custom',
  }
}

/* ── Password Reset Banner ────────────────────── */

function PasswordResetBanner() {
  const { user, fetchMe } = useAuthStore()
  const [open, setOpen] = useState(false)
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  if (!user?.must_reset_password) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (newPw.length < 8) { setError('New password must be at least 8 characters'); return }
    if (newPw !== confirmPw) { setError('Passwords do not match'); return }
    if (newPw === currentPw) { setError('New password must be different from current'); return }
    setSaving(true)
    try {
      await authApi.changePassword(currentPw, newPw)
      // Refresh user data. must_reset_password will now be false.
      await fetchMe()
      // Belt-and-suspenders: if fetchMe still shows the flag (stale cache),
      // force the local state so the banner disappears immediately
      const current = useAuthStore.getState().user
      if (current?.must_reset_password) {
        useAuthStore.setState({ user: { ...current, must_reset_password: false } })
      }
      setOpen(false)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to change password')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <div className="password-reset-banner">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18">
          <path d="M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <span>
          You're using a default password. Please change it now to secure your account.
        </span>
        <button onClick={() => setOpen(true)}>Change Password</button>
      </div>

      {open && (
        <div className="pw-modal-overlay" onClick={() => setOpen(false)}>
          <form className="pw-modal" onClick={e => e.stopPropagation()} onSubmit={handleSubmit}>
            <h3>Change Your Password</h3>
            <p className="pw-modal-subtitle">Choose a strong password you'll remember.</p>
            {error && <div className="pw-modal-error">{error}</div>}
            <label>
              Current Password
              <input
                type="password"
                value={currentPw}
                onChange={e => setCurrentPw(e.target.value)}
                required
                autoFocus
              />
            </label>
            <label>
              New Password
              <input
                type="password"
                value={newPw}
                onChange={e => setNewPw(e.target.value)}
                required
                minLength={8}
                placeholder="At least 8 characters"
              />
            </label>
            <label>
              Confirm New Password
              <input
                type="password"
                value={confirmPw}
                onChange={e => setConfirmPw(e.target.value)}
                required
              />
            </label>
            <div className="pw-modal-actions">
              <button type="button" onClick={() => setOpen(false)} disabled={saving}>Cancel</button>
              <button type="submit" className="pw-modal-submit" disabled={saving}>
                {saving ? 'Saving...' : 'Update Password'}
              </button>
            </div>
          </form>
        </div>
      )}
    </>
  )
}

/* ── Onboarding Banner ─────────────────────────── */

const ONBOARDING_DISMISS_KEY = 'ryozan_onboarding_dismissed'

function OnboardingBanner() {
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(ONBOARDING_DISMISS_KEY) === 'true' } catch { return false }
  })
  const [artistProfile, setArtistProfile] = useState<ArtistDetail | null>(null)
  const [loaded, setLoaded] = useState(false)

  // Only show for artist/admin/founder
  const showRoles = ['assistant', 'artist', 'admin', 'founder']
  const shouldShow = user && showRoles.includes(user.role)

  useEffect(() => {
    if (!shouldShow || !user) return
    // Fetch artist profile to check bio/specialty/portfolio
    artistsApi.list()
      .then((artists) => {
        const me = artists.find((a) => a.id === user.id)
        if (me) return artistsApi.get(me.slug)
        return null
      })
      .then((data) => {
        if (data) setArtistProfile(data)
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [shouldShow, user])

  if (!shouldShow || !loaded || dismissed) return null

  // Checklist items
  const checks = [
    { label: 'Upload an avatar', done: !!(user?.avatar_url) },
    { label: 'Write your bio', done: !!((artistProfile?.bio?.length ?? 0) > 10) },
    { label: 'Set your specialty', done: !!((artistProfile?.specialty?.length ?? 0) > 0) },
    { label: 'Add a portfolio image', done: !!((artistProfile?.portfolio_images?.length ?? 0) > 0) },
  ]

  const completed = checks.filter((c) => c.done).length
  const total = checks.length
  const pct = Math.round((completed / total) * 100)

  // All done -- hide banner
  if (completed === total) return null

  const handleDismiss = () => {
    setDismissed(true)
    try { localStorage.setItem(ONBOARDING_DISMISS_KEY, 'true') } catch { /* ignore */ }
  }

  return (
    <div className="onboarding-banner">
      <details className="onboarding-banner-content">
        <summary className="onboarding-banner-top">
          <span className="onboarding-banner-heading">
            <span className="onboarding-banner-title">Complete your profile</span>
            <span className="onboarding-pct">{completed} of {total} done</span>
          </span>
          <span className="onboarding-progress" aria-hidden="true">
            <span className="onboarding-progress-fill" style={{ width: `${pct}%` }} />
          </span>
          <button className="onboarding-cta" onClick={() => navigate('/dashboard/artist-profile')}>
          Complete Profile
        </button>
          <button className="onboarding-dismiss" onClick={handleDismiss} aria-label="Dismiss">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </summary>
        <ul className="onboarding-checklist">
          {checks.map((item) => (
            <li key={item.label} className={item.done ? 'done' : ''}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14">
                {item.done
                  ? <polyline points="20 6 9 17 4 12"/>
                  : <circle cx="12" cy="12" r="9"/>}
              </svg>
              <span>{item.label}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  )
}

/* ── Helpers ──────────────────────────────────── */

function timeAgo(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return new Date(dateStr).toLocaleDateString()
}

/* ── Route → page title map ───────────────────── */

const pageTitles: Record<string, string> = {
  '/dashboard': 'NEXUS',
  '/dashboard/huddle': 'HUDDLE',
  '/dashboard/huddle/bookings': 'Bookings',
  '/dashboard/huddle/clients': 'Clients',
  '/dashboard/huddle/schedule': 'Schedule',
  '/dashboard/huddle/documents': 'Documents', // legacy redirect, now in Assets panel
  '/dashboard/courier': 'COURIER',
  '/dashboard/guild': 'GUILD',
  '/dashboard/vault': 'VAULT',
  '/dashboard/hustle': 'HUSTLE',
  '/dashboard/flash': 'HUSTLE',
  '/dashboard/quests': 'Quests',
  '/dashboard/wallet': 'Wallet',
  '/dashboard/messages': 'Messages',
  '/dashboard/profile': 'Profile',
  '/dashboard/artist-profile': 'Profile',
  '/dashboard/admin/users': 'User Management',
  '/dashboard/herald': 'HERALD',
  '/dashboard/mods': 'MODS',
}

function mobilePortalTabForRoute(pathname: string, role?: string): string | null {
  if (pathname === ROUTES.DASHBOARD) return null
  // The Mods rail has no portal tab of its own; its page renders on phones too.
  if (pathname.startsWith(ROUTES.MODS)) return null
  const collector = role === 'collector'

  if (pathname.startsWith('/dashboard/courier') || pathname.startsWith('/dashboard/messages')) return 'table'
  if (pathname.startsWith('/dashboard/guild') || pathname.startsWith('/dashboard/quests') || pathname.startsWith('/dashboard/club') || pathname.startsWith('/dashboard/herald')) return 'club'
  if (pathname.startsWith('/dashboard/profile') || pathname.startsWith('/dashboard/artist-profile')) return collector ? 'you' : 'hub'
  if (pathname.startsWith('/dashboard/shop') || pathname.startsWith('/dashboard/loot')) return collector ? 'wall' : 'hub'
  if (pathname.startsWith('/dashboard/huddle') || pathname.startsWith('/dashboard/bookings') || pathname.startsWith('/dashboard/book')) return collector ? 'home' : 'books'
  if (pathname.startsWith('/dashboard/hustle') || pathname.startsWith('/dashboard/flash') || pathname.startsWith('/dashboard/vault') || pathname.startsWith('/dashboard/wallet')) return 'hub'
  return 'home'
}

/* The Mods rail's two items share a page and differ by ?view=. */
function sameView(to: string, search: string): boolean {
  const wanted = new URLSearchParams(to.split('?')[1] ?? '').get('view')
  return wanted === new URLSearchParams(search).get('view')
}

/* Mod routes get the Oswald-styled title treatment */
const MOD_TITLE_ROUTES = new Set(['/dashboard', '/dashboard/huddle', '/dashboard/courier', '/dashboard/guild', '/dashboard/vault', '/dashboard/hustle', '/dashboard/flash', '/dashboard/herald', '/dashboard/mods'])

/* ── Notification URL sanitizer ───────────────── */
// Maps known bad / stale action_urls to correct ROUTES constants.
// Falls back to ROUTES.DASHBOARD for anything unrecognisable.

const KNOWN_ROUTE_VALUES = new Set(Object.values(ROUTES))

function sanitizeNotifUrl(raw: string, title?: string | null): string {
  let url = raw

  // 1. Title-based overrides (most reliable signal)
  const t = (title || '').toLowerCase()
  if (t.includes('artist scroll')) return ROUTES.ARTIST_SCROLL
  if (t.includes('booking')) return ROUTES.BOOKINGS
  if (t.includes('waiver')) return ROUTES.ASSETS
  if (t.includes('quest')) return ROUTES.QUESTS
  if (t.includes('message') || t.includes('courier')) return ROUTES.COURIER
  if (t.includes('schedule')) return ROUTES.SCHEDULE
  if (t.includes('wallet') || t.includes('payment')) return ROUTES.WALLET

  // 2. Path-based fixes for known bad patterns
  if (url.includes('/huddle/bookings') || url.includes('/bookings')) url = ROUTES.BOOKINGS
  else if (url.includes('/codex/waivers') || url.includes('/waivers')) url = ROUTES.ASSETS
  else if (url.includes('/guild/quests') || url.includes('/quests')) url = ROUTES.QUESTS
  else if (url.includes('/courier')) url = ROUTES.COURIER
  else if (url.includes('/schedule')) url = ROUTES.SCHEDULE

  // 3. If URL exactly matches a known route, trust it
  if (KNOWN_ROUTE_VALUES.has(url as typeof ROUTES[keyof typeof ROUTES])) return url

  // 4. Allow valid internal prefixes through
  if (url.startsWith('/dashboard') || url.startsWith('/form') || url.startsWith('/checkin')) return url

  // 5. Fallback: anything else goes to dashboard home
  return ROUTES.DASHBOARD
}

/* ── Component ────────────────────────────────── */

export default function DashboardLayout() {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [isMobilePortalViewport, setIsMobilePortalViewport] = useState(() => window.matchMedia('(max-width: 720px)').matches)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [userPopover, setUserPopover] = useState(false)
  const pageTitle = pageTitles[location.pathname] || 'Dashboard'
  const sidebarRef = useRef<HTMLElement>(null)

  // Swipe left to dismiss sidebar on mobile
  const sidebarSwipe = useSwipeDismiss({
    direction: 'left',
    onDismiss: () => setSidebarOpen(false),
  })

  // ── Notification dropdown state ──
  const [notifOpen, setNotifOpen] = useState(false)
  const [notifList, setNotifList] = useState<Notification[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const notifRef = useRef<HTMLDivElement>(null)
  const playerCardRef = useRef<HTMLDivElement>(null)

  const effectiveRole = useAuthStore((s) => s.effectiveRole)
  const viewAs = useAuthStore((s) => s.viewAs)
  const devMode = useAuthStore((s) => s.devMode)
  const toggleDevMode = useAuthStore((s) => s.toggleDevMode)
  // When View As is active, layout adapts to show the target role's UI chrome
  const layoutRole = viewAs ? effectiveRole : user?.role
  const isArtist = ['assistant', 'artist', 'admin', 'founder'].includes(layoutRole || '')
  const canUseGala = ['admin', 'founder'].includes(layoutRole || '')
  const displayTitle = pageTitle === 'COURIER' && layoutRole === 'collector' ? 'Messages' : pageTitle
  const playerRoleLabel = layoutRole ? layoutRole.charAt(0).toUpperCase() + layoutRole.slice(1) : 'Collector'
  const playerLevelLabel = (() => {
    switch (layoutRole) {
      case 'founder':
        return 'LVL 99 Wizard Architect / Origin'
      case 'admin':
        return 'LVL 70 Operations Architect / Command'
      case 'assistant':
        return 'LVL 40 Guild Coordinator / Support'
      case 'artist':
        return 'LVL 25 Studio Adept / Craft'
      default:
        return 'LVL 10 Initiate / Discovery'
    }
  })()
  const [galaDrawerOpen, setGalaDrawerOpen] = useState(false)
  const [galaPanelCollapsed, setGalaPanelCollapsed] = useState(true)
  const [galaComposeRequest, setGalaComposeRequest] = useState<GalaComposeRequest | null>(null)
  const galaComposeRequestId = useRef(0)
  const visibleGalaDrawerOpen = canUseGala && galaDrawerOpen
  const [posModalOpen, setPosModalOpen] = useState(false)
  const [nexusToolbarItems, setNexusToolbarItems] = useState<NexusToolbarItem[]>([])

  useEffect(() => {
    const query = window.matchMedia('(max-width: 720px)')
    const sync = () => setIsMobilePortalViewport(query.matches)
    sync()
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])

  // Listen for POS open event from toolkit charm
  useEffect(() => {
    const handler = () => setPosModalOpen(true)
    window.addEventListener('ryozan:open-pos', handler)
    return () => window.removeEventListener('ryozan:open-pos', handler)
  }, [])

  // Time-based greeting for nexus header
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning,' : hour < 17 ? 'Good afternoon,' : 'Good evening,'

  // Listen for hotbar actions (charge -> POS, agent -> Gala)
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail
      const action = typeof detail === 'string' ? detail : detail?.action
      if (action === 'charge') setPosModalOpen(true)
      if (action === 'agent-assist' && canUseGala) {
        galaComposeRequestId.current += 1
        setGalaDrawerOpen(true)
        setGalaPanelCollapsed(false)
        setGalaComposeRequest({
          id: galaComposeRequestId.current,
          agentId: detail?.agentId === 'xar' ? 'xar' : 'gala',
          message: detail?.message || 'Help me operate the current dashboard workflow. Keep context from prior pages, stay scoped to the active app, and ask for missing details before suggesting write actions.',
        })
      }
    }
    window.addEventListener('hotbar-action', handler)
    return () => window.removeEventListener('hotbar-action', handler)
  }, [canUseGala])

  // Listen for gala-toggle events (orb in toolkit toggles the Gala panel)
  useEffect(() => {
    const handler = () => {
      if (canUseGala) setGalaPanelCollapsed(prev => !prev)
    }
    window.addEventListener('gala-toggle', handler)
    return () => window.removeEventListener('gala-toggle', handler)
  }, [canUseGala])

  // Broadcast Gala panel state so siblings (e.g. orb in toolkit) can react
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('gala-state', { detail: { collapsed: galaPanelCollapsed } }))
  }, [galaPanelCollapsed])

  useEffect(() => {
    const register = (event: Event) => {
      const detail = (event as CustomEvent<NexusToolbarRegisterDetail>).detail
      setNexusToolbarItems(Array.isArray(detail?.items) ? detail.items : [])
    }
    const clear = () => setNexusToolbarItems([])
    window.addEventListener('ryozan:nexus-toolbar-register', register)
    window.addEventListener('ryozan:nexus-toolbar-clear', clear)
    return () => {
      window.removeEventListener('ryozan:nexus-toolbar-register', register)
      window.removeEventListener('ryozan:nexus-toolbar-clear', clear)
    }
  }, [])

  // ── Unread message count ──
  const [unreadMsgCount, setUnreadMsgCount] = useState(0)

  const fetchMsgCount = useCallback(async () => {
    try {
      const data = await convsApi.list()
      const cutoff = Date.now() - 24 * 60 * 60 * 1000
      const unread = (data.conversations || []).filter(c =>
        c.last_message && new Date(c.last_message.created_at).getTime() > cutoff
      )
      setUnreadMsgCount(unread.length)
    } catch { /* non-critical */ }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: kick off initial fetch + polling interval
    fetchMsgCount()
    const interval = setInterval(fetchMsgCount, 60_000)
    return () => clearInterval(interval)
  }, [fetchMsgCount])

  const fetchNotifications = useCallback(async () => {
    try {
      const data = await notifApi.list({ limit: 3 })
      setNotifList(data.notifications)
      setUnreadCount(data.unread_count)
    } catch {
      // silently fail, notifications are non-critical
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchNotifications()
    const interval = setInterval(fetchNotifications, 60_000)
    return () => clearInterval(interval)
  }, [fetchNotifications])

  const handleMarkRead = async (notif: Notification) => {
    // Immediately mark read in local state and remove from list
    setNotifList(prev => prev.filter(n => n.id !== notif.id))
    if (!notif.is_read) {
      setUnreadCount(prev => Math.max(0, prev - 1))
      notifApi.markRead(notif.id).catch(() => {})
    }
    if (notif.action_url) {
      const url = sanitizeNotifUrl(notif.action_url, notif.title)
      navigate(url)
      setNotifOpen(false)
    }
  }

  const handleMarkAllRead = async () => {
    try {
      await notifApi.markAllRead()
      setNotifList(prev => prev.map(n => ({ ...n, is_read: true })))
      setUnreadCount(0)
    } catch { /* ignore */ }
  }

  // Close dropdowns on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setNotifOpen(false)
      }
      if (playerCardRef.current && !playerCardRef.current.contains(e.target as Node)) {
        setUserPopover(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const userInitial = (user?.name || user?.email || '?').charAt(0).toUpperCase()
  const [avatarBroken, setAvatarBroken] = useState(false)
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setAvatarBroken(false) }, [user?.avatar_url])
  const navSections = getNavSections(viewAs ? effectiveRole : user?.role)
  const devModeMeta = getDevModeMeta()

  const handleLogout = () => {
    logout()
    navigate('/portal')
  }

  const handleBackNavigation = () => {
    if (window.history.length > 1) {
      navigate(-1)
      return
    }
    navigate(ROUTES.DASHBOARD)
  }

  const handleGalaTopbarToggle = () => {
    setGalaDrawerOpen(true)
    setGalaPanelCollapsed(prev => galaDrawerOpen ? !prev : false)
  }

  const closeSidebar = () => setSidebarOpen(false)

  const portalConfigRequested = location.pathname === ROUTES.ARTIST_PROFILE
    && new URLSearchParams(location.search).get('portalConfig') === '1'
  const ownerAccountsRequested = location.pathname === ROUTES.ADMIN_USERS && user?.role === 'founder'
  const redirectedPortalTab = isMobilePortalViewport && !portalConfigRequested && !ownerAccountsRequested
    ? mobilePortalTabForRoute(location.pathname, layoutRole)
    : null
  if (redirectedPortalTab) {
    const targetParams = new URLSearchParams(location.search)
    targetParams.set('portal', redirectedPortalTab)
    return <Navigate to={`${ROUTES.DASHBOARD}?${targetParams.toString()}`} replace />
  }

  if (location.pathname === ROUTES.DASHBOARD) {
    return (
      <section className={`app-view app-view--${layoutRole || 'collector'} app-view--portal-home`}>
        <main className="main-area">
          <div className="dash-content">
            <div className="nexus-body">
              <div className="nexus-canvas">
                <Outlet />
              </div>
            </div>
          </div>
        </main>
      </section>
    )
  }

  return (
    // Original: <main class="portal-shell"> wraps everything
    // but for the dashboard we skip straight to app-view
    // since React Router handles the login/dashboard split
    <section className={`app-view app-view--${layoutRole || 'collector'}${location.pathname === ROUTES.DASHBOARD ? ' app-view--portal-home' : ''}`}>

      {/* ── Sidebar (matches artist-portal.html lines 75-97) ── */}
      <aside
        ref={sidebarRef}
        className={`sidebar${sidebarOpen ? ' mobile-open' : ''}`}
        id="sidebar"
        aria-expanded={sidebarOpen}
        {...sidebarSwipe}
      >
        <div className="sidebar-header">
          <img
            src="/assets/brand/ryozan-mark.svg"
            alt="Ryozan"
            className="sidebar-logo"
            width={50}
            height={50}
          />
        </div>

        <nav className="sidebar-nav">
          {navSections.map((section, si) => (
            <div key={si}>
              {section.label && (
                <div className="sidebar-section-label">{section.label}</div>
              )}
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `sidebar-item${isActive && (!item.to.startsWith(ROUTES.MODS) || sameView(item.to, location.search)) ? ' is-active' : ''}`
                  }
                  data-label={item.label}
                  aria-label={item.label}
                  onClick={closeSidebar}
                >
                  <div className="sidebar-item-icon-wrap" aria-hidden="true">
                    {item.icon}
                    {item.to === ROUTES.COURIER && unreadMsgCount > 0 && (
                      <span className="sidebar-unread-dot" />
                    )}
                  </div>
                  <span className="sidebar-item-label">{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
          {/* Dev Mode toggle — founder/admin only */}
          {(user?.role === 'founder' || user?.role === 'admin') && (
            <button
              className={`sidebar-devmode-toggle${devMode ? ' sidebar-devmode-toggle--on' : ''}`}
              data-label="Dev"
              aria-label="Toggle dev mode"
              onClick={toggleDevMode}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="3" y="4" width="18" height="16" rx="2"/>
                <path d="M8 9l3 3-3 3"/>
                <path d="M13 15h3"/>
              </svg>
              <span className="sidebar-devmode-label">Dev</span>
            </button>
          )}
        </nav>

        <div className="sidebar-footer">
          <a href="/" className="sidebar-item sidebar-return-link" data-label="Return to site" aria-label="Return to site" onClick={closeSidebar}>
            {icons.back}
            <span className="sidebar-return-text">Return to site</span>
          </a>
        </div>
      </aside>

      {/* ── Mobile overlay ── */}
      {sidebarOpen && (
        <div className="sidebar-overlay active" onClick={closeSidebar} />
      )}

      {/* ── Main Area (matches artist-portal.html lines 112-221) ── */}
      <div className="main-area">
        <header className={`topbar${visibleGalaDrawerOpen ? ' gala-active' : ''}`}>
          <div className="topbar-left">
            <button
              className="sidebar-mobile-toggle"
              onClick={() => setSidebarOpen(prev => !prev)}
              aria-label="Toggle menu"
            >
              {icons.menu}
            </button>
            {location.pathname !== ROUTES.DASHBOARD && (
              <button
                type="button"
                className="topbar-back-btn"
                onClick={handleBackNavigation}
                aria-label="Go back"
                title="Back"
              >
                {icons.back}
              </button>
            )}
            <span className="nexus-ryozan-label">Ryozan</span>
            {devModeMeta && (
              <span className={`topbar-env-chip topbar-env-chip--${devModeMeta.tone}`} title={`${devModeMeta.label} • ${devModeMeta.detail}`}>
                <strong>{devModeMeta.label}</strong>
                <small>{devModeMeta.detail}</small>
              </span>
            )}
            {location.pathname !== '/dashboard' && (
              <h1 className={`topbar-page-title${MOD_TITLE_ROUTES.has(location.pathname) ? ' mod-title' : ''}`}>{displayTitle}</h1>
            )}
          </div>
          <div className="topbar-center">
            {devModeMeta && (
              <Suspense fallback={null}>
                <DevModeBarTabs />
              </Suspense>
            )}
          </div>
          <div className="top-actions">
            <NexusSearch />
            {layoutRole === 'collector' && (
              <div className="topbar-stats">
                <span className="topbar-stat">
                  <span className="topbar-stat-value">{user?.bttcoin_balance ?? 0}</span>
                  <span className="topbar-stat-label">RYO</span>
                </span>
                <span className="topbar-stat">
                  <span className="topbar-stat-value">Lv {Math.floor((user?.bttcoin_balance ?? 0) / 300) + 1}</span>
                  <span className="topbar-stat-label">Level</span>
                </span>
              </div>
            )}
            {canUseGala && (
              <button
                type="button"
                className={`topbar-icon-btn topbar-gala-btn${visibleGalaDrawerOpen && !galaPanelCollapsed ? ' active' : ''}`}
                aria-label={visibleGalaDrawerOpen && !galaPanelCollapsed ? 'Close Gala' : 'Open Gala'}
                onClick={handleGalaTopbarToggle}
              >
                <svg className="topbar-svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="11" width="18" height="10" rx="2"/>
                  <circle cx="12" cy="5" r="2"/>
                  <path d="M12 7v4"/>
                  <path d="M8 16h.01"/>
                  <path d="M16 16h.01"/>
                </svg>
              </button>
            )}
            <NavLink to={ROUTES.COURIER} className="topbar-icon-btn" aria-label="Messages">
              {icons.messages}
              {unreadMsgCount > 0 && (
                <span className="topbar-badge">{unreadMsgCount > 9 ? '9+' : unreadMsgCount}</span>
              )}
            </NavLink>
            <div className="notif-dropdown-wrap" ref={notifRef}>
              <button
                className="topbar-icon-btn"
                aria-label="Notifications"
                onClick={() => { setNotifOpen(prev => !prev); if (!notifOpen) fetchNotifications() }}
              >
                {icons.bell}
                {unreadCount > 0 && (
                  <span className="topbar-badge notif-badge">
                    {unreadCount > 9 ? '9+' : unreadCount}
                  </span>
                )}
              </button>

              {notifOpen && (
                <div className="notif-dropdown">
                  <div className="notif-dropdown-header">
                    <span className="notif-dropdown-title">Notifications</span>
                    {unreadCount > 0 && (
                      <button className="notif-mark-all" onClick={handleMarkAllRead}>
                        Mark all read
                      </button>
                    )}
                  </div>

                  <div className="notif-dropdown-list">
                    {notifList.length === 0 ? (
                      <div className="notif-empty">No notifications yet</div>
                    ) : (
                      notifList.map(notif => (
                        <button
                          key={notif.id}
                          className={`notif-item${notif.is_read ? '' : ' notif-unread'}`}
                          onClick={() => handleMarkRead(notif)}
                        >
                          <div className="notif-item-dot" />
                          <div className="notif-item-content">
                            <div className="notif-item-title">{notif.title}</div>
                            <div className="notif-item-msg">{notif.body}</div>
                            <div className="notif-item-time">
                              {timeAgo(notif.created_at)}
                            </div>
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* ── Player Avatar + Card ── */}
            <div className="topbar-player-wrap" ref={playerCardRef}>
              <button
                className="topbar-player-btn"
                onClick={() => setUserPopover(prev => !prev)}
                aria-label="Player menu"
              >
                <div className="topbar-player-avatar">
                  {user?.avatar_url && !avatarBroken
                    ? <img src={user.avatar_url} alt={user.name} onError={() => setAvatarBroken(true)} />
                    : userInitial}
                </div>
              </button>

              {userPopover && (
                <div className="player-card">
                  <div className="player-card-header">
                    <div className="player-card-avatar">
                      {user?.avatar_url && !avatarBroken
                        ? <img src={user.avatar_url} alt={user.name} onError={() => setAvatarBroken(true)} />
                        : userInitial}
                    </div>
                    <div className="player-card-identity">
                      <div className="player-card-name">{user?.name || 'User'}</div>
                      <div className="player-card-role">{playerRoleLabel} / {playerLevelLabel}</div>
                    </div>
                  </div>
                  <div className="player-card-stats">
                    <button className="player-card-stat" onClick={() => { navigate('/dashboard/vault'); setUserPopover(false) }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="12" cy="12" r="10"/><path d="M12 6v12"/><path d="M15.5 9.5a3 3 0 0 0-3-1.5H11a3 3 0 0 0 0 4h2a3 3 0 0 1 0 4h-1.5a3 3 0 0 1-3-1.5"/>
                      </svg>
                      <span className="player-card-stat-value">{user?.bttcoin_balance ?? 0}</span>
                      <span className="player-card-stat-label">Ryo</span>
                    </button>
                    <button className="player-card-stat" onClick={() => { navigate('/dashboard/vault'); setUserPopover(false) }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="2" y="6" width="20" height="14" rx="2"/><path d="M2 10h20"/>
                      </svg>
                      <span className="player-card-stat-value">$0.00</span>
                      <span className="player-card-stat-label">Stripe</span>
                    </button>
                  </div>
                  <div className="player-card-actions">
                    <button className="player-card-action" onClick={() => { navigate('/dashboard/profile'); setUserPopover(false) }}>
                      {icons.profile}
                      <span>View Profile</span>
                    </button>
                    <button className="player-card-action" onClick={() => { navigate('/dashboard/artist-profile'); setUserPopover(false) }}>
                      {icons.settings}
                      <span>Settings</span>
                    </button>
                    <div className="player-card-divider" />
                    <button className="player-card-action danger" onClick={handleLogout}>
                      {icons.logout}
                      <span>Sign Out</span>
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </header>

        {devModeMeta && (
          <Suspense fallback={null}>
            <DevModeBar />
          </Suspense>
        )}
        <PasswordResetBanner />
        <OnboardingBanner />

        <div className={`dash-content${isArtist ? ' nexus-terminal' : ''}`} style={ownerAccountsRequested && isMobilePortalViewport ? { height: 'auto', minHeight: 0, flex: '1 1 auto' } : undefined}>
          {isArtist && location.pathname !== ROUTES.DASHBOARD && nexusToolbarItems.length > 0 ? (
            <header className="NAV_BAR nexus-header nexus-header-global">
              <nav className="NAV_BAR__toolbar nexus-toolbar" aria-label={`${displayTitle} toolbar`}>
                {nexusToolbarItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`NAV_BAR__item nexus-toolbar-item${item.active ? ' active' : ''}`}
                    onClick={item.onClick}
                  >
                    <span className="NAV_BAR__icon nexus-toolbar-icon">{item.icon}</span>
                    <span className="NAV_BAR__label nexus-toolbar-label">{item.label}</span>
                  </button>
                ))}
              </nav>
            </header>
          ) : location.pathname === '/dashboard' && layoutRole === 'collector' ? (
            <div className="cp-greeting">
              <span className="cp-greeting-label">{greeting}</span>
              <span className="cp-greeting-name">{user?.name?.split(' ')[0] || 'Collector'}</span>
            </div>
          ) : null}
          <div className={isArtist ? 'nexus-body' : 'nexus-body'}>
            {isArtist ? (
              <div className="nexus-canvas">
                <Outlet />
              </div>
            ) : (
              <div className="nexus-canvas" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <Outlet />
              </div>
            )}
            {canUseGala && (galaDrawerOpen || !galaPanelCollapsed) && (
              <>
                <Suspense fallback={null}>
                  <GalaPanel
                    className={`${visibleGalaDrawerOpen ? 'drawer-open' : ''}${galaPanelCollapsed ? ' gala-collapsed' : ''}`}
                    composeRequest={galaComposeRequest}
                    onClose={() => setGalaDrawerOpen(false)}
                    onCollapse={() => setGalaPanelCollapsed(true)}
                  />
                </Suspense>
                {visibleGalaDrawerOpen && (
                  <div className="gala-drawer-overlay active" onClick={() => setGalaDrawerOpen(false)} />
                )}
              </>
            )}
          </div>
        </div>
        {/* Legacy desktop Quick Cast hotbar is benched pending its replacement UI. */}

        {/* Mobile action sheet and Gala FAB remain available while the desktop bar is benched. */}
        {['artist', 'admin', 'founder'].includes(layoutRole || '') && (
          <Suspense fallback={null}>
            <MobileActionSheet
              onGalaToggle={() => setGalaDrawerOpen(prev => !prev)}
              galaOpen={visibleGalaDrawerOpen}
              galaEnabled={canUseGala}
            />
          </Suspense>
        )}

        {/* POS Modal, including actions dispatched by the mobile action sheet. */}
        {posModalOpen && (
          <Suspense fallback={null}>
            <POSModal open={posModalOpen} onClose={() => setPosModalOpen(false)} />
          </Suspense>
        )}
      </div>
    </section>
  )
}
