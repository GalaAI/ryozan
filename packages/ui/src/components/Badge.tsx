import * as React from 'react';
import { color, font, radius } from '../tokens';

export type BadgeTone = 'ok' | 'pending' | 'danger' | 'info' | 'gold' | 'neutral';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  /** Solid capsule (over imagery) vs hairline outline. */
  filled?: boolean;
  children?: React.ReactNode;
}

const tones: Record<BadgeTone, string> = {
  ok: color.spring,
  pending: color.pending,
  danger: color.pink,
  info: color.sky,
  gold: color.gold,
  neutral: color.textMuted,
};

/** Status pill. Solid capsules over imagery, outline on panels. */
export function Badge({ tone = 'neutral', filled = false, style, children, ...rest }: BadgeProps) {
  const c = tones[tone];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        minHeight: 'var(--space-6)',
        padding: '0 var(--space-3)',
        borderRadius: radius.pill,
        border: filled ? 0 : `1px solid ${c}`,
        background: filled ? c : 'transparent',
        color: filled ? color.smoke : c,
        fontFamily: font.btn,
        fontSize: 'var(--text-xs)',
        fontWeight: 700,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  );
}

export interface StatusTextProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  /** Show the leading ● dot. */
  dot?: boolean;
  /** Let a long line (an error sentence) wrap inside its panel instead of holding one line. */
  wrap?: boolean;
  children?: React.ReactNode;
}

/** Bare status line — "● Confirmed". No capsule, sits inside panel headers. */
export function StatusText({ tone = 'ok', dot = true, wrap = false, style, children, ...rest }: StatusTextProps) {
  return (
    <span
      style={{
        fontFamily: font.btn,
        fontSize: 'var(--text-xs)',
        fontWeight: 700,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
        color: tones[tone],
        whiteSpace: wrap ? 'normal' : 'nowrap',
        ...style,
      }}
      {...rest}
    >
      {dot ? '● ' : null}
      {children}
    </span>
  );
}
