/**
 * The shop's email sender, for the owner and admins on the Connections screen.
 * By default mail goes out from the platform sender under the studio's name.
 * The shop can add its own domain: Ryozan creates it on the platform's email
 * account, lists the DNS records to add, and checks them. Once the domain is
 * verified, the shop's mail goes out from it. On the demo shop nothing reaches
 * the email provider and the records are a sample.
 */

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { shopConnections, type ShopEmailConnection as ShopEmail } from '../lib/api'

type Load = { state: 'loading' } | { state: 'ready'; value: ShopEmail } | { state: 'failed' }

const STATUS_LABEL: Record<string, string> = {
  not_started: 'Waiting for DNS records',
  pending: 'Checking DNS records',
  verified: 'Verified',
  failed: 'Records not found',
  temporary_failure: 'Records not found yet',
}

function errorText(err: unknown, fallback: string) {
  return err instanceof Error && err.message ? err.message : fallback
}

function shopEmailTone(load: Load) {
  if (load.state === 'loading') return 'loading' as const
  if (load.state === 'failed') return 'failed' as const
  if (load.value.domain && load.value.status === 'verified' && !load.value.demo) return 'ready' as const
  if (load.value.domain) return 'incomplete' as const
  return load.value.platform_ready || load.value.demo ? ('available' as const) : ('disconnected' as const)
}

export default function ShopEmailConnection() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const [open, setOpen] = useState(false)
  const [domain, setDomain] = useState('')
  const [local, setLocal] = useState('hello')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setLoad({ state: 'ready', value: await shopConnections.email() })
    } catch {
      setLoad({ state: 'failed' })
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const act = async (work: () => Promise<ShopEmail>, fallback: string) => {
    setBusy(true)
    setError(null)
    try {
      setLoad({ state: 'ready', value: await work() })
    } catch (err) {
      setError(errorText(err, fallback))
    } finally {
      setBusy(false)
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void act(() => shopConnections.addEmailDomain(domain.trim(), local.trim() || 'hello'), 'The domain could not be added.')
  }

  const tone = shopEmailTone(load)
  const value = load.state === 'ready' ? load.value : null
  const status = load.state === 'loading'
    ? 'Checking'
    : load.state === 'failed'
      ? 'Unavailable'
      : value?.domain
        ? STATUS_LABEL[value.status || ''] || value.status || 'Added'
        : value?.platform_ready || value?.demo ? 'Ryozan sender' : 'Not set up'
  const detail = load.state === 'loading'
    ? 'Reading where your shop email goes out from.'
    : load.state === 'failed'
      ? 'The email sender could not be read. No sender is being assumed.'
      : value?.domain && value.status === 'verified' && !value.demo
        ? `Your shop's mail goes out from ${value.from_address}.`
        : value?.platform_ready || value?.demo
          ? `Your shop's mail goes out from ${value?.sending_from || 'the Ryozan sender'} as ${value?.sender_name || 'your studio'}. Add your own domain to send from it instead.`
          : 'This server has no email sender set up yet.'

  return (
    <article className={`ryozan-artist-connection ryozan-artist-connection--${tone}`} data-connection="shop-email">
      <div className="ryozan-artist-connection__mark" aria-hidden="true">@</div>
      <div className="ryozan-artist-connection__copy">
        <header>
          <strong>Shop email</strong>
          <span>{status}</span>
        </header>
        <p>{detail}</p>
        <button type="button" disabled={load.state !== 'ready'} onClick={() => setOpen((was) => !was)}>
          {open ? 'Close' : value?.domain ? 'Manage domain' : 'Use your own domain'}
        </button>
      </div>
      {open && value ? (
        <div className="ryozan-shop-email" data-testid="shop-email-panel">
          {value.demo ? (
            <p className="ryozan-shop-email__demo" role="note">
              Demo shop: nothing is registered with the email provider, and the records below are a sample. It clears at the nightly reset.
            </p>
          ) : null}
          {error ? <p className="ryozan-shop-email__error" role="alert">{error}</p> : null}
          {value.domain ? (
            <>
              <p>
                Add these records at your domain's DNS provider, then check them. Mail keeps going out from the Ryozan sender until <strong>{value.domain}</strong> is verified.
              </p>
              <table className="ryozan-shop-email__records">
                <thead>
                  <tr><th>Type</th><th>Name</th><th>Value</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {value.records.map((record, index) => (
                    <tr key={`${record.type}-${record.name}-${index}`}>
                      <td>{record.type}{record.priority != null ? ` ${record.priority}` : ''}</td>
                      <td><code>{record.name}</code></td>
                      <td><code>{record.value}</code></td>
                      <td>{record.status ? STATUS_LABEL[record.status] || record.status : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="ryozan-shop-email__actions">
                <button type="button" disabled={busy} onClick={() => void act(shopConnections.checkEmailDomain, 'The records could not be checked.')}>
                  {busy ? 'Working...' : 'Check records'}
                </button>
                <button type="button" className="is-danger" disabled={busy} onClick={() => void act(shopConnections.removeEmailDomain, 'The domain could not be removed.')}>
                  Remove domain
                </button>
              </div>
            </>
          ) : (
            <form className="ryozan-shop-email__form" onSubmit={submit}>
              <label>
                <span>Send as</span>
                <input value={local} onChange={(event) => setLocal(event.target.value)} aria-label="Sender name before the @" />
              </label>
              <span aria-hidden="true">@</span>
              <label>
                <span>Your domain</span>
                <input value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="yourstudio.com" aria-label="Your domain" required />
              </label>
              <button type="submit" disabled={busy || !domain.trim()}>{busy ? 'Adding...' : 'Add domain'}</button>
            </form>
          )}
        </div>
      ) : null}
    </article>
  )
}
