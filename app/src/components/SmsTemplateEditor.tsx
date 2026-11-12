/**
 * SmsTemplateEditor — founder UI for editing, previewing, and test-sending
 * SMS templates and inspecting the message log.
 *
 * Three modes inside a single component:
 *   list      — table of templates, click to edit
 *   edit      — single template view (body textarea + variables + preview)
 *   log       — recent SMS messages with status + retry
 *
 * Mirrors EmailTemplateEditor structurally so the UX stays familiar.
 * Constraints kept simple: every SMS body is plain text (no HTML) and
 * we surface live segment count so the founder sees Twilio billing
 * implications while typing.
 */
import { useCallback, useEffect, useState } from 'react'

import { API_BASE } from '../lib/api'

// ── Types ────────────────────────────────────────────────

interface TemplateVar {
  key: string
  label: string
  sample: string
}

interface SmsTemplate {
  id: string
  slug: string
  name: string
  category: string
  body: string
  variables: TemplateVar[]
  is_active: boolean
  description: string | null
  owner_id: string | null
  is_shop: boolean
  is_mine: boolean
  created_at: string
  updated_at: string
}

interface SmsLogRow {
  id: string
  direction: 'outbound' | 'inbound'
  to_phone: string
  from_phone: string
  body: string
  status: string
  error_code: string | null
  error_message: string | null
  twilio_sid: string | null
  template_slug: string | null
  num_segments: number | null
  created_at: string
  delivered_at: string | null
}

interface SmsStats {
  total: number
  sent: number
  delivered: number
  failed: number
  inbound: number
  opted_in_users: number
  opted_out_users: number
  scope?: 'shop' | 'personal'
}

interface PreviewResult {
  body: string
  length: number
  segments: number
}

// ── API helpers ────────────────────────────────────────────

