// The public booking flow (/book, /book/<artist>, /book/sesh, /book/confirm, /ask),
// rendered to HTML in node with synthetic data only. Same harness as the studio
// site test: esbuild bundles the views and helpers, nothing contacts a server.
import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const here = fileURLToPath(new URL('.', import.meta.url))
const entry = `
  import { createElement as h } from 'react'
  import { renderToStaticMarkup } from 'react-dom/server'
  import { MemoryRouter } from 'react-router-dom'
  import { HelmetProvider } from 'react-helmet-async'
  import { ArtistStepView, CheckoutReturnView, SeshStepView, SubmittedView } from './PublicBookingPage'
  import { normalizeStudioSettings } from '../lib/studio-settings'
  export * as flow from '../lib/public-booking'
  const page = (element, path) => renderToStaticMarkup(
    h(HelmetProvider, { context: {} }, h(MemoryRouter, { initialEntries: [path] }, element)))
  export const settingsFrom = normalizeStudioSettings
  export const artistStep = (props, path = '/book') => page(h(ArtistStepView, { onSelect() {}, onContinue() {}, missingSlug: null, ...props }), path)
  export const checkoutReturn = (props) => page(h(CheckoutReturnView, props), '/book/confirm')
  export const submittedView = (props) => page(h(SubmittedView, props), '/book/confirm')
  export const seshStep = (props) => page(h(SeshStepView, { problem: null, onChange() {}, onContinue() {}, ...props }), '/book/sesh')
`
// @ryozan/ui ships TypeScript source, so it is bundled; every other package stays external.
const bundleRyozanUi = {
  name: 'bundle-ryozan-ui',
  setup(pluginBuild) {
    pluginBuild.onResolve({ filter: /^[^./]/ }, (args) => (args.path.startsWith('@ryozan/ui') ? undefined : { path: args.path, external: true }))
  },
}
const bundled = await build({
  stdin: { contents: entry, resolveDir: here, loader: 'jsx' },
  bundle: true,
  write: false,
  plugins: [bundleRyozanUi],
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  loader: { '.css': 'empty' },
  define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"production"' },
  logLevel: 'silent',
})
const outDir = new URL('../../node_modules/.cache/public-booking-test/', import.meta.url)
mkdirSync(outDir, { recursive: true })
const outFile = new URL('bundle.mjs', outDir)
writeFileSync(outFile, bundled.outputFiles[0].text)
const site = await import(outFile.href)
const { flow } = site

const LEGACY = /BTTCO|Blind Tiger|\bJustin\b|\bKathryn\b|Greenville|ryozan-studio\.co/i
const settings = site.settingsFrom({ studio_name: 'Example Studio', site: { mode: 'studio' } })
const roster = [
  { id: '1', slug: 'test-artist', name: 'Test Artist', specialty: ['fine line'], avatar_url: null, availability_source: 'none', booking_mode: null },
]
const text = html => html.replace(/<[^>]+>/g, ' ')

test('the artist slug survives the URL and step routes are not artists', () => {
  assert.equal(flow.artistSlugFromPath('/book/test-artist'), 'test-artist')
  assert.equal(flow.artistSlugFromPath('/book/test-artist/'), 'test-artist')
  assert.equal(flow.artistSlugFromPath('/book/sesh'), null)
  assert.equal(flow.artistSlugFromPath('/book/confirm'), null)
  assert.equal(flow.artistSlugFromPath('/book'), null)
  assert.equal(flow.artistBookingPath('test artist'), '/book/test%20artist')
})

test('the draft survives storage failures and keeps three slot rows', () => {
  const broken = { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') } }
  const draft = flow.readDraft(broken)
  assert.equal(draft.slots.length, 3)
  flow.writeDraft(broken, draft)
  const store = new Map()
  const memory = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) }
  flow.writeDraft(memory, { ...draft, artistSlug: 'test-artist', slots: [{ date: '2030-01-02', time: '13:00' }] })
  const back = flow.readDraft(memory)
  assert.equal(back.artistSlug, 'test-artist')
  assert.deepEqual(back.slots[0], { date: '2030-01-02', time: '13:00' })
  assert.equal(back.slots.length, 3)
})

