/**
 * FormFieldRenderer - renders a single form field based on its type.
 *
 * Shared between DocumentHub (dashboard) and PublicFormPage (magic link).
 * Uses DOMPurify to sanitize admin-defined HTML in checkbox labels.
 */

import { useState, useRef, useCallback, useEffect } from 'react'
import DOMPurify from 'dompurify'
import SignaturePad from './SignaturePad'
import { CustomSelect } from './CustomSelect'
import { BodyMap } from './booking/BodyMap'
import { artists as artistsApi, waiver as waiverApi } from '../lib/api'
import type { FormField } from '../lib/form-utils'

interface Props {
  field: FormField
  value: string | boolean
  onChange: (name: string, value: string | boolean) => void
}

/**
 * Sanitize admin-defined HTML labels.
 * Only allows safe inline tags (links, bold, italic) - strips scripts, events, etc.
 */
function sanitizeLabel(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['a', 'b', 'strong', 'em', 'i', 'br', 'span'],
    ALLOWED_ATTR: ['href', 'target', 'rel', 'class'],
  })
}

/** The label as plain text, for aria-label: admin labels may carry inline HTML such as links. */
function plainLabel(label: string): string {
  return label.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim()
}

function displayField(field: FormField): FormField {
  if (field.name !== 'tattoo_description') return field
  return {
    ...field,
    label: 'Tattoo Description',
    placeholder: field.placeholder || 'Describe the tattoo in one sentence.',
  }
}

export default function FormFieldRenderer({ field, value, onChange }: Props) {
  field = displayField(field)
  const val = value ?? ''

  switch (field.type) {
    case 'file':
      return <FileUploadField field={field} value={val as string} onChange={onChange} />

    case 'artist_select':
      return <ArtistSelectField field={field} value={val as string} onChange={onChange} />

    case 'terms_scroll':
      return <TermsScrollField field={field} checked={!!value} onChange={onChange} />

    case 'body_map':
      return (
        <div className="doc-form-field" key={field.name}>
          <label className="doc-field-label">
            {field.label}
            {field.required && <span className="doc-required">*</span>}
          </label>
          <BodyMap
            value={val as string}
            onChange={(_, label) => onChange(field.name, label)}
          />
        </div>
      )

    case 'checkbox': {
      const hasLink = field.label.includes('<a ')
      const safeHtml = hasLink ? sanitizeLabel(field.label) : ''
      return (
        <label className="form-checkbox-label" key={field.name}>
          <input
            type="checkbox"
            name={field.name}
            aria-label={plainLabel(field.label)}
            aria-required={field.required || undefined}
            checked={!!value}
            onChange={(e) => onChange(field.name, e.target.checked)}
          />
          {hasLink ? (
            <span dangerouslySetInnerHTML={{ __html: safeHtml }} />
          ) : (
            field.label
          )}
          {field.required && <span className="doc-required">*</span>}
        </label>
      )
    }

    case 'signature':
      return (
        <div className="doc-form-field" key={field.name}>
          <label className="doc-field-label">
            {field.label}
            {field.required && <span className="doc-required">*</span>}
          </label>
          <SignaturePad
            onSign={(dataUrl) => onChange(field.name, dataUrl)}
            value={val as string}
            ariaLabel={plainLabel(field.label) || 'Signature'}
          />
        </div>
      )

    case 'textarea':
      return (
        <div className="doc-form-field" key={field.name}>
          <label className="doc-field-label">
            {field.label}
            {field.required && <span className="doc-required">*</span>}
          </label>
          <textarea
            className="form-input doc-input"
            name={field.name}
            aria-label={plainLabel(field.label)}
            rows={3}
            required={field.required}
            placeholder={field.placeholder || ''}
            maxLength={field.maxLength}
            value={val as string}
            onChange={(e) => onChange(field.name, e.target.value)}
          />
        </div>
      )

    case 'select':
      return (
        <div className="doc-form-field" key={field.name}>
          <label className="doc-field-label">
            {field.label}
            {field.required && <span className="doc-required">*</span>}
          </label>
          <CustomSelect
            value={val as string}
            onChange={(v) => onChange(field.name, v)}
            options={(field.options || []).map((opt) => ({ value: opt, label: opt }))}
            placeholder={field.placeholder || 'Select...'}
            ariaLabel={plainLabel(field.label)}
          />
        </div>
      )

    default:
      return (
        <div className="doc-form-field" key={field.name}>
          <label className="doc-field-label">
            {field.label}
            {field.required && <span className="doc-required">*</span>}
          </label>
          <input
            className="form-input doc-input"
            name={field.name}
            aria-label={plainLabel(field.label)}
            type={field.type || 'text'}
            required={field.required}
            placeholder={field.placeholder || ''}
            maxLength={field.maxLength}
            value={val as string}
            onChange={(e) => onChange(field.name, e.target.value)}
          />
        </div>
      )
  }
}


