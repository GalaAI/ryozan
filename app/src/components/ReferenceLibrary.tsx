/**
 * ReferenceLibrary: Upload, browse, and view reference images/files.
 *
 * Uses scroll-tile tombstone pattern from the design system.
 * Pinned items sort first, then alphabetical by filename.
 */

import { useEffect, useState, useRef, useCallback } from 'react'
import { getToken, API_BASE } from '../lib/api'
import '../styles/reference-library.css'

/* ── Types ────────────────────────────────────────────────── */

interface Reference {
  id: string
  filename: string
  original_filename: string
  mime_type: string
  file_size: number
  file_url: string
  uploaded_by: string
  is_pinned: boolean
  pin_order: number
  tags: string[]
  view_count: number
  created_at: string
}

type ViewMode = 'fit' | 'fill' | 'actual'

interface ViewModal {
  open: boolean
  ref: Reference | null
  viewMode: ViewMode
}

/* ── Icons ────────────────────────────────────────────────── */

const Icons = {
  plus: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
    </svg>
  ),
  eye: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
    </svg>
  ),
  share: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/>
    </svg>
  ),
  pin: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="17" x2="12" y2="22"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z"/>
    </svg>
  ),
  doc: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
  ),
  search: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
    </svg>
  ),
  x: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>
  ),
  book: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
    </svg>
  ),
}

/* ── Helpers ───────────────────────────────────────────────── */

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function isImageType(mime: string): boolean {
  return mime.startsWith('image/')
}

function isPdfType(mime: string): boolean {
  return mime === 'application/pdf'
}

const RECENTLY_VIEWED_KEY = 'ryozan_ref_recently_viewed'

