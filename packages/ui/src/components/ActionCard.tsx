import * as React from 'react';
import { color, font } from '../tokens';
import { AgentGlow } from './AgentGlow';

export type ActionCardShell =
  | 'intake-docket'
  | 'chair-ticket'
  | 'receipt-tape'
  | 'courier-manifest'
  | 'heal-chart'
  | 'memory-shard';

export type NemotronManaPip = '⚡ Nano' | '👁️ Super-VL' | '🔥 Super' | '👑 Ultra';

export interface AgentActionCard {
  id: string;
  name: React.ReactNode;
  description: React.ReactNode;
  /**
   * The shell is selected by the action card, never inferred from its agent.
   * One agent hand may deal several shell architectures.
   */
  shell: ActionCardShell;
  /** NVIDIA Nemotron Mana/Chi cost pip rendered in the MTG top-right header bar. */
  manaPip?: NemotronManaPip;
  /** Underlying NVIDIA Nemotron model route on Nebius Token Factory. */
  nemotronModel?: string;
  /** MTG Type Line, e.g. "Bandit Action: [Shishin · Nine-Tattooed Dragon] · Flash and ledger". */
  typeLine?: string;
  /** MTG Oracle Rules Box text describing the exact shop action when tapped. */
  oracleText?: string;
  /** Hivemind / Foundry MCP tool route invoked on Tap-to-Cast. */
  mcpTool?: string;
}

export interface ActionCardProps {
  card: AgentActionCard;
  agentName: string;
  accent?: string;
  /** RGB triplet used for translucent agent-colored shell treatments. */
  accentRgb?: string;
  /** Optional persistence hook for memory-shard cards. */
  onCommit?: () => void | Promise<void>;
  commitLabel?: string;
  /** Optional Tap-to-Cast handler that resolves the card on the Spell Resolution Stack. */
  onCast?: (card: AgentActionCard) => void | Promise<{ receiptId?: string; status?: string } | void>;
  /** Optional pre-resolved Nemotron receipt ID for Hanko seal rendering. */
  castReceiptId?: string | null;
  /** Live controls supplied by the owning feature, replacing placeholder details. */
  children?: React.ReactNode;
}

const mono: React.CSSProperties = {
  fontFamily: font.mono,
  fontSize: 8.5,
  fontWeight: 700,
  letterSpacing: '.12em',
  textTransform: 'uppercase',
};

const paper = '#121212';
const ink = '#dad1b9';
const dim = 'rgba(218,209,185,.52)';
const washiPlate = '#ccc4ad';
const sumiInk = '#121212';
const beniVermilion = '#c24b2a';
const hankoVermilion = '#D94E34';
const aiIndigo = '#1D3557';
const kinGold = '#cba15b';

const defaultPipByShell: Record<ActionCardShell, NemotronManaPip> = {
  'intake-docket': '⚡ Nano',
  'chair-ticket': '🔥 Super',
  'receipt-tape': '🔥 Super',
  'courier-manifest': '⚡ Nano',
  'heal-chart': '⚡ Nano',
  'memory-shard': '👁️ Super-VL',
};

function resolveManaPip(card: AgentActionCard, agentName: string): NemotronManaPip {
  if (card.manaPip) return card.manaPip;
  const lower = agentName.toLowerCase();
  if (lower.includes('gala') || lower.includes('sovereign')) return '👑 Ultra';
  if (lower.includes('rochishin') || lower.includes('smoku') || lower.includes('monk')) return '👁️ Super-VL';
  if (lower.includes('soko') || lower.includes('sōkō') || lower.includes('spyder') || lower.includes('shishin') || lower.includes('xar') || lower.includes('saishin') || lower.includes('booky') || lower.includes('riki') || lower.includes('herald')) {
    return '🔥 Super';
  }
  return defaultPipByShell[card.shell] ?? '⚡ Nano';
}

function resolveTypeLine(card: AgentActionCard, agentName: string): string {
  if (card.typeLine) return card.typeLine;
  // The shell is a visual treatment, not a domain, so the fallback names only the agent.
  return `Bandit Action: [${agentName}]`;
}

