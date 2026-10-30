/**
 * Lightbox: Full-screen photo enlargement overlay.
 *
 * Usage:
 *   <Lightbox images={[...]} startIndex={0} onClose={() => setOpen(false)} />
 *
 * Features:
 *   - Keyboard nav (← → Esc)
 *   - Click outside to close
 *   - Prev / next arrows
 *   - Caption / counter
 *   - Renders via React portal above all other content
 */

import { useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'

export interface LightboxImage {
  src: string
  alt: string
  referrerPolicy?: 'no-referrer'
}

interface LightboxProps {
  images: LightboxImage[]
  currentIndex: number
  onClose: () => void
  onNext: () => void
  onPrev: () => void
}

export default function Lightbox({ images, currentIndex, onClose, onNext, onPrev }: LightboxProps) {
  const image = images[currentIndex]
  const total = images.length

  const handleKey = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
    if (e.key === 'ArrowRight') onNext()
    if (e.key === 'ArrowLeft') onPrev()
  }, [onClose, onNext, onPrev])

  useEffect(() => {
    document.addEventListener('keydown', handleKey)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKey)
      document.body.style.overflow = ''
    }
  }, [handleKey])

  return createPortal(
    <div className="lightbox-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Photo viewer">
      {/* Close */}
      <button className="lightbox-close" onClick={onClose} aria-label="Close">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>

      {/* Prev */}
      {total > 1 && (
        <button className="lightbox-arrow lightbox-arrow--prev" onClick={e => { e.stopPropagation(); onPrev() }} aria-label="Previous photo">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
      )}

      {/* Image */}
      <div className="lightbox-img-wrap" onClick={e => e.stopPropagation()}>
        <img
          key={image.src}
          src={image.src}
          alt={image.alt}
          referrerPolicy={image.referrerPolicy}
          className="lightbox-img"
        />
        {total > 1 && (
          <p className="lightbox-counter">{currentIndex + 1} / {total}</p>
        )}
      </div>

      {/* Next */}
      {total > 1 && (
        <button className="lightbox-arrow lightbox-arrow--next" onClick={e => { e.stopPropagation(); onNext() }} aria-label="Next photo">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      )}
    </div>,
    document.body
  )
}
