/**
 * ChatThread - Discord-style message groups with auto-scroll.
 *
 * Groups consecutive messages from the same sender.
 * First message in a group shows avatar + name + timestamp.
 * Subsequent messages in the group show only content (compact).
 * File/image attachments render inline.
 */

import { useEffect, useRef, useMemo, type ReactNode } from 'react'
import type { ConversationMessage } from '../../lib/api'

interface ChatThreadProps {
  messages: ConversationMessage[]
  currentUserId?: string
  currentUserIds?: string[]
  className?: string
  loading?: boolean
  error?: boolean
  onRetry?: () => void
  emptyTitle?: string
  emptyText?: string
  emptyMeta?: ReactNode
}

interface MessageGroup {
  senderId: string
  senderName: string
  isMine: boolean
  messages: ConversationMessage[]
  timestamp: string
}

interface MessageMeta {
  direction?: string
  from?: string
  from_email?: string
  name?: string
  type?: string
  url?: string
  delivery_status?: string
}

function parseMessageMeta(msg: ConversationMessage): MessageMeta | null {
  if (!msg.metadata_json) return null
  try {
    return typeof msg.metadata_json === 'string' ? JSON.parse(msg.metadata_json) : msg.metadata_json
  } catch {
    return null
  }
}

function isInboundEmail(msg: ConversationMessage): boolean {
  const meta = parseMessageMeta(msg)
  return msg.message_type === 'email' && meta?.direction === 'inbound'
}

function getDisplaySender(msg: ConversationMessage): { id: string; name: string; isExternalInbound: boolean } {
  const meta = parseMessageMeta(msg)
  const isExternalInbound = isInboundEmail(msg)
  if (isExternalInbound) {
    const sender = meta?.from || meta?.from_email || msg.sender_name || 'Email reply'
    return {
      id: `email:${meta?.from_email || sender}`,
      name: sender,
      isExternalInbound,
    }
  }
  return {
    id: msg.sender_id,
    name: msg.sender_name || 'User',
    isExternalInbound,
  }
}