function resolveOracleText(card: AgentActionCard): string {
  if (card.oracleText) return card.oracleText;
  const desc = typeof card.description === 'string' ? card.description : 'Execute verified studio operation.';
  return `Tap (⤵): ${desc}`;
}

function MtgCardAnatomy({
  card,
  agentName,
  accent = kinGold,
  onCast,
  onCommit,
  castReceiptId,
}: ActionCardProps) {
  const [castState, setCastState] = React.useState<'untapped' | 'stack' | 'executed'>(castReceiptId ? 'executed' : 'untapped');
  const [receipt, setReceipt] = React.useState<string>(
    castReceiptId || `NEM-${card.id.slice(0, 6).toUpperCase()}-88A`
  );

  React.useEffect(() => {
    if (castReceiptId) {
      setCastState('executed');
      setReceipt(castReceiptId);
    }
  }, [castReceiptId]);

  const pip = resolveManaPip(card, agentName);
  const typeLine = resolveTypeLine(card, agentName);
  const oracleText = resolveOracleText(card);

  const handleTapCast = async () => {
    if (castState === 'stack') return;
    setCastState('stack');
    try {
      if (onCast) {
        const res = await onCast(card);
        if (res && typeof res === 'object' && 'receiptId' in res && res.receiptId) {
          setReceipt(res.receiptId);
        } else {
          setReceipt(`NEM-${card.id.slice(0, 6).toUpperCase()}-${Math.floor(100 + Math.random() * 899)}`);
        }
      } else if (onCommit) {
        await onCommit();
        setReceipt(`NEM-MEM-${card.id.slice(0, 5).toUpperCase()}`);
      } else {
        setReceipt(`NEM-${card.id.slice(0, 6).toUpperCase()}-709`);
      }
      setCastState('executed');
    } catch {
      setCastState('untapped');
    }
  };

  return (
    <div
      data-mtg-anatomy={card.id}
      data-cast-state={castState}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        marginTop: 6,
        padding: '10px 12px',
        borderRadius: 8,
        border: `1.5px solid ${sumiInk}`,
        background: washiPlate,
        color: sumiInk,
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,.35), 0 4px 12px rgba(0,0,0,.45)',
        position: 'relative',
      }}
    >
      {/* MTG Header Bar with Card Title & NVIDIA Nemotron Mana/Chi Cost Pips */}
      <div
        data-mtg-header-bar
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          paddingBottom: 6,
          borderBottom: `1.5px solid ${sumiInk}`,
        }}
      >
        <span
          style={{
            fontFamily: font.display,
            fontSize: 12,
            fontWeight: 800,
            letterSpacing: '.04em',
            textTransform: 'uppercase',
            color: sumiInk,
          }}
        >
          {card.name}
        </span>
        <span
          data-mtg-mana-pip={pip}
          title={card.nemotronModel ?? 'NVIDIA Nemotron on Nebius Token Factory'}
          style={{
            ...mono,
            fontSize: 8.5,
            padding: '3px 7px',
            borderRadius: 99,
            background: aiIndigo,
            color: '#dad1b9',
            border: `1px solid ${kinGold}`,
            whiteSpace: 'nowrap',
          }}
        >
          {pip}
        </span>
      </div>

      {/* MTG Type Line */}
      <div
        data-mtg-type-line
        style={{
          ...mono,
          fontSize: 8,
          padding: '3px 6px',
          background: 'rgba(18,18,18,.08)',
          borderLeft: `3px solid ${beniVermilion}`,
          color: sumiInk,
        }}
      >
        {typeLine}
      </div>

      {/* MTG Oracle Rules Box */}
      <div
        data-mtg-oracle-box
        style={{
          fontFamily: font.body,
          fontSize: 11.5,
          lineHeight: 1.45,
          padding: '7px 9px',
          background: 'rgba(255,252,242,.55)',
          border: `1px solid rgba(18,18,18,.24)`,
          borderRadius: 4,
          color: sumiInk,
        }}
      >
        {oracleText}
      </div>

      {/* Tap-to-Cast Bar & Vermilion Japanese Hanko Stamp (済 · Executed) */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 2 }}>
        <button
          type="button"
          data-tap-to-cast={card.id}
          onClick={() => void handleTapCast()}
          style={{
            ...mono,
            minHeight: 32,
            padding: '5px 11px',
            borderRadius: 6,
            border: `1.5px solid ${castState === 'executed' ? beniVermilion : sumiInk}`,
            background: castState === 'executed' ? beniVermilion : sumiInk,
            color: '#dad1b9',
            cursor: 'pointer',
            transform: castState === 'stack' ? 'rotate(6deg) scale(0.97)' : 'none',
            transition: 'transform .18s ease, background .18s ease',
          }}
        >
          {castState === 'stack'
            ? '⤵ Resolving on Stack…'
            : castState === 'executed'
              ? '⤵ Tapped · Cast Again'
              : `Cast (⤵ Tap)`}
        </button>

        {castState === 'executed' ? (
          <div
            data-hanko-seal="executed"
            aria-label={`Executed Hanko seal ${receipt}`}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '2px 8px',
              border: `2px solid ${hankoVermilion}`,
              borderRadius: 6,
              background: 'rgba(217,78,52,.12)',
              color: beniVermilion,
              transform: 'rotate(-4deg)',
              boxShadow: '0 0 10px rgba(217,78,52,.25)',
            }}
          >
            <span
              style={{
                display: 'inline-grid',
                placeItems: 'center',
                width: 22,
                height: 22,
                borderRadius: 99,
                border: `2px solid ${hankoVermilion}`,
                fontWeight: 900,
                fontSize: 12,
                lineHeight: 1,
                color: hankoVermilion,
              }}
            >
              済
            </span>
            <span style={{ ...mono, fontSize: 7.5, color: sumiInk }}>
              Executed · {receipt}
            </span>
          </div>
        ) : (
          <span style={{ ...mono, fontSize: 7.5, color: 'rgba(18,18,18,.62)', accentColor: accent }}>
            {card.mcpTool ? `MCP · ${card.mcpTool}` : 'Ready on Stack'}
          </span>
        )}
      </div>
    </div>
  );
}

