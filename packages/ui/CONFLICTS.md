# Token conflicts

Token source for this package is **only** `_ds/.../colors_and_type.css` plus
the `_ds` manifest and adherence config. `tokens-bridge.css`,
`tokens-relic.css` and `Motion Labs/ark-ds/*` were **not** carried over.

Every disagreement is recorded here instead of being silently resolved. Nothing
below is decided — each needs your call.

---

## 1 · The design scenes are authored against Relic names, not `_ds` names

The single biggest finding, and it is the likely root of agents "reinterpreting"
the design: **both scenes reference a token vocabulary that the `_ds` bundle
does not define.** `Portal Mobile v2.dc.html` and `BTTCO Portal One.dc.html`
use `--ink`, `--line`, `--surface`, `--well`, `--ground`, `--accent-coral`,
`--sig-ok`, `--gold-decor` and friends throughout. Those are Relic names. An
implementer loading only the `_ds` bundle gets *undefined variables*, sees a
broken-looking screen, and concludes the design must be rebuilt.

This package resolves the vocabulary to `_ds` names. The mapping used:

| Scene token (Relic) | Value in Relic | Mapped to `_ds` | `_ds` value | Note |
|---|---|---|---|---|
| `--ink` | `#e3dbcb` | `--text` | `#e8dfcf` | near-identical |
| `--ink-dim` | `#c2b9a8` | `--text-secondary` | `#c9c0b0` | near-identical |
| `--ink-mute` | `#9a9184` | `--text-muted` | `#9d9686` | near-identical |
| `--ink-ghost` | `#8b8271` | `--text-muted` | `#9d9686` | **lossy** — Relic had two steps here, `_ds` has one |
| `--track` | `#24231f` | `--text-dim` | `#6f695d` | **wrong by intent** — `--track` is a bar/ring track, not text |
| `--line` | `rgba(166,125,67,.13)` | `--border-subtle` | `rgba(232,223,207,0.12)` | hue shifts gold → bone |
| `--line-2` | `rgba(166,125,67,.2)` | `--panel-border` | `rgba(232,223,207,0.14)` | hue shifts gold → bone |
| `--line-live` | `rgba(166,125,67,.6)` | `--border-active` | `rgba(217,191,149,0.45)` | weaker |
| `--surface` | `#171815` | `--panel` | `#1a1b1a` | near-identical |
| `--ground` | `#0a0b0a` | `--bg` | `#121312` | **`_ds` is lighter** — Relic deepened the ground |
| `--well` | `#060706` | `--bg-dark` | `#121312` | **UNRESOLVED** — `_ds` has no below-ground surface |
| `--gold-decor` / `--gold-fill` | `#c8a15e` | `--gold` | `#a67d43` | **lossy** — `_ds` has no lifted gold for fills |
| `--sig-ok` | `#70c27b` | `--alt-spring` | `#70c27b` | exact |
| `--sig-live` | `#63badf` | `--alt-sky` | `#63badf` | exact |
| `--sig-err` | `#fd8e8e` | `--alt-pink` | `#fd8e8e` | exact |
| `--sig-warn` | `#d38a68` | `--pending` | `#ffb300` | **wrong** — `_ds` `--pending` is the retired mustard family |

**Decide:** either (a) add the missing Relic names to the `_ds` bundle so the
scenes and the package share one vocabulary, or (b) accept the lossy mappings
above and let me restate the scenes in `_ds` names. Until one of these happens,
every implementer will keep hitting the same wall.

## 2 · `--accent-coral` #d38a68 has no `_ds` equivalent

The scenes' primary CTA colour. `_ds` retired `--tiger-orange` (#faae15) to
`--tan` (#d9bf95), which is a bone-tan, not a terracotta. The nearest `_ds`
value is `--alt-pink` (#fd8e8e) — a flash pink, visibly different.

This package maps coral → `--alt-pink`. **Every accent CTA in the portal is
therefore off-hue from the design scenes.** Needs a real decision: add
`--accent-coral` to `_ds`, or restyle the CTAs to `--alt-pink` and accept the
scenes are stale.

## 3 · `--accent-cream` #e4d9c0 vs `--text` #e8dfcf

Scenes use `--accent-cream` for page titles and lit surfaces. `_ds` has no
cream; `--text` is 4 points brighter. Mapped to `--text`. Low impact, but it
means "cream panel" surfaces (the collector Club tile) lose their distinction
from body text.

## 4 · Fonts: the scenes use three faces `_ds` does not ship

| Face | Where used | `_ds` status |
|---|---|---|
| **Poppins** | `--font-display` — every page title and pass headline | **not in `_ds`** |
| **Space Grotesk** | `--font-body` — all prose and chat lines | **not in `_ds`** |
| **Archivo** | desktop topbar title, pass headlines | **not in `_ds`** |

`_ds` declares `--font-display: 'Oswald'`, `--font-body: 'Inter'`. The
adherence lint config (carried into this package verbatim) will **flag Poppins,
Space Grotesk and Archivo as off-system fonts.**

You have stated the current stack is *KC Tallboy, Poppins, JetBrains Mono,
Courier, Space Grotesk*. That contradicts the `_ds` bundle on three of five
names. This package uses the `_ds` names (Oswald/Inter) so the lint passes.

**Decide:** update `_ds` (and its lint allowlist) to the real stack — Poppins,
Space Grotesk, KC Tallboy, JetBrains Mono, Courier — or accept Oswald/Inter in
production and treat the scenes' fonts as unshipped. Archivo appears nowhere in
your stated stack and is probably accidental drift; worth deleting either way.

## 5 · Ground/panel depth: Relic added a step `_ds` lacks

Relic runs `--well #060706` < `--ground #0a0b0a` < `--surface #171815`. `_ds`
runs `--bg #121312` = `--bg-dark` < `--panel #1a1b1a`. The scenes lean on the
below-ground `--well` for inputs, ledgers and the topbar — with `_ds` alone,
inputs sit at the same depth as the page and the recessed look is gone.

Mapped `--well` → `--bg-dark`. **UNRESOLVED**: `_ds` needs a well token, or
inputs need a different treatment.

## 6 · Type scale: rem steps vs the scenes' fixed px

`_ds` ships a 1.25-ratio rem scale (`--text-xs` … `--text-4xl`). The scenes use
fixed px (10.5, 12.5, 13, 25, 33). Components here use the rem scale, so sizes
differ from the scenes by a few points in places. `--text-xs` (0.64rem ≈
10.24px) is the floor — the brand rule against tiny all-caps mono holds.

Relic's `--t-*` steps and `--s-*`/`--r-*` scales were **not** carried over;
`_ds` spacing and radii cover the same ground under different names.

## 7 · Motion

Relic's `--lift-card`/`--lift-cta`/`--lift-press` were not carried over. `_ds`
ships the three transition speeds only. Hover lifts in the scenes
(`translateY(-3px)` cards, `translateY(-5px) scale(1.02)` CTAs) are therefore
**not tokenised** — they live as literals in component source, which the lint
will flag as raw px. **UNRESOLVED**: add lift tokens to `_ds`, or accept the
lint warnings on three lines.
