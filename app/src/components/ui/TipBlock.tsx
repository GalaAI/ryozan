/**
 * TipBlock: Reusable callout component for tips, warnings, and info.
 *
 * Usage:
 *   <TipBlock>Keep your tattoo moisturized.</TipBlock>
 *   <TipBlock variant="warning" label="Warning">Do not scratch.</TipBlock>
 *   <TipBlock variant="info" label="Note">Sessions are non-refundable.</TipBlock>
 */

import type { ReactNode } from 'react'

type Variant = 'tip' | 'warning' | 'info' | 'success'

interface TipBlockProps {
  children: ReactNode
  variant?: Variant
  label?: string
  className?: string
}

export default function TipBlock({
  children,
  variant = 'tip',
  label,
  className = '',
}: TipBlockProps) {
  const displayLabel = label ?? variantLabels[variant]

  return (
    <div className={`tip-block tip-block--${variant} ${className}`}>
      <span className="tip-block-label">{displayLabel}</span>
      <div className="tip-block-content">{children}</div>
    </div>
  )
}

const variantLabels: Record<Variant, string> = {
  tip: 'Tip',
  warning: 'Warning',
  info: 'Note',
  success: 'Done',
}