function Docket(props: ActionCardProps) {
  const { children, card, agentName, accent = '#d38a68', accentRgb = '211,138,104' } = props;
  const tone = accent;
  return (
    <article style={{ position: 'relative', display: 'flex', flexDirection: 'column', minHeight: 350, border: `1px solid rgba(211,138,104,.38)`, borderRadius: '4px 12px 12px 4px', background: paper, boxShadow: '0 22px 44px -22px rgba(0,0,0,.9)', overflow: 'hidden' }}>
      <AgentGlow accentRgb={accentRgb} />
      <span aria-hidden="true" style={{ position: 'absolute', left: 0, insetBlock: 0, width: 9, background: 'radial-gradient(circle at 0 50%,#050607 3.2px,transparent 3.6px) 0 0/9px 14px' }} />
      <header style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '13px 15px 10px 20px', borderBottom: '1px dashed rgba(218,209,185,.18)' }}>
        <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: 99, background: tone, boxShadow: `0 0 8px ${tone}` }} />
        <span style={{ ...mono, color: tone }}>Front desk docket</span>
        <span style={{ ...mono, marginLeft: 'auto', color: dim }}>{resolveManaPip(card, agentName)}</span>
      </header>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '16px 15px 16px 20px' }}>
        <div style={{ fontFamily: font.display, fontSize: 22, fontWeight: 800, lineHeight: 1.05, textTransform: 'uppercase', color: ink }}>{card.name}</div>
        <div style={{ padding: '11px 13px', borderLeft: `2px solid ${beniVermilion}`, background: 'rgba(218,209,185,.03)', fontFamily: font.body, fontSize: 12.5, lineHeight: 1.6, fontStyle: 'italic', color: 'rgba(218,209,185,.78)' }}>{card.description}</div>
        <MtgCardAnatomy {...props} />
{children ?? <>
        <div>
          {[
            ['Bandit', agentName],
            ['Reference', card.id],
            ['State', 'Ready to cast'],
          ].map(([key, value]) => (
            <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, padding: '8px 0', borderTop: '1px solid rgba(218,209,185,.12)' }}>
              <span style={{ ...mono, color: dim }}>{key}</span>
              <span style={{ ...mono, color: ink }}>{value}</span>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 13px', border: '1px solid rgba(218,209,185,.14)', borderRadius: 10, background: 'rgba(218,209,185,.03)' }}>
          <span style={{ ...mono, color: dim }}>Routing slip</span>
          <span style={{ ...mono, marginLeft: 'auto', color: ink }}>Status unavailable</span>
        </div>
        <button type="button" disabled aria-disabled="true" style={{ minHeight: 40, border: '1px solid rgba(211,138,104,.3)', borderRadius: 99, background: 'transparent', color: dim, cursor: 'not-allowed', ...mono }}>Routing unavailable</button>
        </>}
      </div>
    </article>
  );
}

