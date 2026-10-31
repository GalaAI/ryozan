import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

// Executes the Connections visibility rule that ArtistConnections renders from.
const source = await readFile(new URL('./integration-availability.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } })
const lib = {}
vm.runInNewContext(outputText, { exports: lib, require: () => ({}) })
const { integrationAvailability, isShownConnection, shouldReadStatus } = lib

const options = (...rows) => ({ integrations: rows.map(([id, configured, allowed]) => ({ id, configured, allowed })) })

test('a configured and allowed service shows while disconnected, failed or loading', () => {
  const availability = integrationAvailability(options(['pinterest', true, true], ['email_forwarding', true, true]))
  for (const tone of ['disconnected', 'failed', 'loading', 'incomplete', 'ready']) {
    assert.equal(isShownConnection(availability, 'pinterest', tone), true, tone)
  }
  assert.equal(shouldReadStatus(availability, 'email_forwarding'), true)
})

test('a service that is not configured, not allowed or not listed stays hidden and is never read', () => {
  const availability = integrationAvailability(options(
    ['google_calendar', false, true],
    ['stripe_connect', true, false],
    ['push', true, null],
  ))
  for (const id of ['google_calendar', 'stripe_connect', 'push', 'pinterest']) {
    assert.equal(isShownConnection(availability, id, 'ready'), false, id)
    assert.equal(shouldReadStatus(availability, id), false, id)
  }
})

test('a collector is not offered Stripe Connect, so its status endpoint is not called', () => {
  const availability = integrationAvailability(options(['stripe_connect', true, false], ['pinterest', true, true]))
  assert.equal(shouldReadStatus(availability, 'stripe_connect'), false)
  assert.equal(shouldReadStatus(availability, 'pinterest'), true)
})

test('an options failure falls back to reading every status and showing live connections only', () => {
  for (const failed of [null, undefined, {}, { integrations: 'nope' }]) {
    const availability = integrationAvailability(failed)
    assert.equal(availability.state, 'fallback')
    assert.equal(shouldReadStatus(availability, 'stripe_connect'), true)
    assert.equal(isShownConnection(availability, 'pinterest', 'ready'), true)
    assert.equal(isShownConnection(availability, 'pinterest', 'incomplete'), true)
    assert.equal(isShownConnection(availability, 'pinterest', 'disconnected'), false)
    assert.equal(isShownConnection(availability, 'pinterest', 'failed'), false)
  }
})

test('nothing shows and nothing is read while options are still loading', () => {
  const availability = { state: 'loading' }
  assert.equal(isShownConnection(availability, 'pinterest', 'ready'), false)
  assert.equal(shouldReadStatus(availability, 'pinterest'), false)
})

test('ArtistConnections reads options first and gates every status read and card on them', async () => {
  const component = await readFile(new URL('../components/ArtistConnections.tsx', import.meta.url), 'utf8')
  assert.match(component, /integrationAvailability\(await integrations\.options\(\)\.catch\(\(\) => null\)\)/)
  for (const [id, read] of [['stripe_connect', 'stripeConnect.status'], ['email_forwarding', 'emailConnect.status'], ['google_calendar', 'googleAuth.getStatus'], ['google_signin_link', 'googleAuth.getSigninLinkStatus'], ['pinterest', 'pinterestAuth.getStatus']]) {
    assert.ok(component.includes(`readOffered(offered, '${id}', ${read})`), read)
  }
  assert.doesNotMatch(component, /stripeConnect\.status\(\)|emailConnect\.status\(\)|pinterestAuth\.getStatus\(\)/)
  assert.match(component, /availability\.offered\.has\('push'\)/)
  assert.match(component, /<PushOptIn \/>/)
  assert.match(component, /This server has no third-party connections configured for your account\./)
  assert.doesNotMatch(component, /configured for this studio yet/)
})
