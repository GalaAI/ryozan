/**
 * PublicBookingPage: the shop's public booking flow, no sign-in.
 *
 *   /book, /book/<artist>  choose the artist (the slug preselects one)
 *   /book/sesh              describe the piece and propose up to three times
 *   /book/confirm           contact details, submit; also where Stripe returns
 *   /ask                    a question for the shop, not a booking
 *
 * Ported from the legacy studio app's public booking host: the same steps, the
 * same per-tab draft between them and the same API (POST /bookings/submit,
 * POST /bookings/inquiry). The legacy canon pages carried that shop's own art,
 * roster and colours, so this page is assembled from the studio website's
 * existing parts (StudioSiteShell and the website booking form classes in
 * v2-redesign.css). Every name and contact line comes from the studio settings
 * and the artists API.
 *
 * Proposed times carry no hold and no deposit: the artist confirms one and the
 * deposit follows from the portal. A Stripe redirect (flash, held slots) lands
 * on /book/confirm?session_id=..., and the page reads the booking's real state
 * from GET /bookings/checkout-status/{session_id}.
 */
import { useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Helmet } from 'react-helmet-async'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Button, Checkbox, Chip, ChipGroup, MetaList, PageTitle, Panel, SectionLabel, StatusText, TextArea, TextField, token } from '@ryozan/ui'
import StudioSiteShell from '../components/studio-site/StudioSiteShell'
import { useStudioSettings } from '../hooks/useStudioSettings'
import {
  ApiError,
  artists as artistsApi,
  bookings as bookingsApi,
  type ArtistSummary,
  type PublicBookingResponse,
  type PublicCheckoutStatus,
  type PublicInquiryResponse,
  type StudioSettings,
} from '../lib/api'
import {
  BOOK_ROUTE,
  artistSlugFromPath,
  beginRequest,
  bookingPayload,
  checkoutReturn,
  clearDraft,
  completeRequest,
  contactProblem,
  depositLine,
  filledSlots,
  inquiryPayload,
  inquiryProblem,
  readDraft,
  seshProblem,
  slotLabel,
  withFreshInquiryId,
  writeDraft,
  type BookingDraft,
  type BookingRequest,
  type BookingStep,
  type CheckoutReturn,
  type InquiryFields,
} from '../lib/public-booking'
import { listedArtists, studioName } from '../lib/studio-site'

export type RosterLoad = { status: 'loading' } | { status: 'ready'; artists: ArtistSummary[] } | { status: 'failed' }

const NO_PREFERENCE = 'any'