async function api<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const fetchTemplates = (token: string) => api<SmsTemplate[]>(token, '/sms-templates/')
const fetchOne = (token: string, slug: string) => api<SmsTemplate>(token, `/sms-templates/${slug}`)
const patchTemplate = (
  token: string,
  slug: string,
  data: Partial<Pick<SmsTemplate, 'name' | 'body' | 'category' | 'is_active' | 'description'>>,
) =>
  api<SmsTemplate>(token, `/sms-templates/${slug}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
const previewTemplate = (token: string, body: string, sampleData: Record<string, string>) =>
  api<PreviewResult>(token, '/sms-templates/preview', {
    method: 'POST',
    body: JSON.stringify({ body, sample_data: sampleData }),
  })
const seedTemplates = (token: string) =>
  api<{ created: number; updated: number; total: number }>(token, '/sms-templates/seed', {
    method: 'POST',
  })
const createTemplate = (token: string, data: { slug: string; name: string; body: string; category: string; description?: string }) =>
  api<SmsTemplate>(token, '/sms-templates/', {
    method: 'POST',
    body: JSON.stringify({ ...data, variables: [] }),
  })
const deleteTemplate = (token: string, slug: string) =>
  api<void>(token, `/sms-templates/${slug}`, { method: 'DELETE' })
const sendTestSms = (token: string, slug: string, to: string, variables: Record<string, string>) =>
  api<{ sent: number; failed: number; results: Array<{ phone: string; sent: boolean; status: string; error: string | null }> }>(
    token,
    `/sms-templates/${slug}/send`,
    {
      method: 'POST',
      body: JSON.stringify({ recipients: [{ phone: to, name: variables.client_name || '' }], variables }),
    },
  )
const fetchLog = (token: string, opts: { direction?: string; status?: string; phone?: string }) => {
  const q = new URLSearchParams()
  if (opts.direction) q.set('direction', opts.direction)
  if (opts.status) q.set('status', opts.status)
  if (opts.phone) q.set('phone', opts.phone)
  q.set('limit', '50')
  return api<SmsLogRow[]>(token, `/sms/log?${q.toString()}`)
}
const fetchStats = (token: string) => api<SmsStats>(token, '/sms/stats')
const sendManual = (token: string, to: string, body: string) =>
  api<{ status: string; twilio_sid: string | null; error_code: string | null; error_message: string | null; segments: number | null }>(
    token,
    '/sms/send',
    { method: 'POST', body: JSON.stringify({ to, body, is_transactional: true }) },
  )

// ── Constants ────────────────────────────────────────────

const CATEGORY_COLORS: Record<string, string> = {
  compliance: '#e0594b',
  transactional: '#6dbf7e',
  notification: 'var(--gold)',
  marketing: '#7ba0d4',
}

const CATEGORY_LABELS: Record<string, string> = {
  compliance: 'Compliance auto replies',
  transactional: 'Booking auto replies',
  notification: 'Reply notifications',
  marketing: 'Marketing',
}

const AUTO_REPLY_CATEGORIES = new Set(['compliance', 'transactional', 'notification'])

const STATUS_COLORS: Record<string, string> = {
  delivered: '#6dbf7e',
  sent: '#a0d4a8',
  queued: '#b9ad96',
  sending: '#b9ad96',
  received: '#7ba0d4',
  failed: '#e0594b',
  undelivered: '#e0594b',
}

type TemplateFocus = 'all' | 'auto' | 'marketing'

function isAutoReplyTemplate(template: SmsTemplate) {
  return AUTO_REPLY_CATEGORIES.has(template.category)
}

// ── Component ────────────────────────────────────────────

interface Props {
  token: string
  onClose?: () => void
}

type View = 'list' | 'edit' | 'log' | 'manual' | 'create'

export default function SmsTemplateEditor({ token, onClose }: Props) {
  const [view, setView] = useState<View>('list')
  const [templates, setTemplates] = useState<SmsTemplate[]>([])
  const [stats, setStats] = useState<SmsStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingSlug, setEditingSlug] = useState<string | null>(null)
  const [seedBusy, setSeedBusy] = useState(false)
  const [templateFocus, setTemplateFocus] = useState<TemplateFocus>('auto')

  // Fetch only; callers that reload reset loading and error first.
  const requestAll = useCallback(() => Promise.all([fetchTemplates(token), fetchStats(token).catch(() => null)])
    .then(([tpls, st]) => {
      setTemplates(tpls)
      setStats(st)
    })
    .catch((e) => setError(e instanceof Error ? e.message : String(e)))
    .finally(() => setLoading(false)), [token])

  const loadAll = () => {
    setLoading(true)
    setError(null)
    return requestAll()
  }

  // A new token starts a fresh load: reset during render, fetch in the effect.
  const [loadedToken, setLoadedToken] = useState(token)
  if (loadedToken !== token) {
    setLoadedToken(token)
    setLoading(true)
    setError(null)
  }

  useEffect(() => { requestAll() }, [requestAll])

  async function handleSeed() {
    setSeedBusy(true)
    try {
      const res = await seedTemplates(token)
      alert(`Seeded ${res.total} templates (${res.created} new, ${res.updated} updated)`)
      await loadAll()
    } catch (e) {
      alert(`Seed failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSeedBusy(false)
    }
  }

  // ── List view ──
  if (view === 'list' && !editingSlug) {
    const marketingTemplates = templates.filter((template) => template.category === 'marketing')
    const autoReplyTemplates = templates.filter(isAutoReplyTemplate)
    const activeTemplates = templates.filter((template) => template.is_active)
    const visibleTemplates = templates.filter((template) => {
      if (templateFocus === 'marketing') return template.category === 'marketing'
      if (templateFocus === 'auto') return isAutoReplyTemplate(template)
      return true
    })
    const grouped = visibleTemplates.reduce<Record<string, SmsTemplate[]>>((acc, t) => {
      ;(acc[t.category] ||= []).push(t)
      return acc
    }, {})

    return (
      <div className="sms-editor">
        <header className="sms-editor-head">
          <div>
            <h2>SMS Templates</h2>
            <p className="sms-editor-subtitle">
              Manage marketing texts, booking auto replies, compliance replies, and SMS logs.
            </p>
          </div>
          <div className="sms-editor-actions">
            <button onClick={() => setView('create')}>+ New template</button>
            <button onClick={() => setView('manual')}>Send manual</button>
            <button onClick={() => setView('log')}>View log</button>
            {(stats?.scope === 'shop' || !stats) && (
              <button onClick={handleSeed} disabled={seedBusy}>
                {seedBusy ? 'Seeding…' : 'Seed defaults'}
              </button>
            )}
            {onClose && <button onClick={onClose}>Close</button>}
          </div>
        </header>

        <div className="sms-focus-tabs" aria-label="Template focus">
          <button
            type="button"
            className={templateFocus === 'auto' ? 'active' : ''}
            onClick={() => setTemplateFocus('auto')}
          >
            Auto replies
          </button>
          <button
            type="button"
            className={templateFocus === 'marketing' ? 'active' : ''}
            onClick={() => setTemplateFocus('marketing')}
          >
            Marketing
          </button>
          <button
            type="button"
            className={templateFocus === 'all' ? 'active' : ''}
            onClick={() => setTemplateFocus('all')}
          >
            All templates
          </button>
        </div>

        {stats && (
          <div className="sms-stats">
            <Stat label="Auto replies" value={autoReplyTemplates.length} color="#6dbf7e" />
            <Stat label="Marketing" value={marketingTemplates.length} color="#7ba0d4" />
            <Stat label="Active" value={activeTemplates.length} color="var(--gold)" />
            <Stat label="Sent" value={stats.sent} color="#a0d4a8" />
            <Stat label="Delivered" value={stats.delivered} color="#6dbf7e" />
            <Stat label="Failed" value={stats.failed} color="#e0594b" />
            {stats.scope !== 'personal' && (
              <>
                <Stat label="Inbound" value={stats.inbound} color="#7ba0d4" />
                <Stat label="Opted in" value={stats.opted_in_users} color="var(--gold)" />
                <Stat label="Opted out" value={stats.opted_out_users} color="#e0594b" />
              </>
            )}
          </div>
        )}

        {error && <div className="sms-error">{error}</div>}
        {loading && <div className="sms-loading">Loading templates…</div>}

        {!loading && templates.length === 0 && (
          <div className="sms-empty">
            No templates yet. Click <strong>Seed defaults</strong> to install the starter library.
          </div>
        )}

        {!loading && templates.length > 0 && visibleTemplates.length === 0 && (
          <div className="sms-empty">
            No templates match this focus.
          </div>
        )}

        {Object.entries(grouped).map(([cat, tpls]) => (
          <section key={cat} className="sms-cat">
            <h3 style={{ borderLeftColor: CATEGORY_COLORS[cat] || 'var(--gold)' }}>
              {CATEGORY_LABELS[cat] || cat} <span className="sms-cat-count">{tpls.length}</span>
            </h3>
            <ul className="sms-tpl-list">
              {tpls.map(t => (
                <li
                  key={t.id}
                  className={`sms-tpl-row${t.is_active ? '' : ' inactive'}`}
                  onClick={() => { setEditingSlug(t.slug); setView('edit') }}
                >
                  <div className="sms-tpl-main">
                    <div className="sms-tpl-name">
                      {t.name}
                      {t.is_shop && <span className="sms-tpl-badge sms-badge-shop">shop</span>}
                      {t.is_mine && <span className="sms-tpl-badge sms-badge-mine">mine</span>}
                      {!t.is_active && <span className="sms-tpl-badge">disabled</span>}
                    </div>
                    <div className="sms-tpl-body-preview">{t.body}</div>
                  </div>
                  <div className="sms-tpl-meta">
                    <span>{t.body.length} chars</span>
                    <span>{Math.ceil(t.body.length / 160)} seg</span>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))}

        <style>{EDITOR_STYLES}</style>
      </div>
    )
  }

  // ── Edit view ──
  if (view === 'edit' && editingSlug) {
    return (
      <EditPane
        token={token}
        slug={editingSlug}
        onBack={() => { setEditingSlug(null); setView('list'); loadAll() }}
      />
    )
  }

  // ── Log view ──
  if (view === 'log') {
    return <LogPane token={token} onBack={() => { setView('list'); loadAll() }} />
  }

  // ── Manual send view ──
  if (view === 'manual') {
    return <ManualSendPane token={token} onBack={() => { setView('list'); loadAll() }} />
  }

  // ── Create view ──
  if (view === 'create') {
    return (
      <CreatePane
        token={token}
        onCancel={() => setView('list')}
        onCreated={(slug) => { setEditingSlug(slug); setView('edit') }}
      />
    )
  }

  return null
}

function CreatePane({ token, onCancel, onCreated }: { token: string; onCancel: () => void; onCreated: (slug: string) => void }) {
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [body, setBody] = useState('')
  const [category, setCategory] = useState('transactional')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleCreate() {
    setBusy(true)
    setError(null)
    try {
      const tpl = await createTemplate(token, { slug, name, body, category, description })
      onCreated(tpl.slug)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sms-editor">
      <header className="sms-editor-head">
        <button onClick={onCancel} className="sms-back">← Back</button>
        <h2>New Template</h2>
        <div />
      </header>
      <div className="sms-manual-form">
        <label>Slug (lowercase, underscores)</label>
        <input value={slug} onChange={e => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_'))} placeholder="custom_followup" />
        <small className="sms-note">Staff templates auto-prefix to <code>staff:&lt;your-id&gt;:{slug || 'your_slug'}</code>.</small>

        <label>Display name</label>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="Custom Follow-up" />

        <label>Category</label>
        <select value={category} onChange={e => setCategory(e.target.value)}>
          <option value="compliance">compliance</option>
          <option value="transactional">transactional</option>
          <option value="notification">notification</option>
          <option value="marketing">marketing</option>
        </select>

        <label>Body</label>
        <textarea value={body} onChange={e => setBody(e.target.value)} rows={4} placeholder="Hey {{client_name}}, …" />
        <div className="sms-edit-meta">{body.length} chars · {Math.ceil(body.length / 160) || 1} segment</div>

        <label>Description (internal)</label>
        <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} />

        <button onClick={handleCreate} disabled={busy || !slug || !name || !body}>
          {busy ? 'Creating…' : 'Create template'}
        </button>
        {error && <div className="sms-error">{error}</div>}
      </div>
      <style>{EDITOR_STYLES}</style>
    </div>
  )
}

// ── Sub-components ──

function Stat({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="sms-stat">
      <div className="sms-stat-value" style={{ color: color || 'var(--text)' }}>{value}</div>
      <div className="sms-stat-label">{label}</div>
    </div>
  )
}

function EditPane({ token, slug, onBack }: { token: string; slug: string; onBack: () => void }) {
  const [tpl, setTpl] = useState<SmsTemplate | null>(null)
  const [loadingTpl, setLoadingTpl] = useState(true)
  const [name, setName] = useState('')
  const [body, setBody] = useState('')
  const [category, setCategory] = useState('transactional')
  const [description, setDescription] = useState('')
  const [active, setActive] = useState(true)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [sampleVars, setSampleVars] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testPhone, setTestPhone] = useState('')
  const [testResult, setTestResult] = useState<string | null>(null)

  // A new template starts loading during render; the effect only fetches.
  const [tplKey, setTplKey] = useState(() => [token, slug] as const)
  if (tplKey[0] !== token || tplKey[1] !== slug) {
    setTplKey([token, slug])
    setLoadingTpl(true)
  }

  useEffect(() => {
    fetchOne(token, slug).then(t => {
      setTpl(t)
      setName(t.name)
      setBody(t.body)
      setCategory(t.category)
      setDescription(t.description || '')
      setActive(t.is_active)
      const samples: Record<string, string> = {}
      t.variables.forEach(v => { samples[v.key] = v.sample })
      setSampleVars(samples)
    }).catch(e => setError(String(e))).finally(() => setLoadingTpl(false))
  }, [token, slug])

  // Re-render preview on body/sample change
  useEffect(() => {
    if (!tpl) return
    let cancelled = false
    previewTemplate(token, body, sampleVars).then(p => {
      if (!cancelled) setPreview(p)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [token, body, sampleVars, tpl])

  async function handleSave() {
    setBusy(true)
    setError(null)
    try {
      await patchTemplate(token, slug, { name, body, category, description, is_active: active })
      onBack()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleTestSend() {
    if (!testPhone.trim()) return
    setBusy(true)
    setTestResult(null)
    try {
      const res = await sendTestSms(token, slug, testPhone, sampleVars)
      const r = res.results[0]
      if (!r) {
        setTestResult('No send result returned.')
        return
      }
      setTestResult(
        r.sent
          ? `Sent (status: ${r.status})`
          : `Failed (${r.status}): ${r.error || 'unknown error'}`,
      )
    } catch (e) {
      setTestResult(`Error: ${String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  if (loadingTpl) return <div className="sms-loading">Loading template…</div>
  if (!tpl) {
    return (
      <div className="sms-editor sms-editor-edit">
        <header className="sms-editor-head">
          <button onClick={onBack} className="sms-back">← Back</button>
          <h2>Template unavailable</h2>
          <div />
        </header>
        <div className="sms-error">{error || 'Failed to load template.'}</div>
        <style>{EDITOR_STYLES}</style>
      </div>
    )
  }

  return (
    <div className="sms-editor sms-editor-edit">
      <header className="sms-editor-head">
        <button onClick={onBack} className="sms-back">← Back</button>
        <input
          className="sms-name-input"
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="Template name"
        />
        <div className="sms-editor-actions">
          <label className="sms-toggle">
            <input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} />
            Active
          </label>
          {tpl?.is_mine && (
            <button
              onClick={async () => {
                if (!confirm(`Delete template "${tpl.name}"? This cannot be undone.`)) return
                try {
                  await deleteTemplate(token, slug)
                  onBack()
                } catch (e) {
                  alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`)
                }
              }}
              style={{ color: '#e0594b', borderColor: '#e0594b' }}
            >
              Delete
            </button>
          )}
          <button onClick={handleSave} disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </header>

      <div className="sms-edit-grid">
        <section className="sms-edit-body">
          <label>Automation category</label>
          <select
            className="sms-category-select"
            value={category}
            onChange={e => setCategory(e.target.value)}
          >
            <option value="compliance">Compliance auto reply</option>
            <option value="transactional">Booking auto reply</option>
            <option value="notification">Reply notification</option>
            <option value="marketing">Marketing</option>
          </select>

          <label>Message body</label>
          <textarea
            value={body}
            onChange={e => setBody(e.target.value)}
            rows={6}
            placeholder="e.g. Ryozan: Your appointment with {{artist_name}} is confirmed."
          />
          <div className="sms-edit-meta">
            {body.length} chars · {preview ? `${preview.segments} segment${preview.segments === 1 ? '' : 's'}` : '…'}
            {preview && preview.segments > 1 && <span className="sms-warn"> (multi-segment — extra cost)</span>}
          </div>

          <label>Description (internal — not sent)</label>
          <textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            rows={2}
            placeholder="What this template is for…"
          />
        </section>

        <section className="sms-edit-side">
          <h4>Variables</h4>
          {tpl.variables.length === 0 ? (
            <p className="sms-side-empty">No variables.</p>
          ) : (
            <ul className="sms-var-list">
              {tpl.variables.map(v => (
                <li key={v.key}>
                  <code>{`{{${v.key}}}`}</code>
                  <input
                    value={sampleVars[v.key] || ''}
                    onChange={e => setSampleVars(s => ({ ...s, [v.key]: e.target.value }))}
                    placeholder={v.sample}
                  />
                  <small>{v.label}</small>
                </li>
              ))}
            </ul>
          )}

          <h4>Preview</h4>
          <div className="sms-preview-bubble">{preview?.body || body}</div>

          <h4>Test send</h4>
          <input
            value={testPhone}
            onChange={e => setTestPhone(e.target.value)}
            placeholder="+15555550123"
          />
          <button onClick={handleTestSend} disabled={busy || !testPhone}>
            {busy ? 'Sending…' : 'Send to this number'}
          </button>
          {testResult && <div className="sms-test-result">{testResult}</div>}
        </section>
      </div>

      {error && <div className="sms-error">{error}</div>}
      <style>{EDITOR_STYLES}</style>
    </div>
  )
}

function LogPane({ token, onBack }: { token: string; onBack: () => void }) {
  const [rows, setRows] = useState<SmsLogRow[]>([])
  const [direction, setDirection] = useState<string>('')
  const [status, setStatus] = useState<string>('')
  const [phone, setPhone] = useState<string>('')
  const [loading, setLoading] = useState(true)

  const reload = useCallback(() => {
    setLoading(true)
    fetchLog(token, { direction, status, phone })
      .then(setRows)
      .finally(() => setLoading(false))
  }, [token, direction, status, phone])

  useEffect(() => {
    fetchLog(token, { direction, status, phone })
      .then(setRows)
      .finally(() => setLoading(false))
  }, [token, direction, status, phone])

  return (
    <div className="sms-editor sms-editor-log">
      <header className="sms-editor-head">
        <button onClick={onBack} className="sms-back">← Back</button>
        <h2>SMS Log</h2>
        <div className="sms-editor-actions">
          <select value={direction} onChange={e => setDirection(e.target.value)}>
            <option value="">All directions</option>
            <option value="outbound">Outbound</option>
            <option value="inbound">Inbound</option>
          </select>
          <select value={status} onChange={e => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="delivered">Delivered</option>
            <option value="sent">Sent</option>
            <option value="queued">Queued</option>
            <option value="failed">Failed</option>
            <option value="received">Received</option>
          </select>
          <input value={phone} onChange={e => setPhone(e.target.value)} placeholder="Phone filter" />
          <button onClick={reload}>Refresh</button>
        </div>
      </header>

      {loading ? (
        <div className="sms-loading">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="sms-empty">No messages match those filters.</div>
      ) : (
        <table className="sms-log-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Dir</th>
              <th>To / From</th>
              <th>Body</th>
              <th>Status</th>
              <th>Seg</th>
              <th>Template</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.direction === 'outbound' ? '↑' : '↓'}</td>
                <td>
                  <div className="sms-log-phone">{r.direction === 'outbound' ? r.to_phone : r.from_phone}</div>
                </td>
                <td className="sms-log-body">{r.body}</td>
                <td style={{ color: STATUS_COLORS[r.status] || 'var(--text-muted)' }}>
                  {r.status}
                  {r.error_message && <div className="sms-log-err">{r.error_message}</div>}
                </td>
                <td>{r.num_segments ?? '-'}</td>
                <td>{r.template_slug ?? '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <style>{EDITOR_STYLES}</style>
    </div>
  )
}

function ManualSendPane({ token, onBack }: { token: string; onBack: () => void }) {
  const [to, setTo] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  async function handleSend() {
    if (!to.trim() || !body.trim()) return
    setBusy(true)
    setResult(null)
    try {
      const r = await sendManual(token, to, body)
      setResult(
        r.error_code
          ? `Failed (${r.error_code}): ${r.error_message}`
          : `Status: ${r.status}${r.twilio_sid ? ` · ${r.twilio_sid}` : ''}`,
      )
    } catch (e) {
      setResult(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sms-editor">
      <header className="sms-editor-head">
        <button onClick={onBack} className="sms-back">← Back</button>
        <h2>Send Manual SMS</h2>
        <div />
      </header>

      <div className="sms-manual-form">
        <label>To (E.164 or local format)</label>
        <input value={to} onChange={e => setTo(e.target.value)} placeholder="+15555550123" />

        <label>Body</label>
        <textarea value={body} onChange={e => setBody(e.target.value)} rows={4} />
        <div className="sms-edit-meta">
          {body.length} chars · {Math.ceil(body.length / 160) || 1} segment{(Math.ceil(body.length / 160) || 1) === 1 ? '' : 's'}
        </div>

        <button onClick={handleSend} disabled={busy || !to || !body}>
          {busy ? 'Sending…' : 'Send'}
        </button>

        {result && <div className="sms-test-result">{result}</div>}

        <p className="sms-note">
          Recipient must be opted in. Use the opt-in toggle on a user record or send
          them the opt-in flow first.
        </p>
      </div>
      <style>{EDITOR_STYLES}</style>
    </div>
  )
}

// ── Scoped styles ──

const EDITOR_STYLES = `
.sms-editor {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  color: var(--text);
  background: var(--bg-surface);
  height: 100%;
  overflow: auto;
}
.sms-editor-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid var(--panel-border);
  padding-bottom: 12px;
}
.sms-editor-head h2 {
  margin: 0;
  font-size: 18px;
  color: var(--gold);
}
.sms-editor-subtitle {
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 12px;
}
.sms-editor-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.sms-editor-actions button,
.sms-editor-actions select,
.sms-editor-actions input {
  background: var(--panel);
  color: var(--text);
  border: 1px solid var(--panel-border);
  padding: 6px 12px;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
}
.sms-editor-actions button:hover { border-color: var(--gold); }
.sms-back { background: transparent; border: none; color: var(--gold); font-size: 14px; cursor: pointer; }
.sms-name-input {
  flex: 1;
  background: var(--panel);
  color: var(--text);
  border: 1px solid var(--panel-border);
  padding: 6px 12px;
  font-size: 14px;
  border-radius: 4px;
}
.sms-focus-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.sms-focus-tabs button {
  background: var(--panel);
  color: var(--text-muted);
  border: 1px solid var(--panel-border);
  border-radius: 4px;
  padding: 7px 12px;
  font-size: 12px;
  cursor: pointer;
}
.sms-focus-tabs button.active,
.sms-focus-tabs button:hover {
  color: var(--gold);
  border-color: var(--gold);
}
.sms-stats {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px;
  background: var(--panel);
  border-radius: 6px;
  border: 1px solid var(--panel-border);
}
.sms-stat { text-align: center; min-width: 70px; }
.sms-stat-value { font-size: 24px; font-weight: bold; }
.sms-stat-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
.sms-loading, .sms-empty { padding: 32px; text-align: center; color: var(--text-muted); }
.sms-error { padding: 12px; background: rgba(224, 89, 75, 0.1); border-left: 3px solid #e0594b; color: #e0594b; border-radius: 4px; }
.sms-cat { margin-top: 8px; }
.sms-cat h3 {
  margin: 0 0 8px;
  padding-left: 10px;
  border-left: 3px solid var(--gold);
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
}
.sms-cat-count {
  font-size: 11px;
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 999px;
  padding: 2px 8px;
  margin-left: 6px;
}
.sms-tpl-list { list-style: none; padding: 0; margin: 0; }
.sms-tpl-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--panel-border);
  border-radius: 6px;
  margin-bottom: 6px;
  cursor: pointer;
  background: var(--panel);
  transition: border-color 0.1s;
}
.sms-tpl-row:hover { border-color: var(--gold); }
.sms-tpl-row.inactive { opacity: 0.5; }
.sms-tpl-name { font-weight: 600; color: var(--text); }
.sms-tpl-badge {
  font-size: 10px;
  background: rgba(224, 89, 75, 0.2);
  color: #e0594b;
  padding: 2px 6px;
  border-radius: 4px;
  margin-left: 8px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.sms-badge-shop {
  background: rgba(123, 160, 212, 0.2);
  color: #7ba0d4;
}
.sms-badge-mine {
  background: rgba(250, 174, 21, 0.2);
  color: var(--gold);
}
.sms-tpl-body-preview {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.sms-tpl-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  font-size: 11px;
  color: var(--text-muted);
}
.sms-edit-grid {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 16px;
}
@media (max-width: 700px) { .sms-edit-grid { grid-template-columns: 1fr; } }
.sms-edit-body label,
.sms-edit-side label {
  display: block;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  margin: 12px 0 4px;
}
.sms-edit-body textarea,
.sms-edit-body select,
.sms-edit-side input,
.sms-edit-side textarea {
  width: 100%;
  background: var(--panel);
  color: var(--text);
  border: 1px solid var(--panel-border);
  padding: 8px 10px;
  font-size: 13px;
  border-radius: 4px;
  font-family: inherit;
  box-sizing: border-box;
}
.sms-edit-meta { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.sms-category-select { max-width: 280px; }
.sms-warn { color: #e0a04b; }
.sms-edit-side h4 { margin: 16px 0 8px; font-size: 12px; color: var(--gold); text-transform: uppercase; letter-spacing: 0.5px; }
.sms-side-empty { font-size: 12px; color: var(--text-muted); }
.sms-var-list { list-style: none; padding: 0; margin: 0; }
.sms-var-list li { margin-bottom: 12px; }
.sms-var-list code { display: block; font-size: 11px; color: var(--gold); margin-bottom: 4px; }
.sms-var-list small { display: block; font-size: 10px; color: var(--text-muted); margin-top: 2px; }
.sms-preview-bubble {
  background: #1d2733;
  color: #e8f0fa;
  padding: 12px;
  border-radius: 16px 16px 16px 4px;
  font-size: 13px;
  white-space: pre-wrap;
  font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
  line-height: 1.4;
}
.sms-test-result { font-size: 12px; color: var(--text-muted); margin-top: 6px; padding: 8px; background: var(--panel); border-radius: 4px; }
.sms-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-muted); }
.sms-log-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.sms-log-table th,
.sms-log-table td {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid var(--panel-border);
  vertical-align: top;
}
.sms-log-table th { color: var(--text-muted); font-weight: normal; text-transform: uppercase; font-size: 10px; letter-spacing: 0.5px; }
.sms-log-phone { font-family: monospace; }
.sms-log-body { max-width: 280px; word-break: break-word; }
.sms-log-err { font-size: 10px; color: #e0594b; margin-top: 4px; }
.sms-manual-form { display: flex; flex-direction: column; gap: 8px; max-width: 480px; }
.sms-manual-form label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); margin-top: 8px; }
.sms-manual-form input,
.sms-manual-form select,
.sms-manual-form textarea {
  width: 100%;
  background: var(--panel);
  color: var(--text);
  border: 1px solid var(--panel-border);
  padding: 8px 10px;
  font-size: 13px;
  border-radius: 4px;
  font-family: inherit;
  box-sizing: border-box;
}
.sms-manual-form button {
  background: var(--gold);
  color: #121312;
  border: none;
  padding: 8px 16px;
  font-weight: bold;
  border-radius: 4px;
  cursor: pointer;
  margin-top: 12px;
}
.sms-manual-form button:disabled { opacity: 0.4; cursor: not-allowed; }
.sms-note { font-size: 11px; color: var(--text-muted); margin-top: 16px; }
`
