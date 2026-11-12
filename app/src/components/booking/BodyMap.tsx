/**
 * Interactive body placement selector for public waiver check-in.
 *
 * Flash-chart design: the tappable regions ARE the figure. Every segment
 * is visible at rest (like an anatomical placement poster), seams in gold,
 * selected region glows. No separate outline layer.
 *
 * Back view: Full Back is a chip toggle, not an invisible overlay, so
 * upper/lower back stay directly tappable.
 */
import { useState } from 'react'
import { CustomSelect } from '../CustomSelect'
import { REGIONS } from './body-regions'
import '../../styles/booking.css'

export type { BodyRegion } from './body-regions'

// ── SVG path data ───────────────────────────────────────────────
// viewBox="0 0 200 400". Figure centered at x=100.
// 8-head canon: each head unit = 50px, total height ~400px.
// Regions tile edge-to-edge; the seams between them draw the figure.

const FRONT_PATHS: Record<string, string> = {
  // Head: oval
  'head': 'M88,12 C88,5 94,0 100,0 C106,0 112,5 112,12 L112,38 C112,45 106,50 100,50 C94,50 88,45 88,38 Z',
  // Neck
  'neck': 'M93,50 L107,50 L109,65 L91,65 Z',
  // Shoulders
  'shoulder-left': 'M91,65 L70,68 C62,70 56,76 54,82 L64,82 L78,72 L91,68 Z',
  'shoulder-right': 'M109,65 L130,68 C138,70 144,76 146,82 L136,82 L122,72 L109,68 Z',
  // Chest
  'chest': 'M78,72 L91,68 L109,68 L122,72 L124,105 L76,105 Z',
  // Ribs
  'ribs-left': 'M64,82 L78,72 L76,105 L74,140 L58,140 L54,82 Z',
  'ribs-right': 'M136,82 L122,72 L124,105 L126,140 L142,140 L146,82 Z',
  // Stomach
  'stomach': 'M76,105 L124,105 L126,140 L128,175 L72,175 L74,140 Z',
  // Upper arms
  'upper-arm-left': 'M54,82 L58,140 L44,148 L38,92 Z',
  'upper-arm-right': 'M146,82 L142,140 L156,148 L162,92 Z',
  // Forearms
  'forearm-left': 'M38,92 L44,148 L34,198 L26,190 Z',
  'forearm-right': 'M162,92 L156,148 L166,198 L174,190 Z',
  // Wrists
  'wrist-left': 'M26,190 L34,198 L30,212 L22,206 Z',
  'wrist-right': 'M174,190 L166,198 L170,212 L178,206 Z',
  // Hands: rounded mitts
  'hand-left': 'M22,206 L30,212 C29,221 28,229 26,235 C23,241 16,239 17,232 C18,223 20,214 22,206 Z',
  'hand-right': 'M178,206 L170,212 C171,221 172,229 174,235 C177,241 184,239 183,232 C182,223 180,214 178,206 Z',
  // Hips
  'hip-left': 'M72,175 L100,175 L98,195 L78,198 Z',
  'hip-right': 'M100,175 L128,175 L122,198 L102,195 Z',
  // Thighs
  'thigh-left': 'M78,198 L98,195 L96,265 L76,265 Z',
  'thigh-right': 'M102,195 L122,198 L124,265 L104,265 Z',
  // Knees
  'knee-left': 'M76,265 L96,265 L95,290 L77,290 Z',
  'knee-right': 'M104,265 L124,265 L123,290 L105,290 Z',
  // Calves / shins
  'calf-left': 'M77,290 L95,290 L92,348 L78,348 Z',
  'calf-right': 'M105,290 L123,290 L122,348 L108,348 Z',
  // Ankles
  'ankle-left': 'M78,348 L92,348 L91,362 L79,362 Z',
  'ankle-right': 'M108,348 L122,348 L121,362 L109,362 Z',
  // Feet: rounded toes, pointing slightly outward
  'foot-left': 'M79,362 L91,362 C92,369 93,375 93,379 C93,385 87,387 80,387 C73,387 69,383 70,378 C72,372 76,367 79,362 Z',
  'foot-right': 'M109,362 L121,362 C124,367 128,372 130,378 C131,383 127,387 120,387 C113,387 107,385 107,379 C107,375 108,369 109,362 Z',
}

