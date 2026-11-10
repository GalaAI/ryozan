# Shop brand packs

A shop brand pack is a shop's own branded, standalone surfaces, packaged as versioned folders. Today Ryozan hosts one of them: the Email Foundry templates in the staff portal. A pack is shop content, not product UI. Product UI comes only from `@ryozan/ui`; a pack is published per install and never committed to this repository.

## Layout

A pack is one npm-style scope holding versioned packages:

```
@<scope>/
  email-foundry/1.0.0/
    Email Foundry.dc.html
    Email Foundry Mobile.dc.html
    adapter.js
    support.js
    assets/...
```

For example a shop whose scope is `exampleshop` publishes `@exampleshop/email-foundry/1.0.0/...`. The Email Foundry frame loads `email-foundry/1.0.0` from the configured scope; a pack without that package shows "Email Foundry is not set up for this studio".

## Configure an install

| Variable | What it does |
| --- | --- |
| `SHOP_PACK_BASE_URL` | The `https://` base the pack files live under, for example an R2 public prefix such as `https://assets.example.com/shops/example`. The file `<base>/@<scope>/email-foundry/1.0.0/adapter.js` must be reachable. No query or fragment. |
| `SHOP_PACK_SCOPE` | The pack's scope without the `@`, for example `exampleshop`. Lowercase letters, digits and hyphens. |

Set both or neither; one without the other stops the API at startup. With both blank the install has no pack: `/dependencies/...` answers 404 and Email Foundry reports that it is not set up. `GET /api/v1/studio/settings` returns the scope as `shop_pack_scope` (empty without a pack), which is how the web app finds it.

## How the pack is served

`GET /dependencies/@<scope>/<path>` streams the file from `SHOP_PACK_BASE_URL/@<scope>/<path>` (`api/app/services/shop_pack.py`). It is a same-origin proxy, not a redirect, because the hosts fetch the pack document and adapter and write them into a same-origin frame under `<base href="/dependencies/...">`: every font, script, JSON file and image the pack loads afterwards is relative to `/dependencies`. A redirect to the bucket would need CORS on the bucket and would break the API's Content-Security-Policy, which allows same-origin scripts and fonts only.

The proxy:

- serves only paths under `@<SHOP_PACK_SCOPE>/`, and 404s anything else: other scopes, `..`, `.`, empty or hidden segments, absolute paths and backslashes;
- serves only web file types (HTML, CSS, JS, JSON, images, fonts, video) and sets the Content-Type from the extension, never from the bucket; Markdown and other files are not served;
- answers 404 when the bucket does not hold the file (403 or 404), 502 when the bucket fails or is unreachable (5 second connect and 15 second read timeouts) and for files over 16 MB;
- passes `ETag` and `Last-Modified` through and forwards revalidation, with `Cache-Control: public, max-age=300` so a republished pack is picked up within five minutes;
- sandboxes SVG files so an SVG opened directly cannot run script on the app's origin.

The pack runs on the app's origin with the signed-in user's session, so publish only packs you trust, exactly as you would app code.

## Publish a pack

`scripts/publish_shop_pack.py` lists what it would upload and uploads only with `--apply`. Run it from the repo root with the API virtualenv:

```bash
# Dry run: prints every file's key, size and sha256, and the files it skips.
api/.venv/bin/python scripts/publish_shop_pack.py ~/packs --scope exampleshop --bucket studio-assets --prefix shops/example

# Upload. Credentials come from the environment, under the names the API uses.
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=...
api/.venv/bin/python scripts/publish_shop_pack.py ~/packs --scope exampleshop --bucket studio-assets --prefix shops/example --apply
```

`~/packs` is the folder that holds `@exampleshop/`. Files the proxy would not serve (for example `README.md`) are skipped. Then point the install at the bucket's public URL for that prefix:

```
SHOP_PACK_BASE_URL=https://assets.example.com/shops/example
SHOP_PACK_SCOPE=exampleshop
```

The app pins the package version it hosts (`email-foundry/1.0.0`), so a republish of that version overwrites files in place. Browsers may keep an old file for up to five minutes afterwards; a pack that changes its adapter contract needs a new version folder and an app release that points at it.

## Local development

The Vite dev server proxies `/dependencies` to the local API, so set the two variables in `api/.env` and the frame loads the pack exactly as in production.
