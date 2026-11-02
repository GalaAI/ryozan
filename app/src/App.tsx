/**
 * App: Root router for Ryozan.
 *
 * Two layout groups:
 *   1. PublicLayout: site pages (homepage, crew, artist, booking, portal login)
 *   2. DashboardLayout: authenticated pages (behind ProtectedRoute)
 *
 * On mount, if a token exists in memory, attempt to restore session via /auth/me.
 */

import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Suspense, useEffect } from 'react'
import type { ReactNode } from 'react'
import { Analytics } from '@vercel/analytics/react'
import { useAuthStore } from './stores/auth'
import { getToken, hydrateToken } from './lib/api'
import { lazyRetry } from './lib/lazyRetry'
import { beforePublicAnalytics, isProductionAnalytics, publicAnalyticsUrl } from './lib/analyticsPrivacy'

// Layouts
import PublicLayout from './components/layout/PublicLayout'
import ProtectedRoute from './components/layout/ProtectedRoute'
import ErrorBoundary from './components/ErrorBoundary'
import ApiStatusBanner from './components/ApiStatusBanner'
import PortalIntermission from './components/PortalIntermission'
import PortalReveal from './components/PortalReveal'
import PublicSiteSwitch from './components/studio-site/PublicSiteSwitch'

// Public pages: lazy loaded so portal/dashboard entry does not pay for every public surface.
const PublicFormPage = lazyRetry(() => import('./pages/PublicFormPage'))
const AppLandingPage = lazyRetry(() => import('./pages/AppLandingPage'))
const AppPrivacyPage = lazyRetry(() => import('./pages/AppPrivacyPage'))
const AppTermsPage = lazyRetry(() => import('./pages/AppTermsPage'))
const PortalPage = lazyRetry(() => import('./pages/PortalPage'))
const JoinPage = lazyRetry(() => import('./pages/JoinPage'))
const ResetPasswordPage = lazyRetry(() => import('./pages/ResetPasswordPage'))
const QuotePage = lazyRetry(() => import('./pages/QuotePage'))
const QuoteConfirmedPage = lazyRetry(() => import('./pages/QuoteConfirmedPage'))
const NotFoundPage = lazyRetry(() => import('./pages/NotFoundPage'))
// Studio website (PUBLIC_SITE_MODE=studio): only the chosen site's chunk loads.
const StudioHomePage = lazyRetry(() => import('./pages/studio/StudioHomePage'))
const StudioStoryPage = lazyRetry(() => import('./pages/studio/StudioStoryPage'))
const StudioArtistPage = lazyRetry(() => import('./pages/studio/StudioArtistPage'))

// Dashboard pages: lazy loaded with retry (behind auth, not needed on initial load)
const DashboardLayout = lazyRetry(() => import('./components/layout/DashboardLayout'))
const DashboardHome = lazyRetry(() => import('./pages/dashboard/DashboardHome'))
const BookingsManager = lazyRetry(() => import('./pages/dashboard/BookingsManager'))
// MessagesPage deprecated - redirects to /dashboard/courier
const ProfilePage = lazyRetry(() => import('./pages/dashboard/ProfilePage'))
const AdminUsers = lazyRetry(() => import('./pages/dashboard/AdminUsers'))
const ArtistProfileEditor = lazyRetry(() => import('./pages/dashboard/ArtistProfileEditor'))
const ClientsPage = lazyRetry(() => import('./pages/dashboard/ClientsPage'))
// SchedulePage deprecated - schedule now lives in the responsive portal.
const QuestsPage = lazyRetry(() => import('./pages/dashboard/QuestsPage'))
// SettingsPage deprecated - redirects to artist-profile
const WalletPage = lazyRetry(() => import('./pages/dashboard/WalletPage'))
const HuddleLayout = lazyRetry(() => import('./pages/dashboard/HuddleLayout'))
const CourierLayout = lazyRetry(() => import('./pages/dashboard/CourierLayout'))
const EmailFoundryFrame = lazyRetry(() => import('./components/EmailFoundryFrame'))
const GuildPage = lazyRetry(() => import('./pages/dashboard/GuildPage'))
const VaultPage = lazyRetry(() => import('./pages/dashboard/VaultPage'))
const BookPage = lazyRetry(() => import('./pages/dashboard/BookPage'))
const ClubPage = lazyRetry(() => import('./pages/dashboard/ClubPage'))
const LootPage = lazyRetry(() => import('./pages/dashboard/LootPage'))
const ShopPage = lazyRetry(() => import('./pages/dashboard/ShopPage'))
const HustlePage = lazyRetry(() => import('./pages/dashboard/HustlePage'))
const HeraldsPage  = lazyRetry(() => import('./pages/dashboard/HeraldsPage'))

