/**
 * Staff approval card for a booking request.
 *
 * Mounts inside the booking's thread (Courier on desktop, the Booky booking
 * card on the phone portal). Staff pick one of the collector's offered times,
 * or enter the time they agreed by message, confirm the deposit (prefilled
 * from the artist's preset), and send. The backend emails the collector
 * "request approved, deposit pending" with a deposit link only when the
 * artist's Stripe Connect account is ready; otherwise the card states that the
 * email carries the deposit due without a payment link.
 *
 * Built from @bttco/ui primitives and the existing Huddl slot styles
 * (.bkm-collector-*), so it adds no colours, sizes or fonts.
 */
import { useEffect, useMemo, useState } from 'react'
import { Button, StatusText, TextField } from '@bttco/ui'
import {
  bookings as bookingsApi,
  type Booking,
  type BookingApprovalDefaults,
  type BookingApprovalResult,
} from '../../lib/api'
import { customerPrice, formatRequestedSlot, isApprovableStatus, requestedBookingSlots } from '../../lib/format'

export interface ApprovalSlot {
  date: string
  time: string
}

type ApprovableBooking = Pick<Booking, 'id' | 'status' | 'preferred_slots' | 'preferred_date' | 'preferred_time'> &
  Partial<Pick<Booking, 'deposit_authorization_status'>>

const DEPOSIT_SOURCE_HINT: Record<BookingApprovalDefaults['deposit_source'], string> = {
  booking: 'Saved on this booking',
  session_type: 'Artist preset for this session',
  duration_rule: 'Shop default for this length',
  consultation: 'Consultations take no deposit',
}

function sameSlot(a: ApprovalSlot | null, b: ApprovalSlot | null): boolean {
  return Boolean(a && b && a.date === b.date && a.time === b.time)
}

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message
  return 'Could not approve this request'
}

