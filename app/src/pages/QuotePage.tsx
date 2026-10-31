/**
 * QuotePage: Public quote acceptance page (single-panel design).
 *
 * Client receives email with link → lands here → sees everything
 * in one clean card → picks slot → pays deposit via Stripe.
 */

import { useEffect, useState, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { quotes, type QuotePublicView } from '../lib/api'
import { customerPrice, formatCustomerPrice, formatTime12h } from '../lib/format'

function trackGoogleEvent(eventName: string, params: Record<string, string | number | boolean | undefined>) {
  const google = window as Window & {
    gtag?: (command: 'event', target: string, params?: Record<string, string | number | boolean>) => void
  }
  const cleanParams = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined),
  ) as Record<string, string | number | boolean>
  google.gtag?.('event', eventName, cleanParams)
}

export default function QuotePage() {
  const { token } = useParams<{ token: string }>()
  const [quote, setQuote] = useState<QuotePublicView | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null)
  const [accepting, setAccepting] = useState(false)
  const [replyText, setReplyText] = useState('')
  const [sendingReply, setSendingReply] = useState(false)
  const [replySent, setReplySent] = useState(false)
  const [lastSentMessage, setLastSentMessage] = useState('')
  const [attachments, setAttachments] = useState<{ file: File; preview: string }[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!token) return
    quotes.getPublic(token)
      .then((q) => {
        setQuote(q)
        if (q.available_slots.length === 1) setSelectedSlot(0)
      })
      .catch((err) => setError(err.message || 'Quote not found'))
      .finally(() => setLoading(false))
  }, [token])

  const handleReply = async () => {
    if (!replyText.trim() || !token) return
    setSendingReply(true)
    try {
      const msg = replyText.trim()
      await quotes.reply(token, msg)
      setLastSentMessage(msg)
      setReplySent(true)
      setReplyText('')
    } catch (err: unknown) {
      // Real error now — the endpoint exists. Surface it instead of
      // silently opening a mailto (the old fallback used to hide the
      // fact that the route 404'd).
      const message = err instanceof Error ? err.message : 'Could not send reply. Please try again.'
      setError(message)
    } finally {
      setSendingReply(false)
    }
  }

  const handleAccept = async () => {
    if (selectedSlot === null || !token) return
    setAccepting(true)
    try {
      trackGoogleEvent('begin_checkout', {
        currency: 'USD',
        value: customerPrice(quote?.deposit_amount ?? 0),
        checkout_type: 'quote_deposit',
        booking_reference: quote?.reference_code || undefined,
        artist_name: quote?.artist_name,
      })
      const { checkout_url } = await quotes.accept(token, selectedSlot)
      window.location.href = checkout_url
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to process')
      setAccepting(false)
    }
  }

  // Loading / error / expired / accepted states
  if (loading) return <PageShell><p>Loading your quote...</p></PageShell>
  if (error || !quote) return <PageShell><h2>Quote Not Found</h2><p>{error || 'This quote link may have expired or is invalid.'}</p></PageShell>
  if (quote.expired) return <PageShell><h2>Quote Expired</h2><p>This quote has expired. Please contact your artist for a new quote.</p></PageShell>
  if (quote.status === 'accepted') return <PageShell><span className="ryozan-state-check" aria-hidden="true">&#10003;</span><h2>You're Booked!</h2><p>Your deposit has been paid and your appointment is confirmed.</p></PageShell>

  const slot = quote.available_slots[0]
  const slotDate = slot ? new Date(slot.date + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) : null

  return (
    <div className="quote-page" style={{ minHeight: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px 16px', background: 'var(--bg, #0d0e0d)' }}>
      <div style={{
        width: '100%',
        maxWidth: '480px',
        background: 'var(--panel, #1a1b1a)',
        border: '1px solid var(--panel-border)',
        borderRadius: '16px',
        overflow: 'hidden',
      }}>
        {/* Header band */}
        <div style={{
          padding: '20px 24px',
          background: 'var(--panel, rgba(250,174,21,0.06))',
          borderBottom: '1px solid var(--panel-border)',
          display: 'flex',
          alignItems: 'center',
          gap: '14px',
          position: 'relative',
        }}>
          {quote.reference_code && (
            <span style={{ position: 'absolute', top: '10px', right: '16px', fontFamily: "'JetBrains Mono', monospace", fontSize: '0.6rem', color: 'var(--text-muted)' }}>
              #{quote.reference_code}
            </span>
          )}
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
            <span className="cp-greeting-label">quote from,</span>
            <span className="cp-greeting-name" style={{ fontSize: '2.2rem' }}>{quote.artist_name}</span>
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: '20px 24px' }}>
          {/* Message panel */}
          <div style={{
            margin: '0 0 16px',
            border: '1px solid var(--panel-border)',
            borderRadius: '10px',
            overflow: 'hidden',
            background: '#151413',
          }}>
            {/* Messages area */}
            <div style={{ padding: '14px', maxHeight: '220px', overflowY: 'auto' }}>
              {/* Artist bubble */}
              {quote.artist_message && (
                <div style={{ display: 'flex', gap: '8px', marginBottom: '10px' }}>
                  <div style={{
                    width: 26, height: 26, borderRadius: '50%', background: '#2a2520',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '0.6rem', color: 'var(--gold)', fontWeight: 700, flexShrink: 0, marginTop: 2,
                  }}>
                    {quote.artist_name.charAt(0)}
                  </div>
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: "'JetBrains Mono', monospace" }}>
                      {quote.artist_name}
                    </span>
                    <div style={{
                      marginTop: '3px', padding: '9px 12px',
                      background: '#1e1c19', borderRadius: '2px 10px 10px 10px',
                      fontSize: '0.82rem', color: 'var(--text)', lineHeight: 1.5,
                    }}>
                      {quote.artist_message}
                    </div>
                  </div>
                </div>
              )}

              {/* Reply sent */}
              {replySent && (
                <div style={{ animation: 'ryozan-slide-up 0.4s ease forwards' }}>
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '8px' }}>
                    <div style={{
                      padding: '9px 12px', maxWidth: '80%',
                      background: 'rgba(250,174,21,0.12)', borderRadius: '10px 2px 10px 10px',
                      fontSize: '0.82rem', color: 'var(--text)', lineHeight: 1.5,
                    }}>
                      {lastSentMessage}
                    </div>
                  </div>
                  {/* Attachment previews */}
                  {attachments.length > 0 && (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '6px', marginBottom: '8px' }}>
                      {attachments.map((a, i) => (
                        <img key={i} src={a.preview} alt="" style={{ width: 48, height: 48, borderRadius: '6px', objectFit: 'cover' }} />
                      ))}
                    </div>
                  )}
                  <div style={{
                    padding: '10px 14px',
                    background: 'rgba(46,204,113,0.06)', border: '1px solid rgba(46,204,113,0.15)',
                    borderRadius: '8px', textAlign: 'center',
                  }}>
                    <span style={{ fontSize: '0.78rem', color: '#2ecc71', fontWeight: 600 }}>Message sent!</span>
                    <span style={{ display: 'block', fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '3px' }}>
                      Create an account to continue this conversation in your portal
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Input bar */}
            {!replySent && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: '8px 10px',
                borderTop: '1px solid rgba(255,248,236,0.06)',
                background: '#1a1918',
              }}>
                {/* Hidden file input */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  style={{ display: 'none' }}
                  onChange={(e) => {
                    const files = Array.from(e.target.files || [])
                    const newAttachments = files.map(f => ({ file: f, preview: URL.createObjectURL(f) }))
                    setAttachments(prev => [...prev, ...newAttachments].slice(0, 3))
                    e.target.value = ''
                  }}
                />
                {/* Photo button */}
                <button
                  onClick={() => fileInputRef.current?.click()}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer', padding: '4px',
                    color: 'var(--text-muted)', display: 'flex', alignItems: 'center',
                  }}
                  title="Attach photo"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>
                  </svg>
                </button>
                {/* Text input */}
                <input
                  type="text"
                  placeholder={`Reply to ${quote.artist_name}...`}
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && replyText.trim()) handleReply() }}
                  style={{
                    flex: 1, padding: '8px 10px',
                    background: 'transparent', border: 'none',
                    color: 'var(--text)', fontSize: '0.82rem',
                    fontFamily: 'inherit', outline: 'none',
                  }}
                />
                {/* Send */}
                <button
                  onClick={handleReply}
                  disabled={!replyText.trim() || sendingReply}
                  style={{
                    background: (replyText.trim() && !sendingReply) ? 'var(--gold)' : 'transparent',
                    color: (replyText.trim() && !sendingReply) ? 'var(--bg, #121312)' : 'var(--text-muted)',
                    border: 'none', borderRadius: '50%',
                    width: 28, height: 28,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    cursor: replyText.trim() ? 'pointer' : 'default',
                    transition: 'all 0.2s', flexShrink: 0,
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>
                  </svg>
                </button>
              </div>
            )}

            {/* Attachment previews (before sending) */}
            {!replySent && attachments.length > 0 && (
              <div style={{
                display: 'flex', gap: '6px', padding: '6px 10px',
                borderTop: '1px solid rgba(255,248,236,0.04)',
                background: '#1a1918',
              }}>
                {attachments.map((a, i) => (
                  <div key={i} style={{ position: 'relative' }}>
                    <img src={a.preview} alt="" style={{ width: 44, height: 44, borderRadius: '6px', objectFit: 'cover' }} />
                    <button
                      onClick={() => { URL.revokeObjectURL(a.preview); setAttachments(prev => prev.filter((_, j) => j !== i)) }}
                      style={{
                        position: 'absolute', top: -4, right: -4,
                        width: 16, height: 16, borderRadius: '50%',
                        background: '#e74c3c', border: 'none', color: '#fff',
                        fontSize: '10px', cursor: 'pointer',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Details grid */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: '1px',
            background: '#2a2520',
            borderRadius: '8px',
            overflow: 'hidden',
            marginBottom: '16px',
          }}>
            {[
              { label: 'Placement', value: quote.placement || 'TBD' },
              { label: 'Size', value: quote.size_estimate || 'TBD' },
              { label: 'Deposit', value: formatCustomerPrice(quote.deposit_amount), gold: true },
            ].map((item) => (
              <div key={item.label} style={{ padding: '10px 12px', background: '#1e1c19', textAlign: 'center' }}>
                <span style={{ display: 'block', fontFamily: "'JetBrains Mono', monospace", fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>{item.label}</span>
                <span style={{ display: 'block', fontFamily: "'JetBrains Mono', monospace", fontSize: '0.85rem', color: item.gold ? 'var(--gold)' : 'var(--text, #e8dfcf)', fontWeight: 600 }}>{item.value}</span>
              </div>
            ))}
          </div>

          {/* Time slot(s) */}
          {quote.available_slots.length > 0 && (
            <div style={{ marginBottom: '16px' }}>
              {quote.available_slots.length === 1 ? (
                <div style={{
                  padding: '12px 14px',
                  background: 'var(--panel)',
                  border: '1px solid var(--panel-border)',
                  borderRadius: '8px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                }}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="1.8"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
                  <div>
                    <span style={{ display: 'block', fontSize: '0.9rem', color: 'var(--text)', fontWeight: 600 }}>{slotDate}</span>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{formatTime12h(slot.time)} &middot; {slot.duration_minutes} min</span>
                  </div>
                </div>
              ) : (
                <>
                  <p style={{ margin: '0 0 8px', fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Pick a time</p>
                  {quote.available_slots.map((s, i) => {
                    const d = new Date(s.date + 'T00:00:00')
                    const day = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
                    const sel = selectedSlot === i
                    return (
                      <button
                        key={i}
                        onClick={() => setSelectedSlot(i)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '10px',
                          width: '100%',
                          padding: '10px 14px',
                          marginBottom: '6px',
                          background: sel ? 'rgba(250,174,21,0.1)' : 'var(--bg-surface)',
                          border: sel ? '1px solid var(--gold)' : '1px solid var(--panel-border)',
                          borderRadius: '8px',
                          cursor: 'pointer',
                          textAlign: 'left',
                          color: 'var(--text)',
                          fontSize: '0.88rem',
                        }}
                      >
                        <span style={{ color: sel ? 'var(--gold)' : '#666', fontSize: '14px' }}>{sel ? '●' : '○'}</span>
                        <span style={{ fontWeight: 600 }}>{day}</span>
                        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{formatTime12h(s.time)} &middot; {s.duration_minutes} min</span>
                      </button>
                    )
                  })}
                </>
              )}
            </div>
          )}

          {/* CTA */}
          <button
            className="btn btn-primary"
            onClick={handleAccept}
            disabled={selectedSlot === null || accepting}
            style={{
              width: '100%',
              padding: '14px',
              opacity: selectedSlot !== null ? 1 : 0.5,
              cursor: selectedSlot !== null && !accepting ? 'pointer' : 'not-allowed',
            }}
          >
            {accepting ? 'Redirecting to payment...' : 'Pay Deposit'}
          </button>

          <p style={{ margin: '8px 0 0', textAlign: 'center', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            Secure payment powered by Stripe
          </p>

          <div style={{ display: 'flex', justifyContent: 'center', marginTop: '16px' }}>
            <img src="/assets/brand/ryozan-mark.svg" alt="Ryozan" style={{ width: 40, height: 40, opacity: 0.5 }} />
          </div>
        </div>
      </div>
    </div>
  )
}

/* Shell for loading/error/accepted states */
function PageShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="ryozan-error-boundary ryozan-state-boundary">
      <section className="ryozan-error-boundary__panel">
        {children}
      </section>
    </main>
  )
}

