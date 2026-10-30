import * as React from 'react';
import { color, font } from '../tokens';
import { AgentMark } from '../components/AgentMark';
import { ChatBubble } from '../components/ChatBubble';

/**
 * Height of the portal tab bar, published by PortalShell. The fallback matches
 * the shell's own minimum so the sheet still clears the nav if it is mounted
 * outside PortalShell.
 */
const NAV_CLEARANCE = 'var(--bttco-portal-nav-height, calc(78px + env(safe-area-inset-bottom, 0px)))';

export interface ChatLine {
  id: string;
  from: 'agent' | 'user';
  body: React.ReactNode;
  presentation?: 'bubble' | 'card';
  /** Animate a newly returned agent response without replaying stored history. */
  animateReply?: boolean;
}

export interface AgentChatSheetProps {
  open: boolean;
  onClose: () => void;
  agentName: string;
  agentGlyph: React.ReactNode;
  accent?: string;
  /** RGB triplet used by the source scene's agent wash. */
  accentRgb?: string;
  /** Sub-line under the agent name, normally its role. */
  status?: React.ReactNode;
  lines: ChatLine[];
  starters?: Array<{ id: string; label: React.ReactNode; onSelect: () => void }>;
  composer?: React.ReactNode;
  /** Optional in-flow tools dock. It shrinks the thread instead of covering it. */
  dock?: React.ReactNode;
  /** Standalone control anchored inside the sheet when a compact surface has no composer. */
  floatingAction?: React.ReactNode;
  working?: boolean;
  scrim?: boolean;
  /** Full chat rises from 12%. Docked chat occupies an explicitly measured page region. */
  placement?: 'full' | 'docked';
  dockedTop?: number | string;
  dockedBottom?: number | string;
  ariaLabel?: string;
}

