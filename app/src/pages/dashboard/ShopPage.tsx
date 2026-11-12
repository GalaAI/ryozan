/**
 * ShopPage — Flash design storefront for collectors.
 *
 * Browse published flash designs, filter by artist/style, preview a
 * design on the collector, then continue through the real booking flow.
 *
 * Uses the public /flash/store/public endpoint (no auth required to
 * browse and begin booking).
 */

import { useState, useEffect, useCallback, useRef, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { browsePublicFlashStore } from '../../lib/api'
import { customerPrice, formatCustomerPrice } from '../../lib/format'
import FlashPlacementPreview, { type PlacementPreviewResult } from '../../components/FlashPlacementPreview'
import { useStudioSettings } from '../../hooks/useStudioSettings'
import { sampleFlashUrl } from '../../lib/studio-settings'

interface FlashDesign {
  id: string
  name: string | null
  image_url: string | null
  preview_url?: string | null
  source_image_url?: string | null
  crop_units?: 'percent' | 'pixel'
  asset_version?: number
  context_regions?: Array<Record<string, unknown>>
  crop_x: number
  crop_y: number
  crop_w: number
  crop_h: number
  price: number | null
  estimated_minutes: number | null
  style: string | null
  status: string
  artist_id: string
  artist_slug?: string | null
  artist_name?: string | null
  placement?: string | null
}

const STYLE_FILTERS = [
  'all', 'traditional', 'japanese', 'neo-traditional', 'realism',
  'blackwork', 'fine-line', 'watercolor', 'illustrative',
]

// Local development only: stand-in previews from the studio's SAMPLE_FLASH_URLS.
const ENABLE_VISUAL_TEST_FLASH_FALLBACK = import.meta.env.DEV

function trackGoogleEvent(eventName: string, params: Record<string, string | number | boolean | undefined>) {
  const google = window as Window & {
    gtag?: (command: 'event', target: string, params?: Record<string, string | number | boolean>) => void
  }
  const cleanParams = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined),
  ) as Record<string, string | number | boolean>
  google.gtag?.('event', eventName, cleanParams)
}

function cropSheetStyle(
  design: FlashDesign,
  sourceSize: { width: number; height: number } | null,
): CSSProperties | undefined {
  if (design.crop_units === 'pixel' && !sourceSize) return undefined
  const xScale = design.crop_units === 'pixel' ? 100 / sourceSize!.width : 1
  const yScale = design.crop_units === 'pixel' ? 100 / sourceSize!.height : 1
  const cropX = (Number.isFinite(design.crop_x) ? design.crop_x : 0) * xScale
  const cropY = (Number.isFinite(design.crop_y) ? design.crop_y : 0) * yScale
  const cropW = (Number.isFinite(design.crop_w) && design.crop_w > 0 ? design.crop_w : 100) * xScale
  const cropH = (Number.isFinite(design.crop_h) && design.crop_h > 0 ? design.crop_h : 100) * yScale
  return {
    inset: 'auto',
    left: `${-(cropX / cropW) * 100}%`,
    top: `${-(cropY / cropH) * 100}%`,
    width: `${10000 / cropW}%`,
    height: `${10000 / cropH}%`,
    maxWidth: 'none',
    objectFit: 'fill',
  }
}

