/**
 * CheckinPage: the shop's check-in kiosk at /checkin and /checkin/:artistSlug (and legacy /checkin?t=<token>).
 *
 * Three steps, then done: 01 You (find a booking or last visit by email or mobile plus a one-time code, or
 * check in as new), 02 Health, 03 Sign. Built for a shared tablet: after KIOSK_IDLE_RESET_MINUTES with no
 * input the whole flow is remounted from scratch, so nothing one client typed is left for the next.
 *
 * Everything shop specific (studio name, artists, waiver terms) is read from the install at runtime. Built
 * only from @ryozan/ui and existing Ryozan parts (FormFieldRenderer for the ID upload and consents,
 * SignaturePad, the public form shell). Server contract: api/app/api/v1/endpoints/documents.py.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Helmet } from 'react-helmet-async'
import DOMPurify from 'dompurify'
import {
  Badge, Button, Chip, ChipGroup, MetaList, PageTitle, Panel, SectionLabel, StatusText, TextField, token as ds,
} from '@ryozan/ui'
import { QRCodeSVG } from 'qrcode.react'
import {
  artists as artistsApi,
  documents,
  type ArtistSummary,
  type CheckinVerifyResult,
  type DocumentPublicForm,
} from '../lib/api'
import {
  buildWaiverData, contactOk, detailsErrors, digits, healthMissing, healthNeedsReview,
  isoDate, profileHandoffUrl, questionsToAsk, sameAsLast, splitEmergency, visitErrors,
  type CheckinDetails, type CheckinVisit, type HealthState, type YesNo,
} from '../lib/checkin'
import { useKioskIdleReset } from '../hooks/useKioskIdleReset'
import { useStudioSettings } from '../hooks/useStudioSettings'
import FormFieldRenderer from '../components/FormFieldRenderer'
import SignaturePad from '../components/SignaturePad'
import '../styles/dashboard.css'

type Stage = 0 | 1 | 2 | 3 | 4

const STEP_NAMES: Record<number, string> = { 1: 'You', 2: 'Health', 3: 'Sign' }

const EMPTY_DETAILS: CheckinDetails = {
  fullName: '', dob: '', phone: '', email: '', address: '', emergencyName: '', emergencyPhone: '', emergencyAddress: '',
}

const CONSENTS = [
  { name: 'risks_and_consent', label: 'I read the waiver terms and my questions were answered.' },
  { name: 'health_and_eligibility', label: 'I am 18 or older, sober, and my health answers are true.' },
  { name: 'accept_terms', label: 'I choose to get this tattoo.' },
] as const

const ID_FIELD = { name: 'photo_id_url', label: 'Photo ID', type: 'file', required: true, accept: 'image/*,application/pdf' }

const column = { width: '100%', display: 'grid', gap: 'var(--space-5)' } as const
const panelBody = { padding: 'var(--space-5)', gap: 'var(--space-4)' } as const
const twoUp = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 12rem), 1fr))', gap: 'var(--space-4)' } as const
const actions = { display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' } as const

function sanitizeTerms(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['h3', 'h4', 'p', 'strong', 'em', 'b', 'i', 'br', 'ul', 'ol', 'li', 'a', 'span'],
    ALLOWED_ATTR: ['href', 'target', 'rel'],
  })
}

function prettyDate(iso: string | null | undefined, time?: string | null): string {
  if (!iso) return ''
  const day = new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
  return time ? `${day} · ${time}` : day
}

function monthYear(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : ''
}


function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

/** Route wrapper: owns the idle reset. A reset remounts the flow, which drops every piece of state. */
export default function CheckinPage() {
  const { artistSlug } = useParams<{ artistSlug?: string }>()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get('t')
  const [session, setSession] = useState(0)
  const [dirty, setDirty] = useState(false)

  const reset = useCallback(() => {
    setDirty(false)
    setSession((n) => n + 1)
    // A link token belongs to one client; the tablet goes back to the plain kiosk start.
    if (token) navigate(artistSlug ? `/checkin/${encodeURIComponent(artistSlug)}` : '/checkin', { replace: true })
  }, [artistSlug, navigate, token])

  useKioskIdleReset(reset, { enabled: dirty })

  return (
    <CheckinFlow
      key={`${session}:${token ?? ''}:${artistSlug ?? ''}`}
      token={token}
      routeArtist={artistSlug ?? null}
      onDirtyChange={setDirty}
      onStartOver={reset}
    />
  )
}

