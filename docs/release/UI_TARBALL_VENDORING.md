# Shipping a `packages/ui` change into the app

`app/` does not consume `packages/ui/` as a workspace. It installs a committed
tarball:

```jsonc
// app/package.json
"@bttco/ui": "file:vendor/bttco-ui-0.2.0.tgz"
```

The source tree and the shipped artifact are two separate things. **Editing
`packages/ui/src/**` changes nothing in the app until you repack and relock.**

## The procedure

Three steps. Skip any one of them and the change silently does not ship.

```bash
# 1. edit the source
#      packages/ui/src/...

# 2. repack the tarball
cd packages/ui
npm run pack:app                 # writes ../../app/vendor/bttco-ui-<version>.tgz

# 3. relock the integrity hash
cd ../../app
npm install --package-lock-only "@bttco/ui@file:vendor/bttco-ui-<version>.tgz"
```

Commit all three results together: the `packages/ui/src` change, the rebuilt
`app/vendor/bttco-ui-<version>.tgz`, and the one-line `app/package-lock.json`
diff.

## Why step 3 is not optional

This is the part that has cost real debugging time.

`npm ci` does not install `@bttco/ui` from the bytes sitting in `app/vendor/`.
It restores the package from the npm cache, keyed by the `integrity` hash
recorded in `app/package-lock.json`:

```jsonc
"node_modules/@bttco/ui": {
  "version": "0.2.0",
  "resolved": "file:vendor/bttco-ui-0.2.0.tgz",
  "integrity": "sha512-Zrs+6zCk93TuoKq+..."
}
```

Repack the tarball but leave that hash alone and the hash still matches a cache
entry: the *old* one. A completely clean `npm ci` then reinstalls the previous
component, with no warning, no error, and no visible difference in the install
log. Only once `integrity` changes does npm treat it as a cache miss and unpack
what you actually built.

Note the exact form of the command. A bare `npm install --package-lock-only`
prints `up to date` and leaves the stale hash in place. You have to name the
spec (`"@bttco/ui@file:vendor/bttco-ui-<version>.tgz"`) to force npm to
re-resolve the tarball.

## The guard

```bash
cd app
npm run check:ui-tarball
```

`scripts/check-ui-tarball.mjs` fails the build when the two artifacts have
diverged. It asserts three things:

| Check | Catches |
|---|---|
| The `file:` spec resolves to a tarball that exists, and its version matches `packages/ui/package.json` | A version bump that was not vendored |
| `app/package-lock.json` `integrity` equals the real sha512 of the tarball on disk | **Repacked but not relocked**, the silent one |
| Every file npm would pack out of `packages/ui` is present in the tarball with matching content, and the tarball carries nothing extra | Edited source that was never repacked |

Markdown is exempt from the parity comparison. It is packed but inert: nothing
imports it, so a stale copy inside the tarball cannot change behaviour, and
failing a build over a doc typo would only teach people to ignore the check.

Text files are compared with line endings normalised, because the repo runs
`core.autocrlf=true` on Windows checkouts and LF on Linux CI; a byte-exact
comparison would fail on one platform or the other regardless of real drift.
Fonts and other binaries are compared byte for byte.

The check runs in `.github/workflows/app.yml` **before** `npm ci`, so a stale
tarball is reported rather than quietly type-checked and built.
