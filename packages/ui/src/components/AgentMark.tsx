import * as React from 'react';
import { color } from '../tokens';

export interface AgentMarkProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** The agent's glyph. Agents are identified by glyph + accent, never by emoji. */
  glyph: React.ReactNode;
  accent?: string;
  /** px font size of the glyph. */
  scale?: number;
  /** Mirror the glyph — some agent marks are flipped. */
  flipped?: boolean;
}

/** An agent's emblem: glyph in the agent accent with the layered glow. */
export function AgentMark({ glyph, accent = color.gold, scale = 26, flipped = false, style, ...rest }: AgentMarkProps) {
  return (
    <span
      aria-hidden="true"
      style={{
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        transform: flipped ? 'scaleX(-1)' : undefined,
        fontSize: scale,
        lineHeight: 1,
        color: accent,
        textShadow: '0 0 12px, 0 0 26px',
        ...style,
      }}
      {...rest}
    >
      {glyph}
    </span>
  );
}

export interface AgentFabProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  glyph: React.ReactNode;
  /** Agent name — becomes the accessible label. */
  agentName: string;
  accent?: string;
  flipped?: boolean;
}

/** Floating agent button. Glyph only, no chrome — the glow is the affordance. */
export const AgentFab = React.forwardRef<HTMLButtonElement, AgentFabProps>(function AgentFab(
  { glyph, agentName, accent, flipped, style, ...rest },
  ref,
) {
  const resolvedAccent = accent ?? color.gold;

  return (
    <button
      ref={ref}
      type="button"
      aria-label={`Open ${agentName} chat`}
      title={agentName}
      style={{
        width: 54,
        height: 54,
        boxSizing: 'border-box',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 6,
        border: 0,
        borderRadius: '50%',
        background: 'transparent',
        color: resolvedAccent,
        cursor: 'pointer',
        overflow: 'visible',
        ...style,
      }}
      {...rest}
    >
      <AgentMark glyph={glyph} accent={resolvedAccent} scale={44} flipped={flipped} style={{ width: 42, height: 42 }} />
    </button>
  );
});
