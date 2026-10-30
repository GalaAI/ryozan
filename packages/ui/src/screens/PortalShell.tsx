import * as React from 'react';
import { color } from '../tokens';
import { TabBar } from '../components/TabBar';
import type { TabItem } from '../components/TabBar';
import { AgentFab } from '../components/AgentMark';
import { BackgroundField } from '../components/BackgroundField';
import { EdgeDissolveRail } from '../components/EdgeDissolveRail';

const AMBIENT_BLOOM_CROSSFADE_MS = 760;

function softenAmbientRgb(rgb: string, amount = 0.16) {
  const channels = rgb.split(',').map((channel) => Number(channel.trim()));
  if (channels.length !== 3 || channels.some((channel) => !Number.isFinite(channel))) return rgb;

  const neutral = channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  return channels
    .map((channel) => Math.round(channel + (neutral - channel) * amount))
    .join(',');
}

export interface PortalShellProps {
  tabs: TabItem[];
  activeTab: string;
  onSelectTab: (id: string) => void;
  /** Agent accent — tints the tab-bar hairline and the FAB glow. */
  agentAccent?: string;
  /** RGB triplet for the active page agent's ambient top bloom. */
  agentBloomRgb?: string;
  agentGlyph?: React.ReactNode;
  agentName?: string;
  onOpenAgent?: () => void;
  /** Sheets render here — they position against this shell. */
  overlays?: React.ReactNode;
  /** Page identity that stays outside the scrolling content region. */
  header?: React.ReactNode;
  /** The starseed field. On by default — it is the portal's ground, not decor. */
  field?: boolean;
  children?: React.ReactNode;
}

/**
 * The phone shell: scrolling page area, agent FAB, bottom tab bar, and a
 * positioning context for sheets. This is the frame every mobile screen sits
 * in — screens do not draw their own nav.
 */
export function PortalShell({
  tabs, activeTab, onSelectTab, agentAccent = color.gold,
  agentBloomRgb = '226,218,194', agentGlyph, agentName,
  onOpenAgent, overlays, header, field = true, children,
}: PortalShellProps) {
  const ambientBloomRgb = React.useMemo(() => softenAmbientRgb(agentBloomRgb), [agentBloomRgb]);
  const bloomTargetRef = React.useRef(ambientBloomRgb);
  const [bloomLayers, setBloomLayers] = React.useState(() => ({
    colors: [ambientBloomRgb, ambientBloomRgb] as [string, string],
    active: 0 as 0 | 1,
  }));

  React.useEffect(() => {
    if (ambientBloomRgb === bloomTargetRef.current) return;
    bloomTargetRef.current = ambientBloomRgb;
    setBloomLayers((current) => {
      const incoming = current.active === 0 ? 1 : 0;
      const colors = [...current.colors] as [string, string];
      colors[incoming] = ambientBloomRgb;
      return { colors, active: incoming };
    });
  }, [ambientBloomRgb]);

  const ambientBloom = (rgb: string) =>
    `radial-gradient(128% 34% at 50% -2%, rgba(${rgb}, .085), rgba(${rgb}, .028) 46%, rgba(0,0,0,0) 74%)`;
  const reduceMotion = typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ambientBloomLayer = {
    position: 'absolute',
    inset: 0,
    zIndex: 0,
    pointerEvents: 'none',
    transition: reduceMotion
      ? 'none'
      : `opacity ${AMBIENT_BLOOM_CROSSFADE_MS}ms cubic-bezier(.22,.61,.36,1)`,
  } as React.CSSProperties;

  return (
    <div
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        background: color.bg,
        overflow: 'hidden',
        '--portal-agent-accent': agentAccent,
        '--portal-agent-rgb': agentBloomRgb,
        '--bttco-portal-nav-height': 'calc(var(--touch-min) + var(--space-2) + max(var(--space-3), env(safe-area-inset-bottom, 0px)) + 2px)',
      } as React.CSSProperties}
    >
      {field ? (
        <BackgroundField
          position="absolute"
          starRgb={ambientBloomRgb}
          starRestL={0.58}
          starRestA={0.46}
        />
      ) : null}
      <span
        aria-hidden="true"
        className="bttco-portal-ambient-bloom bttco-portal-ambient-bloom--from"
        style={{
          ...ambientBloomLayer,
          background: ambientBloom(bloomLayers.colors[0]),
          opacity: bloomLayers.active === 0 ? 1 : 0,
        }}
      />
      <span
        aria-hidden="true"
        className="bttco-portal-ambient-bloom bttco-portal-ambient-bloom--to"
        style={{
          ...ambientBloomLayer,
          background: ambientBloom(bloomLayers.colors[1]),
          opacity: bloomLayers.active === 1 ? 1 : 0,
        }}
      />
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 0,
          pointerEvents: 'none',
          background: 'radial-gradient(115% 78% at 50% 8%, rgba(0,0,0,0) 42%, rgba(0,0,0,.34) 88%, rgba(0,0,0,.5) 100%)',
        }}
      />
      <EdgeDissolveRail
        rail={header}
        className="bttco-portal-scroll-stage"
        railClassName="bttco-portal-fixed-header"
        viewportClassName="bttco-portal-scroll"
        style={{
          position: 'relative',
          display: 'flex',
          flex: 1,
          minHeight: 0,
          flexDirection: 'column',
        }}
        railStyle={header ? {
          position: 'relative',
          zIndex: 3,
          flexShrink: 0,
          padding: '10px 18px 0',
        } : undefined}
        viewportStyle={{
          position: 'relative',
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          overflowX: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          gap: '13px',
          // The nav owns flow space below this scroller. Safe-area padding
          // stays inside the painted nav rather than extending page geometry.
          padding: 'max(0px, env(safe-area-inset-top, 0px)) 18px 24px',
        }}
      >
        {children}
      </EdgeDissolveRail>

      {agentGlyph && agentName ? (
        <AgentFab
          glyph={agentGlyph}
          agentName={agentName}
          accent={agentAccent}
          onClick={onOpenAgent}
          style={{ position: 'absolute', right: 'var(--space-2)', bottom: 84, zIndex: 30 }}
        />
      ) : null}

      <TabBar
        tabs={tabs}
        activeId={activeTab}
        onSelect={onSelectTab}
        accent={agentAccent}
        accentRgb={agentBloomRgb}
      />
      {overlays}
    </div>
  );
}
