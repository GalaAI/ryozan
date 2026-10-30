import * as React from 'react';
import { color, font, radius, shadow, motion } from '../tokens';
import { GrainOverlay } from './GrainOverlay';
import type { TabItem } from './TabBar';

export interface NavRailProps extends React.HTMLAttributes<HTMLElement> {
  tabs: TabItem[];
  activeId: string;
  onSelect: (id: string) => void;
  /** Rail width. Tablet runs narrow (86px), desktop wider. */
  width?: number;
  /** Tiger mark at the crown. */
  markSrc?: string;
  /** Medallion at the foot — the profile entry point. */
  medallion?: React.ReactNode;
  /** Vertical wordmark between nav and medallion. */
  wordmark?: React.ReactNode;
}

/**
 * The desktop/tablet banner rail: gold trim, chevron-cut bottom, grain,
 * vertical nav. Content-agnostic — pass tabs, mark and medallion in.
 */
export function NavRail({
  tabs, activeId, onSelect, width = 86, markSrc, medallion, wordmark, style, ...rest
}: NavRailProps) {
  const cut = 'polygon(0 0,100% 0,100% calc(100% - 26px),50% 100%,0 calc(100% - 26px))';
  const innerCut = 'polygon(0 0,100% 0,100% calc(100% - 23px),50% 100%,0 calc(100% - 23px))';
  return (
    <aside
      aria-label="Portal navigation"
      style={{ position: 'relative', zIndex: 2, width, flexShrink: 0, display: 'flex', justifyContent: 'center', ...style }}
      {...rest}
    >
      {markSrc ? (
        <img
          src={markSrc}
          alt="Ryozan seal"
          draggable={false}
          style={{ position: 'absolute', top: 'var(--space-4)', left: '50%', transform: 'translateX(-50%)', zIndex: 6, width: 61, height: 56, objectFit: 'contain', pointerEvents: 'none', userSelect: 'none' }}
        />
      ) : null}
      <div
        style={{
          width: '100%',
          display: 'flex',
          clipPath: cut,
          padding: 'var(--space-2) var(--space-1)',
          boxSizing: 'border-box',
          background: color.gold,
          boxShadow: shadow.lg,
        }}
      >
        <div
          style={{
            flex: 1,
            position: 'relative',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            clipPath: innerCut,
            padding: 'var(--space-4) 0 var(--space-16)',
            boxSizing: 'border-box',
            background: color.smoke,
          }}
        >
          <GrainOverlay id="bttcoGrainRail" opacity={0.3} />
          <div style={{ width: 76, height: 56, flexShrink: 0 }} />
          <nav style={{ position: 'relative', zIndex: 3, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 'var(--space-5)', marginTop: 'var(--space-6)', width: 56, flexShrink: 0 }}>
            {tabs.map((t) => {
              const on = t.id === activeId;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => onSelect(t.id)}
                  aria-label={t.label}
                  aria-current={on ? 'page' : undefined}
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: 'var(--space-1)',
                    width: '100%',
                    minHeight: 'var(--touch-min)',
                    padding: 0,
                    border: 0,
                    borderRadius: radius.sm,
                    background: 'transparent',
                    cursor: 'pointer',
                    color: on ? color.tan : color.textMuted,
                    transition: `color ${motion.base}, transform ${motion.fast}`,
                  }}
                >
                  {t.icon ? <span aria-hidden="true" style={{ width: 42, height: 34, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{t.icon}</span> : null}
                  <span style={{ fontFamily: font.btn, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
                    {t.label}
                  </span>
                </button>
              );
            })}
          </nav>
          {wordmark ? <div style={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>{wordmark}</div> : <div style={{ flex: 1 }} />}
          {medallion ? <div style={{ flexShrink: 0, zIndex: 2 }}>{medallion}</div> : null}
        </div>
      </div>
    </aside>
  );
}
