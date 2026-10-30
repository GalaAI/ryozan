/**
 * Which connection cards the account may see, from GET /integrations/options.
 * A card is offered only when its integration is configured on this server AND
 * allowed for the caller. When the options read fails, the surface falls back
 * to the pre-options rule: live connections only.
 */

import type { IntegrationId, IntegrationOptionsResponse } from './api'

export type IntegrationAvailability =
  | { state: 'loading' }
  | { state: 'ready'; offered: ReadonlySet<IntegrationId> }
  | { state: 'fallback' }

export function integrationAvailability(options: IntegrationOptionsResponse | null | undefined): IntegrationAvailability {
  if (!options || !Array.isArray(options.integrations)) return { state: 'fallback' }
  return {
    state: 'ready',
    offered: new Set(options.integrations
      .filter((option) => option.configured === true && option.allowed === true)
      .map((option) => option.id)),
  }
}

/** Status endpoints are read only for offered integrations, so no role triggers a 403. */
export function shouldReadStatus(availability: IntegrationAvailability, id: IntegrationId) {
  return availability.state === 'fallback' || (availability.state === 'ready' && availability.offered.has(id))
}

/**
 * Offered integrations show in every state, including not connected and an
 * honest failed status read. In fallback, disconnected and failed stay hidden.
 */
export function isShownConnection(availability: IntegrationAvailability, id: IntegrationId, tone: string) {
  if (availability.state === 'ready') return availability.offered.has(id)
  if (availability.state === 'fallback') return tone !== 'disconnected' && tone !== 'failed'
  return false
}
