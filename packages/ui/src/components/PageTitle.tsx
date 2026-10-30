import * as React from 'react';
import { color, font } from '../tokens';

export interface PageTitleProps extends React.HTMLAttributes<HTMLElement> {
  /** Mono meta line under the title (e.g. "SAT AUG 1 // 2 APPOINTMENTS TODAY"). */
  meta?: React.ReactNode;
  /** Trailing slot — the avatar/account button in both scenes. */
  action?: React.ReactNode;
  as?: 'h1' | 'h2' | 'div';
  /** Mobile runs 33px; desktop topbar runs fluid. */
  compact?: boolean;
  children?: React.ReactNode;
}

/** The page header: display title, optional mono meta, optional trailing action. */
export function PageTitle({ meta, action, as = 'h1', compact = false, style, children, ...rest }: PageTitleProps) {
  const Tag = as;
  return (
    <div style={{ position: 'relative', zIndex: 3, display: 'flex', flexDirection: 'column', gap: 'var(--space-1)', ...style }} {...rest}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
        <Tag
          style={{
            flex: 1,
            minWidth: 0,
            margin: 0,
            fontFamily: font.display,
            fontWeight: 800,
            fontSize: compact ? 'var(--text-xl)' : 'var(--text-2xl)',
            lineHeight: 1,
            letterSpacing: '0.01em',
            textTransform: 'uppercase',
            color: color.text,
          }}
        >
          {children}
        </Tag>
        {action ? <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>{action}</div> : null}
      </div>
      {meta ? (
        <span
          style={{
            fontFamily: font.btn,
            fontSize: 'var(--text-xs)',
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            color: color.textDim,
          }}
        >
          {meta}
        </span>
      ) : null}
    </div>
  );
}
