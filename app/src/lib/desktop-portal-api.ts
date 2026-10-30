import type {
  ArtifactActionResponse, ArtifactTypeInfo, ArtistDetail, Booking, BookingScheduleSuggestion, BookingScheduleSuggestionResponse, ClubPost, Conversation, ConversationMessage,
  BookingLifecycleEvent, BookingOutcomeEnvelope, BookyDecisionEnvelope,
  GalaChatHistoryMessage, GalaChatResponse, QuestWithProgress, ResponseTemplate, User,
} from './api'

// The frozen package accepts display-only data without routing identities.
// This consumer boundary retains API records and states; api.ts remains the
// sole transport owner, and the renderer owns presentation rather than fetching.
export type DesktopResource<T> =
  | { status: 'loading'; data: null; error: null }
  | { status: 'ready' | 'empty'; data: T; error: null }
  | { status: 'error' | 'blocked'; data: null; error: string }

export interface DesktopCollection<T> { items: T[]; total: number }
export interface DesktopBookingConfirmationData {
  booking: Booking
  outcome: DesktopResource<BookingOutcomeEnvelope | BookyDecisionEnvelope>
  history: DesktopResource<BookingLifecycleEvent[]>
}
export interface DesktopConversationData {
  conversation: Conversation
  booking: Booking | null
  bookingError: string | null
  messages: ConversationMessage[]
  total: number
  has_more: boolean
}
export interface DesktopPortalData {
  user: DesktopResource<User>
  bookings: DesktopResource<DesktopCollection<Booking>>
  inbox: DesktopResource<DesktopCollection<Conversation>>
  artist: DesktopResource<ArtistDetail | null>
  club: DesktopResource<DesktopCollection<ClubPost>>
  quests: DesktopResource<DesktopCollection<QuestWithProgress>>
}

export type DesktopAgentId =
  | 'gala' | 'spyder' | 'bella' | 'booky' | 'mercury' | 'xar' | 'herald' | 'smoku'
  | 'soko' | 'shishin' | 'ensei' | 'saishin' | 'taiso' | 'rochishin' | 'andozen' | 'riki'
export type DesktopArtistProfileChanges = Parameters<Api['artists']['updateProfile']>[0]
export type DesktopAccountScope = Pick<User, 'id' | 'role' | 'proxy_for'>
export type DesktopArtifactIntent = { kind: 'artifact.action'; conversationId: string; messageId: string } & (
  | { messageType: 'quote_proposal'; action: 'confirm' | 'cancel' }
  | { messageType: 'slot_proposal'; action: 'select_slot'; slotIndex: number }
  | { messageType: 'slot_proposal'; action: 'cancel' }
)
export type DesktopPortalIntent =
  | { kind: 'booking.schedule'; bookingId: string; slot: Pick<BookingScheduleSuggestion, 'date' | 'time' | 'duration_minutes'> }
  | { kind: 'booking.create'; booking: Pick<Parameters<Api['bookings']['staffCreate']>[0], 'guest_name' | 'guest_email' | 'guest_phone' | 'description' | 'placement'> }
  | { kind: 'agent.chat'; agentId: DesktopAgentId; message: string; history?: GalaChatHistoryMessage[]; conversationId?: string }
  | { kind: 'client.send'; conversationId: string; message: string }
  | { kind: 'gallery.upload'; file: File }
  | { kind: 'profile.update'; changes: DesktopArtistProfileChanges }
  | { kind: 'profile.avatar'; file: File }
  | { kind: 'gallery.remove'; url: string }
  | DesktopArtifactIntent

// A persisted message is not a provider-delivery receipt. Keep the full API
// response so callers can display actual metadata and tool outcomes separately.
export type DesktopIntentReceipt =
  | { kind: 'booking.schedule'; result: Booking }
  | { kind: 'booking.create'; result: Booking }
  | { kind: 'agent.chat'; result: GalaChatResponse }
  | { kind: 'client.send'; result: ConversationMessage }
  | { kind: 'gallery.upload'; result: ArtistDetail }
  | { kind: 'artifact.action'; result: ArtifactActionResponse }
  | { kind: 'profile.update'; result: ArtistDetail }
  | { kind: 'profile.avatar'; result: ArtistDetail }
  | { kind: 'gallery.remove'; result: ArtistDetail }

