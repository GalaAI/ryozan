---
name: ryozan-design-canon
description: Use before building or changing any Ryozan UI (website, portal, booking, content frames). Build only from the Ryozan Studio Design System artifact and @ryozan/ui; never regenerate, approximate or interpret a design.
---

# Ryozan design canon

The Ryozan Studio Design System artifact (Ryozan Modern Classic) is the source of record for every Ryozan surface. This repo mirrors it and never leads it. Operator rule: agents build from the system; they do not regenerate assumed designs or interpret applications.

## Read order, every time

1. The artifact's `project/README.md` (Build law, canon lock, surfaces, type, colour, motion, imagery, generated Index). If the artifact is not reachable, read `packages/ui/README.md` and `packages/ui/design-system/tokens.json` and say the artifact was not read.
2. `project/api/tokens.md`, then the `project/api/components/<Comp>.md` card of every component the surface uses.
3. The north star for the surface: `website-kit.html` + `WEBSITE-V2-BRIEF.md` (public site); `portal-desktop.dc.html` / `portal-mobile.dc.html` (portal, canon-locked); `packages/ryozan-booking-flow-build/` (booking); `JWT.md` (jwt theme).
4. `AGENTS.md` § Design system.

## Building

- Tokens by name only: `var(--name)` or the `token` export of `@ryozan/ui`. `packages/ui/src/tokens/tokens.css` is GENERATED from `packages/ui/design-system/tokens.json` (`npm run tokens:gen` in `packages/ui`). Never edit it by hand; never add a value outside `tokens.json`.
- Components from `@ryozan/ui` or a registered card. No new components, colours, fonts, radii, easings or layouts in `app/src`.
- Assets copied from the artifact store, never approximated. Portraits and portfolios come from R2 through the API, not the design system.
- A need the system cannot express goes upstream first: add the token, card, asset or rule to the artifact, mirror `tokens.json` here, then build.
- Themes: `default`, `shop`, `jwt`. Public site reads website tokens (`bg`, `text`, `muted`, `panel`, `space-*`, `r-*`); portal reads Relic tokens (`ground`, `surface`, `ink`, `line`, `gold-fill`, `sig-*`, `accent-*`). Never cross them.
- The portal is canon-locked: only the four channels in the README's Building for the portal.

## Gates (all green before "done")

In `app/`: `npm run check:ui-tarball`, `npx tsc -b --noEmit`, `npm run lint` (raw hex, px and off-system `font-family` are errors; `DS_LEGACY_DEBT` in `app/eslint.config.js` lists files still being cleaned; delete a file from the list when it is clean), `npm run lint:adherence:src`, `npm run build`.

A change under `packages/ui/src` reaches the app only after repack and relock: `cd packages/ui && npm run pack:app`, then in `app/`: `npm install --package-lock-only "@ryozan/ui@file:vendor/ryozan-ui-<version>.tgz"`. Commit tarball, lockfile and source together.

## Never

- Render a component from memory or a screenshot when its source exists.
- Add a hex, px or font in app code "for now".
- Restyle, re-time or fork the portal.
- Treat JWT as a separate design language; it is the `jwt` theme on the same spine.
- Merge to `main` or deploy without the operator's explicit approval; `main` auto-deploys.
