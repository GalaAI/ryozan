import { useEffect } from 'react'
import { useAuthStore } from '../stores/auth'
import './PortalReveal.css'

/**
 * PortalReveal — the last beat of the login sequence.
 *
 * The frozen loader ends on an empty dark field (#0b0c0b) by design; its own
 * composition has already faded out by then. This holds that same field over
 * the top of the freshly mounted portal and dissolves it, so the dashboard is
 * revealed underneath rather than cut to.
 *
 * It renders outside <Routes> on purpose. LoginGate unmounts the moment we
 * navigate, so anything owned by that page would vanish exactly when the fade
 * needs to start.
 *
 * The field colour matches the loader's background exactly, so the handoff from
 * the loader iframe to this overlay is invisible: one continuous dark plane
 * that happens to change owner mid-flight.
 */

/** Beat of stillness before dissolving, so a lazy route can paint underneath. */
const HOLD_MS = 220
/** Dissolve. Matches the gate's easing vocabulary. */
const FADE_MS = 900

export default function PortalReveal() {
  const revealing = useAuthStore((s) => s.portalRevealing)
  const endPortalReveal = useAuthStore((s) => s.endPortalReveal)

  useEffect(() => {
    if (!revealing) return

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) {
      endPortalReveal()
      return
    }

    const done = window.setTimeout(endPortalReveal, HOLD_MS + FADE_MS)
    return () => window.clearTimeout(done)
  }, [revealing, endPortalReveal])

  if (!revealing) return null

  return (
    <div className="ryozan-portal-reveal" aria-hidden="true" />
  )
}