type Api = typeof import('./api')
export interface DesktopPortalClients {
  auth: Pick<Api['auth'], 'me'>
  bookings: Pick<Api['bookings'], 'list' | 'get' | 'staffCreate' | 'scheduleSuggestions' | 'update' | 'getBookyProjection' | 'history'>
  conversations: Pick<Api['conversations'], 'list' | 'get' | 'getParticipants' | 'getMessages' | 'sendMessage'>
  messages: Pick<Api['messages'], 'listTypes' | 'artifactAction'>
  artists: Pick<Api['artists'], 'list' | 'get' | 'uploadGalleryImage' | 'updateProfile' | 'uploadAvatar' | 'removeGalleryImage'>
  club: Pick<Api['club'], 'listPosts'>
  quests: Pick<Api['quests'], 'list'>
  gala: Pick<Api['gala'], 'chat'>
  responseTemplates: Pick<Api['responseTemplates'], 'list'>
}

const loading = <T>(): DesktopResource<T> => ({ status: 'loading', data: null, error: null })
export function initialDesktopPortalData(): DesktopPortalData {
  return { user: loading(), bookings: loading(), inbox: loading(), artist: loading(), club: loading(), quests: loading() }
}

export function desktopAgentAllowed(role: User['role'], agentId: DesktopAgentId): boolean {
  if (agentId === 'gala') return role === 'admin' || role === 'founder'
  if (agentId === 'spyder' || agentId === 'soko') return role !== 'collector'
  return ['bella', 'booky', 'mercury', 'xar', 'herald', 'smoku', 'shishin', 'ensei', 'saishin', 'taiso', 'rochishin', 'andozen', 'riki'].includes(agentId)
}

export function desktopGalleryAllowed(role: User['role']): boolean {
  return role === 'artist' || role === 'admin' || role === 'founder'
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'The request failed. Try again.'
}

function requireImageFile(file: File): void {
  if (!(file instanceof File) || file.size === 0) throw new Error('Choose a nonempty image file.')
}

const profileFields: readonly (keyof DesktopArtistProfileChanges)[] = [
  'title', 'specialty', 'bio', 'booking_mode', 'booking_slot_interval_minutes',
  'booking_buffer_minutes', 'booking_min_notice_hours', 'booking_max_advance_days',
  'booking_allow_same_day', 'instagram_url', 'phone', 'facebook_url', 'tiktok_url', 'website_url',
]

function collection<T>(items: T[], total: number): DesktopResource<DesktopCollection<T>> {
  return { status: items.length ? 'ready' : 'empty', data: { items, total }, error: null }
}

