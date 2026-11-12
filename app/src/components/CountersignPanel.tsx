/**
 * CountersignPanel -- Artist waiver counter-signature workflow.
 *
 * Shows waivers with status "pending_review" assigned to the current artist.
 * Artist can review client details, see their signature, then countersign
 * to mark the waiver as completed.
 */

import { useEffect, useState, useCallback, useRef } from 'react'
import { useAuthStore } from '../stores/auth'
import {
  waiver as waiverApi,
  type WaiverListItem,
  type WaiverDetail,
} from '../lib/api'
import SignaturePad from './SignaturePad'
import CountersignModal from './CountersignModal'

/* ── SVG Icons ── */

const Icons = {
  pen: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
    </svg>
  ),
  check: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12"/>
    </svg>
  ),
  chevronLeft: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 18 9 12 15 6"/>
    </svg>
  ),
  fileText: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>
    </svg>
  ),
  user: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
    </svg>
  ),
  mail: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
    </svg>
  ),
  clock: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
    </svg>
  ),
  alertCircle: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
    </svg>
  ),
  checkCircle: (
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
    </svg>
  ),
}

/* ── Styles ── */

const styles: Record<string, React.CSSProperties> = {
  panel: {
    background: 'var(--bg-surface)',
    border: '1px solid var(--border-subtle)',
    borderRadius: '12px',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 'var(--space-4) var(--space-5)',
    borderBottom: '1px solid var(--border-subtle)',
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--space-3)',
  },
  headerTitle: {
    fontFamily: "'Inter', sans-serif",
    fontSize: '0.85rem',
    fontWeight: 700,
    color: 'var(--text-primary)',
    margin: 0,
    textTransform: 'none' as const,
  },
  badge: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: '22px',
    height: '22px',
    padding: '0 6px',
    borderRadius: '11px',
    fontSize: '0.75rem',
    fontWeight: 700,
    background: 'var(--accent)',
    color: '#000',
  },
  list: {
    maxHeight: '400px',
    overflowY: 'auto' as const,
  },
  listItem: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 'var(--space-3) var(--space-5)',
    borderBottom: '1px solid var(--border-subtle)',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  listItemLeft: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '2px',
  },
  clientName: {
    fontSize: '0.9rem',
    fontWeight: 600,
    color: 'var(--text-primary)',
  },
  listMeta: {
    fontSize: '0.78rem',
    color: 'var(--text-muted)',
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
  },
  reviewBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 14px',
    borderRadius: '8px',
    border: '1px solid var(--gold)',
    background: 'transparent',
    color: 'var(--gold)',
    fontSize: '0.8rem',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  // Detail view
  detailHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--space-3)',
    padding: 'var(--space-4) var(--space-5)',
    borderBottom: '1px solid var(--border-subtle)',
  },
  backBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '32px',
    height: '32px',
    borderRadius: '8px',
    border: '1px solid var(--border-subtle)',
    background: 'transparent',
    color: 'var(--text-primary)',
    cursor: 'pointer',
  },
  detailBody: {
    padding: 'var(--space-5)',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 'var(--space-5)',
  },
  section: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 'var(--space-2)',
  },
  sectionLabel: {
    fontSize: '0.72rem',
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
    color: 'var(--text-muted)',
    margin: 0,
  },
  fieldRow: {
    display: 'flex',
    gap: 'var(--space-4)',
    flexWrap: 'wrap' as const,
  },
  field: {
    flex: '1 1 200px',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '2px',
  },
  fieldLabel: {
    fontSize: '0.72rem',
    color: 'var(--text-muted)',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
  },
  fieldValue: {
    fontSize: '0.88rem',
    color: 'var(--text-primary)',
  },
  clientSig: {
    padding: 'var(--space-4)',
    background: 'rgba(255,255,255,0.03)',
    borderRadius: '8px',
    border: '1px solid var(--border-subtle)',
    textAlign: 'center' as const,
  },
  clientSigText: {
    fontFamily: "'Georgia', 'Times New Roman', serif",
    fontStyle: 'italic',
    fontSize: '1.4rem',
    color: 'var(--text-primary)',
    margin: 0,
  },
  sigDate: {
    fontSize: '0.75rem',
    color: 'var(--text-muted)',
    marginTop: 'var(--space-2)',
  },
  // Artist signature input
  sigInputWrapper: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 'var(--space-3)',
  },
  sigInput: {
    width: '100%',
    padding: 'var(--space-3) var(--space-2)',
    background: 'transparent',
    border: 'none',
    borderBottom: '2px solid var(--accent)',
    fontFamily: "'Georgia', 'Times New Roman', serif",
    fontStyle: 'italic',
    fontSize: '1.3rem',
    color: 'var(--text-primary)',
    outline: 'none',
    textAlign: 'center' as const,
  },
  sigHint: {
    fontSize: '0.72rem',
    color: 'var(--text-muted)',
    textAlign: 'center' as const,
  },
  submitBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '8px',
    width: '100%',
    padding: '12px',
    borderRadius: '10px',
    border: 'none',
    background: 'var(--accent)',
    color: '#000',
    fontSize: '0.9rem',
    fontWeight: 700,
    cursor: 'pointer',
    transition: 'opacity 0.15s',
  },
  submitBtnDisabled: {
    opacity: 0.4,
    cursor: 'not-allowed',
  },
  // Success state
  successBox: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    gap: 'var(--space-4)',
    padding: 'var(--space-8) var(--space-5)',
    textAlign: 'center' as const,
  },
  successTitle: {
    fontSize: '1.1rem',
    fontWeight: 700,
    color: 'var(--text-primary)',
    margin: 0,
  },
  successSub: {
    fontSize: '0.85rem',
    color: 'var(--text-muted)',
    margin: 0,
  },
  // Error
  errorBar: {
    padding: 'var(--space-3) var(--space-4)',
    background: 'rgba(220, 50, 50, 0.1)',
    border: '1px solid rgba(220, 50, 50, 0.3)',
    borderRadius: '8px',
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
    fontSize: '0.82rem',
    color: '#dc3232',
  },
  empty: {
    padding: 'var(--space-6) var(--space-5)',
    textAlign: 'center' as const,
    color: 'var(--text-muted)',
    fontSize: '0.85rem',
  },
  loading: {
    padding: 'var(--space-5)',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 'var(--space-3)',
  },
  skeleton: {
    height: '48px',
    borderRadius: '8px',
    background: 'var(--border-subtle)',
    animation: 'pulse 1.5s ease-in-out infinite',
  },
}

