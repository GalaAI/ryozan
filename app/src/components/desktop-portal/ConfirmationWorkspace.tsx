import { useEffect, useRef, useState } from 'react'
import type { Booking, BookingOutcomeEnvelope, BookyDecisionEnvelope } from '../../lib/api'
import type { DesktopBookingConfirmationData, DesktopResource } from '../../lib/desktop-portal-api'

export interface ConfirmationWorkspaceProps {
  bookings: Booking[]
  loadConfirmation: (id: string) => Promise<DesktopBookingConfirmationData>
}

// SchedulingWorkspace owns schedule changes and BookyWorkspace owns booking
// details. This separate read-only panel reuses their theme to inspect actual
// confirmation projections and lifecycle receipts without delivery actions.
export default function ConfirmationWorkspace({ bookings, loadConfirmation }: ConfirmationWorkspaceProps) {
  const [selectedId, setSelectedId] = useState('')
  const booking = bookings.find(item => item.id === selectedId) ?? bookings[0]
  return <section aria-label="Confirmation workspace" data-confirmation-workspace style={{ display: 'grid', gap: 16, minWidth: 0, color: 'var(--ink)' }}>
    <style>{`[data-confirmation-workspace] button:focus-visible,[data-confirmation-workspace] select:focus-visible{outline:2px solid var(--ag-accent);outline-offset:2px}[data-confirmation-workspace] p,[data-confirmation-workspace] dd,[data-confirmation-workspace] li{overflow-wrap:anywhere}[data-confirmation-workspace] dd{margin:0}[data-confirmation-workspace] dt{color:var(--ink-mute)}`}</style>
    <p>Inspect recorded confirmation states and lifecycle history. Loading or refreshing here does not confirm a booking, reserve a time, or send a message.</p>
    {!booking ? <p role="status">No bookings are loaded.</p> : <>
      <label style={{ display: 'grid', gap: 8 }}>Booking
        <select aria-label="Confirmation booking" value={booking.id} onChange={event => setSelectedId(event.target.value)} style={{ maxWidth: '100%', minWidth: 0, padding: 10, background: 'var(--surface)', color: 'var(--ink)', border: '1px solid var(--line-2)', borderRadius: 8 }}>
          {bookings.map(item => <option key={item.id} value={item.id}>{item.resolved_name ?? item.client_name ?? item.guest_name ?? 'Name not provided'} · {item.reference_code ?? item.id} · {item.status}</option>)}
        </select>
      </label>
      <ConfirmationRead key={booking.id} bookingId={booking.id} loadConfirmation={loadConfirmation} />
    </>}
  </section>
}

function ResourceNotice({ resource }: { resource: DesktopResource<unknown> }) {
  if (resource.status === 'error') return <p role="alert">Could not load: {resource.error}</p>
  if (resource.status === 'blocked') return <p role="status">Unavailable: {resource.error}</p>
  if (resource.status === 'loading') return <p role="status">Loading.</p>
  return null
}

function Outcome({ value }: { value: BookingOutcomeEnvelope | BookyDecisionEnvelope }) {
  const fields = [
    ['Truth state', value.truth_state], ['Booking status', value.booking_status],
    ['Calendar', value.calendar_state], ['Invitation', value.invitation_state],
    ['Meeting', value.meet_state], ['Courier', value.courier_state], ['Updated', value.updated_at],
  ]
  return <>
    <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,200px),1fr))', gap: 12 }}>
      {fields.map(([label, state]) => <div key={label}><dt>{label}</dt><dd>{state || 'Not provided'}</dd></div>)}
    </dl>
    <p>These are recorded API states, not an independent verification of customer delivery.</p>
    {'receipt_ids' in value && <>
      <p>Action ID: {value.action_id || 'Not provided'}</p>
      <p>{value.explanation || 'No explanation provided.'}</p>
      <h4>Receipt IDs</h4>
      {value.receipt_ids.length ? <ul>{value.receipt_ids.map((id, index) => <li key={`${id}-${index}`}>{id}</li>)}</ul> : <p>No receipt IDs returned.</p>}
      {value.blocked_reasons.length > 0 && <><h4>Recorded blocking reasons</h4><ul>{value.blocked_reasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul></>}
    </>}
  </>
}

function ConfirmationRead({ bookingId, loadConfirmation }: { bookingId: string; loadConfirmation: ConfirmationWorkspaceProps['loadConfirmation'] }) {
  const generation = useRef(0)
  const busy = useRef(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<DesktopBookingConfirmationData | null>(null)
  useEffect(() => () => { generation.current += 1 }, [])

  async function load() {
    if (busy.current) return
    busy.current = true
    const request = ++generation.current
    setLoading(true); setError(null); setData(null)
    try {
      const result = await loadConfirmation(bookingId)
      if (request !== generation.current) return
      if (result.booking.id !== bookingId
        || (result.outcome.data && 'booking_id' in result.outcome.data && result.outcome.data.booking_id !== bookingId)
        || result.history.data?.some(event => event.booking_id !== bookingId)) {
        throw new Error('The confirmation response does not match this booking. Reload the selected booking.')
      }
      setData(result)
    } catch (failure) {
      if (request === generation.current) setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      if (request === generation.current) { busy.current = false; setLoading(false) }
    }
  }

  return <div aria-busy={loading} style={{ display: 'grid', gap: 12, minWidth: 0 }}>
    <button type="button" disabled={loading} onClick={() => { void load() }}>{loading ? 'Loading confirmation records…' : error ? 'Retry loading records' : data ? 'Refresh records' : 'Load confirmation records'}</button>
    <div role="status" aria-live="polite">{loading ? 'Loading records for the selected booking.' : data ? 'Records loaded.' : !error ? 'Choose Load confirmation records to inspect this booking.' : ''}</div>
    {error && <p role="alert">Confirmation records could not be loaded: {error}</p>}
    {data && <>
      <p>Booking: {data.booking.reference_code ?? data.booking.id}. Current booking status: {data.booking.status}.</p>
      <section aria-label="Confirmation outcome" style={{ border: '1px solid var(--line-2)', borderRadius: 10, padding: 12 }}>
        <h3>Confirmation outcome</h3>
        <ResourceNotice resource={data.outcome} />
        {data.outcome.data && <Outcome value={data.outcome.data} />}
      </section>
      <section aria-label="Lifecycle history" style={{ border: '1px solid var(--line-2)', borderRadius: 10, padding: 12 }}>
        <h3>Lifecycle history</h3>
        <ResourceNotice resource={data.history} />
        {data.history.data && (data.history.data.length ? <ol style={{ display: 'grid', gap: 12 }}>
          {data.history.data.map(event => <li key={event.id}>
            <p>{event.event_type} · {event.created_at}</p>
            <p>{event.summary || 'No summary provided.'}</p>
            <p style={{ color: 'var(--ink-mute)' }}>Event ID: {event.id}. Source: {event.source || 'Not provided'}.</p>
          </li>)}
        </ol> : <p>No lifecycle events returned.</p>)}
      </section>
    </>}
  </div>
}
