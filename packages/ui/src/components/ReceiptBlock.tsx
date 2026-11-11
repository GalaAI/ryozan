import * as React from 'react';
import { color, font, radius } from '../tokens';
import { MetaList } from './MetaRow';

export interface ReceiptBlockProps extends React.HTMLAttributes<HTMLDivElement> {
  eyebrow: React.ReactNode;
  headline?: React.ReactNode;
  rows?: Array<{ label: React.ReactNode; value: React.ReactNode; valueColor?: string }>;
  /** Border + eyebrow colour: the accent of whatever settled. */
  accent?: string;
  note?: React.ReactNode;
  children?: React.ReactNode;
}

/**
 * The settled-transaction block shown after a card commits. Tinted hairline
 * border, mono eyebrow, optional ledger rows. Read-only by construction —
 * a receipt never carries an action.
 */
export function ReceiptBlock({ eyebrow, headline, rows, accent = color.goldText, note, style, children, ...rest }: ReceiptBlockProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-2)',
        padding: 'var(--space-4)',
        border: `1px solid ${accent}`,
        borderRadius: radius.lg,
        background: color.bgSurface,
        ...style,
      }}
      {...rest}
    >
      <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.16em', textTransform: 'uppercase', color: accent }}>
        {eyebrow}
      </span>
      {headline ? (
        <span style={{ fontFamily: font.display, fontWeight: 800, fontSize: 'var(--text-lg)', lineHeight: 1.2, textTransform: 'uppercase', color: color.text }}>
          {headline}
        </span>
      ) : null}
      {rows?.length ? <MetaList rows={rows} /> : null}
      {children}
      {note ? (
        <span style={{ fontFamily: font.body, fontSize: 'var(--text-sm)', lineHeight: 1.5, color: color.textMuted, textWrap: 'pretty' }}>
          {note}
        </span>
      ) : null}
    </div>
  );
}
