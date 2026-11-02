import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type ComponentProps, type CSSProperties, type FormEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useGSAP } from '@gsap/react'
import { gsap } from 'gsap'
import { AccountMenu, ActionCard, AgentChatSheet, AgentTools, Button, HomeScreen, IconButton, PageTitle as SharedPageTitle, PortalShell, ReceiptBlock, SectionLabel, SessionPass, type AgentActionCard, type ChatLine, type ScheduleEntry, type TabItem } from '@ryozan/ui'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  artists as artistsApi,
  bookings as bookingsApi,
  ryoCoin as ryoCoinApi,
  broadcasts as broadcastsApi,
  club as clubApi,
  contacts as contactsApi,
  conversations as conversationsApi,
  gala as galaApi,
  galaUpload,
  quests as questsApi,
  rewards as rewardsApi,
  users as usersApi,
  ApiError,
  type ArtistDetail,
  type Booking,
  type BroadcastItem,
  type ClubPost,
  type Conversation,
  type ConversationMessage,
  type GalaChatResponse,
  type QuestWithProgress,
  type RewardInventory,
  type User,
  type UserMembership,
} from '../../lib/api'
import { dynamicRoutes, ROUTES } from '../../lib/routes'
import { isAdminRole, isArtistPlusRole } from '../../lib/roles'
import { useAuthStore, type ViewAsMode } from '../../stores/auth'
import { Skeleton } from '../../components/ui/skeleton'
import { GalaComponentRenderer } from '../../components/GalaPanel'
import ArtistConnections from '../../components/ArtistConnections'
import StudioSettingsForm from '../../components/StudioSettingsForm'
import ArtistDepositActivity from '../../components/ArtistDepositActivity'
import FlashManager, { type FlashStage } from './FlashManager'
import { CourierBookingInquiryCard } from './CourierLayout'
import BookingApprovalPanel from '../../components/courier/BookingApprovalPanel'
import { requestedBookingSlots, formatRequestedSlot, isApprovableStatus, canApproveBooking } from '../../lib/format'
import AgentEvolutionStudio from '../../components/AgentEvolutionStudio'
import { BRAND } from '../../lib/brand'

const ScrollsPanel = lazy(() => import('../../components/ScrollsPanel'))
const ArtistScroll = lazy(() => import('../../components/ArtistScroll'))
const CollectorShop = lazy(() => import('./ShopPage'))
const POSModal = lazy(() => import('../../components/POSModal'))
const EmailFoundryFrame = lazy(() => import('../../components/EmailFoundryFrame'))

gsap.registerPlugin(useGSAP)

type PortalRole = 'collector' | 'assistant' | 'artist' | 'admin' | 'founder'
type HubWorkflowKind = 'flash' | 'merch' | 'gallery' | 'pos' | 'connections' | 'deposits' | 'options' | 'codex' | 'artist-scroll' | 'email-foundry' | 'studio-settings'
type ClubWorkflowKind = 'loot' | 'campaign' | 'content'
type PortalWorkflowKind = HubWorkflowKind | ClubWorkflowKind
type AgentSessionKind = 'agent' | 'booky' | 'hub'

interface AgentSessionSnapshot {
  agentId: string
  kind: AgentSessionKind
  lines: ChatLine[]
  context: Record<string, unknown>
}
type HubConfigurationTarget = 'augmentz' | 'tools'
type HubOptionTarget = HubConfigurationTarget | 'connections' | 'account' | 'email-foundry'
type AgentChatLaunch = 'direct' | 'fab' | 'anchor'

interface AgentChatOrigin {
  element: HTMLElement | null
  left: number
  top: number
  width: number
  height: number
  borderRadius: number
}
type ContentBrandLane = 'jwt' | 'ryozan'
type ContentDeskView = 'feed' | 'pipeline' | 'connectors'

const HUB_UPLOAD_AGENT_CONTEXT: Partial<Record<HubWorkflowKind, string>> = {
  flash: 'Treat uploaded files as flash inventory. Preserve the authorized artist target and distinguish draft, enriched, and published state.',
  merch: 'Treat uploaded files as merch source assets. Preserve listing, price, inventory, and published state without inventing product data.',
  gallery: 'Treat uploaded files as live artist portfolio work. Verify persisted gallery order and the public artist projection after every change.',
}

function PageTitle({ className, ...props }: ComponentProps<typeof SharedPageTitle>) {
  return <SharedPageTitle {...props} className={['ryozan-page-title', className].filter(Boolean).join(' ')} />
}

interface GenesisProfile {
  focus: string
  cadence: string
  sprite: string
}

interface MobilePortalHomeProps {
  role: PortalRole
  bookings: Booking[]
  unreadCount: number
  loading: boolean
  loadError: boolean
}

interface PendingAgentUpload {
  name: string
  url: string
}

const terminalStatuses = new Set(['completed', 'cancelled', 'canceled', 'no_show', 'expired'])
const BOOKY_ACCENT = '#a33a31'
const BOOKY_ACCENT_RGB = '163,58,49'

function GalaStructuredReply({ response }: { response: GalaChatResponse }) {
  const toolCalls = response.tool_calls ?? []
  const components = response.components ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <span>{response.response || 'The assistant returned structured results without a text summary.'}</span>
      {response.model && <small style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-dim)' }}>Answered by {response.model}{response.model_provider ? ` on ${response.model_provider}` : ''}</small>}
      {components.map((component, index) => (
        <GalaComponentRenderer key={`${component.type}-${index}`} component={component} />
      ))}
      {toolCalls.length > 0 || response.memories_used > 0 ? (
        <details style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-dim)' }}>
          <summary>Assistant receipts</summary>
          {toolCalls.map((receipt, index) => {
            const toolName = typeof receipt.tool === 'string'
              ? receipt.tool
              : typeof receipt.name === 'string'
                ? receipt.name
                : `Tool ${index + 1}`
            const rawResult = receipt.result ?? receipt.status ?? 'Completed'
            const result = typeof rawResult === 'string' ? rawResult : JSON.stringify(rawResult)
            return <div key={`${toolName}-${index}`}>{toolName}: {result.slice(0, 300)}</div>
          })}
          {response.memories_used > 0 ? <div>{response.memories_used} scoped {response.memories_used === 1 ? 'memory' : 'memories'} used</div> : null}
        </details>
      ) : null}
    </div>
  )
}

const agentCardHands: Record<string, AgentActionCard[]> = {
  mercury: [
    { id: 'deal-card', name: 'Deal a card', description: 'Booking, quote, waiver, refs.', shell: 'courier-manifest' },
    { id: 'chase', name: 'Chase', description: 'One nudge on an unflipped card.', shell: 'intake-docket' },
    { id: 'memento', name: 'Memento', description: 'Write something into this agent’s memory by hand.', shell: 'memory-shard' },
  ],
  booky: [
    { id: 'estimate', name: 'Estimate', description: 'Sessions and length, placement first.', shell: 'receipt-tape' },
    { id: 'hold-slot', name: 'Hold a slot', description: '24 hours, nothing charged.', shell: 'chair-ticket' },
    { id: 'reflow', name: 'Reflow', description: 'Rebuild the day around a gap.', shell: 'courier-manifest' },
    { id: 'memento', name: 'Memento', description: 'Write something into this agent’s memory by hand.', shell: 'memory-shard' },
  ],
  gala: [
    { id: 'report-up', name: 'Report up', description: 'Pull today’s log into one ruling.', shell: 'heal-chart' },
    { id: 'write-ark', name: 'Write the record', description: 'File a durable shop record.', shell: 'memory-shard' },
    { id: 'memento', name: 'Memento', description: 'Write something into this agent’s memory by hand.', shell: 'memory-shard' },
  ],
  xar: [
    { id: 'artist-connections', name: 'Connections', description: 'Stripe, Google, Pinterest, and email.', shell: 'receipt-tape' },
    { id: 'price-flash', name: 'Price flash', description: 'Price published work from verified details.', shell: 'receipt-tape' },
    { id: 'publish-page', name: 'Publish a page', description: 'Prepare a flash or merch listing.', shell: 'courier-manifest' },
    { id: 'memento', name: 'Memento', description: 'Write something into this agent’s memory by hand.', shell: 'memory-shard' },
  ],
  herald: [
    { id: 'draft-issue', name: 'Draft an issue', description: 'Herald, waits on your read.', shell: 'intake-docket' },
    { id: 'member-note', name: 'Member note', description: 'Perk update to the Club.', shell: 'courier-manifest' },
    { id: 'memento', name: 'Memento', description: 'Write something into this agent’s memory by hand.', shell: 'memory-shard' },
  ],
  bella: [
    { id: 'answer', name: 'Answer', description: 'Reply in her voice, bot first.', shell: 'intake-docket' },
    { id: 'offer-consult', name: 'Offer a consult', description: 'Named artist, no number.', shell: 'chair-ticket' },
    { id: 'memento', name: 'Memento', description: 'Write something into this agent’s memory by hand.', shell: 'memory-shard' },
  ],
  smoku: [
    { id: 'prepare', name: 'Prepare', description: 'Grounded next step for an appointment.', shell: 'chair-ticket' },
    { id: 'aftercare', name: 'Aftercare', description: 'Care guidance tied to verified status.', shell: 'heal-chart' },
    { id: 'style-guide', name: 'Style guide', description: 'Tattoo style and culture guidance.', shell: 'intake-docket' },
    { id: 'memento', name: 'Memento', description: 'Write something into this agent’s memory by hand.', shell: 'memory-shard' },
  ],
  spyder: [
    { id: 'dispatch', name: 'Dispatch', description: 'Route an open ask to an agent.', shell: 'courier-manifest' },
    { id: 'hold-floor', name: 'Hold the floor', description: 'Freeze auto-dispatch.', shell: 'heal-chart' },
    { id: 'escalate', name: 'Escalate', description: 'Send it up to Gala.', shell: 'intake-docket' },
    { id: 'memento', name: 'Memento', description: 'Write something into this agent’s memory by hand.', shell: 'memory-shard' },
  ],
}

const sourceIcons = {
  home: ['M3 10.5 12 3l9 7.5', 'M5 9.5V21h14V9.5', 'M9.5 21v-6h5v6'],
  books: ['M8 2v4', 'M16 2v4', 'M3 9h18', 'M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z'],
  inbox: ['M4.5 5.5h8a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 3 19V7a1.5 1.5 0 0 1 1.5-1.5z', 'M15 4.2l3.6 1a1.5 1.5 0 0 1 1 1.8L17 17.5'],
  hub: ['M6 20V13', 'M12 20V4', 'M18 20v-9', 'M3.5 20h17'],
  club: ['M12 2l2.9 6.3 6.9.6-5.2 4.6 1.6 6.7L12 16.6 5.8 20.2l1.6-6.7L2.2 8.9l6.9-.6z'],
  profile: ['M20 21a8 8 0 1 0-16 0', 'M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z'],
  wall: ['M12 3v4', 'M12 17v4', 'M3 12h4', 'M17 12h4', 'M6.3 6.3l2.5 2.5', 'M15.2 15.2l2.5 2.5', 'M17.7 6.3l-2.5 2.5', 'M8.8 15.2l-2.5 2.5'],
} as const

type PortalSkeletonVariant = 'list' | 'thread' | 'profile' | 'quest' | 'feed' | 'panel'

function PortalContentSkeleton({ variant, rows = 3, label }: { variant: PortalSkeletonVariant; rows?: number; label: string }) {
  if (variant === 'profile') {
    return (
      <div data-portal-enter className="ryozan-portal-skeleton ryozan-portal-skeleton--profile" role="status" aria-label={label}>
        <div className="ryozan-portal-skeleton__profile-head">
          <Skeleton className="ryozan-portal-skeleton__avatar" />
          <Skeleton className="ryozan-portal-skeleton__title" />
          <Skeleton className="ryozan-portal-skeleton__pill" />
        </div>
        <Skeleton className="ryozan-portal-skeleton__line is-wide" />
        <Skeleton className="ryozan-portal-skeleton__line is-medium" />
        <div className="ryozan-portal-skeleton__chips">{[0, 1, 2].map((item) => <Skeleton key={item} />)}</div>
        <div className="ryozan-portal-skeleton__gallery">{[0, 1, 2, 3].map((item) => <Skeleton key={item} />)}</div>
        <span className="sr-only">{label}</span>
      </div>
    )
  }

  if (variant === 'panel') {
    return (
      <div data-portal-enter className="ryozan-portal-skeleton ryozan-portal-skeleton--panel" role="status" aria-label={label}>
        <Skeleton className="ryozan-portal-skeleton__title" />
        <Skeleton className="ryozan-portal-skeleton__line is-wide" />
        <Skeleton className="ryozan-portal-skeleton__line is-medium" />
        <Skeleton className="ryozan-portal-skeleton__panel-body" />
        <span className="sr-only">{label}</span>
      </div>
    )
  }

  return (
    <div data-portal-enter className={`ryozan-portal-skeleton ryozan-portal-skeleton--${variant}`} role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, index) => (
        <div className="ryozan-portal-skeleton__row" key={index}>
          {variant === 'thread' ? <Skeleton className="ryozan-portal-skeleton__dot" /> : null}
          <span>
            <Skeleton className="ryozan-portal-skeleton__line is-title" />
            <Skeleton className="ryozan-portal-skeleton__line" />
          </span>
          {variant === 'list' || variant === 'quest' ? <Skeleton className="ryozan-portal-skeleton__pill" /> : null}
        </div>
      ))}
      <span className="sr-only">{label}</span>
    </div>
  )
}

function SourceNavIcon({ paths }: { paths: readonly string[] }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths.map((path) => <path key={path} d={path} />)}
    </svg>
  )
}

function SpyderMark({ size = 24, strongBloom = false }: { size?: number; strongBloom?: boolean }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{
        color: 'var(--gold)',
        filter: strongBloom
          ? 'drop-shadow(0 0 4px currentColor) drop-shadow(0 0 10px currentColor)'
          : 'drop-shadow(0 0 4px rgba(217, 191, 149, .45))',
      }}
    >
      <ellipse cx="12" cy="14.6" rx="3.1" ry="4.1" />
      <circle cx="12" cy="9" r="2.1" />
      <path d="M10.1 11.2 6.4 8.4 4.1 9.6M9.1 13.6 5.1 12.9 3 14.9M9.1 16.4 5.4 17.4 4.1 20M10.4 18.6 8.6 21 9.1 22.6M13.9 11.2 17.6 8.4 19.9 9.6M14.9 13.6 18.9 12.9 21 14.9M14.9 16.4 18.6 17.4 19.9 20M13.6 18.6 15.4 21 14.9 22.6M10.7 7.6 9.9 5.9M13.3 7.6 14.1 5.9" />
    </svg>
  )
}

function MercuryMark() {
  const filterId = `ryozan-mercury-glow-${useId().replace(/:/g, '')}`
  const art = () => (
    <>
      <path d="M12 3.2V20.8" />
      <path d="M3.2 12H20.8" />
      <path d="M5.8 5.8 18.2 18.2" />
      <path d="M18.2 5.8 5.8 18.2" />
      <circle cx="12" cy="12" r="3.2" />
    </>
  )

  return (
    <span className="ryozan-mercury-mark" aria-hidden="true">
      <svg viewBox="-15 -15 54 54" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <defs>
          <filter id={filterId} x="-15" y="-15" width="54" height="54" filterUnits="userSpaceOnUse">
            <feGaussianBlur stdDeviation="2.6" />
          </filter>
        </defs>
        <g filter={`url(#${filterId})`} strokeWidth="3" opacity="0.5">{art()}</g>
        <g filter={`url(#${filterId})`} strokeWidth="1.5" opacity="0.85">{art()}</g>
        <g>{art()}</g>
      </svg>
    </span>
  )
}

function conversationTitle(conversation: Conversation) {
  return conversation.name
    || conversation.booking_context?.client_name
    || conversation.last_message?.sender_name
    || 'Conversation'
}

function conversationPreview(conversation: Conversation) {
  const inquiry = conversation.booking_context?.description?.trim()
  if (inquiry && inquiry !== '[redacted note]') return inquiry

  const message = conversation.last_message?.content?.trim()
  if (message && message !== '[redacted note]') return message

  return conversation.booking_context
    ? 'Open the booking inquiry for details'
    : 'Message preview unavailable in this local copy'
}

function MercuryConversationCard({ conversation, messages }: { conversation: Conversation; messages: ConversationMessage[] }) {
  const title = conversationTitle(conversation)
  const context = conversation.booking_context
  const [referencePreview, setReferencePreview] = useState<string | null>(null)
  const meta = context?.reference_code
    ? `${context.reference_code}${context.artist_name ? ` · ${context.artist_name}` : ''}`
    : conversation.channel_context?.channel || 'Courier thread'

  useEffect(() => {
    if (!referencePreview) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setReferencePreview(null)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [referencePreview])

  return (
    <>
      <section className={`ryozan-mercury-thread-card${context ? ' ryozan-mercury-thread-card--booky' : ''}`} aria-label={`${title} Courier thread`}>
        <header>
          <strong>{title}</strong>
          <small>{meta}</small>
        </header>
        {context ? (
          <CourierBookingInquiryCard
            booking={null}
            context={context}
            threadName={title}
            participantCount={conversation.participant_count}
            loading={false}
            onOpenImage={setReferencePreview}
          />
        ) : null}
        <div className="ryozan-mercury-thread-card__messages">
          {messages.filter((message) => message.message_type !== 'booking_intake').length ? messages.filter((message) => message.message_type !== 'booking_intake').map((message) => (
            <article key={message.id}>
              <header>
                <strong>{message.sender_name || 'Ryozan'}</strong>
                <time>{shortTime(message.created_at)}</time>
              </header>
              <p>{message.content || 'Attachment or action card'}</p>
            </article>
          )) : context ? null : <p className="ryozan-mercury-thread-card__empty">No messages are in this thread yet.</p>}
        </div>
      </section>
      {referencePreview ? createPortal(
        <div className="ryozan-mercury-reference-viewer" role="dialog" aria-modal="true" aria-label="Full-resolution booking reference" onClick={() => setReferencePreview(null)}>
          <button type="button" onClick={() => setReferencePreview(null)} aria-label="Close reference viewer">&times;</button>
          <div onClick={(event) => event.stopPropagation()}>
            <img src={referencePreview} alt="Full-resolution booking reference" />
          </div>
        </div>,
        document.body,
      ) : null}
    </>
  )
}

function HeraldMark() {
  return (
    <span className="ryozan-herald-mark" aria-hidden="true">
      <span />
    </span>
  )
}

function AccountAction({ onOpen, expanded }: { onOpen: () => void; expanded: boolean }) {
  return (
    <button
      type="button"
      className="ryozan-mobile-account"
      onClick={onOpen}
      aria-label="Profile and settings"
      aria-haspopup="menu"
      aria-expanded={expanded}
    >
      <img src="/assets/brand/ryozan-mark.svg" alt="" />
    </button>
  )
}

function PortalMotion({
  motionKey,
  exiting,
  onExitComplete,
  children,
}: {
  motionKey: string
  exiting: boolean
  onExitComplete: () => void
  children: ReactNode
}) {
  const scope = useRef<HTMLDivElement>(null)

  useGSAP(() => {
    const elements = Array.from(scope.current?.children ?? []).filter((element): element is HTMLElement => element instanceof HTMLElement)
    if (!elements.length) return
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reducedMotion) {
      gsap.set(elements, { clearProps: 'transform,opacity,visibility,willChange' })
      if (exiting) onExitComplete()
      return
    }

    if (exiting) {
      gsap.timeline({ defaults: { overwrite: 'auto' }, onComplete: onExitComplete })
        .set(elements, { willChange: 'transform,opacity' })
        .to([...elements].reverse(), {
          y: 34,
          rotation: (index) => index % 2 === 0 ? 0.7 : -0.7,
          autoAlpha: 0,
          duration: 0.26,
          ease: 'power2.in',
          stagger: 0.035,
        })
      return
    }

    gsap.timeline({ defaults: { overwrite: 'auto' } })
      .set(elements, { willChange: 'transform,opacity' })
      .fromTo(elements, {
        y: 56,
        autoAlpha: 0,
      }, {
        y: 0,
        autoAlpha: 1,
        duration: 0.725,
        delay: 0.055,
        ease: 'power3.out',
        stagger: 0.065,
      })
      .set(elements, { clearProps: 'transform,opacity,visibility,willChange' })
  }, { scope, dependencies: [motionKey, exiting, onExitComplete], revertOnUpdate: true })

  return <div ref={scope} className="ryozan-portal-page" aria-busy={exiting} style={exiting ? { pointerEvents: 'none' } : undefined}>{children}</div>
}

type PortalThresholdMode = 'enter' | 'exit'

function PortalThresholdTransition({ mode, onComplete }: { mode: PortalThresholdMode; onComplete: () => void }) {
  const scope = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLDivElement>(null)
  const mark = useRef<HTMLDivElement>(null)
  const rule = useRef<HTMLSpanElement>(null)
  const copy = useRef<HTMLDivElement>(null)
  const completed = useRef(false)

  useGSAP(() => {
    const finish = () => {
      if (completed.current) return
      completed.current = true
      onComplete()
    }
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reducedMotion) {
      gsap.set([field.current, mark.current, rule.current, copy.current], { autoAlpha: 1 })
      if (mode === 'exit') {
        gsap.delayedCall(0.7, finish)
      } else {
        gsap.to(scope.current, { autoAlpha: 0, duration: 0.24, delay: 0.65, onComplete: finish })
      }
      return
    }

    const ghosts = gsap.utils.toArray<HTMLElement>('.ryozan-portal-welcome-ghost', scope.current)
    const threshold = [mark.current, rule.current, copy.current]
    const timeline = gsap.timeline({ defaults: { overwrite: 'auto' }, onComplete: finish })

    if (mode === 'exit') {
      timeline
        .set(scope.current, { autoAlpha: 1 })
        .set(field.current, { autoAlpha: 0 })
        .set(threshold, { autoAlpha: 0 })
        .set(rule.current, { scaleX: 0, transformOrigin: '50% 50%' })
        .set(ghosts, { autoAlpha: 0 })
        .to(field.current, { autoAlpha: 1, duration: 0.42, ease: 'power2.inOut' }, 0)
        .to(rule.current, { scaleX: 1, autoAlpha: 1, duration: 0.52, ease: 'power3.out' }, 0.12)
        .fromTo(ghosts, {
          x: (index) => index === 0 ? -15 : 15,
          y: (index) => index === 0 ? 5 : -5,
          autoAlpha: 0,
        }, {
          x: 0,
          y: 0,
          autoAlpha: 0.38,
          duration: 0.48,
          ease: 'power3.out',
          stagger: 0.04,
        }, 0.18)
        .fromTo(mark.current, { scale: 0.86, autoAlpha: 0, filter: 'blur(5px)' }, {
          scale: 1,
          autoAlpha: 1,
          filter: 'blur(0px)',
          duration: 0.5,
          ease: 'power3.out',
        }, 0.28)
        .to(ghosts, { autoAlpha: 0, duration: 0.3, ease: 'power2.out' }, 0.48)
        .fromTo(copy.current, { y: 10, autoAlpha: 0 }, {
          y: 0,
          autoAlpha: 1,
          duration: 0.38,
          ease: 'power3.out',
        }, 0.5)
        .to(mark.current, {
          filter: 'drop-shadow(0 0 8px rgba(226,218,194,.32)) drop-shadow(0 0 26px rgba(188,151,88,.16))',
          duration: 0.42,
          repeat: 1,
          yoyo: true,
          ease: 'sine.inOut',
        }, 0.68)
        .to({}, { duration: 0.5 })
      return
    }

    timeline
      .set(scope.current, { autoAlpha: 1 })
      .set(field.current, { autoAlpha: 1 })
      .set(threshold, { autoAlpha: 0 })
      .set(rule.current, { scaleX: 0, transformOrigin: '50% 50%' })
      .set(ghosts, { autoAlpha: 0 })
      .to(rule.current, { scaleX: 1, autoAlpha: 1, duration: 0.52, ease: 'power2.inOut' }, 0.08)
      .fromTo(ghosts, {
        x: (index) => index === 0 ? -16 : 16,
        y: (index) => index === 0 ? 5 : -5,
        autoAlpha: 0,
      }, {
        x: 0,
        y: 0,
        autoAlpha: 0.4,
        duration: 0.58,
        ease: 'power2.out',
        stagger: 0.04,
      }, 0.12)
      .fromTo(mark.current, { scale: 0.82, autoAlpha: 0, filter: 'blur(6px)' }, {
        scale: 1,
        autoAlpha: 1,
        filter: 'blur(0px)',
        duration: 0.62,
        ease: 'power2.out',
      }, 0.2)
      .fromTo(copy.current, { y: 11, autoAlpha: 0 }, {
        y: 0,
        autoAlpha: 1,
        duration: 0.5,
        ease: 'power2.out',
      }, 0.42)
      .to(ghosts, { autoAlpha: 0, duration: 0.34, ease: 'power2.out' }, 0.72)
      .to(mark.current, {
        filter: 'drop-shadow(0 0 8px rgba(226,218,194,.32)) drop-shadow(0 0 26px rgba(188,151,88,.16))',
        duration: 0.42,
        repeat: 1,
        yoyo: true,
        ease: 'sine.inOut',
      }, 0.7)
      .to(copy.current, { y: -6, autoAlpha: 0, duration: 0.3, ease: 'power2.in' }, 1.55)
      .to(rule.current, { scaleX: 0.22, autoAlpha: 0, duration: 0.34, ease: 'power2.in' }, '<')
      .to(mark.current, { scale: 1.04, autoAlpha: 0, duration: 0.34, ease: 'power2.in' }, '<0.04')
      .to(field.current, { scale: 1.025, autoAlpha: 0, duration: 0.46, ease: 'power1.inOut' }, '<')
      .to(scope.current, { autoAlpha: 0, duration: 0.18, ease: 'power2.out' }, '-=0.16')
  }, { scope, dependencies: [mode, onComplete], revertOnUpdate: true })

  return (
    <div ref={scope} className={`ryozan-portal-welcome is-${mode}`} role="status" aria-label={mode === 'enter' ? 'Welcome to the Ryozan portal' : 'Leaving the Ryozan portal'}>
      <div ref={field} className="ryozan-portal-welcome-field" aria-hidden="true" />
      <div ref={mark} className="ryozan-portal-welcome-mark">
        <span className="ryozan-portal-welcome-halo" aria-hidden="true" />
        <img className="ryozan-portal-welcome-ghost is-left" src="/assets/brand/ryozan-mark.svg" alt="" aria-hidden="true" />
        <img className="ryozan-portal-welcome-ghost is-right" src="/assets/brand/ryozan-mark.svg" alt="" aria-hidden="true" />
        <img className="ryozan-portal-welcome-logo" src="/assets/brand/ryozan-mark.svg" alt="" />
      </div>
      <span ref={rule} className="ryozan-portal-welcome-rule" aria-hidden="true" />
      <div ref={copy} className="ryozan-portal-welcome-copy">
        <strong>{mode === 'enter' ? 'Welcome Stranger' : 'Goodbye Friend'}</strong>
      </div>
    </div>
  )
}

