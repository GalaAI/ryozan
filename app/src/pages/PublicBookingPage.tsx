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
  bookingPayload,
  checkoutReturn,
  clearDraft,
  contactProblem,
  depositLine,
  filledSlots,
  inquiryPayload,
  inquiryProblem,
  readDraft,
  seshProblem,
  slotLabel,
  writeDraft,
  type BookingDraft,
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

type FrameProps = {
  settings: StudioSettings
  eyebrow: string
  title: string
  intro: ReactNode
  summary?: ReactNode
  children: ReactNode
}

/** One step: the website's purpose band, then the website booking card. */
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
            <span className="calendar-purpose-eyebrow">{eyebrow}</span>
            <h1 id="booking-title" className="booking-cta-heading serif">{title}</h1>
          </div>
          <div className="calendar-purpose-copy">{intro}</div>
        </div>
      </section>
      <section aria-label={title}>
        <div className="container">
          <div className="booking-wrap">
            <div className="booking-info">
              <h2 className="serif">{name}</h2>
              {summary}
            </div>
            <div className="booking-form">{children}</div>
          </div>
        </div>
      </section>
    </StudioSiteShell>
  )
}

function DraftSummary({ draft }: { draft: BookingDraft }) {
  const slots = filledSlots(draft.slots)
  return (
    <div className="calendar-purpose-copy">
      {draft.artistName ? <p>Artist: {draft.artistName}</p> : <p>Choose an artist to start.</p>}
      {draft.placement.trim() && <p>Placement: {draft.placement.trim()}</p>}
      {draft.size.trim() && <p>Size: {draft.size.trim()}</p>}
      {slots.length > 0 && <p>Proposed times: {slots.map(slotLabel).join(' · ')}</p>}
    </div>
  )
}

function Problem({ message }: { message: string | null }) {
  if (!message) return null
  return <div className="form-response is-visible" role="alert">{message}</div>
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
      summary={(
        <div className="calendar-purpose-copy">
          {chosen ? <p>{chosen.name}{chosen.specialty?.length ? ` · ${chosen.specialty.join(', ')}` : ''}</p> : <p>Not a tattoo? <Link to={BOOK_ROUTE.ask} className="calendar-purpose-link">Ask the shop</Link></p>}
        </div>
      )}
    >
      <form className="form-grid" onSubmit={submit} noValidate>
        {roster.status === 'loading' && <p className="full-width" aria-live="polite">Loading artists</p>}
        {roster.status === 'failed' && <div className="full-width"><Problem message="The artist list could not be loaded. Refresh to try again." /></div>}
        {roster.status === 'ready' && artists.length === 0 && (
          <p className="full-width">This studio has not listed its artists yet. <Link to={BOOK_ROUTE.ask} className="calendar-purpose-link">Ask the shop</Link></p>
        )}
        {missingSlug && <div className="full-width"><Problem message="There is no artist at that booking link. Choose one below." /></div>}
        {artists.length > 0 && (
          <div className="input-group full-width">
            <label htmlFor="booking-artist">Artist</label>
            <select id="booking-artist" value={selected} onChange={(event) => onSelect(event.currentTarget.value)} required>
              <option value="">Choose an artist</option>
              {artists.map((artist) => <option key={artist.slug} value={artist.slug}>{artist.name}</option>)}
              {anyArtist && <option value={NO_PREFERENCE}>No preference</option>}
            </select>
          </div>
        )}
        {artists.length > 0 && (
          <div className="full-width">
            <button type="submit" className="btn btn-primary" disabled={!selected}>Continue</button>
          </div>
        )}
      </form>
    </BookingFrame>
  )
}

/* ── Step 3, after Stripe: the return ───────────────────────────────── */

export type StatusLoad = { status: 'loading' } | { status: 'ready'; result: PublicCheckoutStatus } | { status: 'missing' }

