/**
 * QuoteConfirmedPage: Post-payment success page.
 *
 * Flow:
 * 1. Collector's Club modal auto-triggers on page load
 * 2. User picks "Hell Yea!" (→ Google signup) or "No thanks, I'm a loner" (→ dismiss)
 * 3. Modal shows confirmation animation → auto-closes
 * 4. "You're Booked!" page revealed with parking, calendar, etc.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { customerPrice, formatTime12h } from '../lib/format'
import { BRAND } from '../lib/brand'
import { API_BASE, auth as apiAuth, GOOGLE_CLIENT_ID } from '../lib/api'
import { fireAppointmentCelebration } from '../lib/appointmentCelebration'
import { AnimatedTicket } from '@/components/ui/ticket-confirmation-card'

/* ── Types ─────────────────────────────────────────────── */

interface BookingDetails {
  client_name?: string
  artist_name: string
  description: string
  placement: string | null
  scheduled_date: string | null
  scheduled_time: string | null
  duration_minutes: number | null
  deposit_amount: number
  reference_code: string | null
}

/* ── Calendar helpers ──────────────────────────────────── */

function buildCalendarDate(date: string, time: string | null): { start: string; end: string } {
  const d = date.replace(/-/g, '')
  if (!time) return { start: d, end: d }
  const t = time.replace(/:/g, '')
  const start = `${d}T${t.padEnd(6, '0')}00`
  const end = `${d}T${String(parseInt(t.slice(0, 2)) + 2).padStart(2, '0')}${t.slice(2).padEnd(4, '0')}00`
  return { start, end }
}

function buildGoogleCalUrl(details: BookingDetails): string | null {
  if (!details.scheduled_date) return null
  const { start, end } = buildCalendarDate(details.scheduled_date, details.scheduled_time)
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: 'Tattoo Session | Ryozan Studio',
    dates: `${start}/${end}`,
    details: `Artist: ${details.artist_name}\n${details.description?.slice(0, 200) || ''}\nPlacement: ${details.placement || 'TBD'}\nRef: ${details.reference_code || ''}`,
    location: BRAND.studio,
  })
  return `https://calendar.google.com/calendar/render?${params.toString()}`
}

function buildIcsContent(details: BookingDetails): string | null {
  if (!details.scheduled_date) return null
  const { start, end } = buildCalendarDate(details.scheduled_date, details.scheduled_time)
  const now = new Date().toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z'
  return [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Ryozan//Booking//EN',
    'BEGIN:VEVENT', `DTSTART:${start}`, `DTEND:${end}`, `DTSTAMP:${now}`,
    'SUMMARY:Tattoo Session | Ryozan Studio',
    `DESCRIPTION:Artist: ${details.artist_name}\\nPlacement: ${details.placement || 'TBD'}\\nRef: ${details.reference_code || ''}`,
    `LOCATION:${BRAND.studio}`,
    'END:VEVENT', 'END:VCALENDAR',
  ].join('\r\n')
}

function downloadIcs(details: BookingDetails) {
  const content = buildIcsContent(details)
  if (!content) return
  const blob = new Blob([content], { type: 'text/calendar;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'bttco-appointment.ics'
  a.click()
  URL.revokeObjectURL(url)
}

/* ── Google OAuth ──────────────────────────────────────── */

function trackGoogleEvent(eventName: string, params: Record<string, string | number | boolean | undefined>) {
  const google = window as Window & {
    gtag?: (command: 'event', target: string, params?: Record<string, string | number | boolean>) => void
  }
  const cleanParams = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined),
  ) as Record<string, string | number | boolean>
  google.gtag?.('event', eventName, cleanParams)
}

function loadGoogleScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.getElementById('google-gsi')) { resolve(); return }
    const script = document.createElement('script')
    script.id = 'google-gsi'
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('Failed to load Google Sign-In'))
    document.head.appendChild(script)
  })
}

/* ── CSS keyframes (injected once) ─────────────────────── */

