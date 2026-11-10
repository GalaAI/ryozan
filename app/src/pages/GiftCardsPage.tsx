/**
 * GiftCardsPage: public gift card purchase at /gift-cards, and the confirmation Stripe returns buyers to
 * (/gift-cards?session_id=..., or ?cancelled=1 when they back out of checkout).
 *
 * The buyer picks an artist and an amount, then whether the code is emailed to the recipient or to them.
 * Limits and the price adjustment come from GET /gift-cards/terms; the artists and studio name come from the
 * install, so nothing here is shop specific. Built only from @ryozan/ui inside the public form shell.
 * Server contract: api/app/api/v1/endpoints/gift_cards.py.
 */

import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Helmet } from 'react-helmet-async'
import {
  Button, Chip, ChipGroup, MetaList, PageTitle, Panel, SectionLabel, StatusText, TextField,
} from '@ryozan/ui'
import {
  artists as artistsApi,
  giftCards,
  type ArtistSummary,
  type GiftCardStatusResponse,
  type GiftCardTerms,
} from '../lib/api'
import {
  GIFT_CARD_POLL_LIMIT, GIFT_CARD_POLL_MS, adjustmentLabel, chargedCents, confirmationPhase, dollarsToCents,
  formatCents, presetAmounts,
} from '../lib/gift-cards'
import { useStudioSettings } from '../hooks/useStudioSettings'
import '../styles/dashboard.css'

const column = { width: '100%', display: 'grid', gap: 'var(--space-5)' } as const
const panelBody = { padding: 'var(--space-5)', gap: 'var(--space-4)' } as const
const twoUp = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 12rem), 1fr))', gap: 'var(--space-4)' } as const


function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

export default function GiftCardsPage() {
  const [params] = useSearchParams()
  const sessionId = params.get('session_id')
  const { settings } = useStudioSettings()
  const studioName = settings.studio_name

  return (
    <div className="public-form-shell">
      <Helmet>
        <title>{studioName ? `Gift cards | ${studioName}` : 'Gift cards'}</title>
        {sessionId && <meta name="robots" content="noindex" />}
      </Helmet>
      <div className="public-form-container">
        <main style={column}>
          {sessionId
            ? <GiftCardConfirmation sessionId={sessionId} studioName={studioName} />
            : <GiftCardPurchase studioName={studioName} cancelled={params.get('cancelled') === '1'} />}
        </main>
      </div>
    </div>
  )
}

function GiftCardConfirmation({ sessionId, studioName }: { sessionId: string; studioName: string }) {
  const navigate = useNavigate()
  const [status, setStatus] = useState<GiftCardStatusResponse | null>(null)
  const [error, setError] = useState('')
  const [gaveUp, setGaveUp] = useState(false)

  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    let attempts = 0
    const poll = async () => {
      attempts += 1
      try {
        const next = await giftCards.status(sessionId)
        if (stopped) return
        setStatus(next)
        setError('')
        if (!confirmationPhase(next).keepPolling) return
      } catch (err) {
        if (stopped) return
        if ((err as { status?: number }).status === 404) {
          setError('We could not find that gift card. If you were charged, contact the shop with your receipt.')
          return
        }
      }
      if (attempts >= GIFT_CARD_POLL_LIMIT) {
        setGaveUp(true)
        return
      }
      timer = window.setTimeout(poll, GIFT_CARD_POLL_MS)
    }
    void poll()
    return () => { stopped = true; if (timer) window.clearTimeout(timer) }
  }, [sessionId])

  const { phase } = confirmationPhase(status)
  const title = phase === 'delivered' ? 'Gift card sent' : phase === 'not_created' ? 'Gift card not created' : 'Confirming your gift'

  return (
    <>
      <PageTitle meta={studioName || undefined}>{title}</PageTitle>
      <Panel surface="raised" style={panelBody} aria-live="polite">
        {status && phase !== 'not_created' && (
          <MetaList rows={[
            { label: 'Gift value', value: formatCents(status.face_value_cents) },
            { label: 'Sent to', value: status.recipient_email_hint },
          ]} />
        )}
        {phase === 'confirming' && !error && <StatusText tone="pending" wrap>Confirming. Waiting on the payment. This page updates on its own.</StatusText>}
        {phase === 'delivering' && <StatusText tone="pending" wrap>Payment confirmed. Sending the email now.</StatusText>}
        {phase === 'delivered' && <StatusText tone="ok" wrap>Delivered. The code is in that inbox.</StatusText>}
        {phase === 'not_created' && <StatusText tone="danger" wrap>The payment did not complete. No gift card was issued.</StatusText>}
        {gaveUp && phase !== 'delivered' && phase !== 'not_created' && (
          <SectionLabel>Still confirming. Refresh this page in a minute; your receipt email has the details.</SectionLabel>
        )}
        {error && <StatusText tone="danger" wrap role="alert">{error}</StatusText>}
        <div><Button variant="outline" onClick={() => navigate('/')}>Back to {studioName || 'the shop'}</Button></div>
      </Panel>
    </>
  )
}

