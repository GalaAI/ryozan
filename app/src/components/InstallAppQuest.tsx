/**
 * InstallAppQuest — "Signal Flare" quest.
 *
 * Two actions bundled into one card:
 *   1. Add to Home Screen (PWA install)
 *   2. Enable push notifications
 *
 * Platform handling:
 *   - Android / Desktop Chrome/Edge: native beforeinstallprompt captured
 *   - iOS Safari: manual Share → Add to Home Screen instructions
 *   - Already standalone: skip install step, go straight to notifications
 *
 * Completing both actions POSTs the push subscription, which auto-completes
 * the backend "enable-notifications" (Signal Flare) quest and awards XP + Ryo.
 */

import { useState, useEffect } from 'react'
import { push } from '../lib/api'

interface InstallAppQuestProps {
  mode?: 'compact' | 'full'
}

type Step = 'install' | 'ios-guide' | 'notify' | 'done' | 'blocked'

function isIOS() {
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  )
}

function isStandalone() {
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    ('standalone' in navigator && (navigator as { standalone?: boolean }).standalone === true)
  )
}

export default function InstallAppQuest({ mode = 'compact' }: InstallAppQuestProps) {
  // Initial step from the install and notification state at mount.
  const [step, setStep] = useState<Step>(() => {
    const alreadyInstalled = isStandalone()
    const notifGranted = Notification.permission === 'granted'
    if (alreadyInstalled && notifGranted) return 'done'
    if (alreadyInstalled) return 'notify'
    return 'install'
  })
  const [deferredPrompt, setDeferredPrompt] = useState<Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Capture the install prompt before it fires
  useEffect(() => {
    const handler = (e: Event) => {
      e.preventDefault()
      setDeferredPrompt(e as Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> })
    }
    window.addEventListener('beforeinstallprompt', handler)
    return () => window.removeEventListener('beforeinstallprompt', handler)
  }, [])

  const handleInstall = async () => {
    if (isIOS()) {
      setStep('ios-guide')
      return
    }
    if (!deferredPrompt) {
      // Already installed or browser doesn't support — skip to notifications
      setStep('notify')
      return
    }
    setBusy(true)
    try {
      await deferredPrompt.prompt()
      const choice = await deferredPrompt.userChoice
      setDeferredPrompt(null)
      if (choice.outcome === 'accepted') {
        setStep('notify')
      }
    } finally {
      setBusy(false)
    }
  }

  const handleEnableNotifications = async () => {
    setBusy(true)
    setError('')
    try {
      // Register service worker if not already
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        setError('Push notifications are not supported on this device.')
        setBusy(false)
        return
      }

      const permission = await Notification.requestPermission()

      if (permission === 'denied') {
        setStep('blocked')
        setBusy(false)
        return
      }
      if (permission !== 'granted') {
        // User dismissed without choosing
        setBusy(false)
        return
      }

      // Get VAPID key
      const { public_key: vapidKey } = await push.getVapidKey()

      // Subscribe via service worker
      const reg = await navigator.serviceWorker.ready
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      })

      const json = sub.toJSON()
      await push.subscribe({
        endpoint: sub.endpoint,
        keys: {
          p256dh: json.keys?.p256dh ?? '',
          auth: json.keys?.auth ?? '',
        },
      })

      setStep('done')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  const isFull = mode === 'full'

  // ── Done ──
  if (step === 'done') {
    return (
      <div style={isFull ? s.fullWrap : s.wrap}>
        <div style={s.doneCard}>
          <div style={s.doneRow}>
            <div style={s.checkCircle}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <span style={s.doneLabel}>App installed</span>
          </div>
          <div style={{ ...s.doneRow, marginTop: '0.5rem' }}>
            <div style={s.checkCircle}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <span style={s.doneLabel}>Notifications enabled</span>
          </div>
        </div>
      </div>
    )
  }

  // ── Blocked ──
  if (step === 'blocked') {
    return (
      <div style={isFull ? s.fullWrap : s.wrap}>
        <p style={s.blockedText}>
          Notifications are blocked. To re-enable, open your browser or OS settings and allow notifications for this site.
        </p>
      </div>
    )
  }

  // ── iOS guide ──
  if (step === 'ios-guide') {
    return (
      <div style={isFull ? s.fullWrap : s.wrap}>
        <div style={s.guideCard}>
          <p style={s.guideTitle}>Add to Home Screen</p>
          <ol style={s.guideList}>
            <li style={s.guideItem}>
              <span style={s.stepNum}>1</span>
              Tap the <strong style={s.gold}>Share</strong> button at the bottom of Safari
              <svg style={{ marginLeft: '0.35rem', verticalAlign: '-4px' }} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
                <polyline points="16 6 12 2 8 6" />
                <line x1="12" y1="2" x2="12" y2="15" />
              </svg>
            </li>
            <li style={s.guideItem}>
              <span style={s.stepNum}>2</span>
              Scroll down and tap <strong style={s.gold}>Add to Home Screen</strong>
            </li>
            <li style={s.guideItem}>
              <span style={s.stepNum}>3</span>
              Tap <strong style={s.gold}>Add</strong> in the top right
            </li>
          </ol>
          <button style={s.btnSecondary} onClick={() => setStep('notify')}>
            Done, enable notifications
          </button>
        </div>
      </div>
    )
  }

  // ── Notify step ──
  if (step === 'notify') {
    return (
      <div style={isFull ? s.fullWrap : s.wrap}>
        {isFull && (
          <p style={s.desc}>
            Get real-time alerts for booking confirmations, flash drops, and appointment reminders.
          </p>
        )}
        {error && <p style={s.errorText}>{error}</p>}
        <button style={s.btnGold} disabled={busy} onClick={handleEnableNotifications}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </svg>
          {busy ? 'Enabling...' : 'Enable Notifications'}
        </button>
      </div>
    )
  }

  // ── Install step (default) ──
  const iosDevice = isIOS()
  const hasPrompt = !!deferredPrompt
  const alreadyInstalled = isStandalone()

  return (
    <div style={isFull ? s.fullWrap : s.wrap}>
      {isFull && (
        <p style={s.desc}>
          Install the app for instant access and enable notifications to stay in the loop on flash drops and booking updates.
        </p>
      )}

      <div style={s.stepRow}>
        <div style={{ ...s.stepDot, background: 'var(--gold-vibrant)' }}>1</div>
        <div style={s.stepInfo}>
          <span style={s.stepLabel}>Add to Home Screen</span>
          {alreadyInstalled && <span style={s.stepCheck}>Already installed</span>}
        </div>
      </div>

      {!alreadyInstalled && (
        <button
          style={{ ...s.btnGold, marginBottom: '0.75rem' }}
          disabled={busy}
          onClick={handleInstall}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          {iosDevice ? 'Show me how' : hasPrompt ? 'Install App' : 'Add to Home Screen'}
        </button>
      )}

      <div style={s.stepRow}>
        <div style={{ ...s.stepDot, background: alreadyInstalled ? 'var(--gold-vibrant)' : 'rgba(250,174,21,0.25)', color: alreadyInstalled ? 'var(--bg)' : 'var(--gold-vibrant)' }}>2</div>
        <div style={s.stepInfo}>
          <span style={s.stepLabel}>Enable Notifications</span>
        </div>
      </div>

      {alreadyInstalled && (
        <>
          {error && <p style={s.errorText}>{error}</p>}
          <button style={s.btnGold} disabled={busy} onClick={handleEnableNotifications}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.73 21a2 2 0 0 1-3.46 0" />
            </svg>
            {busy ? 'Enabling...' : 'Enable Notifications'}
          </button>
        </>
      )}
    </div>
  )
}

