import * as React from 'react';
import { color, font, radius, shadow } from '../tokens';

export interface TopBarProps extends React.HTMLAttributes<HTMLElement> {
  title: React.ReactNode;
  /** Left of the actions — e.g. the exit-preview pill. */
  notice?: React.ReactNode;
  /** Trailing controls: inbox, avatar. */
  actions?: React.ReactNode;
}

/** Desktop/tablet header bar. Title left, notice centre-right, actions right. */
export function TopBar({ title, notice, actions, style, ...rest }: TopBarProps) {
  return (
    <header
      style={{
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-3)',
        flexWrap: 'nowrap',
        minHeight: 68,
        padding: '0 var(--space-5)',
        border: `2px solid ${color.panelBorder}`,
        borderRadius: radius.md,
        background: color.bgDark,
        boxShadow: shadow.lg,
        position: 'relative',
        zIndex: 30,
        boxSizing: 'border-box',
        ...style,
      }}
      {...rest}
    >
      <span
        style={{
          flex: '1 1 auto',
          minWidth: 0,
          fontFamily: font.display,
          fontWeight: 800,
          fontSize: 'var(--text-xl)',
          lineHeight: 1,
          letterSpacing: '0.01em',
          textTransform: 'uppercase',
          color: color.text,
          whiteSpace: 'nowrap',
        }}
      >
        {title}
      </span>
      {notice ? <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>{notice}</div> : null}
      {actions ? <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>{actions}</div> : null}
    </header>
  );
}