function getStoredRecentlyViewed(): string[] {
  try {
    const raw = localStorage.getItem(RECENTLY_VIEWED_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function storeRecentlyViewed(ids: string[]) {
  localStorage.setItem(RECENTLY_VIEWED_KEY, JSON.stringify(ids.slice(0, 10)))
}

/* ── Component ─────────────────────────────────────────────── */

export default function ReferenceLibrary() {
  const token = getToken()

  const [references, setReferences] = useState<Reference[]>([])
  const [recentlyViewed, setRecentlyViewed] = useState<string[]>(getStoredRecentlyViewed)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')
  const [viewModal, setViewModal] = useState<ViewModal>({ open: false, ref: null, viewMode: 'fit' })
  const [dragOver, setDragOver] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)

  /* ── Fetch references ── */

  // Fetch only: state is written once the request answers.
  const requestRefs = useCallback(async () => {
    if (!token) return
    await fetch(`${API_BASE}/references/`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load references`)
        return res.json()
      })
      .then((data) => setReferences(Array.isArray(data) ? data : data.references ?? []))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Failed to load references'))
      .finally(() => setLoading(false))
  }, [token])

  const fetchRefs = async () => {
    if (!token) return
    setLoading(true)
    setError(null)
    await requestRefs()
  }

  // A new token starts a fresh load: reset during render, fetch in the effect.
  const [refsToken, setRefsToken] = useState(token)
  if (refsToken !== token) {
    setRefsToken(token)
    if (token) {
      setLoading(true)
      setError(null)
    }
  }

  useEffect(() => {
    requestRefs()
  }, [requestRefs])

  /* ── Upload ── */

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0 || !token) return
    setUploading(true)
    setError(null)
    try {
      for (const file of Array.from(files)) {
        const fd = new FormData()
        fd.append('file', file)
        const res = await fetch(`${API_BASE}/references/upload`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: fd,
        })
        if (!res.ok) throw new Error(`Upload failed for ${file.name}`)
      }
      await fetchRefs()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  /* ── View ── */

  const handleView = async (ref: Reference) => {
    setViewModal({ open: true, ref, viewMode: 'fit' })
    // Record view
    if (token) {
      fetch(`${API_BASE}/references/${ref.id}/view`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => {})
    }
    // Update recently viewed
    const updated = [ref.id, ...recentlyViewed.filter(id => id !== ref.id)].slice(0, 10)
    setRecentlyViewed(updated)
    storeRecentlyViewed(updated)
  }

  /* ── Pin toggle ── */

  const handlePin = async (ref: Reference) => {
    if (!token) return
    try {
      const res = await fetch(`${API_BASE}/references/${ref.id}/pin`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ is_pinned: !ref.is_pinned }),
      })
      if (!res.ok) throw new Error('Pin toggle failed')
      setReferences(prev =>
        prev.map(r => r.id === ref.id ? { ...r, is_pinned: !r.is_pinned } : r)
      )
    } catch {
      // silent
    }
  }

  /* ── Copy URL ── */

  const handleCopyUrl = (ref: Reference) => {
    navigator.clipboard.writeText(ref.file_url).catch(() => {})
  }

  /* ── Drag and drop ── */

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); setDragOver(true) }
  const onDragLeave = () => setDragOver(false)
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    handleUpload(e.dataTransfer.files)
  }

  /* ── Sort and filter ── */

  const filtered = references
    .filter(r => !searchTerm || r.original_filename.toLowerCase().includes(searchTerm.toLowerCase()))
    .sort((a, b) => {
      if (a.is_pinned && !b.is_pinned) return -1
      if (!a.is_pinned && b.is_pinned) return 1
      if (a.is_pinned && b.is_pinned) return (a.pin_order ?? 0) - (b.pin_order ?? 0)
      return a.original_filename.localeCompare(b.original_filename)
    })

  const recentRefs = recentlyViewed
    .map(id => references.find(r => r.id === id))
    .filter((r): r is Reference => !!r)

  /* ── Render ── */

  return (
    <div>
      {/* Upload area */}
      <div
        className={`ref-upload-zone ${dragOver ? 'drag-over' : ''}`}
        onClick={() => fileInputRef.current?.click()}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,.pdf"
          style={{ display: 'none' }}
          onChange={e => handleUpload(e.target.files)}
        />
        {uploading ? (
          <div className="ref-spinner" />
        ) : (
          <>
            <span className="ref-upload-zone-icon">{Icons.plus}</span>
            <span className="ref-upload-zone-label">Add Reference</span>
            <span className="ref-upload-zone-hint">Upload</span>
          </>
        )}
      </div>

      {error && <div className="ref-error">{error}</div>}

      {/* Search */}
      <div className="ref-search-wrap">
        <span className="ref-search-icon">{Icons.search}</span>
        <input
          type="text"
          className="ref-search-input"
          placeholder="Search references..."
          value={searchTerm}
          onChange={e => setSearchTerm(e.target.value)}
        />
      </div>

      {/* Recently Viewed */}
      {recentRefs.length > 0 && (
        <>
          <div className="ref-recently-label">Recently Viewed</div>
          <div className="ref-recently-row">
            {recentRefs.map(r => (
              <div
                key={r.id}
                className="ref-recently-item"
                onClick={() => handleView(r)}
                title={r.original_filename}
              >
                {isImageType(r.mime_type) ? (
                  <img src={r.file_url} alt={r.original_filename} />
                ) : (
                  <span className="ref-tile-thumb-icon">{Icons.doc}</span>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Grid */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '2rem' }}>
          <div className="ref-spinner" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="ref-empty">
          <span>{Icons.book}</span>
          <p>{searchTerm ? 'No references match your search' : 'No references yet'}</p>
        </div>
      ) : (
        <div className="ref-grid">
          {filtered.map(ref => (
            <div key={ref.id} className="ref-tile-wrapper">
              <div className="ref-tile" onClick={() => handleView(ref)}>
                {ref.is_pinned && <span className="ref-pin-dot" />}
                <div className="ref-tile-thumb">
                  {isImageType(ref.mime_type) ? (
                    <img src={ref.file_url} alt={ref.original_filename} />
                  ) : (
                    <span className="ref-tile-thumb-icon">{Icons.doc}</span>
                  )}
                </div>
                <span className="ref-tile-name" title={ref.original_filename}>
                  {ref.original_filename}
                </span>
                <span className="ref-tile-meta">{formatFileSize(ref.file_size)}</span>
              </div>
              <div className="ref-tile-actions">
                <button
                  className="ref-tile-action-btn"
                  title="Open"
                  onClick={() => handleView(ref)}
                >
                  {Icons.eye}
                </button>
                <button
                  className="ref-tile-action-btn"
                  title="Copy URL"
                  onClick={() => handleCopyUrl(ref)}
                >
                  {Icons.share}
                </button>
                <button
                  className={`ref-tile-action-btn ${ref.is_pinned ? 'ref-pinned' : ''}`}
                  title={ref.is_pinned ? 'Unpin' : 'Pin'}
                  onClick={() => handlePin(ref)}
                >
                  {Icons.pin}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* View Modal */}
      {viewModal.open && viewModal.ref && (
        <div className="ref-view-overlay" onClick={() => setViewModal({ open: false, ref: null, viewMode: 'fit' })}>
          <div className="ref-view-panel" onClick={e => e.stopPropagation()}>
            <div className="ref-view-header">
              <div style={{ flex: 1, minWidth: 0 }}>
                <h3 className="ref-view-title">{viewModal.ref.original_filename}</h3>
                <span className="ref-view-meta">
                  {formatFileSize(viewModal.ref.file_size)} &middot; {viewModal.ref.view_count} views
                </span>
              </div>
              <div className="ref-view-modes">
                {(['fit', 'fill', 'actual'] as ViewMode[]).map(mode => (
                  <button
                    key={mode}
                    className={`ref-view-mode-btn ${viewModal.viewMode === mode ? 'active' : ''}`}
                    onClick={() => setViewModal(prev => ({ ...prev, viewMode: mode }))}
                  >
                    {mode}
                  </button>
                ))}
              </div>
              <button
                className="ref-view-close"
                onClick={() => setViewModal({ open: false, ref: null, viewMode: 'fit' })}
              >
                {Icons.x}
              </button>
            </div>
            <div className="ref-view-content">
              {isImageType(viewModal.ref.mime_type) ? (
                <img
                  src={viewModal.ref.file_url}
                  alt={viewModal.ref.original_filename}
                  className={`ref-view-${viewModal.viewMode}`}
                />
              ) : isPdfType(viewModal.ref.mime_type) ? (
                <iframe src={viewModal.ref.file_url} title={viewModal.ref.original_filename} />
              ) : (
                <div className="ref-empty">
                  <span>{Icons.doc}</span>
                  <p>Preview not available for this file type</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
