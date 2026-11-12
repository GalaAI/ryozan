// The studio website (PUBLIC_SITE_MODE=studio), rendered to HTML in node.
//
// esbuild (Vite's own bundler) bundles the page views and the settings helpers;
// React and the router load from node_modules. CSS imports are dropped and
// nothing contacts a server: the views take settings and artists as props.
import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const here = fileURLToPath(new URL('.', import.meta.url))
const entry = `
  import { createElement as h } from 'react'
  import { renderToStaticMarkup } from 'react-dom/server'
  import { MemoryRouter } from 'react-router-dom'
  import { HelmetProvider } from 'react-helmet-async'
  import { StudioHomeView } from './StudioHomePage'
  import { StudioStoryView } from './StudioStoryPage'
  import { StudioArtistView } from './StudioArtistPage'
  import { normalizeStudioSettings, resolvePublicSiteMode } from '../../lib/studio-settings'
  const page = (element, path = '/') => renderToStaticMarkup(
    h(HelmetProvider, { context: {} }, h(MemoryRouter, { initialEntries: [path] }, element)))
  export const settingsFrom = normalizeStudioSettings
  export { resolvePublicSiteMode }
  export const home = (settings, artists = []) => page(h(StudioHomeView, { settings, artists }))
  export const story = (settings) => page(h(StudioStoryView, { settings }), '/our-story')
  export const artist = (settings, load) => page(h(StudioArtistView, { settings, load }), '/artist/x')
`
const bundled = await build({
  stdin: { contents: entry, resolveDir: here, loader: 'jsx' },
  bundle: true,
  write: false,
  packages: 'external',
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  loader: { '.css': 'empty' },
  define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"production"' },
  logLevel: 'silent',
})
// Written under node_modules so the bundle's bare imports resolve from app/.
const outDir = new URL('../../../node_modules/.cache/studio-site-test/', import.meta.url)
mkdirSync(outDir, { recursive: true })
const outFile = new URL('bundle.mjs', outDir)
writeFileSync(outFile, bundled.outputFiles[0].text)
const site = await import(outFile.href)

// Names, places and domains of the studio this code was forked from. None may
// reach a page that renders from settings.
const LEGACY = /BTTCO|Blind Tiger|\bJustin\b|\bKathryn\b|Greenville|ryozan-studio\.co/i

const empty = site.settingsFrom({ studio_name: '', site: { mode: 'studio' } })
const configured = site.settingsFrom({
  studio_name: 'Lantern Tattoo',
  contact_email: 'hello@lantern.example.test',
  contact_phone: '+1 555 0100',
  address: '1 Example Way',
  site: {
    mode: 'studio',
    tagline: 'Fine line and color by appointment.',
    story_paragraphs: ['We opened in a small room.', 'Now there are three chairs.'],
    hours: ['Tue-Sat 11am-7pm'],
    hero_image_url: 'https://cdn.example.test/front.jpg',
    gallery_image_urls: ['https://cdn.example.test/a.jpg', '/media/b.jpg'],
    social_links: [{ label: 'Instagram', url: 'https://instagram.example.test/lantern' }],
  },
})
const roster = [
  { id: '1', slug: 'ana', name: 'Ana Example', specialty: ['fine line', 'color'], avatar_url: 'https://cdn.example.test/ana.jpg', availability_source: 'none', booking_mode: null },
  { id: '2', slug: '', name: 'No Slug', specialty: null, avatar_url: null, availability_source: 'none', booking_mode: null },
]
const text = html => html.replace(/<[^>]+>/g, ' ')

test('the mode switch picks the studio home only when the studio site is on', () => {
  assert.equal(site.resolvePublicSiteMode(site.settingsFrom({}), 'ryozan.app'), 'product')
  assert.equal(site.resolvePublicSiteMode(empty, 'ryozan.app'), 'studio')
  const hosts = site.settingsFrom({ site: { studio_hosts: ['lantern.example.test'] } })
  assert.equal(site.resolvePublicSiteMode(hosts, 'lantern.example.test'), 'studio')
  assert.equal(site.resolvePublicSiteMode(hosts, 'ryozan.app'), 'product')

  // App.tsx routes `/` through that switch, product landing first.
  const app = readFileSync(new URL('../../App.tsx', import.meta.url), 'utf8')
  assert.match(app, /<Route index element=\{<Suspended><PublicHome \/><\/Suspended>\} \/>/)
  assert.match(app, /<PublicSiteSwitch product=\{<AppLandingPage \/>\} studio=\{<StudioHomePage \/>\} \/>/)
  assert.match(app, /<PublicSiteSwitch product=\{<NotFoundPage \/>\} studio=\{children\} \/>/)
  assert.match(app, /path="our-story" element=\{<Suspended><StudioOnly><StudioStoryPage \/><\/StudioOnly><\/Suspended>\}/)
  assert.match(app, /path="artist\/:slug" element=\{<Suspended><StudioOnly><StudioArtistPage \/><\/StudioOnly><\/Suspended>\}/)
})