function RedirectWithSearch({ to }: { to: string }) {
  const location = useLocation()
  const [path, targetSearch = ''] = to.split('?')
  const params = new URLSearchParams(targetSearch)
  const currentParams = new URLSearchParams(location.search)

  currentParams.forEach((value, key) => {
    if (!params.has(key)) params.append(key, value)
  })

  const query = params.toString()
  return <Navigate to={`${path}${query ? `?${query}` : ''}${location.hash}`} replace />
}

type AppRole = 'collector' | 'assistant' | 'artist' | 'admin' | 'founder'

function RequireRoles({ roles, children }: { roles: AppRole[], children: ReactNode }) {
  const user = useAuthStore((s) => s.user)
  if (!user || !roles.includes(user.role as AppRole)) {
    return <Navigate to="/dashboard" replace />
  }
  return <>{children}</>
}

function Suspended({ children }: { children: ReactNode }) {
  return <Suspense fallback={null}>{children}</Suspense>
}

/**
 * Suspense for the portal entry.
 *
 * The default `Suspended` falls back to null, which leaves the previous screen
 * painted while the lazy chunk downloads. Entering the portal from the public
 * site that reads as a flash of the homepage before the gate appears. A solid
 * field in the loader's own colour covers the gap, so the transition is dark
 * the whole way in instead of showing the page you just left.
 */
function PortalSuspended({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div style={{ position: 'fixed', inset: 0, background: '#0b0c0b', zIndex: 2147482000 }} />}>
      {children}
    </Suspense>
  )
}

/** /: the Ryozan product landing, or the studio's own site in studio mode. */
function PublicHome() {
  return <PublicSiteSwitch product={<AppLandingPage />} studio={<StudioHomePage />} />
}

/** A studio website page; the product surface has no such page. */
function StudioOnly({ children }: { children: ReactNode }) {
  return <PublicSiteSwitch product={<NotFoundPage />} studio={children} />
}

function PublicPrivacy() {
  return <AppPrivacyPage />
}

function PublicTerms() {
  return <AppTermsPage />
}

function GoogleRouteAnalytics() {
  const location = useLocation()

  useEffect(() => {
    const google = window as Window & {
      gtag?: (
        command: 'config' | 'event',
        target: string,
        params?: Record<string, string | number | boolean>
      ) => void
    }
    if (!isProductionAnalytics()) return
    const safeHref = publicAnalyticsUrl(`${window.location.origin}${location.pathname}`)
    if (!safeHref) return
    const safePath = new URL(safeHref).pathname
    google.gtag?.('event', 'page_view', {
      page_title: safePath,
      page_location: safeHref,
      page_path: safePath,
      page_referrer: publicAnalyticsUrl(document.referrer) || '',
    })
  }, [location])

  return null
}

