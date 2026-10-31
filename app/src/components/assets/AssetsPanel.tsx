/**
 * AssetsPanel — Brand Asset Management Hub (replaces Courier tab in Axiom).
 *
 * Sub-tabs:
 *   Email   — Combined template manager + send log (Stats|Edit|Send|Remove per template)
 *   Fonts   — Preview and manage typefaces across the app
 *   Colors  — Live CSS token editor (color pickers → immediate DOM update)
 *   Library — Asset gallery (icons, logos, banners; localStorage-backed)
 *   CSS     — Global CSS injection editor for live style changes
 */
import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react'
import { users as usersApi, listEmailLog, getEmailStats, retryEmailLog, type User, type EmailLogRow, type EmailStatsResponse } from '../../lib/api'
import { useToast } from '../Toast'
import { useStudioSettings } from '../../hooks/useStudioSettings'
import '../../styles/assets-panel.css'

const EmailTemplateEditor = lazy(() => import('../EmailTemplateEditor'))
const SmsTemplateEditor = lazy(() => import('../SmsTemplateEditor'))

type AssetsTab = 'email' | 'sms' | 'fonts' | 'colors' | 'library' | 'css'

import { API_BASE } from '../../lib/api'

interface TemplateVar { key: string; label: string; sample: string }
interface EmailTemplate {
  id: string; slug: string; name: string; subject: string; category: string
  html_body: string; variables: TemplateVar[]; is_active: boolean
  description: string | null; created_at: string; updated_at: string
}

interface Recipient { name: string; email: string; role?: string }

const CATEGORY_COLORS: Record<string, string> = {
  transactional: '#6dbf7e', notification: 'var(--gold)',
  onboarding: '#7ba0d4', 'post-session': '#c49a6c',
}

const STATUS_DOT: Record<string, string> = {
  queued: '#888', sent: '#6dbf7e', failed: '#e05c5c',
  delivered: '#6dbf7e', bounced: '#e05c5c', opened: '#faae15', clicked: '#faae15',
}

const STATUS_COLOR: Record<string, string> = {
  queued: 'var(--muted)', sent: '#6dbf7e', failed: '#e05c5c',
  delivered: '#6dbf7e', bounced: '#e05c5c', opened: 'var(--gold)', clicked: 'var(--gold)',
}

const COLOR_SAVE_KEY = 'ryozan_color_overrides'
// ── CSS token groups for Colors tab ──────────────────────────────────────
const COLOR_GROUPS = [
  {
    label: 'Brand',
    vars: [
      { name: '--gold', label: 'Gold' },
      { name: '--gold-vibrant', label: 'Gold Vibrant' },
      { name: '--tiger-orange', label: 'Tiger Orange' },
    ],
  },
  {
    label: 'Backgrounds',
    vars: [
      { name: '--bg', label: 'App Background' },
      { name: '--panel', label: 'Panel' },
    ],
  },
  {
    label: 'Text',
    vars: [
      { name: '--text', label: 'Primary Text' },
      { name: '--text-secondary', label: 'Secondary Text' },
      { name: '--text-muted', label: 'Muted Text' },
      { name: '--muted', label: 'Muted Alt' },
    ],
  },
  {
    label: 'Borders',
    vars: [
      { name: '--panel-border', label: 'Panel Border' },
      { name: '--border-subtle', label: 'Subtle Border' },
      { name: '--border-active', label: 'Active Border' },
    ],
  },
  {
    label: 'Status',
    vars: [
      { name: '--ok', label: 'Success' },
      { name: '--pending', label: 'Warning' },
      { name: '--danger', label: 'Danger' },
    ],
  },
]

// ── Font definitions ──────────────────────────────────────────────────────
const FONTS = [
  { var: '--font-heading', label: 'Heading', stack: "'KC Tallboy', 'Oswald', sans-serif", preview: 'Ryozan Studio' },
  { var: '--font-display', label: 'Display', stack: "'Oswald', sans-serif", preview: 'CUSTOM INK · FINE ART' },
  { var: '--font-body', label: 'Body', stack: "'Inter', sans-serif", preview: 'Book your next session with us' },
  { var: '--font-accent', label: 'Accent', stack: "'KC SimpleSans', sans-serif", preview: 'Flash Available Now' },
  { var: '--font-mono', label: 'Mono', stack: "'IBM Plex Mono', monospace", preview: '{{client_name}} · BKG-XXXXXX' },
  { var: '--font-btn', label: 'Button', stack: "'JetBrains Mono', monospace", preview: 'BOOK NOW · VIEW FLASH' },
  // Title previews the studio owner's name (titlePreview in AssetsPanel).
  { var: '--font-title', label: 'Title', stack: "'Inter', sans-serif", preview: '' },
]

