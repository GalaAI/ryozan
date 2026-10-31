import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from 'react'
import { bookings as bookingsApi, conversations as conversationsApi, type Booking } from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import MobilePortalHome from './MobilePortalHome'

/**
 * Phones get the mobile portal. Tablet and desktop (768px and up) get the
 * native desktop portal, which extends the same system at larger layouts.
 */
const DesktopPortalPage = lazy(() => import('./DesktopPortalPage'))
// A media query breakpoint, not a spacing value.
// eslint-disable-next-line no-restricted-syntax
const desktopQuery = '(min-width: 768px)'
function subscribeViewport(onChange: () => void) {
  const query = window.matchMedia(desktopQuery)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
const desktopSnapshot = () => window.matchMedia(desktopQuery).matches
const serverSnapshot = () => false

export default function DashboardHome() {
  const desktop = useSyncExternalStore(subscribeViewport, desktopSnapshot, serverSnapshot)
  if (desktop) {
    return <Suspense fallback={<p role="status">Loading desktop portal…</p>}><DesktopPortalPage /></Suspense>
  }
  return <MobileDashboardHome />
}

function MobileDashboardHome() {
  const user = useAuthStore((state) => state.user)
  const effectiveRole = useAuthStore((state) => state.effectiveRole)
  const viewAs = useAuthStore((state) => state.viewAs)
  const [bookings, setBookings] = useState<Booking[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)

  const realRole = user?.role ?? 'collector'
  const role = viewAs ? effectiveRole : realRole

  useEffect(() => {
    let active = true

    const load = async () => {
      setLoading(true)
      setLoadError(false)
      setBookings([])
      setUnreadCount(0)

      const shopWideBookings = role === 'founder' || role === 'admin' || role === 'assistant'
      const [bookingResult, conversationResult] = await Promise.allSettled([
        bookingsApi.list({ limit: 100, all: shopWideBookings }),
        conversationsApi.list(),
      ])

      if (!active) return

      if (bookingResult.status === 'fulfilled') {
        setBookings(bookingResult.value.bookings ?? [])
      } else {
        setLoadError(true)
      }

      if (conversationResult.status === 'fulfilled') {
        setUnreadCount(conversationResult.value.conversations.reduce(
          (total, conversation) => total + conversation.unread_count,
          0,
        ))
      } else {
        setUnreadCount(0)
      }

      setLoading(false)
    }

    void load()
    return () => {
      active = false
    }
  }, [role, user?.id])

  return (
    <MobilePortalHome
      role={role}
      bookings={bookings}
      unreadCount={unreadCount}
      loading={loading}
      loadError={loadError}
    />
  )
}
