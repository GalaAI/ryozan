/* eslint-disable react-refresh/only-export-components */
/**
 * Shared form utilities - used by DocumentHub (dashboard) and PublicFormPage (magic link).
 *
 * Extracted from DocumentsPage.tsx to avoid duplication across form-rendering contexts.
 */

import type { ReactNode } from 'react'

// ── Types ──────────────────────────────────────────

export interface FormField {
  name: string
  label: string
  type: string
  required?: boolean
  placeholder?: string
  options?: string[]
  maxLength?: number
  accept?: string          // file input accept attribute
  terms_title?: string     // title for scroll-to-accept modal
  terms_html?: string      // HTML content for terms modal body
}

// ── Parsers ────────────────────────────────────────

/**
 * Parse a template's `fields_json` string into an array of FormField objects.
 * Handles both `{ fields: [...] }` wrapper format and raw `[...]` arrays.
 */
export function parseFields(fieldsJson: string | undefined | null): FormField[] {
  try {
    const raw = JSON.parse(fieldsJson || '{}')
    return Array.isArray(raw) ? raw : (raw.fields || [])
  } catch {
    return []
  }
}

// ── Constants ──────────────────────────────────────

/** SVG icons for document categories - stroke-based, inherits color from parent. */
export const CATEGORY_ICONS: Record<string, ReactNode> = {
  waiver: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/>
      <rect x="9" y="3" width="6" height="4" rx="1"/>
      <line x1="9" y1="12" x2="15" y2="12"/>
      <line x1="9" y1="16" x2="13" y2="16"/>
    </svg>
  ),
  aftercare: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z"/>
      <line x1="12" y1="10" x2="12" y2="16"/>
      <line x1="9" y1="13" x2="15" y2="13"/>
    </svg>
  ),
  employment: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="14" rx="2"/>
      <path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/>
      <line x1="12" y1="12" x2="12" y2="16"/>
      <line x1="10" y1="14" x2="14" y2="14"/>
    </svg>
  ),
  license: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="5" width="20" height="14" rx="2"/>
      <circle cx="8" cy="12" r="2.5"/>
      <line x1="14" y1="10" x2="19" y2="10"/>
      <line x1="14" y1="14" x2="18" y2="14"/>
    </svg>
  ),
  upload: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
      <polyline points="12 13 12 17"/>
      <polyline points="9 15 12 12 15 15"/>
    </svg>
  ),
}

/** Default icon for unknown categories */
export const DEFAULT_CATEGORY_ICON: ReactNode = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
    <polyline points="14 2 14 8 20 8"/>
  </svg>
)

export const STATUS_COLORS: Record<string, string> = {
  draft: 'status-draft',
  sent: 'status-pending',
  viewed: 'status-pending',
  submitted: 'status-pending',
  signed: 'status-confirmed',
  approved: 'status-confirmed',
  rejected: 'status-cancelled',
  expired: 'status-cancelled',
}

export const CATEGORIES = [
  { key: '', label: 'All' },
  { key: 'waiver', label: 'Waivers' },
  { key: 'aftercare', label: 'Aftercare' },
  { key: 'employment', label: 'Employment' },
  { key: 'license', label: 'Licenses' },
  { key: 'upload', label: 'Uploads' },
] as const

// ── Helpers ────────────────────────────────────────

/** Format an ISO date string for display. Returns "-" for null/undefined. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleDateString()
}

/** Format an ISO date string with time. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

/** Time-ago string for sent/viewed timestamps. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '-'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}
