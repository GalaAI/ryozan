import { useEffect, useRef } from 'react'
import { attachIdleReset, KIOSK_IDLE_RESET_MS } from '../lib/kiosk-idle'

/**
 * Calls `onIdle` after `timeoutMs` (default KIOSK_IDLE_RESET_MS) with no input anywhere on the page.
 * Disarmed while `enabled` is false, so an untouched kiosk start screen does not keep resetting itself.
 */
export function useKioskIdleReset(
  onIdle: () => void,
  { enabled = true, timeoutMs = KIOSK_IDLE_RESET_MS }: { enabled?: boolean; timeoutMs?: number } = {},
) {
  const latest = useRef(onIdle)
  useEffect(() => {
    latest.current = onIdle
  }, [onIdle])

  useEffect(() => {
    if (!enabled) return
    return attachIdleReset(window, () => latest.current(), timeoutMs)
  }, [enabled, timeoutMs])
}
