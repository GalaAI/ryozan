/**
 * The shop's texting line, for the owner and admins on the Connections screen.
 * With Ryozan: a Twilio sub-account for the shop on the platform, a toll-free
 * number, and the toll-free verification carriers require before the line may
 * text the public. Or the shop's own Twilio account. Tokens never come back
 * from the server. On the demo shop nothing reaches Twilio.
 */

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { shopConnections, type ShopTextsConnection as ShopTexts, type ShopTextsVerification } from '../lib/api'

type Load = { state: 'loading' } | { state: 'ready'; value: ShopTexts } | { state: 'failed' }
type Panel = null | 'own' | 'numbers' | 'verify'

const VERIFICATION_LABEL: Record<string, string> = {
  PENDING_REVIEW: 'Verification submitted',
  IN_REVIEW: 'Verification in review',
  TWILIO_APPROVED: 'Verified',
  TWILIO_REJECTED: 'Verification rejected',
}

const BLANK_FORM: ShopTextsVerification = {
  business_name: '',
  business_type: 'PRIVATE_PROFIT',
  ein: '',
  website: 'https://',
  notification_email: '',
  contact_first_name: '',
  contact_last_name: '',
  contact_phone: '',
  street: '',
  city: '',
  state: '',
  postal_code: '',
  use_case: 'ACCOUNT_NOTIFICATIONS',
  use_case_summary: '',
  message_sample: '',
  opt_in_type: 'WEB_FORM',
  opt_in_image_url: 'https://',
  volume: '1,000',
}

const FIELDS: [keyof ShopTextsVerification, string][] = [
  ['business_name', 'Legal business name'],
  ['ein', 'EIN (not needed for a sole proprietor)'],
  ['website', 'Website'],
  ['notification_email', 'Email for verification updates'],
  ['contact_first_name', 'Contact first name'],
  ['contact_last_name', 'Contact last name'],
  ['contact_phone', 'Contact phone (+1...)'],
  ['street', 'Street address'],
  ['city', 'City'],
  ['state', 'State'],
  ['postal_code', 'ZIP code'],
  ['use_case_summary', 'What you text clients about'],
  ['message_sample', 'A real sample message'],
  ['opt_in_image_url', 'Link to a screenshot of where clients agree to texts'],
]

const label = (value: string) => value.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (first) => first.toUpperCase())

function errorText(err: unknown, fallback: string) {
  return err instanceof Error && err.message ? err.message : fallback
}