export function CheckoutReturnView({ settings, ret, load }: { settings: StudioSettings; ret: CheckoutReturn; load: StatusLoad }) {
  if (ret.outcome === 'cancelled') {
    return (
      <BookingFrame
        settings={settings}
        eyebrow="Checkout cancelled"
        title="No payment was taken"
        intro={<p>You left checkout before paying. Nothing was charged.</p>}
      >
        <div className="calendar-purpose-copy">
          {ret.reference && <p>Request {ret.reference} is on file without a deposit. The shop will follow up by email.</p>}
          <div className="calendar-purpose-actions">
            <Link to={BOOK_ROUTE.artist} className="btn btn-primary">Start again</Link>
            <Link to={BOOK_ROUTE.ask} className="calendar-purpose-link">Ask the shop</Link>
          </div>
        </div>
      </BookingFrame>
    )
  }
  const result = load.status === 'ready' ? load.result : null
  const reference = result?.reference_code || ret.reference
  return (
    <BookingFrame
      settings={settings}
      eyebrow="Booking request"
      title={result?.booking_status === 'confirmed' ? 'You are booked' : 'Request received'}
      intro={<p>We sent the details to your email. Keep the reference for anything you ask the shop about this booking.</p>}
      summary={(
        <div className="calendar-purpose-copy">
          {reference && <p>Reference: {reference}</p>}
          {result?.artist_name && <p>Artist: {result.artist_name}</p>}
          {result?.scheduled_date && <p>When: {slotLabel({ date: result.scheduled_date, time: result.scheduled_time || '00:00' })}</p>}
        </div>
      )}
    >
      <div className="calendar-purpose-copy" aria-live="polite">
        {load.status === 'loading' && <p>Checking your payment.</p>}
        {result && <p>{depositLine(result)}</p>}
        {load.status === 'missing' && (
          <p>We could not look this payment up yet. If Stripe showed a receipt, the payment went through; the shop will confirm by email.</p>
        )}
        <div className="calendar-purpose-actions">
          <Link to="/portal" className="btn btn-primary">Open your account</Link>
          <Link to="/" className="calendar-purpose-link">Back to the studio</Link>
        </div>
      </div>
    </BookingFrame>
  )
}

/* ── The page ───────────────────────────────────────────────────────── */

