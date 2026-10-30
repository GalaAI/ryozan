/**
 * Codex: Unified operations panel for the Ryozan dashboard.
 *
 * Sections (single scrollable panel, no tabs):
 *   1. Send: waiver or aftercare via email, with QR code option
 *   2. Documents: sent/uploaded doc tracking with status
 *   3. Upload: drag-drop or button upload with required naming
 *   4. Artist Scroll: link to compliance docs
 *   5. Station Upkeep: daily/weekly/monthly tasks (XP/BTTcoin rewards)
 *
 * Waiver review is handled by the active document workflow.
 * Response templates moved to Settings (future).
 */

import { useEffect, useState, useCallback, useMemo, lazy, Suspense } from 'react'
import { QRCodeSVG } from 'qrcode.react'

const ArtistScroll = lazy(() => import('../../components/ArtistScroll'))
import {
  documents,
  type DocumentTemplate,
  type DocumentInstance,
} from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { useToast } from '../../components/Toast'
// CustomSelect removed - send/upload now inline
import {
  CATEGORY_ICONS,
  DEFAULT_CATEGORY_ICON,
  STATUS_COLORS,
  formatDate,
  formatDateTime,
  timeAgo,
} from '../../lib/form-utils'

/* ══════════════════════════════════════════════════
   Station Upkeep — Quest-based maintenance system
   3 quests (daily/weekly/monthly), each with sub-steps.
   XP awarded on full quest completion only.
   ══════════════════════════════════════════════════ */

interface QuestStep {
  id: string
  title: string
  desc: string
}

interface Quest {
  id: 'daily' | 'weekly' | 'monthly'
  name: string
  xp: number
  color: string
  steps: QuestStep[]
  period: () => string
}

const QUESTS: Quest[] = [
  {
    id: 'daily',
    name: 'Daily',
    xp: 30,
    color: 'var(--gold)',
    period: () => new Date().toISOString().slice(0, 10),
    steps: [
      { id: 'wipe', title: 'Sanitize Station', desc: 'Spray all surfaces and equipment with Madacide. Let sit 2 min. Wipe with clean towel. Air dry.' },
      { id: 'sharps', title: 'Sharps Check', desc: 'Verify sharps container is below the 3/4 fill line. Replace if full.' },
      { id: 'restock', title: 'Restock Supplies', desc: 'Gloves, ink cups, clip cord covers, barriers, paper towels at station.' },
      { id: 'trash', title: 'Empty Trash', desc: 'Station trash and biohazard bag. Replace liners.' },
      { id: 'bathroom', title: 'Bathroom Check', desc: 'Wipe sink and mirror. Restock soap, paper towels, hand sanitizer.' },
      { id: 'lounge', title: 'Lounge Tidy', desc: 'Clear surfaces, wipe table, take out any trash or dishes.' },
    ],
  },
  {
    id: 'weekly',
    name: 'Weekly',
    xp: 45,
    color: '#4a9c8f',
    period: () => {
      const d = new Date()
      const start = new Date(d.getFullYear(), 0, 1)
      const week = Math.ceil(((d.getTime() - start.getTime()) / 86400000 + start.getDay() + 1) / 7)
      return `${d.getFullYear()}-W${week}`
    },
    steps: [
      { id: 'deep-station', title: 'Deep Clean Station', desc: 'All surfaces, chair, lamp, arm rest, equipment trays. Disinfect everything.' },
      { id: 'drawing', title: 'Drawing Room', desc: 'Wipe desks, clean light tables, organize reference materials, restock paper.' },
      { id: 'bathroom', title: 'Bathroom Deep Clean', desc: 'Scrub floor, toilet, sink fixtures. Mop. Restock all supplies.' },
      { id: 'inventory', title: 'Supply Inventory', desc: 'Check stock levels for needles, ink, gloves, barriers, cleaning supplies. Flag anything low.' },
    ],
  },
  {
    id: 'monthly',
    name: 'Monthly',
    xp: 100,
    color: '#c46848',
    period: () => new Date().toISOString().slice(0, 7),
    steps: [
      { id: 'crew', title: 'Crew Cleaning Day', desc: 'Coordinate with team. Schedule a date. Everyone cleans their zone plus shared spaces.' },
      { id: 'biohazard', title: 'Biohazard Pickup', desc: 'Schedule waste pickup with disposal service. Verify manifests are signed.' },
      { id: 'safety', title: 'Safety Equipment', desc: 'Inspect fire extinguisher (charged, accessible). Check first aid kit. Verify exit signs work.' },
      { id: 'audit', title: 'DHEC Self-Audit', desc: 'Walk through SC DHEC 61-42 checklist. Note any issues. Fix before next inspection.' },
    ],
  },
]

