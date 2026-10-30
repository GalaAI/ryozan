import * as React from 'react';

export interface AgentSparklesProps {
  /** RGB triplet for the active agent, such as 112,194,123. */
  accentRgb: string;
  /** Approximate sparkle count per 10,000 square pixels. */
  density?: number;
  minSize?: number;
  maxSize?: number;
  speed?: number;
  /** Initial particle rail. Bottom is used by the card-cast field. */
  origin?: 'field' | 'bottom';
}

interface SparkleParticle {
  x: number;
  y: number;
  size: number;
  phase: number;
  pulse: number;
  drift: number;
  rise: number;
  cross: boolean;
}

/**
 * Net-new source component justification: BackgroundField owns the single
 * shell-wide relic canvas and AgentGlow owns edges, so neither can supply a
 * transparent, agent-scoped interior field. This lightweight canvas keeps the
 * requested sparkle motion in the shared UI package without another particle
 * engine or motion dependency.
 */
export function AgentSparkles({
  accentRgb,
  density = 5.5,
  minSize = .6,
  maxSize = 1.5,
  speed = 1,
  origin = 'field',
}: AgentSparklesProps) {
  const canvasRef = React.useRef<HTMLCanvasElement>(null);

  React.useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    const host = canvas?.parentElement;
    if (!canvas || !context || !host) return;

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let width = 0;
    let height = 0;
    let particles: SparkleParticle[] = [];
    let frame = 0;
    let startedAt = performance.now();

    const makeParticles = (count: number): SparkleParticle[] => Array.from({ length: count }, (_, index) => ({
      x: Math.random(),
      y: origin === 'bottom' ? .94 + Math.random() * .055 : Math.random(),
      size: minSize + Math.random() * Math.max(.1, maxSize - minSize),
      phase: Math.random() * Math.PI * 2,
      pulse: .45 + Math.random() * 1.15,
      drift: (Math.random() - .5) * 5,
      rise: .02 + Math.random() * .035,
      cross: index % 7 === 0,
    }));

    const draw = (now: number) => {
      context.clearRect(0, 0, width, height);
      const elapsed = reducedMotion ? 0 : ((now - startedAt) / 1000) * speed;

      particles.forEach((particle) => {
        const wave = .5 + .5 * Math.sin(particle.phase + elapsed * particle.pulse);
        const x = particle.x * width + Math.sin(particle.phase + elapsed * .23) * particle.drift;
        const yProgress = ((particle.y - elapsed * particle.rise) % 1 + 1) % 1;
        const y = yProgress * height;
        const topFalloff = Math.max(0, Math.min(1, (yProgress - .06) / .22));
        const easedFalloff = topFalloff * topFalloff * (3 - 2 * topFalloff);
        const alpha = (.12 + wave * .72) * easedFalloff;
        const radius = particle.size * (.72 + wave * .38);

        context.save();
        context.translate(x, y);
        context.fillStyle = `rgba(${accentRgb},${alpha})`;
        context.shadowColor = `rgba(${accentRgb},${alpha * .8})`;
        context.shadowBlur = particle.cross ? 8 : 4;

        if (particle.cross) {
          context.fillRect(-radius * 2.7, -radius * .32, radius * 5.4, radius * .64);
          context.fillRect(-radius * .32, -radius * 2.7, radius * .64, radius * 5.4);
          context.rotate(Math.PI / 4);
          context.globalAlpha = .58;
          context.fillRect(-radius * 1.7, -radius * .22, radius * 3.4, radius * .44);
          context.fillRect(-radius * .22, -radius * 1.7, radius * .44, radius * 3.4);
        } else {
          context.beginPath();
          context.arc(0, 0, radius, 0, Math.PI * 2);
          context.fill();
        }
        context.restore();
      });

      if (!reducedMotion) frame = window.requestAnimationFrame(draw);
    };

    const resize = () => {
      window.cancelAnimationFrame(frame);
      frame = 0;
      const bounds = host.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, Math.round(bounds.width));
      height = Math.max(1, Math.round(bounds.height));
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      particles = makeParticles(Math.max(16, Math.round((width * height / 10000) * density)));
      startedAt = performance.now();
      draw(startedAt);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
    };
  }, [accentRgb, density, maxSize, minSize, origin, speed]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{ position: 'absolute', inset: 0, zIndex: 0, display: 'block', pointerEvents: 'none' }}
    />
  );
}
