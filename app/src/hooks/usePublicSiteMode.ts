import type { PublicSiteMode, StudioSettings } from '../lib/api'
import { resolvePublicSiteMode } from '../lib/studio-settings'
import { useStudioSettings } from './useStudioSettings'

/**
 * Which public site this browser gets: the Ryozan product landing or the
 * studio's own website (PUBLIC_SITE_MODE, STUDIO_SITE_HOSTS). `loaded` is
 * false until GET /studio/settings settles; a failed read means "product".
 */
export function usePublicSiteMode(): { mode: PublicSiteMode; loaded: boolean; settings: StudioSettings } {
  const { settings, loaded } = useStudioSettings()
  const hostname = typeof window === 'undefined' ? '' : window.location.hostname
  return { mode: resolvePublicSiteMode(settings, hostname), loaded, settings }
}
