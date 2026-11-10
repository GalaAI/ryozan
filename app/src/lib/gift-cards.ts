/**
 * Gift card purchase math for the public /gift-cards page. Pure, so it is testable without a browser.
 * The server (api/app/api/v1/endpoints/gift_cards.py) owns the real limits and the charge; the page reads
 * them from GET /gift-cards/terms and only mirrors the arithmetic to show the total before checkout.
 */

import type { GiftCardStatusResponse, GiftCardTerms } from './api'

/** Suggested face values in cents; only those inside the shop's allowed range are offered. */
export const GIFT_CARD_PRESETS_CENTS = [5000, 10000, 15000, 25000] as const

/** How often, and how many times, the confirmation page asks whether Stripe has finished. */
export const GIFT_CARD_POLL_MS = 1500
export const GIFT_CARD_POLL_LIMIT = 40

export function presetAmounts(terms: Pick<GiftCardTerms, 'min_amount_cents' | 'max_amount_cents'>): number[] {
  return GIFT_CARD_PRESETS_CENTS.filter((c) => c >= terms.min_amount_cents && c <= terms.max_amount_cents)
}

/** "$1,250" or "1250.5" -> cents; anything else -> null. */
export function dollarsToCents(value: string): number | null {
  const cleaned = value.replace(/[$,\s]/g, '')
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null
  return Math.round(Number(cleaned) * 100)
}

/** The checkout total. Same rounding as payments.customer_price_cents on the server. */
export function chargedCents(faceCents: number, adjustmentBps: number): number {
  const face = Math.max(0, Math.trunc(faceCents))
  return Math.floor((face * (10000 + adjustmentBps) + 5000) / 10000)
}

export function formatCents(cents: number): string {
  return `$${(cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** Basis points as a percent label: 400 -> "4%". */
export function adjustmentLabel(bps: number): string {
  return `${Number((bps / 100).toFixed(2))}%`
}

export type GiftCardPhase = 'confirming' | 'delivering' | 'delivered' | 'not_created'

/** Where a returning buyer's card stands, and whether the page should ask again. */
export function confirmationPhase(status: GiftCardStatusResponse | null): { phase: GiftCardPhase; keepPolling: boolean } {
  if (!status || status.status === 'pending') return { phase: 'confirming', keepPolling: true }
  if (status.status === 'voided') return { phase: 'not_created', keepPolling: false }
  if (!status.delivered) return { phase: 'delivering', keepPolling: true }
  return { phase: 'delivered', keepPolling: false }
}