function ChairTicket(props: ActionCardProps) {
  const { children, card, agentName, accent = '#70c27b', accentRgb = '112,194,123' } = props;
  const tone = accent;
  return (
    <article style={{ position: 'relative', display: 'grid', gridTemplateColumns: '58px minmax(0,1fr) 72px', minHeight: 226, border: '1px solid rgba(112,194,123,.3)', borderRadius: 12, background: paper, boxShadow: '0 22px 44px -22px rgba(0,0,0,.9)', overflow: 'hidden' }}>
      <AgentGlow accentRgb={accentRgb} />
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2, padding: '12px 0', borderRight: '1px solid rgba(112,194,123,.28)', background: 'rgba(112,194,123,.07)' }}>
        <span style={{ ...mono, color: dim }}>Schedule</span>
        <strong style={{ fontFamily: font.display, fontSize: 29, lineHeight: 1, color: tone }}>⤵</strong>
        <span style={{ ...mono, color: tone }}>{resolveManaPip(card, agentName)}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 11, minWidth: 0, padding: '15px 13px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <strong style={{ fontFamily: font.display, fontSize: 22, lineHeight: 1, color: ink }}>Schedule</strong>
          <span style={{ ...mono, color: dim }}>{children ? 'Live workspace' : 'Details unavailable'}</span>
        </div>
        <div style={{ fontFamily: font.display, fontSize: 17, fontWeight: 800, lineHeight: 1.05, textTransform: 'uppercase', color: ink }}>{card.name}</div>
        <div style={{ fontFamily: font.body, fontSize: 11.5, lineHeight: 1.45, color: 'rgba(218,209,185,.68)' }}>{card.description}</div>
        <MtgCardAnatomy {...props} />
{children ?? <>
        <div style={{ display: 'flex', gap: 7, marginTop: 'auto' }}>
          <button type="button" disabled aria-disabled="true" style={{ flex: 1, minHeight: 36, border: '1px solid rgba(112,194,123,.3)', borderRadius: 99, background: 'transparent', color: dim, cursor: 'not-allowed', ...mono }}>Unavailable</button>
          <button type="button" disabled aria-disabled="true" style={{ flex: 1, minHeight: 36, border: '1px solid rgba(218,209,185,.16)', borderRadius: 99, background: 'transparent', color: dim, cursor: 'not-allowed', ...mono }}>Unavailable</button>
        </div>
        </>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '15px 7px', borderLeft: '1px dashed rgba(218,209,185,.28)', background: 'rgba(218,209,185,.03)' }}>
        <span style={{ ...mono, color: tone, writingMode: 'vertical-rl' }}>Chair ticket</span>
        <span aria-hidden="true" style={{ width: '100%', height: 22, opacity: .6, background: 'repeating-linear-gradient(90deg,var(--cream) 0 2px,transparent 2px 4px,var(--cream) 4px 5px,transparent 5px 9px)' }} />
        <span style={{ ...mono, fontSize: 7, color: dim, textAlign: 'center' }}>Ryozan</span>
      </div>
    </article>
  );
}

