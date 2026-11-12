import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// Transpile only this module in memory: its api import is type-only, so it never loads the browser client.
const source = await readFile(new URL('./gift-cards.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
const gift = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('presets stay inside the range the server accepts', () => {
  assert.deepEqual(gift.presetAmounts({ min_amount_cents: 2500, max_amount_cents: 100000 }), [5000, 10000, 15000, 25000])
  assert.deepEqual(gift.presetAmounts({ min_amount_cents: 10000, max_amount_cents: 20000 }), [10000, 15000])
})

test('dollar entry parses to cents', () => {
  assert.equal(gift.dollarsToCents('$1,250'), 125000)
  assert.equal(gift.dollarsToCents('25.5'), 2550)
  assert.equal(gift.dollarsToCents('ten'), null)
  assert.equal(gift.dollarsToCents(''), null)
})

test('checkout total matches the server rounding (payments.customer_price_cents)', () => {
  // (cents * (10000 + bps) + 5000) // 10000
  assert.equal(gift.chargedCents(10000, 400), 10400)
  assert.equal(gift.chargedCents(2513, 400), 2614)
  assert.equal(gift.chargedCents(2500, 0), 2500)
  assert.equal(gift.adjustmentLabel(400), '4%')
  assert.equal(gift.adjustmentLabel(250), '2.5%')
  assert.equal(gift.formatCents(123456), '$1,234.56')
})

test('confirmation polls until the card is delivered or voided', () => {
  const s = (status, delivered) => ({ status, delivered, face_value_cents: 5000, recipient_email_hint: 'r***@example.test' })
  assert.deepEqual(gift.confirmationPhase(null), { phase: 'confirming', keepPolling: true })
  assert.deepEqual(gift.confirmationPhase(s('pending', false)), { phase: 'confirming', keepPolling: true })
  assert.deepEqual(gift.confirmationPhase(s('active', false)), { phase: 'delivering', keepPolling: true })
  assert.deepEqual(gift.confirmationPhase(s('active', true)), { phase: 'delivered', keepPolling: false })
  assert.deepEqual(gift.confirmationPhase(s('voided', false)), { phase: 'not_created', keepPolling: false })
})
