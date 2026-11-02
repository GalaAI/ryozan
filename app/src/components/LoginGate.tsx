import { useCallback, useEffect, useRef, useState } from 'react'
import { BRAND } from '../lib/brand'
import type { CSSProperties, FormEvent } from 'react'
import { useAuthStore } from '../stores/auth'
import LogoLoader from './LogoLoader'
import SignInWave from './SignInWave'
import './LoginGate.css'

/**
 * LoginGate: the Ryozan studio sign-in.
 *
 * Ported from the frozen Login Gate 1.0.0 design component
 * (`app/design-reference/login-gate/1.0.0/login-gate.dc.html`,
 * sha256 f64073234d…3541fd). The composition, timings and easings are copied
 * verbatim from that file and are canon. Diff against it before editing.
 *
 * Three things intentionally differ from the frozen component. The first two
 * because the frozen component is a design spec and not an auth flow:
 *
 *  1. It authenticates. The design component captured a password, dropped it,
 *     and signalled success on a blind 9.4s timer, so any password opened the
 *     portal. Here the sequence only advances after `login()` resolves.
 *  2. It has a failure state. On a rejected credential the gate stays on the
 *     login phase and surfaces the store error instead of playing the loader.
 *  3. Its background. The operator replaced the dim mark and its pointer tilt
 *     with <SignInWave /> on 2026-11-02. Design system card: SignInWave.
 *
 * The loader phase renders <LogoLoader />, the native port of the frozen
 * composition, on the app's own gsap. No iframe, no second React, no CDN, and
 * no authoring pause control.
 */

/**
 * Loader tweaks, from the operator's "Login Compare" design file. The shipping
 * defaults inside LogoLoaderMobile.dc.html are NOT these values: without this
 * the loader renders stars at 28/55 instead of 2/0, a 6.5 outline instead of 4,
 * and the two star colours swapped.
 *
 * Delivered through the loader's own `__llSetTweaks` hook, its documented tweak
 * surface, so nothing here reaches into its DOM.
 */
const LOADER_TWEAKS = {
  loop: false,
  duration: 2.4,
  paused: false,
  textAngle: -9,
  textSize: 114,
  lineGap: 0.92,
  strangerShift: 6,
  hangTime: 4.75,
  swell: 1.22,
  welcomeColor: '#fd8e8e',
  strangerColor: '#63badf',
  shadowOn: true,
  shadowX: -5,
  shadowY: 4,
  shadowBlur: 0,
  shadowAlpha: 0.9,
  outlineOn: true,
  outlineSize: 4,
  starsOn: true,
  starScale: 0.8,
  starSpreadX: 2,
  starSpreadY: 0,
  starColor: '#e4d9c0',
  starAltColor: '#c8a15e',
  twinkleSpeed: 0.75,
} as const

/** Frozen: 0.38s login fade, loader phase begins at +380ms. */
const LEAVE_MS = 380
/**
 * Reduced motion still authenticates identically, it just skips the ceremony.
 * The normal path no longer needs a handoff constant: LogoLoader reports when
 * the composition has actually finished, so the host stops re-deriving the cue
 * from duration and buffer arithmetic the way the frozen file forced it to.
 */
const REDUCED_MOTION_HANDOFF_MS = 900

type Phase = 'login' | 'loader' | 'done'

export interface LoginGateProps {
  /** Called once auth has succeeded and the frozen sequence has played out. */
  onComplete: () => void
  /**
   * Starts Google sign-in. The gate keeps its own designed button: this
   * navigates to the API's OAuth start route rather than going through the
   * Google Identity Services library, whose renderButton would replace this
   * control with Google's own iframe button.
   */
  onGoogle?: () => void
  /**
   * Switches the portal into account creation.
   *
   * The gate previously had no signup affordance at all. Register mode exists
   * in PortalPage with proper tabs, but login mode returns early and renders
   * this gate instead, so those tabs never appeared. The only routes in were
   * a hand-typed ?mode=register or arriving from the waiver flow, which left a
   * remote collector with no way to make an account.
   */
  onCreateAccount?: () => void
  /** Prefilled when a remembered user is returning. */
  initialEmail?: string
}

const panelStyle: CSSProperties = {
  position: 'relative',
  zIndex: 1,
  marginBottom: 'var(--lh-panel-mb)',
  display: 'flex',
  flexDirection: 'column',
  gap: 10,
  borderRadius: 16,
  border: '1px solid rgba(166,125,67,.2)',
  background: 'linear-gradient(180deg,rgba(28,29,28,.9),rgba(13,14,13,.96))',
  boxShadow: 'inset 0 1px 0 rgba(232,223,207,.07),0 1px 2px rgba(0,0,0,.5),0 8px 20px -10px rgba(0,0,0,.75)',
  padding: '16px 15px',
}

