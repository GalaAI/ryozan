import { useRef, useCallback, type TouchEvent } from 'react'

type SwipeDirection = 'left' | 'right'

interface UseSwipeDismissOptions {
  /** Which direction dismisses the drawer */
  direction: SwipeDirection
  /** Callback when swipe threshold is met */
  onDismiss: () => void
  /** Minimum px to trigger dismiss (default: 80) */
  threshold?: number
}

/**
 * Hook for swipe-to-dismiss on mobile drawers.
 * Returns onTouchStart and onTouchEnd handlers to spread onto the element.
 *
 * Usage:
 *   const swipe = useSwipeDismiss({ direction: 'right', onDismiss: closeDrawer })
 *   <div {...swipe}>...</div>
 */
export function useSwipeDismiss({ direction, onDismiss, threshold = 80 }: UseSwipeDismissOptions) {
  const startX = useRef<number | null>(null)
  const startY = useRef<number | null>(null)

  const onTouchStart = useCallback((e: TouchEvent) => {
    startX.current = e.touches[0].clientX
    startY.current = e.touches[0].clientY
  }, [])

  const onTouchEnd = useCallback((e: TouchEvent) => {
    if (startX.current === null || startY.current === null) return

    const dx = e.changedTouches[0].clientX - startX.current
    const dy = e.changedTouches[0].clientY - startY.current

    // Only trigger if horizontal movement dominates vertical (prevents scroll hijack)
    if (Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (direction === 'right' && dx > threshold) onDismiss()
      if (direction === 'left' && dx < -threshold) onDismiss()
    }

    startX.current = null
    startY.current = null
  }, [direction, onDismiss, threshold])

  return { onTouchStart, onTouchEnd }
}
