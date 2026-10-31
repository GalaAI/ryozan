# The starseed field

`relic-field-gsap.js` is the **production background system** for the portal —
not an experiment. Both design scenes boot it. Without it the portal has a flat
dead backdrop, which is one of the ways a correct build still looks wrong.

Two builds ship here:

| File | Use |
|---|---|
| `relic-field-gsap.js` | **the one to ship.** Random heading per pass, held rest between passes, star dimming at rest. Needs GSAP for the pass timing. |
| `relic-field.js` | fallback — continuous drift, no passes, no GSAP dependency. |

Both register `window.RelicField` with the same `start(canvas, host)` signature,
so they are drop-in swaps. Both render a single static frame under
`prefers-reduced-motion` — no ticker, no tweens.

## Loading

Copy the chosen file to `app/public/vendor/relic-field.js` and load it in
`app/index.html` **before** the app bundle:

```html
<script src="https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js"></script>
<script src="/vendor/relic-field.js"></script>
```

GSAP is optional. Without it the gsap build still runs; it just loses the
pass/rest timing and the breathing crest.

## Mounting

```tsx
import { BackgroundField } from '@ryozan/ui';

<div style={{ position: 'relative' }}>
  <BackgroundField position="fixed" />
  {/* everything else, z-index above 0 */}
</div>
```

One field per shell, first child, behind everything. The engine reads its
parameters from the host element's custom properties, so two nested fields
fight over one host.

## Scene values

The scenes override the token defaults because the portal ground is darker:

```
--field-a0: .05        (token default .010)
--field-a1: .16        (token default .064)
--field-star-rgb: 200,161,94   (lifted gold, stars only)
--field-star-rest-l: .75
--field-star-rest-a: .55
--field-pass-secs: 11
--field-rest-secs: 2.5
```

`BackgroundField` defaults to exactly these. Pass props only to deviate.
