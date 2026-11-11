# @ryozan/ui

## The one rule

**Consumers import from `@ryozan/ui`. Nobody authors new UI components.**

If a screen needs an element this package does not export, that element has
not been designed yet. Raise it and wait. Do not write a local component, do
not "temporarily" inline a styled div, do not fork a component to adjust it.
An unanswered question is not permission to author UI.

Corollaries:

- No hex values, no px literals in app code. Use `token.*` or `var(--*)`.
- No new fonts. The stack is fixed (see `fonts/fonts.css` and CONFLICTS.md #4).
- No component internals imports — `@ryozan/ui` only. The lint config enforces this.
- Adjusting a component means changing it *here*, once, with the design owner's
  sign-off — never at the call site.

## Start here

**[GETTING-IT-RUNNING.md](./GETTING-IT-RUNNING.md)** — copy into the repo, the
CSS load order (including the one file that makes screens match the design), and
a complete working phone screen.

## Install

`app/` does not consume this folder as a workspace. It installs a committed
tarball, `app/vendor/ryozan-ui-<version>.tgz`. Editing `src/` here therefore
changes nothing in the app until you repack **and** relock:

```bash
cd packages/ui && npm run pack:app
cd ../../app && npm install --package-lock-only "@ryozan/ui@file:vendor/ryozan-ui-<version>.tgz"
```

Read **[docs/release/UI_TARBALL_VENDORING.md](../../docs/release/UI_TARBALL_VENDORING.md)**
before you ship a change here — skipping the relock step makes `npm ci`
reinstall the old component silently. `cd app && npm run check:ui-tarball`
fails when the two have drifted.

Then, in the app shell, in this order:

```ts
import '@ryozan/ui/fonts.css';   // KC, Suti, Aero Wolves faces
import '@ryozan/ui/tokens.css';       // the token source
import '@ryozan/ui/scene-compat.css'; // scene token vocabulary — REQUIRED
import './app.css';                  // your app styles, last
```

Google-hosted faces (Inter, Oswald, Bebas Neue, IBM Plex Mono, JetBrains Mono)
are not vendored — load them in the shell's `<head>`.

## Use

```tsx
import { Panel, SessionPass, Button, Chip, ChipGroup, token } from '@ryozan/ui';

<SessionPass
  eyebrow="Session pass"
  titleTop="Traditional"
  titleAccent="Panther · Chest"
  status="Confirmed"
  rows={[
    { label: 'Artist', value: 'Justin Walker' },
    { label: 'When', value: 'Sat Aug 1 · 2:00P–5:00P' },
    { label: 'Balance', value: '$150 paid · $350 due', valueColor: token.color.pink },
  ]}
  ctaLabel="View session pass →"
  onCta={openPass}
/>
```

Same component, both surfaces — `railVariant="date"` gives the desktop
date-rail form. Responsive behaviour is the app's job: pick the variant off
your `mode` value. Components carry no page layout and no breakpoints.

## Lint

```bash
pnpm --filter @ryozan/ui lint
```

`.oxlintrc.json` is the design system's adherence config, carried over
verbatim. It flags raw hex, raw px and off-system fonts. Run it on the app
too — that is what catches a rebuild before review does.

## What is in here

| Layer | Components |
|---|---|
| Primitives | Panel, GrainOverlay, EdgeDissolveRail, SectionLabel, PageTitle, MetaRow/MetaList, Button, IconButton, Chip/ChipGroup, Badge/StatusText, TextField, TextArea, Checkbox, Avatar, Wordmark |
| Screens (assembled) | PortalShell, HomeScreen, BookingSheet, AgentChatSheet, AgentTools, DesktopShell |
| Portal elements | SessionPass, ScheduleList, QuickTile, TabBar, NavRail, TopBar, AccountMenu, Sheet, AgentMark/AgentFab, AgentGlow, ChatBubble, ActionCard/ActionCardFace, ReceiptBlock |

### EdgeDissolveRail

Keeps a command rail sharp and stationary while masking only the scrolling
elements as they leave through the top edge. The page field and rail are never
blurred, painted over, or included in the mask.

```tsx
<EdgeDissolveRail rail={<CourierCommandbar />}>
  {threads}
</EdgeDissolveRail>
```

`AgentActionCard.shell` is the card architecture authority. It is assigned to
each card, not to an agent, so one hand can mix the intake docket, chair ticket,
receipt tape, courier manifest, heal chart, and memory shard blueprints.

`AgentGlow` is the shared single-color interaction effect for those shells. It
uses the associated agent RGB, follows pointer input, responds to touch and
keyboard focus, and falls back to a quiet static edge under reduced motion.

`MAPPING.md` maps every region of the two design scenes to its component, and
marks what is still **UNRESOLVED**. `CONFLICTS.md` records every place the
retired token files disagreed with the `_ds` bundle — nothing was silently
merged.

## Not in scope

No pages, no routes, no data fetching, no backend. Components only. Screens
are assembled in the app from these parts, against the design scenes.
