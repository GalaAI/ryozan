/**
 * Size selector with physical reference objects.
 *
 * Instead of a dropdown with "Small (under 2\")", clients see familiar
 * objects they can relate to: a quarter, a credit card, their phone,
 * a paperback book. Each object maps to a size tier that feeds into
 * the artist's session type pricing + duration.
 *
 * Layout: horizontal row of tappable cards, each with an icon and
 * the size tier label. Selected card highlights in gold.
 */
import { BookOpen, Circle, CreditCard, Image, Ruler, Smartphone } from 'lucide-react'
import '../../styles/booking.css'

export interface SizeOption {
  id: string
  label: string
  reference: string
  dimensions: string
  Icon: typeof Circle
}

const SIZE_OPTIONS: SizeOption[] = [
  {
    id: 'small',
    label: 'Small',
    reference: 'Quarter to matchbox',
    dimensions: 'Under 2"',
    Icon: Circle,
  },
  {
    id: 'medium',
    label: 'Medium',
    reference: 'Credit card',
    dimensions: '2" - 4"',
    Icon: CreditCard,
  },
  {
    id: 'large',
    label: 'Large',
    reference: 'Phone screen',
    dimensions: '4" - 8"',
    Icon: Smartphone,
  },
  {
    id: 'xlarge',
    label: 'Extra Large',
    reference: 'Paperback book or bigger',
    dimensions: '8"+',
    Icon: BookOpen,
  },
  {
    id: 'sleeve',
    label: 'Sleeve',
    reference: 'Full or half arm wrap',
    dimensions: 'Wraps around',
    Icon: Ruler,
  },
  {
    id: 'fullback',
    label: 'Full Back',
    reference: 'Shoulder to waist',
    dimensions: 'Full coverage',
    Icon: Image,
  },
]

interface SizeSelectorProps {
  value: string | null
  onChange: (sizeId: string, label: string) => void
}

export function SizeSelector({ value, onChange }: SizeSelectorProps) {
  return (
    <div className="size-selector">
      <div className="size-selector-label">How big?</div>
      <div className="size-selector-grid">
        {SIZE_OPTIONS.map(opt => {
          const Icon = opt.Icon
          return (
            <button
              key={opt.id}
              type="button"
              className={`size-card${value === opt.id ? ' selected' : ''}`}
              onClick={() => onChange(opt.id, opt.label)}
            >
              <span className="size-card-icon"><Icon size={20} strokeWidth={1.8} /></span>
              <span className="size-card-label">{opt.label}</span>
              <span className="size-card-ref">{opt.reference}</span>
              <span className="size-card-dim">{opt.dimensions}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

