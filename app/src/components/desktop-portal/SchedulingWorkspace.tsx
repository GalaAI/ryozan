import { useEffect, useRef, useState } from 'react'
import type { Booking, BookingScheduleSuggestion } from '../../lib/api'

type Suggestions = Awaited<ReturnType<typeof import('../../lib/api').bookings.scheduleSuggestions>>

export interface SchedulingWorkspaceProps {
  bookings: Booking[]
  loadSuggestions: (bookingId: string) => Promise<Suggestions>
  saveSchedule: (bookingId: string, slot: Pick<BookingScheduleSuggestion, 'date' | 'time' | 'duration_minutes'>) => Promise<Booking>
}

// HomeWorkspace owns the calendar view and BookyWorkspace owns booking details.
// This action workspace reuses their theme and the existing suggestion contract
// while pending schedule saves reuse the existing validated booking update.
export default function SchedulingWorkspace({ bookings, loadSuggestions, saveSchedule }: SchedulingWorkspaceProps) {
  const [selectedId, setSelectedId] = useState('')
  const [saving, setSaving] = useState(false)
  const booking = bookings.find(item => item.id === selectedId) ?? bookings[0]

  return <section aria-label="Scheduling workspace" data-scheduling-workspace style={{ display: 'grid', gap: 16, minWidth: 0, color: 'var(--ink)' }}>
    <style>{`[data-scheduling-workspace] button:focus-visible,[data-scheduling-workspace] select:focus-visible{outline:2px solid var(--ag-accent);outline-offset:2px}[data-scheduling-workspace] p{overflow-wrap:anywhere}`}</style>
    <p>Preview availability, then explicitly save a time for a pending inquiry. Saving does not confirm the appointment, reserve a calendar slot, or notify the client.</p>
    {!booking ? <p role="status">No bookings are loaded. Load a booking before checking availability.</p> : <>
      <label style={{ display: 'grid', gap: 8 }}>Booking
        <select aria-label="Booking" disabled={saving} value={booking.id} onChange={event => setSelectedId(event.target.value)} style={{ maxWidth: '100%', minWidth: 0, padding: 10, background: 'var(--surface)', color: 'var(--ink)', border: '1px solid var(--line-2)', borderRadius: 8 }}>
          {bookings.map(item => <option key={item.id} value={item.id}>{item.resolved_name ?? item.client_name ?? item.guest_name ?? 'Name not provided'} · {item.reference_code ?? item.id} · {item.status}</option>)}
        </select>
      </label>
      <BookingSuggestions key={booking.id} booking={booking} loadSuggestions={loadSuggestions} saveSchedule={saveSchedule} onSaving={setSaving} />
    </>}
  </section>
}

