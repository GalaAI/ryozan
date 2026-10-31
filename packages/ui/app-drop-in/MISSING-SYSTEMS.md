# Audit: scene dependencies that exist nowhere in the repo

The starseed field was not a one-off. I swept both design scenes for every
runtime dependency — scripts, globals, keyframes, utility classes, assets — and
checked each against `GalaAI/BTTCO@main`. Below is everything a build would
silently lose, and what state it is in now.

Method: read the `<helmet>` of both scenes, extracted every `<script src>`,
`window.*` global, `@keyframes`, utility class and asset path, then searched the
repo for each.

---

## Fixed in this package

| System | Was | Now |
|---|---|---|
| **Starseed field** (`RelicField`) | misfiled as a dead experiment; absent from repo | ships as `src/field/relic-field-gsap.js` + `<BackgroundField />`, mounted by both shells |
| **Scene token vocabulary** (`--ink`, `--line`, `--surface`, `--well`, `--accent-coral`, `--sig-*`) | undefined in app | `scene-compat.css` |
| **Field tokens** (`--field-rgb`, `--field-a0/a1`, `--field-star-rgb`) | undefined in app | `scene-compat.css` |
| **Motion keyframes** — 25 named animations across both scenes (`pomSheetIn`, `pomCast`, `pomCardIn`, `pomDeal`, `pomPlay`, `pomStamp`, `pomPress`, `pomRecoil`, `btSweep`, `bt-rise`, `bt-spawn`, `bt-gala-in`, `stuIn`…) | none in package or repo | `portal-motion.css`, extracted verbatim |
| **Utility classes** (`.bt-lift`, `.bt-cta`, `.bt-grain`, `.bt-wordmark`, `.bt-medallion`, `.bt-rail-panel`, `.bt-nav-box`, `.bt-nav-label`, `.pom-scroll`, `.bt-tip`, `.bt-cast`) | none | `portal-motion.css` |
| **Reduced-motion guard** | present in scenes, absent in package | `portal-motion.css`, first rule |
| **Type stack** (Poppins, Space Grotesk, Courier) | app on Inter/Oswald | `scene-compat.css` + font link |

The authoring-rig rules (`.pom-desk`, `.pom-side`, `.pom-dock`, `.pom-pickcur`)
were stripped during extraction. They must never ship.

---

## Good news: two dependencies you already have

- **GSAP.** `gsap@^3.15.0` and `@gsap/react@^2.1.2` are already in
  `app/package.json`, and `BookingPage.tsx` already uses `useGSAP` with a
  reduced-motion guard — the right pattern. So the field and the motion layer
  need **no CDN script**: `import { gsap } from 'gsap'` and hand it in. Ignore
  any instruction of mine that said to load GSAP from jsDelivr.
- **Lucide.** `lucide-react@^1.32.0` is installed, which matches the design
  system's icon style exactly. Icons are not a gap.

---

## Answered by reading the code

- **dashboard-nexus.css vs nexus-uwc.css** — two unrelated files, keep both. See #4.

## Still missing — needs your decision, not more files from me

### 1 · The GSAP motion layer itself

The scenes state it plainly: *"GSAP owns every entrance, exit and cast in this
portal."* The keyframes I extracted are only the CSS half. The other half is
imperative — staggered entrances at 725ms on the settle ease, the family clock
that fires matching emblems together, one-shot marking so nothing replays on
revisit, exit choreography with restore.

That lives in the scene's logic class, tangled with the authoring desk that must
not ship. Untangling it is a real job, not an extraction. **Until it is done,
screens will render correctly and appear instantly** — no entrance language.

Cheapest honest path: build one small `useEntrance` hook that does rise-from-right
725ms staggered, and apply it at the shell level. That gets 80% of the language
for a fraction of the work. Say the word.

### 2 · `window.claude.complete` in agent chat

The scenes call it directly for agent replies, with a JSON contract
(`{thinking:[], reply:""}`). That is a design-environment API — **it does not
exist in production.** Every agent line in the portal is currently a stub.

This needs a real backend route. Your repo already has `ai@^6.0.258` and
`streamdown` installed, so the streaming path exists; the agent endpoint and
per-agent system prompts do not. It also needs the memory-consent gate on it,
since the whole point is curation per user.

### 3 · ~~The yard / pod strip~~ — CUT

