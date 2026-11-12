/**
 * ScrollsPanel
 * ────────────────────────────────────────────────────────────────
 * Subtab system for all scroll categories.
 * Built-in tabs: Artist | Shop | Misc
 * Dynamic tabs: created via the + wizard (e.g. Financial, Health)
 *
 * Every tab always ends with an "Add Scroll" tile.
 * Clicking it opens an inline Gala AI indexing panel.
 *
 * CSS: scroll-tiles.css (global)
 */

import { useState, useEffect, useRef, useCallback, lazy, Suspense } from 'react'
import { Chip } from '@ryozan/ui'
import '../styles/scroll-tiles.css'
import {
  listScrollCategories,
  createScrollCategory,
  interpretScrollCategory,
  listScrollCategoryDocs,
  responseTemplates as templatesApi,
  type ResponseTemplate,
  uploadScrollDoc,
  deleteScrollDoc,
  getUserScroll,
  requestArtistScroll,
  remindArtistScroll,
  getArtistScrollRequestStatus,
  documents as documentsApi,
  type ScrollCategory,
  type ScrollDoc,
  type ArtistScrollRequestStatus,
  type ArtistScrollData,
  type ArtistSummary,
  type DocumentTemplate,
  type DocumentInstance,
  waiver as waiverApi,
  artistSignature,
} from '../lib/api'
import { artists as artistsApi, bookings as bookingsApi, calendar as calendarApi, API_BASE } from '../lib/api'
import { ArchiveTab } from '../pages/dashboard/VaultPage'
import SignaturePad from './SignaturePad'
import { useAuthStore } from '../stores/auth'
import { useToast } from './Toast'
import { formatDate } from '../lib/form-utils'
import { resolveWaiverTemplateId } from '../lib/studio-settings'
import { useStudioSettings } from '../hooks/useStudioSettings'

const ArtistScrollForm = lazy(() => import('./ArtistScroll'))

// Fallback built-ins — shown immediately even before API responds.
// The owner archive is supplied by the API because it is founder-only.
const OWNER_ARCHIVE_SLUG = 'owner-archive'
// Client history already has one authoritative archive contract. Expose that
// existing record shelf in Codex instead of creating a second document store.
const CLIENT_RECORDS_CATEGORY: ScrollCategory = {
  id: 'virtual-client-records',
  slug: 'client-records',
  label: 'Clients',
  icon: 'archive',
  description: 'Searchable client waivers, bookings, receipts, and archived documents.',
  is_builtin: true,
  ai_digest_enabled: false,
  ai_digest_prompt: null,
  sort_order: 5,
}

const BUILTIN_CATEGORIES: ScrollCategory[] = [
  { id: 'builtin-artist',  slug: 'artist',  label: 'Artist',  icon: '📜', description: 'Artist compliance scrolls — employment agreements, BBP certs, signatures.', is_builtin: true, ai_digest_enabled: false, ai_digest_prompt: null, sort_order: 0 },
  { id: 'builtin-shop',    slug: 'shop',    label: 'Shop',    icon: '🏪', description: 'Shop operations — licenses, vendor agreements, inspection records.', is_builtin: true, ai_digest_enabled: false, ai_digest_prompt: null, sort_order: 1 },
  { id: 'builtin-templates', slug: 'templates', label: 'Templates', icon: 'message-square', description: 'Reusable auto-message response templates for staff quick-replies.', is_builtin: true, ai_digest_enabled: false, ai_digest_prompt: null, sort_order: 2 },
  { id: 'builtin-waivers', slug: 'waivers', label: 'Waivers', icon: '📋', description: 'Send consent forms and track waiver status.', is_builtin: true, ai_digest_enabled: false, ai_digest_prompt: null, sort_order: 3 },
  { id: 'builtin-upkeep',  slug: 'upkeep',  label: 'Upkeep',  icon: '🔧', description: 'Daily, weekly, and monthly station maintenance quests.', is_builtin: true, ai_digest_enabled: false, ai_digest_prompt: null, sort_order: 4 },
  { id: 'builtin-quests',  slug: 'quests',  label: 'Quest Log', icon: 'quest', description: 'Active, completed, paused, and archived quests for everyone.', is_builtin: true, ai_digest_enabled: false, ai_digest_prompt: null, sort_order: 5 },
]

// ── AvatarOrInitial ───────────────────────────────────────────────
function AvatarOrInitial({ url, initial, size = 32 }: { url: string | null; initial: string; size?: number }) {
  const [broken, setBroken] = useState(false)
  const [lastUrl, setLastUrl] = useState(url)
  if (url !== lastUrl) { setLastUrl(url); setBroken(false) }
  if (!url || broken) {
    return (
      <span style={{
        fontFamily: "'KC Tallboy', 'Oswald', 'Arial Narrow', sans-serif",
        fontSize: size * 0.55,
        fontWeight: 700,
        lineHeight: 1,
        letterSpacing: '0.02em',
      }}>
        {initial}
      </span>
    )
  }
  return (
    <img
      src={url} alt=""
      style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block', fontSize: 0, color: 'transparent' }}
      onError={() => setBroken(true)}
    />
  )
}

// ── Icons ─────────────────────────────────────────────────────────
function FileIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  )
}

function UploadIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  )
}

