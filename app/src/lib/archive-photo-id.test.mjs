import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('./archive-photo-id.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
const { archivePhotoIdUrl } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

const SIGNED = 'https://account-id.r2.cloudflarestorage.com/example-bucket/waivers/pending-ids/x.jpg?X-Amz-Signature=abc'

test('new waivers store photo_id_url and the Vault shows it', () => {
  assert.equal(archivePhotoIdUrl({ photo_id_url: SIGNED }), SIGNED)
})

test('older waivers store photo_id and still show it', () => {
  assert.equal(archivePhotoIdUrl({ photo_id: SIGNED }), SIGNED)
})

test('photo_id_url wins when both are present', () => {
  assert.equal(archivePhotoIdUrl({ photo_id_url: SIGNED, photo_id: 'https://assets.example.test/old.jpg' }), SIGNED)
})

test('missing, empty or non-link values show nothing', () => {
  assert.equal(archivePhotoIdUrl(null), null)
  assert.equal(archivePhotoIdUrl({}), null)
  assert.equal(archivePhotoIdUrl({ photo_id_url: '  ' }), null)
  assert.equal(archivePhotoIdUrl({ photo_id_url: 'javascript:alert(1)' }), null)
  assert.equal(archivePhotoIdUrl({ photo_id: 42 }), null)
})
