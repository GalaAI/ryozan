/**
 * EmailTemplateEditor — Anvl-style 3-panel email toolkit.
 *
 * Layout: topbar (back · name · mode tabs · actions) + body (vars panel | editor | properties).
 * Modes: Code (raw HTML textarea) | Preview (iframe with sample data) | Variables (reference table).
 * Expandable: add new mode tabs, left panel sections, or right panel property groups independently.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import '../styles/email-template-editor.css'

interface TemplateVar {
  key: string
  label: string
  sample: string
}

interface EmailTemplate {
  id: string
  slug: string
  name: string
  subject: string
  category: string
  html_body: string
  variables: TemplateVar[]
  is_active: boolean
  description: string | null
  created_at: string
  updated_at: string
}

type EditorMode = 'code' | 'preview' | 'variables'

import { API_BASE } from '../lib/api'

async function fetchTemplates(token: string): Promise<EmailTemplate[]> {
  const res = await fetch(`${API_BASE}/email-templates/`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('Failed to load templates')
  return res.json()
}

async function updateTemplate(
  token: string,
  slug: string,
  data: Partial<Pick<EmailTemplate, 'name' | 'subject' | 'html_body' | 'is_active' | 'description'>>,
): Promise<EmailTemplate> {
  const res = await fetch(`${API_BASE}/email-templates/${slug}`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to save template')
  return res.json()
}

async function previewTemplate(
  token: string,
  html: string,
  sampleData: Record<string, string>,
): Promise<string> {
  const res = await fetch(`${API_BASE}/email-templates/preview`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ html_body: html, sample_data: sampleData }),
  })
  if (!res.ok) throw new Error('Preview failed')
  const data = await res.json()
  return data.html
}

async function seedTemplates(token: string): Promise<{ created: number; updated: number }> {
  const res = await fetch(`${API_BASE}/email-templates/seed`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('Seed failed')
  return res.json()
}

const CATEGORY_COLORS: Record<string, string> = {
  transactional: '#6dbf7e',
  notification: 'var(--gold)',
  onboarding: '#7ba0d4',
  'post-session': '#c49a6c',
}

interface LayerItem { name: string; line: number; depth: number }

function extractLayers(html: string): LayerItem[] {
  const layers: LayerItem[] = []
  const lines = html.split('\n')
  // Scan for comment markers and significant table/section tags
  lines.forEach((line, i) => {
    const commentMatch = line.match(/<!--\s*[=-]+\s*([A-Z][A-Z\s]+?)\s*[=-]+/)
    if (commentMatch) { layers.push({ name: commentMatch[1].trim(), line: i, depth: 0 }); return }
    const sectionMatch = line.match(/<(table|section|div|header|footer|main)\b[^>]*id=["']?([^"'\s>]+)/i)
    if (sectionMatch) { layers.push({ name: sectionMatch[2], line: i, depth: 1 }); return }
    if (/<body\b/i.test(line)) layers.push({ name: 'BODY', line: i, depth: 0 })
    if (/<head\b/i.test(line)) layers.push({ name: 'HEAD', line: i, depth: 0 })
  })
  return layers
}

interface Props {
  token: string
  initialSlug?: string
  onClose?: () => void
}

export default function EmailTemplateEditor({ token, initialSlug, onClose }: Props) {
  const [templates, setTemplates] = useState<EmailTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingSlug, setEditingSlug] = useState<string | null>(initialSlug ?? null)
  const [editSubject, setEditSubject] = useState('')
  const [editHtml, setEditHtml] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editActive, setEditActive] = useState(true)
  const [saving, setSaving] = useState(false)
  const [previewHtml, setPreviewHtml] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [seedResult, setSeedResult] = useState<string | null>(null)
  const [editorMode, setEditorMode] = useState<EditorMode>('code')
  const [selectedLayer, setSelectedLayer] = useState<number | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const imgInputRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchTemplates(token)
      setTemplates(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { load() }, [load])

  // Auto-start editing if initialSlug is provided (launched from AssetsPanel)
  useEffect(() => {
    if (initialSlug && templates.length > 0 && !editingSlug) {
      const t = templates.find(x => x.slug === initialSlug)
      if (t) startEdit(t)
    }
  }, [initialSlug, templates]) // eslint-disable-line

  const startEdit = (t: EmailTemplate) => {
    setEditingSlug(t.slug)
    setEditSubject(t.subject)
    setEditHtml(t.html_body)
    setEditDescription(t.description ?? '')
    setEditActive(t.is_active)
    setPreviewHtml(null)
    setEditorMode('code')
    setError(null)
    setSelectedLayer(null)
  }

  const cancelEdit = () => {
    if (onClose) { onClose(); return }
    setEditingSlug(null)
    setPreviewHtml(null)
  }

  const handleImgUpload = (files: FileList | null) => {
    if (!files || !files[0]) return
    const reader = new FileReader()
    reader.onload = (e) => {
      const url = e.target?.result as string
      const tag = `<img src="${url}" alt="" style="max-width:100%;display:block;margin:0 auto;" />`
      const ta = textareaRef.current
      if (ta) {
        const pos = ta.selectionStart
        setEditHtml(prev => prev.slice(0, pos) + '\n' + tag + '\n' + prev.slice(pos))
      } else {
        setEditHtml(prev => prev + '\n' + tag)
      }
    }
    reader.readAsDataURL(files[0])
  }

  const jumpToLayer = (line: number) => {
    setSelectedLayer(line)
    const ta = textareaRef.current
    if (!ta) return
    const lines = editHtml.split('\n')
    const charPos = lines.slice(0, line).join('\n').length
    ta.focus()
    ta.setSelectionRange(charPos, charPos + (lines[line]?.length ?? 0))
    // Scroll textarea to approximate position
    const lineHeight = 19.2 // ~1.2rem at 16px
    ta.scrollTop = line * lineHeight - ta.clientHeight / 2
  }

  const saveEdit = async () => {
    if (!editingSlug) return
    setSaving(true)
    try {
      const updated = await updateTemplate(token, editingSlug, {
        subject: editSubject,
        html_body: editHtml,
        is_active: editActive,
        description: editDescription || null,
      })
      setTemplates(prev => prev.map(t => t.slug === editingSlug ? updated : t))
      setEditingSlug(null)
      setPreviewHtml(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const toggleActive = async (t: EmailTemplate) => {
    try {
      const updated = await updateTemplate(token, t.slug, { is_active: !t.is_active })
      setTemplates(prev => prev.map(x => x.slug === t.slug ? updated : x))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Toggle failed')
    }
  }

  const loadPreview = useCallback(async () => {
    const template = templates.find(t => t.slug === editingSlug)
    if (!template) return
    setPreviewLoading(true)
    const sampleData: Record<string, string> = {}
    for (const v of template.variables) sampleData[v.key] = v.sample
    try {
      const html = await previewTemplate(token, editHtml, sampleData)
      setPreviewHtml(html)
    } catch {
      setError('Preview failed')
    } finally {
      setPreviewLoading(false)
    }
  }, [token, editHtml, editingSlug, templates])

  useEffect(() => {
    if (editorMode === 'preview') loadPreview()
  }, [editorMode]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSeed = async () => {
    setSeeding(true)
    setSeedResult(null)
    try {
      const result = await seedTemplates(token)
      setSeedResult(`Created ${result.created}, updated ${result.updated}`)
      await load()
    } catch (e: unknown) {
      setSeedResult(e instanceof Error ? e.message : 'Seed failed')
    } finally {
      setSeeding(false)
    }
  }

  const insertVar = (key: string) => {
    const ta = textareaRef.current
    if (!ta || editorMode !== 'code') return
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const insertion = `{{${key}}}`
    const next = editHtml.slice(0, start) + insertion + editHtml.slice(end)
    setEditHtml(next)
    requestAnimationFrame(() => {
      ta.focus()
      ta.setSelectionRange(start + insertion.length, start + insertion.length)
    })
  }

  const formatDate = (iso: string) => {
    try { return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) }
    catch { return iso }
  }

  if (loading) return (
    <div style={{ padding: '2rem', color: 'var(--text-muted)', textAlign: 'center', fontSize: '0.78rem', fontFamily: 'var(--font-mono)' }}>
      Loading templates...
    </div>
  )

  if (templates.length === 0) return (
    <div style={{ padding: '2rem', textAlign: 'center' }}>
      {error && <p style={{ color: '#e05c5c', marginBottom: '1rem', fontSize: '0.78rem' }}>{error}</p>}
      <p style={{ color: 'var(--text)', marginBottom: '1rem', fontSize: '0.82rem' }}>No email templates found.</p>
      <button
        onClick={handleSeed}
        disabled={seeding}
        style={{
          padding: '0.5rem 1.25rem', fontSize: '0.78rem', fontWeight: 600,
          background: 'var(--gold)', color: '#000', border: 'none',
          borderRadius: '6px', cursor: 'pointer', opacity: seeding ? 0.6 : 1,
        }}
      >
        {seeding ? 'Seeding...' : 'Seed Templates'}
      </button>
      {seedResult && <p style={{ color: 'var(--gold)', marginTop: '0.75rem', fontSize: '0.75rem' }}>{seedResult}</p>}
    </div>
  )

  /* ── Editing view ─────────────────────────────────────────────────── */
  if (editingSlug) {
    const template = templates.find(t => t.slug === editingSlug)
    if (!template) return null
    const catColor = CATEGORY_COLORS[template.category] || 'var(--text-muted)'

    return (
      <div className="ete-root">

        {/* Topbar */}
        <div className="ete-topbar">
          <button className="ete-back-btn" onClick={cancelEdit}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
            Back
          </button>

          <div className="ete-title">
            <span className="ete-name">{template.name}</span>
            <span className="ete-category-badge" style={{ color: catColor, borderColor: catColor }}>
              {template.category}
            </span>
          </div>

          <div className="ete-mode-tabs">
            {(['code', 'preview', 'variables'] as EditorMode[]).map(m => (
              <button
                key={m}
                className={`ete-mode-tab${editorMode === m ? ' active' : ''}`}
                onClick={() => setEditorMode(m)}
              >
                {m}
              </button>
            ))}
          </div>

          <div className="ete-actions">
            <input ref={imgInputRef} type="file" accept="image/*" style={{ display: 'none' }}
              onChange={e => handleImgUpload(e.target.files)} />
            <button className="ete-btn" title="Insert image at cursor" onClick={() => imgInputRef.current?.click()}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ display: 'inline', verticalAlign: 'middle', marginRight: 3 }}>
                <rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
              </svg>
              Image
            </button>
            <button className="ete-btn ete-btn--save" onClick={saveEdit} disabled={saving}>
              {saving ? 'Saving...' : 'Save'}
            </button>
            <button className="ete-btn ete-btn--cancel" onClick={cancelEdit}>
              Cancel
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="ete-body">

          {/* Left — Variables panel */}
          <div className="ete-left">
            <div>
              <div className="ete-section-title">Variables</div>
              {template.variables.length === 0
                ? <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)' }}>No variables</span>
                : (
                  <div className="ete-var-list">
                    {template.variables.map(v => (
                      <button
                        key={v.key}
                        className="ete-var-chip"
                        title={editorMode === 'code' ? `Insert {{${v.key}}} at cursor` : v.label}
                        onClick={() => insertVar(v.key)}
                      >
                        <span className="ete-var-key">{`{{${v.key}}}`}</span>
                        <span className="ete-var-sample">{v.sample}</span>
                      </button>
                    ))}
                  </div>
                )
              }
              {editorMode === 'code' && template.variables.length > 0 && (
                <p className="ete-var-hint">Click to insert at cursor</p>
              )}
            </div>

            {/* Layers */}
            {editorMode === 'code' && (() => {
              const layers = extractLayers(editHtml)
              if (layers.length === 0) return null
              return (
                <div>
                  <div className="ete-section-title">Layers</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                    {layers.map((layer, i) => (
                      <button
                        key={i}
                        onClick={() => jumpToLayer(layer.line)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: '5px',
                          padding: `4px ${4 + layer.depth * 10}px`,
                          border: 'none',
                          background: selectedLayer === layer.line ? 'rgba(166,125,67,0.12)' : 'transparent',
                          borderRadius: '4px',
                          color: selectedLayer === layer.line ? 'var(--gold)' : 'var(--text-muted)',
                          fontSize: '0.6rem', fontFamily: 'var(--font-mono)',
                          cursor: 'pointer', textAlign: 'left', width: '100%',
                          transition: 'all 0.12s',
                        }}
                      >
                        <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <rect x="2" y="7" width="20" height="14" rx="2" /><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2" />
                        </svg>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {layer.name}
                        </span>
                        <span style={{ marginLeft: 'auto', opacity: 0.4, fontSize: '0.5rem' }}>{layer.line + 1}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )
            })()}

            {/* Expandable: add more left panel sections here */}
          </div>

          {/* Center — Editor / Preview / Variables table */}
          <div className="ete-center">
            {editorMode === 'code' && (
              <textarea
                ref={textareaRef}
                className="ete-code-editor"
                value={editHtml}
                onChange={e => setEditHtml(e.target.value)}
                spellCheck={false}
                autoComplete="off"
              />
            )}

            {editorMode === 'preview' && (
              previewLoading
                ? <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '0.78rem', fontFamily: 'var(--font-mono)' }}>
                    Rendering...
                  </div>
                : <iframe
                    key={previewHtml}
                    className="ete-preview-frame"
                    srcDoc={previewHtml ?? '<p style="padding:2rem;color:#999">No preview yet</p>'}
                    title="Email preview"
                    sandbox="allow-same-origin"
                  />
            )}

            {editorMode === 'variables' && (
              <div className="ete-vars-table">
                <div style={{ marginBottom: '10px' }}>
                  <div className="ete-section-title">Variable Reference</div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'auto auto 1fr', gap: '0 8px', marginBottom: '6px', padding: '0 10px' }}>
                  <span style={{ fontSize: '0.52rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Key</span>
                  <span style={{ fontSize: '0.52rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Label</span>
                  <span style={{ fontSize: '0.52rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Sample</span>
                </div>
                <div className="ete-vars-grid">
                  {template.variables.map(v => (
                    <div key={v.key} className="ete-vars-row">
                      <span className="ete-vars-row-key">{`{{${v.key}}}`}</span>
                      <span className="ete-vars-row-label">{v.label}</span>
                      <span className="ete-vars-row-sample">{v.sample}</span>
                    </div>
                  ))}
                  {template.variables.length === 0 && (
                    <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', padding: '8px' }}>No variables defined</span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Right — Properties panel */}
          <div className="ete-right">

            {/* Subject */}
            <div>
              <label className="ete-prop-label">Subject</label>
              <input
                className="ete-prop-input"
                value={editSubject}
                onChange={e => setEditSubject(e.target.value)}
                placeholder="Email subject line"
              />
              <div className="ete-char-count">{editSubject.length} chars</div>
            </div>

            {/* Active toggle */}
            <div>
              <label className="ete-prop-label">Status</label>
              <div className="ete-toggle-row">
                <span className="ete-toggle-label">{editActive ? 'Active' : 'Inactive'}</span>
                <label className="ete-toggle">
                  <input type="checkbox" checked={editActive} onChange={e => setEditActive(e.target.checked)} />
                  <span className="ete-toggle-track" />
                </label>
              </div>
            </div>

            {/* Description */}
            <div>
              <label className="ete-prop-label">Description</label>
              <textarea
                className="ete-prop-textarea"
                value={editDescription}
                onChange={e => setEditDescription(e.target.value)}
                placeholder="Optional note about this template"
              />
            </div>

            {/* Metadata */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <label className="ete-prop-label">Info</label>
              <div className="ete-meta-item">
                <span className="ete-meta-key">Slug</span>
                <span className="ete-meta-val" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.62rem' }}>{template.slug}</span>
              </div>
              <div className="ete-meta-item">
                <span className="ete-meta-key">Category</span>
                <span className="ete-meta-val" style={{ color: catColor }}>{template.category}</span>
              </div>
              <div className="ete-meta-item">
                <span className="ete-meta-key">Variables</span>
                <span className="ete-meta-val">{template.variables.length}</span>
              </div>
              <div className="ete-meta-item">
                <span className="ete-meta-key">HTML Size</span>
                <span className="ete-meta-val" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.6rem' }}>
                  {(editHtml.length / 1024).toFixed(1)} KB
                </span>
              </div>
              <div className="ete-meta-item">
                <span className="ete-meta-key">Updated</span>
                <span className="ete-meta-val">{formatDate(template.updated_at)}</span>
              </div>
            </div>

            {/* Expandable: add property groups here (block inspector, link manager, etc.) */}
          </div>
        </div>

        {error && <div className="ete-error">{error}</div>}
      </div>
    )
  }

  /* ── List view ────────────────────────────────────────────────────── */
  const grouped = templates.reduce<Record<string, EmailTemplate[]>>((acc, t) => {
    const cat = t.category || 'other'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(t)
    return acc
  }, {})

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <div className="ete-list-header">
        <span className="ete-list-count">{templates.length} templates</span>
        <button className="ete-list-sync-btn" onClick={handleSeed} disabled={seeding}>
          {seeding ? 'Syncing...' : 'Sync from code'}
        </button>
      </div>

      {seedResult && (
        <p style={{ color: 'var(--gold)', fontSize: '0.68rem', textAlign: 'center', fontFamily: 'var(--font-mono)' }}>
          {seedResult}
        </p>
      )}

      {Object.entries(grouped).map(([category, items]) => (
        <div key={category}>
          <div className="ete-cat-label" style={{ color: CATEGORY_COLORS[category] || 'var(--text-muted)' }}>
            {category}
          </div>
          {items.map(t => (
            <div key={t.slug} className="ete-template-row" style={{ opacity: t.is_active ? 1 : 0.5 }}>
              <button
                className="ete-template-dot"
                style={{ background: t.is_active ? '#6dbf7e' : '#555' }}
                title={t.is_active ? 'Active — click to disable' : 'Inactive — click to enable'}
                onClick={() => toggleActive(t)}
              />
              <div className="ete-template-info">
                <div className="ete-template-name">{t.name}</div>
                <div className="ete-template-subject">{t.subject}</div>
              </div>
              <button className="ete-edit-btn" onClick={() => startEdit(t)}>Edit</button>
            </div>
          ))}
        </div>
      ))}

      {error && <p style={{ color: '#e05c5c', fontSize: '0.72rem', textAlign: 'center', fontFamily: 'var(--font-mono)' }}>{error}</p>}
    </div>
  )
}