export default function ShopTextsConnection() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const [panel, setPanel] = useState<Panel>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [numbers, setNumbers] = useState<{ phone_number: string; friendly_name?: string }[]>([])
  const [picked, setPicked] = useState('')
  const [own, setOwn] = useState({ sid: '', token: '', number: '' })
  const [form, setForm] = useState<ShopTextsVerification>(BLANK_FORM)

  const refresh = useCallback(async () => {
    try {
      setLoad({ state: 'ready', value: await shopConnections.texts() })
    } catch {
      setLoad({ state: 'failed' })
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const act = async (work: () => Promise<ShopTexts>, fallback: string, next: Panel = null) => {
    setBusy(true)
    setError(null)
    try {
      setLoad({ state: 'ready', value: await work() })
      setPanel(next)
    } catch (err) {
      setError(errorText(err, fallback))
    } finally {
      setBusy(false)
    }
  }

  const listNumbers = async () => {
    setBusy(true)
    setError(null)
    try {
      const found = await shopConnections.findTextsNumbers()
      setNumbers(found.numbers)
      setPicked(found.numbers[0]?.phone_number || '')
      setPanel('numbers')
    } catch (err) {
      setError(errorText(err, 'Numbers could not be listed.'))
    } finally {
      setBusy(false)
    }
  }

  const submitOwn = (event: FormEvent) => {
    event.preventDefault()
    void act(() => shopConnections.connectOwnTexts(own.sid.trim(), own.token.trim(), own.number.trim()), 'Twilio did not accept those details.')
    setOwn({ sid: '', token: '', number: '' })
  }

  const submitVerification = (event: FormEvent) => {
    event.preventDefault()
    void act(() => shopConnections.submitTextsVerification({ ...form, ein: form.ein?.trim() || undefined }), 'The verification could not be submitted.')
  }

  const value = load.state === 'ready' ? load.value : null
  const verification = value?.verification_status || null
  const tone = load.state === 'loading'
    ? 'loading'
    : load.state === 'failed'
      ? 'failed'
      : value?.sending_ready
        ? 'ready'
        : value?.mode
          ? 'incomplete'
          : 'disconnected'
  const status = load.state === 'loading'
    ? 'Checking'
    : load.state === 'failed'
      ? 'Unavailable'
      : value?.sending_ready
        ? value.phone_number || 'Ready'
        : verification
          ? VERIFICATION_LABEL[verification] || verification
          : value?.phone_number
            ? 'Needs verification'
            : value?.mode
              ? 'Needs a number'
              : 'Not set up'
  const detail = load.state === 'loading'
    ? 'Reading your shop texting line.'
    : load.state === 'failed'
      ? 'The texting line could not be read. No line is being assumed.'
      : value?.sending_ready
        ? `Your shop's texts go out from ${value.phone_number}${value.mode === 'own' ? ' on your Twilio account' : ''}.`
        : verification === 'TWILIO_REJECTED'
          ? `Twilio rejected the verification${value?.verification_reason ? `: ${value.verification_reason}` : ''}. Disconnect and start again with corrected details.`
          : verification
            ? 'Carriers block toll-free texts until Twilio approves the verification, which usually takes 3 to 5 business days.'
            : value?.phone_number
              ? `${value.phone_number} is yours. Carriers require a toll-free verification before it can text clients.`
              : value?.mode === 'platform'
                ? 'Your shop has its own Twilio sub-account on Ryozan. Pick a toll-free number next.'
                : 'Text clients from your own number. Ryozan sets it up on its Twilio account, or connect yours.'

  const field = (name: keyof ShopTextsVerification, text: string) => (
    <label key={name}>
      <span>{text}</span>
      <input value={form[name] || ''} onChange={(event) => setForm((was) => ({ ...was, [name]: event.target.value }))} />
    </label>
  )

  return (
    <article className={`ryozan-artist-connection ryozan-artist-connection--${tone}`} data-connection="shop-texts">
      <div className="ryozan-artist-connection__mark" aria-hidden="true">#</div>
      <div className="ryozan-artist-connection__copy">
        <header>
          <strong>Shop texts</strong>
          <span>{status}</span>
        </header>
        <p>{detail}</p>
        {value && !value.mode ? (
          <div className="ryozan-shop-email__actions">
            <button type="button" disabled={busy || !value.platform_ready} onClick={() => void act(shopConnections.startPlatformTexts, 'The texting line could not be set up.')}>
              {busy ? 'Setting up...' : 'Set up with Ryozan'}
            </button>
            {!value.demo ? (
              <button type="button" disabled={busy} onClick={() => setPanel(panel === 'own' ? null : 'own')}>Use my own Twilio</button>
            ) : null}
          </div>
        ) : null}
        {value?.mode === 'platform' && !value.phone_number ? (
          <button type="button" disabled={busy} onClick={() => void listNumbers()}>{busy ? 'Finding...' : 'Find a number'}</button>
        ) : null}
        {value?.mode === 'platform' && value.phone_number && !verification ? (
          <button type="button" disabled={busy} onClick={() => setPanel(panel === 'verify' ? null : 'verify')}>Verify for texting</button>
        ) : null}
        {value?.mode ? (
          <div className="ryozan-shop-email__actions">
            {verification || value.mode === 'own' ? (
              <button type="button" disabled={busy} onClick={() => void act(shopConnections.checkTexts, 'The status could not be checked.')}>Check status</button>
            ) : null}
            <button type="button" className="is-danger" disabled={busy} onClick={() => void act(shopConnections.disconnectTexts, 'The line could not be disconnected.')}>
              Disconnect
            </button>
          </div>
        ) : null}
      </div>
      {value?.demo || error || panel ? (
        <div className="ryozan-shop-email" data-testid="shop-texts-panel">
          {value?.demo ? (
            <p className="ryozan-shop-email__demo" role="note">
              Demo shop: no Twilio account, number or verification is made or bought. It clears at the nightly reset.
            </p>
          ) : null}
          {error ? <p className="ryozan-shop-email__error" role="alert">{error}</p> : null}
          {panel === 'own' ? (
            <form className="ryozan-shop-email__form" onSubmit={submitOwn}>
              <label><span>Account SID</span><input value={own.sid} onChange={(event) => setOwn((was) => ({ ...was, sid: event.target.value }))} placeholder="AC..." /></label>
              <label><span>Auth Token</span><input type="password" autoComplete="off" value={own.token} onChange={(event) => setOwn((was) => ({ ...was, token: event.target.value }))} /></label>
              <label><span>Your Twilio number</span><input value={own.number} onChange={(event) => setOwn((was) => ({ ...was, number: event.target.value }))} placeholder="+15125550100" /></label>
              <button type="submit" disabled={busy || !own.sid || !own.token || !own.number}>{busy ? 'Checking...' : 'Connect'}</button>
            </form>
          ) : null}
          {panel === 'numbers' ? (
            <form className="ryozan-shop-email__form" onSubmit={(event) => {
              event.preventDefault()
              void act(() => shopConnections.buyTextsNumber(picked), 'The number could not be bought.')
            }}>
              <label>
                <span>Toll-free number</span>
                <select value={picked} onChange={(event) => setPicked(event.target.value)}>
                  {numbers.map((number) => <option key={number.phone_number} value={number.phone_number}>{number.friendly_name || number.phone_number}</option>)}
                </select>
              </label>
              <button type="submit" disabled={busy || !picked}>{busy ? 'Getting it...' : value?.demo ? 'Use this number (demo)' : 'Get this number'}</button>
            </form>
          ) : null}
          {panel === 'verify' && value ? (
            <form className="ryozan-shop-texts__verify" onSubmit={submitVerification}>
              <label>
                <span>Business type</span>
                <select value={form.business_type} onChange={(event) => setForm((was) => ({ ...was, business_type: event.target.value as ShopTextsVerification['business_type'] }))}>
                  <option value="PRIVATE_PROFIT">Registered business</option>
                  <option value="SOLE_PROPRIETOR">Sole proprietor</option>
                  <option value="NON_PROFIT">Non-profit</option>
                </select>
              </label>
              {FIELDS.map(([name, text]) => field(name, text))}
              <label>
                <span>Kind of texts</span>
                <select value={form.use_case} onChange={(event) => setForm((was) => ({ ...was, use_case: event.target.value }))}>
                  {value.use_cases.map((option) => <option key={option} value={option}>{label(option)}</option>)}
                </select>
              </label>
              <label>
                <span>How clients agree to texts</span>
                <select value={form.opt_in_type} onChange={(event) => setForm((was) => ({ ...was, opt_in_type: event.target.value }))}>
                  {value.opt_in_types.map((option) => <option key={option} value={option}>{label(option)}</option>)}
                </select>
              </label>
              <label>
                <span>Texts a month</span>
                <select value={form.volume} onChange={(event) => setForm((was) => ({ ...was, volume: event.target.value }))}>
                  {value.volumes.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              </label>
              <button type="submit" disabled={busy}>{busy ? 'Submitting...' : 'Submit verification'}</button>
            </form>
          ) : null}
        </div>
      ) : null}
    </article>
  )
}
