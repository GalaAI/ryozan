import * as React from 'react';
import { color, font, radius } from '../tokens';

export interface TabItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
  badge?: number;
}

export interface TabBarProps extends React.HTMLAttributes<HTMLElement> {
  tabs: TabItem[];
  activeId: string;
  onSelect: (id: string) => void;
  /** Agent accent for the top hairline and active glow. Pass a token handle. */
  accent?: string;
  /** Agent bloom RGB triplet used by the original scene's nav light sweep. */
  accentRgb?: string;
}

/** Mobile bottom navigation. Icon over mono label, numeric badge top-right. */
export function TabBar({
  tabs,
  activeId,
  onSelect,
  accent = color.gold,
  accentRgb = '166,125,67',
  style,
  ...rest
}: TabBarProps) {
  return (
    <nav
      aria-label="Portal navigation"
      style={{
        // Stay in the shell's flex flow. The root owns the selected viewport,
        // and the safe area remains inside this painted surface.
        position: 'relative',
        width: '100%',
        zIndex: 2,
        flexShrink: 0,
        display: 'flex',
        justifyContent: 'space-around',
        gap: 0,
        padding: 'var(--space-2) var(--space-1) max(var(--space-3), env(safe-area-inset-bottom, 0px))',
        borderTop: `2px solid rgba(${accentRgb}, .45)`,
        background: 'linear-gradient(180deg, #101010, #0B0B0B)',
        ...style,
      }}
      {...rest}
    >
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          inset: '-2px 0 auto',
          height: 2,
          overflow: 'hidden',
          pointerEvents: 'none',
        }}
      >
        <span
          className="ryozan-tab-bar__sweep"
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '38%',
            height: '100%',
            background: `linear-gradient(90deg, rgba(0,0,0,0), rgba(${accentRgb}, .95), rgba(0,0,0,0))`,
            filter: 'blur(.4px)',
          }}
        />
      </span>
      {tabs.map((t) => {
        const on = t.id === activeId;
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => onSelect(t.id)}
            aria-current={on ? 'page' : undefined}
            aria-label={t.badge ? `${t.label}, ${t.badge} unread` : t.label}
            style={{
              position: 'relative',
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 'var(--space-1)',
              minHeight: 'var(--touch-min)',
              padding: '0 var(--space-1)',
              border: 0,
              background: 'transparent',
              color: on ? accent : color.textMuted,
              cursor: 'pointer',
              fontFamily: font.btn,
              fontSize: 'var(--text-xs)',
              fontWeight: 700,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              textShadow: on ? `0 0 10px ${accent}, 0 0 22px ${accent}` : 'none',
            }}
          >
            {t.icon ? (
              <span
                aria-hidden="true"
                style={{
                  display: 'flex',
                  filter: on
                    ? `drop-shadow(0 0 7px ${accent}) drop-shadow(0 0 16px ${accent})`
                    : 'none',
                }}
              >
                {t.icon}
              </span>
            ) : null}
            {t.label}
            {t.badge ? (
              <span
                aria-hidden="true"
                className="ryozan-tab-bar__badge"
                style={{
                  position: 'absolute',
                  top: 'var(--space-0)',
                  right: 'calc(50% - var(--space-5))',
                  width: '24px',
                  minWidth: '24px',
                  maxWidth: '24px',
                  height: '18px',
                  padding: 0,
                  boxSizing: 'border-box',
                  borderRadius: radius.pill,
                  background: color.pink,
                  color: color.smoke,
                  overflow: 'hidden',
                  fontSize: '9px',
                  fontWeight: 700,
                  fontVariantNumeric: 'tabular-nums',
                  letterSpacing: '-0.04em',
                  lineHeight: 1,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {t.badge > 99 ? '99+' : t.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
}
