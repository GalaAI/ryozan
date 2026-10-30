import * as React from 'react';
import { color } from '../tokens';
import { NavRail } from '../components/NavRail';
import { TopBar } from '../components/TopBar';
import { Wordmark } from '../components/Wordmark';
import { Avatar } from '../components/Avatar';
import type { TabItem } from '../components/TabBar';
import { BackgroundField } from '../components/BackgroundField';

export interface DesktopShellProps {
  tabs: TabItem[];
  activeTab: string;
  onSelectTab: (id: string) => void;
  title: React.ReactNode;
  /** Topbar right side — inbox button, avatar button. */
  actions?: React.ReactNode;
  /** Topbar notice slot — the exit-preview pill. */
  notice?: React.ReactNode;
  markSrc?: string;
  avatarSrc?: string;
  /** 'tablet' narrows the rail and hides the wordmark, per the scene. */
  mode?: 'tablet' | 'desktop';
  /** Right-hand context pane. Desktop only — pass nothing on tablet. */
  context?: React.ReactNode;
  overlays?: React.ReactNode;
  /** The starseed field. On by default — it is the portal's ground, not decor. */
  field?: boolean;
  children?: React.ReactNode;
}

/**
 * Tablet/desktop frame: nav rail, topbar, main region, optional context pane.
 * `mode` comes from the app's width measurement — this shell has no media
 * queries and must not grow any.
 */
export function DesktopShell({
  tabs, activeTab, onSelectTab, title, actions, notice,
  markSrc, avatarSrc, mode = 'desktop', context, overlays, field = true, children,
}: DesktopShellProps) {
  const isTablet = mode === 'tablet';
  return (
    <div style={{ position: 'relative', display: 'flex', height: '100%', minHeight: 0, background: color.bg, overflow: 'hidden' }}>
      {field ? <BackgroundField position="fixed" /> : null}
      <NavRail
        tabs={tabs}
        activeId={activeTab}
        onSelect={onSelectTab}
        width={isTablet ? 86 : 108}
        markSrc={markSrc}
        wordmark={isTablet ? undefined : <Wordmark fontSize={46} />}
        medallion={<Avatar src={avatarSrc} diameter={52} dashed />}
      />
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', position: 'relative', zIndex: 1, padding: 'var(--space-3)', gap: 'var(--space-3)' }}>
        <TopBar title={title} notice={notice} actions={actions} />
        <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: 'var(--space-4)' }}>
          <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', overflowY: 'auto' }}>
            {children}
          </div>
          {context && !isTablet ? (
            <div style={{ width: 380, flexShrink: 0, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', overflowY: 'auto' }}>
              {context}
            </div>
          ) : null}
        </div>
      </div>
      {overlays}
    </div>
  );
}