function sessionStore(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function errorText(caught: unknown, fallback: string): string {
  return caught instanceof ApiError && caught.message ? caught.message : fallback
}

/* ── Frame ──────────────────────────────────────────────────────────── */
/*
 * Everything below is composed from @ryozan/ui. The only website classes left
 * are layout (container, the purpose band grid, form-grid columns, the action
 * row); @ryozan/ui has no layout primitive yet. Prose sits in the website's
 * calendar-purpose-copy, since @ryozan/ui has no body-text primitive either.
 */

type MetaRows = Array<{ label: string; value: string }>

/** Panel padding and rhythm, from the spacing scale. */
const PANEL_BODY = { padding: token.space.s6, gap: token.space.s4 } as const

type FrameProps = {
  settings: StudioSettings
  eyebrow: string
  title: string
  intro: ReactNode
  summary?: ReactNode
  children: ReactNode
}

/** One step: the website's purpose band, then the summary and the step's panel. */
export function BookingFrame({ settings, eyebrow, title, intro, summary, children }: FrameProps) {
  const name = studioName(settings)
  return (
    <StudioSiteShell settings={settings} showArtists>
      <Helmet>
        <title>{`${title} | ${name}`}</title>
        <meta name="robots" content="noindex" />
      </Helmet>
      <section className="calendar-purpose" aria-labelledby="booking-title">
        <div className="container calendar-purpose-grid">
          <div>
            <SectionLabel tone="gold" scale="md">{eyebrow}</SectionLabel>
            <PageTitle id="booking-title">{title}</PageTitle>
          </div>
          <div className="calendar-purpose-copy">{intro}</div>
        </div>
      </section>
      <section aria-label={title}>
        <div className="container calendar-purpose-grid">
          <Panel surface="flat" style={PANEL_BODY}>
            <PageTitle as="h2" compact>{name}</PageTitle>
            {summary}
          </Panel>
          <Panel lifted style={PANEL_BODY}>{children}</Panel>
        </div>
      </section>
    </StudioSiteShell>
  )
}

function DraftSummary({ draft }: { draft: BookingDraft }) {
  const slots = filledSlots(draft.slots)
  if (!draft.artistName) return <div className="calendar-purpose-copy"><p>Choose an artist to start.</p></div>
  const rows: MetaRows = [{ label: 'Artist', value: draft.artistName }]
  if (draft.placement.trim()) rows.push({ label: 'Placement', value: draft.placement.trim() })
  if (draft.size.trim()) rows.push({ label: 'Size', value: draft.size.trim() })
  if (slots.length > 0) rows.push({ label: 'Proposed times', value: slots.map(slotLabel).join(' · ') })
  return <MetaList rows={rows} />
}

/** A form or load error: a wrapping StatusText, so the sentence stays inside the panel. */
function Problem({ message }: { message: string | null }) {
  if (!message) return null
  return <StatusText tone="danger" role="alert" wrap>{message}</StatusText>
}

/* ── Step 1: artist ─────────────────────────────────────────────────── */

type ArtistStepProps = {
  settings: StudioSettings
  roster: RosterLoad
  selected: string
  /** The slug a /book/<slug> link asked for when no listed artist has it. */
  missingSlug: string | null
  onSelect: (slug: string) => void
  onContinue: () => void
}

export function ArtistStepView({ settings, roster, selected, missingSlug, onSelect, onContinue }: ArtistStepProps) {
  const artists = roster.status === 'ready' ? listedArtists(roster.artists) : []
  const anyArtist = Boolean(settings.default_artist_slug)
  const chosen = artists.find((artist) => artist.slug === selected)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onContinue()
  }
  return (
    <BookingFrame
      settings={settings}
      eyebrow="Book a session · Step 1 of 3"
      title="Choose an artist"
      intro={<p>Pick who you want to work with. You will describe the piece and propose times next, and the artist confirms one.</p>}
      summary={chosen
        ? <MetaList rows={[{ label: 'Artist', value: `${chosen.name}${chosen.specialty?.length ? ` · ${chosen.specialty.join(', ')}` : ''}` }]} />
        : <div className="calendar-purpose-copy"><p>Not a tattoo? <Link to={BOOK_ROUTE.ask}>Ask the shop</Link></p></div>}
    >
      <form className="form-grid" onSubmit={submit} noValidate>
        {roster.status === 'loading' && <div className="full-width" aria-live="polite"><StatusText tone="pending">Loading artists</StatusText></div>}
        {roster.status === 'failed' && <div className="full-width"><Problem message="The artist list could not be loaded. Refresh to try again." /></div>}
        {roster.status === 'ready' && artists.length === 0 && (
          <div className="full-width calendar-purpose-copy"><p>This studio has not listed its artists yet. <Link to={BOOK_ROUTE.ask}>Ask the shop</Link></p></div>
        )}
        {missingSlug && <div className="full-width"><Problem message="There is no artist at that booking link. Choose one below." /></div>}
        {artists.length > 0 && (
          <ChipGroup className="full-width" label="Artist" role="group" aria-label="Artist">
            {artists.map((artist) => (
              <Chip key={artist.slug} selected={selected === artist.slug} onClick={() => onSelect(artist.slug)}>{artist.name}</Chip>
            ))}
            {anyArtist && <Chip selected={selected === NO_PREFERENCE} onClick={() => onSelect(NO_PREFERENCE)}>No preference</Chip>}
          </ChipGroup>
        )}
        {artists.length > 0 && (
          <div className="full-width">
            <Button type="submit" disabled={!selected}>Continue</Button>
          </div>
        )}
      </form>
    </BookingFrame>
  )
}

/* ── Step 3, after Stripe: the return ───────────────────────────────── */

export type StatusLoad = { status: 'loading' } | { status: 'ready'; result: PublicCheckoutStatus } | { status: 'missing' }

/** The closing actions every finished step offers. */
function DoneActions({ primary, to }: { primary: string; to: string }) {
  const navigate = useNavigate()
  return (
    <div className="calendar-purpose-actions">
      <Button onClick={() => navigate(to)}>{primary}</Button>
      <Button variant="ghost" onClick={() => navigate('/')}>Back to the studio</Button>
    </div>
  )
}

