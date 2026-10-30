import * as React from 'react';
import { color, font } from '../tokens';

export type SectionLabelTone = 'muted' | 'gold' | 'ghost' | 'ink';

export interface SectionLabelProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: SectionLabelTone;
  /** Mono label sizes in the scenes: 'sm' 10.5px-equivalent, 'md' 12.5px, 'lg' 14.5px. */
  scale?: 'sm' | 'md' | 'lg';
  children?: React.ReactNode;
}

const tones: Record<SectionLabelTone, string> = {
  muted: color.textMuted,
  gold: color.gold,
  ghost: color.textDim,
  ink: color.text,
};

const scales = { sm: '0.66rem', md: '0.78rem', lg: '0.9rem' } as const;

/**
 * The mono, uppercase, wide-tracked section label — the most repeated text
 * element in both portals ("TODAY", "TIME · OPEN SLOTS", "NEW BOOKING").
 * Never use below 'sm': the brand forbids tiny all-caps mono.
 */
export function SectionLabel({ tone = 'muted', scale = 'sm', style, children, ...rest }: SectionLabelProps) {
  return (
    <span
      style={{
        fontFamily: font.btn,
        fontSize: scales[scale],
        fontWeight: 700,
        letterSpacing: '0.18em',
        textTransform: 'uppercase',
        color: tones[tone],
        lineHeight: 1.5,
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  );
}