function FlashPreview({ design, index }: { design: FlashDesign; index: number }) {
  const [sourceFallbackActive, setSourceFallbackActive] = useState(false)
  const [fallbackActive, setFallbackActive] = useState(false)
  const [failed, setFailed] = useState(false)
  const [sourceSize, setSourceSize] = useState<{ width: number; height: number } | null>(null)
  const { settings: studioSettings } = useStudioSettings()
  const fallbackSrc = ENABLE_VISUAL_TEST_FLASH_FALLBACK ? sampleFlashUrl(studioSettings, index) : null
  const sourceUrl = design.source_image_url || design.image_url
  const previewUrl = design.preview_url || (!design.source_image_url ? design.image_url : null)
  const src = fallbackActive && fallbackSrc
    ? fallbackSrc
    : sourceFallbackActive
      ? sourceUrl
      : previewUrl || sourceUrl
  const isSheetCrop = !fallbackActive && (sourceFallbackActive || !previewUrl) && Boolean(sourceUrl)

  if (!src || failed) {
    return (
      <div className="shop-design-placeholder">
        {src ? 'Preview unavailable' : 'No preview'}
      </div>
    )
  }

  return (
    <div className="shop-design-preview">
      <img
        src={src}
        alt={design.name || 'Flash design'}
        className="shop-design-img"
        style={isSheetCrop ? cropSheetStyle(design, sourceSize) : undefined}
        referrerPolicy="no-referrer"
        loading="lazy"
        decoding="async"
        onLoad={(event) => {
          if (isSheetCrop) {
            setSourceSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })
          }
        }}
        onError={() => {
          if (!sourceFallbackActive && previewUrl && sourceUrl && sourceUrl !== previewUrl) {
            setSourceFallbackActive(true)
            return
          }
          if (!fallbackActive && fallbackSrc) {
            setFallbackActive(true)
            return
          }
          setFailed(true)
        }}
      />
      {fallbackActive && (
        <span className="shop-preview-fallback-badge">Test preview</span>
      )}
    </div>
  )
}

interface ShopPageProps {
  artistSlug?: string
}

