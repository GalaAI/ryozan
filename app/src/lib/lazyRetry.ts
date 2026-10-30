import { lazy } from 'react'

/**
 * Wraps a dynamic import with a single retry after 1s delay.
 * Prevents ErrorBoundary from triggering on transient chunk load failures.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyRetry<T extends React.ComponentType<any>>(
  factory: () => Promise<{ default: T }>
) {
  return lazy(() =>
    factory().catch(() =>
      new Promise<{ default: T }>((resolve) =>
        setTimeout(() => resolve(factory()), 1000)
      )
    )
  )
}