/** Source-faithful Portal Mobile v2 agent thread. */
export function AgentChatSheet({
  open,
  onClose,
  agentName,
  agentGlyph,
  accent = color.gold,
  accentRgb = '166,125,67',
  status,
  lines,
  starters,
  composer,
  dock,
  floatingAction,
  working = false,
  scrim = true,
  placement = 'full',
  dockedTop = '12%',
  dockedBottom = 0,
  ariaLabel,
}: AgentChatSheetProps) {
  if (!open) return null;

  const docked = placement === 'docked';

  return (
    <>
      {scrim ? (
        <div
          className="bttco-agent-chat-scrim"
          onClick={onClose}
          aria-hidden="true"
          style={{
            position: 'absolute',
            top: 0,
            right: 0,
            bottom: NAV_CLEARANCE,
            left: 0,
            zIndex: 69,
            background: 'linear-gradient(180deg, rgba(7,8,7,.84) 0%, rgba(7,8,7,.82) 70%, rgba(7,8,7,.56) 88%, rgba(7,8,7,0) 100%)',
            backdropFilter: 'blur(7px)',
            WebkitBackdropFilter: 'blur(7px)',
            WebkitMaskImage: 'linear-gradient(180deg, #000 0%, #000 72%, rgba(0,0,0,.72) 88%, transparent 100%)',
            maskImage: 'linear-gradient(180deg, #000 0%, #000 72%, rgba(0,0,0,.72) 88%, transparent 100%)',
            pointerEvents: 'auto',
          }}
        />
      ) : null}
      <div
        role={docked ? 'region' : 'dialog'}
        aria-label={ariaLabel ?? `${agentName} chat`}
        aria-modal={!docked && scrim ? true : undefined}
        className="bttco-agent-chat-sheet"
        style={{
          '--bttco-agent-accent': accent,
          '--bttco-agent-rgb': accentRgb,
          position: 'absolute',
          ...(docked
            ? { top: dockedTop, right: 0, bottom: dockedBottom, left: 0 }
            : { inset: '12% 0 0' }),
          zIndex: 70,
          display: 'flex',
          flexDirection: 'column',
          borderTop: `2px solid rgba(${accentRgb},.45)`,
          borderRadius: '22px 22px 0 0',
          overflow: 'hidden',
          background: 'linear-gradient(180deg,#1e1f1e 0%,#1a1b1a 16%,#161716 34%,#121312 52%,#0f100f 70%,#0b0c0b 86%,#080908 100%)',
          boxShadow: '0 -16px 40px rgba(0,0,0,.55)',
        } as React.CSSProperties}
      >
        <span
          aria-hidden="true"
          style={{
            position: 'absolute',
            inset: '-2px 0 auto',
            zIndex: 4,
            height: 2,
            overflow: 'hidden',
            pointerEvents: 'none',
          }}
        >
          <span
            className="bttco-tab-bar__sweep"
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '38%',
              height: '100%',
              background: `linear-gradient(90deg,rgba(0,0,0,0),rgba(${accentRgb},.95),rgba(0,0,0,0))`,
              filter: 'blur(.4px)',
            }}
          />
        </span>
        <div
          className="bttco-agent-chat-header"
          style={{
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            gap: 11,
            padding: '13px 16px',
            borderBottom: `1px solid ${color.borderSubtle}`,
            background: `radial-gradient(140% 130% at 12% 0%, rgba(${accentRgb},.20), rgba(0,0,0,0) 62%), linear-gradient(180deg,#161815,#0d0f0d)`,
          }}
        >
          <AgentMark glyph={agentGlyph} accent={accent} scale={34} style={{ width: 36, height: 36 }} />
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 1 }}>
            <span
              style={{
                fontFamily: font.display,
                fontWeight: 800,
                fontSize: 15,
                textTransform: 'uppercase',
                letterSpacing: '.03em',
                color: color.text,
              }}
            >
              {agentName}
            </span>
            {status ? (
              <span style={{ fontFamily: font.btn, fontSize: 9, letterSpacing: '.12em', textTransform: 'uppercase', color: color.textMuted }}>
                {status}
              </span>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close chat"
            style={{
              flexShrink: 0,
              width: 34,
              height: 34,
              border: 0,
              borderRadius: 9,
              background: 'transparent',
              color: color.boneWhite,
              cursor: 'pointer',
              fontFamily: font.btn,
              fontSize: 27,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            ✕
          </button>
        </div>

        <div
          className="bttco-agent-chat-thread"
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 11,
            padding: floatingAction
              ? '14px 15px 68px'
              : composer
                ? '14px 15px calc(94px + env(safe-area-inset-bottom, 0px))'
                // No composer and no floating action still needs bottom clearance.
                // A full-placement sheet is inset '12% 0 0', so its lower edge runs
                // under the portal tab bar. Without this the last line of a
                // read-only card (the debrief summary) sits behind the nav and
                // cannot be scrolled into view.
                : `14px 15px calc(${NAV_CLEARANCE} + 14px)`,
            scrollPaddingBottom: composer
              ? 'calc(94px + env(safe-area-inset-bottom, 0px))'
              : floatingAction
                ? undefined
                : `calc(${NAV_CLEARANCE} + 14px)`,
          }}
        >
          {lines.map((line) => line.presentation === 'card' ? (
            <div key={line.id} className="bttco-agent-chat-line" style={{ width: '100%', flexShrink: 0 }}>
              {line.body}
            </div>
          ) : (
            <ChatBubble
              key={line.id}
              from={line.from}
              glyph={line.from === 'agent' ? agentGlyph : undefined}
              accent={accent}
              className={`bttco-agent-chat-line${line.animateReply ? ' bttco-agent-chat-line--typed' : ''}`}
              data-chat-line-id={line.id}
            >
              {line.body}
            </ChatBubble>
          ))}

          {starters?.length ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, paddingTop: 4 }}>
              {starters.map((starter) => (
                <button
                  key={starter.id}
                  type="button"
                  onClick={starter.onSelect}
                  style={{
                    minHeight: 38,
                    padding: '0 13px',
                    border: `1px solid ${color.panelBorder}`,
                    borderRadius: 99,
                    background: 'transparent',
                    color: color.text,
                    fontFamily: font.body,
                    fontSize: 12.5,
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  {starter.label}
                </button>
              ))}
            </div>
          ) : null}

          {working ? (
            <div className="bttco-agent-thinking" role="status" aria-label={`${agentName} is thinking`}>
              <AgentMark
                glyph={agentGlyph}
                accent={accent}
                scale={22}
                className="bttco-agent-thinking-mark"
                style={{ width: 30, height: 30 }}
              />
            </div>
          ) : null}
        </div>

        {floatingAction ? (
          <div className="bttco-agent-chat-floating-action" style={{ position: 'absolute', right: 14, bottom: 14, zIndex: 2 }}>
            {floatingAction}
          </div>
        ) : null}

        {dock ? (
          <div
            className="bttco-agent-chat-dock"
            style={{
              flexShrink: 0,
              maxHeight: '48%',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'visible',
              scrollbarWidth: 'none',
              marginBottom: composer ? 'calc(66px + env(safe-area-inset-bottom, 0px))' : 0,
            }}
          >
            {dock}
          </div>
        ) : null}

        {composer ? (
          <div
            className="bttco-agent-chat-composer"
            style={{
              position: 'absolute',
              right: 12,
              bottom: 'calc(12px + env(safe-area-inset-bottom, 0px))',
              left: 12,
              zIndex: 6,
              padding: 0,
              border: 0,
              background: 'transparent',
              filter: 'drop-shadow(0 12px 18px rgba(0,0,0,.42))',
            }}
          >
            {composer}
          </div>
        ) : null}
      </div>
    </>
  );
}
