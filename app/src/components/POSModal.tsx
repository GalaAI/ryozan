/**
 * POSModal - Point of Sale charge modal.
 *
 * Flow: Mode Select → Amount Entry → QR Code → Payment Confirmed
 *
 * Supports two modes:
 *   1. Booking-linked: picks from today's bookings, shows deposit math
 *   2. Quick Charge: manual amount entry for walk-ins
 *
 * QR code encodes a Stripe Checkout URL. Client scans → pays via
 * card, Apple Pay, Google Pay, Venmo, or Cash App.
 */

import { useState, useEffect, useRef, useCallback, type ReactNode, type FC } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import {
  pos,
  giftCards,
  bookings as bookingsApi,
  users as usersApi,
  type Booking,
  type POSChargeResponse,
  type POSInvoiceResponse,
  type User,
  type GiftCardRedeemResponse,
} from '../lib/api'
import { customerPrice } from '../lib/format'

/* ── Types ─────────────────────────────── */

type POSStep = 'numpad' | 'source' | 'method' | 'cash-confirm' | 'gift-card-confirm' | 'qr' | 'confirmed' | 'invoice-sent'
type PaymentMethod = 'cash' | 'card' | 'gift-card'

type ChargeContext = {
  booking: Booking | null
  clientName: string
  clientEmail: string
  description: string
}

interface POSModalProps {
  open: boolean
  onClose: () => void
  /** Pre-select a booking (when triggered from bookings list) */
  preselectedBooking?: Booking
  /** Skip mode select and jump to a specific flow */
  initialMode?: 'appointment' | 'walkin' | 'clover' | 'stripe' | 'custom' | 'cash' | 'online'
  /** Pre-fill the amount from the numpad widget */
  initialAmount?: string
  /** Render inline as a charm panel (no overlay/backdrop, no centered modal box) */
  embedded?: boolean
}

/* ── Icons ─────────────────────────────── */

const Icons = {
  close: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>
  ),
  calendar: (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
    </svg>
  ),
  zap: (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
    </svg>
  ),
  check: (
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--ok)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
    </svg>
  ),
  back: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>
    </svg>
  ),
  invoice: (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>
    </svg>
  ),
}

/* ── Component ─────────────────────────── */

