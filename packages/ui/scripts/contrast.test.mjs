// Contrast floor for every @ryozan/ui colour pair the design system documents
// (WCAG 2 relative luminance). Reads design-system/tokens.json, resolves
// {alias} values per theme, composites translucent colours over the ground
// they sit on, and asserts:
//   text 4.5:1 · large-only text (24px+) 3:1 · control outlines, focus rings,
//   hover edges and meaningful fills 3:1 · focus 3:1 against the resting edge.
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
const col = (name, theme) => parse(raw(name, theme));

/** Website surfaces (what @ryozan/ui components and website pages paint). */
function website(theme) {
  const bg = col('bg', theme);
  const tok = (n) => { const c = col(n, theme); return c[3] < 1 ? over(c, bg) : c; };
  return {
    bg, 'bg-dark': tok('bg-dark'), panel: tok('panel'), 'panel-hover': tok('panel-hover'),
    'bg-surface-elevated': tok('bg-surface-elevated'), 'color-surface': tok('color-surface'),
    'color-surface-2': tok('color-surface-2'),
    // panel-raised gradient stops (production-only.css), over bg
    'panel-raised top': over([28, 29, 28, 0.92], bg), 'panel-raised bottom': over([12, 13, 12, 0.97], bg),
  };
}

/** Portal surfaces (what the design scenes paint), washes over `surface`. */
function portal(theme) {
  const surface = col('surface', theme);
  return {
    surface, ground: col('ground', theme), 'ground-2': col('ground-2', theme), well: col('well', theme),
    'surface-2': over(col('surface-2', theme), surface), 'surface-3': over(col('surface-3', theme), surface),
  };
}

function onAll(fgName, grounds, min, theme, why) {
  const fg = col(fgName, theme);
  for (const [ground, g] of Object.entries(grounds)) {
    const r = ratio(fg[3] < 1 ? over(fg, g) : fg, g);
    assert.ok(r >= min, `${fgName} on ${ground} (${theme}) is ${r.toFixed(2)}:1, needs ${min}:1 (${why})`);
  }
}

function pair(fgName, bgName, min, theme, why) {
  const r = ratio(col(fgName, theme), col(bgName, theme));
  assert.ok(r >= min, `${fgName} on ${bgName} (${theme}) is ${r.toFixed(2)}:1, needs ${min}:1 (${why})`);
}

for (const theme of themes) {
  test(`website text reads 4.5:1 on every website surface (${theme})`, () => {
    for (const n of ['text', 'text-secondary', 'muted', 'text-muted', 'text-dim', 'gold-text', 'danger', 'ok', 'alt-spring', 'alt-pink', 'alt-sky', 'pending']) {
      onAll(n, website(theme), 4.5, theme, 'text');
    }
  });

  test(`website outlines, focus and hover edges read 3:1 (${theme})`, () => {
    onAll('field-border', website(theme), 3, theme, 'resting control outline');
    onAll('border-hover', website(theme), 3, theme, 'hover edge');
    onAll('border-active', website(theme), 3, theme, 'focus indicator');
    onAll('gold', website(theme), 3, theme, 'gold borders, selected chip edge, large gold text');
    const focus = col('border-active', theme);
    const rest = col('field-border', theme);
    const hover = col('border-hover', theme);
    assert.ok(ratio(focus, rest) >= 3, `focus vs resting edge (${theme}) is ${ratio(focus, rest).toFixed(2)}:1, needs 3:1`);
    assert.notDeepEqual(focus, hover, 'hover and focus must differ');
    assert.notDeepEqual(rest, hover, 'hover must differ from rest');
  });

  test(`text on fills reads 4.5:1 (${theme})`, () => {
    pair('on-gold', 'gold', 4.5, theme, 'primary button label, filled gold badge');
    for (const fill of ['alt-spring', 'pending', 'alt-pink', 'alt-sky', 'text-muted']) pair('alt-smoke', fill, 4.5, theme, 'filled badge');
    pair('gold-ink', 'gold-fill', 4.5, theme, 'text on gold-fill');
    pair('gold-ink', 'gold-decor', 4.5, theme, 'text on gold-decor');
    pair('accent-ink', 'accent-coral', 4.5, theme, 'coral pill label');
    pair('flame-ink', 'flame', 4.5, theme, 'destructive button label');
    for (const g of ['accent-brick', 'accent-army', 'oxblood']) pair('accent-cream-tile', g, 4.5, theme, 'text on a dark tile');
    pair('hero-ink', 'hero-bg', 4.5, theme, 'hero text');
    pair('accent-ink', 'accent-cream-tile', 4.5, theme, 'text on a cream fill');
    for (const n of ['plate-ink', 'plate-mute', 'plate-accent']) pair(n, 'plate', 4.5, theme, 'text on plate');
    pair('ink', 'sage', 4.5, theme, 'text on a sage tile');
  });

  test(`portal text reads 4.5:1 on every portal surface (${theme})`, () => {
    for (const n of ['ink', 'ink-dim', 'ink-mute', 'ink-ghost', 'sig-live', 'sig-warn', 'sig-err', 'sig-ok', 'accent-seal', 'accent-cream']) {
      onAll(n, portal(theme), 4.5, theme, 'text');
    }
  });

  test(`portal large text, rings and fills read 3:1 (${theme})`, () => {
    onAll('numeral', portal(theme), 3, theme, 'numerals, 24px and up only');
    onAll('line-live', portal(theme), 3, theme, 'portal focus ring');
    onAll('gold-fill', portal(theme), 3, theme, 'progress fill, picked chip');
  });
}
