import * as React from 'react';
import { color, font, radius, shadow } from '../tokens';

export interface SheetProps extends React.HTMLAttributes<HTMLDivElement> {
  open: boolean;
  onClose: () => void;
  /** Accessible name — required, the sheet is a dialog. */
  label: string;
  /** Mono eyebrow above the title. */
  eyebrow?: React.ReactNode;
  title?: React.ReactNode;
  /** Top hairline + eyebrow colour. Pass the owning agent's accent token. */
  accent?: string;
  /** Suppress the backdrop — the docked-agent case. */
  scrim?: boolean;
  /** Height cap; content scrolls inside. */
  maxHeight?: string;
  footer?: React.ReactNode;
  children?: React.ReactNode;
}

/**
 * Bottom sheet: scrim, top hairline in the owning accent, header with eyebrow
 * and close, scrolling body, optional pinned footer. Every mobile sheet in the
 * portal (booking, settings, quote, chat) is this shell.
 */
export function Sheet({
  open, onClose, label, eyebrow, title, accent = color.gold,
  scrim = true, maxHeight = '86%', footer, style, children, ...rest
}: SheetProps) {
  if (!open) return null;
  return (
    <>
      {scrim ? (
        <div
          onClick={onClose}
          aria-hidden="true"
          style={{ position: 'absolute', inset: 0, zIndex: 38, background: 'rgba(9,10,9,.72)', backdropFilter: 'blur(3px)' }}
        />
      ) : null}
      <div
        role="dialog"
        aria-label={label}
        aria-modal={scrim ? true : undefined}
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 0,
          zIndex: 39,
          display: 'flex',
          flexDirection: 'column',
          maxHeight,
          borderTop: `2px solid ${accent}`,
          borderRadius: `${radius.xl} ${radius.xl} 0 0`,
          background: color.panelRaised,
          boxShadow: shadow.lg,
          overflow: 'hidden',
          ...style,
        }}
        {...rest}
      >
        {(eyebrow || title) ? (
          <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-4) var(--space-4) var(--space-2)' }}>
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              {eyebrow ? (
                <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.16em', textTransform: 'uppercase', color: accent }}>
                  {eyebrow}
                </span>
              ) : null}
              {title ? (
                <span style={{ fontFamily: font.display, fontWeight: 800, fontSize: 'var(--text-lg)', letterSpacing: '0.03em', textTransform: 'uppercase', color: color.text }}>
                  {title}
                </span>
              ) : null}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label={`Close ${label}`}
              style={{ flexShrink: 0, width: 'var(--touch-min)', height: 'var(--touch-min)', border: 0, borderRadius: radius.md, background: 'transparent', color: color.text, cursor: 'pointer', fontFamily: font.btn, fontSize: 'var(--text-base)' }}
            >
              ✕
            </button>
          </div>
        ) : null}
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 'var(--space-3)', padding: 'var(--space-2) var(--space-4) var(--space-6)' }}>
          {children}
        </div>
        {footer ? (
          <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', padding: 'var(--space-3) var(--space-4) var(--space-6)', borderTop: `1px solid ${color.borderSubtle}` }}>
            {footer}
          </div>
        ) : null}
      </div>
    </>
  );
}