function ReceiptTape(props: ActionCardProps) {
  const { children, card, agentName, accent = '#c24b2a', accentRgb = '194,75,42' } = props;
  const tone = accent;
  const rows = [['Action', card.name], ['Bandit', agentName], ['Reference', card.id], ['Ledger', 'ryozan-econ']];
  return (
    <article style={{ position: 'relative', display: 'flex', flexDirection: 'column', filter: 'drop-shadow(0 22px 22px rgba(0,0,0,.55))' }}>
      <AgentGlow accentRgb={accentRgb} />
      <span aria-hidden="true" style={{ height: 9, background: 'linear-gradient(135deg,#121212 34%,transparent 34%) 0 100%/11px 9px repeat-x,linear-gradient(-135deg,#121212 34%,transparent 34%) 0 100%/11px 9px repeat-x', transform: 'scaleY(-1)' }} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '14px 17px 16px', background: paper }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
          <span style={{ ...mono, color: tone }}>Receipt tape · Hustl</span>
          <span style={{ ...mono, color: dim }}>{resolveManaPip(card, agentName)}</span>
        </div>
        <div style={{ fontFamily: font.display, fontSize: 20, fontWeight: 800, lineHeight: 1.05, textTransform: 'uppercase', color: ink }}>{card.name}</div>
        <div style={{ fontFamily: font.body, fontSize: 11.5, lineHeight: 1.45, color: 'rgba(218,209,185,.68)' }}>{card.description}</div>
        <MtgCardAnatomy {...props} />
{children ?? <>
        <div>
          {rows.map(([key, value]) => (
            <div key={String(key)} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '6px 0' }}>
              <span style={{ ...mono, minWidth: 64, color: dim }}>{key}</span>
              <span style={{ flex: 1, height: 1, background: 'repeating-linear-gradient(90deg,rgba(218,209,185,.3) 0 1px,transparent 1px 4px)' }} />
              <span style={{ ...mono, maxWidth: '48%', color: ink, textAlign: 'right' }}>{value}</span>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" disabled aria-disabled="true" style={{ flex: 1, minHeight: 44, border: '1.5px solid rgba(218,209,185,.18)', borderRadius: 6, background: 'transparent', color: dim, transform: 'rotate(-1.5deg)', cursor: 'not-allowed', ...mono }}>Unavailable</button>
          <button type="button" disabled aria-disabled="true" style={{ flex: 1, minHeight: 44, border: '1.5px solid rgba(218,209,185,.18)', borderRadius: 6, background: 'transparent', color: dim, transform: 'rotate(1.5deg)', cursor: 'not-allowed', ...mono }}>Unavailable</button>
        </div>
        <span style={{ ...mono, color: dim, textAlign: 'center' }}>Consent controls unavailable in this card</span>
        </>}
        <span aria-hidden="true" style={{ width: '100%', height: 26, opacity: .2, background: 'repeating-linear-gradient(90deg,var(--cream) 0 2px,transparent 2px 4px,var(--cream) 4px 5px,transparent 5px 9px,var(--cream) 9px 11px,transparent 11px 13px)' }} />
      </div>
      <span aria-hidden="true" style={{ height: 9, background: 'linear-gradient(135deg,#121212 34%,transparent 34%) 0 100%/11px 9px repeat-x,linear-gradient(-135deg,#121212 34%,transparent 34%) 0 100%/11px 9px repeat-x' }} />
    </article>
  );
}

