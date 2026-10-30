import * as React from 'react';

export interface BackgroundFieldProps {
  /**
   * Trough and crest alpha. Scene values are .05 / .16 — brighter than the
   * token defaults because the portal sits on a darker ground.
   */
  a0?: number;
  a1?: number;
  /** Mark colour, "r,g,b". Defaults to bone. */
  rgb?: string;
  /** Star colour, "r,g,b". Scenes use lifted gold 200,161,94. */
  starRgb?: string;
  /** Resting dim applied to stars only (lightness, alpha). */
  starRestL?: number;
  starRestA?: number;
  /** Pointer response. 0 disables the hover lift. */
  hover?: number;
  /** Seconds per pass, and the held rest between passes. */
  passSecs?: number;
  restSecs?: number;
  /** fixed = viewport (desktop shells); absolute = inside a frame (phone). */
  position?: 'fixed' | 'absolute';
  /** Slow breathing of the crest. Needs GSAP; skipped silently without it. */
  breathe?: boolean;
  style?: React.CSSProperties;
}

/**
 * The starseed field — the portal's background system, not decoration.
 *
 * A drifting dot-wave with stars, rendered to canvas by RelicField. It is
 * never off: it rests at a fraction of the crest and a pass lifts from there.
 * Honours prefers-reduced-motion by rendering one static frame.
 *
 * Mount ONE per shell, as the first child, behind everything. The field reads
 * its parameters off this element's own custom properties, so nesting two
 * fields fights over the same host.
 */
export function BackgroundField({
  a0 = 0.05, a1 = 0.16, rgb, starRgb = '200,161,94',
  starRestL = 0.75, starRestA = 0.55, hover = 1,
  passSecs = 11, restSecs = 2.5,
  position = 'absolute', breathe = true, style,
}: BackgroundFieldProps) {
  const hostRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);

  React.useEffect(() => {
    const w = window as unknown as {
      RelicField?: { start: (c: HTMLCanvasElement, host: HTMLElement) => void };
      gsap?: { to: (t: unknown, v: Record<string, unknown>) => { kill: () => void } };
    };
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;

    let tween: { kill: () => void } | undefined;
    let tries = 0;
    let dead = false;

    const boot = () => {
      if (dead) return;
      if (w.RelicField) {
        w.RelicField.start(canvas, host);
        if (breathe && w.gsap) {
          tween = w.gsap.to(host, {
            '--field-a1': a1 * 1.3,
            duration: 9, ease: 'sine.inOut', yoyo: true, repeat: -1,
          });
        }
        return;
      }
      // RelicField loads as a plain script; wait for it rather than assuming.
      if (tries++ < 40) setTimeout(boot, 200);
    };
    boot();

    return () => { dead = true; tween?.kill(); };
  }, [a1, breathe]);

  const vars = {
    '--field-a0': a0,
    '--field-a1': a1,
    ...(rgb ? { '--field-rgb': rgb } : {}),
    '--field-star-rgb': starRgb,
    '--field-star-rest-l': starRestL,
    '--field-star-rest-a': starRestA,
    '--field-hover': hover,
    '--field-pass-secs': passSecs,
    '--field-rest-secs': restSecs,
  } as React.CSSProperties;

  return (
    <div ref={hostRef} aria-hidden="true" style={{ position, inset: 0, zIndex: 0, pointerEvents: 'none', ...vars, ...style }}>
      <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
    </div>
  );
}
