import * as React from 'react';
import { color, font, radius } from '../tokens';
import { AgentMark } from './AgentMark';

export interface ChatBubbleProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 'agent' bubbles carry the mark and notch left; 'user' align right. */
  from: 'agent' | 'user';
  /** Agent glyph — required for from="agent". */
  glyph?: React.ReactNode;
  accent?: string;
  children?: React.ReactNode;
}

/** A single chat line. The notched corner faces the speaker. */
export function ChatBubble({ from, glyph, accent = color.gold, style, children, ...rest }: ChatBubbleProps) {
  const isAgent = from === 'agent';
  return (
    <div
      data-chat-from={from}
      style={{
        display: 'flex',
        gap: 'var(--space-2)',
        alignItems: 'flex-start',
        maxWidth: isAgent ? '84%' : '78%',
        minWidth: 0,
        alignSelf: isAgent ? 'flex-start' : 'flex-end',
        ...style,
      }}
      {...rest}
    >
      {isAgent && glyph ? <AgentMark glyph={glyph} accent={accent} scale={20} style={{ width: 26, height: 26 }} /> : null}
      <span
        style={{
          fontFamily: font.body,
          fontSize: 'var(--text-sm)',
          lineHeight: 1.55,
          color: isAgent ? color.text : '#22100a',
          border: isAgent ? `1px solid ${accent}` : 0,
          borderRadius: isAgent
            ? `${radius.sm} ${radius.lg} ${radius.lg} ${radius.lg}`
            : `${radius.lg} ${radius.sm} ${radius.lg} ${radius.lg}`,
          padding: 'var(--space-2) var(--space-3)',
          background: isAgent ? color.panel : color.boneWhite,
          textWrap: 'pretty',
          maxWidth: '100%',
        }}
      >
        {children}
      </span>
    </div>
  );
}
