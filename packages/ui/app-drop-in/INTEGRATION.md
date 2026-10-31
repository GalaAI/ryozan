# Integrating into GalaAI/BTTCO

Read against the real repo at `main` (`app/` = Vite + React 19 + TS + Tailwind v4).
Four files to copy, four import lines. Nothing existing gets rewritten.

| Copy from `app-drop-in/` | To |
|---|---|
| `scene-compat.css` | `app/src/styles/` |
| `portal-motion.css` | `app/src/styles/` |
| `dashboard-nexus.css` | `app/src/styles/` |
| `../src/field/relic-field-gsap.js` | `app/public/vendor/relic-field.js` |

Then the four `@import` lines below, in order, and the package itself.

---

## What is actually wrong today

Not the components. Two things in the app's CSS:

**1 · `app/src/styles/tokens.css` has drifted from the design system.**
It calls itself the single source of truth, and seven of its values no longer
match `_ds`:

| Token | In your repo | Design system | Symptom |
|---|---|---|---|
| `--text-secondary` | `#C0C0C0` | `#c9c0b0` | cool grey where the brand is warm bone |
| `--text-muted` | `#666666` | `#9d9686` | mono labels far too dark |
| `--muted` | `#b9ad96` | `#9d9686` | inconsistent with the above |
| `--bone-white` | `#d4c9b5` | `#d9bf95` | headings lose the tan |
| `--panel` | `rgba(255,248,236,.04)` | `#1a1b1a` | warm wash over near-black goes **brown** |
| `--panel-border` | gold @ 25% | bone @ 14% | every card border the wrong hue |
| `--font-title` | `Inter` | `Bebas Neue` | titles in the wrong typeface |
| `--tiger-orange` | `#faae15` | retired → `#d9bf95` | the mustard yellow the system killed |

**2 · The scene vocabulary does not exist in the app at all.**
Both design scenes are authored in `--ink`, `--line`, `--surface`, `--well`,
`--ground`, `--accent-coral`, `--sig-ok/live/err/warn`, `--gold-decor`. None of
those are defined anywhere in `app/src`. Anything built from the scenes renders
with **undefined colours** — which looks like a broken design, which is why an
agent concludes the design must be wrong and rebuilds it.

---

## Step 1 · Add the compat file

Copy `scene-compat.css` to `app/src/styles/scene-compat.css`.

It applies **globally**. Every screen restyles — dashboard, public site, portal
— which is the goal: one vocabulary, one look, no compat scope to unwind later.

**Brand assets are untouched.** No logo, tiger mark, flash illustration, shop
photo or crew portrait is referenced by a token. Those are `<img>` sources and
`background-image` URLs; nothing in this file can reach them. What changes is
chrome: text colour, panel fill, border hue, typeface.

**Expect two visible shifts across existing screens:**

1. **Panels go solid.** They were a translucent warm wash, which over
   near-black goes brown and compounds into mud when nested. Now near-black and
   solid — colour belongs in the border and the label. Layered cards will read
   flatter and cleaner. If one now looks too flat, give it a border, not a tint.
2. **Everything changes typeface** — Poppins for display, Space Grotesk for
   body. Metrics differ from Oswald/Inter, so tight fixed-width labels are
   where to look for overflow first.

Also: `--tiger-orange` #faae15 now resolves to the warm tan, so any yellow
"live"/"new" badge goes tan. If a badge genuinely needs urgency, point it at
`--sig-warn` (terracotta).

**The Nexus UWC panel is handled too.** It shipped with its own token set lifted
from GalaX, so it ignored Ryozan colours and the global realignment could not
reach it. `scene-compat.css` now repoints `--nuwc-*` at the real tokens, moves
its theme presets onto the heritage palette, and lifts its 7–9px uppercase mono
up to the 10.5px floor. Structure untouched.

**If one screen needs to stay put:** add `data-bt-legacy` to its root element
and it holds the old values. Temporary parking for one screen while you fix it
— every use is a to-do, not a strategy.

## Step 2 · One line in `app/src/index.css`

```diff
  /* ── Design Tokens (single source of truth) ────────── */
  @import "./styles/tokens.css";
+ @import "./styles/scene-compat.css";
```

Immediately after, before `components.css`. Order matters.

## Step 2b · Load Poppins and Space Grotesk

The scenes are drawn in Poppins (display) and Space Grotesk (body). Your repo
ships neither. Add one line to `app/index.html`:

```html
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;800;900&family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
```

KC Tallboy and KC SimpleSans are already loaded from `/assets/fonts/` by
`index.css` — leave those alone. Courier is a system face, nothing to load.

The compat file points `--font-display` at Poppins and `--font-body` at Space
Grotesk **inside `[data-bt-portal]` only**, so the legacy dashboard stays on
Inter/Oswald and nothing you already shipped changes typeface.

If a face fails to load the fallbacks are Oswald and Inter — degraded, never
broken.

## Step 2c · Load the starseed field

The portal's background is a drifting dot-wave with stars, rendered to canvas.
It is a real system, not decoration, and it exists nowhere in your repo — without
it the portal sits on a flat dead backdrop.

Copy `packages/ui/src/field/relic-field-gsap.js` to
`app/public/vendor/relic-field.js`, then in `app/index.html` before the bundle:

