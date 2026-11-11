import * as React from 'react';
import { color, font, radius } from '../tokens';
import { Panel } from './Panel';
import { Button } from './Button';

export interface ScheduleEntry {
  id: string;
  time: React.ReactNode;
  label: React.ReactNode;
  /** Right-side state text ("Confirmed", "Deposit due"). Omitted when actionLabel is set. */
  state?: React.ReactNode;
  stateColor?: string;
  /** Renders a trailing action pill instead of state text — the open-slot case. */
  actionLabel?: React.ReactNode;
  onAction?: () => void;
  onSelect?: () => void;
  dim?: boolean;
}

export interface ScheduleListProps extends React.HTMLAttributes<HTMLDivElement> {
  entries: ScheduleEntry[];
}

/**
 * The staff day list: mono time column, session label, then either a state
 * word or a "quick book" action for open slots.
 */
export function ScheduleList({ entries, style, ...rest }: ScheduleListProps) {
  return (
    <Panel radiusStep="lg" style={style} {...rest}>
      {entries.map((e, i) => {
        const Row = e.onSelect ? 'button' : 'div';
        return (
          <Row
            key={e.id}
            {...(e.onSelect ? { type: 'button' as const, onClick: e.onSelect } : {})}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-3)',
              width: '100%',
              minHeight: 'var(--touch-min)',
              padding: '0 var(--space-4)',
              border: 0,
              borderBottom: i === entries.length - 1 ? 0 : `1px solid ${color.borderSubtle}`,
              borderRadius: 0,
              background: 'transparent',
              textAlign: 'left',
              cursor: e.onSelect ? 'pointer' : 'default',
            }}
          >
            <span style={{ flexShrink: 0, width: 46, fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, color: color.goldText }}>
              {e.time}
            </span>
            <span
              style={{
                flex: 1,
                minWidth: 0,
                fontFamily: font.body,
                fontSize: 'var(--text-sm)',
                fontWeight: 600,
                color: e.dim ? color.textDim : color.text,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {e.label}
            </span>
            {e.actionLabel ? (
              <Button variant="outline" size="sm" onClick={e.onAction} style={{ flexShrink: 0, borderRadius: radius.pill }}>
                {e.actionLabel}
              </Button>
            ) : e.state ? (
              <span style={{ flexShrink: 0, fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: e.stateColor ?? color.textMuted }}>
                {e.state}
              </span>
            ) : null}
          </Row>
        );
      })}
    </Panel>
  );
}
