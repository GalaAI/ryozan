/**
 * PushOptIn: notification opt-in card for the dashboard.
 *
 * Shows contextual UI based on push notification state:
 * - Unsupported browser: hidden
 * - Permission not yet asked: "Enable Notifications" CTA
 * - Permission denied: explanation + link to browser settings
 * - Permission granted: success state with disable option
 * - Loading: spinner while registering
 */

import { useState, useEffect, useCallback } from 'react'
import {
  isPushSupported,
  getPushPermissionState,
  registerPushNotifications,
  unregisterPushNotifications,
} from '../lib/pushNotifications'

const BellIcon = () => (
  <svg
    width="22"
    height="22"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
    <path d="M13.73 21a2 2 0 0 1-3.46 0" />
  </svg>
)

const CheckIcon = () => (
  <svg
    width="18"
    height="18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.5"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <polyline points="20 6 9 17 4 12" />
  </svg>
)

const BellOffIcon = () => (
  <svg
    width="22"
    height="22"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    <path d="M18.63 13A17.89 17.89 0 0 1 18 8" />
    <path d="M6.26 6.26A5.86 5.86 0 0 0 6 8c0 7-3 9-3 9h14" />
    <path d="M18 8a6 6 0 0 0-9.33-5" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </svg>
)

type PushState = 'unsupported' | 'default' | 'denied' | 'granted' | 'loading' | 'error'

export default function PushOptIn() {
  const [state, setState] = useState<PushState>('default')
  const [error, setError] = useState<string | null>(null)

  const checkState = useCallback(() => {
    if (!isPushSupported()) {
      setState('unsupported')
      return
    }
    const perm = getPushPermissionState()
    if (perm === 'unsupported') {
      setState('unsupported')
    } else {
      setState(perm)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    checkState()
  }, [checkState])

  const handleEnable = async () => {
    setState('loading')
    setError(null)
    try {
      await registerPushNotifications()
      setState('granted')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to enable notifications'
      if (msg.includes('denied')) {
        setState('denied')
      } else {
        setState('error')
        setError(msg)
      }
    }
  }

  const handleDisable = async () => {
    setState('loading')
    try {
      await unregisterPushNotifications()
      setState('default')
    } catch {
      // Best effort, still show as default
      setState('default')
    }
  }

  // Don't render if browser doesn't support push
  if (state === 'unsupported') return null

  return (
    <div style={styles.card}>
      <div style={styles.header}>
        <div style={styles.iconWrap}>
          {state === 'granted' ? <CheckIcon /> : state === 'denied' ? <BellOffIcon /> : <BellIcon />}
        </div>
        <div style={styles.textWrap}>
          {state === 'granted' ? (
            <>
              <span style={styles.title}>Notifications Active</span>
              <span style={styles.subtitle}>You will receive push alerts for bookings, messages, and quests.</span>
            </>
          ) : state === 'denied' ? (
            <>
              <span style={styles.title}>Notifications Blocked</span>
              <span style={styles.subtitle}>
                Your browser has blocked notifications. Open your browser settings to re-enable them for this site.
              </span>
            </>
          ) : state === 'loading' ? (
            <>
              <span style={styles.title}>Setting up...</span>
              <span style={styles.subtitle}>Registering for push notifications.</span>
            </>
          ) : state === 'error' ? (
            <>
              <span style={styles.title}>Something went wrong</span>
              <span style={styles.subtitle}>{error || 'Could not enable notifications. Try again.'}</span>
            </>
          ) : (
            <>
              <span style={styles.title}>Enable Notifications</span>
              <span style={styles.subtitle}>
                Get alerts when bookings come in, clients message you, or new quests drop.
              </span>
            </>
          )}
        </div>
      </div>

      <div style={styles.actions}>
        {state === 'granted' ? (
          <button style={styles.btnSecondary} onClick={handleDisable}>
            Disable
          </button>
        ) : state === 'denied' ? null : state === 'loading' ? (
          <div style={styles.spinner} />
        ) : (
          <button style={styles.btnPrimary} onClick={handleEnable}>
            {state === 'error' ? 'Retry' : 'Turn On'}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Inline styles (matches Ryozan dark theme tokens) ──

const styles: Record<string, React.CSSProperties> = {
  card: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
    padding: '16px',
    borderRadius: '12px',
    background: 'rgba(255, 248, 236, 0.04)',
    border: '1px solid rgba(166, 125, 67, 0.25)',
  },
  header: {
    display: 'flex',
    gap: '12px',
    alignItems: 'flex-start',
  },
  iconWrap: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '40px',
    height: '40px',
    borderRadius: '10px',
    background: 'rgba(166, 125, 67, 0.15)',
    color: '#a67d43',
    flexShrink: 0,
  },
  textWrap: {
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    minWidth: 0,
  },
  title: {
    fontSize: '14px',
    fontWeight: 600,
    color: '#e8dfcf',
  },
  subtitle: {
    fontSize: '12px',
    color: '#b9ad96',
    lineHeight: '1.4',
  },
  actions: {
    display: 'flex',
    justifyContent: 'flex-end',
    minHeight: '36px',
    alignItems: 'center',
  },
  btnPrimary: {
    padding: '8px 20px',
    borderRadius: '8px',
    border: 'none',
    background: '#a67d43',
    color: '#121312',
    fontSize: '13px',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'opacity 0.15s',
  },
  btnSecondary: {
    padding: '8px 20px',
    borderRadius: '8px',
    border: '1px solid rgba(166, 125, 67, 0.35)',
    background: 'transparent',
    color: '#b9ad96',
    fontSize: '13px',
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'opacity 0.15s',
  },
  spinner: {
    width: '20px',
    height: '20px',
    border: '2px solid rgba(166, 125, 67, 0.3)',
    borderTopColor: '#a67d43',
    borderRadius: '50%',
    animation: 'spin 0.8s linear infinite',
  },
}