test('the booking payload proposes distinct times and no hold or deposit', () => {
  const draft = {
    ...flow.emptyDraft(),
    artistSlug: 'test-artist',
    description: 'A small synthetic line drawing.',
    placement: 'forearm',
    slots: [{ date: '2030-01-02', time: '13:00' }, { date: '2030-01-02', time: '13:00' }, { date: '', time: '' }],
    firstName: 'Sam', lastName: 'Example', email: 'sam@example.test', phone: '', smsOptIn: true,
  }
  assert.equal(flow.seshProblem(draft), null)
  assert.equal(flow.contactProblem(draft), null)
  const payload = flow.bookingPayload(draft)
  assert.deepEqual(payload.preferred_slots, [{ date: '2030-01-02', time: '13:00' }])
  assert.equal(payload.artist_slug, 'test-artist')
  assert.equal(payload.sms_opt_in, false, 'no SMS consent without a phone number')
  assert.equal(payload.hold_token, undefined)
  assert.equal(payload.preferred_date, undefined)
  assert.match(flow.seshProblem({ ...draft, slots: [] }), /at least one/)
  assert.match(flow.seshProblem({ ...draft, description: 'short' }), /at least 10/)
  assert.match(flow.contactProblem({ ...draft, email: 'nope' }), /email/)
})

test('the Stripe return is read from the query, and a session id must look like one', () => {
  assert.deepEqual(flow.checkoutReturn('?checkout=success&ref=BKG-TEST1&session_id=cs_test_fake'), { outcome: 'success', reference: 'BKG-TEST1', sessionId: 'cs_test_fake' })
  assert.deepEqual(flow.checkoutReturn('?session_id=cs_test_fake'), { outcome: 'success', reference: '', sessionId: 'cs_test_fake' })
  assert.deepEqual(flow.checkoutReturn('?checkout=cancelled&ref=BKG-TEST1'), { outcome: 'cancelled', reference: 'BKG-TEST1', sessionId: null })
  assert.equal(flow.checkoutReturn('?session_id=../../x'), null)
  assert.equal(flow.checkoutReturn(''), null)
  assert.equal(flow.depositLine({ deposit_state: 'paid' }), 'Deposit received.')
  assert.match(flow.depositLine({ deposit_state: 'pending' }), /processing/)
})