export default function App() {
  const fetchMe = useAuthStore((s) => s.fetchMe)
  const portalIntermissionActive = useAuthStore((s) => s.portalIntermissionActive)
  const completePortalIntermission = useAuthStore((s) => s.completePortalIntermission)

  // Restore session from localStorage on app startup
  useEffect(() => {
    const hydrated = hydrateToken()
    if (hydrated || getToken()) {
      fetchMe()
    }
  }, [fetchMe])

  return (
    <ErrorBoundary>
    <GoogleRouteAnalytics />
    <ApiStatusBanner />
    <Routes>
      {/* ── Public site ── */}
      <Route element={<PublicLayout />}>
        <Route index element={<Suspended><PublicHome /></Suspended>} />
        <Route path="our-story" element={<Suspended><StudioOnly><StudioStoryPage /></StudioOnly></Suspended>} />
        <Route path="artist/:slug" element={<Suspended><StudioOnly><StudioArtistPage /></StudioOnly></Suspended>} />
        {/* Booking requests use the signed-in booking page. The inherited standalone
            booking canon belonged to the legacy studio app and is not part of Ryozan. */}
        {['book/*', 'ask', 'booking', 'bookings', 'appointment', 'appointments', 'consultation', 'inquiry'].map(path => (
          <Route key={path} path={path} element={<Navigate to="/dashboard/book" replace />} />
        ))}
        <Route path="portal" element={<PortalSuspended><PortalPage /></PortalSuspended>} />
        <Route path="login" element={<Navigate to="/portal" replace />} />
        <Route path="account" element={<Navigate to="/portal" replace />} />
        <Route path="terms" element={<Suspended><PublicTerms /></Suspended>} />
        {/* The product surface has one privacy page and one terms page; every
            inherited legal alias lands on one of them. */}
        {['legal', 'legal/app', 'legal/terms', 'legal/store', 'legal/licenses', 'terms-of-service', 'tos'].map(path => (
          <Route key={path} path={path} element={<Navigate to="/terms" replace />} />
        ))}
        {['legal/privacy', 'legal/sms', 'privacy-policy', 'privacy/policy', 'sms-terms', 'sms'].map(path => (
          <Route key={path} path={path} element={<Navigate to="/privacy" replace />} />
        ))}
        {['checkin/*', 'waiver', 'waiver/:artistSlug'].map(path => (
          <Route key={path} path={path} element={<Navigate to="/portal" replace />} />
        ))}
        <Route path="privacy" element={<Suspended><PublicPrivacy /></Suspended>} />
        <Route path="register" element={<Navigate to="/portal" replace />} />
      </Route>

      {/* ── Public form (magic link, no nav chrome) ── */}
      <Route path="form/:token" element={<Suspended><PublicFormPage /></Suspended>} />

      {/* ── Invite acceptance (no nav chrome) ── */}
      <Route path="join" element={<Suspended><JoinPage /></Suspended>} />

      {/* ── Password reset link target (no nav chrome, no auth) ── */}
      <Route path="reset-password" element={<Suspended><ResetPasswordPage /></Suspended>} />

      {/* ── Public quote pages (no nav chrome, no auth) ── */}
      <Route path="quote/:token" element={<Suspended><QuotePage /></Suspended>} />
      <Route path="quote/:token/confirmed" element={<Suspended><QuoteConfirmedPage /></Suspended>} />

      {/* ── Authenticated dashboard ── */}
      <Route element={<ProtectedRoute />}>
        <Route element={<Suspended><DashboardLayout /></Suspended>}>
          <Route path="dashboard" element={<Suspended><DashboardHome /></Suspended>} />

          {/* Huddl: booking hub (charms render internally, no child routes) */}
          <Route path="dashboard/huddle" element={<RequireRoles roles={['assistant', 'artist', 'admin', 'founder']}><Suspended><HuddleLayout /></Suspended></RequireRoles>} />

          {/* Courier: communication & marketing hub */}
          <Route path="dashboard/courier" element={<Suspended><CourierLayout /></Suspended>} />
          <Route
            path="dashboard/email-foundry"
            element={(
              <RequireRoles roles={['admin', 'founder']}>
                <Suspended><EmailFoundryFrame surface="desktop" /></Suspended>
              </RequireRoles>
            )}
          />

          {/* Guild: engagement & gamification hub */}
          <Route path="dashboard/guild" element={<Suspended><GuildPage /></Suspended>} />

          {/* Operations pages (accessible from Nexus toolbelt links) */}
          {/* Redirect old booking routes to Huddl with bookings charm */}
          <Route path="dashboard/bookings" element={<Suspended><BookingsManager /></Suspended>} />
          <Route path="dashboard/huddle/bookings" element={<RedirectWithSearch to="/dashboard/huddle?charm=bookings" />} />
          <Route path="dashboard/huddle/clients" element={<RequireRoles roles={['assistant', 'artist', 'admin', 'founder']}><Suspended><ClientsPage /></Suspended></RequireRoles>} />
          <Route path="dashboard/huddle/schedule" element={<Navigate to="/dashboard" replace />} />
          {/* Documents now live in the Assets panel on the Nexus dashboard */}
          <Route path="dashboard/huddle/documents" element={<Navigate to="/dashboard?panel=assets" replace />} />

          <Route path="dashboard/quests" element={<Suspended><QuestsPage /></Suspended>} />
          <Route path="dashboard/vault" element={<RequireRoles roles={['assistant', 'artist', 'admin', 'founder']}><Suspended><VaultPage /></Suspended></RequireRoles>} />
          <Route path="dashboard/wallet" element={<Suspended><WalletPage /></Suspended>} />
          <Route path="dashboard/messages" element={<Navigate to="/dashboard/courier" replace />} />
          <Route path="dashboard/profile" element={<Suspended><ProfilePage /></Suspended>} />
          <Route path="dashboard/artist-profile" element={<RequireRoles roles={['artist', 'admin', 'founder']}><Suspended><ArtistProfileEditor /></Suspended></RequireRoles>} />
          <Route path="dashboard/settings" element={<RequireRoles roles={['artist', 'admin', 'founder']}><RedirectWithSearch to="/dashboard/artist-profile" /></RequireRoles>} />
          <Route path="dashboard/scroll" element={<RedirectWithSearch to="/dashboard?portal=hub&workflow=artist-scroll" />} />
          <Route
            path="dashboard/admin/users"
            element={(
              <RequireRoles roles={['admin', 'founder']}>
                <Suspended><AdminUsers /></Suspended>
              </RequireRoles>
            )}
          />

          {/* Collector routes */}
          <Route path="dashboard/book" element={<Suspended><BookPage /></Suspended>} />
          <Route path="dashboard/club" element={<Suspended><ClubPage /></Suspended>} />
          <Route path="dashboard/loot" element={<Suspended><LootPage /></Suspended>} />
          <Route path="dashboard/shop" element={<Suspended><ShopPage /></Suspended>} />
          <Route path="dashboard/hustle" element={<RequireRoles roles={['artist', 'admin', 'founder']}><Suspended><HustlePage /></Suspended></RequireRoles>} />
          <Route path="dashboard/flash" element={<RequireRoles roles={['artist', 'admin', 'founder']}><Navigate to="/dashboard/hustle" replace /></RequireRoles>} />
          <Route path="dashboard/herald" element={<Suspended><HeraldsPage /></Suspended>} />
        </Route>
      </Route>

      {/* ── 404 catch-all ── */}
      <Route path="*" element={<Suspended><NotFoundPage /></Suspended>} />
    </Routes>
    {portalIntermissionActive ? <PortalIntermission onComplete={completePortalIntermission} /> : null}
    {/* Outside <Routes>: it has to keep painting over the portal after the
        page that started it has unmounted. */}
    <PortalReveal />
    {isProductionAnalytics() && <Analytics beforeSend={beforePublicAnalytics} />}
    </ErrorBoundary>
  )
}
