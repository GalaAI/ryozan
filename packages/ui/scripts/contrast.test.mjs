// Contrast floor for every @ryozan/ui colour pair the design system documents
// (WCAG 2 relative luminance). Reads design-system/tokens.json, resolves
// {alias} values per theme, composites translucent colours over the ground
// they sit on, and asserts:
//   text 4.5:1 · large-only text (24px+) 3:1 · control outlines, focus rings,
//   hover edges and meaningful fills 3:1 · focus 3:1 against the resting edge.
// Run: node --test packages/ui/scripts/contrast.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
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

  test(`accent-cream-tile reads 4.5:1 on panel-raised and every fixed-dark website surface (${theme})`, () => {
    // Error-boundary, state-boundary and public-form panels paint panel-raised, a fixed dark
    // gradient that does not follow the theme; their headings must use accent-cream-tile.
    const bg = col('bg', theme);
    onAll('accent-cream-tile', { 'panel-raised top': over([28, 29, 28, 0.92], bg), 'panel-raised bottom': over([12, 13, 12, 0.97], bg), ...website(theme) }, 4.5, theme, 'heading on a fixed-dark panel');
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

// ── App stylesheet consumers ───────────────────────────────────────────────────
// Token pairs pass above, but a rule can still pick a theme-following text token
// (ink, cream…) for a panel that paints a fixed dark ground in every theme
// (panel-raised, dark gradients, brick-red fills). Resolve each app text rule's
// ground — its own background, an exact context rule, or an ancestor class — and
// require 4.5:1 in every theme wherever that ground is fixed-dark.
const stylesDir = join(here, '..', '..', '..', 'app', 'src', 'styles');
const productionOnly = (() => {
  const file = join(here, '..', 'src', 'tokens', 'production-only.css');
  const out = new Map();
  if (!existsSync(file)) return out;
  for (const m of readFileSync(file, 'utf8').matchAll(/--([\w-]+):\s*([^;]+);/g)) out.set(m[1], m[2].trim());
  return out;
})();
const TEXT_TOKENS = new Set(['text', 'text-secondary', 'text-muted', 'muted', 'ink', 'ink-dim', 'ink-mute', 'ink-ghost', 'cream', 'accent-cream', 'accent-cream-tile', 'plate-ink', 'plate-mute']);

/** Opaque colour stops a CSS background value paints in a theme, or null if it uses a token we cannot resolve. */
function stopsOf(value, theme) {
  const bg = col('bg', theme);
  let v = value;
  for (let i = 0; i < 4; i++) {
    v = v.replace(/var\(--([\w-]+)(?:,[^()]*(?:\([^()]*\)[^()]*)*)?\)/g, (all, name) => {
      if (byName.has(name)) return raw(name, theme);
      if (productionOnly.has(name)) return productionOnly.get(name);
      return all;
    });
  }
  if (/var\(--/.test(v)) return null;
  const found = [...v.matchAll(/#[0-9a-fA-F]{6}\b|rgba?\([^()]*\)/g)].map((m) => {
    const s = m[0];
    if (s.startsWith('#')) return parse(s);
    const p = s.slice(s.indexOf('(') + 1, -1).split(',').map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  });
  if (!found.length || found.some((c) => c.some((x) => Number.isNaN(x)))) return null;
  return found.map((c) => (c[3] < 1 ? over(c, bg) : c));
}

/** Fixed-dark: the same stops in every theme, all dark. */
function fixedDark(value) {
  const per = themes.map((t) => stopsOf(value, t));
  if (per.some((s) => !s)) return null;
  const key = (s) => JSON.stringify(s.map((c) => c.slice(0, 3).map(Math.round)));
  if (new Set(per.map(key)).size !== 1) return null;
  return per[0].every((c) => lum(c) < 0.12) ? per[0] : null;
}

function cssRules() {
  const rules = [];
  for (const name of ['components.css', 'booking.css', 'dashboard.css']) {
    const file = join(stylesDir, name);
    if (!existsSync(file)) continue;
    const css = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const sel = m[1].trim().replace(/\s+/g, ' ');
      if (!sel || sel.startsWith('@') || /:root|data-theme|:hover|:focus|::/.test(sel)) continue;
      const color = /(?:^|;)\s*color\s*:\s*var\(--([\w-]+)/.exec(m[2]);
      const background = /(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)/.exec(m[2]);
      rules.push({ file: name, sel, color: color && color[1], background: background && background[1].trim() });
    }
  }
  return rules;
}

const splitTop = (sel) => { const out = []; let d = 0, cur = ''; for (const ch of sel) { if (ch === '(') d++; if (ch === ')') d--; if (ch === ',' && d === 0) { out.push(cur.trim()); cur = ''; } else cur += ch; } if (cur.trim()) out.push(cur.trim()); return out; };

test('app text on fixed-dark panels reads 4.5:1 in every theme', { skip: !existsSync(stylesDir) && 'app/src/styles not present (packed tarball)' }, () => {
  const rules = cssRules();
  const bySel = new Map(); // exact selector part -> background value (last wins, like the cascade)
  const byClass = new Map(); // last class of a selector part -> background value
  for (const r of rules) {
    if (!r.background) continue;
    for (const part of splitTop(r.sel)) {
      bySel.set(part, r.background);
      const last = part.split(/\s*[>+~]\s*|\s+/).pop();
      for (const c of last.matchAll(/\.([\w-]+)/g)) if (!byClass.has(c[1])) byClass.set(c[1], r.background);
    }
  }
  const failures = [];
  for (const r of rules) {
    if (!r.color || !TEXT_TOKENS.has(r.color) || !byName.has(r.color)) continue;
    for (const part of splitTop(r.sel)) {
      const segs = part.split(/\s*[>+~]\s*|\s+/).filter(Boolean);
      let ground = r.background;
      if (!ground && segs.length > 1) {
        const context = part.slice(0, part.lastIndexOf(segs[segs.length - 1])).replace(/[\s>+~]+$/, '').trim();
        ground = bySel.get(context);
        if (!ground) for (const seg of segs.slice(0, -1).reverse()) { const c = [...seg.matchAll(/\.([\w-]+)/g)].pop(); if (c && byClass.has(c[1])) { ground = byClass.get(c[1]); break; } }
      }
      if (!ground) continue;
      const stops = fixedDark(ground);
      if (!stops) continue;
      for (const theme of themes) {
        const fg = col(r.color, theme);
        const worst = Math.min(...stops.map((g) => ratio(fg, g)));
        if (worst < 4.5) failures.push(`${r.file} ${part} -> ${r.color} (${theme}) ${worst.toFixed(2)}:1`);
      }
      break;
    }
  }
  assert.deepEqual(failures, [], `text below 4.5:1 on fixed-dark panels:\n${failures.join('\n')}`);
});


// ── Back-office stays dark (owner ruling 2026-11-11) ─────────────────────────────
// The portal and dashboard are the back office: dark in every theme. Inside them,
// text may never use a token that follows the theme (it goes dark in shop). This
// checks token NAMES, so it also catches rules whose ground cannot be resolved.
// Scope: the authenticated shell (.app-view, DashboardLayout) and the portal root
// (.ryozan-mobile-portal-home, which holds the hub). A rule is in scope when its
// selector names a scope root, or any class only back-office source renders.
const appSrc = join(here, '..', '..', '..', 'app', 'src');
export const SCOPE_ROOTS = ['app-view', 'ryozan-mobile-portal-home'];
export const THEME_FOLLOWING_TEXT = ['ink', 'ink-dim', 'ink-mute', 'ink-ghost', 'cream', 'accent-cream'];
// Gold that follows the theme (darkened for paper in shop). Banned in the back office in
// every property (text, borders, fills, outlines): use gold-vibrant, and bg-dark on it.
export const THEME_FOLLOWING_GOLD = ['gold', 'gold-text', 'gold-fill', 'gold-decor', 'accent-seal', 'on-gold', 'gold-ink', 'gold-wash'];
export const BACKOFFICE_GOLD = ['gold-vibrant', 'bg-dark'];
// Light text tokens that turn dark in shop, banned in the back office as fills too.
export const THEME_FOLLOWING_FILL = ['cream', 'accent-cream'];
// Back-office pages whose markup is portalled into the desktop portal's shadow root.
export const SHADOW_PINNED_FILES = ['pages/dashboard/DesktopPortalPage.tsx'];
// Constant light text: same light value in every theme (website vocabulary plus the tile).
export const BACKOFFICE_TEXT = ['text', 'text-primary', 'text-secondary', 'text-muted', 'muted', 'bone-white', 'accent-cream-tile'];
// Narrow, named exceptions: light inner surfaces that deliberately follow the theme.
export const BACKOFFICE_EXCEPTIONS = [
  { file: 'dashboard.css', selector: '.flash-design-card--inventory .flash-design-cutout-preview > span', reason: 'index badge paints its own --well; ink follows the theme together with well (13.23:1 shop, 15.86:1 default)' },
  { file: 'dashboard.css', selector: '.ryozan-books-page .ryozan-books-calendar > strong', reason: 'books calendar and list paint var(--surface) on the books page; ink follows it (15.75:1 shop, 14.52:1 default)' },
  { file: 'dashboard.css', selector: '.ryozan-books-page .ryozan-calendar-grid button', reason: 'calendar day buttons on the books-page var(--surface) card' },
  { file: 'dashboard.css', selector: '.ryozan-books-page .ryozan-book-row strong', reason: 'book rows inside the books-page var(--surface) list' },
  { file: 'dashboard.css', selector: '.ryozan-books-page .ryozan-portal-empty', reason: 'empty state on the books-page var(--surface) card' },
  { file: 'dashboard.css', selector: '.ryozan-books-page .ryozan-calendar-weekdays span', reason: 'weekday labels on the books-page var(--surface) card (was literal #6f5f55, 2.99:1 in default)' },
  { file: 'dashboard.css', selector: '.ryozan-books-page .ryozan-book-row small', reason: 'book-row meta on the books-page var(--surface) list' },
  { file: 'dashboard.css', selector: '.ryozan-hub-workflow-form input', reason: 'input paints its own var(--well); ink follows it' },
  { file: 'dashboard.css', selector: '.ryozan-huddl-booking-prompt > strong', reason: 'booking prompt card paints var(--surface)' },
  { file: 'booking.css', selector: '.bkm-booky-sources strong', reason: 'source chips sit on the var(--surface) sources panel' },
];

function sourceFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, name.name);
    if (name.isDirectory()) out.push(...sourceFiles(p));
    else if (/\.(tsx?|jsx?)$/.test(name.name) && !/\.(test|spec)\./.test(name.name)) out.push(p);
  }
  return out;
}

