/**
 * DevModeBar — GalaX Design Tools (GDT) v1
 *
 * Founder/admin-only persistent dev toolbar. Pinned below topbar when devMode is active.
 *
 * Tabs:
 *   ROLE    — inline ViewAs role switcher
 *   API     — backend health + latency ping
 *   BUILD   — env, version, flags
 *   DESIGN  — live element picker, scoped tweaks, design tokens, and draw-to-place drafting
 *
 * Architecture note: this file is the canonical GDT implementation.
 * Extract to @galax/design-tools package when porting to GalaX OS / other products.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useAuthStore, type ViewAsMode } from '../stores/auth'

/* ── Types ──────────────────────────────────────────────────── */

type Tab = 'role' | 'api' | 'build' | 'design'

interface ApiStatus {
  ok: boolean
  latency: number | null
  checked: number | null
}

interface InspectedEl {
  tag: string
  id: string
  classes: string[]
  component: string | null
  dims: { w: number; h: number; x: number; y: number }
  cssVars: Record<string, string>
  path: string
}

interface CSSTokenGroup {
  label: string
  tokens: { name: string; value: string }[]
}

interface DraftElement {
  x: number
  y: number
  w: number
  h: number
  type: 'button' | 'card' | 'panel' | 'input' | 'nav' | 'modal'
  label: string
  action: string
  notes: string
}

interface EnchantDraft {
  effect: 'fade' | 'slide' | 'scale' | 'glow' | 'pulse' | 'reveal' | 'custom'
  trigger: 'hover' | 'click' | 'load' | 'scroll' | 'focus'
  duration: string
  easing: string
  rules: string
  notes: string
}

interface TweakDraft {
  background: string
  color: string
  borderRadius: string
  padding: string
  margin: string
  width: string
  height: string
  opacity: string
  transform: string
  notes: string
}

/* ── Constants ──────────────────────────────────────────────── */

const API_BASE = (import.meta.env.VITE_API_URL ?? '') + '/health'
const API_DISPLAY = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/health`
  : 'Same-origin /health through app host'
const ENV = import.meta.env.VITE_APP_ENV ?? import.meta.env.MODE ?? 'unknown'
const BUILD_VERSION = __APP_VERSION__ ?? 'dev'

const ROLE_OPTIONS: { mode: NonNullable<ViewAsMode>; label: string; color: string }[] = [
  { mode: 'collector', label: 'Collector', color: '#6dbf7e' },
  { mode: 'assistant', label: 'Assistant', color: '#7eb3df' },
  { mode: 'artist',    label: 'Artist',    color: '#a6885d' },
  { mode: 'admin',     label: 'Admin',     color: '#df7e7e' },
]

/* ── Helpers ────────────────────────────────────────────────── */

async function pingApi(): Promise<ApiStatus> {
  const start = performance.now()
  try {
    const res = await fetch(API_BASE, { method: 'GET', cache: 'no-store', signal: AbortSignal.timeout(4000) })
    return { ok: res.ok, latency: Math.round(performance.now() - start), checked: Date.now() }
  } catch {
    return { ok: false, latency: null, checked: Date.now() }
  }
}

/** Walk React fiber tree upward to find nearest named component. */
function getReactComponent(el: Element): string | null {
  const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'))
  if (!key) return null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fiber: any = (el as any)[key]
  while (fiber) {
    const name: string | undefined = fiber.type?.displayName || fiber.type?.name
    if (name && name.length > 1 && !/^[a-z]/.test(name) && name !== 'Fragment') return name
    fiber = fiber.return
  }
  return null
}

/** Build a short CSS selector path for an element. */
function buildPath(el: Element): string {
  const parts: string[] = []
  let cur: Element | null = el
  let depth = 0
  while (cur && depth < 4) {
    const tag = cur.tagName.toLowerCase()
    const id = cur.id ? `#${cur.id}` : ''
    const cls = cur.classList.length > 0 ? `.${Array.from(cur.classList).slice(0, 2).join('.')}` : ''
    parts.unshift(`${tag}${id}${cls}`)
    cur = cur.parentElement
    depth++
  }
  return parts.join(' > ')
}

/** Extract CSS custom properties actually resolved on an element. */
function getElementCSSVars(el: Element): Record<string, string> {
  const computed = window.getComputedStyle(el)
  const result: Record<string, string> = {}
  // Check inline style + known token names referenced
  const allVars = getAllCSSVars()
  for (const name of Object.keys(allVars)) {
    const val = computed.getPropertyValue(name).trim()
    if (val) result[name] = val
  }
  return result
}

/** Read all CSS custom properties from :root across all stylesheets. */
function getAllCSSVars(): Record<string, string> {
  const vars: Record<string, string> = {}
  try {
    for (const sheet of document.styleSheets) {
      try {
        for (const rule of sheet.cssRules) {
          if (rule instanceof CSSStyleRule && rule.selectorText === ':root') {
            const style = rule.style
            for (let i = 0; i < style.length; i++) {
              const prop = style[i]
              if (prop.startsWith('--')) {
                vars[prop] = style.getPropertyValue(prop).trim()
              }
            }
          }
        }
      } catch { /* cross-origin sheet */ }
    }
  } catch { /* no styleSheets */ }
  return vars
}

