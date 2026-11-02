import { useState, type CSSProperties } from 'react'
import type { ArtifactTypeInfo, Booking, Conversation, ConversationMessage, User } from '../../lib/api'
import type { DesktopConversationData, DesktopPortalIntent, DesktopResource, DesktopCollection } from '../../lib/desktop-portal-api'
import { formatCustomerPrice } from '../../lib/format'
import Lightbox from '../Lightbox'
import { useLightbox } from '../../hooks/useLightbox'
import { studioTimeZone } from '../../hooks/useStudioSettings'
import '../../styles/lightbox.css'

// The existing Courier cards own mobile/global styles and their own fetches.
// This desktop-only view transcribes the approved DC Courier list, booking stub
// and playing-card faces; all reads/writes remain in the page's API adapter.
type ArtifactIntent = Extract<DesktopPortalIntent, { kind: 'artifact.action' }>
export interface CourierWorkspaceProps {
  inbox: DesktopResource<DesktopCollection<Conversation>>
  selectedId: string | null
  thread: DesktopConversationData | null
  threadLoading: boolean
  threadError: string
  user: User
  bookings: Booking[]
  draft: string
  working: boolean
  artifactTypes: ArtifactTypeInfo[]
  onSelect: (id: string) => void
  onDraft: (value: string) => void
  onSend: () => void
  onAction: (intent: ArtifactIntent) => void
  onMercury: (prompt: string) => void
  onRetry: () => void
}
const mono: CSSProperties = { fontFamily: "'JetBrains Mono',monospace", fontSize: 10.5, letterSpacing: '.1em', color: 'var(--ink-ghost)' }
const title: CSSProperties = { fontFamily: "'Poppins',sans-serif", fontSize: 13.5, fontWeight: 800, textTransform: 'uppercase', color: 'var(--ink)' }
const pill: CSSProperties = { minHeight: 31, borderRadius: 99, border: '1px solid var(--gold-decor)', background: 'transparent', color: 'var(--ink)', padding: '0 13px', cursor: 'pointer', ...mono }
const str = (value: unknown, fallback = 'Not provided') => typeof value === 'string' && value ? value : fallback
const money = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? formatCustomerPrice(value) : 'Not provided'
function safeLink(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  try { const url = new URL(value, window.location.origin); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined } catch { return undefined }
}
const name = (conversation: Conversation) => conversation.name ?? conversation.booking_context?.client_name ?? 'Conversation'
const time = (date: string) => new Date(date).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: studioTimeZone() })

