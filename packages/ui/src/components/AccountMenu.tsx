import * as React from 'react';
import { color, font, radius, shadow } from '../tokens';
import { Avatar } from './Avatar';

export interface AccountMenuRow {
  id: string;
  label: React.ReactNode;
  /** Right-aligned mono value — the settings-row pattern. */
  value?: React.ReactNode;
  valueColor?: string;
  onSelect?: () => void;
  href?: string;
  external?: boolean;
}

export interface AccountMenuProps extends React.HTMLAttributes<HTMLDivElement> {
  userName: React.ReactNode;
  roleLabel: React.ReactNode;
  avatarSrc?: string;
  rows: AccountMenuRow[];
  /** Extra block above the rows — e.g. the founder view-as chip group. */
  children?: React.ReactNode;
}

/** The account/settings popover. Identity header, optional block, then rows. */
export function AccountMenu({ userName, roleLabel, avatarSrc, rows, style, children, ...rest }: AccountMenuProps) {
  return (
    <div
      role="menu"
      aria-label="Account"
      style={{
        width: 262,
        display: 'flex',
        flexDirection: 'column',
        border: `1px solid ${color.panelBorder}`,
        borderRadius: radius.lg,
        background: color.bgDark,
        boxShadow: shadow.lg,
        overflow: 'hidden',
        ...style,
      }}
      {...rest}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-3) var(--space-4)', borderBottom: `1px solid ${color.borderSubtle}`, background: color.panel }}>
        <Avatar src={avatarSrc} diameter={38} />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontFamily: font.body, fontSize: 'var(--text-sm)', fontWeight: 700, color: color.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {userName}
          </span>
          <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: color.goldText }}>
            {roleLabel}
          </span>
        </div>
      </div>
      {children ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', padding: 'var(--space-3) var(--space-4)', borderBottom: `1px solid ${color.borderSubtle}` }}>
          {children}
        </div>
      ) : null}
      {rows.map((r) => {
        const shared: React.CSSProperties = {
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-2)',
          minHeight: 'var(--touch-min)',
          padding: '0 var(--space-4)',
          border: 0,
          borderBottom: `1px solid ${color.borderSubtle}`,
          background: 'transparent',
          color: color.text,
          cursor: 'pointer',
          textAlign: 'left',
          textDecoration: 'none',
          fontFamily: font.body,
          fontSize: 'var(--text-sm)',
        };
        const inner = (
          <>
            <span style={{ flex: 1, minWidth: 0 }}>{r.label}</span>
            {r.value ? (
              <span style={{ flexShrink: 0, fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: r.valueColor ?? color.textMuted }}>
                {r.value}
              </span>
            ) : null}
            {r.external ? <span aria-hidden="true" style={{ fontFamily: font.btn, fontSize: 'var(--text-sm)' }}>↗</span> : null}
          </>
        );
        return r.href ? (
          <a key={r.id} href={r.href} target={r.external ? '_blank' : undefined} rel={r.external ? 'noopener' : undefined} style={shared} role="menuitem">
            {inner}
          </a>
        ) : (
          <button key={r.id} type="button" onClick={r.onSelect} style={shared} role="menuitem">
            {inner}
          </button>
        );
      })}
    </div>
  );
}