// Subtab SVG icons keyed by category slug
function ScrollTabIcon({ slug }: { slug: string }) {
  const s = { width: 13, height: 13, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: '1.8', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  switch (slug) {
    case 'artist':
      // person silhouette
      return (
        <svg {...s}>
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      )
    case 'shop':
      // storefront
      return (
        <svg {...s}>
          <path d="M3 9l1-5h16l1 5" />
          <path d="M3 9a2 2 0 0 0 2 2 2 2 0 0 0 2-2 2 2 0 0 0 2 2 2 2 0 0 0 2-2 2 2 0 0 0 2 2 2 2 0 0 0 2-2" />
          <path d="M5 20V11m14 9V11" />
          <rect x="9" y="14" width="6" height="6" rx="1" />
        </svg>
      )
    case 'misc':
    case 'templates':
    case 'message-square':
      return (
        <svg {...s}>
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      )
    case 'financial':
      // dollar sign in circle
      return (
        <svg {...s}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v10M9.5 9.5a2.5 2.5 0 0 1 5 0c0 1.5-1 2-2.5 2.5S9.5 13 9.5 14.5a2.5 2.5 0 0 0 5 0" />
        </svg>
      )
    case 'health':
      // heart with cross
      return (
        <svg {...s}>
          <path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" />
          <path d="M12 5v6M9 8h6" />
        </svg>
      )
    case 'legal':
      // scales / balance
      return (
        <svg {...s}>
          <path d="M12 3v18" />
          <path d="M5 21h14" />
          <path d="m5 8 7-5 7 5" />
          <path d="M5 8c0 2.2-1.7 4-4 4s-4-1.8-4-4l4-7" />
          <path d="M19 8c0 2.2 1.7 4 4 4s4-1.8 4-4l-4-7" />
        </svg>
      )
    case 'waivers':
      // send / paper plane
      return (
        <svg {...s}>
          <path d="M22 2 11 13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
      )
    case 'upkeep':
      return (
        <svg {...s}>
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
        </svg>
      )
    case 'quest':
    case 'quests':
      return (
        <svg {...s}>
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
        </svg>
      )
    case 'client-records':
      return (
        <svg {...s}>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M8 2v4M16 2v4M3 9h18" />
          <circle cx="9" cy="14" r="2" />
          <path d="M13 13h5M13 16h4" />
        </svg>
      )
    default:
      // generic document
      return <FileIcon size={13} />
  }
}

// ── Artist scroll detail view ─────────────────────────────────────
function ArtistScrollDetail({ scrollData, onBack }: { scrollData: ArtistScrollData; onBack: () => void }) {
  return (
    <div>
      <button onClick={onBack} className="scroll-doc-row-action" style={{ marginBottom: '1rem' }}>
        ← Back to Artists
      </button>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
        {/* Status bar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', padding: '0.75rem 1rem', background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: '8px' }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', background: scrollData.status === 'signed' ? '#6dbf7e' : '#e05c5c', flexShrink: 0 }} />
          <div>
            <div style={{ fontSize: '0.82rem', color: 'var(--text)', fontWeight: 600 }}>
              {scrollData.user_name ?? 'Artist'} — Artist Scroll
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--muted)', marginTop: 2 }}>
              {scrollData.status === 'signed'
                ? `Signed ${scrollData.signed_at ? new Date(scrollData.signed_at).toLocaleDateString() : ''}`
                : scrollData.status}
            </div>
          </div>
          <div style={{ marginLeft: 'auto', fontSize: '0.68rem', color: 'var(--muted)', fontFamily: 'monospace' }}>
            #{scrollData.id.slice(0, 8).toUpperCase()}
          </div>
        </div>
        {/* Artist signature. Founder review is recorded separately from artist submission. */}
        <div style={{ padding: '0.85rem 1rem', background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: '8px' }}>
          <div style={{ fontSize: '0.68rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.5rem' }}>Artist Signature</div>
          {scrollData.artist_signature
            ? <img src={scrollData.artist_signature} alt="Artist Signature" style={{ maxWidth: '100%', maxHeight: 100, objectFit: 'contain', borderRadius: 4 }} />
            : <span style={{ fontSize: '0.75rem', color: '#e05c5c' }}>Not captured</span>}
        </div>
        {/* BBP */}
        <div style={{ padding: '0.85rem 1rem', background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: '8px' }}>
          <div style={{ fontSize: '0.68rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.5rem' }}>BBP Training Certificate</div>
          {scrollData.bbp_cert_url
            ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <img src={scrollData.bbp_cert_url} alt="BBP Cert" style={{ maxWidth: 220, maxHeight: 160, objectFit: 'contain', borderRadius: 6, border: '1px solid var(--panel-border)' }} />
                <a href={scrollData.bbp_cert_url} target="_blank" rel="noopener noreferrer"
                  style={{ fontSize: '0.75rem', color: 'var(--gold-vibrant)', textDecoration: 'none', padding: '0.35rem 0.75rem', border: '1px solid rgba(166,125,67,0.3)', borderRadius: 5 }}>
                  Open Full Size
                </a>
              </div>
            )
            : <span style={{ fontSize: '0.75rem', color: '#e05c5c' }}>No certificate uploaded</span>}
        </div>
        {/* Fields */}
        {scrollData.fields && Object.keys(scrollData.fields).filter(k => !['bbp_cert_url', 'owner_sig_url'].includes(k)).length > 0 && (
          <div style={{ padding: '0.85rem 1rem', background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: '8px' }}>
            <div style={{ fontSize: '0.68rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.65rem' }}>Application Fields</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem 1.5rem' }}>
              {Object.entries(scrollData.fields)
                .filter(([k]) => !['bbp_cert_url', 'owner_sig_url'].includes(k))
                .map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ fontSize: '0.62rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{k.replace(/_/g, ' ')}</span>
                    <span style={{ fontSize: '0.76rem', color: 'var(--text)' }}>{String(v) || '—'}</span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Upkeep Section (lifted from DocumentHub) ─────────────────────

interface QuestStep { id: string; title: string; desc: string }
interface Quest { id: 'daily' | 'weekly' | 'monthly'; name: string; xp: number; color: string; steps: QuestStep[]; period: () => string }

const UPKEEP_QUESTS: Quest[] = [
  {
    id: 'daily', name: 'Daily', xp: 30, color: 'var(--gold-vibrant)',
    period: () => new Date().toISOString().slice(0, 10),
    steps: [
      { id: 'wipe',     title: 'Sanitize Station',  desc: 'Spray all surfaces with Madacide. Let sit 2 min. Wipe with clean towel. Air dry.' },
      { id: 'sharps',   title: 'Sharps Check',       desc: 'Verify sharps container is below the 3/4 fill line. Replace if full.' },
      { id: 'restock',  title: 'Restock Supplies',   desc: 'Gloves, ink cups, clip cord covers, barriers, paper towels at station.' },
      { id: 'trash',    title: 'Empty Trash',         desc: 'Station trash and biohazard bag. Replace liners.' },
      { id: 'bathroom', title: 'Bathroom Check',      desc: 'Wipe sink and mirror. Restock soap, paper towels, hand sanitizer.' },
      { id: 'lounge',   title: 'Lounge Tidy',         desc: 'Clear surfaces, wipe table, take out any trash or dishes.' },
    ],
  },
  {
    id: 'weekly', name: 'Weekly', xp: 45, color: '#4a9c8f',
    period: () => { const d = new Date(); const s = new Date(d.getFullYear(), 0, 1); const w = Math.ceil(((d.getTime() - s.getTime()) / 86400000 + s.getDay() + 1) / 7); return `${d.getFullYear()}-W${w}` },
    steps: [
      { id: 'deep-station', title: 'Deep Clean Station',  desc: 'All surfaces, chair, lamp, arm rest, equipment trays.' },
      { id: 'drawing',      title: 'Drawing Room',         desc: 'Wipe desks, clean light tables, organize reference materials.' },
      { id: 'bathroom',     title: 'Bathroom Deep Clean',  desc: 'Scrub floor, toilet, sink fixtures. Mop. Restock all supplies.' },
      { id: 'inventory',    title: 'Supply Inventory',     desc: 'Check stock levels for needles, ink, gloves, barriers. Flag anything low.' },
    ],
  },
  {
    id: 'monthly', name: 'Monthly', xp: 100, color: '#c46848',
    period: () => new Date().toISOString().slice(0, 7),
    steps: [
      { id: 'crew',      title: 'Crew Cleaning Day',   desc: 'Coordinate with team. Schedule a date. Everyone cleans their zone.' },
      { id: 'biohazard', title: 'Biohazard Pickup',    desc: 'Schedule waste pickup. Verify manifests are signed.' },
      { id: 'safety',    title: 'Safety Equipment',    desc: 'Inspect fire extinguisher. Check first aid kit. Verify exit signs work.' },
      { id: 'audit',     title: 'DHEC Self-Audit',     desc: 'Walk through SC DHEC 61-42 checklist. Note any issues.' },
    ],
  },
]

function UpkeepSection() {
  const { user } = useAuthStore()
  const userId = user?.id || 'unknown'
  const [activeQuest, setActiveQuest] = useState<'daily' | 'weekly' | 'monthly'>('daily')
  const [completed, setCompleted] = useState<Set<string>>(() => {
    const loaded = new Set<string>()
    try {
      for (const q of UPKEEP_QUESTS) {
        const key = `ryozan_quest_${userId}_${q.id}_${q.period()}`
        const stored = localStorage.getItem(key)
        if (stored) JSON.parse(stored).forEach((id: string) => loaded.add(`${q.id}:${id}`))
      }
    } catch { /* ignore */ }
    return loaded
  })

  const toggle = useCallback((quest: Quest, stepId: string) => {
    const key = `${quest.id}:${stepId}`
    setCompleted(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      const done = quest.steps.filter(s => next.has(`${quest.id}:${s.id}`)).map(s => s.id)
      localStorage.setItem(`ryozan_quest_${userId}_${quest.id}_${quest.period()}`, JSON.stringify(done))
      return next
    })
  }, [userId])

  const quest = UPKEEP_QUESTS.find(q => q.id === activeQuest)!
  const doneCount = quest.steps.filter(s => completed.has(`${quest.id}:${s.id}`)).length
  const isComplete = doneCount === quest.steps.length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="upkeep-tabs">
        {UPKEEP_QUESTS.map(q => {
          const qDone = q.steps.filter(s => completed.has(`${q.id}:${s.id}`)).length
          const qComplete = qDone === q.steps.length
          return (
            <button
              key={q.id}
              className={`upkeep-tab${activeQuest === q.id ? ' active' : ''}${qComplete ? ' complete' : ''}`}
              style={activeQuest === q.id ? { borderColor: q.color, color: q.color } : qComplete ? { color: q.color, opacity: 1 } : {}}
              onClick={() => setActiveQuest(q.id)}
            >
              {q.name} · {qDone}/{q.steps.length}
            </button>
          )
        })}
      </div>

      <div className={`upkeep-card${isComplete ? ' complete' : ''}`} style={isComplete ? { borderColor: quest.color } : {}}>
        <div className="upkeep-card-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span className="upkeep-card-title">{quest.name} Tasks</span>
            <span className="upkeep-card-xp" style={{ color: quest.color }}>+{quest.xp} XP</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <div className="upkeep-progress-bar">
              <div className="upkeep-progress-fill" style={{ width: `${(doneCount / quest.steps.length) * 100}%`, background: quest.color }} />
            </div>
            <span className="upkeep-progress-count">{doneCount}/{quest.steps.length}</span>
            {isComplete && <span className="upkeep-card-done-label" style={{ color: quest.color }}>Done ✓</span>}
          </div>
        </div>

        <div className="upkeep-steps">
          {quest.steps.map(step => {
            const done = completed.has(`${quest.id}:${step.id}`)
            return (
              <label key={step.id} className={`upkeep-step${done ? ' done' : ''}`} onClick={() => toggle(quest, step.id)}>
                <div className={`upkeep-step-check${done ? ' checked' : ''}`} style={done ? { background: quest.color, borderColor: quest.color } : {}}>
                  <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="#0e0d0b" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12"/>
                  </svg>
                </div>
                <div className="upkeep-step-body">
                  <span className="upkeep-step-title">{step.title}</span>
                  <span className="upkeep-step-desc">{step.desc}</span>
                </div>
              </label>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ── Waivers Section (lifted from DocumentHub) ─────────────────────

/* ── Waiver Sign-Off Modal ── */
function WaiverSignModal({ d, onClose, onDone }: {
  d: DocumentInstance
  onClose: () => void
  onDone: () => void
}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const data: Record<string, any> = (() => {
    try { return JSON.parse(d.data_json || '{}') } catch { return {} }
  })()

  const alreadySigned = !!data.countersigned_by
  const [priceValue, setPriceValue] = useState(data.quoted_price || '')
  const [condition, setCondition] = useState('Good')
  const [artistSig, setArtistSig] = useState('')
  const [savedSigUrl, setSavedSigUrl] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [printing, setPrinting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const now = new Date()
  const dateTimeStr = now.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
    ' at ' + now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

  // Hide hotbar while modal is open
  useEffect(() => {
    const hotbar = document.querySelector('.hotbar') as HTMLElement | null
    if (hotbar) hotbar.style.display = 'none'
    return () => { if (hotbar) hotbar.style.display = '' }
  }, [])

  // Load saved signature
  useEffect(() => {
    if (!alreadySigned) {
      artistSignature.get().then(res => {
        if (res.signature_url) {
          setSavedSigUrl(res.signature_url)
          setArtistSig(res.signature_url)
        }
      }).catch(() => {})
    }
  }, [alreadySigned])

  const handleSubmit = async () => {
    if (!artistSig || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      if (artistSig.startsWith('data:')) {
        artistSignature.save(artistSig).catch(() => {})
      }
      await waiverApi.countersign(d.id, {
        signature_data: artistSig,
        client_condition_notes: condition.toLowerCase(),
        notes: `Condition: ${condition}. Price: $${priceValue || 'N/A'}`,
      })
      const pdfRes = await waiverApi.exportPdf(d.id)
      setSuccess(true)
      if (pdfRes.pdf_url) window.open(pdfRes.pdf_url, '_blank')
      setTimeout(() => { onDone() }, 1500)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-off failed')
    } finally {
      setSubmitting(false)
    }
  }

  const handlePrint = async () => {
    if (printing) return
    setPrinting(true)
    setError(null)
    try {
      const result = await waiverApi.exportPdf(d.id)
      if (!result.pdf_url) throw new Error('Printable waiver is unavailable')
      window.open(result.pdf_url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not prepare printable waiver')
    } finally {
      setPrinting(false)
    }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.88)', backdropFilter: 'blur(6px)' }} onClick={onClose}>
      <div style={{ width: 440, maxHeight: '85vh', overflowY: 'auto', background: 'var(--bg-surface)', border: '1px solid var(--panel-border)', borderRadius: 12, padding: 0 }} onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.85rem 1.2rem', borderBottom: '1px solid var(--panel-border)' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: '0.9rem', fontWeight: 700, color: 'var(--text)' }}>
              {data.full_name || d.recipient_name || 'Client'}
            </h3>
            {data.reference_code && (
              <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>{data.reference_code}</span>
            )}
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '1.2rem', cursor: 'pointer', padding: '0.25rem' }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        {success ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.6rem', padding: '2.5rem 1.5rem' }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#6dbf7e" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" opacity="0.3"/><polyline points="9 12 11.5 14.5 16 9.5"/>
            </svg>
            <span style={{ fontSize: '0.9rem', fontWeight: 700, color: '#6dbf7e' }}>Signed &amp; Archived</span>
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>PDF opened in new tab</span>
          </div>
        ) : (
          <div style={{ padding: '1rem 1.2rem', display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
            {/* Contact Info */}
            <div>
              <p style={{ fontSize: '0.62rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 0.4rem' }}>Contact</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.3rem 1rem' }}>
                {data.full_name && <Field label="Name" value={data.full_name} />}
                {(data.email || d.recipient_email) && <Field label="Email" value={data.email || d.recipient_email} />}
                {data.phone && <Field label="Phone" value={data.phone} />}
                {data.emergency_contact && <Field label="Emergency" value={`${data.emergency_contact} ${data.emergency_phone || ''}`} />}
                {data.emergency_contact_address && <Field label="Emergency Address" value={data.emergency_contact_address} />}
              </div>
            </div>

            {/* Tattoo Details */}
            <div>
              <p style={{ fontSize: '0.62rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: '0 0 0.4rem' }}>Tattoo</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.3rem 1rem' }}>
                {data.tattoo_description && <Field label="Description" value={data.tattoo_description} />}
                {data.placement && <Field label="Placement" value={data.placement} />}
                {data.artist_name && <Field label="Artist" value={data.artist_name} />}
              </div>
            </div>

            <div style={{ height: 1, background: 'var(--panel-border)' }} />

            {alreadySigned ? (
              /* Read-only view for already signed waivers */
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <p style={{ fontSize: '0.62rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#6dbf7e', margin: 0 }}>Signed Off</p>
                <Field label="Artist" value={data.countersigned_by} />
                <Field label="Condition" value={(data.client_condition_notes || 'Good').replace('_', ' ')} />
                {data.countersigned_at && <Field label="Date" value={formatDate(data.countersigned_at)} />}
                {data.artist_countersignature && data.artist_countersignature.startsWith('http') && (
                  <img src={data.artist_countersignature} alt="Artist signature" style={{ maxWidth: 200, height: 50, objectFit: 'contain', borderRadius: 4, border: '1px solid var(--panel-border)', background: '#fff', display: 'block', fontSize: 0, color: 'transparent' }} />
                )}
                <button
                  type="button"
                  onClick={handlePrint}
                  disabled={printing}
                  className="scroll-waiver-print"
                >
                  {printing ? 'Preparing PDF...' : 'Print waiver packet'}
                </button>
                <span className="scroll-waiver-print-note">Includes completed fields, photo ID, and captured signatures.</span>
              </div>
            ) : (
              /* Sign-off form */
              <>
                {/* Quoted Price (editable) */}
                <div>
                  <label style={{ fontSize: '0.62rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', display: 'block', marginBottom: '0.3rem' }}>Quoted Price</label>
                  <input
                    value={priceValue}
                    onChange={e => setPriceValue(e.target.value)}
                    placeholder="Enter price"
                    style={{ width: '100%', padding: '0.4rem 0.6rem', background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: 6, color: 'var(--text)', fontSize: '0.85rem', fontFamily: 'inherit', outline: 'none', boxSizing: 'border-box' }}
                  />
                </div>

                {/* Condition */}
                <div>
                  <label style={{ fontSize: '0.62rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', display: 'block', marginBottom: '0.3rem' }}>Client Condition</label>
                  <select
                    value={condition}
                    onChange={e => setCondition(e.target.value)}
                    style={{ width: '100%', padding: '0.4rem 0.6rem', background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: 6, color: 'var(--text)', fontSize: '0.82rem', fontFamily: 'inherit', appearance: 'auto' as const }}
                  >
                    <option value="Good">Good</option>
                    <option value="Bad">Bad</option>
                    <option value="Other">Other</option>
                  </select>
                </div>

                {/* Date/Time */}
                <Field label="Date / Time" value={dateTimeStr} />

                {/* Signature */}
                <div>
                  <label style={{ fontSize: '0.62rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', display: 'block', marginBottom: '0.3rem' }}>Your Signature</label>
                  {savedSigUrl && !artistSig.startsWith('data:') ? (
                    <div>
                      <img src={savedSigUrl} alt="Saved signature" style={{ width: '100%', height: 60, objectFit: 'contain', borderRadius: 4, border: '1px solid var(--panel-border)', background: '#fff', display: 'block', fontSize: 0, color: 'transparent' }} />
                      <button onClick={() => { setSavedSigUrl(null); setArtistSig('') }} style={{ fontSize: '0.62rem', color: 'var(--gold-vibrant)', background: 'none', border: 'none', cursor: 'pointer', padding: '0.2rem 0', marginTop: '0.2rem' }}>
                        Draw new signature
                      </button>
                    </div>
                  ) : (
                    <SignaturePad onSign={setArtistSig} value={artistSig} width={396} height={80} strokeColor="#e8e0d4" />
                  )}
                </div>

                {/* Error */}
                {error && (
                  <div style={{ padding: '0.4rem 0.6rem', background: 'rgba(220,50,50,0.08)', border: '1px solid rgba(220,50,50,0.25)', borderRadius: 6, fontSize: '0.72rem', color: '#dc3232' }}>
                    {error}
                  </div>
                )}

                {/* Submit */}
                <button
                  onClick={handleSubmit}
                  disabled={!artistSig || submitting}
                  style={{
                    width: '100%', padding: '0.65rem', border: 'none', borderRadius: 8,
                    background: artistSig ? 'var(--gold-vibrant)' : 'rgba(166,125,67,0.15)',
                    color: artistSig ? '#0e0d0b' : 'var(--text-muted)',
                    fontSize: '0.85rem', fontWeight: 700, cursor: artistSig ? 'pointer' : 'not-allowed',
                    opacity: submitting ? 0.5 : 1,
                  }}
                >
                  {submitting ? 'Signing...' : 'Sign & Archive'}
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span style={{ color: 'var(--text-muted)', fontSize: '0.6rem', display: 'block' }}>{label}</span>
      <span style={{ color: 'var(--text)', fontSize: '0.78rem' }}>{value}</span>
    </div>
  )
}

function WaiversSection() {
  const { user } = useAuthStore()
  const toast = useToast()
  const isAdmin = ['assistant', 'admin', 'founder'].includes(user?.role || '')

  const [loading, setLoading] = useState(true)
  const [templates, setTemplates] = useState<DocumentTemplate[]>([])
  const { settings: studioSettings } = useStudioSettings()
  const waiverTemplateId = resolveWaiverTemplateId(studioSettings, templates)
  const [trackingDocs, setTrackingDocs] = useState<DocumentInstance[]>([])
  const [resentIds, setResentIds] = useState<Set<string>>(new Set())
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set())
  const [archiveOpen, setArchiveOpen] = useState(false)

  // Send form state
  const [sendOpen, setSendOpen] = useState(false)
  const [sendTemplateId, setSendTemplateId] = useState('')
  const [sendRecipient, setSendRecipient] = useState('')
  const [sendEmail, setSendEmail] = useState('')
  const [sending, setSending] = useState(false)
  const [sentSuccess, setSentSuccess] = useState(false)

  // Autosuggest state
  const [clientSuggestions, setClientSuggestions] = useState<Array<{ name: string; email: string }>>([])
  const [nameFocused, setNameFocused] = useState(false)

  const fetchClientSuggestions = useCallback(async () => {
    try {
      const today = new Date()
      const [bookingsRes, calRes] = await Promise.allSettled([
        bookingsApi.list({ all: true, limit: 200 }),
        calendarApi.getUpcoming(3),
      ])

      const seen = new Set<string>()
      const suggestions: Array<{ name: string; email: string; sortKey: number }> = []

      if (bookingsRes.status === 'fulfilled') {
        for (const b of bookingsRes.value.bookings) {
          const name = b.resolved_name || b.guest_name || b.client_name
          const email = b.resolved_email || b.guest_email
          if (!name || !email) continue
          const key = `${name.toLowerCase()}:${email.toLowerCase()}`
          if (seen.has(key)) continue
          seen.add(key)
          const date = b.scheduled_date || b.preferred_date || b.created_at
          const diff = date ? Math.abs(new Date(date).getTime() - today.getTime()) : Infinity
          suggestions.push({ name, email, sortKey: diff })
        }
      }

      if (calRes.status === 'fulfilled') {
        for (const ev of calRes.value.events) {
          const emailMatch = ev.description?.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/)?.[0]
          const name = ev.summary
          if (!name) continue
          const email = emailMatch || ''
          const key = `${name.toLowerCase()}:${email.toLowerCase()}`
          if (seen.has(key)) continue
          seen.add(key)
          const diff = Math.abs(new Date(ev.start).getTime() - today.getTime())
          suggestions.push({ name, email, sortKey: diff })
        }
      }

      suggestions.sort((a, b) => a.sortKey - b.sortKey)
      setClientSuggestions(suggestions.map(({ name, email }) => ({ name, email })))
    } catch { /* silent */ }
  }, [])

  // Fetch only: state is written once both lists answer.
  const requestDocs = useCallback(() => Promise.all([
    documentsApi.listTemplates(),
    documentsApi.listDocuments({ category: 'waiver' }),
  ])
    .then(([tplRes, docRes]) => {
      setTemplates(tplRes.templates)
      setTrackingDocs(docRes.documents)
    })
    .catch(() => { /* silent */ })
    .finally(() => setLoading(false)), [])

  const fetchDocs = () => {
    setLoading(true)
    return requestDocs()
  }

  // Mount load: `loading` already starts true.
  useEffect(() => { requestDocs() }, [requestDocs])

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!sendTemplateId || !sendRecipient || !sendEmail) return
    setSending(true)
    try {
      await documentsApi.send({ template_id: sendTemplateId, recipient_name: sendRecipient, recipient_email: sendEmail, reminder_hours: 6, expires_hours: 72 })
      setSentSuccess(true)
      setSendOpen(false)
      setSendTemplateId(''); setSendRecipient(''); setSendEmail('')
      setTimeout(() => setSentSuccess(false), 3000)
      fetchDocs()
    } catch { toast.error('Send failed') }
    finally { setSending(false) }
  }

  const handleRemind = async (id: string) => {
    try {
      await documentsApi.remind(id)
      setResentIds(prev => new Set([...prev, id]))
    } catch { toast.error('Remind failed') }
  }

  const handleCancel = async (id: string) => {
    setCancellingIds(prev => new Set([...prev, id]))
    try {
      await documentsApi.remove(id)
      setTrackingDocs(prev => prev.filter(d => d.id !== id))
    } catch { toast.error('Delete failed') }
    finally { setCancellingIds(prev => { const s = new Set(prev); s.delete(id); return s }) }
  }

  // Modal state
  const [modalDoc, setModalDoc] = useState<DocumentInstance | null>(null)

  const activeDocs = trackingDocs.filter(d => d.status !== 'approved' && d.status !== 'completed' && d.status !== 'cancelled')
  const archivedDocs = trackingDocs.filter(d => d.status === 'approved' || d.status === 'completed')

  // Tile status helpers
  const statusDot = (status: string) => {
    if (status === 'pending_review' || status === 'submitted' || status === 'signed') return 'uploaded'
    if (status === 'approved' || status === 'completed') return 'signed'
    if (status === 'sent') return ''
    return 'missing'
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {/* Header: count + send button */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '0.7rem', color: 'var(--muted)' }}>
          {loading ? 'Loading...' : `${trackingDocs.length} waiver${trackingDocs.length !== 1 ? 's' : ''}`}
        </span>
        <button
          disabled={!sendOpen && !waiverTemplateId}
          title={waiverTemplateId ? undefined : 'No waiver template yet. Create an active waiver template or set WAIVER_TEMPLATE_ID.'}
          onClick={() => {
            const opening = !sendOpen
            setSendOpen(opening)
            if (opening) {
              setSendTemplateId(waiverTemplateId ?? '')
              setSendRecipient('')
              setSendEmail('')
              fetchClientSuggestions()
            }
          }}
          style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', padding: '0.32rem 0.75rem', background: sendOpen ? 'rgba(166,125,67,0.1)' : 'transparent', border: '1px solid rgba(166,125,67,0.35)', borderRadius: '6px', color: 'var(--gold-vibrant)', fontSize: '0.7rem', fontWeight: 600, cursor: 'pointer', transition: 'all 0.15s' }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 2 11 13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
          Send Waiver
        </button>
      </div>

      {/* Inline send form */}
      {sendOpen && (() => {
        const filteredSuggestions = clientSuggestions.filter(s =>
          sendRecipient.trim() === '' || s.name.toLowerCase().includes(sendRecipient.toLowerCase())
        )
        const inputStyle = { background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: '6px', color: 'var(--text)', padding: '0.45rem 0.65rem', fontSize: '0.78rem', fontFamily: "'Inter', sans-serif", width: '100%', boxSizing: 'border-box' as const }
        return (
          <form onSubmit={handleSend} style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem', padding: '0.85rem 1rem', background: 'rgba(166,125,67,0.03)', border: '1px solid rgba(166,125,67,0.18)', borderRadius: '9px' }}>
            {/* Template locked to Tattoo Consent Waiver */}
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', padding: '0.3rem 0.5rem', background: 'rgba(166,125,67,0.06)', borderRadius: '5px', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
              Tattoo Consent Waiver
            </div>
            {/* Recipient name with autosuggest */}
            <div style={{ position: 'relative' }}>
              <input
                value={sendRecipient}
                onChange={e => { setSendRecipient(e.target.value); setSendEmail('') }}
                onFocus={() => setNameFocused(true)}
                onBlur={() => setTimeout(() => setNameFocused(false), 150)}
                placeholder="Recipient name"
                required
                autoComplete="off"
                style={inputStyle}
              />
              {nameFocused && filteredSuggestions.length > 0 && (
                <div style={{ position: 'absolute', top: 'calc(100% + 2px)', left: 0, right: 0, zIndex: 200, background: '#111009', border: '1px solid rgba(166,125,67,0.3)', borderRadius: '6px', maxHeight: '160px', overflowY: 'auto', boxShadow: '0 6px 20px rgba(0,0,0,0.5)' }}>
                  {filteredSuggestions.slice(0, 8).map((s, i) => (
                    <div
                      key={i}
                      onMouseDown={() => { setSendRecipient(s.name); setSendEmail(s.email) }}
                      style={{ padding: '0.45rem 0.65rem', fontSize: '0.75rem', cursor: 'pointer', borderBottom: i < Math.min(filteredSuggestions.length, 8) - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none', display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(166,125,67,0.1)')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                    >
                      <span style={{ color: 'var(--text)', flexShrink: 0 }}>{s.name}</span>
                      {s.email && <span style={{ color: 'var(--text-muted)', fontSize: '0.68rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.email}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
            {/* Email — auto-filled from suggestion, still editable */}
            <input
              type="email"
              value={sendEmail}
              onChange={e => setSendEmail(e.target.value)}
              placeholder="client@email.com"
              required
              style={inputStyle}
            />
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button type="button" onClick={() => setSendOpen(false)}
                style={{ padding: '0.4rem 0.85rem', background: 'transparent', border: '1px solid var(--panel-border)', borderRadius: '6px', color: 'var(--muted)', fontSize: '0.75rem', cursor: 'pointer' }}>Cancel</button>
              <button type="submit" disabled={sending}
                style={{ padding: '0.4rem 0.85rem', background: 'var(--gold-vibrant)', border: 'none', borderRadius: '6px', color: '#0e0d0b', fontSize: '0.75rem', fontWeight: 700, cursor: 'pointer', opacity: sending ? 0.6 : 1 }}>
                {sending ? 'Sending...' : 'Send'}
              </button>
            </div>
          </form>
        )
      })()}

      {sentSuccess && (
        <div style={{ padding: '0.6rem 1rem', background: 'rgba(109,191,126,0.08)', border: '1px solid rgba(109,191,126,0.3)', borderRadius: '7px', fontSize: '0.75rem', color: '#6dbf7e', textAlign: 'center' }}>
          Waiver sent successfully
        </div>
      )}

      {/* Tile grid — active (non-archived) waivers */}
      {!loading && activeDocs.length === 0 && archivedDocs.length === 0 && (
        <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--muted)', fontSize: '0.78rem' }}>No waivers on file yet.</div>
      )}
      {activeDocs.length > 0 && (
        <div className="scroll-tile-grid">
          {activeDocs.map(d => {
            const name = d.recipient_name || d.title
            const initial = (name?.[0] ?? '?').toUpperCase()
            const needsSign = isAdmin && ['pending_review', 'submitted', 'signed'].includes(d.status)
            const isSent = d.status === 'sent'

            return (
              <div key={d.id} className="scroll-tile-wrapper">
                <button className="scroll-tile" onClick={() => setModalDoc(d)}>
                  <div className={`scroll-tile-status ${statusDot(d.status)}`} />
                  <div className="scroll-tile-avatar">
                    <AvatarOrInitial url={null} initial={initial} size={38} />
                  </div>
                  <div className="scroll-tile-name">{name}</div>
                  <div className="scroll-tile-desc">
                    {isSent ? 'Awaiting' : d.status.replace(/_/g, ' ')}
                  </div>
                </button>
                <div className="scroll-tile-actions">
                  {needsSign && (
                    <button className="scroll-tile-action-btn" onClick={() => setModalDoc(d)} title="Sign">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
                      </svg>
                      Sign
                    </button>
                  )}
                  {isSent && (
                    <>
                      {resentIds.has(d.id)
                        ? <span className="scroll-tile-action-btn sent">Sent!</span>
                        : <button className="scroll-tile-action-btn" onClick={() => handleRemind(d.id)} title="Resend">Resend</button>
                      }
                      <button
                        className="scroll-tile-action-btn"
                        onClick={() => handleCancel(d.id)}
                        disabled={cancellingIds.has(d.id)}
                        title="Cancel"
                        style={{ color: 'rgba(224,92,92,0.7)' }}
                      >
                        {cancellingIds.has(d.id) ? '...' : 'Cancel'}
                      </button>
                    </>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Archive — approved / completed waivers */}
      {archivedDocs.length > 0 && (
        <div style={{ marginTop: '0.75rem' }}>
          <button
            onClick={() => setArchiveOpen(o => !o)}
            style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', background: 'none', border: 'none', color: 'var(--muted)', fontSize: '0.65rem', fontFamily: "'JetBrains Mono', monospace", letterSpacing: '0.06em', textTransform: 'uppercase', cursor: 'pointer', padding: '0.2rem 0', opacity: 0.6, transition: 'opacity 0.15s' }}
            onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
            onMouseLeave={e => (e.currentTarget.style.opacity = '0.6')}
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={{ transform: archiveOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}>
              <polyline points="9 18 15 12 9 6"/>
            </svg>
            Archive · {archivedDocs.length}
          </button>
          {archiveOpen && (
            <div className="scroll-tile-grid" style={{ marginTop: '0.6rem' }}>
              {archivedDocs.map(d => {
                const name = d.recipient_name || d.title
                const initial = (name?.[0] ?? '?').toUpperCase()
                return (
                  <div key={d.id} className="scroll-tile-wrapper">
                    <button className="scroll-tile" onClick={() => setModalDoc(d)}>
                      <div className="scroll-tile-status signed" />
                      <div className="scroll-tile-avatar">
                        <AvatarOrInitial url={null} initial={initial} size={38} />
                      </div>
                      <div className="scroll-tile-name">{name}</div>
                      <div className="scroll-tile-desc">approved</div>
                    </button>
                    <div className="scroll-tile-actions">
                      <button className="scroll-tile-action-btn sent" onClick={() => setModalDoc(d)} title="View">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
                        </svg>
                        View
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Sign-off modal */}
      {modalDoc && (
        <WaiverSignModal
          d={modalDoc}
          onClose={() => setModalDoc(null)}
          onDone={() => { setModalDoc(null); fetchDocs() }}
        />
      )}
    </div>
  )
}

// ── Inline Gala AI upload panel (shown inside tile grid) ──────────
function AddScrollInlinePanel({
  categorySlug,
  aiDigestEnabled,
  onUploaded,
  onClose,
}: {
  categorySlug: string
  aiDigestEnabled: boolean
  onUploaded: (doc: ScrollDoc) => void
  onClose: () => void
}) {
  const [uploading, setUploading] = useState(false)
  const [useAi, setUseAi] = useState(aiDigestEnabled)
  const [isId, setIsId] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  async function doUpload(file: File) {
    setUploading(true)
    setError(null)
    try {
      const result = await uploadScrollDoc({ categorySlug, file, aiDigestEnabled: useAi, isId })
      onUploaded({
        id: result.id,
        user_id: null,
        user_name: null,
        title: result.title,
        status: result.status,
        file_url: result.file_url,
        signed_at: null,
        created_at: result.created_at,
        ai_digest: result.ai_digest as Record<string, unknown> | null,
        notes: null,
      })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
      setUploading(false)
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) doUpload(file)
  }

  return (
    <div className="scroll-ai-index-panel" style={{ marginTop: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h4 className="scroll-ai-index-title">
          Add Scroll
          <span className="scroll-gala-badge" style={{ marginLeft: '0.5rem', verticalAlign: 'middle' }}>GALA</span>
        </h4>
        <button onClick={onClose}
          style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: '1rem', lineHeight: 1 }}>
          ✕
        </button>
      </div>

      <p className="scroll-ai-index-hint">
        Upload a document to this scroll tab. Gala can extract structured intel from the file
        and enrich your Nexus profile automatically.
      </p>

      {/* Drop zone */}
      <div
        className={`scroll-upload-tile${dragOver ? ' drag-over' : ''}`}
        style={{ minHeight: 80 }}
        onClick={() => !uploading && inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <input ref={inputRef} type="file" style={{ display: 'none' }} onChange={e => {
          const f = e.target.files?.[0]
          if (f) doUpload(f)
          e.target.value = ''
        }} accept=".pdf,.jpg,.jpeg,.png,.webp,.txt,.csv,.doc,.docx,.xls,.xlsx" />
        <div className="scroll-upload-tile-icon">
          {uploading
            ? <span style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>Uploading...</span>
            : <UploadIcon />
          }
        </div>
        <div className="scroll-upload-tile-label">
          {uploading ? 'Storing scroll...' : 'Drop file or click to upload'}
        </div>
        <div className="scroll-upload-tile-hint">PDF · Image · CSV · Doc · Spreadsheet</div>
      </div>

      {/* A government ID is stored privately and never sent to the Gala AI digest. */}
      <Chip selected={isId} onClick={() => setIsId(v => !v)} disabled={uploading}>
        This file is an ID
      </Chip>

      {/* Gala AI toggle */}
      <div
        className={`scroll-gala-toggle-card${useAi ? ' active' : ''}`}
        onClick={() => setUseAi(v => !v)}
      >
        <div className="scroll-gala-toggle-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
          </svg>
        </div>
        <div className="scroll-gala-toggle-body">
          <div className="scroll-gala-toggle-label">
            Gala AI Digest
            <span className="scroll-gala-badge">AI</span>
          </div>
          <div className="scroll-gala-toggle-hint">
            Auto-extract structured intel and enrich your Nexus profile
          </div>
        </div>
        <div className={`scroll-gala-toggle-pill${useAi ? ' on' : ''}`}>
          <div className="scroll-gala-toggle-thumb" />
        </div>
      </div>

      {error && (
        <div style={{ fontSize: '0.72rem', color: '#e05c5c', padding: '0.5rem 0.75rem', background: 'rgba(224,92,92,0.07)', borderRadius: '6px' }}>
          {error}
        </div>
      )}
    </div>
  )
}

// ── Scroll doc tile (uploaded document shown as tombstone tile) ────
function ScrollDocTile({
  doc,
  onDelete,
}: {
  doc: ScrollDoc
  onDelete?: () => void
}) {
  const ext = doc.title.split('.').pop()?.toUpperCase() ?? 'FILE'
  const shortName = doc.title.length > 18
    ? doc.title.slice(0, 16) + '…'
    : doc.title

  return (
    <div className="scroll-tile" style={{ position: 'relative' }}>
      {/* status dot */}
      <div className={`scroll-tile-status${doc.ai_digest ? ' uploaded' : ''}`} />

      {/* avatar area shows file type */}
      <div className="scroll-tile-avatar" style={{ background: 'rgba(255,248,236,0.04)' }}>
        <FileIcon />
      </div>

      <div className="scroll-tile-name">{shortName}</div>
      <div className="scroll-tile-meta">{ext}</div>

      {/* action row */}
      <div style={{ display: 'flex', gap: '0.3rem', marginTop: '0.15rem' }}>
        {doc.file_url && (
          <a
            href={doc.file_url}
            target="_blank"
            rel="noopener noreferrer"
            className="scroll-doc-row-action"
            style={{ textDecoration: 'none', fontSize: '0.58rem', padding: '0.15rem 0.4rem' }}
            onClick={e => e.stopPropagation()}
          >
            Open
          </a>
        )}
        {onDelete && (
          <button
            className="scroll-doc-row-action"
            style={{ borderColor: 'rgba(224,92,92,0.3)', color: '#e05c5c', fontSize: '0.58rem', padding: '0.15rem 0.4rem' }}
            onClick={e => { e.stopPropagation(); onDelete() }}
          >
            Del
          </button>
        )}
      </div>

      {doc.ai_digest && (
        <span className="scroll-gala-badge" style={{ marginTop: '0.1rem' }}>AI</span>
      )}
    </div>
  )
}

// ── Templates Section ─────────────────────────────────────────────

function TemplatesSection() {
  const { user } = useAuthStore()
  const isPrivileged = user?.role === 'founder' || user?.role === 'admin'
  const [templates, setTemplates] = useState<ResponseTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newBody, setNewBody] = useState('')
  const [creating, setCreating] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    templatesApi.list()
      .then(setTemplates)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  async function handleCreate() {
    if (!newTitle.trim() || !newBody.trim()) return
    setCreating(true)
    setError(null)
    try {
      const t = await templatesApi.create({ title: newTitle.trim(), body: newBody.trim() })
      setTemplates(prev => [...prev, t])
      setNewTitle('')
      setNewBody('')
      setShowCreate(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setCreating(false)
    }
  }

  async function handleUpdate(id: string) {
    setError(null)
    try {
      const updated = await templatesApi.update(id, { title: editTitle.trim(), body: editBody.trim() })
      setTemplates(prev => prev.map(t => t.id === id ? updated : t))
      setEditingId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed')
    }
  }

  async function handleDelete(id: string) {
    try {
      await templatesApi.remove(id)
      setTemplates(prev => prev.filter(t => t.id !== id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  function startEdit(t: ResponseTemplate) {
    setEditingId(t.id)
    setEditTitle(t.title)
    setEditBody(t.body)
    setError(null)
  }

  return (
    <div className="tmpl-section">
      <div className="tmpl-header">
        <div>
          <div className="tmpl-heading">Response Templates</div>
          <div className="tmpl-subheading">Staff quick-replies available in the Courier chat toolbar</div>
        </div>
        <button className="tmpl-new-btn" onClick={() => { setShowCreate(v => !v); setError(null) }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
          New
        </button>
      </div>

      {showCreate && (
        <div className="tmpl-form">
          <input
            className="tmpl-input"
            placeholder="Label (e.g. Running Late)"
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            autoFocus
          />
          <textarea
            className="tmpl-textarea"
            placeholder="Message body..."
            value={newBody}
            onChange={e => setNewBody(e.target.value)}
            rows={2}
          />
          {error && <div className="tmpl-error">{error}</div>}
          <div className="tmpl-form-actions">
            <button className="tmpl-btn-cancel" onClick={() => setShowCreate(false)}>Cancel</button>
            <button
              className="tmpl-btn-save"
              onClick={handleCreate}
              disabled={!newTitle.trim() || !newBody.trim() || creating}
            >
              {creating ? 'Saving...' : 'Save Template'}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="tmpl-empty">Loading...</div>
      ) : templates.length === 0 && !showCreate ? (
        <div className="tmpl-empty">No templates yet — create one above.</div>
      ) : (
        <div className="tmpl-list">
          {templates.map(t => {
            const canEdit = isPrivileged || t.created_by === user?.id
            if (editingId === t.id) {
              return (
                <div key={t.id} className="tmpl-row tmpl-row--editing">
                  <input
                    className="tmpl-input"
                    value={editTitle}
                    onChange={e => setEditTitle(e.target.value)}
                    autoFocus
                  />
                  <textarea
                    className="tmpl-textarea"
                    value={editBody}
                    onChange={e => setEditBody(e.target.value)}
                    rows={2}
                  />
                  {error && <div className="tmpl-error">{error}</div>}
                  <div className="tmpl-form-actions">
                    <button className="tmpl-btn-cancel" onClick={() => setEditingId(null)}>Cancel</button>
                    <button
                      className="tmpl-btn-save"
                      onClick={() => handleUpdate(t.id)}
                      disabled={!editTitle.trim() || !editBody.trim()}
                    >
                      Save
                    </button>
                  </div>
                </div>
              )
            }
            return (
              <div key={t.id} className="tmpl-row">
                <div className="tmpl-row-content">
                  <span className="tmpl-pill">{t.title}</span>
                  <span className="tmpl-body">{t.body}</span>
                </div>
                {canEdit && (
                  <div className="tmpl-row-actions">
                    <button className="tmpl-icon-btn" onClick={() => startEdit(t)} title="Edit">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                      </svg>
                    </button>
                    <button className="tmpl-icon-btn tmpl-icon-btn--del" onClick={() => handleDelete(t.id)} title="Delete">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                      </svg>
                    </button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Quest Log Section ─────────────────────────────────────────────

type QuestEntry = {
  id: string
  title: string
  description?: string
  category?: string
  progress: number
  target_count: number
  xp_reward: number
  completed: boolean
  claimed: boolean
  completed_at?: string | null
  claimed_at?: string | null
}

type QuestFilter = 'active' | 'completed' | 'archived'

function QuestLogSection() {
  const [filter, setFilter] = useState<QuestFilter>('active')
  const [quests, setQuests] = useState<QuestEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: show spinner before async fetch
    setLoading(true)
    const token = localStorage.getItem('ryozan_token')
    fetch(`${API_BASE}/quests/`, {
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => {
        setQuests((data.quests || []).map((q: {
          quest: { id: string; title: string; description?: string; category?: string; target_count: number; xp_reward: number }
          progress: number; completed: boolean; claimed: boolean; completed_at?: string | null; claimed_at?: string | null
        }) => ({
          id: q.quest.id,
          title: q.quest.title,
          description: q.quest.description,
          category: q.quest.category,
          progress: q.progress,
          target_count: q.quest.target_count,
          xp_reward: q.quest.xp_reward,
          completed: q.completed,
          claimed: q.claimed,
          completed_at: q.completed_at,
          claimed_at: q.claimed_at,
        })))
      })
      .catch(() => setQuests([]))
      .finally(() => setLoading(false))
  }, [])

  const filtered = quests.filter(q => {
    if (filter === 'active') return !q.completed && !q.claimed
    if (filter === 'completed') return q.completed && !q.claimed
    if (filter === 'archived') return q.claimed
    return true
  })

  const counts = {
    active: quests.filter(q => !q.completed && !q.claimed).length,
    completed: quests.filter(q => q.completed && !q.claimed).length,
    archived: quests.filter(q => q.claimed).length,
  }

  return (
    <div className="quest-log">
      <div className="quest-log-header">
        <div className="quest-log-title-row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gold-vibrant)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
          </svg>
          <span className="quest-log-title">Quest Log</span>
        </div>
        <div className="quest-log-filters">
          {(['active', 'completed', 'archived'] as QuestFilter[]).map(f => (
            <button
              key={f}
              className={`quest-log-filter-btn${filter === f ? ' active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
              {counts[f] > 0 && <span className="quest-log-filter-count">{counts[f]}</span>}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="quest-log-empty">Loading quests...</div>
      ) : filtered.length === 0 ? (
        <div className="quest-log-empty">
          {filter === 'active' ? 'No active quests. All tasks complete.' :
           filter === 'completed' ? 'No completed quests awaiting claim.' :
           'No archived quests on record.'}
        </div>
      ) : (
        <div className="quest-log-list">
          {filtered.map(q => {
            const pct = Math.min(100, Math.round((q.progress / q.target_count) * 100))
            return (
              <div key={q.id} className={`quest-log-card${q.completed ? ' done' : ''}${q.claimed ? ' archived' : ''}`}>
                <div className="quest-log-card-top">
                  <div className="quest-log-card-info">
                    {q.category && (
                      <span className="quest-log-card-cat">{q.category}</span>
                    )}
                    <span className="quest-log-card-title">{q.title}</span>
                    {q.description && (
                      <span className="quest-log-card-desc">{q.description}</span>
                    )}
                  </div>
                  <div className="quest-log-card-xp">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                    </svg>
                    +{q.xp_reward} XP
                  </div>
                </div>
                <div className="quest-log-progress-row">
                  <div className="quest-log-track">
                    <div className="quest-log-fill" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="quest-log-count">{q.progress} / {q.target_count}</span>
                </div>
                {q.claimed && q.claimed_at && (
                  <span className="quest-log-timestamp">
                    Claimed {new Date(q.claimed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                  </span>
                )}
                {q.completed && !q.claimed && q.completed_at && (
                  <span className="quest-log-timestamp quest-log-timestamp--ready">
                    Completed {new Date(q.completed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} — Ready to claim
                  </span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Add-tab wizard modal ──────────────────────────────────────────

interface CategoryPreset {
  type: string
  label: string
  desc: string
  icon: string
  accent: string
  aiDigest: boolean
  aiPrompt: string
  builds: string[]
}

const CATEGORY_PRESETS: CategoryPreset[] = [
  {
    type: 'financial',
    label: 'Financial Records',
    desc: 'Budget audits, tax documents, transaction logs, and expense reports.',
    icon: 'dollar',
    accent: '#4caf50',
    aiDigest: true,
    aiPrompt: 'Extract totals, vendor names, line items, dates, and transaction amounts.',
    builds: ['Document upload zone', 'Gala AI data extraction', 'Nexus ledger sync'],
  },
  {
    type: 'health',
    label: 'Health & Safety',
    desc: 'Health department certifications, inspection records, and handler cards.',
    icon: 'health',
    accent: '#2196f3',
    aiDigest: true,
    aiPrompt: 'Extract certificate names, expiration dates, certifying authority, and compliance status.',
    builds: ['Document upload zone', 'Gala AI extraction', 'Expiry date tracking'],
  },
  {
    type: 'legal',
    label: 'Legal & Compliance',
    desc: 'Contracts, NDAs, licensing agreements, and compliance documentation.',
    icon: 'legal',
    accent: '#faae15',
    aiDigest: true,
    aiPrompt: 'Extract party names, effective dates, key clauses, expiration dates, and obligations.',
    builds: ['Document upload zone', 'Gala AI clause indexing', 'Signature tracking'],
  },
  {
    type: 'portfolio',
    label: 'Artist Portfolio',
    desc: 'Work samples, process photos, bio materials, and showcase content.',
    icon: 'image',
    accent: '#a67d43',
    aiDigest: false,
    aiPrompt: '',
    builds: ['Image & document upload', 'Gallery view tile', 'Public showcase toggle'],
  },
  {
    type: 'operations',
    label: 'Operations',
    desc: 'SOPs, training materials, scheduling templates, and staff documentation.',
    icon: 'clipboard',
    accent: '#9c27b0',
    aiDigest: false,
    aiPrompt: 'Extract procedures, responsible parties, timelines, and action items.',
    builds: ['Document upload zone', 'Version history', 'Staff access control'],
  },
  {
    type: 'custom',
    label: 'Custom',
    desc: 'Define your own category from scratch. You control the name, purpose, and AI behavior.',
    icon: 'pen',
    accent: '#888',
    aiDigest: false,
    aiPrompt: '',
    builds: ['You configure everything', 'Optional Gala AI digest', 'Full control'],
  },
]

function WizardCatIcon({ slug, size = 18 }: { slug: string; size?: number }) {
  const p = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: '1.8', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  switch (slug) {
    case 'dollar':
      return <svg {...p}><path d="M12 1v22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
    case 'health':
      return <svg {...p}><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
    case 'legal':
      return <svg {...p}><path d="M12 3v18"/><path d="M5 21h14"/><path d="m5 8 7-5 7 5"/><path d="M5 8a4 4 0 0 1-4 4"/><path d="M19 8a4 4 0 0 0 4 4"/></svg>
    case 'image':
      return <svg {...p}><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
    case 'clipboard':
      return <svg {...p}><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>
    case 'pen':
      return <svg {...p}><line x1="12" y1="20" x2="21" y2="20"/><path d="m16.5 3.5 3 3L7 19l-4 1 1-4Z"/></svg>
    default:
      return <svg {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
  }
}

function AddScrollTabWizard({
  onClose,
  onCreated,
  isFounderOrAdmin = false,
}: {
  onClose: () => void
  onCreated: (cat: ScrollCategory) => void
  isFounderOrAdmin?: boolean
}) {
  const [mode, setMode] = useState<'guided' | 'gala'>('guided')
  const [step, setStep] = useState<1 | 2>(1)
  const [selectedPreset, setSelectedPreset] = useState<CategoryPreset | null>(null)
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [aiDigest, setAiDigest] = useState(false)
  const [aiPrompt, setAiPrompt] = useState('')
  const [galaIntent, setGalaIntent] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    document.body.classList.add('wizard-open')
    return () => document.body.classList.remove('wizard-open')
  }, [])

  function selectPreset(preset: CategoryPreset) {
    setSelectedPreset(preset)
    setLabel(preset.label)
    setDescription(preset.desc)
    setAiDigest(preset.aiDigest)
    setAiPrompt(preset.aiPrompt)
    setError(null)
    setStep(2)
  }

  function goBack() {
    setStep(1)
    setError(null)
  }

  async function handleCreate() {
    if (!label.trim()) return
    setSaving(true)
    setError(null)
    try {
      const cat = await createScrollCategory({
        label: label.trim(),
        description: description.trim(),
        icon: selectedPreset?.icon ?? 'file',
        ai_digest_enabled: aiDigest,
        ai_digest_prompt: aiPrompt.trim(),
      })
      onCreated(cat)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create tab')
    } finally {
      setSaving(false)
    }
  }

  async function handleGalaCreate() {
    if (!galaIntent.trim()) return
    setSaving(true)
    setError(null)
    try {
      const cat = await interpretScrollCategory(galaIntent.trim())
      onCreated(cat)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gala failed to interpret — try again')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="scroll-wizard-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="scroll-wizard-panel">

        {/* Header */}
        <div className="scroll-wizard-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            {step === 2 && mode === 'guided' && (
              <button className="scroll-wizard-back-btn" onClick={goBack} title="Back">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 18 9 12 15 6"/>
                </svg>
              </button>
            )}
            <div>
              <h3 className="scroll-wizard-title">
                {mode === 'gala' ? 'Ask Gala' : step === 1 ? 'New Scroll Tab' : selectedPreset?.label ?? 'Configure Tab'}
              </h3>
              <p className="scroll-wizard-subtitle">
                {mode === 'gala'
                  ? 'Describe what you need and Gala configures everything.'
                  : step === 1
                    ? 'Choose a category type to get started.'
                    : 'Review and customize before creating.'}
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
            {isFounderOrAdmin && (
              <div className="scroll-wizard-mode-toggle" style={{ margin: 0 }}>
                <button
                  className={`scroll-wizard-mode-btn${mode === 'guided' ? ' active' : ''}`}
                  onClick={() => { setMode('guided'); setStep(1) }}
                >
                  Guided
                </button>
                <button
                  className={`scroll-wizard-mode-btn${mode === 'gala' ? ' active' : ''}`}
                  onClick={() => setMode('gala')}
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: '0.25rem', verticalAlign: '-1px' }}>
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                  </svg>
                  Gala
                </button>
              </div>
            )}
            <button className="scroll-wizard-close" onClick={onClose} aria-label="Close">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
        </div>

        {/* Step indicator */}
        {mode === 'guided' && (
          <div className="scroll-wizard-steps">
            <div className={`scroll-wizard-step${step === 1 ? ' active' : step === 2 ? ' done' : ''}`}>
              <span className="scroll-wizard-step-dot">{step === 2 ? '✓' : '1'}</span>
              <span>Choose type</span>
            </div>
            <div className="scroll-wizard-step-line" />
            <div className={`scroll-wizard-step${step === 2 ? ' active' : ''}`}>
              <span className="scroll-wizard-step-dot">2</span>
              <span>Configure</span>
            </div>
          </div>
        )}

        {/* ── Step 1: Category cards ── */}
        {mode === 'guided' && step === 1 && (
          <div className="scroll-wizard-cat-grid">
            {CATEGORY_PRESETS.map((preset) => (
              <button
                key={preset.type}
                className="scroll-wizard-cat-card"
                onClick={() => selectPreset(preset)}
                style={{ '--cat-accent': preset.accent } as React.CSSProperties}
              >
                <div className="scroll-wizard-cat-icon-wrap">
                  <WizardCatIcon slug={preset.icon} size={18} />
                </div>
                <div className="scroll-wizard-cat-body">
                  <div className="scroll-wizard-cat-name">{preset.label}</div>
                  <div className="scroll-wizard-cat-desc">{preset.desc}</div>
                  {preset.aiDigest && (
                    <div className="scroll-wizard-cat-ai-pill">
                      <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                      </svg>
                      AI Digest
                    </div>
                  )}
                </div>
                <svg className="scroll-wizard-cat-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 18 15 12 9 6"/>
                </svg>
              </button>
            ))}
          </div>
        )}

        {/* ── Step 2: Configure ── */}
        {mode === 'guided' && step === 2 && selectedPreset && (
          <>
            {/* Name */}
            <div>
              <div className="scroll-wizard-field-label">Tab name</div>
              <input
                className="scroll-wizard-input"
                value={label}
                onChange={e => setLabel(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && !saving && label.trim() && handleCreate()}
                autoFocus
              />
            </div>

            {/* Description */}
            <div>
              <div className="scroll-wizard-field-label">Description</div>
              <input
                className="scroll-wizard-input"
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="What goes in this tab?"
              />
            </div>

            {/* What gets built */}
            <div className="scroll-wizard-builds">
              <div className="scroll-wizard-builds-label">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--gold-vibrant)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
                </svg>
                Auto-configured on create
              </div>
              <div className="scroll-wizard-builds-list">
                {selectedPreset.builds.map((item) => (
                  <div key={item} className="scroll-wizard-build-item">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--gold-vibrant)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    {item}
                  </div>
                ))}
              </div>
            </div>

            {/* AI Digest toggle */}
            <label className="scroll-wizard-toggle-row">
              <input type="checkbox" checked={aiDigest} onChange={e => setAiDigest(e.target.checked)} />
              <div>
                <div className="scroll-wizard-toggle-label">
                  Gala AI Digest
                  {selectedPreset.aiDigest && (
                    <span className="scroll-wizard-recommended">Recommended</span>
                  )}
                </div>
                <div className="scroll-wizard-toggle-hint">
                  Gala reads uploads, extracts structured data, and syncs intel to your Nexus profile.
                </div>
              </div>
            </label>

            {aiDigest && (
              <div>
                <div className="scroll-wizard-field-label">What should Gala extract?</div>
                <input
                  className="scroll-wizard-input"
                  placeholder="e.g. totals, vendor names, expiration dates"
                  value={aiPrompt}
                  onChange={e => setAiPrompt(e.target.value)}
                />
              </div>
            )}
          </>
        )}

        {/* ── Gala mode ── */}
        {mode === 'gala' && (
          <div className="scroll-wizard-gala-block">
            <div className="scroll-wizard-gala-header">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--gold-vibrant)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
              </svg>
              <span>Describe the tab you need</span>
            </div>
            <p className="scroll-wizard-gala-desc">
              Gala interprets your intent and automatically configures the label, icon, description, upload structure, and AI digest settings.
            </p>
            <textarea
              className="scroll-wizard-gala-textarea"
              placeholder="e.g. A tab for tracking health department certificates and food handler cards for the shop"
              value={galaIntent}
              onChange={e => setGalaIntent(e.target.value)}
              rows={4}
              autoFocus
            />
          </div>
        )}

        {error && <div className="scroll-wizard-error">{error}</div>}

        {/* Footer buttons */}
        {(mode === 'gala' || step === 2) && (
          <div className="scroll-wizard-btn-row">
            <button className="scroll-wizard-btn-cancel" onClick={mode === 'guided' ? goBack : onClose}>
              {mode === 'guided' ? 'Back' : 'Cancel'}
            </button>
            {mode === 'guided' ? (
              <button
                className="scroll-wizard-btn-create"
                onClick={handleCreate}
                disabled={!label.trim() || saving}
              >
                {saving
                  ? <><span className="scroll-wizard-spinner" />Creating...</>
                  : 'Create Tab'}
              </button>
            ) : (
              <button
                className="scroll-wizard-btn-create"
                onClick={handleGalaCreate}
                disabled={!galaIntent.trim() || saving}
              >
                {saving
                  ? <><span className="scroll-wizard-spinner" />Thinking...</>
                  : 'Let Gala Build It'}
              </button>
            )}
          </div>
        )}

      </div>
    </div>
  )
}

// ── Main ScrollsPanel ─────────────────────────────────────────────

export default function ScrollsPanel() {
  const { user } = useAuthStore()
  const isFounderOrAdmin = user?.role === 'founder' || user?.role === 'admin'
  const [categories, setCategories] = useState<ScrollCategory[]>(BUILTIN_CATEGORIES)
  const [, setCatsLoading] = useState(true)
  const [activeSlug, setActiveSlug] = useState<string>(() => {
    const requested = new URLSearchParams(window.location.search).get('category')
    return requested || 'artist'
  })
  const [showWizard, setShowWizard] = useState(false)

  // Which tab currently has the inline add panel open
  const [addPanelSlug, setAddPanelSlug] = useState<string | null>(null)

  // Artist tab state
  const [artistList, setArtistList] = useState<ArtistSummary[]>([])
  const setSelectedArtistId = useState<string | null>(null)[1]
  const setArtistScrollData = useState<ArtistScrollData | 'loading' | null>(null)[1]

  // Inline tile expansion
  const [expandedArtistId, setExpandedArtistId] = useState<string | null>(null)
  const [expandMode, setExpandMode] = useState<'view' | 'edit'>('view')
  // Cache scroll data per artist so we don't re-fetch on every open
  const [scrollDataCache, setScrollDataCache] = useState<Record<string, ArtistScrollData | null>>({})
  const [expandLoading, setExpandLoading] = useState(false)
  const [scrollRequestStatus, setScrollRequestStatus] = useState<Record<string, ArtistScrollRequestStatus | null>>({})

  // Generic tab docs state
  const [docsBySlug, setDocsBySlug] = useState<Record<string, ScrollDoc[]>>({})
  const [docsLoading, setDocsLoading] = useState(false)

  // Scroll request / remind button state per artist id
  const [scrollActionState, setScrollActionState] = useState<Record<string, 'idle' | 'loading' | 'sent' | 'error'>>({})

  // Load categories — if API returns data, replace built-ins
  useEffect(() => {
    listScrollCategories()
      .then(cats => { if (cats.length > 0) setCategories(cats) })
      .catch(console.error)
      .finally(() => setCatsLoading(false))
  }, [])

  // The owner archive is founder-only; anyone else lands on the artist tab.
  if (activeSlug === OWNER_ARCHIVE_SLUG && user?.role && user.role !== 'founder') {
    setActiveSlug('artist')
  }

  // Entering the artist tab as an artist opens their own scroll. The visible
  // state adjusts during render; the effect below only fetches.
  const [artistTabSeen, setArtistTabSeen] = useState<readonly [string, boolean] | null>(null)
  if (!artistTabSeen || artistTabSeen[0] !== activeSlug || artistTabSeen[1] !== isFounderOrAdmin) {
    setArtistTabSeen([activeSlug, isFounderOrAdmin])
    if (activeSlug === 'artist' && !isFounderOrAdmin && user?.id) {
      if (!(user.id in scrollDataCache)) {
        setExpandLoading(true)
      } else {
        setExpandedArtistId(user.id)
        setExpandMode('edit')
      }
    }
  }

  // Load artist list for artist tab (founder/admin only — artists see own scroll directly)
  useEffect(() => {
    if (activeSlug !== 'artist') return
    if (!isFounderOrAdmin) {
      // Artist viewing their own scroll — auto-expand
      if (user?.id && !(user.id in scrollDataCache)) {
        getUserScroll(user.id)
          .then(resp => {
            setScrollDataCache(prev => ({ ...prev, [user.id]: resp.scroll ?? null }))
            setExpandedArtistId(user.id)
            setExpandMode('edit')
          })
          .catch(() => setScrollDataCache(prev => ({ ...prev, [user.id]: null })))
          .finally(() => setExpandLoading(false))
      }
      return
    }
    if (artistList.length > 0) return
    artistsApi.list()
      .then(setArtistList)
      .catch(console.error)
  }, [activeSlug, isFounderOrAdmin]) // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-fetch scroll completion status for all artists (founder/admin only)
  useEffect(() => {
    if (!isFounderOrAdmin) return
    if (artistList.length === 0) return
    artistList.forEach(a => {
      if (a.id in scrollDataCache) return
      getUserScroll(a.id)
        .then(resp => setScrollDataCache(prev => ({ ...prev, [a.id]: resp.scroll ?? null })))
        .catch(() => setScrollDataCache(prev => ({ ...prev, [a.id]: null })))
    })
  }, [artistList, isFounderOrAdmin]) // eslint-disable-line react-hooks/exhaustive-deps

  // Founder review needs request/reminder state before opening an empty scroll.
  useEffect(() => {
    if (!isFounderOrAdmin) return
    if (artistList.length === 0) return
    artistList.forEach(a => {
      if (a.id in scrollRequestStatus) return
      getArtistScrollRequestStatus(a.id)
        .then(status => setScrollRequestStatus(prev => ({ ...prev, [a.id]: status })))
        .catch(() => setScrollRequestStatus(prev => ({ ...prev, [a.id]: null })))
    })
  }, [artistList, isFounderOrAdmin]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load docs for non-artist tabs: each tab change that finds no cached docs
  // records a request during render, and the effect fetches it.
  const [docsSlugSeen, setDocsSlugSeen] = useState<string | null>(null)
  const [docsRequest, setDocsRequest] = useState<{ slug: string } | null>(null)
  if (docsSlugSeen !== activeSlug) {
    setDocsSlugSeen(activeSlug)
    if (activeSlug !== 'artist' && activeSlug !== 'quests' && activeSlug !== 'client-records' && !docsBySlug[activeSlug]) {
      setDocsLoading(true)
      setDocsRequest({ slug: activeSlug })
    }
  }

  useEffect(() => {
    if (!docsRequest) return
    const { slug } = docsRequest
    listScrollCategoryDocs(slug)
      .then(resp => setDocsBySlug(prev => ({ ...prev, [slug]: resp.docs })))
      .catch(console.error)
      .finally(() => setDocsLoading(false))
  }, [docsRequest])

  async function handleScrollRequest(artistId: string, type: 'request' | 'remind', e: React.MouseEvent) {
    e.stopPropagation()
    setScrollActionState(prev => ({ ...prev, [artistId + type]: 'loading' }))
    try {
      if (type === 'request') {
        await requestArtistScroll(artistId)
      } else {
        await remindArtistScroll(artistId)
      }
      try {
        const status = await getArtistScrollRequestStatus(artistId)
        setScrollRequestStatus(prev => ({ ...prev, [artistId]: status }))
      } catch { /* status is advisory */ }
      setScrollActionState(prev => ({ ...prev, [artistId + type]: 'sent' }))
      // Reset to idle after 3s
      setTimeout(() => setScrollActionState(prev => ({ ...prev, [artistId + type]: 'idle' })), 3000)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed'
      // 429 = debounced, show special state
      setScrollActionState(prev => ({ ...prev, [artistId + type]: msg.includes('429') || msg.toLowerCase().includes('wait') ? 'sent' : 'error' }))
      setTimeout(() => setScrollActionState(prev => ({ ...prev, [artistId + type]: 'idle' })), 4000)
    }
  }

  async function handleExpandTile(artistId: string, mode: 'view' | 'edit') {
    const safeMode = mode === 'edit' && artistId !== user?.id ? 'view' : mode
    // Toggle off if already expanded in same mode
    if (expandedArtistId === artistId && expandMode === safeMode) {
      setExpandedArtistId(null)
      return
    }
    setExpandedArtistId(artistId)
    setExpandMode(safeMode)

    // Fetch scroll data if not cached
    if (!(artistId in scrollDataCache)) {
      setExpandLoading(true)
      try {
        const resp = await getUserScroll(artistId)
        setScrollDataCache(prev => ({ ...prev, [artistId]: resp.scroll ?? null }))
      } catch {
        setScrollDataCache(prev => ({ ...prev, [artistId]: null }))
      } finally {
        setExpandLoading(false)
      }
    }
  }

  function handleCollapseExpanded() {
    setExpandedArtistId(null)
  }

  // Derive tile descriptor: 3-5 words based on cached scroll state
  function getTileDesc(artistId: string): string {
    if (!(artistId in scrollDataCache)) return 'tap to view scroll'
    const sd = scrollDataCache[artistId]
    if (!sd) return 'no docs on file'
    if (sd.status === 'signed') {
      const hasBbp = !!sd.bbp_cert_url
      const hasCpr = !!(sd.fields?.cpr_cert_url || sd.cpr_cert_url)
      const hasFirstAid = !!(sd.fields?.first_aid_cert_url || sd.first_aid_cert_url)
      if (hasBbp && hasCpr && hasFirstAid) return 'all certs on file'
      if (hasBbp) return 'scroll signed + bbp'
      return 'scroll signed'
    }
    return 'scroll incomplete'
  }

  function renderEmptyArtistScrollReview(a: ArtistSummary) {
    const status = scrollRequestStatus[a.id]
    const requestState = scrollActionState[a.id + 'request'] ?? 'idle'
    const remindState = scrollActionState[a.id + 'remind'] ?? 'idle'
    const requestSent = Boolean(status?.request_sent)
    const lastTouch = status?.last_reminder_at || status?.last_request_at
    const actionType: 'request' | 'remind' = requestSent ? 'remind' : 'request'
    const actionState = actionType === 'request' ? requestState : remindState
    const actionLabel = actionState === 'loading'
      ? 'Sending...'
      : actionState === 'sent'
        ? 'Sent'
        : requestSent
          ? 'Remind Artist'
          : 'Send Artist Scroll'

    return (
      <div className="scroll-empty-review">
        <div className="scroll-empty-review-icon" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 2 11 13"/>
            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
        </div>
        <div className="scroll-empty-review-copy">
          <span className="scroll-empty-review-kicker">Artist Scroll Required</span>
          <h3>{a.name} has no completed scroll on file.</h3>
          <div className="scroll-empty-review-meta">
            <span>{requestSent ? 'Request already sent' : 'Not sent yet'}</span>
            {lastTouch && <span>Last touch {new Date(lastTouch).toLocaleString()}</span>}
            {status?.next_reminder_at && <span>Next daily reminder {new Date(status.next_reminder_at).toLocaleString()}</span>}
          </div>
        </div>
        <button
          className={`scroll-empty-review-action${actionState === 'sent' ? ' sent' : ''}${actionState === 'error' ? ' error' : ''}`}
          onClick={e => handleScrollRequest(a.id, actionType, e)}
          disabled={actionState === 'loading'}
          aria-label={actionLabel}
          title={actionLabel}
        >
          {actionState === 'sent' ? (
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
          ) : actionState === 'loading' ? (
            <span className="scroll-empty-review-action-text">...</span>
          ) : (
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M22 2 11 13"/>
              <polygon points="22 2 15 22 11 13 2 9 22 2"/>
            </svg>
          )}
          <span className="scroll-empty-review-action-text">{actionLabel}</span>
        </button>
      </div>
    )
  }

  function handleTabCreated(cat: ScrollCategory) {
    setCategories(prev => [...prev, cat])
    setShowWizard(false)
    setActiveSlug(cat.slug)
  }

  function handleDocUploaded(slug: string, doc: ScrollDoc) {
    setDocsBySlug(prev => ({
      ...prev,
      [slug]: [doc, ...(prev[slug] ?? [])],
    }))
    setAddPanelSlug(null)
  }

  async function handleDeleteDoc(slug: string, docId: string) {
    try {
      await deleteScrollDoc(docId)
      setDocsBySlug(prev => ({
        ...prev,
        [slug]: (prev[slug] ?? []).filter(d => d.id !== docId),
      }))
    } catch (err) {
      console.error('Delete scroll doc failed:', err)
    }
  }

  // The immutable submission archive is visible only to founders. The API applies
  // the same role gate, while this filter prevents the tab from flashing for staff.
  const visibleCategories = categories.filter(c => c.slug !== OWNER_ARCHIVE_SLUG || user?.role === 'founder')
    .concat(isFounderOrAdmin ? [CLIENT_RECORDS_CATEGORY] : [])
  const activeCat = visibleCategories.find(c => c.slug === activeSlug)
  const isOwnerArchive = activeSlug === OWNER_ARCHIVE_SLUG

  // ── Artist tab: add tile opens the inline panel pinned to the artist grid
  // For artist tab, add tile label is "Add Scroll" with a note it's for other tabs
  // (artist scrolls come from the ArtistScroll form flow, not uploads)

  return (
    <div style={{ padding: '1rem 1.25rem', paddingBottom: 'var(--hotbar-clearance, 100px)' }}>

      {/* ── Subtab bar ── */}
      <div className="scroll-subtabs">
        {visibleCategories.map(cat => (
          <button
            key={cat.slug}
            className={`scroll-subtab${activeSlug === cat.slug ? ' active' : ''}`}
            onClick={() => {
              setActiveSlug(cat.slug)
              setSelectedArtistId(null)
              setArtistScrollData(null)
              setAddPanelSlug(null)
            }}
          >
            <ScrollTabIcon slug={cat.slug} />
            {cat.label}
          </button>
        ))}
        <button
          className="scroll-subtab scroll-subtab-add"
          onClick={() => setShowWizard(true)}
          title="Add a new scroll tab"
        >
          +
        </button>
      </div>

      {/* ── Artist tab ── */}
      {activeSlug === 'artist' && (
        <>
          {/* Artist card grid */}
          <>
            <div className="scroll-tile-grid">
                {artistList.map(a => {
                  const reqState = scrollActionState[a.id + 'request'] ?? 'idle'
                  const remState = scrollActionState[a.id + 'remind'] ?? 'idle'
                  const isExpanded = expandedArtistId === a.id
                  const cachedScroll = scrollDataCache[a.id]
                  const hasCompleteScroll = cachedScroll?.status === 'signed'
                  const requestStatus = scrollRequestStatus[a.id]
                  const requestSent = Boolean(requestStatus?.request_sent)
                  const desc = getTileDesc(a.id)

                  return (
                    <div key={a.id} className={`scroll-tile-wrapper${isExpanded ? ' expanded unwravel' : ''}`}>
                      {isExpanded ? (
                        /* ── Expanded: inline header + panel ── */
                        <>
                          <div className="scroll-tile-expanded-header" onClick={handleCollapseExpanded}>
                            <div className="scroll-tile-avatar">
                              <AvatarOrInitial url={a.avatar_url} initial={(a.name?.[0] ?? '?').toUpperCase()} size={32} />
                            </div>
                            <span className="scroll-tile-expanded-header-name">{a.name}</span>
                            <div style={{ display: 'flex', gap: '0.35rem', marginLeft: 'auto', alignItems: 'center' }}>
                              {/* Toggle view/edit */}
                              <button
                                className={`scroll-tile-action-btn${expandMode === 'view' ? ' active' : ''}`}
                                style={{ padding: '0.3rem 0.5rem', flex: 'none', borderRadius: '5px' }}
                                onClick={e => { e.stopPropagation(); setExpandMode('view') }}
                                title="View scroll"
                              >
                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
                                </svg>
                              </button>
                              {a.id === user?.id && (
                                <button
                                  className={`scroll-tile-action-btn${expandMode === 'edit' ? ' active' : ''}`}
                                  style={{ padding: '0.3rem 0.5rem', flex: 'none', borderRadius: '5px' }}
                                  onClick={e => { e.stopPropagation(); setExpandMode('edit') }}
                                  title="Edit / fill scroll"
                                >
                                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                  </svg>
                                </button>
                              )}
                              <button className="scroll-tile-expanded-close" onClick={e => { e.stopPropagation(); handleCollapseExpanded() }} title="Close">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                                </svg>
                              </button>
                            </div>
                          </div>
                          <div className="scroll-tile-expanded-panel">
                            {expandLoading ? (
                              <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--muted)', fontSize: '0.8rem' }}>Loading scroll...</div>
                            ) : isFounderOrAdmin && !cachedScroll ? (
                              renderEmptyArtistScrollReview(a)
                            ) : expandMode === 'view' ? (
                              cachedScroll ? (
                                <ArtistScrollDetail
                                  scrollData={cachedScroll}
                                  onBack={handleCollapseExpanded}
                                />
                              ) : (
                                <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--muted)', fontSize: '0.82rem' }}>
                                  No completed scroll on file for this artist.
                                </div>
                              )
                            ) : (
                              <Suspense fallback={<div style={{ padding: '2rem', textAlign: 'center', color: 'var(--muted)', fontSize: '0.8rem' }}>Loading form...</div>}>
                                <ArtistScrollForm artistName={a.name} onSubmitted={async () => {
                                  try {
                                    const resp = await getUserScroll(a.id)
                                    setScrollDataCache(prev => ({ ...prev, [a.id]: resp.scroll ?? null }))
                                  } catch { /* silent */ }
                                  setExpandedArtistId(null)
                                }} />
                              </Suspense>
                            )}
                          </div>
                        </>
                      ) : (
                        /* ── Collapsed: standard tombstone ── */
                        <>
                          <button
                            className="scroll-tile"
                            onClick={() => handleExpandTile(a.id, 'view')}
                          >
                            <div className="scroll-tile-avatar">
                              <AvatarOrInitial
                                url={a.avatar_url}
                                initial={(a.name?.[0] ?? '?').toUpperCase()}
                                size={38}
                              />
                            </div>
                            <div className="scroll-tile-name">{a.name}</div>
                            <div className="scroll-tile-desc">{desc}</div>
                          </button>

                          {/* Action bar */}
                          <div className="scroll-tile-actions">
                            {hasCompleteScroll ? (
                              /* Scroll complete — show eye + pen */
                              <>
                                <button
                                  className="scroll-tile-action-btn"
                                  onClick={e => { e.stopPropagation(); handleExpandTile(a.id, 'view') }}
                                  title="View scroll"
                                >
                                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
                                  </svg>
                                </button>
                                {a.id === user?.id && (
                                  <button
                                    className="scroll-tile-action-btn"
                                    onClick={e => { e.stopPropagation(); handleExpandTile(a.id, 'edit') }}
                                    title="Edit scroll"
                                  >
                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                    </svg>
                                  </button>
                                )}
                              </>
                            ) : isFounderOrAdmin ? (
                              /* Scroll incomplete + founder/admin: send once, then remind daily until complete */
                              requestSent ? (
                                <>
                                  <button
                                    className={`scroll-tile-action-btn${remState === 'sent' ? ' sent' : remState === 'error' ? ' error' : ''}`}
                                    onClick={e => handleScrollRequest(a.id, 'remind', e)}
                                    disabled={remState === 'loading'}
                                    title="Send scroll reminder notification"
                                  >
                                    {remState === 'sent' ? (
                                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                                    ) : remState === 'loading' ? (
                                      <span style={{ fontSize: '0.58rem' }}>...</span>
                                    ) : (
                                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>
                                      </svg>
                                    )}
                                  </button>
                                </>
                              ) : (
                                <>
                                <button
                                  className={`scroll-tile-action-btn${reqState === 'sent' ? ' sent' : reqState === 'error' ? ' error' : ''}`}
                                  onClick={e => handleScrollRequest(a.id, 'request', e)}
                                  disabled={reqState === 'loading'}
                                  title="Send scroll request notification"
                                >
                                  {reqState === 'sent' ? (
                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                                  ) : reqState === 'loading' ? (
                                    <span style={{ fontSize: '0.58rem' }}>...</span>
                                  ) : (
                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                      <path d="M22 2 11 13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
                                    </svg>
                                  )}
                                </button>
                                </>
                              )
                            ) : (
                              /* Artist viewing own scroll tile (no actions) */
                              <button
                                className="scroll-tile-action-btn"
                                onClick={e => { e.stopPropagation(); handleExpandTile(a.id, 'edit') }}
                                title="Fill out your scroll"
                              >
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                                </svg>
                              </button>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  )
                })}

                {/* Always-present add tile for Artist tab */}
                <button
                  className="scroll-tile scroll-tile-add"
                  onClick={() => setAddPanelSlug(addPanelSlug === 'artist' ? null : 'artist')}
                  title="Add a scroll document"
                >
                  <div className="scroll-tile-add-icon">+</div>
                  <div className="scroll-tile-name" style={{ opacity: 0.7 }}>Add Scroll</div>
                  <div className="scroll-tile-meta">Upload</div>
                </button>
              </div>

              {/* Inline add panel for artist tab */}
              {addPanelSlug === 'artist' && (
                <AddScrollInlinePanel
                  categorySlug="artist"
                  aiDigestEnabled={false}
                  onUploaded={doc => handleDocUploaded('artist', doc)}
                  onClose={() => setAddPanelSlug(null)}
                />
              )}
          </>
        </>
      )}

      {/* ── Upkeep tab ── */}
      {activeSlug === 'upkeep' && (
        <div style={{ padding: '0.25rem 0' }}>
          <UpkeepSection />
        </div>
      )}

      {/* ── Waivers tab ── */}
      {activeSlug === 'waivers' && (
        <div style={{ padding: '0.25rem 0' }}>
          <WaiversSection />
        </div>
      )}

      {/* ── Templates tab ── */}
      {activeSlug === 'templates' && <TemplatesSection />}

      {/* ── Quest Log tab ── */}
      {activeSlug === 'quests' && <QuestLogSection />}

      {/* ── Client records: reuse the canonical archive contract ── */}
      {activeSlug === 'client-records' && <ArchiveTab />}

      {/* ── All other tabs (shop, custom) ── */}
      {activeSlug !== 'artist' && activeSlug !== 'upkeep' && activeSlug !== 'waivers' && activeSlug !== 'templates' && activeSlug !== 'quests' && activeSlug !== 'client-records' && (
        <div>
          {/* Tab description */}
          {activeCat?.description && (
            <div style={{ fontSize: '0.72rem', color: 'var(--muted)', lineHeight: 1.6, marginBottom: '0.85rem' }}>
              {activeCat.description}
            </div>
          )}

          {docsLoading ? (
            <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--muted)', fontSize: '0.78rem' }}>
              Loading...
            </div>
          ) : (
            <>
              {/* Scroll tile grid */}
              <div className="scroll-tile-grid">
                {/* Uploaded doc tiles */}
                {(docsBySlug[activeSlug] ?? []).map(doc => (
                  <ScrollDocTile
                    key={doc.id}
                    doc={doc}
                    onDelete={isOwnerArchive ? undefined : () => handleDeleteDoc(activeSlug, doc.id)}
                  />
                ))}

                {!isOwnerArchive && (
                  <button
                    className="scroll-tile scroll-tile-add"
                    onClick={() => setAddPanelSlug(addPanelSlug === activeSlug ? null : activeSlug)}
                    title="Upload a new scroll"
                  >
                    <div className="scroll-tile-add-icon">+</div>
                    <div className="scroll-tile-name" style={{ opacity: 0.7 }}>Add Scroll</div>
                    <div className="scroll-tile-meta">Upload</div>
                  </button>
                )}
              </div>

              {/* Inline Gala AI upload panel */}
              {!isOwnerArchive && addPanelSlug === activeSlug && (
                <AddScrollInlinePanel
                  categorySlug={activeSlug}
                  aiDigestEnabled={activeCat?.ai_digest_enabled ?? false}
                  onUploaded={doc => handleDocUploaded(activeSlug, doc)}
                  onClose={() => setAddPanelSlug(null)}
                />
              )}

              {/* Empty state hint */}
              {(docsBySlug[activeSlug] ?? []).length === 0 && addPanelSlug !== activeSlug && (
                <div style={{ textAlign: 'center', padding: '1.25rem 0', fontSize: '0.73rem', color: 'var(--muted)', opacity: 0.55 }}>
                  {isOwnerArchive ? 'No artist submissions have been filed yet.' : 'No scrolls yet — tap the + tile to upload one.'}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Add-tab wizard ── */}
      {showWizard && (
        <AddScrollTabWizard
          onClose={() => setShowWizard(false)}
          onCreated={handleTabCreated}
          isFounderOrAdmin={isFounderOrAdmin}
        />
      )}
    </div>
  )
}