function BookingStub({ conversation, booking }: { conversation: Conversation; booking?: Booking }) {
  const [expanded, setExpanded] = useState(true)
  const context = conversation.booking_context
  const images = (context?.reference_urls ?? []).flatMap((url, index) => {
    const src = safeLink(url)
    return src ? [{ src, alt: `Booking reference ${index + 1}`, referrerPolicy: 'no-referrer' as const }] : []
  })
  const viewer = useLightbox(images.length)
  const [failedImages, setFailedImages] = useState<string[]>([])
  if (!context) return <header style={{ padding: '13px 16px', borderBottom: '1px solid var(--line)', background: 'linear-gradient(100deg,#1d1d1d,#0c0c0c 80%)' }}><strong>{name(conversation)}</strong></header>
  const date = context.scheduled_date ?? context.preferred_date
  return <section data-booking-stub={context.id} style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', borderRadius: 10, background: 'var(--surface)', border: '2px solid #D4C9B5CC', boxShadow: '0 10px 26px rgba(0,0,0,.35)', overflow: 'hidden' }}>
    <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', border: 0, background: 'transparent', cursor: 'pointer', textAlign: 'left', color: 'var(--ink)' }}>
      <span style={{ ...title, padding: '5px 8px', border: '1px dotted var(--line-2)', borderRadius: 8 }}>{date ? new Date(date+'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'TBD'}</span>
      <span style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}><span style={mono}>{context.booking_kind} · {context.reference_code ?? context.id} · {context.status}</span><span style={{ ...title, color: 'var(--accent-coral)' }}>{context.client_name ?? name(conversation)}</span></span><span>{expanded ? '−' : '+'}</span>
    </button>
    {expanded && <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,minmax(0,1fr))', borderTop: '2px solid #D4C9B5CC' }}>{[['Type', context.style_preference ?? context.booking_kind], ['Placement', context.placement], ['Size', booking?.size_estimate], ['Budget', booking?.budget]].map(([label,value]) => <div key={label} style={{ padding: '7px 10px', borderRight: '1px solid #D4C9B5CC' }}><div style={mono}>{label}</div><div style={{ ...title, fontSize: 11 }}>{value ?? 'Not provided'}</div></div>)}</div>
      <div style={{ padding: '8px 12px 10px', borderTop: '1px dashed var(--line-2)' }}><span style={mono}>{context.artist_name ?? 'Artist not assigned'} · {context.scheduled_time ?? 'Time pending'}</span><p style={{ fontSize: 11.5, color: 'var(--ink-mute)', whiteSpace: 'pre-wrap' }}>{context.description}</p></div>
      {!!context.reference_urls.length && <div style={{ padding: '8px 12px 11px', borderTop: '1px dashed var(--line-2)', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {images.map((image, index) => <button key={`${image.src}-${index}`} type="button" aria-label={`Preview ${image.alt.toLowerCase()}`} onClick={() => viewer.openAt(index)} style={{ width: 88, height: 88, padding: 0, overflow: 'hidden', borderRadius: 8, border: '1px solid var(--line-2)', background: 'var(--ground)', color: 'var(--ink)', cursor: 'zoom-in' }}>
          {failedImages.includes(image.src) ? <span style={{ fontSize: 11 }}>Reference {index + 1}<br />Preview unavailable</span> : <img src={image.src} alt={image.alt} referrerPolicy="no-referrer" loading="lazy" onError={() => setFailedImages(current => [...current, image.src])} style={{ display: 'block', width: '100%', height: '100%', objectFit: 'cover' }} />}
        </button>)}
        {images.length < context.reference_urls.length && <span style={mono}>Some references are unavailable.</span>}
      </div>}
    </>}
    {viewer.open && images[viewer.index] && <Lightbox images={images} currentIndex={viewer.index} onClose={viewer.close} onNext={viewer.next} onPrev={viewer.prev} />}
  </section>
}

function ArtifactCard({ message, client, types, working, onAction }: { message: ConversationMessage; client: boolean; types: ArtifactTypeInfo[]; working: boolean; onAction: (intent: ArtifactIntent) => void }) {
  const [flipped, setFlipped] = useState(false)
  const payload = message.artifact_payload ?? {}
  const state = message.artifact_state ?? {}
  const quote = message.message_type === 'quote_proposal'
  const slots = message.message_type === 'slot_proposal' && Array.isArray(payload.slots) ? payload.slots.map((slot,index) => ({ slot, index })).filter((entry): entry is { slot: Record<string, unknown>; index: number } => !!entry.slot && typeof entry.slot === 'object') : []
  const allowed = types.find(type => type.message_type === message.message_type)?.allowed_actions ?? []
  const pending = state.status === 'pending'
  const known = quote || message.message_type === 'slot_proposal'
  const action = (verb: 'confirm'|'cancel'|'select_slot', index?: number) => {
    if (quote && verb !== 'select_slot') onAction({ kind: 'artifact.action', conversationId: message.conversation_id, messageId: message.id, messageType: 'quote_proposal', action: verb })
    else if (!quote && verb === 'select_slot') onAction({ kind: 'artifact.action', conversationId: message.conversation_id, messageId: message.id, messageType: 'slot_proposal', action: 'select_slot', slotIndex: index! })
    else if (!quote && verb === 'cancel') onAction({ kind: 'artifact.action', conversationId: message.conversation_id, messageId: message.id, messageType: 'slot_proposal', action: 'cancel' })
  }
  if (!flipped) return <button type="button" data-artifact-back={message.id} onClick={() => setFlipped(true)} aria-label={`Open ${message.message_type.replaceAll('_',' ')} card`} style={{ width: 118, height: 168, padding: 0, border: '1.5px solid var(--gold-decor)', borderRadius: 12, background: 'linear-gradient(160deg,#20261f,#0d0f0d)', cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 11, boxShadow: '0 12px 28px rgba(0,0,0,.55)' }}><span aria-hidden="true" style={{ width: 74, height: 104, border: '1px solid rgba(200,161,94,.4)', borderRadius: 8, display: 'grid', placeItems: 'center', background: 'repeating-linear-gradient(45deg,rgba(200,161,94,.07) 0 6px,transparent 6px 12px)' }}><span style={{ fontFamily: "'KC Tallboy',sans-serif", fontSize: 17, color: '#D8AE62' }}>BT</span><span style={{ color: 'var(--gold)' }}>✦</span></span><span style={{ ...mono, color: 'var(--gold)', fontSize: 10 }}>{message.message_type.replaceAll('_',' ')}</span></button>
  return <article data-artifact-face={message.id} style={{ display: 'flex', flexDirection: 'column', background: 'var(--surface)', border: '1px solid var(--gold-decor)', borderRadius: 14, width: 260 }}>
    <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '9px 14px 7px' }}><span style={{ ...title, fontSize: 14 }}>Action · {quote ? 'Quote' : message.message_type === 'slot_proposal' ? 'Booking' : 'Details'}</span><button type="button" aria-label="Turn card over" onClick={() => setFlipped(false)} style={{ ...pill, minHeight: 28, padding: '0 8px' }}>↶</button></header>
    <div aria-hidden="true" style={{ height: 3, margin: '0 14px', background: 'repeating-linear-gradient(90deg,var(--gold) 0 4px,transparent 4px 8px)', opacity: .35 }} />
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 14px 12px' }}>
      <strong style={title}>{str(state.status, 'State unavailable')}</strong>
      {quote && <><span>Estimate: {money(payload.estimated_total_min)} - {money(payload.estimated_total_max)}</span><span>Deposit: {money(payload.deposit_amount)}</span></>}
      {slots.map(({slot,index}) => <button type="button" key={`${str(slot.date)}-${str(slot.time)}-${index}`} style={pill} disabled={working || !client || !pending || !allowed.includes('select_slot')} onClick={() => action('select_slot',index)} aria-pressed={state.selected_index === index}>{str(slot.label, `${str(slot.date)} · ${str(slot.time)}`)} {state.selected_index === index ? '✓' : ''}</button>)}
      {message.content && <p style={{ margin: 0, fontSize: 11.5, color: 'var(--ink-mute)' }}>{message.content}</p>}
      {client && quote && pending && allowed.includes('confirm') && <button type="button" style={pill} disabled={working} onClick={() => action('confirm')}>Confirm quote</button>}
      {client && known && pending && allowed.includes('cancel') && <button type="button" style={pill} disabled={working} onClick={() => action('cancel')}>Decline</button>}
      {!client && pending && <span style={mono}>Waiting for the client’s response.</span>}
      {!known && <details><summary>Stored artifact details</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 10 }}>{JSON.stringify({ payload, state },null,2)}</pre></details>}
      {state.status === 'awaiting_payment' && <><p>Awaiting payment. Payment completion has not been confirmed.</p>{client && safeLink(state.checkout_url) && <a style={pill} href={safeLink(state.checkout_url)} target="_blank" rel="noreferrer">Resume checkout</a>}{client && allowed.includes('cancel') && <button type="button" style={pill} disabled={working} onClick={() => action('cancel')}>Cancel quote</button>}</>}
      <span style={{ ...mono, overflowWrap: 'anywhere' }}>{message.id}</span>
    </div>
  </article>
}