test('the artist step lists the roster from the API and names the shop from settings', () => {
  const html = site.artistStep({ settings, roster: { status: 'ready', artists: roster }, selected: 'test-artist' }, '/book/test-artist')
  assert.doesNotMatch(html, LEGACY)
  assert.match(html, /<button type="button" aria-pressed="true"[^>]*>Test Artist<\/button>/)
  assert.doesNotMatch(html.slice(html.indexOf('<main>'), html.indexOf('</main>')), /<select|<option|<textarea|class="(btn|input-group|form-response)/, 'the step is built from @ryozan/ui, not site form markup')
  assert.match(text(html), /Example Studio/)
  assert.doesNotMatch(html, /No preference/, 'no "any artist" choice without a default artist')
  const withDefault = site.settingsFrom({ studio_name: 'Example Studio', default_artist_slug: 'test-artist' })
  assert.match(site.artistStep({ settings: withDefault, roster: { status: 'ready', artists: roster }, selected: '' }), /No preference/)
  const missing = site.artistStep({ settings, roster: { status: 'ready', artists: roster }, selected: '', missingSlug: 'gone' })
  assert.match(missing, /no artist at that booking link/)
})

test('the confirm page shows the server state, never the redirect alone', () => {
  const ret = { outcome: 'success', reference: 'BKG-TEST1', sessionId: 'cs_test_fake' }
  const loading = site.checkoutReturn({ settings, ret, load: { status: 'loading' } })
  assert.match(loading, /Checking your payment/)
  assert.doesNotMatch(loading, /Deposit received/)
  const ready = site.checkoutReturn({
    settings,
    ret,
    load: { status: 'ready', result: { reference_code: 'BKG-TEST1', artist_name: 'Test Artist', booking_status: 'confirmed', deposit_state: 'paid', deposit_amount: 50, scheduled_date: null, scheduled_time: null } },
  })
  assert.match(ready, /You are booked/)
  assert.match(ready, /Deposit received/)
  assert.match(ready, /BKG-TEST1/)
  assert.doesNotMatch(ready, LEGACY)
  const cancelled = site.checkoutReturn({ settings, ret: { outcome: 'cancelled', reference: 'BKG-TEST1', sessionId: null }, load: { status: 'missing' } })
  assert.match(cancelled, /No payment was taken/)
})

test('a second booking after a sent one starts a fresh form with a new idempotency id', () => {
  const filled = {
    ...flow.emptyDraft(),
    artistSlug: 'test-artist', artistName: 'Test Artist',
    description: 'A small synthetic line drawing.', placement: 'forearm', size: 'palm',
    slots: [{ date: '2030-01-02', time: '13:00' }, { date: '', time: '' }, { date: '', time: '' }],
    firstName: 'Sam', lastName: 'Example', email: 'sam@example.test', phone: '', smsOptIn: false,
  }
  const first = flow.bookingPayload(filled)
  const response = { reference_code: 'BKG-TEST1', artist_name: 'Test Artist', message: 'Synthetic request received.', checkout_url: null }

  // The send: the answer is kept for the confirmation, the sent draft is not.
  const sent = flow.completeRequest(response)
  assert.equal(sent.submitted, response)
  assert.notEqual(sent.draft.inquiryId, first.inquiry_id)
  assert.equal(sent.draft.description, '')
  assert.match(site.submittedView({ settings, submitted: sent.submitted }), /Request sent/)

  // Only the artist step starts the next request; history back to /book/confirm still shows the answer.
  assert.equal(flow.beginRequest(sent, 'confirm'), sent)
  assert.equal(flow.beginRequest(sent, 'sesh'), sent)
  const next = flow.beginRequest(sent, 'artist')
  assert.equal(next.submitted, null, 'the old success screen is gone')
  assert.equal(next.draft.artistSlug, '')
  assert.equal(next.draft.description, '')
  assert.deepEqual(next.draft.slots, flow.emptyDraft().slots)
  assert.equal(flow.beginRequest(next, 'artist'), next, 'nothing to reset without a sent request')

  // The fresh form: no artist chosen, empty fields.
  const artistHtml = site.artistStep({ settings, roster: { status: 'ready', artists: roster }, selected: next.draft.artistSlug })
  assert.doesNotMatch(artistHtml, /aria-pressed="true"/)
  assert.match(artistHtml, /disabled=""[^>]*>Continue/)
  const seshHtml = site.seshStep({ settings, draft: { ...next.draft, artistSlug: 'test-artist', artistName: 'Test Artist' } })
  assert.doesNotMatch(seshHtml, /synthetic line drawing|forearm|2030-01-02/i)

  // Walking the steps again sends a different idempotency id.
  const second = flow.bookingPayload({ ...filled, inquiryId: next.draft.inquiryId })
  assert.notEqual(second.inquiry_id, first.inquiry_id)
  assert.notEqual(second.inquiry_id, sent.draft.inquiryId)
})

test('a sent question hands the next request a new id and keeps the contact fields', () => {
  const draft = { ...flow.emptyDraft(), firstName: 'Sam', email: 'sam@example.test' }
  const after = flow.withFreshInquiryId(draft)
  assert.notEqual(after.inquiryId, draft.inquiryId)
  assert.equal(after.firstName, 'Sam')
  assert.equal(after.email, 'sam@example.test')
})