/** Group CSS vars into design categories. */
function groupTokens(vars: Record<string, string>): CSSTokenGroup[] {
  const colors: { name: string; value: string }[] = []
  const spacing: { name: string; value: string }[] = []
  const radius: { name: string; value: string }[] = []
  const typography: { name: string; value: string }[] = []
  const other: { name: string; value: string }[] = []

  const isColor = (v: string) =>
    v.startsWith('#') || v.startsWith('rgb') || v.startsWith('hsl') ||
    /^[a-z]+$/i.test(v.trim())

  for (const [name, value] of Object.entries(vars)) {
    const n = name.toLowerCase()
    if (n.includes('space') || n.includes('gap') || n.includes('padding') || n.includes('margin')) {
      spacing.push({ name, value })
    } else if (n.includes('radius') || n.includes('rounded')) {
      radius.push({ name, value })
    } else if (n.includes('font') || n.includes('type') || n.includes('text') || n.includes('letter') || n.includes('line')) {
      typography.push({ name, value })
    } else if (isColor(value)) {
      colors.push({ name, value })
    } else {
      other.push({ name, value })
    }
  }

  return [
    { label: 'Colors', tokens: colors },
    { label: 'Spacing', tokens: spacing },
    { label: 'Radius', tokens: radius },
    { label: 'Typography', tokens: typography },
    { label: 'Other', tokens: other },
  ].filter(g => g.tokens.length > 0)
}

function isColorValue(v: string) {
  return v.startsWith('#') || v.startsWith('rgb') || v.startsWith('hsl')
}

/* ── Sub-components ─────────────────────────────────────────── */

function DesignTab({ embedded = false }: { embedded?: boolean }) {
  const [groups, setGroups] = useState<CSSTokenGroup[]>([])
  const [activeGroup, setActiveGroup] = useState(0)
  const [copied, setCopied] = useState<string | null>(null)
  const [editingToken, setEditingToken] = useState<string | null>(null)
  const [editedTokens, setEditedTokens] = useState<Record<string, string>>({})
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  useEffect(() => {
    setGroups(groupTokens(getAllCSSVars()))
  }, [])

  const copy = (text: string) => {
    navigator.clipboard.writeText(text).catch(() => {})
    setCopied(text)
    setTimeout(() => setCopied(null), 1200)
  }

  const applyLive = (name: string, value: string) => {
    document.documentElement.style.setProperty(name, value)
  }

  const commitEdit = (name: string, newValue: string, originalValue: string) => {
    setEditingToken(null)
    if (newValue.trim() === '' || newValue === originalValue) return
    applyLive(name, newValue)
    setEditedTokens(prev => ({ ...prev, [name]: newValue }))
    // Refresh groups after edit
    setTimeout(() => setGroups(groupTokens(getAllCSSVars())), 50)
  }

  const saveToFile = async () => {
    const count = Object.keys(editedTokens).length
    if (count === 0) return
    setSaving(true)
    try {
      const res = await fetch('/dev-api/write-tokens', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tokens: editedTokens }),
      })
      const data = await res.json()
      if (data.ok) {
        setSaveMsg(`Saved ${data.updated} token${data.updated !== 1 ? 's' : ''} to tokens.css`)
        setEditedTokens({})
      } else {
        setSaveMsg(`Error: ${data.error}`)
      }
    } catch (err) {
      setSaveMsg(`Failed: ${String(err)}`)
    } finally {
      setSaving(false)
      setTimeout(() => setSaveMsg(null), 3000)
    }
  }

  const group = groups[activeGroup]
  const filteredTokens = group?.tokens.filter(({ name, value }) => {
    const q = query.trim().toLowerCase()
    if (!q) return true
    return name.toLowerCase().includes(q) || value.toLowerCase().includes(q)
  }) ?? []
  const dirtyCount = Object.keys(editedTokens).length

  return (
    <div className={embedded ? 'devbar-design-panel devbar-design-panel--embedded' : 'devbar-panel devbar-design-panel'}>
      <div className="devbar-tool-summary">
        <span className="devbar-tool-scope">Scope: system tokens</span>
        <span>Live preview applies immediately. Save writes to `tokens.css` and can affect the whole app.</span>
      </div>

      {/* Group tabs + save action */}
      <div className="devbar-design-groups">
        {groups.map((g, i) => (
          <button
            key={g.label}
            className={`devbar-design-group-btn${activeGroup === i ? ' active' : ''}`}
            onClick={() => setActiveGroup(i)}
          >
            {g.label}
            <span className="devbar-design-group-count">{g.tokens.length}</span>
          </button>
        ))}
        {dirtyCount > 0 && (
          <button
            className="devbar-design-save-btn"
            onClick={saveToFile}
            disabled={saving}
            title="Write edited tokens to tokens.css"
          >
            {saving ? 'Saving…' : `Save ${dirtyCount}`}
          </button>
        )}
        {saveMsg && <span className="devbar-save-msg">{saveMsg}</span>}
      </div>

      <input
        className="devbar-token-search"
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder="Search token name or value"
      />

      {/* Token grid */}
      {group && (
        <div className="devbar-design-tokens">
          {filteredTokens.map(({ name, value }) => {
            const isDirty = name in editedTokens
            const isEditing = editingToken === name
            return (
              <div
                key={name}
                className={`devbar-token-row${isDirty ? ' devbar-token-row--dirty' : ''}`}
                title={isEditing ? undefined : `Click swatch/preview to copy; click value to edit`}
              >
                {/* Swatch/preview — click to copy */}
                <button
                  className="devbar-token-swatch-btn"
                  onClick={() => copy(name)}
                  tabIndex={0}
                >
                  {isColorValue(value) ? (
                    <span className="devbar-token-swatch" style={{ background: value }} />
                  ) : (
                    <span className="devbar-token-preview">
                      {value.length > 12 ? value.slice(0, 12) + '…' : value}
                    </span>
                  )}
                </button>

                <span className="devbar-token-name">{name}</span>

                {/* Value — click to edit inline */}
                {isEditing ? (
                  <input
                    className="devbar-token-edit-input"
                    defaultValue={value}
                    autoFocus
                    onBlur={(e) => commitEdit(name, e.target.value, value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitEdit(name, e.currentTarget.value, value)
                      if (e.key === 'Escape') setEditingToken(null)
                    }}
                    onClick={(e) => e.stopPropagation()}
                  />
                ) : (
                  <button
                    className="devbar-token-value-btn"
                    onClick={() => setEditingToken(name)}
                    title="Click to edit"
                  >
                    {isDirty && <span className="devbar-token-dirty-dot" />}
                    {value.length > 22 ? value.slice(0, 22) + '…' : value}
                  </button>
                )}

                {copied === name && <span className="devbar-token-copied">copied</span>}
              </div>
            )
          })}
          {filteredTokens.length === 0 && (
            <span className="devbar-note">No matching tokens in this group.</span>
          )}
        </div>
      )}
    </div>
  )
}