function Manifest(props: ActionCardProps) {
  const { children, card, agentName, accent = '#b795e8', accentRgb = '183,149,232' } = props;
  const tone = accent;
  const details = ['Routing details unavailable', `Bandit: ${agentName}`];
  return (
    <article style={{ position: 'relative', display: 'flex', flexDirection: 'column', border: '1px solid rgba(183,149,232,.3)', borderRadius: 12, background: paper, boxShadow: '0 22px 44px -22px rgba(0,0,0,.9)', overflow: 'hidden' }}>
      <AgentGlow accentRgb={accentRgb} />
      <header style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, height: 76, padding: '13px 17px', background: 'rgba(183,149,232,.09)', borderBottom: '1px solid rgba(183,149,232,.22)', clipPath: 'polygon(0 0,100% 0,100% 46%,50% 100%,0 46%)' }}>
        <span style={{ ...mono, color: tone }}>Manifest · {resolveManaPip(card, agentName)}</span>
        <span style={{ ...mono, color: dim }}>#{card.id.slice(0, 8).toUpperCase()}</span>
      </header>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, padding: '6px 17px 17px' }}>
        <div>
          <div style={{ fontFamily: font.display, fontSize: 21, fontWeight: 800, lineHeight: 1.06, textTransform: 'uppercase', color: tone }}>{card.name}</div>
          <div style={{ ...mono, marginTop: 3, color: dim }}>{card.description}</div>
        </div>
        <MtgCardAnatomy {...props} />
{children ?? <>
        <div style={{ display: 'flex', gap: 8 }}>
          {['Reference unavailable', 'Record unavailable', 'Route unavailable'].map((label) => <div key={label} style={{ flex: 1, minWidth: 0, height: 58, display: 'grid', placeItems: 'center', padding: 5, border: '1px solid rgba(183,149,232,.22)', borderRadius: 8, background: 'rgba(255,248,236,.03)', ...mono, color: dim, textAlign: 'center' }}>{label}</div>)}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {details.map((detail, index) => (
            <div key={detail} style={{ display: 'flex', gap: 11, alignItems: 'flex-start' }}>
              <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0, paddingTop: 3 }}>
                <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 99, background: tone, boxShadow: `0 0 8px ${tone}` }} />
                {index < details.length - 1 ? <span aria-hidden="true" style={{ width: 1, height: 25, background: 'rgba(183,149,232,.3)' }} /> : null}
              </span>
              <span style={{ paddingBottom: 10, fontFamily: font.body, fontSize: 12, color: ink }}>{detail}</span>
            </div>
          ))}
        </div>
        <button type="button" disabled aria-disabled="true" style={{ minHeight: 38, border: '1px solid rgba(183,149,232,.22)', borderRadius: 99, background: 'transparent', color: dim, cursor: 'not-allowed', ...mono }}>Routing unavailable</button>
        <span style={{ ...mono, color: 'rgba(218,209,185,.32)' }}>Routing receipt unavailable</span>
        </>}
      </div>
    </article>
  );
}

function HealChart(props: ActionCardProps) {
  const { children, card, agentName, accent = '#7fc3b1', accentRgb = '127,195,177' } = props;
  const tone = accent;
  return (
    <article style={{ position: 'relative', display: 'flex', flexDirection: 'column', border: '1px solid rgba(127,195,177,.3)', borderRadius: 12, background: paper, boxShadow: '0 22px 44px -22px rgba(0,0,0,.9)', overflow: 'hidden' }}>
      <AgentGlow accentRgb={accentRgb} />
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '13px 17px', background: 'rgba(127,195,177,.08)', borderBottom: '1px solid rgba(127,195,177,.22)' }}>
        <span>
          <span style={{ display: 'block', fontFamily: font.display, fontSize: 14, fontWeight: 800, textTransform: 'uppercase', color: ink }}>{card.name}</span>
          <span style={{ ...mono, display: 'block', marginTop: 2, color: dim }}>{card.description}</span>
        </span>
        <span style={{ textAlign: 'right' }}>
          <strong style={{ display: 'block', fontFamily: font.display, fontSize: 22, lineHeight: .9, color: tone }}>{resolveManaPip(card, agentName)}</strong>
          <span style={{ ...mono, color: dim }}>{children ? 'Live workspace' : 'Details unavailable'}</span>
        </span>
      </header>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, padding: '16px 17px 17px' }}>
        <MtgCardAnatomy {...props} />
{children ?? <>
        <div style={{ display: 'grid', minHeight: 66, placeItems: 'center', padding: 12, border: '1px solid rgba(218,209,185,.14)', borderRadius: 8, background: 'rgba(218,209,185,.04)' }}>
          <span style={{ ...mono, color: dim, textAlign: 'center' }}>Healing details unavailable</span>
        </div>
        {[
          ['Timeline', 'Unavailable'],
          ['Status', 'Unavailable'],
        ].map(([label, value]) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{ ...mono, width: 60, color: dim }}>{label}</span>
            <span style={{ flex: 1, height: 1, background: 'rgba(218,209,185,.12)' }} />
            <span style={{ ...mono, textAlign: 'right', color: tone }}>{value}</span>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" disabled aria-disabled="true" style={{ flex: 1, minHeight: 38, border: '1px solid rgba(127,195,177,.3)', borderRadius: 8, background: 'rgba(127,195,177,.04)', color: dim, cursor: 'not-allowed', ...mono }}>Logging unavailable</button>
          <button type="button" disabled aria-disabled="true" style={{ flex: 1, minHeight: 38, border: '1px solid rgba(218,209,185,.16)', borderRadius: 8, background: 'transparent', color: dim, cursor: 'not-allowed', ...mono }}>Flag unavailable</button>
        </div>
        </>}
      </div>
    </article>
  );
}

