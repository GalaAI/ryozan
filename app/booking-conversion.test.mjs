// The startup harness covers runtime ownership, not client booking receipts.
// Exercise the shipped confirmation controller with synthetic data so these
// regressions cannot create a real booking, payment, or client message.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { createRequire } from 'node:module'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// Extend the existing synthetic conversion harness with the staff intake
// projection. Render the actual shared Courier/Booky card with no API calls.
const nativeRequire = createRequire(import.meta.url)
function loadRequestedDateViews() {
  const pricing = {}
  runInNewContext(ts.transpileModule(readFileSync(new URL('./src/lib/format.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: pricing })
  const source = readFileSync(new URL('./src/pages/dashboard/CourierLayout.tsx', import.meta.url), 'utf8')
  const card = {}
  runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, { exports: card, require: name => {
    if (name === 'react' || name === 'react/jsx-runtime') return nativeRequire(name)
    if (name === '../../lib/format') return pricing
    if (name === '../../components/ui/StatusBadge') return { default: () => null }
    return {}
  } })
  const portal = readFileSync(new URL('./src/pages/dashboard/MobilePortalHome.tsx', import.meta.url), 'utf8')
  const tree = ts.createSourceFile('portal.tsx', portal, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const when = tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'formatWhen')
  const queue = {}
  runInNewContext(ts.transpileModule('export ' + when.getText(tree), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: queue, ...pricing, localDateKey: () => '2026-10-12', formatTime: pricing.formatTime12h })
  return { ...pricing, ...card, ...queue }
}

test('Courier and Booky show every requested option when only thread context is available', () => {
  const views = loadRequestedDateViews()
  const context = { status: 'pending', preferred_slots: [
    { date: '2026-11-03', time: '14:30' }, { date: '2026-11-05', time: '11:00' }, { date: '2026-11-07', time: '16:00' },
  ] }
  const markup = renderToStaticMarkup(createElement(views.CourierBookingInquiryCard, {
    booking: null, context, threadName: 'Synthetic', loading: false, onOpenImage() {},
  }))
  assert.match(markup, /Requested option 1: Tue, Nov 3, 2026 at 2:30 PM/)
  assert.match(markup, /Requested option 2: Thu, Nov 5, 2026 at 11:00 AM/)
  assert.match(markup, /Requested option 3: Sat, Nov 7, 2026 at 4:00 PM/)
  assert.doesNotMatch(markup, /Booked/)
  assert.match(views.formatWhen(context), /^Requested:.*Nov 3.*Nov 5.*Nov 7/)
})

test('legacy date and time remain visible alongside a separately booked appointment', () => {
  const views = loadRequestedDateViews()
  const context = { status: 'confirmed', preferred_date: '2026-11-03', preferred_time: '14:30', scheduled_date: '2026-11-06', scheduled_time: '12:00' }
  const markup = renderToStaticMarkup(createElement(views.CourierBookingInquiryCard, {
    booking: null, context, threadName: 'Synthetic', loading: false, onOpenImage() {},
  }))
  assert.match(markup, /Requested: Tue, Nov 3, 2026 at 2:30 PM/)
  assert.match(markup, /Booked Nov 6, 2026 at 12:00 PM/)
  assert.doesNotMatch(views.formatWhen(context), /Requested|Nov 3/)
  assert.equal(views.formatWhen({ preferred_date: '2026-11-03' }), 'Requested: Tue, Nov 3, 2026')
  assert.equal(views.formatWhen({}), 'Date pending')
})

test('full booking preferences take precedence over stale thread context', () => {
  const views = loadRequestedDateViews()
  const markup = renderToStaticMarkup(createElement(views.CourierBookingInquiryCard, {
    booking: { status: 'quoted', preferred_slots: [{ date: '2026-11-05', time: '11:00' }] },
    context: { preferred_slots: [{ date: '2026-11-03', time: '14:30' }] },
    threadName: 'Synthetic', loading: false, onOpenImage() {},
  }))
  assert.match(markup, /Requested: Thu, Nov 5, 2026 at 11:00 AM/)
  assert.doesNotMatch(markup, /Nov 3|Booked/)
})
