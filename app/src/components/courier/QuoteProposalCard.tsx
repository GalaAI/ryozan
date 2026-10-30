/**
 * Quote Proposal artifact card.
 *
 * Renders a `message_type === 'quote_proposal'` message as an
 * interactive card with CONFIRM / CANCEL actions. Used in both the
 * staff-side Courier thread and the client-side public quote page.
 *
 * The card's visual state is driven entirely by the backend's
 * `artifact_state.status`:
 *
 *   - pending           → shows CONFIRM + CANCEL (client-facing)
 *                         shows "Awaiting client response" (staff-facing)
 *   - awaiting_payment  → shows "Pay deposit →" link to Stripe
 *   - confirmed         → shows ✓ confirmed + timestamp
 *   - cancelled         → muted "cancelled" banner
 *   - expired           → muted "expired" banner
 *
 * Actions dispatch via `messagesApi.artifactAction()` and the caller
 * refreshes the conversation to see the new state.
 */
import { useState } from 'react'
import { messages as messagesApi, type ConversationMessage } from '../../lib/api'
import { formatCustomerPrice } from '../../lib/format'
import '../../styles/courier.css'

interface QuoteProposalPayload {
  booking_id: string
  deposit_amount: number
  estimated_total_min: number | null
  estimated_total_max: number | null
  expires_in_days: number
}

interface QuoteProposalState {
  status: 'pending' | 'awaiting_payment' | 'confirmed' | 'cancelled' | 'expired'
  confirmed_at: string | null
  cancelled_at: string | null
  checkout_url: string | null
  stripe_checkout_session_id: string | null
}

interface QuoteProposalCardProps {
  message: ConversationMessage
  /** True if the viewer is the client who can confirm/cancel. False for staff-side preview. */
  viewerIsClient: boolean
  /** Called after a successful action so the parent can refetch the conversation. */
  onStateChange?: (newState: QuoteProposalState) => void
}

function formatMoney(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return formatCustomerPrice(n)
}

function formatPriceRange(min: number | null, max: number | null): string {
  if (min !== null && max !== null && min !== max) {
    return `${formatMoney(min)} – ${formatMoney(max)}`
  }
  if (max !== null) return formatMoney(max)
  if (min !== null) return formatMoney(min)
  return 'To be discussed'
}

export function QuoteProposalCard({ message, viewerIsClient, onStateChange }: QuoteProposalCardProps) {
  const payload = (message.artifact_payload ?? {}) as unknown as QuoteProposalPayload
  const state = (message.artifact_state ?? { status: 'pending' }) as unknown as QuoteProposalState
  const [busy, setBusy] = useState<null | 'confirm' | 'cancel'>(null)
  const [error, setError] = useState<string | null>(null)

  const hasDeposit = (payload.deposit_amount ?? 0) > 0

  const dispatch = async (action: 'confirm' | 'cancel') => {
    setBusy(action)
    setError(null)
    try {
      const res = await messagesApi.artifactAction(message.id, action)
      const newState = res.artifact_state as unknown as QuoteProposalState
      onStateChange?.(newState)
      // If confirm routed to Stripe, redirect to checkout
      if (action === 'confirm' && newState.status === 'awaiting_payment' && newState.checkout_url) {
        window.location.href = newState.checkout_url
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="qp-card">
      <div className="qp-card-header">
        <span className="qp-card-label">Quote Proposal</span>
        {state.status === 'confirmed' && <span className="qp-card-badge qp-card-badge--ok">Confirmed</span>}
        {state.status === 'cancelled' && <span className="qp-card-badge qp-card-badge--mute">Cancelled</span>}
        {state.status === 'expired' && <span className="qp-card-badge qp-card-badge--mute">Expired</span>}
        {state.status === 'awaiting_payment' && <span className="qp-card-badge qp-card-badge--warn">Awaiting payment</span>}
        {state.status === 'pending' && <span className="qp-card-badge">Pending</span>}
      </div>

      <table className="qp-card-rows">
        <tbody>
          <tr>
            <td>Estimated Total</td>
            <td className="qp-card-value">{formatPriceRange(payload.estimated_total_min, payload.estimated_total_max)}</td>
          </tr>
          <tr>
            <td>Deposit</td>
            <td className="qp-card-value qp-card-value--gold">
              {hasDeposit ? formatMoney(payload.deposit_amount) : 'No deposit required'}
            </td>
          </tr>
        </tbody>
      </table>

      {error && <div className="qp-card-error">{error}</div>}

      {/* Client-facing pending actions */}
      {viewerIsClient && state.status === 'pending' && (
        <div className="qp-card-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy !== null}
            onClick={() => dispatch('confirm')}
          >
            {busy === 'confirm' ? 'Processing…' : hasDeposit ? 'Confirm & Pay' : 'Confirm'}
          </button>
          <button
            type="button"
            className="btn btn-outline"
            disabled={busy !== null}
            onClick={() => dispatch('cancel')}
          >
            Decline
          </button>
        </div>
      )}

      {/* Client-facing awaiting payment — show resume-checkout link */}
      {viewerIsClient && state.status === 'awaiting_payment' && state.checkout_url && (
        <div className="qp-card-actions">
          <a className="btn btn-primary" href={state.checkout_url}>
            Resume checkout →
          </a>
          <button
            type="button"
            className="btn btn-outline"
            disabled={busy !== null}
            onClick={() => dispatch('cancel')}
          >
            Cancel
          </button>
        </div>
      )}

      {/* Staff-facing preview states */}
      {!viewerIsClient && state.status === 'pending' && (
        <div className="qp-card-note">Waiting on client to confirm or decline.</div>
      )}
      {!viewerIsClient && state.status === 'awaiting_payment' && (
        <div className="qp-card-note">Client confirmed — awaiting Stripe payment.</div>
      )}
      {!viewerIsClient && state.status === 'confirmed' && state.confirmed_at && (
        <div className="qp-card-note">Confirmed {new Date(state.confirmed_at).toLocaleString()}</div>
      )}
    </div>
  )
}
