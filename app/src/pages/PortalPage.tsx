/**
 * PortalPage: Collector's Club entry point.
 *
 * Login is owned by <LoginGate>, the ported Login Gate 1.0.0 composition. It
 * renders full screen and owns the whole first-entry sequence: credentials,
 * loader, handoff. The old login card and its returning-user greeting were
 * replaced by it, not kept alongside it.
 *
 * This page still owns the surfaces the frozen composition cannot host:
 *   - Registration (?mode=register, or ?from=waiver with prefilled name/email)
 *   - Account recovery (?recover=1)
 *
 * On successful auth, redirects to /dashboard.
 */

import { useState, useEffect, type FormEvent } from 'react'
import { useNavigate, Navigate, Link, useSearchParams } from 'react-router-dom'
import { useAuthStore } from '../stores/auth'
import { getRememberedUser } from '../stores/auth'
import { useToast } from '../components/Toast'
import LoginGate from '../components/LoginGate'
import { API_BASE, auth as apiAuth, mcpOAuth, studio as apiStudio, type McpOAuthPreview } from '../lib/api'
import '../styles/artist-portal.css'

function authReturnDestination(searchParams: URLSearchParams): string {
  const requested = searchParams.get('returnTo')
  if (!requested || requested.includes('\\')) return '/dashboard'
  if (
    requested === '/dashboard'
    || requested.startsWith('/dashboard?')
    || requested.startsWith('/dashboard#')
    || requested.startsWith('/dashboard/')
  ) {
    return requested
  }
  return '/dashboard'
}

