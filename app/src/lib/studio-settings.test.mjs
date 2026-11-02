import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// Transpile only this module in memory: it has type-only imports, so it loads
// without the browser API client and never contacts a server.
const source = await readFile(new URL('./studio-settings.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
const studio = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const {
  NEUTRAL_STUDIO_SETTINGS, normalizeStudioSettings, createStudioSettingsLoader, isClosedDay, isRequestOnlyDay,
  todayInStudioTimezone, timezoneLabel, sampleFlashUrl, resolveWaiverTemplateId, weekdayOf,
  NEUTRAL_STUDIO_SITE, resolvePublicSiteMode,
} = studio

const configured = normalizeStudioSettings({
  studio_name: 'Example Ink',
  owner_name: 'Sam Example',
  contact_email: 'shop@example.test',
  contact_phone: '+1 555 0100',
  address: '',
  timezone: 'America/Chicago',
  booking_days: { closed_weekdays: ['sun', 'mon'], request_only_weekdays: ['tue'], request_only_artist_slugs: ['sam'] },
  default_artist_slug: 'sam',
  waiver_template_id: '',
  sample_flash_urls: ['https://cdn.example.test/a.jpg', 'https://cdn.example.test/b.jpg'],
})

// 2026-11-01 is a Sunday; the 2nd a Monday, the 3rd a Tuesday.
const SUNDAY = '2026-11-01'
const TUESDAY = '2026-11-03'
const WEDNESDAY = '2026-11-04'

test('neutral settings hold no studio data', () => {
  for (const [key, value] of Object.entries(NEUTRAL_STUDIO_SETTINGS)) {
    if (key === 'booking_days' || key === 'site') continue
    assert.ok(value === '' || (Array.isArray(value) && value.length === 0), key)
  }
  assert.deepEqual(NEUTRAL_STUDIO_SETTINGS.booking_days, { closed_weekdays: [], request_only_weekdays: [], request_only_artist_slugs: [] })
  // A new install shows the product landing and has no studio website content.
  assert.equal(NEUTRAL_STUDIO_SETTINGS.site, NEUTRAL_STUDIO_SITE)
  assert.equal(NEUTRAL_STUDIO_SITE.mode, 'product')
  for (const [key, value] of Object.entries(NEUTRAL_STUDIO_SITE)) {
    if (key === 'mode') continue
    assert.ok(value === '' || (Array.isArray(value) && value.length === 0), key)
  }
})

test('malformed bodies normalize to neutral values', () => {
  assert.equal(normalizeStudioSettings(null), NEUTRAL_STUDIO_SETTINGS)
  const odd = normalizeStudioSettings({ owner_name: 7, booking_days: { closed_weekdays: ['SUN', 'funday', 3] }, sample_flash_urls: 'x' })
  assert.equal(odd.owner_name, '')
  assert.deepEqual(odd.booking_days.closed_weekdays, ['sun'])
  assert.deepEqual(odd.sample_flash_urls, [])
})

test('a new install closes no day and makes none request-only', () => {
  assert.equal(weekdayOf(SUNDAY), 'sun')
  for (const day of [SUNDAY, TUESDAY, WEDNESDAY]) {
    assert.equal(isClosedDay(NEUTRAL_STUDIO_SETTINGS, day), false)
    assert.equal(isRequestOnlyDay(NEUTRAL_STUDIO_SETTINGS, day, 'sam'), false)
  }
})

test('configured booking days apply to the listed artists', () => {
  assert.equal(isClosedDay(configured, SUNDAY), true)
  assert.equal(isClosedDay(configured, WEDNESDAY), false)
  assert.equal(isRequestOnlyDay(configured, TUESDAY, 'sam'), true)
  assert.equal(isRequestOnlyDay(configured, TUESDAY, 'SAM'), true)
  assert.equal(isRequestOnlyDay(configured, TUESDAY, 'other'), false)
  assert.equal(isRequestOnlyDay(configured, WEDNESDAY, 'sam'), false)
  assert.equal(isRequestOnlyDay(configured, TUESDAY, null), false)
})

test('request-only days with no artist list apply to every artist', () => {
  const everyone = normalizeStudioSettings({ booking_days: { request_only_weekdays: ['tue'] } })
  assert.equal(isRequestOnlyDay(everyone, TUESDAY, 'anyone'), true)
})

test('today follows the studio zone and falls back to the browser zone', () => {
  const lateEvening = new Date('2026-11-04T03:30:00Z') // 21:30 on the 3rd in Chicago
  assert.equal(todayInStudioTimezone(configured, lateEvening), TUESDAY)
  assert.match(todayInStudioTimezone(NEUTRAL_STUDIO_SETTINGS, lateEvening), /^\d{4}-\d{2}-\d{2}$/)
  assert.match(todayInStudioTimezone({ ...configured, timezone: 'Not/AZone' }, lateEvening), /^\d{4}-\d{2}-\d{2}$/)
  assert.equal(timezoneLabel(configured), 'CHICAGO')
  assert.equal(timezoneLabel(NEUTRAL_STUDIO_SETTINGS), '')
})

test('sample flash previews come only from settings', () => {
  assert.equal(sampleFlashUrl(NEUTRAL_STUDIO_SETTINGS, 0), null)
  assert.equal(sampleFlashUrl(configured, 0), 'https://cdn.example.test/a.jpg')
  assert.equal(sampleFlashUrl(configured, 3), 'https://cdn.example.test/b.jpg')
})

test('waiver template: configured id, else the named waiver, else the first active waiver', () => {
  const templates = [
    { id: 'inactive', name: 'Tattoo Consent Waiver', category: 'waiver', is_active: false },
    { id: 'other-waiver', name: 'Piercing Waiver', category: 'waiver', is_active: true },
    { id: 'named', name: 'Tattoo Consent Waiver', category: 'waiver', is_active: true },
    { id: 'form', name: 'Intake', category: 'intake', is_active: true },
  ]
  assert.equal(resolveWaiverTemplateId({ ...NEUTRAL_STUDIO_SETTINGS, waiver_template_id: 'set-id' }, templates), 'set-id')
  assert.equal(resolveWaiverTemplateId(NEUTRAL_STUDIO_SETTINGS, templates), 'named')
  assert.equal(resolveWaiverTemplateId(NEUTRAL_STUDIO_SETTINGS, templates.filter((t) => t.id !== 'named')), 'other-waiver')
  assert.equal(resolveWaiverTemplateId(NEUTRAL_STUDIO_SETTINGS, [templates[3]]), null)
})

test('email_domain is the install receiving domain, empty when handles are off', () => {
  assert.equal(NEUTRAL_STUDIO_SETTINGS.email_domain, '')
  assert.equal(configured.email_domain, '')
  assert.equal(normalizeStudioSettings({ email_domain: ' IN.Example.test ' }).email_domain, 'in.example.test')
  assert.equal(normalizeStudioSettings({ email_domain: 42 }).email_domain, '')
})

test('loader reads once, and a failed read is neutral and retried', async () => {
  let calls = 0
  const loader = createStudioSettingsLoader(async () => { calls += 1; return { owner_name: 'Sam' } })
  const [first, second] = await Promise.all([loader.load(), loader.load()])
  assert.equal(first.owner_name, 'Sam')
  assert.equal(second, first)
  await loader.load()
  assert.equal(calls, 1)
  assert.equal(loader.cached(), first)

  let attempts = 0
  const flaky = createStudioSettingsLoader(async () => {
    attempts += 1
    if (attempts === 1) throw new Error('offline')
    return { owner_name: 'Back' }
  })
  assert.equal(await flaky.load(), NEUTRAL_STUDIO_SETTINGS)
  assert.equal(flaky.cached(), null)
  assert.equal((await flaky.load()).owner_name, 'Back')
})

test('site settings normalize; unsafe or malformed values become neutral', () => {
  assert.deepEqual(normalizeStudioSettings({}).site, NEUTRAL_STUDIO_SITE)
  const site = normalizeStudioSettings({
    site: {
      mode: ' STUDIO ',
      studio_hosts: ['Ink.Example.test', 'https://bad.example.test', 3],
      tagline: '  Custom work. ',
      story_paragraphs: ['One.', '', 'Two.'],
      hours: ['Tue-Sat 11-7'],
      hero_image_url: 'javascript:alert(1)',
      gallery_image_urls: ['https://cdn.example.test/a.jpg', '//cdn.example.test/b.jpg', '/media/c.jpg', 'http://cdn.example.test/d.jpg', '/e.jpg")'],
      social_links: [{ label: 'Instagram', url: 'https://ig.example.test/ink' }, { label: '', url: 'https://x.example.test' }, { label: 'X', url: 'mailto:a@b' }, 'nope'],
    },
  }).site
  assert.deepEqual(site, {
    mode: 'studio',
    studio_hosts: ['ink.example.test'],
    tagline: 'Custom work.',
    story_paragraphs: ['One.', 'Two.'],
    hours: ['Tue-Sat 11-7'],
    hero_image_url: '',
    gallery_image_urls: ['https://cdn.example.test/a.jpg', '/media/c.jpg'],
    social_links: [{ label: 'Instagram', url: 'https://ig.example.test/ink' }],
  })
  assert.equal(normalizeStudioSettings({ site: { mode: 'shop' } }).site.mode, 'product')
})

test('the public site mode: product by default, studio by setting or listed host', () => {
  const product = normalizeStudioSettings({})
  assert.equal(resolvePublicSiteMode(NEUTRAL_STUDIO_SETTINGS, 'ryozan.app'), 'product')
  assert.equal(resolvePublicSiteMode(product, 'localhost'), 'product')

  const studioMode = normalizeStudioSettings({ site: { mode: 'studio' } })
  assert.equal(resolvePublicSiteMode(studioMode, 'anything.example.test'), 'studio')
  assert.equal(resolvePublicSiteMode(studioMode, ''), 'studio')

  const byHost = normalizeStudioSettings({ site: { mode: 'product', studio_hosts: ['ink.example.test'] } })
  assert.equal(resolvePublicSiteMode(byHost, 'ink.example.test'), 'studio')
  assert.equal(resolvePublicSiteMode(byHost, 'INK.example.test.'), 'studio')
  assert.equal(resolvePublicSiteMode(byHost, 'www.ink.example.test'), 'product')
  assert.equal(resolvePublicSiteMode(byHost, 'example.test'), 'product')
  assert.equal(resolvePublicSiteMode(byHost, ''), 'product')
})
