#!/usr/bin/env node
/**
 * New artifact justification:
 * BTTCO had no way to notice that `packages/ui/src` and the vendored tarball
 * the app actually installs had drifted apart. The drift is silent by design
 * of the setup, so it needed its own guard rather than a change to an existing
 * script.
 *
 * ============================================================================
 * WHY THIS EXISTS: the @ryozan/ui vendoring contract
 * ============================================================================
 *
 * `app/` does NOT consume `packages/ui/` as a workspace. It installs a
 * committed tarball:
 *
 *     app/package.json   "@ryozan/ui": "file:vendor/ryozan-ui-<version>.tgz"
 *
 * Two consequences, both of which have already bitten this repo:
 *
 *   1. Editing `packages/ui/src/**` changes NOTHING in the app. The source
 *      tree and the shipped tarball are separate artifacts. Commit cbabfaf
 *      added `--brick-red` to `packages/ui/src/tokens/tokens.css` and never
 *      repacked, so the token never reached the app.
 *
 *   2. Repacking the tarball is ALSO not enough. `npm ci` restores the
 *      package from the npm cache keyed by the `integrity` hash recorded in
 *      `app/package-lock.json` -- not by the bytes on disk. Repack without
 *      touching the lockfile and a clean `npm ci` will silently reinstall the
 *      OLD component. Only after the `integrity` line changes does npm treat
 *      it as a cache miss and unpack the new tarball.
 *
 * ============================================================================
 * THE FULL PROCEDURE (all three parts, or the change does not ship)
 * ============================================================================
 *
 *   1. Edit the source:      packages/ui/src/**
 *   2. Repack the tarball:   cd packages/ui && npm run pack:app
 *   3. Relock the integrity: cd app && npm install --package-lock-only \
 *                              "@ryozan/ui@file:vendor/ryozan-ui-<version>.tgz"
 *
 * Step 3 needs the explicit `@ryozan/ui@file:...` argument. A bare
 * `npm install --package-lock-only` reports "up to date" and leaves the stale
 * integrity hash in place, which is exactly the state this check exists to
 * catch. Naming the spec forces npm to re-resolve the tarball and rewrite the
 * hash -- a one-line lockfile diff.
 *
 *   Commit all three: the source files, app/vendor/ryozan-ui-<version>.tgz,
 *   and app/package-lock.json. Commit ecb0ace is a correct example.
 *
 * Prose version of the same procedure: docs/release/UI_TARBALL_VENDORING.md
 *
 * ============================================================================
 * WHAT THIS SCRIPT CHECKS
 * ============================================================================
 *
 *   A. The `file:` specifier in app/package.json points at a tarball that
 *      exists, and its version matches packages/ui/package.json.
 *   B. The `integrity` recorded in app/package-lock.json equals the real
 *      sha512 of the tarball on disk. This is the guard for "repacked but
 *      forgot the lockfile", the failure mode npm ci hides.
 *   C. Every file npm would pack out of packages/ui (per its `files` field)
 *      is present in the tarball with matching content, and the tarball
 *      carries nothing extra. Markdown is exempt -- it is packed but inert,
 *      and failing a build over a doc typo would only teach people to ignore
 *      this check.
 *
 * Text files are compared with line endings normalised, because the repo runs
 * core.autocrlf=true on Windows and LF on Linux CI; a byte-exact comparison
 * would fail on one of the two platforms regardless of real drift. Binary
 * files (fonts) are compared byte for byte.
 *
 * Usage:  npm run check:ui-tarball   (from app/)
 *         node scripts/check-ui-tarball.mjs
 */