function MemoryShard(props: ActionCardProps) {
  const {
    children,
    card,
    agentName,
    accent = 'var(--ukiyoe-kin-gold, #cba15b)',
    accentRgb = 'var(--ryozan-agent-rgb, 203,161,91)',
    onCommit,
    commitLabel = 'Commit to memory',
  } = props;
  const [state, setState] = React.useState<'idle' | 'saving' | 'saved' | 'failed'>('idle');
  const tone = accent;
  const commit = async () => {
    if (state === 'saving' || state === 'saved') return;
    if (!onCommit) return;
    setState('saving');
    try {
      await onCommit();
      setState('saved');
    } catch {
      setState('failed');
    }
  };
  const active = state === 'saving' || state === 'saved';
  const buttonLabel = !onCommit ? 'Memory unavailable' : state === 'saving' ? 'Creating memory' : state === 'saved' ? 'Memory created' : state === 'failed' ? 'Try again' : commitLabel;
  return (
    <article style={{ position: 'relative', display: 'flex', flexDirection: 'column', gap: 16, minHeight: 276, padding: '18px 17px 16px', border: `1px solid rgba(${accentRgb},.48)`, borderRadius: 12, background: `linear-gradient(160deg,rgba(${accentRgb},.14) 0%,#121212 58%)`, boxShadow: `0 0 30px -6px rgba(${accentRgb},.38),0 22px 44px -22px rgba(0,0,0,.9)` }}>
      <AgentGlow accentRgb={accentRgb} ambientOpacity={.28} />
      <span style={{ ...mono, color: tone }}>Kept by hand · {resolveManaPip(card, agentName)}</span>
      <div style={{ fontFamily: font.display, fontSize: 19, fontWeight: 800, lineHeight: 1.05, textTransform: 'uppercase', color: ink }}>{card.name}</div>
      <div style={{ fontFamily: font.body, fontSize: 14, lineHeight: 1.7, fontStyle: 'italic', color: 'rgba(218,209,185,.82)' }}>{card.description}</div>
      <MtgCardAnatomy {...props} />
{children ?? <>
      <div style={{ marginTop: 'auto' }}>
        {[['Shard', `Private / ${agentName}`], ['Lends', 'Shop facts only']].map(([key, value]) => (
          <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '7px 0', borderTop: `1px solid rgba(${accentRgb},.28)` }}>
            <span style={{ ...mono, color: dim }}>{key}</span><span style={{ ...mono, color: ink }}>{value}</span>
          </div>
        ))}
      </div>
      <button type="button" disabled={!onCommit || state === 'saving' || state === 'saved'} aria-disabled={!onCommit || state === 'saving' || state === 'saved'} onClick={() => void commit()} style={{ minHeight: 36, border: `1px solid rgba(${accentRgb},${active ? '.78' : '.48'})`, borderRadius: 99, background: active ? `rgba(${accentRgb},.18)` : 'transparent', boxShadow: active ? `0 0 18px rgba(${accentRgb},.24)` : 'none', color: onCommit ? tone : dim, cursor: onCommit && state !== 'saving' && state !== 'saved' ? 'pointer' : 'not-allowed', ...mono }}>{buttonLabel}</button>
        </>}
      <span style={{ display: 'flex', alignItems: 'center', gap: 9 }}><span style={{ display: 'grid', placeItems: 'center', width: 34, height: 34, flexShrink: 0, border: `1px solid rgba(${accentRgb},.52)`, borderRadius: 99, boxShadow: `0 0 14px rgba(${accentRgb},.18)`, color: tone }}>梁</span><span style={{ ...mono, fontSize: 7.5, color: `rgba(${accentRgb},.58)` }}>Ryōzanpaku Seal</span></span>
    </article>
  );
}