/* ── Helpers ── */

function formatDate(iso: string | null): string {
  if (!iso) return '--'
  try {
    return new Date(iso).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  } catch {
    return iso
  }
}


/* ── Component ── */

type ViewState =
  | { mode: 'list' }
  | { mode: 'detail'; waiverId: string }
  | { mode: 'success'; refCode: string | null }

type CountersignPanelProps = {
  className?: string
  variant?: 'standard' | 'dock'
  onPendingCountChange?: (count: number) => void
  openRequestToken?: number
}

export default function CountersignPanel({
  className,
  variant = 'standard',
  onPendingCountChange,
  openRequestToken = 0,
}: CountersignPanelProps) {
  const user = useAuthStore((s) => s.user)
  const isDock = variant === 'dock'
  const [pending, setPending] = useState<WaiverListItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [view, setView] = useState<ViewState>({ mode: 'list' })
  const [detail, setDetail] = useState<WaiverDetail | null>(null)
  const [detailLoading] = useState(false)
  const [signature, setSignature] = useState('')
  const [conditionNotes, setConditionNotes] = useState('good')
  const [aftercareCert, setAftercareCert] = useState(false)
  const [saveSigForFuture, setSaveSigForFuture] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [modalWaiverId, setModalWaiverId] = useState<string | null>(null)
  const handledOpenRequest = useRef(0)
  const formReady = !!signature && aftercareCert && !submitting

  const userName = user?.name
  const fetchPending = useCallback(() => waiverApi.list({ status: 'pending_review', limit: 50 })
    .then((res) => {
      // Filter to only show waivers assigned to the current artist
      const myWaivers = userName
        ? res.waivers.filter((w: WaiverListItem) =>
            !w.artist_name || w.artist_name.toLowerCase() === userName.toLowerCase()
          )
        : res.waivers
      setPending(myWaivers)
    })
    .catch(() => {
      // silent
    })
    .finally(() => setIsLoading(false)), [userName])

  useEffect(() => {
    fetchPending()
  }, [fetchPending])

  useEffect(() => {
    onPendingCountChange?.(pending.length)
  }, [onPendingCountChange, pending.length])

  useEffect(() => {
    if (openRequestToken <= 0 || openRequestToken === handledOpenRequest.current || pending.length === 0) return
    handledOpenRequest.current = openRequestToken
    setModalWaiverId(pending[0].id)
  }, [openRequestToken, pending])

  const handleCountersign = async () => {
    if (!signature || !aftercareCert || view.mode !== 'detail') return
    setSubmitting(true)
    setError(null)
    try {
      const price = detail?.quoted_price || 'N/A'
      const res = await waiverApi.countersign(view.waiverId, {
        signature_data: signature,
        session_duration_minutes: undefined,
        client_condition_notes: conditionNotes,
        notes: `Aftercare certified. Condition: ${conditionNotes}. Price: $${price}`,
      })
      // Remove from pending list
      setPending((prev) => prev.filter((w) => w.id !== view.waiverId))
      setView({ mode: 'success', refCode: res.reference_code })
    } catch (err) {
      setError((err as Error).message || 'Countersign failed')
    } finally {
      setSubmitting(false)
    }
  }

  const goBack = () => {
    setView({ mode: 'list' })
    setDetail(null)
    setSignature('')
    setConditionNotes('good')
    setAftercareCert(false)
    setError(null)
  }

  // Don't render for non-artist roles
  if (!user || !['assistant', 'artist', 'admin', 'founder'].includes(user.role)) {
    return null
  }

  // Don't render when nothing is pending and we're in list view
  if (view.mode === 'list' && !isLoading && pending.length === 0) {
    return null
  }

  return (
    <div
      className={['countersign-panel', isDock ? 'countersign-panel--dock' : '', className].filter(Boolean).join(' ')}
      style={styles.panel}
    >
      {/* ── List View ── */}
      {view.mode === 'list' && (
        <>
          <div style={styles.header}>
            <div style={styles.headerLeft}>
              {Icons.pen}
              <h3 style={styles.headerTitle}>Waivers Pending Review</h3>
              {pending.length > 0 && (
                <span style={styles.badge}>{pending.length}</span>
              )}
            </div>
          </div>

          {isLoading ? (
            <div style={styles.loading}>
              {[1, 2, 3].map((i) => (
                <div key={i} style={styles.skeleton} />
              ))}
            </div>
          ) : (
            <div style={{ ...styles.list, maxHeight: isDock ? '126px' : styles.list.maxHeight }}>
              {pending.map((w) => (
                <div
                  key={w.id}
                  style={styles.listItem}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.background = 'transparent')
                  }
                >
                  <div style={styles.listItemLeft}>
                    <span style={styles.clientName}>
                      {w.guest_name || 'Unknown Client'}{/* waivers always write guest_name directly — no client_id path */}
                    </span>
                    <span style={styles.listMeta}>
                      {Icons.clock}
                      {formatDate(w.signed_at || w.created_at)}
                      {w.reference_code && (
                        <>
                          <span style={{ opacity: 0.4 }}>|</span>
                          {w.reference_code}
                        </>
                      )}
                    </span>
                  </div>
                  <button
                    className="cs-review-btn"
                    onClick={() => setModalWaiverId(w.id)}
                  >
                    {Icons.fileText} Review & Sign
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Detail View (Artist Sign-Off Only) ── */}
      {view.mode === 'detail' && (
        <>
          <div style={styles.detailHeader}>
            <button style={styles.backBtn} onClick={goBack}>
              {Icons.chevronLeft}
            </button>
            <h3 style={styles.headerTitle}>
              {detail ? `${detail.full_name} ${detail.reference_code ? `\u00b7 ${detail.reference_code}` : ''}` : 'Loading...'}
            </h3>
          </div>

          {detailLoading ? (
            <div style={styles.loading}>
              {[1, 2].map((i) => (
                <div key={i} style={styles.skeleton} />
              ))}
            </div>
          ) : detail ? (
            <div style={styles.detailBody}>
              {/* Photo ID */}
              <div style={styles.section}>
                <p style={styles.sectionLabel}>Photo ID</p>
                {detail.photo_id_url ? (
                  <div style={{ borderRadius: '8px', overflow: 'hidden', border: '1px solid var(--border-subtle)' }}>
                    <img
                      src={detail.photo_id_url}
                      alt="Client Photo ID"
                      style={{ width: '100%', maxHeight: '200px', objectFit: 'contain', display: 'block', background: 'rgba(0,0,0,0.2)' }}
                    />
                  </div>
                ) : (
                  <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', fontStyle: 'italic', margin: 0 }}>No photo ID uploaded</p>
                )}
              </div>

              {/* Post-Procedure Sign-Off */}
              <div style={styles.section}>
                <p style={styles.sectionLabel}>Post-Procedure Sign-Off</p>

                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Artist</span>
                  <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>{detail.artist_name || user?.name || 'Unknown'}</span>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-subtle)' }}>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Agreed Price</span>
                  <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {detail.quoted_price ? `$${detail.quoted_price}` : 'Not specified'}
                  </span>
                </div>

                {/* Condition Dropdown */}
                <div style={{ marginTop: '0.75rem' }}>
                  <label style={{ ...styles.sectionLabel, marginBottom: '0.35rem', display: 'block' }}>Condition of Client</label>
                  <select
                    style={{
                      width: '100%',
                      padding: '0.5rem 0.75rem',
                      background: 'var(--bg-surface)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '8px',
                      color: 'var(--text-primary)',
                      fontSize: '0.85rem',
                      fontFamily: 'inherit',
                      outline: 'none',
                      appearance: 'auto' as const,
                    }}
                    value={conditionNotes || 'good'}
                    onChange={(e) => setConditionNotes(e.target.value)}
                  >
                    <option value="good">Good</option>
                    <option value="minor_irritation">Minor Irritation</option>
                    <option value="needs_followup">Needs Follow-up</option>
                  </select>
                </div>

                {/* Aftercare Certification Checkbox */}
                <label className="form-checkbox-label" style={{
                  marginTop: '0.75rem',
                  padding: '0.75rem',
                  background: 'rgba(255,255,255,0.02)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '8px',
                  fontSize: '0.75rem',
                  lineHeight: 1.5,
                }}>
                  <input
                    type="checkbox"
                    checked={aftercareCert}
                    onChange={(e) => setAftercareCert(e.target.checked)}
                  />
                  <span>
                    I certify that {detail.full_name} was given proper aftercare instructions,
                    is leaving in {(conditionNotes || 'good') === 'good' ? 'good' : 'noted'} condition,
                    and client paid the agreed price of ${detail.quoted_price || '___'}.
                  </span>
                </label>
              </div>

              {/* Artist Signature */}
              <div style={styles.section}>
                <p style={styles.sectionLabel}>Artist Signature</p>
                <SignaturePad
                  onSign={setSignature}
                  value={signature}
                  strokeColor="#e8e0d4"
                />
              </div>

              {/* Save signature for future waivers */}
              <label className="form-checkbox-label">
                <input
                  type="checkbox"
                  checked={saveSigForFuture}
                  onChange={(e) => setSaveSigForFuture(e.target.checked)}
                />
                <span>Save my signature for future waivers</span>
              </label>

              {/* Error */}
              {error && (
                <div style={styles.errorBar}>
                  {Icons.alertCircle} {error}
                </div>
              )}

              {/* Submit (hidden until form is complete) */}
              {formReady && (
                <button
                  className="cs-review-btn"
                  style={{ width: '100%', justifyContent: 'center', padding: '12px' }}
                  onClick={handleCountersign}
                >
                  {submitting ? 'Submitting...' : 'Submit'}
                </button>
              )}
            </div>
          ) : error ? (
            <div style={{ padding: 'var(--space-5)' }}>
              <div style={styles.errorBar}>
                {Icons.alertCircle} {error}
              </div>
            </div>
          ) : null}
        </>
      )}

      {/* ── Success View (auto-returns to list) ── */}
      {view.mode === 'success' && (
        <SuccessAnimation
          refCode={view.refCode}
          pendingCount={pending.length}
          onDone={goBack}
        />
      )}

      {modalWaiverId && (
        <CountersignModal
          waiverId={modalWaiverId}
          onClose={() => setModalWaiverId(null)}
          onCompleted={() => {
            const completedId = modalWaiverId
            setModalWaiverId(null)
            if (completedId) {
              setPending((prev) => prev.filter((w) => w.id !== completedId))
            }
          }}
        />
      )}
    </div>
  )
}

/* ── Success animation: checkmark + auto-return ── */

function SuccessAnimation({ refCode, pendingCount, onDone }: {
  refCode: string | null
  pendingCount: number
  onDone: () => void
}) {
  useEffect(() => {
    const timer = setTimeout(onDone, 2500)
    return () => clearTimeout(timer)
  }, [onDone])

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '2.5rem 1.5rem',
      gap: '0.75rem',
      animation: 'cs-fade-in 0.3s ease-out',
    }}>
      <style>{`
        @keyframes cs-fade-in {
          from { opacity: 0; transform: scale(0.9); }
          to { opacity: 1; transform: scale(1); }
        }
        @keyframes cs-check-draw {
          from { stroke-dashoffset: 30; }
          to { stroke-dashoffset: 0; }
        }
      `}</style>
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" opacity="0.3" />
        <polyline
          points="9 12 11.5 14.5 16 9.5"
          style={{ strokeDasharray: 30, animation: 'cs-check-draw 0.5s ease-out 0.2s both' }}
        />
      </svg>
      <h3 style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--gold)', margin: 0 }}>
        Signed & Archived
      </h3>
      <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: 0, textAlign: 'center' }}>
        {refCode ? `${refCode} completed.` : 'Waiver completed.'}
        {pendingCount > 0 && ` ${pendingCount} remaining.`}
      </p>
    </div>
  )
}
