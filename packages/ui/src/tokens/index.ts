/**
 * Token handles for @bttco/ui.
 *
 * Every value is a `var(--token)` reference — NOT a literal. The literals
 * live in exactly one place (tokens.css, derived from the _ds bundle), so a
 * token change propagates without touching component source. Components in
 * this package must reference `token.*`, never a hex or px literal.
 */

export const color = {
  gold: 'var(--gold)',
  goldVibrant: 'var(--gold-vibrant)',
  goldGlow: 'var(--gold-glow)',
  tan: 'var(--tan)',
  tanGlow: 'var(--tan-glow)',
  cream: 'var(--cream)',

  bg: 'var(--bg)',
  bgDark: 'var(--bg-dark)',
  bgSurface: 'var(--bg-surface)',
  bgSurfaceElevated: 'var(--bg-surface-elevated)',
  bgElevated: 'var(--bg-elevated)',
  bgHover: 'var(--bg-hover)',
  bgActive: 'var(--bg-active)',
  sidebarBg: 'var(--sidebar-bg)',

  panel: 'var(--panel)',
  panelHover: 'var(--panel-hover)',
  panelRaised: 'var(--panel-raised)',
  panelBorder: 'var(--panel-border)',
  panelBorderHover: 'var(--panel-border-hover)',

  text: 'var(--text)',
  textSecondary: 'var(--text-secondary)',
  textMuted: 'var(--text-muted)',
  textDim: 'var(--text-dim)',
  boneWhite: 'var(--bone-white)',

  sage: 'var(--alt-sage)',
  spring: 'var(--alt-spring)',
  pink: 'var(--alt-pink)',
  sky: 'var(--alt-sky)',
  smoke: 'var(--alt-smoke)',
  oxblood: 'var(--alt-oxblood)',

  ok: 'var(--ok)',
  pending: 'var(--pending)',
  danger: 'var(--danger)',
  info: 'var(--info)',

  borderSubtle: 'var(--border-subtle)',
  borderActive: 'var(--border-active)',
} as const;

export const font = {
  heading: 'var(--font-heading)',
  display: 'var(--font-display)',
  body: 'var(--font-body)',
  accent: 'var(--font-accent)',
  mono: 'var(--font-mono)',
  btn: 'var(--font-btn)',
  title: 'var(--font-title)',
  script: 'var(--font-script)',
} as const;

export const size = {
  xs: 'var(--text-xs)',
  sm: 'var(--text-sm)',
  base: 'var(--text-base)',
  lg: 'var(--text-lg)',
  xl: 'var(--text-xl)',
  xl2: 'var(--text-2xl)',
  xl3: 'var(--text-3xl)',
  xl4: 'var(--text-4xl)',
} as const;

export const space = {
  s0: 'var(--space-0)', s1: 'var(--space-1)', s2: 'var(--space-2)',
  s3: 'var(--space-3)', s4: 'var(--space-4)', s5: 'var(--space-5)',
  s6: 'var(--space-6)', s8: 'var(--space-8)', s10: 'var(--space-10)',
  s12: 'var(--space-12)', s16: 'var(--space-16)', s20: 'var(--space-20)',
} as const;

export const radius = {
  sm: 'var(--radius-sm)',
  md: 'var(--radius-md)',
  lg: 'var(--radius-lg)',
  xl: 'var(--radius-xl)',
  pill: 'var(--radius-pill)',
} as const;

export const shadow = {
  sm: 'var(--shadow-sm)',
  md: 'var(--shadow-md)',
  lg: 'var(--shadow-lg)',
  gold: 'var(--shadow-gold)',
  glow: 'var(--shadow-glow)',
  lift: 'var(--shadow-lift)',
} as const;

export const motion = {
  fast: 'var(--transition-fast)',
  base: 'var(--transition-base)',
  slow: 'var(--transition-slow)',
} as const;

export const layout = {
  touchMin: 'var(--touch-min)',
} as const;

export const token = { color, font, size, space, radius, shadow, motion, layout } as const;
export type Token = typeof token;