const STYLE_ID = 'bttco-confirmed-anims'
function injectAnimations() {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
    @keyframes bttco-fade-in { from { opacity: 0; transform: scale(0.95) translateY(10px); } to { opacity: 1; transform: scale(1) translateY(0); } }
    @keyframes bttco-fade-out { from { opacity: 1; transform: scale(1); } to { opacity: 0; transform: scale(0.95) translateY(10px); } }
    @keyframes bttco-backdrop-in { from { opacity: 0; } to { opacity: 1; } }
    @keyframes bttco-backdrop-out { from { opacity: 1; } to { opacity: 0; } }
    @keyframes bttco-check-pop { 0% { transform: scale(0); opacity: 0; } 50% { transform: scale(1.3); } 100% { transform: scale(1); opacity: 1; } }
    @keyframes bttco-slide-up { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
  `
  document.head.appendChild(style)
}

/* ── Component ─────────────────────────────────────────── */

type ModalPhase = 'intro' | 'confirming' | 'closing' | 'closed'

export default function QuoteConfirmedPage() {
  const { token } = useParams<{ token: string }>()
  const [modalPhase, setModalPhase] = useState<ModalPhase>('intro')
  const [accountCreated, setAccountCreated] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [details, setDetails] = useState<BookingDetails | null>(null)
  const [calendarAdded, setCalendarAdded] = useState(false)
  const [welcomeVisible, setWelcomeVisible] = useState(false)
  const modalRef = useRef<HTMLDivElement>(null)
  const purchaseTrackedRef = useRef(false)
  const celebrationPlayedRef = useRef(false)

  useEffect(() => { injectAnimations() }, [])

  useEffect(() => {
    if (!details || modalPhase !== 'closed' || celebrationPlayedRef.current) return
    celebrationPlayedRef.current = true
    const timers = fireAppointmentCelebration()
    return () => timers.forEach(window.clearTimeout)
  }, [details, modalPhase])

  // Fetch booking details for calendar links
  useEffect(() => {
    if (!token) return
    fetch(`${API_BASE}/quotes/public/${token}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          const fallbackSlot = data.available_slots?.[0]
          setDetails({
            client_name: data.client_name || 'Guest Collector',
            artist_name: data.artist_name || 'Artist',
            description: data.description || '',
            placement: data.placement,
            scheduled_date: data.selected_date?.slice(0, 10) || fallbackSlot?.date || null,
            scheduled_time: data.selected_time || fallbackSlot?.time || null,
            duration_minutes: data.selected_duration || fallbackSlot?.duration_minutes || null,
            deposit_amount: Number(data.deposit_amount || 0),
            reference_code: data.reference_code,
          })
        }
      })
      .catch(() => {})
  }, [token])

  useEffect(() => {
    if (!details || purchaseTrackedRef.current) return
    purchaseTrackedRef.current = true
    trackGoogleEvent('purchase', {
      currency: 'USD',
      value: customerPrice(details.deposit_amount),
      transaction_id: details.reference_code || token,
      booking_reference: details.reference_code || undefined,
      item_name: 'Tattoo deposit',
      artist_name: details.artist_name,
    })
  }, [details, token])

  // Close modal with animation. Hold confirming phase so user can read the message.
  const closeModal = useCallback((didCreate: boolean) => {
    setModalPhase('confirming')
    setTimeout(() => {
      setModalPhase('closing')
      setTimeout(() => {
        setModalPhase('closed')
        if (didCreate) {
          setWelcomeVisible(true)
          setTimeout(() => setWelcomeVisible(false), 5000)
        }
      }, 500)
    }, didCreate ? 3000 : 2000)
  }, [])

  // Google signup handler
  const handleGoogleSignup = useCallback(async () => {
    setError(null)
    if (!GOOGLE_CLIENT_ID) {
      setError('Google sign-up is not configured yet.')
      return
    }
    try {
      await loadGoogleScript()
      const google = (window as unknown as Record<string, unknown>).google as { accounts: { id: { initialize: (opts: unknown) => void; prompt: () => void } } }
      google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: async (response: { credential: string }) => {
          setCreating(true)
          try {
            await apiAuth.google(response.credential, token)
            setAccountCreated(true)
            closeModal(true)
          } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Google sign-up failed')
          } finally {
            setCreating(false)
          }
        },
      })
      google.accounts.id.prompt()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not load Google Sign-In')
    }
  }, [token, closeModal])

  // "No thanks" handler
  const handleDecline = useCallback(() => {
    closeModal(false)
  }, [closeModal])

  const cardStyle = {
    margin: '16px 0',
    padding: '16px 20px',
    background: '#191a19',
    border: '1px solid #3a301e',
    borderRadius: '12px',
    width: '100%',
    maxWidth: '384px',
    boxSizing: 'border-box' as const,
  }

  const labelStyle = {
    margin: '0 0 6px' as const,
    fontSize: '11px',
    color: '#D38A68',
    fontFamily: "'Space Grotesk', sans-serif",
    textTransform: 'uppercase' as const,
    letterSpacing: '1.5px',
    fontWeight: 700,
  }

  return (
    <div className="quote-page" style={{ minHeight: '100dvh', padding: '32px 16px', overflowY: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-start', background: '#0b0c0b' }}>
      {/* -- Collector's Club Modal -- */}
      {modalPhase !== 'closed' && (
        <div style={{
          position: 'fixed',
          inset: 0,
          zIndex: 9999,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(0,0,0,0.82)',
          backdropFilter: 'blur(8px)',
          animation: modalPhase === 'closing'
            ? 'bttco-backdrop-out 0.4s ease forwards'
            : 'bttco-backdrop-in 0.4s ease forwards',
        }}>
          <div
            ref={modalRef}
            style={{
              width: '90%',
              maxWidth: '420px',
              background: '#191a19',
              border: '1px solid #3a301e',
              borderRadius: '16px',
              padding: '32px 28px',
              textAlign: 'center',
              boxShadow: '0 25px 60px rgba(0,0,0,0.75), 0 0 35px rgba(211,138,104,0.12)',
              animation: modalPhase === 'closing'
                ? 'bttco-fade-out 0.35s ease forwards'
                : 'bttco-fade-in 0.5s ease forwards',
            }}
          >
            {modalPhase === 'intro' && (
              <>
                {/* Tiger icon */}
                <img
                  src="/assets/brand/ryozan-mark.svg"
                  alt="Ryozan"
                  style={{ width: 72, height: 72, marginBottom: '16px', display: 'block', margin: '0 auto 16px' }}
                />

                {/* Title */}
                <h2 style={{
                  fontFamily: "'Poppins', sans-serif",
                  fontSize: '1.45rem',
                  fontWeight: 900,
                  color: '#f4ecdd',
                  textTransform: 'uppercase',
                  letterSpacing: '1.5px',
                  margin: '0 0 8px',
                }}>
                  Collector's Club
                </h2>

                <p style={{ color: '#e2dac2', fontSize: '0.95rem', lineHeight: 1.6, margin: '0 0 8px' }}>
                  You're in! Want to join the Collector's Club?
                </p>
                <p style={{ color: 'rgba(232,223,207,0.65)', fontSize: '0.8rem', lineHeight: 1.5, margin: '0 0 24px' }}>
                  Faster check-ins, rewards, side quests, exclusive drops, and future appointments auto-added to your calendar.
                </p>

                {error && (
                  <p style={{ margin: '0 0 12px', color: '#e74c3c', fontSize: '0.85rem' }}>{error}</p>
                )}

                {/* Hell Yea! */}
                <button
                  onClick={handleGoogleSignup}
                  disabled={creating}
                  style={{
                    width: '100%',
                    padding: '14px 28px',
                    background: 'linear-gradient(135deg, #e09f7d 0%, #D38A68 50%, #b86f4e 100%)',
                    color: '#0b0c0b',
                    border: '1px solid #3a301e',
                    borderRadius: '8px',
                    fontFamily: "'Poppins', sans-serif",
                    textTransform: 'uppercase',
                    letterSpacing: '1.5px',
                    fontSize: '1rem',
                    fontWeight: 800,
                    cursor: creating ? 'not-allowed' : 'pointer',
                    opacity: creating ? 0.7 : 1,
                    marginBottom: '10px',
                    boxShadow: '0 4px 18px rgba(211,138,104,0.3)',
                  }}
                >
                  {creating ? 'Setting up...' : 'Hell Yea!'}
                </button>

                {/* Google badge under Hell Yea */}
                <button
                  onClick={handleGoogleSignup}
                  disabled={creating}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '8px',
                    width: '100%',
                    padding: '10px',
                    background: '#101110',
                    color: '#f4ecdd',
                    border: '1px solid #3a301e',
                    borderRadius: '8px',
                    fontSize: '0.85rem',
                    fontWeight: 600,
                    cursor: creating ? 'not-allowed' : 'pointer',
                    marginBottom: '20px',
                  }}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>
                  Sign up with Google
                </button>

                {/* Divider */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', margin: '0 0 20px' }}>
                  <div style={{ flex: 1, height: '1px', background: '#3a301e' }} />
                  <span style={{ color: 'rgba(232,223,207,0.5)', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '1px' }}>or</span>
                  <div style={{ flex: 1, height: '1px', background: '#3a301e' }} />
                </div>

                {/* No thanks */}
                <button
                  onClick={handleDecline}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'rgba(232,223,207,0.65)',
                    fontSize: '0.85rem',
                    cursor: 'pointer',
                    padding: '8px 16px',
                  }}
                >
                  No thanks, I'm a loner
                </button>
              </>
            )}

            {/* Confirming phase: success or decline animation */}
            {modalPhase === 'confirming' && (
              <div style={{ padding: '20px 0' }}>
                {accountCreated ? (
                  <>
                    {/* Trophy SVG icon */}
                    <div style={{
                      width: '64px',
                      height: '64px',
                      margin: '0 auto 16px',
                      animation: 'bttco-check-pop 0.6s ease forwards',
                    }}>
                      <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" width="64" height="64">
                        <circle cx="32" cy="32" r="30" fill="rgba(250,174,21,0.12)" stroke="#faae15" strokeWidth="2" />
                        <path d="M20 18h24v4c0 5-3 9-7 11v3h-10v-3c-4-2-7-6-7-11v-4z" fill="#faae15" opacity="0.9" />
                        <path d="M16 18h4v4c0 2-1 3-2.5 4L16 27v-9z" fill="#d4940f" />
                        <path d="M44 18h4v9l-1.5-1c-1.5-1-2.5-2-2.5-4v-4z" fill="#d4940f" />
                        <rect x="27" y="36" width="10" height="3" rx="1" fill="#faae15" />
                        <rect x="24" y="39" width="16" height="4" rx="2" fill="#faae15" opacity="0.8" />
                        <path d="M30 24l2-4 2 4 4 .5-3 3 .7 4.5-3.7-2-3.7 2 .7-4.5-3-3z" fill="#fff8ec" opacity="0.9" />
                      </svg>
                    </div>
                    <h3 style={{
                      fontFamily: "'KC Tallboy', Oswald, 'Arial Narrow', sans-serif",
                      fontSize: '1.3rem',
                      color: 'var(--gold, #faae15)',
                      textTransform: 'uppercase',
                      letterSpacing: '1px',
                      margin: '0 0 8px',
                      animation: 'bttco-slide-up 0.4s ease 0.3s both',
                    }}>
                      Welcome to the Club!
                    </h3>
                    <p style={{
                      color: 'var(--text, #e8dfcf)',
                      fontSize: '0.9rem',
                      margin: 0,
                      animation: 'bttco-slide-up 0.4s ease 0.5s both',
                    }}>
                      Your account has been created. Let's see your appointment.
                    </p>
                  </>
                ) : (
                  <>
                    {/* X SVG icon (declined) */}
                    <div style={{
                      width: '56px',
                      height: '56px',
                      margin: '0 auto 16px',
                      animation: 'bttco-check-pop 0.5s ease forwards',
                    }}>
                      <svg viewBox="0 0 56 56" fill="none" xmlns="http://www.w3.org/2000/svg" width="56" height="56">
                        <circle cx="28" cy="28" r="26" fill="rgba(185,173,150,0.08)" stroke="var(--muted, #b9ad96)" strokeWidth="2" strokeDasharray="4 3" />
                        <path d="M20 20l16 16M36 20l-16 16" stroke="var(--muted, #b9ad96)" strokeWidth="3" strokeLinecap="round" />
                      </svg>
                    </div>
                    <h3 style={{
                      fontFamily: "'KC Tallboy', Oswald, 'Arial Narrow', sans-serif",
                      fontSize: '1.1rem',
                      color: 'var(--muted, #b9ad96)',
                      textTransform: 'uppercase',
                      letterSpacing: '1px',
                      margin: '0 0 6px',
                      animation: 'bttco-slide-up 0.4s ease 0.2s both',
                    }}>
                      Ouch, rejected...
                    </h3>
                    <p style={{
                      color: 'var(--muted, #b9ad96)',
                      fontSize: '0.85rem',
                      margin: 0,
                      opacity: 0.7,
                      animation: 'bttco-slide-up 0.4s ease 0.4s both',
                    }}>
                      No worries, you can always join later!
                    </p>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Welcome toast notification ── */}
      {welcomeVisible && (
        <div style={{
          position: 'fixed',
          top: '24px',
          right: '24px',
          zIndex: 10000,
          background: 'var(--bg, #121312)',
          border: '1px solid rgba(46,204,113,0.4)',
          borderRadius: '12px',
          padding: '14px 20px',
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
          animation: 'bttco-slide-up 0.4s ease forwards',
          maxWidth: '340px',
        }}>
          <div style={{
            width: '36px',
            height: '36px',
            borderRadius: '50%',
            background: 'rgba(46,204,113,0.15)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}>
            <span style={{ color: '#2ecc71', fontSize: '18px', fontWeight: 'bold' }}>✓</span>
          </div>
          <div>
            <p style={{ margin: '0 0 2px', color: 'var(--gold, #faae15)', fontSize: '0.85rem', fontWeight: 700 }}>
              Account Created
            </p>
            <p style={{ margin: 0, color: 'var(--muted, #b9ad96)', fontSize: '0.8rem' }}>
              Welcome to the Collector's Club!
            </p>
          </div>
        </div>
      )}

      {/* -- Main confirmation content -- */}
      <div className="quote-container" style={{
        opacity: modalPhase === 'closed' ? 1 : 0.3,
        transition: 'opacity 0.6s ease',
        pointerEvents: modalPhase === 'closed' ? 'auto' : 'none',
        width: '100%',
        maxWidth: '420px',
        margin: '0 auto',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '16px 0 40px',
      }}>
        {/* Ticket Confirmation Card */}
        {details ? (
          <AnimatedTicket
            ticketId={details.reference_code || 'Ryozan-CONF'}
            amount={details.deposit_amount}
            date={details.scheduled_date ? `${details.scheduled_date}T${details.scheduled_time || '12:00'}` : new Date()}
            cardHolder={details.client_name || 'Guest Collector'}
            last4Digits="ONLINE"
            barcodeValue={details.reference_code || 'Ryozan-SEALED'}
            artistName={details.artist_name}
            title="You're Booked!"
            subtitle="Deposit received. Chair confirmed."
            className="mb-4"
          />
        ) : (
          <div className="quote-accepted" style={{ background: '#191a19', border: '1px solid #3a301e', borderRadius: '16px', padding: '28px', textAlign: 'center', width: '100%', maxWidth: '384px', marginBottom: '16px' }}>
            <div className="quote-check" style={{ color: '#70c27b', fontSize: '32px', marginBottom: '10px' }}>✓</div>
            <h1 style={{ fontFamily: "'Poppins', sans-serif", fontSize: '1.5rem', fontWeight: 800, color: '#f4ecdd', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '10px' }}>
              You're Booked!
            </h1>
            <p style={{ color: '#e2dac2', fontSize: '0.95rem', lineHeight: 1.6, marginBottom: '8px' }}>
              Your deposit has been received and your appointment is confirmed.
            </p>
          </div>
        )}

        {/* Add to Calendar */}
        {details?.scheduled_date && (
          <div style={cardStyle}>
            <p style={labelStyle}>Add to Calendar</p>
            {!calendarAdded ? (
              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginTop: '10px' }}>
                <a
                  href={buildGoogleCalUrl(details) || '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => setCalendarAdded(true)}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '8px',
                    padding: '10px 16px', background: 'rgba(211,138,104,0.12)',
                    border: '1px solid rgba(211,138,104,0.3)', borderRadius: '6px',
                    color: '#D38A68', textDecoration: 'none', fontSize: '0.82rem', fontWeight: 600,
                  }}
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V9h14v11zM7 11h5v5H7z"/></svg>
                  Google Calendar
                </a>
                <button
                  onClick={() => { downloadIcs(details); setCalendarAdded(true) }}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '8px',
                    padding: '10px 16px', background: '#101110',
                    border: '1px solid #3a301e', borderRadius: '6px',
                    color: '#f4ecdd', fontSize: '0.82rem', fontWeight: 600, cursor: 'pointer',
                  }}
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
                  Download .ics
                </button>
              </div>
            ) : (
              <p style={{ margin: '8px 0 0', color: '#70c27b', fontSize: '0.85rem', fontWeight: 500 }}>
                Event added to your calendar.
              </p>
            )}
            <p style={{ margin: '10px 0 0', color: 'rgba(232,223,207,0.55)', fontSize: '0.78rem', fontFamily: "'Space Grotesk', sans-serif" }}>
              {details.scheduled_date}{details.scheduled_time ? ` at ${formatTime12h(details.scheduled_time)}` : ''} with {details.artist_name}
            </p>
          </div>
        )}

        {/* Account status (only if they created one) */}
        {accountCreated && (
          <div style={{
            ...cardStyle,
            background: 'rgba(112,194,123,0.08)',
            border: '1px solid rgba(112,194,123,0.3)',
            textAlign: 'center',
          }}>
            <p style={{ margin: 0, color: '#70c27b', fontSize: '0.9rem', fontWeight: 600 }}>
              Welcome to the Collector's Club!
            </p>
          </div>
        )}

        {/* Navigation buttons */}
        <div style={{ textAlign: 'center', marginTop: '20px', display: 'flex', flexDirection: 'column', gap: '10px', alignItems: 'center', width: '100%', maxWidth: '384px' }}>
          {accountCreated && (
            <Link
              to="/dashboard"
              style={{
                width: '100%', boxSizing: 'border-box',
                display: 'inline-block', padding: '12px 24px',
                background: 'linear-gradient(135deg, #e09f7d 0%, #D38A68 50%, #b86f4e 100%)',
                color: '#0b0c0b', borderRadius: '8px', textDecoration: 'none',
                fontFamily: "'Poppins', sans-serif",
                textTransform: 'uppercase', letterSpacing: '1px', fontSize: '0.85rem',
                fontWeight: 800, border: '1px solid #3a301e',
                boxShadow: '0 4px 18px rgba(211,138,104,0.3)',
              }}
            >
              View My Account
            </Link>
          )}
          <Link
            to="/"
            style={{
              width: '100%', boxSizing: 'border-box',
              display: 'inline-block', padding: '12px 24px',
              background: '#101110', border: '1px solid #3a301e',
              color: '#f4ecdd', borderRadius: '8px', textDecoration: 'none',
              fontFamily: "'Poppins', sans-serif",
              textTransform: 'uppercase', letterSpacing: '1px', fontSize: '0.85rem',
              fontWeight: 700,
            }}
          >
            {accountCreated ? 'Return to Site' : 'Back to Ryozan'}
          </Link>
        </div>
      </div>
    </div>
  )
}
