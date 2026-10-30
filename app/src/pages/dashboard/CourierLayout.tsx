/**
 * CourierLayout: communication and marketing workspace.
 *
 * Structure: .nexus-header toolbar -> .nexus-body (.nexus-viewport)
 * Same chrome as Nexus and Huddle.
 *
 * Charms (toolbar):
 *   - Chat: messaging with quick replies
 *   - Templates: reusable message templates
 *   - Channels: email and social routing
 *   - Contacts: client rolodex from conversations
 */

import type { ReactNode } from 'react'
import { useState, useEffect, useCallback, useRef, useMemo, lazy, Suspense } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuthStore } from '../../stores/auth'
import {
  conversations as convApi,
  users as usersApi,
  artists as artistsApi,
  bookings as bookingsApi,
  responseTemplates as templatesApi,
  listEmailLog,
  getEmailStats,
  retryEmailLog,
  type Conversation,
  type ConversationMessage,
  type CourierChannelStatus,
  type User,
  type ResponseTemplate,
  type EmailLogRow,
  type EmailStatsResponse,
  type Booking,
  ApiError,
} from '../../lib/api'
import { useToast } from '../../components/Toast'
import StatusBadge from '../../components/ui/StatusBadge'
import { requestedBookingSlots, formatRequestedSlot, isApprovableStatus, canApproveBooking } from '../../lib/format'
import ConversationList from '../../components/chat/ConversationList'
import ChatThread from '../../components/chat/ChatThread'
import MessageInput from '../../components/chat/MessageInput'
import BookingApprovalPanel, { type ApprovalSlot } from '../../components/courier/BookingApprovalPanel'
import '../../styles/courier.css'

const ContactsPanel = lazy(() => import('./huddle/ContactsMod'))
const EmailConnectQuest = lazy(() => import('../../components/EmailConnectQuest'))

/* SVG Icons */

const Icons = {
  chat: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  ),
  templates: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      <line x1="9" y1="9" x2="15" y2="9"/><line x1="9" y1="13" x2="13" y2="13"/>
    </svg>
  ),
  channels: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 4h16v16H4z"/><path d="m22 6-10 7L2 6"/><path d="M16 14h4"/><path d="M18 12v4"/>
    </svg>
  ),
  contacts: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/>
    </svg>
  ),
  arrowRight: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>
    </svg>
  ),
  calendar: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>
    </svg>
  ),
  clock: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
    </svg>
  ),
  dollar: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 2v20m5-17H9.5a3.5 3.5 0 1 0 0 7h5a3.5 3.5 0 1 1 0 7H7"/>
    </svg>
  ),
  hash: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>
    </svg>
  ),
  mail: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>
    </svg>
  ),
  palette: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10"/><circle cx="12" cy="8" r="1.5" fill="currentColor"/>
      <circle cx="8" cy="12" r="1.5" fill="currentColor"/><circle cx="16" cy="12" r="1.5" fill="currentColor"/>
      <circle cx="12" cy="16" r="1.5" fill="currentColor"/>
    </svg>
  ),
  phone: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.12.91.32 1.8.57 2.65a2 2 0 0 1-.45 2.11L8 9.72a16 16 0 0 0 6.28 6.28l1.24-1.23a2 2 0 0 1 2.11-.45c.85.25 1.74.45 2.65.57A2 2 0 0 1 22 16.92z"/>
    </svg>
  ),
  pin: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0z"/><circle cx="12" cy="10" r="3"/>
    </svg>
  ),
  ruler: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 17 17 4l3 3L7 20l-3-3z"/><path d="m14 7 3 3M11 10l2 2M8 13l3 3"/>
    </svg>
  ),
  user: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
    </svg>
  ),
}

/* Charm types */

type CourierCharm = 'chat' | 'templates' | 'channels' | 'contacts'

interface ToolBeltItem {
  id: CourierCharm
  label: string
  icon: ReactNode
}

const TOOL_BELT: ToolBeltItem[] = [
  { id: 'chat',      label: 'Chat',      icon: Icons.chat },
  { id: 'templates', label: 'Templates', icon: Icons.templates },
  { id: 'channels',  label: 'Channels',  icon: Icons.channels },
  { id: 'contacts',  label: 'Contacts',  icon: Icons.contacts },
]

const DEFAULT_COURIER_NUMBER_LANES = [
  { id: 'all', label: 'All', maskedNumber: null, enabled: true },
  { id: 'bella', label: 'Bella', maskedNumber: null, enabled: false },
  { id: 'gala', label: 'Gala', maskedNumber: null, enabled: false },
  { id: 'ryozan-collector', label: 'ryozan-collector / Sigma', maskedNumber: null, enabled: false },
]

interface CourierMessageMeta {
  direction?: string
  from?: string
  from_email?: string
  to?: string
}

function conversationMatchesLane(conversation: Conversation, laneId: string): boolean {
  return laneId === 'all' || conversation.channel_context?.lane_id === laneId
}

function parseCourierMessageMeta(message: ConversationMessage | null | undefined): CourierMessageMeta | null {
  if (!message?.metadata_json) return null
  try {
    return typeof message.metadata_json === 'string' ? JSON.parse(message.metadata_json) : message.metadata_json
  } catch {
    return null
  }
}

function isInboundEmailMessage(message: ConversationMessage | null | undefined): boolean {
  const meta = parseCourierMessageMeta(message)
  return message?.message_type === 'email' && meta?.direction === 'inbound'
}

function getCourierMessageSenderName(message: ConversationMessage): string {
  const meta = parseCourierMessageMeta(message)
  if (isInboundEmailMessage(message)) return meta?.from || meta?.from_email || message.sender_name || 'Email reply'
  return message.sender_name || 'Unknown'
}

function isCourierDocumentVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState === 'visible'
}

const STYLE_LABELS: Record<string, string> = {
  traditional: 'Traditional',
  japanese: 'Japanese',
  fineline: 'Fine Line',
  blackgrey: 'Black & Grey',
  'black-grey': 'Black & Grey',
  neotraditional: 'Neo-Traditional',
  realism: 'Realism',
  illustrative: 'Illustrative',
  othernotsure: 'Other / Not Sure',
}

function imageThumb(url: string, size = 'w260'): string {
  if (url.startsWith('https://') && !url.includes('drive.google.com')) return url
  const m1 = url.match(/\/d\/([a-zA-Z0-9_-]+)/)
  if (m1) return `https://drive.google.com/thumbnail?id=${m1[1]}&sz=${size}`
  const m2 = url.match(/[?&]id=([a-zA-Z0-9_-]+)/)
  if (m2) return `https://drive.google.com/thumbnail?id=${m2[1]}&sz=${size}`
  if (url.includes('lh3.googleusercontent.com')) return url
  return url
}

function imageOriginal(url: string): string {
  const directId = url.match(/\/d\/([a-zA-Z0-9_-]+)/)?.[1]
    || url.match(/[?&]id=([a-zA-Z0-9_-]+)/)?.[1]
  return directId ? `https://drive.google.com/uc?export=view&id=${directId}` : url
}

function formatStyle(slug: string | null | undefined): string | null {
  if (!slug) return null
  return STYLE_LABELS[slug.toLowerCase()] || slug
}

function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null
  const date = new Date(iso.includes('T') ? iso : `${iso}T00:00:00`)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatTime(time: string | null | undefined): string | null {
  if (!time) return null
  const [h, m] = time.split(':')
  const hour = Number(h)
  if (Number.isNaN(hour)) return time
  const minute = m || '00'
  const period = hour >= 12 ? 'PM' : 'AM'
  const displayHour = hour % 12 || 12
  return `${displayHour}:${minute.padStart(2, '0')} ${period}`
}

