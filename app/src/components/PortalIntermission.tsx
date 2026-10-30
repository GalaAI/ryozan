import { useEffect } from 'react'
import LogoLoader from './LogoLoader'

/**
 * PortalIntermission — the portal entry ceremony for paths that have no gate.
 *
 * LoginGate owns the login-entry sequence and plays its own loader. This covers
 * the other doorways: an already-authenticated user returning to the portal,
 * Google sign-in, and registration.
 *
 * It used to iframe `logo-loader/1.0.1`, a superseded desktop-only cut, and
 * derive its exit from `0.96 * duration + 1.42 + buffer + 5.11`. Both are gone.
 * It renders the native composition and waits to be told it finished.
 */
export default function PortalIntermission({ onComplete }: { onComplete: () => void }) {
  // The loader's liquid pour and hang run on GSAP timers, so reduced motion skips
  // the ceremony entirely rather than sitting through it.
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  useEffect(() => { if (reduced) onComplete() }, [reduced, onComplete])
  if (reduced) return null
  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 2147483000, background: '#0b0c0b', overscrollBehavior: 'none', touchAction: 'none' }}
      role="status"
      aria-label="Opening your portal"
      aria-live="polite"
    >
      <LogoLoader onFinished={onComplete} />
    </div>
  )
}
