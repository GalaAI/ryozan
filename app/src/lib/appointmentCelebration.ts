import confetti from 'canvas-confetti'

// This is the single appointment-celebration owner for hosted booking and quote-confirmed flows.
// It extends the existing canvas-confetti dependency instead of introducing another particle system.
export function fireAppointmentCelebration(): number[] {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return []

  const styles = getComputedStyle(document.documentElement)
  const colors = ['--tiger-orange', '--gold', '--bone-white', '--ok', '--danger']
    .map((token) => styles.getPropertyValue(token).trim())
    .filter(Boolean)
  const burst = (originX: number, angle: number) => confetti({
    particleCount: 42,
    spread: 58,
    startVelocity: 28,
    gravity: 0.82,
    scalar: 0.86,
    ticks: 170,
    angle,
    origin: { x: originX, y: 0.68 },
    ...(colors.length ? { colors } : {}),
    zIndex: 10002,
    disableForReducedMotion: true,
  })

  burst(0.38, 68)
  burst(0.62, 112)

  return [
    window.setTimeout(() => {
      confetti({
        particleCount: 24,
        spread: 76,
        startVelocity: 18,
        gravity: 0.72,
        scalar: 0.72,
        ticks: 140,
        origin: { x: 0.5, y: 0.62 },
        ...(colors.length ? { colors } : {}),
        zIndex: 10002,
        disableForReducedMotion: true,
      })
    }, 260),
  ]
}