function CheckinFlow({ token: linkToken, routeArtist, onDirtyChange, onStartOver }: {
  token: string | null
  routeArtist: string | null
  onDirtyChange: (dirty: boolean) => void
  onStartOver: () => void
}) {
  const { settings } = useStudioSettings()
  const studioName = settings.studio_name

  const [stage, setStage] = useState<Stage>(1)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})

  // 01 lookup
  const [contact, setContact] = useState('')
  const [code, setCode] = useState('')
  const [challenge, setChallenge] = useState<{ id: string; sentTo: string } | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [ctx, setCtx] = useState<CheckinVerifyResult | null>(null)
  const [token, setToken] = useState<string | null>(linkToken)
  const [form, setForm] = useState<DocumentPublicForm | null>(null)
  const [dead, setDead] = useState<{ title: string; body: string } | null>(null)

  // 01 details
  const [crew, setCrew] = useState<ArtistSummary[]>([])
  const [artistLocked, setArtistLocked] = useState(Boolean(routeArtist))
  const [visit, setVisit] = useState<CheckinVisit>({ artistSlug: routeArtist ?? '', description: '', placement: '', price: '', date: '' })
  const [details, setDetails] = useState<CheckinDetails>(EMPTY_DETAILS)
  const [detailsCollapsed, setDetailsCollapsed] = useState(false)
  const [prefillNote, setPrefillNote] = useState('')
  const [photoIdUrl, setPhotoIdUrl] = useState('')
  const [reusePhotoId, setReusePhotoId] = useState(false)

  // 02 health
  const [health, setHealth] = useState<HealthState>({ answers: {}, sameOffered: false, allergyDetail: '', medications: '' })

  // 03 sign
  const [consents, setConsents] = useState<Record<string, boolean>>({})
  const [signature, setSignature] = useState('')
  const [reuseSignature, setReuseSignature] = useState(false)
  const [reference, setReference] = useState('')
  const [profileQr, setProfileQr] = useState(false)

  useEffect(() => {
    onDirtyChange(stage !== 1 || detailsOpen || contact !== '' || challenge !== null)
  }, [stage, detailsOpen, contact, challenge, onDirtyChange])

  useEffect(() => {
    let active = true
    artistsApi.list()
      .then((rows) => { if (active) setCrew(rows.filter((a) => a.slug && a.name)) })
      // No invented roster: if the shop's artists cannot load, the front desk picks with the client.
      .catch(() => { if (active) setCrew([]) })
    return () => { active = false }
  }, [])

  const go = (next: Stage) => {
    setStage(next)
    setErrors({})
    setNotice('')
    window.scrollTo(0, 0)
  }

  /** Open the details card. `res` is a verified lookup; `typed` is what a walk-in typed in the finder. */
  const openDetails = useCallback(async (res: CheckinVerifyResult | null, typed: string, formToken: string | null) => {
    setCtx(res)
    setDetailsOpen(true)
    const base: CheckinDetails = { ...EMPTY_DETAILS }
    if (!res && typed) {
      if (typed.includes('@')) base.email = typed
      else base.phone = typed
    }
    const booking = res?.booking
    setVisit((v) => ({
      ...v,
      description: v.description || booking?.description || '',
      placement: v.placement || booking?.placement || '',
      date: v.date || booking?.date?.slice(0, 10) || isoDate(),
      artistSlug: v.artistSlug || booking?.artist_slug || '',
    }))
    if (booking?.artist_slug) setArtistLocked(true)
    const onFile = res?.on_file
    if (onFile?.photo_id_url) setReusePhotoId(true)
    if (onFile?.signature_url) setReuseSignature(true)
    if (onFile && Object.keys(onFile.health || {}).length) setHealth((h) => ({ ...h, sameOffered: true, prior: onFile.health }))

    if (!formToken) {
      setDetails(base)
      return
    }
    try {
      const loaded = await documents.getPublicForm(formToken)
      if (loaded.expired) {
        setDead({ title: 'Link expired', body: 'This check-in link has expired or was already signed. Ask the front desk for a fresh one.' })
        setStage(0)
        return
      }
      setForm(loaded)
      const v = loaded.initial_values || {}
      const [ecName, ecPhone] = splitEmergency(v.emergency_contact)
      const merged: CheckinDetails = {
        fullName: base.fullName || v.full_name || '',
        dob: base.dob || v.date_of_birth || '',
        phone: base.phone || v.phone || '',
        email: base.email || v.email || '',
        address: base.address || v.street_address || '',
        emergencyName: base.emergencyName || ecName,
        emergencyPhone: base.emergencyPhone || ecPhone,
        emergencyAddress: base.emergencyAddress || v.emergency_contact_address || '',
      }
      setDetails(merged)
      if (v.artist_name) {
        setVisit((cur) => (cur.artistSlug ? cur : { ...cur, artistSlug: v.artist_name }))
        setArtistLocked(true)
      }
      const filled = [v.full_name, v.email, v.phone, v.date_of_birth, v.street_address].some(Boolean)
      if (filled) {
        setPrefillNote(`Filled from ${onFile ? 'your last visit' : booking ? 'your booking' : 'your record'}. Edit anything that changed.`)
      }
      if (onFile && Object.keys(detailsErrors(merged)).length === 0) setDetailsCollapsed(true)
    } catch (err) {
      setDetails(base)
      const status = (err as { status?: number }).status
      if (status === 404 || status === 410) {
        setDead({ title: 'Link expired', body: 'This check-in link is not valid any more. Ask the front desk for a fresh one.' })
        setStage(0)
        return
      }
      setNotice(messageOf(err, 'Your check-in could not be loaded. Ask the front desk.'))
    }
  }, [])

  // A link (?t=) skips the finder: the staff already know who this is.
  useEffect(() => {
    if (linkToken) void openDetails(null, '', linkToken)
  }, [linkToken, openDetails])

  const lookup = async () => {
    const value = contact.trim()
    if (!contactOk(value)) {
      setErrors({ contact: 'Enter an email or a 10 digit mobile number.' })
      return
    }
    setErrors({})
    setBusy(true)
    try {
      const res = await documents.checkinLookup(value)
      if (res.found) {
        setChallenge({ id: res.challenge, sentTo: res.sent_to })
        setCode('')
      } else {
        await openDetails(null, value, null)
      }
    } catch (err) {
      setErrors({ contact: messageOf(err, 'That did not work. Try again or ask the front desk.') })
    } finally {
      setBusy(false)
    }
  }

  const verify = async () => {
    if (!challenge) return
    const entered = digits(code)
    if (entered.length !== 6) {
      setErrors({ code: 'The code is 6 digits.' })
      return
    }
    setErrors({})
    setBusy(true)
    try {
      const res = await documents.checkinVerify(challenge.id, entered)
      setToken(res.token)
      await openDetails(res, '', res.token)
    } catch (err) {
      setErrors({ code: messageOf(err, 'That code does not match.') })
    } finally {
      setBusy(false)
    }
  }

  const stepOne = async () => {
    const found = {
      ...visitErrors(visit),
      ...detailsErrors(details),
      ...(reusePhotoId || photoIdUrl ? {} : { photoId: 'We need a photo of your ID.' }),
    }
    setErrors(found)
    if (Object.keys(found).length) {
      if (detailsCollapsed && Object.keys(detailsErrors(details)).length) setDetailsCollapsed(false)
      setNotice('Check the highlighted fields.')
      return
    }
    if (token) {
      if (!form) {
        try { setForm(await documents.getPublicForm(token)) } catch { /* terms fall back to the front desk */ }
      }
      go(2)
      return
    }
    setBusy(true)
    try {
      const res = await documents.checkin({ name: details.fullName.trim(), email: details.email.trim(), artist_slug: visit.artistSlug || undefined })
      setToken(res.token)
      try { setForm(await documents.getPublicForm(res.token)) } catch { /* terms fall back to the front desk */ }
      go(2)
    } catch (err) {
      setNotice(messageOf(err, 'Check-in could not start. Ask the front desk.'))
    } finally {
      setBusy(false)
    }
  }

  const stepTwo = () => {
    const missing = healthMissing(health)
    if (missing.length) {
      setErrors(Object.fromEntries(missing.map((k) => [k, k === 'allergyDetail' ? 'Tell us what.' : 'Pick one.'])))
      setNotice('Answer every question.')
      return
    }
    if (health.answers.intoxicated !== 'No') return
    go(3)
  }

  const submit = async () => {
    const found: Record<string, string> = {}
    for (const c of CONSENTS) if (!consents[c.name]) found[c.name] = 'Required.'
    if (!reuseSignature && !signature) found.signature = 'Sign above to finish.'
    setErrors(found)
    if (Object.keys(found).length || !token) {
      setNotice(token ? 'Confirm all three statements and sign.' : 'Your check-in expired. Start over.')
      return
    }
    setBusy(true)
    setNotice('')
    try {
      const data = buildWaiverData({
        details, visit, health,
        photoIdUrl, reusePhotoId, reuseSignature,
      })
      const res = await documents.submitPublicForm(token, { data, signature_data: reuseSignature ? undefined : signature })
      const ref = (res as unknown as { reference_code?: string }).reference_code
      setReference(ref ?? '')
      go(4)
    } catch (err) {
      setNotice(messageOf(err, 'Your waiver could not be filed. Try again or ask the front desk.'))
    } finally {
      setBusy(false)
    }
  }

  const terms = useMemo(
    () => (form?.fields ?? []).filter((f) => f.type === 'terms_scroll' && typeof f.terms_html === 'string' && f.terms_html),
    [form],
  )

  const setAnswer = (key: string, value: YesNo) => {
    setHealth((h) => ({ ...h, answers: { ...h.answers, [key]: value } }))
    setErrors((e) => { const next = { ...e }; delete next[key]; return next })
  }

  const yesNo = (key: string, question: string, hint?: string, order: YesNo[] = ['No', 'Yes']) => (
    <div key={key} style={{ display: 'grid', gap: 'var(--space-1)' }}>
      <ChipGroup label={question} role="group" aria-label={question}>
        {order.map((v) => (
          <Chip key={v} selected={health.answers[key] === v} onClick={() => setAnswer(key, v)} aria-label={`${question} ${v}`}>
            {v}
          </Chip>
        ))}
      </ChipGroup>
      {(hint || errors[key]) && <SectionLabel tone={errors[key] ? 'gold' : 'ghost'}>{errors[key] || hint}</SectionLabel>}
    </div>
  )

  const field = (key: keyof CheckinDetails, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <TextField
      label={label}
      value={details[key]}
      onChange={(e) => setDetails((d) => ({ ...d, [key]: e.target.value }))}
      hint={errors[key]}
      aria-invalid={Boolean(errors[key])}
      {...props}
    />
  )

  const stepMeta = stage >= 1 && stage <= 3 ? `Step ${stage} of 3 · ${STEP_NAMES[stage]}` : ''
  const meta = [studioName, stepMeta].filter(Boolean).join(' // ')
  const title = stage === 0 ? dead?.title ?? 'Link expired' : stage === 2 ? 'Health' : stage === 3 ? 'Sign' : stage === 4 ? 'You are set' : 'Check in'
  const booking = ctx?.booking
  const onFile = ctx?.on_file

  return (
    <div className="public-form-shell">
      <Helmet>
        <title>{studioName ? `Check in | ${studioName}` : 'Check in'}</title>
        <meta name="robots" content="noindex" />
      </Helmet>
      <div className="public-form-container">
        <main style={column} aria-live="polite">
          <PageTitle
            meta={meta || undefined}
            action={stage === 2 || stage === 3
              ? <Button variant="ghost" size="sm" onClick={() => go((stage - 1) as Stage)}>Back</Button>
              : undefined}
          >
            {title}
          </PageTitle>

          {/* 01 · You: finder */}
          {stage === 1 && !detailsOpen && (
            <Panel surface="raised" style={panelBody}>
              {!challenge ? (
                <form
                  noValidate
                  style={{ display: 'grid', gap: 'var(--space-4)' }}
                  onSubmit={(e) => { e.preventDefault(); void lookup() }}
                >
                  <SectionLabel>Email or mobile. We will find your booking and anything from last time.</SectionLabel>
                  <TextField
                    label="Email or mobile"
                    inputMode="email"
                    autoComplete="off"
                    value={contact}
                    onChange={(e) => setContact(e.target.value)}
                    hint={errors.contact}
                    aria-invalid={Boolean(errors.contact)}
                  />
                  <div style={actions}>
                    <Button type="submit" disabled={busy}>{busy ? 'Looking…' : 'Find me'}</Button>
                    <Button variant="outline" disabled={busy} onClick={() => void openDetails(null, contact.trim(), null)}>I am new</Button>
                  </div>
                </form>
              ) : (
                <form
                  noValidate
                  style={{ display: 'grid', gap: 'var(--space-4)' }}
                  onSubmit={(e) => { e.preventDefault(); void verify() }}
                >
                  <SectionLabel tone="gold">We sent a 6 digit code to {challenge.sentTo}. Enter it to pull up your info.</SectionLabel>
                  <TextField
                    label="6 digit code"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    hint={errors.code}
                    aria-invalid={Boolean(errors.code)}
                    autoFocus
                  />
                  <div style={actions}>
                    <Button type="submit" disabled={busy}>{busy ? 'Checking…' : 'Verify'}</Button>
                    <Button variant="outline" disabled={busy} onClick={() => { setChallenge(null); void lookup() }}>Send again</Button>
                  </div>
                </form>
              )}
            </Panel>
          )}

          {/* 01 · You: details */}
          {stage === 1 && detailsOpen && (
            <>
              {ctx && (
                <Panel surface="raised" style={panelBody}>
                  <SectionLabel tone="gold" scale="md">
                    {ctx.visits ? `Welcome back${ctx.first_name ? `, ${ctx.first_name}` : ''}` : booking ? 'You are booked' : `Found you${ctx.first_name ? `, ${ctx.first_name}` : ''}`}
                  </SectionLabel>
                  {(ctx.member || ctx.visits > 0) && (
                    <div style={actions}>
                      {ctx.member && <Badge tone="gold">Member</Badge>}
                      {ctx.visits > 0 && (
                        <Badge>{`${ctx.visits} visit${ctx.visits > 1 ? 's' : ''}${ctx.last_visit ? ` · last ${monthYear(ctx.last_visit)}` : ''}`}</Badge>
                      )}
                    </div>
                  )}
                  {booking && (
                    <MetaList rows={[
                      { label: 'Artist', value: booking.artist_name || '' },
                      { label: 'Piece', value: booking.description || '' },
                      { label: 'Where', value: booking.placement || '' },
                      { label: 'When', value: prettyDate(booking.date, booking.time) },
                    ].filter((r) => r.value)} />
                  )}
                </Panel>
              )}

              {!artistLocked && (
                <Panel surface="raised" style={panelBody}>
                  <ChipGroup label="Who is tattooing you?">
                    {crew.map((a) => (
                      <Chip key={a.slug} selected={visit.artistSlug === a.slug} onClick={() => setVisit((v) => ({ ...v, artistSlug: a.slug }))}>
                        {a.name}
                      </Chip>
                    ))}
                  </ChipGroup>
                  {crew.length === 0 && <SectionLabel tone="ghost">Ask the front desk which artist to pick.</SectionLabel>}
                  {errors.artist && <StatusText tone="danger" wrap>{errors.artist}</StatusText>}
                </Panel>
              )}

              <Panel surface="raised" style={panelBody}>
                <SectionLabel>This visit</SectionLabel>
                {artistLocked && visit.artistSlug && (
                  <MetaList rows={[{
                    label: 'Artist',
                    value: crew.find((a) => a.slug === visit.artistSlug)?.name || booking?.artist_name || visit.artistSlug,
                  }]} />
                )}
                <TextField label="What you are getting" maxLength={180} placeholder="Describe it in one sentence" value={visit.description}
                  onChange={(e) => setVisit((v) => ({ ...v, description: e.target.value }))} hint={errors.description} aria-invalid={Boolean(errors.description)} />
                <TextField label="Where on your body" value={visit.placement}
                  onChange={(e) => setVisit((v) => ({ ...v, placement: e.target.value }))} hint={errors.placement} aria-invalid={Boolean(errors.placement)} />
                <div style={twoUp}>
                  <TextField label="Agreed price" inputMode="decimal" value={visit.price}
                    onChange={(e) => setVisit((v) => ({ ...v, price: e.target.value }))} hint={errors.price} aria-invalid={Boolean(errors.price)} />
                  <TextField label="Date" type="date" value={visit.date}
                    onChange={(e) => setVisit((v) => ({ ...v, date: e.target.value }))} hint={errors.date} aria-invalid={Boolean(errors.date)} />
                </div>
              </Panel>

              {prefillNote && !detailsCollapsed && <SectionLabel tone="gold">{prefillNote}</SectionLabel>}

              {detailsCollapsed ? (
                <Panel surface="raised" style={panelBody}>
                  <SectionLabel>Your details</SectionLabel>
                  <MetaList rows={[
                    { label: 'Name', value: details.fullName },
                    { label: 'Mobile', value: details.phone },
                    { label: 'Email', value: details.email },
                    { label: 'Emergency', value: details.emergencyName },
                  ].filter((r) => r.value)} />
                  <div><Button variant="ghost" size="sm" onClick={() => setDetailsCollapsed(false)}>Edit</Button></div>
                </Panel>
              ) : (
                <Panel surface="raised" style={panelBody}>
                  <SectionLabel>Your details</SectionLabel>
                  {field('fullName', 'Legal name', { autoComplete: 'off', placeholder: 'As it reads on your ID' })}
                  {field('dob', 'Date of birth', { type: 'date', autoComplete: 'off' })}
                  <div style={twoUp}>
                    {field('phone', 'Mobile', { type: 'tel', inputMode: 'tel', autoComplete: 'off' })}
                    {field('email', 'Email', { type: 'email', inputMode: 'email', autoComplete: 'off' })}
                  </div>
                  {field('address', 'Home address', { autoComplete: 'off', placeholder: 'Street, city, zip' })}
                  <SectionLabel>Emergency contact</SectionLabel>
                  <div style={twoUp}>
                    {field('emergencyName', 'Name', { autoComplete: 'off' })}
                    {field('emergencyPhone', 'Phone', { type: 'tel', inputMode: 'tel', autoComplete: 'off' })}
                  </div>
                  {field('emergencyAddress', 'Their address (optional)', { autoComplete: 'off' })}
                </Panel>
              )}

              <Panel surface="raised" style={panelBody}>
                {reusePhotoId ? (
                  <>
                    <SectionLabel>Photo ID</SectionLabel>
                    <StatusText tone="ok" wrap>ID on file from your last visit.</StatusText>
                    <div><Button variant="ghost" size="sm" onClick={() => setReusePhotoId(false)}>Use a new photo</Button></div>
                  </>
                ) : (
                  <FormFieldRenderer field={ID_FIELD} value={photoIdUrl} onChange={(_, v) => setPhotoIdUrl(String(v))} />
                )}
                {errors.photoId && <StatusText tone="danger" wrap>{errors.photoId}</StatusText>}
                <SectionLabel tone="ghost">18 and up, verified by photo ID. Keep it flat and in focus.</SectionLabel>
              </Panel>
            </>
          )}

          {/* 02 · Health */}
          {stage === 2 && (
            <Panel surface="raised" style={panelBody}>
              <SectionLabel>Straight answers keep you safe. Only your artist sees these.</SectionLabel>
              {yesNo('intoxicated', 'Had alcohol or drugs today?', 'Includes cannabis and edibles')}
              {health.sameOffered && yesNo('same', 'Health the same as last time?', onFile?.signed_at ? `From your visit in ${monthYear(onFile.signed_at)}` : undefined, ['Yes', 'No'])}
              {questionsToAsk(health).map((q) => yesNo(q.key, q.question, q.hint))}
              {questionsToAsk(health).some((q) => q.key === 'allergy') && health.answers.allergy === 'Yes' && (
                <TextField label="What are you allergic to?" value={health.allergyDetail}
                  onChange={(e) => setHealth((h) => ({ ...h, allergyDetail: e.target.value }))} hint={errors.allergyDetail} aria-invalid={Boolean(errors.allergyDetail)} />
              )}
              {!(sameAsLast(health) && health.prior?.medical_conditions?.trim()) && (
                <TextField label="Medications or conditions (optional)" placeholder="Anything that could affect healing" value={health.medications}
                  onChange={(e) => setHealth((h) => ({ ...h, medications: e.target.value }))} />
              )}
              {healthNeedsReview(health) && (
                <StatusText tone="pending" wrap>Heads up. Your artist will go over this with you before you start.</StatusText>
              )}
              {health.answers.intoxicated === 'Yes' && (
                <StatusText tone="danger" wrap role="alert">Not today. We cannot tattoo anyone under the influence. Let us reschedule.</StatusText>
              )}
            </Panel>
          )}

          {/* 03 · Sign */}
          {stage === 3 && (
            <Panel surface="raised" style={panelBody}>
              <SectionLabel>Read it through before you sign.</SectionLabel>
              {terms.length > 0 ? terms.map((t) => (
                <Panel key={String(t.name)} surface="well" style={{ padding: 'var(--space-4)', gap: 'var(--space-2)' }} tabIndex={0} aria-label={String(t.terms_title || 'Waiver terms')}>
                  <SectionLabel tone="gold">{String(t.terms_title || 'Waiver terms')}</SectionLabel>
                  {/* Safe: the shop's own template text, sanitized above. */}
                  <div dangerouslySetInnerHTML={{ __html: sanitizeTerms(String(t.terms_html)) }} />
                </Panel>
              )) : (
                <SectionLabel tone="ghost">Ask the front desk for the full waiver text before you sign.</SectionLabel>
              )}
              {CONSENTS.map((c) => (
                <div key={c.name}>
                  <FormFieldRenderer
                    field={{ name: c.name, label: c.label, type: 'checkbox', required: true }}
                    value={Boolean(consents[c.name])}
                    onChange={(name, value) => setConsents((cur) => ({ ...cur, [name]: Boolean(value) }))}
                  />
                  {errors[c.name] && <StatusText tone="danger" wrap>{errors[c.name]}</StatusText>}
                </div>
              ))}
              <SectionLabel>Signature</SectionLabel>
              {reuseSignature && onFile?.signature_url ? (
                <>
                  <img src={onFile.signature_url} alt="Your saved signature" style={{ maxWidth: '100%' }} />
                  <div style={actions}>
                    <Chip selected onClick={() => setReuseSignature(true)}>Sign with this</Chip>
                    <Chip onClick={() => setReuseSignature(false)}>Draw a new one</Chip>
                  </div>
                </>
              ) : (
                <>
                  <SignaturePad onSign={setSignature} value={signature} />
                  {onFile?.signature_url && (
                    <div><Chip onClick={() => setReuseSignature(true)}>Use my saved signature</Chip></div>
                  )}
                </>
              )}
              {errors.signature && <StatusText tone="danger" wrap>{errors.signature}</StatusText>}
            </Panel>
          )}

          {/* Done */}
          {stage === 4 && (
            <Panel surface="raised" style={panelBody}>
              <SectionLabel scale="md">
                {ctx?.member ? 'Waiver filed and your visit is logged. Your artist will sign off.' : 'Waiver filed. Your artist will review and sign off.'}
              </SectionLabel>
              {reference && <MetaList rows={[{ label: 'Reference', value: reference }]} />}
              {!ctx?.member && (
                <>
                  <SectionLabel tone="gold">Next time, skip the paperwork</SectionLabel>
                  <SectionLabel>Make a free profile and we keep your details, ID and signature for your next visit.</SectionLabel>
                  {/* Hand-off to the client's own phone: the shared tablet stays in the kiosk, so the idle reset
                      still clears this screen. */}
                  {profileQr ? (
                    <div style={{ display: 'grid', gap: ds.space.s3, justifyItems: 'start' }}>
                      <QRCodeSVG value={profileHandoffUrl(window.location.origin, details)} size={200} level="M"
                        marginSize={2} bgColor={ds.color.text} fgColor={ds.color.bg}
                        aria-label="QR code to make your profile on your phone" />
                      <SectionLabel>Scan with your phone camera to finish your profile there.</SectionLabel>
                    </div>
                  ) : (
                    <div style={actions}>
                      <Button onClick={() => setProfileQr(true)}>Create my profile</Button>
                    </div>
                  )}
                </>
              )}
              <div><Button variant="outline" onClick={onStartOver}>Done</Button></div>
            </Panel>
          )}

          {/* Dead link */}
          {stage === 0 && (
            <Panel surface="raised" style={panelBody}>
              <SectionLabel>{dead?.body ?? 'Ask the front desk for a fresh check-in link.'}</SectionLabel>
              <div><Button variant="outline" onClick={onStartOver}>Start over</Button></div>
            </Panel>
          )}

          {notice && <StatusText tone="danger" wrap role="alert">{notice}</StatusText>}

          {stage === 1 && detailsOpen && (
            <Button size="lg" fullWidth disabled={busy} onClick={() => void stepOne()}>{busy ? 'One moment…' : 'Next'}</Button>
          )}
          {stage === 2 && (
            <Button size="lg" fullWidth disabled={busy || health.answers.intoxicated === 'Yes'} onClick={stepTwo}>Next</Button>
          )}
          {stage === 3 && (
            <Button size="lg" fullWidth disabled={busy} onClick={() => void submit()}>{busy ? 'Filing your waiver…' : 'Sign waiver'}</Button>
          )}
        </main>
      </div>
    </div>
  )
}
