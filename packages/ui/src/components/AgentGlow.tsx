import * as React from 'react';

export interface AgentGlowProps {
  /** RGB triplet for the associated agent, such as 183,149,232. */
  accentRgb: string;
  /** Radius of the localized edge light in CSS pixels. */
  spread?: number;
  /** Resting opacity when there is no pointer, touch, or keyboard focus. */
  ambientOpacity?: number;
  /** Peak opacity while the surface is active. */
  activeOpacity?: number;
  disabled?: boolean;
}

/**
 * Net-new source component justification: the portal has static agent bloom,
 * but no existing owner for pointer, touch, and focus-driven edge light. This
 * component adds that behavior without a package dependency or rainbow color
 * model and leaves layout, data, and action ownership with its parent surface.
 */
export function AgentGlow({
  accentRgb,
  spread = 76,
  ambientOpacity = .2,
  activeOpacity = .92,
  disabled = false,
}: AgentGlowProps) {
  const glowRef = React.useRef<HTMLSpanElement>(null);

  React.useEffect(() => {
    const glow = glowRef.current;
    const owner = glow?.parentElement;
    if (!glow || !owner || disabled) return;

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const setPosition = (clientX: number, clientY: number) => {
      const bounds = owner.getBoundingClientRect();
      glow.style.setProperty('--bttco-agent-glow-x', `${clientX - bounds.left}px`);
      glow.style.setProperty('--bttco-agent-glow-y', `${clientY - bounds.top}px`);
    };
    const setOpacity = (value: number) => glow.style.setProperty('--bttco-agent-glow-opacity', String(value));

    if (reducedMotion) {
      glow.style.setProperty('--bttco-agent-glow-x', '50%');
      glow.style.setProperty('--bttco-agent-glow-y', '50%');
      setOpacity(ambientOpacity);
      return;
    }

    let settleTimer: number | undefined;
    const settle = (delay = 0) => {
      window.clearTimeout(settleTimer);
      settleTimer = window.setTimeout(() => setOpacity(ambientOpacity), delay);
    };
    const onPointerMove = (event: PointerEvent) => {
      setPosition(event.clientX, event.clientY);
      setOpacity(activeOpacity);
    };
    const onPointerEnter = (event: PointerEvent) => {
      setPosition(event.clientX, event.clientY);
      setOpacity(activeOpacity);
    };
    const onPointerDown = (event: PointerEvent) => {
      setPosition(event.clientX, event.clientY);
      setOpacity(1);
    };
    const onPointerUp = () => settle(520);
    const onPointerLeave = () => settle();
    const onFocusIn = () => setOpacity(activeOpacity);
    const onFocusOut = (event: FocusEvent) => {
      if (!owner.contains(event.relatedTarget as Node | null)) settle();
    };

    owner.addEventListener('pointermove', onPointerMove);
    owner.addEventListener('pointerenter', onPointerEnter);
    owner.addEventListener('pointerdown', onPointerDown);
    owner.addEventListener('pointerup', onPointerUp);
    owner.addEventListener('pointercancel', onPointerUp);
    owner.addEventListener('pointerleave', onPointerLeave);
    owner.addEventListener('focusin', onFocusIn);
    owner.addEventListener('focusout', onFocusOut);
    return () => {
      window.clearTimeout(settleTimer);
      owner.removeEventListener('pointermove', onPointerMove);
      owner.removeEventListener('pointerenter', onPointerEnter);
      owner.removeEventListener('pointerdown', onPointerDown);
      owner.removeEventListener('pointerup', onPointerUp);
      owner.removeEventListener('pointercancel', onPointerUp);
      owner.removeEventListener('pointerleave', onPointerLeave);
      owner.removeEventListener('focusin', onFocusIn);
      owner.removeEventListener('focusout', onFocusOut);
    };
  }, [activeOpacity, ambientOpacity, disabled]);

  if (disabled) return null;

  return (
    <span
      ref={glowRef}
      aria-hidden="true"
      data-agent-glow={accentRgb}
      style={{
        position: 'absolute',
        inset: -1,
        zIndex: 8,
        borderRadius: 'inherit',
        padding: 1,
        pointerEvents: 'none',
        background: `radial-gradient(${spread}px circle at var(--bttco-agent-glow-x,50%) var(--bttco-agent-glow-y,50%),rgba(${accentRgb},.98) 0%,rgba(${accentRgb},.62) 34%,rgba(${accentRgb},.18) 56%,rgba(${accentRgb},0) 76%)`,
        WebkitMask: 'linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0)',
        WebkitMaskComposite: 'xor',
        mask: 'linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0)',
        maskComposite: 'exclude',
        opacity: 'var(--bttco-agent-glow-opacity,.2)',
        filter: `drop-shadow(0 0 7px rgba(${accentRgb},.58))`,
        transition: 'opacity .24s ease',
        willChange: 'opacity,background',
      } as React.CSSProperties}
    />
  );
}
