/**
 * One-time move of browser storage keys off the legacy `bttco` prefix.
 *
 * The app began as BTTCO and kept per-device state (quest progress, hotbar,
 * colour overrides, onboarding and welcome flags, collector club drafts) under
 * `bttco_*` and `bttco.*` keys. Those keys now read `ryozan_*` / `ryozan.*`.
 * For every legacy key: copy its value to the new key when the new key is
 * absent, then delete the legacy key. Running it twice is a no-op.
 *
 * The auth token already lives under `ryozan_token`. A leftover `bttco_token`
 * predates that rename (which had no migration, so a later logout never
 * cleared it); it is deleted, never copied, so a stale session from another
 * account cannot be revived on a shared device.
 *
 * Imported first in main.tsx so it lands before any module reads a key.
 */

const LEGACY_PREFIXES: ReadonlyArray<readonly [legacy: string, current: string]> = [
  ['bttco_', 'ryozan_'],
  ['bttco.', 'ryozan.'],
]

const DROP_WITHOUT_COPY = new Set(['bttco_token'])

export type KeyValueStorage = Pick<Storage, 'length' | 'key' | 'getItem' | 'setItem' | 'removeItem'>

/** Moves legacy keys in one storage area. Returns the new keys written. */
export function migrateLegacyStorageKeys(storage: KeyValueStorage): string[] {
  const legacyKeys: string[] = []
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index)
    if (key && LEGACY_PREFIXES.some(([legacy]) => key.startsWith(legacy))) legacyKeys.push(key)
  }

  const written: string[] = []
  for (const legacyKey of legacyKeys) {
    try {
      if (!DROP_WITHOUT_COPY.has(legacyKey)) {
        const [legacy, current] = LEGACY_PREFIXES.find(([prefix]) => legacyKey.startsWith(prefix))!
        const nextKey = current + legacyKey.slice(legacy.length)
        const value = storage.getItem(legacyKey)
        if (value !== null && storage.getItem(nextKey) === null) {
          storage.setItem(nextKey, value)
          written.push(nextKey)
        }
      }
      storage.removeItem(legacyKey)
    } catch {
      // Quota or a blocked storage area: keep the legacy key so a later boot can retry.
    }
  }
  return written
}

export function runLegacyStorageMigration(): void {
  if (typeof window === 'undefined') return
  for (const area of ['localStorage', 'sessionStorage'] as const) {
    try {
      migrateLegacyStorageKeys(window[area])
    } catch {
      // Storage disabled (private mode, sandboxed frame): nothing to migrate.
    }
  }
}

runLegacyStorageMigration()