export default function ShopPage({ artistSlug }: ShopPageProps) {
  const navigate = useNavigate()
  const [designs, setDesigns] = useState<FlashDesign[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [styleFilter, setStyleFilter] = useState('all')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [previewDesign, setPreviewDesign] = useState<FlashDesign | null>(null)
  const requestSequence = useRef(0)

  // Fetch only: state is written once the request answers.
  const requestDesigns = useCallback(() => {
    const requestId = ++requestSequence.current
    return browsePublicFlashStore({
      artist_slug: artistSlug,
      style: styleFilter !== 'all' ? styleFilter : undefined,
      limit: 30,
    })
      .then((data) => {
        if (requestId !== requestSequence.current) return
        setDesigns(data.designs || [])
        setTotal(data.total || 0)
      })
      .catch((caught) => {
        if (requestId !== requestSequence.current) return
        setDesigns([])
        setTotal(0)
        setLoadError(caught instanceof Error ? caught.message : 'The flash storefront could not be loaded.')
      })
      .finally(() => {
        if (requestId === requestSequence.current) setLoading(false)
      })
  }, [artistSlug, styleFilter])

  const loadDesigns = () => {
    setLoading(true)
    setLoadError('')
    return requestDesigns()
  }

  // A new artist or style filter starts a fresh load: reset during render, fetch in the effect.
  const [designsQuery, setDesignsQuery] = useState<readonly [typeof artistSlug, string] | null>(null)
  if (!designsQuery || designsQuery[0] !== artistSlug || designsQuery[1] !== styleFilter) {
    setDesignsQuery([artistSlug, styleFilter])
    setLoading(true)
    setLoadError('')
  }

  useEffect(() => { requestDesigns() }, [requestDesigns])

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      return prev.has(id) ? new Set() : new Set([id])
    })
  }

  const handleBookingHandoff = () => {
    const design = designs.find(candidate => selected.has(candidate.id))
    if (!design) return
    const resolvedArtistSlug = design.artist_slug || artistSlug
    if (!resolvedArtistSlug) {
      setLoadError('This design is missing its artist booking route. Open the artist page and try again.')
      return
    }
    trackGoogleEvent('begin_checkout', {
      currency: 'USD',
      value: customerPrice(Number(design.price || 0)),
      items_selected: 1,
      checkout_type: 'flash_booking_handoff',
    })
    const params = new URLSearchParams({
      context: 'flash-store',
      flash_id: design.id,
      description: design.name || 'Published flash design',
    })
    if (design.placement) params.set('placement', design.placement)
    navigate(`/book/${encodeURIComponent(resolvedArtistSlug)}?${params.toString()}`)
  }

  const handlePreviewSaved = async (result: PlacementPreviewResult) => {
    if (!previewDesign) return
    const resolvedArtistSlug = previewDesign.artist_slug || artistSlug
    if (!resolvedArtistSlug) {
      throw new Error('This design is missing its artist booking route. Choose the artist page and try again.')
    }
    const params = new URLSearchParams({
      context: 'flash-placement-preview',
      flash_id: previewDesign.id,
      placement: result.placement,
      preview_url: result.url,
      preview_method: result.method,
      description: `${previewDesign.name || 'Published flash design'} placement preview`,
    })
    setPreviewDesign(null)
    navigate(`/book/${encodeURIComponent(resolvedArtistSlug)}?${params.toString()}`)
  }

  return (
    <div className="shop-page">
      <div className="shop-header">
        <h2>Flash Designs</h2>
        <span className="shop-count">{total} available</span>
      </div>

      {/* Style filter */}
      <div className="shop-filters">
        {STYLE_FILTERS.map(s => (
          <button
            key={s}
            className={`shop-filter-btn ${styleFilter === s ? 'active' : ''}`}
            onClick={() => setStyleFilter(s)}
          >
            {s === 'all' ? 'All Styles' : s}
          </button>
        ))}
      </div>

      {/* Booking handoff bar, sticky when one design is selected. */}
      {selected.size > 0 && (
        <div className="flash-checkout-bar">
          <div className="flash-checkout-info">
            <span className="flash-checkout-count">Flash selected</span>
            <span className="flash-checkout-discount">Booking confirms availability and payment</span>
          </div>
          <div className="flash-checkout-right">
            <button
              className="shop-claim-btn"
              onClick={handleBookingHandoff}
            >
              Continue to booking
            </button>
            <button
              className="flash-checkout-clear"
              onClick={() => setSelected(new Set())}
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {/* Design grid */}
      {loading ? (
        <div className="shop-loading">Loading designs...</div>
      ) : loadError ? (
        <div className="shop-empty" role="alert">
          <p>{loadError}</p>
          <button type="button" className="shop-claim-btn" onClick={() => void loadDesigns()}>Retry storefront</button>
        </div>
      ) : designs.length === 0 ? (
        <div className="shop-empty">
          <p>No flash designs available yet.</p>
          <p>Check back soon for new drops from our artists.</p>
        </div>
      ) : (
        <div className="shop-grid">
          {designs.map((d, i) => {
            const isSelected = selected.has(d.id)
            const isClaimed = d.status === 'claimed'
            return (
              <div
                key={d.id}
                className={`shop-design-card${isSelected ? ' selected' : ''}${isClaimed ? ' claimed' : ''}`}
                role="button"
                tabIndex={isClaimed ? -1 : 0}
                aria-pressed={isSelected}
                onClick={() => !isClaimed && toggleSelect(d.id)}
                onKeyDown={event => {
                  if (!isClaimed && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault()
                    toggleSelect(d.id)
                  }
                }}
              >
                <FlashPreview design={d} index={i} />
                <div className="shop-design-info">
                  <span className="shop-design-name">{d.name || 'Untitled Design'}</span>
                  {d.style && <span className="shop-design-style">{d.style}</span>}
                  {d.estimated_minutes && <span className="shop-design-time">{d.estimated_minutes} min</span>}
                </div>
                <div className="shop-design-footer">
                  {d.price ? (
                    <span className="shop-design-price">{formatCustomerPrice(Number(d.price))}</span>
                  ) : (
                    <span className="shop-design-price-tbd">Price TBD</span>
                  )}
                  {isClaimed ? (
                    <span className="shop-claimed-badge">Claimed</span>
                  ) : isSelected ? (
                    <span className="shop-selected-badge">Selected</span>
                  ) : null}
                </div>
                {!isClaimed && (
                  <button
                    type="button"
                    className="shop-preview-tryon-btn"
                    onClick={event => {
                      event.stopPropagation()
                      setPreviewDesign(d)
                    }}
                  >
                    Preview on me
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      {previewDesign && (
        <FlashPlacementPreview
          design={previewDesign}
          onClose={() => setPreviewDesign(null)}
          onSaved={handlePreviewSaved}
        />
      )}
    </div>
  )
}