function formatMessageTime(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`

  const isToday = date.toDateString() === now.toDateString()
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  const isYesterday = date.toDateString() === yesterday.toDateString()

  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (isToday) return `Today at ${time}`
  if (isYesterday) return `Yesterday at ${time}`
  return `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })} at ${time}`
}

function groupMessages(messages: ConversationMessage[], currentUserIds: string[]): MessageGroup[] {
  const groups: MessageGroup[] = []
  const viewerIds = new Set(currentUserIds.filter(Boolean))
  for (const msg of messages) {
    const last = groups[groups.length - 1]
    const displaySender = getDisplaySender(msg)
    const isMine = !displaySender.isExternalInbound && viewerIds.has(msg.sender_id)
    // Group if same sender and within 5 minutes of previous message
    if (last && last.senderId === displaySender.id) {
      const prevTime = new Date(last.messages[last.messages.length - 1].created_at).getTime()
      const thisTime = new Date(msg.created_at).getTime()
      if (thisTime - prevTime < 5 * 60 * 1000) {
        last.messages.push(msg)
        continue
      }
    }
    groups.push({
      senderId: displaySender.id,
      senderName: displaySender.name,
      isMine,
      messages: [msg],
      timestamp: msg.created_at,
    })
  }
  return groups
}

function parseFileMeta(msg: ConversationMessage): { url: string; name: string; type: string } | null {
  const meta = parseMessageMeta(msg)
  if (meta && 'url' in meta) return { url: String(meta.url), name: String(meta.name || 'file'), type: String(meta.type || '') }
  return null
}

function displayMessageContent(msg: ConversationMessage): string {
  if (msg.message_type !== 'booking_intake' || !msg.content.startsWith('Booking inquiry from')) {
    return msg.content
  }

  const lines = msg.content.split(/\r?\n/)
  const blankIndex = lines.findIndex((line) => line.trim() === '')
  if (blankIndex === -1) return msg.content

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
  return noteLines.length ? noteLines.join(' ') : msg.content
}

export default function ChatThread({
  messages,
  currentUserId,
  currentUserIds,
  className,
  loading = false,
  error = false,
  onRetry,
  emptyTitle = 'No messages yet',
  emptyText = 'Start the conversation. Send a message below.',
  emptyMeta,
}: ChatThreadProps) {
  const feedRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const previousLastMessageIdRef = useRef<string | null>(null)
  const wasNearBottomRef = useRef(true)
  const viewerIds = useMemo(
    () => currentUserIds ?? (currentUserId ? [currentUserId] : []),
    [currentUserId, currentUserIds],
  )
  const groups = useMemo(() => groupMessages(messages, viewerIds), [messages, viewerIds])

  useEffect(() => {
    const lastMessageId = messages[messages.length - 1]?.id ?? null
    const previousLastMessageId = previousLastMessageIdRef.current
    previousLastMessageIdRef.current = lastMessageId

    if (!lastMessageId || lastMessageId === previousLastMessageId) return

    const shouldScroll = !previousLastMessageId || wasNearBottomRef.current
    if (shouldScroll) {
      endRef.current?.scrollIntoView({ behavior: previousLastMessageId ? 'smooth' : 'auto' })
    }
  }, [messages])

  const handleScroll = () => {
    const feed = feedRef.current
    if (!feed) return
    wasNearBottomRef.current = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120
  }

  if (messages.length === 0) {
    const title = error ? 'Could not load messages' : loading ? 'Loading messages' : emptyTitle
    const text = error ? 'Retry the thread or select another conversation.' : loading ? 'Fetching the latest thread activity.' : emptyText

    return (
      <div
        ref={feedRef}
        className={className || 'courier-feed'}
        onScroll={handleScroll}
        role="status"
        aria-label="Conversation messages"
        aria-live="polite"
      >
        <div className="courier-thread-empty-card">
          <div className="courier-thread-empty-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" width="26" height="26">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div className="courier-thread-empty-title">{title}</div>
          <div className="courier-thread-empty-text">{text}</div>
          {error && onRetry && (
            <button type="button" className="courier-thread-retry-btn" onClick={onRetry}>
              Retry
            </button>
          )}
          {!error && emptyMeta && <div className="courier-thread-empty-meta">{emptyMeta}</div>}
        </div>
        <div ref={endRef} />
      </div>
    )
  }

  return (
    <div
      ref={feedRef}
      className={className || 'courier-feed'}
      onScroll={handleScroll}
      role="log"
      aria-label="Conversation messages"
      aria-live="polite"
      aria-relevant="additions text"
      aria-atomic="false"
    >
      {groups.map((group, gi) => (
        <div key={gi} className={`msg-group${group.isMine ? ' msg-group--mine msg-group--staff' : ''}`}>
          {/* Group header: avatar + name + timestamp */}
          <div className="msg-group-header">
            <div className="msg-group-avatar">
              {group.senderName.charAt(0).toUpperCase()}
            </div>
            <span className="msg-group-name">{group.isMine ? 'You' : group.senderName}</span>
            <span className="msg-group-time">{formatMessageTime(group.timestamp)}</span>
          </div>

          {/* Messages in this group */}
          <div className="msg-group-body">
            {group.messages.map((msg) => {
              const file = parseFileMeta(msg)
              const isImage = msg.message_type === 'image' || file?.type?.startsWith('image/')
              const content = displayMessageContent(msg)
              const meta = parseMessageMeta(msg)

              return (
                <div key={msg.id} className="msg-line">
                  {/* Text content (skip if it's just a file label) */}
                  {content && !content.startsWith('[Image:') && !content.startsWith('[File:') && (
                    <div className="msg-text">{content}</div>
                  )}

                  {/* File attachment */}
                  {file && (
                    isImage ? (
                      <a href={file.url} target="_blank" rel="noopener noreferrer" className="msg-image-attach">
                        <img src={file.url} alt={file.name} />
                      </a>
                    ) : (
                      <a href={file.url} target="_blank" rel="noopener noreferrer" className="msg-file-attach">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>
                        </svg>
                        {file.name}
                      </a>
                    )
                  )}

                  {/* Delivery Status */}
                  {group.isMine && meta?.delivery_status && (
                    <div className={`msg-delivery-status status-${meta.delivery_status}`}>
                      {meta.delivery_status === 'queued' ? 'Queued' :
                       meta.delivery_status === 'sending' ? 'Sending' :
                       meta.delivery_status === 'failed' || meta.delivery_status === 'undelivered' || meta.delivery_status === 'bounced' ? '⚠ Failed to deliver' :
                       meta.delivery_status === 'delivered' ? '✓ Delivered' :
                       meta.delivery_status === 'opened' ? '✓✓ Read' : 
                       meta.delivery_status === 'clicked' ? '✓✓ Clicked' : 
                       meta.delivery_status === 'sent' ? '✓ Sent' : 
                       meta.delivery_status}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  )
}
