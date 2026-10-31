# Getting this into Ryozan and running

Vite + React 19 + TypeScript. Four steps, then a screen renders.

## 1 · Copy the package in

Put the whole `packages/ui/` folder at the repo root:

```
<user-home>\.ark\solatree\augments\GalaAI\Ryozan\packages\ryozan-ui\
```

If the repo has no workspace yet, add one at the root `package.json`:

```json
{ "workspaces": ["app", "packages/*"] }
```

Then from the repo root:

```bash
npm install
npm install @ryozan/ui --workspace app
```

No build step for the package — it ships `.tsx` source and Vite compiles it
with the app.

## 2 · Load the CSS, in this order

In the app entry (`app/src/main.tsx`), **above** any app styles:

```ts
import '@ryozan/ui/fonts.css';
import '@ryozan/ui/tokens.css';
import '@ryozan/ui/scene-compat.css';   // ← do not skip this one
import './styles/app.css';
```

**`scene-compat.css` is the file that makes the screens look like the design.**
Without it `--ink`, `--line`, `--surface`, `--well`, `--accent-coral` and the
`--sig-*` signals are undefined, and every screen renders with missing colours.
That is the single most likely reason a build "looks nothing like the design."

Google faces still load in `index.html`:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=Oswald:wght@400;500;600;700&family=Bebas+Neue&family=IBM+Plex+Mono:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
```

## 3 · Render a screen

Screens are assembled already. This is a complete working phone home:

```tsx
import { PortalShell, HomeScreen, BookingSheet, token } from '@ryozan/ui';

const TABS = [
  { id: 'home', label: 'Home' },
  { id: 'book', label: 'Book' },
  { id: 'cards', label: 'Cards' },
  { id: 'you', label: 'You' },
  { id: 'shop', label: 'Shop' },
];

export function Phone() {
  const [tab, setTab] = React.useState('home');
  const [booking, setBooking] = React.useState(false);

  return (
    <PortalShell
      tabs={TABS}
      activeTab={tab}
      onSelectTab={setTab}
      agentGlyph="◷"
      agentName="Booky"
      agentAccent={token.color.spring}
      onOpenAgent={() => setBooking(true)}
      overlays={
        <BookingSheet
          open={booking}
          onClose={() => setBooking(false)}
          dayLabel="Sat Aug 1"
          name={name}
          onNameChange={setName}
          times={[{ id: '10', label: '10:00A' }, { id: '14', label: '2:00P' }]}
          selectedTime={time}
          onSelectTime={setTime}
          types={[{ id: 'full', label: 'Full day' }, { id: 'half', label: 'Half day' }]}
          selectedType={type}
          onSelectType={setType}
          artists={[{ id: 'justin', label: 'Justin' }, { id: 'jade', label: 'Jade' }]}
          selectedArtist={artist}
          onSelectArtist={setArtist}
          onCommit={commit}
          note="Booky sends the hold and the deposit link when you confirm."
        />
      }
    >
      <HomeScreen
        role="staff"
        title="Home"
        meta="Sat Aug 1 // 2 appointments today"
        galaGlyph="✦"
        galaLine="Two in the chair, one deposit outstanding."
        today={[
          { id: '1', time: '10:00A', label: 'Marcus · panther, chest', state: 'Confirmed', stateColor: token.color.spring },
          { id: '2', time: '2:00P', label: 'Open slot', actionLabel: 'Quick book', onAction: () => setBooking(true) },
        ]}
      />
    </PortalShell>
  );
}
```

Desktop and tablet use `DesktopShell` with the same `HomeScreen`, passing
`passRail="date"`. `mode` comes from your existing width measurement — the
shells contain no media queries.

## 4 · Lint the app, not just the package

```bash
npx oxlint --config packages/ui/.oxlintrc.json app/src
```

This flags raw hex, raw px and off-system fonts in **app code**. That is the
check that catches a rebuild in review instead of in production.

---

## If it still looks wrong

In order of likelihood:

1. `scene-compat.css` not imported, or imported after app styles.
2. App styles overriding with their own `--bg`/`--text` — the app's old
   `tokens.css` and this package both define root variables. Load order decides.
   Delete the app's copies rather than fighting them.
3. `tokens-bridge.css` / `tokens-relic.css` still imported somewhere in the app.
   Those are retired. Remove the imports; they will fight this package.
4. Fonts: the scenes were drawn in Poppins and Space Grotesk, which the `_ds`
   bundle does not ship. Screens will render in Oswald/Inter until you settle
   CONFLICTS.md #4. Same layout, different letterforms.

## If Claude Code rebuilds the UI anyway

It has no reason to now — the screens exist. Point it at the assembled screens,
not the primitives, and hold it to this:

> `@ryozan/ui` exports the screens. Use `HomeScreen`, `PortalShell`,
> `BookingSheet`, `AgentChatSheet`, `DesktopShell`. Do not write JSX that
> reimplements a screen. If a screen needs a block that does not exist, stop and
> ask. Run the oxlint config on `app/src` before you call anything done.
