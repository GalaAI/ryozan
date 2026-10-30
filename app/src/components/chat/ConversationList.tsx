/**
 * ConversationList: Reusable conversation rail for messaging UIs.
 *
 * Renders a search bar + scrollable list of conversations.
 * Used by Courier messaging surfaces.
 */

import { useState } from 'react'
import { conversations as convsApi } from '../../lib/api'
import type { Conversation, ConversationMessage } from '../../lib/api'

interface MessageMeta {
  direction?: string
  from?: string
  from_email?: string
}

function parseMessageMeta(msg: ConversationMessage | null | undefined): MessageMeta | null {
  if (!msg?.metadata_json) return null
  try {
    return typeof msg.metadata_json === 'string' ? JSON.parse(msg.metadata_json) : msg.metadata_json
  } catch {
    return null
  }
}

function isInboundEmail(msg: ConversationMessage | null | undefined): boolean {
  const meta = parseMessageMeta(msg)
  return msg?.message_type === 'email' && meta?.direction === 'inbound'
}

function getInboundEmailSender(msg: ConversationMessage): string | null {
  const meta = parseMessageMeta(msg)
  return meta?.from || meta?.from_email || null
}

function getCourierLaneLabel(conversation: Conversation): string | null {
  const context = conversation.channel_context
  if (!context?.lane_label) return null
  if (context.assigned_agent_label && context.assigned_agent_label !== context.lane_label) {
    return `${context.lane_label} / ${context.assigned_agent_label}`
  }
  return context.lane_label
}

function getTimeDiffHours(dateStr: string): number {
  return (Date.now() - new Date(dateStr).getTime()) / (1000 * 60 * 60)
}

