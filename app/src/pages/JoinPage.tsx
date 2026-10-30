/**
 * JoinPage: artist / staff invite acceptance flow.
 *
 * Route: /join?token=<invite_jwt>
 *
 * Decodes the invite token client-side to pre-fill name + email,
 * then lets the user choose a password to activate their account.
 * On success, logs them in and redirects to the dashboard.
 */

import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { auth, setToken } from '../lib/api'
import { useAuthStore } from '../stores/auth'
import '../styles/join.css'

interface InvitePayload {
  email: string
  name: string
  role: string
  slug?: string
  exp: number
}

/** Decode JWT payload without verification (verification happens server-side). */
function decodeJwt(token: string): InvitePayload | null {
  try {
    const base64Url = token.split('.')[1]
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/')
    const json = decodeURIComponent(
      atob(base64)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    )
    return JSON.parse(json)
  } catch {
    return null
  }
}

export default function JoinPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const fetchMe = useAuthStore((s) => s.fetchMe)

  const [payload, setPayload] = useState<InvitePayload | null>(null)
  const [tokenError, setTokenError] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const token = params.get('token') ?? ''

  useEffect(() => {
    if (!token) {
      setTokenError('No invite token found. This link may be broken.')
      return
    }
    const p = decodeJwt(token)
    if (!p) {
      setTokenError('Invalid invite token.')
      return
    }
    if (Date.now() / 1000 > p.exp) {
      setTokenError('This invite link has expired. Ask the shop admin for a new one.')
      return
    }
    setPayload(p)
  }, [token])

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
      const res = await auth.registerInvite(token, password)
      setToken(res.access_token)
      await fetchMe()
      navigate('/dashboard', { replace: true })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Something went wrong. Try again.')
    } finally {
      setLoading(false)
    }
  }

  if (tokenError) {
    return (
      <div className="join-page">
        <div className="join-card">
          <div className="join-logo">
            <img src="/assets/brand/ryozan-mark.svg" alt="Ryozan Studio" />
          </div>
          <div className="join-token-error">
            <span className="join-token-error-icon">✕</span>
            <p>{tokenError}</p>
          </div>
        </div>
      </div>
    )
  }

  if (!payload) {
    return (
      <div className="join-page">
        <div className="join-card join-loading">Verifying invite…</div>
      </div>
    )
  }

  return (
    <div className="join-page">
      <div className="join-card">
        {/* Header */}
        <div className="join-logo">
          <img src="/assets/brand/ryozan-mark.svg" alt="Ryozan Studio" />
        </div>

        <div className="join-welcome">
          <h1>Welcome, {payload.name.split(' ')[0]}.</h1>
          <p>You've been invited to the Ryozan portal as an <strong>{payload.role}</strong>.</p>
          <p>Set a password to activate your account.</p>
        </div>

        {/* Pre-filled identity */}
        <div className="join-identity">
          <div className="join-identity-row">
            <span className="join-identity-label">Name</span>
            <span className="join-identity-value">{payload.name}</span>
          </div>
          <div className="join-identity-row">
            <span className="join-identity-label">Email</span>
            <span className="join-identity-value">{payload.email}</span>
          </div>
          {payload.slug && (
            <div className="join-identity-row">
              <span className="join-identity-label">Artist page</span>
              <span className="join-identity-value">/artist/{payload.slug}</span>
            </div>
          )}
        </div>

        {/* Password form */}
        <form className="join-form" onSubmit={handleSubmit}>
          <div className="join-field">
            <label htmlFor="join-password">Password</label>
            <input
              id="join-password"
              type="password"
              placeholder="At least 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              required
            />
          </div>

          <div className="join-field">
            <label htmlFor="join-confirm">Confirm password</label>
            <input
              id="join-confirm"
              type="password"
              placeholder="Repeat password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </div>

          {error && <p className="join-error">{error}</p>}

          <button type="submit" className="join-btn" disabled={loading}>
            {loading ? 'Activating…' : 'Activate Account'}
          </button>
        </form>

        <p className="join-footnote">
          Already have a password? <a href="/portal">Sign in here</a>
        </p>
      </div>
    </div>
  )
}
