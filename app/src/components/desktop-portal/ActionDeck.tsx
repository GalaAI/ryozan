import { useEffect, useRef, useState, type ReactNode } from 'react'
import { gsap } from 'gsap'
import { useGSAP } from '@gsap/react'

gsap.registerPlugin(useGSAP)

export function deckMagnification(center: number, pointer: number | null, reduced: boolean) {
  if (pointer === null || reduced) return 0
  const distance = Math.min(Math.abs(center - pointer) / 140, 1)
  return (1 + Math.cos(distance * Math.PI)) / 2
}

export interface DesktopDeckAction {
  shell?: import('@ryozan/ui').ActionCardShell
  id: string
  title: string
  description: string
  cta: string
  manaPip?: '⚡ Nano' | '👁️ Super-VL' | '🔥 Super' | '👑 Ultra'
  typeLine?: string
  oracleText?: string
  executedReceipt?: string
  run?: () => void
  component?: ReactNode
}

// The source cast bar owns this visual contract: overlapping 56x80 charcoal
// backs with embossed agent marks, Neo-Ukiyo-e Mana/Chi pips, and Hanko (済) stamps.
export function ActionDeck({ actions, glyph, working, onActivate }: { actions: DesktopDeckAction[]; glyph: ReactNode; working: boolean; onActivate: (action: DesktopDeckAction) => void }) {
  const root = useRef<HTMLDivElement>(null)
  const dock = useRef<HTMLDivElement>(null)
  const tooltip = useRef<HTMLDivElement>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const pending = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const showPreview = (id: string | null, immediate = false) => {
    if (pending.current === id && !immediate) return
    if (timer.current) clearTimeout(timer.current)
    pending.current = id
    if (!id || immediate) setHovered(id)
    else {
      setHovered(null)
      timer.current = setTimeout(() => setHovered(id), 180)
    }
  }
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const preview = actions.find(item => item.id === hovered)
  const { contextSafe } = useGSAP(() => {}, { scope: root })
  const hover = (clientX: number | null, activeId: string | null = null) => contextSafe(() => {
    if (!dock.current) return
    const row = dock.current
    const box = row.getBoundingClientRect()
    const zoom = box.width / row.offsetWidth || 1
    const pointer = clientX === null ? null : (clientX-box.left)/zoom
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const cards = Array.from(row.querySelectorAll<HTMLButtonElement>('[data-deck-card]'))
    const weights = cards.map(node => deckMagnification(node.offsetLeft + node.offsetWidth / 2, pointer, reduced))
    const expansion = weights.map((weight, index) => cards[index].offsetWidth * .38 * weight)
    let shift = -expansion.reduce((sum, value) => sum + value, 0) / 2
    cards.forEach((node,index) => {
      const weight = weights[index]
      node.dataset.hoverActive = String(node.dataset.deckCard === activeId)
      // Stable hit areas prevent scaling from feeding back into pointer distance.
      gsap.to(node.querySelector('[data-deck-back]'), { x: shift + expansion[index] / 2, y: reduced ? 0 : -18*weight, scale: 1+.38*weight, rotation: (index-(cards.length-1)/2)*3.2*(1-weight), duration: reduced ? 0 : .3, ease: 'back.out(1.2)', overwrite: true })
      shift += expansion[index]
      node.style.zIndex = String(1+Math.round(weight*5))
    })
    const activeCard = cards.find(node => node.dataset.deckCard === activeId)
    if (pointer !== null && activeCard && tooltip.current) {
      const center = activeCard.offsetLeft+activeCard.offsetWidth/2
      // Keep reading text upright and anchored to the card, not the pointer.
      gsap.to(tooltip.current, { x: center-row.offsetWidth/2, rotation: 0, duration: reduced ? 0 : .35, ease: 'power3.out', overwrite: 'auto' })
    }
  })()
  useGSAP(() => {
    if (!tooltip.current) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    gsap.to(tooltip.current, { autoAlpha: hovered ? 1 : 0, y: reduced || hovered ? 0 : 4, scale: reduced || hovered ? 1 : .96, duration: reduced ? 0 : .22, ease: 'power3.out', overwrite: 'auto' })
  }, { dependencies: [hovered], scope: root })
  return <div ref={root} data-action-deck style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 28 }}>
    <style>{`[data-action-deck] button{font-family:'JetBrains Mono',monospace;cursor:pointer}[data-deck-card]:focus-visible,[data-action-deck] button:focus-visible{outline:2px solid var(--ag-accent);outline-offset:3px}[data-hover-active=true] [data-deck-back]{border-color:color-mix(in oklab,var(--ag-accent) 55%,transparent)!important}`}</style>
    <style>{`[data-deck-glow]{position:absolute;inset:-1px;border-radius:10px;pointer-events:none;opacity:0;box-shadow:0 0 16px color-mix(in oklab,var(--ag-accent) 55%,transparent),0 0 34px color-mix(in oklab,var(--ag-accent) 22%,transparent)}[data-hover-active=true]{border-color:color-mix(in oklab,var(--ag-accent) 55%,transparent)!important}[data-hover-active=true] [data-deck-glow]{animation:deck-breathe 4.4s ease-in-out infinite}[data-hover-active=true] [data-deck-emblem]{animation:deck-emblem 4.4s ease-in-out infinite;text-shadow:0 1px 0 color-mix(in oklab,var(--ag-accent) 30%,transparent),0 -1px 1px rgba(0,0,0,.6)!important}@keyframes deck-breathe{0%,100%{opacity:.5}45%{opacity:1}}@keyframes deck-emblem{0%,100%{color:rgba(0,0,0,.5);filter:drop-shadow(0 0 0 transparent)}26%,48%{color:var(--ag-accent);filter:drop-shadow(0 0 8px var(--ag-accent))}}@media(prefers-reduced-motion:reduce){[data-deck-glow],[data-deck-emblem]{animation:none!important}[data-hover-active=true] [data-deck-glow]{opacity:1}[data-hover-active=true] [data-deck-emblem]{color:var(--ag-accent)!important;filter:drop-shadow(0 0 8px var(--ag-accent))}}`}</style>
    <div ref={tooltip} id="desktop-deck-tooltip" role="tooltip" aria-hidden={!hovered} style={{ visibility: 'hidden', position: 'absolute', left: '50%', bottom: 178, width: 'min(20rem, calc(100vw - var(--space-8)))', marginLeft: 'calc(min(20rem, calc(100vw - var(--space-8))) / -2)', padding: 'var(--space-3)', boxSizing: 'border-box', borderRadius: 'var(--radius-md)', border: '1.5px solid var(--ukiyoe-kin-gold, #cba15b)', background: 'var(--surface, #121212)', boxShadow: '0 var(--space-2) var(--space-6) rgba(0,0,0,.65)', transformOrigin: 'bottom center', textAlign: 'left', lineHeight: 1.4 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6, marginBottom: 4 }}>
        <strong style={{ display: 'block', fontSize: 'var(--text-base)', fontWeight: 900, lineHeight: 1.2, color: 'var(--ink, #dad1b9)' }}>{preview?.title}</strong>
        <span data-tooltip-mana-pip style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 99, background: '#1D3557', color: '#dad1b9', border: '1px solid #cba15b', whiteSpace: 'nowrap' }}>{preview?.manaPip ?? '🔥 Super'}</span>
      </div>
      {preview?.typeLine && <span style={{ display: 'block', fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: '#cba15b', borderLeft: '2px solid #c24b2a', paddingLeft: 6, marginBottom: 6 }}>{preview.typeLine}</span>}
      <span style={{ display: 'block', color: 'var(--ink-mute)', fontSize: 'var(--text-sm)', lineHeight: 1.5, overflowWrap: 'anywhere' }}>{preview?.oracleText ?? preview?.description}</span>
      <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6, paddingTop: 5, borderTop: '1px dashed rgba(203,161,91,.24)', fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: '#c24b2a' }}>
        <span>⤵ Tap to Cast</span>
        {preview?.executedReceipt && <span style={{ color: '#D94E34', fontWeight: 800 }}>済 · {preview.executedReceipt}</span>}
      </span>
    </div>
    <div ref={dock} role="group" aria-label="Page action cards" onPointerMove={event => { if (event.pointerType !== 'touch') { const id=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-deck-card]')?.dataset.deckCard ?? null; hover(event.clientX,id); showPreview(id) } }} onPointerLeave={() => { hover(null); showPreview(null) }} style={{ position: 'absolute', left: '50%', bottom: 20, transform: 'translateX(-50%)', display: 'flex', alignItems: 'flex-end', padding: '8px 18px 10px', pointerEvents: 'auto', perspective: 900 }}>
      {actions.map((item,index) => {
        // Only a real execution receipt seals a card; opening a card is not an execution.
        const receipt = item.executedReceipt
        const pipSymbol = (item.manaPip ?? '🔥').split(' ')[0]
        return <button key={item.id} type="button" data-deck-card={item.id} data-deck-tapped={receipt ? 'true' : undefined} aria-label={item.title} disabled={working} aria-describedby={hovered === item.id ? 'desktop-deck-tooltip' : undefined}
          onClick={() => {
            showPreview(null)
            hover(null)
            onActivate(item)
          }}
          onPointerEnter={event => { if (event.pointerType !== 'touch') { showPreview(item.id); hover(event.clientX,item.id) } }}
          onFocus={event => { showPreview(item.id, true); const box=event.currentTarget.getBoundingClientRect(); hover(box.left+box.width/2,item.id) }}
          onBlur={() => { hover(null); showPreview(null) }}
          onKeyDown={event => { if (event.key === 'Escape') { showPreview(null); hover(null) } }}
          style={{ position: 'relative', flex: '0 0 auto', width: 56, height: 80, marginLeft: index ? -9 : 0, padding: 0, border: 0, background: 'transparent', borderRadius: 9 }}>
          <span data-deck-back style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', pointerEvents: 'none', border: `1px solid ${receipt ? '#D94E34' : 'color-mix(in oklab,var(--ag-accent) 28%,transparent)'}`, borderRadius: 9, background: 'linear-gradient(160deg,#1b1a18,#050607)', transform: `rotate(${(index-(actions.length-1)/2)*3.2}deg)`, transformOrigin: '50% 140%', boxShadow: '0 12px 24px -14px rgba(0,0,0,.95)' }}>
            <span data-deck-glow aria-hidden="true" />
            <span aria-hidden="true" style={{ position: 'absolute', top: 4, right: 5, fontSize: 9, lineHeight: 1, opacity: .85 }}>{pipSymbol}</span>
            <span data-deck-emblem aria-hidden="true" style={{ fontSize: 26, color: 'rgba(0,0,0,.55)', textShadow: '0 1px 0 rgba(232,223,207,.10),0 -1px 1px rgba(0,0,0,.6)' }}>{glyph}</span>
            <span aria-hidden="true" style={{ position: 'absolute', left: 3, right: 3, bottom: 5, fontSize: 7.5, lineHeight: 1.15, letterSpacing: '.06em', textTransform: 'uppercase', color: 'rgba(232,223,207,.78)', textAlign: 'center', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{item.title}</span>
            {receipt && <span data-deck-hanko="executed" aria-hidden="true" style={{ position: 'absolute', bottom: 4, right: 4, width: 15, height: 15, borderRadius: 99, border: '1.5px solid #D94E34', background: 'rgba(217,78,52,.2)', color: '#D94E34', fontSize: 8.5, fontWeight: 900, display: 'grid', placeItems: 'center' }}>済</span>}
          </span>
        </button>
      })}
    </div>

  </div>
}