export default function PublicBookingPage({ step }: { step: BookingStep }) {
  const { settings } = useStudioSettings()
  const location = useLocation()
  const navigate = useNavigate()
  const storage = useMemo(sessionStore, [])
  const [draft, setDraftState] = useState<BookingDraft>(() => readDraft(storage))
  const [roster, setRoster] = useState<RosterLoad>({ status: 'loading' })
  const [problem, setProblem] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [submitted, setSubmitted] = useState<PublicBookingResponse | null>(null)
  const [asked, setAsked] = useState<PublicInquiryResponse | null>(null)
  const [statusLoad, setStatusLoad] = useState<{ sessionId: string; load: StatusLoad } | null>(null)

  const deepLinkSlug = step === 'artist' ? artistSlugFromPath(location.pathname) : null
  const ret = step === 'confirm' ? checkoutReturn(location.search) : null
  const needsRoster = step === 'artist' || step === 'ask'

  const setDraft = (next: BookingDraft) => {
    setDraftState(next)
    writeDraft(storage, next)
  }
  const patch = (fields: Partial<BookingDraft>) => setDraft({ ...draft, ...fields })

  useEffect(() => {
    setProblem(null)
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
  useEffect(() => {
    if (deepLinked && draft.artistSlug !== deepLinked.slug) {
      setDraft({ ...draft, artistSlug: deepLinked.slug, artistName: deepLinked.name })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepLinked?.slug])

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
  if (step === 'confirm' && submitted) {
    return (
      <BookingFrame
        settings={settings}
        eyebrow="Booking request"
        title={submitted.checkout_url ? 'Taking you to the deposit' : 'Request sent'}
        intro={<p>{submitted.message}</p>}
        summary={(
          <div className="calendar-purpose-copy">
            {submitted.reference_code && <p>Reference: {submitted.reference_code}</p>}
            {submitted.artist_name && <p>Artist: {submitted.artist_name}</p>}
          </div>
        )}
      >
        <div className="calendar-purpose-copy" aria-live="polite">
          {submitted.checkout_url
            ? <p>Opening secure checkout. <a className="calendar-purpose-link" href={submitted.checkout_url}>Continue to checkout</a></p>
            : <p>Your artist reviews the request and confirms one of your times by email.</p>}
          <div className="calendar-purpose-actions">
            <Link to="/portal" className="btn btn-primary">Open your account</Link>
            <Link to="/" className="calendar-purpose-link">Back to the studio</Link>
          </div>
        </div>
      </BookingFrame>
    )
  }

  /* Steps 2 and 3 need what came before them. */
  if (!draft.artistSlug) return <Navigate to={BOOK_ROUTE.artist} replace />
  if (step === 'confirm' && seshProblem(draft)) return <Navigate to={BOOK_ROUTE.sesh} replace />

  /* Step 2 */
  if (step === 'sesh') {
    const submit = (event: FormEvent) => {
      event.preventDefault()
      const missing = seshProblem(draft)
      if (missing) { setProblem(missing); return }
      navigate(BOOK_ROUTE.confirm)
    }
    return (
      <BookingFrame
        settings={settings}
        eyebrow="Book a session · Step 2 of 3"
        title="Tell us about the piece"
        intro={<p>Describe the idea, where it goes and roughly how big. Propose up to three times that work for you; nothing is held until the artist confirms one.</p>}
        summary={<DraftSummary draft={draft} />}
      >
        <form className="form-grid" onSubmit={submit} noValidate>
          <div className="input-group full-width">
            <label htmlFor="booking-description">The idea</label>
            <textarea id="booking-description" rows={5} value={draft.description} onChange={(e) => patch({ description: e.currentTarget.value })} required />
          </div>
          <div className="input-group">
            <label htmlFor="booking-placement">Placement</label>
            <input id="booking-placement" value={draft.placement} onChange={(e) => patch({ placement: e.currentTarget.value })} />
          </div>
          <div className="input-group">
            <label htmlFor="booking-size">Size</label>
            <input id="booking-size" value={draft.size} onChange={(e) => patch({ size: e.currentTarget.value })} />
          </div>
          {draft.slots.map((slot, index) => (
            <div key={index} className="input-group full-width">
              <label htmlFor={`booking-slot-date-${index}`}>{index === 0 ? 'Preferred time' : `Another time (optional)`}</label>
              <div className="form-grid">
                <input
                  id={`booking-slot-date-${index}`}
                  type="date"
                  aria-label={`Date ${index + 1}`}
                  value={slot.date}
                  onChange={(e) => patch({ slots: draft.slots.map((s, i) => (i === index ? { ...s, date: e.currentTarget.value } : s)) })}
                />
                <input
                  type="time"
                  aria-label={`Time ${index + 1}`}
                  value={slot.time}
                  onChange={(e) => patch({ slots: draft.slots.map((s, i) => (i === index ? { ...s, time: e.currentTarget.value } : s)) })}
                />
              </div>
            </div>
          ))}
          <div className="full-width"><Problem message={problem} /></div>
          <div className="full-width calendar-purpose-actions">
            <button type="submit" className="btn btn-primary">Continue</button>
            <Link to={BOOK_ROUTE.artist} className="calendar-purpose-link">Back</Link>
          </div>
        </form>
      </BookingFrame>
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
      setSubmitted(response)
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
  return (
    <form className="form-grid" onSubmit={onSubmit} noValidate>
      {children}
      <div className="input-group">
        <label htmlFor="contact-first">First name</label>
        <input id="contact-first" autoComplete="given-name" value={fields.firstName} onChange={(e) => onChange({ firstName: e.currentTarget.value })} required />
      </div>
      <div className="input-group">
        <label htmlFor="contact-last">Last name</label>
        <input id="contact-last" autoComplete="family-name" value={fields.lastName} onChange={(e) => onChange({ lastName: e.currentTarget.value })} required />
      </div>
      <div className="input-group">
        <label htmlFor="contact-email">Email</label>
        <input id="contact-email" type="email" autoComplete="email" value={fields.email} onChange={(e) => onChange({ email: e.currentTarget.value })} required />
      </div>
      <div className="input-group">
        <label htmlFor="contact-phone">Phone (optional)</label>
        <input id="contact-phone" type="tel" autoComplete="tel" value={fields.phone} onChange={(e) => onChange({ phone: e.currentTarget.value })} />
      </div>
      {fields.phone.trim() && (
        <label className="consent-row full-width">
          <input type="checkbox" checked={fields.smsOptIn} onChange={(e) => onChange({ smsOptIn: e.currentTarget.checked })} />
          <span>Text me updates about this request. Message and data rates may apply; reply STOP to opt out. <a href="/sms-terms">SMS terms</a> · <a href="/privacy">Privacy</a></span>
        </label>
      )}
      <div className="full-width"><Problem message={problem} /></div>
      <div className="full-width calendar-purpose-actions">
        <button type="submit" className="btn btn-primary" disabled={sending}>{sending ? 'Sending' : submitLabel}</button>
        {back && <Link to={back} className="calendar-purpose-link">Back</Link>}
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
        <div className="calendar-purpose-copy">
          <p>Reference: {asked.reference_code}</p>
          <div className="calendar-purpose-actions">
            <Link to={BOOK_ROUTE.artist} className="btn btn-primary">Book a session</Link>
            <Link to="/" className="calendar-purpose-link">Back to the studio</Link>
          </div>
        </div>
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
          <div className="input-group full-width">
            <label htmlFor="ask-artist">For</label>
            <select id="ask-artist" value={fields.artistSlug} onChange={(e) => setFields({ ...fields, artistSlug: e.currentTarget.value })}>
              <option value="">The shop</option>
              {artists.map((artist) => <option key={artist.slug} value={artist.slug}>{artist.name}</option>)}
            </select>
          </div>
        )}
        <div className="input-group full-width">
          <label htmlFor="ask-message">Your question</label>
          <textarea id="ask-message" rows={5} value={fields.message} onChange={(e) => setFields({ ...fields, message: e.currentTarget.value })} required />
        </div>
      </ContactForm>
    </BookingFrame>
  )
}