```html
<script src="/vendor/relic-field.js"></script>
```

**No GSAP CDN needed** — `gsap@^3.15.0` is already in `app/package.json`. Expose
it once for the field to pick up, in `main.tsx`:

```ts
import { gsap } from 'gsap';
(window as unknown as { gsap: typeof gsap }).gsap = gsap;
```

Without GSAP the field still runs, minus the pass/rest timing. Mount one
`<BackgroundField />` per shell — both shells do it for you. Reduced-motion users
get a single static frame automatically.

## Step 2d · Motion and utility classes

Copy `portal-motion.css` to `app/src/styles/portal-motion.css` and import it
after the compat file:

```diff
  @import "./styles/tokens.css";
  @import "./styles/scene-compat.css";
+ @import "./styles/portal-motion.css";
```

25 named animations and the utility classes the scenes reference by name
(`.bt-lift`, `.bt-cta`, `.bt-grain`, `.pom-scroll`, `.bt-wordmark`,
`.bt-medallion`, the nav-rail hover set). Without them sheets appear with no
entrance and the grain film reads flat. The authoring rig's own rules were
stripped during extraction.

Note: this only covers the CSS half of the motion language. See
**MISSING-SYSTEMS.md #1** for the GSAP half, which is not built.

## Step 2e · The dashboard type patch

`dashboard-nexus.css` is in this folder. Copy it to `app/src/styles/` and import
it last:

```diff
  @import "./styles/tokens.css";
  @import "./styles/scene-compat.css";
  @import "./styles/portal-motion.css";
+ @import "./styles/dashboard-nexus.css";
```

Type only — it aligns the legacy dashboard's headings, stat cards, booking rows
and badges to the brand faces. The design scene has always loaded it; the app
never did.

## Step 3 · Add the package

Copy `packages/ui/` to the repo root. Root `package.json`:

```json
{ "workspaces": ["app", "packages/*"] }
```

```bash
npm install
npm install @ryozan/ui --workspace app
```

**Do NOT import `@ryozan/ui/tokens.css` or `@ryozan/ui/fonts.css` in this repo.**
The app already defines those tokens and already loads KC Tallboy and
KC SimpleSans from `/assets/fonts/`. Importing the package's copies would
create two competing `:root` blocks — exactly the fight to avoid. Those exports
exist for standalone use only.

Vite compiles the package's `.tsx` with the app. No build step.

## Step 4 · Mount a screen

No wrapper needed — the tokens are global now:

```tsx
import { PortalShell, HomeScreen, token } from '@ryozan/ui';

<div style={{ height: '100dvh' }}>
  <PortalShell tabs={TABS} activeTab={tab} onSelectTab={setTab}>
    <HomeScreen
      role="staff"
      title="Home"
      meta="Sat Aug 1 // 2 appointments today"
      today={[
        { id: '1', time: '10:00A', label: 'Marcus · panther, chest',
          state: 'Confirmed', stateColor: token.color.spring },
        { id: '2', time: '2:00P', label: 'Open slot',
          actionLabel: 'Quick book', onAction: openBooking },
      ]}
    />
  </PortalShell>
</div>
```

If it renders wrong, check in this order: (1) the `@import` is after
`tokens.css` and before `components.css`; (2) the Google font link is in
`index.html`; (3) nothing else in the app re-declares `:root` after this file —
`tokens.css` says "no other file should define :root vars," and this file is
the one sanctioned exception.

## Step 5 · Lint the app

```bash
npx oxlint --config packages/ui/.oxlintrc.json app/src
```

Flags raw hex, raw px and off-system fonts **in app code**. Expect a large
number of existing hits in the legacy dashboard; what matters is that new
portal code adds none.

---

## Read this too

**[MISSING-SYSTEMS.md](./MISSING-SYSTEMS.md)** — a full sweep of every scene
dependency against your repo. Five things are still missing; one needs a one-line answer from you (whether
`dashboard-nexus.css` and `nexus-uwc.css` are the same system).

## Known remaining gaps

- **Fonts are resolved** (step 2b): Poppins + Space Grotesk load from Google,
  scoped to the portal. Note this contradicts the `_ds` bundle, which declares
  Oswald/Inter — so the adherence lint config will flag both faces as
  off-system. Either update `_ds` (and its `fontFamilies` allowlist) to the
  real stack, or expect those two warnings and ignore them. Worth fixing
  upstream so the lint stays trustworthy.
- **Four GAP values** (`--well`, `--track`, `--sig-warn`, `--accent-coral`) are
  literals in the compat file because nothing upstream defines them.
  `--accent-coral` is the important one: it is every CTA in the portal.
- **`--text-dim` moved to the system's `#6f695d`**, which is lighter than your
  `#4a4a4a`. `dashboard.css` uses it for disabled controls, so disabled text is
  now more visible. If a disabled control reads as enabled, that control wants a
  real disabled treatment rather than a darker grey — there is a commented
  one-line revert in the CSS while you get to it.
- **Sweep for the old names.** `--tiger-orange` still resolves, but it should not
  survive: `grep -rn "tiger-orange" app/src` and retire each hit. Same for any
  `#faae15`, `#C0C0C0` or `#666666` literal that bypassed tokens entirely — the
  oxlint config in step 5 will find those.
