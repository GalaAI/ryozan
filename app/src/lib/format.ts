/** Submitted choices are preferences, never confirmed appointment times. */
export function requestedBookingSlots(booking: {
  preferred_slots?: Array<{ date: string; time: string }> | null
  preferred_date?: string | null
  preferred_time?: string | null
}): Array<{ date: string; time: string | null }> {
  if (booking.preferred_slots?.length) return booking.preferred_slots
  return booking.preferred_date ? [{ date: booking.preferred_date, time: booking.preferred_time ?? null }] : []
}

/** A request staff can still approve: not yet booked, cancelled or done. */
export function isApprovableStatus(status: string | null | undefined): boolean {
  return status === 'pending' || status === 'quoted'
}

/** Mirrors the API's booking management check: shop managers, or the booking's own artist. */
export function canApproveBooking(role: string | null | undefined, userId: string | null | undefined, artistId: string | null | undefined): boolean {
  if (role === 'assistant' || role === 'admin' || role === 'founder') return true
  return role === 'artist' && Boolean(userId) && userId === artistId
}

/** Calendar dates are local wall dates, not UTC timestamps. */
export function formatRequestedSlot(date: string, time: string | null): string {
  const value = new Date(`${date}T12:00:00`)
  const day = Number.isNaN(value.getTime()) ? date : value.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
  })
  return time ? `${day} at ${formatTime12h(time)}` : day
}
export const CUSTOMER_PRICE_INCREASE_PERCENT = 4

/** Return the single customer-facing price after Ryozan's four-percent increase. */
export function customerPrice(amount: number): number {
  return Math.round(amount * (100 + CUSTOMER_PRICE_INCREASE_PERCENT)) / 100
}

export function formatCustomerPrice(amount: number): string {
  return customerPrice(amount).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/**
 * Format 24h time string (HH:MM) to 12h format (h:MM AM/PM).
 * Returns original string if parsing fails.
 */
export function formatTime12h(time: string | null | undefined): string {
  if (!time) return ''
  const parts = time.split(':')
  if (parts.length < 2) return time
  const h = parseInt(parts[0], 10)
  const m = parts[1]
  if (isNaN(h)) return time
  const period = h >= 12 ? 'PM' : 'AM'
  const hour = h === 0 ? 12 : h > 12 ? h - 12 : h
  return `${hour}:${m} ${period}`
}