test('empty settings: neutral copy, no empty sections, no original-studio content', () => {
  const pages = [
    site.home(empty),
    site.story(empty),
    site.artist(empty, { status: 'missing' }),
    site.artist(empty, { status: 'loading' }),
  ]
  for (const html of pages) {
    assert.doesNotMatch(html, LEGACY)
    assert.doesNotMatch(html, /<img/, 'no image without an image setting')
    assert.doesNotMatch(html, /mailto:|tel:/)
    assert.match(html, /Tattoo studio/)
  }
  const home = pages[0]
  assert.match(home, /href="\/book"/)
  assert.match(home, /href="\/ask"/)
  for (const id of ['about', 'artists', 'gallery']) assert.doesNotMatch(home, new RegExp(`id="${id}"`), id)
  assert.doesNotMatch(home, /--studio-hero-image/)
  assert.doesNotMatch(home, />Our story</, 'no story link without a story')
  assert.match(pages[1], /has not published its story yet/)
  assert.match(pages[2], /Artist not found/)
})

test('configured settings and artists render as given', () => {
  const home = site.home(configured, roster)
  assert.doesNotMatch(home, LEGACY)
  assert.match(home, /<h1[^>]*>Lantern Tattoo<\/h1>/)
  assert.match(home, /Fine line and color by appointment\./)
  assert.match(home, /--studio-hero-image:url\(&quot;https:\/\/cdn\.example\.test\/front\.jpg&quot;\)/)
  assert.match(home, /We opened in a small room\./)
  assert.doesNotMatch(home, /Now there are three chairs/, 'the home page teases the first paragraph only')
  assert.match(home, /href="\/artist\/ana"/)
  assert.match(home, /src="https:\/\/cdn\.example\.test\/ana\.jpg"/)
  assert.match(home, /fine line · color/)
  assert.doesNotMatch(home, /No Slug/, 'an artist without a slug has no page to link')
  assert.match(home, /src="\/media\/b\.jpg"/)
  assert.match(home, /href="mailto:hello@lantern\.example\.test"/)
  assert.match(home, /href="tel:\+15550100"/)
  assert.match(text(home), /1 Example Way/)
  assert.match(text(home), /Tue-Sat 11am-7pm/)
  assert.match(home, /href="https:\/\/instagram\.example\.test\/lantern"/)

  const story = site.story(configured)
  assert.match(story, /We opened in a small room\./)
  assert.match(story, /Now there are three chairs\./)

  const artist = site.artist(configured, {
    status: 'ready',
    artist: {
      ...roster[0],
      title: 'Resident artist',
      bio: 'Ten years of fine line.\n\nBooks open monthly.',
      instagram_url: 'https://instagram.example.test/ana',
      website_url: 'javascript:alert(1)',
      portfolio_images: ['https://cdn.example.test/p1.jpg', 'https://cdn.example.test/p2.jpg'],
      portfolio_thumbs: { 'https://cdn.example.test/p1.jpg': 'https://cdn.example.test/p1-640.webp' },
    },
  })
  assert.doesNotMatch(artist, LEGACY)
  assert.match(artist, /<h1[^>]*>Ana Example<\/h1>/)
  assert.match(artist, /Resident artist/)
  assert.match(artist, /<p>Ten years of fine line\.<\/p><p>Books open monthly\.<\/p>/)
  assert.match(artist, /src="https:\/\/cdn\.example\.test\/p1-640\.webp"/)
  assert.match(artist, /src="https:\/\/cdn\.example\.test\/p2\.jpg"/)
  assert.match(artist, /href="https:\/\/instagram\.example\.test\/ana"/)
  assert.doesNotMatch(artist, /javascript:/)
})