function UpkeepSection() {
  const { user } = useAuthStore()
  const userId = user?.id || 'unknown'

  const [completed, setCompleted] = useState<Set<string>>(() => {
    const loaded = new Set<string>()
    try {
      for (const q of QUESTS) {
        const key = `bttco_quest_${userId}_${q.id}_${q.period()}`
        const stored = localStorage.getItem(key)
        if (stored) JSON.parse(stored).forEach((id: string) => loaded.add(`${q.id}:${id}`))
      }
    } catch { /* ignore */ }
    return loaded
  })

  const [activeQuest, setActiveQuest] = useState<'daily' | 'weekly' | 'monthly'>('daily')

  const toggle = useCallback((quest: Quest, stepId: string) => {
    const compositeKey = `${quest.id}:${stepId}`
    setCompleted((prev) => {
      const next = new Set(prev)
      if (next.has(compositeKey)) next.delete(compositeKey)
      else next.add(compositeKey)
      const done = quest.steps.filter((s) => next.has(`${quest.id}:${s.id}`)).map((s) => s.id)
      localStorage.setItem(`bttco_quest_${userId}_${quest.id}_${quest.period()}`, JSON.stringify(done))
      return next
    })
  }, [userId])

  const quest = QUESTS.find((q) => q.id === activeQuest)!
  const doneCount = quest.steps.filter((s) => completed.has(`${quest.id}:${s.id}`)).length
  const isComplete = doneCount === quest.steps.length

  return (
    <div className="codex-section">
      <div className="codex-section-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
        <span>Station Upkeep</span>
      </div>

      {/* Quest tabs */}
      <div className="codex-upkeep-filters">
        {QUESTS.map((q) => {
          const qDone = q.steps.filter((s) => completed.has(`${q.id}:${s.id}`)).length
          const qComplete = qDone === q.steps.length
          return (
            <button
              key={q.id}
              className={`codex-upkeep-pill${activeQuest === q.id ? ' active' : ''}${qComplete ? ' complete' : ''}`}
              onClick={() => setActiveQuest(q.id)}
              style={activeQuest === q.id ? { borderColor: q.color } : undefined}
            >
              {q.name}
              <span className="codex-upkeep-pill-count">{qDone}/{q.steps.length}</span>
            </button>
          )
        })}
      </div>

      {/* Active quest card */}
      <div className="codex-quest-card" style={{ borderColor: isComplete ? quest.color : 'var(--border-subtle)' }}>
        <div className="codex-quest-header">
          <div className="codex-quest-title-row">
            <span className="codex-quest-name">{quest.name}</span>
            <span className="codex-quest-xp" style={{ color: quest.color }}>+{quest.xp} XP</span>
          </div>
          <div className="codex-quest-progress">
            <div className="codex-quest-bar">
              <div className="codex-quest-fill" style={{ width: `${(doneCount / quest.steps.length) * 100}%`, background: quest.color }} />
            </div>
            <span className="codex-quest-count">{doneCount}/{quest.steps.length}</span>
          </div>
          {isComplete && <span className="codex-quest-complete">Quest Complete</span>}
        </div>

        <div className="codex-quest-steps">
          {quest.steps.map((step) => {
            const done = completed.has(`${quest.id}:${step.id}`)
            return (
              <label key={step.id} className={`codex-quest-step${done ? ' done' : ''}`}>
                <input type="checkbox" checked={done} onChange={() => toggle(quest, step.id)} />
                <div className="codex-quest-step-content">
                  <span className="codex-quest-step-title">{step.title}</span>
                  <span className="codex-quest-step-desc">{step.desc}</span>
                </div>
              </label>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default function DocumentHub() {
  const user = useAuthStore((s) => s.user)
  const effectiveRole = useAuthStore((s) => s.effectiveRole)
  const toast = useToast()
  const isAdmin = ['assistant', 'admin', 'founder'].includes(effectiveRole)

  // ── State ──────────────────────────────────
  const [loading, setLoading] = useState(true)
  const [category] = useState('')

  // Data
  const [templates, setTemplates] = useState<DocumentTemplate[]>([])
  const [trackingDocs, setTrackingDocs] = useState<DocumentInstance[]>([])

  // Upload state
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploadCategory, setUploadCategory] = useState('upload')
  const [uploading, setUploading] = useState(false)

  // Send state
  const [sendTemplateId, setSendTemplateId] = useState('')
  const [sendRecipient, setSendRecipient] = useState('')
  const [sendEmail, setSendEmail] = useState('')
  const [sendReminderH, setSendReminderH] = useState(6)
  const [sendExpiresH, setSendExpiresH] = useState(72)
  const [sending, setSending] = useState(false)
  const [sentSuccess, setSentSuccess] = useState(false)

  // Artist Scroll inline panel
  const [activeCard, setActiveCard] = useState<'scroll' | 'send' | 'upload' | 'upkeep' | null>(null)
  const toggleCard = (card: typeof activeCard) => setActiveCard(prev => prev === card ? null : card)

  // Document detail modal
  const [docDetail, setDocDetail] = useState<DocumentInstance | null>(null)
  const [resentIds, setResentIds] = useState<Set<string>>(new Set())
  const [qrToken, setQrToken] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  // ── Data fetching ────────────────────────────
  const fetchDocs = useCallback(async () => {
    setLoading(true)
    try {
      const params = category ? { category } : undefined
      const [tplRes, docRes] = await Promise.all([
        documents.listTemplates(params),
        documents.listDocuments(params),
      ])
      setTemplates(tplRes.templates)
      setTrackingDocs(docRes.documents)
    } catch (err) {
      console.error('Codex fetch failed:', err)
    } finally {
      setLoading(false)
    }
  }, [category])

  useEffect(() => { fetchDocs() }, [fetchDocs])

  // ── Client-side search filtering ───────────
  const filteredDocs = useMemo(() => {
    return trackingDocs.filter((d) => d.category !== 'waiver')
  }, [trackingDocs])

  // Upload logic now inline in the card panel

  // ── Send logic ─────────────────────────────
  function openSend(templateId?: string) {
    setSendTemplateId(templateId || '')
    setSendRecipient('')
    setSendEmail('')
    setSendReminderH(6)
    setSendExpiresH(72)
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault()
    if (!sendTemplateId || !sendEmail || !sendRecipient) return
    setSending(true)
    try {
      await documents.send({
        template_id: sendTemplateId,
        recipient_email: sendEmail,
        recipient_name: sendRecipient,
        reminder_hours: sendReminderH,
        expires_hours: sendExpiresH,
      })
      setActiveCard(null)
      setSentSuccess(true)
      setTimeout(() => {
        setSentSuccess(false)
        fetchDocs()
      }, 2200)
    } catch (err) {
      console.error('Send failed:', err)
      toast.error('Failed to send document. Please try again.')
    } finally {
      setSending(false)
    }
  }

  // ── Document actions ───────────────────────
  async function handleSign(docId: string) {
    try {
      await documents.sign(docId)
      fetchDocs()
    } catch (err) { console.error('Sign failed:', err) }
  }

  async function handleApprove(docId: string) {
    try {
      await documents.updateStatus(docId, 'approved')
      fetchDocs()
    } catch (err) { console.error('Approve failed:', err) }
  }

  async function handleRemind(docId: string) {
    try {
      await documents.remind(docId)
      setResentIds((prev) => new Set(prev).add(docId))
      setTimeout(() => setResentIds((prev) => {
        const next = new Set(prev); next.delete(docId); return next
      }), 3000)
    } catch (err) { console.error('Remind failed:', err) }
  }

  async function handleRemove(docId: string) {
    try {
      await documents.updateStatus(docId, 'archived')
      setTrackingDocs((prev) => prev.filter((d) => d.id !== docId))
      if (docDetail?.id === docId) setDocDetail(null)
      toast.success('Archived to Vault')
    } catch (err) { console.error('Archive failed:', err) }
  }

  // ── Render ─────────────────────────────────
  return (
    <div className="dashboard-panel codex-panel">
      {/* Header */}
      <header className="doc-hub-header">
        <div className="doc-tabs">
          <span className="codex-title">Codex</span>
        </div>
      </header>

      {/* ── Quick Actions Row (4 cards) ── */}
      <div className="codex-actions-row">
        <div className={`codex-action-card${activeCard === 'scroll' ? ' active' : ''}`} onClick={() => toggleCard('scroll')}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 19.5A2.5 2.5 0 016.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
          </svg>
          <span>Artist Scroll</span>
          <span className="codex-action-desc">{activeCard === 'scroll' ? 'Tap to collapse' : 'Compliance docs'}</span>
        </div>
        <div className={`codex-action-card${activeCard === 'send' ? ' active' : ''}`} onClick={() => { toggleCard('send'); if (activeCard !== 'send') openSend() }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
          </svg>
          <span>Send Waiver</span>
          <span className="codex-action-desc">{activeCard === 'send' ? 'Tap to collapse' : 'Email a consent form'}</span>
        </div>
        <div className={`codex-action-card${activeCard === 'upload' ? ' active' : ''}`} onClick={() => toggleCard('upload')}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          <span>Upload Document</span>
          <span className="codex-action-desc">{activeCard === 'upload' ? 'Tap to collapse' : 'Upload a file'}</span>
        </div>
        <div className={`codex-action-card${activeCard === 'upkeep' ? ' active' : ''}`} onClick={() => toggleCard('upkeep')}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" /><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
          </svg>
          <span>Station Upkeep</span>
          <span className="codex-action-desc">{activeCard === 'upkeep' ? 'Tap to collapse' : 'Daily tasks & quests'}</span>
        </div>
      </div>

      {/* ── Expanded Panel (only one at a time) ── */}
      {activeCard === 'scroll' && (
        <div className="codex-scroll-panel">
          <Suspense fallback={<div style={{ padding: '2rem', color: 'var(--text-muted)', fontSize: '0.8rem' }}>Loading scroll...</div>}>
            <ArtistScroll artistName={user?.name || 'Artist'} />
          </Suspense>
        </div>
      )}

      {activeCard === 'send' && (
        <div className="codex-expanded-panel">
          <form className="codex-inline-form" onSubmit={handleSend}>
            <div className="doc-form-field">
              <label className="doc-field-label">Asset</label>
              <select className="form-input doc-input" value={sendTemplateId} onChange={(e) => setSendTemplateId(e.target.value)} required>
                <option value="">Select an asset...</option>
                {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
            <div className="doc-form-field">
              <label className="doc-field-label">Recipient Name</label>
              <input className="form-input doc-input" value={sendRecipient} onChange={(e) => setSendRecipient(e.target.value)} placeholder="Client name" required />
            </div>
            <div className="doc-form-field">
              <label className="doc-field-label">Email</label>
              <input className="form-input doc-input" type="email" value={sendEmail} onChange={(e) => setSendEmail(e.target.value)} placeholder="client@email.com" required />
            </div>
            <div className="codex-form-actions">
              <button type="submit" className="cs-review-btn" disabled={sending}>{sending ? 'Sending...' : 'Send'}</button>
              <button type="button" className="action-btn" onClick={() => setActiveCard(null)}>Cancel</button>
            </div>
          </form>
          <div className="codex-poster-links">
            <span className="codex-poster-links-label">Walk-in posters</span>
            <a href="/assets/print/waiver-qr-counter-4x6.pdf" target="_blank" rel="noopener noreferrer">Counter card (4×6)</a>
            <span aria-hidden="true">·</span>
            <a href="/assets/print/waiver-qr-poster-8.5x11.pdf" target="_blank" rel="noopener noreferrer">Full poster (8.5×11)</a>
          </div>
        </div>
      )}

      {activeCard === 'upload' && (
        <div className="codex-expanded-panel">
          <form className="codex-inline-form" onSubmit={async (e) => {
            e.preventDefault()
            if (!uploadFile || !uploadTitle) return
            setUploading(true)
            try {
              await documents.upload(uploadFile, uploadCategory, uploadTitle)
              setUploadFile(null)
              setUploadTitle('')
              setUploadCategory('upload')
              setActiveCard(null)
              fetchDocs()
              toast.success('Document uploaded')
            } catch { toast.error('Upload failed') }
            finally { setUploading(false) }
          }}>
            <div className="doc-form-field">
              <label className="doc-field-label">Document Name</label>
              <input className="form-input doc-input" value={uploadTitle} onChange={(e) => setUploadTitle(e.target.value)} placeholder="Name this document" required />
            </div>
            <div className="doc-form-field">
              <label className="doc-field-label">File</label>
              <input className="form-input doc-input" type="file" onChange={(e) => setUploadFile(e.target.files?.[0] || null)} required />
            </div>
            <div className="codex-form-actions">
              <button type="submit" className="cs-review-btn" disabled={uploading}>{uploading ? 'Uploading...' : 'Upload'}</button>
              <button type="button" className="action-btn" onClick={() => setActiveCard(null)}>Cancel</button>
            </div>
          </form>
        </div>
      )}

      {activeCard === 'upkeep' && (
        <div className="codex-expanded-panel">
          <UpkeepSection />
        </div>
      )}

      {/* ── Below cards: hidden when any card is expanded ── */}
      {!activeCard && (
      <>

      {/* ── Document Tracking List ── */}
      {loading ? (
        <div className="messages-empty"><p>Loading...</p></div>
      ) : (
        <>
          {filteredDocs.length > 0 && (
            <div className="doc-instances-list">
              {filteredDocs.map((d) => (
                <div key={d.id} className="doc-instance-row" onClick={() => setDocDetail(d)} style={{ cursor: 'pointer' }}>
                  <span className="doc-instance-icon">
                    {CATEGORY_ICONS[d.category] || DEFAULT_CATEGORY_ICON}
                  </span>
                  <div className="doc-instance-info">
                    <span className="doc-instance-title">
                      {d.recipient_name ? `${d.recipient_name} \u2014 ${d.title}` : d.title}
                      <span className={`booking-status ${STATUS_COLORS[d.status] || ''}`}>
                        {d.status}
                      </span>
                    </span>
                    <span className="doc-instance-meta">
                      {d.template_name && <span>{d.template_name} · </span>}
                      {formatDate(d.created_at)}
                      {d.recipient_email && <span> · {d.recipient_email}</span>}
                      {d.sent_at && <span> · Sent {timeAgo(d.sent_at)}</span>}
                      {d.viewed_at && <span> · Viewed {timeAgo(d.viewed_at)}</span>}
                      {d.signed_at && <span> · Signed {formatDate(d.signed_at)}</span>}
                    </span>
                  </div>
                  <div className="doc-instance-actions" onClick={(e) => e.stopPropagation()}>
                    {d.status === 'submitted' && (
                      <button className="action-btn" onClick={() => handleSign(d.id)}>
                        Sign
                      </button>
                    )}
                    {d.status === 'signed' && isAdmin && (
                      <button className="action-btn" onClick={() => handleApprove(d.id)}>
                        Approve
                      </button>
                    )}
                    {d.status === 'sent' && (
                      resentIds.has(d.id) ? (
                        <span className="action-btn action-btn-confirmed">Sent!</span>
                      ) : (
                        <button className="action-btn" onClick={() => handleRemind(d.id)}>
                          Resend
                        </button>
                      )
                    )}
                    {d.file_url && (
                      <a className="action-btn" href={d.file_url} target="_blank" rel="noopener noreferrer">
                        View
                      </a>
                    )}
                    {d.send_token && (
                      <>
                        <button className="action-btn" title="Show QR code" onClick={() => setQrToken(d.send_token)}>
                          QR
                        </button>
                        <button
                          className="action-btn"
                          title="Copy public link"
                          onClick={() => {
                            navigator.clipboard.writeText(`${window.location.origin}/form/${d.send_token}`)
                            setCopiedId(d.id)
                            setTimeout(() => setCopiedId(null), 2000)
                          }}
                        >
                          {copiedId === d.id ? 'Copied!' : 'Link'}
                        </button>
                      </>
                    )}
                    <button className="action-btn action-btn-danger" onClick={() => handleRemove(d.id)}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="21 8 21 21 3 21 3 8" /><rect x="1" y="3" width="22" height="5" /><line x1="10" y1="12" x2="14" y2="12" />
                      </svg>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

        </>
      )}
      </>
      )}

      {/* Upload modal removed - now inline in Codex card */}

      {/* ═══ SENT SUCCESS ANIMATION ═══ */}
      {sentSuccess && (
        <div className="sent-success-overlay">
          <div className="sent-success-card">
            <div className="sent-success-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
              </svg>
            </div>
            <h3 className="sent-success-title">Document Sent</h3>
            <p className="sent-success-desc">
              {sendRecipient} will receive it at {sendEmail}
            </p>
          </div>
        </div>
      )}

      {/* Send modal removed - now inline in Codex card */}

      {/* ═══ DOCUMENT DETAIL MODAL ═══ */}
      {docDetail && (
        <div className="doc-modal-overlay" onClick={() => setDocDetail(null)}>
          <div className="doc-modal" style={{ width: 520 }} onClick={(e) => e.stopPropagation()}>
            <div className="doc-modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span className="doc-template-icon" style={{ width: 36, height: 36, minWidth: 36 }}>
                  {CATEGORY_ICONS[docDetail.category] || DEFAULT_CATEGORY_ICON}
                </span>
                <div>
                  <h3 className="doc-modal-title">{docDetail.title}</h3>
                  <p className="doc-modal-desc">
                    <span className={`booking-status ${STATUS_COLORS[docDetail.status] || ''}`} style={{ fontSize: '0.7rem' }}>
                      {docDetail.status}
                    </span>
                    {docDetail.category && <span> · {docDetail.category}</span>}
                  </p>
                </div>
              </div>
              <button className="doc-modal-close" onClick={() => setDocDetail(null)}>&times;</button>
            </div>
            <div className="waiver-detail-body">
              <div className="waiver-detail-section">
                <div className="waiver-detail-section-title">Recipient</div>
                <DetailRow label="Name" value={docDetail.recipient_name || '\u2014'} />
                <DetailRow label="Email" value={docDetail.recipient_email || '\u2014'} />
              </div>
              <div className="waiver-detail-section">
                <div className="waiver-detail-section-title">Document Details</div>
                {docDetail.template_name && <DetailRow label="Asset" value={docDetail.template_name} />}
                <DetailRow label="Category" value={docDetail.category} />
                <DetailRow label="Created" value={formatDateTime(docDetail.created_at)} />
                {docDetail.sent_at && <DetailRow label="Sent" value={formatDateTime(docDetail.sent_at)} />}
                {docDetail.viewed_at && <DetailRow label="Viewed" value={formatDateTime(docDetail.viewed_at)} />}
                {docDetail.signed_at && <DetailRow label="Signed" value={formatDateTime(docDetail.signed_at)} />}
              </div>
              {docDetail.file_url && (
                <div className="waiver-detail-section">
                  <a className="action-btn" href={docDetail.file_url} target="_blank" rel="noopener noreferrer" style={{ display: 'inline-block' }}>
                    View / Download
                  </a>
                </div>
              )}
              <div className="waiver-detail-section">
                <div className="doc-detail-actions">
                  {docDetail.status === 'submitted' && (
                    <button className="action-btn" onClick={() => { handleSign(docDetail.id); setDocDetail(null) }}>
                      Sign
                    </button>
                  )}
                  {docDetail.status === 'signed' && isAdmin && (
                    <button className="action-btn" onClick={() => { handleApprove(docDetail.id); setDocDetail(null) }}>
                      Approve
                    </button>
                  )}
                  {docDetail.status === 'sent' && (
                    <button className="action-btn" onClick={() => { handleRemind(docDetail.id); setDocDetail(null) }}>
                      Send Reminder
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ QR CODE MODAL ═══ */}
      {qrToken && (
        <div className="doc-modal-overlay" onClick={() => setQrToken(null)}>
          <div className="doc-modal" style={{ width: 340, textAlign: 'center' }} onClick={(e) => e.stopPropagation()}>
            <div className="doc-modal-header">
              <h3 className="doc-modal-title">Scan to Sign</h3>
              <button className="doc-modal-close" onClick={() => setQrToken(null)}>&times;</button>
            </div>
            <div style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem' }}>
              <div style={{ background: '#fff', padding: '16px', borderRadius: '12px', display: 'inline-block' }}>
                <QRCodeSVG
                  value={`${window.location.origin}/form/${qrToken}`}
                  size={200}
                  level="M"
                  includeMargin={false}
                />
              </div>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: 0 }}>
                Client scans with their phone camera to open the waiver form
              </p>
              <button
                className="action-btn"
                style={{ fontSize: '0.75rem' }}
                onClick={() => {
                  navigator.clipboard.writeText(`${window.location.origin}/form/${qrToken}`)
                  setCopiedId('qr-modal')
                  setTimeout(() => setCopiedId(null), 2000)
                }}
              >
                {copiedId === 'qr-modal' ? 'Copied!' : 'Copy Link'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* Tiny helper for detail rows */
function DetailRow({ label, value, flag }: { label: string; value: string; flag?: boolean }) {
  return (
    <div className="waiver-detail-row">
      <span className="waiver-detail-label">{label}</span>
      <span className={`waiver-detail-value${flag ? ' waiver-health-flag' : ''}`}>
        {value || '\u2014'}
      </span>
    </div>
  )
}