export function CourierWorkspace(props: CourierWorkspaceProps) {
  const [query, setQuery] = useState('')
  const [fan, setFan] = useState(false)
  const [overviewFilter, setOverviewFilter] = useState<'all' | 'unread' | 'bookings'>('all')
  const loaded = props.inbox.data?.items ?? []
  const unread = (item: Conversation) => item.is_unread || item.unread_count > 0
  const list = props.inbox.data?.items.filter(item => `${name(item)} ${item.last_message?.content ?? ''}`.toLowerCase().includes(query.toLowerCase())) ?? []
  const overview = list.filter(item => overviewFilter === 'unread' ? unread(item) : overviewFilter === 'bookings' ? !!item.booking_context : true)
    .sort((a,b) => Date.parse(b.last_message?.created_at ?? b.updated_at) - Date.parse(a.last_message?.created_at ?? a.updated_at))
  const context = props.thread?.conversation.booking_context
  const booking = props.thread?.booking ?? props.bookings.find(item => item.id === context?.id)
  // The actual booking client identity controls client actions, not role preview.
  const client = !!booking?.client_id && booking.client_id === props.user.id
  return <div data-courier-workspace style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex', alignItems: 'stretch', overflow: 'hidden', height: '100%', color: 'var(--ink)', fontFamily: "'Space Grotesk',sans-serif" }}>
    <aside style={{ flexShrink: 0, width: 230, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '10px 10px 6px' }}><input aria-label="Search threads" placeholder="Search threads…" value={query} onChange={event => setQuery(event.target.value)} style={{ width: '100%', boxSizing: 'border-box', background: 'var(--ground)', border: '1px solid var(--line)', borderRadius: 99, minHeight: 32, padding: '0 13px', color: 'var(--ink)', fontSize: 12.5 }} /></div>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '2px 8px 8px' }}>
        {props.inbox.error && <p role="alert">{props.inbox.error}</p>}
        {props.inbox.status === 'loading' && <p role="status">Loading threads…</p>}
        {list.map(item => <button type="button" key={item.id} data-conversation-id={item.id} aria-pressed={item.id === props.selectedId} onClick={() => props.onSelect(item.id)} style={{ display: 'flex', alignItems: 'center', gap: 11, width: '100%', padding: '10px 10px 9px', border: 0, borderRadius: 10, background: item.id === props.selectedId ? 'var(--surface)' : 'transparent', cursor: 'pointer', textAlign: 'left', color: 'var(--ink)' }}>
          <span style={{ flexShrink: 0, width: 30, height: 30, borderRadius: 99, background: 'var(--surface)', display: 'grid', placeItems: 'center', fontFamily: "'Bebas Neue',sans-serif", fontSize: 13.5, color: 'var(--gold)' }}>{name(item).split(' ').map(word=>word[0]).slice(0,2).join('')}</span>
          <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 1 }}><strong style={{ fontSize: 13.5 }}>{name(item)}</strong><span style={{ fontSize: 12, color: 'var(--ink-ghost)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.last_message?.content ?? 'No messages'}</span></span><span style={mono}>{item.unread_count > 0 ? item.unread_count : ''}</span>
        </button>)}
        {props.inbox.status !== 'loading' && !props.inbox.error && !list.length && <p>No matching threads.</p>}
      </div>
    </aside>
    <section style={{ flex: 1.25, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0, width: '100%' }}>
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 10, padding: 16, overflowY: 'auto' }}>
        {props.threadLoading ? <p role="status">Loading conversation…</p> : props.threadError ? <div role="alert"><p>{props.threadError}</p><button type="button" style={pill} onClick={props.onRetry}>Retry thread</button></div> : props.thread ? <>
          <BookingStub key={props.thread.conversation.id} conversation={props.thread.conversation} booking={booking} />
          {props.thread.bookingError && <p role="status">Booking details unavailable: {props.thread.bookingError}. Client action eligibility could not be verified.</p>}
          {props.thread.messages.filter(item => !(item.message_type === 'booking_intake' && context)).map(item => <div key={item.id} data-message-id={item.id} style={{ display: 'flex', flexDirection: 'column', gap: 3, maxWidth: '90%', alignSelf: item.sender_id === props.user.id ? 'flex-end' : 'flex-start' }}>
            <span style={mono}>{item.sender_name ?? 'Participant'}</span>
            {item.message_type === 'text' ? <span style={{ padding: '9px 13px', borderRadius: 13, background: item.sender_id === props.user.id ? 'rgba(183,149,232,.18)' : 'var(--surface)', fontSize: 13.5, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{item.content}</span> : <ArtifactCard message={item} client={client} types={props.artifactTypes} working={props.working} onAction={props.onAction} />}
            <span style={mono}>{time(item.created_at)}</span>
          </div>)}
          {!props.thread.messages.length && <p>No messages yet.</p>}
          {props.thread.has_more && <p style={mono}>Showing {props.thread.messages.length} of {props.thread.total} messages.</p>}
        </> : <section aria-label="Inbox overview" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <header><h2 style={{ ...title, fontSize: 22, margin: '0 0 6px' }}>Inbox overview</h2><p style={{ ...mono, margin: 0 }}>Your conversations, at a glance.</p></header>
          {props.inbox.status === 'loading' ? <p role="status">Loading inbox overview…</p> : props.inbox.error ? <p role="alert">Inbox overview unavailable: {props.inbox.error}</p> : <>
            <p style={{ ...mono, margin: 0 }}>Showing {loaded.length} of {props.inbox.data?.total ?? 0} conversations.</p>
            <div aria-label="Overview filters" style={{ display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 10 }}>
              {([{ id: 'all', label: 'All', count: loaded.length }, { id: 'unread', label: 'Unread', count: loaded.filter(unread).length }, { id: 'bookings', label: 'Bookings', count: loaded.filter(item => !!item.booking_context).length }] as const).map(item => <button type="button" key={item.id} aria-pressed={overviewFilter === item.id} onClick={() => setOverviewFilter(item.id)} style={{ padding: '14px 12px', textAlign: 'left', cursor: 'pointer', borderRadius: 10, border: `1px solid ${overviewFilter === item.id ? 'var(--ag-accent)' : 'var(--line)'}`, background: 'var(--surface)', color: 'var(--ink)' }}><span style={{ ...title, display: 'block', fontSize: 24 }}>{item.count}</span><span style={mono}>{item.label}</span></button>)}
            </div>
            <h3 style={{ ...title, margin: 0 }}>Recent {overviewFilter === 'unread' ? 'unread threads' : overviewFilter === 'bookings' ? 'booking threads' : 'conversations'}</h3>
            {!overview.length && <p style={{ color: 'var(--ink-mute)' }}>{query ? 'No conversations match your search and filter.' : !loaded.length ? 'Your inbox is empty.' : overviewFilter === 'unread' ? 'You’re all caught up. No unread conversations.' : 'No booking conversations in the loaded inbox.'}</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {overview.slice(0,8).map(item => <button type="button" key={item.id} aria-label={`Open conversation with ${name(item)}`} onClick={() => props.onSelect(item.id)} style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: 14, borderRadius: 10, border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--ink)', textAlign: 'left', cursor: 'pointer' }}>
                <span style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}><strong>{name(item)}</strong>{unread(item) && <span style={{ ...mono, color: 'var(--ag-accent)' }}>{item.unread_count > 0 ? `${item.unread_count} unread` : 'Unread'}</span>}</span>
                <span style={{ color: 'var(--ink-mute)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>{item.last_message?.content || 'No messages yet'}</span>
                <span style={mono}>{time(item.last_message?.created_at ?? item.updated_at)}{item.booking_context ? ` · ${item.booking_context.status}` : ''}</span>
              </button>)}
            </div>
            {overview.length > 8 && <p style={mono}>Showing the 8 most recent matching threads. Use the thread list to browse all {overview.length}.</p>}
          </>}
        </section>}
      </div>
      {props.thread && !props.threadLoading && <form onSubmit={event => { event.preventDefault(); props.onSend() }} style={{ flexShrink: 0, position: 'relative', display: 'flex', gap: 8, padding: '12px 14px' }}>
        {fan && <div aria-label="Taisō cards" style={{ position: 'absolute', bottom: 56, left: 8, display: 'flex', alignItems: 'flex-end', padding: '14px 18px 10px', zIndex: 5 }}>{[{ label: 'Review', rank: 'J', prompt: 'Review this conversation and identify what needs a reply. Do not send anything.' }, { label: 'Draft', rank: 'Q', prompt: 'Draft a reply to this conversation for my review. Do not send it.' }, { label: 'Next move', rank: 'K', prompt: 'Suggest the next action for this conversation. Do not execute actions.' }].map((item,i) => <button type="button" key={item.label} data-mercury-card={item.label} disabled={props.working} onClick={() => { props.onMercury(item.prompt); setFan(false) }} style={{ width: 74, height: 104, marginLeft: i ? -8 : 0, borderRadius: 10, border: '1px solid var(--gold-decor)', background: 'linear-gradient(160deg,var(--surface),var(--ground))', cursor: 'pointer', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '7px 8px', transform: `rotate(${(i-1)*9}deg)`, transformOrigin: '50% 120%', boxShadow: '0 6px 18px rgba(0,0,0,.45)' }}><span style={{ ...mono, color: 'var(--gold)' }}>{item.rank}</span><span style={{ fontSize: 34, color: '#B795E8' }}>✺</span><span style={{ ...mono, fontSize: 10 }}>{item.label}</span></button>)}</div>}
        <button type="button" aria-label="Taisō cards" aria-expanded={fan} onClick={() => setFan(!fan)} style={{ ...pill, border: 0, color: '#B795E8', fontSize: 22 }}>✺</button>
        <input aria-label="Message recipient" placeholder={`Message ${name(props.thread.conversation)}…`} value={props.draft} onChange={event => props.onDraft(event.target.value)} style={{ flex: 1, minWidth: 0, border: 0, borderRadius: 99, minHeight: 34, padding: '0 14px', color: 'var(--ink)', fontSize: 13, background: 'linear-gradient(-90deg,#0F0E0E,#141414)', boxShadow: 'inset 0 2px 6px rgba(0,0,0,.55)' }} />
        <button type="submit" style={{ ...pill, background: 'rgba(183,149,232,.35)' }} disabled={props.working || !props.draft.trim()}>Send</button>
      </form>}
    </section>
  </div>
}
