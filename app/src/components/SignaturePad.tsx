/**
 * SignaturePad: reusable canvas-based signature capture.
 *
 * Returns a base64 PNG data URL via `onSign(dataUrl)` when the user
 * lifts their pen/finger. Clears with the built-in "Clear" button.
 *
 * Used by DocumentHub (dashboard forms) and PublicFormPage (magic links).
 */

import { useRef, useCallback } from 'react'

interface SignaturePadProps {
  /** Called with a base64 PNG data URL after each stroke, or '' on clear */
  onSign: (dataUrl: string) => void
  /** Current signature value (truthy = "captured" state) */
  value?: string
  width?: number
  height?: number
  /** Stroke color. Defaults to brand gold. */
  strokeColor?: string
  /** Stroke width in px */
  strokeWidth?: number
  /** Accessible name for the pad and its clear button. */
  ariaLabel?: string
}

export default function SignaturePad({
  onSign,
  value,
  width = 460,
  height = 140,
  strokeColor = '#faae15',
  strokeWidth = 2.5,
  ariaLabel = 'Signature',
}: SignaturePadProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const drawingRef = useRef(false)

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      drawingRef.current = true
      const canvas = canvasRef.current
      if (!canvas) return
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      const rect = canvas.getBoundingClientRect()
      const scaleX = canvas.width / rect.width
      const scaleY = canvas.height / rect.height
      ctx.beginPath()
      ctx.moveTo(
        (e.clientX - rect.left) * scaleX,
        (e.clientY - rect.top) * scaleY,
      )
      canvas.setPointerCapture(e.pointerId)
    },
    [],
  )

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (!drawingRef.current) return
      const canvas = canvasRef.current
      if (!canvas) return
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      const rect = canvas.getBoundingClientRect()
      const scaleX = canvas.width / rect.width
      const scaleY = canvas.height / rect.height
      ctx.strokeStyle = strokeColor
      ctx.lineWidth = strokeWidth
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.lineTo(
        (e.clientX - rect.left) * scaleX,
        (e.clientY - rect.top) * scaleY,
      )
      ctx.stroke()
    },
    [strokeColor, strokeWidth],
  )

  const handlePointerUp = useCallback(() => {
    drawingRef.current = false
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    // Check if canvas has any drawn content (alpha channel > 0)
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
    const hasContent = data.some((_, i) => i % 4 === 3 && data[i] > 0)
    if (hasContent) {
      onSign(canvas.toDataURL('image/png'))
    }
  }, [onSign])

  const handleClear = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height)
    onSign('')
  }, [onSign])

  return (
    <div className={`sig-pad-zone ${value ? 'has-sig' : ''}`}>
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className="sig-canvas"
        role="img"
        aria-label={value ? `${ariaLabel}, captured` : `${ariaLabel} pad. Sign with mouse or finger`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      />
      {!value && (
        <span className="sig-placeholder">Sign here with mouse or finger</span>
      )}
      <div className="sig-actions">
        <button type="button" className="sig-clear-btn" onClick={handleClear} aria-label={`Clear ${ariaLabel.toLowerCase()}`}>
          Clear
        </button>
        {value && <span className="sig-confirmed">Signature captured</span>}
      </div>
    </div>
  )
}