const inputStyle: CSSProperties = {
  background: 'rgba(13,14,13,.9)',
  border: '1px solid rgba(166,125,67,.2)',
  borderRadius: 10,
  padding: '0 12px',
  fontFamily: "'JetBrains Mono',monospace",
  fontSize: 16,
  color: '#e3dbcb',
  height: 36,
}

const primaryButtonStyle: CSSProperties = {
  minHeight: 44,
  padding: '9px 18px',
  borderRadius: 99,
  border: 'none',
  background: '#D38A68',
  color: '#29140E',
  boxShadow: '0 0 22px 2px #D38A6875',
  fontFamily: "'JetBrains Mono',monospace",
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: '.14em',
  textTransform: 'uppercase',
  cursor: 'pointer',
}

const googleButtonStyle: CSSProperties = {
  minHeight: 44,
  padding: '9px 18px',
  borderRadius: 99,
  border: '1px solid rgba(166,125,67,.35)',
  background: 'rgba(13,14,13,.9)',
  color: '#e3dbcb',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 10,
  fontFamily: "'JetBrains Mono',monospace",
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: '.14em',
  textTransform: 'uppercase',
  cursor: 'pointer',
}

export default function LoginGate({ onComplete, onGoogle, onCreateAccount, initialEmail = '' }: LoginGateProps) {
  const login = useAuthStore((s) => s.login)
  const clearError = useAuthStore((s) => s.clearError)
  const error = useAuthStore((s) => s.error)
  const isLoading = useAuthStore((s) => s.isLoading)
  const completePortalIntermission = useAuthStore((s) => s.completePortalIntermission)
  const beginPortalReveal = useAuthStore((s) => s.beginPortalReveal)

  const [phase, setPhase] = useState<Phase>('login')
  const [email, setEmail] = useState(initialEmail)
  const [password, setPassword] = useState('')
  const [leaving, setLeaving] = useState(false)
  const [reducedMotion, setReducedMotion] = useState(() =>
    window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  const loaderRef = useRef<HTMLIFrameElement>(null)
  const timers = useRef<number[]>([])

  /** Seed the loader with LOADER_TWEAKS once its hook exists; the frame boots
   *  its own runtime, so retry until `__llSetTweaks` appears, then stop. */
  useEffect(() => {
    if (phase !== 'loader') return
    let done = false
    const seed = () => {
      if (done) return true
      const w = loaderRef.current?.contentWindow as (Window & {
        __llSetTweaks?: (t: Record<string, unknown>) => void
      }) | null
      if (!w?.__llSetTweaks) return false
      w.__llSetTweaks({ ...LOADER_TWEAKS })
      done = true
      return true
    }
    if (seed()) return
    const poll = window.setInterval(() => { if (seed()) window.clearInterval(poll) }, 50)
    return () => window.clearInterval(poll)
  }, [phase])

  const after = useCallback((fn: () => void, ms: number) => {
    timers.current.push(window.setTimeout(fn, ms))
  }, [])

  useEffect(() => () => { timers.current.forEach(window.clearTimeout) }, [])

  /**
   * The gate owns its viewport and scrolls when its form cannot fit. The app's body is
   * `overflow: hidden auto`, which on systems set to always-visible scrollbars
   * paints a bar down the edge of the login screen. Locked for as long as the
   * gate is mounted, and restored exactly as found on the way out.
   */
  useEffect(() => {
    const body = document.body
    const previous = body.style.overflow
    body.style.overflow = 'hidden'
    return () => { body.style.overflow = previous }
  }, [])

  useEffect(() => {
    const q = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReducedMotion(q.matches)
    q.addEventListener('change', update)
    return () => q.removeEventListener('change', update)
  }, [])

  /**
   * Auth first, ceremony second. This ordering is the whole point of the port:
   * a rejected credential never reaches the loader.
   */
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (leaving || isLoading) return
    clearError()

    try {
      await login(email, password)
    } catch {
      // The store holds the message; stay on the login phase and show it.
      return
    }

    // The gate is itself the portal entry ceremony, so clear the separate
    // intermission the store queues on login. Without this the app would
    // stack PortalIntermission on top of the gate's own loader.
    completePortalIntermission()

    setLeaving(true)
    after(() => setPhase('loader'), LEAVE_MS)
    // Reduced motion never plays the composition, so nothing will report back.
    if (reducedMotion) after(finish, REDUCED_MOTION_HANDOFF_MS)
  }

  /**
   * Raise the dark field FIRST so it is already painting when the route swaps,
   * then navigate. PortalReveal lives above the router and fades it away over
   * the mounted portal, so the loader dissolves onto the dashboard instead of
   * cutting to it. Guarded because the reduced-motion timer and the loader's
   * own callback are two paths to the same place.
   */
  const finished = useRef(false)
  const finish = () => {
    if (finished.current) return
    finished.current = true
    setPhase('done')
    beginPortalReveal()
    onComplete()
  }

  return (
    <div className="lg-root">
      {phase === 'login' && (
        <div
          style={{
            minHeight: '100%',
            display: 'flex',
            flexDirection: 'column',
            // Keep the login composition clear of the installed app status bar.
            // viewport-fit=cover exposes safe-area insets on capable devices.
            padding: 'calc(min(54px,5vh) + env(safe-area-inset-top)) 18px calc(18px + env(safe-area-inset-bottom))',
            animation: leaving ? 'lhFadeOut .38s cubic-bezier(.16,1,.3,1) both' : 'none',
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          <SignInWave />

          <div data-rise="1" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, paddingTop: 'min(46px,4vh)', position: 'relative', zIndex: 1 }}>
            <span style={{
              fontFamily: "'KC Tallboy','Bebas Neue',sans-serif",
              fontSize: 'var(--lh-title-fs)', lineHeight: 1, letterSpacing: '.06em',
              textTransform: 'uppercase', color: '#e4d9c0', textAlign: 'center',
              filter: 'drop-shadow(-7px -2px 0 rgba(99,41,35,.9)) drop-shadow(0 2px 10px rgba(0,0,0,.85))',
              marginTop: 'var(--lh-title-mt)',
            }}>{BRAND.product}</span>
            <span style={{
              fontFamily: "'JetBrains Mono',monospace", fontSize: 14, fontWeight: 700,
              letterSpacing: '.26em', textTransform: 'uppercase', color: '#D38A68',
              background: 'rgba(11,12,11,.72)', padding: '7px 16px', borderRadius: 99,
              textShadow: '0 1px 4px rgba(0,0,0,.9)', marginTop: -15,
            }}>Studio sign-in</span>
          </div>

          <div style={{ flex: 1 }} />

          <form data-rise="2" style={panelStyle} onSubmit={submit} noValidate>
            <input
              data-rise="3" value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="Email" type="email" inputMode="email" autoComplete="email"
              aria-label="Email" style={inputStyle}
            />
            <input
              data-rise="4" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="Password" type="password" autoComplete="current-password"
              aria-label="Password" style={inputStyle}
            />
            {error && <div className="lg-error" role="alert">{error}</div>}
            <button data-rise="5" type="submit" disabled={isLoading || leaving} style={primaryButtonStyle}>
              {isLoading ? 'Checking' : 'Let me in'}
            </button>
            {onGoogle && <>
            <div data-rise="6" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '2px 0' }}>
              <span style={{ flex: 1, height: 1, background: 'rgba(166,125,67,.2)' }} />
              <span style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 9.5, letterSpacing: '.16em', textTransform: 'uppercase', color: 'rgba(227,219,203,.4)' }}>or</span>
              <span style={{ flex: 1, height: 1, background: 'rgba(166,125,67,.2)' }} />
            </div>
            <button data-rise="7" type="button" onClick={onGoogle} disabled={isLoading || leaving} style={googleButtonStyle}>
              <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
                <path fill="#e3dbcb" d="M21.35 11.1H12v3.2h5.38c-.5 2.32-2.44 3.66-5.38 3.66a5.96 5.96 0 1 1 0-11.92c1.52 0 2.89.55 3.96 1.45l2.4-2.4A9.9 9.9 0 0 0 12 2.1a9.9 9.9 0 1 0 0 19.8c5.7 0 9.5-4 9.5-9.65 0-.4-.05-.77-.15-1.15Z" />
              </svg>
              Google
            </button>
            </>}
            {onCreateAccount && (
              <div
                data-rise="8"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 6,
                  paddingTop: 4,
                  fontFamily: "'JetBrains Mono',monospace",
                  fontSize: 10.5,
                  letterSpacing: '.12em',
                  textTransform: 'uppercase',
                  color: 'rgba(227,219,203,.45)',
                }}
              >
                <span>New here?</span>
                <button
                  type="button"
                  onClick={onCreateAccount}
                  disabled={isLoading || leaving}
                  style={{
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    cursor: isLoading || leaving ? 'default' : 'pointer',
                    font: 'inherit',
                    letterSpacing: 'inherit',
                    textTransform: 'inherit',
                    color: '#c8a15e',
                    textDecoration: 'underline',
                    textUnderlineOffset: 3,
                  }}
                >
                  Create an account
                </button>
              </div>
            )}
          </form>
        </div>
      )}

      {phase !== 'login' && (
        <div style={{ position: 'absolute', inset: 0 }} role="status" aria-label="Opening your portal" aria-live="polite">
          {phase === 'loader' && <LogoLoader onFinished={finish} />}
        </div>
      )}
    </div>
  )
}