function GiftCardPurchase({ studioName, cancelled }: { studioName: string; cancelled: boolean }) {
  const navigate = useNavigate()
  const [terms, setTerms] = useState<GiftCardTerms | null>(null)
  const [artists, setArtists] = useState<ArtistSummary[]>([])
  const [loadError, setLoadError] = useState('')
  const [artistId, setArtistId] = useState('')
  const [preset, setPreset] = useState<number | null>(null)
  const [custom, setCustom] = useState('')
  const [purchaserName, setPurchaserName] = useState('')
  const [purchaserEmail, setPurchaserEmail] = useState('')
  const [recipientName, setRecipientName] = useState('')
  const [recipientEmail, setRecipientEmail] = useState('')
  const [sendToRecipient, setSendToRecipient] = useState(true)
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([giftCards.terms(), artistsApi.list()])
      .then(([t, rows]) => {
        if (!active) return
        const list = rows.filter((a) => a.id && a.name)
        setTerms(t)
        setArtists(list)
        setArtistId((cur) => cur || (list.length === 1 ? list[0].id : ''))
        const presets = presetAmounts(t)
        setPreset((cur) => cur ?? presets[Math.min(1, presets.length - 1)] ?? null)
      })
      .catch(() => { if (active) setLoadError('Gift cards could not be loaded. Try again in a minute.') })
    return () => { active = false }
  }, [])

  const presets = useMemo(() => (terms ? presetAmounts(terms) : []), [terms])
  const faceValue = custom.trim() ? dollarsToCents(custom) : preset
  const inRange = terms && faceValue !== null && faceValue >= terms.min_amount_cents && faceValue <= terms.max_amount_cents

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    if (!terms) return
    if (!artistId) { setError('Pick the artist the gift is for.'); return }
    if (!inRange || faceValue === null) {
      setError(`Choose an amount from ${formatCents(terms.min_amount_cents)} to ${formatCents(terms.max_amount_cents)}.`)
      return
    }
    if (!purchaserName.trim() || !purchaserEmail.trim() || !recipientName.trim() || (sendToRecipient && !recipientEmail.trim())) {
      setError('Fill in every name and email.')
      return
    }
    setSubmitting(true)
    try {
      const result = await giftCards.checkout({
        artist_id: artistId,
        amount_cents: faceValue,
        purchaser_name: purchaserName.trim(),
        purchaser_email: purchaserEmail.trim(),
        recipient_name: recipientName.trim(),
        recipient_email: sendToRecipient ? recipientEmail.trim() : undefined,
        send_to_recipient: sendToRecipient,
        message: message.trim() || undefined,
      })
      window.location.assign(result.checkout_url)
    } catch (err) {
      setError(messageOf(err, 'Checkout could not be started. Try again.'))
      setSubmitting(false)
    }
  }

  return (
    <>
      <PageTitle
        meta={[studioName, 'Gift cards'].filter(Boolean).join(' // ')}
        action={<Button variant="ghost" size="sm" onClick={() => navigate('/')}>Back</Button>}
      >
        Give their next tattoo
      </PageTitle>

      {cancelled && <StatusText tone="pending" wrap>Checkout was cancelled. Nothing was charged.</StatusText>}
      {loadError && <StatusText tone="danger" wrap role="alert">{loadError}</StatusText>}

      <form noValidate onSubmit={(e) => void submit(e)} aria-label="Buy a gift card" style={column}>
        <Panel surface="raised" style={panelBody}>
          <SectionLabel>Tattoo credit with the artist you choose, emailed to them or to you.</SectionLabel>
          <ChipGroup label="Artist" role="group" aria-label="Artist">
            {artists.map((a) => (
              <Chip key={a.id} selected={artistId === a.id} onClick={() => setArtistId(a.id)}>{a.name}</Chip>
            ))}
          </ChipGroup>
          {terms && artists.length === 0 && <SectionLabel tone="ghost">No artists are taking gift cards right now.</SectionLabel>}
          <ChipGroup label="Amount" role="group" aria-label="Amount">
            {presets.map((cents) => (
              <Chip key={cents} selected={!custom.trim() && preset === cents} onClick={() => { setPreset(cents); setCustom('') }}>
                {formatCents(cents).replace('.00', '')}
              </Chip>
            ))}
          </ChipGroup>
          <TextField
            label="Or another amount"
            inputMode="decimal"
            placeholder={terms ? `${formatCents(terms.min_amount_cents)} to ${formatCents(terms.max_amount_cents)}` : ''}
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
          />
        </Panel>

        <Panel surface="raised" style={panelBody}>
          <SectionLabel>From</SectionLabel>
          <div style={twoUp}>
            <TextField label="Your name" autoComplete="name" maxLength={255} value={purchaserName} onChange={(e) => setPurchaserName(e.target.value)} />
            <TextField label="Your email" type="email" autoComplete="email" value={purchaserEmail} onChange={(e) => setPurchaserEmail(e.target.value)} />
          </div>
          <ChipGroup label="Who gets the email" role="group" aria-label="Who gets the email">
            <Chip selected={sendToRecipient} onClick={() => setSendToRecipient(true)}>Email it to them</Chip>
            <Chip selected={!sendToRecipient} onClick={() => setSendToRecipient(false)}>Email it to me</Chip>
          </ChipGroup>
          <SectionLabel>To</SectionLabel>
          <div style={twoUp}>
            <TextField label="Their name" maxLength={255} value={recipientName} onChange={(e) => setRecipientName(e.target.value)} />
            {sendToRecipient && (
              <TextField label="Their email" type="email" value={recipientEmail} onChange={(e) => setRecipientEmail(e.target.value)} />
            )}
          </div>
          <TextField label="Message (optional)" maxLength={500} value={message} onChange={(e) => setMessage(e.target.value)} />
        </Panel>

        <Panel surface="raised" style={panelBody}>
          <MetaList rows={[
            { label: 'Gift value', value: inRange && faceValue !== null ? formatCents(faceValue) : 'Pick an amount' },
            {
              label: 'Checkout total',
              value: inRange && faceValue !== null && terms ? formatCents(chargedCents(faceValue, terms.price_adjustment_bps)) : 'Pick an amount',
            },
          ]} />
          {terms && terms.price_adjustment_bps > 0 && (
            <SectionLabel tone="ghost">Checkout total includes the {adjustmentLabel(terms.price_adjustment_bps)} card price adjustment.</SectionLabel>
          )}
          {error && <StatusText tone="danger" wrap role="alert">{error}</StatusText>}
          <Button type="submit" size="lg" fullWidth disabled={submitting || !terms || artists.length === 0}>
            {submitting ? 'Starting checkout…' : 'Continue to secure checkout'}
          </Button>
        </Panel>
      </form>
    </>
  )
}