Dropped on request. `.bt-yard`, `[data-yard]`, `.bt-vp` and the three sprite
animations (`bt-yard-idle/run/wave`) are removed from `portal-motion.css`. The
sprite sheets they animated were never in the repo, so nothing is lost.

Do not re-add it from the scenes — it is cut, not pending. If the pod comes back
later it needs its sprite assets first.

### 4 · ~~dashboard-nexus.css vs nexus-uwc.css~~ — ANSWERED

Read both. **Two unrelated files, no conflict, keep both.**

- `_ds/patches/dashboard-nexus.css` — a **type-only patch** for the legacy
  `dashboard.css`. Selectors: `.nexus-greeting`, `.nexus-toolbelt`,
  `.nexus-tool-label`, `.panel-title`, `.stat-label/.stat-value`,
  `.nexus-book-*`, `.nexus-badge`, `.section-label`. Its own header states it
  changes no layout and no colour.
- `app/src/styles/nexus-uwc.css` — a **full component**, namespaced `.nuwc-*`:
  the Nexus UWC panel with calendar, debrief pane, quest rows, theme presets.

Zero shared selectors. But two real problems came out of reading them:

**4a · The design-system patch is never applied.** `BTTCO Portal One.dc.html`
loads `dashboard-nexus.css`; `app/src/index.css` does not. So the dashboard type
alignment exists only in the design scene — production has never had it. Add it
after `portal-motion.css` if you want the legacy dashboard's type to match:

```diff
+ @import "./styles/dashboard-nexus.css";
```

Copy the file from `_ds/.../patches/` into `app/src/styles/` first. Type-only, so
the risk is low — but it will visibly change the legacy dashboard's headings.

**4b · `nexus-uwc.css` is a second design system inside your app.** It declares
its own token set lifted from GalaX and does not use Ryozan tokens at all:

| `nexus-uwc.css` | Value | Ryozan equivalent |
|---|---|---|
| `--nuwc-accent` | `#a6885d` (GalaX gold) | `--gold` `#a67d43` |
| `--nuwc-cream` | `#f5f0e1` | `--text` `#e8dfcf` |
| `--nuwc-bg` | `#090909` (near-pure black) | `--bg` `#121312` |
| `--nuwc-text` | `#f5f0e1` | `--text` `#e8dfcf` |

Two consequences. First, **the global realignment cannot reach it** — it defines
its own variables, so that panel keeps the GalaX look while the rest of the app
moves to brand. Second, the brand rule against near-pure black
(`#121312`, green undertone, never `#000`) is broken there by design.

The fix is mechanical: repoint the `--nuwc-*` block at Ryozan tokens
(`--nuwc-accent: var(--gold)` and so on). One block, ~14 lines, and that panel
joins the system. The theme presets (blood/forest/ocean/ash) can stay — they are
a real feature, and ocean's `#63badf` is already the heritage sky.

**Also worth knowing:** `nexus-uwc.css` uses `font-size: 8px` and `9px` on
uppercase letter-spaced mono in about a dozen places. That breaks the type floor
you set — never that small in all caps. Same mechanical fix, larger diff.

### 5 · Asset paths will not resolve

The scenes reference `assets/thumbs/tiger-mark-trim.png`,
`assets/tiger-mark-trim-crop.png`, `assets/seal-tiger.png`,
`assets/barcode-BT-4831.png`. Production loads brand assets from the R2 CDN
(`https://assets.ryozan.app/...`). Every one of these paths breaks on
copy. Barcodes are server-generated (`/barcode/{ref}.png`) and that route does
not exist yet either.

### 6 · Desktop scroll-snap choreography

`initSnap`, `snapTo`, the `[data-screen-label]` panel model and the pointer-coarse
guard that disables it on phones. Not extracted — `DesktopShell` scrolls
normally. Fine for now, but it is why desktop will feel less deliberate than the
scene.

---

## What I would do in order

1. Ship what is here — tokens, fonts, field, keyframes, screens. Verify one
   screen renders.
2. Decide on #4a (import the type patch — one line, visible change to the legacy
   dashboard) and #4b (repoint `--nuwc-*` at Ryozan tokens so that panel stops
   being a second design system).
3. Point assets at the CDN (#5). Mechanical, and it makes the portal look right.
4. Build the agent endpoint (#2). Nothing in the agent layer is real until this
   exists.
5. Then the entrance hook (#1). It is the most visible polish and the safest to
   defer.
