import * as React from 'react';
import { color, font, radius } from '../tokens';
import { Panel } from './Panel';
import { MetaList } from './MetaRow';
import { StatusText } from './Badge';
import { Button } from './Button';

export interface SessionPassRow {
  label: React.ReactNode;
  value: React.ReactNode;
  valueColor?: string;
}

export interface SessionPassProps {
  eyebrow: React.ReactNode;
  /** Two display lines; the second takes the accent colour. */
  titleTop: React.ReactNode;
  titleAccent: React.ReactNode;
  rows: SessionPassRow[];
  status?: React.ReactNode;
  ctaLabel?: React.ReactNode;
  onCta?: () => void;
  secondaryLabel?: React.ReactNode;
  onSecondary?: () => void;
  /** 'stub' = the vertical 刺青 ticket stub (mobile). 'date' = the date rail (desktop). */
  railVariant?: 'stub' | 'date';
  /** Required for railVariant="date". */
  date?: { month: string; day: string; weekday: string; note?: React.ReactNode };
  /** Letters set down the vertical stub. The product name, never a hard-coded shop. */
  brand?: string;
  style?: React.CSSProperties;
}

function StubRail({ brand }: { brand: string }) {
  return (
    <div
      aria-hidden="true"
      style={{
        flexShrink: 0,
        width: 44,
        marginTop: -6,
        filter: 'drop-shadow(4px 6px 7px rgba(0,0,0,.55))',
        position: 'relative',
        top: 3,
        left: -5,
      }}
    >
      <div
        style={{
          clipPath: 'polygon(0 0,100% 0,100% calc(100% - 18px),50% 100%,0 calc(100% - 18px))',
          padding: '2.5px 2.5px 4px',
          background: 'linear-gradient(0deg,#c8a15e,#6b4d2c)',
          borderRadius: 4,
        }}
      >
        <div
          style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          clipPath: 'polygon(0 0,100% 0,100% calc(100% - 15px),50% 100%,0 calc(100% - 15px))',
          padding: '14px 0 26px',
          background: 'linear-gradient(180deg,#7a3129,#4c1f1a)',
          borderRadius: 4,
        }}
        >
          <span style={{ fontSize: 16, lineHeight: 1.25, color: '#EBDCC3', writingMode: 'vertical-rl', textOrientation: 'upright', letterSpacing: '0.2em' }}>
            刺青
          </span>
          <span style={{ width: 22, borderTop: '1px dashed rgba(235,220,195,.5)', margin: '10px 0' }} />
          <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', lineHeight: 1.02, fontFamily: font.heading, fontSize: 17, color: '#E0B76F' }}>
            {brand.split('').map((c, i) => <span key={i}>{c}</span>)}
          </span>
        </div>
      </div>
    </div>
  );
}

function DateRail({ date }: { date: NonNullable<SessionPassProps['date']> }) {
  return (
    <div
      style={{
        position: 'relative',
        flexShrink: 0,
        width: 128,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-1)',
        padding: 'var(--space-4) var(--space-3)',
        background: color.sage,
        borderRight: `1px solid ${color.borderSubtle}`,
      }}
    >
      <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.3em', textTransform: 'uppercase', color: color.text }}>{date.month}</span>
      <span style={{ fontFamily: font.title, fontSize: 'var(--text-3xl)', lineHeight: 0.9, color: color.boneWhite }}>{date.day}</span>
      <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.26em', textTransform: 'uppercase', color: color.text }}>{date.weekday}</span>
      {date.note ? (
        <span style={{ marginTop: 'var(--space-2)', fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: color.text, border: `1px solid ${color.panelBorder}`, borderRadius: radius.pill, padding: 'var(--space-1) var(--space-3)' }}>
          {date.note}
        </span>
      ) : null}
    </div>
  );
}

/**
 * The collector's session pass — the hero of both home screens. Ticket stub
 * on mobile, date rail on desktop; identical field set either way.
 */
export function SessionPass({
  eyebrow, titleTop, titleAccent, rows, status, ctaLabel, onCta,
  secondaryLabel, onSecondary, railVariant = 'stub', date, brand = 'RYOZAN', style,
}: SessionPassProps) {
  const isStub = railVariant === 'stub';
  return (
    <Panel
      radiusStep="xl"
      style={{
        flexDirection: 'row',
        gap: 0,
        ...(isStub ? { padding: '0 16px 16px 14px', overflow: 'visible' } : null),
        ...style,
      }}
    >
      {railVariant === 'date' && date ? <DateRail date={date} /> : <StubRail brand={brand} />}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', padding: isStub ? '16px 0 0 14px' : 'var(--space-4) var(--space-5)' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 'var(--space-2)' }}>
          <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.22em', textTransform: 'uppercase', color: color.textSecondary }}>
            {eyebrow}
          </span>
          {status ? <StatusText tone="ok">{status}</StatusText> : null}
        </div>
        <span style={{ marginTop: 'var(--space-2)', fontFamily: font.display, fontWeight: 800, fontSize: 'var(--text-xl)', lineHeight: 1.04, textTransform: 'uppercase', color: color.text }}>
          {titleTop}
        </span>
        <span style={{ fontFamily: font.display, fontWeight: 800, fontSize: 'var(--text-xl)', lineHeight: 1.04, textTransform: 'uppercase', color: color.pink }}>
          {titleAccent}
        </span>
        <MetaList rows={rows} style={{ marginTop: 'var(--space-3)' }} />
        {ctaLabel ? (
          <Button variant="accent" fullWidth onClick={onCta} style={{ marginTop: 'var(--space-3)' }}>
            {ctaLabel}
          </Button>
        ) : null}
        {secondaryLabel ? (
          <Button variant="ghost" fullWidth size="sm" onClick={onSecondary} style={{ marginTop: 'var(--space-2)' }}>
            {secondaryLabel}
          </Button>
        ) : null}
      </div>
    </Panel>
  );
}
