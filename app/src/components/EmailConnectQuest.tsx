/**
 * EmailConnectQuest -- full email connect experience for claiming a studio email
 * handle on the install's receiving domain (studio settings `email_domain`).
 *
 * Three steps:
 *   1. Handle picker: input with @<email_domain> suffix, availability check
 *   2. Verification pending: waiting for user to click email verification link
 *   3. Connected: shows claimed handle with green checkmark, release option
 *
 * Two modes:
 *   - compact (default): inline quest card for QuestsPage
 *   - full: standalone section for SettingsPage
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { emailConnect, type EmailStatus, type HandleCheck } from '../lib/api'
import { useStudioSettings } from '../hooks/useStudioSettings'

interface EmailConnectQuestProps {
  /** 'compact' for quest card inline, 'full' for settings page */
  mode?: 'compact' | 'full'
}

export default function EmailConnectQuest({ mode = 'compact' }: EmailConnectQuestProps) {
  const { settings: studioSettings } = useStudioSettings()
  const emailSuffix = studioSettings.email_domain ? `@${studioSettings.email_domain}` : ''
  const [status, setStatus] = useState<EmailStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [handle, setHandle] = useState('')
  const [personalEmail, setPersonalEmail] = useState('')
  const [checking, setChecking] = useState(false)
  const [handleResult, setHandleResult] = useState<HandleCheck | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')
  const [resending, setResending] = useState(false)
  const [resent, setResent] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const [confirmRelease, setConfirmRelease] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  const fetchStatus = useCallback(async () => {
    try {
      const s = await emailConnect.status()
      setStatus(s)
    } catch {
      // No route yet, that's fine
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  // Poll for verification completion when pending
  useEffect(() => {
    if (!status?.connected || status?.verified) return
    const interval = setInterval(async () => {
      try {
        const s = await emailConnect.status()
        if (s.verified) {
          setStatus(s)
          clearInterval(interval)
        }
      } catch { /* ignore */ }
    }, 8000)
    return () => clearInterval(interval)
  }, [status?.connected, status?.verified])

  // Debounced handle availability check
  useEffect(() => {
    if (handle.length < 2) {
      setHandleResult(null)
      return
    }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setChecking(true)
      try {
        const result = await emailConnect.checkHandle(handle)
        setHandleResult(result)
      } catch {
        setHandleResult(null)
      } finally {
        setChecking(false)
      }
    }, 400)
    return () => clearTimeout(debounceRef.current)
  }, [handle])

  const handleClaim = async () => {
    if (!handleResult?.available || !personalEmail) return
    setSubmitting(true)
    setError('')
    try {
      const result = await emailConnect.connect(handleResult.handle, personalEmail)
      setStatus(result)
      setSent(true)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Something went wrong'
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  const handleResend = async () => {
    if (!status?.handle || !status?.forward_to) return
    setResending(true)
    try {
      await emailConnect.resend(status.handle, status.forward_to)
      setResent(true)
      setTimeout(() => setResent(false), 5000)
    } catch { /* ignore */ }
    finally { setResending(false) }
  }

  const handleRelease = async () => {
    if (!confirmRelease) {
      setConfirmRelease(true)
      return
    }
    setReleasing(true)
    try {
      await emailConnect.release()
      setStatus(null)
      setHandle('')
      setPersonalEmail('')
      setSent(false)
      setHandleResult(null)
      setConfirmRelease(false)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to release handle'
      setError(msg)
    } finally {
      setReleasing(false)
    }
  }

  const isFullMode = mode === 'full'

  if (loading) {
    return (
      <div style={isFullMode ? styles.fullContainer : styles.container}>
        <p style={styles.subtext}>Loading email status...</p>
      </div>
    )
  }

  // ── Step 3: Connected & Verified ──
  if (status?.verified) {
    return (
      <div style={isFullMode ? styles.fullContainer : styles.container}>
        <div style={styles.connectedCard}>
          <div style={styles.connectedHeader}>
            <div style={styles.verifiedBadge}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                <polyline points="22 4 12 14.01 9 11.01" />
              </svg>
              <span style={styles.connectedEmail}>{status.bttco_email}</span>
            </div>
            {isFullMode && (
              <span style={styles.activeTag}>Active</span>
            )}
          </div>

          <p style={styles.forwardInfo}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
            Forwarding to {status.forward_to}
          </p>

          {isFullMode && (
            <div style={styles.releaseRow}>
              {confirmRelease ? (
                <div style={styles.confirmGroup}>
                  <span style={styles.confirmText}>Release this handle? This cannot be undone.</span>
                  <div style={styles.confirmButtons}>
                    <button
                      onClick={handleRelease}
                      disabled={releasing}
                      style={styles.btnDanger}
                    >
                      {releasing ? 'Releasing...' : 'Yes, Release'}
                    </button>
                    <button
                      onClick={() => setConfirmRelease(false)}
                      style={styles.btnCancel}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button onClick={handleRelease} style={styles.btnRelease}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M18 6 6 18" /><path d="m6 6 12 12" />
                  </svg>
                  Release Handle
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── Step 2: Verification Pending ──
  if (sent || (status?.connected && !status?.verified)) {
    const targetEmail = status?.forward_to || personalEmail
    return (
      <div style={isFullMode ? styles.fullContainer : styles.container}>
        <div style={styles.pendingCard}>
          <div style={styles.pendingHeader}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="4" width="20" height="16" rx="2" />
              <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
            </svg>
            <span style={styles.pendingTitle}>Verification Sent</span>
          </div>

          <p style={styles.pendingText}>
            We sent a verification link to <strong style={{ color: 'var(--text)' }}>{targetEmail}</strong>.
            Click the link in your email to activate{' '}
            <strong style={{ color: 'var(--gold-vibrant)', fontFamily: "'JetBrains Mono', monospace" }}>
              {status?.bttco_email}
            </strong>
          </p>

          <div style={styles.pendingActions}>
            <button
              onClick={handleResend}
              disabled={resending || resent}
              style={styles.btnLink}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="23 4 23 10 17 10" />
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
              </svg>
              {resent ? 'Sent!' : resending ? 'Resending...' : 'Resend Verification'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  // ── Step 1: Handle Picker ──
  const handleValid = handle.length >= 3 && handle.length <= 20
  const canSubmit = handleResult?.available && personalEmail && handleValid && !submitting

  return (
    <div style={isFullMode ? styles.fullContainer : styles.container}>
      {isFullMode && (
        <p style={styles.sectionDesc}>
          Claim your own <strong style={{ color: 'var(--gold-vibrant)' }}>{emailSuffix || 'studio'}</strong> email address.
          Incoming mail will be forwarded to your personal inbox.
        </p>
      )}

      <p style={styles.label}>Choose your handle</p>

      {/* Handle input with domain suffix */}
      <div style={styles.handleGroup}>
        <input
          type="text"
          placeholder="your-name"
          value={handle}
          onChange={(e) => {
            const val = e.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, '')
            setHandle(val)
            setError('')
          }}
          maxLength={20}
          style={styles.handleInput}
          spellCheck={false}
          autoComplete="off"
        />
        {emailSuffix && <span style={styles.domain}>{emailSuffix}</span>}
      </div>

      {/* Validation hint */}
      {handle.length > 0 && handle.length < 3 && (
        <p style={{ ...styles.hint, color: 'var(--error, #ef5350)' }}>
          Minimum 3 characters
        </p>
      )}

      {/* Availability feedback */}
      {checking && <p style={styles.hint}>Checking availability...</p>}
      {handleResult && !checking && handleValid && (
        <p style={{
          ...styles.hint,
          color: handleResult.available ? 'var(--ok, #4caf50)' : 'var(--error, #ef5350)',
        }}>
          {handleResult.available ? (
            <>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" style={{ verticalAlign: '-1px', marginRight: '4px' }}>
                <polyline points="20 6 9 17 4 12" />
              </svg>
              {handleResult.bttco_email} is available
            </>
          ) : (
            <>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" style={{ verticalAlign: '-1px', marginRight: '4px' }}>
                <path d="M18 6 6 18" /><path d="m6 6 12 12" />
              </svg>
              {handleResult.handle} is taken or reserved
            </>
          )}
        </p>
      )}

      {/* Personal email */}
      {handleResult?.available && handleValid && (
        <>
          <p style={{ ...styles.label, marginTop: '1rem' }}>Forward mail to</p>
          <input
            type="email"
            placeholder="your.email@gmail.com"
            value={personalEmail}
            onChange={(e) => setPersonalEmail(e.target.value)}
            style={styles.emailInput}
            autoComplete="email"
          />
        </>
      )}

      {error && (
        <p style={{ ...styles.hint, color: 'var(--error, #ef5350)' }}>{error}</p>
      )}

      {/* Claim button */}
      {handleResult?.available && handleValid && personalEmail && (
        <button
          onClick={handleClaim}
          disabled={!canSubmit}
          style={{
            ...styles.btnGold,
            opacity: canSubmit ? 1 : 0.5,
          }}
        >
          {submitting ? (
            <>Sending verification...</>
          ) : (
            <>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="4" width="20" height="16" rx="2" />
                <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
              </svg>
              Claim Handle
            </>
          )}
        </button>
      )}
    </div>
  )
}

// ── Styles ──────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  container: {
    padding: '0.75rem 0',
  },
  fullContainer: {
    padding: 0,
  },
  sectionDesc: {
    fontSize: '0.82rem',
    color: 'var(--muted)',
    lineHeight: 1.6,
    marginBottom: '1rem',
  },
  label: {
    fontSize: '0.75rem',
    color: 'var(--muted)',
    marginBottom: '0.4rem',
    fontWeight: 600,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
  },
  handleGroup: {
    display: 'flex',
    alignItems: 'center',
    background: 'rgba(0, 0, 0, 0.3)',
    border: '1px solid rgba(212, 168, 83, 0.25)',
    borderRadius: '8px',
    overflow: 'hidden',
    transition: 'border-color 0.2s',
  },
  handleInput: {
    background: 'transparent',
    border: 'none',
    outline: 'none',
    padding: '0.7rem 0.85rem',
    color: 'var(--text)',
    fontSize: '0.92rem',
    fontFamily: "'JetBrains Mono', monospace",
    fontWeight: 500,
    flex: 1,
    minWidth: 0,
    letterSpacing: '0.02em',
  },
  domain: {
    padding: '0.7rem 0.85rem',
    fontSize: '0.78rem',
    color: 'var(--gold-vibrant)',
    background: 'rgba(212, 168, 83, 0.06)',
    borderLeft: '1px solid rgba(212, 168, 83, 0.15)',
    whiteSpace: 'nowrap' as const,
    fontFamily: "'JetBrains Mono', monospace",
    fontWeight: 500,
    letterSpacing: '0.01em',
  },
  emailInput: {
    width: '100%',
    background: 'rgba(0, 0, 0, 0.3)',
    border: '1px solid rgba(212, 168, 83, 0.25)',
    borderRadius: '8px',
    padding: '0.7rem 0.85rem',
    color: 'var(--text)',
    fontSize: '0.88rem',
    fontFamily: "'JetBrains Mono', monospace",
    outline: 'none',
    boxSizing: 'border-box' as const,
  },
  hint: {
    fontSize: '0.73rem',
    marginTop: '0.35rem',
    fontWeight: 500,
    color: 'var(--muted)',
    display: 'flex',
    alignItems: 'center',
  },
  btnGold: {
    marginTop: '1rem',
    padding: '0.6rem 1.4rem',
    background: 'var(--gold-vibrant)',
    color: 'var(--bg)',
    border: 'none',
    borderRadius: '8px',
    fontSize: '0.82rem',
    fontWeight: 700,
    cursor: 'pointer',
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '0.5rem',
    transition: 'opacity 0.2s',
  },

  // ── Pending state ──
  pendingCard: {
    background: 'rgba(212, 168, 83, 0.04)',
    border: '1px solid rgba(212, 168, 83, 0.15)',
    borderRadius: '10px',
    padding: '1rem',
  },
  pendingHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    color: 'var(--gold-vibrant)',
    marginBottom: '0.6rem',
  },
  pendingTitle: {
    fontSize: '0.88rem',
    fontWeight: 700,
  },
  pendingText: {
    fontSize: '0.8rem',
    color: 'var(--muted)',
    lineHeight: 1.6,
    margin: 0,
  },
  pendingActions: {
    marginTop: '0.75rem',
    display: 'flex',
    gap: '1rem',
    alignItems: 'center',
  },
  btnLink: {
    background: 'none',
    border: 'none',
    color: 'var(--gold-vibrant)',
    fontSize: '0.75rem',
    fontWeight: 600,
    cursor: 'pointer',
    padding: 0,
    display: 'flex',
    alignItems: 'center',
    gap: '0.35rem',
    opacity: 0.85,
  },

  // ── Connected state ──
  connectedCard: {
    background: 'rgba(76, 175, 80, 0.04)',
    border: '1px solid rgba(76, 175, 80, 0.15)',
    borderRadius: '10px',
    padding: '1rem',
  },
  connectedHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  verifiedBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    color: 'var(--ok, #4caf50)',
  },
  connectedEmail: {
    fontSize: '0.92rem',
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.01em',
  },
  activeTag: {
    fontSize: '0.65rem',
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.08em',
    color: 'var(--ok, #4caf50)',
    background: 'rgba(76, 175, 80, 0.1)',
    padding: '0.2rem 0.6rem',
    borderRadius: '4px',
  },
  forwardInfo: {
    fontSize: '0.78rem',
    color: 'var(--muted)',
    marginTop: '0.5rem',
    display: 'flex',
    alignItems: 'center',
    gap: '0.3rem',
  },
  releaseRow: {
    marginTop: '1rem',
    paddingTop: '0.75rem',
    borderTop: '1px solid rgba(255, 255, 255, 0.06)',
  },
  btnRelease: {
    background: 'none',
    border: 'none',
    color: 'var(--muted)',
    fontSize: '0.73rem',
    fontWeight: 600,
    cursor: 'pointer',
    padding: 0,
    display: 'flex',
    alignItems: 'center',
    gap: '0.35rem',
    opacity: 0.7,
  },
  confirmGroup: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '0.5rem',
  },
  confirmText: {
    fontSize: '0.78rem',
    color: 'var(--error, #ef5350)',
    fontWeight: 500,
  },
  confirmButtons: {
    display: 'flex',
    gap: '0.5rem',
  },
  btnDanger: {
    padding: '0.4rem 0.8rem',
    background: 'rgba(239, 83, 80, 0.15)',
    color: 'var(--error, #ef5350)',
    border: '1px solid rgba(239, 83, 80, 0.3)',
    borderRadius: '6px',
    fontSize: '0.75rem',
    fontWeight: 700,
    cursor: 'pointer',
  },
  btnCancel: {
    padding: '0.4rem 0.8rem',
    background: 'rgba(255, 255, 255, 0.04)',
    color: 'var(--muted)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: '6px',
    fontSize: '0.75rem',
    fontWeight: 600,
    cursor: 'pointer',
  },
  subtext: {
    fontSize: '0.75rem',
    color: 'var(--muted)',
    marginTop: '0.3rem',
    lineHeight: 1.5,
  },
}