function formatConvoTime(dateStr: string): string {
  const date = new Date(dateStr)
  const diffHours = getTimeDiffHours(dateStr)
  if (diffHours < 24) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (diffHours < 48) return 'Yesterday'
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function extractLegacyBookingIntakeNote(content: string): string {
  if (!content.startsWith('Booking inquiry from')) return content

  const lines = content.split(/\r?\n/)
  const blankIndex = lines.findIndex((line) => line.trim() === '')
  if (blankIndex === -1) return content

  const detailPrefixes = [
    'Placement:',
    'Size:',
    'Style:',
    'Budget:',
    'Timeline:',
    'Preferred date:',
    'Preferred time:',
    'Notes:',
    'References:',
  ]
  const noteLines: string[] = []
  for (const line of lines.slice(blankIndex + 1)) {
    const trimmed = line.trim()
    if (!trimmed) continue
    if (detailPrefixes.some((prefix) => trimmed.startsWith(prefix))) break
    noteLines.push(trimmed)
  }
  return noteLines.length ? noteLines.join(' ') : content
}

interface ConversationListProps {
  conversations: Conversation[]
  activeId: string | null
  currentUserId?: string
  currentUserIds?: string[]
  channelFilters?: Array<{ id: string; label: string; maskedNumber: string | null; enabled: boolean }>
  selectedChannelFilter?: string
  onChannelFilterChange?: (id: string) => void
  onSelect: (id: string) => void
  onNewConversation?: () => void
  loading?: boolean
  error?: boolean
  onRetry?: () => void
  onActionError?: (message: string) => void
  onConversationRemoved?: (id: string) => void
  className?: string
}

export default function ConversationList({
  conversations,
  activeId,
  currentUserId,
  currentUserIds,
  channelFilters,
  selectedChannelFilter = 'all',
  onChannelFilterChange,
  onSelect,
  onNewConversation,
  loading = false,
  error = false,
  onRetry,
  onActionError,
  onConversationRemoved,
  className,
}: ConversationListProps) {
  const [search, setSearch] = useState('')
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState<{ id: string; action: 'archive' | 'delete' } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const getName = (conv: Conversation): string => {
    const viewerIds = currentUserIds ?? (currentUserId ? [currentUserId] : [])
    const bookingClientName = conv.booking_context?.client_name?.trim()
    if (bookingClientName) return bookingClientName
    const strippedBookingName = conv.name?.replace(/^booking\s*:\s*/i, '').trim()
    if (strippedBookingName && strippedBookingName !== conv.name) return strippedBookingName
    if (conv.name) return conv.name
    if (conv.last_message && isInboundEmail(conv.last_message)) {
      return getInboundEmailSender(conv.last_message) || conv.last_message.sender_name || 'Email reply'
    }
    if (conv.last_message?.sender_name && !viewerIds.includes(conv.last_message.sender_id)) {
      return conv.last_message.sender_name
    }
    return 'Direct Message'
  }

  const getPreview = (conv: Conversation): string => {
    if (!conv.last_message) return 'No messages yet'
    const viewerIds = currentUserIds ?? (currentUserId ? [currentUserId] : [])
    const prefix = !isInboundEmail(conv.last_message) && viewerIds.includes(conv.last_message.sender_id) ? 'You: ' : ''
    const content = conv.last_message.message_type === 'booking_intake'
      ? (conv.booking_context?.description || extractLegacyBookingIntakeNote(conv.last_message.content))
      : conv.last_message.content
    return prefix + (content.length > 60 ? content.slice(0, 60) + '...' : content)
  }

  const getTime = formatConvoTime

  const searchableText = (conv: Conversation): string => [
    getName(conv),
    conv.last_message ? getPreview(conv) : null,
    conv.booking_context?.reference_code,
    conv.booking_context?.client_name,
    conv.booking_context?.client_email,
    conv.booking_context?.description,
    getCourierLaneLabel(conv),
  ].filter(Boolean).join(' ').toLowerCase()

  const filtered = conversations
    .filter((c) => !hidden.has(c.id))
    .filter((c) => {
      const q = search.trim().toLowerCase()
      return q ? searchableText(c).includes(q) : true
    })

  const handleConfirm = async () => {
    if (!confirming) return
    const { id, action } = confirming
    setBusy(id)
    setConfirming(null)
    try {
      if (action === 'archive') await convsApi.archive(id)
      else await convsApi.delete(id)
      setHidden((prev) => new Set([...prev, id]))
      onConversationRemoved?.(id)
    } catch {
      onActionError?.(action === 'delete' ? 'Thread could not be deleted' : 'Thread could not be archived')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className={className}>
      <div className="courier-rail-header">
        <span className="courier-rail-title">Messages</span>
        {onNewConversation && (
          <button
            type="button"
            className="msg-new-btn"
            onClick={onNewConversation}
            aria-label="New message"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
              <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        )}
      </div>

      <div className="courier-search-wrap">
        <input
          type="text"
          placeholder="Search..."
          className="courier-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search conversations"
        />
        {channelFilters && channelFilters.length > 0 && onChannelFilterChange && (
          <div className="courier-number-filter" role="tablist" aria-label="Filter conversations by receiving number">
            {channelFilters.map((filter) => (
              <button
                key={filter.id}
                type="button"
                className={`courier-number-filter-btn${selectedChannelFilter === filter.id ? ' active' : ''}`}
                onClick={() => onChannelFilterChange(filter.id)}
                disabled={!filter.enabled && filter.id !== 'all'}
                role="tab"
                aria-selected={selectedChannelFilter === filter.id}
                title={filter.maskedNumber ? `${filter.label} ${filter.maskedNumber}` : filter.label}
              >
                <span>{filter.label}</span>
                {filter.maskedNumber && <small>{filter.maskedNumber}</small>}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="courier-conv-list">
        {loading && conversations.length === 0 ? (
          <p className="courier-rail-state">
            Loading conversations...
          </p>
        ) : error && conversations.length === 0 ? (
          <div className="courier-rail-state courier-rail-state--stack">
            <p>
              Could not load conversations.
            </p>
            {onRetry && (
              <button type="button" className="courier-rail-state-btn" onClick={onRetry}>
                Retry
              </button>
            )}
          </div>
        ) : filtered.length === 0 ? (
          <p className="courier-rail-state">
            {search ? 'No matches' : 'No conversations yet'}
          </p>
        ) : (
          filtered.map((conv) => {
            const displayName = getName(conv)
            const isActive = activeId === conv.id
            return (
            <div key={conv.id} className={`courier-conv-wrap${busy === conv.id ? ' is-busy' : ''}`}>
              {confirming?.id === conv.id ? (
                <div className="courier-conv-confirm">
                  <span className="courier-conv-confirm-text">
                    {confirming.action === 'delete' ? 'Delete forever?' : 'Archive this thread?'}
                  </span>
                  <button
                    type="button"
                    onClick={handleConfirm}
                    className={`courier-conv-confirm-btn${confirming.action === 'delete' ? ' courier-conv-confirm-btn--danger' : ''}`}
                  >
                    {confirming.action === 'delete' ? 'Delete' : 'Archive'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(null)}
                    className="courier-conv-confirm-btn courier-conv-confirm-btn--ghost"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className={`courier-conv-item${isActive ? ' active' : ''}`}>
                  <button
                    type="button"
                    className="courier-conv-main"
                    onClick={() => busy !== conv.id && onSelect(conv.id)}
                    disabled={busy === conv.id}
                    aria-label={`Open conversation with ${displayName}`}
                    aria-current={isActive ? 'true' : undefined}
                  >
                    <span className="courier-conv-avatar">
                      {displayName.charAt(0).toUpperCase()}
                    </span>
                    <span className="courier-conv-info">
                      <span className="courier-conv-name-row">
                        <span className="courier-conv-name">{displayName}</span>
                        {conv.channel_context?.routing_status === 'verified' && getCourierLaneLabel(conv) && (
                          <span className="courier-conv-lane">
                            {getCourierLaneLabel(conv)}
                            {conv.channel_context.business_number_masked
                              ? ` ${conv.channel_context.business_number_masked}`
                              : ''}
                          </span>
                        )}
                        {conv.unread_count > 0 && (
                          <span className="courier-unread-badge">{conv.unread_count}</span>
                        )}
                      </span>
                      <span className="courier-conv-preview">{getPreview(conv)}</span>
                    </span>
                    {conv.last_message && (
                      <span className="courier-conv-time">
                        {getTime(conv.last_message.created_at)}
                      </span>
                    )}
                  </button>
                  <div className="conv-item-actions">
                    <button
                      type="button"
                      className="conv-action-btn"
                      title="Archive"
                      aria-label={`Archive ${displayName}`}
                      onClick={() => setConfirming({ id: conv.id, action: 'archive' })}
                    >
                      {/* inbox/archive icon */}
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>
                      </svg>
                    </button>
                    <button
                      type="button"
                      className="conv-action-btn conv-action-btn--delete"
                      title="Delete forever"
                      aria-label={`Delete ${displayName} forever`}
                      onClick={() => setConfirming({ id: conv.id, action: 'delete' })}
                    >
                      {/* trash icon */}
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/>
                      </svg>
                    </button>
                  </div>
                </div>
              )}
            </div>
            )
          })
        )}
      </div>
    </div>
  )
}
