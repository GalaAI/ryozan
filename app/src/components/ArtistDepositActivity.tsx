/**
 * Shared artist deposit activity for the desktop Vault and phone Hub workflow.
 * No existing component owns line-item artist deposit history, so this extends
 * the role-scoped booking API without creating another ledger or payment path.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { bookings as bookingsApi, type Booking } from '../lib/api'
import { useAuthStore } from '../stores/auth'

const CLOSED_DEPOSIT_STATUSES = new Set([
  'cancelled',
  'canceled',
  'declined',
  'expired',
  'completed',
  'no_show',
])

type DepositState = 'paid' | 'due' | 'closed'

function formatDepositDate(value: string): string {
  const source = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00` : value
  return new Date(source).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function formatDepositAmount(value: number): string {
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

function depositState(booking: Booking): DepositState {
  if (booking.deposit_paid) return 'paid'
  if (CLOSED_DEPOSIT_STATUSES.has(booking.status.toLowerCase())) return 'closed'
  return 'due'
}

export default function ArtistDepositActivity({ surface = 'vault' }: { surface?: 'vault' | 'workflow' }) {
  const user = useAuthStore((state) => state.user)
  const [bookings, setBookings] = useState<Booking[]>([])
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const rootRef = useRef<HTMLElement>(null)

  useEffect(() => {
    let active = true

    const load = async () => {
      if (user?.role !== 'artist') {
        if (active) setLoadState('failed')
        return
      }

      setLoadState('loading')
      try {
        const firstPage = await bookingsApi.list({ limit: 100 })
        const rows = [...firstPage.bookings]
        for (let skip = 100; skip < firstPage.total; skip += 100) {
          const page = await bookingsApi.list({ skip, limit: 100 })
          rows.push(...page.bookings)
        }
        if (!active) return
        setBookings(rows.filter((booking) => booking.artist_id === user.id))
        setLoadState('ready')
      } catch {
        if (!active) return
        setBookings([])
        setLoadState('failed')
      }
    }

    void load()
    return () => {
      active = false
    }
  }, [user?.id, user?.role])

  useEffect(() => {
    if (surface !== 'vault' || loadState === 'loading') return
    if (new URLSearchParams(window.location.search).get('section') !== 'deposits') return
    rootRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [loadState, surface])

  const deposits = useMemo(() => bookings
    .filter((booking) => (booking.deposit_amount ?? 0) > 0)
    .sort((left, right) => {
      const leftDate = left.scheduled_date ?? left.preferred_date ?? left.created_at
      const rightDate = right.scheduled_date ?? right.preferred_date ?? right.created_at
      return rightDate.localeCompare(leftDate)
    }), [bookings])

  const customerPaid = deposits
    .filter((booking) => depositState(booking) === 'paid')
    .reduce((sum, booking) => sum + (booking.deposit_amount ?? 0), 0)
  const quotedDue = deposits
    .filter((booking) => depositState(booking) === 'due')
    .reduce((sum, booking) => sum + (booking.deposit_amount ?? 0), 0)

  return (
    <section
      ref={rootRef}
      id="artist-deposit-activity"
      className={`vault-card card artist-deposit-activity artist-deposit-activity--${surface}`}
      aria-busy={loadState === 'loading'}
    >
      <div className="nexus-panel-header">
        <span>Deposit activity</span>
        <small>{loadState === 'ready' ? `${deposits.length} booking${deposits.length === 1 ? '' : 's'}` : 'Artist earnings'}</small>
      </div>
      <div className="vault-card-body">
        {loadState === 'loading' ? (
          <div className="vault-empty">Loading deposit activity.</div>
        ) : loadState === 'failed' ? (
          <div className="vault-empty">
            Deposit activity could not be loaded. No zero-dollar history is being substituted.
          </div>
        ) : deposits.length === 0 ? (
          <div className="vault-empty">No deposits have been assigned to your bookings yet.</div>
        ) : (
          <>
            <div className="artist-deposit-summary" aria-label="Artist deposit totals">
              <div><span>Customer paid</span><strong>{formatDepositAmount(customerPaid)}</strong></div>
              <div><span>Quoted due</span><strong>{formatDepositAmount(quotedDue)}</strong></div>
            </div>
            <p className="artist-deposit-disclosure">
              Paid is the customer charge recorded after checkout. Due is the quoted deposit before checkout.
            </p>
            <div className="artist-deposit-list">
              {deposits.map((booking) => {
                const state = depositState(booking)
                const date = booking.scheduled_date ?? booking.preferred_date ?? booking.created_at
                return (
                  <div className="artist-deposit-row" key={booking.id}>
                    <div>
                      <strong>{booking.resolved_name || booking.client_name || booking.guest_name || 'Client'}</strong>
                      <small>{formatDepositDate(date)} · {booking.description || 'Tattoo booking'}</small>
                    </div>
                    <span>
                      <strong>{formatDepositAmount(booking.deposit_amount ?? 0)}</strong>
                      <small className={`artist-deposit-state artist-deposit-state--${state}`}>
                        {state === 'paid' ? 'Paid · customer charge' : state === 'due' ? 'Due · quoted' : 'Closed'}
                      </small>
                    </span>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    </section>
  )
}
