import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import gsap from 'gsap'
import './LogoLoader.css'

/**
 * LogoLoader — native port of the frozen LogoLoaderMobile.dc.html.
 *
 * Runs on the app's own gsap. No iframe, no support.js, no second React, no
 * Babel transpiling in the browser, and no authoring pause control.
 *
 * The pour is a real surface simulation: the waterline is 33 spring-coupled
 * points (shallow-water propagation + damping). A stream, wandered by a gsap
 * sine tween, pushes the surface down where it lands; displaced water travels
 * outward as waves. gsap.ticker is the clock and a power2.out tween owns the
 * rise, so the pour starts fast and eases off like a real vessel filling.
 * Disturbance decays with fill so 100% lands dead level. Rendered as a
 * clip-path polygon: hard edge, no gradients.
 *
 * Every physical constant, keyframe and timing below is copied from the frozen
 * file. Defaults are the operator's approved values from "Login Compare", not
 * the package defaults, so they live in source and cannot be stripped by an
 * external restore.
 */

const MARK = '/assets/brand/ryozan-mark.svg'

export interface LogoLoaderProps {
  /** Pour runtime, seconds. */
  duration?: number
  /** Hold between the breathe and the slingshot, seconds. */
  buffer?: number
  /** Pour turbulence. Visual only. */
  chop?: number
  /** Restart after the outro. False for the login gate. */
  loop?: boolean
  /** Fires when the composition has finished fading. The frozen file had no
   *  callback, which forced every host to re-derive the cue arithmetic. */
  onFinished?: () => void

  textAngle?: number
  textSize?: number
  lineGap?: number
  strangerShift?: number
  hangTime?: number
  swell?: number
  welcomeColor?: string
  strangerColor?: string
  shadowOn?: boolean
  shadowX?: number
  shadowY?: number
  shadowBlur?: number
  shadowAlpha?: number
  welcomeShadowColor?: string
  strangerShadowColor?: string
  outlineOn?: boolean
  outlineSize?: number
  starsOn?: boolean
  starScale?: number
  starSpreadX?: number
  starSpreadY?: number
  starColor?: string
  starAltColor?: string
  twinkleSpeed?: number
}

type Phase = 'fill' | 'bloom' | 'launch'

/** Star field, positions and both animation delays copied from the frozen file. */
const STARS: Array<{ l: string; t: string; s: string; alt?: boolean; d: string; tall?: boolean }> = [
  { l: '30%', t: '8%', s: '.34em', d: '1.45s,2.4s' },
  { l: '54%', t: '3%', s: '.45em', alt: true, d: '1.55s,2.9s' },
  { l: '81%', t: '9%', s: '.37em', d: '1.5s,3.1s' },
  { l: '90%', t: '37%', s: '.53em', alt: true, d: '1.62s,2.6s', tall: true },
  { l: '97%', t: '62%', s: '.29em', d: '1.58s,3s' },
  { l: '87%', t: '88%', s: '.2em', alt: true, d: '1.7s,3.2s' },
  { l: '57%', t: '96%', s: '.53em', d: '1.6s,2.65s', tall: true },
  { l: '30%', t: '93%', s: '.43em', alt: true, d: '1.76s,3.15s' },
  { l: '10%', t: '80%', s: '.26em', d: '1.52s,2.5s' },
  { l: '2%', t: '49%', s: '.2em', alt: true, d: '1.68s,2.75s' },
  { l: '8%', t: '22%', s: '.24em', d: '1.65s,3.05s' },
  { l: '6%', t: '0%', s: '.66em', alt: true, d: '1.74s,2.95s', tall: true },
  { l: '69%', t: '12%', s: '.26em', d: '1.54s,2.7s' },
  { l: '88%', t: '23%', s: '.26em', d: '1.63s,3.08s' },
  { l: '46%', t: '96%', s: '.26em', alt: true, d: '1.71s,2.85s' },
]

const markLayer: CSSProperties = {
  position: 'absolute',
  inset: 0,
  backgroundImage: `url('${MARK}')`,
  backgroundSize: 'contain',
  backgroundRepeat: 'no-repeat',
  backgroundPosition: '50% 50%',
}

