import { useEffect, useState } from 'react'
import { studio, type StudioSettings } from '../lib/api'
import { createStudioSettingsLoader, NEUTRAL_STUDIO_SETTINGS } from '../lib/studio-settings'

const loader = createStudioSettingsLoader(() => studio.getSettings())

/** Prefetch so the first component to mount usually finds the settings ready. */
export function loadStudioSettings(): Promise<StudioSettings> {
  return loader.load()
}

/** Pick up a save the owner just made, past the browser's five-minute cache. */
export function refreshStudioSettings(): Promise<void> {
  return loader.refresh(() => studio.getSettings({ cache: 'reload' }))
}

/** The studio's IANA zone for showing times; the browser's own until settings load. */
export function studioTimeZone(): string | undefined {
  return loader.cached()?.timezone || undefined
}

/**
 * The studio's public settings. `loaded` is false until the read settles;
 * until then, and if it fails, `settings` is neutral.
 */
export function useStudioSettings(): { settings: StudioSettings; loaded: boolean } {
  const [settings, setSettings] = useState<StudioSettings | null>(() => loader.cached())

  useEffect(() => {
    if (settings) return
    let active = true
    void loader.load().then((next) => { if (active) setSettings(next) })
    return () => { active = false }
  }, [settings])

  return { settings: settings ?? NEUTRAL_STUDIO_SETTINGS, loaded: settings !== null }
}