// ── CSS snippets ──────────────────────────────────────────────────────────
const CSS_SNIPPETS = [
  { label: 'Dark mode override', hint: 'Force dark on all surfaces', css: ':root { --bg: #0a0a0a; --panel: rgba(255,255,255,0.03); }' },
  { label: 'Gold accent boost', hint: 'Brighter gold tones', css: ':root { --gold: #c49a43; --gold-vibrant: #d4a84b; }' },
  { label: 'Tight typography', hint: 'Reduce heading letter spacing', css: 'h2, h3 { letter-spacing: 0.04em; }' },
  { label: 'Rounded cards', hint: 'Increase card border radius', css: ':root { --radius-md: 12px; --radius-lg: 20px; }' },
]

interface LibraryAsset { id: string; name: string; category: string; url: string; createdAt: number }
const LIBRARY_KEY = 'ryozan_asset_library'
const LIBRARY_CATS = ['all', 'logos', 'icons', 'banners', 'art', 'stickers']

function loadLibrary(): LibraryAsset[] {
  try { return JSON.parse(localStorage.getItem(LIBRARY_KEY) ?? '[]') } catch { return [] }
}
function saveLibrary(assets: LibraryAsset[]) {
  localStorage.setItem(LIBRARY_KEY, JSON.stringify(assets))
}

interface Props { token: string }

