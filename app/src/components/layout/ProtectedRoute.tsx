/**
 * ProtectedRoute: Auth guard for dashboard routes.
 *
 * If not authenticated, redirects to /portal (login page).
 * While checking auth state (isLoading), keeps a blank app-colored canvas so
 * the public-site fallback and dashboard shell never flash between routes.
 *
 * Portals keep loaded records in memory, so an in-app tab switch may send no
 * request at all. The guard re-checks the session on every navigation and when
 * the window regains focus (at most every SESSION_RECHECK_MS); a revoked token then
 * answers 401, which the API client turns into a sign-out (onSessionExpired).
 */

import { useEffect, useRef } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { auth as authApi } from '../../lib/api'
import { useAuthStore } from '../../stores/auth'

const SESSION_RECHECK_MS = 10_000

export default function ProtectedRoute() {
  const { isAuthenticated, isLoading } = useAuthStore()
  const location = useLocation()
  const lastCheck = useRef(0)

  useEffect(() => {
    if (!isAuthenticated) return
    const check = () => {
      lastCheck.current = Date.now()
      // Only a 401 matters here, and the API client already handles it.
      void authApi.me().catch(() => undefined)
    }
    const recheck = () => { if (Date.now() - lastCheck.current >= SESSION_RECHECK_MS) check() }
    // Every in-app navigation (a tab switch changes the search) checks once.
    check()
    const onVisible = () => { if (document.visibilityState === 'visible') recheck() }
    window.addEventListener('focus', recheck)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.removeEventListener('focus', recheck)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [isAuthenticated, location.pathname, location.search])

  if (isLoading) {
    return <div aria-hidden="true" style={{ minHeight: '100dvh', background: '#0b0c0b' }} />
  }

  if (!isAuthenticated) {
    const returnTo = `${location.pathname}${location.search}${location.hash}`
    const loginSearch = new URLSearchParams({ returnTo }).toString()
    return <Navigate to={`/portal?${loginSearch}`} replace />
  }

  return <Outlet />
}
