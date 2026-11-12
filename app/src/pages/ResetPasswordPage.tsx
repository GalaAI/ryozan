/**
 * ResetPasswordPage: redeem an emailed password-reset link.
 *
 * Route: /reset-password?token=<reset_jwt>
 *
 * The token stays opaque to the browser: nothing here decodes it, and validity,
 * expiry, single-use and type are still decided entirely server-side. But the
 * page now *asks* the server on load, via GET /auth/reset-password/validate,
 * instead of learning nothing until submit.
 *
 * That distinction matters. Previously an expired link rendered a perfectly
 * ordinary form, and the user only found out after inventing a password and
 * typing it twice. Asking up front keeps the server as the sole authority while
 * letting the page say "this expired" before wasting anyone's time, and offer a
 * fresh link on the spot.
 *
 * On success the user is sent to /portal to sign in with the new password.
 */

import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import { auth } from '../lib/api'
import '../styles/join.css'

export default function ResetPasswordPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  // Checked against the server before the form is shown. An expired link used
  // to render a perfectly normal form and only fail at submit, after the user
  // had already invented a password and typed it twice.
  const [tokenState, setTokenState] = useState<'checking' | 'usable' | 'dead'>('checking')
  const [resendEmail, setResendEmail] = useState('')
  const [resendSent, setResendSent] = useState(false)
  const [resendLoading, setResendLoading] = useState(false)

  const token = params.get('token') ?? ''

  // No token at all is dead on arrival (decided during render); otherwise the
  // effect asks the server.
  const [tokenSeen, setTokenSeen] = useState<string | null>(null)
  if (tokenSeen !== token) {
    setTokenSeen(token)
    if (!token) setTokenState('dead')
  }

  useEffect(() => {
    if (!token) return
    let cancelled = false
    auth.validateResetToken(token)
      .then(r => { if (!cancelled) setTokenState(r.usable ? 'usable' : 'dead') })
      // A failed check is not a dead token. Fall through to the form rather
      // than blocking a good link on a blip; the redeem call decides for real.
      .catch(() => { if (!cancelled) setTokenState('usable') })
    return () => { cancelled = true }
  }, [token])

  async function handleResend(e: React.FormEvent) {
    e.preventDefault()
    if (!resendEmail.trim()) return
    setResendLoading(true)
    try {
      await auth.forgotPassword(resendEmail.trim())
    } catch {
      // forgot-password answers identically for known and unknown addresses,
      // so there is nothing useful to show on failure either way.
    } finally {
      setResendSent(true)
      setResendLoading(false)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')

    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }

    setLoading(true)
    try {
      await auth.resetPassword(token, password)
      setDone(true)
      // Reset does not sign the user in. Send them to the portal to log in
      // with the password they just chose.
      setTimeout(() => navigate('/portal', { replace: true }), 1500)
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : 'This reset link is invalid or has expired. Request a new one.'
      )
    } finally {
      setLoading(false)
    }
  }

  if (tokenState === 'checking') {
    return (
      <div className="join-page">
        <div className="join-card">
          <div className="join-logo">
            <img src="/assets/brand/ryozan-mark.svg" alt="Ryozan Studio" />
          </div>
          <p className="join-footnote">Checking your link...</p>
        </div>
      </div>
    )
  }

  // Dead link. Say so plainly and offer a new one right here, because the only
  // thing this person can actually do about it is request another.
  if (tokenState === 'dead') {
    return (
      <div className="join-page">
        <div className="join-card">
          <div className="join-logo">
            <img src="/assets/brand/ryozan-mark.svg" alt="Ryozan Studio" />
          </div>
          <div className="join-token-error">
            <span className="join-token-error-icon">✕</span>
            <p>
              {token
                ? 'This reset link has expired or has already been used.'
                : 'No reset token found. This link may be broken or incomplete.'}
            </p>
          </div>

          {resendSent ? (
            <p className="join-footnote">
              If an account exists for that email, a new link is on its way.
              It is good for 24 hours.
            </p>
          ) : (
            <form onSubmit={handleResend}>
              <label className="join-label" htmlFor="resendEmail">
                Send me a new link
              </label>
              <input
                id="resendEmail"
                type="email"
                className="join-input"
                value={resendEmail}
                onChange={e => setResendEmail(e.target.value)}
                placeholder="you@example.com"
                autoComplete="email"
                required
              />
              <button type="submit" className="join-btn" disabled={resendLoading}>
                {resendLoading ? 'Sending...' : 'Email me a new link'}
              </button>
            </form>
          )}

          <p className="join-footnote">
            <Link to="/portal">Back to sign in</Link>
          </p>
        </div>
      </div>
    )
  }

  if (done) {
    return (
      <div className="join-page">
        <div className="join-card">
          <div className="join-logo">
            <img src="/assets/brand/ryozan-mark.svg" alt="Ryozan Studio" />
          </div>
          <div className="join-welcome">
            <h1>Password updated.</h1>
            <p>Taking you to sign in…</p>
          </div>
          <p className="join-footnote">
            <Link to="/portal">Sign in now</Link>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="join-page">
      <div className="join-card">
        <div className="join-logo">
          <img src="/assets/brand/ryozan-mark.svg" alt="Ryozan Studio" />
        </div>

        <div className="join-welcome">
          <h1>Choose a new password.</h1>
          <p>This link works once, and only for a short window.</p>
        </div>

        <form className="join-form" onSubmit={handleSubmit}>
          <div className="join-field">
            <label htmlFor="reset-password">New password</label>
            <input
              id="reset-password"
              type="password"
              placeholder="At least 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              required
            />
          </div>

          <div className="join-field">
            <label htmlFor="reset-confirm">Confirm new password</label>
            <input
              id="reset-confirm"
              type="password"
              placeholder="Repeat password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </div>

          {error && <p className="join-error">{error}</p>}

          <button type="submit" className="join-btn" disabled={loading}>
            {loading ? 'Updating…' : 'Set New Password'}
          </button>
        </form>

        <p className="join-footnote">
          Remembered it? <Link to="/portal">Sign in here</Link>
        </p>
      </div>
    </div>
  )
}
