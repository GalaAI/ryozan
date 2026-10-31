import * as React from 'react';
import { color, font } from '../tokens';
import { ActionCardFace, type AgentActionCard } from '../components/ActionCard';
import { AgentSparkles } from '../components/AgentSparkles';

export type { AgentActionCard } from '../components/ActionCard';

export type AgentToolTab = 'upload' | 'cards' | 'memory';

export interface AgentMemoryItem {
  id: string;
  text: React.ReactNode;
  source: React.ReactNode;
  shared?: boolean;
  onForget?: () => void;
}

export interface AgentToolsProps {
  open: boolean;
  onClose: () => void;
  agentName: string;
  agentGlyph: React.ReactNode;
  accent?: string;
  accentRgb?: string;
  cards: AgentActionCard[];
  onCast: (card: AgentActionCard) => void;
  onUpload?: (file: File) => void;
  memory?: AgentMemoryItem[];
  initialTab?: AgentToolTab;
  scrimTop?: string;
  /** Render inside AgentChatSheet as an in-flow dock with no scrim. */
  docked?: boolean;
}

const tabs: Array<{ id: AgentToolTab; label: string }> = [
  { id: 'upload', label: 'Upload' },
  { id: 'cards', label: 'Cards' },
  { id: 'memory', label: 'Memory' },
];

/**
 * Source-extracted Agent Tools layer from Portal Mobile v2.dc.html.
 * It can float above the portal or dock inside AgentChatSheet. Both modes emit
 * a typed card or upload intent instead of owning application data or execution.
 */
