import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test, { mock } from 'node:test'
import ts from 'typescript'

// Transpile each pure module in memory; neither imports the browser API client.
async function load(file) {
  const source = await readFile(new URL(file, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const idle = await load('./kiosk-idle.ts')
const checkin = await load('./checkin.ts')

test('the idle reset defaults to two minutes', () => {
  assert.equal(idle.KIOSK_IDLE_RESET_MINUTES, 2)
  assert.equal(idle.KIOSK_IDLE_RESET_MS, 120_000)
})

test('idle timer fires once after the timeout and activity restarts it', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const onIdle = mock.fn()
  const timer = idle.createIdleTimer(onIdle)
  timer.poke()
  t.mock.timers.tick(idle.KIOSK_IDLE_RESET_MS - 1)
  assert.equal(onIdle.mock.callCount(), 0)
  timer.poke()
  t.mock.timers.tick(idle.KIOSK_IDLE_RESET_MS - 1)
  assert.equal(onIdle.mock.callCount(), 0, 'a poke restarts the countdown')
  t.mock.timers.tick(1)
  assert.equal(onIdle.mock.callCount(), 1)
  t.mock.timers.tick(idle.KIOSK_IDLE_RESET_MS * 3)
  assert.equal(onIdle.mock.callCount(), 1, 'fires once, not on an interval')
  timer.stop()
})

test('a stopped timer never fires and ignores later pokes', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const onIdle = mock.fn()
  const timer = idle.createIdleTimer(onIdle, 1000)
  timer.poke()
  timer.stop()
  timer.poke()
  t.mock.timers.tick(5000)
  assert.equal(onIdle.mock.callCount(), 0)
})

test('attachIdleReset resets on idle, counts every activity event, and detaches cleanly', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const target = new EventTarget()
  const onIdle = mock.fn()
  const detach = idle.attachIdleReset(target, onIdle)

  for (const type of idle.KIOSK_ACTIVITY_EVENTS) {
    t.mock.timers.tick(idle.KIOSK_IDLE_RESET_MS - 1000)
    target.dispatchEvent(new Event(type))
  }
  assert.equal(onIdle.mock.callCount(), 0, 'continuous input keeps the session alive')

  t.mock.timers.tick(idle.KIOSK_IDLE_RESET_MS)
  assert.equal(onIdle.mock.callCount(), 1, 'two quiet minutes reset the kiosk')

  target.dispatchEvent(new Event('pointerdown'))
  detach()
  t.mock.timers.tick(idle.KIOSK_IDLE_RESET_MS * 2)
  target.dispatchEvent(new Event('keydown'))
  t.mock.timers.tick(idle.KIOSK_IDLE_RESET_MS * 2)
  assert.equal(onIdle.mock.callCount(), 1, 'detached: no timer, no listeners')
})

test('contact accepts an email or a ten digit number', () => {
  assert.ok(checkin.contactOk('client@example.test'))
  assert.ok(checkin.contactOk('(555) 010-0134'))
  assert.ok(checkin.contactOk('1 555 010 0134'))
  assert.ok(!checkin.contactOk('555 0134'))
  assert.ok(!checkin.contactOk('client@'))
})

test('adults only, measured on the day', () => {
  const now = new Date(2026, 10, 10) // 10 Nov 2026, local
  assert.ok(checkin.isAdult('2008-11-10', now))
  assert.ok(!checkin.isAdult('2008-11-11', now))
  assert.ok(!checkin.isAdult('1890-01-01', now))
  assert.ok(!checkin.isAdult('', now))
})

test('price and emergency contact parsing', () => {
  assert.equal(checkin.parsePrice('$1,250'), '1250')
  assert.equal(checkin.parsePrice('0'), null)
  assert.equal(checkin.parsePrice('abc'), null)
  assert.deepEqual(checkin.splitEmergency('Test Contact 555 010 0199'), ['Test Contact', '555 010 0199'])
  assert.deepEqual(checkin.splitEmergency('Test Contact'), ['Test Contact', ''])
})