export function CheckoutReturnView({ settings, ret, load }: { settings: StudioSettings; ret: CheckoutReturn; load: StatusLoad }) {
  const navigate = useNavigate()
  if (ret.outcome === 'cancelled') {
    return (
      <BookingFrame
        settings={settings}
        eyebrow="Checkout cancelled"
        title="No payment was taken"
        intro={<p>You left checkout before paying. Nothing was charged.</p>}
      >
        {ret.reference && <div className="calendar-purpose-copy"><p>Request {ret.reference} is on file without a deposit. The shop will follow up by email.</p></div>}
        <div className="calendar-purpose-actions">
          <Button onClick={() => navigate(BOOK_ROUTE.artist)}>Start again</Button>
          <Button variant="ghost" onClick={() => navigate(BOOK_ROUTE.ask)}>Ask the shop</Button>
        </div>
      </BookingFrame>
    )
  }
  const result = load.status === 'ready' ? load.result : null
  const reference = result?.reference_code || ret.reference
  const rows: MetaRows = []
  if (reference) rows.push({ label: 'Reference', value: reference })
  if (result?.artist_name) rows.push({ label: 'Artist', value: result.artist_name })
  if (result?.scheduled_date) rows.push({ label: 'When', value: slotLabel({ date: result.scheduled_date, time: result.scheduled_time || '00:00' }) })
  return (
    <BookingFrame
      settings={settings}
      eyebrow="Booking request"
      title={result?.booking_status === 'confirmed' ? 'You are booked' : 'Request received'}
      intro={<p>We sent the details to your email. Keep the reference for anything you ask the shop about this booking.</p>}
      summary={rows.length > 0 ? <MetaList rows={rows} /> : undefined}
    >
      <div aria-live="polite">
        {load.status === 'loading' && <StatusText tone="pending">Checking your payment.</StatusText>}
        <div className="calendar-purpose-copy">
          {result && <p>{depositLine(result)}</p>}
          {load.status === 'missing' && (
            <p>We could not look this payment up yet. If Stripe showed a receipt, the payment went through; the shop will confirm by email.</p>
          )}
        </div>
      </div>
      <DoneActions primary="Open your account" to="/portal" />
    </BookingFrame>
  )
}

/* ── Step 3, after a submit ─────────────────────────────────────────── */

export function SubmittedView({ settings, submitted }: { settings: StudioSettings; submitted: PublicBookingResponse }) {
  const rows: MetaRows = []
  if (submitted.reference_code) rows.push({ label: 'Reference', value: submitted.reference_code })
  if (submitted.artist_name) rows.push({ label: 'Artist', value: submitted.artist_name })
  return (
    <BookingFrame
      settings={settings}
      eyebrow="Booking request"
      title={submitted.checkout_url ? 'Taking you to the deposit' : 'Request sent'}
      intro={<p>{submitted.message}</p>}
      summary={rows.length > 0 ? <MetaList rows={rows} /> : undefined}
    >
      <div className="calendar-purpose-copy" aria-live="polite">
        {submitted.checkout_url
          ? <p>Opening secure checkout. <a href={submitted.checkout_url}>Continue to checkout</a></p>
          : <p>Your artist reviews the request and confirms one of your times by email.</p>}
      </div>
      <DoneActions primary="Open your account" to="/portal" />
    </BookingFrame>
  )
}

/* ── Step 2: the piece ──────────────────────────────────────────────── */

