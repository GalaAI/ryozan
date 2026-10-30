/**
 * PublicFormPage: Magic-link form page at /form/:token.
 *
 * No authentication required. Recipients land here from an email link,
 * fill out the form fields, optionally sign, and submit.
 *
 * States: loading → expired | error | form → submitting → success
 */

import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  documents,
  type DocumentPublicForm,
} from '../lib/api'
import { parseFields } from '../lib/form-utils'
import FormFieldRenderer from '../components/FormFieldRenderer'
import SignaturePad from '../components/SignaturePad'
import '../styles/dashboard.css'

type PageState = 'loading' | 'form' | 'submitting' | 'success' | 'expired' | 'error'

export default function PublicFormPage() {
  const { token } = useParams<{ token: string }>()
  const [state, setState] = useState<PageState>(token ? 'loading' : 'error')
  const [formData, setFormData] = useState<DocumentPublicForm | null>(null)
  const [values, setValues] = useState<Record<string, string | boolean>>({})
  const [signature, setSignature] = useState('')
  const [error, setError] = useState(token ? '' : 'Invalid form link.')
  const [showConfirm, setShowConfirm] = useState(false)

  // ── Fetch form data on mount ──
  useEffect(() => {
    if (!token) return
    documents.getPublicForm(token)
      .then((data) => {
        if (data.expired) {
          setState('expired')
        } else {
          setFormData(data)
          setValues(data.initial_values || {})
          setState('form')
        }
      })
      .catch((err) => {
        setError(err?.message || 'Could not load form.')
        setState('error')
      })
  }, [token])

  // ── Field change handler ──
  const handleChange = (name: string, value: string | boolean) => {
    setValues((prev) => ({ ...prev, [name]: value }))
  }

  const validateRequiredFields = () => {
    const isWaiver = formData?.category === 'waiver'
    const missing = fields.find((field) => {
      const waiverRequired = isWaiver && ['tattoo_placement', 'placement', 'photo_id_url', 'photo_id'].includes(field.name)
      if (!field.required && !waiverRequired) return false
      const value = values[field.name]
      return typeof value === 'boolean' ? !value : !String(value || '').trim()
    })

    if (missing) {
      setError(`${missing.label} is required`)
      return false
    }

    if ((formData?.requires_signature || isWaiver) && !signature) {
      setError('Signature is required')
      return false
    }

    return true
  }

  // ── Submit handler ──
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!token || !formData) return
    setError('')

    if (!validateRequiredFields()) return

    setState('submitting')
    try {
      await documents.submitPublicForm(token, {
        data: values,
        signature_data: signature || undefined,
      })
      setState('success')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Submission failed'
      setError(msg)
      setState('form')
    }
  }

  // ── Parse fields from form data ──
  const fields = formData
    ? parseFields(JSON.stringify(formData.fields))
    : []

  return (
    <div className="public-form-shell">
      <div className="public-form-container">
        {/* ── Loading ── */}
        {state === 'loading' && (
          <div className="public-form-status">
            <div className="loading-spinner" />
            <p>Loading form…</p>
          </div>
        )}

        {/* ── Expired ── */}
        {state === 'expired' && (
          <div className="public-form-status">
            <div className="public-form-icon expired">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            </div>
            <h2>Form Expired</h2>
            <p>This form link has expired. Please contact the sender for a new link.</p>
          </div>
        )}

        {/* ── Error ── */}
        {state === 'error' && (
          <div className="public-form-status">
            <div className="public-form-icon error">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <line x1="15" y1="9" x2="9" y2="15" />
                <line x1="9" y1="9" x2="15" y2="15" />
              </svg>
            </div>
            <h2>Something went wrong</h2>
            <p>{error}</p>
          </div>
        )}

        {/* ── Success: Collector's Club CTA ── */}
        {state === 'success' && !showConfirm && (
          <div className="public-form-status">
            <div className="public-form-icon success">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                <polyline points="22 4 12 14.01 9 11.01" />
              </svg>
            </div>
            <h2>{formData?.title || 'Document'} Submitted</h2>
            <p>{formData?.category === 'waiver'
              ? 'Your artist will review and countersign shortly.'
              : 'Your submission has been received.'
            }</p>
            <div style={{ marginTop: '1.5rem' }}>
              <Link
                to={`/portal?email=${encodeURIComponent(values.email as string || formData?.recipient_name || '')}&name=${encodeURIComponent(values.full_name as string || formData?.recipient_name || '')}&from=waiver`}
                className="public-form-cta-btn"
              >
                Join Collector's Club
              </Link>
              <p className="public-form-cta-sub">
                Fast check-ins, aftercare tracking, and rewards
              </p>
              <button
                type="button"
                className="public-form-skip-btn"
                onClick={() => setShowConfirm(true)}
              >
                No thanks, I'm good
              </button>
            </div>
          </div>
        )}

        {/* ── Final confirmation ── */}
        {state === 'success' && showConfirm && (
          <div className="public-form-status">
            <div className="public-form-icon success">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                <polyline points="22 4 12 14.01 9 11.01" />
              </svg>
            </div>
            <h2>You're all set!</h2>
            <p>Your waiver has been submitted. You can close this page.</p>
          </div>
        )}

        {/* ── Form ── */}
        {(state === 'form' || state === 'submitting') && formData && (
          <form onSubmit={handleSubmit} className="public-form-body">
            <div className="public-form-title-block">
              <h2>{formData.title}</h2>
              {formData.description && (
                <p className="public-form-desc">{formData.description}</p>
              )}
              {formData.recipient_name && (
                <p className="public-form-recipient">
                  For: <strong>{formData.recipient_name}</strong>
                </p>
              )}
              {formData.prefill_notice && (
                <p className="public-form-prefill-notice">{formData.prefill_notice}</p>
              )}
            </div>

            {/* ── Form fields ── */}
            <div className="public-form-fields">
              {fields.map((field) => (
                <FormFieldRenderer
                  key={field.name}
                  field={field}
                  value={values[field.name] ?? ''}
                  onChange={handleChange}
                />
              ))}
            </div>

            {/* ── Signature pad ── */}
            {formData.requires_signature && (
              <div className="doc-form-field">
                <label className="doc-field-label">
                  Signature <span className="doc-required">*</span>
                </label>
                <SignaturePad
                  onSign={setSignature}
                  value={signature}
                />
              </div>
            )}

            {/* ── Error message ── */}
            {error && state === 'form' && (
              <div className="public-form-error">{error}</div>
            )}

            {/* ── Submit ── */}
            <button
              type="submit"
              className="doc-btn doc-btn-primary public-form-submit"
              disabled={state === 'submitting'}
            >
              {state === 'submitting' ? 'Submitting…' : 'Submit'}
            </button>
          </form>
        )}

        {/* ── Footer ── */}
        <div className="public-form-footer">
          <p>&copy; {new Date().getFullYear()} Ryozan Studio</p>
        </div>
      </div>
    </div>
  )
}
