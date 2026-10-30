/**
 * Local camera and photo compositor for artist-published flash.
 *
 * No existing Ryozan component owns live camera compositing or a consented
 * placement capture. This bounded owner reuses the canonical body-region list
 * and public booking upload instead of creating another media or booking path.
 */
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { API_BASE, bookings } from '../lib/api'
import { REGIONS } from './booking/BodyMap'
import '../styles/flash-placement-preview.css'

export interface PlacementPreviewDesign {
  id: string
  name?: string | null
  image_url?: string | null
  preview_url?: string | null
  source_image_url?: string | null
  placement?: string | null
}

export interface PlacementPreviewResult {
  url: string
  placement: string
  method: 'camera_overlay' | 'photo_overlay'
}

interface FlashPlacementPreviewProps {
  design: PlacementPreviewDesign
  onClose: () => void
  onSaved: (result: PlacementPreviewResult) => void | Promise<void>
}

type PreviewMode = 'camera' | 'photo'

function stopStream(stream: MediaStream | null) {
  stream?.getTracks().forEach(track => track.stop())
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('The preview image could not be loaded for capture.'))
    image.src = url
  })
}

function drawCover(
  context: CanvasRenderingContext2D,
  source: CanvasImageSource,
  sourceWidth: number,
  sourceHeight: number,
  targetWidth: number,
  targetHeight: number,
) {
  const scale = Math.max(targetWidth / sourceWidth, targetHeight / sourceHeight)
  const width = sourceWidth * scale
  const height = sourceHeight * scale
  context.drawImage(source, (targetWidth - width) / 2, (targetHeight - height) / 2, width, height)
}

