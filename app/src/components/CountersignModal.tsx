/**
 * CountersignModal -- Quick-sign workflow spawned from the Inbox.
 *
 * Workflow:
 *   1. Opens with waiver detail + artist's saved signature pre-filled
 *   2. Date/time auto-filled to now (editable)
 *   3. Condition pre-set to "good", aftercare pre-checked
 *   4. Confirm or Deny buttons
 *   5. Confirm -> spawns a printable one-page completed waiver document
 */

import { useEffect, useState, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useAuthStore } from '../stores/auth'
import {
  waiver as waiverApi,
  artistSignature as artistSigApi,
  type WaiverDetail,
} from '../lib/api'
import SignaturePad from './SignaturePad'

interface CountersignModalProps {
  waiverId: string
  onClose: () => void
  onCompleted?: () => void
}

type ModalView = 'loading' | 'review' | 'document' | 'denied' | 'error'

const CONDITION_OPTIONS = [
  { value: 'good', label: 'Good' },
  { value: 'minor_irritation', label: 'Minor Irritation' },
  { value: 'needs_followup', label: 'Needs Follow-up' },
]

function fmtDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (withTime) {
      return d.toLocaleString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit',
      })
    }
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return iso
  }
}

function toLocalDateTimeInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function fmtPrice(p: string | null | undefined): string {
  if (!p) return '—'
  const cleaned = String(p).replace(/^\$+/, '').trim()
  return cleaned ? `$${cleaned}` : '—'
}