/* ── BuildTab ────────────────────────────────────────── */

interface UserInfo {
  role?: string
  email?: string
  [key: string]: unknown
}

function BuildTab({ user }: { user: UserInfo | null }) {
  const [publishing, setPublishing] = useState(false)
  const [publishMsg, setPublishMsg] = useState<string | null>(null)
  const [commitMsg, setCommitMsg] = useState('chore: GDT reviewed change')

  const publish = async () => {
    setPublishing(true)
    setPublishMsg(null)
    try {
      const res = await fetch('/dev-api/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: commitMsg }),
      })
      const data = await res.json()
      setPublishMsg(data.ok ? 'Pushed. Railway deploy triggered.' : `Error: ${data.error}`)
    } catch (err) {
      setPublishMsg(`Failed: ${String(err)}`)
    } finally {
      setPublishing(false)
    }
  }

  return (
    <div className="devbar-panel">
      <div className="devbar-panel-row devbar-build-row">
        <span className="devbar-kv"><span>version</span><strong>{BUILD_VERSION}</strong></span>
        <span className="devbar-kv"><span>env</span><strong>{ENV}</strong></span>
        <span className="devbar-kv"><span>api route</span><strong>{import.meta.env.VITE_API_URL || 'same-origin API'}</strong></span>
        <span className="devbar-kv"><span>role</span><strong>{user?.role ?? 'none'}</strong></span>
        <span className="devbar-kv"><span>user</span><strong>{user?.email ?? 'none'}</strong></span>
      </div>

      {/* Publish pipeline */}
      <div className="devbar-panel-row devbar-publish-row">
        <span className="devbar-publish-label">Reviewed publish</span>
        <input
          className="devbar-commit-input"
          value={commitMsg}
          onChange={e => setCommitMsg(e.target.value)}
          placeholder="Git commit message after review"
          title="Use only after the branch has been implemented and reviewed"
        />
        <button
          className="devbar-publish-btn"
          onClick={publish}
          disabled={publishing}
          title="Legacy direct publish: use only after checks and review"
        >
          {publishing ? 'Pushing...' : 'Publish reviewed'}
          {!publishing && (
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>
            </svg>
          )}
        </button>
        {publishMsg && <span className="devbar-publish-msg">{publishMsg}</span>}
      </div>
      <p className="devbar-note">This is the final reviewed publish trigger. Prompt-based Draw, Tweaks, and Enchant requests should become branch/PR work first, then publish only after checks pass.</p>
    </div>
  )
}

