/**
 * StatusBadge: Unified status pill for bookings, quests, payments.
 *
 * Usage:
 *   <StatusBadge status="confirmed" />
 *   <StatusBadge status="pending" size="sm" />
 */

type BadgeVariant = 'warning' | 'success' | 'gold' | 'danger' | 'muted' | 'info'

const STATUS_MAP: Record<string, BadgeVariant> = {
  pending: 'warning',
  PENDING: 'warning',
  quoted: 'info',
  QUOTED: 'info',
  confirmed: 'success',
  CONFIRMED: 'success',
  completed: 'gold',
  COMPLETED: 'gold',
  cancelled: 'danger',
  CANCELLED: 'danger',
  no_show: 'muted',
  NO_SHOW: 'muted',
  expired: 'muted',
  EXPIRED: 'muted',
}

interface StatusBadgeProps {
  status: string
  label?: string
  size?: 'sm' | 'md'
  className?: string
}

export default function StatusBadge({
  status,
  label,
  size,
  className = '',
}: StatusBadgeProps) {
  const variant = STATUS_MAP[status] || 'muted'
  const sizeClass = size === 'sm' ? ' badge-sm' : ''
  return (
    <span className={`badge badge-${variant}${sizeClass} ${className}`}>
      {label ?? status}
    </span>
  )
}