export function CourierBookingInquiryCard({
  booking,
  context,
  threadName,
  participantCount,
  loading,
  onOpenImage,
  children,
}: {
  booking: Booking | null
  context: NonNullable<Conversation['booking_context']>
  threadName: string
  participantCount: number | undefined
  loading: boolean
  onOpenImage: (url: string) => void
  /** Staff actions for this request, such as the approval card. */
  children?: ReactNode
}) {
  const clientName = booking?.resolved_name || booking?.client_name || booking?.guest_name || context.client_name || threadName
  const email = booking?.resolved_email || booking?.guest_email || context.client_email
  const phone = booking?.resolved_phone || booking?.guest_phone || context.client_phone
  const referenceCode = booking?.reference_code || context.reference_code
  const status = booking?.status || context.status
  const bookingKind = booking?.booking_kind || context.booking_kind
  const consultationMethod = booking?.consultation_method || context.consultation_method
  const placement = booking?.placement || context.placement
  const style = formatStyle(booking?.style_preference || context.style_preference)
  const requestedSlots = requestedBookingSlots({
    preferred_slots: booking?.preferred_slots ?? context.preferred_slots,
    preferred_date: booking?.preferred_date ?? context.preferred_date,
    preferred_time: booking?.preferred_time ?? context.preferred_time,
  })
  const scheduledDate = formatDate(booking?.scheduled_date || context.scheduled_date)
  const scheduledTime = formatTime(booking?.scheduled_time || context.scheduled_time)
  const writtenRequest = booking?.description || context.description
  const referenceUrls = (booking?.reference_urls || context.reference_urls || []).filter(Boolean)

  return (
    <div className="courier-booking-context courier-booking-context--huddle bkm-card expanded" data-courier-context-widget="thread-grounding">
      <div className={`bkm-indicator status-${status}`} />

      <div className="courier-booking-huddle-main">
        <div className="bkm-card-top courier-booking-huddle-top">
          <div className="courier-booking-huddle-title">
            <span className="bkm-client">{clientName}</span>
            {participantCount !== undefined && (
              <span className="courier-booking-party-count">{participantCount} participant{participantCount !== 1 ? 's' : ''}</span>
            )}
          </div>
          <StatusBadge status={status} />
          {bookingKind === 'consultation' && (
            <span className="bkm-meta-item">
              Consultation{consultationMethod ? ` - ${consultationMethod.replace('_', ' ')}` : ''}
            </span>
          )}
        </div>

        <div className="bkm-card-meta courier-booking-huddle-meta">
          {context.artist_name && <span className="bkm-meta-item">{Icons.user} {context.artist_name}</span>}
          {style && <span className="bkm-meta-item">{Icons.palette} {style}</span>}
          {placement && <span className="bkm-meta-item">{Icons.pin} {placement}</span>}
          {requestedSlots.map((slot, index) => (
            <span key={`${slot.date}-${slot.time}-${index}`} className="bkm-meta-item">
              {Icons.calendar} Requested{requestedSlots.length > 1 ? ` option ${index + 1}` : ''}: {formatRequestedSlot(slot.date, slot.time)}
            </span>
          ))}
          {scheduledDate && <span className="bkm-meta-item">{Icons.calendar} Booked {scheduledDate}{scheduledTime ? ` at ${scheduledTime}` : ''}</span>}
          {loading && <span className="bkm-meta-item courier-booking-loading">Loading full intake...</span>}
        </div>

        <div className="bkm-detail-header courier-booking-huddle-detail">
          <div className="courier-booking-huddle-copy">
            {writtenRequest && (
              <div className="courier-booking-written-request">
                <span>Written request</span>
                <p>{writtenRequest}</p>
              </div>
            )}
            <div className="bkm-chips">
              {email && (
                <a className="bkm-chip" href={`mailto:${email}`}>
                  {Icons.mail} {email}
                </a>
              )}
              {phone && (
                <a className="bkm-chip" href={`tel:${phone}`}>
                  {Icons.phone} {phone}
                </a>
              )}
              {referenceCode && <span className="bkm-chip bkm-chip-mono">{Icons.hash} {referenceCode}</span>}
              {booking?.size_estimate && <span className="bkm-chip">{Icons.ruler} {booking.size_estimate}</span>}
              {booking?.budget && <span className="bkm-chip">{Icons.dollar} {booking.budget}</span>}
              {booking?.timeline && <span className="bkm-chip">{Icons.clock} {booking.timeline}</span>}
            </div>
          </div>

          {referenceUrls.length > 0 && (
            <div className="bkm-ref-images courier-booking-reference-images">
              {referenceUrls.map((url, i) => (
                <button
                  key={`${url}-${i}`}
                  className="bkm-ref-thumb"
                  type="button"
                  aria-label={`Open reference ${i + 1} at full resolution`}
                  onClick={() => onOpenImage(imageOriginal(url))}
                >
                  <img src={imageThumb(url)} alt={`Reference ${i + 1}`} />
                  <span className="bkm-ref-thumb-zoom" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none"><circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/></svg>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {(booking?.client_notes || booking?.artist_notes) && (
          <div className="courier-booking-notes-grid">
            {booking?.client_notes && (
              <div className="courier-booking-note">
                <span>Collector notes</span>
                <p>{booking.client_notes}</p>
              </div>
            )}
            {booking?.artist_notes && (
              <div className="courier-booking-note">
                <span>Staff notes</span>
                <p>{booking.artist_notes}</p>
              </div>
            )}
          </div>
        )}
        {children}
      </div>
    </div>
  )
}

/* Templates panel */

function TemplatesPanel({
  templates,
  onAdd,
  onEdit,
  onDelete,
}: {
  templates: ResponseTemplate[]
  onAdd: (title: string, body: string) => Promise<void>
  onEdit: (id: string, title: string, body: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
}) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody] = useState('')
  const [saving, setSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const [showAdd, setShowAdd] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newBody, setNewBody] = useState('')
  const [adding, setAdding] = useState(false)

  const startEdit = (t: ResponseTemplate) => {
    setEditingId(t.id)
    setEditTitle(t.title)
    setEditBody(t.body)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditTitle('')
    setEditBody('')
  }

  const commitEdit = async (id: string) => {
    if (!editTitle.trim() || !editBody.trim()) return
    setSaving(true)
    try {
      await onEdit(id, editTitle.trim(), editBody.trim())
      cancelEdit()
    } finally {
      setSaving(false)
    }
  }

  const commitAdd = async () => {
    if (!newTitle.trim() || !newBody.trim()) return
    setAdding(true)
    try {
      await onAdd(newTitle.trim(), newBody.trim())
      setNewTitle('')
      setNewBody('')
      setShowAdd(false)
    } finally {
      setAdding(false)
    }
  }

  const confirmDelete = async (id: string) => {
    setDeletingId(id)
    try {
      await onDelete(id)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="courier-templates">
      <div className="courier-templates-header">
        <span className="courier-templates-title">Templates</span>
        <button
          type="button"
          className="courier-templates-add-btn"
          onClick={() => { setShowAdd(v => !v); setNewTitle(''); setNewBody('') }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
          New
        </button>
      </div>

      {showAdd && (
        <div className="courier-tpl-form courier-tpl-form--add">
          <input
            className="courier-tpl-input"
            placeholder="Title (e.g. On my way)"
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            aria-label="Template title"
            autoFocus
          />
          <textarea
            className="courier-tpl-textarea"
            placeholder="Message body"
            rows={2}
            value={newBody}
            onChange={e => setNewBody(e.target.value)}
            aria-label="Template body"
          />
          <div className="courier-tpl-form-actions">
            <button type="button" className="courier-tpl-btn courier-tpl-btn--cancel" onClick={() => setShowAdd(false)}>Cancel</button>
            <button
              type="button"
              className="courier-tpl-btn courier-tpl-btn--save"
              onClick={commitAdd}
              disabled={adding || !newTitle.trim() || !newBody.trim()}
            >
              {adding ? 'Saving...' : 'Add'}
            </button>
          </div>
        </div>
      )}

      <div className="courier-tpl-list">
        {templates.length === 0 && (
          <div className="courier-tpl-empty">No templates yet.</div>
        )}
        {templates.map(t => (
          <div key={t.id} className={`courier-tpl-row${editingId === t.id ? ' editing' : ''}`}>
            {editingId === t.id ? (
              <div className="courier-tpl-form">
                <input
                  className="courier-tpl-input"
                  value={editTitle}
                  onChange={e => setEditTitle(e.target.value)}
                  aria-label={`Template title for ${t.title}`}
                  autoFocus
                />
                <textarea
                  className="courier-tpl-textarea"
                  rows={2}
                  value={editBody}
                  onChange={e => setEditBody(e.target.value)}
                  aria-label={`Template body for ${t.title}`}
                />
                <div className="courier-tpl-form-actions">
                  <button type="button" className="courier-tpl-btn courier-tpl-btn--cancel" onClick={cancelEdit}>Cancel</button>
                  <button
                    type="button"
                    className="courier-tpl-btn courier-tpl-btn--save"
                    onClick={() => commitEdit(t.id)}
                    disabled={saving || !editTitle.trim() || !editBody.trim()}
                  >
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <button
                  type="button"
                  className="courier-tpl-info"
                  onClick={() => startEdit(t)}
                  aria-label={`Edit template ${t.title}`}
                >
                  <span className="courier-tpl-name">{t.title}</span>
                  <span className="courier-tpl-body">{t.body}</span>
                </button>
                <button
                  type="button"
                  className="courier-tpl-delete"
                  onClick={() => confirmDelete(t.id)}
                  disabled={deletingId === t.id}
                  aria-label="Delete template"
                >
                  {deletingId === t.id ? (
                    <span className="courier-tpl-spinner" />
                  ) : (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                  )}
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ChannelsPanel({
  status,
  loading,
  onRefresh,
  onSetupEmail,
  onSetupSocial,
  emailLog,
  emailStats,
  emailLoading,
  emailRetrying,
  onRetry,
  canSeeDelivery,
}: {
  status: CourierChannelStatus | null
  loading: boolean
  onRefresh: () => void
  onSetupEmail: () => void
  onSetupSocial: (channel: CourierChannelStatus['channels'][number]) => void
  emailLog: EmailLogRow[]
  emailStats: EmailStatsResponse | null
  emailLoading: boolean
  emailRetrying: string | null
  onRetry: (id: string) => Promise<void>
  canSeeDelivery: boolean
}) {
  const channels = status?.channels || []
  const tools = status?.tools || []
  const totals = emailStats?.by_template_status ?? []
  const deliveredCount = totals
    .filter((row) => ['sent', 'delivered', 'opened', 'clicked'].includes(row.status))
    .reduce((sum, row) => sum + row.count, 0)
  const engagedCount = totals
    .filter((row) => ['opened', 'clicked'].includes(row.status))
    .reduce((sum, row) => sum + row.count, 0)
  const failedCount = totals
    .filter((row) => ['failed', 'bounced'].includes(row.status))
    .reduce((sum, row) => sum + row.count, 0)
  const channelActionLabel = (channel: CourierChannelStatus['channels'][number]) => {
    if (channel.kind === 'email') return channel.connected ? 'Manage email' : 'Add email route'
    return channel.connected ? 'Edit profile link' : `Add ${channel.label}`
  }

  return (
    <div className="courier-channels">
      <div className="courier-channels-header">
        <div>
          <span className="courier-channels-title">Channel Routing</span>
          <p className="courier-channels-subtitle">Ryozan Nexus connectors feeding Courier.</p>
        </div>
        <button type="button" className="courier-channel-refresh" onClick={onRefresh} disabled={loading}>
          {loading ? 'Checking...' : 'Refresh'}
        </button>
      </div>

      <div className="courier-channel-grid">
        {channels.map((channel) => (
          <div key={channel.id} className={`courier-channel-card${channel.connected ? ' connected' : ''}`}>
            <div className="courier-channel-top">
              <span className="courier-channel-label">{channel.label}</span>
              <span className={`courier-channel-state${channel.connected ? ' connected' : ''}`}>
                {channel.connected ? 'Connected' : 'Setup needed'}
              </span>
            </div>
            <div className="courier-channel-route">
              {channel.route || (channel.kind === 'email' ? 'No route claimed' : 'No profile link')}
            </div>
            {channel.forward_to && (
              <div className="courier-channel-forward">Forwards to {channel.forward_to}</div>
            )}
            <div className="courier-channel-meta">
              <span>{channel.direction.replaceAll('_', ' ')}</span>
              <span>{channel.status.replaceAll('_', ' ')}</span>
            </div>
            <div className="courier-channel-actions">
              <button
                type="button"
                className="courier-channel-add"
                onClick={() => channel.kind === 'email' ? onSetupEmail() : onSetupSocial(channel)}
              >
                {channelActionLabel(channel)}
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="courier-routing-policy">
        <span>Archive by default</span>
        <span>{status?.policy.archive_by_default ? 'On' : 'Off'}</span>
        <span>Audit required</span>
        <span>{status?.policy.audit_required ? 'On' : 'Off'}</span>
      </div>

      {tools.length > 0 && (
        <section className="courier-delivery-panel">
          <div className="courier-delivery-head">
            <div>
              <span className="courier-delivery-kicker">Courier tools</span>
              <h3>Provider lanes</h3>
              <p>Business channels stay on Twilio and Meta. Personal voice stays isolated on the open-source lane.</p>
            </div>
          </div>
          <div className="courier-channel-grid">
            {tools.map((tool) => (
              <div key={tool.id} className={`courier-channel-card${tool.status === 'ready' ? ' connected' : ''}`}>
                <div className="courier-channel-top">
                  <span className="courier-channel-label">{tool.label}</span>
                  <span className={`courier-channel-state${tool.status === 'ready' ? ' connected' : ''}`}>
                    {tool.status.replaceAll('_', ' ')}
                  </span>
                </div>
                <div className="courier-channel-route">{tool.provider} / {tool.lane}</div>
                <div className="courier-channel-meta">
                  <span>{tool.capabilities.slice(0, 3).join(', ')}</span>
                  <span>{tool.webhook_url ? 'webhook' : tool.endpoint ? 'endpoint' : 'planned'}</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {canSeeDelivery && (
        <section className="courier-delivery-panel">
          <div className="courier-delivery-head">
            <div>
              <span className="courier-delivery-kicker">Founder visibility</span>
              <h3>Email delivery</h3>
              <p>Confirms if collector mail was sent, delivered, opened, bounced, or failed.</p>
            </div>
          </div>

          <div className="courier-delivery-stats">
            <div className="courier-delivery-stat">
              <strong>{emailStats?.total_sent ?? deliveredCount}</strong>
              <span>sent</span>
            </div>
            <div className="courier-delivery-stat">
              <strong>{deliveredCount}</strong>
              <span>delivered</span>
            </div>
            <div className="courier-delivery-stat">
              <strong>{engagedCount}</strong>
              <span>opened</span>
            </div>
            <div className="courier-delivery-stat courier-delivery-stat--warn">
              <strong>{emailStats?.total_failed ?? failedCount}</strong>
              <span>failed</span>
            </div>
          </div>

          <div className="courier-delivery-note">
            Collector replies to Courier notification emails are forwarded and archived back into the originating conversation thread.
          </div>

          <div className="courier-delivery-log">
            <div className="courier-delivery-log-head">
              <span>Recent email events</span>
              <button type="button" className="courier-channel-refresh" onClick={onRefresh} disabled={loading || emailLoading}>
                {loading || emailLoading ? 'Checking...' : 'Refresh'}
              </button>
            </div>

            {emailLoading ? (
              <div className="courier-delivery-empty">Loading delivery activity...</div>
            ) : emailLog.length === 0 ? (
              <div className="courier-delivery-empty">No email activity logged yet.</div>
            ) : (
              <div className="courier-delivery-log-list">
                {emailLog.map((row) => (
                  <div key={row.id} className="courier-delivery-row">
                    <span className={`courier-delivery-dot courier-delivery-dot--${row.status}`} />
                    <div className="courier-delivery-copy">
                      <div className="courier-delivery-primary">
                        <span className="courier-delivery-email">{row.to_email}</span>
                        <span className={`courier-delivery-badge courier-delivery-badge--${row.status}`}>{row.status}</span>
                        <span className="courier-delivery-template">{row.template}</span>
                      </div>
                      <div className="courier-delivery-subject">{row.subject}</div>
                      {row.error && <div className="courier-delivery-error">{row.error}</div>}
                      <div className="courier-delivery-meta">
                        {new Date(row.created_at).toLocaleString()}
                        {row.retry_count > 0 ? ` · ${row.retry_count} retry${row.retry_count > 1 ? 's' : ''}` : ''}
                      </div>
                    </div>
                    {(row.status === 'failed' || row.status === 'bounced') && (
                      <button
                        type="button"
                        className="courier-delivery-retry"
                        onClick={() => void onRetry(row.id)}
                        disabled={emailRetrying === row.id}
                        aria-label={`Retry email delivery to ${row.to_email}`}
                      >
                        {emailRetrying === row.id ? '...' : 'Retry'}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  )
}

/* Main Component */

export default function CourierLayout() {
  const currentUser = useAuthStore((s) => s.user)
  const effectiveRole = useAuthStore((s) => s.effectiveRole)
  const toast = useToast()
  const toastRef = useRef(toast)
  const navigate = useNavigate()
  const currentUserIds = useMemo(
    () => [currentUser?.id, currentUser?.proxy_for].filter(Boolean) as string[],
    [currentUser?.id, currentUser?.proxy_for],
  )
  const isCollector = effectiveRole === 'collector'
  const canUseCourierTools = !isCollector

  const [activeCharm, setActiveCharm] = useState<CourierCharm>('chat')
  const [railOpen, setRailOpen] = useState(true)

  // Read conversation ID from navigation state or query params
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  // Huddl hands over an approval intent when staff pick a collector time or
  // choose to message first; the thread opens with the approval card ready.
  const approvalIntent = useMemo(() => {
    const state = location.state as {
      approveBookingId?: string
      approveSlot?: ApprovalSlot | null
      approveCustom?: boolean
      approveDuration?: number | null
    } | null
    if (!state?.approveBookingId) return null
    return {
      bookingId: state.approveBookingId,
      slot: state.approveSlot ?? null,
      custom: Boolean(state.approveCustom),
      duration: state.approveDuration ?? null,
      nonce: location.key,
    }
  }, [location.state, location.key])
  const focusComposer = useCallback(() => {
    document.querySelector<HTMLTextAreaElement>('.courier-thread-stage textarea')?.focus()
  }, [])
  const initialConvId = (location.state as { conversationId?: string })?.conversationId
    || searchParams.get('convo')
    || searchParams.get('conv')
    || null

  // Conversations
  const [convList, setConvList] = useState<Conversation[]>([])
  const [activeConvId, setActiveConvId] = useState<string | null>(initialConvId)
  const [convLoading, setConvLoading] = useState(false)
  const [convError, setConvError] = useState(false)
  const [selectedNumberLane, setSelectedNumberLane] = useState('all')

  // Messages
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [messagesLoading, setMessagesLoading] = useState(false)
  const [messagesError, setMessagesError] = useState(false)
  const [assistError, setAssistError] = useState<string | null>(null)
  const [newMessage, setNewMessage] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [workspaceNote, setWorkspaceNote] = useState('')

  // Quick reply / templates
  const [showQuickReply, setShowQuickReply] = useState(false)
  const [quickReplies, setQuickReplies] = useState<ResponseTemplate[]>([])
  const quickReplyRef = useRef<HTMLDivElement | null>(null)
  const [channelStatus, setChannelStatus] = useState<CourierChannelStatus | null>(null)
  const [channelsLoading, setChannelsLoading] = useState(false)
  const [emailLog, setEmailLog] = useState<EmailLogRow[]>([])
  const [emailStats, setEmailStats] = useState<EmailStatsResponse | null>(null)
  const [emailDeliveryLoading, setEmailDeliveryLoading] = useState(false)
  const [emailRetrying, setEmailRetrying] = useState<string | null>(null)
  const [emailSetupOpen, setEmailSetupOpen] = useState(false)
  const [activeBooking, setActiveBooking] = useState<Booking | null>(null)
  const [activeBookingLoading, setActiveBookingLoading] = useState(false)
  const [bookingLightboxUrl, setBookingLightboxUrl] = useState<string | null>(null)
  const channelStatusAttemptedRef = useRef(false)
  const convErrorToastShownRef = useRef(false)
  const messagesErrorToastShownRef = useRef(false)
  const channelErrorToastShownRef = useRef(false)
  const emailDeliveryErrorToastShownRef = useRef(false)
  const activeConvIdRef = useRef(activeConvId)
  const conversationsRequestSeqRef = useRef(0)
  const messagesRequestSeqRef = useRef(0)
  const conversationsInFlightRef = useRef(false)
  const messagesInFlightRef = useRef(false)
  const typingInFlightRef = useRef(false)
  const channelStatusRequestSeqRef = useRef(0)
  const emailDeliveryRequestSeqRef = useRef(0)
  const galaAssistRequestSeqRef = useRef(0)
  const sendRequestSeqRef = useRef(0)
  const uploadRequestSeqRef = useRef(0)
  const canSeeDelivery = currentUser?.role === 'founder' || currentUser?.role === 'admin'
  const visibleToolbar = useMemo(
    () => canUseCourierTools ? TOOL_BELT : TOOL_BELT.filter((tool) => tool.id === 'chat'),
    [canUseCourierTools]
  )

  useEffect(() => {
    window.dispatchEvent(new CustomEvent('bttco:nexus-toolbar-register', {
      detail: {
        items: visibleToolbar.map((tool) => ({
          id: tool.id,
          label: tool.label,
          icon: tool.icon,
          active: activeCharm === tool.id,
          onClick: () => setActiveCharm(tool.id),
        })),
      },
    }))

    return () => {
      window.dispatchEvent(new CustomEvent('bttco:nexus-toolbar-clear'))
    }
  }, [activeCharm, visibleToolbar])

  // New conversation
  const [showNewConv, setShowNewConv] = useState(false)
  const [userRoster, setUserRoster] = useState<User[]>([])
  const [rosterLoading, setRosterLoading] = useState(false)
  const [rosterError, setRosterError] = useState(false)
  const [contactSearch, setContactSearch] = useState('')
  const [selectedRecipients, setSelectedRecipients] = useState<User[]>([])
  const [isStartingConversation, setIsStartingConversation] = useState(false)
  const isStartingConversationRef = useRef(false)

  const courierNumberLanes = useMemo(() => {
    if (!channelStatus?.sms_lanes) return DEFAULT_COURIER_NUMBER_LANES
    return [
      DEFAULT_COURIER_NUMBER_LANES[0],
      ...channelStatus.sms_lanes.map((lane) => ({
        id: lane.id,
        label: lane.assigned_agent_label && lane.assigned_agent_label !== lane.label
          ? `${lane.label} / ${lane.assigned_agent_label}`
          : lane.label,
        maskedNumber: lane.masked_number,
        enabled: lane.configured && lane.status === 'ready',
      })),
    ]
  }, [channelStatus])
  const filteredConvList = useMemo(
    () => convList.filter((conversation) => conversationMatchesLane(conversation, selectedNumberLane)),
    [convList, selectedNumberLane],
  )
  const activeConversation = convList.find((c) => c.id === activeConvId)
  const bookingContext = activeConversation?.booking_context
  const composerTemplates = useMemo(() => quickReplies.slice(0, 4), [quickReplies])

  useEffect(() => {
    if (!showQuickReply) return

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (target && quickReplyRef.current?.contains(target)) return
      setShowQuickReply(false)
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowQuickReply(false)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [showQuickReply])

  useEffect(() => {
    if (!bookingContext?.id) {
      setActiveBooking(null)
      setActiveBookingLoading(false)
      return
    }

    let cancelled = false
    setActiveBookingLoading(true)
    bookingsApi.get(bookingContext.id)
      .then((booking) => {
        if (!cancelled) setActiveBooking(booking)
      })
      .catch(() => {
        if (!cancelled) setActiveBooking(null)
      })
      .finally(() => {
        if (!cancelled) setActiveBookingLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [bookingContext?.id])

  useEffect(() => {
    toastRef.current = toast
  }, [toast])

  useEffect(() => {
    activeConvIdRef.current = activeConvId
  }, [activeConvId])

  useEffect(() => {
    const incomingConvId = (location.state as { conversationId?: string })?.conversationId
      || searchParams.get('convo')
      || searchParams.get('conv')
      || null
    if (incomingConvId !== activeConvIdRef.current) {
      setActiveConvId(incomingConvId)
    }
  }, [location.state, searchParams])

  const selectConversation = useCallback((conversationId: string | null) => {
    setActiveConvId(conversationId)
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('conv')
      if (conversationId) next.set('convo', conversationId)
      else next.delete('convo')
      return next
    }, { replace: true })
  }, [setSearchParams])

  useEffect(() => {
    if (canUseCourierTools) return
    if (activeCharm !== 'chat') setActiveCharm('chat')
  }, [activeCharm, canUseCourierTools])

  // Load conversations
  const loadConversations = useCallback(async () => {
    if (conversationsInFlightRef.current) return
    conversationsInFlightRef.current = true
    const requestSeq = ++conversationsRequestSeqRef.current
    setConvLoading(true)
    try {
      const data = await convApi.list()
      if (conversationsRequestSeqRef.current !== requestSeq) return
      setConvList(data.conversations)
      setConvError(false)
      convErrorToastShownRef.current = false
    } catch (error) {
      if (conversationsRequestSeqRef.current !== requestSeq) return
      setConvError(true)
      if (!convErrorToastShownRef.current) {
        toastRef.current.error(error instanceof ApiError ? error.message : 'Failed to load conversations')
        convErrorToastShownRef.current = true
      }
    } finally {
      conversationsInFlightRef.current = false
      if (conversationsRequestSeqRef.current === requestSeq) {
        setConvLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    loadConversations()
    const pollConversations = () => {
      if (isCourierDocumentVisible()) loadConversations()
    }
    const handleVisibilityChange = () => {
      if (isCourierDocumentVisible()) loadConversations()
    }
    const interval = setInterval(pollConversations, 15000)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [loadConversations])

  useEffect(() => {
    if (selectedNumberLane === 'all') return
    if (filteredConvList.length === 0) {
      setActiveConvId(null)
      return
    }
    if (!activeConvId || !filteredConvList.some((conversation) => conversation.id === activeConvId)) {
      setActiveConvId(filteredConvList[0].id)
    }
  }, [activeConvId, filteredConvList, selectedNumberLane])

  // Load response templates for quick-reply toolbar
  useEffect(() => {
    if (isCollector) return
    templatesApi.list()
      .then(list => setQuickReplies(list))
      .catch((error) => toastRef.current.error(error instanceof ApiError ? error.message : 'Failed to load templates'))
  }, [isCollector])

  const loadChannelStatus = useCallback(async () => {
    const requestSeq = ++channelStatusRequestSeqRef.current
    setChannelsLoading(true)
    try {
      const data = await convApi.channelStatus()
      if (channelStatusRequestSeqRef.current !== requestSeq) return
      setChannelStatus(data)
      channelErrorToastShownRef.current = false
    } catch (error) {
      if (channelStatusRequestSeqRef.current !== requestSeq) return
      if (!channelErrorToastShownRef.current) {
        toastRef.current.error(error instanceof ApiError ? error.message : 'Failed to load channel routing')
        channelErrorToastShownRef.current = true
      }
    } finally {
      if (channelStatusRequestSeqRef.current === requestSeq) {
        setChannelsLoading(false)
      }
    }
  }, [])

  const loadEmailDelivery = useCallback(async () => {
    if (!canSeeDelivery) return
    const requestSeq = ++emailDeliveryRequestSeqRef.current
    setEmailDeliveryLoading(true)
    try {
      const [log, stats] = await Promise.all([
        listEmailLog({ limit: 25 }),
        getEmailStats(30),
      ])
      if (emailDeliveryRequestSeqRef.current !== requestSeq) return
      setEmailLog(log.items)
      setEmailStats(stats)
      emailDeliveryErrorToastShownRef.current = false
    } catch (error) {
      if (emailDeliveryRequestSeqRef.current !== requestSeq) return
      if (!emailDeliveryErrorToastShownRef.current) {
        toastRef.current.error(error instanceof ApiError ? error.message : 'Failed to load email delivery')
        emailDeliveryErrorToastShownRef.current = true
      }
    } finally {
      if (emailDeliveryRequestSeqRef.current === requestSeq) {
        setEmailDeliveryLoading(false)
      }
    }
  }, [canSeeDelivery])

  useEffect(() => {
    if (!isCollector && !channelStatusAttemptedRef.current) {
      channelStatusAttemptedRef.current = true
      loadChannelStatus()
      if (activeCharm === 'channels') void loadEmailDelivery()
    }
  }, [activeCharm, isCollector, loadChannelStatus, loadEmailDelivery])

  const refreshChannels = useCallback(() => {
    loadChannelStatus()
    void loadEmailDelivery()
  }, [loadChannelStatus, loadEmailDelivery])

  const closeEmailSetup = useCallback(() => {
    setEmailSetupOpen(false)
    refreshChannels()
  }, [refreshChannels])

  const retryEmailDelivery = useCallback(async (id: string) => {
    setEmailRetrying(id)
    try {
      const res = await retryEmailLog(id)
      toast[res.success ? 'success' : 'error'](res.message)
      await loadEmailDelivery()
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Retry failed')
    } finally {
      setEmailRetrying(null)
    }
  }, [loadEmailDelivery, toast])

  const openSocialSetup = useCallback(() => {
    navigate('/dashboard/artist-profile')
  }, [navigate])

  // Template CRUD handlers
  const handleAddTemplate = useCallback(async (title: string, body: string) => {
    try {
      const created = await templatesApi.create({ title, body, sort_order: quickReplies.length })
      setQuickReplies(prev => [...prev, created])
      toast.success('Template added')
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Template could not be added')
      throw error
    }
  }, [quickReplies.length, toast])

  const handleEditTemplate = useCallback(async (id: string, title: string, body: string) => {
    try {
      const updated = await templatesApi.update(id, { title, body })
      setQuickReplies(prev => prev.map(t => t.id === id ? updated : t))
      toast.success('Template saved')
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Template could not be saved')
      throw error
    }
  }, [toast])

  const handleDeleteTemplate = useCallback(async (id: string) => {
    try {
      await templatesApi.remove(id)
      setQuickReplies(prev => prev.filter(t => t.id !== id))
      toast.success('Template removed')
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Template could not be removed')
      throw error
    }
  }, [toast])

  // Load messages when conversation changes
  const loadMessages = useCallback(async (options: { showLoading?: boolean } = {}) => {
    const conversationId = activeConvIdRef.current
    const showLoading = options.showLoading ?? true

    if (messagesInFlightRef.current) return
    messagesInFlightRef.current = true
    const requestSeq = ++messagesRequestSeqRef.current

    if (!conversationId) {
      setMessages([])
      setMessagesError(false)
      setMessagesLoading(false)
      messagesErrorToastShownRef.current = false
      messagesInFlightRef.current = false
      return
    }
    if (showLoading) setMessagesLoading(true)
    try {
      const data = await convApi.getMessages(conversationId, { limit: 100 })
      if (messagesRequestSeqRef.current !== requestSeq || activeConvIdRef.current !== conversationId) return
      setMessages(data.messages.reverse())
      setMessagesError(false)
      messagesErrorToastShownRef.current = false
    } catch (error) {
      if (messagesRequestSeqRef.current !== requestSeq || activeConvIdRef.current !== conversationId) return
      if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
        selectConversation(null)
        toastRef.current.error(error.status === 404 ? 'Conversation not found' : 'Conversation access denied')
        return
      }
      setMessagesError(true)
      if (!messagesErrorToastShownRef.current) {
        toastRef.current.error(error instanceof ApiError ? error.message : 'Failed to load messages')
        messagesErrorToastShownRef.current = true
      }
    } finally {
      messagesInFlightRef.current = false
      if (messagesRequestSeqRef.current === requestSeq && activeConvIdRef.current === conversationId) {
        setMessagesLoading(false)
      }
    }
  }, [selectConversation])

  useEffect(() => {
    setMessages([])
    setMessagesError(false)
    messagesErrorToastShownRef.current = false
    loadMessages({ showLoading: true })
    if (activeConvId) {
      convApi.markRead(activeConvId)
        .then(() => {
          setConvList((prev) => prev.map((conv) => (
            conv.id === activeConvId
              ? { ...conv, unread_count: 0, is_unread: false, last_read_at: new Date().toISOString() }
              : conv
          )))
        })
        .catch(() => {})
    }
  }, [activeConvId, loadMessages])

  // Poll messages every 5s
  useEffect(() => {
    if (!activeConvId) return
    const pollMessages = () => {
      if (isCourierDocumentVisible()) loadMessages({ showLoading: false })
    }
    const handleVisibilityChange = () => {
      if (isCourierDocumentVisible()) loadMessages({ showLoading: false })
    }
    const interval = setInterval(pollMessages, 5000)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [activeConvId, loadMessages])

  // Typing indicator
  const [typingNames, setTypingNames] = useState<string[]>([])
  const typingDebounce = useRef<ReturnType<typeof setTimeout> | null>(null)

  const signalTyping = useCallback(() => {
    if (!activeConvId) return
    if (typingDebounce.current) return // throttle to once per 3s
    convApi.setTyping(activeConvId).catch(() => {})
    typingDebounce.current = setTimeout(() => { typingDebounce.current = null }, 3000)
  }, [activeConvId])

  useEffect(() => {
    return () => {
      if (typingDebounce.current) {
        clearTimeout(typingDebounce.current)
        typingDebounce.current = null
      }
    }
  }, [activeConvId])

  // Poll typing status every 2s
  useEffect(() => {
    if (!activeConvId) { setTypingNames([]); return }
    const conversationId = activeConvId
    const poll = async () => {
      if (!isCourierDocumentVisible()) return
      if (typingInFlightRef.current) return
      typingInFlightRef.current = true
      try {
        const res = await convApi.getTyping(conversationId)
        if (activeConvIdRef.current === conversationId) {
          setTypingNames(res.typing)
        }
      } catch { /* silent */ }
      finally {
        typingInFlightRef.current = false
      }
    }
    poll()
    const interval = setInterval(poll, 2000)
    const handleVisibilityChange = () => {
      if (isCourierDocumentVisible()) poll()
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [activeConvId])

  // Send message
  const handleSend = async () => {
    if (!activeConvId || !newMessage.trim() || isSending) return
    const conversationId = activeConvId
    const requestSeq = sendRequestSeqRef.current + 1
    sendRequestSeqRef.current = requestSeq
    setIsSending(true)
    try {
      const msg = await convApi.sendMessage(conversationId, newMessage.trim())
      if (sendRequestSeqRef.current === requestSeq && activeConvIdRef.current === conversationId) {
        setMessages((prev) => [...prev, msg])
        setNewMessage('')
      }
      loadConversations()
    } catch (error) {
      if (sendRequestSeqRef.current === requestSeq && activeConvIdRef.current === conversationId) {
        toast.error(error instanceof ApiError ? error.message : 'Failed to send message')
      }
    } finally {
      if (sendRequestSeqRef.current === requestSeq) {
        setIsSending(false)
      }
    }
  }

  // File upload
  const [isUploading, setIsUploading] = useState(false)

  const handleFileUpload = async (files: FileList | null) => {
    if (!files || !files[0] || !activeConvId) return
    const conversationId = activeConvId
    const requestSeq = uploadRequestSeqRef.current + 1
    uploadRequestSeqRef.current = requestSeq
    setIsUploading(true)
    try {
      // Upload file via bookings image upload (reuse existing endpoint)
      const result = await bookingsApi.uploadImage(files[0])
      const fileName = files[0].name
      const isImage = files[0].type.startsWith('image/')
      // Send as a message with file metadata
      const meta = JSON.stringify({ url: result.url, name: fileName, type: files[0].type })
      const msg = await convApi.sendMessage(
        conversationId,
        isImage ? `[Image: ${fileName}]` : `[File: ${fileName}]`,
        isImage ? 'image' : 'file',
        meta,
      )
      if (uploadRequestSeqRef.current === requestSeq && activeConvIdRef.current === conversationId) {
        setMessages((prev) => [...prev, msg])
      }
      loadConversations()
    } catch (error) {
      if (uploadRequestSeqRef.current === requestSeq && activeConvIdRef.current === conversationId) {
        toast.error(error instanceof ApiError ? error.message : 'Failed to upload file')
      }
    } finally {
      if (uploadRequestSeqRef.current === requestSeq) {
        setIsUploading(false)
      }
    }
  }

  // Gala AI assist
  const [galaLoading, setGalaLoading] = useState(false)

  const handleGalaAssist = async () => {
    if (!activeConvId) return
    const conversationId = activeConvId
    const requestSeq = galaAssistRequestSeqRef.current + 1
    galaAssistRequestSeqRef.current = requestSeq
    setGalaLoading(true)
    setAssistError(null)
    try {
      const result = await convApi.galaAssist(conversationId)
      if (galaAssistRequestSeqRef.current === requestSeq && activeConvIdRef.current === conversationId) {
        setNewMessage(result.draft)
      }
    } catch (error) {
      if (galaAssistRequestSeqRef.current !== requestSeq || activeConvIdRef.current !== conversationId) return
      const message = error instanceof ApiError ? error.message : 'Gala could not generate a response'
      setAssistError(message)
      toast.error(message)
    } finally {
      if (galaAssistRequestSeqRef.current === requestSeq) {
        setGalaLoading(false)
      }
    }
  }

  // Quick reply
  const handleQuickReply = (text: string) => {
    setNewMessage(text)
    setShowQuickReply(false)
  }

  // New conversation
  const openNewConversation = async () => {
    setShowNewConv(true)
    setContactSearch('')
    setSelectedRecipients([])
    setRosterLoading(true)
    setRosterError(false)
    try {
      const isAdmin = ['assistant', 'admin', 'founder'].includes(effectiveRole)
      if (isCollector) {
        // Collectors see artists only (public endpoint, no auth required)
        const artistList = await artistsApi.list()
        setUserRoster(artistList.map((a) => ({
          id: a.id as unknown as string,
          email: '',
          name: a.name,
          role: 'artist' as const,
          avatar_url: a.avatar_url || null,
          bttcoin_balance: 0,
          quest_points: 0,
          marketing_opt_in: false,
          must_reset_password: false,
          reasons: null,
          gala_personality: 0,
          nexus_profile: null as unknown as User['nexus_profile'],
          created_at: '',
        } as User)).filter((u) => u.id !== currentUser?.id))
      } else if (isAdmin) {
        // Admins/founders see everyone
        const data = await usersApi.list({ limit: 200 })
        setUserRoster(data.filter((u: User) => u.id !== currentUser?.id))
      } else {
        // Artists see only their clients
        const clients = await usersApi.myClients()
        setUserRoster(clients.filter((u: User) => u.id !== currentUser?.id))
      }
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Failed to load contacts')
      setRosterError(true)
    } finally {
      setRosterLoading(false)
    }
  }

  const toggleRecipient = (user: User) => {
    setSelectedRecipients((prev) =>
      prev.find((r) => r.id === user.id)
        ? prev.filter((r) => r.id !== user.id)
        : [...prev, user]
    )
  }

  const startConversation = async (recipients?: User[]) => {
    const targets = recipients || selectedRecipients
    if (targets.length === 0 || isStartingConversationRef.current) return
    isStartingConversationRef.current = true
    setIsStartingConversation(true)
    try {
      const isGroup = targets.length > 1
      const conv = await convApi.create({
        participant_ids: targets.map((u) => u.id),
        is_group: isGroup,
        name: isGroup ? targets.map((u) => (u.name || 'User').split(' ')[0]).join(', ') : undefined,
      })
      setShowNewConv(false)
      setSelectedRecipients([])
      loadConversations()
      selectConversation(conv.id)
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : 'Failed to create conversation')
    } finally {
      isStartingConversationRef.current = false
      setIsStartingConversation(false)
    }
  }

  const closeNewConversation = useCallback(() => {
    if (isStartingConversationRef.current) return
    setShowNewConv(false)
  }, [])

  const handleConversationRemoved = useCallback((conversationId: string) => {
    setConvList((prev) => prev.filter((conv) => conv.id !== conversationId))
    if (activeConvIdRef.current === conversationId) {
      selectConversation(null)
    }
    void loadConversations()
  }, [loadConversations, selectConversation])

  useEffect(() => {
    if (!showNewConv && !emailSetupOpen && !bookingLightboxUrl) return

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (bookingLightboxUrl) {
        setBookingLightboxUrl(null)
        return
      }
      if (emailSetupOpen) {
        closeEmailSetup()
        return
      }
      if (showNewConv) {
        closeNewConversation()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [bookingLightboxUrl, closeEmailSetup, closeNewConversation, emailSetupOpen, showNewConv])

  // Filter roster by search + group by role
  const filteredRoster = userRoster.filter((u) => {
    // Collectors only see artists/founders
    if (isCollector && !['artist', 'assistant', 'admin', 'founder'].includes(u.role)) return false
    if (!contactSearch.trim()) return true
    const q = contactSearch.toLowerCase()
    return (u.name || '').toLowerCase().includes(q) || (u.email || '').toLowerCase().includes(q)
  })
  const rosterArtists = filteredRoster.filter((u) => ['assistant', 'artist', 'admin', 'founder'].includes(u.role))
  const rosterClients = filteredRoster.filter((u) => !['assistant', 'artist', 'admin', 'founder'].includes(u.role))

  // Helpers
  const getConvName = (conv: Conversation): string => {
    const bookingClientName = conv.booking_context?.client_name?.trim()
    if (bookingClientName) return bookingClientName
    const strippedBookingName = conv.name?.replace(/^booking\s*:\s*/i, '').trim()
    if (strippedBookingName && strippedBookingName !== conv.name) return strippedBookingName
    if (conv.name) return conv.name
    if (conv.last_message && isInboundEmailMessage(conv.last_message)) {
      return getCourierMessageSenderName(conv.last_message)
    }
    if (conv.last_message?.sender_name && !currentUserIds.includes(conv.last_message.sender_id)) {
      return conv.last_message.sender_name
    }
    return 'Direct Message'
  }

  const threadName = activeConversation ? getConvName(activeConversation) : 'Chat'
  const recentThreadMessages = useMemo(() => messages.slice(-12), [messages])
  const latestInboundMessage = useMemo(
    () => [...messages].reverse().find((message) => isInboundEmailMessage(message) || !currentUserIds.includes(message.sender_id)) ?? null,
    [currentUserIds, messages],
  )
  const recentParticipants = useMemo(() => {
    const seen = new Set<string>()
    return recentThreadMessages
      .filter((message) => {
        const senderName = getCourierMessageSenderName(message)
        const key = `${isInboundEmailMessage(message) ? 'email' : message.sender_id || 'unknown'}:${senderName}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
      .map((message) => ({
        id: message.sender_id || message.id,
        name: getCourierMessageSenderName(message),
      }))
      .slice(0, 4)
  }, [recentThreadMessages])
  const threadSummary = useMemo(() => {
    if (!activeConversation) return 'Open a thread to start drafting, capturing notes, and coordinating next steps.'
    if (!recentThreadMessages.length && bookingContext) {
      return `${threadName} is attached to ${bookingContext.reference_code || 'an active booking'} and ready for outreach.`
    }
    if (!recentThreadMessages.length) return `${threadName} is open and ready for the first outbound touch.`
    const snippets = recentThreadMessages
      .map((message) => message.content?.trim())
      .filter(Boolean)
      .slice(-3)
    return snippets.length > 0
      ? snippets.join(' ')
      : `${threadName} has activity, but the latest messages are attachments or structured payloads.`
  }, [activeConversation, bookingContext, recentThreadMessages, threadName])
  const threadSignals = useMemo(() => {
    const signals: string[] = []
    if (bookingContext?.status) signals.push(`Booking status: ${bookingContext.status}`)
    if (bookingContext?.scheduled_date) {
      signals.push(`Session window: ${new Date(bookingContext.scheduled_date).toLocaleString()}`)
    }
    if (latestInboundMessage?.created_at) {
      signals.push(`Latest inbound: ${new Date(latestInboundMessage.created_at).toLocaleString()}`)
    }
    if (activeConversation?.unread_count) signals.push(`${activeConversation.unread_count} unread message${activeConversation.unread_count === 1 ? '' : 's'}`)
    if (recentParticipants.length > 1) {
      signals.push(`Recent voices: ${recentParticipants.map((participant) => participant.name.split(' ')[0]).join(', ')}`)
    }
    return signals.slice(0, 5)
  }, [activeConversation, bookingContext, latestInboundMessage, recentParticipants])
  const threadSuggestions = useMemo(() => {
    const suggestions: string[] = []
    if (bookingContext) {
      suggestions.push(`Confirm ${bookingContext.reference_code || 'the booking'} details and next steps.`)
      suggestions.push(`Offer a concise prep and aftercare reminder for ${threadName}.`)
    } else {
      suggestions.push(`Reply with a concise update and one clear next action for ${threadName}.`)
    }
    if (latestInboundMessage?.content) {
      suggestions.push(`Answer the latest client message from ${getCourierMessageSenderName(latestInboundMessage)} and keep the thread moving.`)
    }
    suggestions.push('Turn this thread into a polished outbound message with a warm, direct tone.')
    return suggestions.slice(0, 3)
  }, [bookingContext, latestInboundMessage, threadName])
  const threadChecklist = useMemo(() => {
    if (!activeConversation) return []
    return [
      { label: 'Conversation open', done: true },
      { label: 'Booking context attached', done: Boolean(bookingContext) },
      { label: 'Reply draft staged', done: newMessage.trim().length > 0 },
      { label: 'Private workspace note captured', done: workspaceNote.trim().length > 0 },
    ]
  }, [activeConversation, bookingContext, newMessage, workspaceNote])

  useEffect(() => {
    sendRequestSeqRef.current += 1
    uploadRequestSeqRef.current += 1
    galaAssistRequestSeqRef.current += 1
    setIsSending(false)
    setIsUploading(false)
    setGalaLoading(false)
    setWorkspaceNote('')
    setShowQuickReply(false)
    setAssistError(null)
  }, [activeConvId])

  useEffect(() => {
    const detail = activeConversation ? {
      active: true,
      threadName,
      participantCount: activeConversation.participant_count,
      bookingContext: bookingContext ? {
        id: bookingContext.id,
        reference_code: bookingContext.reference_code,
        status: bookingContext.status,
        artist_name: bookingContext.artist_name,
        description: bookingContext.description,
        booking_url: bookingContext.booking_url,
        client_email: bookingContext.client_email,
        client_phone: bookingContext.client_phone,
      } : null,
      threadSummary,
      threadSignals,
      threadSuggestions,
      threadChecklist,
      workspaceNote,
      canUseCourierTools,
    } : { active: false }

    window.dispatchEvent(new CustomEvent('bttco:courier-workspace', { detail }))
  }, [
    activeConversation,
    bookingContext,
    canUseCourierTools,
    threadChecklist,
    threadName,
    threadSignals,
    threadSuggestions,
    threadSummary,
    workspaceNote,
  ])

  const handleUseSuggestion = useCallback((text: string) => {
    setNewMessage(text)
  }, [])

  const handleSummarizeToNotes = useCallback(() => {
    const note = [
      `Summary: ${threadSummary}`,
      threadSignals.length ? `Signals: ${threadSignals.join(' | ')}` : '',
    ].filter(Boolean).join('\n')
    setWorkspaceNote(note)
  }, [threadSignals, threadSummary])

  useEffect(() => {
    const noteHandler = (event: Event) => {
      const detail = (event as CustomEvent<{ note?: string }>).detail
      setWorkspaceNote(detail?.note ?? '')
    }
    const suggestionHandler = (event: Event) => {
      const detail = (event as CustomEvent<{ text?: string }>).detail
      if (detail?.text) handleUseSuggestion(detail.text)
    }
    const summarizeHandler = () => handleSummarizeToNotes()

    window.addEventListener('bttco:courier-workspace-note-change', noteHandler)
    window.addEventListener('bttco:courier-use-suggestion', suggestionHandler)
    window.addEventListener('bttco:courier-save-note-context', summarizeHandler)

    return () => {
      window.removeEventListener('bttco:courier-workspace-note-change', noteHandler)
      window.removeEventListener('bttco:courier-use-suggestion', suggestionHandler)
      window.removeEventListener('bttco:courier-save-note-context', summarizeHandler)
      window.dispatchEvent(new CustomEvent('bttco:courier-workspace', { detail: { active: false } }))
    }
  }, [handleSummarizeToNotes, handleUseSuggestion])

  const handleBookingAssist = useCallback(async () => {
    if (!bookingContext?.id) return
    const conversationId = activeConvIdRef.current
    const bookingId = bookingContext.id
    const requestSeq = galaAssistRequestSeqRef.current + 1
    galaAssistRequestSeqRef.current = requestSeq
    setGalaLoading(true)
    setAssistError(null)
    try {
      const result = await bookingsApi.galaAssist(bookingId)
      if (galaAssistRequestSeqRef.current === requestSeq && activeConvIdRef.current === conversationId) {
        setNewMessage(result.draft)
      }
    } catch (error) {
      if (galaAssistRequestSeqRef.current !== requestSeq || activeConvIdRef.current !== conversationId) return
      const message = error instanceof ApiError ? error.message : 'Booking assist could not generate a response'
      setAssistError(message)
      toast.error(message)
    } finally {
      if (galaAssistRequestSeqRef.current === requestSeq) {
        setGalaLoading(false)
      }
    }
  }, [bookingContext?.id, toast])

  // Input toolbar (left of text field)
  const inputToolbar = activeConvId ? (
    <div className="courier-input-tools">
      {/* File upload */}
      <input
        type="file"
        id="courier-file-input"
        className="courier-file-input"
        accept="image/*,.pdf,.doc,.docx"
        onChange={(e) => { handleFileUpload(e.target.files); e.target.value = '' }}
        aria-label="Attach image or document"
      />
      <button
        type="button"
        className="courier-tool-btn"
        onClick={() => document.getElementById('courier-file-input')?.click()}
        disabled={isUploading}
        title="Attach"
        aria-label="Attach file"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
        </svg>
      </button>

      {/* Gala AI generate */}
      <button
        type="button"
        className={`courier-tool-btn courier-tool-btn--gala${galaLoading ? ' loading' : ''}`}
        onClick={handleGalaAssist}
        disabled={galaLoading || messages.length === 0}
        title="Gala assist"
        aria-label="Generate reply draft"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
          <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
        </svg>
      </button>

      {bookingContext?.id && (
        <button
          type="button"
          className="courier-tool-btn courier-tool-btn--booking"
          onClick={handleBookingAssist}
          disabled={galaLoading}
          title="Booking assist"
          aria-label="Generate booking reply draft"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
            <path d="M8 2v4" />
            <path d="M16 2v4" />
            <rect x="3" y="4" width="18" height="18" rx="2" />
            <path d="M3 10h18" />
          </svg>
        </button>
      )}

      {/* Quick replies */}
      <div className="quick-reply-wrap" ref={quickReplyRef}>
        <button
          type="button"
          className="courier-tool-btn"
          onClick={() => setShowQuickReply(!showQuickReply)}
          title="Templates"
          aria-label="Open templates"
          aria-expanded={showQuickReply}
          aria-haspopup="menu"
          aria-controls="courier-template-menu"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
            <line x1="17" y1="10" x2="3" y2="10" /><line x1="21" y1="6" x2="3" y2="6" /><line x1="21" y1="14" x2="3" y2="14" /><line x1="17" y1="18" x2="3" y2="18" />
          </svg>
        </button>
        {showQuickReply && (
          <div
            id="courier-template-menu"
            className="quick-reply-dropdown"
            role="menu"
            aria-label="Message templates"
          >
            <div className="quick-reply-dropdown-title" role="presentation">Templates</div>
            {quickReplies.map((t) => (
              <button
                type="button"
                key={t.id}
                className="quick-reply-option"
                onClick={() => handleQuickReply(t.body)}
                role="menuitem"
              >
                <span className="quick-reply-pill">{t.title}</span>
                <span className="quick-reply-body">{t.body}</span>
              </button>
            ))}
            {quickReplies.length === 0 && (
              <div className="quick-reply-empty" role="status">No templates yet. Add them in Templates.</div>
            )}
          </div>
        )}
      </div>
    </div>
  ) : null


  // Shared new conversation modal renderer
  const renderNewConvModal = () => showNewConv ? (
    <div className="msg-modal-overlay" onClick={closeNewConversation}>
      <div
        className="msg-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="courier-new-message-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="courier-new-message-title" className="msg-modal-title">New Message</h3>
        <div className="courier-contact-search">
          <input
            type="text"
            className="bkm-edit-input courier-contact-search-input"
            placeholder="Search contacts..."
            value={contactSearch}
            onChange={(e) => setContactSearch(e.target.value)}
            aria-label="Search contacts"
            autoFocus
          />
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            className="courier-contact-search-icon">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </div>
        {selectedRecipients.length > 0 && (
          <div className="courier-recipient-chips">
            {selectedRecipients.map((u) => (
              <button
                key={u.id}
                type="button"
                className="bkm-chip courier-recipient-chip"
                onClick={() => toggleRecipient(u)}
                aria-label={`Remove ${u.name || u.email} from recipients`}
              >
                {(u.name || u.email).split(' ')[0]} &times;
              </button>
            ))}
          </div>
        )}
        <div className="msg-user-list courier-contact-list">
          {rosterLoading ? (
            <p className="courier-contact-state">Loading...</p>
          ) : rosterError ? (
            <div className="courier-contact-state courier-contact-state--stack">
              <p>Failed to load contacts</p>
              <button type="button" className="courier-rail-state-btn" onClick={openNewConversation}>Retry</button>
            </div>
          ) : filteredRoster.length === 0 ? (
            <p className="courier-contact-state">{contactSearch ? 'No matches' : 'No contacts found'}</p>
          ) : (
            <>
              {rosterArtists.length > 0 && (
                <>
                  <div className="msg-user-group-label">Artists</div>
                  {rosterArtists.map((u) => {
                    const selected = !!selectedRecipients.find((r) => r.id === u.id)
                    return (
                      <button key={u.id} type="button" className={`msg-user-item${selected ? ' msg-user-item--selected' : ''}`}
                        disabled={isStartingConversation}
                        onClick={() => isCollector ? startConversation([u]) : toggleRecipient(u)}>
                        <div className="msg-user-avatar">{(u.name || u.email).charAt(0).toUpperCase()}</div>
                        <div className="msg-user-info">
                          <div className="msg-user-name">{u.name || u.email}</div>
                          <div className="msg-user-role">{u.role}</div>
                        </div>
                        {!isCollector && selected && (
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="courier-contact-check">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        )}
                      </button>
                    )
                  })}
                </>
              )}
              {!isCollector && rosterClients.length > 0 && (
                <>
                  <div className="msg-user-group-label">Clients</div>
                  {rosterClients.map((u) => {
                    const selected = !!selectedRecipients.find((r) => r.id === u.id)
                    return (
                      <button
                        key={u.id}
                        type="button"
                        className={`msg-user-item${selected ? ' msg-user-item--selected' : ''}`}
                        disabled={isStartingConversation}
                        onClick={() => toggleRecipient(u)}
                      >
                        <div className="msg-user-avatar">{(u.name || u.email).charAt(0).toUpperCase()}</div>
                        <div className="msg-user-info">
                          <div className="msg-user-name">{u.name || u.email}</div>
                          <div className="msg-user-role">{u.role}</div>
                        </div>
                        {selected && (
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="courier-contact-check">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        )}
                      </button>
                    )
                  })}
                </>
              )}
            </>
          )}
        </div>
        <div className="courier-contact-actions">
          <button
            type="button"
            className="btn btn-outline courier-contact-action"
            disabled={isStartingConversation}
            onClick={() => setShowNewConv(false)}
          >
            Cancel
          </button>
          {!isCollector && (
            <button
              type="button"
              className="btn btn-primary courier-contact-action"
              disabled={selectedRecipients.length === 0 || isStartingConversation}
              onClick={() => startConversation()}
            >
              {isStartingConversation ? 'Starting...' : selectedRecipients.length > 1 ? 'Start Group' : 'Message'}
            </button>
          )}
        </div>
      </div>
    </div>
  ) : null

  /* ═══════════════════════════════════════════════════
     Universal layout: same UI for all roles
     ═══════════════════════════════════════════════════ */
  return (
    <div className="dashboard-panel home-panel nexus courier-page">
      <div className="nexus-body">
        <div className="nexus-viewport">
          {activeCharm === 'chat' && (
            <div className={`courier-layout${railOpen ? '' : ' courier-layout--rail-closed'}`}>
              {/* Rail: overflow:hidden required for align-self:stretch to work */}
              <div className="courier-rail-float">
                <div className="courier-rail">
                  <ConversationList
                    conversations={filteredConvList}
                    activeId={activeConvId}
                    currentUserId={currentUser?.id}
                    currentUserIds={currentUserIds}
                    channelFilters={isCollector ? DEFAULT_COURIER_NUMBER_LANES.slice(0, 1) : courierNumberLanes}
                    selectedChannelFilter={selectedNumberLane}
                    onChannelFilterChange={setSelectedNumberLane}
                    onSelect={selectConversation}
                    onNewConversation={openNewConversation}
                    loading={convLoading}
                    error={convError}
                    onRetry={loadConversations}
                    onActionError={(message) => toast.error(message)}
                    onConversationRemoved={handleConversationRemoved}
                  />
                </div>
              </div>
              {/* Pull tab lives outside the float so it can escape overflow:hidden */}
              <button
                type="button"
                className="courier-rail-tab"
                onClick={() => setRailOpen(p => !p)}
                aria-label={railOpen ? 'Collapse messages' : 'Expand messages'}
              >
                <svg
                  width="9" height="9" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                  className="courier-rail-tab-chevron"
                >
                  <path d="M15 18l-6-6 6-6"/>
                </svg>
                <span className="courier-rail-tab-label">Msgs</span>
                {convList.length > 0 && (
                  <span className="courier-rail-tab-count">{filteredConvList.length}</span>
                )}
              </button>

              <div className="courier-thread">
                {!activeConvId ? (
                  <div className="courier-empty">
                    <div className="courier-empty-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" width="48" height="48">
                        <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
                      </svg>
                    </div>
                    <p>Select a conversation to start messaging</p>
                  </div>
                ) : (
                  <div className="courier-thread-shell courier-thread-shell--inline-context">
                    <div className="courier-thread-stage">
                      <div className="courier-thread-header">
                        {bookingContext && (
                          <CourierBookingInquiryCard
                            booking={activeBooking}
                            context={bookingContext}
                            threadName={threadName}
                            participantCount={activeConversation?.participant_count}
                            loading={activeBookingLoading}
                            onOpenImage={setBookingLightboxUrl}
                          >
                            {bookingContext?.id && activeBooking && canApproveBooking(effectiveRole, currentUser?.id, activeBooking.artist_id) && isApprovableStatus(activeBooking.status) && (
                              <BookingApprovalPanel
                                key={`${bookingContext.id}-${approvalIntent?.bookingId === bookingContext.id ? approvalIntent.nonce : 'idle'}`}
                                booking={{
                                  id: bookingContext.id,
                                  status: activeBooking?.status ?? bookingContext.status ?? 'pending',
                                  preferred_slots: activeBooking?.preferred_slots ?? bookingContext.preferred_slots,
                                  preferred_date: activeBooking?.preferred_date ?? bookingContext.preferred_date,
                                  preferred_time: activeBooking?.preferred_time ?? bookingContext.preferred_time ?? null,
                                }}
                                initialSlot={approvalIntent?.bookingId === bookingContext.id ? approvalIntent.slot : null}
                                initialCustom={approvalIntent?.bookingId === bookingContext.id && approvalIntent.custom}
                                initialDuration={approvalIntent?.bookingId === bookingContext.id ? approvalIntent.duration : null}
                                onApproved={(result) => {
                                  setActiveBooking(result.booking)
                                  void loadMessages()
                                }}
                                onMessage={focusComposer}
                                messageLabel="Message to coordinate a time"
                              />
                            )}
                          </CourierBookingInquiryCard>
                        )}
                      </div>
                      <ChatThread
                        messages={messages}
                        currentUserId={currentUser?.id}
                        currentUserIds={currentUserIds}
                        loading={messagesLoading}
                        error={messagesError}
                        onRetry={loadMessages}
                        emptyTitle={bookingContext ? 'Booking thread ready' : undefined}
                        emptyText={
                          bookingContext
                            ? 'Use the booking context above to reply, request details, or move the inquiry forward.'
                            : undefined
                        }
                        emptyMeta={bookingContext ? (
                          <>
                            {bookingContext.reference_code && <span>{bookingContext.reference_code}</span>}
                            {bookingContext.status && <span>{bookingContext.status}</span>}
                            {bookingContext.artist_name && <span>{bookingContext.artist_name}</span>}
                          </>
                        ) : undefined}
                      />
                      {typingNames.length > 0 && (
                        <div className="courier-typing">
                          <span className="courier-typing-dots"><span /><span /><span /></span>
                          {typingNames.join(', ')} {typingNames.length === 1 ? 'is' : 'are'} typing
                        </div>
                      )}
                      <MessageInput
                        value={newMessage}
                        onChange={(v) => { setNewMessage(v); setAssistError(null); signalTyping() }}
                        onSend={handleSend}
                        disabled={isSending}
                        renderTop={(
                          <div className="courier-composer-top">
                            {assistError ? (
                              <span className="courier-composer-error">{assistError}</span>
                            ) : (
                              <>
                                <span className="courier-composer-kicker">Templates</span>
                                {composerTemplates.map((template) => (
                                  <button
                                    key={template.id}
                                    className="courier-composer-chip"
                                    type="button"
                                    title={template.body}
                                    onClick={() => handleUseSuggestion(template.body)}
                                  >
                                    {template.title}
                                  </button>
                                ))}
                                {composerTemplates.length === 0 && (
                                  <span className="courier-composer-empty">No templates</span>
                                )}
                              </>
                            )}
                          </div>
                        )}
                        renderLeft={inputToolbar}
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Templates Charm */}
          {canUseCourierTools && activeCharm === 'templates' && (
            <TemplatesPanel
              templates={quickReplies}
              onAdd={handleAddTemplate}
              onEdit={handleEditTemplate}
              onDelete={handleDeleteTemplate}
            />
          )}

          {/* Channels Charm */}
          {canUseCourierTools && activeCharm === 'channels' && (
            <ChannelsPanel
              status={channelStatus}
              loading={channelsLoading}
              onRefresh={refreshChannels}
              onSetupEmail={() => setEmailSetupOpen(true)}
              onSetupSocial={openSocialSetup}
              emailLog={emailLog}
              emailStats={emailStats}
              emailLoading={emailDeliveryLoading}
              emailRetrying={emailRetrying}
              onRetry={retryEmailDelivery}
              canSeeDelivery={canSeeDelivery}
            />
          )}

          {/* Contacts Charm */}
          {canUseCourierTools && activeCharm === 'contacts' && (
            <Suspense fallback={null}>
              <ContactsPanel
                conversations={convList}
                currentUserId={currentUser?.id}
                currentUserIds={currentUserIds}
                onSelectContact={(conversationId) => {
                  selectConversation(conversationId)
                  setActiveCharm('chat')
                }}
              />
            </Suspense>
          )}
        </div>
      </div>

      {renderNewConvModal()}
      {emailSetupOpen && (
        <div className="doc-modal-overlay" onClick={closeEmailSetup}>
          <div
            className="courier-email-setup-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="courier-email-setup-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="courier-email-setup-head">
              <div>
                <span className="courier-delivery-kicker">Channel setup</span>
                <h3 id="courier-email-setup-title">Claim email route</h3>
              </div>
              <button
                type="button"
                className="courier-email-setup-close"
                onClick={closeEmailSetup}
                aria-label="Close email setup"
              >
                ×
              </button>
            </div>
            <Suspense fallback={null}>
              <EmailConnectQuest mode="full" />
            </Suspense>
          </div>
        </div>
      )}
      {bookingLightboxUrl && (
        <div className="bkm-lightbox" onClick={() => setBookingLightboxUrl(null)}>
          <div
            className="bkm-lightbox-content"
            role="dialog"
            aria-modal="true"
            aria-label="Booking reference image"
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              className="bkm-lightbox-close"
              onClick={() => setBookingLightboxUrl(null)}
              aria-label="Close booking reference image"
            >
              ×
            </button>
            <img src={bookingLightboxUrl} alt="Booking reference" className="bkm-lightbox-img" />
          </div>
        </div>
      )}
    </div>
  )
}
