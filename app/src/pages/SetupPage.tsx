/**
 * SetupPage: claim a new install.
 *
 * Route: /setup. Shown until the install has an owner; after that it sends the
 * visitor to sign in. The server decides both, and refuses a claim without the
 * setup code from its startup log, so finding the URL first gets a stranger nowhere.
 */

import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { studio } from '../lib/api'
import { browserTimezone, studioTimezoneOptions } from '../lib/studio-settings'
import { useAuthStore } from '../stores/auth'
import '../styles/join.css'

export default function SetupPage() {
  const navigate = useNavigate()
  const loginWithToken = useAuthStore((s) => s.loginWithToken)
  const zones = useMemo(() => studioTimezoneOptions(), [])

  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const [setupCode, setSetupCode] = useState('')
  const [studioName, setStudioName] = useState('')
  const [timezone, setTimezone] = useState(browserTimezone)
  const [ownerName, setOwnerName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let active = true
    void studio.getSetupStatus()
      .then((status) => { if (active) setNeedsSetup(status.needs_setup) })
      .catch(() => { if (active) setError('This server is not answering. Try again in a minute.') })
    return () => { active = false }
  }, [])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    setLoading(true)
    try {
      const { access_token } = await studio.claimInstall({
        setup_code: setupCode,
        studio_name: studioName,
        timezone,
        owner_name: ownerName,
        email,
        password,
      })
      await loginWithToken(access_token)
      navigate('/dashboard', { replace: true })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'That did not work. Try again.')
    } finally {
      setLoading(false)
    }
  }

  if (needsSetup === false) return <Navigate to="/portal" replace />

  return (
    <div className="join-page">
      <div className="join-card">
        <div className="join-logo">
          <img src="/assets/brand/ryozan-mark.svg" alt="Ryozan" />
        </div>

        <div className="join-welcome">
          <h1>Set up your studio.</h1>
          <p>You become the owner of this install. You can change the studio details later, in Studio settings.</p>
        </div>

        <form className="join-form" onSubmit={handleSubmit}>
          <div className="join-field">
            <label htmlFor="setup-code">Setup code</label>
            <input
              id="setup-code"
              value={setupCode}
              onChange={(e) => setSetupCode(e.target.value)}
              placeholder="XXXX-XXXX-XXXX"
              autoComplete="off"
              autoFocus
              required
            />
            <p className="join-footnote">It is in your server log, on the line that starts with “[Ryozan] New install”.</p>
          </div>

          <div className="join-field">
            <label htmlFor="setup-studio">Studio name</label>
            <input id="setup-studio" value={studioName} onChange={(e) => setStudioName(e.target.value)} maxLength={120} required />
          </div>

          <div className="join-field">
            <label htmlFor="setup-timezone">Time zone</label>
            <input
              id="setup-timezone"
              list="setup-timezones"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              required
            />
            <datalist id="setup-timezones">
              {zones.map((zone) => <option key={zone} value={zone} />)}
            </datalist>
          </div>

          <div className="join-field">
            <label htmlFor="setup-owner">Your name</label>
            <input id="setup-owner" value={ownerName} onChange={(e) => setOwnerName(e.target.value)} autoComplete="name" maxLength={120} required />
          </div>

          <div className="join-field">
            <label htmlFor="setup-email">Your email</label>
            <input id="setup-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
          </div>

          <div className="join-field">
            <label htmlFor="setup-password">Password</label>
            <input
              id="setup-password"
              type="password"
              placeholder="At least 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
          </div>

          {error && <p className="join-error" role="alert">{error}</p>}

          <button type="submit" className="join-btn" disabled={loading || needsSetup === null}>
            {loading ? 'Setting up…' : 'Create my studio'}
          </button>
        </form>
      </div>
    </div>
  )
}
