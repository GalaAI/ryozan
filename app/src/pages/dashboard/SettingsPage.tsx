/**
 * SettingsPage: Account settings including password change, notification preferences,
 * email marketing preferences, and account info.
 */

import { useState, useEffect, useCallback, type FormEvent } from 'react'
import { useAuthStore } from '../../stores/auth'
import { auth, users, googleAuth, plaid, notificationPreferences, ApiError } from '../../lib/api'
import { usePlaidLink } from 'react-plaid-link'
import { useToast } from '../../components/Toast'
import EmailConnectQuest from '../../components/EmailConnectQuest'
import PushOptIn from '../../components/PushOptIn'
import GalaPersonalitySlider from '../../components/GalaPersonalitySlider'

const STAFF_ROLES = ['assistant', 'artist', 'admin', 'founder']

export default function SettingsPage() {
  const { user, fetchMe } = useAuthStore()
  const effectiveRole = useAuthStore((s) => s.effectiveRole)
  const isStaff = STAFF_ROLES.includes(effectiveRole)
  const toast = useToast()

  // ── Password change state ──
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [pwSaving, setPwSaving] = useState(false)
  const [pwSuccess, setPwSuccess] = useState('')
  const [pwError, setPwError] = useState('')

  // ── Notification preferences ──
  const [emailBooking, setEmailBooking] = useState(true)
  const [emailMessages, setEmailMessages] = useState(true)
  const [emailQuests, setEmailQuests] = useState(true)
  const [pushBooking, setPushBooking] = useState(true)
  const [pushMessages, setPushMessages] = useState(true)
  const [pushQuests, setPushQuests] = useState(true)
  const [notifSaving, setNotifSaving] = useState(false)
  const [, setNotifLoaded] = useState(false)

  // ── Marketing preferences ──
  const [marketingOptIn, setMarketingOptIn] = useState(user?.marketing_opt_in ?? false)
  const [marketingSaving, setMarketingSaving] = useState(false)

  // ── Google Calendar ──
  const [googleConnected, setGoogleConnected] = useState(false)
  const [googleEmail, setGoogleEmail] = useState<string | null>(null)
  const [googleHealthy, setGoogleHealthy] = useState(false)
  const [googleReconnectRequired, setGoogleReconnectRequired] = useState(false)
  const [googleSource, setGoogleSource] = useState<string | null>(null)
  const [googleCalendarSummary, setGoogleCalendarSummary] = useState<string | null>(null)
  const [googleReason, setGoogleReason] = useState<string | null>(null)
  const [googleLoading, setGoogleLoading] = useState(true)
  const [googleActionLoading, setGoogleActionLoading] = useState(false)

  // ── Google Calendar: check connection status ──
  useEffect(() => {
    let cancelled = false
    googleAuth.getStatus()
      .then((data) => {
        if (cancelled) return
        setGoogleConnected(data.connected)
        setGoogleEmail(data.email ?? null)
        setGoogleHealthy(Boolean(data.healthy))
        setGoogleReconnectRequired(Boolean(data.reconnect_required))
        setGoogleSource(data.source ?? null)
        setGoogleCalendarSummary(data.calendar_summary ?? null)
        setGoogleReason(data.reason ?? null)
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setGoogleLoading(false) })
    return () => { cancelled = true }
  }, [])

  // ── Google Calendar: detect ?google=connected in URL ──
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('google') === 'connected') {
      toast.success('Google Calendar connected successfully')
      setGoogleConnected(true)
      // Clean up the URL without a reload
      const url = new URL(window.location.href)
      url.searchParams.delete('google')
      window.history.replaceState({}, '', url.pathname + url.search)
      // Re-fetch status to get the email
      googleAuth.getStatus()
        .then((data) => {
          setGoogleConnected(data.connected)
          setGoogleEmail(data.email ?? null)
          setGoogleHealthy(Boolean(data.healthy))
          setGoogleReconnectRequired(Boolean(data.reconnect_required))
          setGoogleSource(data.source ?? null)
          setGoogleCalendarSummary(data.calendar_summary ?? null)
          setGoogleReason(data.reason ?? null)
        })
        .catch(() => {})
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleGoogleConnect = async () => {
    setGoogleActionLoading(true)
    try {
      const { authorization_url } = await googleAuth.getConnectUrl()
      window.location.href = authorization_url
    } catch {
      toast.error('Failed to start Google connection')
      setGoogleActionLoading(false)
    }
  }

  const handleGoogleDisconnect = async () => {
    setGoogleActionLoading(true)
    try {
      await googleAuth.disconnect()
      setGoogleConnected(false)
      setGoogleEmail(null)
      setGoogleHealthy(false)
      setGoogleReconnectRequired(true)
      setGoogleSource('none')
      setGoogleCalendarSummary(null)
      setGoogleReason('not_connected')
      toast.success('Google Calendar disconnected')
    } catch {
      toast.error('Failed to disconnect Google account')
    } finally {
      setGoogleActionLoading(false)
    }
  }

  // ── Plaid Bank Connection ──
  const [plaidConnected, setPlaidConnected] = useState(false)
  const [plaidLoading, setPlaidLoading] = useState(true)
  const [plaidLinkToken, setPlaidLinkToken] = useState<string | null>(null)
  const [plaidActionLoading, setPlaidActionLoading] = useState(false)

  useEffect(() => {
    plaid.status()
      .then((data) => { setPlaidConnected(data.connected) })
      .catch(() => {})
      .finally(() => setPlaidLoading(false))
  }, [])

  const handlePlaidConnect = async () => {
    setPlaidActionLoading(true)
    try {
      const data = await plaid.createLinkToken()
      setPlaidLinkToken(data.link_token)
    } catch {
      toast.error('Failed to start bank connection')
      setPlaidActionLoading(false)
    }
  }

  const onPlaidSuccess = useCallback(async (publicToken: string) => {
    try {
      await plaid.exchangePublicToken(publicToken)
      setPlaidConnected(true)
      toast.success('Bank account connected')
    } catch {
      toast.error('Failed to connect bank account')
    } finally {
      setPlaidActionLoading(false)
      setPlaidLinkToken(null)
    }
  }, [toast])

  const handlePlaidDisconnect = async () => {
    setPlaidActionLoading(true)
    try {
      await plaid.disconnect()
      setPlaidConnected(false)
      toast.success('Bank account disconnected')
    } catch {
      toast.error('Failed to disconnect bank account')
    } finally {
      setPlaidActionLoading(false)
    }
  }

  const { open: openPlaidLink, ready: plaidReady } = usePlaidLink({
    token: plaidLinkToken,
    onSuccess: onPlaidSuccess,
    onExit: () => { setPlaidActionLoading(false); setPlaidLinkToken(null) },
  })

  useEffect(() => {
    if (plaidLinkToken && plaidReady) openPlaidLink()
  }, [plaidLinkToken, plaidReady, openPlaidLink])

  const handlePasswordChange = async (e: FormEvent) => {
    e.preventDefault()
    setPwError('')
    setPwSuccess('')

    if (newPassword !== confirmPassword) {
      setPwError('New passwords do not match')
      return
    }
    if (newPassword.length < 8) {
      setPwError('Password must be at least 8 characters')
      return
    }
    if (currentPassword === newPassword) {
      setPwError('New password must be different from current password')
      return
    }

    setPwSaving(true)
    try {
      await auth.changePassword(currentPassword, newPassword)
      setPwSuccess('Password updated successfully')
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      toast.success('Password updated')
      setTimeout(() => setPwSuccess(''), 5000)
    } catch (err) {
      if (err instanceof ApiError) {
        setPwError(err.message)
      } else {
        setPwError('Something went wrong')
      }
    } finally {
      setPwSaving(false)
    }
  }

  // Load notification preferences from API
  useEffect(() => {
    notificationPreferences.get()
      .then((data) => {
        setEmailBooking(data.email_bookings ?? true)
        setEmailMessages(data.email_messages ?? true)
        setEmailQuests(data.email_quests ?? true)
        setPushBooking(data.push_bookings ?? true)
        setPushMessages(data.push_messages ?? true)
        setPushQuests(data.push_quests ?? true)
        setNotifLoaded(true)
      })
      .catch(() => {})
  }, [])

  const handleNotifSave = async () => {
    setNotifSaving(true)
    try {
      await notificationPreferences.update({
        email_bookings: emailBooking,
        email_messages: emailMessages,
        email_quests: emailQuests,
        push_bookings: pushBooking,
        push_messages: pushMessages,
        push_quests: pushQuests,
      })
      toast.success('Notification preferences saved')
    } catch {
      toast.error('Failed to save notification preferences')
    } finally {
      setNotifSaving(false)
    }
  }

  const handleMarketingToggle = async (checked: boolean) => {
    setMarketingOptIn(checked)
    setMarketingSaving(true)
    try {
      await users.updateProfile({ marketing_opt_in: checked })
      await fetchMe()
      toast.success(checked ? 'Subscribed to email marketing' : 'Unsubscribed from email marketing')
    } catch {
      setMarketingOptIn(!checked)
      toast.error('Failed to update marketing preference')
    } finally {
      setMarketingSaving(false)
    }
  }

  return (
    <div className="dashboard-panel">
      <header className="panel-header">
        <h1 className="panel-title">Settings</h1>
      </header>
      <div style={{ padding: '2rem', maxWidth: '560px', margin: '0 auto' }}>

        {/* ── Password Change ── */}
        <section className="settings-section">
          <h2 className="settings-section-title">Change Password</h2>
          <p className="settings-section-desc">
            Update your account password. You'll need to enter your current password first.
          </p>

          <form onSubmit={handlePasswordChange} className="settings-form">
            <div className="form-group">
              <label className="form-label">Current Password</label>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="form-input"
                required
                autoComplete="current-password"
              />
            </div>

            <div className="form-group">
              <label className="form-label">New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="form-input"
                required
                minLength={8}
                autoComplete="new-password"
              />
            </div>

            <div className="form-group">
              <label className="form-label">Confirm New Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="form-input"
                required
                minLength={8}
                autoComplete="new-password"
              />
            </div>

            {pwError && <div className="settings-msg settings-msg--error">{pwError}</div>}
            {pwSuccess && <div className="settings-msg settings-msg--success">{pwSuccess}</div>}

            <button type="submit" className="btn-gold" disabled={pwSaving}>
              {pwSaving ? 'Updating...' : 'Update Password'}
            </button>
          </form>
        </section>

        {/* ── Email Handle (staff only) ── */}
        {isStaff && (
        <section className="settings-section">
          <h2 className="settings-section-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: '-2px', marginRight: '0.4rem' }}>
              <rect x="2" y="4" width="20" height="16" rx="2" />
              <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
            </svg>
            Email Handle
          </h2>
          <EmailConnectQuest mode="full" />
        </section>
        )}

        {/* ── Google Calendar ── */}
        {isStaff && (
        <section className="settings-section">
          <h2 className="settings-section-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: '-2px', marginRight: '0.4rem' }}>
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
              <line x1="16" y1="2" x2="16" y2="6" />
              <line x1="8" y1="2" x2="8" y2="6" />
              <line x1="3" y1="10" x2="21" y2="10" />
            </svg>
            Google Calendar
          </h2>
          <p className="settings-section-desc">
            Connect your Google account to sync calendar events into the portal and manage availability.
          </p>

          <div className="settings-form">
            {googleLoading ? (
              <div style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>Checking connection...</div>
            ) : googleConnected ? (
              <>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                  padding: '0.75rem 1rem',
                  background: googleReconnectRequired ? 'rgba(248, 113, 113, 0.08)' : 'rgba(34, 197, 94, 0.08)',
                  borderRadius: '8px',
                  border: googleReconnectRequired ? '1px solid rgba(248, 113, 113, 0.25)' : '1px solid rgba(34, 197, 94, 0.2)',
                }}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={googleReconnectRequired ? '#f87171' : '#4ade80'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                    <polyline points="22 4 12 14.01 9 11.01" />
                  </svg>
                  <div>
                    <div style={{ fontSize: '0.85rem', fontWeight: 600, color: googleReconnectRequired ? '#f87171' : '#4ade80' }}>
                      {googleReconnectRequired ? 'Reconnect Required' : googleHealthy ? 'Connected and Healthy' : 'Connected'}
                    </div>
                    {googleEmail && (
                      <div style={{ fontSize: '0.78rem', color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
                        {googleEmail}
                      </div>
                    )}
                    {googleCalendarSummary && (
                      <div style={{ fontSize: '0.78rem', color: 'var(--muted)', fontFamily: "'JetBrains Mono', monospace" }}>
                        Calendar: {googleCalendarSummary}
                      </div>
                    )}
                    {googleSource === 'founder_fallback' && (
                      <div style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>
                        Using founder calendar fallback.
                      </div>
                    )}
                    {googleReason && googleReason !== 'not_connected' && (
                      <div style={{ fontSize: '0.78rem', color: googleReconnectRequired ? '#fca5a5' : 'var(--muted)' }}>
                        Status: {googleReason.replaceAll('_', ' ')}
                      </div>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleGoogleDisconnect}
                  disabled={googleActionLoading}
                  style={{
                    padding: '0.5rem 1rem',
                    fontSize: '0.82rem',
                    fontFamily: "'JetBrains Mono', monospace",
                    background: 'transparent',
                    color: '#f87171',
                    border: '1px solid rgba(220, 50, 50, 0.3)',
                    borderRadius: '6px',
                    cursor: googleActionLoading ? 'wait' : 'pointer',
                    opacity: googleActionLoading ? 0.6 : 1,
                    alignSelf: 'flex-start',
                  }}
                >
                  {googleActionLoading ? 'Disconnecting...' : 'Disconnect'}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={handleGoogleConnect}
                disabled={googleActionLoading}
                className="btn-gold"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: '0.85rem',
                  opacity: googleActionLoading ? 0.6 : 1,
                  cursor: googleActionLoading ? 'wait' : 'pointer',
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
                  <line x1="16" y1="2" x2="16" y2="6" />
                  <line x1="8" y1="2" x2="8" y2="6" />
                  <line x1="3" y1="10" x2="21" y2="10" />
                </svg>
                {googleActionLoading ? 'Connecting...' : 'Connect Google Account'}
              </button>
            )}

            <div style={{
              padding: '0.75rem',
              background: 'var(--bg-surface)',
              borderRadius: '8px',
              border: '1px solid var(--border-subtle)',
              marginTop: '0.25rem',
            }}>
              <p style={{ color: 'var(--muted)', fontSize: '0.78rem', margin: 0, lineHeight: 1.5 }}>
                Grants calendar access so Ryozan can read availability and create, update, or remove booking events.
                You can disconnect at any time.
              </p>
            </div>
          </div>
        </section>
        )}

        {/* ── Plaid Bank Connection ── */}
        <section className="settings-section">
          <h2 className="settings-section-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: '-2px', marginRight: '0.4rem' }}>
              <path d="M2 17V7a2 2 0 012-2h16a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2z"/>
              <path d="M6 9h.01M10 9h.01"/>
            </svg>
            Bank Account
          </h2>
          <p className="settings-section-desc">
            Connect your bank account via Plaid to see your balance in the portal.
          </p>

          <div className="settings-form">
            {plaidLoading ? (
              <div style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>Checking connection...</div>
            ) : plaidConnected ? (
              <>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                  padding: '0.75rem 1rem',
                  background: 'rgba(59, 130, 246, 0.08)',
                  borderRadius: '8px',
                  border: '1px solid rgba(59, 130, 246, 0.2)',
                }}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                    <polyline points="22 4 12 14.01 9 11.01" />
                  </svg>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#60a5fa' }}>Bank Connected</div>
                </div>
                <button
                  type="button"
                  onClick={handlePlaidDisconnect}
                  disabled={plaidActionLoading}
                  style={{
                    padding: '0.5rem 1rem',
                    fontSize: '0.82rem',
                    fontFamily: "'JetBrains Mono', monospace",
                    background: 'transparent',
                    color: '#f87171',
                    border: '1px solid rgba(220, 50, 50, 0.3)',
                    borderRadius: '6px',
                    cursor: plaidActionLoading ? 'wait' : 'pointer',
                    opacity: plaidActionLoading ? 0.6 : 1,
                    alignSelf: 'flex-start',
                  }}
                >
                  {plaidActionLoading ? 'Disconnecting...' : 'Disconnect'}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={handlePlaidConnect}
                disabled={plaidActionLoading}
                className="btn-gold"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: '0.85rem',
                  opacity: plaidActionLoading ? 0.6 : 1,
                  cursor: plaidActionLoading ? 'wait' : 'pointer',
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                {plaidActionLoading ? 'Connecting...' : 'Connect Bank Account'}
              </button>
            )}

            <div style={{
              padding: '0.75rem',
              background: 'var(--bg-surface)',
              borderRadius: '8px',
              border: '1px solid var(--border-subtle)',
              marginTop: '0.25rem',
            }}>
              <p style={{ color: 'var(--muted)', fontSize: '0.78rem', margin: 0, lineHeight: 1.5 }}>
                Securely connects via Plaid. We only read your balance and transaction history. You can disconnect at any time.
              </p>
            </div>
          </div>
        </section>

        {/* ── Email Marketing ── */}
        <section className="settings-section">
          <h2 className="settings-section-title">Email Marketing</h2>
          <p className="settings-section-desc">
            Manage your subscription to Ryozan Studio email campaigns.
            Includes booking announcements, flash events, and exclusive offers.
          </p>

          <div className="settings-form">
            <label className="form-checkbox-label" style={{ opacity: marketingSaving ? 0.6 : undefined }}>
              <input
                type="checkbox"
                checked={marketingOptIn}
                onChange={(e) => handleMarketingToggle(e.target.checked)}
                disabled={marketingSaving}
              />
              Receive email campaigns and marketing updates
            </label>

            <div style={{
              padding: '0.75rem',
              background: 'rgba(250, 174, 21, 0.05)',
              borderRadius: '8px',
              border: '1px solid rgba(250, 174, 21, 0.12)',
              marginTop: '0.5rem',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2">
                  <rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
                </svg>
                <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--gold)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Mailchimp Powered
                </span>
              </div>
              <p style={{ color: 'var(--muted)', fontSize: '0.78rem', margin: 0, lineHeight: 1.5 }}>
                Your email preferences sync with our Mailchimp audiences. You can unsubscribe at any time
                from here or via the unsubscribe link in any email.
              </p>
            </div>
          </div>
        </section>

        {/* ── Notification Preferences ── */}
        <section className="settings-section">
          <h2 className="settings-section-title">Notifications</h2>
          <p className="settings-section-desc">
            Choose which notifications you receive by channel.
          </p>

          <div className="settings-form">
            <h3 style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--gold)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.5rem' }}>Email</h3>
            <label className="form-checkbox-label">
              <input type="checkbox" checked={emailBooking} onChange={(e) => setEmailBooking(e.target.checked)} />
              Booking updates and confirmations
            </label>
            <label className="form-checkbox-label">
              <input type="checkbox" checked={emailMessages} onChange={(e) => setEmailMessages(e.target.checked)} />
              New messages
            </label>
            <label className="form-checkbox-label">
              <input type="checkbox" checked={emailQuests} onChange={(e) => setEmailQuests(e.target.checked)} />
              Quest rewards and challenges
            </label>

            <h3 style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--gold)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.5rem', marginTop: '1rem' }}>Push</h3>
            <label className="form-checkbox-label">
              <input type="checkbox" checked={pushBooking} onChange={(e) => setPushBooking(e.target.checked)} />
              Booking updates and confirmations
            </label>
            <label className="form-checkbox-label">
              <input type="checkbox" checked={pushMessages} onChange={(e) => setPushMessages(e.target.checked)} />
              New messages
            </label>
            <label className="form-checkbox-label">
              <input type="checkbox" checked={pushQuests} onChange={(e) => setPushQuests(e.target.checked)} />
              Quest rewards and challenges
            </label>

            <button
              type="button"
              className="btn-gold"
              onClick={handleNotifSave}
              disabled={notifSaving}
              style={{ marginTop: '0.75rem' }}
            >
              {notifSaving ? 'Saving...' : 'Save Preferences'}
            </button>
          </div>
        </section>

        {/* ── Push Notifications ── */}
        <section className="settings-section">
          <h2 className="settings-section-title">Push Notifications</h2>
          <p className="settings-section-desc">
            Enable browser push notifications to get real-time alerts even when the portal is closed.
          </p>
          <PushOptIn />
        </section>

        {/* ── Gala Personality (staff only) ── */}
        {isStaff && (
        <section className="settings-section">
          <h2 className="settings-section-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: '-2px', marginRight: '0.4rem' }}>
              <path d="M12 2a7 7 0 0 1 7 7c0 3.5-2 6-4 7.5V18H9v-1.5C7 15 5 12.5 5 9a7 7 0 0 1 7-7z" />
              <line x1="9" y1="21" x2="15" y2="21" />
            </svg>
            Gala Personality
          </h2>
          <p className="settings-section-desc">
            Control how much of Gala's personality shows up in your notifications, quest text, and portal messages.
          </p>
          <GalaPersonalitySlider />
        </section>
        )}

        {/* ── Account Info ── */}
        <section className="settings-section">
          <h2 className="settings-section-title">Account</h2>

          <div className="settings-info-row">
            <span className="settings-info-label">Email</span>
            <span className="settings-info-value">{user?.email}</span>
          </div>
          <div className="settings-info-row">
            <span className="settings-info-label">Role</span>
            <span className="settings-info-value" style={{ textTransform: 'capitalize' }}>
              {user?.role}
            </span>
          </div>
          <div className="settings-info-row">
            <span className="settings-info-label">Member since</span>
            <span className="settings-info-value">
              {user?.created_at ? new Date(user.created_at).toLocaleDateString() : '\u2014'}
            </span>
          </div>
        </section>
      </div>
    </div>
  )
}
