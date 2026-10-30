import * as React from 'react';
import { color, radius, motion, layout } from '../tokens';

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required — the button has no text. */
  label: string;
  /** Numeric badge, top-right. Hidden when undefined or 0. */
  badge?: number;
  shape?: 'card' | 'pill';
  children?: React.ReactNode;
}

/** Square chrome button for topbar icons (inbox, close, settings). */
export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, badge, shape = 'card', style, children, ...rest },
  ref,
) {
  return (
    <span style={{ position: 'relative', flexShrink: 0, display: 'inline-flex' }}>
      <button
        ref={ref}
        type="button"
        aria-label={badge ? `${label}, ${badge} unread` : label}
        title={label}
        style={{
          width: layout.touchMin,
          height: layout.touchMin,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 0,
          border: `1px solid ${color.panelBorder}`,
          borderRadius: shape === 'pill' ? radius.pill : radius.md,
          background: color.bgDark,
          color: color.text,
          cursor: 'pointer',
          transition: `color ${motion.base}, border-color ${motion.base}`,
          ...style,
        }}
        {...rest}
      >
        {children}
      </button>
      {badge ? (
        <span
          aria-hidden="true"
          style={{
            position: 'absolute',
            top: 'calc(var(--space-1) * -1)',
            right: 'calc(var(--space-1) * -1)',
            width: '24px',
            minWidth: '24px',
            maxWidth: '24px',
            height: '18px',
            padding: 0,
            boxSizing: 'border-box',
            borderRadius: radius.pill,
            background: color.pink,
            color: color.smoke,
            fontFamily: 'var(--font-btn)',
            overflow: 'hidden',
            fontSize: '9px',
            fontWeight: 700,
            fontVariantNumeric: 'tabular-nums',
            letterSpacing: '-0.04em',
            lineHeight: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {badge > 99 ? '99+' : badge}
        </span>
      ) : null}
    </span>
  );
});