export default function FlashPlacementPreview({ design, onClose, onSaved }: FlashPlacementPreviewProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const photoRef = useRef<HTMLImageElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [mode, setMode] = useState<PreviewMode | null>(null)
  const [photoUrl, setPhotoUrl] = useState<string | null>(null)
  const [placement, setPlacement] = useState(design.placement || '')
  const [scale, setScale] = useState(1)
  const [rotation, setRotation] = useState(0)
  const [x, setX] = useState(0)
  const [y, setY] = useState(0)
  const [consent, setConsent] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [designUrl, setDesignUrl] = useState('')
  const hasPublishedAsset = Boolean(design.preview_url || design.image_url || design.source_image_url)

  useEffect(() => {
    if (!hasPublishedAsset) {
      setDesignUrl('')
      return
    }
    const controller = new AbortController()
    let objectUrl = ''
    void fetch(`${API_BASE}/flash/public/designs/${encodeURIComponent(design.id)}/image`, {
      signal: controller.signal,
    })
      .then(response => {
        if (!response.ok) throw new Error('Published flash preview unavailable.')
        return response.blob()
      })
      .then(blob => {
        objectUrl = URL.createObjectURL(blob)
        setDesignUrl(objectUrl)
      })
      .catch(caught => {
        if (controller.signal.aborted) return
        setDesignUrl('')
        setError(caught instanceof Error ? caught.message : 'Published flash preview unavailable.')
      })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [design.id, hasPublishedAsset])

  useEffect(() => () => {
    stopStream(streamRef.current)
    if (photoUrl) URL.revokeObjectURL(photoUrl)
  }, [photoUrl])

  useEffect(() => {
    if (mode !== 'camera' || !videoRef.current || !streamRef.current) return
    const video = videoRef.current
    video.srcObject = streamRef.current
    void video.play().catch(() => {
      setError('The camera opened, but the live preview could not start. Choose a photo instead.')
    })
  }, [mode])

  const startCamera = async () => {
    setError('')
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Live camera preview is not supported here. Choose a photo instead.')
      return
    }
    try {
      stopStream(streamRef.current)
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
        audio: false,
      })
      streamRef.current = stream
      setMode('camera')
    } catch {
      setError('Camera access was not granted. You can still choose a photo from this device.')
    }
  }

  const choosePhoto = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    if (!file.type.startsWith('image/')) {
      setError('Choose a JPEG, PNG, or WebP image.')
      return
    }
    stopStream(streamRef.current)
    streamRef.current = null
    setPhotoUrl(previous => {
      if (previous) URL.revokeObjectURL(previous)
      return URL.createObjectURL(file)
    })
    setMode('photo')
    setError('')
  }

  const savePreview = async () => {
    if (!mode || !designUrl || !consent || !placement) return
    setSaving(true)
    setError('')
    try {
      const canvas = document.createElement('canvas')
      canvas.width = 1080
      canvas.height = 1350
      const context = canvas.getContext('2d')
      if (!context) throw new Error('This browser could not prepare the placement capture.')

      if (mode === 'camera') {
        const video = videoRef.current
        if (!video || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !video.videoWidth) {
          throw new Error('The camera is still starting. Try Save and continue again in a moment.')
        }
        drawCover(context, video, video.videoWidth, video.videoHeight, canvas.width, canvas.height)
      } else {
        const photo = photoRef.current
        if (!photo || !photo.complete || !photo.naturalWidth) {
          throw new Error('The selected photo is still loading.')
        }
        drawCover(context, photo, photo.naturalWidth, photo.naturalHeight, canvas.width, canvas.height)
      }

      const flash = await loadImage(designUrl)
      const baseWidth = canvas.width * 0.45 * scale
      const baseHeight = baseWidth * (flash.naturalHeight / flash.naturalWidth)
      context.save()
      context.translate(canvas.width * (0.5 + x / 100), canvas.height * (0.5 + y / 100))
      context.rotate(rotation * Math.PI / 180)
      context.drawImage(flash, -baseWidth / 2, -baseHeight / 2, baseWidth, baseHeight)
      context.restore()

      context.fillStyle = 'rgba(12, 12, 12, 0.7)'
      context.fillRect(0, canvas.height - 58, canvas.width, 58)
      context.fillStyle = '#f1ead9'
      context.font = '28px sans-serif'
      context.textAlign = 'center'
      context.fillText('Placement preview - final size and fit are approved with your artist', canvas.width / 2, canvas.height - 20)

      const blob = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(result => result ? resolve(result) : reject(new Error('The placement capture could not be encoded.')), 'image/png')
      })
      const file = new File([blob], `flash-placement-${design.id}.png`, { type: 'image/png' })
      const uploaded = await bookings.uploadImage(file)
      await onSaved({
        url: uploaded.url,
        placement,
        method: mode === 'camera' ? 'camera_overlay' : 'photo_overlay',
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The placement preview could not be saved. Try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="placement-preview-backdrop" role="presentation" onMouseDown={event => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="placement-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="placement-preview-title">
        <header className="placement-preview-header">
          <div>
            <span className="placement-preview-kicker">Real flash. Your placement.</span>
            <h2 id="placement-preview-title">Preview {design.name || 'this design'} on you</h2>
          </div>
          <button type="button" className="placement-preview-close" onClick={onClose} aria-label="Close placement preview">×</button>
        </header>

        <p className="placement-preview-privacy">Camera and photo pixels stay on this device until you choose Save and continue.</p>

        <div className="placement-preview-source-actions">
          <button type="button" className={mode === 'camera' ? 'is-active' : ''} onClick={() => void startCamera()}>Use camera</button>
          <button type="button" className={mode === 'photo' ? 'is-active' : ''} onClick={() => fileRef.current?.click()}>Choose photo</button>
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={choosePhoto} />
        </div>

        <div className="placement-preview-stage" data-mode={mode || 'empty'}>
          {mode === 'camera' && <video ref={videoRef} muted playsInline autoPlay aria-label="Live placement camera" />}
          {mode === 'photo' && photoUrl && <img ref={photoRef} src={photoUrl} alt="Selected body placement" className="placement-preview-background" />}
          {!mode && <div className="placement-preview-empty">Start the camera or choose a clear photo of the placement area.</div>}
          {mode && designUrl && (
            <img
              src={designUrl}
              alt={design.name || 'Published flash design'}
              className="placement-preview-flash"
              style={{
                left: `${50 + x}%`,
                top: `${50 + y}%`,
                width: `${45 * scale}%`,
                transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
              }}
            />
          )}
        </div>

        <div className="placement-preview-controls" aria-disabled={!mode}>
          <label>
            Placement
            <select value={placement} onChange={event => setPlacement(event.target.value)}>
              <option value="">Choose body area</option>
              {REGIONS.map(region => <option key={region.id} value={region.label}>{region.label}</option>)}
            </select>
          </label>
          <label>Size <input type="range" min="0.35" max="1.8" step="0.05" value={scale} onChange={event => setScale(Number(event.target.value))} disabled={!mode} /></label>
          <label>Rotate <input type="range" min="-180" max="180" step="1" value={rotation} onChange={event => setRotation(Number(event.target.value))} disabled={!mode} /></label>
          <label>Left / right <input type="range" min="-38" max="38" step="1" value={x} onChange={event => setX(Number(event.target.value))} disabled={!mode} /></label>
          <label>Up / down <input type="range" min="-38" max="38" step="1" value={y} onChange={event => setY(Number(event.target.value))} disabled={!mode} /></label>
        </div>

        <label className="placement-preview-consent">
          <input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />
          I consent to upload this preview to Ryozan and share it with my selected artist for this booking.
        </label>

        {error && <p className="placement-preview-error" role="alert">{error}</p>}
        <footer className="placement-preview-footer">
          <p>This is a visualization, not a final stencil. Your artist approves exact sizing and fit in person.</p>
          <button type="button" onClick={() => void savePreview()} disabled={!mode || !designUrl || !placement || !consent || saving}>
            {saving ? 'Saving preview...' : 'Save and continue'}
          </button>
        </footer>
      </section>
    </div>
  )
}