/** Each load is independently scoped by auth.me, never by a development role preview. */
export function createDesktopPortalApi(injected?: DesktopPortalClients) {
  let generation = 0
  const clients = async (): Promise<DesktopPortalClients> => injected ?? await import('./api')

  async function readArtifactTypes(): Promise<ArtifactTypeInfo[]> {
    const api = await clients()
    await api.auth.me()
    return (await api.messages.listTypes()).types
  }

  function requireSameScope(current: DesktopAccountScope, expected: DesktopAccountScope) {
    if (current.id !== expected.id || current.role !== expected.role || current.proxy_for !== expected.proxy_for) {
      throw new Error('Your account scope changed. Reload the conversation.')
    }
  }

  async function assertSameScope(api: DesktopPortalClients, user: DesktopAccountScope) {
    const current = await api.auth.me()
    requireSameScope(current, user)
  }

  async function readResponseTemplates(expectedScope?: DesktopAccountScope): Promise<ResponseTemplate[]> {
    const api = await clients()
    const user = await api.auth.me()
    if (expectedScope) requireSameScope(user, expectedScope)
    if (!['assistant', 'artist', 'admin', 'founder'].includes(user.role)) {
      throw new Error('Quick replies are unavailable for your account role.')
    }
    // This is the shared staff library. Keep server order and all creators;
    // selecting a response changes a composer draft, never sends a message.
    const templates = await api.responseTemplates.list()
    await assertSameScope(api, user)
    return templates
  }

  async function readBookingConfirmation(bookingId: string, expectedScope?: DesktopAccountScope): Promise<DesktopBookingConfirmationData> {
    if (!bookingId.trim()) throw new Error('Choose an existing booking.')
    const api = await clients()
    const user = await api.auth.me()
    if (expectedScope) requireSameScope(user, expectedScope)
    const booking = await api.bookings.get(bookingId)
    if (booking.id !== bookingId) throw new Error('Booking identity mismatch. Reload the booking.')
    const admin = user.role === 'admin' || user.role === 'founder'
    const assigned = user.role === 'artist' && booking.artist_id === user.id
    const client = booking.client_id === user.id
    const manager = admin || user.role === 'assistant' || assigned
    if (!manager && !client) throw new Error('This booking is outside your account scope.')
    await assertSameScope(api, user)
    const outcomeRead = async (): Promise<DesktopBookingConfirmationData['outcome']> => {
      // The projection endpoint does not grant assistants shop-wide access.
      if (!(admin || booking.artist_id === user.id || client)) {
        return { status: 'blocked', data: null, error: 'Confirmation projection is unavailable for this account on this booking.' }
      }
      try {
        const result = await api.bookings.getBookyProjection(bookingId)
        if ('booking_id' in result && result.booking_id !== bookingId) throw new Error('Confirmation identity mismatch. Reload the booking.')
        return { status: 'ready', data: result, error: null }
      } catch (error) { return { status: 'error', data: null, error: errorText(error) } }
    }
    const historyRead = async (): Promise<DesktopBookingConfirmationData['history']> => {
      if (!manager || user.role === 'collector') return { status: 'blocked', data: null, error: 'Lifecycle history is available only to the assigned artist or shop management.' }
      try {
        const result = await api.bookings.history(bookingId)
        if (result.some(event => event.booking_id !== bookingId)) throw new Error('Lifecycle history identity mismatch. Reload the booking.')
        return { status: result.length ? 'ready' : 'empty', data: result, error: null }
      } catch (error) { return { status: 'error', data: null, error: errorText(error) } }
    }
    const [outcome, history] = await Promise.all([outcomeRead(), historyRead()])
    await assertSameScope(api, user)
    return { booking, outcome, history }
  }

  async function readScheduleSuggestions(bookingId: string, expectedScope?: DesktopAccountScope): Promise<BookingScheduleSuggestionResponse> {
    if (!bookingId.trim()) throw new Error('Choose an existing booking.')
    const api = await clients()
    const user = await api.auth.me()
    if (expectedScope) requireSameScope(user, expectedScope)
    const manager = ['assistant', 'admin', 'founder'].includes(user.role)
    if (!manager && user.role !== 'artist') throw new Error('Schedule suggestions are unavailable for this role.')
    const booking = await api.bookings.get(bookingId)
    if (booking.id !== bookingId || !booking.artist_id) throw new Error('Booking identity mismatch. Reload the booking.')
    if (!manager && booking.artist_id !== user.id) throw new Error('Only the assigned artist or shop management can manage this booking.')
    await assertSameScope(api, user)
    // The shared scheduler remains authoritative. This read reserves no slot
    // and does not invoke rescheduling, confirmation, or provider writes.
    const response = await api.bookings.scheduleSuggestions(bookingId, { days: 45, limit: 5 })
    await assertSameScope(api, user)
    if (response.booking_id !== bookingId || response.artist_id !== booking.artist_id) {
      throw new Error('Schedule suggestion identity mismatch. Reload the booking.')
    }
    return response
  }

  async function readConversation(conversationId: string): Promise<DesktopConversationData> {
    if (!conversationId.trim()) throw new Error('Choose an existing conversation.')
    const api = await clients()
    const user = await api.auth.me()
    const conversation = await api.conversations.get(conversationId)
    if (conversation.id !== conversationId) throw new Error('Conversation identity mismatch.')
    const response = await api.conversations.getMessages(conversationId, { limit: 100 })
    let booking: Booking | null = null
    let bookingError: string | null = null
    if (conversation.booking_context?.id) {
      try {
        booking = await api.bookings.get(conversation.booking_context.id)
        if (booking.id !== conversation.booking_context.id) {
          booking = null
          bookingError = 'Booking identity mismatch. Reload the conversation.'
        }
      } catch (error) { bookingError = errorText(error) }
    }
    // A session can change while the thread requests are in flight. Never
    // release the old account's messages into a newly authenticated view.
    const current = await api.auth.me()
    if (current.id !== user.id || current.role !== user.role || current.proxy_for !== user.proxy_for) {
      throw new Error('Your account scope changed. Reload the conversation.')
    }
    if (response.messages.some(message => message.conversation_id !== conversationId)) {
      throw new Error('Message conversation identity mismatch.')
    }
    // Courier returns the newest page in descending created_at order. The
    // desktop thread and bounded Mercury transcript consume oldest-first order.
    // Reverse a copy so callers never mutate the transport response's array.
    return { conversation, booking, bookingError, ...response, messages: [...response.messages].reverse() }
  }

  async function load(onState?: (state: DesktopPortalData) => void): Promise<DesktopPortalData> {
    const requestGeneration = ++generation
    let state = initialDesktopPortalData()
    const publish = () => { if (requestGeneration === generation) onState?.(state) }
    const set = <K extends keyof DesktopPortalData>(key: K, resource: DesktopPortalData[K]) => {
      state = { ...state, [key]: resource }
      publish()
    }
    publish()
    let api: DesktopPortalClients
    let user: User
    try {
      api = await clients()
      user = await api.auth.me()
      set('user', { status: 'ready', data: user, error: null })
    } catch (error) {
      const failed = { status: 'error' as const, data: null, error: errorText(error) }
      const blocked = { status: 'blocked' as const, data: null, error: 'Sign in to load this section.' }
      state = { user: failed, bookings: blocked, inbox: blocked, artist: blocked, club: blocked, quests: blocked }
      publish()
      return state
    }

    const read = async <K extends keyof DesktopPortalData>(key: K, operation: () => Promise<DesktopPortalData[K]>) => {
      try { set(key, await operation()) }
      catch (error) { set(key, { status: 'error', data: null, error: errorText(error) }) }
    }
    await Promise.all([
      read('bookings', async () => {
        // The shop-wide flag is allowed only for authenticated admin/founder.
        // Assistants already receive shop scope; artists keep backend own scope.
        const response = await api.bookings.list({ limit: 100, all: user.role === 'admin' || user.role === 'founder' })
        return collection(response.bookings, response.total)
      }),
      read('inbox', async () => {
        const response = await api.conversations.list()
        return collection(response.conversations, response.total)
      }),
      read('artist', async () => {
        if (!desktopGalleryAllowed(user.role)) {
          return { status: 'blocked', data: null, error: 'Artist profile access is unavailable for this role.' }
        }
        const roster = await api.artists.list()
        const own = roster.find(artist => artist.id === user.id)
        if (!own) return { status: 'empty', data: null, error: null }
        return { status: 'ready', data: await api.artists.get(own.slug), error: null }
      }),
      read('club', async () => {
        const response = await api.club.listPosts({ limit: 50 })
        return collection(response.posts, response.total)
      }),
      read('quests', async () => {
        const response = await api.quests.list()
        return collection(response.quests, response.total)
      }),
    ])
    return state
  }

  async function execute(intent: DesktopPortalIntent, expectedScope?: DesktopAccountScope): Promise<DesktopIntentReceipt> {
    const api = await clients()
    const user = await api.auth.me()
    if (expectedScope) requireSameScope(user, expectedScope)
    const studioReceipt = async <K extends 'gallery.upload' | 'profile.avatar' | 'profile.update' | 'gallery.remove'>(kind: K, operation: () => Promise<ArtistDetail>): Promise<{ kind: K; result: ArtistDetail }> => {
      const result = await operation()
      await assertSameScope(api, user)
      return { kind, result }
    }
    switch (intent.kind) {
      case 'booking.schedule': {
        const { date, time, duration_minutes: duration } = intent.slot
        const parsedDate = new Date(`${date}T00:00:00Z`)
        if (!intent.bookingId.trim() || !/^\d{4}-\d{2}-\d{2}$/.test(date)
          || !Number.isFinite(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== date
          || !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)
          || !Number.isInteger(duration) || duration < 15 || duration > 720) {
          throw new Error('Choose a valid suggested date, time, and duration between 15 and 720 minutes.')
        }
        const manager = ['assistant', 'admin', 'founder'].includes(user.role)
        if (!manager && user.role !== 'artist') throw new Error('Scheduling is unavailable for this role.')
        const booking = await api.bookings.get(intent.bookingId)
        if (booking.id !== intent.bookingId || !booking.artist_id) throw new Error('Booking identity mismatch. Reload the booking.')
        if (!manager && booking.artist_id !== user.id) throw new Error('Only the assigned artist or shop management can manage this booking.')
        if (booking.status !== 'pending') throw new Error('Only pending inquiries can use this scheduling action. Reload the booking.')
        await assertSameScope(api, user)
        // PATCH validates conflicts and booking rules without reschedule's
        // automatic confirmation. Never forward status, payment, or overrides.
        const changes = { scheduled_date: date, scheduled_time: time, duration_minutes: duration, expected_status: 'pending' as const }
        const requireSavedSchedule = (saved: Booking) => {
          if (saved.id !== booking.id || saved.artist_id !== booking.artist_id || saved.status !== 'pending'
            || saved.scheduled_date !== date || saved.scheduled_time?.slice(0, 5) !== time || saved.duration_minutes !== duration) {
            throw new Error('The returned booking does not match the pending schedule.')
          }
        }
        let acknowledged = false
        try {
          const response = await api.bookings.update(booking.id, changes)
          acknowledged = true
          requireSavedSchedule(response)
          await assertSameScope(api, user)
          const persisted = await api.bookings.get(booking.id)
          await assertSameScope(api, user)
          requireSavedSchedule(persisted)
          return { kind: intent.kind, result: persisted }
        } catch (error) {
          // Explicit client rejections are safe to correct. Network/server or
          // readback failures cannot establish whether a write committed.
          const status = error && typeof error === 'object' && 'status' in error ? error.status : undefined
          if (!acknowledged && typeof status === 'number' && [400, 401, 403, 404, 409, 422].includes(status)) throw error
          throw new Error(`Save outcome is unverified. Check the booking before retrying; the schedule may have been saved. ${errorText(error)}`)
        }
      }
      case 'agent.chat': {
        if (!desktopAgentAllowed(user.role, intent.agentId)) throw new Error('This agent is unavailable for your account role.')
        if (!intent.message.trim()) throw new Error('Enter a message.')
        const context: Record<string, unknown> = { agent_id: intent.agentId, surface: 'desktop' }
        if (intent.conversationId) {
          if (intent.agentId !== 'mercury' && intent.agentId !== 'taiso') throw new Error('Conversation context is supported here only for Mercury.')
          const conversation = await api.conversations.get(intent.conversationId)
          if (conversation.id !== intent.conversationId) throw new Error('Conversation identity mismatch.')
          Object.assign(context, { conversation_id: conversation.id, conversation_name: conversation.name, portal_tab: 'courier' })
          await assertSameScope(api, user)
        }
        const result = await api.gala.chat({
          message: intent.message,
          history: intent.history,
          context,
        })
        return { kind: intent.kind, result }
      }
      case 'artifact.action': {
        const thread = await readConversation(intent.conversationId)
        const message = thread.messages.find(item => item.id === intent.messageId)
        if (!message || message.message_type !== intent.messageType) throw new Error('Artifact no longer matches this conversation. Reload the thread.')
        // Artifact endpoint requires actual participation, not proxy-only access.
        const participants = await api.conversations.getParticipants(intent.conversationId)
        if (!participants.some(item => item.user_id === user.id)) throw new Error('Artifact actions require your account to be a participant.')
        const types = await readArtifactTypes()
        if (!types.find(item => item.message_type === message.message_type)?.allowed_actions.includes(intent.action)) {
          throw new Error('This artifact action is unavailable.')
        }
        const state = message.artifact_state?.status
        const pending = state === 'pending'
        if (!pending && !(intent.messageType === 'quote_proposal' && intent.action === 'cancel' && state === 'awaiting_payment')) {
          throw new Error('This artifact is no longer awaiting that action.')
        }
        if (intent.messageType === 'quote_proposal' && intent.action === 'confirm' && user.role === 'collector') {
          const bookingId = message.artifact_payload?.booking_id
          if (typeof bookingId !== 'string') throw new Error('Quote booking identity is missing.')
          const booking = await api.bookings.get(bookingId)
          if (booking.client_id !== user.id) throw new Error('Only the booking client or staff can confirm this quote.')
        }
        let actionData: Record<string, unknown> | undefined
        if (intent.action === 'select_slot') {
          const slots = message.artifact_payload?.slots
          if (!Array.isArray(slots) || !Number.isInteger(intent.slotIndex) || intent.slotIndex < 0 || intent.slotIndex >= slots.length) {
            throw new Error('Choose an available proposed slot.')
          }
          actionData = { slot_index: intent.slotIndex }
        }
        await assertSameScope(api, user)
        const result = await api.messages.artifactAction(message.id, intent.action, actionData)
        // Never infer payment or Calendar delivery from the artifact status.
        return { kind: intent.kind, result }
      }
      case 'client.send': {
        if (!intent.conversationId.trim()) throw new Error('Choose an existing conversation.')
        if (!intent.message.trim()) throw new Error('Enter a message.')
        // Resolve authorization against the current token before sending. The
        // send endpoint repeats its own participant/proxy authorization check.
        const conversation = await api.conversations.get(intent.conversationId)
        if (conversation.id !== intent.conversationId) throw new Error('Conversation identity mismatch.')
        await assertSameScope(api, user)
        return { kind: intent.kind, result: await api.conversations.sendMessage(conversation.id, intent.message, 'text') }
      }
      case 'booking.create': {
        if (!['admin', 'founder'].includes(user.role)) throw new Error('Staff booking creation requires an admin or founder account.')
        const { guest_name, guest_email, guest_phone, description, placement } = intent.booking
        if (!guest_name?.trim() || !(guest_email?.trim() || guest_phone?.trim()) || !description?.trim()) {
          throw new Error('Enter a client name, email or phone, and description.')
        }
        const own = (await api.artists.list()).find(artist => artist.id === user.id)
        if (!own) throw new Error('Your artist profile is unavailable.')
        await assertSameScope(api, user)
        // This first desktop flow records an inquiry only. Confirmation and
        // its provider side effects remain in the existing confirmation flow.
        const result = await api.bookings.staffCreate({
          artist_id: own.id, guest_name: guest_name.trim(),
          guest_email: guest_email?.trim() || undefined, guest_phone: guest_phone?.trim() || undefined,
          description: description.trim(), placement: placement?.trim() || undefined,
          status: 'pending', confirm_calendar: false,
        })
        return { kind: intent.kind, result }
      }
      case 'gallery.upload':
        if (!desktopGalleryAllowed(user.role)) throw new Error('Artist gallery upload is unavailable for your account role.')
        requireImageFile(intent.file)
        await assertSameScope(api, user)
        return studioReceipt(intent.kind, () => api.artists.uploadGalleryImage(intent.file))
      case 'profile.avatar':
        if (!desktopGalleryAllowed(user.role)) throw new Error('Artist profile editing is unavailable for your account role.')
        requireImageFile(intent.file)
        await assertSameScope(api, user)
        return studioReceipt(intent.kind, () => api.artists.uploadAvatar(intent.file))
      case 'profile.update': {
        if (!desktopGalleryAllowed(user.role)) throw new Error('Artist profile editing is unavailable for your account role.')
        if (!intent.changes || Object.keys(intent.changes).some(key => !profileFields.includes(key as keyof DesktopArtistProfileChanges))) {
          throw new Error('Unsupported artist profile field.')
        }
        const changes: DesktopArtistProfileChanges = {}
        for (const key of profileFields) {
          if (Object.hasOwn(intent.changes, key) && intent.changes[key] !== undefined) {
            Object.assign(changes, { [key]: intent.changes[key] })
          }
        }
        if (!Object.keys(changes).length) throw new Error('No profile changes supplied.')
        await assertSameScope(api, user)
        return studioReceipt(intent.kind, () => api.artists.updateProfile(changes))
      }
      case 'gallery.remove': {
        if (!desktopGalleryAllowed(user.role)) throw new Error('Artist gallery editing is unavailable for your account role.')
        if (!intent.url.trim()) throw new Error('Choose an existing gallery image.')
        const own = (await api.artists.list()).find(artist => artist.id === user.id)
        if (!own) throw new Error('Artist profile is unavailable.')
        const profile = await api.artists.get(own.slug)
        if (profile.id !== user.id || !profile.portfolio_images?.includes(intent.url)) throw new Error('Image does not belong to your current gallery.')
        await assertSameScope(api, user)
        return studioReceipt(intent.kind, () => api.artists.removeGalleryImage(intent.url))
      }
    }
  }

  return { load, readConversation, readScheduleSuggestions, readBookingConfirmation, readArtifactTypes, readResponseTemplates, execute, cancelLoad: () => { generation += 1 } }
}
