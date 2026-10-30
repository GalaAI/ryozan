// Test the shared outbound policy rather than a second copy of its route regex.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

// CI uses Node 20. Transpile the real policy without relying on Node 24's TS loader.
const source = readFileSync(new URL('./src/lib/analyticsPrivacy.ts', import.meta.url), 'utf8')
const exports = {}
runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports, URL })
const { publicAnalyticsUrl, beforePublicAnalytics, bookingOutcome } = exports

test('private and unknown routes are excluded, including short tokens', () => {
  for (const path of ['/form/short', '/quote/short', '/quote/short/confirmed', '/portal', '/dashboard/courier', '/reset-password', '/join', '/checkin', '/something-new']) {
    assert.equal(publicAnalyticsUrl('https://ryozan.app' + path + '?token=private'), null)
  }
  assert.equal(publicAnalyticsUrl('not a url'), null)
})

test('public pageviews and events lose all queries, fragments and dynamic identifiers', () => {
  const clean = beforePublicAnalytics({ type: 'event', url: 'https://ryozan.app/book/confirm?ref=PRIVATE&checkout=success#private' })
  assert.deepEqual(JSON.parse(JSON.stringify(clean)), { type: 'event', url: 'https://ryozan.app/book/confirm' })
  assert.equal(publicAnalyticsUrl('https://ryozan.app/artist/private-token'), 'https://ryozan.app/artist')
  assert.equal(publicAnalyticsUrl('https://ryozan.app/book/private-token'), 'https://ryozan.app/book')
})

test('review views, empty responses, and checkout starts are not confirmed bookings', () => {
  assert.equal(bookingOutcome({}), null)
  assert.equal(bookingOutcome({ reference_code: 'TEST', booking_status: 'confirmed', idempotent_acknowledgement: true }), null)
  assert.equal(bookingOutcome({ checkout: 'success' }), null)
  assert.equal(bookingOutcome({ reference_code: 'TEST', checkout_url: 'https://checkout.stripe.com/test' }), 'checkout_required')
  assert.equal(bookingOutcome({ reference_code: 'TEST', booking_status: 'pending' }), 'request_accepted')
  assert.equal(bookingOutcome({ reference_code: 'TEST', booking_status: 'confirmed', deposit_amount: 0 }), 'booking_confirmed')
  assert.equal(bookingOutcome({ kind: 'inquiry', booking_status: 'inquiry' }), 'inquiry_accepted')
})
