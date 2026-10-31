import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// Transpile only this module in memory. Under node there is no `window`, so the
// import-time migration is a no-op and each test drives a fake storage area.
const source = await readFile(new URL('./legacy-storage.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
const { migrateLegacyStorageKeys } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

function memoryStorage(entries = {}) {
  const map = new Map(Object.entries(entries))
  return {
    map,
    get length() { return map.size },
    key: (index) => [...map.keys()][index] ?? null,
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)) },
    removeItem: (key) => { map.delete(key) },
  }
}

test('moves underscore and dotted legacy keys to the ryozan prefix', () => {
  const storage = memoryStorage({
    bttco_hotbar: '["a"]',
    'bttco_quest_u1_daily_2026-10-31': 'true',
    'bttco.club.genesis.c9': '{"step":2}',
    'bttco.portal.welcome.v1:u1': 'seen',
    ryozan_token: 'jwt',
    unrelated: 'x',
  })
  const written = migrateLegacyStorageKeys(storage)
  assert.deepEqual(written.sort(), [
    'ryozan.club.genesis.c9',
    'ryozan.portal.welcome.v1:u1',
    'ryozan_hotbar',
    'ryozan_quest_u1_daily_2026-10-31',
  ])
  assert.deepEqual(Object.fromEntries(storage.map), {
    ryozan_token: 'jwt',
    unrelated: 'x',
    ryozan_hotbar: '["a"]',
    'ryozan_quest_u1_daily_2026-10-31': 'true',
    'ryozan.club.genesis.c9': '{"step":2}',
    'ryozan.portal.welcome.v1:u1': 'seen',
  })
})

test('an existing new key wins and the legacy key is still removed', () => {
  const storage = memoryStorage({ bttco_hotbar: 'old', ryozan_hotbar: 'new' })
  assert.deepEqual(migrateLegacyStorageKeys(storage), [])
  assert.deepEqual(Object.fromEntries(storage.map), { ryozan_hotbar: 'new' })
})

test('a leftover legacy auth token is dropped, never copied over the session key', () => {
  const absent = memoryStorage({ bttco_token: 'stale' })
  migrateLegacyStorageKeys(absent)
  assert.deepEqual(Object.fromEntries(absent.map), {})

  const present = memoryStorage({ bttco_token: 'stale', ryozan_token: 'live' })
  migrateLegacyStorageKeys(present)
  assert.deepEqual(Object.fromEntries(present.map), { ryozan_token: 'live' })
})

test('a failed write keeps the legacy key for the next boot', () => {
  const storage = memoryStorage({ bttco_asset_library: '[]' })
  storage.setItem = () => { throw new Error('QuotaExceededError') }
  assert.deepEqual(migrateLegacyStorageKeys(storage), [])
  assert.deepEqual(Object.fromEntries(storage.map), { bttco_asset_library: '[]' })
})

test('running twice is a no-op', () => {
  const storage = memoryStorage({ bttco_onboarding_dismissed: '1' })
  migrateLegacyStorageKeys(storage)
  assert.deepEqual(migrateLegacyStorageKeys(storage), [])
  assert.deepEqual(Object.fromEntries(storage.map), { ryozan_onboarding_dismissed: '1' })
})
