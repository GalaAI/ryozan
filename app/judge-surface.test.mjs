// Judge-surface contracts: the dev proxy reaches the local API, and the
// inherited shop pages are not routed on the product surface.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')

test('the dev proxy defaults to the local API on 18788 with no committed override', () => {
  assert.equal(existsSync(new URL('./.env.development', import.meta.url)), false)
  assert.match(read('./vite.config.ts'), /return 'http:\/\/127\.0\.0\.1:18788'/)
})

test('the inherited shop site is gone: one product surface, no shop pages or location data', () => {
  const app = read('./src/App.tsx')
  for (const page of ['HomePage', 'OurStoryPage', 'CrewPage', 'ArtistPage', 'LegalPage', 'ContactPage', 'ParkingPage', 'GiftCardsPage']) {
    assert.doesNotMatch(app, new RegExp(`\b${page}\b`), page)
    assert.equal(existsSync(new URL(`./src/pages/${page}.tsx`, import.meta.url)), false, page)
  }
  assert.doesNotMatch(app, /isAppProductSurface|productSurface/)
  assert.doesNotMatch(read('./src/pages/dashboard/HeraldsPage.tsx'), /Pendleton|ParkingMap|parking/i)
})

test('public static pages and roster cards carry no legacy shop identity', () => {
  for (const file of ['./public/privacy.html', './public/sms-terms.html', './public/sitemap.xml']) {
    assert.doesNotMatch(read(file), /BTTCO|Blind Tiger|Greenville|Pendleton/, file)
  }
  const cards = read('./src/lib/agent-card-hands.ts')
  const tools = [...cards.matchAll(/banditCard\('[a-z]+', (?:'[^']*', ){4}'([a-z_]+)'/g)].map(m => m[1])
  assert.ok(tools.length >= 18)
  const api = read('../api/app/api/v1/endpoints/gala_chat.py')
  for (const tool of new Set(tools)) assert.match(api, new RegExp(`"name": "${tool}"`), tool)
})

// Letters set one per span (a vertical stub) read as "B T T C O" in page text,
// which a plain word scan misses. Collapse runs of single spaced letters first.
export const collapseSpacedLetters = text => text.replace(/\b(?:[A-Za-z][\s ]+){2,}[A-Za-z]\b/g, run => run.replace(/[\s ]+/g, ''))
const LEGACY_IDENTITY = /BTTCO|Blind Tiger|Greenville|Pendleton/i

test('the spaced-letter scan catches a split legacy name', () => {
  assert.match(collapseSpacedLetters('Next up B T T C O\nB\nT\nT\nC\nO'), /BTTCO/)
  assert.doesNotMatch(collapseSpacedLetters('R Y O Z A N'), LEGACY_IDENTITY)
})

test('brand components spell the product name, never the legacy shop', () => {
  for (const file of ['SessionPass.tsx', 'Wordmark.tsx', 'ActionCard.tsx']) {
    const source = read(`../packages/ui/src/components/${file}`)
    assert.doesNotMatch(source, /['"`]BTTCO['"`]|Blind Tiger/, file)
  }
  // The card type line is visible on every card; it must not carry an em dash.
  assert.doesNotMatch(read('../packages/ui/src/components/ActionCard.tsx'), /\u2014/)
  assert.match(read('../packages/ui/src/components/SessionPass.tsx'), /brand = 'RYOZAN'/)
  assert.match(read('../packages/ui/src/components/Wordmark.tsx'), /text = 'RYOZAN'/)
  assert.match(read('./src/pages/dashboard/MobilePortalHome.tsx'), /<SessionPass \{\.\.\.pass\} brand=\{BRAND\.product\.toUpperCase\(\)\} \/>/)
})

test('a rate limit or network error never signs the judge out', () => {
  const store = read('./src/stores/auth.ts')
  const fetchMe = store.slice(store.indexOf('fetchMe: async'), store.indexOf('loginWithToken: async'))
  assert.match(fetchMe, /status === 401 \|\| status === 403/)
  assert.match(fetchMe, /else \{\s*set\(\{ isLoading: false \}\)/)
})

test('reduced motion skips the portal intermission loader', () => {
  const source = read('./src/components/PortalIntermission.tsx')
  assert.match(source, /prefers-reduced-motion: reduce/)
  assert.match(source, /if \(reduced\) onComplete\(\)/)
  assert.match(source, /if \(reduced\) return null/)
})

test('first paint is branded: fonts never block it and app routes show the seal', () => {
  const html = read('./index.html')
  for (const link of html.match(/<link[^>]+fonts\.googleapis\.com\/css2[^>]*>/g) ?? []) {
    if (!html.slice(html.indexOf(link) - 12, html.indexOf(link)).includes('<noscript>')) assert.match(link, /media="print" onload="this\.media='all'"/)
  }
  assert.match(html, /<div data-static-app-shell[^>]*><img src="\/assets\/brand\/ryozan-mark\.svg"/)
  assert.match(html, /html\[data-bttco-app-route\] \[data-static-app-shell\] \{ display: grid/)
})

test('mobile Connections opens for every signed-in role, collectors over You', () => {
  const mobile = read('./src/pages/dashboard/MobilePortalHome.tsx')
  assert.match(mobile, /const canUseArtistConnections = Boolean\(user\?\.id\)/)
  assert.doesNotMatch(mobile, /canUseArtistConnections = \['artist', 'founder'\]/)
  assert.match(mobile, /\{ id: 'connections', label: 'Connections', value: 'Open', onSelect: \(\) => \{ setAccountOpen\(false\); openArtistConnections\(\) \} \}/)
  assert.match(mobile, /const search = `\?portal=\$\{isCollector \? 'you' : 'hub'\}&workflow=connections`/)
  assert.match(mobile, /setActiveTab\(isCollector \? 'you' : 'hub'\)/)
  assert.match(mobile, /\(isCollector && activeTab === 'you'\)\) && agentChatMode === 'hub-workflow-full'/)
  // Deposits stay scoped to real artist accounts.
  assert.match(mobile, /const canViewArtistDeposits = role === 'artist' && user\?\.role === 'artist'/)
})

test('push opt-in is mounted from Connections, not only the unreachable Settings page', () => {
  assert.match(read('./src/components/ArtistConnections.tsx'), /import PushOptIn from '\.\/PushOptIn'/)
})