export default function BookingApprovalPanel({
  booking,
  initialSlot = null,
  initialCustom = false,
  initialDuration,
  onApproved,
  onMessage,
  messageLabel = 'Message to coordinate',
}: {
  booking: ApprovableBooking
  initialSlot?: ApprovalSlot | null
  initialCustom?: boolean
  /** Length picked in Huddl for the handed-over time; otherwise the booking default. */
  initialDuration?: number | null
  onApproved?: (result: BookingApprovalResult) => void
  onMessage?: () => void
  messageLabel?: string
}) {
  const offered = useMemo(
    () => requestedBookingSlots({
      preferred_slots: booking.preferred_slots,
      preferred_date: booking.preferred_date,
      preferred_time: booking.preferred_time,
    }).filter((slot): slot is ApprovalSlot => Boolean(slot.date && slot.time)),
    [booking.preferred_slots, booking.preferred_date, booking.preferred_time],
  )
  const initialIsOffered = initialSlot ? offered.some((slot) => sameSlot(slot, initialSlot)) : false
  const [selected, setSelected] = useState<ApprovalSlot | null>(initialIsOffered ? initialSlot : null)
  const [custom, setCustom] = useState(initialCustom || Boolean(initialSlot && !initialIsOffered))
  const [customDate, setCustomDate] = useState(initialSlot && !initialIsOffered ? initialSlot.date : '')
  const [customTime, setCustomTime] = useState(initialSlot && !initialIsOffered ? initialSlot.time : '')
  const [defaults, setDefaults] = useState<BookingApprovalDefaults | null>(null)
  const [deposit, setDeposit] = useState('')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BookingApprovalResult | null>(null)

  useEffect(() => {
    let cancelled = false
    bookingsApi.approvalDefaults(booking.id)
      .then((loaded) => {
        if (cancelled) return
        setDefaults(loaded)
        setDeposit(String(loaded.deposit_amount))
      })
      .catch((err) => { if (!cancelled) setError(errorMessage(err)) })
    return () => { cancelled = true }
  }, [booking.id])

  const chosen: ApprovalSlot | null = custom
    ? (customDate && customTime ? { date: customDate, time: customTime } : null)
    : selected
  const open = custom || selected !== null
  const chosenDuration = (initialDuration && chosen && initialSlot && sameSlot(chosen, initialSlot))
    ? initialDuration
    : defaults?.duration_minutes
  const depositValue = Number(deposit)
  const depositValid = deposit.trim() !== '' && Number.isFinite(depositValue) && depositValue >= 0
  const needsDeposit = depositValid && depositValue > 0
  const canSubmit = Boolean(chosen && depositValid && defaults && !submitting)

  const pick = (slot: ApprovalSlot) => {
    setCustom(false)
    setSelected(sameSlot(slot, selected) ? null : slot)
    setError(null)
  }

  const submit = async () => {
    if (!chosen || !defaults) return
    setSubmitting(true)
    setError(null)
    try {
      const approved = await bookingsApi.approve(booking.id, {
        scheduled_date: chosen.date,
        scheduled_time: chosen.time,
        duration_minutes: chosenDuration,
        deposit_amount: Math.round(depositValue * 100) / 100,
        artist_message: note.trim() || undefined,
      })
      setResult(approved)
      onApproved?.(approved)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  if ((!isApprovableStatus(booking.status) || booking.deposit_authorization_status === 'authorized') && !result) return null

  if (result) {
    const when = formatRequestedSlot(result.scheduled_date, result.scheduled_time)
    const collectorAmount = customerPrice(result.deposit_amount)
    const outcome = result.deposit_amount > 0
      ? `Approved for ${when}. $${collectorAmount} deposit due.`
      : `Approved and booked for ${when}. No deposit needed.`
    const email = !result.email_queued
      ? result.deposit_url
        ? 'No collector email on file. Send them the deposit link below.'
        : 'No collector email on file, so no email was sent.'
      : result.deposit_link_sent
        ? 'Approval email with the deposit link is queued for the collector.'
        : 'Approval email is queued for the collector. It has no deposit link.'
    return (
      <div className="bkm-collector-slots" data-booking-approval="done" aria-live="polite">
        <StatusText data-approval-copy tone="ok">{outcome}</StatusText>
        <StatusText data-approval-copy tone={result.email_queued ? 'info' : 'pending'}>{email}</StatusText>
        {!result.email_queued && result.deposit_url && (
          <>
            <TextField label="Deposit link" value={result.deposit_url} readOnly onFocus={(e) => e.target.select()} />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => { void navigator.clipboard?.writeText(result.deposit_url ?? '').catch(() => {}) }}
            >
              Copy deposit link
            </Button>
          </>
        )}
      </div>
    )
  }

  return (
    <div className="bkm-collector-slots" data-booking-approval="open" aria-label="Approve this request">
      <div className="bkm-collector-slots-head">
        <span className="bkm-edit-label">Approve request</span>
        <span>{offered.length ? 'Pick a time or message to coordinate' : 'Set a time or message to coordinate'}</span>
      </div>

      <div className="bkm-collector-slot-grid">
        {offered.map((slot) => {
          const isSelected = !custom && sameSlot(slot, selected)
          return (
            <button
              key={`${slot.date}-${slot.time}`}
              type="button"
              className={`bkm-collector-slot${isSelected ? ' selected' : ''}`}
              aria-pressed={isSelected}
              onClick={() => pick(slot)}
            >
              <span>{formatRequestedSlot(slot.date, slot.time)}</span>
            </button>
          )
        })}
        <button
          type="button"
          className={`bkm-collector-slot${custom ? ' selected' : ''}`}
          aria-pressed={custom}
          onClick={() => { setCustom(!custom); setSelected(null); setError(null) }}
        >
          <span>Another time</span>
        </button>
      </div>

      {custom && (
        <div className="bkm-slot-confirm-row">
          <TextField label="Date" type="date" value={customDate} onChange={(e) => setCustomDate(e.target.value)} />
          <TextField label="Time" type="time" value={customTime} onChange={(e) => setCustomTime(e.target.value)} />
        </div>
      )}

      {open && (
        <>
          <div className="bkm-slot-confirm-row">
            <TextField
              label="Deposit ($)"
              type="number"
              inputMode="decimal"
              min={0}
              step={5}
              value={deposit}
              onChange={(e) => setDeposit(e.target.value)}
              hint={defaults
                ? `${DEPOSIT_SOURCE_HINT[defaults.deposit_source]}${needsDeposit ? `. Collector pays $${customerPrice(depositValue)}` : ''}`
                : 'Loading preset...'}
            />
            <TextField
              label="Note to collector (optional)"
              value={note}
              maxLength={1000}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>

          {defaults && needsDeposit && (defaults.stripe_ready ? (
            <StatusText data-approval-copy tone="ok">The approval email includes a deposit link.</StatusText>
          ) : (
            <StatusText data-approval-copy tone="pending">
              Card deposits are not set up for {defaults.artist_name}, so the approval email states the deposit due without a payment link.
            </StatusText>
          ))}

          <div className="bkm-slot-confirm-row">
            <span className="bkm-slot-selected-chip">
              {chosen ? formatRequestedSlot(chosen.date, chosen.time) : 'Choose a date and time'}
              {chosen && chosenDuration ? ` · ${chosenDuration % 60 === 0 ? `${chosenDuration / 60}h` : `${chosenDuration} min`}` : ''}
              {needsDeposit ? ` · $${depositValue} deposit` : depositValid ? ' · no deposit' : ''}
            </span>
            <Button variant="primary" size="sm" type="button" disabled={!canSubmit} onClick={submit}>
              {submitting ? 'Approving...' : needsDeposit ? 'Approve and request deposit' : 'Approve and book'}
            </Button>
          </div>
        </>
      )}

      {error && <StatusText data-approval-copy tone="danger" role="alert">{error}</StatusText>}

      {onMessage && (
        <div className="bkm-slot-confirm-row">
          <Button variant="ghost" size="sm" type="button" onClick={onMessage}>{messageLabel}</Button>
        </div>
      )}
    </div>
  )
}
