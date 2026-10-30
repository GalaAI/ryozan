import * as React from 'react';
import { color } from '../tokens';

export interface WordmarkProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Vertical stack (nav rail, pass stub) or horizontal. */
  orientation?: 'vertical' | 'horizontal';
  /** px cap height of the letters. */
  fontSize?: number;
  text?: string;
}

/** The product wordmark set in KC Tallboy. Decorative, aria-hidden, never a heading. */
export function Wordmark({ orientation = 'vertical', fontSize = 46, text = 'RYOZAN', style, ...rest }: WordmarkProps) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'flex',
        flexDirection: orientation === 'vertical' ? 'column' : 'row',
        alignItems: 'center',
        justifyContent: 'center',
        lineHeight: 0.8,
        fontFamily: 'var(--font-heading)',
        fontSize,
        letterSpacing: '0.04em',
        color: color.tan,
        pointerEvents: 'none',
        userSelect: 'none',
        ...style,
      }}
      {...rest}
    >
      {text.split('').map((ch, i) => (
        <span key={i}>{ch}</span>
      ))}
    </span>
  );
}
