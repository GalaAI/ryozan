import * as React from 'react';
import { color, font, radius, motion } from '../tokens';

export interface ChipProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'aria-pressed'> {
  selected?: boolean;
  /** Accent used when selected. Pass a token handle. */
  accent?: string;
  children?: React.ReactNode;
}

/**
 * The selectable pill: time slots, session types, artists, view-as roles.
 * Renders as a real toggle button with aria-pressed — do not substitute a div.
 */
export const Chip = React.forwardRef<HTMLButtonElement, ChipProps>(function Chip(
  { selected = false, accent = color.gold, style, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-pressed={selected}
      style={{
        minHeight: '32px',
        padding: '0 var(--space-3)',
        borderRadius: radius.pill,
        border: `1px solid ${selected ? accent : color.panelBorder}`,
        background: selected ? color.bgActive : 'transparent',
        color: selected ? color.text : color.textMuted,
        cursor: 'pointer',
        fontFamily: font.btn,
        fontSize: 'var(--text-xs)',
        fontWeight: 700,
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        transition: `border-color ${motion.fast}, color ${motion.fast}, background ${motion.fast}`,
        ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
});

export interface ChipGroupProps extends React.HTMLAttributes<HTMLDivElement> {
  label?: React.ReactNode;
  children?: React.ReactNode;
}

/** Wrapping row of Chips with an optional mono label above. */
export function ChipGroup({ label, style, children, ...rest }: ChipGroupProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', ...style }} {...rest}>
      {label ? (
        <span
          style={{
            fontFamily: font.btn,
            fontSize: 'var(--text-xs)',
            fontWeight: 700,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            color: color.textMuted,
          }}
        >
          {label}
        </span>
      ) : null}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-1)' }}>{children}</div>
    </div>
  );
}
