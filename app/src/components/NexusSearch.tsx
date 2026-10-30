/**
 * NexusSearch -- Global command-palette search for the Nexus dashboard.
 *
 * Phase 1: Client-side navigation shortcuts + quick actions
 * Phase 2: Wire to /api/search for cross-entity results (bookings, clients, docs, messages)
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { ROUTES } from '../lib/routes'
import { useNavigate } from 'react-router-dom'

interface SearchResult {
  id: string
  label: string
  desc: string
  icon: React.ReactNode
  action: () => void
  category: string
}

const SEARCH_ICON = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="8" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </svg>
)

/* Static navigation shortcuts -- always available */
function getNavResults(navigate: ReturnType<typeof useNavigate>): SearchResult[] {
  return [
    { id: 'nav-hub', label: 'Hub', desc: 'Dashboard overview', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.DASHBOARD) },
    { id: 'nav-bookings', label: 'Bookings', desc: 'View all bookings', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.BOOKINGS) },
    { id: 'nav-clients', label: 'Clients', desc: 'Client roster', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.CLIENTS) },
    { id: 'nav-schedule', label: 'Schedule', desc: 'View your schedule', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.SCHEDULE) },
    { id: 'nav-codex', label: 'Codex', desc: 'Waivers, compliance, documents', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.ASSETS) },
    { id: 'nav-messages', label: 'Messages', desc: 'Direct messages', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.COURIER) },
    { id: 'nav-profile', label: 'Artist Profile', desc: 'Edit portfolio & bio', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.ARTIST_PROFILE) },
    { id: 'nav-wallet', label: 'Wallet', desc: 'RYO reward balance', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.WALLET) },
    { id: 'nav-settings', label: 'Settings', desc: 'Account settings', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.SETTINGS) },
    { id: 'nav-quests', label: 'Quests', desc: 'Active quests & rewards', icon: SEARCH_ICON, category: 'Navigate', action: () => navigate(ROUTES.QUESTS) },
  ]
}

export default function NexusSearch() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [selectedIdx, setSelectedIdx] = useState(0)

  // Filter results by query
  const allResults = getNavResults(navigate)
  const filtered = query.trim()
    ? allResults.filter(
        (r) =>
          r.label.toLowerCase().includes(query.toLowerCase()) ||
          r.desc.toLowerCase().includes(query.toLowerCase()) ||
          r.category.toLowerCase().includes(query.toLowerCase())
      )
    : allResults

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setSelectedIdx(0) }, [filtered.length])

  // Keyboard shortcut: Ctrl+K or / to open
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        setOpen(true)
      }
      if (e.key === '/' && !open && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
        e.preventDefault()
        setOpen(true)
      }
      if (e.key === 'Escape' && open) {
        setOpen(false)
        setQuery('')
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [open])

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
        setQuery('')
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  // Execute result
  const executeResult = useCallback((result: SearchResult) => {
    result.action()
    setOpen(false)
    setQuery('')
  }, [])

  // Keyboard navigation in dropdown
  function handleInputKey(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx((i) => Math.min(i + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && filtered[selectedIdx]) {
      e.preventDefault()
      executeResult(filtered[selectedIdx])
    }
  }

  return (
    <div className="NAV_BAR__search nexus-search-wrap" ref={wrapRef}>
      {/* Collapsed trigger */}
      <button
        className="nexus-search-trigger"
        onClick={() => setOpen(true)}
      >
        {SEARCH_ICON}
        <span className="nexus-search-placeholder">Search...</span>
      </button>

      {/* Expanded dropdown */}
      {open && (
        <div className="nexus-search-dropdown">
          <div className="nexus-search-input-row">
            {SEARCH_ICON}
            <input
              ref={inputRef}
              type="text"
              className="nexus-search-input"
              placeholder="Search pages, actions, clients..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleInputKey}
            />
            {query && (
              <button className="nexus-search-clear" onClick={() => setQuery('')}>&times;</button>
            )}
          </div>
          <div className="nexus-search-results">
            {filtered.length === 0 ? (
              <div className="nexus-search-empty">No results</div>
            ) : (
              filtered.map((r, i) => (
                <button
                  key={r.id}
                  className={`nexus-search-result${i === selectedIdx ? ' selected' : ''}`}
                  onClick={() => executeResult(r)}
                  onMouseEnter={() => setSelectedIdx(i)}
                >
                  <div className="nexus-search-result-info">
                    <span className="nexus-search-result-label">{r.label}</span>
                    <span className="nexus-search-result-desc">{r.desc}</span>
                  </div>
                  <span className="nexus-search-result-cat">{r.category}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
