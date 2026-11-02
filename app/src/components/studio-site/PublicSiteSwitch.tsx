import type { ReactNode } from 'react'
import { usePublicSiteMode } from '../../hooks/usePublicSiteMode'

/**
 * Renders `studio` when this install shows the studio website at `/`, else
 * `product`. Nothing renders until the settings read settles, so the product
 * landing never flashes before a studio site (or the other way round).
 */
export default function PublicSiteSwitch({ product, studio }: { product: ReactNode; studio: ReactNode }) {
  const { mode, loaded } = usePublicSiteMode()
  if (!loaded) return null
  return <>{mode === 'studio' ? studio : product}</>
}