export default function AssetsPanel({ token }: Props) {
  const toast = useToast()
  const { settings: studioSettings } = useStudioSettings()
  const titlePreview = (studioSettings.owner_name || 'Artist Name').toUpperCase()
  const [activeTab, setActiveTab] = useState<AssetsTab>('email')

  // Email tab state
  const [templates, setTemplates] = useState<EmailTemplate[]>([])
  const [templatesLoading, setTemplatesLoading] = useState(true)
  const [editingSlug, setEditingSlug] = useState<string | null>(null)
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null)
  const [removingSlug, setRemovingSlug] = useState<string | null>(null)
  const [emailLog, setEmailLog] = useState<EmailLogRow[]>([])
  const [emailStats, setEmailStats] = useState<EmailStatsResponse | null>(null)
  const [emailLoading, setEmailLoading] = useState(false)
  const [emailStatusFilter, setEmailStatusFilter] = useState('')
  const [emailSearch, setEmailSearch] = useState('')
  const [emailRetrying, setEmailRetrying] = useState<string | null>(null)

  // Send modal state
  const [sendSlug, setSendSlug] = useState<string | null>(null)
  const [sendSearch, setSendSearch] = useState('')
  const [sendSuggestions, setSendSuggestions] = useState<User[]>([])
  const [sendRecipients, setSendRecipients] = useState<Recipient[]>([])
  const [sending, setSending] = useState(false)
  const [allUsers, setAllUsers] = useState<User[]>([])

  // Stats modal state
  const [statsSlug, setStatsSlug] = useState<string | null>(null)

  // Colors tab state
  const [colorValues, setColorValues] = useState<Record<string, string>>({})
  const [savedColors, setSavedColors] = useState<Record<string, string>>({})
  // Library tab state
  const [library, setLibrary] = useState<LibraryAsset[]>(loadLibrary)
  const [libCat, setLibCat] = useState('all')
  const libInputRef = useRef<HTMLInputElement>(null)

  // CSS tab state
  const [customCss, setCustomCss] = useState('')
  const [cssStatus, setCssStatus] = useState<{ msg: string; ok: boolean } | null>(null)
  const cssStyleRef = useRef<HTMLStyleElement | null>(null)

  // ── Load templates ──────────────────────────────────────────────────────
  const loadTemplates = useCallback(async () => {
    setTemplatesLoading(true)
    try {
      const res = await fetch(`${API_BASE}/email-templates/`, { headers: { Authorization: `Bearer ${token}` } })
      if (!res.ok) throw new Error()
      setTemplates(await res.json())
    } catch { /* silent */ }
    finally { setTemplatesLoading(false) }
  }, [token])

  // ── Load email log ──────────────────────────────────────────────────────
  const loadEmailLog = useCallback(async () => {
    setEmailLoading(true)
    try {
      const [log, stats] = await Promise.all([
        listEmailLog({ status: emailStatusFilter || undefined, search: emailSearch || undefined, limit: 100 }),
        getEmailStats(30),
      ])
      setEmailLog(log.items)
      setEmailStats(stats)
    } catch { /* silent */ }
    finally { setEmailLoading(false) }
  }, [emailStatusFilter, emailSearch])

  useEffect(() => { if (activeTab === 'email') { loadTemplates(); loadEmailLog() } }, [activeTab]) // eslint-disable-line

  useEffect(() => { if (activeTab === 'email') loadEmailLog() }, [emailStatusFilter, emailSearch]) // eslint-disable-line

  // ── Load users for send autocomplete ────────────────────────────────────
  useEffect(() => {
    if (!sendSlug) return
    usersApi.list({ limit: 500 }).then(setAllUsers).catch(() => {})
  }, [sendSlug])

  // ── Send search filter ───────────────────────────────────────────────────
  useEffect(() => {
    if (!sendSearch.trim()) { setSendSuggestions([]); return }
    const q = sendSearch.toLowerCase()
    setSendSuggestions(
      allUsers.filter(u =>
        (u.name?.toLowerCase().includes(q) || u.email?.toLowerCase().includes(q)) &&
        !sendRecipients.find(r => r.email === u.email)
      ).slice(0, 8)
    )
  }, [sendSearch, allUsers, sendRecipients])

  // ── Color init ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (activeTab !== 'colors') return
    const style = getComputedStyle(document.documentElement)
    const vals: Record<string, string> = {}
    for (const g of COLOR_GROUPS)
      for (const v of g.vars)
        vals[v.name] = style.getPropertyValue(v.name).trim()
    // Apply and merge any saved overrides
    const saved: Record<string, string> = JSON.parse(localStorage.getItem(COLOR_SAVE_KEY) ?? '{}')
    for (const [k, val] of Object.entries(saved)) {
      document.documentElement.style.setProperty(k, val)
      vals[k] = val
    }
    setSavedColors(saved)
    setColorValues(vals)
  }, [activeTab])

  // ── Helpers ───────────────────────────────────────────────────────────────
  const toggleActive = async (t: EmailTemplate) => {
    try {
      const res = await fetch(`${API_BASE}/email-templates/${t.slug}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !t.is_active }),
      })
      if (!res.ok) throw new Error()
      const updated = await res.json()
      setTemplates(prev => prev.map(x => x.slug === t.slug ? updated : x))
    } catch { toast.error('Toggle failed') }
  }

  const removeTemplate = async (slug: string) => {
    setRemovingSlug(slug)
    try {
      const res = await fetch(`${API_BASE}/email-templates/${slug}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: false }),
      })
      if (!res.ok) throw new Error()
      setTemplates(prev => prev.map(t => t.slug === slug ? { ...t, is_active: false } : t))
      toast.success('Template deactivated')
    } catch { toast.error('Remove failed') }
    finally { setRemovingSlug(null); setConfirmRemove(null) }
  }

  const sendTemplate = async () => {
    if (!sendSlug || sendRecipients.length === 0) return
    setSending(true)
    try {
      const res = await fetch(`${API_BASE}/email-templates/${sendSlug}/send`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ recipients: sendRecipients, variables: {} }),
      })
      const data = await res.json()
      toast.success(`Sent to ${data.sent}/${sendRecipients.length} recipients`)
      setSendSlug(null); setSendRecipients([]); setSendSearch('')
      loadEmailLog()
    } catch { toast.error('Send failed') }
    finally { setSending(false) }
  }

  const addRecipient = (r: Recipient) => {
    if (!sendRecipients.find(x => x.email === r.email))
      setSendRecipients(prev => [...prev, r])
    setSendSearch(''); setSendSuggestions([])
  }

  const addAudience = (role: string) => {
    const matching = allUsers
      .filter(u => role === 'all' || u.role === role)
      .map(u => ({ email: u.email!, name: u.name ?? u.email!, role: u.role }))
      .filter(u => !sendRecipients.find(r => r.email === u.email))
    setSendRecipients(prev => [...prev, ...matching])
  }

  const retryEmail = async (id: string) => {
    setEmailRetrying(id)
    try {
      const res = await retryEmailLog(id)
      toast[res.success ? 'success' : 'error'](res.message)
      loadEmailLog()
    } catch { toast.error('Retry failed') }
    finally { setEmailRetrying(null) }
  }

  const applyColor = (varName: string, value: string) => {
    document.documentElement.style.setProperty(varName, value)
    setColorValues(prev => ({ ...prev, [varName]: value }))
  }

  const pickColor = async (varName: string) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const EyeDropper = (window as any).EyeDropper
    if (!EyeDropper) { toast.error('EyeDropper not supported in this browser'); return }
    try {
      const { sRGBHex } = await new EyeDropper().open()
      applyColor(varName, sRGBHex)
    } catch { /* user cancelled */ }
  }

  const saveColors = () => {
    localStorage.setItem(COLOR_SAVE_KEY, JSON.stringify(colorValues))
    setSavedColors({ ...colorValues })
    toast.success('Color overrides saved')
  }
  const applyCSS = () => {
    if (!cssStyleRef.current) {
      const el = document.createElement('style')
      el.id = 'ryozan-custom-css'
      document.head.appendChild(el)
      cssStyleRef.current = el
    }
    try {
      cssStyleRef.current.textContent = customCss
      setCssStatus({ msg: 'Applied', ok: true })
    } catch (e) {
      setCssStatus({ msg: String(e), ok: false })
    }
  }

  const resetCSS = () => {
    if (cssStyleRef.current) cssStyleRef.current.textContent = ''
    setCustomCss('')
    setCssStatus(null)
  }

  const uploadAsset = (files: FileList | null) => {
    if (!files) return
    Array.from(files).forEach(file => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const url = e.target?.result as string
        const asset: LibraryAsset = {
          id: crypto.randomUUID(), name: file.name,
          category: 'art', url, createdAt: Date.now(),
        }
        setLibrary(prev => { const next = [asset, ...prev]; saveLibrary(next); return next })
      }
      reader.readAsDataURL(file)
    })
  }

  const removeAsset = (id: string) => {
    setLibrary(prev => { const next = prev.filter(a => a.id !== id); saveLibrary(next); return next })
  }

  // ── Stats data ───────────────────────────────────────────────────────────
  const getTemplateStats = (slug: string) => {
    const rows = emailLog.filter(r => r.template === slug)
    const sent = rows.filter(r => ['sent', 'delivered', 'opened', 'clicked'].includes(r.status)).length
    const failed = rows.filter(r => ['failed', 'bounced'].includes(r.status)).length
    const opened = rows.filter(r => ['opened', 'clicked'].includes(r.status)).length
    return { total: rows.length, sent, failed, opened }
  }

  // ── Grouped templates ────────────────────────────────────────────────────
  const grouped = templates.reduce<Record<string, EmailTemplate[]>>((acc, t) => {
    const cat = t.category || 'other'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(t)
    return acc
  }, {})

  const filteredLib = library.filter(a => libCat === 'all' || a.category === libCat)

  // ── If editing a template, show the full 3-panel editor ─────────────────
  if (editingSlug) {
    return (
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <Suspense fallback={<div style={{ padding: '2rem', color: 'var(--text-muted)', textAlign: 'center' }}>Loading editor...</div>}>
          <EmailTemplateEditor
            token={token}
            initialSlug={editingSlug}
            onClose={() => { setEditingSlug(null); loadTemplates() }}
          />
        </Suspense>
      </div>
    )
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* Sub-tab navigation */}
      <nav className="assets-nav">
        {(['email', 'sms', 'fonts', 'colors', 'library', 'css'] as AssetsTab[]).map(t => (
          <button
            key={t}
            className={`assets-tab${activeTab === t ? ' active' : ''}`}
            onClick={() => setActiveTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>

      {/* ── SMS TAB ──────────────────────────────────────────────────────── */}
      {activeTab === 'sms' && (
        <Suspense fallback={<div style={{ padding: '2rem', color: 'var(--text-muted)', textAlign: 'center' }}>Loading SMS toolkit…</div>}>
          <SmsTemplateEditor token={token} />
        </Suspense>
      )}

      {/* ── EMAIL TAB ────────────────────────────────────────────────────── */}
      {activeTab === 'email' && (
        <div className="assets-content">

          {/* Template list */}
          <div className="assets-section-head">
            <span className="assets-section-title">Templates ({templates.length})</span>
            <button className="assets-section-action" onClick={loadTemplates}>Refresh</button>
          </div>

          {templatesLoading
            ? <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem', padding: '1rem 0' }}>Loading...</div>
            : Object.entries(grouped).map(([cat, items]) => (
              <div key={cat} style={{ marginBottom: '12px' }}>
                <div style={{ fontSize: '0.55rem', color: CATEGORY_COLORS[cat] || 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', fontFamily: 'var(--font-mono)', fontWeight: 700, marginBottom: '5px' }}>
                  {cat}
                </div>
                {items.map(t => (
                  <div key={t.slug}>
                    {confirmRemove === t.slug ? (
                      <div className="tpl-confirm">
                        <span className="tpl-confirm-text">Remove "{t.name}"?</span>
                        <button className="tpl-confirm-yes" onClick={() => removeTemplate(t.slug)} disabled={removingSlug === t.slug}>
                          {removingSlug === t.slug ? '...' : 'Yes, Remove'}
                        </button>
                        <button className="tpl-confirm-no" onClick={() => setConfirmRemove(null)}>Cancel</button>
                      </div>
                    ) : (
                      <div className="tpl-card" style={{ opacity: t.is_active ? 1 : 0.45 }}>
                        <button
                          className="tpl-dot"
                          style={{ background: t.is_active ? '#6dbf7e' : '#555' }}
                          title={t.is_active ? 'Click to deactivate' : 'Click to activate'}
                          onClick={() => toggleActive(t)}
                        />
                        <div className="tpl-info">
                          <div className="tpl-name">{t.name}</div>
                          <div className="tpl-subject">{t.subject}</div>
                        </div>
                        <div className="tpl-actions">
                          <button className="tpl-btn tpl-btn--stats" onClick={() => setStatsSlug(t.slug)}>Stats</button>
                          <button className="tpl-btn" onClick={() => setEditingSlug(t.slug)}>Edit</button>
                          <button className="tpl-btn tpl-btn--send" onClick={() => { setSendSlug(t.slug); setSendRecipients([]) }}>Send</button>
                          <button className="tpl-btn tpl-btn--remove" onClick={() => setConfirmRemove(t.slug)}>✕</button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))
          }

          {/* Email log divider */}
          <div className="assets-divider">
            <div className="assets-divider-line" />
            <span className="assets-divider-label">Send Log</span>
            <div className="assets-divider-line" />
          </div>

          {/* Stats bar */}
          {emailStats && (
            <div className="email-stats-bar">
              {[
                { label: 'Sent (30d)', value: emailStats.total_sent, color: '#6dbf7e' },
                { label: 'Failed (30d)', value: emailStats.total_failed, color: '#e05c5c' },
                { label: 'Total in log', value: emailLog.length, color: 'var(--gold)' },
              ].map(s => (
                <div key={s.label} className="email-stat-card">
                  <div className="email-stat-value" style={{ color: s.color }}>{s.value}</div>
                  <div className="email-stat-label">{s.label}</div>
                </div>
              ))}
            </div>
          )}

          {/* Filters */}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '10px' }}>
            <select className="form-input" style={{ flex: '0 0 120px', fontSize: '0.72rem', padding: '0.3rem 0.55rem' }}
              value={emailStatusFilter} onChange={e => setEmailStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              {['queued', 'sent', 'failed', 'delivered', 'bounced', 'opened', 'clicked'].map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <input className="form-input" style={{ flex: 1, fontSize: '0.72rem', padding: '0.3rem 0.55rem' }}
              placeholder="Search recipient..." value={emailSearch} onChange={e => setEmailSearch(e.target.value)} />
          </div>

          {/* Log rows */}
          {emailLoading
            ? <div style={{ color: 'var(--text-muted)', fontSize: '0.72rem', padding: '1rem 0' }}>Loading log...</div>
            : emailLog.length === 0
              ? <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)', fontSize: '0.78rem' }}>No emails logged yet</div>
              : emailLog.map(row => (
                <div key={row.id} className="email-log-row">
                  <div style={{ width: 8, height: 8, borderRadius: '50%', marginTop: '0.35rem', background: STATUS_DOT[row.status] ?? '#888' }} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '200px' }}>{row.to_email}</span>
                      <span style={{ fontSize: '0.55rem', padding: '1px 6px', borderRadius: '999px', background: 'rgba(255,255,255,0.06)', color: STATUS_COLOR[row.status] ?? 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{row.status}</span>
                      <span style={{ fontSize: '0.58rem', color: 'var(--gold)' }}>{row.template}</span>
                    </div>
                    <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.subject}</div>
                    {row.error && <div style={{ fontSize: '0.62rem', color: '#e05c5c', marginTop: '2px' }}>{row.error}</div>}
                    <div style={{ fontSize: '0.55rem', color: 'var(--text-muted)', opacity: 0.5, marginTop: '2px' }}>
                      {new Date(row.created_at).toLocaleString()}
                      {row.retry_count > 0 && ` · ${row.retry_count} retry${row.retry_count > 1 ? 's' : ''}`}
                    </div>
                  </div>
                  {(row.status === 'failed' || row.status === 'bounced') && (
                    <button className="action-btn" style={{ fontSize: '0.6rem', padding: '0.2rem 0.5rem', marginTop: '2px' }}
                      disabled={emailRetrying === row.id} onClick={() => retryEmail(row.id)}>
                      {emailRetrying === row.id ? '...' : 'Retry'}
                    </button>
                  )}
                </div>
              ))
          }
        </div>
      )}

      {/* ── FONTS TAB ────────────────────────────────────────────────────── */}
      {activeTab === 'fonts' && (
        <div className="assets-content">
          <div className="assets-section-head">
            <span className="assets-section-title">Typefaces</span>
          </div>
          {FONTS.map(f => (
            <div key={f.var} className="font-card">
              <div className="font-card-head">
                <span className="font-card-var">{f.var}</span>
                <span className="font-card-stack">{f.label}</span>
              </div>
              <div className="font-preview" style={{ fontFamily: f.stack }}>{f.var === '--font-title' ? titlePreview : f.preview}</div>
              <div style={{ marginTop: '6px', display: 'flex', gap: '6px' }}>
                {['0.6rem', '0.8rem', '1rem', '1.3rem', '1.8rem'].map(size => (
                  <span key={size} style={{ fontFamily: f.stack, fontSize: size, color: 'var(--text-muted)' }}>Aa</span>
                ))}
              </div>
            </div>
          ))}
          <p style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginTop: '12px', lineHeight: 1.5 }}>
            Font files are loaded via @font-face in <code style={{ fontFamily: 'var(--font-mono)' }}>index.css</code>. To add or replace fonts, upload .otf/.woff2 files to <code style={{ fontFamily: 'var(--font-mono)' }}>public/assets/fonts/</code> and update the @font-face declaration.
          </p>
        </div>
      )}

      {/* ── COLORS TAB ───────────────────────────────────────────────────── */}
      {activeTab === 'colors' && (
        <div className="assets-content">
          <div className="assets-section-head">
            <span className="assets-section-title">CSS Tokens</span>
            <div style={{ display: 'flex', gap: '6px' }}>
              <button className="assets-section-action" onClick={saveColors}>Save</button>
              <button className="assets-section-action" style={{ color: 'var(--text-muted)' }} onClick={() => {
                document.documentElement.removeAttribute('style')
                localStorage.removeItem(COLOR_SAVE_KEY)
                const style = getComputedStyle(document.documentElement)
                const vals: Record<string, string> = {}
                for (const g of COLOR_GROUPS)
                  for (const v of g.vars)
                    vals[v.name] = style.getPropertyValue(v.name).trim()
                setSavedColors({})
                setColorValues(vals)
              }}>Reset</button>
            </div>
          </div>
          <p style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginBottom: '14px', lineHeight: 1.5 }}>
            Live edits apply immediately. Save persists across reloads. To lock in permanently, update <code style={{ fontFamily: 'var(--font-mono)' }}>tokens.css</code>.          </p>
          {COLOR_GROUPS.map(group => (
            <div key={group.label} className="color-group">
              <div className="color-group-title">{group.label}</div>
              {group.vars.map(v => {
                const raw = colorValues[v.name] ?? ''
                const isHex = raw.startsWith('#')
                const isDirty = raw !== savedColors[v.name]
                return (
                  <div key={v.name} className="color-row">
                    <div className="color-swatch" style={{ background: raw }} />
                    <div className="color-info">
                      <div className="color-var-name" style={{ color: raw || undefined, transition: 'color 0.2s' }}>
                        {v.name}
                        {isDirty && <span style={{ marginLeft: 5, fontSize: '0.5rem', opacity: 0.6, color: 'var(--text-muted)' }}>●</span>}
                      </div>
                      <div className="color-var-value">{raw || '—'}</div>
                    </div>
                    <button
                      title="Pick color from screen"
                      onClick={() => pickColor(v.name)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '2px 4px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', opacity: 0.7 }}
                      onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                      onMouseLeave={e => (e.currentTarget.style.opacity = '0.7')}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M2 13.5V20a2 2 0 002 2h.5"/><path d="M20.9 3.1a2.828 2.828 0 00-4 0l-9.5 9.5 4 4 9.5-9.5a2.828 2.828 0 000-4z"/><path d="M4.5 20.5 2 22"/>
                      </svg>
                    </button>
                    {isHex
                      ? <input type="color" className="color-input" value={raw || '#000000'}                          onChange={e => applyColor(v.name, e.target.value)} />
                      : <input type="text" style={{ width: 90, padding: '3px 6px', fontSize: '0.6rem', fontFamily: 'var(--font-mono)', background: 'rgba(0,0,0,0.3)', border: '1px solid var(--panel-border)', borderRadius: '4px', color: 'var(--text)' }}
                          defaultValue={raw} onBlur={e => applyColor(v.name, e.target.value)} />
                    }
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      )}

      {/* ── LIBRARY TAB ──────────────────────────────────────────────────── */}
      {activeTab === 'library' && (
        <div className="assets-content">
          <div className="assets-section-head">
            <span className="assets-section-title">Asset Library ({library.length})</span>
            <button className="assets-section-action" onClick={() => libInputRef.current?.click()}>Upload</button>
          </div>

          <input ref={libInputRef} type="file" accept="image/*" multiple style={{ display: 'none' }}
            onChange={e => uploadAsset(e.target.files)} />

          <div className="library-upload-zone" onClick={() => libInputRef.current?.click()}>
            <svg className="library-upload-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <span className="library-upload-text">Drop files or click to upload</span>
            <span className="library-upload-hint">PNG, JPG, SVG, WebP, GIF</span>
          </div>

          <div className="library-cats">
            {LIBRARY_CATS.map(cat => (
              <button key={cat} className={`library-cat-btn${libCat === cat ? ' active' : ''}`}
                onClick={() => setLibCat(cat)}>
                {cat}
              </button>
            ))}
          </div>

          {filteredLib.length === 0
            ? <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                No assets yet — upload images to build your library
              </div>
            : <div className="library-grid">
                {filteredLib.map(asset => (
                  <div key={asset.id} className="library-asset">
                    <img src={asset.url} alt={asset.name} />
                    <span className="library-asset-name">{asset.name}</span>
                    <button className="library-asset-del" onClick={() => removeAsset(asset.id)}>✕</button>
                  </div>
                ))}
              </div>
          }
        </div>
      )}

      {/* ── CSS TAB ──────────────────────────────────────────────────────── */}
      {activeTab === 'css' && (
        <div className="assets-content">
          <div className="assets-section-head">
            <span className="assets-section-title">Global CSS Editor</span>
          </div>
          <p style={{ fontSize: '0.62rem', color: 'var(--text-muted)', marginBottom: '12px', lineHeight: 1.5 }}>
            CSS injected here applies live to the entire app. Use this to prototype style changes. Copy final rules to the appropriate <code style={{ fontFamily: 'var(--font-mono)' }}>.css</code> source file to persist.
          </p>

          <div className="css-editor-wrap">
            <textarea
              className="css-editor-textarea"
              value={customCss}
              onChange={e => setCustomCss(e.target.value)}
              placeholder={`/* Live CSS editor — changes apply instantly */\n\n/* Example: tweak gold hue */\n:root { --gold: #c49a43; }\n\n/* Example: card radius */\n.dashboard-card { border-radius: 16px; }`}
              spellCheck={false}
            />
            <div className="css-editor-actions">
              <button className="css-apply-btn" onClick={applyCSS}>Apply</button>
              <button className="css-reset-btn" onClick={resetCSS}>Clear</button>
              {cssStatus && (
                <span className={`css-status css-status--${cssStatus.ok ? 'ok' : 'err'}`}>
                  {cssStatus.msg}
                </span>
              )}
            </div>

            <div style={{ marginTop: '8px' }}>
              <div className="assets-section-title" style={{ marginBottom: '8px' }}>Quick Snippets</div>
              <div className="css-snippets">
                {CSS_SNIPPETS.map(s => (
                  <button key={s.label} className="css-snippet-btn"
                    onClick={() => setCustomCss(prev => prev ? prev + '\n\n' + s.css : s.css)}>
                    <span className="css-snippet-label">{s.label}</span>
                    <span className="css-snippet-hint">{s.hint}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── SEND MODAL ───────────────────────────────────────────────────── */}
      {sendSlug && (() => {
        const tpl = templates.find(t => t.slug === sendSlug)
        return (
          <div className="assets-modal-backdrop" onClick={() => setSendSlug(null)}>
            <div className="assets-modal" onClick={e => e.stopPropagation()}>
              <div className="assets-modal-head">
                <span className="assets-modal-title">Send — {tpl?.name}</span>
                <button className="assets-modal-close" onClick={() => setSendSlug(null)}>✕</button>
              </div>
              <div className="assets-modal-body">
                <div style={{ marginBottom: '8px' }}>
                  <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Audience Presets</span>
                </div>
                <div className="send-presets">
                  {[
                    { label: 'All Users', role: 'all' },
                    { label: 'Artists', role: 'artist' },
                    { label: 'Collectors', role: 'collector' },
                    { label: 'Assistants', role: 'assistant' },
                    { label: 'Admins', role: 'admin' },
                  ].map(p => (
                    <button key={p.role} className="send-preset-btn" onClick={() => addAudience(p.role)}>
                      {p.label}
                    </button>
                  ))}
                </div>

                <div style={{ marginBottom: '6px', marginTop: '10px' }}>
                  <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Search Recipients</span>
                </div>
                <div className="send-search-wrap">
                  <input
                    className="send-search"
                    placeholder="Search by name or email..."
                    value={sendSearch}
                    onChange={e => setSendSearch(e.target.value)}
                    autoFocus
                  />
                  {sendSuggestions.length > 0 && (
                    <div className="send-suggestions">
                      {sendSuggestions.map(u => (
                        <button key={u.id} className="send-suggestion"
                          onClick={() => addRecipient({ email: u.email!, name: u.name ?? u.email!, role: u.role })}>
                          <div>
                            <div className="send-suggestion-name">{u.name ?? u.email}</div>
                            <div className="send-suggestion-email">{u.email}</div>
                          </div>
                          <span className="send-suggestion-role">{u.role}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div style={{ marginBottom: '6px' }}>
                  <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                    Selected ({sendRecipients.length})
                  </span>
                </div>
                <div className="send-chips">
                  {sendRecipients.length === 0
                    ? <span className="send-empty-chips">No recipients selected</span>
                    : sendRecipients.map(r => (
                      <span key={r.email} className="send-chip">
                        {r.name !== r.email ? r.name : r.email}
                        <button className="send-chip-remove" onClick={() => setSendRecipients(prev => prev.filter(x => x.email !== r.email))}>✕</button>
                      </span>
                    ))
                  }
                </div>
              </div>
              <div className="assets-modal-foot">
                <button className="ete-btn ete-btn--cancel" onClick={() => setSendSlug(null)}>Cancel</button>
                <button
                  className="ete-btn ete-btn--save"
                  onClick={sendTemplate}
                  disabled={sending || sendRecipients.length === 0}
                >
                  {sending ? 'Sending...' : `Send to ${sendRecipients.length}`}
                </button>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── STATS MODAL ──────────────────────────────────────────────────── */}
      {statsSlug && (() => {
        const tpl = templates.find(t => t.slug === statsSlug)
        const stats = getTemplateStats(statsSlug)
        const deliveryRate = stats.total > 0 ? Math.round((stats.sent / stats.total) * 100) : 0
        const openRate = stats.sent > 0 ? Math.round((stats.opened / stats.sent) * 100) : 0
        return (
          <div className="assets-modal-backdrop" onClick={() => setStatsSlug(null)}>
            <div className="assets-modal" style={{ maxWidth: 400 }} onClick={e => e.stopPropagation()}>
              <div className="assets-modal-head">
                <span className="assets-modal-title">Stats — {tpl?.name}</span>
                <button className="assets-modal-close" onClick={() => setStatsSlug(null)}>✕</button>
              </div>
              <div className="assets-modal-body">
                <div className="stats-grid">
                  {[
                    { label: 'Total Sends', value: stats.total, color: 'var(--text)' },
                    { label: 'Delivered', value: stats.sent, color: '#6dbf7e' },
                    { label: 'Failed', value: stats.failed, color: '#e05c5c' },
                    { label: 'Opened', value: stats.opened, color: 'var(--gold)' },
                    { label: 'Delivery Rate', value: `${deliveryRate}%`, color: '#6dbf7e' },
                    { label: 'Open Rate', value: `${openRate}%`, color: 'var(--gold)' },
                  ].map(s => (
                    <div key={s.label} className="stats-card">
                      <div className="stats-card-value" style={{ color: s.color }}>{s.value}</div>
                      <div className="stats-card-label">{s.label}</div>
                    </div>
                  ))}
                </div>
                <p style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textAlign: 'center', fontFamily: 'var(--font-mono)' }}>
                  Based on send log — last 100 entries
                </p>
              </div>
              <div className="assets-modal-foot">
                <button className="ete-btn" onClick={() => setStatsSlug(null)}>Close</button>
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