const details = {
  fullName: 'Test Client', dob: '1990-01-01', phone: '555 010 0134', email: 'client@example.test',
  address: '1 Example Way, Example City 00000', emergencyName: 'Test Contact', emergencyPhone: '555 010 0199', emergencyAddress: '',
}
const visit = { artistSlug: 'test-artist', description: 'Small line flower', placement: 'Left forearm', price: '$250', date: '2026-11-10' }

test('validation names each missing field', () => {
  assert.deepEqual(checkin.detailsErrors(details, new Date(2026, 10, 10)), {})
  assert.deepEqual(checkin.visitErrors(visit), {})
  const errs = checkin.visitErrors({ ...visit, artistSlug: '', price: '0' })
  assert.deepEqual(Object.keys(errs).sort(), ['artist', 'price'])
})

test('health: every question, unless the client keeps the answers on file', () => {
  const fresh = { answers: { intoxicated: 'No' }, sameOffered: false, allergyDetail: '', medications: '' }
  assert.deepEqual(checkin.healthMissing(fresh), checkin.HEALTH_QUESTIONS.map((q) => q.key))
  const same = { answers: { intoxicated: 'No', same: 'Yes' }, sameOffered: true, allergyDetail: '', medications: '' }
  assert.deepEqual(checkin.healthMissing(same), [])
  const allergic = { ...fresh, answers: { intoxicated: 'No', blood_thinners: 'No', allergy: 'Yes', pregnant: 'No', blood_disorder: 'No', skin_condition: 'No' } }
  assert.deepEqual(checkin.healthMissing(allergic), ['allergyDetail'])
  assert.ok(checkin.healthNeedsReview(allergic))
})

test('the waiver payload carries what the server requires', () => {
  const health = {
    answers: { intoxicated: 'No', blood_thinners: 'No', allergy: 'No', pregnant: 'No', blood_disorder: 'No', skin_condition: 'No' },
    sameOffered: false, allergyDetail: '', medications: '',
  }
  const data = checkin.buildWaiverData({ details, visit, health, priorHealth: {}, photoIdUrl: 'https://files.example.test/id', reusePhotoId: false, reuseSignature: false })
  assert.equal(data.quoted_price, '250')
  assert.equal(data.intoxicated, 'No')
  assert.equal(data.emergency_contact, 'Test Contact 555 010 0199')
  assert.equal(data.photo_id_url, 'https://files.example.test/id')
  assert.equal(data.reuse_photo_id, undefined)
  for (const key of ['health_and_eligibility', 'risks_and_consent', 'accept_terms']) assert.equal(data[key], true)

  const reused = checkin.buildWaiverData({
    details, visit, priorHealth: { pregnant: 'No', allergies: 'Latex' },
    health: { answers: { intoxicated: 'No', same: 'Yes' }, sameOffered: true, allergyDetail: '', medications: '' },
    photoIdUrl: '', reusePhotoId: true, reuseSignature: true,
  })
  assert.equal(reused.reuse_photo_id, true)
  assert.equal(reused.reuse_signature, true)
  assert.equal(reused.photo_id_url, undefined)
  assert.equal(reused.allergies, 'Latex')
  assert.equal(reused.health_same_as_last_visit, true)
})

test('kiosk and gift card pages use @ryozan/ui status parts, never a local status component', async () => {
  for (const page of ['../pages/CheckinPage.tsx', '../pages/GiftCardsPage.tsx']) {
    const source = await readFile(new URL(page, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /function Note\b|<Note\b/, `${page} must not author its own status component`)
    for (const tag of source.match(/<StatusText\b[^>]*>/g) ?? []) {
      assert.match(tag, /\bwrap\b/, `${page}: a status sentence wraps inside its panel (${tag})`)
    }
  }
})
