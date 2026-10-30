import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// The desktop adapter tests cover domain routing, not this browser transport.
// Reuse their in-memory TypeScript test pattern to exercise body failures without live requests.
const source = (await readFile(new URL('./api.ts', import.meta.url), 'utf8'))
  .replaceAll('import.meta.env', '({})') + '\nexport { request };'
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
const { request, checkApiHealth, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
globalThis.localStorage = { getItem: () => null }

test('request handles header and body timeout consistently, without retrying writes', async t => {
  for (const phase of ['headers', 'success-body', 'error-body']) {
    let calls = 0
    t.mock.method(globalThis, 'fetch', async () => {
      calls++
      const fail = () => { throw new DOMException('signal is aborted without reason', 'TimeoutError') }
      if (phase === 'headers') fail()
      return { ok: phase === 'success-body', status: phase === 'success-body' ? 200 : 503, json: fail }
    })
    await assert.rejects(request('/test', { method: 'POST' }), err => {
      assert.ok(err instanceof ApiError)
      assert.equal(err.code, 'request_timeout')
      assert.match(err.message, /Check its status before submitting again/)
      assert.doesNotMatch(err.message, /signal is aborted/)
      return true
    })
    assert.equal(calls, 1)
  }
})

test('signal reason distinguishes body AbortError caused by timeout from cancellation', async t => {
  for (const [reason, code] of [[new DOMException('', 'TimeoutError'), 'request_timeout'], [undefined, 'request_cancelled']]) {
    const controller = new AbortController()
    t.mock.method(globalThis, 'fetch', async () => ({ ok: true, status: 200, json: async () => {
      controller.abort(reason)
      throw new DOMException('signal is aborted without reason', 'AbortError')
    } }))
    await assert.rejects(request('/test', { signal: controller.signal }), err => err.code === code)
  }
})

test('network, malformed response, structured server errors and no-content remain distinct', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch') })
  await assert.rejects(request('/test'), err => err.code === 'network_error')
  t.mock.method(globalThis, 'fetch', async () => new Response('not json'))
  await assert.rejects(request('/test'), err => err.code === 'invalid_response')
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: { code: 'denied', message: 'Access denied' } }, { status: 403 }))
  await assert.rejects(request('/test'), err => err.status === 403 && err.code === 'denied' && err.message === 'Access denied')
  t.mock.method(globalThis, 'fetch', async () => new Response('proxy error', { status: 502, statusText: 'Bad Gateway' }))
  await assert.rejects(request('/test'), err => err.status === 502 && err.message === 'Bad Gateway')
  t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 204 }))
  assert.equal(await request('/test'), undefined)
})

test('health failures never infer database outage from transport failures and recover on success', async t => {
  for (const [error, failure] of [[new DOMException('', 'TimeoutError'), 'timeout'], [new DOMException('', 'AbortError'), 'cancelled'], [new TypeError('Failed to fetch'), 'network'], [new SyntaxError('bad json'), 'invalid_response']]) {
    t.mock.method(globalThis, 'fetch', async () => ({ ok: true, json: async () => { throw error } }))
    assert.deepEqual(await checkApiHealth(), { ok: false, db: 'unknown', failure })
  }
  t.mock.method(globalThis, 'fetch', async () => new Response('proxy error', { status: 502 }))
  assert.equal((await checkApiHealth()).failure, 'server')
  t.mock.method(globalThis, 'fetch', async () => Response.json({ db: 'unreachable' }, { status: 503 }))
  assert.deepEqual(await checkApiHealth(), { ok: false, db: 'unreachable', version: undefined, failure: 'server' })
  t.mock.method(globalThis, 'fetch', async () => Response.json({ db: 'connected', version: 'test' }))
  assert.deepEqual(await checkApiHealth(), { ok: true, db: 'connected', version: 'test' })
})