import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { gunzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')
const uiRoot = path.join(repoRoot, 'packages', 'ui')
const appRoot = path.join(repoRoot, 'app')

// npm never packs these regardless of the `files` field.
const NEVER_PACKED = new Set([
  '.git',
  '.gitignore',
  '.npmignore',
  '.npmrc',
  '.DS_Store',
  'node_modules',
  'package-lock.json',
  'npm-debug.log',
  'CVS',
  '.svn',
  '.hg',
])

/**
 * Markdown is packed (README, MAPPING, CONFLICTS, GETTING-IT-RUNNING) but it is
 * inert: nothing in the app imports it, so a stale copy inside the tarball
 * cannot change behaviour. Requiring a repack for a doc typo would train people
 * to ignore this check, so parity is enforced on the code and assets only.
 */
const isInertDoc = (relative) => relative.toLowerCase().endsWith('.md')

const failures = []

function fail(message) {
  failures.push(message)
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'))
}

function isBinary(buffer) {
  // git's own heuristic: a NUL byte in the leading block means binary.
  return buffer.subarray(0, 8000).includes(0)
}

function contentHash(buffer) {
  const normalised = isBinary(buffer)
    ? buffer
    : Buffer.from(buffer.toString('utf8').replace(/\r\n/g, '\n'), 'utf8')
  return createHash('sha256').update(normalised).digest('hex')
}

/** Read a POSIX/ustar tar stream into a Map of entry path -> file contents. */
function readTar(buffer) {
  const entries = new Map()
  let offset = 0

  while (offset + 512 <= buffer.length) {
    const header = buffer.subarray(offset, offset + 512)
    if (header.every((byte) => byte === 0)) break

    const readField = (start, length) =>
      header.subarray(start, start + length).toString('utf8').replace(/\0.*/, '')

    let name = readField(0, 100)
    const prefix = readField(345, 155)
    if (prefix) name = `${prefix}/${name}`

    const size = parseInt(readField(124, 12).trim() || '0', 8)
    const typeFlag = String.fromCharCode(header[156])
    const body = buffer.subarray(offset + 512, offset + 512 + size)

    // '0' and NUL are regular files; 'x'/'g' are pax metadata, 5 is a dir.
    if (typeFlag === '0' || typeFlag === '\0') entries.set(name, body)

    offset += 512 + Math.ceil(size / 512) * 512
  }

  return entries
}

/** Enumerate the files npm would pack out of packages/ui, as posix paths. */
function enumerateSource(pkg) {
  const wanted = new Set(['package.json', ...(pkg.files ?? [])])
  const found = new Map()

  const walk = (relative) => {
    const absolute = path.join(uiRoot, relative)
    if (!fs.existsSync(absolute)) {
      fail(`packages/ui/package.json "files" lists "${relative}", which does not exist on disk.`)
      return
    }
    if (fs.statSync(absolute).isDirectory()) {
      for (const entry of fs.readdirSync(absolute).sort()) {
        if (NEVER_PACKED.has(entry)) continue
        walk(`${relative}/${entry}`)
      }
      return
    }
    found.set(relative, fs.readFileSync(absolute))
  }

  for (const entry of [...wanted].sort()) {
    if (NEVER_PACKED.has(entry)) continue
    walk(entry)
  }

  return found
}

function remedy(tarballName) {
  return [
    '',
    'REMEDY -- all three steps, or the change does not reach the app:',
    '',
    '  1. Repack the tarball from source:',
    '       cd packages/ui && npm run pack:app',
    '',
    '  2. Refresh the integrity hash in the lockfile (the explicit spec is',
    '     required -- a bare --package-lock-only says "up to date" and does',
    '     nothing):',
    `       cd app && npm install --package-lock-only "@ryozan/ui@file:${tarballRelative}"`,
    '',
    `  3. Commit app/vendor/${tarballName} AND app/package-lock.json together`,
    '     with the packages/ui source change.',
    '',
    'Step 2 is the one people skip. `npm ci` restores @ryozan/ui from the npm',
    'cache keyed by the `integrity` hash in app/package-lock.json, not by the',
    'bytes of the tarball. Repack without relocking and a clean `npm ci` will',
    'silently reinstall the OLD component with no error.',
    '',
    'Background: docs/release/UI_TARBALL_VENDORING.md',
  ].join('\n')
}

// --- A. resolve the vendored tarball ---------------------------------------

const appPkg = readJson(path.join(appRoot, 'package.json'))
const uiPkg = readJson(path.join(uiRoot, 'package.json'))

const specifier = appPkg.dependencies?.['@ryozan/ui']
if (typeof specifier !== 'string' || !specifier.startsWith('file:')) {
  console.error(
    `app/package.json no longer declares @ryozan/ui as a file: dependency (found ${specifier}).\n` +
      'If the app moved to a workspace dependency, delete this check and its CI step ' +
      'instead of leaving it half-wired.',
  )
  process.exit(1)
}

const tarballRelative = specifier.slice('file:'.length)
const tarballPath = path.join(appRoot, tarballRelative)
const tarballName = path.basename(tarballRelative)

if (!fs.existsSync(tarballPath)) {
  console.error(
    `app/package.json depends on ${specifier} but app/${tarballRelative} does not exist.` +
      `\n${remedy(tarballName)}`,
  )
  process.exit(1)
}

const expectedName = `ryozan-ui-${uiPkg.version}.tgz`
if (tarballName !== expectedName) {
  fail(
    `Version mismatch: packages/ui/package.json is ${uiPkg.version}, so the vendored tarball ` +
      `should be ${expectedName}, but app/package.json installs ${tarballName}.`,
  )
}

// --- B. lockfile integrity must match the tarball on disk ------------------

const lockPath = path.join(appRoot, 'package-lock.json')
const lock = readJson(lockPath)
const lockEntry = lock.packages?.['node_modules/@ryozan/ui']
const tarballBytes = fs.readFileSync(tarballPath)
const actualIntegrity = `sha512-${createHash('sha512').update(tarballBytes).digest('base64')}`

if (!lockEntry) {
  fail('app/package-lock.json has no "node_modules/@ryozan/ui" entry.')
} else if (lockEntry.integrity !== actualIntegrity) {
  fail(
    'app/package-lock.json integrity does not match the tarball on disk.\n' +
      `  lockfile: ${lockEntry.integrity}\n` +
      `  tarball:  ${actualIntegrity}\n` +
      '  This is the silent one: npm ci will install the cached OLD package.',
  )
}

// --- C. source tree vs tarball contents ------------------------------------

const tarEntries = readTar(gunzipSync(tarballBytes))
const packed = new Map()
for (const [name, body] of tarEntries) {
  if (!name.startsWith('package/')) continue
  const relative = name.slice('package/'.length)
  if (isInertDoc(relative)) continue
  packed.set(relative, body)
}

const source = new Map()
for (const [relative, body] of enumerateSource(uiPkg)) {
  if (isInertDoc(relative)) continue
  source.set(relative, body)
}

const missing = []
const extra = []
const changed = []

for (const relative of [...new Set([...source.keys(), ...packed.keys()])].sort()) {
  const sourceBytes = source.get(relative)
  const packedBytes = packed.get(relative)
  if (!packedBytes) missing.push(relative)
  else if (!sourceBytes) extra.push(relative)
  else if (contentHash(sourceBytes) !== contentHash(packedBytes)) changed.push(relative)
}

if (changed.length) {
  fail(
    `${changed.length} file(s) differ between packages/ui and app/vendor/${tarballName}:\n` +
      changed.map((f) => `  changed   packages/ui/${f}`).join('\n'),
  )
}
if (missing.length) {
  fail(
    `${missing.length} file(s) exist in packages/ui but are absent from the tarball:\n` +
      missing.map((f) => `  missing   packages/ui/${f}`).join('\n'),
  )
}
if (extra.length) {
  fail(
    `${extra.length} file(s) are in the tarball but no longer in packages/ui:\n` +
      extra.map((f) => `  stale     ${f}`).join('\n'),
  )
}

// --- report ----------------------------------------------------------------

if (failures.length) {
  console.error(`@ryozan/ui source and app/vendor/${tarballName} have DIVERGED.\n`)
  console.error(failures.join('\n\n'))
  console.error(remedy(tarballName))
  process.exit(1)
}

console.log(
  `@ryozan/ui is in sync: ${source.size} packed file(s) (markdown excluded) match ` +
    `app/vendor/${tarballName}, and app/package-lock.json integrity matches the tarball.`,
)
