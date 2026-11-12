/**
 * Idle reset for shared check-in tablets.
 *
 * A client who walks away mid check-in must not leave their name, contact, health answers or ID on the
 * screen for the next person. After KIOSK_IDLE_RESET_MINUTES with no input, the kiosk clears its state
 * and returns to the start. Pure: no React and no DOM globals beyond the EventTarget it is handed, so the
 * timing is testable with fake timers.
 */

/** Minutes of no input before a shared tablet clears and returns to the kiosk start. */
export const KIOSK_IDLE_RESET_MINUTES = 2
export const KIOSK_IDLE_RESET_MS = KIOSK_IDLE_RESET_MINUTES * 60 * 1000

/** Any of these counts as someone still using the tablet. */
export const KIOSK_ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'input', 'touchstart', 'wheel'] as const

export interface IdleTimer {
  /** Activity seen: restart the countdown. */
  poke(): void
  /** Cancel the countdown for good. */
  stop(): void
}

/** One countdown that fires `onIdle` once, `timeoutMs` after the last poke. */
export function createIdleTimer(onIdle: () => void, timeoutMs: number = KIOSK_IDLE_RESET_MS): IdleTimer {
  let handle: ReturnType<typeof setTimeout> | null = null
  let stopped = false
  const clear = () => {
    if (handle !== null) clearTimeout(handle)
    handle = null
  }
  return {
    poke() {
      if (stopped) return
      clear()
      handle = setTimeout(() => {
        handle = null
        onIdle()
      }, timeoutMs)
    },
    stop() {
      stopped = true
      clear()
    },
  }
}

/**
 * Arm the countdown now and restart it on every activity event `target` sees. Returns the detach
 * function, which removes the listeners and cancels the countdown.
 */
export function attachIdleReset(target: EventTarget, onIdle: () => void, timeoutMs: number = KIOSK_IDLE_RESET_MS): () => void {
  const timer = createIdleTimer(onIdle, timeoutMs)
  const poke = () => timer.poke()
  for (const type of KIOSK_ACTIVITY_EVENTS) target.addEventListener(type, poke, { capture: true, passive: true })
  timer.poke()
  return () => {
    for (const type of KIOSK_ACTIVITY_EVENTS) target.removeEventListener(type, poke, { capture: true })
    timer.stop()
  }
}