/* ── File Upload Field (uploads to API, stores URL) ── */

function FileUploadField({
  field,
  value,
  onChange,
}: {
  field: FormField
  value: string
  onChange: (name: string, value: string) => void
}) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [showGuide, setShowGuide] = useState(false)
  const cameraInputRef = useRef<HTMLInputElement>(null)

  const handleFile = async (file: File) => {
    setUploading(true)
    setError('')
    setShowGuide(false)
    try {
      const result = await waiverApi.uploadPhotoId(file)
      onChange(field.name, result.photo_id_url)
      setDisplayName(file.name)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const openCamera = () => {
    setShowGuide(false)
    // Must click a DOM-attached input: iOS Safari suspends the page while the
    // camera app is open and garbage-collects detached inputs, so a
    // createElement'd input's onchange never fires (silent photo loss).
    cameraInputRef.current?.click()
  }

  return (
    <div className="doc-form-field" key={field.name}>
      <label className="doc-field-label">
        {field.label}
        {field.required && <span className="doc-required">*</span>}
      </label>
      {error && <div className="error" style={{ marginBottom: '0.5rem', fontSize: '0.85rem' }}>{error}</div>}

      {/* Persistent camera input — stays mounted while the camera app is open */}
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: 'none' }}
        onChange={(e) => {
          if (e.target.files?.[0]) handleFile(e.target.files[0])
          e.target.value = ''
        }}
      />

      {/* Camera guide overlay */}
      {showGuide && (
        <div className="id-guide-overlay" onClick={() => setShowGuide(false)}>
          <div className="id-guide-card" onClick={(e) => e.stopPropagation()}>
            <div className="id-guide-header">
              <span>Photo ID Guide</span>
              <button type="button" className="id-guide-close" onClick={() => setShowGuide(false)}>&times;</button>
            </div>
            <div className="id-guide-frame">
              <div className="id-guide-outline">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" opacity="0.4">
                  <rect x="2" y="4" width="20" height="16" rx="2" />
                  <circle cx="8" cy="12" r="2.5" />
                  <path d="M14 10h4M14 14h4M6 18h12" />
                </svg>
              </div>
            </div>
            <div className="id-guide-tips">
              <div className="id-guide-tip">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                Front of ID only (Driver's License, Passport, State ID)
              </div>
              <div className="id-guide-tip">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                Flat surface, good lighting, no glare or shadows
              </div>
              <div className="id-guide-tip">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                All text and photo must be clearly readable
              </div>
            </div>
            <button type="button" className="id-guide-capture-btn" onClick={openCamera}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                <circle cx="12" cy="13" r="4"/>
              </svg>
              Open Camera
            </button>
          </div>
        </div>
      )}

      <div className={`id-upload-zone ${value ? 'has-file' : ''}`}>
        {uploading ? (
          <div className="id-upload-success">
            <div className="id-upload-spinner" />
            <span className="id-upload-filename">Uploading...</span>
          </div>
        ) : value ? (
          <div className="id-upload-success">
            <div className="id-upload-check">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                <polyline points="22 4 12 14.01 9 11.01"/>
              </svg>
            </div>
            <span className="id-upload-filename">{displayName || 'Uploaded'}</span>
            <button
              type="button"
              className="id-upload-change"
              onClick={() => { onChange(field.name, ''); setDisplayName('') }}
            >
              Change
            </button>
          </div>
        ) : (
          <>
            <div className="id-upload-icon">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="3" width="20" height="14" rx="2"/>
                <circle cx="9" cy="10" r="2.5"/>
                <path d="M15 7h.01"/><path d="M17 3v2"/><path d="M7 3v2"/>
                <path d="M2 21h20"/><path d="M4 17l4-4 3 3 2-2 5 5"/>
              </svg>
            </div>
            <span className="id-upload-title">Upload Photo ID</span>
            <span className="id-upload-subtitle">Take a clear photo of your government-issued ID</span>
            <div className="id-upload-actions">
              <button
                type="button"
                className="id-btn id-btn-camera"
                onClick={() => setShowGuide(true)}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                  <circle cx="12" cy="13" r="4"/>
                </svg>
                Take Photo
              </button>
              <label className="id-btn id-btn-upload">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="17 8 12 3 7 8"/>
                  <line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
                Upload File
                <input
                  type="file"
                  accept={field.accept || 'image/*'}
                  style={{ display: 'none' }}
                  onChange={(e) => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }}
                />
              </label>
            </div>
            <span className="id-upload-formats">JPG, PNG, or PDF (Max 10 MB)</span>
          </>
        )}
      </div>
    </div>
  )
}