function resolveImport(from, spec) {
  if (!spec.startsWith('.')) return null;
  const base = join(dirname(from), spec);
  for (const c of [base, `${base}.tsx`, `${base}.ts`, `${base}.jsx`, `${base}.js`, join(base, 'index.tsx'), join(base, 'index.ts')]) {
    if (existsSync(c) && statSync(c).isFile()) return c;
  }
  return null;
}

/** Source files the authenticated routes reach and no public route does. */
export function backofficeFiles() {
  const app = join(appSrc, 'App.tsx');
  const text = readFileSync(app, 'utf8');
  const ids = new Map();
  for (const m of text.matchAll(/(?:const|let)\s+(\w+)\s*=\s*\w*\(\s*\(\)\s*=>\s*import\(\s*['"]([^'"]+)['"]\s*\)/g)) ids.set(m[1], resolveImport(app, m[2]));
  for (const m of text.matchAll(/import\s+(?:(\w+)|\{([^}]+)\})\s+from\s+['"]([^'"]+)['"]/g)) {
    const file = resolveImport(app, m[3]);
    if (m[1]) ids.set(m[1], file);
    if (m[2]) for (const n of m[2].split(',')) { const k = n.trim().split(/\s+as\s+/).pop(); if (k) ids.set(k, file); }
  }
  // The <Route element={<ProtectedRoute />}> block, matched by nesting.
  const start = text.indexOf('<Route element={<ProtectedRoute />}>');
  assert.ok(start > 0, 'App.tsx: ProtectedRoute block not found');
  // Walk <Route …> tags (braces may hold nested JSX such as element={<X />}).
  let depth = 0, end = -1;
  for (let i = start; i < text.length && end < 0;) {
    if (text.startsWith('</Route>', i)) { depth--; i += 8; if (depth === 0) end = i; continue; }
    if (text.startsWith('<Route', i) && /[\s>]/.test(text[i + 6])) {
      let j = i + 6, braces = 0;
      for (; j < text.length; j++) { const ch = text[j]; if (ch === '{') braces++; else if (ch === '}') braces--; else if (ch === '>' && braces === 0) break; }
      if (text[j - 1] !== '/') depth++;
      i = j + 1; continue;
    }
    i++;
  }
  assert.ok(end > start, 'App.tsx: ProtectedRoute block end not found');
  const tags = (s) => new Set([...s.matchAll(/<([A-Z]\w*)/g)].map((m) => m[1]).filter((t) => ids.get(t)));
  const routesStart = text.indexOf('<Routes');
  const inside = tags(text.slice(start, end));
  const outside = tags(text.slice(routesStart, start) + text.slice(end));
  const reach = (roots) => {
    const seen = new Set(); const stack = [...roots].map((t) => ids.get(t)).filter(Boolean);
    while (stack.length) {
      const f = stack.pop(); if (seen.has(f)) continue; seen.add(f);
      const src = readFileSync(f, 'utf8');
      for (const m of src.matchAll(/(?:from\s+|import\(\s*)['"]([^'"]+)['"]/g)) { const r = resolveImport(f, m[1]); if (r && !seen.has(r)) stack.push(r); }
    }
    return seen;
  };
  const dash = reach(inside), pub = reach([...outside].filter((t) => !inside.has(t)));
  return new Set([...dash].filter((f) => !pub.has(f)));
}

/** Class names each file renders (className strings and templates; dynamic suffixes are skipped). */
function classesIn(file) {
  const src = readFileSync(file, 'utf8');
  const out = new Set();
  for (const m of src.matchAll(/className=(?:"([^"]*)"|'([^']*)'|\{\s*`([^`]*)`|\{\s*['"]([^'"]*)['"])/g)) {
    const v = (m[1] ?? m[2] ?? m[3] ?? m[4] ?? '').replace(/\$\{[^}]*\}/g, ' ');
    for (const c of v.split(/\s+/)) if (/^[a-zA-Z][\w-]*$/.test(c)) out.add(c);
  }
  // String literals inside className={cond ? 'a' : 'b'} and class lists.
  for (const m of src.matchAll(/className=\{[^}]*\}/g)) for (const s of m[0].matchAll(/['"`]([\w -]+)['"`]/g)) for (const c of s[1].split(/\s+/)) if (/^[a-zA-Z][\w-]*$/.test(c)) out.add(c);
  return out;
}

export function backofficeClasses() {
  const office = backofficeFiles();
  const inOffice = new Set(), elsewhere = new Set();
  for (const f of sourceFiles(appSrc)) {
    if (f.includes(`${'/'}desktop-portal${'/'}`)) continue; // own scoped vocabulary (source.css)
    for (const c of classesIn(f)) (office.has(f) ? inOffice : elsewhere).add(c);
  }
  return new Set([...inOffice].filter((c) => !elsewhere.has(c)).concat(SCOPE_ROOTS));
}

export function backofficeViolations() {
  const office = backofficeClasses();
  const exceptions = new Set(BACKOFFICE_EXCEPTIONS.map((e) => `${e.file}|${e.selector}`));
  const out = [];
  for (const name of readdirSync(stylesDir).filter((n) => n.endsWith('.css'))) {
    const css = readFileSync(join(stylesDir, name), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const sel = m[1].trim().replace(/\s+/g, ' ');
      if (!sel || sel.startsWith('@') || /:root|\[data-theme/.test(sel)) continue;
      const hits = [];
      const color = /(?:^|;)\s*color\s*:\s*var\(--([\w-]+)/.exec(m[2]);
      if (color && THEME_FOLLOWING_TEXT.includes(color[1])) hits.push(`var(--${color[1]})`);
      for (const d of m[2].matchAll(/(?:^|;)\s*([\w-]+)\s*:([^;]*)/g)) {
        for (const t of d[2].matchAll(/var\(--([\w-]+)/g)) {
          if (THEME_FOLLOWING_GOLD.includes(t[1])) hits.push(`${d[1]}: var(--${t[1]})`);
          // cream and accent-cream also follow the theme as fills, borders and accents.
          else if (d[1] !== 'color' && !d[1].startsWith('--') && THEME_FOLLOWING_FILL.includes(t[1])) hits.push(`${d[1]}: var(--${t[1]})`);
        }
      }
      // A local custom property that wraps a theme-following text token (for example
      // --huddl-ink: var(--ink), or a var() fallback) carries the theme in by indirection.
      for (const d of m[2].matchAll(/(?:^|;)\s*(--[\w-]+)\s*:([^;]*)/g)) {
        for (const t of d[2].matchAll(/var\(--([\w-]+)/g)) if (THEME_FOLLOWING_TEXT.includes(t[1])) hits.push(`${d[1]}: var(--${t[1]})`);
      }
      if (!hits.length) continue;
      for (const part of splitTop(sel)) {
        const classes = [...part.matchAll(/\.([\w-]+)/g)].map((c) => c[1]);
        if (!classes.some((c) => office.has(c))) continue;
        if (exceptions.has(`${name}|${part}`)) continue;
        for (const h of hits) out.push(`${name} ${part} -> ${h}`);
      }
    }
  }
  // Inline styles and component-scoped CSS in back-office source. Files that render
  // inside the desktop portal's shadow root are left out: its host pins every one of
  // these tokens to a constant (see the desktop portal test).
  for (const f of [...backofficeFiles()].sort()) {
    if (f.includes(`${'/'}desktop-portal${'/'}`) || SHADOW_PINNED_FILES.some((p) => f.endsWith(p))) continue;
    const src = readFileSync(f, 'utf8');
    for (const t of src.matchAll(/var\(--([\w-]+)/g)) if (THEME_FOLLOWING_GOLD.includes(t[1]) || THEME_FOLLOWING_TEXT.includes(t[1])) out.push(`${f.slice(appSrc.length + 1)} -> var(--${t[1]})`);
  }
  return out;
}

test('back office stays dark: no theme-following text or gold tokens in portal or dashboard rules', { skip: !existsSync(join(appSrc, 'App.tsx')) && 'app/src not present (packed tarball)' }, () => {
  for (const n of [...THEME_FOLLOWING_TEXT, ...BACKOFFICE_TEXT]) assert.ok(byName.has(n), `unknown colour token ${n}`);
  for (const n of BACKOFFICE_TEXT) {
    const per = themes.map((t) => raw(n, t));
    assert.equal(new Set(per).size, 1, `${n} must be constant across themes to be back-office text`);
  }
  for (const n of THEME_FOLLOWING_TEXT) assert.ok(new Set(themes.map((t) => raw(n, t))).size > 1, `${n} is listed as theme-following but is constant`);
  for (const n of THEME_FOLLOWING_GOLD) assert.ok(new Set(themes.map((t) => raw(n, t))).size > 1, `${n} is listed as theme-following but is constant`);
  for (const n of BACKOFFICE_GOLD) assert.equal(new Set(themes.map((t) => raw(n, t))).size, 1, `${n} must be constant across themes for the back office`);
  const v = backofficeViolations();
  assert.deepEqual(v, [], `theme-following text or gold inside the back office (text: ${BACKOFFICE_TEXT.join(', ')}; gold: ${BACKOFFICE_GOLD.join(', ')}):\n${v.join('\n')}`);
});

// ── Fixed dark shells (owner ruling 2026-11-11) ──────────────────────────────────
// Huddle and the desktop portal paint their own shell surfaces. Each must resolve to
// the same dark colour in every theme, and their text tokens to the same light one.
export const FIXED_DARK_SHELLS = [
  { file: 'dashboard.css', selector: '.huddl-shell', surfaces: ['background', '--huddl-surface', '--huddl-surface-strong'], text: ['--huddl-ink', '--huddl-muted', '--huddl-accent'] },
];

function declarations(file, selector) {
  const css = readFileSync(join(stylesDir, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  const out = new Map();
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (m[1].trim().replace(/\s+/g, ' ') !== selector) continue;
    for (const d of m[2].matchAll(/(?:^|;)\s*([\w-]+)\s*:([^;]*)/g)) out.set(d[1], d[2].trim());
  }
  return out;
}

/** The one colour a value resolves to in every theme (light text), or null if it varies or cannot resolve. */
function fixedColour(value) {
  const per = themes.map((t) => stopsOf(value, t));
  if (per.some((s) => !s || s.length !== 1)) return null;
  return new Set(per.map((s) => s[0].slice(0, 3).map(Math.round).join())).size === 1 ? per[0][0] : null;
}

test('Huddle shell surfaces are constant dark and its text constant light in every theme', { skip: !existsSync(stylesDir) && 'app/src/styles not present (packed tarball)' }, () => {
  for (const shell of FIXED_DARK_SHELLS) {
    const decl = declarations(shell.file, shell.selector);
    assert.ok(decl.size, `${shell.file} ${shell.selector} not found`);
    const surfaces = [];
    for (const p of shell.surfaces) {
      assert.ok(decl.has(p), `${shell.selector} must declare ${p}`);
      const dark = fixedDark(decl.get(p));
      assert.ok(dark, `${shell.selector} ${p}: ${decl.get(p)} must be the same dark colour in every theme`);
      surfaces.push(...dark);
    }
    for (const p of shell.text) {
      assert.ok(decl.has(p), `${shell.selector} must declare ${p}`);
      const fg = fixedColour(decl.get(p));
      assert.ok(fg, `${shell.selector} ${p}: ${decl.get(p)} must be the same colour in every theme`);
      for (const bg of surfaces) {
        const k = ratio(fg, bg);
        assert.ok(k >= 4.5, `${shell.selector} ${p} on its own surface ${bg.slice(0, 3)}: ${k.toFixed(2)}:1 < 4.5`);
      }
    }
  }
});

// The desktop portal (app/src/components/desktop-portal) is canon-locked: source.css
// is extracted from the frozen canon and carries its own [data-theme="shop"] block.
// It renders in a shadow root, so that block applies only if something inside the
// shadow tree carries data-theme; the host pins every surface and text token to the
// theme-constant --color-* primitives. This test holds both halves without editing canon.
const portalDir = join(appSrc, 'components', 'desktop-portal');
export const PORTAL_SURFACES = ['--ground', '--ground-2', '--surface', '--surface-2'];
export const PORTAL_TEXT = ['--ink', '--ink-dim', '--ink-mute', '--ink-ghost', '--text', '--text-primary', '--accent-cream'];

test('desktop portal stays dark in every theme: shadow host pins, no theme switch inside', { skip: !existsSync(portalDir) && 'desktop portal not present (packed tarball)' }, () => {
  const host = readFileSync(join(portalDir, 'NativeDesktopPortal.tsx'), 'utf8');
  assert.ok(/attachShadow\(/.test(host), 'desktop portal must render in a shadow root');
  const pin = /`:host\{([^`]*?)\}/.exec(host);
  assert.ok(pin, 'NativeDesktopPortal.tsx: :host token pin not found');
  const decl = new Map([...pin[1].matchAll(/(--[\w-]+):([^;]*)/g)].map((d) => [d[1], d[2].trim()]));
  const surfaces = [];
  for (const p of PORTAL_SURFACES) {
    assert.ok(decl.has(p), `desktop portal :host must pin ${p}`);
    const dark = fixedDark(decl.get(p));
    assert.ok(dark, `desktop portal ${p}: ${decl.get(p)} must be the same dark colour in every theme`);
    surfaces.push(...dark);
  }
  for (const p of PORTAL_TEXT) {
    assert.ok(decl.has(p), `desktop portal :host must pin ${p}`);
    const fg = fixedColour(decl.get(p));
    assert.ok(fg, `desktop portal ${p}: ${decl.get(p)} must be the same colour in every theme`);
    for (const bg of surfaces) assert.ok(ratio(fg, bg) >= 4.5, `desktop portal ${p} on ${bg.slice(0, 3)}: ${ratio(fg, bg).toFixed(2)}:1`);
  }
  // Nothing in the portal may switch the theme, or source.css's shop block would apply.
  const switches = sourceFiles(portalDir).filter((f) => /data-theme|dataset\.theme/.test(readFileSync(f, 'utf8')));
  assert.deepEqual(switches, [], 'desktop portal sources must not set data-theme');
  assert.ok(!/:host\(\s*\[data-theme|:host-context\(/.test(readFileSync(join(portalDir, 'source.css'), 'utf8')), 'source.css must not theme the shadow host from outside');
});

// The back-office roots pin the theme-following gold (owner ruling 2026-11-11), so
// @ryozan/ui components and global element rules that paint gold inline stay constant.
export const BACKOFFICE_GOLD_PIN = { file: 'dashboard.css', selector: '.app-view, .ryozan-mobile-portal-home', tokens: ['--gold', '--gold-text', '--gold-fill', '--gold-decor', '--on-gold', '--gold-ink', '--upload-ink'] };

test('back-office roots pin gold to a constant in every theme, readable on the dark shell', { skip: !existsSync(stylesDir) && 'app/src/styles not present (packed tarball)' }, () => {
  const decl = declarations(BACKOFFICE_GOLD_PIN.file, BACKOFFICE_GOLD_PIN.selector);
  assert.ok(decl.size, `${BACKOFFICE_GOLD_PIN.selector} gold pin not found`);
  const shell = ['color-ground', 'color-surface', 'color-surface-2'].map((n) => col(n, themes[0]));
  for (const p of BACKOFFICE_GOLD_PIN.tokens) {
    assert.ok(decl.has(p), `back-office roots must pin ${p}`);
    const c = fixedColour(decl.get(p));
    assert.ok(c, `${p}: ${decl.get(p)} must be the same colour in every theme`);
    if (p === '--on-gold' || p === '--gold-ink') {
      const gold = fixedColour(decl.get('--gold'));
      assert.ok(ratio(c, gold) >= 4.5, `${p} on pinned gold: ${ratio(c, gold).toFixed(2)}:1`);
    } else for (const bg of shell) assert.ok(ratio(c, bg) >= 4.5, `${p} on the dark shell: ${ratio(c, bg).toFixed(2)}:1`);
  }
});

// ── Contrast sweep (owner ruling 2026-11-11, "fix the rest") ─────────────────────
// Each pair names the rule that sets the text colour and the rule that paints its
// ground; every stop of the ground (gradients composited over bg) must read 4.5:1
// in every theme. These are the pairs the render pass found under the floor.
export const SWEEP_PAIRS = [
  { file: 'dashboard.css', fg: '.ryozan-hub-upload-toggle > span > small', bg: '.ryozan-hub-upload-toggle', why: 'hub Publish toggle label (was 2.11:1)' },
  { file: 'dashboard.css', fg: '.ryozan-hub-upload-stack .ryozan-club-actions-inner > button', bg: '.ryozan-hub-upload-stack .ryozan-club-actions-inner', why: 'hub upload stack action labels (was 4.16:1)' },
  { file: 'dashboard.css', fg: '.ryozan-hub-options > strong, .ryozan-hub-pos > strong', bg: '.ryozan-hub-options, .ryozan-hub-pos', why: '+POS tile title (was 3.19:1)' },
  { file: 'dashboard.css', fg: '.ryozan-hub-options > span, .ryozan-hub-pos > span', bg: '.ryozan-hub-options, .ryozan-hub-pos', why: '+POS tile detail (was 2.62:1)' },
  { file: 'artist-scroll.css', fg: '.scroll-stage-gate-btn', bg: '.scroll-stage-gate-btn', why: 'Accept Section button (was 1.12:1 in shop)' },
];

test('sweep pairs read 4.5:1 in every theme', { skip: !existsSync(stylesDir) && 'app/src/styles not present (packed tarball)' }, () => {
  for (const p of SWEEP_PAIRS) {
    const fg = declarations(p.file, p.fg).get('color');
    const bgDecl = declarations(p.file, p.bg);
    const bg = bgDecl.get('background') ?? bgDecl.get('background-color');
    assert.ok(fg && bg, `${p.file}: ${p.fg} colour or ${p.bg} background not found`);
    for (const theme of themes) {
      const fgStops = stopsOf(fg, theme), bgStops = stopsOf(bg, theme);
      assert.ok(fgStops && bgStops, `${p.why}: cannot resolve ${fg} on ${bg}`);
      for (const b of bgStops) {
        const k = ratio(fgStops[0], b);
        assert.ok(k >= 4.5, `${p.why} (${theme}): ${k.toFixed(2)}:1 on ${b.slice(0, 3).map(Math.round)}`);
      }
    }
  }
});

// Coral count chips in the desktop portal's own workspace templates (not the frozen
// shell, see below). They render in the portal's shadow root, so each colour resolves
// through the :host pin in NativeDesktopPortal.tsx: ink and fill must both be tokens
// (no hex literals, AGENTS.md), and the ink must read 4.5:1 on the fill in every theme.
const coralTemplates = ['home-workspace.html', 'booky-workspace.html'];
test('coral count chips read 4.5:1 through the portal pins', { skip: !existsSync(portalDir) && 'desktop portal not present (packed tarball)' }, () => {
  const host = readFileSync(join(portalDir, 'NativeDesktopPortal.tsx'), 'utf8');
  const pin = new Map([...(/`:host\{([^`]*?)\}/.exec(host)?.[1] ?? '').matchAll(/(--[\w-]+):([^;]*)/g)].map((d) => [d[1], d[2].trim()]));
  const resolve = (value) => { const v = /^var\((--[\w-]+)\)$/.exec(value); return v && pin.has(v[1]) ? fixedColour(pin.get(v[1])) : null; };
  let seen = 0;
  for (const name of coralTemplates) {
    for (const m of readFileSync(join(portalDir, name), 'utf8').matchAll(/style="([^"]*background:var\(--accent-coral\)[^"]*)"/g)) {
      const ink = /(?:^|;)color:([^;]+)/.exec(m[1]);
      if (!ink) continue;
      seen++;
      assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(ink[1]), `${name}: coral chip ink ${ink[1]} is a hex literal; use a token`);
      const fg = resolve(ink[1].trim()), bg = resolve('var(--accent-coral)');
      assert.ok(fg && bg, `${name}: ${ink[1]} or accent-coral does not resolve through the :host pin`);
      const k = ratio(fg, bg);
      assert.ok(k >= 4.5, `${name}: ${ink[1]} on accent-coral reads ${k.toFixed(2)}:1`);
    }
  }
  assert.ok(seen >= 4, `expected the four coral chips and buttons, found ${seen}`);
  // shell.html is left out: it is extracted from the frozen canon (extract-source.mjs)
  // and its unread badge (4.48:1) changes upstream, with a canon version bump.
});

// Booky's accent is its nav and glyph colour on the dark mobile portal.
test('Booky accent reads 4.5:1 on the dark portal', { skip: !existsSync(join(appSrc, 'pages', 'dashboard', 'MobilePortalHome.tsx')) && 'app/src not present (packed tarball)' }, () => {
  const src = readFileSync(join(appSrc, 'pages', 'dashboard', 'MobilePortalHome.tsx'), 'utf8');
  const accent = /const BOOKY_ACCENT = '([^']+)'/.exec(src);
  assert.ok(accent, 'BOOKY_ACCENT not found');
  for (const theme of themes) {
    const fg = stopsOf(accent[1], theme);
    assert.ok(fg, `BOOKY_ACCENT ${accent[1]} must resolve to a token`);
    for (const g of ['color-ground', 'color-surface']) assert.ok(ratio(fg[0], col(g, theme)) >= 4.5, `BOOKY_ACCENT on ${g} (${theme}): ${ratio(fg[0], col(g, theme)).toFixed(2)}:1`);
  }
});

// SessionPass: the vertical brand letters sit on the oxblood ribbon gradient.
test('SessionPass ribbon letters read 4.5:1 on both ribbon stops', () => {
  const src = readFileSync(join(here, '..', 'src', 'components', 'SessionPass.tsx'), 'utf8');
  const ribbon = /linear-gradient\(180deg,(#[0-9a-fA-F]{6}),(#[0-9a-fA-F]{6})\)/.exec(src);
  const letters = /color: '(#[0-9a-fA-F]{6})' \}\}>\s*\{brand\.split/.exec(src);
  assert.ok(ribbon && letters, 'SessionPass ribbon or letters not found');
  for (const stop of [ribbon[1], ribbon[2]]) {
    const k = ratio(parse(letters[1]), parse(stop));
    assert.ok(k >= 4.5, `SessionPass letters ${letters[1]} on ${stop}: ${k.toFixed(2)}:1`);
  }
});

// Colours may be CSS variables (agent accents are var(--token)), so no source may build
// a translucent colour by appending hex alpha to an interpolated colour: `${accent}22`
// becomes invalid CSS. Use color-mix(in srgb, <colour> N%, transparent).
test('no hex alpha suffix on interpolated colours', () => {
  const roots = [join(here, '..', 'src'), appSrc].filter((d) => existsSync(d));
  const hits = [];
  for (const dir of roots) for (const f of sourceFiles(dir)) {
    for (const m of readFileSync(f, 'utf8').matchAll(/\$\{([^}]*(?:accent|color|colour|tone|tint)[^}]*)\}[0-9a-fA-F]{2}(?![0-9a-zA-Z_])/gi)) hits.push(`${f.slice(f.indexOf('/src/') + 1)}: \${${m[1]}}…`);
  }
  assert.deepEqual(hits, [], `hex alpha appended to an interpolated colour (use color-mix):\n${hits.join('\n')}`);
});

// ── Component contrast gaps (owner ruling 2026-11-11, "fix the rest") ────────────
const uiSrc = join(here, '..', 'src');
/** A @ryozan/ui `color.<key>` resolved through src/tokens/index.ts to its token. */
function uiColor(key, theme) {
  const idx = readFileSync(join(uiSrc, 'tokens', 'index.ts'), 'utf8');
  const block = idx.slice(idx.indexOf('export const color = {'));
  const m = new RegExp(`\\n\\s*${key}:\\s*'var\\(--([\\w-]+)\\)'`).exec(block.slice(0, block.indexOf('\n};')));
  assert.ok(m, `color.${key} not found in tokens/index.ts`);
  return col(m[1], theme);
}
const rgbaOf = (s) => { const p = /rgba\(([^)]*)\)/.exec(s)[1].split(',').map((x) => x.trim()); return [+p[0], +p[1], +p[2], +p[3]]; };

test('SessionPass date rail labels read 4.5:1 on the sage rail', () => {
  const src = readFileSync(join(uiSrc, 'components', 'SessionPass.tsx'), 'utf8');
  const rail = /background: color\.(\w+),\s*borderRight/.exec(src);
  assert.ok(rail, 'date rail background not found');
  for (const field of ['month', 'weekday']) {
    const fg = new RegExp(`color: color\\.(\\w+) \\}\\}>\\{date\\.${field}\\}`).exec(src);
    assert.ok(fg, `date.${field} colour not found`);
    for (const theme of themes) {
      const k = ratio(uiColor(fg[1], theme), uiColor(rail[1], theme));
      assert.ok(k >= 4.5, `date.${field} color.${fg[1]} on color.${rail[1]} (${theme}): ${k.toFixed(2)}:1`);
    }
  }
});

test('ActionCard muted labels read 4.5:1 on the card paper and every wash it paints', () => {
  const src = readFileSync(join(uiSrc, 'components', 'ActionCard.tsx'), 'utf8');
  const paper = parse(/const paper = '(#[0-9a-fA-F]{6})'/.exec(src)[1]);
  const dim = rgbaOf(/const dim = '(rgba\([^)]*\))'/.exec(src)[1]);
  // Translucent card washes up to 12% (heavier fills are controls with their own ink).
  const washes = [...src.matchAll(/background: '(rgba\([^)]*\))'/g)].map((m) => rgbaOf(m[1])).filter((c) => c[3] <= 0.12);
  assert.ok(washes.length >= 5, 'card washes not found');
  for (const g of [paper, ...washes.map((w) => over(w, paper))]) {
    const k = ratio(over(dim, g), g);
    assert.ok(k >= 4.5, `ActionCard dim ${dim} on ${g.slice(0, 3).map(Math.round)}: ${k.toFixed(2)}:1`);
  }
  // The Ready on Stack / MCP label on the washi plate band.
  const plate = parse(/const washiPlate = '(#[0-9a-fA-F]{6})'/.exec(src)[1]);
  const ready = rgbaOf(/color: '(rgba\(18,18,18,[^)]*\))', accentColor: accent/.exec(src)[1]);
  const k = ratio(over(ready, plate), plate);
  assert.ok(k >= 4.5, `ActionCard ready label on the washi plate: ${k.toFixed(2)}:1`);
});

test('AgentTools resting card glyph reads 3:1 on the card back', () => {
  const src = readFileSync(join(uiSrc, 'screens', 'AgentTools.tsx'), 'utf8');
  const css = readFileSync(join(uiSrc, 'tokens', 'scene-compat.css'), 'utf8');
  const cream = (/--cream-rgb:\s*([\d,\s]+);/.exec(readFileSync(join(uiSrc, 'tokens', 'production-only.css'), 'utf8'))[1]).split(',').map(Number);
  const back = /backgroundImage?: 'linear-gradient\(165deg,(#[0-9a-fA-F]{6}),(#[0-9a-fA-F]{6})\)'/.exec(src) ?? /background: 'linear-gradient\(165deg,(#[0-9a-fA-F]{6}),(#[0-9a-fA-F]{6})\)'/.exec(src);
  assert.ok(back, 'card back gradient not found');
  const glyphs = [
    ['inline emblem', /lineHeight: 1, color: 'rgba\(var\(--cream-rgb\),([\d.]+)\)', textShadow/.exec(src)],
    ['.ryozan-agent-card-emblem', /\.ryozan-agent-card-emblem \{[^}]*color: rgba\(var\(--cream-rgb\), ([\d.]+)\)/.exec(css)],
    ['breathe rest', /0%, 100% \{ color: rgba\(var\(--cream-rgb\), ([\d.]+)\)/.exec(css)],
  ];
  for (const [name, m] of glyphs) {
    assert.ok(m, `${name}: resting glyph must be cream at an alpha (found another colour)`);
    for (const stop of [back[1], back[2]]) {
      const g = parse(stop), k = ratio(over([...cream, +m[1]], g), g);
      assert.ok(k >= 3, `${name} on ${stop}: ${k.toFixed(2)}:1`);
    }
  }
});