function localDateKey(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function bookingTimestamp(booking: Booking): number {
  const date = booking.scheduled_date ?? booking.preferred_date
  if (!date) return Number.MAX_SAFE_INTEGER
  const time = booking.scheduled_time ?? booking.preferred_time ?? '23:59'
  return new Date(`${date}T${time}`).getTime()
}

function formatTime(value: string | null): string {
  if (!value) return 'Time pending'
  const [hours = '0', minutes = '00'] = value.split(':')
  const hour = Number(hours)
  if (!Number.isFinite(hour)) return value
  const suffix = hour >= 12 ? 'P' : 'A'
  const clockHour = hour % 12 || 12
  return `${clockHour}:${minutes}${suffix}`
}

function formatWhen(booking: Booking): string {
  if (!booking.scheduled_date) {
    const requested = requestedBookingSlots(booking)
    return requested.length
      ? `Requested: ${requested.map((slot) => formatRequestedSlot(slot.date, slot.time)).join('; ')}`
      : 'Date pending'
  }
  const date = booking.scheduled_date ?? booking.preferred_date
  if (!date) return 'Date pending'
  const parsed = new Date(`${date}T12:00:00`)
  const day = date === localDateKey() ? 'Today' : parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  return `${day} · ${formatTime(booking.scheduled_time ?? booking.preferred_time)}`
}

function bookingTitle(booking: Booking): string {
  return booking.client_name ?? booking.resolved_name ?? booking.guest_name ?? 'Client'
}

function bookingSubject(booking: Booking): string {
  const description = booking.description?.trim() ?? ''
  if (import.meta.env.DEV && /local public inquiry validation.*do not contact/i.test(description)) {
    return 'Local first'
  }
  const subject = [booking.style_preference, booking.placement].filter(Boolean).join(' · ')
  return subject || description || 'Tattoo session'
}

function statusLabel(status: string): string {
  return status.replaceAll('_', ' ')
}

function statusColor(status: string): string {
  const normalized = status.toLowerCase()
  if (normalized === 'confirmed' || normalized === 'completed') return 'var(--sig-ok)'
  if (normalized.includes('deposit')) return 'var(--alt-sky)'
  if (normalized === 'cancelled' || normalized === 'canceled') return 'var(--sig-err)'
  return 'var(--gold)'
}

function shortDate(value: string): string {
  return new Date(value).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase()
}

function shortTime(value: string): string {
  return new Date(value).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

function monthCells(date: Date): Array<{ key: string; day: number; inMonth: boolean }> {
  const year = date.getFullYear()
  const month = date.getMonth()
  const first = new Date(year, month, 1)
  const start = new Date(year, month, 1 - first.getDay())
  return Array.from({ length: 42 }, (_, index) => {
    const cell = new Date(start)
    cell.setDate(start.getDate() + index)
    return {
      key: localDateKey(cell),
      day: cell.getDate(),
      inMonth: cell.getMonth() === month,
    }
  })
}

function DailyDebriefCard({ dateLabel, bookings }: { dateLabel: string; bookings: Booking[] }) {
  const activeBookings = bookings.filter((booking) => !terminalStatuses.has(booking.status.toLowerCase()))
  const paidDeposits = bookings
    .filter((booking) => booking.deposit_paid)
    .reduce((sum, booking) => sum + (booking.deposit_amount ?? 0), 0)
  const firstBooking = activeBookings[0]
  const summary = activeBookings.length
    ? `${activeBookings.length} ${activeBookings.length === 1 ? 'appointment' : 'appointments'} on the books. ${paidDeposits > 0 ? `$${paidDeposits.toLocaleString('en-US')} in paid deposits.` : 'No paid deposits recorded.'}`
    : 'No appointments are scheduled. Nothing is currently held or confirmed for this day.'

  return (
    <ReceiptBlock
      eyebrow="Daily debrief"
      headline={dateLabel}
      accent="var(--portal-agent-accent)"
      rows={[
        { label: 'First', value: firstBooking ? formatTime(firstBooking.scheduled_time) : 'Open' },
        { label: 'Appointments', value: activeBookings.length },
        { label: 'Deposits', value: `$${paidDeposits.toLocaleString('en-US')} paid`, valueColor: paidDeposits > 0 ? 'var(--sig-ok)' : undefined },
      ]}
      note={summary}
      style={{
        background: 'linear-gradient(160deg,rgba(var(--portal-agent-rgb),.11),rgba(26,27,26,.98) 56%)',
        boxShadow: '0 0 26px -8px rgba(var(--portal-agent-rgb),.34), 0 18px 38px -22px rgba(0,0,0,.9)',
      }}
    >
      {bookings.length ? (
        <div style={{ display: 'flex', flexDirection: 'column', borderTop: '1px solid rgba(var(--portal-agent-rgb),.24)' }}>
          {bookings.map((booking) => (
            <div key={booking.id} style={{ display: 'grid', gridTemplateColumns: '58px minmax(0,1fr) auto', gap: 8, alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontFamily: 'var(--font-btn)', fontSize: 9.5, letterSpacing: '.08em', color: 'var(--portal-agent-accent)' }}>{formatTime(booking.scheduled_time)}</span>
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'var(--font-body)', fontSize: 12, fontWeight: 700, color: 'var(--ink)' }}>{bookingTitle(booking)} · {bookingSubject(booking)}</span>
              <span style={{ fontFamily: 'var(--font-btn)', fontSize: 8.5, letterSpacing: '.08em', textTransform: 'uppercase', color: statusColor(booking.status) }}>{statusLabel(booking.status)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </ReceiptBlock>
  )
}

function WorkflowSteps({ labels, active, accent, onSelect }: { labels: string[]; active: number; accent: string; onSelect?: (index: number) => void }) {
  return (
    <div className="ryozan-hub-workflow-steps" aria-label="Workflow progress" role={onSelect ? 'tablist' : undefined}>
      {labels.map((label, index) => {
        const className = index === active ? 'active' : index < active ? 'complete' : ''
        const style = { '--workflow-accent': accent } as CSSProperties
        return onSelect ? (
          <button key={label} type="button" role="tab" aria-selected={index === active} className={className} style={style} onClick={() => onSelect(index)}>
            {label}
          </button>
        ) : (
          <span key={label} className={className} style={style}>{label}</span>
        )
      })}
    </div>
  )
}

function MerchWorkflowCard({ onOpenCrafts }: { onOpenCrafts: () => void }) {
  const [step, setStep] = useState(0)
  const [file, setFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [price, setPrice] = useState('')
  const [runSize, setRunSize] = useState('')

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  const chooseFile = (nextFile: File | undefined) => {
    if (!nextFile) return
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setFile(nextFile)
    setPreviewUrl(URL.createObjectURL(nextFile))
    setStep(1)
  }

  return (
    <section className="ryozan-hub-workflow-card ryozan-hub-workflow-card--merch" aria-label="Merch upload workflow">
      <header><span>Merch drop</span><strong>{step === 0 ? 'Upload the product' : step === 1 ? 'Edit the listing' : 'Enrich the details'}</strong></header>
      <WorkflowSteps labels={['Upload', 'Edit', 'Enrich']} active={step} accent="var(--accent-coral)" />
      {step === 0 ? (
        <label className="ryozan-hub-workflow-drop">
          <b>↑</b><strong>Shoot it or upload it</strong><span>A product photo or the artwork file.</span>
          <input type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" hidden onChange={(event) => chooseFile(event.target.files?.[0])} />
        </label>
      ) : null}
      {step === 1 ? (
        <div className="ryozan-hub-workflow-form">
          {previewUrl ? <img src={previewUrl} alt="Merch upload preview" /> : null}
          <div>
            <input aria-label="Product name" placeholder="Product name" value={name} onChange={(event) => setName(event.target.value)} />
            <input aria-label="Price" inputMode="decimal" placeholder="Price" value={price} onChange={(event) => setPrice(event.target.value)} />
            <input aria-label="Run size" inputMode="numeric" placeholder="Run size" value={runSize} onChange={(event) => setRunSize(event.target.value)} />
          </div>
          <button type="button" disabled={!name.trim() || !price.trim()} onClick={() => setStep(2)}>Enrich listing →</button>
        </div>
      ) : null}
      {step === 2 ? (
        <div className="ryozan-hub-workflow-summary">
          <strong>{name}</strong>
          <span>${price}{runSize ? ` · run of ${runSize}` : ''}</span>
          <p>This draft is staged locally. Crafts persistence is not connected yet, so nothing is published or charged.</p>
          <button type="button" onClick={onOpenCrafts}>Open Crafts inventory →</button>
        </div>
      ) : null}
      {file ? <small>{file.name}</small> : null}
    </section>
  )
}

function OptionsWorkflowCard({ onNavigate, isFounderOrAdmin, canManageConnections }: {
  onNavigate: (target: HubOptionTarget) => void
  isFounderOrAdmin: boolean
  canManageConnections: boolean
}) {
  const options = [
    { id: 'augmentz' as const, label: 'Augmentz', detail: isFounderOrAdmin ? 'Clover, Mercury + Plaid, Google Business, Drive / Dropbox.' : 'Founder and admin commerce rails.', available: isFounderOrAdmin },
    { id: 'connections' as const, label: 'Connections', detail: 'Stripe, Google, Pinterest, and Ryozan Email.', available: canManageConnections },
    { id: 'tools' as const, label: 'Tools', detail: 'Daily, sesh, booking, communication, socials, and gallery controls.', available: true },
    { id: 'email-foundry' as const, label: 'Email Foundry', detail: 'Templates, scheduling, merge fields, and governed outbox.', available: isFounderOrAdmin },
    { id: 'account' as const, label: 'Account', detail: 'Identity, access, and password settings.', available: true },
  ]

  return (
    <section className="ryozan-hub-workflow-card ryozan-hub-workflow-card--options" aria-label="Hub options workflow">
      <header><span>{isFounderOrAdmin ? 'Founder tools' : 'Your controls'}</span><strong>Choose where to work</strong></header>
      <div className="ryozan-hub-option-list">
        {options.map((option) => (
          <button key={option.id} type="button" disabled={!option.available} onClick={() => onNavigate(option.id)}>
            <strong>{option.label}</strong><span>{option.detail}</span>
          </button>
        ))}
      </div>
    </section>
  )
}

function FlashWorkflowCard({ artistId }: { artistId?: string }) {
  const [stage, setStage] = useState<FlashStage>('upload')
  const stages: Array<{ id: FlashStage; label: string }> = [
    { id: 'upload', label: 'Upload' },
    { id: 'enrich', label: 'Identify' },
    { id: 'publish', label: 'Publish' },
  ]

  return (
    <section className="ryozan-hub-workflow-card ryozan-hub-workflow-card--flash" aria-label="Flash upload workflow">
      <header className="ryozan-flash-stage-header" aria-label="Flash workflow stage">
        {stages.map((item) => (
          <span key={item.id} className={stage === item.id ? 'active' : ''} aria-current={stage === item.id ? 'step' : undefined}>
            {item.label}
          </span>
        ))}
      </header>
      <FlashManager artistId={artistId} compact mode="sheet-publish" onStageChange={setStage} />
    </section>
  )
}

function GalleryWorkflowCard({ initialProfile, onProfileChange }: {
  initialProfile: ArtistDetail
  onProfileChange: (profile: ArtistDetail) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [profile, setProfile] = useState(initialProfile)
  const [uploading, setUploading] = useState(false)
  const [removingUrl, setRemovingUrl] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ kind: 'idle' | 'success' | 'error'; text: string }>({
    kind: 'idle',
    text: 'Changes publish to your live artist page after verification.',
  })

  const applyProfile = (nextProfile: ArtistDetail) => {
    setProfile(nextProfile)
    onProfileChange(nextProfile)
  }

  const verifyPublicProjection = async (expectedProfile: ArtistDetail) => {
    const projectedProfile = await artistsApi.get(expectedProfile.slug)
    const expectedImages = expectedProfile.portfolio_images ?? []
    const projectedImages = projectedProfile.portfolio_images ?? []
    const matches = expectedImages.length === projectedImages.length
      && expectedImages.every((url, index) => url === projectedImages[index])
    if (!matches) throw new Error('The upload saved, but the live gallery has not refreshed yet. Try again in a moment.')
    applyProfile(projectedProfile)
    return projectedProfile
  }

  const reconcilePublicProfile = async (fallbackProfile: ArtistDetail) => {
    try {
      applyProfile(await artistsApi.get(fallbackProfile.slug))
    } catch {
      applyProfile(fallbackProfile)
    }
  }

  const uploadGalleryFiles = async (files: FileList | null) => {
    const pendingFiles = Array.from(files ?? [])
    if (!pendingFiles.length || uploading || removingUrl) return

    setUploading(true)
    setNotice({ kind: 'idle', text: `Uploading 0 of ${pendingFiles.length}` })
    let latestProfile = profile
    let uploadedCount = 0
    try {
      for (const file of pendingFiles) {
        latestProfile = await artistsApi.uploadGalleryImage(file)
        uploadedCount += 1
        applyProfile(latestProfile)
        setNotice({ kind: 'idle', text: `Uploading ${uploadedCount} of ${pendingFiles.length}` })
      }
      await verifyPublicProjection(latestProfile)
      setNotice({
        kind: 'success',
        text: `${uploadedCount} image${uploadedCount === 1 ? '' : 's'} uploaded and verified on the live page.`,
      })
    } catch (error: unknown) {
      await reconcilePublicProfile(latestProfile)
      const reason = error instanceof Error ? error.message : 'The gallery upload failed.'
      setNotice({
        kind: 'error',
        text: uploadedCount
          ? `${uploadedCount} of ${pendingFiles.length} uploaded. ${reason}`
          : reason,
      })
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const removeGalleryImage = async (url: string) => {
    if (uploading || removingUrl) return
    setRemovingUrl(url)
    setNotice({ kind: 'idle', text: 'Removing image from the live gallery.' })
    let latestProfile = profile
    try {
      latestProfile = await artistsApi.removeGalleryImage(url)
      applyProfile(latestProfile)
      await verifyPublicProjection(latestProfile)
      setNotice({ kind: 'success', text: 'Image removed and the live page is updated.' })
    } catch (error: unknown) {
      await reconcilePublicProfile(latestProfile)
      setNotice({
        kind: 'error',
        text: error instanceof Error ? error.message : 'The image could not be removed.',
      })
    } finally {
      setRemovingUrl(null)
    }
  }

  const images = profile.portfolio_images ?? []
  const working = uploading || removingUrl !== null

  return (
    <section className="ryozan-hub-workflow-card ryozan-hub-workflow-card--gallery" aria-label="Live gallery workflow">
      <header><span>Artist page</span><strong>Live gallery</strong></header>
      <label className={`ryozan-hub-workflow-drop${working ? ' is-disabled' : ''}`}>
        <b aria-hidden="true">↑</b>
        <strong>{uploading ? 'Uploading artwork' : 'Add portfolio images'}</strong>
        <span>JPG, PNG, WebP, or GIF. Select more than one if needed.</span>
        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif,.jpg,.jpeg,.png,.webp,.gif"
          multiple
          hidden
          disabled={working}
          onChange={(event) => void uploadGalleryFiles(event.target.files)}
        />
      </label>
      {images.length ? (
        <div className="ryozan-hub-gallery-manager" aria-label="Current live gallery">
          {images.map((url, index) => (
            <figure key={`${url}-${index}`}>
              <img src={url} alt={`${profile.name} portfolio work ${index + 1}`} />
              <button
                type="button"
                disabled={working}
                aria-label={`Remove portfolio image ${index + 1}`}
                onClick={() => void removeGalleryImage(url)}
              >
                {removingUrl === url ? 'Removing' : 'Remove'}
              </button>
            </figure>
          ))}
        </div>
      ) : <p className="ryozan-hub-gallery-manager-empty">Your live artist page has no gallery images yet.</p>}
      <div className={`ryozan-hub-gallery-notice is-${notice.kind}`} aria-live="polite">{notice.text}</div>
    </section>
  )
}

function CodexWorkflowCard() {
  return (
    <section className="ryozan-hub-workflow-card ryozan-hub-workflow-card--codex" aria-label="Codex document library workflow">
      <header><span>Document library</span><strong>Codex</strong></header>
      <div className="ryozan-mobile-codex ryozan-hub-workflow-codex">
        <Suspense fallback={<div className="ryozan-portal-empty-card">Loading Codex</div>}>
          <ScrollsPanel />
        </Suspense>
      </div>
    </section>
  )
}

const ARTIST_TOUR_STEPS = [
  { selector: '[data-tour="flash"]', eyebrow: '1 of 4', title: 'Publish flash sheets', body: 'Upload one full sheet. Xar lists the visible designs and publishes the sheet to your Flash Wall.' },
  { selector: '[data-tour="pos"]', eyebrow: '2 of 4', title: 'Take a payment', body: 'Open checkout for a card, QR, cash, saved collector, or invoice flow.' },
  { selector: '[data-tour="codex"]', eyebrow: '3 of 4', title: 'Find shop records', body: 'Codex holds your assigned Scroll, waivers, client records, and shop documents.' },
  { selector: '[data-tour="profile"]', eyebrow: '4 of 4', title: 'Keep your page current', body: 'Your bio, specialties, socials, gallery, and live artist page all start here.' },
] as const

function ArtistPortalTour({ onClose, onOpenScroll }: { onClose: () => void; onOpenScroll: () => void }) {
  const [step, setStep] = useState(0)
  const current = ARTIST_TOUR_STEPS[step]

  useEffect(() => {
    const target = document.querySelector<HTMLElement>(current.selector)
    target?.classList.add('ryozan-artist-tour-target')
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    return () => target?.classList.remove('ryozan-artist-tour-target')
  }, [current])

  return (
    <div className="ryozan-artist-tour-layer">
      <aside className="ryozan-artist-tour" role="dialog" aria-label="Artist portal walkthrough">
        <span>{current.eyebrow}</span>
        <strong>{current.title}</strong>
        <p>{current.body}</p>
        <div>
          <button type="button" onClick={onClose}>Skip</button>
          {step > 0 ? <button type="button" onClick={() => setStep(value => value - 1)}>Back</button> : null}
          {step < ARTIST_TOUR_STEPS.length - 1
            ? <button type="button" onClick={() => setStep(value => value + 1)}>Next</button>
            : <button type="button" onClick={onOpenScroll}>Preview Artist Scroll</button>}
        </div>
      </aside>
    </div>
  )
}

function MercuryThreadLoading({ title }: { title: string }) {
  return (
    <div className="ryozan-mercury-thread-loading" role="status" aria-live="polite">
      <MercuryMark />
      <em>Opening {title}</em>
    </div>
  )
}

type MercuryContact = Pick<User, 'id' | 'name' | 'email' | 'role' | 'avatar_url'>
type MercuryDraftChannel = 'courier' | 'social' | 'phone'

function MercuryRolodexCard({ contacts, onCompose }: { contacts: MercuryContact[]; onCompose: (contact: MercuryContact) => void }) {
  return (
    <section className="ryozan-mercury-action-card" aria-label="Courier Rolodex">
      <header><span>Rolodex</span><strong>Contacts</strong><small>{contacts.length} saved</small></header>
      <div className="ryozan-mercury-contact-list">
        {contacts.length ? contacts.map((contact) => (
          <button type="button" key={contact.id} onClick={() => onCompose(contact)}>
            <i>{contact.name?.trim().charAt(0) || '?'}</i>
            <span><strong>{contact.name || 'Unnamed contact'}</strong><small>{contact.email || contact.role}</small></span>
            <b>Compose</b>
          </button>
        )) : <p>No saved contacts are available yet.</p>}
      </div>
    </section>
  )
}

function MercuryComposeCard({ contacts, initialContact, onStart }: {
  contacts: MercuryContact[]
  initialContact?: MercuryContact
  onStart: (contact: MercuryContact, channel: MercuryDraftChannel) => void
}) {
  const [selectedId, setSelectedId] = useState(initialContact?.id ?? contacts[0]?.id ?? '')
  const [channel, setChannel] = useState<MercuryDraftChannel>('courier')
  const selected = contacts.find((contact) => contact.id === selectedId)

  return (
    <section className="ryozan-mercury-action-card" aria-label="Compose a Courier draft">
      <header><span>New draft</span><strong>Compose</strong><small>Taisō assisted</small></header>
      <div className="ryozan-mercury-compose-channels" role="group" aria-label="Delivery service">
        {(['courier', 'social', 'phone'] as const).map((option) => (
          <button
            type="button"
            className={channel === option ? 'active' : ''}
            key={option}
            disabled={option !== 'courier'}
            aria-disabled={option !== 'courier'}
            title={option === 'courier' ? 'Create a Courier draft' : `${option} delivery needs the studio's own ${option === 'social' ? 'Instagram, Facebook or WhatsApp' : 'SMS'} account`}
            onClick={() => option === 'courier' && setChannel(option)}
          >
            {option}
          </button>
        ))}
      </div>
      <p>Courier drafts stay inside Ryozan. Social and phone delivery switch on once a studio connects its own Instagram, Facebook, WhatsApp or SMS account in Settings; this demo has none connected.</p>
      <label className="ryozan-mercury-compose-contact">
        <span>Contact</span>
        <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
          {contacts.map((contact) => <option value={contact.id} key={contact.id}>{contact.name || contact.email}</option>)}
        </select>
      </label>
      {!contacts.length && <p>No contacts yet. Conversations you start with the studio show up here.</p>}
      <Button className="ryozan-mercury-compose-start" variant="outline" fullWidth disabled={!selected} onClick={() => selected && onStart(selected, channel)}>
        Start draft
      </Button>
    </section>
  )
}

function ArtistScrollWorkflowCard({ artistName, preview = false }: { artistName: string; preview?: boolean }) {
  return (
    <section className="ryozan-spyder-artist-scroll-card" aria-label={`${artistName} Artist Scroll`}>
      <header className="ryozan-spyder-artist-scroll-card__intro">
        <span>Assigned by Ryozan</span>
        <strong>Complete your Artist Scroll</strong>
        <p>{preview ? 'Founder preview. Work through the exact artist experience without saving a submission.' : 'This is one continuous record bound to your account. Fill and sign it yourself, or ask Spyder below for guidance. Spyder cannot accept terms, sign, or submit on your behalf.'}</p>
      </header>
      <Suspense fallback={<PortalContentSkeleton variant="panel" label="Loading Artist Scroll" />}>
        <ArtistScroll artistName={artistName} layout="continuous" preview={preview} />
      </Suspense>
    </section>
  )
}

function POSWorkflowCard({ onClose, onOpenConnections }: { onClose: () => void; onOpenConnections?: () => void }) {
  return (
    <section className="ryozan-hub-workflow-card ryozan-hub-workflow-card--pos" aria-label="Point of sale workflow">
      {onOpenConnections ? (
        <button type="button" className="ryozan-pos-connections-entry" onClick={onOpenConnections}>
          <span><strong>Stripe setup</strong><small>Connection and payout readiness</small></span>
          <b>Open Connections</b>
        </button>
      ) : null}
      <Suspense fallback={<PortalContentSkeleton variant="panel" label="Loading point of sale" />}>
        <POSModal open embedded onClose={onClose} />
      </Suspense>
    </section>
  )
}

function PortalCalendar({ bookings, nowMs, selectedDate, cueDate = null, onSelectDate, label = 'Appointment calendar' }: {
  bookings: Booking[]
  nowMs: number
  selectedDate: string | null
  cueDate?: string | null
  onSelectDate: (dateKey: string, calendar: HTMLDivElement) => void
  label?: string
}) {
  const calendarRef = useRef<HTMLDivElement>(null)
  const currentDate = new Date(nowMs)
  const scheduledDates = new Set(bookings.map((booking) => booking.scheduled_date ?? booking.preferred_date).filter(Boolean))

  return (
    <div ref={calendarRef} data-portal-calendar data-portal-enter className="ryozan-books-calendar" aria-label={label}>
      <strong>{currentDate.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }).toUpperCase()}</strong>
      <div className="ryozan-calendar-rule" />
      <div className="ryozan-calendar-grid ryozan-calendar-weekdays">
        {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}
      </div>
      <div className="ryozan-calendar-grid">
        {monthCells(currentDate).map((cell) => (
          <button
            type="button"
            key={cell.key}
            data-date={cell.key}
            className={`${cell.inMonth ? '' : 'outside'}${cell.key === localDateKey(currentDate) ? ' today' : ''}${cell.key === selectedDate ? ' selected' : ''}${cell.key === cueDate ? ' schedule-cue' : ''}`}
            onClick={() => calendarRef.current && onSelectDate(cell.key, calendarRef.current)}
            aria-label={`Select ${cell.key}`}
            aria-pressed={cell.key === selectedDate}
          >
            {cell.day}
            {scheduledDates.has(cell.key) ? <i /> : null}
          </button>
        ))}
      </div>
    </div>
  )
}

function CollectorHuddlPage({ bookings, nowMs, selectedDate, pass, onSelectDate, onBook, headerAction }: {
  bookings: Booking[]
  nowMs: number
  selectedDate: string
  pass: ComponentProps<typeof SessionPass> | null
  onSelectDate: (dateKey: string, calendar: HTMLDivElement) => void
  onBook: () => void
  headerAction: ReactNode
}) {
  const selectedLabel = new Date(`${selectedDate}T12:00:00`).toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })

  return (
    <>
      <div data-portal-enter>
        <PageTitle action={headerAction}>Huddl</PageTitle>
      </div>
      <PortalCalendar
        bookings={bookings}
        nowMs={nowMs}
        selectedDate={selectedDate}
        onSelectDate={onSelectDate}
        label="Choose an appointment date"
      />
      {pass ? (
        <div data-portal-enter className="ryozan-huddl-pass">
          <SessionPass {...pass} brand={BRAND.product.toUpperCase()} />
        </div>
      ) : (
        <section data-portal-enter className="ryozan-huddl-booking-prompt" aria-live="polite">
          <span>{selectedLabel}</span>
          <strong>No session pass for this date.</strong>
          <p>Want to book your next tattoo?</p>
          <button type="button" onClick={onBook}>Start booking →</button>
        </section>
      )}
    </>
  )
}

function BooksPage({ bookings, loading, loadError, nowMs, selectedDate, cueDate, onOpenDay, onOpenBooking, onOpenBooks, headerAction }: {
  bookings: Booking[]
  loading: boolean
  loadError: boolean
  nowMs: number
  selectedDate: string | null
  cueDate: string | null
  onOpenDay: (dateKey: string, calendar: HTMLDivElement) => void
  onOpenBooking: (booking: Booking, trigger: HTMLButtonElement) => void
  onOpenBooks: () => void
  headerAction: ReactNode
}) {
  const queue = bookings
    .filter((booking) => !terminalStatuses.has(booking.status.toLowerCase()))
    .filter((booking) => bookingTimestamp(booking) >= nowMs || !booking.scheduled_date)
    .sort((a, b) => bookingTimestamp(a) - bookingTimestamp(b))
    .slice(0, 3)

  return (
    <section className="ryozan-books-page">
      <PageTitle action={headerAction}>The Books</PageTitle>
      <PortalCalendar bookings={bookings} nowMs={nowMs} selectedDate={selectedDate} cueDate={cueDate} onSelectDate={onOpenDay} label="Shop booking calendar" />
      <SectionLabel>Booking inquiries</SectionLabel>
      <div data-portal-enter className="ryozan-portal-list">
        {loading ? <PortalContentSkeleton variant="list" label="Loading live bookings" /> : null}
        {loadError ? <button type="button" className="ryozan-portal-empty" onClick={() => window.location.reload()}>Bookings unavailable. Retry</button> : null}
        {!loading && !loadError && queue.length === 0 ? <button type="button" className="ryozan-portal-empty" onClick={onOpenBooks}>No active booking inquiries. Open books</button> : null}
        {!loading && !loadError ? queue.map((booking) => (
          <button
            type="button"
            className="ryozan-book-row"
            key={booking.id}
            data-booking-id={booking.id}
            aria-label={`Open ${bookingTitle(booking)} with Booky`}
            onClick={(event) => onOpenBooking(booking, event.currentTarget)}
          >
            <span className="ryozan-book-row__details">
              <strong>{bookingTitle(booking)}</strong>
              <small>{bookingSubject(booking)} · {formatWhen(booking)}</small>
            </span>
            <span className="ryozan-book-row__action" aria-hidden="true">Open</span>
          </button>
        )) : null}
      </div>
    </section>
  )
}

function CourierCommandbar({ query, onQueryChange, onOpenRolodex, onCompose, actionsAvailable }: {
  query: string
  onQueryChange: (query: string) => void
  onOpenRolodex: () => void
  onCompose: () => void
  actionsAvailable: boolean
}) {
  return (
    <div data-portal-enter className="ryozan-courier-commandbar">
      <label>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6" /><path d="m16 16 4 4" /></svg>
        <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search Courier" aria-label="Search Courier threads" />
        {query ? <button type="button" onClick={() => onQueryChange('')} aria-label="Clear search">×</button> : null}
      </label>
      <IconButton className="ryozan-courier-commandbar__action" label={actionsAvailable ? 'Open Rolodex' : 'Contact discovery unavailable for assistants'} onClick={onOpenRolodex} disabled={!actionsAvailable} style={!actionsAvailable ? { cursor: 'not-allowed', opacity: .45 } : undefined}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M19 8v6M22 11h-6" /></svg>
      </IconButton>
      <IconButton className="ryozan-courier-commandbar__action" label={actionsAvailable ? 'Compose new message' : 'New contact conversations unavailable for assistants'} onClick={onCompose} disabled={!actionsAvailable} style={!actionsAvailable ? { cursor: 'not-allowed', opacity: .45 } : undefined}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z" /></svg>
      </IconButton>
    </div>
  )
}

function InboxPage({ conversations, state, query, onOpenConversation, onDeleteConversation, onCreateMemory, canCreateMemory }: {
  conversations: Conversation[]
  state: 'idle' | 'loading' | 'ready' | 'failed'
  query: string
  onOpenConversation: (conversation: Conversation, trigger: HTMLButtonElement) => void
  onDeleteConversation: (conversation: Conversation) => void
  onCreateMemory: (conversation: Conversation) => void
  canCreateMemory: boolean
}) {
  const filteredConversations = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    if (!normalized) return conversations
    return conversations.filter((conversation) => [
      conversationTitle(conversation),
      conversationPreview(conversation),
      conversation.booking_context?.reference_code,
      conversation.booking_context?.artist_name,
    ].filter(Boolean).some((value) => String(value).toLowerCase().includes(normalized)))
  }, [conversations, query])

  // Chronological, the way a messages app behaves. This used to sort unread to
  // the top, so opening a thread made it jump down the list under the one you
  // had just been reading. Read state is carried by the row's own styling
  // instead: `.unread` keeps the accent border and the lit dot, `.read` drops
  // both. Position never moves.
  const orderedConversations = filteredConversations

  return (
    <>
      {state === 'loading' ? <PortalContentSkeleton variant="thread" rows={6} label="Loading live conversations" /> : null}
      {state === 'failed' ? <div data-portal-enter className="ryozan-portal-empty-card">Courier is unavailable right now</div> : null}
      {state === 'ready' && conversations.length === 0 ? <div data-portal-enter className="ryozan-portal-empty-card">No conversations are waiting</div> : null}
      {state === 'ready' && conversations.length > 0 && filteredConversations.length === 0 ? <div data-portal-enter className="ryozan-portal-empty-card">No Courier threads match that search</div> : null}
      {state === 'ready' ? orderedConversations.map((conversation) => {
        const name = conversationTitle(conversation)
        return (
          <article data-portal-enter className={`ryozan-thread-row${conversation.is_unread ? ' unread' : ' read'}`} key={conversation.id}>
            <button type="button" className="ryozan-thread-row__open" onClick={(event) => onOpenConversation(conversation, event.currentTarget)}>
              <i />
              <span>
                <strong>{name}</strong>
                <small>{conversationPreview(conversation)}</small>
              </span>
              <time>{shortTime(conversation.updated_at)}</time>
            </button>
            <span className="ryozan-thread-row__actions">
              {canCreateMemory ? (
                <IconButton label={`Create memory from ${name}`} onClick={() => onCreateMemory(conversation)}>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 8.5 8.5 3 12l5.5 3.5L12 21l3.5-5.5L21 12l-5.5-3.5Z" /><path d="M12 8v8M8 12h8" /></svg>
                </IconButton>
              ) : null}
              <IconButton label={`Delete ${name}`} onClick={() => onDeleteConversation(conversation)}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5" /></svg>
              </IconButton>
            </span>
          </article>
        )
      }) : null}
    </>
  )
}

function conversationThreadMeta(conversation: Conversation) {
  const context = conversation.booking_context
  if (context?.reference_code) {
    return `${context.reference_code}${context.artist_name ? ` · ${context.artist_name}` : ''}`
  }
  return conversation.channel_context?.channel || 'Courier thread'
}

/**
 * The client-side thread screen. Collectors reach this from the INBOX list
 * instead of the Mercury agent panel: staff read a thread to work it, a client
 * reads a thread to answer it, so this surface is a transcript and a
 * composer and nothing else. No rolodex, no broadcast, no templates, no
 * reply-draft assist — those belong to the staff console.
 */
function CollectorThreadPage({
  conversation,
  messages,
  state,
  currentUserId,
  draft,
  sending,
  sendError,
  onDraftChange,
  onSend,
  onRetry,
}: {
  conversation: Conversation
  messages: ConversationMessage[]
  state: 'loading' | 'ready' | 'failed'
  currentUserId: string | undefined
  draft: string
  sending: boolean
  sendError: string | null
  onDraftChange: (value: string) => void
  onSend: () => void
  onRetry: () => void
}) {
  const title = conversationTitle(conversation)
  const context = conversation.booking_context
  const [referencePreview, setReferencePreview] = useState<string | null>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)

  // `booking_intake` is the machine record behind the inquiry card above the
  // transcript. Showing it again as a message would read as a duplicate.
  const visibleMessages = useMemo(
    () => messages.filter((message) => message.message_type !== 'booking_intake'),
    [messages],
  )

  useEffect(() => {
    if (!referencePreview) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setReferencePreview(null)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [referencePreview])

  // Rest on the newest message, the way a messages app opens.
  useEffect(() => {
    const transcript = transcriptRef.current
    if (!transcript) return
    transcript.scrollTo({
      top: transcript.scrollHeight,
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    })
  }, [state, visibleMessages.length])

  // Grow with the reply instead of hiding it behind a one-line window.
  useEffect(() => {
    const field = composerRef.current
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${field.scrollHeight}px`
  }, [draft])

  const canSend = Boolean(draft.trim()) && !sending && state !== 'failed'

  return (
    <section className="ryozan-collector-thread" aria-label={`${title} conversation`}>
      <div className="ryozan-collector-thread__transcript" ref={transcriptRef}>
        {context ? (
          <CourierBookingInquiryCard
            booking={null}
            context={context}
            threadName={title}
            participantCount={conversation.participant_count}
            loading={false}
            onOpenImage={setReferencePreview}
          />
        ) : null}

        {state === 'loading' ? <PortalContentSkeleton variant="thread" rows={4} label={`Loading ${title}`} /> : null}

        {state === 'failed' ? (
          <button type="button" className="ryozan-portal-empty-card ryozan-collector-thread__retry" onClick={onRetry}>
            This conversation could not be loaded · retry
          </button>
        ) : null}

        {state === 'ready' && !visibleMessages.length ? (
          <p className="ryozan-collector-thread__empty">
            No messages here yet. Yours will be the first.
          </p>
        ) : null}

        {state === 'ready' ? visibleMessages.map((message) => {
          const mine = Boolean(currentUserId) && message.sender_id === currentUserId
          return (
            <article key={message.id} className={`ryozan-collector-message${mine ? ' is-mine' : ''}`}>
              <header>
                <strong>{mine ? 'You' : message.sender_name || 'Ryozan'}</strong>
                <time dateTime={message.created_at}>{shortTime(message.created_at)}</time>
              </header>
              <p>{message.content || 'Attachment or action card'}</p>
            </article>
          )
        }) : null}
      </div>

      <form
        className="ryozan-collector-thread__composer"
        onSubmit={(event) => {
          event.preventDefault()
          onSend()
        }}
      >
        {sendError ? <p className="ryozan-collector-thread__error" role="alert">{sendError}</p> : null}
        <div>
          <textarea
            ref={composerRef}
            aria-label={`Reply to ${title}`}
            placeholder="Write a reply"
            rows={1}
            value={draft}
            disabled={state === 'failed'}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                onSend()
              }
            }}
          />
          <button type="submit" disabled={!canSend}>{sending ? 'Sending' : 'Send'}</button>
        </div>
      </form>

      {referencePreview ? createPortal(
        <div className="ryozan-mercury-reference-viewer" role="dialog" aria-modal="true" aria-label="Full-resolution booking reference" onClick={() => setReferencePreview(null)}>
          <button type="button" onClick={() => setReferencePreview(null)} aria-label="Close reference viewer">&times;</button>
          <div onClick={(event) => event.stopPropagation()}>
            <img src={referencePreview} alt="Full-resolution booking reference" />
          </div>
        </div>,
        document.body,
      ) : null}
    </section>
  )
}

function HubPage({ profile, state, onRetry, onOpenFlash, canUploadFlash, onOpenMerch, onOpenConnections, canManageConnections, onOpenDeposits, canViewDeposits, onOpenPos, onOpenOptions, onOpenCodex, onOpenAgentEvolution, canManageAgents, onEditProfile, onOpenBio, onOpenSpecialties, onOpenSocials, onOpenGallery, headerAction }: {
  profile: ArtistDetail | null
  state: 'idle' | 'loading' | 'ready' | 'failed'
  onRetry: () => void
  onOpenFlash: (anchor: HTMLButtonElement) => void
  canUploadFlash: boolean
  onOpenMerch: (anchor: HTMLButtonElement) => void
  onOpenConnections: (anchor: HTMLButtonElement) => void
  canManageConnections: boolean
  onOpenDeposits: (anchor: HTMLButtonElement) => void
  canViewDeposits: boolean
  onOpenPos: (anchor: HTMLButtonElement) => void
  onOpenOptions: (anchor: HTMLButtonElement) => void
  onOpenCodex: (anchor: HTMLButtonElement) => void
  onOpenAgentEvolution: () => void
  canManageAgents: boolean
  onEditProfile: () => void
  onOpenBio: () => void
  onOpenSpecialties: () => void
  onOpenSocials: () => void
  onOpenGallery: (anchor: HTMLButtonElement) => void
  headerAction: ReactNode
}) {
  const [copied, setCopied] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const uploadMenuId = useId()
  const socials = profile ? [
    profile.instagram_url ? { tag: 'IG', label: 'Instagram' } : null,
    profile.facebook_url ? { tag: 'FB', label: 'Facebook' } : null,
    profile.tiktok_url ? { tag: 'TT', label: 'TikTok' } : null,
    profile.website_url ? { tag: 'WEB', label: 'Website' } : null,
  ].filter((social): social is { tag: string; label: string } => social !== null) : []

  const copyPageLink = () => {
    if (!profile) return
    navigator.clipboard.writeText(`${window.location.origin}/artist/${profile.slug}`)
      .then(() => setCopied(true))
      .catch(() => setCopied(false))
  }

  return (
    <>
      <PageTitle action={headerAction}>Hub</PageTitle>
      <div className="ryozan-hub-actions">
        <div data-portal-enter className={`ryozan-membership-stack ryozan-hub-upload-stack${uploadOpen ? ' is-open' : ''}`}>
          <button
            data-tour="flash"
            type="button"
            className="ryozan-hub-upload-toggle"
            aria-expanded={uploadOpen}
            aria-controls={uploadMenuId}
            onClick={() => setUploadOpen((current) => !current)}
          >
            <span><small>Publish</small><strong>+ Upload</strong></span>
            <b>Flash · Merch · Gallery</b>
            <svg className="ryozan-hub-upload-chevron" width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <path d="m4.5 7 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <div id={uploadMenuId} className="ryozan-club-actions ryozan-hub-upload-menu" aria-label="Choose an upload workflow" aria-hidden={!uploadOpen}>
            <div className="ryozan-club-actions-inner">
              {canUploadFlash ? <button type="button" tabIndex={uploadOpen ? 0 : -1} onClick={(event) => { setUploadOpen(false); onOpenFlash(event.currentTarget) }}><strong>Flash</strong><span>Identify · Price · Publish</span></button> : null}
              <button type="button" tabIndex={uploadOpen ? 0 : -1} onClick={(event) => { setUploadOpen(false); onOpenMerch(event.currentTarget) }}><strong>Merch</strong><span>Product · Listing · Inventory</span></button>
              <button type="button" tabIndex={uploadOpen ? 0 : -1} onClick={(event) => { setUploadOpen(false); onOpenGallery(event.currentTarget) }}><strong>Gallery</strong><span>Upload · Verify live page</span></button>
            </div>
          </div>
        </div>
        {canManageConnections ? (
          <button data-portal-enter data-tour="connections" type="button" className="ryozan-hub-codex ryozan-hub-codex--connections" onClick={(event) => onOpenConnections(event.currentTarget)}>
            <strong>+Connections</strong>
            <span>Stripe · Google · Pinterest · Email</span>
          </button>
        ) : null}
        {canViewDeposits ? (
          <button data-portal-enter type="button" className="ryozan-hub-codex ryozan-hub-codex--deposits" onClick={(event) => onOpenDeposits(event.currentTarget)}>
            <strong>+Deposits</strong>
            <span>Paid · Due · Booking history</span>
          </button>
        ) : null}
        <button data-portal-enter data-tour="pos" type="button" className="ryozan-hub-pos" onClick={(event) => onOpenPos(event.currentTarget)}>
          <strong>+POS</strong>
          <span>Stripe · QR · Card or cash</span>
        </button>
        <button data-portal-enter data-tour="codex" type="button" className="ryozan-hub-codex" onClick={(event) => onOpenCodex(event.currentTarget)}>
          <strong>+Codex</strong>
          <span>Documents · Waivers · Scrolls</span>
        </button>
        {canManageAgents ? (
          <button data-portal-enter type="button" className="ryozan-hub-codex ryozan-hub-codex--agents" onClick={onOpenAgentEvolution}>
            <strong>+Agents</strong>
            <span>Bella · Gala · Phone behavior</span>
          </button>
        ) : null}
      </div>

      {state === 'loading' || state === 'idle' ? <PortalContentSkeleton variant="profile" label="Loading your live artist page" /> : null}
      {state === 'failed' ? (
        <button data-portal-enter type="button" className="ryozan-portal-empty-card ryozan-hub-retry" onClick={onRetry}>
          Artist profile unavailable · retry
        </button>
      ) : null}
      {state === 'ready' && profile ? (
        <>
          <section data-portal-enter data-tour="profile" className="ryozan-hub-profile-card">
            <div className="ryozan-hub-profile-main">
              <div className="ryozan-hub-profile-heading">
                <button type="button" className="ryozan-hub-avatar" onClick={onEditProfile} aria-label="Edit profile icon">
                  <img src={profile.avatar_url || '/assets/brand/ryozan-mark.svg'} alt="" />
                  <i aria-hidden="true">✎</i>
                </button>
                <h2>{profile.name}</h2>
                <button type="button" className="ryozan-hub-copy" onClick={copyPageLink}>{copied ? 'Link copied' : 'Copy link'}</button>
              </div>
              <button type="button" className="ryozan-hub-bio" onClick={onOpenBio}>
                <span>{profile.bio || 'Add a bio to introduce your work and approach.'}</span>
                <strong>+ Edit bio</strong>
              </button>
              <div className="ryozan-hub-specialties">
                {(profile.specialty || []).map((specialty) => <span key={specialty}>{specialty}</span>)}
                <button type="button" onClick={onOpenSpecialties}>+ Edit</button>
              </div>
            </div>
            <div className="ryozan-hub-profile-contact">
              <button type="button" className="ryozan-hub-socials" onClick={onOpenSocials}>
                {socials.map((social) => <i key={social.label} aria-label={social.label}>{social.tag}</i>)}
                <span>+ Socials</span>
              </button>
            </div>
          </section>
          <div data-portal-enter className="ryozan-hub-profile-actions">
            <button type="button" className="ryozan-hub-profile-options" onClick={(event) => onOpenOptions(event.currentTarget)}>+ Options</button>
          </div>
        </>
      ) : null}
    </>
  )
}

function HubUnavailablePage({ headerAction }: { headerAction: ReactNode }) {
  return (
    <>
      <PageTitle action={headerAction}>Hub</PageTitle>
      <div data-portal-enter className="ryozan-portal-empty-card">
        Hub publishing is available to artist, admin, and founder accounts. Books and Courier remain available for assistant work.
      </div>
    </>
  )
}

function CodexPage({ onBack, headerAction }: { onBack: () => void; headerAction: ReactNode }) {
  return (
    <>
      <PageTitle action={headerAction}>Codex</PageTitle>
      <button data-portal-enter type="button" className="ryozan-codex-back" onClick={onBack}>
        <span aria-hidden="true">←</span> Hub
      </button>
      <section data-portal-enter className="ryozan-mobile-codex" aria-label="Codex document library">
        <Suspense fallback={<PortalContentSkeleton variant="panel" label="Loading Codex" />}>
          <ScrollsPanel />
        </Suspense>
      </section>
    </>
  )
}

function ClubLootCard({ quests, onClaimQuest }: { quests: QuestWithProgress[]; onClaimQuest: (questId: string) => Promise<void> }) {
  const [claimingQuest, setClaimingQuest] = useState<string | null>(null)
  const [claimError, setClaimError] = useState(false)
  const [inventory, setInventory] = useState<RewardInventory | null>(null)
  const [inventoryState, setInventoryState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [selectedLootSlot, setSelectedLootSlot] = useState(0)
  const [playingOffer, setPlayingOffer] = useState<string | null>(null)
  const [offerWorking, setOfferWorking] = useState(false)
  const claimableQuests = quests.filter((item) => item.completed && !item.claimed)
  const claimedQuests = quests.filter((item) => item.claimed)
  const activeOffer = inventory?.offers.find((offer) => offer.id === playingOffer)
  const lootGrants = inventory?.grants.filter((grant) => grant.unlock_kind) ?? []
  const lootSlots = Array.from({ length: Math.max(8, Math.ceil(lootGrants.length / 4) * 4) }, (_, index) => lootGrants[index] ?? null)
  const selectedLoot = lootSlots[selectedLootSlot] ?? null
  const selectedLootName = selectedLoot?.unlock_slug
    ? selectedLoot.unlock_slug.split(/[-_]/).filter(Boolean).map((part) => `${part[0]?.toUpperCase() ?? ''}${part.slice(1)}`).join(' ')
    : selectedLoot?.unlock_kind === 'loot_pack'
      ? 'Loot Pack'
      : selectedLoot?.unlock_kind?.replace('_', ' ') ?? 'Empty slot'
  const selectedLootGlyph = selectedLoot?.unlock_kind === 'loot_pack'
    ? '▣'
    : selectedLoot?.unlock_kind === 'art'
      ? '✦'
      : selectedLoot?.unlock_kind === 'digital_merch'
        ? '◆'
        : '◇'

  const loadInventory = useCallback(() => {
    setInventoryState('loading')
    rewardsApi.inventory()
      .then((nextInventory) => {
        setInventory(nextInventory)
        setInventoryState('ready')
      })
      .catch(() => {
        setInventory(null)
        setInventoryState('failed')
      })
  }, [])

  useEffect(() => {
    loadInventory()
  }, [loadInventory])

  const completeMiniGame = async () => {
    if (!activeOffer) return
    setOfferWorking(true)
    setClaimError(false)
    try {
      await rewardsApi.completeOffer(activeOffer.id)
      setPlayingOffer(null)
      loadInventory()
    } catch {
      setClaimError(true)
    } finally {
      setOfferWorking(false)
    }
  }

  const claimQuest = async (questId: string) => {
    setClaimingQuest(questId)
    setClaimError(false)
    try {
      await onClaimQuest(questId)
    } catch {
      setClaimError(true)
    } finally {
      setClaimingQuest(null)
    }
  }

  return (
    <section className="ryozan-club-tool ryozan-club-tool--loot" aria-label="Loot inventory">
      <header><span>Loot</span><strong>Your inventory</strong></header>
      <div className="ryozan-loot-summary">
        <span><b>{claimableQuests.length}</b> claimable</span>
        <span><b>{inventoryState === 'failed' ? '—' : inventory?.total_unlocks ?? claimedQuests.length}</b> collected</span>
      </div>
      <div className="ryozan-loot-cabinet">
        <article className={`ryozan-loot-preview${selectedLoot ? ' has-item' : ''}`} aria-live="polite">
          <span>Selected item</span>
          <div aria-hidden="true"><i>{selectedLootGlyph}</i></div>
          <strong>{inventoryState === 'failed' ? 'Inventory unavailable' : inventoryState === 'loading' ? 'Checking inventory' : selectedLootName}</strong>
          <small>{inventoryState === 'failed'
            ? 'Reward records could not be loaded.'
            : inventoryState === 'loading'
              ? 'Loading owned items.'
              : selectedLoot
                ? selectedLoot.unlock_kind?.replace('_', ' ')
                : `Slot ${selectedLootSlot + 1} · empty`}</small>
        </article>
        <div className="ryozan-loot-slot-bank">
          <header><strong>Item slots</strong><span>{lootGrants.length}/{lootSlots.length}</span></header>
          <div className="ryozan-loot-slots" aria-label="Inventory item slots">
            {lootSlots.map((loot, index) => {
              const glyph = loot?.unlock_kind === 'loot_pack' ? '▣' : loot?.unlock_kind === 'art' ? '✦' : loot?.unlock_kind === 'digital_merch' ? '◆' : '◇'
              return (
                <button
                  key={loot?.id ?? `empty-${index}`}
                  type="button"
                  className={loot ? 'has-item' : undefined}
                  aria-label={loot ? `Select ${loot.unlock_slug ?? loot.unlock_kind}` : `Select empty inventory slot ${index + 1}`}
                  aria-pressed={selectedLootSlot === index}
                  disabled={inventoryState !== 'ready'}
                  onClick={() => setSelectedLootSlot(index)}
                >
                  <span aria-hidden="true">{glyph}</span>
                  <small>{String(index + 1).padStart(2, '0')}</small>
                </button>
              )
            })}
          </div>
        </div>
      </div>
      <div className="ryozan-loot-items">
        {quests.filter((item) => item.completed || item.claimed).slice(0, 4).map((item) => (
          <div key={item.quest.id}>
            <span aria-hidden="true">◆</span>
            <p><strong>{item.quest.title}</strong><small>{item.quest.coin_reward} RYO · {item.quest.xp_reward} XP</small></p>
            {item.claimed ? <b>Owned</b> : (
              <button type="button" disabled={claimingQuest === item.quest.id} onClick={() => claimQuest(item.quest.id)}>
                {claimingQuest === item.quest.id ? 'Claiming' : 'Claim'}
              </button>
            )}
          </div>
        ))}
        {!claimableQuests.length && !claimedQuests.length && inventoryState === 'ready' ? <p className="ryozan-club-tool-empty">Complete side quests to unlock rewards and collectibles.</p> : null}
        {inventory?.grants.slice(0, 3).map((grant) => (
          <div key={grant.id}>
            <span aria-hidden="true">◆</span>
            <p><strong>{grant.unlock_kind === 'loot_pack' ? 'Loot Pack' : grant.unlock_kind?.replace('_', ' ')}</strong><small>{grant.unlock_slug}</small></p>
            <b>Owned</b>
          </div>
        ))}
        {inventory?.offers.filter((offer) => offer.status === 'offered').slice(0, 1).map((offer) => (
          <div key={offer.id}>
            <span aria-hidden="true">◇</span>
            <p><strong>Minigame ready</strong><small>Pick a card to unlock {offer.reward_kind.replace('_', ' ')}</small></p>
            <button type="button" onClick={() => setPlayingOffer(offer.id)}>Play</button>
          </div>
        ))}
      </div>
      {activeOffer ? (
        <div className="ryozan-loot-minigame" aria-label="Tiger Draw minigame">
          <strong>Tiger Draw</strong>
          <small>Pick one card. The earned drop is already sealed inside the offer.</small>
          <div>
            {[1, 2, 3].map((card) => (
              <button key={card} type="button" disabled={offerWorking} onClick={completeMiniGame} aria-label={`Pick card ${card}`}>◆</button>
            ))}
          </div>
        </div>
      ) : null}
      {claimError ? <p className="ryozan-club-tool-error">That reward could not be claimed. Try again.</p> : null}
      <small>Art, digital merch, and Loot Packs collect here as off-chain ownership receipts.</small>
    </section>
  )
}

function ClubCampaignCard({ collectorId, onOpenHerald }: { collectorId: string; onOpenHerald: () => void }) {
  const genesisStorageKey = `ryozan.club.genesis.${collectorId}`
  const [genesisProfile, setGenesisProfile] = useState<GenesisProfile | null>(() => {
    try {
      const stored = window.localStorage.getItem(genesisStorageKey)
      return stored ? JSON.parse(stored) as GenesisProfile : null
    } catch {
      return null
    }
  })
  const [genesisStep, setGenesisStep] = useState(-1)
  const [genesisDraft, setGenesisDraft] = useState<Partial<GenesisProfile>>({})
  const genesisQuestions = [
    { prompt: 'What are you building toward?', options: ['Create', 'Confidence', 'Connection'] },
    { prompt: 'How do you move best?', options: ['Steady', 'Bold', 'Curious'] },
    { prompt: 'Choose your Automaton sprite.', options: ['Tiger', 'Spark', 'Shade'] },
  ]
  const activeGenesisQuestion = genesisStep >= 0 ? genesisQuestions[genesisStep] : null

  const chooseGenesisAnswer = (value: string) => {
    if (genesisStep === 0) {
      setGenesisDraft({ focus: value })
      setGenesisStep(1)
      return
    }
    if (genesisStep === 1) {
      setGenesisDraft((current) => ({ ...current, cadence: value }))
      setGenesisStep(2)
      return
    }
    const completed: GenesisProfile = {
      focus: genesisDraft.focus ?? 'Create',
      cadence: genesisDraft.cadence ?? 'Steady',
      sprite: value,
    }
    setGenesisProfile(completed)
    setGenesisDraft({})
    setGenesisStep(-1)
    try {
      window.localStorage.setItem(genesisStorageKey, JSON.stringify(completed))
    } catch {
      // The local trial remains usable if browser storage is unavailable.
    }
  }

  return (
    <section className="ryozan-club-tool ryozan-club-tool--codex" aria-label="Campaign Codex">
      <header><span>Codex</span><strong>Campaign</strong></header>
      {genesisProfile ? (
        <div className="ryozan-genesis-complete">
          <span className={`ryozan-genesis-sprite is-${genesisProfile.sprite.toLowerCase()}`} aria-label={`${genesisProfile.sprite} Automaton`}>◆</span>
          <div><small>Genesis sealed</small><strong>{genesisProfile.sprite} Automaton</strong><p>{genesisProfile.focus} path · {genesisProfile.cadence} nature</p></div>
          <button type="button" onClick={onOpenHerald}>Continue with Herald</button>
        </div>
      ) : activeGenesisQuestion ? (
        <div className="ryozan-genesis-session">
          <span className="ryozan-genesis-sprite is-gala" aria-label="Gala pixel guide">✦</span>
          <small>Genesis {genesisStep + 1} of {genesisQuestions.length}</small>
          <strong>{activeGenesisQuestion.prompt}</strong>
          <div>{activeGenesisQuestion.options.map((option) => <button type="button" key={option} onClick={() => chooseGenesisAnswer(option)}>{option}</button>)}</div>
        </div>
      ) : (
        <div className="ryozan-genesis-locked">
          <span className="ryozan-genesis-sprite is-herald" aria-hidden="true">◆</span>
          <div><small>Campaign locked</small><strong>Create your Genesis</strong><p>Herald introduces one private Gala session. Your answers shape a permanent Automaton guide.</p></div>
          <button type="button" onClick={() => setGenesisStep(0)}>Begin with Herald</button>
        </div>
      )}
      <small>After Genesis, Gala leaves the campaign. Herald and your Automaton carry the story forward.</small>
    </section>
  )
}

function ClubChatCard({ embedded = false }: { embedded?: boolean }) {
  const [chatState, setChatState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [chatPosts, setChatPosts] = useState<ClubPost[]>([])
  const [chatDraft, setChatDraft] = useState('')
  const [chatPosting, setChatPosting] = useState(false)

  const loadPosts = useCallback(() => {
    setChatState('loading')
    clubApi.listPosts({ limit: 10 })
      .then((result) => {
        setChatPosts(result.posts)
        setChatState('ready')
      })
      .catch(() => setChatState('failed'))
  }, [])

  useEffect(() => loadPosts(), [loadPosts])

  const submitChatPost = async () => {
    const body = chatDraft.trim()
    if (!body || chatPosting) return
    setChatPosting(true)
    try {
      const post = await clubApi.createPost({ body, post_type: 'general' })
      setChatPosts((posts) => [post, ...posts])
      setChatDraft('')
      setChatState('ready')
    } catch {
      setChatState('failed')
    } finally {
      setChatPosting(false)
    }
  }

  return (
    <section className={`ryozan-club-tool ryozan-club-tool--chat${embedded ? ' is-embedded' : ''}`} aria-label="Club Chat">
      {!embedded ? <header><span>Chat</span><strong>Club forum</strong></header> : null}
      <div className="ryozan-club-chat-compose">
        <textarea value={chatDraft} onChange={(event) => setChatDraft(event.target.value)} placeholder="Share with the Club" maxLength={2000} rows={2} />
        <button type="button" disabled={!chatDraft.trim() || chatPosting} onClick={submitChatPost}>{chatPosting ? 'Posting' : 'Post'}</button>
      </div>
      {chatState === 'loading' ? <PortalContentSkeleton variant="thread" rows={3} label="Loading Club Chat" /> : null}
      {chatState === 'failed' ? <button type="button" className="ryozan-club-chat-retry" onClick={loadPosts}>Chat unavailable · retry</button> : null}
      {chatState === 'ready' && chatPosts.length === 0 ? <p className="ryozan-club-tool-empty">No Club posts yet.</p> : null}
      {chatPosts.slice(0, 6).map((post) => (
        <article key={post.id}><strong>{post.author_name || 'Collector'}</strong><p>{post.body}</p><small>{shortDate(post.created_at)} · {post.likes_count} likes</small></article>
      ))}
    </section>
  )
}

const founderContentFeed: Record<ContentBrandLane, Array<{ id: string; title: string; kind: string; state: string; next: string }>> = {
  jwt: [
    { id: 'jwt-weekly', title: 'Weekly E-zine + Safety Meeting', kind: 'Weekly package', state: 'Blueprint', next: 'Choose the week\'s premise' },
    { id: 'jwt-flash', title: 'Weekly flash sheet', kind: 'Visual practice', state: 'Cadence needed', next: 'Attach theme and references' },
    { id: 'jwt-painting', title: 'Monthly painting', kind: 'Long-form practice', state: 'Cadence needed', next: 'Set the monthly concept' },
    { id: 'jwt-ideas', title: 'Build and teaching ideas', kind: 'Opportunity bank', state: 'Draft only', next: 'Route research through Scout' },
  ],
  ryozan: [
    { id: 'ryozan-issue', title: 'Herald shop issue', kind: 'E-zine projection', state: 'Blueprint', next: 'Choose the collector lesson' },
    { id: 'ryozan-guild', title: 'Guild learning bulletin', kind: 'Scholar projection', state: 'Not configured', next: 'Ratify Smoku routing' },
    { id: 'ryozan-drop', title: 'Flash and shop drop', kind: 'Commerce story', state: 'Draft only', next: 'Attach approved art' },
    { id: 'ryozan-clips', title: 'Collector clips and stories', kind: 'Derivative set', state: 'Draft only', next: 'Select approved source media' },
  ],
}

const founderPipelineStages = [
  ['Intake', 'AInbox', 'Partial'],
  ['Evidence', 'Scout + Specktr', 'Not configured'],
  ['Scroll', 'Scholar', 'Not configured'],
  ['Skrypt', 'Muse', 'Configured'],
  ['Production', 'Creator Runtime', 'Partial'],
  ['Review', 'Aegis', 'Required'],
  ['Handoff', 'Herald', 'Partial'],
] as const

const founderConnectors = [
  ['AInbox intake', 'Capture and route source material', 'Partial'],
  ['Nexus record', 'Provenance, state, and receipts', 'Authority'],
  ['Social delivery', 'Channel drafts and JWT atomization', 'Configured'],
  ['Google Workspace', 'Docs, mail, and calendar handoff', 'Audit needed'],
  ['Phone agents', 'Bella intake and Gala operator calls', 'Needs wiring'],
  ['Sigma', 'Gaming and on-screen derivative planning', 'Preparatory'],
  ['Smoku', 'Guild curriculum projection', 'Not configured'],
] as const

function FounderHeraldContentDesk({ onBriefHerald }: { onBriefHerald: () => void }) {
  const [brand, setBrand] = useState<ContentBrandLane>('jwt')
  const [view, setView] = useState<ContentDeskView>('feed')
  const [selectedId, setSelectedId] = useState(founderContentFeed.jwt[0].id)
  const feed = founderContentFeed[brand]
  const selected = feed.find((item) => item.id === selectedId) ?? feed[0]

  const selectBrand = (lane: ContentBrandLane) => {
    setBrand(lane)
    setSelectedId(founderContentFeed[lane][0].id)
  }

  return (
    <section className="ryozan-club-tool ryozan-founder-content" aria-label="Founder Herald content desk">
      <header><span>Content</span><strong>Local blueprint · draft only</strong></header>
      <div className="ryozan-content-brand-tabs" aria-label="Content brand lane">
        <button type="button" aria-pressed={brand === 'jwt'} onClick={() => selectBrand('jwt')}>JWT</button>
        <button type="button" aria-pressed={brand === 'ryozan'} onClick={() => selectBrand('ryozan')}>Ryozan</button>
      </div>
      <div className="ryozan-content-view-tabs" aria-label="Content desk view">
        {(['feed', 'pipeline', 'connectors'] as ContentDeskView[]).map((item) => (
          <button type="button" key={item} aria-pressed={view === item} onClick={() => setView(item)}>{item === 'connectors' ? 'I/O' : item}</button>
        ))}
      </div>

      {view === 'feed' ? (
        <div className="ryozan-content-feed">
          {feed.map((item) => (
            <button type="button" key={item.id} aria-pressed={selected.id === item.id} onClick={() => setSelectedId(item.id)}>
              <span>{item.kind}</span><strong>{item.title}</strong><small>{item.state}</small>
            </button>
          ))}
          <div className="ryozan-content-inspector">
            <span>Next action</span><strong>{selected.next}</strong><small>No publishing or scheduling is enabled from this trial.</small>
          </div>
        </div>
      ) : null}

      {view === 'pipeline' ? (
        <div className="ryozan-content-pipeline">
          {founderPipelineStages.map(([stage, owner, state], index) => (
            <div key={stage}><i>{String(index + 1).padStart(2, '0')}</i><p><strong>{stage}</strong><span>{owner}</span></p><small>{state}</small></div>
          ))}
        </div>
      ) : null}

      {view === 'connectors' ? (
        <div className="ryozan-content-connectors">
          {founderConnectors.map(([name, purpose, state]) => (
            <div key={name}><p><strong>{name}</strong><span>{purpose}</span></p><small>{state}</small></div>
          ))}
        </div>
      ) : null}

      <button type="button" className="ryozan-content-brief" onClick={onBriefHerald}>Brief Herald →</button>
      <small>JWT and Ryozan remain separate brand lanes. Collector surfaces receive approved Ryozan projections only.</small>
    </section>
  )
}

function ClubPage({ quests, broadcasts, membership, state, heraldState, onOpenClub, onOpenWorkflow, headerAction, collectorView, founderView, profileName, profileAvatar, ryoBalance }: {
  quests: QuestWithProgress[]
  broadcasts: BroadcastItem[]
  membership: UserMembership | null
  state: 'idle' | 'loading' | 'ready' | 'failed'
  heraldState: 'idle' | 'loading' | 'ready' | 'failed'
  onOpenClub: () => void
  onOpenWorkflow: (kind: ClubWorkflowKind, anchor: HTMLButtonElement) => void
  headerAction: ReactNode
  collectorView: boolean
  founderView: boolean
  profileName: string
  profileAvatar: string | null
  ryoBalance: number | null
}) {
  const [openClubSections, setOpenClubSections] = useState<Array<'membership' | 'quests' | 'herald'>>([])
  const [heraldFeedTab, setHeraldFeedTab] = useState<'news' | 'chat'>('news')
  const [failedProfileAvatar, setFailedProfileAvatar] = useState<string | null>(null)
  const membershipMenuId = useId()
  const questMenuId = useId()
  const heraldMenuId = useId()
  const membershipOpen = openClubSections.includes('membership')
  const questsOpen = openClubSections.includes('quests')
  const upperAccordionCount = Number(membershipOpen) + Number(questsOpen)
  const heraldCompacted = upperAccordionCount === 2
  const heraldOpen = !heraldCompacted || openClubSections.includes('herald')
  const heraldSizeClass = upperAccordionCount === 0 ? 'is-roomy' : upperAccordionCount === 1 ? 'is-standard' : 'is-compacted'
  const toggleClubSection = (section: 'membership' | 'quests' | 'herald') => {
    setOpenClubSections((sections) => (
      sections.includes(section)
        ? sections.filter((item) => item !== section)
        : [...sections, section].slice(-2)
    ))
  }
  const closeClubSection = (section: 'membership' | 'quests' | 'herald') => {
    setOpenClubSections((sections) => sections.filter((item) => item !== section))
  }
  const earnedXp = quests.reduce((total, item) => total + (item.completed ? item.quest.xp_reward : 0), 0)
  const membershipTier = collectorView && membership?.effective_tier === 'staff'
    ? 'collector'
    : membership?.effective_tier ?? (collectorView ? 'collector' : 'staff')
  const membershipUsage = !membership
    ? state === 'loading' ? 'syncing member access' : 'access unavailable'
    : collectorView && membership.effective_tier === 'staff'
    ? 'member access'
    : membership.ai_messages_remaining == null
      ? 'unmetered'
      : `${membership.ai_messages_remaining} AI messages left`
  const membershipStatus = membership?.membership_status ?? (state === 'loading' ? 'loading' : 'offline')

  return (
    <>
      <PageTitle action={headerAction}>Club</PageTitle>
      <div data-portal-enter className={`ryozan-membership-stack${membershipOpen ? ' is-open' : ''}`}>
          <button
            type="button"
            className="ryozan-membership-card"
            aria-expanded={membershipOpen}
            aria-controls={membershipMenuId}
            onClick={() => toggleClubSection('membership')}
          >
            <span className="ryozan-membership-avatar" aria-hidden="true">
              {profileAvatar && profileAvatar !== failedProfileAvatar
                ? <img src={profileAvatar} alt="" onError={() => setFailedProfileAvatar(profileAvatar)} />
                : <SourceNavIcon paths={sourceIcons.profile} />}
            </span>
            <span className="ryozan-membership-copy">
              <span>Membership</span>
              <strong>{membershipTier}</strong>
              <small>{membershipStatus} · {membershipUsage}</small>
            </span>
            <span className="ryozan-membership-wallet">
              <small>RYO</small>
              <strong>{ryoBalance == null ? '—' : ryoBalance.toLocaleString()}</strong>
            </span>
            <svg className="ryozan-membership-chevron" width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <path d="m4.5 7 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <div id={membershipMenuId} className="ryozan-club-actions" aria-label={`${profileName} Club collection`} aria-hidden={!membershipOpen}>
            <div className="ryozan-club-actions-inner">
              <button type="button" tabIndex={membershipOpen ? 0 : -1} onClick={(event) => { closeClubSection('membership'); onOpenWorkflow('loot', event.currentTarget) }}><strong>Loot</strong><span>Rewards · Flash · Digital merch</span></button>
              <button type="button" tabIndex={membershipOpen ? 0 : -1} onClick={(event) => { closeClubSection('membership'); onOpenWorkflow('campaign', event.currentTarget) }}><strong>Codex</strong><span>Campaign · Quests · Tattoo journey</span></button>
              {founderView ? <button type="button" tabIndex={membershipOpen ? 0 : -1} onClick={(event) => { closeClubSection('membership'); onOpenWorkflow('content', event.currentTarget) }}><strong>Content</strong><span>JWT · Ryozan · Weekly engine</span></button> : null}
            </div>
          </div>
      </div>
      <div data-portal-enter className={`ryozan-quest-card${questsOpen ? ' is-open' : ''}`}>
        <button
          type="button"
          className="ryozan-quest-toggle"
          aria-expanded={questsOpen}
          aria-controls={questMenuId}
          onClick={() => toggleClubSection('quests')}
        >
          <strong>Side quests</strong>
          <span>{earnedXp} XP</span>
          <svg className="ryozan-quest-chevron" width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
            <path d="m4.5 7 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <div id={questMenuId} className="ryozan-quest-body" aria-hidden={!questsOpen}>
          <div className="ryozan-quest-body-inner">
            {state === 'loading' ? <PortalContentSkeleton variant="quest" rows={4} label="Loading live quests" /> : null}
            {state === 'failed' ? <p>Club data is unavailable right now</p> : null}
            {state === 'ready' && quests.length === 0 ? <p>No active quests</p> : null}
            {state === 'ready' ? quests.slice(0, 4).map((item) => (
              <button type="button" tabIndex={questsOpen ? 0 : -1} key={item.quest.id} onClick={onOpenClub} aria-label={`Open ${item.quest.title}`}>
                <i className={item.completed ? 'done' : ''} />
                <span className={item.completed ? 'done' : ''}>{item.quest.title}</span>
                <b>+{item.quest.xp_reward} XP</b>
              </button>
            )) : null}
          </div>
        </div>
      </div>
      <section data-portal-enter className={`ryozan-herald-feed ${heraldSizeClass}${heraldOpen ? ' is-open' : ''}`} aria-label="Herald newsfeed">
        <button
          type="button"
          className="ryozan-herald-feed-heading"
          aria-expanded={heraldOpen}
          aria-controls={heraldCompacted ? heraldMenuId : undefined}
          disabled={!heraldCompacted}
          onClick={() => {
            if (heraldCompacted) toggleClubSection('herald')
          }}
        >
          <HeraldMark />
          <span><small>Herald</small><strong>Newsfeed</strong></span>
          <b>{heraldState === 'ready' ? `${broadcasts.length} ${broadcasts.length === 1 ? 'issue' : 'issues'}` : 'Club wire'}</b>
          {heraldCompacted ? <svg className="ryozan-herald-feed-chevron" width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
            <path d="m4.5 7 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg> : null}
        </button>
        {heraldCompacted ? (
          <div className="ryozan-herald-compact-notice" aria-live="polite">
            <HeraldMark />
            <span>
              <strong>{heraldState === 'failed'
                ? 'Club wire unavailable'
                : heraldState === 'ready' && broadcasts[0]
                  ? broadcasts[0].title
                  : heraldState === 'ready'
                    ? 'No new Herald notifications'
                    : 'Checking the Club wire'}</strong>
              <small>{heraldState === 'ready' && broadcasts[0]
                ? broadcasts[0].body
                : 'Open Herald for the latest Club activity.'}</small>
            </span>
          </div>
        ) : null}
        <div id={heraldMenuId} className="ryozan-herald-feed-body" aria-hidden={!heraldOpen} inert={!heraldOpen}>
          <div className="ryozan-herald-feed-tabs" role="tablist" aria-label="Herald newsfeed views">
            <button type="button" role="tab" tabIndex={heraldOpen ? 0 : -1} aria-selected={heraldFeedTab === 'news'} onClick={() => setHeraldFeedTab('news')}>News</button>
            <button type="button" role="tab" tabIndex={heraldOpen ? 0 : -1} aria-selected={heraldFeedTab === 'chat'} onClick={() => setHeraldFeedTab('chat')}>Chat</button>
          </div>
          {heraldFeedTab === 'news' ? <div className="ryozan-herald-feed-list" role="tabpanel" aria-label="Herald news">
            {heraldState === 'loading' || heraldState === 'idle' ? (
              <PortalContentSkeleton variant="feed" rows={3} label="Opening the Club wire" />
            ) : null}
            {heraldState === 'failed' ? (
              <div className="ryozan-herald-feed-state is-failed"><HeraldMark /><strong>The Club wire is unavailable</strong><p>Open Herald to ask what is waiting or try again.</p><button type="button" tabIndex={heraldOpen ? 0 : -1} onClick={onOpenClub}>Open Herald</button></div>
            ) : null}
            {heraldState === 'ready' ? broadcasts.map((item) => (
              <button type="button" tabIndex={heraldOpen ? 0 : -1} className="ryozan-club-story" key={item.id} onClick={onOpenClub}>
                <header>
                  <strong>{item.pinned ? 'Pinned · ' : ''}{item.broadcast_type || 'Club news'}</strong>
                  <time>{shortDate(item.created_at)}</time>
                </header>
                <h2>{item.title}</h2>
                <p>{item.body}</p>
                <footer><span>{item.author_name || 'Herald'}</span><b>Read with Herald →</b></footer>
              </button>
            )) : null}
            {heraldState === 'ready' && broadcasts.length === 0 ? (
              <div className="ryozan-herald-feed-state"><HeraldMark /><strong>Nothing from Herald yet</strong><p>Published Club issues will appear here.</p><button type="button" tabIndex={heraldOpen ? 0 : -1} onClick={onOpenClub}>Open Herald</button></div>
            ) : null}
          </div> : <div className="ryozan-herald-feed-chat" role="tabpanel" aria-label="Club community conversations"><ClubChatCard embedded /></div>}
        </div>
      </section>
    </>
  )
}

interface AccountSettingsActionCardProps {
  agentName: string
  accent: string
}

interface HubConfigurationActionCardProps {
  target: HubConfigurationTarget
  agentName: string
  accent: string
  onOpenConfiguration: () => void
}

/**
 * The Hub option list already owns discovery. This card gives Augmentz and
 * Tools the same agent-thread handoff as Account while retaining their real
 * configuration destinations until those controls are fully embedded.
 */
function HubConfigurationActionCard({ target, agentName, accent, onOpenConfiguration }: HubConfigurationActionCardProps) {
  const augmentz = target === 'augmentz'
  return (
    <section
      className="ryozan-portal-account-settings ryozan-portal-account-settings--thread ryozan-hub-configuration-action"
      aria-label={`${augmentz ? 'Augmentz' : 'Tools'} configuration action card`}
      style={{ '--account-accent': accent } as CSSProperties}
    >
      <header className="ryozan-portal-account-settings__thread-header">
        <span>{agentName} · Hub action</span>
        <strong>{augmentz ? 'Augmentz' : 'Tools'}</strong>
        <p>{augmentz
          ? 'Manage the services connected to commerce, workspace, storage, and shop operations.'
          : 'Manage the working surfaces for booking, communication, content, and the artist profile.'}</p>
      </header>
      <div className="ryozan-hub-configuration-action__body">
        {(augmentz
          ? [
              ['Commerce', 'Clover · Mercury + Plaid'],
              ['Workspace', 'Google Business · Calendar'],
              ['Storage', 'Drive · Dropbox'],
            ]
          : [
              ['Schedule', 'Books · Daily · Sesh'],
              ['Communication', 'Courier · Socials'],
              ['Studio', 'Profile · Gallery · Content'],
            ]
        ).map(([label, value]) => (
          <div key={label}><span>{label}</span><strong>{value}</strong></div>
        ))}
        <button className="ryozan-portal-account-settings__save" type="button" onClick={onOpenConfiguration}>
          Open {augmentz ? 'Augmentz' : 'Tools'} configuration
        </button>
      </div>
    </section>
  )
}

function AccountSettingsActionCard({ agentName, accent }: AccountSettingsActionCardProps) {
  const user = useAuthStore((state) => state.user)
  const fetchMe = useAuthStore((state) => state.fetchMe)
  const [nameDraft, setNameDraft] = useState(user?.name ?? '')
  const [marketingDraft, setMarketingDraft] = useState(user?.marketing_opt_in ?? false)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle')
  const roleLabel = user?.role === 'founder'
    ? 'Founder · full access'
    : user?.role === 'admin'
      ? 'Admin · full access'
      : user?.role === 'artist'
        ? 'Artist'
        : user?.role === 'assistant'
          ? 'Assistant'
          : 'Collector'
  const memberSince = user?.created_at
    ? new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(user.created_at))
    : 'Not available'

  const saveSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const name = nameDraft.trim()
    if (!name || saveState === 'saving') return

    setSaveState('saving')
    try {
      await usersApi.updateProfile({ name, marketing_opt_in: marketingDraft })
      await fetchMe()
      setSaveState('saved')
    } catch {
      setSaveState('failed')
    }
  }

  return (
    <section
      className="ryozan-portal-account-settings ryozan-portal-account-settings--thread"
      aria-label="Account settings action card"
      style={{ '--account-accent': accent } as CSSProperties}
    >
      <header className="ryozan-portal-account-settings__thread-header">
        <span>{agentName} · account action</span>
        <strong>Account settings</strong>
        <p>Name and contact preferences save to your Ryozan account.</p>
      </header>
      <form onSubmit={saveSettings}>
        <label className="ryozan-portal-account-settings__field">
          <span>Display name</span>
          <input
            type="text"
            value={nameDraft}
            onChange={(event) => {
              setNameDraft(event.target.value)
              setSaveState('idle')
            }}
            autoComplete="name"
            required
          />
        </label>
        <div className="ryozan-portal-account-settings__facts" aria-label="Account facts">
          <div><span>Role</span><strong>{roleLabel}</strong></div>
          <div><span>Member since</span><strong>{memberSince}</strong></div>
        </div>
        <label className="ryozan-portal-account-settings__consent">
          <input
            type="checkbox"
            checked={marketingDraft}
            onChange={(event) => {
              setMarketingDraft(event.target.checked)
              setSaveState('idle')
            }}
          />
          <span><strong>Receive marketing emails</strong><small>Shop news, flash drops, and member updates.</small></span>
        </label>
        <button className="ryozan-portal-account-settings__save" type="submit" disabled={!nameDraft.trim() || saveState === 'saving'}>
          {saveState === 'saving' ? 'Saving…' : 'Save account'}
        </button>
        <p className={`ryozan-portal-account-settings__status is-${saveState}`} aria-live="polite">
          {saveState === 'saved' ? 'Account updated.' : saveState === 'failed' ? 'Could not save. Try again.' : ''}
        </p>
      </form>
    </section>
  )
}

function PortalViewportDebug({ enabled }: { enabled: boolean }) {
  const [lines, setLines] = useState<string[]>([])

  useEffect(() => {
    if (!enabled) return
    const selectors = [
      'html', 'body', '#root', '.app-view--portal-home', '.main-area',
      '.dash-content', '.nexus-body', '.nexus-canvas', '.ryozan-mobile-portal-home',
      'nav[aria-label="Portal navigation"]',
    ]
    const update = () => {
      const visual = window.visualViewport
      setLines([
        `window inner: ${window.innerWidth} × ${window.innerHeight}`,
        `visual viewport: ${Math.round(visual?.width ?? 0)} × ${Math.round(visual?.height ?? 0)} @ y${Math.round(visual?.offsetTop ?? 0)}`,
        ...selectors.map((selector) => {
          const rect = document.querySelector<HTMLElement>(selector)?.getBoundingClientRect()
          return `${selector}: ${rect ? `y${Math.round(rect.top)} h${Math.round(rect.height)} b${Math.round(rect.bottom)}` : 'missing'}`
        }),
      ])
    }
    update()
    window.addEventListener('resize', update)
    window.visualViewport?.addEventListener('resize', update)
    window.visualViewport?.addEventListener('scroll', update)
    return () => {
      window.removeEventListener('resize', update)
      window.visualViewport?.removeEventListener('resize', update)
      window.visualViewport?.removeEventListener('scroll', update)
    }
  }, [enabled])

  if (!enabled) return null
  return createPortal(
    <pre style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 2147483647,
      margin: 0, padding: '6px 8px', maxHeight: '42dvh', overflow: 'auto',
      background: 'rgba(0,0,0,.88)', color: '#7dff9a', font: '10px/1.35 monospace',
      whiteSpace: 'pre-wrap', pointerEvents: 'none',
    }}>{lines.join('\n')}</pre>,
    document.body,
  )
}

export default function MobilePortalHome({ role, bookings, unreadCount, loading, loadError }: MobilePortalHomeProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const portalRootRef = useRef<HTMLDivElement>(null)
  const accountMenuRef = useRef<HTMLDivElement>(null)
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const viewAs = useAuthStore((state) => state.viewAs)
  const setViewAs = useAuthStore((state) => state.setViewAs)
  const isCollector = role === 'collector'
  const isFounderOrAdmin = isAdminRole(role)
  const canUsePortalAi = Boolean(user)
  const canUseArtistHub = isArtistPlusRole(role)
  // Every signed-in account opens Connections; GET /integrations/options decides which services it offers.
  const canUseArtistConnections = Boolean(user?.id)
  const canViewArtistDeposits = role === 'artist' && user?.role === 'artist'
  const canOpenMercuryContacts = true
  const initialSearchParams = new URLSearchParams(location.search)
  const requestedPortalTab = initialSearchParams.get('portal')
  const viewportDebug = initialSearchParams.get('viewport-debug') === '1'
  const requestedCodex = initialSearchParams.get('panel') === 'codex' || initialSearchParams.get('hub') === 'codex'
  const requestedAgentEvolution = initialSearchParams.get('panel') === 'agents'
  const requestedArtistScroll = initialSearchParams.get('workflow') === 'artist-scroll'
  const requestedConnections = initialSearchParams.get('workflow') === 'connections'
  const requestedDeposits = initialSearchParams.get('workflow') === 'deposits'
  const artistPreview = role === 'artist' && user?.role === 'founder' && viewAs === 'artist'
  const artistScrollEligible = ['artist', 'admin', 'founder'].includes(role)
    && ['artist', 'admin', 'founder'].includes(user?.role ?? '')
  const forceArtistTour = initialSearchParams.get('tour') === '1'
  const welcomeSessionKey = `ryozan.portal.welcome.v1:${user?.id ?? role}`
  const artistTourKey = `ryozan.artist-tour.v1:${user?.id ?? role}`
  const availablePortalTabs = isCollector
    ? ['home', 'wall', 'table', 'club', 'you']
    : ['home', 'books', 'table', 'hub', 'club']
  const [activeTab, setActiveTab] = useState(() => {
    if ((requestedCodex || requestedAgentEvolution) && !isCollector) return 'hub'
    return availablePortalTabs.includes(requestedPortalTab ?? '') ? requestedPortalTab! : 'home'
  })
  const [pendingTab, setPendingTab] = useState<string | null>(null)
  const [conversationState, setConversationState] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle')
  const [conversationRows, setConversationRows] = useState<Conversation[]>([])
  const [courierQuery, setCourierQuery] = useState('')
  const [clubState, setClubState] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle')
  const [heraldState, setHeraldState] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle')
  const [questRows, setQuestRows] = useState<QuestWithProgress[]>([])
  const [clubRows, setClubRows] = useState<BroadcastItem[]>([])
  const [membership, setMembership] = useState<UserMembership | null>(null)
  const [ryoBalance, setRyoBalance] = useState<number | null>(user?.bttcoin_balance ?? null)
  const [profileState, setProfileState] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle')
  const [artistProfile, setArtistProfile] = useState<ArtistDetail | null>(null)
  const [accountOpen, setAccountOpen] = useState(false)
  const [agentOverrideId, setAgentOverrideId] = useState<'bella' | 'gala' | 'spyder' | null>(null)
  const [agentChatOpen, setAgentChatOpen] = useState(false)
  const [agentChatClosing, setAgentChatClosing] = useState(false)
  const [agentChatLaunch, setAgentChatLaunch] = useState<AgentChatLaunch>('direct')
  const agentChatOriginRef = useRef<AgentChatOrigin | null>(null)
  const agentPanelTimelineRef = useRef<ReturnType<typeof gsap.timeline> | null>(null)
  const [agentToolsOpen, setAgentToolsOpen] = useState(false)
  const [agentToolsClosing, setAgentToolsClosing] = useState(false)
  const [agentChatDraft, setAgentChatDraft] = useState('')
  const [agentChatWorking, setAgentChatWorking] = useState(false)
  const [agentThreads, setAgentThreads] = useState<Record<string, ChatLine[]>>({})
  const [selectedMercuryConversationId, setSelectedMercuryConversationId] = useState<string | null>(() => initialSearchParams.get('convo'))
  const mercuryThreadRequestRef = useRef(0)
  // Collector thread screen. Staff read a selected thread inside the Mercury
  // panel; collectors get a real messages screen on the tab itself, so it
  // carries its own transcript, draft, and send state.
  const [collectorThreadMessages, setCollectorThreadMessages] = useState<ConversationMessage[]>([])
  const [collectorThreadState, setCollectorThreadState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [collectorDraft, setCollectorDraft] = useState('')
  const [collectorSending, setCollectorSending] = useState(false)
  const [collectorSendError, setCollectorSendError] = useState<string | null>(null)
  const collectorThreadRequestRef = useRef(0)
  const collectorSendSeqRef = useRef(0)
  const agentChatRequestRef = useRef(0)
  const pendingAgentUploadsRef = useRef<Record<string, PendingAgentUpload[]>>({})
  const activeAgentSessionRef = useRef<AgentSessionSnapshot | null>(null)
  const handledArtistScrollRouteRef = useRef<string | null>(null)
  const handledConnectionsRouteRef = useRef<string | null>(null)
  const handledDepositsRouteRef = useRef<string | null>(null)
  const [agentChatMode, setAgentChatMode] = useState<'full' | 'booky-debrief-full' | 'hub-workflow-full'>('full')
  const [bookyDebriefLines, setBookyDebriefLines] = useState<ChatLine[]>([])
  const [selectedBookyDate, setSelectedBookyDate] = useState<string | null>(null)
  const [selectedBookyBookingId, setSelectedBookyBookingId] = useState<string | null>(null)
  const [selectedHuddlDate, setSelectedHuddlDate] = useState<string | null>(null)
  const [pendingTodayDebrief, setPendingTodayDebrief] = useState(false)
  const [calendarCueDate, setCalendarCueDate] = useState<string | null>(null)
  const [hubWorkflowLines, setHubWorkflowLines] = useState<ChatLine[]>([])
  const [selectedHubWorkflow, setSelectedHubWorkflow] = useState<PortalWorkflowKind | null>(null)
  const [hubSurface, setHubSurface] = useState<'home' | 'codex' | 'evolution'>(() => (
    requestedCodex ? 'codex' : requestedAgentEvolution ? 'evolution' : 'home'
  ))
  const [mainSiteExitTarget, setMainSiteExitTarget] = useState<string | null>(null)
  const [artistTourVisible, setArtistTourVisible] = useState(forceArtistTour && role === 'artist')
  const hasAuthorizedFlashTarget = role !== 'assistant' || Boolean(artistProfile?.id)

  const captureAgentChatOrigin = useCallback((explicitAnchor?: HTMLElement | null) => {
    const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const candidate = explicitAnchor ?? focused
    const insidePortal = Boolean(candidate && portalRootRef.current?.contains(candidate))
    const insideOpenPanel = Boolean(candidate?.closest('.ryozan-agent-chat-sheet'))
    if (!candidate || !insidePortal || insideOpenPanel) {
      agentChatOriginRef.current = null
      setAgentChatLaunch('direct')
      return
    }

    const rect = candidate.getBoundingClientRect()
    if (rect.width < 2 || rect.height < 2) {
      agentChatOriginRef.current = null
      setAgentChatLaunch('direct')
      return
    }

    agentChatOriginRef.current = {
      element: candidate,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
      borderRadius: Number.parseFloat(window.getComputedStyle(candidate).borderRadius) || 18,
    }
    setAgentChatLaunch('anchor')
  }, [])

  const showArtistConnections = useCallback(() => {
    setAccountOpen(false)
    setPendingTab(null)
    // Collectors have no Hub tab; their workflow sheet opens over You.
    setActiveTab(isCollector ? 'you' : 'hub')
    setHubSurface('home')
    setAgentOverrideId(null)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentChatLaunch('direct')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow('connections')
    setAgentChatMode('hub-workflow-full')
    setHubWorkflowLines([{
      id: `artist-connections-${user?.id ?? 'current'}`,
      from: 'agent',
      presentation: 'card',
      body: <ArtistConnections />,
    }])
    setAgentChatOpen(true)
  }, [isCollector, user?.id])

  const showStudioSettings = useCallback(() => {
    setAccountOpen(false)
    setPendingTab(null)
    setActiveTab('hub')
    setHubSurface('home')
    setAgentOverrideId(null)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentChatLaunch('direct')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow('studio-settings')
    setAgentChatMode('hub-workflow-full')
    setHubWorkflowLines([{
      id: 'studio-settings',
      from: 'agent',
      presentation: 'card',
      body: <StudioSettingsForm />,
    }])
    setAgentChatOpen(true)
  }, [])

  const openArtistConnections = useCallback((anchor?: HTMLElement | null) => {
    if (!canUseArtistConnections) return
    const search = `?portal=${isCollector ? 'you' : 'hub'}&workflow=connections`
    const target = `${ROUTES.DASHBOARD}${search}`
    if (location.search === search) {
      captureAgentChatOrigin(anchor)
      showArtistConnections()
      return
    }
    navigate(target, { replace: true })
  }, [canUseArtistConnections, captureAgentChatOrigin, isCollector, location.search, navigate, showArtistConnections])

  const showArtistDeposits = useCallback(() => {
    setAccountOpen(false)
    setPendingTab(null)
    setActiveTab('hub')
    setHubSurface('home')
    setAgentOverrideId(null)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentChatLaunch('direct')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow('deposits')
    setAgentChatMode('hub-workflow-full')
    setHubWorkflowLines([{
      id: `artist-deposits-${user?.id ?? 'current'}`,
      from: 'agent',
      presentation: 'card',
      body: <ArtistDepositActivity surface="workflow" />,
    }])
    setAgentChatOpen(true)
  }, [user?.id])

  const openArtistDeposits = useCallback((anchor?: HTMLElement | null) => {
    if (!canViewArtistDeposits) return
    const target = `${ROUTES.DASHBOARD}?portal=hub&workflow=deposits`
    if (location.search === '?portal=hub&workflow=deposits') {
      captureAgentChatOrigin(anchor)
      showArtistDeposits()
      return
    }
    navigate(target, { replace: true })
  }, [canViewArtistDeposits, captureAgentChatOrigin, location.search, navigate, showArtistDeposits])

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const routeTabs = isCollector
      ? ['home', 'wall', 'table', 'club', 'you']
      : ['home', 'books', 'table', 'hub', 'club']
    const panel = params.get('panel')
    const nextHubSurface = !isCollector && (panel === 'codex' || params.get('hub') === 'codex')
      ? 'codex'
      : !isCollector && panel === 'agents'
        ? 'evolution'
        : 'home'
    const routeTab = params.get('portal')
    const nextTab = nextHubSurface !== 'home'
      ? 'hub'
      : routeTabs.includes(routeTab ?? '')
        ? routeTab!
        : 'home'
    const nextConversationId = nextTab === 'table' ? params.get('convo') : null
    const keepArtistScroll = params.get('workflow') === 'artist-scroll' && artistScrollEligible
    const keepConnections = params.get('workflow') === 'connections' && canUseArtistConnections
    const keepDeposits = params.get('workflow') === 'deposits' && canViewArtistDeposits

    setPendingTab(null)
    setActiveTab(nextTab)
    setHubSurface(nextHubSurface)
    setSelectedMercuryConversationId(nextConversationId)

    if (!nextConversationId) {
      collectorThreadRequestRef.current += 1
      collectorSendSeqRef.current += 1
      setCollectorThreadMessages([])
      setCollectorThreadState('loading')
      setCollectorDraft('')
      setCollectorSendError(null)
      setCollectorSending(false)
    }

    if (!keepArtistScroll && !keepConnections && !keepDeposits && !nextConversationId) {
      setSelectedHubWorkflow(null)
      setSelectedBookyDate(null)
      setSelectedBookyBookingId(null)
      setAgentOverrideId(null)
      setAgentToolsOpen(false)
      setAgentChatOpen(false)
      setAgentChatClosing(false)
      setAgentChatMode('full')
    }
    if (!keepConnections) handledConnectionsRouteRef.current = null
    if (!keepDeposits) handledDepositsRouteRef.current = null
  }, [artistScrollEligible, canUseArtistConnections, canViewArtistDeposits, isCollector, location.search])

  useEffect(() => {
    if (role !== 'artist' || requestedArtistScroll || requestedConnections || requestedDeposits) return
    if (forceArtistTour) {
      setArtistTourVisible(true)
      return
    }
    try {
      setArtistTourVisible(window.localStorage.getItem(artistTourKey) !== 'complete')
    } catch {
      setArtistTourVisible(true)
    }
  }, [artistTourKey, forceArtistTour, requestedArtistScroll, requestedConnections, requestedDeposits, role])

  const completeArtistTour = useCallback(() => {
    try {
      window.localStorage.setItem(artistTourKey, 'complete')
    } catch {
      // The tour can still close when browser storage is unavailable.
    }
    setArtistTourVisible(false)
  }, [artistTourKey])

  const openArtistScrollPreview = useCallback(() => {
    completeArtistTour()
    navigate(`${ROUTES.DASHBOARD}?portal=hub&workflow=artist-scroll`, { replace: true })
  }, [completeArtistTour, navigate])

  useEffect(() => {
    if (!requestedArtistScroll || !artistScrollEligible || !user?.id) return
    const routeKey = `${user.id}:${location.search}`
    if (handledArtistScrollRouteRef.current === routeKey) return
    handledArtistScrollRouteRef.current = routeKey

    try {
      window.sessionStorage.setItem(welcomeSessionKey, 'seen')
    } catch {
      // The assigned Scroll still opens when browser storage is unavailable.
    }
    setAccountOpen(false)
    setPendingTab(null)
    setActiveTab('hub')
    setHubSurface('home')
    setAgentOverrideId('spyder')
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentChatLaunch('direct')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow('artist-scroll')
    setAgentChatMode('hub-workflow-full')
    setHubWorkflowLines([{
      id: `spyder-artist-scroll-${user.id}`,
      from: 'agent',
      presentation: 'card',
      body: <ArtistScrollWorkflowCard artistName={user.name || 'Artist'} preview={artistPreview} />,
    }])
    setAgentChatOpen(true)
  }, [artistPreview, artistScrollEligible, location.search, requestedArtistScroll, user?.id, user?.name, welcomeSessionKey])

  useEffect(() => {
    if (!requestedConnections || !canUseArtistConnections || !user?.id) return
    const routeKey = `${user.id}:${location.search}`
    if (handledConnectionsRouteRef.current === routeKey) return
    handledConnectionsRouteRef.current = routeKey
    showArtistConnections()
  }, [canUseArtistConnections, location.search, requestedConnections, showArtistConnections, user?.id])

  useEffect(() => {
    if (!requestedDeposits || !canViewArtistDeposits || !user?.id) return
    const routeKey = `${user.id}:${location.search}`
    if (handledDepositsRouteRef.current === routeKey) return
    handledDepositsRouteRef.current = routeKey
    showArtistDeposits()
  }, [canViewArtistDeposits, location.search, requestedDeposits, showArtistDeposits, user?.id])


  const requestMainSiteExit = useCallback(() => {
    setAccountOpen(false)
    setMainSiteExitTarget('/')
  }, [])

  const openAccountSettings = useCallback(() => {
    captureAgentChatOrigin()
    const target = isCollector
      ? {
          id: 'bella' as const,
          name: 'Ensei',
          accent: '#D38A68',
          opener: 'Your account is open. I can help you keep your profile and shop preferences current.',
          tab: 'home',
        }
      : isFounderOrAdmin
        ? {
            id: 'gala' as const,
            name: 'Gala',
            accent: '#D9BF95',
            opener: 'Your founder account controls are ready in the Hub.',
            tab: 'hub',
          }
        : {
            id: 'spyder' as const,
            name: 'Sōkō',
            accent: '#D9BF95',
            opener: 'Your staff account controls are ready in the Hub.',
            tab: 'hub',
          }
    const cardLine: ChatLine = {
      id: `${target.id}-account-settings`,
      from: 'agent',
      presentation: 'card',
      body: <AccountSettingsActionCard agentName={target.name} accent={target.accent} />,
    }

    setAgentThreads((threads) => {
      const existing = threads[target.id] ?? [{ id: `${target.id}-opener`, from: 'agent' as const, body: target.opener }]
      return {
        ...threads,
        [target.id]: [...existing.filter((line) => line.id !== cardLine.id), cardLine],
      }
    })
    setAccountOpen(false)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentOverrideId(target.id)
    setAgentChatMode('full')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow(null)
    setPendingTab(null)
    setActiveTab(target.tab)
    navigate(`${ROUTES.DASHBOARD}?portal=${target.tab}`, { replace: true })
    setAgentChatOpen(true)
  }, [captureAgentChatOrigin, isCollector, isFounderOrAdmin, navigate])

  const openHubConfiguration = useCallback((target: HubConfigurationTarget) => {
    captureAgentChatOrigin()
    const agentTarget = isFounderOrAdmin
      ? { id: 'gala' as const, name: 'Gala', accent: '#D9BF95', opener: 'Your founder Hub is ready.' }
      : { id: 'spyder' as const, name: 'Sōkō', accent: '#D9BF95', opener: 'Your staff Hub is ready.' }
    const destination = target === 'augmentz'
      ? `${ROUTES.ARTIST_PROFILE}?portalConfig=1&tab=integrations&section=integrations`
      : `${ROUTES.ARTIST_PROFILE}?portalConfig=1&tab=settings&section=app-settings`
    const cardId = `${agentTarget.id}-${target}-configuration`
    const cardLine: ChatLine = {
      id: cardId,
      from: 'agent',
      presentation: 'card',
      body: (
        <HubConfigurationActionCard
          target={target}
          agentName={agentTarget.name}
          accent={agentTarget.accent}
          onOpenConfiguration={() => navigate(destination)}
        />
      ),
    }

    setAgentThreads((threads) => {
      const existing = threads[agentTarget.id] ?? [{ id: `${agentTarget.id}-opener`, from: 'agent' as const, body: agentTarget.opener }]
      return {
        ...threads,
        [agentTarget.id]: [...existing.filter((line) => line.id !== cardId), cardLine],
      }
    })
    setAccountOpen(false)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentOverrideId(agentTarget.id)
    setAgentChatMode('full')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow(null)
    setPendingTab(null)
    setActiveTab('hub')
    navigate(`${ROUTES.DASHBOARD}?portal=hub`, { replace: true })
    setAgentChatOpen(true)
  }, [captureAgentChatOrigin, isFounderOrAdmin, navigate])

  const closeAccount = useCallback(() => {
    setAccountOpen(false)
  }, [])

  const completeMainSiteExit = useCallback(() => {
    if (mainSiteExitTarget) window.location.assign(mainSiteExitTarget)
  }, [mainSiteExitTarget])

  useEffect(() => {
    if (!isCollector) return
    rewardsApi.recordPortalActivity().catch(() => undefined)
  }, [isCollector])

  const loadConversations = useCallback(() => {
    if (conversationState !== 'idle') return
    setConversationState('loading')
    conversationsApi.list()
      .then((result) => {
        setConversationRows(result.conversations)
        setConversationState('ready')
      })
      .catch(() => setConversationState('failed'))
  }, [conversationState])

  const loadClub = useCallback(() => {
    if (clubState !== 'idle') return
    setClubState('loading')
    setHeraldState('loading')
    Promise.allSettled([questsApi.list(), broadcastsApi.list(10), usersApi.membership(), ryoCoinApi.getWallet()])
      .then(([questResult, broadcastResult, membershipResult, walletResult]) => {
        if (questResult.status === 'fulfilled') setQuestRows(questResult.value.quests)
        if (broadcastResult.status === 'fulfilled') {
          setClubRows(broadcastResult.value.broadcasts)
          setHeraldState('ready')
        } else {
          setHeraldState('failed')
        }
        if (membershipResult.status === 'fulfilled') setMembership(membershipResult.value)
        if (walletResult.status === 'fulfilled') setRyoBalance(walletResult.value.balance)
        setClubState([questResult, broadcastResult, membershipResult].some((result) => result.status === 'fulfilled') ? 'ready' : 'failed')
      })
  }, [clubState])

  const claimClubQuest = useCallback(async (questId: string) => {
    const result = await questsApi.claim(questId)
    setRyoBalance(result.new_bttcoin_balance)
    setQuestRows((rows) => rows.map((item) => (
      item.quest.id === questId
        ? { ...item, claimed: true, claimed_at: new Date().toISOString() }
        : item
    )))
  }, [])

  const loadArtistProfile = useCallback(() => {
    if (!canUseArtistHub) return
    if (profileState === 'loading' || profileState === 'ready') return
    setProfileState('loading')
    artistsApi.list()
      .then((artists) => {
        const currentArtist = artists.find((artist) => artist.id === user?.id)
        if (!currentArtist) throw new Error('Authenticated artist profile was not found')
        return artistsApi.get(currentArtist.slug)
      })
      .then((profile) => {
        setArtistProfile(profile)
        setProfileState('ready')
      })
      .catch(() => setProfileState('failed'))
  }, [canUseArtistHub, profileState, user?.id])

  useEffect(() => {
    if (activeTab === 'table') loadConversations()
    if (activeTab === 'hub' && canUseArtistHub) loadArtistProfile()
    if (activeTab === 'club') loadClub()
  }, [activeTab, canUseArtistHub, loadArtistProfile, loadClub, loadConversations])

  useEffect(() => {
    if (!accountOpen) return
    const focusTimer = window.requestAnimationFrame(() => {
      accountMenuRef.current?.querySelector<HTMLElement>('[role="menuitem"], button')?.focus()
    })
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeAccount()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.cancelAnimationFrame(focusTimer)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [accountOpen, closeAccount])

  const [now] = useState(() => Date.now())
  const activeBookings = bookings
    .filter((booking) => !terminalStatuses.has(booking.status.toLowerCase()))
    .filter((booking) => bookingTimestamp(booking) >= now || !booking.scheduled_date)
    .sort((a, b) => bookingTimestamp(a) - bookingTimestamp(b))
  const nextBooking = activeBookings[0]
  const todayKey = localDateKey()
  const todayBookings = bookings
    .filter((booking) => booking.scheduled_date === todayKey)
    .filter((booking) => !terminalStatuses.has(booking.status.toLowerCase()))
    .sort((a, b) => bookingTimestamp(a) - bookingTimestamp(b))

  const metaDate = new Date().toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  }).toUpperCase().replace(',', '')
  const meta = `${metaDate} // ${todayBookings.length} ${todayBookings.length === 1 ? 'appointment' : 'appointments'} today`
  const visibleUnreadCount = conversationState === 'ready'
    ? conversationRows.reduce((total, conversation) => total + conversation.unread_count, 0)
    : unreadCount
  const bookingDestination = (booking: Booking) => {
    if (isCollector) {
      return booking.reference_code ? dynamicRoutes.collectorBookingRef(booking.reference_code) : ROUTES.BOOKINGS_MANAGER
    }
    return booking.reference_code ? dynamicRoutes.bookingRef(booking.reference_code) : ROUTES.BOOKINGS
  }

  const sessionPassFor = (booking: Booking): ComponentProps<typeof SessionPass> => ({
    eyebrow: isCollector ? 'Session pass' : 'Next up',
    titleTop: bookingTitle(booking),
    titleAccent: bookingSubject(booking),
    status: statusLabel(booking.status),
    rows: [
      { label: 'When', value: formatWhen(booking) },
      { label: 'Type', value: [booking.style_preference, booking.size_estimate].filter(Boolean).join(' · ') || 'Details pending' },
      {
        label: 'Deposit',
        value: booking.deposit_amount == null
          ? 'Not set'
          : `$${booking.deposit_amount.toFixed(0)} · ${booking.deposit_paid ? 'Paid' : 'Due'}`,
        valueColor: booking.deposit_paid ? 'var(--sig-ok)' : 'var(--sig-warn)',
      },
    ],
    ctaLabel: isCollector ? 'View your pass →' : "Today’s schedule →",
    onCta: () => {
      if (isCollector) {
        navigate(bookingDestination(booking))
        return
      }
      setPendingTodayDebrief(true)
      setCalendarCueDate(null)
      selectTab('books')
    },
  })

  const schedule: ScheduleEntry[] = todayBookings.length
    ? todayBookings.map((booking) => ({
        id: booking.id,
        time: formatTime(booking.scheduled_time),
        label: `${bookingTitle(booking)} · ${bookingSubject(booking)}`,
        state: statusLabel(booking.status),
        stateColor: statusColor(booking.status),
        onSelect: () => navigate(bookingDestination(booking)),
      }))
    : [{
        id: 'open-books',
        time: 'OPEN',
        label: loadError ? 'Schedule unavailable' : loading ? 'Loading appointments' : 'No appointments scheduled',
        actionLabel: loadError ? 'Retry' : 'Open books',
        onAction: () => loadError ? window.location.reload() : navigate(ROUTES.BOOKINGS),
        dim: true,
      }]

  const tabs: TabItem[] = isCollector
    ? [
        { id: 'home', label: 'Home', icon: <SourceNavIcon paths={sourceIcons.home} /> },
        { id: 'wall', label: 'Shop', icon: <SourceNavIcon paths={sourceIcons.wall} /> },
        { id: 'table', label: 'Inbox', icon: <SourceNavIcon paths={sourceIcons.inbox} />, badge: visibleUnreadCount },
        { id: 'club', label: 'Club', icon: <SourceNavIcon paths={sourceIcons.club} /> },
        { id: 'you', label: 'Huddl', icon: <SourceNavIcon paths={sourceIcons.profile} /> },
      ]
    : [
        { id: 'home', label: 'Home', icon: <SourceNavIcon paths={sourceIcons.home} /> },
        { id: 'books', label: 'Books', icon: <SourceNavIcon paths={sourceIcons.books} /> },
        { id: 'table', label: 'Inbox', icon: <SourceNavIcon paths={sourceIcons.inbox} />, badge: visibleUnreadCount },
        isFounderOrAdmin
          ? { id: 'hub', label: 'Hub', icon: <SourceNavIcon paths={sourceIcons.hub} /> }
          : { id: 'hub', label: 'Hub', icon: <SourceNavIcon paths={sourceIcons.hub} /> },
        { id: 'club', label: 'Club', icon: <SourceNavIcon paths={sourceIcons.club} /> },
      ]

  const pass: ComponentProps<typeof SessionPass> = loading
    ? {
        eyebrow: isCollector ? 'Session pass' : 'Next up',
        titleTop: 'Schedule',
        titleAccent: 'Loading',
        rows: [
          { label: 'When', value: 'Checking live bookings' },
          { label: 'Type', value: 'Waiting for booking data' },
          { label: 'Deposit', value: 'Waiting' },
        ],
      }
    : nextBooking
      ? sessionPassFor(nextBooking)
      : {
          eyebrow: isCollector ? 'Session pass' : 'Next up',
          titleTop: loadError ? 'Schedule' : 'No appointment',
          titleAccent: loadError ? 'Unavailable' : 'Scheduled',
          rows: [
            { label: 'When', value: 'Open' },
            { label: 'Type', value: loadError ? 'Could not load' : 'Available' },
            { label: 'Deposit', value: 'None' },
          ],
          ctaLabel: loadError ? 'Retry →' : 'Open books →',
          onCta: () => loadError ? window.location.reload() : openBooking(),
        }

  const huddlDate = selectedHuddlDate
    ?? nextBooking?.scheduled_date
    ?? nextBooking?.preferred_date
    ?? todayKey
  const huddlBooking = activeBookings.find((booking) => (
    (booking.scheduled_date ?? booking.preferred_date) === huddlDate
  ))
  const huddlPass = huddlBooking ? sessionPassFor(huddlBooking) : null

  const galaLine = loadError
    ? 'The schedule could not be loaded. Retry to restore live booking data.'
    : loading
      ? 'Loading today’s appointments and booking states.'
      : todayBookings.length
        ? `${todayBookings.length} ${todayBookings.length === 1 ? 'appointment is' : 'appointments are'} on today’s schedule.`
        : 'No appointments are scheduled today. Your books are open.'

  const openConversation = useCallback(async (conversation: Conversation, origin?: HTMLButtonElement | null) => {
    // Collectors get the thread screen on the tab, not the agent panel. The
    // panel is a staff working surface: it grounds Mercury in the transcript so
    // it can draft house replies. A client opening their own thread wants to
    // read it and answer it.
    if (isCollector) {
      mercuryThreadRequestRef.current += 1
      setAccountOpen(false)
      setAgentOverrideId(null)
      setAgentChatOpen(false)
      setAgentChatClosing(false)
      setAgentToolsOpen(false)
      setAgentChatWorking(false)
      setAgentChatDraft('')
      setSelectedMercuryConversationId(conversation.id)
      navigate(`${ROUTES.DASHBOARD}?portal=table&convo=${encodeURIComponent(conversation.id)}`, { replace: true })
      return
    }

    const requestId = ++mercuryThreadRequestRef.current
    const title = conversationTitle(conversation)
    if (!agentChatOpen) captureAgentChatOrigin(origin)
    setSelectedMercuryConversationId(conversation.id)
    setAccountOpen(false)
    setAgentOverrideId(null)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentChatMode('full')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow(null)
    setAgentThreads((threads) => ({
      ...threads,
      mercury: [{
        id: `mercury-thread-loading-${conversation.id}`,
        from: 'agent',
        presentation: 'card',
        body: <MercuryThreadLoading title={title} />,
      }],
    }))
    setAgentChatWorking(true)
    setAgentChatOpen(true)
    navigate(`${ROUTES.DASHBOARD}?portal=table&convo=${encodeURIComponent(conversation.id)}`, { replace: true })

    try {
      const result = await conversationsApi.getMessages(conversation.id, { limit: 100 })
      if (mercuryThreadRequestRef.current !== requestId) return
      const messages = [...result.messages].reverse()
      setAgentThreads((threads) => ({
        ...threads,
        mercury: [
          {
            id: `mercury-thread-${conversation.id}`,
            from: 'agent',
            presentation: 'card',
            body: <MercuryConversationCard conversation={conversation} messages={messages} />,
          },
        ],
      }))
      void conversationsApi.markRead(conversation.id).then(() => {
        setConversationRows((rows) => rows.map((row) => row.id === conversation.id
          ? { ...row, is_unread: false, unread_count: 0 }
          : row))
      }).catch(() => undefined)
    } catch {
      if (mercuryThreadRequestRef.current !== requestId) return
      setAgentThreads((threads) => ({
        ...threads,
        mercury: [{ id: `mercury-thread-failed-${conversation.id}`, from: 'agent', body: `${title} could not be loaded. The Courier list is still available behind this panel.` }],
      }))
    } finally {
      if (mercuryThreadRequestRef.current === requestId) setAgentChatWorking(false)
    }
  }, [agentChatOpen, captureAgentChatOrigin, isCollector, navigate])

  const loadMercuryContacts = useCallback(async (): Promise<MercuryContact[]> => {
    if (!canOpenMercuryContacts) {
      throw new Error('Contact discovery is unavailable for assistant accounts.')
    }
    if (isCollector) {
      const rows = await artistsApi.list()
      return rows
        .map((artist) => ({ id: String(artist.id), name: artist.name, email: '', role: 'artist' as const, avatar_url: artist.avatar_url || null }))
        .filter((contact) => contact.id !== user?.id)
    }
    if (role === 'assistant') {
      const result = await contactsApi.list({ limit: 200 })
      return result.contacts.map((contact) => ({
        id: contact.id,
        name: contact.name,
        email: contact.email ?? '',
        role: 'collector' as const,
        avatar_url: null,
      }))
    }
    if (role === 'admin' || role === 'founder') {
      const rows = await usersApi.list({ limit: 200 })
      return rows.filter((contact) => contact.id !== user?.id)
    }
    const rows = await usersApi.myClients()
    return rows.filter((contact) => contact.id !== user?.id)
  }, [canOpenMercuryContacts, isCollector, role, user?.id])

  const prepareMercurySurface = useCallback(() => {
    captureAgentChatOrigin()
    mercuryThreadRequestRef.current += 1
    setSelectedMercuryConversationId(null)
    setAccountOpen(false)
    setAgentOverrideId(null)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentChatMode('full')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow(null)
    setAgentChatDraft('')
    setAgentChatOpen(true)
    navigate(`${ROUTES.DASHBOARD}?portal=table`, { replace: true })
  }, [captureAgentChatOrigin, navigate])

  const showMercuryCompose = useCallback((contacts: MercuryContact[], initialContact?: MercuryContact) => {
    setAgentThreads((threads) => ({
      ...threads,
      mercury: [{
        id: `mercury-compose-${Date.now()}`,
        from: 'agent',
        presentation: 'card',
        body: <MercuryComposeCard
          contacts={contacts}
          initialContact={initialContact}
          onStart={(contact, channel) => {
            setAgentChatWorking(true)
            void conversationsApi.create({ participant_ids: [contact.id] })
              .then(async (conversation) => {
                const refreshed = await conversationsApi.list().catch(() => null)
                if (refreshed) setConversationRows(refreshed.conversations)
                await openConversation(conversation)
                if (channel !== 'courier') setAgentChatDraft(`Draft a ${channel} message for ${contact.name || contact.email}.`)
              })
              .catch(() => {
                setAgentThreads((current) => ({
                  ...current,
                  mercury: [{ id: 'mercury-compose-failed', from: 'agent', body: 'That draft could not be opened. Your Courier list is unchanged.' }],
                }))
              })
              .finally(() => setAgentChatWorking(false))
          }}
        />,
      }],
    }))
  }, [openConversation])

  const openMercuryCompose = useCallback(() => {
    prepareMercurySurface()
    // The dedicated inline Mercury loader is the sole activity signal here.
    // Do not also mount AgentChatSheet's generic thinking glyph.
    setAgentChatWorking(false)
    setAgentThreads((threads) => ({ ...threads, mercury: [{ id: 'mercury-compose-loading', from: 'agent', body: 'Loading your contacts.' }] }))
    void loadMercuryContacts()
      .then((contacts) => showMercuryCompose(contacts))
      .catch(() => setAgentThreads((threads) => ({ ...threads, mercury: [{ id: 'mercury-compose-unavailable', from: 'agent', body: 'Contacts are unavailable right now.' }] })))
      .finally(() => setAgentChatWorking(false))
  }, [loadMercuryContacts, prepareMercurySurface, showMercuryCompose])

  const openMercuryRolodex = useCallback(() => {
    prepareMercurySurface()
    setAgentChatWorking(true)
    setAgentThreads((threads) => ({ ...threads, mercury: [{ id: 'mercury-rolodex-loading', from: 'agent', body: 'Opening your Rolodex.' }] }))
    void loadMercuryContacts()
      .then((contacts) => {
        setAgentThreads((threads) => ({
          ...threads,
          mercury: [{
            id: 'mercury-rolodex',
            from: 'agent',
            presentation: 'card',
            body: <MercuryRolodexCard contacts={contacts} onCompose={(contact) => showMercuryCompose(contacts, contact)} />,
          }],
        }))
      })
      .catch(() => setAgentThreads((threads) => ({ ...threads, mercury: [{ id: 'mercury-rolodex-unavailable', from: 'agent', body: 'The Rolodex is unavailable right now.' }] })))
      .finally(() => setAgentChatWorking(false))
  }, [loadMercuryContacts, prepareMercurySurface, showMercuryCompose])

  const deleteMercuryConversation = useCallback((conversation: Conversation) => {
    const title = conversationTitle(conversation)
    if (!window.confirm(`Remove ${title} from your Courier inbox? This archives the thread for your account.`)) return
    void conversationsApi.delete(conversation.id)
      .then(() => {
        setConversationRows((rows) => rows.filter((row) => row.id !== conversation.id))
        if (selectedMercuryConversationId === conversation.id) {
          setSelectedMercuryConversationId(null)
          setAgentChatOpen(false)
        }
      })
      .catch(() => window.alert(`${title} could not be deleted.`))
  }, [selectedMercuryConversationId])

  const createMercuryMemorySuggestion = useCallback((conversation: Conversation) => {
    if (!canUsePortalAi) return
    const title = conversationTitle(conversation)
    prepareMercurySurface()
    setAgentChatWorking(true)
    setAgentThreads((threads) => ({
      ...threads,
      mercury: [{ id: `mercury-memory-loading-${conversation.id}`, from: 'agent', body: `Digesting ${title} into a safe memory suggestion.` }],
    }))

    void conversationsApi.getMessages(conversation.id, { limit: 100 })
      .then(async (result) => {
        const messages = [...result.messages].reverse().slice(-30)
        const transcript = messages
          .map((message) => `${message.sender_id === user?.id ? 'Signed-in user' : message.sender_name || 'Participant'}: ${message.content}`)
          .join('\n')
          .slice(0, 10000)
        const response = await galaApi.chat({
          message: `Suggest one concise, durable memory from this Courier thread. Do not save it yet. Preserve only the signed-in user's follow-up preference or requested next action. Do not retain another person's private message text, health details, credentials, or payment data.\n\nThread transcript:\n${transcript}`,
          context: {
            portal_tab: 'table',
            agent_id: 'mercury', agent_name: 'Taisō',
            conversation_id: conversation.id,
            conversation_name: title,
            instruction: 'Return only the proposed memory in plain language. This is a suggestion for user review, not permission to persist it.',
          },
        })
        const suggestion = response.response?.trim() || 'No safe durable memory was found in this thread.'
        const memoryCard: AgentActionCard = {
          id: `courier-memory-${conversation.id}`,
          name: 'Memory suggestion',
          description: suggestion,
          shell: 'memory-shard',
        }
        setAgentThreads((threads) => ({
          ...threads,
          mercury: [{
            id: `mercury-memory-card-${conversation.id}`,
            from: 'agent',
            presentation: 'card',
            body: <ActionCard
              card={memoryCard}
              agentName="Mercury"
              accent="#B795E8"
              accentRgb="183,149,232"
              commitLabel="Create memory"
              onCommit={() => galaApi.closeSession({
                agent_id: 'mercury',
                turns: [
                  { role: 'user', content: `Create the approved memory suggestion for Courier thread ${title}.` },
                  { role: 'assistant', content: suggestion },
                ],
                context: { portal_tab: 'table', conversation_id: conversation.id, conversation_name: title },
                memory_consent: true,
              }).then(() => undefined)}
            />,
          }],
        }))
      })
      .catch(() => setAgentThreads((threads) => ({
        ...threads,
        mercury: [{ id: `mercury-memory-failed-${conversation.id}`, from: 'agent', body: `${title} could not be digested. The thread was not changed.` }],
      })))
      .finally(() => setAgentChatWorking(false))
  }, [canUsePortalAi, prepareMercurySurface, user?.id])

  // Staff deep link: rehydrate the Mercury panel for `?convo=`. Collectors are
  // driven by `collectorThreadId` below instead, so they are excluded here or
  // this would keep re-opening a panel they never see.
  useEffect(() => {
    if (isCollector) return
    if (!selectedMercuryConversationId || conversationState !== 'ready' || agentChatOpen) return
    const conversation = conversationRows.find((row) => row.id === selectedMercuryConversationId)
    if (conversation) void openConversation(conversation)
  }, [agentChatOpen, conversationRows, conversationState, isCollector, openConversation, selectedMercuryConversationId])

  const selectedMercuryConversation = selectedMercuryConversationId
    ? conversationRows.find((conversation) => conversation.id === selectedMercuryConversationId) ?? null
    : null

  const sendMercuryCollectorMessage = useCallback(async () => {
    const conversation = selectedMercuryConversation
    const content = agentChatDraft.trim()
    if (!conversation || !content || agentChatWorking) return
    setAgentChatWorking(true)
    try {
      const message = await conversationsApi.sendMessage(conversation.id, content)
      setAgentChatDraft('')
      setConversationRows((rows) => rows.map((row) => (
        row.id === conversation.id ? { ...row, last_message: message, updated_at: message.created_at } : row
      )))
      await openConversation(conversation)
    } catch (error) {
      setAgentThreads((threads) => ({
        ...threads,
        mercury: [
          ...(threads.mercury ?? []),
          {
            id: `collector-send-error-${Date.now()}`,
            from: 'agent',
            body: error instanceof ApiError ? error.message : 'That message could not be sent. Try again.',
            animateReply: true,
          },
        ],
      }))
    } finally {
      setAgentChatWorking(false)
    }
  }, [agentChatDraft, agentChatWorking, openConversation, selectedMercuryConversation])

  const collectorThreadId = isCollector ? selectedMercuryConversationId : null
  const collectorThread = isCollector ? selectedMercuryConversation : null

  const loadCollectorThread = useCallback((conversationId: string, options: { showLoading?: boolean } = {}) => {
    const requestId = ++collectorThreadRequestRef.current
    if (options.showLoading ?? true) setCollectorThreadState('loading')
    return conversationsApi.getMessages(conversationId, { limit: 100 })
      .then((result) => {
        if (collectorThreadRequestRef.current !== requestId) return
        setCollectorThreadMessages([...result.messages].reverse())
        setCollectorThreadState('ready')
      })
      .catch(() => {
        if (collectorThreadRequestRef.current !== requestId) return
        // A failed background poll must not tear down a transcript that is
        // already on screen. Only a cold open falls through to the retry card.
        setCollectorThreadState((current) => (current === 'ready' ? current : 'failed'))
      })
  }, [])

  useEffect(() => {
    if (!collectorThreadId) return
    setCollectorThreadMessages([])
    setCollectorDraft('')
    setCollectorSendError(null)
    setCollectorSending(false)
    void loadCollectorThread(collectorThreadId, { showLoading: true })
    void conversationsApi.markRead(collectorThreadId)
      .then(() => {
        setConversationRows((rows) => rows.map((row) => (
          row.id === collectorThreadId ? { ...row, is_unread: false, unread_count: 0 } : row
        )))
      })
      .catch(() => undefined)
  }, [collectorThreadId, loadCollectorThread])

  // Same 5s cadence the staff console uses, so an incoming reply lands without
  // the client refreshing.
  useEffect(() => {
    if (!collectorThreadId) return
    const poll = () => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
      void loadCollectorThread(collectorThreadId, { showLoading: false })
    }
    const interval = window.setInterval(poll, 5000)
    document.addEventListener('visibilitychange', poll)
    return () => {
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', poll)
    }
  }, [collectorThreadId, loadCollectorThread])

  const sendCollectorMessage = useCallback(async () => {
    const conversationId = collectorThreadId
    const content = collectorDraft.trim()
    if (!conversationId || !content || collectorSending) return
    const requestSeq = ++collectorSendSeqRef.current
    setCollectorSending(true)
    setCollectorSendError(null)
    try {
      const message = await conversationsApi.sendMessage(conversationId, content)
      if (collectorSendSeqRef.current !== requestSeq) return
      // Discard any poll already in flight against the pre-send transcript,
      // then append the record the server actually stored.
      collectorThreadRequestRef.current += 1
      setCollectorThreadMessages((current) => (
        current.some((existing) => existing.id === message.id) ? current : [...current, message]
      ))
      setCollectorThreadState('ready')
      setCollectorDraft('')
      setConversationRows((rows) => rows.map((row) => (
        row.id === conversationId ? { ...row, last_message: message, updated_at: message.created_at } : row
      )))
    } catch (error) {
      if (collectorSendSeqRef.current !== requestSeq) return
      setCollectorSendError(error instanceof ApiError ? error.message : 'That reply could not be sent. Try again.')
    } finally {
      if (collectorSendSeqRef.current === requestSeq) setCollectorSending(false)
    }
  }, [collectorDraft, collectorSending, collectorThreadId])

  const closeCollectorThread = useCallback(() => {
    collectorThreadRequestRef.current += 1
    collectorSendSeqRef.current += 1
    setSelectedMercuryConversationId(null)
    setCollectorThreadMessages([])
    setCollectorThreadState('loading')
    setCollectorDraft('')
    setCollectorSendError(null)
    setCollectorSending(false)
    navigate(`${ROUTES.DASHBOARD}?portal=table`, { replace: true })
  }, [navigate])
  const accountAction = <AccountAction expanded={accountOpen} onOpen={() => {
    setAgentChatOpen(false)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAccountOpen((open) => !open)
  }} />

  const homeAgent = isCollector
    ? {
        id: 'smoku', name: 'Rochishin', role: 'Tattoo guide', accent: '#8FB89A', bloom: '143,184,154', glyph: '◉' as ReactNode,
        opener: nextBooking ? `Your next appointment is ${formatWhen(nextBooking)}. Ask me what to bring or what happens next.` : 'Your account is open. Ask me about tattoo styles, booking preparation, aftercare, or what happens next.',
        suggestion: 'What should I know before my appointment?',
      }
    : isFounderOrAdmin
      ? {
          id: 'gala', name: 'Gala', role: 'Orchestrator', accent: '#D9BF95', bloom: '232,223,207', glyph: '✦' as ReactNode,
          opener: galaLine,
          suggestion: 'What is stuck, and who owns the next verified step?',
        }
      : {
          id: 'spyder', name: 'Sōkō', role: 'Operator · back of house', accent: 'var(--gold)', bloom: '217,191,149', glyph: <SpyderMark size={34} strongBloom /> as ReactNode,
          opener: galaLine,
          suggestion: 'What is stuck on the floor?',
        }

  const collectorBookyActive = isCollector && (activeTab === 'you' || (activeTab === 'wall' && Boolean(nextBooking)))
  const pageAgent = activeTab === 'books' || collectorBookyActive
    ? {
        id: 'booky', name: 'Saishin', role: 'Booking agent', accent: BOOKY_ACCENT, bloom: BOOKY_ACCENT_RGB, glyph: '◷' as ReactNode,
        opener: `${activeBookings.length} active ${activeBookings.length === 1 ? 'booking is' : 'bookings are'} loaded. Ask me about gaps, follow-up, or scheduling risk.`,
        suggestion: 'Show me the next gap worth filling.',
      }
    : isCollector && activeTab === 'wall'
      ? {
          id: 'xar', name: 'Shishin', role: 'Shop agent', accent: 'var(--alt-sky)', bloom: '99,186,223', glyph: '◈' as ReactNode,
          opener: 'The shop is open. I can help you browse flash and merch without changing your booking.',
          suggestion: 'Show me what is available.',
        }
    : activeTab === 'table'
      ? isCollector ? {
          // Same agent slot, client-facing copy. "Deals for the house" and
          // "draft replies for the stale threads" are back-of-house language;
          // a collector's inbox is their own correspondence with the shop.
          id: 'mercury', name: 'Taisō', role: 'Your messages', accent: '#B795E8', bloom: '183,149,232', glyph: <MercuryMark /> as ReactNode,
          opener: conversationState === 'ready'
            ? `You have ${conversationRows.length} ${conversationRows.length === 1 ? 'conversation' : 'conversations'} with the shop. Open one to read it and write back.`
            : 'Your conversations with the shop live here. Open one to read it and write back.',
          suggestion: 'Start a new message.',
        } : {
          id: 'mercury', name: 'Taisō', role: 'Courier · deals for the house', accent: '#B795E8', bloom: '183,149,232', glyph: <MercuryMark /> as ReactNode,
          opener: conversationState === 'ready' ? `${conversationRows.length} Courier ${conversationRows.length === 1 ? 'thread is' : 'threads are'} loaded. Tell me which reply or follow-up needs work.` : 'Open a Courier thread or ask me to review what needs a reply.',
          suggestion: 'Draft replies for the stale threads.',
        }
      : activeTab === 'hub'
        ? isFounderOrAdmin ? {
            id: 'gala', name: 'Gala', role: 'Orchestrator', accent: '#D9BF95', bloom: '232,223,207', glyph: '✦' as ReactNode,
            opener: 'Hub is open. Delegate here and I will keep the work tied to the current Ryozan surface.',
            suggestion: 'What is stuck, and who is sitting on it?',
          } : {
            id: 'xar', name: 'Shishin', role: 'Hustle commerce agent', accent: 'var(--alt-sky)', bloom: '99,186,223', glyph: '◈' as ReactNode,
            opener: 'Hub inventory is open. I can stage flash, merch, and creator commerce work without inventing missing product data.',
            suggestion: 'What are we getting ready to sell?',
          }
        : activeTab === 'club'
          ? {
              id: 'herald', name: 'Riki', role: 'Club agent · the ezine', accent: '#8FB89A', bloom: '143,184,154', glyph: <HeraldMark /> as ReactNode,
              opener: heraldState === 'ready'
                ? `${clubRows.length} Club ${clubRows.length === 1 ? 'issue is' : 'issues are'} loaded. Ask what needs review before it ships.`
                : heraldState === 'failed'
                  ? 'The Herald newsfeed is unavailable right now. I can still help with Club issues, quests, or member follow-up.'
                  : 'Opening the Club wire. Ask me about Club issues, quests, or member follow-up.',
              suggestion: 'What needs my read before it ships?',
            }
          : homeAgent

  const accountAgent = agentOverrideId === 'bella'
    ? {
        id: 'bella', name: 'Ensei', role: 'Front desk · the shop bot', accent: '#D38A68', bloom: '211,138,104', glyph: '✧' as ReactNode,
        opener: 'Your account is open. I can help you keep your profile and shop preferences current.',
        suggestion: 'Help me update my account.',
      }
    : agentOverrideId === 'gala'
      ? {
          id: 'gala', name: 'Gala', role: 'Orchestrator', accent: '#D9BF95', bloom: '232,223,207', glyph: '✦' as ReactNode,
          opener: 'Your founder account controls are ready in the Hub.',
          suggestion: 'Show me the account controls.',
        }
      : agentOverrideId === 'spyder'
        ? {
            id: 'spyder', name: 'Sōkō', role: 'Operator · back of house', accent: 'var(--gold)', bloom: '217,191,149', glyph: <SpyderMark size={34} strongBloom /> as ReactNode,
            opener: 'Your staff account controls are ready in the Hub.',
            suggestion: 'Show me the account controls.',
          }
        : null
  const agent = accountAgent ?? pageAgent
  const selectedBookyBooking = selectedBookyBookingId
    ? bookings.find((booking) => booking.id === selectedBookyBookingId) ?? null
    : null

  const agentLines = agentThreads[agent.id] ?? [{ id: `${agent.id}-opener`, from: 'agent', body: agent.opener }]
  const mercuryConversationActive = (agent.id === 'mercury' || agent.id === 'pulse') && Boolean(selectedMercuryConversation)
  const mercuryModeAccent = mercuryConversationActive ? '#C8B2F0' : agent.accent
  const mercuryModeBloom = mercuryConversationActive ? '200,178,240' : agent.bloom
  const bookyDebriefActive = agent.id === 'booky' && agentChatMode !== 'full'
  // Collectors open Connections over You, since they have no Hub tab.
  const hubWorkflowActive = (activeTab === 'hub' || activeTab === 'club' || (isCollector && activeTab === 'you')) && agentChatMode === 'hub-workflow-full'
  const artistScrollWorkflowActive = hubWorkflowActive && selectedHubWorkflow === 'artist-scroll' && agent.id === 'spyder'
  const displayedAgentLines = hubWorkflowActive
    ? hubWorkflowLines
    : bookyDebriefActive
    ? bookyDebriefLines
    : agentLines
  const renderedAgentLines = useMemo<ChatLine[]>(() => displayedAgentLines.map((line) => {
    if (line.presentation === 'card' || typeof line.body !== 'string') return line
    const words = line.body.trim().split(/\s+/)
    return {
      ...line,
      body: (
        <span className="ryozan-agent-message-copy" aria-label={line.body}>
          <span aria-hidden="true">
            {words.map((word, index) => (
              <span key={`${line.id}-${index}`}>
                <span className="ryozan-agent-message-word">{word}</span>
                {index < words.length - 1 ? ' ' : null}
              </span>
            ))}
          </span>
        </span>
      ),
    }
  }), [displayedAgentLines])
  const latestDisplayedLine = displayedAgentLines.at(-1)

  useEffect(() => {
    if (!agentChatOpen) return
    activeAgentSessionRef.current = {
      agentId: agent.id,
      kind: hubWorkflowActive ? 'hub' : bookyDebriefActive ? 'booky' : 'agent',
      lines: displayedAgentLines,
      context: {
        portal_tab: activeTab,
        conversation_id: (agent.id === 'mercury' || agent.id === 'pulse') ? selectedMercuryConversationId : undefined,
        conversation_name: (agent.id === 'mercury' || agent.id === 'pulse') && selectedMercuryConversation
          ? conversationTitle(selectedMercuryConversation)
          : undefined,
        selected_date: bookyDebriefActive ? selectedBookyDate : undefined,
        booking_id: bookyDebriefActive ? selectedBookyBooking?.id : undefined,
        booking_reference: bookyDebriefActive ? selectedBookyBooking?.reference_code : undefined,
        hub_workflow: hubWorkflowActive ? selectedHubWorkflow : undefined,
      },
    }
  }, [
    activeTab,
    agent.id,
    agentChatOpen,
    bookyDebriefActive,
    displayedAgentLines,
    hubWorkflowActive,
    selectedBookyDate,
    selectedBookyBooking,
    selectedHubWorkflow,
    selectedMercuryConversation,
    selectedMercuryConversationId,
  ])

  useEffect(() => {
    if (agentChatOpen) return
    const snapshot = activeAgentSessionRef.current
    if (!snapshot) return
    activeAgentSessionRef.current = null
    agentChatRequestRef.current += 1
    setAgentChatWorking(false)

    const turns = snapshot.lines
      .filter((line): line is ChatLine & { body: string } => (
        typeof line.body === 'string' && !line.id.endsWith('-opener')
      ))
      .map((line) => ({
        role: line.from === 'agent' ? 'assistant' as const : 'user' as const,
        content: line.body,
      }))
    const hasUserTurn = turns.some((turn) => turn.role === 'user')

    const clearSnapshot = () => {
      if (snapshot.kind === 'hub') {
        setHubWorkflowLines((lines) => lines === snapshot.lines ? [] : lines)
      } else if (snapshot.kind === 'booky') {
        setBookyDebriefLines((lines) => lines === snapshot.lines ? [] : lines)
      } else {
        setAgentThreads((threads) => {
          if (threads[snapshot.agentId] !== snapshot.lines) return threads
          const next = { ...threads }
          delete next[snapshot.agentId]
          return next
        })
      }
    }

    if (!hasUserTurn) {
      clearSnapshot()
      return
    }

    void galaApi.closeSession({
      agent_id: snapshot.agentId,
      turns,
      context: snapshot.context,
      memory_consent: false,
    }).then((result) => {
      if (result.compacted) clearSnapshot()
    }).catch(() => {
      // Keep the local snapshot recoverable when compaction cannot be proven.
    })
  }, [agentChatOpen])

  const finalizeAgentChatClose = useCallback(() => {
    agentPanelTimelineRef.current = null
    agentChatOriginRef.current = null
    setAgentToolsOpen(false)
    setAgentToolsClosing(false)
    setAgentChatOpen(false)
    setAgentChatClosing(false)
    setAgentChatDraft('')
    setAgentChatWorking(false)
    setAgentChatLaunch('direct')
    setAgentOverrideId(null)
    if (agent.id === 'booky') {
      setSelectedBookyDate(null)
      setSelectedBookyBookingId(null)
    }
    if ((agent.id === 'mercury' || agent.id === 'pulse') && selectedMercuryConversationId) {
      setSelectedMercuryConversationId(null)
      navigate(`${ROUTES.DASHBOARD}?portal=table`, { replace: true })
    }
    if (activeTab === 'hub' || activeTab === 'club') setSelectedHubWorkflow(null)
  }, [activeTab, agent.id, navigate, selectedMercuryConversationId])

  const requestAgentChatClose = useCallback(() => {
    if (!agentChatOpen || agentChatClosing) return
    setAgentToolsOpen(false)
    setAgentChatClosing(true)
  }, [agentChatClosing, agentChatOpen])

  const finalizeAgentToolsClose = useCallback(() => {
    setAgentToolsOpen(false)
    setAgentToolsClosing(false)
  }, [])

  const requestAgentToolsClose = useCallback(() => {
    if (!agentToolsOpen || agentToolsClosing) return
    setAgentToolsClosing(true)
  }, [agentToolsClosing, agentToolsOpen])

  useGSAP((_context, contextSafe) => {
    if (!agentChatOpen || agentChatClosing) return
    const root = portalRootRef.current
    const sheet = root?.querySelector<HTMLElement>('.ryozan-agent-chat-sheet')
    if (!root || !sheet) return

    const header = sheet.querySelector<HTMLElement>('.ryozan-agent-chat-header')
    const scrim = root.querySelector<HTMLElement>('.ryozan-agent-chat-scrim')
    const fab = root.querySelector<HTMLElement>(`button[aria-label="Open ${agent.name} chat"]`)
    const navigation = root.querySelector<HTMLElement>('nav[aria-label="Portal navigation"]')
    const lines = gsap.utils.toArray<HTMLElement>('.ryozan-agent-chat-line', sheet)
    const messageWords = gsap.utils.toArray<HTMLElement>('.ryozan-agent-message-word', sheet)
    const stageContent = gsap.utils.toArray<HTMLElement>(
      '.ryozan-agent-chat-thread, .ryozan-agent-chat-floating-action, .ryozan-agent-chat-dock, .ryozan-agent-chat-composer',
      sheet,
    )
    const sheetRect = sheet.getBoundingClientRect()
    const navigationRect = navigation?.getBoundingClientRect()
    const foldedY = Math.max(56, (navigationRect?.top ?? root.getBoundingClientRect().bottom) - sheetRect.top + 6)
    const savedOrigin = agentChatOriginRef.current
    const originElement = agentChatLaunch === 'fab' ? fab : savedOrigin?.element
    const liveOriginRect = originElement?.isConnected ? originElement.getBoundingClientRect() : null
    const origin = liveOriginRect && liveOriginRect.width > 1 && liveOriginRect.height > 1
      ? {
          left: liveOriginRect.left,
          top: liveOriginRect.top,
          width: liveOriginRect.width,
          height: liveOriginRect.height,
          borderRadius: Number.parseFloat(window.getComputedStyle(originElement!).borderRadius) || savedOrigin?.borderRadius || 18,
        }
      : savedOrigin
    const hasOrigin = Boolean(origin && agentChatLaunch !== 'direct')
    const x = origin ? origin.left + origin.width / 2 - (sheetRect.left + sheetRect.width / 2) : 0
    const y = origin ? origin.top + origin.height / 2 - (sheetRect.top + sheetRect.height / 2) : foldedY
    const scaleX = origin ? gsap.utils.clamp(.08, .88, origin.width / sheetRect.width) : 1
    const scaleY = origin ? gsap.utils.clamp(.045, .62, origin.height / sheetRect.height) : 1
    const finishOpen = (contextSafe ?? ((callback: () => void) => callback))(() => {
      agentPanelTimelineRef.current = null
    })

    agentPanelTimelineRef.current?.kill()
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set([sheet, header, scrim, ...stageContent, ...lines, ...messageWords, originElement].filter(Boolean), {
        clearProps: 'clipPath,transform,opacity,visibility,filter,willChange,borderRadius',
      })
      finishOpen()
      return
    }

    const timeline = gsap.timeline({
      defaults: { overwrite: 'auto' },
      onComplete: finishOpen,
    })
    agentPanelTimelineRef.current = timeline

    if (hasOrigin && origin) {
      timeline
        .set(sheet, {
          x,
          y,
          scaleX,
          scaleY,
          transformOrigin: '50% 50%',
          borderRadius: origin.borderRadius,
          autoAlpha: .22,
          filter: `blur(${Math.min(7, Math.max(3, 7 - scaleX * 5))}px)`,
          willChange: 'transform,opacity,filter,border-radius',
        }, 0)
        .set([header, ...stageContent], { y: 14, autoAlpha: 0, willChange: 'transform,opacity' }, 0)
        .to(sheet, {
          x: 0,
          y: 0,
          scaleX: 1,
          scaleY: 1,
          borderRadius: '22px 22px 0 0',
          autoAlpha: 1,
          filter: 'blur(0px)',
          duration: .54,
          ease: 'expo.out',
        }, 0)
        .to(header, { y: 0, autoAlpha: 1, duration: .3, ease: 'power3.out' }, .14)
        .to(stageContent, { y: 0, autoAlpha: 1, duration: .34, ease: 'power3.out', stagger: .035 }, .2)

      if (originElement?.isConnected) {
        timeline
          .to(originElement, { scale: .94, duration: .11, ease: 'power2.inOut' }, 0)
          .to(originElement, { scale: 1, duration: .28, ease: 'back.out(1.8)', clearProps: 'transform' }, .2)
      }
    } else {
      timeline
        .fromTo(sheet, {
          y: foldedY,
          clipPath: 'inset(100% 0 0 0 round 22px 22px 0 0)',
          autoAlpha: 1,
          willChange: 'transform,clip-path',
        }, {
          y: 0,
          clipPath: 'inset(0% 0 0 0 round 22px 22px 0 0)',
          autoAlpha: 1,
          duration: .5,
          ease: 'power4.out',
        }, 0)
        .fromTo(stageContent, { y: 14, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: .32, ease: 'power3.out', stagger: .035 }, .16)
    }

    if (scrim) {
      timeline.fromTo(scrim, { autoAlpha: 0 }, { autoAlpha: 1, duration: .34, ease: 'power1.out' }, .04)
    }
    if (messageWords.length) {
      timeline.fromTo(messageWords, {
        y: 5,
        autoAlpha: 0,
        filter: `blur(5px) drop-shadow(0 0 8px rgba(${agent.bloom},.42))`,
      }, {
        y: 0,
        autoAlpha: 1,
        filter: 'blur(0px) drop-shadow(0 0 0 rgba(0,0,0,0))',
        duration: .34,
        ease: 'power3.out',
        stagger: .022,
      }, .24)
    }
    timeline.set([sheet, header, ...stageContent, ...messageWords, ...(scrim ? [scrim] : [])], {
      clearProps: 'clipPath,transform,opacity,visibility,filter,willChange,borderRadius',
    })
  }, {
    scope: portalRootRef,
    dependencies: [activeTab, agent.id, agentChatLaunch, agentChatMode, agentChatOpen, agentChatClosing],
    revertOnUpdate: true,
  })

  useGSAP((_context, contextSafe) => {
    if (!agentChatOpen || !agentChatClosing) return
    const root = portalRootRef.current
    const sheet = root?.querySelector<HTMLElement>('.ryozan-agent-chat-sheet')
    if (!root || !sheet) return

    const header = sheet.querySelector<HTMLElement>('.ryozan-agent-chat-header')
    const scrim = root.querySelector<HTMLElement>('.ryozan-agent-chat-scrim')
    const fab = root.querySelector<HTMLElement>(`button[aria-label="Open ${agent.name} chat"]`)
    const navigation = root.querySelector<HTMLElement>('nav[aria-label="Portal navigation"]')
    const stageContent = gsap.utils.toArray<HTMLElement>(
      '.ryozan-agent-chat-thread, .ryozan-agent-chat-floating-action, .ryozan-agent-chat-dock, .ryozan-agent-chat-composer',
      sheet,
    )
    const sheetRect = sheet.getBoundingClientRect()
    const navigationRect = navigation?.getBoundingClientRect()
    const foldedY = Math.max(56, (navigationRect?.top ?? root.getBoundingClientRect().bottom) - sheetRect.top + 6)
    const savedOrigin = agentChatOriginRef.current
    const originElement = agentChatLaunch === 'fab' ? fab : savedOrigin?.element
    const liveOriginRect = originElement?.isConnected ? originElement.getBoundingClientRect() : null
    const origin = liveOriginRect && liveOriginRect.width > 1 && liveOriginRect.height > 1
      ? {
          left: liveOriginRect.left,
          top: liveOriginRect.top,
          width: liveOriginRect.width,
          height: liveOriginRect.height,
          borderRadius: Number.parseFloat(window.getComputedStyle(originElement!).borderRadius) || savedOrigin?.borderRadius || 18,
        }
      : savedOrigin
    const hasOrigin = Boolean(origin && agentChatLaunch !== 'direct')
    const x = origin ? origin.left + origin.width / 2 - (sheetRect.left + sheetRect.width / 2) : 0
    const y = origin ? origin.top + origin.height / 2 - (sheetRect.top + sheetRect.height / 2) : foldedY
    const scaleX = origin ? gsap.utils.clamp(.08, .88, origin.width / sheetRect.width) : 1
    const scaleY = origin ? gsap.utils.clamp(.045, .62, origin.height / sheetRect.height) : 1
    const finishClose = (contextSafe ?? ((callback: () => void) => callback))(() => finalizeAgentChatClose())

    agentPanelTimelineRef.current?.kill()
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      finishClose()
      return
    }

    const timeline = gsap.timeline({ defaults: { overwrite: 'auto' }, onComplete: finishClose })
    agentPanelTimelineRef.current = timeline
    timeline
      .to(stageContent, { y: 10, autoAlpha: 0, duration: .16, ease: 'power2.in', stagger: .018 }, 0)
      .to(header, { y: -8, autoAlpha: 0, duration: .18, ease: 'power2.in' }, .04)

    if (scrim) timeline.to(scrim, { autoAlpha: 0, duration: .24, ease: 'power1.in' }, 0)

    if (hasOrigin && origin) {
      timeline.to(sheet, {
        x,
        y,
        scaleX,
        scaleY,
        transformOrigin: '50% 50%',
        borderRadius: origin.borderRadius,
        autoAlpha: .08,
        filter: 'blur(5px)',
        duration: .38,
        ease: 'power4.in',
        willChange: 'transform,opacity,filter,border-radius',
      }, .06)
      if (originElement?.isConnected) {
        timeline.fromTo(originElement, { scale: .96 }, { scale: 1, duration: .24, ease: 'back.out(1.7)', clearProps: 'transform' }, .24)
      }
    } else {
      timeline.to(sheet, {
        y: foldedY,
        clipPath: 'inset(100% 0 0 0 round 22px 22px 0 0)',
        autoAlpha: 1,
        duration: .38,
        ease: 'power4.in',
        willChange: 'transform,clip-path',
      }, .06)
    }
  }, {
    scope: portalRootRef,
    dependencies: [agent.id, agentChatClosing, agentChatLaunch, agentChatMode, agentChatOpen, finalizeAgentChatClose],
    revertOnUpdate: true,
  })

  useEffect(() => {
    if (agentChatOpen) return
    const navigation = portalRootRef.current?.querySelector<HTMLElement>('nav[aria-label="Portal navigation"]')
    const navigationItems = navigation ? gsap.utils.toArray<HTMLElement>(':scope > button', navigation) : []
    const fab = portalRootRef.current?.querySelector<HTMLElement>(`button[aria-label="Open ${agent.name} chat"]`)
    gsap.set([...navigationItems, fab].filter(Boolean), { clearProps: 'transform,opacity,visibility,willChange' })
  }, [agent.name, agentChatOpen])

  useGSAP(() => {
    if (!agentChatOpen || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const sheet = portalRootRef.current?.querySelector<HTMLElement>('.ryozan-agent-chat-sheet')
    if (!sheet) return

    const stateSelector = [
      '.ryozan-agent-chat-line',
      '.ryozan-agent-tool-layer',
      '.ryozan-agent-tools > div:last-child',
      '.ryozan-hub-workflow-drop',
      '.ryozan-hub-workflow-form',
      '.ryozan-hub-workflow-summary',
      '.ryozan-hub-option-list',
      '.flash-sheets-view',
      '.flash-review-view',
    ].join(',')
    const known = new WeakSet<Element>()
    const tweens: gsap.core.Tween[] = []
    sheet.querySelectorAll(stateSelector).forEach((element) => known.add(element))

    const animateState = (element: HTMLElement) => {
      if (known.has(element)) return
      known.add(element)
      const tween = gsap.fromTo(element, {
        y: 18,
        scale: 0.992,
        autoAlpha: 0,
        willChange: 'transform,opacity',
      }, {
        y: 0,
        scale: 1,
        autoAlpha: 1,
        duration: 0.42,
        delay: 0.025,
        ease: 'power3.out',
        overwrite: 'auto',
        clearProps: 'transform,opacity,visibility,willChange',
      })
      tweens.push(tween)
      if (element.matches('.ryozan-agent-chat-line')) {
        const words = gsap.utils.toArray<HTMLElement>('.ryozan-agent-message-word', element)
        if (words.length) {
          tweens.push(gsap.fromTo(words, {
            y: 7,
            autoAlpha: 0,
            filter: `blur(6px) drop-shadow(0 0 9px rgba(${agent.bloom},.52))`,
            willChange: 'transform,opacity,filter',
          }, {
            y: 0,
            autoAlpha: 1,
            filter: 'blur(0px) drop-shadow(0 0 0 rgba(0,0,0,0))',
            duration: 0.46,
            delay: 0.08,
            stagger: 0.032,
            ease: 'power3.out',
            clearProps: 'transform,opacity,visibility,filter,willChange',
          }))
        }
      }
    }

    const observer = new MutationObserver((records) => {
      records.forEach((record) => {
        record.addedNodes.forEach((node) => {
          if (!(node instanceof HTMLElement)) return
          if (node.matches(stateSelector)) {
            animateState(node)
            return
          }
          node.querySelectorAll<HTMLElement>(stateSelector).forEach((candidate) => {
            if (!candidate.parentElement?.closest(stateSelector)) animateState(candidate)
          })
        })
      })
    })
    observer.observe(sheet, { childList: true, subtree: true })

    return () => {
      observer.disconnect()
      tweens.forEach((tween) => tween.kill())
    }
  }, { scope: portalRootRef, dependencies: [agent.bloom, agentChatOpen], revertOnUpdate: true })

  useGSAP(() => {
    if (!agentChatOpen || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const sheet = portalRootRef.current?.querySelector<HTMLElement>('.ryozan-agent-chat-sheet')
    if (!sheet) return

    const thinkingMark = sheet.querySelector<HTMLElement>('.ryozan-agent-thinking-mark')
    if (agentChatWorking && thinkingMark) {
      const timeline = gsap.timeline({ repeat: -1, defaults: { overwrite: 'auto' } })
        .set(thinkingMark, { transformOrigin: '50% 50%', willChange: 'transform,filter' })
        .to(thinkingMark, { rotation: '+=360', duration: 0.52, ease: 'power2.inOut' })
        .to(thinkingMark, { duration: 0.3 })
        .to(thinkingMark, { rotation: '+=360', duration: 0.52, ease: 'power2.inOut' })
        .to(thinkingMark, { duration: 0.48 })
      return () => timeline.kill()
    }

    if (!latestDisplayedLine?.animateReply) return
    const readyLine = Array.from(sheet.querySelectorAll<HTMLElement>('[data-chat-line-id]'))
      .find((line) => line.dataset.chatLineId === latestDisplayedLine.id)
    const readyMark = readyLine?.querySelector<HTMLElement>(':scope > span[aria-hidden="true"]')
    if (!readyMark) return

    gsap.fromTo(readyMark, {
      scale: 0.92,
      filter: `drop-shadow(0 0 2px rgba(${agent.bloom},.18))`,
      willChange: 'transform,filter',
    }, {
      scale: 1.22,
      filter: `drop-shadow(0 0 13px rgba(${agent.bloom},.92))`,
      duration: 0.22,
      ease: 'power2.inOut',
      repeat: 3,
      yoyo: true,
      clearProps: 'transform,filter,willChange',
    })
  }, {
    scope: portalRootRef,
    dependencies: [agent.bloom, agentChatOpen, agentChatWorking, latestDisplayedLine?.id],
    revertOnUpdate: true,
  })

  useGSAP(() => {
    if (!agentToolsOpen || !agentToolsClosing) return
    const tools = portalRootRef.current?.querySelector<HTMLElement>('.ryozan-agent-tools')
    if (!tools) {
      finalizeAgentToolsClose()
      return
    }
    const layer = tools.querySelector<HTMLElement>('.ryozan-agent-tool-layer')
    const tabs = tools.querySelector<HTMLElement>(':scope > div:last-child')
    const targets = [layer, tabs].filter((target): target is HTMLElement => Boolean(target))
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !targets.length) {
      finalizeAgentToolsClose()
      return
    }
    gsap.to(targets, {
      y: 18,
      autoAlpha: 0,
      duration: 0.22,
      ease: 'power2.in',
      stagger: 0.025,
      overwrite: 'auto',
      onComplete: finalizeAgentToolsClose,
    })
  }, {
    scope: portalRootRef,
    dependencies: [agentToolsClosing, agentToolsOpen, finalizeAgentToolsClose],
    revertOnUpdate: true,
  })

  const castAgentCard = (card: AgentActionCard) => {
    if (agent.id === 'xar' && card.id === 'artist-connections') {
      openArtistConnections()
      return
    }
    const currentLines = agentThreads[agent.id] ?? [{ id: `${agent.id}-opener`, from: 'agent' as const, body: agent.opener }]
    const castLine: ChatLine = {
      id: `${agent.id}-cast-${Date.now()}`,
      from: 'user',
      presentation: 'card',
      body: <ActionCard card={card} agentName={agent.name} accent={agent.accent} accentRgb={agent.bloom} />,
    }

    if (agent.id === 'gala' && card.id === 'report-up') {
      if (agentChatWorking) return
      setAgentThreads((threads) => ({ ...threads, gala: [...currentLines, castLine] }))
      setAgentChatWorking(true)
      const requestId = ++agentChatRequestRef.current
      void galaApi.chat({
        message: 'Pull the current Ryozan business overview.',
        action_intent: 'business_stats.read',
        context: {
          portal_tab: activeTab,
          agent_id: 'gala',
          agent_name: 'Gala',
        },
      }).then((response) => {
        if (requestId !== agentChatRequestRef.current) return
        const receiptLine: ChatLine = {
          id: `gala-report-${Date.now()}`,
          from: 'agent',
          body: <GalaStructuredReply response={response} />,
          animateReply: true,
        }
        setAgentThreads((threads) => ({
          ...threads,
          gala: [...(threads.gala ?? [...currentLines, castLine]), receiptLine],
        }))
      }).catch(() => {
        if (requestId !== agentChatRequestRef.current) return
        setAgentThreads((threads) => ({
          ...threads,
          gala: [...(threads.gala ?? [...currentLines, castLine]), {
            id: `gala-report-error-${Date.now()}`,
            from: 'agent',
            body: 'The business report failed. No success receipt was created.',
            animateReply: true,
          }],
        }))
      }).finally(() => {
        if (requestId === agentChatRequestRef.current) setAgentChatWorking(false)
      })
      return
    }

    if (hubWorkflowActive) {
      setHubWorkflowLines((lines) => [...lines, castLine])
    } else if (bookyDebriefActive) {
      setBookyDebriefLines((lines) => [...lines, castLine])
    } else {
      setAgentThreads((threads) => ({ ...threads, [agent.id]: [...currentLines, castLine] }))
    }
  }

  const uploadToAgent = async (file: File) => {
    const requestAgent = agent
    const requestIsDebrief = requestAgent.id === 'booky' && agentChatMode !== 'full'
    const requestIsContextWorkflow = (activeTab === 'hub' || activeTab === 'club') && agentChatMode === 'hub-workflow-full'
    const uploadLineId = `${requestAgent.id}-upload-${Date.now()}`
    const uploadLine: ChatLine = { id: uploadLineId, from: 'user', body: `Uploading ${file.name}...` }
    const updateUploadLine = (body: string) => {
      const update = (lines: ChatLine[]) => lines.map((line) => line.id === uploadLineId ? { ...line, body } : line)
      if (requestIsContextWorkflow) {
        setHubWorkflowLines(update)
      } else if (requestIsDebrief) {
        setBookyDebriefLines(update)
      } else {
        setAgentThreads((threads) => ({ ...threads, [requestAgent.id]: update(threads[requestAgent.id] ?? []) }))
      }
    }

    if (selectedHubWorkflow === 'flash' && !hasAuthorizedFlashTarget) {
      const deniedLine: ChatLine = {
        id: uploadLineId,
        from: 'user',
        body: 'Flash upload needs an authorized artist target. Choose an assigned artist before trying again.',
      }
      if (requestIsContextWorkflow) {
        setHubWorkflowLines((lines) => [...lines, deniedLine])
      } else if (requestIsDebrief) {
        setBookyDebriefLines((lines) => [...lines, deniedLine])
      } else {
        setAgentThreads((threads) => ({
          ...threads,
          [requestAgent.id]: [...(threads[requestAgent.id] ?? [{ id: `${requestAgent.id}-opener`, from: 'agent' as const, body: requestAgent.opener }]), deniedLine],
        }))
      }
      return
    }

    if (requestIsContextWorkflow) {
      setHubWorkflowLines((lines) => [...lines, uploadLine])
    } else if (requestIsDebrief) {
      setBookyDebriefLines((lines) => [...lines, uploadLine])
    } else {
      setAgentThreads((threads) => ({
        ...threads,
        [requestAgent.id]: [...(threads[requestAgent.id] ?? [{ id: `${requestAgent.id}-opener`, from: 'agent' as const, body: requestAgent.opener }]), uploadLine],
      }))
    }
    setAgentToolsOpen(false)

    try {
      const result = await galaUpload({
        file,
        category: selectedHubWorkflow === 'flash' ? 'flash' : selectedHubWorkflow === 'gallery' ? 'portfolio' : 'other',
        note: `Agent Tools upload for ${requestAgent.name} from the ${activeTab} portal${selectedHubWorkflow ? `, ${selectedHubWorkflow} workflow` : ''}.`,
      })
      if (result.status !== 'ok' || !result.url) {
        throw new Error('The server did not return a storage receipt.')
      }
      pendingAgentUploadsRef.current[requestAgent.id] = [
        ...(pendingAgentUploadsRef.current[requestAgent.id] ?? []),
        { name: file.name, url: result.url },
      ]
      updateUploadLine(`${result.message} Storage receipt confirmed in ${result.category}. It will be attached to your next message.`)
    } catch (error) {
      const reason = error instanceof ApiError ? error.message : 'Upload failed'
      updateUploadLine(`Upload failed for ${file.name}. Nothing was attached. ${reason}`)
    }
  }

  const sendAgentMessage = async () => {
    const text = agentChatDraft.trim()
    if (!text || agentChatWorking) return

    const requestAgent = agent
    const requestUploads = [...(pendingAgentUploadsRef.current[requestAgent.id] ?? [])]
    const messageWithUploads = requestUploads.length > 0
      ? `${text}\n\nAttached upload references:\n${requestUploads.map((upload) => `- ${upload.name}: ${upload.url}`).join('\n')}`
      : text
    const requestIsDebrief = requestAgent.id === 'booky' && agentChatMode !== 'full'
    const requestIsContextWorkflow = (activeTab === 'hub' || activeTab === 'club') && agentChatMode === 'hub-workflow-full'
    const currentLines = requestIsContextWorkflow
      ? hubWorkflowLines
      : requestIsDebrief
      ? bookyDebriefLines
      : agentThreads[requestAgent.id] ?? [{ id: `${requestAgent.id}-opener`, from: 'agent' as const, body: requestAgent.opener }]
    const userLine: ChatLine = { id: `${requestAgent.id}-user-${Date.now()}`, from: 'user', body: text }
    if (requestIsContextWorkflow) {
      setHubWorkflowLines((lines) => [...lines, userLine])
    } else if (requestIsDebrief) {
      setBookyDebriefLines((lines) => [...lines, userLine])
    } else {
      setAgentThreads((threads) => ({ ...threads, [requestAgent.id]: [...currentLines, userLine] }))
    }
    setAgentChatDraft('')
    setAgentChatWorking(true)
    const requestId = ++agentChatRequestRef.current

    try {
      const response = await galaApi.chat({
        message: messageWithUploads,
        history: currentLines
          .filter((line): line is ChatLine & { body: string } => typeof line.body === 'string')
          .slice(-20)
          .map((line) => ({ role: line.from === 'agent' ? 'assistant' as const : 'user' as const, content: line.body })),
        context: artistScrollWorkflowActive ? {
          agent_id: 'spyder',
          hub_workflow: 'artist-scroll',
        } : {
          user_name: user?.name,
          user_role: role,
          current_path: window.location.pathname,
          portal_tab: activeTab,
          agent_id: requestAgent.id,
          agent_name: requestAgent.name,
          agent_role: requestAgent.role,
          conversation_id: (requestAgent.id === 'mercury' || requestAgent.id === 'pulse') ? selectedMercuryConversationId : undefined,
          conversation_name: (requestAgent.id === 'mercury' || requestAgent.id === 'pulse') && selectedMercuryConversation ? conversationTitle(selectedMercuryConversation) : undefined,
          selected_date: requestIsDebrief ? selectedBookyDate : undefined,
          booking_id: requestIsDebrief ? selectedBookyBooking?.id : undefined,
          booking_reference: requestIsDebrief ? selectedBookyBooking?.reference_code : undefined,
          hub_workflow: requestIsContextWorkflow ? selectedHubWorkflow : undefined,
          instruction: `Operate as ${requestAgent.name}, the ${requestAgent.role}. Stay inside the current Ryozan portal context. Use available live data and do not invent missing records.${(requestAgent.id === 'mercury' || requestAgent.id === 'pulse') && selectedMercuryConversationId ? ' Assist with the selected Courier thread and preserve its real record as the source of truth.' : ''}${requestIsDebrief && selectedBookyBooking ? ' Answer specifically about the selected booking inquiry and preserve its reference as the source of truth.' : requestIsDebrief ? ' Answer specifically about the selected calendar day.' : ''}${requestIsContextWorkflow ? ` Answer specifically about the active portal workflow and preserve staged versus persisted state. ${HUB_UPLOAD_AGENT_CONTEXT[selectedHubWorkflow as HubWorkflowKind] ?? ''}` : ''}`,
        },
      })
      if (requestId !== agentChatRequestRef.current) return
      if (requestUploads.length > 0) {
        const attachedUrls = new Set(requestUploads.map((upload) => upload.url))
        pendingAgentUploadsRef.current[requestAgent.id] = (pendingAgentUploadsRef.current[requestAgent.id] ?? [])
          .filter((upload) => !attachedUrls.has(upload.url))
      }
      const agentLine: ChatLine = {
        id: `${requestAgent.id}-agent-${Date.now()}`,
        from: 'agent',
        body: <GalaStructuredReply response={response} />,
        animateReply: true,
      }
      if (requestIsContextWorkflow) {
        setHubWorkflowLines((lines) => [...lines, agentLine])
      } else if (requestIsDebrief) {
        setBookyDebriefLines((lines) => [...lines, agentLine])
      } else {
        setAgentThreads((threads) => ({
          ...threads,
          [requestAgent.id]: [...(threads[requestAgent.id] ?? [...currentLines, userLine]), agentLine],
        }))
      }
    } catch {
      if (requestId !== agentChatRequestRef.current) return
      const errorLine: ChatLine = {
        id: `${requestAgent.id}-error-${Date.now()}`,
        from: 'agent',
        body: 'I could not reach the Ryozan assistant service. Your message is still here. Try again in a moment.',
        animateReply: true,
      }
      if (requestIsContextWorkflow) {
        setHubWorkflowLines((lines) => [...lines, errorLine])
      } else if (requestIsDebrief) {
        setBookyDebriefLines((lines) => [...lines, errorLine])
      } else {
        setAgentThreads((threads) => ({
          ...threads,
          [requestAgent.id]: [...(threads[requestAgent.id] ?? [...currentLines, userLine]), errorLine],
        }))
      }
    } finally {
      if (requestId === agentChatRequestRef.current) setAgentChatWorking(false)
    }
  }

  const [approvedBookings, setApprovedBookings] = useState<Record<string, Booking>>({})

  // "Message to coordinate" leaves the Booky card for the booking's own thread.
  const messageBookingThread = async (booking: Booking) => {
    try {
      const conversationId = booking.conversation_id
        || (await bookingsApi.ensureConversation(booking.id)).conversation_id
      const rows = conversationRows.some((row) => row.id === conversationId)
        ? conversationRows
        : (await conversationsApi.list()).conversations
      const conversation = rows.find((row) => row.id === conversationId)
      if (conversation) {
        setConversationRows(rows)
        await openConversation(conversation)
        return
      }
      navigate(`${ROUTES.COURIER}?convo=${encodeURIComponent(conversationId)}`)
    } catch {
      navigate(ROUTES.COURIER)
    }
  }

  const openBookyBooking = (listedBooking: Booking, trigger: HTMLButtonElement) => {
    // The Books list comes from the parent load; an approval made here must win
    // over that stale row so reopening the card cannot approve twice.
    const booking = approvedBookings[listedBooking.id] ?? listedBooking
    if (selectedBookyBookingId === booking.id && agentChatOpen && agentChatMode === 'booky-debrief-full') {
      requestAgentChatClose()
      return
    }

    const context: NonNullable<Conversation['booking_context']> = {
      id: booking.id,
      reference_code: booking.reference_code,
      status: booking.status,
      client_name: booking.resolved_name || booking.client_name || booking.guest_name,
      client_email: booking.resolved_email || booking.guest_email,
      client_phone: booking.resolved_phone || booking.guest_phone,
      artist_name: booking.artist_name,
      description: booking.description,
      reference_urls: booking.reference_urls ?? [],
      booking_kind: booking.booking_kind ?? 'tattoo_session',
      consultation_method: booking.consultation_method ?? null,
      placement: booking.placement,
      style_preference: booking.style_preference,
      preferred_date: booking.preferred_date,
      preferred_time: booking.preferred_time,
      preferred_slots: booking.preferred_slots,
      scheduled_date: booking.scheduled_date,
      scheduled_time: booking.scheduled_time,
      booking_url: booking.reference_code ? dynamicRoutes.bookingRef(booking.reference_code) : ROUTES.BOOKINGS,
    }

    setBookyDebriefLines([{
      id: `booky-booking-${booking.id}`,
      from: 'agent',
      presentation: 'card',
      body: (
        <section className="ryozan-booky-booking-card" aria-label={`${bookingTitle(booking)} booking inquiry`}>
          <CourierBookingInquiryCard
            booking={booking}
            context={context}
            threadName={bookingTitle(booking)}
            participantCount={undefined}
            loading={false}
            onOpenImage={(url) => window.open(url, '_blank', 'noopener,noreferrer')}
          >
            {canApproveBooking(role, user?.id, booking.artist_id) && isApprovableStatus(booking.status) && (
              <BookingApprovalPanel
                booking={booking}
                onApproved={(result) => setApprovedBookings((prev) => ({ ...prev, [booking.id]: result.booking }))}
                onMessage={() => void messageBookingThread(booking)}
                messageLabel="Message to coordinate a time"
              />
            )}
          </CourierBookingInquiryCard>
        </section>
      ),
    }])
    captureAgentChatOrigin(trigger)
    setAgentOverrideId(null)
    setAgentChatMode('booky-debrief-full')
    setSelectedBookyDate(booking.scheduled_date ?? booking.preferred_date)
    setSelectedBookyBookingId(booking.id)
    setAgentToolsOpen(false)
    setAgentChatOpen(true)

    const params = new URLSearchParams(location.search)
    if (location.pathname !== ROUTES.DASHBOARD || params.has('charm') || params.has('ref')) {
      params.set('portal', 'books')
      params.delete('charm')
      params.delete('ref')
      navigate(`${ROUTES.DASHBOARD}?${params.toString()}`, { replace: true })
    }
  }

  const openBookyDebrief = useCallback((dateKey: string, calendar: HTMLDivElement) => {
    if (selectedBookyDate === dateKey && agentChatOpen && agentChatMode === 'booky-debrief-full') {
      requestAgentChatClose()
      return
    }

    const dateLabel = new Date(`${dateKey}T12:00:00`).toLocaleDateString('en-US', {
      weekday: 'long',
      month: 'short',
      day: 'numeric',
    })
    const dayBookings = bookings
      .filter((booking) => booking.scheduled_date === dateKey)
      .sort((a, b) => bookingTimestamp(a) - bookingTimestamp(b))

    const debriefLines: ChatLine[] = [{
      id: `booky-${dateKey}-brief`,
      from: 'agent',
      presentation: 'card',
      body: <DailyDebriefCard dateLabel={dateLabel} bookings={dayBookings} />,
    }]

    captureAgentChatOrigin(calendar)
    setBookyDebriefLines(debriefLines)
    setAgentOverrideId(null)
    setAgentChatMode('booky-debrief-full')
    setSelectedBookyDate(dateKey)
    setSelectedBookyBookingId(null)
    setAgentToolsOpen(false)
    setAgentChatOpen(true)
  }, [agentChatMode, agentChatOpen, bookings, captureAgentChatOrigin, requestAgentChatClose, selectedBookyDate])

  useEffect(() => {
    if (!pendingTodayDebrief || activeTab !== 'books') return

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let debriefTimer = 0
    const cueTimer = window.setTimeout(() => {
      setCalendarCueDate(todayKey)
      debriefTimer = window.setTimeout(() => {
        const calendar = document.querySelector<HTMLDivElement>('[data-portal-calendar]')
        if (calendar) openBookyDebrief(todayKey, calendar)
        setCalendarCueDate(null)
        setPendingTodayDebrief(false)
      }, reducedMotion ? 0 : 900)
    }, reducedMotion ? 0 : 900)

    return () => {
      window.clearTimeout(cueTimer)
      window.clearTimeout(debriefTimer)
    }
  }, [activeTab, openBookyDebrief, pendingTodayDebrief, todayKey])

  const openHubWorkflow = (kind: HubWorkflowKind, anchor: HTMLButtonElement) => {
    if (!canUseArtistHub) return
    if (kind === 'flash' && !hasAuthorizedFlashTarget) return
    if (selectedHubWorkflow === kind && agentChatOpen && agentChatMode === 'hub-workflow-full') {
      requestAgentChatClose()
      return
    }

    const body = kind === 'flash'
      ? <FlashWorkflowCard artistId={artistProfile?.id} />
      : kind === 'merch'
        ? <MerchWorkflowCard onOpenCrafts={() => navigate(`${ROUTES.HUSTLE}?stash=crafts`)} />
      : kind === 'gallery' && artistProfile
        ? <GalleryWorkflowCard
          initialProfile={artistProfile}
          onProfileChange={(nextProfile) => {
            setArtistProfile(nextProfile)
            setProfileState('ready')
          }}
        />
        : kind === 'pos'
          ? <POSWorkflowCard onClose={requestAgentChatClose} onOpenConnections={canUseArtistConnections ? () => openArtistConnections() : undefined} />
        : kind === 'codex'
          ? <CodexWorkflowCard />
        : kind === 'artist-scroll'
          ? <ArtistScrollWorkflowCard artistName={user?.name || 'Artist'} preview={artistPreview} />
        : kind === 'email-foundry'
          ? <EmailFoundryFrame surface="mobile" />
          : <OptionsWorkflowCard
            isFounderOrAdmin={isFounderOrAdmin}
            canManageConnections={canUseArtistConnections}
            onNavigate={(target) => {
              if (target === 'account') openAccountSettings()
              else if (target === 'connections') openArtistConnections()
              else if (target === 'email-foundry') openHubWorkflow('email-foundry', anchor)
              else openHubConfiguration(target)
            }}
          />

    captureAgentChatOrigin(anchor)
    setHubWorkflowLines([{
      id: `hub-${kind}-${Date.now()}`,
      from: 'agent',
      presentation: 'card',
      body,
    }])
    setSelectedHubWorkflow(kind)
    setAgentChatMode('hub-workflow-full')
    setAgentToolsOpen(false)
    setAgentChatOpen(true)
  }

  const openClubWorkflow = (kind: ClubWorkflowKind, anchor: HTMLButtonElement) => {
    if (selectedHubWorkflow === kind && agentChatOpen && agentChatMode === 'hub-workflow-full') {
      requestAgentChatClose()
      return
    }

    const returnToHerald = () => {
      setAgentChatMode('full')
      setSelectedHubWorkflow(null)
      setHubWorkflowLines([])
    }
    const body = kind === 'loot'
      ? <ClubLootCard quests={questRows} onClaimQuest={claimClubQuest} />
      : kind === 'campaign'
        ? <ClubCampaignCard collectorId={user?.id ?? 'collector-preview'} onOpenHerald={returnToHerald} />
        : <FounderHeraldContentDesk onBriefHerald={() => setAgentChatMode('hub-workflow-full')} />

    captureAgentChatOrigin(anchor)
    setHubWorkflowLines([{
      id: `club-${kind}-${Date.now()}`,
      from: 'agent',
      presentation: 'card',
      body,
    }])
    setSelectedHubWorkflow(kind)
    setAgentChatMode('hub-workflow-full')
    setAgentToolsOpen(false)
    setAgentChatOpen(true)
  }

  const completeTabTransition = useCallback(() => {
    if (!pendingTab) return
    const nextTab = pendingTab
    setActiveTab(nextTab)
    setPendingTab(null)
    navigate(`${ROUTES.DASHBOARD}?portal=${encodeURIComponent(nextTab)}`, { replace: true })
    window.requestAnimationFrame(() => {
      portalRootRef.current?.querySelector<HTMLElement>('.ryozan-portal-scroll')?.scrollTo({ top: 0 })
    })
  }, [navigate, pendingTab])

  function selectTab(id: string) {
    if (id === activeTab || pendingTab) return
    setAccountOpen(false)
    setAgentOverrideId(null)
    setAgentChatOpen(false)
    setAgentChatClosing(false)
    setAgentToolsOpen(false)
    setAgentChatMode('full')
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow(null)
    setHubSurface('home')
    // Leaving the tab closes an open collector thread, so the INBOX list is
    // what comes back on return.
    if (collectorThreadId) closeCollectorThread()
    if (id === 'table') loadConversations()
    if (id === 'hub' && canUseArtistHub) loadArtistProfile()
    if (id === 'club') loadClub()
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setActiveTab(id)
      navigate(`${ROUTES.DASHBOARD}?portal=${encodeURIComponent(id)}`, { replace: true })
      window.requestAnimationFrame(() => {
        portalRootRef.current?.querySelector<HTMLElement>('.ryozan-portal-scroll')?.scrollTo({ top: 0 })
      })
      return
    }
    setPendingTab(id)
  }

  const openPageAgent = canUsePortalAi ? () => {
    setAccountOpen(false)
    agentChatOriginRef.current = null
    setAgentChatLaunch('fab')
    setAgentChatMode('full')
    if (activeTab === 'table') {
      mercuryThreadRequestRef.current += 1
      setSelectedMercuryConversationId(null)
      setAgentThreads((threads) => ({
        ...threads,
        mercury: [{ id: 'mercury-opener', from: 'agent', body: pageAgent.opener }],
      }))
      navigate(`${ROUTES.DASHBOARD}?portal=table`, { replace: true })
    }
    setSelectedBookyDate(null)
    setSelectedBookyBookingId(null)
    setSelectedHubWorkflow(null)
    setAgentToolsOpen(false)
    setAgentChatOpen(true)
  } : undefined
  // A client books through the page agent: the signed-in booking flow belongs to
  // the studio's own artists, and the mobile portal has no separate booking tab.
  const openBooking = () => isCollector && openPageAgent ? openPageAgent() : navigate(isCollector ? ROUTES.BOOK : ROUTES.BOOKINGS)

  let page: ReactNode
  if (isCollector && activeTab === 'wall') {
    page = (
      <>
        <PageTitle action={accountAction}>Shop</PageTitle>
        <div className="ryozan-collector-shop-links" data-portal-enter>
          <span>Flash from the card table</span>
        </div>
        <Suspense fallback={<PortalContentSkeleton variant="panel" label="Loading the shop" />}>
          <CollectorShop />
        </Suspense>
      </>
    )
  } else if (isCollector && activeTab === 'you') {
    page = (
      <CollectorHuddlPage
        bookings={bookings}
        nowMs={now}
        selectedDate={huddlDate}
        pass={huddlPass}
        onSelectDate={(dateKey, calendar) => {
          setSelectedHuddlDate(dateKey)
          openBookyDebrief(dateKey, calendar)
        }}
        onBook={openBooking}
        headerAction={accountAction}
      />
    )
  } else if (activeTab === 'books') {
    page = <BooksPage bookings={bookings} loading={loading} loadError={loadError} nowMs={now} selectedDate={selectedBookyDate} cueDate={calendarCueDate} onOpenDay={openBookyDebrief} onOpenBooking={openBookyBooking} onOpenBooks={() => navigate(ROUTES.BOOKINGS)} headerAction={accountAction} />
  } else if (activeTab === 'table') {
    page = collectorThread ? (
      <CollectorThreadPage
        key={collectorThread.id}
        conversation={collectorThread}
        messages={collectorThreadMessages}
        state={collectorThreadState}
        currentUserId={user?.id}
        draft={collectorDraft}
        sending={collectorSending}
        sendError={collectorSendError}
        onDraftChange={setCollectorDraft}
        onSend={() => void sendCollectorMessage()}
        onRetry={() => void loadCollectorThread(collectorThread.id, { showLoading: true })}
      />
    ) : (
      <InboxPage conversations={conversationRows} state={conversationState} query={courierQuery} onOpenConversation={(conversation, trigger) => void openConversation(conversation, trigger)} onDeleteConversation={deleteMercuryConversation} onCreateMemory={createMercuryMemorySuggestion} canCreateMemory={canUsePortalAi} />
    )
  } else if (activeTab === 'hub') {
    page = !canUseArtistHub ? (
      <HubUnavailablePage headerAction={accountAction} />
    ) : hubSurface === 'codex' ? (
      <CodexPage
        onBack={() => {
          setHubSurface('home')
          navigate(`${ROUTES.DASHBOARD}?portal=hub`, { replace: true })
        }}
        headerAction={accountAction}
      />
    ) : hubSurface === 'evolution' ? (
      <AgentEvolutionStudio
        onBack={() => {
          setHubSurface('home')
          navigate(`${ROUTES.DASHBOARD}?portal=hub`, { replace: true })
        }}
      />
    ) : (
      <HubPage
        profile={artistProfile}
        state={profileState}
        onRetry={loadArtistProfile}
        onOpenFlash={(anchor) => openHubWorkflow('flash', anchor)}
        canUploadFlash={hasAuthorizedFlashTarget}
        onOpenMerch={(anchor) => openHubWorkflow('merch', anchor)}
        onOpenConnections={(anchor) => openArtistConnections(anchor)}
        canManageConnections={canUseArtistConnections}
        onOpenDeposits={(anchor) => openArtistDeposits(anchor)}
        canViewDeposits={canViewArtistDeposits}
        onOpenPos={(anchor) => openHubWorkflow('pos', anchor)}
        onOpenOptions={(anchor) => openHubWorkflow('options', anchor)}
        onOpenCodex={(anchor) => openHubWorkflow('codex', anchor)}
        onOpenAgentEvolution={() => {
          setHubSurface('evolution')
          setSelectedHubWorkflow(null)
          setAgentChatOpen(false)
          navigate(`${ROUTES.DASHBOARD}?portal=hub&panel=agents`, { replace: true })
        }}
        canManageAgents={user?.role === 'founder' || user?.role === 'admin'}
        onEditProfile={() => navigate(`${ROUTES.ARTIST_PROFILE}?tab=settings`)}
        onOpenBio={() => navigate(`${ROUTES.ARTIST_PROFILE}?tab=profile`)}
        onOpenSpecialties={() => navigate(`${ROUTES.ARTIST_PROFILE}?tab=profile`)}
        onOpenSocials={() => navigate(`${ROUTES.ARTIST_PROFILE}?tab=contact`)}
        onOpenGallery={(anchor) => openHubWorkflow('gallery', anchor)}
        headerAction={accountAction}
      />
    )
  } else if (activeTab === 'club') {
    page = (
      <ClubPage
        quests={questRows}
        broadcasts={clubRows}
        membership={membership}
        state={clubState}
        heraldState={heraldState}
        onOpenClub={() => {
          captureAgentChatOrigin()
          setAccountOpen(false)
          setAgentChatMode('full')
          setSelectedHubWorkflow(null)
          setAgentToolsOpen(false)
          setAgentChatOpen(true)
        }}
        onOpenWorkflow={openClubWorkflow}
        headerAction={accountAction}
        collectorView={isCollector}
        founderView={user?.role === 'founder' && !isCollector}
        profileName={user?.name || 'Ryozan member'}
        profileAvatar={user?.avatar_url ?? null}
        ryoBalance={ryoBalance}
      />
    )
  } else {
    page = (
      <div className="ryozan-home-screen">
        <HomeScreen
          role={isCollector ? 'collector' : 'staff'}
          title="Home"
          meta={meta}
          headerAction={accountAction}
          pass={pass}
          galaLine={isCollector ? undefined : galaLine}
          galaGlyph={isCollector ? undefined : isFounderOrAdmin ? '✦' : <SpyderMark size={15} />}
          today={isCollector ? undefined : schedule}
        />
      </div>
    )
  }

  const agentToolsPanel = (
    <AgentTools
      open={agentToolsOpen}
      onClose={requestAgentToolsClose}
      agentName={agent.name}
      agentGlyph={agent.glyph}
      accent={agent.accent}
      accentRgb={agent.bloom}
      cards={agentCardHands[agent.id] ?? agentCardHands.gala}
      onCast={castAgentCard}
      onUpload={uploadToAgent}
      memory={agentThreads[agent.id]?.filter((line): line is ChatLine & { body: string } => line.from === 'user' && typeof line.body === 'string').map((line) => ({ id: line.id, text: line.body, source: 'this session' })) ?? []}
      docked
      scrimTop="calc(12% + 73px)"
    />
  )

  const realRole = user?.role ?? role
  const roleLabels: Record<PortalRole, string> = {
    founder: 'Founder · full access',
    admin: 'Admin · full access',
    artist: 'Artist',
    assistant: 'Assistant',
    collector: 'Collector',
  }
  const viewRoles: Array<{ label: string; mode: ViewAsMode }> = [
    { label: 'Founder', mode: null },
    { label: 'Admin', mode: 'admin' },
    { label: 'Artist', mode: 'artist' },
    { label: 'Collector', mode: 'collector' },
  ]
  const applyViewAs = (mode: ViewAsMode) => {
    setViewAs(mode)
    setAccountOpen(false)
    setAgentOverrideId(null)
    setAgentChatOpen(false)
    setAgentToolsOpen(false)
    setPendingTab(null)
    setActiveTab('home')
    navigate(`${ROUTES.DASHBOARD}?portal=home`, { replace: true })
  }
  const signOut = () => {
    closeAccount()
    logout()
    navigate(ROUTES.PORTAL, { replace: true })
  }
  const accountOverlay = accountOpen ? (
    <>
      <div
        className="ryozan-account-menu-scrim"
        aria-hidden="true"
        onClick={closeAccount}
      />
      <div ref={accountMenuRef} className="ryozan-account-menu-position">
        <AccountMenu
          className="ryozan-account-menu"
          userName={user?.name || 'Ryozan member'}
          roleLabel={roleLabels[realRole]}
          avatarSrc={user?.avatar_url || '/assets/brand/ryozan-mark.svg'}
          rows={[
            { id: 'account-settings', label: 'Account settings', value: 'Open', onSelect: openAccountSettings },
            ...(canUseArtistConnections ? [{ id: 'connections', label: 'Connections', value: 'Open', onSelect: () => { setAccountOpen(false); openArtistConnections() } }] : []),
            ...(realRole === 'founder' ? [{ id: 'team', label: 'Team', value: 'Open', onSelect: () => { setAccountOpen(false); navigate(ROUTES.ADMIN_USERS) } }] : []),
            ...(realRole === 'founder' ? [{ id: 'studio-settings', label: 'Studio settings', value: 'Open', onSelect: showStudioSettings }] : []),
            ...(role === 'artist' ? [{ id: 'artist-tour', label: 'Replay portal walkthrough', value: 'Start', onSelect: () => { setAccountOpen(false); setActiveTab('hub'); setArtistTourVisible(true) } }] : []),
            { id: 'main-site', label: 'Return to main site', onSelect: requestMainSiteExit },
            { id: 'sign-out', label: 'Sign out', onSelect: signOut },
          ]}
          style={{ width: 238 }}
        >
          {user?.role === 'founder' ? (
            <>
              <span className="ryozan-account-menu-view-label">View as · founder tool</span>
              <div className="ryozan-account-menu-view-roles">
                {viewRoles.map(({ label, mode }) => {
                  const selected = viewAs === mode
                  return (
                    <button
                      key={label}
                      type="button"
                      onClick={() => applyViewAs(mode)}
                      aria-pressed={selected}
                      className={selected ? 'is-active' : undefined}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
            </>
          ) : null}
        </AccountMenu>
      </div>
    </>
  ) : null

  return (
    <div ref={portalRootRef} className={`ryozan-mobile-portal-home${collectorThread ? ' has-courier-thread' : ''}`}>
      <PortalViewportDebug enabled={viewportDebug} />
      {/* The enter threshold used to play here, which meant every login showed
          "Welcome Stranger" twice: once from LoginGate's LogoLoader on the way
          in, then again the instant the portal mounted. LoginGate owns the
          entry ceremony. The exit threshold below is untouched. */}
      <PortalShell
        tabs={tabs}
        activeTab={activeTab}
        onSelectTab={selectTab}
        agentAccent={agent.accent}
        agentBloomRgb={agent.bloom}
        // No agent mark over a client's own thread: the one composer on that
        // screen sends to a person, and a floating agent beside it invites the
        // exact confusion this screen exists to remove.
        agentGlyph={collectorThread || !canUsePortalAi ? undefined : agent.glyph}
        agentName={collectorThread || !canUsePortalAi ? undefined : agent.name}
        onOpenAgent={openPageAgent}
        header={activeTab === 'table' ? (
          collectorThread ? (
            <div className="ryozan-collector-thread-bar">
              <button type="button" className="ryozan-collector-thread-bar__back" onClick={closeCollectorThread}>
                <span aria-hidden="true">←</span> Inbox
              </button>
              <span>
                <strong>{conversationTitle(collectorThread)}</strong>
                <small>{conversationThreadMeta(collectorThread)}</small>
              </span>
              {accountAction}
            </div>
          ) : (
            <>
              <PageTitle action={accountAction}>Courier</PageTitle>
              <CourierCommandbar query={courierQuery} onQueryChange={setCourierQuery} onOpenRolodex={openMercuryRolodex} onCompose={openMercuryCompose} actionsAvailable={canOpenMercuryContacts} />
            </>
          )
        ) : undefined}
        overlays={(
          <>
            {artistTourVisible ? <ArtistPortalTour onClose={completeArtistTour} onOpenScroll={openArtistScrollPreview} /> : null}
            {accountOverlay}
            <AgentChatSheet
              open={agentChatOpen}
              onClose={requestAgentChatClose}
            placement="full"
            ariaLabel={artistScrollWorkflowActive ? 'Spyder Artist Scroll' : bookyDebriefActive ? 'Booky debrief' : hubWorkflowActive ? `${agent.name} Hub workflow` : undefined}
            scrim={agent.id !== 'booky'}
            agentName={mercuryConversationActive && selectedMercuryConversation ? conversationTitle(selectedMercuryConversation) : agent.name}
            agentGlyph={mercuryConversationActive ? <MercuryMark /> : agent.glyph}
            accent={mercuryModeAccent}
            accentRgb={mercuryModeBloom}
            status={mercuryConversationActive
              ? 'Courier'
              : artistScrollWorkflowActive
              ? 'Artist Scroll · Spyder guided fill'
              : bookyDebriefActive && selectedBookyBooking
              ? selectedBookyBooking.reference_code || bookingTitle(selectedBookyBooking)
              : bookyDebriefActive && selectedBookyDate
              ? new Date(`${selectedBookyDate}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).replace(',', '')
              : hubWorkflowActive && selectedHubWorkflow
                ? `${selectedHubWorkflow?.replace("-", " ")} workflow`
              : (agent.id === 'mercury' || agent.id === 'pulse') ? selectedMercuryConversation ? conversationTitle(selectedMercuryConversation) : undefined : agent.role}
            lines={renderedAgentLines}
            working={agentChatWorking}
            dock={canUsePortalAi ? agentToolsPanel : undefined}
            composer={!canUsePortalAi && !mercuryConversationActive ? undefined : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {canUsePortalAi ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (agentToolsOpen) requestAgentToolsClose()
                      else {
                        setAgentToolsClosing(false)
                        setAgentToolsOpen(true)
                      }
                    }}
                    aria-label={mercuryConversationActive ? 'Mercury Playing Cards' : `${agent.name} tools`}
                    aria-pressed={agentToolsOpen}
                    style={{
                      flexShrink: 0,
                      width: 42,
                      height: 42,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      border: 0,
                      borderRadius: '50%',
                      background: 'transparent',
                      color: mercuryModeAccent,
                      fontSize: 28,
                      lineHeight: 1,
                      textShadow: '0 0 10px, 0 0 22px',
                      cursor: 'pointer',
                    }}
                  >
                    {mercuryConversationActive ? <MercuryMark /> : agent.glyph}
                  </button>
                ) : null}
                <div style={{ position: 'relative', flex: 1, minWidth: 0, display: 'flex' }}>
                  {!agentChatDraft ? (
                    <span
                      aria-hidden="true"
                      style={{
                        position: 'absolute',
                        left: 15,
                        right: 15,
                        top: '50%',
                        transform: 'translateY(-50%)',
                        pointerEvents: 'none',
                        fontFamily: 'var(--font-body)',
                        fontSize: 13,
                        color: 'var(--ink-ghost)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {mercuryConversationActive && selectedMercuryConversation
                        ? `Message ${conversationTitle(selectedMercuryConversation)}`
                        : artistScrollWorkflowActive
                          ? 'Ask Spyder about this Artist Scroll'
                          : bookyDebriefActive
                            ? 'Ask Booky about this day'
                            : agent.suggestion}
                    </span>
                  ) : null}
                  <input
                    aria-label={mercuryConversationActive && selectedMercuryConversation ? `Message ${conversationTitle(selectedMercuryConversation)}` : `Message ${agent.name}`}
                    value={agentChatDraft}
                    onChange={(event) => setAgentChatDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault()
                        if (mercuryConversationActive) void sendMercuryCollectorMessage()
                        else void sendAgentMessage()
                      } else if ((event.key === 'Tab' || event.key === 'ArrowRight') && !agentChatDraft) {
                        event.preventDefault()
                        setAgentChatDraft(agent.suggestion)
                      }
                    }}
                    style={{
                      flex: 1,
                      minWidth: 0,
                      minHeight: 42,
                      padding: '0 15px',
                      border: 0,
                      borderRadius: 99,
                      background: 'linear-gradient(-90deg,#0F0E0E,#141414)',
                      boxShadow: 'inset 0 2px 6px rgba(0,0,0,.55), inset 0 1px 0 rgba(0,0,0,.4)',
                      color: 'var(--ink)',
                      fontFamily: 'var(--font-body)',
                      fontSize: 13,
                      outline: 'none',
                    }}
                  />
                </div>
                <button
                  type="button"
                  aria-label={mercuryConversationActive && selectedMercuryConversation ? `Send to ${conversationTitle(selectedMercuryConversation)}` : (agent.id === 'mercury' || agent.id === 'pulse') ? 'Ask Taisō' : 'Send'}
                  onClick={() => {
                    if (mercuryConversationActive) void sendMercuryCollectorMessage()
                    else void sendAgentMessage()
                  }}
                  disabled={!agentChatDraft.trim() || agentChatWorking}
                  style={{
                    flexShrink: 0,
                    minHeight: 42,
                    padding: '0 18px',
                    borderRadius: 99,
                    border: 0,
                    background: mercuryModeAccent,
                    boxShadow: `0 0 18px rgba(${mercuryModeBloom},.5)`,
                    color: '#12100c',
                    cursor: agentChatDraft.trim() && !agentChatWorking ? 'pointer' : 'default',
                    opacity: agentChatDraft.trim() && !agentChatWorking ? 1 : .45,
                    fontFamily: 'var(--font-btn)',
                    fontSize: 10.5,
                    fontWeight: 700,
                    letterSpacing: '.14em',
                    textTransform: 'uppercase',
                  }}
                >
                  {mercuryConversationActive ? 'Send' : (agent.id === 'mercury' || agent.id === 'pulse') ? 'Ask Taisō' : 'Send'}
                </button>
              </div>
            )}
            />
          </>
        )}
      >
        <PortalMotion motionKey={activeTab} exiting={pendingTab !== null} onExitComplete={completeTabTransition}>{page}</PortalMotion>
      </PortalShell>
      {mainSiteExitTarget ? <PortalThresholdTransition mode="exit" onComplete={completeMainSiteExit} /> : null}
    </div>
  )
}