export function SeshStepView({ settings, draft, problem, onChange, onContinue }: {
  settings: StudioSettings
  draft: BookingDraft
  problem: string | null
  onChange: (fields: Partial<BookingDraft>) => void
  onContinue: () => void
}) {
  const navigate = useNavigate()
  const setSlot = (index: number, fields: Partial<BookingDraft['slots'][number]>) =>
    onChange({ slots: draft.slots.map((s, i) => (i === index ? { ...s, ...fields } : s)) })
  return (
    <BookingFrame
      settings={settings}
      eyebrow="Book a session · Step 2 of 3"
      title="Tell us about the piece"
      intro={<p>Describe the idea, where it goes and roughly how big. Propose up to three times that work for you; nothing is held until the artist confirms one.</p>}
      summary={<DraftSummary draft={draft} />}
    >
      <form className="form-grid" onSubmit={(event) => { event.preventDefault(); onContinue() }} noValidate>
        <div className="full-width">
          <TextArea id="booking-description" label="The idea" value={draft.description} onChange={(e) => onChange({ description: e.currentTarget.value })} required />
        </div>
        <TextField id="booking-placement" label="Placement" value={draft.placement} onChange={(e) => onChange({ placement: e.currentTarget.value })} />
        <TextField id="booking-size" label="Size" value={draft.size} onChange={(e) => onChange({ size: e.currentTarget.value })} />
        {draft.slots.map((slot, index) => (
          <div key={index} className="full-width">
            <SectionLabel scale="md">{index === 0 ? 'Preferred time' : 'Another time (optional)'}</SectionLabel>
            <div className="form-grid">
              <TextField
                id={`booking-slot-date-${index}`}
                type="date"
                aria-label={`Date ${index + 1}`}
                value={slot.date}
                onChange={(e) => setSlot(index, { date: e.currentTarget.value })}
              />
              <TextField
                type="time"
                aria-label={`Time ${index + 1}`}
                value={slot.time}
                onChange={(e) => setSlot(index, { time: e.currentTarget.value })}
              />
            </div>
          </div>
        ))}
        <div className="full-width"><Problem message={problem} /></div>
        <div className="full-width calendar-purpose-actions">
          <Button type="submit">Continue</Button>
          <Button variant="ghost" onClick={() => navigate(BOOK_ROUTE.artist)}>Back</Button>
        </div>
      </form>
    </BookingFrame>
  )
}

/* ── The page ───────────────────────────────────────────────────────── */

