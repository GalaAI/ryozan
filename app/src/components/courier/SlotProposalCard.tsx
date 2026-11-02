/**
 * Slot Proposal artifact card.
 *
 * Renders a `message_type === 'slot_proposal'` message as an interactive
 * mini-calendar card. The client picks one slot from the artist's
 * whitelisted options. Staff see which slot was selected (or "pending").
 *
 * States: pending → confirmed (after select_slot) or cancelled.
 */
import { useState } from 'react'
import { messages as messagesApi, type ConversationMessage } from '../../lib/api'
import { studioTimeZone } from '../../hooks/useStudioSettings'
import '../../styles/courier.css'

interface ProposedSlot {
  date: string
  time: string
  duration_minutes: number
  label: string | null
}

interface SlotProposalState {
  status: 'pending' | 'confirmed' | 'cancelled'
  selected_index: number | null
  confirmed_at: string | null
  cancelled_at: string | null
}

interface SlotProposalCardProps {
  message: ConversationMessage
  viewerIsClient: boolean
  onStateChange?: (newState: SlotProposalState) => void
}

function formatSlotLabel(slot: ProposedSlot): string {
  if (slot.label) return slot.label
  try {
    const d = new Date(`${slot.date}T${slot.time}`)
    const day = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: studioTimeZone() })
    const timeStr = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: studioTimeZone() })
    return `${day} · ${timeStr}`
  } catch {
    return `${slot.date} ${slot.time}`
  }
}

function formatDuration(mins: number): string {
  if (mins >= 60) return `${mins / 60}h`
  return `${mins}m`
}

export function SlotProposalCard({ message, viewerIsClient, onStateChange }: SlotProposalCardProps) {
  const payload = message.artifact_payload as unknown as { booking_id: string; slots: ProposedSlot[] } | null
  const state = (message.artifact_state ?? { status: 'pending' }) as unknown as SlotProposalState
  const slots = payload?.slots ?? []
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectSlot = async (index: number) => {
    setBusy(true)
    setError(null)
    try {
      const res = await messagesApi.artifactAction(message.id, 'select_slot', { slot_index: index })
      onStateChange?.(res.artifact_state as unknown as SlotProposalState)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to select slot')
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await messagesApi.artifactAction(message.id, 'cancel')
      onStateChange?.(res.artifact_state as unknown as SlotProposalState)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to cancel')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sp-card">
      <div className="qp-card-header">
        <span className="qp-card-label">Available Slots</span>
        {state.status === 'confirmed' && <span className="qp-card-badge qp-card-badge--ok">Booked</span>}
        {state.status === 'cancelled' && <span className="qp-card-badge qp-card-badge--mute">Cancelled</span>}
        {state.status === 'pending' && <span className="qp-card-badge">Pick one</span>}
      </div>

      <div className="sp-slot-list">
        {slots.map((slot, i) => {
          const isSelected = state.selected_index === i
          const isPending = state.status === 'pending'
          return (
            <button
              key={`${slot.date}-${slot.time}`}
              type="button"
              className={`sp-slot-btn${isSelected ? ' sp-slot-btn--selected' : ''}${!isPending && !isSelected ? ' sp-slot-btn--muted' : ''}`}
              disabled={busy || !isPending || !viewerIsClient}
              onClick={() => selectSlot(i)}
            >
              <span className="sp-slot-label">{formatSlotLabel(slot)}</span>
              <span className="sp-slot-dur">{formatDuration(slot.duration_minutes)}</span>
              {isSelected && <span className="sp-slot-check">✓</span>}
            </button>
          )
        })}
      </div>

      {error && <div className="qp-card-error">{error}</div>}

      {/* Client can cancel a pending proposal */}
      {viewerIsClient && state.status === 'pending' && (
        <button
          type="button"
          className="sp-cancel-btn"
          disabled={busy}
          onClick={cancel}
        >
          None of these work
        </button>
      )}

      {/* Staff notes */}
      {!viewerIsClient && state.status === 'pending' && (
        <div className="qp-card-note">Client is choosing a time slot.</div>
      )}
      {!viewerIsClient && state.status === 'confirmed' && state.confirmed_at && (
        <div className="qp-card-note">
          Slot confirmed: {slots[state.selected_index ?? 0] && formatSlotLabel(slots[state.selected_index ?? 0])}
          {' · '}{new Date(state.confirmed_at).toLocaleString('en-US', { timeZone: studioTimeZone(), month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}
        </div>
      )}
    </div>
  )
}
