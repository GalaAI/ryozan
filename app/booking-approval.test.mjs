// Render the shipped approval card with synthetic data and stubbed API
// clients, so these regressions can never approve a real booking or email a
// collector. Interaction and network paths are covered by the backend
// PostgreSQL suite; this locks what staff see first.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { createRequire } from 'node:module'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const nativeRequire = createRequire(import.meta.url)

function transpile(path, extra = {}) {
  const exports = {}
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, { exports, ...extra })
  return exports
}

function loadPanel() {
  const format = transpile('./src/lib/format.ts')
  const stub = (name) => ({ children, label, hint, ...rest }) => createElement('span', { 'data-ui': name, ...rest }, label, hint, children)
  const ui = {
    Button: ({ children, ...rest }) => createElement('button', rest, children),
    StatusText: stub('status'),
    TextField: ({ label, hint, ...rest }) => createElement('label', null, label, createElement('input', rest), hint),
  }
  const api = {
    bookings: { approvalDefaults: () => new Promise(() => {}), approve: () => new Promise(() => {}) },
    stripeConnect: { onboard: () => new Promise(() => {}) },
  }
  const panel = transpile('./src/components/courier/BookingApprovalPanel.tsx', {
    require: (name) => {
      if (name === 'react' || name === 'react/jsx-runtime') return nativeRequire(name)
      if (name === '@ryozan/ui') return ui
      if (name === '../../lib/api') return api
      if (name === '../../lib/format') return format
      return {}
    },
  })
  return { Panel: panel.default, format }
}

const pending = {
  id: 'synthetic-booking',
  status: 'pending',
  preferred_slots: [
    { date: '2026-11-03', time: '14:30' },
    { date: '2026-11-05', time: '11:00' },
    { date: '2026-11-07', time: '16:00' },
  ],
  preferred_date: null,
  preferred_time: null,
}

test('approval card lists every offered time, another time, and the message path', () => {
  const { Panel } = loadPanel()
  const markup = renderToStaticMarkup(createElement(Panel, { booking: pending, onMessage() {}, messageLabel: 'Message to coordinate a time' }))
  assert.match(markup, /Approve request/)
  assert.match(markup, /Tue, Nov 3, 2026 at 2:30 PM/)
  assert.match(markup, /Thu, Nov 5, 2026 at 11:00 AM/)
  assert.match(markup, /Sat, Nov 7, 2026 at 4:00 PM/)
  assert.match(markup, /Another time/)
  assert.match(markup, /Message to coordinate a time/)
  assert.doesNotMatch(markup, /Deposit/, 'deposit stays hidden until a time is chosen')
})

test('a time handed over from Huddl opens the confirm step with that slot selected', () => {
  const { Panel } = loadPanel()
  const markup = renderToStaticMarkup(createElement(Panel, { booking: pending, initialSlot: pending.preferred_slots[1] }))
  assert.match(markup, /aria-pressed="true"[^>]*><span>Thu, Nov 5, 2026 at 11:00 AM/)
  assert.match(markup, /Deposit \(\$\)/)
  assert.match(markup, /Loading preset/)
  assert.match(markup, /<button[^>]*disabled=""[^>]*>Approve and book<\/button>/, 'cannot send before the deposit preset loads')
})

test('a time agreed by message opens date and time fields', () => {
  const { Panel } = loadPanel()
  const markup = renderToStaticMarkup(createElement(Panel, { booking: pending, initialCustom: true }))
  assert.match(markup, /type="date"/)
  assert.match(markup, /type="time"/)
  assert.match(markup, /Choose a date and time/)
})

test('booked, cancelled and completed requests show no approval card', () => {
  const { Panel, format } = loadPanel()
  for (const status of ['confirmed', 'cancelled', 'completed']) {
    assert.equal(renderToStaticMarkup(createElement(Panel, { booking: { ...pending, status } })), '')
    assert.equal(format.isApprovableStatus(status), false)
  }
  assert.equal(format.isApprovableStatus('quoted'), true)
})

test('only shop managers and the booking artist see the approval card', () => {
  const { format } = loadPanel()
  for (const role of ['assistant', 'admin', 'founder']) assert.equal(format.canApproveBooking(role, 'u1', 'other'), true)
  assert.equal(format.canApproveBooking('artist', 'u1', 'u1'), true)
  assert.equal(format.canApproveBooking('artist', 'u1', 'other'), false)
  assert.equal(format.canApproveBooking('collector', 'u1', 'u1'), false)
  assert.equal(format.canApproveBooking('artist', null, null), false)
})