function DesignTool() {
  const [active, setActive] = useState(false)
  const [mode, setMode] = useState<'design' | 'draw' | 'enchant' | 'tokens'>('design')
  const [selected, setSelected] = useState<InspectedEl | null>(null)
  const [designModalOpen, setDesignModalOpen] = useState(false)
  const [designModalPos, setDesignModalPos] = useState<{ x: number; y: number } | null>(null)
  const [globalStyleTarget, setGlobalStyleTarget] = useState(false)
  const [draft, setDraft] = useState<DraftElement | null>(null)
  const [enchant, setEnchant] = useState<EnchantDraft>({
    effect: 'glow',
    trigger: 'hover',
    duration: '180ms',
    easing: 'ease-out',
    rules: '',
    notes: '',
  })
  const [tweaks, setTweaks] = useState<TweakDraft>({
    background: '',
    color: '',
    borderRadius: '',
    padding: '',
    margin: '',
    width: '',
    height: '',
    opacity: '',
    transform: '',
    notes: '',
  })
  const [copied, setCopied] = useState<string | null>(null)
  const overlayRef = useRef<HTMLDivElement | null>(null)
  const drawRef = useRef<HTMLDivElement | null>(null)
  const selectedElRef = useRef<HTMLElement | null>(null)

  const copy = (text: string) => {
    navigator.clipboard.writeText(text).catch(() => {})
    setCopied(text)
    setTimeout(() => setCopied(null), 1200)
  }

  const capture = useCallback((el: Element): InspectedEl => {
    const rect = el.getBoundingClientRect()
    const cssVars = getElementCSSVars(el)
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      classes: Array.from(el.classList),
      component: getReactComponent(el),
      dims: { w: Math.round(rect.width), h: Math.round(rect.height), x: Math.round(rect.left), y: Math.round(rect.top) },
      cssVars,
      path: buildPath(el),
    }
  }, [])

  // Overlay highlighter
  const moveOverlay = useCallback((el: Element) => {
    const rect = el.getBoundingClientRect()
    if (!overlayRef.current) {
      const div = document.createElement('div')
      div.setAttribute('id', 'gdt-inspect-overlay')
      document.body.appendChild(div)
      overlayRef.current = div
    }
    Object.assign(overlayRef.current.style, {
      position: 'fixed',
      top: `${rect.top}px`,
      left: `${rect.left}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
      outline: '1.5px solid #5b9bd5',
      background: 'rgba(91,155,213,0.08)',
      pointerEvents: 'none',
      zIndex: '99999',
      borderRadius: '2px',
    })
  }, [])

  const removeOverlay = useCallback(() => {
    overlayRef.current?.remove()
    overlayRef.current = null
  }, [])

  const removeDrawOverlay = useCallback(() => {
    drawRef.current?.remove()
    drawRef.current = null
  }, [])

  const canPickElement = mode === 'design' || mode === 'enchant'

  useEffect(() => {
    if (!active || !canPickElement) {
      removeOverlay()
      document.body.removeAttribute('data-gdt-inspect')
      return
    }
    document.body.setAttribute('data-gdt-inspect', 'true')

    const onMove = (e: MouseEvent) => {
      const el = document.elementFromPoint(e.clientX, e.clientY)
      if (el && el.id !== 'gdt-inspect-overlay') moveOverlay(el)
    }
    const onClick = (e: MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const el = document.elementFromPoint(e.clientX, e.clientY)
      if (el && el.id !== 'gdt-inspect-overlay') {
        selectedElRef.current = el as HTMLElement
        setSelected(capture(el))
        setDesignModalPos({
          x: Math.min(Math.max(e.clientX + 12, 12), window.innerWidth - 460),
          y: Math.min(Math.max(e.clientY + 12, 72), window.innerHeight - 520),
        })
        setActive(false)
        if (mode === 'design') setDesignModalOpen(true)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setActive(false)
    }

    document.addEventListener('mousemove', onMove, true)
    document.addEventListener('click', onClick, true)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousemove', onMove, true)
      document.removeEventListener('click', onClick, true)
      document.removeEventListener('keydown', onKey)
      removeOverlay()
      document.body.removeAttribute('data-gdt-inspect')
    }
  }, [active, canPickElement, capture, mode, moveOverlay, removeOverlay])

  useEffect(() => {
    if (mode !== 'draw' || !active) {
      removeDrawOverlay()
      document.body.removeAttribute('data-gdt-draw')
      return
    }

    document.body.setAttribute('data-gdt-draw', 'true')
    let start: { x: number; y: number } | null = null

    const ensureDrawOverlay = () => {
      if (!drawRef.current) {
        const div = document.createElement('div')
        div.setAttribute('id', 'gdt-draw-overlay')
        document.body.appendChild(div)
        drawRef.current = div
      }
      return drawRef.current
    }

    const setRect = (x: number, y: number, w: number, h: number) => {
      const div = ensureDrawOverlay()
      Object.assign(div.style, {
        position: 'fixed',
        left: `${x}px`,
        top: `${y}px`,
        width: `${w}px`,
        height: `${h}px`,
        border: '1.5px solid #a67d43',
        background: 'rgba(166,125,67,0.14)',
        pointerEvents: 'none',
        zIndex: '99998',
        borderRadius: '6px',
      })
    }

    const onDown = (e: MouseEvent) => {
      if ((e.target as HTMLElement).closest('.devbar')) return
      e.preventDefault()
      e.stopPropagation()
      start = { x: e.clientX, y: e.clientY }
      setRect(start.x, start.y, 0, 0)
    }

    const onMove = (e: MouseEvent) => {
      if (!start) return
      const x = Math.min(start.x, e.clientX)
      const y = Math.min(start.y, e.clientY)
      const w = Math.abs(e.clientX - start.x)
      const h = Math.abs(e.clientY - start.y)
      setRect(x, y, w, h)
    }

    const onUp = (e: MouseEvent) => {
      if (!start) return
      e.preventDefault()
      e.stopPropagation()
      const x = Math.min(start.x, e.clientX)
      const y = Math.min(start.y, e.clientY)
      const w = Math.abs(e.clientX - start.x)
      const h = Math.abs(e.clientY - start.y)
      start = null
      if (w < 12 || h < 12) {
        removeDrawOverlay()
        return
      }
      setDraft({
        x: Math.round(x),
        y: Math.round(y),
        w: Math.round(w),
        h: Math.round(h),
        type: 'button',
        label: 'New component',
        action: '',
        notes: '',
      })
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setActive(false)
        removeDrawOverlay()
      }
    }

    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('mousemove', onMove, true)
    document.addEventListener('mouseup', onUp, true)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown, true)
      document.removeEventListener('mousemove', onMove, true)
      document.removeEventListener('mouseup', onUp, true)
      document.removeEventListener('keydown', onKey)
      removeDrawOverlay()
      document.body.removeAttribute('data-gdt-draw')
    }
  }, [active, mode, removeDrawOverlay])

  const aiPrompt = draft
    ? [
        'Create or modify a Ryozan React UI component from this GDT draft.',
        `Component type: ${draft.type}`,
        `Screen box: x=${draft.x}, y=${draft.y}, width=${draft.w}, height=${draft.h}`,
        `Visible label: ${draft.label}`,
        `Intended function: ${draft.action || 'not specified'}`,
        `Implementation notes: ${draft.notes || 'none'}`,
        'Use existing Ryozan design tokens, preserve auth boundaries, and wire backend/API changes only after locating the existing API pattern.',
      ].join('\n')
    : ''

  const enchantPrompt = [
    'Create or modify a Ryozan UI animation/effect from this GDT Enchant draft.',
    selected
      ? `Target selector: ${selected.path}`
      : 'Target selector: not selected. Ask for or infer the safest target before editing.',
    selected?.component ? `React component: ${selected.component}` : 'React component: unknown',
    `Effect: ${enchant.effect}`,
    `Trigger: ${enchant.trigger}`,
    `Duration: ${enchant.duration}`,
    `Easing: ${enchant.easing}`,
    `Animation rules: ${enchant.rules || 'Use a restrained Ryozan-appropriate motion pattern.'}`,
    `Implementation notes: ${enchant.notes || 'none'}`,
    'Respect prefers-reduced-motion. Keep effects scoped to the target component or selector unless explicitly asked to change system-wide motion tokens.',
    'Use existing Ryozan design tokens and run matching frontend checks after editing.',
  ].join('\n')

  const applyTweak = (patch: Partial<TweakDraft>) => {
    const next = { ...tweaks, ...patch }
    setTweaks(next)
    const el = selectedElRef.current
    if (!el) return
    el.style.background = next.background
    el.style.color = next.color
    el.style.borderRadius = next.borderRadius
    el.style.padding = next.padding
    el.style.margin = next.margin
    el.style.width = next.width
    el.style.height = next.height
    el.style.opacity = next.opacity
    el.style.transform = next.transform
  }

  const resetLiveTweaks = () => {
    const el = selectedElRef.current
    if (el) {
      el.style.background = ''
      el.style.color = ''
      el.style.borderRadius = ''
      el.style.padding = ''
      el.style.margin = ''
      el.style.width = ''
      el.style.height = ''
      el.style.opacity = ''
      el.style.transform = ''
    }
    setTweaks({
      background: '',
      color: '',
      borderRadius: '',
      padding: '',
      margin: '',
      width: '',
      height: '',
      opacity: '',
      transform: '',
      notes: '',
    })
  }

  const reusableSelector = selected?.classes.length
    ? `.${selected.classes[0]}`
    : selected?.id
      ? `#${selected.id}`
      : selected?.path || 'unknown'

  const tweakPrompt = [
    'Create or modify scoped Ryozan UI styling from this GDT Tweaks draft.',
    selected
      ? `Target selector: ${selected.path}`
      : 'Target selector: not selected. Ask for or infer the safest target before editing.',
    `Edit mode: ${globalStyleTarget ? 'global reusable selector/class' : 'selected element only'}`,
    `Reusable selector candidate: ${reusableSelector}`,
    selected?.component ? `React component: ${selected.component}` : 'React component: unknown',
    `Background: ${tweaks.background || 'unchanged'}`,
    `Text color: ${tweaks.color || 'unchanged'}`,
    `Border radius: ${tweaks.borderRadius || 'unchanged'}`,
    `Padding: ${tweaks.padding || 'unchanged'}`,
    `Margin: ${tweaks.margin || 'unchanged'}`,
    `Width: ${tweaks.width || 'unchanged'}`,
    `Height: ${tweaks.height || 'unchanged'}`,
    `Opacity: ${tweaks.opacity || 'unchanged'}`,
    `Transform: ${tweaks.transform || 'unchanged'}`,
    `Implementation notes: ${tweaks.notes || 'none'}`,
    globalStyleTarget
      ? 'Apply this to the reusable class/selector in the nearest appropriate component stylesheet so every matching element receives the update. Do not edit root design tokens unless explicitly requested.'
      : 'Keep this scoped to the selected element or its nearest safe component-specific selector. Do not edit root design tokens or broad shared classes.',
    'Use existing Ryozan design tokens where possible and run matching frontend checks after editing.',
  ].join('\n')

  const cssVarEntries = selected ? Object.entries(selected.cssVars) : []

  const switchMode = (next: typeof mode) => {
    setMode(next)
    if (next === 'tokens') {
      setActive(false)
      return
    }
    if (next === 'draw') {
      setActive(true)
      return
    }
    if (next === 'design') {
      setActive(true)
      return
    }
    setActive(!selected)
  }

  const closeDesignModal = (reset: boolean) => {
    if (reset) resetLiveTweaks()
    setDesignModalOpen(false)
    setDesignModalPos(null)
    setActive(false)
  }

  return (
    <div className="devbar-panel devbar-inspect-panel">
      <div className="devbar-panel-row devbar-inspect-controls">
        <button
          className={`devbar-inspect-toggle${mode === 'design' ? ' active' : ''}`}
          onClick={() => switchMode('design')}
          title="Select one element and open the focused design modal"
        >
          Design
        </button>
        <button
          className={`devbar-inspect-toggle${mode === 'draw' ? ' active' : ''}`}
          onClick={() => switchMode('draw')}
        >
          Draw
        </button>
        <button
          className={`devbar-inspect-toggle${mode === 'enchant' ? ' active' : ''}`}
          onClick={() => switchMode('enchant')}
          title="Draft effects and animation rules for the selected element"
        >
          Enchant
        </button>
        <button
          className={`devbar-inspect-toggle${mode === 'tokens' ? ' active' : ''}`}
          onClick={() => switchMode('tokens')}
        >
          Tokens
        </button>
        <button
          className={`devbar-inspect-toggle${active ? ' active' : ''}`}
          onClick={() => setActive(p => !p)}
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          {mode === 'draw'
            ? active ? 'Drawing... (ESC to stop)' : 'Draw Box'
            : mode === 'enchant'
              ? active ? 'Selecting... (ESC to stop)' : 'Pick Target'
            : active ? 'Selecting... (ESC to stop)' : 'Pick Element'}
        </button>
        {selected && (
          <button className="devbar-inspect-clear" onClick={() => setSelected(null)}>Clear</button>
        )}
        {draft && (
          <button className="devbar-inspect-clear" onClick={() => { setDraft(null); removeDrawOverlay() }}>Clear Draft</button>
        )}
      </div>

      {mode === 'tokens' && <DesignTab embedded />}

      {mode !== 'tokens' && !selected && !draft && (
        <p className="devbar-note">
          {mode === 'draw'
            ? active ? 'Drawing is armed. Drag anywhere on the page to size a proposed UI element.' : 'Draw mode is ready. Use Draw Box to arm placement.'
            : mode === 'enchant'
              ? active ? 'Enchant is armed. Click the element that should receive the effect.' : 'Enchant needs a target. Use Pick Target to select one.'
            : active ? 'Design is armed. Click one element to open the design modal.' : 'Design needs a target. Use Pick Element to select one.'}
        </p>
      )}

      {mode === 'draw' && draft && (
        <div className="devbar-inspect-result devbar-draft-result">
          <div className="devbar-ir-block">
            <div className="devbar-ir-section-label">Draft Element</div>
            <div className="devbar-draft-grid">
              <label>
                Type
                <select value={draft.type} onChange={e => setDraft({ ...draft, type: e.target.value as DraftElement['type'] })}>
                  <option value="button">Button</option>
                  <option value="card">Card</option>
                  <option value="panel">Panel</option>
                  <option value="input">Input</option>
                  <option value="nav">Nav</option>
                  <option value="modal">Modal</option>
                </select>
              </label>
              <label>
                Label
                <input value={draft.label} onChange={e => setDraft({ ...draft, label: e.target.value })} />
              </label>
              <label className="devbar-draft-wide">
                Function
                <input value={draft.action} onChange={e => setDraft({ ...draft, action: e.target.value })} placeholder="What should this do?" />
              </label>
              <label className="devbar-draft-wide">
                UX/backend notes
                <input value={draft.notes} onChange={e => setDraft({ ...draft, notes: e.target.value })} placeholder="Data, API, permissions, state, route, or behavior" />
              </label>
            </div>
          </div>
          <div className="devbar-ir-block devbar-ir-dims">
            <span className="devbar-ir-dim">{draft.w} x {draft.h}</span>
            <span className="devbar-ir-dim-pos">@ {draft.x}, {draft.y}</span>
          </div>
          <button className="devbar-ir-path" onClick={() => copy(aiPrompt)} title="Copy AI implementation prompt">
            Copy AI implementation prompt
            {copied === aiPrompt && <span className="devbar-token-copied">copied</span>}
          </button>
        </div>
      )}

      {mode === 'enchant' && (
        <div className="devbar-inspect-result devbar-enchant-result">
          <div className="devbar-ir-block">
            <div className="devbar-ir-section-label">Enchant Target</div>
            {selected ? (
              <>
                <div className="devbar-ir-row">
                  <span className="devbar-ir-tag">{selected.tag}</span>
                  {selected.component && <span className="devbar-ir-component">&lt;{selected.component}&gt;</span>}
                </div>
                <button className="devbar-ir-path" onClick={() => copy(selected.path)} title="Copy selector path">
                  {selected.path}
                  {copied === selected.path && <span className="devbar-token-copied">copied</span>}
                </button>
              </>
            ) : (
              <span className="devbar-note">No target selected yet. Use Pick Target before sending this to AI.</span>
            )}
          </div>

          <div className="devbar-ir-block">
            <div className="devbar-ir-section-label">Effect Rules</div>
            <div className="devbar-draft-grid devbar-enchant-grid">
              <label>
                Effect
                <select value={enchant.effect} onChange={e => setEnchant({ ...enchant, effect: e.target.value as EnchantDraft['effect'] })}>
                  <option value="fade">Fade</option>
                  <option value="slide">Slide</option>
                  <option value="scale">Scale</option>
                  <option value="glow">Glow</option>
                  <option value="pulse">Pulse</option>
                  <option value="reveal">Reveal</option>
                  <option value="custom">Custom</option>
                </select>
              </label>
              <label>
                Trigger
                <select value={enchant.trigger} onChange={e => setEnchant({ ...enchant, trigger: e.target.value as EnchantDraft['trigger'] })}>
                  <option value="hover">Hover</option>
                  <option value="click">Click</option>
                  <option value="load">Load</option>
                  <option value="scroll">Scroll</option>
                  <option value="focus">Focus</option>
                </select>
              </label>
              <label>
                Duration
                <input value={enchant.duration} onChange={e => setEnchant({ ...enchant, duration: e.target.value })} />
              </label>
              <label>
                Easing
                <input value={enchant.easing} onChange={e => setEnchant({ ...enchant, easing: e.target.value })} />
              </label>
              <label className="devbar-draft-wide">
                Rules
                <input value={enchant.rules} onChange={e => setEnchant({ ...enchant, rules: e.target.value })} placeholder="When should it run, stop, repeat, or be disabled?" />
              </label>
              <label className="devbar-draft-wide">
                Notes
                <input value={enchant.notes} onChange={e => setEnchant({ ...enchant, notes: e.target.value })} placeholder="Mood, intensity, backend state, accessibility, or component constraints" />
              </label>
            </div>
          </div>

          <button className="devbar-ir-path" onClick={() => copy(enchantPrompt)} title="Copy AI animation prompt">
            Copy Enchant AI prompt
            {copied === enchantPrompt && <span className="devbar-token-copied">copied</span>}
          </button>
        </div>
      )}

      {designModalOpen && selected && (
        <div className="devbar-modal-overlay devbar-modal-overlay--anchored" role="dialog" aria-modal="true" aria-labelledby="gdtDesignModalTitle">
          <div
            className="devbar-modal devbar-modal--tile"
            style={designModalPos ? { left: `${designModalPos.x}px`, top: `${designModalPos.y}px` } : undefined}
          >
            <div className="devbar-modal-header">
              <div>
                <div className="devbar-ir-section-label" id="gdtDesignModalTitle">Design Target</div>
                <div className="devbar-ir-row">
                  <span className="devbar-ir-tag">{selected.tag}</span>
                  {selected.id && <span className="devbar-ir-id">#{selected.id}</span>}
                  {selected.component && <span className="devbar-ir-component">&lt;{selected.component}&gt;</span>}
                  <span className="devbar-ir-dim">{selected.dims.w} x {selected.dims.h}</span>
                  <span className="devbar-ir-dim-pos">@ {selected.dims.x}, {selected.dims.y}</span>
                </div>
              </div>
              <button className="devbar-modal-close" onClick={() => closeDesignModal(true)} aria-label="Cancel design edits">Cancel</button>
            </div>

            <button className="devbar-ir-path" onClick={() => copy(selected.path)} title="Copy selector path">
              {selected.path}
              {copied === selected.path && <span className="devbar-token-copied">copied</span>}
            </button>

            <div className="devbar-tool-summary">
              <span className="devbar-tool-scope">Scope: {globalStyleTarget ? 'global selector' : 'selected element'}</span>
              <span>Selection is paused while this tile is open. Preview edits apply locally, then AI can convert them to proper component CSS.</span>
            </div>

            <label className="devbar-scope-toggle">
              <input
                type="checkbox"
                checked={globalStyleTarget}
                onChange={(e) => setGlobalStyleTarget(e.target.checked)}
              />
              <span>
                Change global selector
                <small>{reusableSelector}</small>
              </span>
            </label>

            <div className="devbar-modal-grid">
              <label>
                Background
                <input value={tweaks.background} onChange={e => applyTweak({ background: e.target.value })} placeholder="var(--bg-surface)" />
              </label>
              <label>
                Text
                <input value={tweaks.color} onChange={e => applyTweak({ color: e.target.value })} placeholder="var(--gold-vibrant)" />
              </label>
              <label>
                Radius
                <input value={tweaks.borderRadius} onChange={e => applyTweak({ borderRadius: e.target.value })} placeholder="8px" />
              </label>
              <label>
                Width
                <input value={tweaks.width} onChange={e => applyTweak({ width: e.target.value })} placeholder="auto / 320px / 100%" />
              </label>
              <label>
                Height
                <input value={tweaks.height} onChange={e => applyTweak({ height: e.target.value })} placeholder="auto / 48px" />
              </label>
              <label>
                Opacity
                <input value={tweaks.opacity} onChange={e => applyTweak({ opacity: e.target.value })} placeholder="0.95" />
              </label>
              <label className="devbar-modal-wide">
                Padding
                <input value={tweaks.padding} onChange={e => applyTweak({ padding: e.target.value })} placeholder="top right bottom left, e.g. 12px 16px" />
              </label>
              <label className="devbar-modal-wide">
                Margin
                <input value={tweaks.margin} onChange={e => applyTweak({ margin: e.target.value })} placeholder="space outside element, e.g. 0 0 16px" />
              </label>
              <label className="devbar-modal-wide">
                Transform / placement
                <input value={tweaks.transform} onChange={e => applyTweak({ transform: e.target.value })} placeholder="translateX(8px), translateY(-2px), scale(1.02)" />
              </label>
              <label className="devbar-modal-wide">
                Notes for AI
                <input value={tweaks.notes} onChange={e => setTweaks({ ...tweaks, notes: e.target.value })} placeholder="What should this look or feel like?" />
              </label>
            </div>

            {cssVarEntries.length > 0 && (
              <div className="devbar-ir-block">
                <div className="devbar-ir-section-label">Resolved Tokens On Target</div>
                <div className="devbar-design-tokens devbar-design-tokens--sm">
                  {cssVarEntries.slice(0, 10).map(([name, value]) => (
                    <button key={name} className="devbar-token-row" onClick={() => copy(name)}>
                      {isColorValue(value)
                        ? <span className="devbar-token-swatch" style={{ background: value }} />
                        : <span className="devbar-token-preview">{value.slice(0, 8)}</span>
                      }
                      <span className="devbar-token-name">{name}</span>
                      <span className="devbar-token-value">{value.slice(0, 20)}</span>
                      {copied === name && <span className="devbar-token-copied">copied</span>}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="devbar-modal-actions">
              <button className="devbar-modal-btn" onClick={() => copy(tweakPrompt)}>
                Copy AI change prompt
                {copied === tweakPrompt && <span className="devbar-token-copied">copied</span>}
              </button>
              <button className="devbar-modal-btn" onClick={resetLiveTweaks}>Reset preview</button>
              <button className="devbar-modal-btn muted" onClick={() => closeDesignModal(true)}>Cancel</button>
              <button className="devbar-modal-btn primary" onClick={() => closeDesignModal(false)}>Apply preview</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── DevModeBarTabs: renders inside topbar center ────────────── */

export function DevModeBarTabs() {
  const devMode   = useAuthStore((s) => s.devMode)
  const user      = useAuthStore((s) => s.user)
  const viewAs    = useAuthStore((s) => s.viewAs)
  const setViewAs = useAuthStore((s) => s.setViewAs)
  const gdtTab    = useAuthStore((s) => s.gdtTab)
  const setGdtTab = useAuthStore((s) => s.setGdtTab)

  const isPrivileged = user?.role === 'founder' || user?.role === 'admin'
  if (!devMode || !isPrivileged) return null

  const TABS: { id: Tab; label: string; badge?: string }[] = [
    { id: 'role',    label: 'ROLE',    badge: viewAs ?? undefined },
    { id: 'api',     label: 'API' },
    { id: 'build',   label: 'BUILD' },
    { id: 'design',  label: 'DESIGN' },
  ]

  return (
    <div className="gdt-topbar-strip">
      <span className="devbar-label">GDT</span>
      <div className="devbar-tabs">
        {TABS.map(({ id, label, badge }) => (
          <button
            key={id}
            className={`devbar-tab${gdtTab === id ? ' devbar-tab--active' : ''}`}
            onClick={() => setGdtTab(gdtTab === id ? null : id)}
          >
            {label}
            {badge && <span className="devbar-tab-badge">{badge}</span>}
          </button>
        ))}
      </div>
      {viewAs && (
        <span className="devbar-viewing">
          <strong>{viewAs}</strong>
          <button className="devbar-exit" onClick={() => setViewAs(null)}>exit</button>
        </span>
      )}
    </div>
  )
}

/* ── DevModeBar: panel only, renders below topbar ────────────── */

export default function DevModeBar() {
  const devMode   = useAuthStore((s) => s.devMode)
  const user      = useAuthStore((s) => s.user)
  const viewAs    = useAuthStore((s) => s.viewAs)
  const setViewAs = useAuthStore((s) => s.setViewAs)
  const gdtTab    = useAuthStore((s) => s.gdtTab)

  const [apiStatus, setApiStatus] = useState<ApiStatus>({ ok: false, latency: null, checked: null })
  const [pinging, setPinging]     = useState(false)

  useEffect(() => {
    if (gdtTab !== 'api') return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: show spinner before async fetch
    setPinging(true)
    pingApi().then(s => { setApiStatus(s); setPinging(false) })
  }, [gdtTab])

  const isPrivileged = user?.role === 'founder' || user?.role === 'admin'
  if (!devMode || !isPrivileged || !gdtTab) return null

  return (
    <div className="devbar devbar--panel-only" role="region" aria-label="GDT panel">
      {/* ROLE */}
      {gdtTab === 'role' && (
        <div className="devbar-panel">
          <div className="devbar-panel-row">
            {ROLE_OPTIONS.map(({ mode, label, color }) => {
              const active = viewAs === mode
              return (
                <button
                  key={mode}
                  className={`devbar-role-btn${active ? ' devbar-role-btn--active' : ''}`}
                  style={{ '--role-color': color } as React.CSSProperties}
                  onClick={() => setViewAs(active ? null : mode)}
                >
                  {label}
                  {active && <span className="devbar-role-dot" />}
                </button>
              )
            })}
          </div>
          <p className="devbar-note">UI only. API calls still use your real founder token.</p>
        </div>
      )}

      {/* API */}
      {gdtTab === 'api' && (
        <div className="devbar-panel">
          <div className="devbar-panel-row devbar-api-row">
            <span className={`devbar-api-dot${apiStatus.ok ? ' devbar-api-dot--ok' : ' devbar-api-dot--err'}`} />
            <span className="devbar-api-label">
              {pinging ? 'Pinging...' : apiStatus.ok ? 'API online' : 'API unreachable'}
            </span>
            {apiStatus.latency !== null && <span className="devbar-api-latency">{apiStatus.latency}ms</span>}
            {apiStatus.checked && !pinging && (
              <span className="devbar-api-time">checked {new Date(apiStatus.checked).toLocaleTimeString()}</span>
            )}
            <button
              className="devbar-api-refresh"
              onClick={() => { setPinging(true); pingApi().then(s => { setApiStatus(s); setPinging(false) }) }}
              disabled={pinging}
            >
              {pinging ? '...' : 'Refresh'}
            </button>
          </div>
          <p className="devbar-note">{API_DISPLAY}</p>
        </div>
      )}

      {/* BUILD */}
      {gdtTab === 'build' && <BuildTab user={user as unknown as UserInfo} />}

      {gdtTab === 'design' && <DesignTool />}
    </div>
  )
}
