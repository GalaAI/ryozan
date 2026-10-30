/**
 * ViewAsToolbar - Founder-only floating perspective switcher.
 *
 * Renders a compact toolbar pinned to the bottom-right that lets the founder
 * preview the app as different roles (Collector, Artist, Admin).
 * Only the UI rendering context changes. API calls still use the real token.
 *
 * When a View As mode is active, a tinted banner warns that you're in preview.
 * Click "Exit Preview" or the active mode again to return to your real view.
 */

import { useState, useEffect, useRef, type ReactNode } from 'react'
import { useAuthStore, type ViewAsMode } from '../stores/auth'

/* ── SVG Icons (Lucide stroke style, matches DashboardHome) ── */

const ModeIcons: Record<NonNullable<ViewAsMode>, ReactNode> = {
  collector: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
    </svg>
  ),
  assistant: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><line x1="19" y1="8" x2="19" y2="14" /><line x1="22" y1="11" x2="16" y2="11" />
    </svg>
  ),
  artist: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2z" />
      <path d="M12 2C6.5 2 2 6.5 2 12c0 1.8.5 3.5 1.3 5L12 14l8.7 3c.8-1.5 1.3-3.2 1.3-5 0-5.5-4.5-10-10-10z" />
    </svg>
  ),
  admin: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  ),
}

const EyeIcon = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
  </svg>
)

const VIEW_MODES: { mode: NonNullable<ViewAsMode>; label: string }[] = [
  { mode: 'collector',  label: 'Collector' },
  { mode: 'assistant',  label: 'Assistant' },
  { mode: 'artist',     label: 'Artist' },
  { mode: 'admin',      label: 'Admin' },
]

export default function ViewAsToolbar({ sidebar }: { sidebar?: boolean } = {}) {
  const user = useAuthStore((s) => s.user)
  const viewAs = useAuthStore((s) => s.viewAs)
  const setViewAs = useAuthStore((s) => s.setViewAs)
  const [expanded, setExpanded] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  // Close panel when clicking outside
  useEffect(() => {
    if (!expanded) return
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setExpanded(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [expanded])

  // Only founders see this toolbar
  if (user?.role !== 'founder') return null

  const activeMode = VIEW_MODES.find((m) => m.mode === viewAs)

  return (
    <>
      {/* Preview banner - shown when View As is active */}
      {viewAs && activeMode && (
        <div className="viewas-banner">
          <span className="viewas-banner__label">
            {ModeIcons[activeMode.mode]}
            <span>Viewing as <strong>{activeMode.label}</strong></span>
          </span>
          <span className="viewas-banner__divider" />
          <span className="viewas-banner__hint">UI only. API calls use your real account</span>
          <button onClick={() => setViewAs(null)} className="viewas-banner__exit">
            Exit Preview
          </button>
        </div>
      )}

      {/* Toolbar */}
      <div ref={wrapRef} className={sidebar ? 'viewas-sidebar' : 'viewas-float'}>
        {/* Expanded mode buttons */}
        {expanded && (
          <div className="viewas-panel">
            <div className="viewas-panel__heading">View As</div>
            {VIEW_MODES.map(({ mode, label }) => {
              const isActive = viewAs === mode
              return (
                <button
                  key={mode}
                  onClick={() => {
                    setViewAs(isActive ? null : mode)
                    if (!isActive) setExpanded(false)
                  }}
                  className={`viewas-panel__btn ${isActive ? 'viewas-panel__btn--active' : ''}`}
                >
                  <span className="viewas-panel__btn-icon">{ModeIcons[mode]}</span>
                  <span>{label}</span>
                  {isActive && <span className="viewas-panel__btn-badge">Active</span>}
                </button>
              )
            })}
          </div>
        )}

        {/* Toggle button */}
        <button
          onClick={() => setExpanded((e) => !e)}
          className={`viewas-toggle ${viewAs ? 'viewas-toggle--active' : ''}`}
        >
          {EyeIcon}
          <span>{viewAs && activeMode ? activeMode.label : 'View'}</span>
        </button>
      </div>
    </>
  )
}
