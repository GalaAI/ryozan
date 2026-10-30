/**
 * ContactsMod: Courier Rolodex.
 *
 * Mirrors contacts collected from Ryozan accounts, bookings, waivers, and
 * connected intake sources exposed through the private contacts API.
 */

import { useEffect, useMemo, useState } from 'react'
import { contacts as contactsApi, type ContactRecord, type Conversation } from '../../../lib/api'

function formatTimeLabel(dateStr: string | null): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  const diffHours = (Date.now() - date.getTime()) / (1000 * 60 * 60)
  if (diffHours < 24) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (diffHours < 48) return 'Yesterday'
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function cleanName(value: string | null | undefined): string {
  const stripped = (value || '').replace(/^booking\s*:\s*/i, '').trim()
  return stripped || 'Unknown'
}

function contactKey(email?: string | null, phone?: string | null, name?: string | null): string {
  if (email) return `email:${email.toLowerCase()}`
  if (phone) return `phone:${phone.replace(/\D+/g, '')}`
  return `name:${cleanName(name).toLowerCase()}`
}

interface ContactsModProps {
  conversations: Conversation[]
  currentUserId?: string
  currentUserIds?: string[]
  onSelectContact: (conversationId: string) => void
}

interface RolodexContact {
  id: string
  name: string
  email: string | null
  phone: string | null
  sources: string[]
  bookingCount: number
  lifecycleStage: string
  lastActivity: string | null
  conversationId: string | null
}

function sourceLabel(source: string): string {
  if (source === 'bttco_user') return 'Account'
  if (source === 'booking') return 'Booking'
  if (source === 'waiver') return 'Waiver'
  return source.replace(/_/g, ' ')
}

export default function ContactsMod({
  conversations,
  currentUserId,
  currentUserIds,
  onSelectContact,
}: ContactsModProps) {
  const [search, setSearch] = useState('')
  const [apiContacts, setApiContacts] = useState<ContactRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    contactsApi.list({ limit: 1000 })
      .then((response) => {
        if (!cancelled) setApiContacts(response.contacts)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Unable to load contacts')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const contacts = useMemo(() => {
    const viewerIds = currentUserIds ?? (currentUserId ? [currentUserId] : [])
    const conversationByKey = new Map<string, Conversation>()
    for (const conv of conversations) {
      const keys = [
        contactKey(conv.booking_context?.client_email, conv.booking_context?.client_phone, conv.booking_context?.client_name),
        contactKey(null, null, conv.name),
        conv.last_message?.sender_name && !viewerIds.includes(conv.last_message.sender_id)
          ? contactKey(null, null, conv.last_message.sender_name)
          : null,
      ].filter(Boolean) as string[]
      for (const key of keys) {
        if (!conversationByKey.has(key)) conversationByKey.set(key, conv)
      }
    }

    const rows = new Map<string, RolodexContact>()
    for (const contact of apiContacts) {
      const key = contactKey(contact.email, contact.phone, contact.name)
      const conversation = conversationByKey.get(key)
      rows.set(key, {
        id: contact.id,
        name: cleanName(contact.name),
        email: contact.email,
        phone: contact.phone,
        sources: contact.tags.filter((tag) => ['bttco_user', 'booking', 'waiver'].includes(tag)),
        bookingCount: contact.booking_count,
        lifecycleStage: contact.lifecycle_stage,
        lastActivity: contact.last_booking_at,
        conversationId: conversation?.id || null,
      })
    }

    for (const conv of conversations) {
      const name = cleanName(
        conv.booking_context?.client_name
        || conv.name
        || (conv.last_message?.sender_name && !viewerIds.includes(conv.last_message.sender_id)
          ? conv.last_message.sender_name
          : null),
      )
      const key = contactKey(conv.booking_context?.client_email, conv.booking_context?.client_phone, name)
      if (rows.has(key)) {
        const existing = rows.get(key)
        if (existing && !existing.conversationId) existing.conversationId = conv.id
        continue
      }
      rows.set(key, {
        id: conv.id,
        name,
        email: conv.booking_context?.client_email || null,
        phone: conv.booking_context?.client_phone || null,
        sources: conv.booking_context ? ['booking'] : ['conversation'],
        bookingCount: conv.booking_context ? 1 : 0,
        lifecycleStage: conv.booking_context ? 'client' : 'conversation',
        lastActivity: conv.last_message?.created_at || null,
        conversationId: conv.id,
      })
    }

    return Array.from(rows.values()).sort((a, b) => {
      if (!a.lastActivity && b.lastActivity) return 1
      if (a.lastActivity && !b.lastActivity) return -1
      if (a.lastActivity && b.lastActivity) {
        return new Date(b.lastActivity).getTime() - new Date(a.lastActivity).getTime()
      }
      return a.name.localeCompare(b.name)
    })
  }, [apiContacts, conversations, currentUserId, currentUserIds])

  const filtered = search.trim()
    ? contacts.filter((c) => {
        const q = search.toLowerCase()
        return c.name.toLowerCase().includes(q)
          || (c.email || '').toLowerCase().includes(q)
          || (c.phone || '').toLowerCase().includes(q)
          || c.sources.some((source) => source.toLowerCase().includes(q))
      })
    : contacts

  return (
    <div className="contacts-layout" data-courier-contacts>
      <div className="contacts-search-wrap">
        <input
          type="text"
          placeholder="Search contacts..."
          className="courier-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="contacts-list">
        {filtered.length === 0 ? (
          <p className="contacts-empty">
            {loading ? 'Loading contacts...' : error || (search ? 'No matching contacts' : 'No contacts collected yet.')}
          </p>
        ) : (
          filtered.map((contact) => (
            <button
              type="button"
              key={contact.id}
              className={`contact-card${!contact.conversationId ? ' contact-card--disabled' : ''}`}
              onClick={() => contact.conversationId && onSelectContact(contact.conversationId)}
              disabled={!contact.conversationId}
            >
              <div className="contact-avatar">
                {contact.name.charAt(0).toUpperCase()}
              </div>
              <div className="contact-info">
                <div className="contact-name">{contact.name}</div>
                <div className="contact-email">
                  {contact.email || contact.phone || 'No direct channel yet'}
                </div>
                <div className="contact-source-row">
                  {(contact.sources.length ? contact.sources : [contact.lifecycleStage]).slice(0, 4).map((source) => (
                    <span key={source} className="contact-source-chip">{sourceLabel(source)}</span>
                  ))}
                  {contact.bookingCount > 0 && <span className="contact-source-chip">{contact.bookingCount} booking{contact.bookingCount === 1 ? '' : 's'}</span>}
                </div>
              </div>
              {contact.lastActivity && (
                <span className="contact-last-msg">
                  {formatTimeLabel(contact.lastActivity)}
                </span>
              )}
            </button>
          ))
        )}
      </div>
    </div>
  )
}