function rgba(hex: string, a: number): string {
  const v = (hex ?? '#f4ecdd').replace('#', '')
  const n = parseInt(v.length === 3 ? v.split('').map((c) => c + c).join('') : v, 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}

export default function LogoLoader({
  duration = 2.4,
  buffer = 0.2,
  chop = 2.3,
  loop = false,
  onFinished,
  textAngle = -9,
  textSize = 114,
  lineGap = 0.92,
  strangerShift = 6,
  hangTime = 4.75,
  swell = 1.22,
  welcomeColor = '#fd8e8e',
  strangerColor = '#63badf',
  shadowOn = true,
  shadowX = -5,
  shadowY = 4,
  shadowBlur = 0,
  shadowAlpha = 0.9,
  welcomeShadowColor = '#f4ecdd',
  strangerShadowColor = '#f4ecdd',
  outlineOn = true,
  outlineSize = 4,
  starsOn = true,
  starScale = 0.8,
  starSpreadX = 2,
  starSpreadY = 0,
  starColor = '#e4d9c0',
  starAltColor = '#c8a15e',
  twinkleSpeed = 0.75,
}: LogoLoaderProps) {
  const [phase, setPhase] = useState<Phase>('fill')
  // A new `loop` restarts the sequence below; the phase resets during render.
  const [phaseLoop, setPhaseLoop] = useState(loop)
  if (phaseLoop !== loop) {
    setPhaseLoop(loop)
    setPhase('fill')
  }
  const fillRef = useRef<HTMLSpanElement>(null)

  // Physics state. Refs, not state: this mutates every animation frame.
  const h = useRef<Float64Array>(new Float64Array(33))
  const v = useRef<Float64Array>(new Float64Array(33))
  const lq = useRef({ p: 0 })
  const pour = useRef({ x: 0.5 })
  const done = useRef(false)
  const timers = useRef<number[]>([])

  useEffect(() => {
    const N = 33
    h.current = new Float64Array(N)
    v.current = new Float64Array(N)
    lq.current = { p: 0 }
    pour.current = { x: 0.5 }
    done.current = false

    const at = (fn: () => void, ms: number) => { timers.current.push(window.setTimeout(fn, ms)) }

    /* 100%: near-instant bloom (.12s), one slow breathe, then slingshot. */
    const after = () => {
      gsap.ticker.remove(tick)
      tw1.kill()
      tw2?.kill()
      const el = fillRef.current
      if (el) el.style.clipPath = 'none'
      setPhase('bloom')
      at(() => {
        setPhase('launch')
        // Outro: wipe at +.36s, stars from +1.45s, hang, then fade.
        at(() => onFinished?.(), (0.36 + hangTime) * 1000)
      }, 1120 + buffer * 1000)
    }

    const step = (dt: number) => {
      const el = fillRef.current
      if (!el) return
      dt = Math.min(dt || 0.016, 1 / 30)
      /* the edge overshoots to 106%, so coverage is total at p ~ .963 — fire
         the bloom there instead of riding the eased tween's crawl to 1 */
      const p = lq.current.p
      const calm = Math.max(0, 1 - p)
      if (p >= 0.963 && !done.current) { done.current = true; after(); return }

      const hh = h.current
      const vv = v.current
      const drive = calm * chop

      /* the stream lands: push down at impact, spray to neighbors, some chaos */
      const idx = Math.max(1, Math.min(N - 2, Math.round(pour.current.x * (N - 1))))
      vv[idx] -= 680 * drive * dt
      vv[idx - 1] -= 320 * drive * dt
      vv[idx + 1] -= 320 * drive * dt
      vv[idx] += (Math.random() - 0.5) * 480 * drive * dt

      /* propagation: each point pulled toward its neighbors' mean */
      const k = 190
      for (let i = 0; i < N; i++) {
        const l = hh[i > 0 ? i - 1 : 1]
        const r = hh[i < N - 1 ? i + 1 : N - 2]
        vv[i] += k * (l + r - 2 * hh[i]) * dt
      }

      const df = Math.max(0, 1 - (2 + 3 * p) * dt)
      const cap = 30 * chop
      let mean = 0
      for (let i = 0; i < N; i++) {
        vv[i] *= df
        hh[i] += vv[i] * dt
        if (hh[i] > cap) hh[i] = cap
        else if (hh[i] < -cap) hh[i] = -cap
        mean += hh[i]
      }
      /* volume conservation: the surface's mean stays on the waterline */
      mean /= N
      for (let i = 0; i < N; i++) hh[i] -= mean * 0.08

      const H = el.offsetHeight || 1
      const yBase = H * (1 - (p * 106 - 2) / 100)
      let pts = '0% 100%'
      for (let i = 0; i < N; i++) {
        pts += `,${((i / (N - 1)) * 100).toFixed(2)}% ${(yBase + hh[i]).toFixed(1)}px`
      }
      pts += ',100% 100%'
      el.style.clipPath = `polygon(${pts})`
    }

    const tw1 = gsap.to(lq.current, {
      p: 1, duration, ease: 'power2.out',
      onComplete: () => { if (!done.current) { done.current = true; after() } },
    })
    let tw2: gsap.core.Tween | undefined
    const wander = () => {
      tw2 = gsap.to(pour.current, {
        x: gsap.utils.random(0.22, 0.78),
        duration: gsap.utils.random(0.8, 1.7),
        ease: 'sine.inOut',
        onComplete: wander,
      })
    }
    wander()
    const tick = (_t: number, dtMs: number) => step((dtMs || 16) / 1000)
    gsap.ticker.add(tick)

    const running = timers
    return () => {
      gsap.ticker.remove(tick)
      tw1.kill()
      tw2?.kill()
      running.current.forEach(window.clearTimeout)
      running.current = []
    }
    // Restarting on a prop change would reset the pour mid-flight; the gate
    // mounts this once per sequence.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loop])

  /* outline: 16-direction zero-blur shadows in the background ink — sits on
     top of the drop shadow, never thins the glyphs like text-stroke would */
  const outline = outlineOn
    ? Array.from({ length: 16 }, (_, i) => {
        const a = (i * Math.PI) / 8
        return `${(Math.cos(a) * outlineSize).toFixed(1)}px ${(Math.sin(a) * outlineSize).toFixed(1)}px 0 #0b0c0b`
      }).join(',')
    : 'none'

  /* the drop shadow is a filter, not a text-shadow — so the outline rings the
     letters only and never paints over or clips the shadow */
  const sh = (c: string) =>
    shadowOn ? `drop-shadow(${shadowX}px ${shadowY}px ${shadowBlur}px ${rgba(c, shadowAlpha)})` : 'none'

  return (
    <div
      className="ll-root"
      style={{ ['--tw' as string]: twinkleSpeed, ['--ll-swell' as string]: swell, ['--ll-swell-end' as string]: swell + 0.04 }}
    >
      <div
        data-ll={phase}
        style={{ position: 'relative', zIndex: 1, width: 'min(340px,78vw)', aspectRatio: '2544/2210', margin: 40 }}
      >
        <span data-mark="1" aria-hidden="true" style={{ ...markLayer, opacity: 0.1, filter: 'grayscale(.4)' }} />
        <span
          data-mark="1"
          ref={fillRef}
          role="img"
          aria-label="Ryozan Studio tiger mark, loading"
          style={{ ...markLayer, clipPath: 'inset(100% 0 0 0)' }}
        />
        <span
          data-bloom="1"
          aria-hidden="true"
          style={{ ...markLayer, filter: 'drop-shadow(0 0 18px rgba(200,161,94,.4)) drop-shadow(0 0 44px rgba(200,161,94,.18))' }}
        />
      </div>

      {phase === 'launch' && (
        <span
          className="ll-welcome"
          style={{
            position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
            animation: `llSwipe .52s cubic-bezier(.7,0,.9,.42) .36s both, llHang ${hangTime}s cubic-bezier(.16,1,.3,1) .36s both`,
            zIndex: 0, padding: '0 24px',
          }}
        >
          <span
            style={{
              position: 'relative', width: '6.05em', height: '3.82em',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: `min(${textSize}px, 16.5vw)`,
            }}
          >
            <span
              style={{
                display: 'flex', flexDirection: 'column', lineHeight: lineGap,
                fontFamily: "'Suti','Brush Script MT',cursive",
                transform: `rotate(${textAngle}deg)`, fontSize: '1em',
              }}
            >
              <span style={{ display: 'block', color: welcomeColor, textShadow: outline, filter: sh(welcomeShadowColor) }}>
                Welcome
              </span>
              <span
                style={{
                  display: 'block', alignSelf: 'flex-end', transform: `translateX(${strangerShift}%)`,
                  color: strangerColor, textShadow: outline, filter: sh(strangerShadowColor),
                }}
              >
                Stranger
              </span>
            </span>

            <span
              style={{
                position: 'absolute', inset: `-${starSpreadY}% -${starSpreadX}%`, zIndex: 0,
                pointerEvents: 'none', display: starsOn ? 'block' : 'none',
                color: starColor, transform: `scale(${starScale})`,
              }}
            >
              {STARS.map((s, i) => (
                <span
                  key={i}
                  data-star={s.tall ? 'tall' : '1'}
                  aria-hidden="true"
                  style={{
                    left: s.l, top: s.t, fontSize: s.s,
                    color: s.alt ? starAltColor : undefined,
                    animationDelay: s.d,
                  }}
                >
                  ✦
                </span>
              ))}
            </span>
          </span>
        </span>
      )}
    </div>
  )
}