function BookingSuggestions({ booking, loadSuggestions, saveSchedule, onSaving }: { booking: Booking; loadSuggestions: SchedulingWorkspaceProps['loadSuggestions']; saveSchedule: SchedulingWorkspaceProps['saveSchedule']; onSaving: (saving: boolean) => void }) {
  const generation = useRef(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [response, setResponse] = useState<Suggestions | null>(null)
  const [preview, setPreview] = useState<BookingScheduleSuggestion | null>(null)
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const [saveError, setSaveError] = useState('')
  const [saved, setSaved] = useState<Booking | null>(null)

  useEffect(() => () => { generation.current += 1 }, [])

  const load = async () => {
    if (loading || savingRef.current) return
    const request = ++generation.current
    setLoading(true)
    setError(null)
    setResponse(null)
    setPreview(null)
    setSaved(null)
    setSaveError('')
    try {
      const result = await loadSuggestions(booking.id)
      if (request !== generation.current) return
      if (result.booking_id !== booking.id || result.artist_id !== booking.artist_id) throw new Error('The availability response does not match this booking and artist. Please retry.')
      setResponse(result)
    } catch (failure) {
      if (request === generation.current) setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      if (request === generation.current) setLoading(false)
    }
  }

  const save = async () => {
    if (!preview || booking.status !== 'pending' || savingRef.current) return
    const request = generation.current
    savingRef.current = true; setSaving(true); onSaving(true); setSaveError('')
    try {
      const result = await saveSchedule(booking.id, preview)
      if (request !== generation.current) return
      setSaved(result); setPreview(null); setResponse(null)
    } catch (failure) {
      if (request === generation.current) setSaveError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      savingRef.current = false
      if (request === generation.current) { setSaving(false); onSaving(false) }
    }
  }

  return <div style={{ display: 'grid', gap: 12, minWidth: 0 }} aria-busy={loading || saving}>
    <p style={{ color: 'var(--ink-mute)' }}>{booking.description || 'No description provided.'}</p>
    <button type="button" disabled={loading || saving} onClick={() => { void load() }}>{loading ? 'Checking availability…' : error ? 'Retry availability' : response ? 'Refresh availability' : 'Check availability'}</button>
    <div role="status" aria-live="polite">{loading ? 'Loading suggestions for the selected booking.' : response ? `${response.suggestions.length} suggestions returned.` : !error ? 'Choose Check availability to load suggestions.' : ''}</div>
    {error && <p role="alert">Availability could not be loaded: {error}</p>}
    {saveError && <p role="alert">{saveError}</p>}
    {saved && <section aria-label="Schedule saved"><p role="status">Saved and read back: {saved.reference_code} · {saved.scheduled_date} at {saved.scheduled_time} · {saved.duration_minutes} minutes · {saved.status}.</p><p>Still pending. This does not confirm or reserve the appointment.</p></section>}
    {response && <>
      <p>Timezone: {response.timezone}. Duration: {response.duration_minutes} minutes.</p>
      <p style={{ color: 'var(--ink-mute)' }}>{response.calendar_connected ? 'Calendar connection reported by the API. Suggestions are not reserved and must be rechecked before confirmation.' : 'Calendar is not connected. These suggestions do not prove freedom from external calendar conflicts.'}</p>
      {response.suggestions.length === 0 ? <p>No suggested times were returned. Refresh to check again; no appointment has changed.</p> : <div role="group" aria-label="Suggested times" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,240px),1fr))', gap: 10 }}>
        {response.suggestions.map((suggestion, index) => <button key={`${suggestion.date}-${suggestion.time}-${index}`} type="button" disabled={saving} aria-pressed={preview === suggestion} onClick={() => setPreview(suggestion)} style={{ display: 'grid', gap: 6, padding: 12, textAlign: 'left', background: 'var(--surface)', color: 'var(--ink)', border: `1px solid ${preview === suggestion ? 'var(--ag-accent)' : 'var(--line-2)'}`, borderRadius: 10, overflowWrap: 'anywhere' }}>
          <strong>{suggestion.label || `${suggestion.date} ${suggestion.time}`}</strong>
          <span>{suggestion.date} · {suggestion.time} · {suggestion.duration_minutes} minutes</span>
          <span style={{ color: 'var(--ink-mute)' }}>{suggestion.rationale}</span>
          <span>Source: {suggestion.source}. Confidence: {suggestion.confidence}.</span>
        </button>)}
      </div>}
      {preview && <section aria-label="Selected time preview" style={{ border: '1px solid var(--ag-accent)', borderRadius: 10, padding: 12 }}>
        <p role="status">Preview only: {preview.date} at {preview.time} ({response.timezone}), {preview.duration_minutes} minutes. No appointment has changed.</p>
        <button type="button" disabled={saving} onClick={() => setPreview(null)}>Clear preview</button>
        {booking.status === 'pending' ? <button type="button" disabled={saving} onClick={() => { void save() }}>{saving ? 'Saving pending schedule…' : 'Save pending schedule'}</button> : <p>Only pending inquiries can be saved here. Existing appointments require the rescheduling workflow.</p>}
      </section>}
    </>}
  </div>
}