export default function PublicBookingPage({ step }: { step: BookingStep }) {
  const { settings } = useStudioSettings()
  const location = useLocation()
  const navigate = useNavigate()
  const storage = useMemo(() => sessionStore(), [])
  // The routes share this one instance, so the draft and the sent request live together.
  const [request, setRequest] = useState<BookingRequest<PublicBookingResponse>>(() => ({ draft: readDraft(storage), submitted: null }))
  const { draft, submitted } = request
  const [roster, setRoster] = useState<RosterLoad>({ status: 'loading' })
  const [problem, setProblem] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [asked, setAsked] = useState<PublicInquiryResponse | null>(null)
  const [statusLoad, setStatusLoad] = useState<{ sessionId: string; load: StatusLoad } | null>(null)

  const deepLinkSlug = step === 'artist' ? artistSlugFromPath(location.pathname) : null
  const ret = step === 'confirm' ? checkoutReturn(location.search) : null
  const needsRoster = step === 'artist' || step === 'ask'

  const updateDraft = (change: (current: BookingDraft) => BookingDraft) => {
    setRequest((current) => {
      const next = change(current.draft)
      if (next === current.draft) return current
      writeDraft(storage, next)
      return { ...current, draft: next }
    })
  }
  const patch = (fields: Partial<BookingDraft>) => updateDraft((current) => ({ ...current, ...fields }))

  // Declared before the deep-link check: a new request starts clean, then the link preselects.
  // Both adjust state during render when their input changes.
  const [stepSeen, setStepSeen] = useState<BookingStep | null>(null)
  if (stepSeen !== step) {
    setStepSeen(step)
    setProblem(null)
    setRequest((current) => beginRequest(current, step))
    if (step !== 'ask') setAsked(null)
  }
  useEffect(() => {
    document.getElementById('root')?.scrollTo?.({ top: 0 })
  }, [step])

  useEffect(() => {
    if (!needsRoster) return
    let active = true
    artistsApi.list()
      .then((artists) => { if (active) setRoster({ status: 'ready', artists }) })
      .catch(() => { if (active) setRoster({ status: 'failed' }) })
    return () => { active = false }
  }, [needsRoster])

  // A /book/<slug> link preselects that artist once the roster confirms it exists.
  const deepLinked = roster.status === 'ready' && deepLinkSlug
    ? listedArtists(roster.artists).find((artist) => artist.slug === deepLinkSlug) ?? null
    : null
  const [deepLinkSeen, setDeepLinkSeen] = useState<string | undefined>(undefined)
  if (deepLinkSeen !== deepLinked?.slug) {
    setDeepLinkSeen(deepLinked?.slug)
    if (deepLinked) {
      updateDraft((current) => (current.artistSlug === deepLinked.slug
        ? current
        : { ...current, artistSlug: deepLinked.slug, artistName: deepLinked.name }))
    }
  }

  const sessionId = ret?.sessionId ?? null
  useEffect(() => {
    if (!sessionId) return
    let active = true
    bookingsApi.checkoutStatus(sessionId)
      .then((result) => { if (active) setStatusLoad({ sessionId, load: { status: 'ready', result } }) })
      .catch(() => { if (active) setStatusLoad({ sessionId, load: { status: 'missing' } }) })
    return () => { active = false }
  }, [sessionId])

  /* Stripe return */
  if (ret) {
    const load: StatusLoad = !ret.sessionId
      ? { status: 'missing' }
      : statusLoad?.sessionId === ret.sessionId ? statusLoad.load : { status: 'loading' }
    return <CheckoutReturnView settings={settings} ret={ret} load={load} />
  }

  /* Step 1 */
  if (step === 'artist') {
    const selectArtist = (slug: string) => {
      const artist = roster.status === 'ready' ? roster.artists.find((a) => a.slug === slug) : undefined
      patch({ artistSlug: slug, artistName: slug === NO_PREFERENCE ? 'No preference' : artist?.name ?? '' })
    }
    return (
      <ArtistStepView
        settings={settings}
        roster={roster}
        selected={draft.artistSlug}
        missingSlug={roster.status === 'ready' && deepLinkSlug && !deepLinked ? deepLinkSlug : null}
        onSelect={selectArtist}
        onContinue={() => { if (draft.artistSlug) navigate(BOOK_ROUTE.sesh) }}
      />
    )
  }

  /* /ask */
  if (step === 'ask') {
    return (
      <AskView
        settings={settings}
        roster={roster}
        sending={sending}
        problem={problem}
        asked={asked}
        draft={draft}
        onSubmit={async (fields) => {
          const missing = inquiryProblem(fields)
          if (missing) { setProblem(missing); return }
          setSending(true)
          setProblem(null)
          try {
            setAsked(await bookingsApi.submitInquiry(inquiryPayload(fields, draft.inquiryId)))
            // The sent question owns that id; the next request gets its own.
            updateDraft(withFreshInquiryId)
          } catch (caught) {
            setProblem(errorText(caught, 'The question could not be sent. Try again in a moment.'))
          } finally {
            setSending(false)
          }
        }}
      />
    )
  }

  /* Step 3 after a submit */
  if (step === 'confirm' && submitted) return <SubmittedView settings={settings} submitted={submitted} />

  /* Steps 2 and 3 need what came before them. */
  if (!draft.artistSlug) return <Navigate to={BOOK_ROUTE.artist} replace />
  if (step === 'confirm' && seshProblem(draft)) return <Navigate to={BOOK_ROUTE.sesh} replace />

  /* Step 2 */
  if (step === 'sesh') {
    return (
      <SeshStepView
        settings={settings}
        draft={draft}
        problem={problem}
        onChange={patch}
        onContinue={() => {
          const missing = seshProblem(draft)
          if (missing) { setProblem(missing); return }
          navigate(BOOK_ROUTE.confirm)
        }}
      />
    )
  }

  /* Step 3 */
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const missing = contactProblem(draft) ?? seshProblem(draft)
    if (missing) { setProblem(missing); return }
    setSending(true)
    setProblem(null)
    try {
      const response = await bookingsApi.submitPublic(bookingPayload(draft))
      clearDraft(storage)
      setRequest(completeRequest(response))
      if (response.checkout_url) window.location.assign(response.checkout_url)
    } catch (caught) {
      setProblem(errorText(caught, 'The request could not be sent. Try again in a moment.'))
    } finally {
      setSending(false)
    }
  }
  return (
    <BookingFrame
      settings={settings}
      eyebrow="Book a session · Step 3 of 3"
      title="Where do we reach you?"
      intro={<p>The shop replies by email. Add a phone number if you would like text updates about this request.</p>}
      summary={<DraftSummary draft={draft} />}
    >
      <ContactForm
        fields={draft}
        onChange={patch}
        sending={sending}
        problem={problem}
        submitLabel="Send request"
        onSubmit={submit}
        back={BOOK_ROUTE.sesh}
      />
    </BookingFrame>
  )
}

/* ── Shared contact fields ──────────────────────────────────────────── */

type ContactFields = Pick<BookingDraft, 'firstName' | 'lastName' | 'email' | 'phone' | 'smsOptIn'>