const BACK_PATHS: Record<string, string> = {
  'head': FRONT_PATHS['head'],
  'neck': FRONT_PATHS['neck'],
  'shoulder-left': FRONT_PATHS['shoulder-left'],
  'shoulder-right': FRONT_PATHS['shoulder-right'],
  // Back regions replace chest/stomach
  'upper-back': 'M78,72 L122,72 L124,130 L76,130 Z',
  'lower-back': 'M76,130 L124,130 L128,175 L72,175 Z',
  // Ribs visible from back too
  'ribs-left': FRONT_PATHS['ribs-left'],
  'ribs-right': FRONT_PATHS['ribs-right'],
  // Arms same from back
  'upper-arm-left': FRONT_PATHS['upper-arm-left'],
  'upper-arm-right': FRONT_PATHS['upper-arm-right'],
  'forearm-left': FRONT_PATHS['forearm-left'],
  'forearm-right': FRONT_PATHS['forearm-right'],
  'wrist-left': FRONT_PATHS['wrist-left'],
  'wrist-right': FRONT_PATHS['wrist-right'],
  'hand-left': FRONT_PATHS['hand-left'],
  'hand-right': FRONT_PATHS['hand-right'],
  // Lower body same from back
  'hip-left': FRONT_PATHS['hip-left'],
  'hip-right': FRONT_PATHS['hip-right'],
  'thigh-left': FRONT_PATHS['thigh-left'],
  'thigh-right': FRONT_PATHS['thigh-right'],
  'knee-left': FRONT_PATHS['knee-left'],
  'knee-right': FRONT_PATHS['knee-right'],
  'calf-left': FRONT_PATHS['calf-left'],
  'calf-right': FRONT_PATHS['calf-right'],
  'ankle-left': FRONT_PATHS['ankle-left'],
  'ankle-right': FRONT_PATHS['ankle-right'],
  'foot-left': FRONT_PATHS['foot-left'],
  'foot-right': FRONT_PATHS['foot-right'],
}

// Torso outline used to highlight the whole back when Full Back is selected
const FULL_BACK_PATH = 'M78,72 L122,72 L128,175 L72,175 Z'

const FRONT_HIDDEN = new Set(['upper-back', 'lower-back', 'full-back'])
const BACK_HIDDEN = new Set(['chest', 'stomach'])

// Registration tick marks along the left edge — one per head unit
const TICK_YS = [50, 100, 150, 200, 250, 300, 350]

interface BodyMapProps {
  value: string | null
  onChange: (regionId: string, label: string) => void
}

export function BodyMap({ value, onChange }: BodyMapProps) {
  const [view, setView] = useState<'front' | 'back'>('front')

  const paths = view === 'front' ? FRONT_PATHS : BACK_PATHS
  const hidden = view === 'front' ? FRONT_HIDDEN : BACK_HIDDEN

  const selectedRegion = REGIONS.find(r => r.id === value || r.label === value)
  const visibleRegions = REGIONS.filter(r => !hidden.has(r.id))
  const selectedValue = selectedRegion?.id || ''
  const fullBackSelected = selectedValue === 'full-back'

  return (
    <div className="body-map">
      <div className="body-map-header">
        <span className="body-map-label">Tap placement</span>
        <div className="body-map-view-tabs" aria-label="Body view">
          {(['front', 'back'] as const).map(option => (
            <button
              key={option}
              type="button"
              className={`body-map-tab${view === option ? ' active' : ''}`}
              onClick={() => setView(option)}
            >
              {option === 'front' ? 'Front' : 'Back'}
            </button>
          ))}
        </div>
      </div>

      <div className="body-map-canvas">
        <svg viewBox="0 0 200 400" className="body-map-svg" role="img" aria-label={`${view} body placement map`}>
          {/* Stencil chart furniture: center axis + head-unit ticks */}
          <g className="body-map-chart" aria-hidden="true">
            <line x1="100" y1="0" x2="100" y2="396" className="body-map-axis" />
            {TICK_YS.map(y => (
              <line key={y} x1="5" y1={y} x2="12" y2={y} className="body-map-tick" />
            ))}
          </g>

          {/* Tappable regions — the segments ARE the figure */}
          {Object.entries(paths).map(([id, d]) => {
            const region = REGIONS.find(r => r.id === id)
            const isSelected =
              value === id ||
              value === region?.label ||
              (fullBackSelected && (id === 'upper-back' || id === 'lower-back'))
            return (
              <path
                key={id}
                d={d}
                className={`body-map-region${isSelected ? ' selected' : ''}`}
                onClick={() => region && onChange(region.id, region.label)}
                role="button"
                tabIndex={0}
                aria-label={region?.label || id}
                onKeyDown={(event) => {
                  if ((event.key === 'Enter' || event.key === ' ') && region) {
                    event.preventDefault()
                    onChange(region.id, region.label)
                  }
                }}
              />
            )
          })}

          {/* Full Back selected: single glowing torso, seams hidden underneath */}
          {view === 'back' && fullBackSelected && (
            <path d={FULL_BACK_PATH} className="body-map-region selected" style={{ pointerEvents: 'none' }} />
          )}
        </svg>

        {/* View watermark, stencil style */}
        <span className="body-map-watermark" aria-hidden="true">{view}</span>

        {/* Full Back quick-pick (back view only) */}
        {view === 'back' && (
          <button
            type="button"
            className={`body-map-chip${fullBackSelected ? ' active' : ''}`}
            onClick={() => onChange('full-back', 'Full Back')}
          >
            Full Back
          </button>
        )}

        {/* Selected label badge */}
        {selectedRegion && (
          <div className="body-map-selected">
            {selectedRegion.label}
          </div>
        )}
      </div>

      <div className="body-map-fallback">
        <label id="body-map-placement-label">Choose from list</label>
        <CustomSelect
          value={selectedValue}
          onChange={(v) => {
            const region = REGIONS.find(r => r.id === v)
            if (region) onChange(region.id, region.label)
          }}
          options={visibleRegions.map(region => ({ value: region.id, label: region.label }))}
          placeholder="Select placement"
        />
      </div>
    </div>
  )
}

