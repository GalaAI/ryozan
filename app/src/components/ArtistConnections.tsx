/**
 * Shared artist-owned connection surface for the mobile Hub.
 * Existing API clients remain the connection authorities; this component only
 * composes their truthful status and actions without adding a connector registry.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  emailConnect,
  googleAuth,
  pinterestAuth,
  stripeConnect,
  type EmailStatus,
  type StripeConnectStatus,
} from '../lib/api'
import EmailConnectQuest from './EmailConnectQuest'

type Loadable<T> =
  | { state: 'loading'; value: null }
  | { state: 'ready'; value: T }
  | { state: 'failed'; value: null }

type GoogleStatus = Awaited<ReturnType<typeof googleAuth.getStatus>>
type GoogleSigninStatus = Awaited<ReturnType<typeof googleAuth.getSigninLinkStatus>>
type PinterestStatus = Awaited<ReturnType<typeof pinterestAuth.getStatus>>
type ConnectionTone = 'loading' | 'failed' | 'disconnected' | 'incomplete' | 'ready' | 'available'
type ActingConnection = 'stripe' | 'google' | 'google-signin' | 'pinterest' | null

const loading = <T,>(): Loadable<T> => ({ state: 'loading', value: null })

interface ConnectionCardProps {
  name: string
  mark: string
  tone: ConnectionTone
  status: string
  detail: string
  action?: {
    label: string
    onClick: () => void
    disabled?: boolean
    danger?: boolean
  }
  children?: ReactNode
}

function ConnectionCard({ name, mark, tone, status, detail, action, children }: ConnectionCardProps) {
  return (
    <article className={`bttco-artist-connection bttco-artist-connection--${tone}`} data-connection={name.toLowerCase().replace(/\s+/g, '-')}>
      <div className="bttco-artist-connection__mark" aria-hidden="true">{mark}</div>
      <div className="bttco-artist-connection__copy">
        <header>
          <strong>{name}</strong>
          <span>{status}</span>
        </header>
        <p>{detail}</p>
        {action ? (
          <button
            type="button"
            className={action.danger ? 'is-danger' : undefined}
            disabled={action.disabled}
            onClick={action.onClick}
          >
            {action.label}
          </button>
        ) : null}
      </div>
      {children}
    </article>
  )
}

function stripePresentation(status: Loadable<StripeConnectStatus>) {
  if (status.state === 'loading') {
    return {
      tone: 'loading' as const,
      label: 'Checking',
      detail: 'Reading your Stripe merchant and payout readiness.',
    }
  }
  if (status.state === 'failed') {
    return {
      tone: 'failed' as const,
      label: 'Unavailable',
      detail: 'Stripe status could not be read. No connection state is being assumed.',
    }
  }
  if (status.value.provider_state === 'unavailable') {
    return {
      tone: 'failed' as const,
      label: 'Provider unavailable',
      detail: 'Stripe could not confirm the account state. Setup is paused until status is available.',
    }
  }
  if (status.value.provider_state === 'not_connected' || !status.value.connected) {
    return {
      tone: 'disconnected' as const,
      label: 'Not connected',
      detail: 'Connect Stripe to accept card payments as the merchant and receive your payouts.',
    }
  }
  if (!status.value.routing_ready || !status.value.charges_enabled || !status.value.payouts_enabled) {
    const requirementCount = status.value.requirements_due
    const missing = [
      !status.value.charges_enabled ? 'card payments' : null,
      !status.value.payouts_enabled ? 'payouts' : null,
    ].filter(Boolean).join(' and ')
    return {
      tone: 'incomplete' as const,
      label: 'Setup incomplete',
      detail: requirementCount > 0
        ? `Stripe reports ${requirementCount} outstanding ${requirementCount === 1 ? 'requirement' : 'requirements'}. Finish ${requirementCount === 1 ? 'it' : 'them'} before setup can be ready.`
        : missing
        ? `Stripe still needs information before ${missing} can be enabled.`
        : 'Stripe still needs business, identity, or bank information before this account can route payments.',
    }
  }
  return {
    tone: 'ready' as const,
    label: 'Payment ready',
    detail: 'Card payments and payouts are enabled for your connected merchant account.',
  }
}

// Per the 2026-10-29 decision, a service that is not connected or whose
// provider is unavailable shows no control; the list holds live services only.
function isShownConnection(tone: string) {
  return tone !== 'disconnected' && tone !== 'failed'
}

export default function ArtistConnections() {
  const location = useLocation()
  const navigate = useNavigate()
  const requestRef = useRef(0)
  const handledStripeRefreshRef = useRef<string | null>(null)
  const stripeReturn = new URLSearchParams(location.search).get('stripe')
  const googleSigninReturn = new URLSearchParams(location.search).get('google_signin')
  const [stripe, setStripe] = useState<Loadable<StripeConnectStatus>>(loading)
  const [email, setEmail] = useState<Loadable<EmailStatus>>(loading)
  const [google, setGoogle] = useState<Loadable<GoogleStatus>>(loading)
  const [googleSignin, setGoogleSignin] = useState<Loadable<GoogleSigninStatus>>(loading)
  const [pinterest, setPinterest] = useState<Loadable<PinterestStatus>>(loading)
  const [acting, setActing] = useState<ActingConnection>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [emailExpanded, setEmailExpanded] = useState(false)

  const loadStatuses = useCallback(async () => {
    const requestId = ++requestRef.current
    setStripe(loading())
    setEmail(loading())
    setGoogle(loading())
    setGoogleSignin(loading())
    setPinterest(loading())
    setActionError(null)

    const [stripeResult, emailResult, googleResult, googleSigninResult, pinterestResult] = await Promise.allSettled([
      stripeConnect.status(),
      emailConnect.status(),
      googleAuth.getStatus(),
      googleAuth.getSigninLinkStatus(),
      pinterestAuth.getStatus(),
    ])
    if (requestRef.current !== requestId) return

    setStripe(stripeResult.status === 'fulfilled'
      ? { state: 'ready', value: stripeResult.value }
      : { state: 'failed', value: null })
    setEmail(emailResult.status === 'fulfilled'
      ? { state: 'ready', value: emailResult.value }
      : { state: 'failed', value: null })
    setGoogle(googleResult.status === 'fulfilled'
      ? { state: 'ready', value: googleResult.value }
      : { state: 'failed', value: null })
    setGoogleSignin(googleSigninResult.status === 'fulfilled'
      ? { state: 'ready', value: googleSigninResult.value }
      : { state: 'failed', value: null })
    setPinterest(pinterestResult.status === 'fulfilled'
      ? { state: 'ready', value: pinterestResult.value }
      : { state: 'failed', value: null })
  }, [])

  const openStripeOnboarding = useCallback(async (refreshCallback = false) => {
    setActing('stripe')
    setActionError(null)
    try {
      const result = await stripeConnect.onboard()
      if (!result.url) throw new Error('Stripe did not return a secure setup link.')
      window.location.assign(result.url)
    } catch {
      setActionError(refreshCallback
        ? 'Stripe could not renew the expired setup link. Continue setup to try again.'
        : 'Stripe setup could not be opened. Your existing connection was not changed.')
      setActing(null)
    }
  }, [])

  useEffect(() => {
    let active = true
    void loadStatuses().then(() => {
      if (!active || stripeReturn !== 'return') return
      const params = new URLSearchParams(location.search)
      params.delete('stripe')
      const search = params.toString()
      navigate(`${location.pathname}${search ? `?${search}` : ''}`, { replace: true })
    })
    return () => {
      active = false
      requestRef.current += 1
    }
  }, [loadStatuses, location.pathname, location.search, navigate, stripeReturn])

  useEffect(() => {
    if (stripeReturn !== 'refresh') return
    const callbackKey = `${location.pathname}${location.search}`
    if (handledStripeRefreshRef.current === callbackKey) return
    handledStripeRefreshRef.current = callbackKey
    void openStripeOnboarding(true)
  }, [location.pathname, location.search, openStripeOnboarding, stripeReturn])

  const startStripeOnboarding = async () => {
    if (acting) return
    await openStripeOnboarding()
  }

  const connectGoogle = async () => {
    if (acting) return
    setActing('google')
    setActionError(null)
    try {
      const result = await googleAuth.getConnectUrl()
      if (!result.authorization_url) throw new Error('Google did not return an authorization link.')
      window.location.assign(result.authorization_url)
    } catch {
      setActionError('Google connection could not be opened. Try again.')
      setActing(null)
    }
  }

  const linkGoogleSignin = async () => {
    if (acting) return
    setActing('google-signin')
    setActionError(null)
    try {
      const result = await googleAuth.getSigninLinkUrl()
      if (!result.authorization_url) throw new Error('Google did not return a sign-in link.')
      window.location.assign(result.authorization_url)
    } catch {
      setActionError('Google sign-in could not be opened. Try again.')
      setActing(null)
    }
  }

  const disconnectGoogle = async () => {
    if (acting) return
    setActing('google')
    setActionError(null)
    try {
      await googleAuth.disconnect()
      await loadStatuses()
    } catch {
      setActionError('Google could not be disconnected. Its prior state is still shown.')
    } finally {
      setActing(null)
    }
  }

  const connectPinterest = async () => {
    if (acting) return
    setActing('pinterest')
    setActionError(null)
    try {
      const result = await pinterestAuth.getConnectUrl()
      if (!result.authorize_url) throw new Error('Pinterest did not return an authorization link.')
      window.location.assign(result.authorize_url)
    } catch {
      setActionError('Pinterest connection could not be opened. Try again.')
      setActing(null)
    }
  }

  const disconnectPinterest = async () => {
    if (acting) return
    setActing('pinterest')
    setActionError(null)
    try {
      await pinterestAuth.disconnect()
      await loadStatuses()
    } catch {
      setActionError('Pinterest could not be disconnected. Its prior state is still shown.')
    } finally {
      setActing(null)
    }
  }

  const stripeView = stripePresentation(stripe)
  const allLoading = [stripe, email, google, googleSignin, pinterest].every((item) => item.state === 'loading')

  const emailView = email.state === 'loading'
    ? { tone: 'loading' as const, label: 'Checking', detail: 'Reading your Ryozan address status.' }
    : email.state === 'failed'
      ? { tone: 'failed' as const, label: 'Unavailable', detail: 'Email status could not be read. No address state is being assumed.' }
      : email.value.verified
        ? { tone: 'ready' as const, label: email.value.bttco_email || 'Active', detail: `Mail forwards to ${email.value.forward_to || 'your verified inbox'}.` }
        : email.value.connected
          ? { tone: 'incomplete' as const, label: 'Verify your inbox', detail: 'Your address is reserved, but the forwarding inbox still needs verification.' }
          : { tone: 'disconnected' as const, label: 'Not claimed', detail: 'Claim an @ryozan.app address and forward replies to your inbox.' }

  const googleView = google.state === 'loading'
    ? { tone: 'loading' as const, label: 'Checking', detail: 'Reading Calendar connection health.' }
    : google.state === 'failed'
      ? { tone: 'failed' as const, label: 'Unavailable', detail: 'Google status could not be read. Calendar access is not being assumed.' }
      : google.value.connected && google.value.healthy && google.value.calendar_accessible
        ? { tone: 'ready' as const, label: google.value.managed ? 'Managed by Ryozan' : 'Calendar ready', detail: google.value.calendar_summary ? `${google.value.calendar_summary} is available for booking conflict checks.` : 'Calendar access is healthy for booking conflict checks.' }
        : google.value.connected
          ? { tone: 'incomplete' as const, label: 'Reconnect needed', detail: google.value.reason || 'Google is linked, but Calendar access needs attention.' }
          : { tone: 'disconnected' as const, label: 'Not connected', detail: 'Connect Google Calendar for availability and booking conflict checks.' }

  const googleSigninView = googleSignin.state === 'loading'
    ? { tone: 'loading' as const, label: 'Checking', detail: 'Reading your Google sign-in link.' }
    : googleSignin.state === 'failed'
      ? { tone: 'failed' as const, label: 'Unavailable', detail: 'Google sign-in status could not be read.' }
      : googleSigninReturn === 'error'
        ? { tone: 'incomplete' as const, label: 'Link failed', detail: 'Google did not confirm that account. Try linking again.' }
        : googleSignin.value.linked
          ? { tone: 'ready' as const, label: 'Linked', detail: 'Sign in with Google opens this account.' }
          : { tone: 'disconnected' as const, label: 'Not linked', detail: 'Link your Google account so Sign in with Google opens this portal.' }

  const pinterestView = pinterest.state === 'loading'
    ? { tone: 'loading' as const, label: 'Checking', detail: 'Reading your Pinterest connection.' }
    : pinterest.state === 'failed'
      ? { tone: 'failed' as const, label: 'Unavailable', detail: 'Pinterest status could not be read. No board access is being assumed.' }
      : pinterest.value.connected
        ? { tone: 'ready' as const, label: pinterest.value.username ? `@${pinterest.value.username}` : 'Connected', detail: 'Your boards are available for booking references.' }
        : { tone: 'disconnected' as const, label: 'Not connected', detail: 'Connect Pinterest to bring your own boards into reference workflows.' }

  return (
    <section className="bttco-hub-workflow-card bttco-artist-connections" aria-label="Artist Connections">
      <header>
        <span>Shishin · Your services</span>
        <strong>Connections</strong>
        <p>Live services tied to your artist work. A service appears here once the studio has configured it.</p>
      </header>

      {stripeReturn === 'refresh' ? (
        <div className="bttco-artist-connections__notice is-warning" role="status">
          That Stripe setup link expired. Opening a fresh secure link now.
        </div>
      ) : stripeReturn === 'return' ? (
        <div className="bttco-artist-connections__notice is-success" role="status">
          Welcome back from Stripe. The readiness state below was refreshed from your account.
        </div>
      ) : null}

      {actionError ? <div className="bttco-artist-connections__notice is-error" role="alert">{actionError}</div> : null}

      <div className="bttco-artist-connections__toolbar">
        <span>{allLoading ? 'Checking your services' : 'Personal artist connections'}</span>
        <button type="button" disabled={allLoading || Boolean(acting)} onClick={() => void loadStatuses()}>Refresh</button>
      </div>

      {!allLoading && ![stripeView, googleView, googleSigninView, pinterestView, emailView].some(view => isShownConnection(view.tone)) && (
        <p role="status">No external services are configured for this studio yet. Booking, Courier and the agents work without them.</p>
      )}
      <div className="bttco-artist-connections__list">
        {isShownConnection(stripeView.tone) && (
          <ConnectionCard
            name="Stripe"
            mark="S"
            tone={stripeView.tone}
            status={stripeView.label}
            detail={stripeView.detail}
            action={stripe.state === 'failed'
              ? { label: 'Retry status', onClick: () => void loadStatuses(), disabled: Boolean(acting) }
              : stripe.state === 'ready' && stripe.value.provider_state === 'unavailable'
                ? { label: 'Retry status', onClick: () => void loadStatuses(), disabled: Boolean(acting) }
              : stripe.state === 'ready' && stripe.value.routing_ready && stripe.value.charges_enabled && stripe.value.payouts_enabled
                ? { label: 'Refresh status', onClick: () => void loadStatuses(), disabled: Boolean(acting) }
                : stripe.state === 'ready'
                  ? { label: acting === 'stripe' ? 'Opening Stripe...' : stripe.value.connected ? 'Continue setup' : 'Start setup', onClick: () => void startStripeOnboarding(), disabled: Boolean(acting) }
                  : { label: 'Checking...', onClick: () => undefined, disabled: true }}
          >
            {stripe.state === 'ready' && stripe.value.connected ? (
              <div className="bttco-artist-connection__checks" aria-label="Stripe capability readiness">
                <span className={stripe.value.charges_enabled ? 'is-ready' : undefined}>Payments {stripe.value.charges_enabled ? 'ready' : 'needed'}</span>
                <span className={stripe.value.payouts_enabled ? 'is-ready' : undefined}>Payouts {stripe.value.payouts_enabled ? 'ready' : 'needed'}</span>
              </div>
            ) : null}
          </ConnectionCard>
        )}

        {isShownConnection(googleView.tone) && (
          <ConnectionCard
            name="Google Calendar"
            mark="G"
            tone={googleView.tone}
            status={googleView.label}
            detail={googleView.detail}
            action={google.state === 'failed'
              ? { label: 'Retry status', onClick: () => void loadStatuses(), disabled: Boolean(acting) }
              : google.state === 'ready' && google.value.managed
                ? undefined
                : google.state === 'ready' && google.value.connected && google.value.healthy && google.value.calendar_accessible
                  ? { label: acting === 'google' ? 'Disconnecting...' : 'Disconnect', onClick: () => void disconnectGoogle(), disabled: Boolean(acting), danger: true }
                  : google.state === 'ready'
                    ? { label: acting === 'google' ? 'Opening Google...' : google.value.connected ? 'Reconnect Google' : 'Connect Google', onClick: () => void connectGoogle(), disabled: Boolean(acting) }
                    : { label: 'Checking...', onClick: () => undefined, disabled: true }}
          />
        )}

        {isShownConnection(googleSigninView.tone) && (
          <ConnectionCard
            name="Google sign-in"
            mark="G"
            tone={googleSigninView.tone}
            status={googleSigninView.label}
            detail={googleSigninView.detail}
            action={googleSignin.state === 'failed'
              ? { label: 'Retry status', onClick: () => void loadStatuses(), disabled: Boolean(acting) }
              : googleSignin.state === 'ready'
                ? { label: acting === 'google-signin' ? 'Opening Google...' : googleSignin.value.linked ? 'Relink Google' : 'Link Google', onClick: () => void linkGoogleSignin(), disabled: Boolean(acting) }
                : { label: 'Checking...', onClick: () => undefined, disabled: true }}
          />
        )}

        {isShownConnection(pinterestView.tone) && (
          <ConnectionCard
            name="Pinterest"
            mark="P"
            tone={pinterestView.tone}
            status={pinterestView.label}
            detail={pinterestView.detail}
            action={pinterest.state === 'failed'
              ? { label: 'Retry status', onClick: () => void loadStatuses(), disabled: Boolean(acting) }
              : pinterest.state === 'ready' && pinterest.value.connected
                ? { label: acting === 'pinterest' ? 'Disconnecting...' : 'Disconnect', onClick: () => void disconnectPinterest(), disabled: Boolean(acting), danger: true }
                : pinterest.state === 'ready'
                  ? { label: acting === 'pinterest' ? 'Opening Pinterest...' : 'Connect Pinterest', onClick: () => void connectPinterest(), disabled: Boolean(acting) }
                  : { label: 'Checking...', onClick: () => undefined, disabled: true }}
          />
        )}

        {isShownConnection(emailView.tone) && (
          <ConnectionCard
            name="Ryozan Email"
            mark="@"
            tone={emailView.tone}
            status={emailView.label}
            detail={emailView.detail}
            action={email.state === 'failed'
              ? { label: 'Retry status', onClick: () => void loadStatuses(), disabled: Boolean(acting) }
              : email.state === 'loading'
                ? { label: 'Checking...', onClick: () => undefined, disabled: true }
                : { label: emailExpanded ? 'Close email setup' : email.value.verified ? 'Manage address' : email.value.connected ? 'Finish verification' : 'Claim address', onClick: () => {
                    if (emailExpanded) void loadStatuses()
                    setEmailExpanded((expanded) => !expanded)
                  } }}
          >
            {emailExpanded ? (
              <div className="bttco-artist-connection__email" data-testid="artist-email-connection">
                <EmailConnectQuest mode="full" />
              </div>
            ) : null}
          </ConnectionCard>
        )}
      </div>
    </section>
  )
}