export function AgentTools({
  open,
  onClose,
  agentName,
  agentGlyph,
  accent = color.gold,
  accentRgb = '166,125,67',
  cards,
  onCast,
  onUpload,
  memory = [],
  initialTab = 'cards',
  scrimTop = '0',
  docked = false,
}: AgentToolsProps) {
  const [activeTab, setActiveTab] = React.useState<AgentToolTab>(initialTab);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [castingId, setCastingId] = React.useState<string | null>(null);
  const [dragY, setDragY] = React.useState(0);
  const cardRefs = React.useRef(new Map<string, HTMLButtonElement>());
  const dragRef = React.useRef<{ pointerId: number; startY: number; cardId: string; currentY: number } | null>(null);
  const suppressClickRef = React.useRef(false);

  React.useEffect(() => {
    if (!open) return;
    setActiveTab(initialTab);
    setSelectedId(null);
    setCastingId(null);
    setDragY(0);
    dragRef.current = null;
  }, [initialTab, open, agentName]);

  if (!open) return null;

  const selected = cards.find((card) => card.id === selectedId) ?? null;

  const cast = () => {
    if (!selected || castingId) return;
    const selectedElement = cardRefs.current.get(selected.id);
    const allElements = cards.map((card) => cardRefs.current.get(card.id)).filter((element): element is HTMLButtonElement => Boolean(element));
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    setCastingId(selected.id);
    setDragY(0);
    const finish = () => {
      onCast(selected);
      setCastingId(null);
      setSelectedId(null);
      onClose();
    };

    if (reducedMotion || !selectedElement || typeof selectedElement.animate !== 'function') {
      finish();
      return;
    }

    allElements.filter((element) => element !== selectedElement).forEach((element, index) => {
      const direction = index % 2 ? -1 : 1;
      element.animate(
        [
          { transform: element.style.transform, opacity: 1 },
          { transform: `translate(${direction * (16 + index * 10)}px, 360px) scale(.88) rotate(${direction * (38 + index * 14)}deg)`, opacity: 0 },
        ],
        { duration: 440, delay: index * 60, easing: 'cubic-bezier(.4,0,1,1)', fill: 'forwards' },
      );
    });

    const played = selectedElement.animate(
      [
        { transform: selectedElement.style.transform, opacity: 1, offset: 0 },
        { transform: 'translateY(-52px) scale(1.1) rotate(0deg) rotateY(0deg)', opacity: 1, offset: .28 },
        { transform: 'translateY(-52px) scale(1.1) rotate(0deg) rotateY(180deg)', opacity: 1, offset: .72 },
        { transform: 'translateY(-132px) scale(.88) rotate(0deg) rotateY(180deg)', opacity: 0, offset: 1 },
      ],
      { duration: 1140, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards' },
    );
    played.addEventListener('finish', finish, { once: true });
  };

  return (
    <>
      {!docked ? (
        <div
          onClick={onClose}
          aria-hidden="true"
          style={{
            position: 'absolute',
            top: scrimTop,
            left: 0,
            right: 0,
            bottom: 80,
            zIndex: 77,
            background: 'rgba(5,6,5,.72)',
            backdropFilter: 'blur(8px) saturate(.72)',
            WebkitBackdropFilter: 'blur(8px) saturate(.72)',
          }}
        />
      ) : null}
      <div
        role={docked ? 'region' : 'dialog'}
        aria-label={`${agentName} tools`}
        className="ryozan-agent-tools"
        style={docked
          ? {
              position: 'relative',
              zIndex: 2,
              flex: '1 1 auto',
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              pointerEvents: 'none',
              overflow: 'visible',
              isolation: 'isolate',
              background: 'transparent',
            }
          : { position: 'absolute', left: 0, right: 0, bottom: 78, zIndex: 78, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', gap: 10, pointerEvents: 'none' }}
      >
        {docked ? (
          <div className="ryozan-agent-sparkles-layer" aria-hidden="true" style={{ position: 'fixed', inset: 0, zIndex: 0, overflow: 'hidden', pointerEvents: 'none' }}>
            <AgentSparkles accentRgb={accentRgb} density={7} speed={1.8} origin="bottom" />
          </div>
        ) : null}
        {activeTab === 'upload' ? (
          <div className="ryozan-agent-tool-layer ryozan-agent-tool-layer--upload" style={{ position: 'relative', zIndex: 1, flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', padding: '13px 26px', pointerEvents: 'none' }}>
            <label aria-label={`Upload to ${agentName}`} style={{ pointerEvents: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 7, minHeight: 116, border: `1px dashed ${accent}`, borderRadius: 14, background: 'rgba(19,20,19,.60)', boxShadow: `inset 0 1px 0 rgba(var(--cream-rgb),.045), inset 0 -22px 34px rgba(0,0,0,.42), inset 0 0 24px rgba(${accentRgb},.055), 0 12px 28px -20px rgba(0,0,0,.95)`, cursor: 'pointer' }}>
              <input type="file" accept="image/*" onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload?.(file); }} style={{ display: 'none' }} />
              <span aria-hidden="true" style={{ fontSize: 24, color: accent, opacity: .5 }}>＋</span>
              <span style={{ fontFamily: font.body, fontSize: 14.5, color: color.text, opacity: .5 }}>Upload</span>
            </label>
          </div>
        ) : null}

        {activeTab === 'memory' ? (
          <div className="ryozan-agent-tool-layer ryozan-agent-tool-layer--memory" style={{ position: 'relative', zIndex: 1, flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', overflow: 'hidden', pointerEvents: 'none' }}>
            <div style={{ pointerEvents: 'auto', margin: '0 12px', display: 'flex', flexDirection: 'column', gap: 7, maxHeight: 340, overflowY: 'auto', padding: 13, border: `1px solid ${accent}`, borderRadius: 18, background: 'linear-gradient(180deg,rgba(24,23,21,.995),rgba(11,12,11,1))' }}>
              {!memory.length ? <span style={{ fontFamily: font.body, fontSize: 12, lineHeight: 1.5, color: color.textMuted }}>Nothing remembered yet. Close a thread and what was said gets compacted here.</span> : null}
              {memory.map((item, index) => (
                <React.Fragment key={item.id}>
                  {item.shared && !memory.slice(0, index).some((candidate) => candidate.shared) ? <span style={{ fontFamily: font.mono, fontSize: 9, fontWeight: 700, letterSpacing: '.18em', textTransform: 'uppercase', color: color.textDim, paddingTop: 4 }}>From the hive</span> : null}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3, padding: '9px 11px', borderLeft: `2px solid ${item.shared ? accent : 'rgba(217,191,149,.35)'}`, background: 'rgba(19,20,19,.94)' }}>
                    <span style={{ fontFamily: font.body, fontSize: 12, lineHeight: 1.5, color: color.text }}>{item.text}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontFamily: font.mono, fontSize: 9, letterSpacing: '.12em', textTransform: 'uppercase', color: color.textDim }}>{item.source}</span>
                      <span style={{ flex: 1 }} />
                      {item.onForget ? <button type="button" onClick={item.onForget} style={{ border: 0, background: 'transparent', padding: 0, fontFamily: font.mono, fontSize: 9, letterSpacing: '.12em', textTransform: 'uppercase', color: color.textDim, cursor: 'pointer' }}>Forget</button> : null}
                    </div>
                  </div>
                </React.Fragment>
              ))}
            </div>
          </div>
        ) : null}

        {activeTab === 'cards' ? (
          <div className="ryozan-agent-tool-layer ryozan-agent-tool-layer--cards" style={{ position: 'relative', zIndex: 1, flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', overflow: 'visible', pointerEvents: 'none' }}>
            <div style={{ position: 'relative', pointerEvents: 'auto', display: 'flex', alignItems: 'flex-end', justifyContent: 'center', padding: '30px 4px 2px', perspective: 900 }}>
              {cards.map((card, index) => {
                const selectedCard = selectedId === card.id;
                const rotation = (index - (cards.length - 1) / 2) * 9;
                return (
                  <button
                    key={card.id}
                    ref={(element) => { if (element) cardRefs.current.set(card.id, element); else cardRefs.current.delete(card.id); }}
                    type="button"
                    onClick={() => {
                      if (suppressClickRef.current) {
                        suppressClickRef.current = false;
                        return;
                      }
                      if (!castingId) setSelectedId(card.id);
                    }}
                    onPointerDown={(event) => {
                      if (!selectedCard || castingId) return;
                      dragRef.current = { pointerId: event.pointerId, startY: event.clientY, cardId: card.id, currentY: 0 };
                      event.currentTarget.setPointerCapture(event.pointerId);
                      setDragY(0);
                    }}
                    onPointerMove={(event) => {
                      const drag = dragRef.current;
                      if (!drag || drag.pointerId !== event.pointerId || drag.cardId !== card.id) return;
                      const nextY = Math.max(-92, Math.min(0, event.clientY - drag.startY));
                      drag.currentY = nextY;
                      if (nextY < -4) suppressClickRef.current = true;
                      setDragY(nextY);
                    }}
                    onPointerUp={(event) => {
                      const drag = dragRef.current;
                      if (!drag || drag.pointerId !== event.pointerId || drag.cardId !== card.id) return;
                      dragRef.current = null;
                      if (drag.currentY <= -44) cast();
                      else setDragY(0);
                    }}
                    onPointerCancel={() => {
                      dragRef.current = null;
                      setDragY(0);
                    }}
                    onKeyDown={(event) => {
                      if (selectedCard && event.key === 'ArrowUp') {
                        event.preventDefault();
                        cast();
                      }
                    }}
                    aria-label={typeof card.name === 'string' ? card.name : `Select ${agentName} card ${index + 1}`}
                    aria-pressed={selectedCard}
                    style={{ position: 'relative', flex: '0 0 auto', width: 72, height: 104, marginLeft: index === 0 ? 0 : -22, padding: 0, border: `1px solid ${selectedCard ? accent : 'rgba(var(--cream-rgb),.14)'}`, borderRadius: 9, background: 'linear-gradient(160deg,#1b1a18,#0d0e0d)', cursor: castingId ? 'default' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', transformOrigin: '50% 140%', transformStyle: 'preserve-3d', transform: `translateY(${selectedCard ? -18 + dragY : 0}px) rotate(${rotation}deg)`, transition: dragRef.current?.cardId === card.id ? 'border-color .22s ease' : 'transform .28s cubic-bezier(.16,1,.3,1), border-color .22s ease', boxShadow: '0 12px 24px -14px rgba(0,0,0,.95)', touchAction: selectedCard ? 'none' : 'manipulation' }}
                  >
                    <span className={`ryozan-agent-card-rim${selectedCard ? ' ryozan-agent-card-rim--selected' : ''}`} aria-hidden="true" style={{ position: 'absolute', inset: -1, zIndex: -1, borderRadius: 10, pointerEvents: 'none', boxShadow: selectedCard ? `0 0 0 1px rgba(${accentRgb},.55), 0 0 9px rgba(${accentRgb},.45), 0 0 22px rgba(${accentRgb},.22), 0 0 44px rgba(${accentRgb},.1)` : 'none' }} />
                    <span aria-hidden="true" style={{ position: 'absolute', inset: 0, display: 'block', borderRadius: 8, background: 'linear-gradient(165deg,#171817,#0b0c0b)', transform: 'rotateY(180deg)', backfaceVisibility: 'hidden' }}>
                      <ActionCardFace shell={card.shell} accent={accent} />
                    </span>
                    <span className={`ryozan-agent-card-emblem${selectedCard ? ' ryozan-agent-card-emblem--selected' : ''}`} aria-hidden="true" style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', backfaceVisibility: 'hidden', fontSize: 30, lineHeight: 1, color: 'rgba(0,0,0,.55)', textShadow: `0 1px 0 ${selectedCard ? 'rgba(var(--cream-rgb),.16)' : 'rgba(var(--cream-rgb),.08)'}, 0 -1px 1px rgba(0,0,0,.6)` }}>{agentGlyph}</span>
                  </button>
                );
              })}
            </div>
            <div style={{ position: 'relative', pointerEvents: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, padding: '8px', margin: '6px 8px', borderRadius: 10, background: 'transparent' }}>
              <span style={{ fontFamily: font.display, fontWeight: 800, fontSize: 13, textTransform: 'uppercase', letterSpacing: '.04em', color: color.text }}>{selected?.name ?? 'Pick a card'}</span>
              <span style={{ textAlign: 'center', fontFamily: font.body, fontSize: 11.5, lineHeight: 1.45, color: color.textMuted }}>{selected?.description ?? 'Choose a card to reveal its action.'}</span>
              <span aria-label="Swipe up to cast card" style={{ minHeight: 34, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, color: accent, opacity: selected ? 1 : .42, fontFamily: font.mono, fontSize: 9, fontWeight: 700, letterSpacing: '.14em', textTransform: 'uppercase', textShadow: selected ? `0 0 6px rgba(${accentRgb},.72), 0 0 14px rgba(${accentRgb},.38)` : 'none', transition: 'opacity .18s ease, text-shadow .22s ease' }}>
                <span>Swipe</span>
                <svg width="13" height="16" viewBox="0 0 13 16" fill="none" aria-hidden="true"><path d="M6.5 15V2M1.5 7 6.5 2l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
                <span>to cast card</span>
              </span>
            </div>
          </div>
        ) : null}

        <div style={{ position: 'relative', zIndex: 2, flexShrink: 0, pointerEvents: 'auto', display: 'flex', alignItems: 'stretch', background: 'transparent' }}>
          {tabs.map((tab) => {
            const active = activeTab === tab.id;
            return (
              <button key={tab.id} type="button" onClick={() => { setActiveTab(tab.id); setSelectedId(null); }} aria-pressed={active} style={{ position: 'relative', flex: 1, minHeight: 42, border: 0, backgroundColor: 'transparent', backgroundImage: active ? `linear-gradient(90deg,rgba(${accentRgb},0) 0%,rgba(${accentRgb},.95) 26%,rgba(${accentRgb},.95) 74%,rgba(${accentRgb},0) 100%)` : 'none', backgroundRepeat: 'no-repeat', backgroundPosition: 'bottom center', backgroundSize: '100% 2px', color: active ? color.text : color.textDim, textShadow: active ? `0 0 12px rgba(${accentRgb},.5)` : 'none', fontFamily: font.mono, fontSize: 10, fontWeight: 700, letterSpacing: '.12em', textTransform: 'uppercase', cursor: 'pointer' }}>
                <span aria-hidden="true" style={{ position: 'absolute', left: '-34%', right: '-34%', bottom: -30, height: 96, background: active ? `radial-gradient(46% 44% at 50% 68%,rgba(${accentRgb},.34) 0%,rgba(${accentRgb},.26) 18%,rgba(${accentRgb},.16) 36%,rgba(${accentRgb},.085) 52%,rgba(${accentRgb},.04) 66%,rgba(${accentRgb},.015) 80%,rgba(${accentRgb},0) 100%)` : 'none', pointerEvents: 'none', marginTop: -36 }} />
                <span style={{ position: 'relative', display: 'inline-block', transform: active ? 'scale(1.09)' : 'scale(1)', transition: 'transform .2s cubic-bezier(.16,1,.3,1)' }}>{tab.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}
