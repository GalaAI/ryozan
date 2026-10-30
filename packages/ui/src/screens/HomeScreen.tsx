import * as React from 'react';
import { color } from '../tokens';
import { PageTitle } from '../components/PageTitle';
import { SectionLabel } from '../components/SectionLabel';
import { Avatar } from '../components/Avatar';
import { SessionPass } from '../components/SessionPass';
import type { SessionPassProps } from '../components/SessionPass';
import { ScheduleList } from '../components/ScheduleList';
import type { ScheduleEntry } from '../components/ScheduleList';
import { QuickTile } from '../components/QuickTile';
import { AgentMark } from '../components/AgentMark';

export interface HomeTile {
  id: string;
  label: React.ReactNode;
  value: React.ReactNode;
  background?: string;
  valueColor?: string;
  labelColor?: string;
  onSelect?: () => void;
}

export interface HomeScreenProps {
  /** Which home this is. Staff sees the day; collectors see their pass. */
  role: 'staff' | 'collector';
  title: React.ReactNode;
  /** Mono meta under the title. */
  meta?: React.ReactNode;
  avatarSrc?: string;
  onOpenAccount?: () => void;
  /** Exact header action from the source scene, such as the tiger mark. */
  headerAction?: React.ReactNode;

  /** Collector: the session pass. Omit when there is no upcoming session. */
  pass?: Omit<SessionPassProps, 'railVariant'>;
  /** Collector: the two-up tiles under the pass. */
  tiles?: HomeTile[];

  /** Staff: today's chair list. */
  today?: ScheduleEntry[];
  /** Staff: one line from Gala above the day. */
  galaLine?: React.ReactNode;
  galaGlyph?: React.ReactNode;

  /** Desktop passes 'date' to get the wide pass. */
  passRail?: 'stub' | 'date';
}

/**
 * Home, assembled. Same component both roles — the role prop decides which
 * blocks appear, exactly as the scene does. No layout invention: header,
 * then pass or day, then tiles.
 */
export function HomeScreen({
  role, title, meta, avatarSrc, onOpenAccount, headerAction,
  pass, tiles, today, galaLine, galaGlyph, passRail = 'stub',
}: HomeScreenProps) {
  return (
    <>
      <PageTitle
        meta={meta}
        action={
          headerAction ?? (onOpenAccount ? (
            <button
              type="button"
              onClick={onOpenAccount}
              aria-label="Profile and settings"
              style={{ width: 'var(--touch-min)', height: 'var(--touch-min)', padding: 0, border: 0, background: 'transparent', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <Avatar src={avatarSrc} diameter={46} />
            </button>
          ) : undefined)
        }
      >
        {title}
      </PageTitle>

      {pass ? <SessionPass {...pass} railVariant={passRail} /> : null}

      {role === 'collector' && tiles?.length ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)' }}>
          {tiles.map((t) => (
            <QuickTile
              key={t.id}
              label={t.label}
              value={t.value}
              background={t.background}
              valueColor={t.valueColor}
              labelColor={t.labelColor}
              onClick={t.onSelect}
            />
          ))}
        </div>
      ) : null}

      {role === 'staff' && galaLine ? (
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 9, padding: '2px 2px 0' }}>
          {galaGlyph ? <AgentMark glyph={galaGlyph} accent={color.tan} scale={15} style={{ position: 'relative', top: 2 }} /> : null}
          <span style={{ flex: 1, minWidth: 0, fontFamily: 'var(--font-body)', fontSize: 'var(--text-sm)', lineHeight: 1.5, color: color.textMuted, textWrap: 'pretty' }}>
            {galaLine}
          </span>
        </div>
      ) : null}

      {role === 'staff' && today?.length ? (
        <>
          <SectionLabel scale="lg">Today</SectionLabel>
          <ScheduleList entries={today} />
        </>
      ) : null}
    </>
  );
}