function ContactForm({ fields, onChange, sending, problem, submitLabel, onSubmit, back, children }: {
  fields: ContactFields
  onChange: (fields: Partial<ContactFields>) => void
  sending: boolean
  problem: string | null
  submitLabel: string
  onSubmit: (event: FormEvent) => void
  back?: string
  children?: ReactNode
}) {
  const navigate = useNavigate()
  return (
    <form className="form-grid" onSubmit={onSubmit} noValidate>
      {children}
      <TextField id="contact-first" label="First name" autoComplete="given-name" value={fields.firstName} onChange={(e) => onChange({ firstName: e.currentTarget.value })} required />
      <TextField id="contact-last" label="Last name" autoComplete="family-name" value={fields.lastName} onChange={(e) => onChange({ lastName: e.currentTarget.value })} required />
      <TextField id="contact-email" label="Email" type="email" autoComplete="email" value={fields.email} onChange={(e) => onChange({ email: e.currentTarget.value })} required />
      <TextField id="contact-phone" label="Phone (optional)" type="tel" autoComplete="tel" value={fields.phone} onChange={(e) => onChange({ phone: e.currentTarget.value })} />
      {fields.phone.trim() && (
        <div className="full-width calendar-purpose-copy">
          <Checkbox
            id="contact-sms-opt-in"
            label="Text me updates about this request."
            checked={fields.smsOptIn}
            onChange={(e) => onChange({ smsOptIn: e.currentTarget.checked })}
            aria-describedby="contact-sms-terms"
          />
          <p id="contact-sms-terms">Message and data rates may apply; reply STOP to opt out. <a href="/sms-terms">SMS terms</a> · <a href="/privacy">Privacy</a></p>
        </div>
      )}
      <div className="full-width"><Problem message={problem} /></div>
      <div className="full-width calendar-purpose-actions">
        <Button type="submit" disabled={sending}>{sending ? 'Sending' : submitLabel}</Button>
        {back && <Button variant="ghost" onClick={() => navigate(back)}>Back</Button>}
      </div>
    </form>
  )
}

/* ── /ask ───────────────────────────────────────────────────────────── */

function AskView({ settings, roster, sending, problem, asked, draft, onSubmit }: {
  settings: StudioSettings
  roster: RosterLoad
  sending: boolean
  problem: string | null
  asked: PublicInquiryResponse | null
  draft: BookingDraft
  onSubmit: (fields: InquiryFields) => void
}) {
  const [fields, setFields] = useState<InquiryFields>({
    artistSlug: '',
    firstName: draft.firstName,
    lastName: draft.lastName,
    email: draft.email,
    phone: draft.phone,
    smsOptIn: false,
    message: '',
  })
  const artists = roster.status === 'ready' ? listedArtists(roster.artists) : []
  if (asked) {
    return (
      <BookingFrame settings={settings} eyebrow="Ask the shop" title="Question sent" intro={<p>{asked.message}</p>}>
        <MetaList rows={[{ label: 'Reference', value: asked.reference_code }]} />
        <DoneActions primary="Book a session" to={BOOK_ROUTE.artist} />
      </BookingFrame>
    )
  }
  return (
    <BookingFrame
      settings={settings}
      eyebrow="Ask the shop"
      title="A question first?"
      intro={<p>Pricing, aftercare, a cover up, anything that is not a booking yet. The shop answers by email.</p>}
    >
      <ContactForm
        fields={fields}
        onChange={(next) => setFields({ ...fields, ...next })}
        sending={sending}
        problem={problem}
        submitLabel="Send question"
        onSubmit={(event) => { event.preventDefault(); onSubmit(fields) }}
      >
        {artists.length > 0 && (
          <ChipGroup className="full-width" label="For" role="group" aria-label="For">
            <Chip selected={fields.artistSlug === ''} onClick={() => setFields({ ...fields, artistSlug: '' })}>The shop</Chip>
            {artists.map((artist) => (
              <Chip key={artist.slug} selected={fields.artistSlug === artist.slug} onClick={() => setFields({ ...fields, artistSlug: artist.slug })}>{artist.name}</Chip>
            ))}
          </ChipGroup>
        )}
        <div className="full-width">
          <TextArea id="ask-message" label="Your question" value={fields.message} onChange={(e) => setFields({ ...fields, message: e.currentTarget.value })} required />
        </div>
      </ContactForm>
    </BookingFrame>
  )
}