export default function PortalPage() {
  const remembered = getRememberedUser()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const toast = useToast()

  // Pre-fill from waiver flow query params
  const prefillEmail = searchParams.get('email') || ''
  const prefillName = searchParams.get('name') || ''
  const fromWaiver = searchParams.get('from') === 'waiver'
  const mcpOAuthRequest = searchParams.get('mcp_oauth_request')
  const authReturnTo = authReturnDestination(searchParams)

  // Mode: login or register (default to register if coming from waiver, or when
  // /register redirected here with ?mode=register)
  const wantsRegister = fromWaiver || searchParams.get('mode') === 'register'
  const [mode, setMode] = useState<'login' | 'register'>(wantsRegister ? 'register' : 'login')

  // The frozen Login Gate composition has no "forgot password" or "create
  // account" affordance and its contract forbids adding copy, so those two
  // entries are reached by query param until a 1.0.1 cut adds them:
  //   /portal?recover=1        password recovery
  //   /portal?mode=register    registration
  const wantsRecovery = searchParams.get('recover') === '1'

  // Whether the visitor was ALREADY signed in when this page mounted.
  //
  // This must be captured once, not read reactively. `login()` flips
  // isAuthenticated inside the await, which re-rendered this page and fired the
  // redirect below before LoginGate could report that its sequence had started,
  // so signing in bounced straight past the loader instead of playing it.
  // Reading it at mount removes the race: arriving signed in still redirects,
  // signing in here hands control to the gate.
  const [wasSignedInOnArrival] = useState(() => useAuthStore.getState().isAuthenticated)

  // Shared between the gate prefill and the register form.
  const [email, setEmail] = useState(remembered?.email ?? prefillEmail)
  const [showRecovery, setShowRecovery] = useState(wantsRecovery)
  const [recoveryEmail, setRecoveryEmail] = useState('')
  const [recoveryLoading, setRecoveryLoading] = useState(false)

  // Register state
  const [regName, setRegName] = useState(prefillName)
  const [regEmail, setRegEmail] = useState(prefillEmail)
  const [regPassword, setRegPassword] = useState('')
  const [regConfirm, setRegConfirm] = useState('')
  const [regCode, setRegCode] = useState(searchParams.get('code') ?? '')
  const [regError, setRegError] = useState('')
  const [regLoading, setRegLoading] = useState(false)
  const [mcpPreview, setMcpPreview] = useState<McpOAuthPreview | null>(null)
  const [mcpError, setMcpError] = useState('')
  const [mcpBusy, setMcpBusy] = useState(false)

  const { register, clearError, isAuthenticated, user } = useAuthStore()


  const { loginWithToken } = useAuthStore()

  // Google sign-in is driven as a plain OAuth redirect, not through the Google
  // Identity Services library.
  //
  // Two reasons, and both were live bugs. GSI's renderButton draws Google's own
  // button inside an iframe, so the portal could not keep its designed control.
  // And GSI's redirect mode proves the request with a cross-site g_csrf_token
  // cookie, which Safari blocks and an installed PWA partitions, so it failed on
  // exactly the home-screen install it was added to serve.
  //
  // Navigating to /auth/google/start sidesteps both: any control can trigger it,
  // and legitimacy rides in a signed state parameter rather than a cookie.
  const googleStartUrl = new URL(`${API_BASE}/auth/google/start`, window.location.origin).toString()
  const startGoogleSignIn = () => { window.location.assign(googleStartUrl) }
  const [signInOptions, setSignInOptions] = useState<{ google: boolean; registration: boolean; signup_code_required?: boolean }>({ google: false, registration: false })
  // An empty install has nobody to sign in yet: its first visitor sets it up.
  useEffect(() => {
    let active = true
    void apiStudio.getSetupStatus().then((status) => { if (active && status.needs_setup && !status.has_accounts) navigate('/setup', { replace: true }) }).catch(() => undefined)
    return () => { active = false }
  }, [navigate])
  useEffect(() => { let active = true; void apiAuth.options().then(value => { if (active) setSignInOptions(value) }).catch(() => undefined); return () => { active = false } }, [])

  // The callback hands the session back in the URL fragment, which browsers
  // never transmit to a server. Consume it, then strip it from history so the
  // token is not left sitting in the address bar.
  useEffect(() => {
    const match = /[#&]gsi_token=([^&]+)/.exec(window.location.hash)
    if (match) {
      const token = decodeURIComponent(match[1])
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
      void loginWithToken(token).then(() => {
        if (!mcpOAuthRequest) navigate(authReturnTo, { replace: true })
      }).catch(() => toast.error('Could not finish signing you in. Please try again.'))
      return
    }
    const failure = new URLSearchParams(window.location.search).get('gsi_error')
    if (failure) {
      window.history.replaceState(null, '', window.location.pathname)
      // Say which stage failed. The previous build showed one generic message
      // for every cause, which made a real failure impossible to report.
      const reasons: Record<string, string> = {
        cancelled: 'Google sign-in was cancelled.',
        state: 'That sign-in link expired. Please try again.',
        exchange: 'Google could not confirm that account. Please try again.',
        invalid: 'That Google account could not be verified.',
        unconfigured: 'Google sign-in is not configured. Use email and password.',
      }
      toast.error(reasons[failure] ?? 'Google sign-in failed. Please try again.')
    }
  }, [authReturnTo, navigate, toast, loginWithToken, mcpOAuthRequest])

  useEffect(() => {
    if (!isAuthenticated || !mcpOAuthRequest) return
    mcpOAuth.preview(mcpOAuthRequest)
      .then(setMcpPreview)
      .catch((err) => setMcpError(err instanceof Error ? err.message : 'Authorization request failed'))
  }, [isAuthenticated, mcpOAuthRequest])

  const handleMcpDecision = async (approve: boolean) => {
    if (!mcpOAuthRequest) return
    setMcpBusy(true)
    setMcpError('')
    try {
      const result = await mcpOAuth.decide(mcpOAuthRequest, approve)
      window.location.assign(result.redirect_url)
    } catch (err) {
      setMcpError(err instanceof Error ? err.message : 'Authorization could not be completed')
      setMcpBusy(false)
    }
  }

  if (isAuthenticated && mcpOAuthRequest) {
    return (
      <main className="portal-shell">
        <section className="login-view">
          <div className="login-card">
            <h1 className="club-title">Connect Ryozan</h1>
            <p className="club-subtitle">
              {mcpPreview
                ? `${mcpPreview.client_name} wants to use Ryozan as ${user?.name || user?.email}.`
                : 'Checking this authorization request...'}
            </p>
            {mcpPreview && (
              <div className="stack compact" style={{ textAlign: 'left', margin: '1rem 0' }}>
                <strong>Access requested</strong>
                {mcpPreview.requested_scopes.map((scope) => (
                  <span key={scope} className="small">{scope}</span>
                ))}
                <span className="small">Ryozan role: {mcpPreview.role}</span>
              </div>
            )}
            {mcpError && <div className="error">{mcpError}</div>}
            {mcpPreview && !mcpPreview.can_approve && (
              <div className="error">
                This Ryozan role cannot grant: {mcpPreview.blocked_scopes.join(', ')}
              </div>
            )}
            <div className="auth-modal-actions" style={{ marginTop: '1rem' }}>
              <button
                type="button"
                className="btn btn-outline"
                disabled={mcpBusy}
                onClick={() => void handleMcpDecision(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={mcpBusy || !mcpPreview?.can_approve}
                onClick={() => void handleMcpDecision(true)}
              >
                {mcpBusy ? 'Connecting...' : 'Connect'}
              </button>
            </div>
          </div>
        </section>
      </main>
    )
  }

  // Already signed in on arrival: nothing to gate, go straight through.
  // Signing in on this page is LoginGate's to finish; it navigates itself.
  // An MCP OAuth request is the exception: that flow needs to land on the
  // consent screen above, not the return destination.
  if (wasSignedInOnArrival && !mcpOAuthRequest) {
    return <Navigate to={authReturnTo} replace />
  }


  const handleRegister = async (e: FormEvent) => {
    e.preventDefault()
    setRegError('')

    if (!regName.trim()) {
      setRegError('Name is required')
      return
    }
    if (!regEmail.trim()) {
      setRegError('Email is required')
      return
    }
    if (regPassword.length < 8) {
      setRegError('Password must be at least 8 characters')
      return
    }
    if (regPassword !== regConfirm) {
      setRegError('Passwords do not match')
      return
    }

    setRegLoading(true)
    try {
      const message = await register({
        email: regEmail.trim(),
        password: regPassword,
        name: regName.trim(),
        marketing_opt_in: true,
        signup_code: signInOptions.signup_code_required ? regCode.trim() : undefined,
      })
      toast.info(message)
      if (useAuthStore.getState().isAuthenticated) {
        if (!mcpOAuthRequest) navigate(authReturnTo, { replace: true })
        return
      }
      setRegPassword('')
      setRegConfirm('')
      setEmail(regEmail.trim())
      setMode('login')
    } catch (err) {
      setRegError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setRegLoading(false)
    }
  }

  const handleRecovery = async (e: FormEvent) => {
    e.preventDefault()
    setRecoveryLoading(true)
    try {
      // The API answers identically for known and unknown emails, so the
      // message below must not imply the address was found. Confirming an
      // account exists here would turn this form into an enumeration oracle.
      const res = await apiAuth.forgotPassword(recoveryEmail.trim())
      toast.info(res.message)
      setShowRecovery(false)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : 'Could not send the reset link. Try again in a moment.'
      )
    } finally {
      setRecoveryLoading(false)
    }
  }

  const switchMode = (newMode: 'login' | 'register') => {
    clearError()
    setRegError('')
    setMode(newMode)
    // Sync email between modes
    if (newMode === 'register') {
      setRegEmail(email || prefillEmail)
      setRegName(regName || prefillName)
    } else {
      setEmail(regEmail || prefillEmail)
    }
  }

  // ── Login: the frozen Login Gate composition, wired to the real auth store ──
  // This replaces the old Collector's Club login card entirely. Registration
  // keeps the card below; the gate's composition is frozen and cannot host the
  // extra fields or the mode tabs.
  if (mode === 'login' && !wantsRecovery) {
    return (
      <LoginGate
        initialEmail={remembered?.email ?? prefillEmail}
        // Only offer sign-in paths this install can complete (GET /auth/options).
        onGoogle={signInOptions.google ? startGoogleSignIn : undefined}
        // Login mode returns early with the gate, so the register tabs below
        // were unreachable. This is the only way into account creation for
        // someone arriving at /portal without a hand-typed ?mode=register.
        onCreateAccount={signInOptions.registration ? () => switchMode('register') : undefined}
        onComplete={() => {
          // An MCP OAuth authorization renders its consent screen above once
          // isAuthenticated flips, so the gate must not navigate away from it.
          if (!mcpOAuthRequest) navigate(authReturnTo, { replace: true })
        }}
      />
    )
  }

  return (
    <main className="portal-shell">
      <section className="login-view">
        <div className="login-card">
          {/* Back link */}
          <Link to="/" className="back-link">
            &larr; Back to Ryozan
          </Link>

          {/* Branding */}
          <h1 className="club-title">Collector's Club</h1>

              {/* New/unknown user: subtitle */}
              <p className="club-subtitle">
                {mode === 'register'
                  ? 'Create your account to unlock Side Quests, fast check-ins, and exclusive rewards!'
                  : 'Join Collector\'s Club for Side Quests, fast check-ins, and epic giveaways!'}
              </p>
              <div className="auth-avatar-default" aria-hidden="true">
                <div className="auth-default-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="#999" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                    <circle cx="12" cy="7" r="4" />
                  </svg>
                </div>
              </div>

          {/* Mode tabs */}
          <div style={{
            display: 'flex',
            gap: '0',
            marginTop: '0.75rem',
            marginBottom: '0.25rem',
            borderBottom: '1px solid rgba(250, 174, 21, 0.2)',
          }}>
            <button
              type="button"
              onClick={() => switchMode('login')}
              style={{
                flex: 1,
                padding: '0.6rem',
                background: 'none',
                border: 'none',
                borderBottom: mode === 'login' ? '2px solid var(--gold)' : '2px solid transparent',
                color: mode === 'login' ? 'var(--gold)' : 'var(--muted)',
                fontSize: '0.82rem',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'color 0.2s, border-color 0.2s',
              }}
            >
              Login
            </button>
            <button
              type="button"
              onClick={() => switchMode('register')}
              style={{
                flex: 1,
                padding: '0.6rem',
                background: 'none',
                border: 'none',
                borderBottom: mode === 'register' ? '2px solid var(--gold)' : '2px solid transparent',
                color: mode === 'register' ? 'var(--gold)' : 'var(--muted)',
                fontSize: '0.82rem',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'color 0.2s, border-color 0.2s',
              }}
            >
              Create Account
            </button>
          </div>

          {/* Error banners */}
          {regError && mode === 'register' && (
            <div
              className="error"
              onClick={() => setRegError('')}
              style={{ cursor: 'pointer', marginTop: '0.6rem' }}
            >
              {regError}
            </div>
          )}

          {/* ── Register form ── */}
          {mode === 'register' && (
            <form onSubmit={handleRegister} className="stack auth-panel">
              <label htmlFor="regName">Full Name</label>
              <input
                type="text"
                id="regName"
                className="auth-login-input"
                required
                placeholder="First and Last Name"
                value={regName}
                onChange={(e) => setRegName(e.target.value)}
              />

              <label htmlFor="regEmail">Email</label>
              <input
                type="email"
                id="regEmail"
                className="auth-login-input"
                required
                placeholder="you@email.com"
                value={regEmail}
                onChange={(e) => setRegEmail(e.target.value)}
              />

              <label htmlFor="regPassword">Password</label>
              <input
                type="password"
                id="regPassword"
                className="auth-login-input"
                required
                placeholder="At least 8 characters"
                minLength={8}
                value={regPassword}
                onChange={(e) => setRegPassword(e.target.value)}
              />

              <label htmlFor="regConfirm">Confirm Password</label>
              <input
                type="password"
                id="regConfirm"
                className="auth-login-input"
                required
                placeholder="Re-enter password"
                minLength={8}
                value={regConfirm}
                onChange={(e) => setRegConfirm(e.target.value)}
              />

              {signInOptions.signup_code_required && (
                <>
                  <label htmlFor="regCode">Invite Code</label>
                  <input
                    type="text"
                    id="regCode"
                    className="auth-login-input"
                    required
                    autoComplete="off"
                    placeholder="From the testing instructions"
                    value={regCode}
                    onChange={(e) => setRegCode(e.target.value)}
                  />
                </>
              )}

              {fromWaiver && (
                <p style={{ fontSize: '0.75rem', color: 'var(--muted)', margin: '0.25rem 0 0', lineHeight: 1.5 }}>
                  Your waiver has been submitted. Create an account to track your bookings,
                  earn RYO, and access the Collector's Club.
                </p>
              )}

              <button
                type="submit"
                className="btn btn-primary login-submit-btn"
                disabled={regLoading}
              >
                {regLoading ? 'Creating Account...' : 'Join Collector\'s Club'}
              </button>

              {signInOptions.google && (
                <button
                  type="button"
                  className="btn btn-secondary login-google-btn"
                  onClick={startGoogleSignIn}
                  disabled={regLoading}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
                    <path fill="currentColor" d="M21.35 11.1H12v3.2h5.38c-.5 2.32-2.44 3.66-5.38 3.66a5.96 5.96 0 1 1 0-11.92c1.52 0 2.89.55 3.96 1.45l2.4-2.4A9.9 9.9 0 0 0 12 2.1a9.9 9.9 0 1 0 0 19.8c5.7 0 9.5-4 9.5-9.65 0-.4-.05-.77-.15-1.15Z" />
                  </svg>
                  Continue with Google
                </button>
              )}
            </form>
          )}

          {/* Help link */}
          {mode === 'login' && (
            <div className="auth-help">
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault()
                  setShowRecovery(true)
                }}
              >
                Forgot your password?
              </a>
            </div>
          )}
        </div>
      </section>

      {/* ── Recovery modal ── */}
      {showRecovery && (
        <div
          className="auth-modal"
          role="dialog"
          aria-labelledby="recoveryTitle"
          aria-modal="true"
        >
          <div className="auth-modal-card">
            <h3 id="recoveryTitle">Reset Password</h3>
            <p className="small">
              Enter your account email and we'll send you a link to set a new password.
              {/* Mirrors the API default, Settings.PASSWORD_RESET_EXPIRE_MINUTES. */}
              The link expires in 24 hours and works once.
            </p>
            {/*
              The old "Recover username/email" checkbox was removed rather than
              implemented: accounts are identified by email only (there is no
              username), and you must already know the address to type it into
              this field, so there is nothing to recover. Keeping a control that
              could only ever be a no-op is what caused this bug in the first
              place. With that gone, a "Reset password" checkbox is redundant:
              the form now does exactly one thing and says so.
            */}
            <form onSubmit={handleRecovery} className="stack compact">
              <label htmlFor="recoveryEmail">Account Email</label>
              <input
                type="email"
                id="recoveryEmail"
                required
                placeholder="you@email.com"
                value={recoveryEmail}
                onChange={(e) => setRecoveryEmail(e.target.value)}
              />

              <div className="auth-modal-actions">
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => setShowRecovery(false)}
                  disabled={recoveryLoading}
                >
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={recoveryLoading}>
                  {recoveryLoading ? 'Sending...' : 'Send Reset Link'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  )
}
