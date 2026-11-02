import { useEffect, useRef } from 'react'
import gsap from 'gsap'
import { BRAND } from '../lib/brand'

/**
 * SignInWave: the gold seigaiha field behind the sign-in gate. Design system
 * card: SignInWave. Every value here comes from that card; retune it there first.
 *
 * Decorative only, and signing in never depends on it. Mouse only, so touch
 * screens get the still wave. Reduced motion is still too, and a hidden page
 * pauses.
 */

const LIGHT_RADIUS = 420
const FOLLOW_SECONDS = 0.4285714285714286
const FADE_SECONDS = 1.3
const RIPPLE_SPEED = 0.7
const RIPPLE_PIXELS = 3
const BACKING_MARGIN = 24

export default function SignInWave() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const lightRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const light = lightRef.current
    const ctx = canvas?.getContext('2d')
    const backing = document.createElement('canvas')
    const backctx = backing.getContext('2d')
    if (!canvas || !light || !ctx || !backctx) return

    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const tile = new Image()
    const pointer = { x: window.innerWidth / 2, y: window.innerHeight / 2, amount: 0 }
    let width = 0
    let height = 0
    let pattern: CanvasPattern | null = null
    let clock = 0
    let settled = false
    let follow: gsap.core.Tween | null = null
    let fade: gsap.core.Tween | null = null

    const paintLight = () => {
      light.style.setProperty('--wave-x', String(pointer.x))
      light.style.setProperty('--wave-y', String(pointer.y))
      light.style.setProperty('--wave-glow', String(pointer.amount))
    }

    const reset = () => {
      follow?.kill()
      fade?.kill()
      pointer.amount = 0
      settled = false
      paintLight()
    }

    const draw = (dt: number) => {
      if (!pattern) return
      clock += dt
      if (pointer.amount < 0.001) {
        // The wave is still until the pointer moves, so don't repaint it every frame.
        if (settled) return
        settled = true
      } else {
        settled = false
      }
      ctx.fillStyle = pattern
      ctx.fillRect(0, 0, width, height)
      if (pointer.amount < 0.001 || media.matches) return
      // Displace small cells of the pre-tiled backing canvas near the pointer.
      const step = Math.max(6, Math.ceil(LIGHT_RADIUS / 40))
      const firstY = Math.max(0, Math.floor((pointer.y - LIGHT_RADIUS) / step) * step)
      const firstX = Math.max(0, Math.floor((pointer.x - LIGHT_RADIUS) / step) * step)
      for (let y = firstY; y < Math.min(height, pointer.y + LIGHT_RADIUS); y += step) {
        for (let x = firstX; x < Math.min(width, pointer.x + LIGHT_RADIUS); x += step) {
          const dx = x + step / 2 - pointer.x
          const dy = y + step / 2 - pointer.y
          const distance = Math.hypot(dx, dy)
          const falloff = Math.max(0, 1 - distance / LIGHT_RADIUS)
          if (!falloff) continue
          const shift = Math.sin(distance * 0.025 - clock * 1.5 * RIPPLE_SPEED) * falloff * falloff * RIPPLE_PIXELS * pointer.amount
          const scale = Math.max(1, distance)
          ctx.drawImage(backing, x + BACKING_MARGIN + (dx / scale) * shift, y + BACKING_MARGIN + (dy / scale) * shift, step, step, x, y, step, step)
        }
      }
    }

    const resize = () => {
      width = window.innerWidth
      height = window.innerHeight
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (tile.naturalWidth) {
        pattern = ctx.createPattern(tile, 'repeat')
        backing.width = width + BACKING_MARGIN * 2
        backing.height = height + BACKING_MARGIN * 2
        backctx.setTransform(1, 0, 0, 1, BACKING_MARGIN, BACKING_MARGIN)
        const backingPattern = backctx.createPattern(tile, 'repeat')
        if (backingPattern) {
          backctx.fillStyle = backingPattern
          backctx.fillRect(-BACKING_MARGIN, -BACKING_MARGIN, backing.width, backing.height)
        }
      }
      settled = false
      draw(0)
    }

    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || media.matches || document.hidden) return
      follow?.kill()
      fade?.kill()
      follow = gsap.to(pointer, { x: event.clientX, y: event.clientY, amount: 1, duration: FOLLOW_SECONDS, ease: 'power2.out', onUpdate: paintLight })
      fade = gsap.to(pointer, { amount: 0, delay: FOLLOW_SECONDS, duration: FADE_SECONDS, ease: 'power2.out', onUpdate: paintLight })
    }

    const tick = (_time: number, deltaMs: number) => draw(Math.min(deltaMs / 1000, 0.05))

    const sync = () => {
      gsap.ticker.remove(tick)
      reset()
      draw(0)
      if (!media.matches && !document.hidden) gsap.ticker.add(tick)
    }

    let disposed = false
    tile.onload = () => {
      if (disposed) return
      resize()
      sync()
    }
    tile.src = BRAND.waveTile

    window.addEventListener('resize', resize)
    window.addEventListener('pointermove', move, { passive: true })
    window.addEventListener('blur', reset)
    document.addEventListener('pointerleave', reset)
    document.addEventListener('visibilitychange', sync)
    media.addEventListener('change', sync)

    return () => {
      disposed = true
      tile.onload = null
      gsap.ticker.remove(tick)
      follow?.kill()
      fade?.kill()
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('blur', reset)
      document.removeEventListener('pointerleave', reset)
      document.removeEventListener('visibilitychange', sync)
      media.removeEventListener('change', sync)
    }
  }, [])

  return (
    <div className="lg-wave-field" data-art-in="1" aria-hidden="true">
      <canvas ref={canvasRef} className="lg-wave-canvas" />
      <div ref={lightRef} className="lg-wave-light" />
      <div className="lg-wave-vignette" />
    </div>
  )
}