/* ── Artist Select Field (fetches from API) ────────── */

function ArtistSelectField({
  field,
  value,
  onChange,
}: {
  field: FormField
  value: string
  onChange: (name: string, value: string) => void
}) {
  const [options, setOptions] = useState<{ value: string; label: string }[]>([])

  useEffect(() => {
    artistsApi.list()
      .then((artists) => {
        setOptions(artists.map((a) => ({ value: a.slug, label: a.name })))
      })
      // No invented roster: if the studio's artists can't load, the select stays empty.
      .catch(() => setOptions([]))
  }, [])

  return (
    <div className="doc-form-field" key={field.name}>
      <label className="doc-field-label">
        {field.label}
        {field.required && <span className="doc-required">*</span>}
      </label>
      <CustomSelect
        value={value}
        onChange={(v) => onChange(field.name, v)}
        options={options}
        placeholder={options.length === 0 ? 'Loading artists...' : 'Select your artist'}
        ariaLabel={plainLabel(field.label)}
      />
    </div>
  )
}


/* ── Terms Scroll-to-Accept Field ──────────────────── */

function TermsScrollField({
  field,
  checked,
  onChange,
}: {
  field: FormField
  checked: boolean
  onChange: (name: string, value: boolean) => void
}) {
  const [modalOpen, setModalOpen] = useState(false)
  const [scrolledToBottom, setScrolledToBottom] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30
    if (atBottom && !scrolledToBottom) setScrolledToBottom(true)
  }, [scrolledToBottom])

  /* Content is admin-defined in DB, sanitize before rendering */
  const safeHtml = DOMPurify.sanitize(field.terms_html || '', {
    ALLOWED_TAGS: ['h3', 'h4', 'p', 'strong', 'em', 'b', 'i', 'br', 'ul', 'ol', 'li', 'a', 'span'],
    ALLOWED_ATTR: ['href', 'target', 'rel', 'class'],
  })

  return (
    <>
      <label
        className={`form-checkbox-label terms-scroll-trigger ${checked ? 'terms-accepted' : ''}`}
        onClick={(e) => {
          e.preventDefault()
          if (!checked) {
            setModalOpen(true)
            setScrolledToBottom(false)
          } else {
            onChange(field.name, false)
          }
        }}
      >
        <input type="checkbox" checked={checked} readOnly aria-label={plainLabel(field.label)} aria-required={field.required || undefined} />
        <span>{field.label}</span>
        {field.required && <span className="doc-required">*</span>}
        {!checked && (
          <svg className="terms-scroll-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        )}
      </label>

      {modalOpen && (
        <div className="terms-modal-overlay" onClick={() => setModalOpen(false)}>
          <div className="terms-modal" onClick={(e) => e.stopPropagation()}>
            <div className="terms-modal-header">
              <h3>{field.terms_title || 'Terms & Conditions'}</h3>
              <button
                type="button"
                className="terms-modal-close"
                onClick={() => setModalOpen(false)}
                aria-label="Close"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
            {/* Safe: content is DOMPurify-sanitized above */}
            <div
              className="terms-modal-body"
              ref={scrollRef}
              onScroll={handleScroll}
              dangerouslySetInnerHTML={{ __html: safeHtml }}
            />
            {!scrolledToBottom && (
              <div className="terms-scroll-hint">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="6 9 12 15 18 9" />
                </svg>
                Scroll to read all terms
              </div>
            )}
            <div className="terms-modal-footer">
              <button
                type="button"
                className="terms-modal-btn terms-decline"
                onClick={() => setModalOpen(false)}
              >
                Decline
              </button>
              <button
                type="button"
                className={`terms-modal-btn terms-accept ${scrolledToBottom ? '' : 'terms-btn-locked'}`}
                disabled={!scrolledToBottom}
                onClick={() => {
                  onChange(field.name, true)
                  setModalOpen(false)
                }}
              >
                I Accept
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