export default function POSModal({ open, onClose, preselectedBooking, initialMode, initialAmount, embedded = false }: POSModalProps) {
  const [step, setStep] = useState<POSStep>('numpad')
  const [todayBookings, setTodayBookings] = useState<Booking[]>([])
  const [savedCollectors, setSavedCollectors] = useState<User[]>([])
  const [collectorsLoading, setCollectorsLoading] = useState(false)
  const [selectedBooking, setSelectedBooking] = useState<Booking | null>(null)
  const [amountDollars, setAmountDollars] = useState('')
  const [clientName, setClientName] = useState('')
  const [clientEmail, setClientEmail] = useState('')
  const [description, setDescription] = useState('')
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [chargeResult, setChargeResult] = useState<POSChargeResponse | null>(null)
  const [invoiceResult, setInvoiceResult] = useState<POSInvoiceResponse | null>(null)
  const [paymentStatus, setPaymentStatus] = useState<'waiting' | 'paid' | 'expired'>('waiting')
  const [cashConfirmed, setCashConfirmed] = useState(false)
  const [giftCardCode, setGiftCardCode] = useState('')
  const [giftCardResult, setGiftCardResult] = useState<GiftCardRedeemResponse | null>(null)
  const [error, setError] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Load today's bookings
  useEffect(() => {
    if (!open) return
    bookingsApi.list({ limit: 100 }).then((res) => {
      const bks = (res as { bookings: Booking[] }).bookings || []
      const todayStr = new Date().toISOString().slice(0, 10)
      setTodayBookings(bks.filter(b => {
        if (b.status === 'cancelled' || b.status === 'CANCELLED') return false
        // Match by scheduled_date or scheduled_time
        const schedDate = b.scheduled_date ? String(b.scheduled_date).slice(0, 10) : null
        const schedTime = b.scheduled_time ? String(b.scheduled_time).slice(0, 10) : null
        return schedDate === todayStr || schedTime === todayStr
      }))
    }).catch(() => {})
  }, [open])

  // Reuse the artist-scoped client roster that powers the existing Rolodex.
  // This keeps saved collectors tied to real booking relationships and avoids
  // creating a second client directory inside checkout.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setCollectorsLoading(true)
    usersApi.myClients()
      .then((clients) => {
        if (!cancelled) setSavedCollectors(clients)
      })
      .catch(() => {
        if (!cancelled) setSavedCollectors([])
      })
      .finally(() => {
        if (!cancelled) setCollectorsLoading(false)
      })
    return () => { cancelled = true }
  }, [open])

  // Handle initialMode from POS widget — preserves backwards compat
  useEffect(() => {
    if (!open || preselectedBooking) return
    if (initialAmount) setAmountDollars(initialAmount)
    if (initialMode === 'appointment') {
      // Skip numpad if amount preset, jump to source picker
      setStep(initialAmount ? 'source' : 'numpad')
    } else if (initialMode === 'cash') {
      setPaymentMethod('cash')
      setStep(initialAmount ? 'source' : 'numpad')
    } else if (initialMode === 'stripe' || initialMode === 'online' || initialMode === 'clover') {
      setPaymentMethod('card')
      setStep(initialAmount ? 'source' : 'numpad')
    } else if (initialMode === 'walkin' || initialMode === 'custom') {
      setSelectedBooking(null)
      setDescription(initialMode === 'custom' ? 'Custom charge' : 'Walk-in')
      setStep(initialAmount ? 'method' : 'numpad')
    }
  }, [open, initialMode, initialAmount, preselectedBooking])

  // If preselected booking, prefill and skip to method picker
  useEffect(() => {
    if (open && preselectedBooking) {
      setSelectedBooking(preselectedBooking)
      setClientName(preselectedBooking.resolved_name || preselectedBooking.client_name || '')
      setClientEmail(preselectedBooking.resolved_email || preselectedBooking.guest_email || '')
      setDescription(preselectedBooking.description || '')
      const deposit = preselectedBooking.deposit_amount || 0
      const total = preselectedBooking.estimated_total || 0
      if (total > deposit) {
        setAmountDollars(((total - deposit) / 1).toFixed(2))
      }
      setStep('method')
    }
  }, [open, preselectedBooking])

  // Reset on close
  useEffect(() => {
    if (!open) {
      setStep('numpad')
      setSelectedBooking(null)
      setAmountDollars('')
      setClientName('')
      setClientEmail('')
      setDescription('')
      setPaymentMethod(null)
      setCashConfirmed(false)
      setGiftCardCode('')
      setGiftCardResult(null)
      setChargeResult(null)
      setInvoiceResult(null)
      setPaymentStatus('waiting')
      setError('')
      setIsCreating(false)
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [open])

  // Poll for payment status
  // Polling only drives the on-screen state now. The ledger row is written by
  // the Stripe webhook, so this no longer needs the booking/client/description
  // context it used to pass to /pos/record.
  const startPolling = useCallback((sessionId: string) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const res = await pos.status(sessionId)
        if (res.payment_status === 'paid') {
          setPaymentStatus('paid')
          setStep('confirmed')
          if (pollRef.current) clearInterval(pollRef.current)
          // The card row is written server-side by the Stripe webhook
          // (_record_pos_transaction). It used to be written here, which meant
          // closing this modal before the client finished paying lost the record
          // permanently while Stripe still had the money. Do not restore it:
          // two writers would double-count the same payment.
        } else if (res.status === 'expired') {
          setPaymentStatus('expired')
          if (pollRef.current) clearInterval(pollRef.current)
        }
      } catch { /* keep polling */ }
    }, 3000)
  }, [])

  // Cleanup polling on unmount
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const handleCreateCharge = async (context?: ChargeContext) => {
    const sourceBooking = context ? context.booking : selectedBooking
    const sourceClientName = context ? context.clientName : clientName
    const sourceClientEmail = context ? context.clientEmail : clientEmail
    const sourceDescription = context ? context.description : description
    const cents = Math.round(parseFloat(amountDollars) * 100)
    if (!cents || cents < 50) {
      setError('Minimum charge is $0.50')
      return
    }
    setError('')
    setIsCreating(true)
    try {
      const result = await pos.charge({
        amount_cents: cents,
        booking_id: sourceBooking?.id,
        client_name: sourceClientName || undefined,
        client_email: sourceClientEmail || undefined,
        description: sourceDescription || undefined,
      })
      setChargeResult(result)
      setStep('qr')
      startPolling(result.session_id)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create charge')
    } finally {
      setIsCreating(false)
    }
  }

  const handleSelectBooking = (b: Booking) => {
    setSelectedBooking(b)
    setClientName(b.resolved_name || b.client_name || '')
    setClientEmail(b.resolved_email || b.guest_email || '')
    setDescription(b.description || '')
    setStep('method')
  }

  const handleSelectUnassignedMethod = (method: PaymentMethod) => {
    const context: ChargeContext = {
      booking: null,
      clientName: '',
      clientEmail: '',
      description: 'Walk-in or off-book payment',
    }
    setSelectedBooking(null)
    setClientName('')
    setClientEmail('')
    setDescription(context.description)
    setPaymentMethod(method)
    if (method === 'cash') setStep('cash-confirm')
    else if (method === 'gift-card') setStep('gift-card-confirm')
    else void handleCreateCharge(context)
  }

  const handleSelectCollector = (collector: User) => {
    setSelectedBooking(null)
    setClientName(collector.name)
    setClientEmail(collector.email)
    setDescription('Tattoo session')
    setStep('method')
  }

  const handleSelectMethod = (m: PaymentMethod) => {
    setPaymentMethod(m)
    if (m === 'cash') setStep('cash-confirm')
    else if (m === 'gift-card') setStep('gift-card-confirm')
    else handleCreateCharge() // card → Stripe QR
  }

  const handleRedeemGiftCard = async () => {
    const cents = Math.round(numericAmount * 100)
    if (!giftCardCode.trim()) {
      setError('Enter the gift-card code')
      return
    }
    setError('')
    setIsCreating(true)
    try {
      const result = await giftCards.redeem({
        code: giftCardCode,
        amount_cents: cents,
        booking_id: selectedBooking?.id,
        note: description || undefined,
      })
      setGiftCardResult(result)
      setStep('confirmed')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Gift card could not be redeemed')
    } finally {
      setIsCreating(false)
    }
  }

  const handleSendInvoice = async () => {
    const cents = Math.round(parseFloat(amountDollars) * 100)
    if (!clientEmail) {
      setError('Select a collector with an email to send an invoice')
      return
    }
    if (!cents || cents < 50) {
      setError('Minimum invoice is $0.50')
      return
    }
    setError('')
    setIsCreating(true)
    try {
      const result = await pos.invoice({
        amount_cents: cents,
        booking_id: selectedBooking?.id,
        client_name: clientName || undefined,
        client_email: clientEmail,
        description: description || undefined,
      })
      setInvoiceResult(result)
      setStep('invoice-sent')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to send invoice')
    } finally {
      setIsCreating(false)
    }
  }

  const handleConfirmCash = async () => {
    setIsCreating(true)
    try {
      await pos.record({
        amount_cents: Math.round(customerAmount * 100),
        commission_base_cents: Math.round(numericAmount * 100),
        payment_method: 'cash',
        processor: 'manual',
        booking_id: selectedBooking?.id || null,
        client_name: clientName || (selectedBooking?.resolved_name || selectedBooking?.client_name) || null,
        description: description || (selectedBooking?.description) || 'Cash payment',
        notes: 'Deposited to safe as contractor contribution',
      })
    } catch {
      // Record failed but don't block the flow — cash was already collected
    } finally {
      setIsCreating(false)
    }
    setCashConfirmed(true)
    setStep('confirmed')
  }

  // Numpad handlers
  const handleNumpadKey = (key: string) => {
    setError('')
    if (key === 'C') { setAmountDollars(''); return }
    if (key === '⌫') {
      setAmountDollars((value) => {
        const cents = Math.max(0, Math.round(Number.parseFloat(value || '0') * 100))
        const nextDigits = String(cents || '').slice(0, -1)
        return nextDigits ? (Number(nextDigits) / 100).toFixed(2) : ''
      })
      return
    }
    if (!/^\d{1,2}$/.test(key)) return
    setAmountDollars((value) => {
      const cents = Math.max(0, Math.round(Number.parseFloat(value || '0') * 100))
      const nextDigits = `${cents || ''}${key}`.replace(/^0+/, '')
      if (!nextDigits) return ''
      if (nextDigits.length > 8) return value
      return (Number(nextDigits) / 100).toFixed(2)
    })
  }

  const numericAmount = parseFloat(amountDollars || '0')
  const customerAmount = customerPrice(numericAmount)
  const canContinueFromNumpad = numericAmount >= 0.5

  if (!open) return null

  // Embedded mode: render the panel inline (no fixed overlay) so it can be slotted
  // into the Nexus canvas like other charms (Hub, Codex, Profile, Axiom).
  const Wrapper: FC<{ children: ReactNode }> = ({ children }) => embedded
    ? <div className="pos-modal pos-modal-embedded">{children}</div>
    : (
      <div className="pos-overlay" onClick={onClose}>
        <div className="pos-modal" onClick={(e) => e.stopPropagation()}>{children}</div>
      </div>
    )

  return (
    <Wrapper>
        {/* Header */}
        <div className="pos-header">
          {step !== 'numpad' && step !== 'confirmed' && step !== 'invoice-sent' && (
            <button
              className="pos-back-btn"
              onClick={() => {
                if (step === 'qr') { if (pollRef.current) clearInterval(pollRef.current) }
                if (step === 'source') setStep('numpad')
                else if (step === 'method') setStep(selectedBooking || preselectedBooking ? 'source' : 'numpad')
                else if (step === 'cash-confirm') setStep('method')
                else if (step === 'gift-card-confirm') setStep('method')
                else if (step === 'qr') setStep('method')
              }}
            >
              {Icons.back}
            </button>
          )}
          <h2 className="pos-title">
            {step === 'numpad' && 'Checkout'}
            {step === 'source' && 'Pick Source'}
            {step === 'method' && 'Payment Method'}
            {step === 'cash-confirm' && 'Confirm Cash'}
            {step === 'gift-card-confirm' && 'Redeem Gift Card'}
            {step === 'qr' && 'Scan to Pay'}
            {step === 'confirmed' && (paymentMethod === 'cash' ? 'Cash Recorded' : paymentMethod === 'gift-card' ? 'Gift Card Redeemed' : 'Payment Received')}
            {step === 'invoice-sent' && 'Invoice Sent'}
          </h2>
          <div className="pos-integrations">
            <span className="pos-int-badge">
              <span className="pos-int-dot" />
              Stripe
            </span>
            <span className="pos-int-badge">
              <span className="pos-int-dot pos-int-dot--live" />
              Cash
            </span>
          </div>
        </div>

        {/* Body */}
        <div className="pos-body">
          {error && <div className="pos-error">{error}</div>}

          {/* Step 1: Numpad — enter amount */}
          {step === 'numpad' && (
            <div className="pos-numpad-wrap">
              <div className="pos-numpad-display">
                <span className="pos-numpad-dollar">$</span>
                <span className="pos-numpad-value">{amountDollars || '0.00'}</span>
              </div>
              <div className="pos-numpad-grid">
                {['1','2','3','4','5','6','7','8','9','00','0','⌫'].map(key => (
                  <button
                    key={key}
                    type="button"
                    className={`pos-numpad-key ${key === '⌫' ? 'pos-key-backspace' : ''}`}
                    onClick={() => handleNumpadKey(key)}
                  >
                    {key}
                  </button>
                ))}
              </div>
              <div className="pos-numpad-actions">
                <button
                  type="button"
                  className="pos-numpad-clear"
                  onClick={() => handleNumpadKey('C')}
                  disabled={!amountDollars}
                >
                  Clear
                </button>
                <button
                  type="button"
                  className="btn btn-primary pos-numpad-continue"
                  onClick={() => setStep('source')}
                  disabled={!canContinueFromNumpad}
                >
                  Continue → ${customerAmount.toFixed(2)}
                </button>
              </div>
            </div>
          )}

          {/* Step 2: Source — choose an unassigned method or a known client source */}
          {step === 'source' && (
            <div className="pos-source-wrap">
              <div className="pos-source-amount">
                Charging <strong>${customerAmount.toFixed(2)}</strong>
              </div>

              <div className="pos-source-methods" aria-label="Payment method preference">
                <button
                  type="button"
                  className={`pos-source-method${paymentMethod === 'card' ? ' active' : ''}`}
                  onClick={() => handleSelectUnassignedMethod('card')}
                  aria-pressed={paymentMethod === 'card'}
                >
                  <span>Charge</span>
                  <small>Stripe checkout</small>
                </button>
                <button
                  type="button"
                  className={`pos-source-method${paymentMethod === 'cash' ? ' active' : ''}`}
                  onClick={() => handleSelectUnassignedMethod('cash')}
                  aria-pressed={paymentMethod === 'cash'}
                >
                  <span>Cash</span>
                  <small>Record payment</small>
                </button>
                <button
                  type="button"
                  className={`pos-source-method pos-source-method-gift${paymentMethod === 'gift-card' ? ' active' : ''}`}
                  onClick={() => handleSelectUnassignedMethod('gift-card')}
                  aria-pressed={paymentMethod === 'gift-card'}
                >
                  <span>Gift Card</span>
                  <small>Redeem code</small>
                </button>
              </div>

              {todayBookings.length > 0 && (
                <div className="pos-source-divider">Today's Appointments</div>
              )}

              {todayBookings.length === 0 ? (
                <div className="pos-source-empty">No appointments scheduled for today</div>
              ) : (
                todayBookings.map((b) => {
                  const deposit = b.deposit_amount || 0
                  const total = b.estimated_total || 0
                  const remainder = total > deposit ? total - deposit : 0
                  return (
                    <button key={b.id} className="pos-source-card" onClick={() => handleSelectBooking(b)}>
                      <div className="pos-source-info">
                        <span className="pos-source-label">{b.resolved_name || b.client_name || 'Walk-in'}</span>
                        <span className="pos-source-sub">
                          {b.scheduled_time && <>{b.scheduled_time} · </>}
                          {b.description || 'Tattoo session'}
                        </span>
                      </div>
                      <div className="pos-source-amounts">
                        {deposit > 0 && <span className="pos-booking-deposit">Deposit ${deposit.toFixed(2)}</span>}
                        {remainder > 0 && <span className="pos-booking-remainder">Due ${remainder.toFixed(2)}</span>}
                      </div>
                    </button>
                  )
                })
              )}

              <div className="pos-source-divider">Saved Collectors</div>
              {collectorsLoading ? (
                <div className="pos-source-empty">Loading saved collectors...</div>
              ) : savedCollectors.length === 0 ? (
                <div className="pos-source-empty">No saved collectors yet</div>
              ) : (
                <div className="pos-collector-list">
                  {savedCollectors.map((collector) => (
                    <button
                      key={collector.id}
                      type="button"
                      className="pos-source-card pos-collector-card"
                      onClick={() => handleSelectCollector(collector)}
                    >
                      <span className="pos-collector-avatar" aria-hidden="true">
                        {collector.name.slice(0, 1).toUpperCase()}
                      </span>
                      <span className="pos-source-info">
                        <span className="pos-source-label">{collector.name}</span>
                        <span className="pos-source-sub">{collector.email}</span>
                      </span>
                      <span className="pos-collector-state">
                        {collector.stripe_customer_id ? 'Payment profile' : 'Portal profile'}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Step 3: collect now by cash/card or send the same checkout by email */}
          {step === 'method' && (
            <>
              <div className="pos-source-amount">
                {selectedBooking ? (
                  <>{selectedBooking.resolved_name || selectedBooking.client_name || 'Walk-in'} · <strong>${customerAmount.toFixed(2)}</strong></>
                ) : (
                  <>{clientName || 'Custom charge'} · <strong>${customerAmount.toFixed(2)}</strong></>
                )}
              </div>
              <div className="pos-mode-grid">
                <button className="pos-mode-card pos-method-cash" onClick={() => handleSelectMethod('cash')}>
                  <div className="pos-mode-icon">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/>
                      <path d="M6 10h.01M18 14h.01"/>
                    </svg>
                  </div>
                  <div className="pos-mode-label">Cash</div>
                  <div className="pos-mode-desc">Record payment, deposit to safe</div>
                </button>
                <button
                  className="pos-mode-card pos-method-card pos-mode-icon-gold"
                  onClick={() => handleSelectMethod('card')}
                  disabled={isCreating}
                >
                  <div className="pos-mode-icon pos-mode-icon-gold">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/>
                    </svg>
                  </div>
                  <div className="pos-mode-label">Card</div>
                  <div className="pos-mode-desc">{isCreating ? 'Generating QR…' : 'Stripe QR for client to scan'}</div>
                </button>
                <button
                  className="pos-mode-card pos-method-invoice"
                  onClick={handleSendInvoice}
                  disabled={isCreating || !clientEmail}
                >
                  <div className="pos-mode-icon">{Icons.invoice}</div>
                  <div className="pos-mode-label">Send Invoice</div>
                  <div className="pos-mode-desc">
                    {clientEmail ? `Email secure payment link to ${clientEmail}` : 'Client email required'}
                  </div>
                </button>
                <button className="pos-mode-card pos-method-gift" onClick={() => handleSelectMethod('gift-card')}>
                  <div className="pos-mode-icon">GC</div>
                  <div className="pos-mode-label">Gift Card</div>
                  <div className="pos-mode-desc">Redeem an artist-specific code</div>
                </button>
              </div>
            </>
          )}

          {/* Step 4a: Cash confirm */}
          {step === 'cash-confirm' && (
            <div className="pos-cash-confirm">
              <div className="pos-cash-amount">${customerAmount.toFixed(2)}</div>
              <div className="pos-cash-summary">
                <div><span>Source</span><strong>{selectedBooking ? (selectedBooking.resolved_name || selectedBooking.client_name) : 'Custom'}</strong></div>
                {selectedBooking?.description && (
                  <div><span>Description</span><strong>{selectedBooking.description}</strong></div>
                )}
                <div><span>Method</span><strong>Cash</strong></div>
              </div>
              <p className="pos-cash-note">
                Cash will be recorded and deposited to the safe as a contractor contribution.
              </p>
              <button className="btn btn-primary pos-charge-btn" onClick={handleConfirmCash}>
                Confirm Cash Payment
              </button>
            </div>
          )}

          {step === 'gift-card-confirm' && (
            <div className="pos-cash-confirm">
              <div className="pos-cash-amount">${numericAmount.toFixed(2)}</div>
              <div className="pos-cash-summary">
                <label htmlFor="gift-card-code">Gift-card code</label>
                <input
                  id="gift-card-code"
                  className="pos-form-input"
                  value={giftCardCode}
                  onChange={(event) => setGiftCardCode(event.target.value.toUpperCase())}
                  placeholder="BT-XXXX-XXXX-XXXX"
                  autoComplete="off"
                />
              </div>
              <p className="pos-cash-note">The card must belong to the selected appointment artist.</p>
              <button className="btn btn-primary pos-charge-btn" onClick={handleRedeemGiftCard} disabled={isCreating}>
                {isCreating ? 'Redeeming...' : 'Redeem Gift Card'}
              </button>
            </div>
          )}

          {/* Step: QR Code Display */}
          {step === 'qr' && chargeResult && (
            <div className="pos-qr-container">
              <div className="pos-qr-amount">
                ${(chargeResult.amount_cents / 100).toFixed(2)}
              </div>
              <div className="pos-qr-desc">{chargeResult.description}</div>

              <div className="pos-qr-code">
                <QRCodeSVG
                  value={chargeResult.checkout_url}
                  size={220}
                  level="M"
                  bgColor="transparent"
                  fgColor="#a67d43"
                />
              </div>

              <div className={`pos-qr-status ${paymentStatus}`}>
                {paymentStatus === 'waiting' && (
                  <>
                    <div className="pos-pulse-ring" />
                    <span>Waiting for payment...</span>
                  </>
                )}
                {paymentStatus === 'expired' && (
                  <span className="pos-status-expired">Session expired - go back and try again</span>
                )}
              </div>

              <div className="pos-qr-hint">
                Client scans QR code with their phone camera
              </div>
            </div>
          )}

          {/* Step: Confirmed (cash or card) */}
          {step === 'confirmed' && (paymentMethod === 'cash' ? cashConfirmed : paymentMethod === 'gift-card' ? giftCardResult : chargeResult) && (
            <div className="pos-confirmed">
              {Icons.check}
              <h3 className="pos-confirmed-title">
                {paymentMethod === 'cash' ? 'Cash Recorded' : paymentMethod === 'gift-card' ? 'Gift Card Redeemed' : 'Payment Received'}
              </h3>
              <div className="pos-confirmed-amount">
                ${paymentMethod === 'gift-card'
                  ? (giftCardResult ? (giftCardResult.redeemed_cents / 100).toFixed(2) : '0.00')
                  : paymentMethod === 'cash'
                  ? numericAmount.toFixed(2)
                  : (chargeResult ? (chargeResult.amount_cents / 100).toFixed(2) : '0.00')}
              </div>
              <div className="pos-confirmed-desc">
                {clientName && <span>{clientName}{(chargeResult?.description || description) ? ' · ' : ''}</span>}
                {chargeResult?.description || description || (selectedBooking ? selectedBooking.description : '')}
              </div>
              {paymentMethod === 'cash' && (
                <p className="pos-cash-note">Deposit cash to the safe.</p>
              )}
              {paymentMethod === 'gift-card' && giftCardResult && (
                <p className="pos-cash-note">Remaining balance: ${(giftCardResult.balance_cents / 100).toFixed(2)}</p>
              )}
              <button className="btn btn-primary pos-done-btn" onClick={onClose}>
                Done
              </button>
            </div>
          )}

          {step === 'invoice-sent' && invoiceResult && (
            <div className="pos-confirmed pos-invoice-sent">
              {Icons.check}
              <h3 className="pos-confirmed-title">Invoice Sent</h3>
              <div className="pos-confirmed-amount">
                ${(invoiceResult.amount_cents / 100).toFixed(2)}
              </div>
              <div className="pos-confirmed-desc">
                Secure payment link sent to {invoiceResult.recipient_email}
              </div>
              <button className="btn btn-primary pos-done-btn" onClick={onClose}>
                Done
              </button>
            </div>
          )}
        </div>
    </Wrapper>
  )
}
