/**
 * ApiStatusBanner - shows a persistent banner when the API is unreachable.
 *
 * Polls /health on mount + every 30s. When the API comes back, the banner
 * auto-dismisses. This prevents the "Internal Server Error" confusion;
 * users see a clear "we're having trouble connecting" message instead.
 */

import { useState, useEffect, useCallback } from 'react'
import { checkApiHealth, type HealthStatus } from '../lib/api'

const isDev = import.meta.env.DEV

export default function ApiStatusBanner() {
  const [status, setStatus] = useState<HealthStatus | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [wasEverReachable, setWasEverReachable] = useState(false)

  const poll = useCallback(async () => {
    const result = await checkApiHealth()
    if (result.ok) setWasEverReachable(true)
    setStatus(result)
    if (result.ok) setDismissed(false)
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    poll()
    const interval = setInterval(poll, 30_000)
    return () => clearInterval(interval)
  }, [poll])

  if (!status || status.ok || dismissed) return null

  if (isDev && !wasEverReachable) return null

  return (
    <aside className="bttco-api-status" role="status" aria-live="polite">
      <span className="bttco-api-status__mark" aria-hidden="true">!</span>
      <span className="bttco-api-status__copy">
        {status.db === 'unreachable'
          ? 'Unable to reach the server. Some features may not work.'
          : 'Server is experiencing issues. We\'re working on it.'}
      </span>
      <button
        type="button"
        className="bttco-api-status__action"
        onClick={() => setDismissed(true)}
      >
        Dismiss
      </button>
      <button
        type="button"
        className="bttco-api-status__action"
        onClick={poll}
      >
        Retry
      </button>
    </aside>
  )
}