// Converts VAPID base64 key to Uint8Array for PushManager
function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = window.atob(base64)
  return new Uint8Array([...raw].map((c) => c.charCodeAt(0)))
}

const s: Record<string, React.CSSProperties> = {
  wrap: { padding: '0.75rem 0' },
  fullWrap: { padding: 0 },

  desc: {
    fontSize: '0.82rem',
    color: 'var(--muted)',
    lineHeight: 1.6,
    marginBottom: '1rem',
  },

  stepRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.65rem',
    marginBottom: '0.5rem',
  },
  stepDot: {
    width: '22px',
    height: '22px',
    borderRadius: '50%',
    background: 'var(--gold-vibrant)',
    color: 'var(--bg)',
    fontSize: '0.7rem',
    fontWeight: 700,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  stepInfo: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
  },
  stepLabel: {
    fontSize: '0.82rem',
    fontWeight: 600,
    color: 'var(--text)',
  },
  stepCheck: {
    fontSize: '0.7rem',
    color: 'var(--ok, #4caf50)',
    fontWeight: 600,
  },

  btnGold: {
    width: '100%',
    padding: '0.6rem 1.2rem',
    background: 'var(--gold-vibrant)',
    color: 'var(--bg)',
    border: 'none',
    borderRadius: '8px',
    fontSize: '0.82rem',
    fontWeight: 700,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '0.5rem',
    transition: 'opacity 0.2s',
  },
  btnSecondary: {
    marginTop: '0.85rem',
    width: '100%',
    padding: '0.55rem 1rem',
    background: 'rgba(250,174,21,0.1)',
    color: 'var(--gold-vibrant)',
    border: '1px solid rgba(250,174,21,0.3)',
    borderRadius: '8px',
    fontSize: '0.8rem',
    fontWeight: 600,
    cursor: 'pointer',
  },

  doneCard: {
    background: 'rgba(76, 175, 80, 0.05)',
    border: '1px solid rgba(76, 175, 80, 0.18)',
    borderRadius: '10px',
    padding: '0.85rem 1rem',
  },
  doneRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.6rem',
  },
  checkCircle: {
    width: '24px',
    height: '24px',
    borderRadius: '50%',
    background: 'rgba(76, 175, 80, 0.15)',
    color: 'var(--ok, #4caf50)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  doneLabel: {
    fontSize: '0.85rem',
    fontWeight: 600,
    color: 'var(--text)',
  },

  guideCard: {
    background: 'rgba(250, 174, 21, 0.04)',
    border: '1px solid rgba(250, 174, 21, 0.15)',
    borderRadius: '10px',
    padding: '1rem',
  },
  guideTitle: {
    fontSize: '0.88rem',
    fontWeight: 700,
    color: 'var(--text)',
    marginBottom: '0.75rem',
  },
  guideList: {
    paddingLeft: 0,
    listStyle: 'none',
    margin: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: '0.6rem',
  },
  guideItem: {
    fontSize: '0.82rem',
    color: 'var(--muted)',
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    lineHeight: 1.5,
  },
  stepNum: {
    width: '20px',
    height: '20px',
    borderRadius: '50%',
    background: 'rgba(250,174,21,0.15)',
    color: 'var(--gold-vibrant)',
    fontSize: '0.68rem',
    fontWeight: 700,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  gold: { color: 'var(--gold-vibrant)' },

  blockedText: {
    fontSize: '0.8rem',
    color: 'var(--muted)',
    lineHeight: 1.6,
  },
  errorText: {
    fontSize: '0.75rem',
    color: 'var(--error, #ef5350)',
    marginBottom: '0.5rem',
    fontWeight: 500,
  },
}