export default function CountersignModal({ waiverId, onClose, onCompleted }: CountersignModalProps) {
  const user = useAuthStore(s => s.user)
  const [view, setView] = useState<ModalView>('loading')
  const [detail, setDetail] = useState<WaiverDetail | null>(null)
  const [savedSigUrl, setSavedSigUrl] = useState<string | null>(null)
  const [signature, setSignature] = useState<string>('')          // base64 data URL of new sig (if user re-signed)
  const [useSignaturePad, setUseSignaturePad] = useState(false)   // toggle re-sign vs use saved
  const [condition, setCondition] = useState('good')
  const [aftercareCert, setAftercareCert] = useState(true)
  const [signedAt, setSignedAt] = useState<string>(toLocalDateTimeInput(new Date()))
  const [priceOverride, setPriceOverride] = useState<string>('')        // Artist must add price if missing
  const [placementOverride, setPlacementOverride] = useState<string>('') // Artist must add placement if missing
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [completedRefCode, setCompletedRefCode] = useState<string | null>(null)
  const [completedSignedAt, setCompletedSignedAt] = useState<string | null>(null)
  const printRef = useRef<HTMLDivElement>(null)

  // Load waiver + saved signature
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [w, sig] = await Promise.all([
          waiverApi.get(waiverId),
          artistSigApi.get().catch(() => ({ signature_url: null })),
        ])
        if (cancelled) return
        setDetail(w)
        setSavedSigUrl(sig.signature_url)
        // If no saved sig, force the SignaturePad to be visible so the artist can sign now
        if (!sig.signature_url) setUseSignaturePad(true)
        setView('review')
      } catch (err) {
        if (cancelled) return
        setError((err as Error).message || 'Failed to load waiver')
        setView('error')
      }
    }
    load()
    return () => { cancelled = true }
  }, [waiverId])

  // Lock body scroll when modal is open
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  const effectiveSignature = useSignaturePad ? signature : (savedSigUrl || '')
  const effectivePrice = (detail?.quoted_price && String(detail.quoted_price).trim()) || priceOverride.trim()
  const effectivePlacement = (detail?.tattoo_placement && String(detail.tattoo_placement).trim()) || placementOverride.trim()
  const canConfirm = !!effectiveSignature && aftercareCert && !!effectivePrice && !!effectivePlacement && !submitting
  // Check-in intake health line: sobriety first (SC DPH 801.C), then any Yes answers the artist should see.
  const healthSummary = (() => {
    if (!detail) return ''
    const yes = (v?: string | null) => String(v || '').toLowerCase() === 'yes'
    const flags = [
      yes(detail.pregnant) && 'pregnant or nursing',
      yes(detail.blood_thinners) && 'blood thinners',
      yes(detail.blood_disorder) && 'bleeding disorder',
      yes(detail.skin_condition) && 'skin condition at site',
      detail.allergies && !/^none$/i.test(detail.allergies.trim()) && `allergies: ${detail.allergies}`,
    ].filter(Boolean)
    const parts: string[] = []
    if (detail.intoxicated) parts.push(yes(detail.intoxicated) ? 'NOT SOBER' : 'Sober')
    parts.push(flags.length ? `Flags: ${flags.join(', ')}` : (detail.intoxicated ? 'No flags' : ''))
    if (detail.health_same_as_last_visit) parts.push('same as last visit')
    return parts.filter(Boolean).join(' · ')
  })()

  const handleConfirm = async () => {
    if (!canConfirm || !detail) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await waiverApi.countersign(waiverId, {
        signature_data: effectiveSignature,
        client_condition_notes: condition,
        notes: `Aftercare certified. Condition: ${condition}. Price: ${fmtPrice(effectivePrice)}`,
      })
      // If artist signed with a fresh signature and it's not yet saved, persist it for next time
      if (useSignaturePad && signature && !savedSigUrl) {
        try { await artistSigApi.save(signature) } catch { /* non-blocking */ }
      }
      setCompletedRefCode(res.reference_code)
      setCompletedSignedAt(res.artist_signed_at)
      setView('document')
      onCompleted?.()
    } catch (err) {
      setError((err as Error).message || 'Countersign failed')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDeny = async () => {
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await waiverApi.updateStatus(waiverId, 'rejected', 'Denied at countersign')
      setView('denied')
      onCompleted?.()
    } catch (err) {
      setError((err as Error).message || 'Deny failed')
    } finally {
      setSubmitting(false)
    }
  }

  const handlePrint = () => window.print()

  // Guard against SSR / HMR edge cases where document.body might not yet exist
  if (typeof document === 'undefined' || !document.body) return null

  const clientFirstName = detail?.full_name.split(' ')[0] || 'Client'
  const reviewArtistName = detail?.artist_name || user?.name || '—'
  const reviewPrice = effectivePrice ? fmtPrice(effectivePrice) : 'Pending confirmation'
  const reviewPlacement = effectivePlacement || 'Pending placement confirmation'

  return createPortal(
    <div
      className="cs-modal-overlay"
      onClick={(e) => { if (e.target === e.currentTarget && view !== 'document') onClose() }}
      role="dialog"
      aria-modal="true"
    >
      <div className={`cs-modal ${view === 'document' ? 'cs-modal-document' : ''}`}>
        {/* Close button (top-right) */}
        <button className="cs-modal-close" onClick={onClose} aria-label="Close">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>

        {/* ── Loading ── */}
        {view === 'loading' && (
          <div className="cs-modal-loading">
            <span className="cs-spinner" />
            <span>Loading waiver…</span>
          </div>
        )}

        {/* ── Error ── */}
        {view === 'error' && (
          <div className="cs-modal-loading">
            <p style={{ color: '#dc3232' }}>{error || 'Failed to load.'}</p>
            <button className="cs-btn cs-btn-secondary" onClick={onClose}>Close</button>
          </div>
        )}

        {/* ── Denied confirmation ── */}
        {view === 'denied' && (
          <div className="cs-modal-loading">
            <h3 style={{ color: 'var(--text)', margin: 0 }}>Waiver denied</h3>
            <p style={{ color: 'var(--text-muted)', textAlign: 'center', maxWidth: 320 }}>
              The waiver has been marked as rejected. The client will be notified.
            </p>
            <button className="cs-btn cs-btn-primary" onClick={onClose}>Done</button>
          </div>
        )}

        {/* ── Review (countersign form) ── */}
        {view === 'review' && detail && (
          <>
            <header className="cs-modal-header">
              <span className="cs-modal-kicker">Countersign Review</span>
              <h2 className="cs-modal-title">Waiver</h2>
              <span className="cs-modal-sub">
                {detail.full_name}{detail.reference_code ? ` · ${detail.reference_code}` : ''}
              </span>
            </header>

            <div className="cs-modal-body bt-scrollbar">
              <div className="cs-review-shell">
                <div className="cs-review-main">
                  <div className="cs-fact-grid">
                    <div className="cs-fact"><span>Artist</span><strong>{reviewArtistName}</strong></div>
                    <div className="cs-fact"><span>Tattoo</span><strong>{detail.tattoo_description || '—'}</strong></div>
                    <div className="cs-fact"><span>Placement</span><strong>{effectivePlacement || <em style={{ color: 'var(--gold)', fontWeight: 400 }}>required ↓</em>}</strong></div>
                    <div className="cs-fact"><span>Quoted Price</span><strong>{effectivePrice ? fmtPrice(effectivePrice) : <em style={{ color: 'var(--gold)', fontWeight: 400 }}>required ↓</em>}</strong></div>
                    {detail.session_date && (
                      <div className="cs-fact"><span>Session Date</span><strong>{detail.session_date}</strong></div>
                    )}
                    {healthSummary && (
                      <div className="cs-fact"><span>Health</span><strong>{healthSummary}</strong></div>
                    )}
                  </div>

                  {!detail.tattoo_placement && (
                    <label className="cs-field">
                      <span>Placement <em className="cs-required">(required - client did not provide)</em></span>
                      <input
                        type="text"
                        value={placementOverride}
                        onChange={(e) => setPlacementOverride(e.target.value)}
                        placeholder="e.g. Right forearm, inner"
                      />
                    </label>
                  )}

                  {!detail.quoted_price && (
                    <label className="cs-field">
                      <span>Agreed Price <em className="cs-required">(required - confirm before signing)</em></span>
                      <input
                        type="text"
                        inputMode="decimal"
                        value={priceOverride}
                        onChange={(e) => setPriceOverride(e.target.value.replace(/^\$+/, ''))}
                        placeholder="e.g. 180"
                      />
                    </label>
                  )}

                  <label className="cs-field">
                    <span>Date & Time</span>
                    <input
                      type="datetime-local"
                      value={signedAt}
                      onChange={(e) => setSignedAt(e.target.value)}
                    />
                  </label>

                  <label className="cs-field">
                    <span>Condition of Client</span>
                    <select value={condition} onChange={(e) => setCondition(e.target.value)}>
                      {CONDITION_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>

                  <label className="cs-checkbox">
                    <input type="checkbox" checked={aftercareCert} onChange={(e) => setAftercareCert(e.target.checked)} />
                    <span>
                      I certify that {clientFirstName} received aftercare instructions,
                      is leaving in {CONDITION_OPTIONS.find(o => o.value === condition)?.label.toLowerCase() || 'good'} condition,
                      and paid the agreed price of {fmtPrice(effectivePrice)}.
                    </span>
                  </label>

                  <div className="cs-field">
                    <span>Artist Signature</span>
                    {!useSignaturePad && savedSigUrl ? (
                      <div className="cs-saved-sig">
                        <img src={savedSigUrl} alt="Saved signature" />
                        <button className="cs-link-btn" onClick={() => { setUseSignaturePad(true); setSignature('') }}>
                          Re-sign
                        </button>
                      </div>
                    ) : (
                      <>
                        <SignaturePad onSign={setSignature} value={signature} strokeColor="#e8e0d4" height={120} />
                        {savedSigUrl && (
                          <button className="cs-link-btn" onClick={() => { setUseSignaturePad(false); setSignature('') }}>
                            Use saved signature
                          </button>
                        )}
                      </>
                    )}
                  </div>

                  {error && <div className="cs-error">{error}</div>}
                </div>

                <aside className="cs-review-aside">
                  <section className="cs-evidence-card cs-evidence-card-contract">
                    <div className="cs-evidence-header">
                      <span className="cs-evidence-kicker">Contract</span>
                      <span className="cs-evidence-status">Ready for countersign</span>
                    </div>
                    <h3 className="cs-evidence-title">Tattoo Consent & Release Waiver</h3>
                    <div className="cs-contract-meta">
                      <div><span>Client</span><strong>{detail.full_name}</strong></div>
                      <div><span>Artist</span><strong>{reviewArtistName}</strong></div>
                      <div><span>Placement</span><strong>{reviewPlacement}</strong></div>
                      <div><span>Price</span><strong>{reviewPrice}</strong></div>
                    </div>
                    <p className="cs-contract-copy">
                      Client affirms identity verification, medical disclosure, consent to procedure,
                      and acknowledgement of aftercare and risk release for the session described above.
                    </p>
                  </section>

                  <section className="cs-evidence-card">
                    <div className="cs-evidence-header">
                      <span className="cs-evidence-kicker">Evidence</span>
                    </div>
                    <div className="cs-evidence-grid">
                      <div className="cs-proof-card">
                        <span className="cs-proof-label">Photo ID{detail.photo_id_reused_from ? ' · on file, check in person' : ''}</span>
                        {detail.photo_id_url ? (
                          <img src={detail.photo_id_url} alt="Client photo ID" className="cs-proof-image cs-proof-image-id" />
                        ) : (
                          <div className="cs-proof-empty">No photo ID</div>
                        )}
                      </div>
                      <div className="cs-proof-card">
                        <span className="cs-proof-label">Client Signature{detail.signature_adopted_from ? ' · saved, confirmed today' : ''}</span>
                        {detail.signature_url ? (
                          <img src={detail.signature_url} alt="Client signature" className="cs-proof-image cs-proof-image-signature" />
                        ) : (
                          <div className="cs-proof-empty">No signature</div>
                        )}
                      </div>
                    </div>
                  </section>
                </aside>
              </div>
            </div>

            <footer className="cs-modal-footer">
              <button
                className="cs-btn cs-btn-secondary"
                onClick={handleDeny}
                disabled={submitting}
              >
                {submitting ? 'Working…' : 'Deny'}
              </button>
              <button
                className="cs-btn cs-btn-primary"
                onClick={handleConfirm}
                disabled={!canConfirm}
              >
                {submitting ? 'Signing…' : 'Confirm & Sign'}
              </button>
            </footer>
          </>
        )}

        {/* ── Completed waiver document (printable one-pager) ── */}
        {view === 'document' && detail && (
          <>
            <header className="cs-modal-header cs-no-print">
              <h2 className="cs-modal-title">Waiver Complete</h2>
              <span className="cs-modal-sub">{completedRefCode || detail.reference_code}</span>
            </header>

            <div className="cs-doc-scroll" ref={printRef}>
              <article className="cs-doc">
                <header className="cs-doc-header">
                  <h1>Tattoo Consent & Release Waiver</h1>
                  <div className="cs-doc-meta">
                    <span><strong>Reference:</strong> {completedRefCode || detail.reference_code}</span>
                    <span><strong>Status:</strong> Completed</span>
                  </div>
                </header>

                <section className="cs-doc-section">
                  <h2>Client Information</h2>
                  <div className="cs-doc-grid">
                    <div><span>Full Name</span><strong>{detail.full_name}</strong></div>
                    <div><span>Date of Birth</span><strong>{fmtDate(detail.date_of_birth)}</strong></div>
                    <div><span>Phone</span><strong>{detail.phone}</strong></div>
                    <div><span>Email</span><strong>{detail.email}</strong></div>
                    <div><span>Emergency Contact</span><strong>{detail.emergency_contact}</strong></div>
                    {detail.emergency_contact_address && <div><span>Emergency Address</span><strong>{detail.emergency_contact_address}</strong></div>}
                    {detail.social_handle && <div><span>Social</span><strong>{detail.social_handle}</strong></div>}
                  </div>
                </section>

                <section className="cs-doc-section">
                  <h2>Procedure</h2>
                  <div className="cs-doc-grid">
                    <div><span>Artist</span><strong>{detail.artist_name}</strong></div>
                    <div><span>Description</span><strong>{detail.tattoo_description}</strong></div>
                    <div><span>Placement</span><strong>{detail.tattoo_placement}</strong></div>
                    <div><span>Agreed Price</span><strong>{fmtPrice(detail.quoted_price)}</strong></div>
                  </div>
                </section>

                <section className="cs-doc-section">
                  <h2>Medical Disclosures</h2>
                  <div className="cs-doc-grid">
                    <div><span>Pregnant / Nursing</span><strong>{detail.pregnant}</strong></div>
                    <div><span>Blood Disorder</span><strong>{detail.blood_disorder}</strong></div>
                    <div><span>Skin Condition</span><strong>{detail.skin_condition}</strong></div>
                    <div><span>Allergies</span><strong>{detail.allergies || 'None'}</strong></div>
                    <div className="cs-doc-grid-wide"><span>Other Conditions</span><strong>{detail.medical_conditions || 'None'}</strong></div>
                  </div>
                </section>

                <section className="cs-doc-section">
                  <h2>Photo ID on File</h2>
                  {detail.photo_id_url ? (
                    <img src={detail.photo_id_url} alt="Client photo ID" className="cs-doc-id" />
                  ) : (
                    <p className="cs-doc-empty">No photo ID uploaded.</p>
                  )}
                </section>

                <section className="cs-doc-section">
                  <h2>Acknowledgement & Release</h2>
                  <p className="cs-doc-legal">
                    I, {detail.full_name}, acknowledge that I have been fully informed of the inherent risks
                    associated with receiving a tattoo. I confirm that I am of legal age, am not under the
                    influence of alcohol or drugs, and have disclosed all relevant medical conditions. I release
                    {' '}{detail.artist_name} and Ryozan Studio from any and all claims arising out of
                    this procedure, including but not limited to allergic reactions, infection, scarring, and
                    dissatisfaction with the result. I acknowledge receipt of aftercare instructions and accept
                    sole responsibility for proper aftercare.
                  </p>
                </section>

                <section className="cs-doc-section cs-doc-sigs">
                  <div className="cs-doc-sig-block">
                    <h3>Client Signature</h3>
                    {detail.signature_url ? (
                      <img src={detail.signature_url} alt="Client signature" className="cs-doc-sig-img" />
                    ) : (
                      <div className="cs-doc-sig-empty">No signature</div>
                    )}
                    <div className="cs-doc-sig-meta">
                      <span>{detail.full_name}</span>
                      <span>{fmtDate(detail.signed_at, true)}</span>
                    </div>
                  </div>
                  <div className="cs-doc-sig-block">
                    <h3>Artist Signature</h3>
                    {effectiveSignature ? (
                      <img src={effectiveSignature} alt="Artist signature" className="cs-doc-sig-img" />
                    ) : (
                      <div className="cs-doc-sig-empty">No signature</div>
                    )}
                    <div className="cs-doc-sig-meta">
                      <span>{detail.artist_name || user?.name}</span>
                      <span>{fmtDate(completedSignedAt || signedAt, true)}</span>
                    </div>
                  </div>
                </section>

              </article>
            </div>

            <footer className="cs-modal-footer cs-no-print">
              <button className="cs-btn cs-btn-secondary" onClick={onClose}>Close</button>
              <button className="cs-btn cs-btn-primary" onClick={handlePrint}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>
                </svg>
                Print
              </button>
            </footer>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
