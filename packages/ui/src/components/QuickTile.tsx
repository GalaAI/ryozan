import * as React from 'react';
import { color, font, radius } from '../tokens';

export interface QuickTileProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  label: React.ReactNode;
  /** Big display value ("Day 3"). */
  value: React.ReactNode;
  /** Filled tile background. Pass a token handle. */
  background?: string;
  valueColor?: string;
  labelColor?: string;
}

/** Two-up home tile: mono label over a display value. Always a button. */
export const QuickTile = React.forwardRef<HTMLButtonElement, QuickTileProps>(function QuickTile(
  { label, value, background = color.panel, valueColor = color.pink, labelColor = color.text, style, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-1)',
        padding: 'var(--space-4)',
        minHeight: 'var(--touch-min)',
        border: 0,
        borderRadius: radius.lg,
        background,
        cursor: 'pointer',
        textAlign: 'left',
        overflow: 'hidden',
        ...style,
      }}
      {...rest}
    >
      <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.22em', textTransform: 'uppercase', color: labelColor }}>
        {label}
      </span>
      <span style={{ fontFamily: font.display, fontWeight: 800, fontSize: 'var(--text-xl)', lineHeight: 1, textTransform: 'uppercase', color: valueColor }}>
        {value}
      </span>
    </button>
  );
});