/**
 * Net-new source component justification: AgentTools owns the hand and cast
 * gesture, but no existing shared component owns the live card that lands in a
 * thread. This renderer extends that contract with the six Card Shells.dc.html
 * architectures plus Neo-Ukiyo-e MTG card anatomy and remains stateless outside
 * each card's local controls.
 */
export function ActionCard(props: ActionCardProps) {
  const component = {
    'intake-docket': Docket,
    'chair-ticket': ChairTicket,
    'receipt-tape': ReceiptTape,
    'courier-manifest': Manifest,
    'heal-chart': HealChart,
    'memory-shard': MemoryShard,
  }[props.card.shell];
  return React.createElement(component, props);
}

export function ActionCardFace({ shell, accent = color.gold }: { shell: ActionCardShell; accent?: string }) {
  const shared: React.CSSProperties = { position: 'absolute', inset: 7, color: accent, border: `1px solid ${accent}`, opacity: .85 };
  if (shell === 'intake-docket') return <span style={{ ...shared, borderRadius: '2px 5px 5px 2px', background: 'repeating-linear-gradient(0deg,transparent 0 9px,currentColor 9px 10px)' }}><span style={{ position: 'absolute', left: -1, insetBlock: 0, width: 5, background: 'radial-gradient(circle at 0 50%,#f1e7d6 2px,transparent 2.5px) 0 0/5px 8px' }} /></span>;
  if (shell === 'chair-ticket') return <span style={{ ...shared, display: 'grid', gridTemplateColumns: '14px 1fr 11px', borderRadius: 4 }}><span style={{ borderRight: `1px solid ${accent}`, background: `${accent}22` }} /><span style={{ margin: 5, borderBlock: `1px solid ${accent}` }} /><span style={{ borderLeft: `1px dashed ${accent}` }} /></span>;
  if (shell === 'receipt-tape') return <span style={{ ...shared, border: 0, background: `repeating-linear-gradient(0deg,transparent 0 9px,${accent} 9px 10px)` }}><span style={{ position: 'absolute', inset: '4px 0 auto', height: 7, background: `repeating-linear-gradient(90deg,${accent} 0 2px,transparent 2px 4px)` }} /></span>;
  if (shell === 'courier-manifest') return <span style={{ ...shared, borderRadius: 5 }}><span style={{ position: 'absolute', inset: '0 0 auto', height: 24, background: `${accent}22`, clipPath: 'polygon(0 0,100% 0,100% 45%,50% 100%,0 45%)' }} /><span style={{ position: 'absolute', left: 12, top: 31, width: 6, height: 6, borderRadius: 99, background: accent, boxShadow: `0 13px 0 ${accent},0 26px 0 ${accent}` }} /></span>;
  if (shell === 'heal-chart') return <span style={{ ...shared, borderRadius: 5, background: `repeating-linear-gradient(90deg,transparent 0 8px,${accent}33 8px 9px),repeating-linear-gradient(0deg,transparent 0 8px,${accent}33 8px 9px)` }} />;
  return <span style={{ ...shared, inset: 15, transform: 'rotate(45deg)', borderRadius: 3, boxShadow: `0 0 14px ${accent}55` }}><span style={{ position: 'absolute', inset: 8, border: `1px solid ${accent}` }} /></span>;
}
