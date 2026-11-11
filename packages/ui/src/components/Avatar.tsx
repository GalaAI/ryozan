import * as React from 'react';
import { color, radius } from '../tokens';

export interface AvatarProps extends React.HTMLAttributes<HTMLSpanElement> {
  src?: string;
  /** Fallback initial when src is absent. */
  initial?: string;
  /** px diameter. Interactive avatars must clear the touch minimum. */
  diameter?: number;
  ringColor?: string;
  /** Dashed ring = the rail medallion treatment. */
  dashed?: boolean;
}

/** Round avatar with the gold ring. Presentational only — wrap for a button. */
export function Avatar({ src, initial, diameter = 40, ringColor = color.gold, dashed = false, style, ...rest }: AvatarProps) {
  return (
    <span
      aria-hidden={initial ? undefined : true}
      style={{
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: diameter,
        height: diameter,
        borderRadius: radius.pill,
        border: `2px ${dashed ? 'dashed' : 'solid'} ${ringColor}`,
        backgroundColor: color.smoke,
        backgroundImage: src ? `url(${src})` : undefined,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        color: color.goldText,
        fontFamily: 'var(--font-btn)',
        fontSize: 'var(--text-sm)',
        fontWeight: 700,
        overflow: 'hidden',
        ...style,
      }}
      {...rest}
    >
      {src ? null : initial}
    </span>
  );
}
