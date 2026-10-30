import * as React from 'react';

export interface GrainOverlayProps {
  /** Unique per mount point — two overlays sharing an id share a filter. */
  id?: string;
  /** 0–1. Scenes use .22 on panels, .30 on the nav rail. */
  opacity?: number;
  baseFrequency?: number;
}

/**
 * The fractal-noise film over panels. Purely decorative: aria-hidden,
 * pointer-events none, overlay blend. Absolutely positioned — the parent
 * must establish a positioning context (Panel already does).
 */
export function GrainOverlay({ id = 'bttcoGrain', opacity = 0.22, baseFrequency = 0.65 }: GrainOverlayProps) {
  return (
    <svg
      aria-hidden="true"
      width="100%"
      height="100%"
      preserveAspectRatio="none"
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        mixBlendMode: 'overlay',
        opacity,
        zIndex: 0,
      }}
    >
      <filter id={id}>
        <feTurbulence type="fractalNoise" baseFrequency={baseFrequency} numOctaves={5} stitchTiles="stitch" />
        <feColorMatrix type="saturate" values="0" />
      </filter>
      <rect x="0" y="0" width="100%" height="100%" filter={`url(#${id})`} />
    </svg>
  );
}
