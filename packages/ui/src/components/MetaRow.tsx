import * as React from 'react';
import { color, font } from '../tokens';

export interface MetaRowProps extends React.HTMLAttributes<HTMLDivElement> {
  label: React.ReactNode;
  value: React.ReactNode;
  /** Value colour override — pass a token handle, never a literal. */
  valueColor?: string;
  /** First row in a stack omits its top rule. */
  divider?: boolean;
}

/**
 * The key/value ledger row: mono label left, mono value right, hairline
 * rule above. Used in the session pass, settings menus and every receipt.
 */
export function MetaRow({ label, value, valueColor = color.text, divider = true, style, ...rest }: MetaRowProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 'var(--space-2)',
        padding: 'var(--space-1) 0',
        borderTop: divider ? `1px solid ${color.borderSubtle}` : undefined,
        ...style,
      }}
      {...rest}
    >
      <span
        style={{
          fontFamily: font.btn,
          fontSize: 'var(--text-xs)',
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: color.textDim,
        }}
      >
        {label}
      </span>
      <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, color: valueColor, textAlign: 'right' }}>
        {value}
      </span>
    </div>
  );
}

export interface MetaListProps extends React.HTMLAttributes<HTMLDivElement> {
  rows: Array<{ label: React.ReactNode; value: React.ReactNode; valueColor?: string }>;
}

/** A stack of MetaRows; the first row keeps its rule to close the block above. */
export function MetaList({ rows, style, ...rest }: MetaListProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', ...style }} {...rest}>
      {rows.map((r, i) => (
        <MetaRow key={i} label={r.label} value={r.value} valueColor={r.valueColor} />
      ))}
    </div>
  );
}
