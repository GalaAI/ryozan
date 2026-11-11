// Contrast floor for @ryozan/ui tokens (WCAG 2 relative luminance).
// Reads design-system/tokens.json, resolves {alias} values per theme,
// composites translucent colours over their ground, and asserts each pair
// the design rulings of 2026-11-11 fixed:
//   1. text-input focus: border-active is a visible ring, 3:1 against the
//      surfaces it sits on and against the resting panel-border;
//   2. text-dim holds 4.5:1 on every website surface it is set on;
//   3. small gold text (gold-text) holds 4.5:1, text on a gold fill (on-gold)
//      holds 4.5:1, and shop `gold` keeps 3:1 for large text, borders, accents.
// Run: node --test packages/ui/scripts/contrast.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const tokens = JSON.parse(readFileSync(join(here, '..', 'design-system', 'tokens.json'), 'utf8'));
const themes = tokens.color.themes.map((t) => t.id);
const byName = new Map(tokens.color.tokens.map((t) => [t.name, t.value]));

/** A colour token's literal value in a theme, aliases followed. */
function raw(name, theme, depth = 0) {
  assert.ok(depth < 16, `alias chain too deep at ${name}`);
  const v = byName.get(name);
  assert.ok(v !== undefined, `missing colour token ${name}`);
  const s = typeof v === 'string' ? v : (v[theme] ?? v[themes[0]]);
  const alias = /^\{(.+)\}$/.exec(s);
  return alias ? raw(alias[1], theme, depth + 1) : s;
}

function parse(s) {
  if (s.startsWith('#')) {
    let h = s.slice(1);
    if (h.length <= 4) h = [...h].map((c) => c + c).join('');
    const n = (i) => parseInt(h.slice(i, i + 2), 16);
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1];
  }
  const m = /^rgba?\(([^)]*)\)$/.exec(s);
  assert.ok(m, `unparsed colour ${s}`);
  const p = m[1].split(',').map(Number);
  return [p[0], p[1], p[2], p[3] ?? 1];
}

const over = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
const lum = (c) => {
  const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
};
const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };

/**
 * The website surfaces the package paints these tokens on, as opaque colours.
 * Washes and the raised-panel gradient stops (production-only.css) are
 * composited over `bg`, which is what sits under them on every page.
 */
function surfaces(theme) {
  const bg = parse(raw('bg', theme));
  const tok = (n) => { const c = parse(raw(n, theme)); return c[3] < 1 ? over(c, bg) : c; };
  return {
    bg,
    'bg-dark': tok('bg-dark'),
    panel: tok('panel'),
    'panel-hover': tok('panel-hover'),
    'bg-surface-elevated': tok('bg-surface-elevated'),
    'color-surface': tok('color-surface'),
    'color-surface-2': tok('color-surface-2'),
    'panel-raised top': over([28, 29, 28, 0.92], bg),
    'panel-raised bottom': over([12, 13, 12, 0.97], bg),
  };
}

function assertOnAll(fgName, min, theme, why) {
  const fg = parse(raw(fgName, theme));
  for (const [ground, g] of Object.entries(surfaces(theme))) {
    const r = ratio(fg[3] < 1 ? over(fg, g) : fg, g);
    assert.ok(r >= min, `${fgName} on ${ground} (${theme}) is ${r.toFixed(2)}:1, needs ${min}:1 — ${why}`);
  }
}

for (const theme of themes) {
  test(`text-dim reads 4.5:1 on every website surface (${theme})`, () => {
    assertOnAll('text-dim', 4.5, theme, 'hints, meta and ledger labels are text');
  });

  test(`gold-text reads 4.5:1 on every website surface (${theme})`, () => {
    assertOnAll('gold-text', 4.5, theme, 'small gold text: outline labels, links, mono labels');
  });

  test(`on-gold reads 4.5:1 on a gold fill (${theme})`, () => {
    const r = ratio(parse(raw('on-gold', theme)), parse(raw('gold', theme)));
    assert.ok(r >= 4.5, `on-gold on gold (${theme}) is ${r.toFixed(2)}:1`);
  });

  test(`gold keeps 3:1 on bg for large text, borders and accents (${theme})`, () => {
    const r = ratio(parse(raw('gold', theme)), parse(raw('bg', theme)));
    assert.ok(r >= 3, `gold on bg (${theme}) is ${r.toFixed(2)}:1`);
  });

  test(`border-active is a visible focus ring (${theme})`, () => {
    assertOnAll('border-active', 3, theme, 'focus indicator');
    const active = parse(raw('border-active', theme));
    const rest = parse(raw('panel-border', theme));
    assert.notDeepEqual(active, rest, 'border-active must differ from panel-border');
    const r = ratio(active, rest);
    assert.ok(r >= 3, `border-active vs panel-border (${theme}) is ${r.toFixed(2)}:1, needs 3:1 change of state`);
  });
}
