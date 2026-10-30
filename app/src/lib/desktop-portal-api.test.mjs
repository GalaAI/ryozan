import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// Transpile only this adapter in memory. Inject every transport so these tests
// cannot import the browser client or contact a DB, provider, or live API.
const source = await readFile(new URL('./desktop-portal-api.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
const { createDesktopPortalApi } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

function setup(role = 'artist') {
  const calls = []
  const user = { id: 'real-user-id', role }
  const clients = {
    auth: { me: async () => user },
    bookings: { get: async id => ({ id, client_id: user.id }), list: async params => { calls.push(['bookings', params]); return { bookings: [{ id: 'booking-id', status: 'expired', client_id: null }], total: 101 } } },
    conversations: {
      list: async () => ({ conversations: [], total: 0 }),
      get: async id => ({ id }),
      getParticipants: async () => [{ user_id: user.id }],
      getMessages: async id => ({ messages: [{ id: 'message-id', conversation_id: id, artifact_payload: { quote_id: 'quote-id' }, artifact_state: { status: 'awaiting_payment' } }], total: 150, has_more: true }),
      sendMessage: async (...args) => { calls.push(['send', ...args]); return { id: 'message-id', metadata_json: '{"delivery":"pending"}' } },
    },
    messages: {
      listTypes: async () => ({ types: [{ message_type: 'quote_proposal', allowed_actions: ['confirm', 'cancel'] }, { message_type: 'slot_proposal', allowed_actions: ['select_slot', 'cancel'] }] }),
      artifactAction: async (...args) => { calls.push(['artifact', ...args]); return { message_id: args[0], message_type: 'slot_proposal', artifact_state: { status: 'confirmed', selected_index: args[2]?.slot_index } } },
    },
    artists: {
      list: async () => [{ id: user.id, slug: 'own-artist' }],
      get: async slug => ({ id: user.id, slug, portfolio_images: ['https://assets.example.test/image.jpg'] }),
      uploadGalleryImage: async file => { calls.push(['upload', file]); return { id: user.id, portfolio_images: ['persisted-url'] } },
      updateProfile: async changes => { calls.push(['profile', changes]); return { id: user.id, ...changes } },
      uploadAvatar: async file => { calls.push(['avatar', file]); return { id: user.id, avatar_url: 'persisted-avatar-url' } },
      removeGalleryImage: async url => { calls.push(['remove', url]); return { id: user.id, portfolio_images: [] } },
    },
    club: { listPosts: async () => ({ posts: [], total: 0 }) },
    quests: { list: async () => ({ quests: [], total: 0 }) },
    responseTemplates: { list: async () => { calls.push(['templates.list']); return [] } },
    gala: { chat: async data => { calls.push(['chat', data]); return { response: 'reply', tool_calls: [{ status: 'blocked' }] } } },
  }
  return { clients, calls, adapter: createDesktopPortalApi(clients) }
}

test('quick replies preserve shared records and server order for every staff role without writes', async () => {
  const records = Object.freeze([
    Object.freeze({ id: 'shared-template', title: 'Shared', body: 'Shared response', sort_order: 0, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }),
    Object.freeze({ id: 'other-owner-template', title: 'Other', body: 'Other response', sort_order: 1, created_by: 'other-user', created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' }),
  ])
  for (const role of ['assistant', 'artist', 'admin', 'founder']) {
    const { adapter, clients, calls } = setup(role)
    clients.responseTemplates.list = async () => { calls.push(['templates.list']); return records }
    const result = await adapter.readResponseTemplates({ id: 'real-user-id', role })
    assert.deepEqual(result, records)
    assert.equal(result, records)
    assert.deepEqual(calls, [['templates.list']])
  }
})

test('quick replies reject collectors and mismatched initiating scope before list', async () => {
  const collector = setup('collector')
  await assert.rejects(collector.adapter.readResponseTemplates(), /unavailable/)
  assert.deepEqual(collector.calls, [])
  for (const change of [{ id: 'other' }, { role: 'founder' }, { proxy_for: 'other' }]) {
    const { adapter, calls } = setup()
    await assert.rejects(adapter.readResponseTemplates({ id: 'real-user-id', role: 'artist', ...change }), /scope changed/)
    assert.deepEqual(calls, [])
  }
})

test('quick replies suppress results when identity role or proxy changes during fetch', async () => {
  for (const change of [{ id: 'other' }, { role: 'founder' }, { proxy_for: 'other' }]) {
    const { adapter, clients, calls } = setup()
    clients.responseTemplates.list = async () => {
      calls.push(['templates.list'])
      clients.auth.me = async () => ({ id: 'real-user-id', role: 'artist', ...change })
      return [{ id: 'old-account-template' }]
    }
    await assert.rejects(adapter.readResponseTemplates(), /scope changed/)
    assert.deepEqual(calls, [['templates.list']])
  }
})

test('quick replies keep empty success distinct from auth and list failures', async () => {
  const { adapter, clients, calls } = setup()
  assert.deepEqual(await adapter.readResponseTemplates(), [])
  const failure = new Error('Template service unavailable')
  clients.responseTemplates.list = async () => { throw failure }
  await assert.rejects(adapter.readResponseTemplates(), error => error === failure)
  clients.auth.me = async () => { throw new Error('Session expired') }
  await assert.rejects(adapter.readResponseTemplates(), /Session expired/)
  assert.deepEqual(calls, [['templates.list']])
})

function scheduleSetup(role = 'artist') {
  const context = setup(role)
  const response = {
    booking_id: 'booking-id', artist_id: 'real-user-id', timezone: 'America/New_York',
    duration_minutes: 120, calendar_connected: false,
    suggestions: [{ date: '2026-09-20', time: '13:00', label: 'Sunday afternoon', duration_minutes: 120, source: 'venue', confidence: 'medium', rationale: 'Within booking rules' }],
  }
  context.clients.bookings.get = async id => ({ id, artist_id: 'real-user-id' })
  context.clients.bookings.scheduleSuggestions = async (...args) => { context.calls.push(['suggestions', ...args]); return response }
  return { ...context, response }
}

test('schedule read preserves the complete existing contract and empty results', async () => {
  for (const role of ['artist', 'assistant', 'admin', 'founder']) {
    const { adapter, response, calls } = scheduleSetup(role)
    assert.equal(await adapter.readScheduleSuggestions('booking-id', { id: 'real-user-id', role }), response)
    assert.deepEqual(calls, [['suggestions', 'booking-id', { days: 45, limit: 5 }]])
    response.suggestions = []
    assert.equal(await adapter.readScheduleSuggestions('booking-id'), response)
  }
})

test('schedule read rejects absent, denied and mismatched booking identities before suggestions', async () => {
  for (const variant of ['blank', 'collector', 'foreign', 'mismatch', 'missing-artist', 'denied']) {
    const { adapter, clients, calls } = scheduleSetup(variant === 'collector' ? 'collector' : 'artist')
    if (variant === 'foreign') clients.bookings.get = async id => ({ id, artist_id: 'other' })
    if (variant === 'mismatch') clients.bookings.get = async () => ({ id: 'other', artist_id: 'real-user-id' })
    if (variant === 'missing-artist') clients.bookings.get = async id => ({ id })
    if (variant === 'denied') clients.bookings.get = async () => { throw new Error('Booking access denied') }
    await assert.rejects(adapter.readScheduleSuggestions(variant === 'blank' ? ' ' : 'booking-id'))
    assert.deepEqual(calls, [])
  }
})

test('schedule read rejects response booking or artist mismatch and propagates API failures', async () => {
  for (const key of ['booking_id', 'artist_id']) {
    const { adapter, response } = scheduleSetup()
    response[key] = 'other'
    await assert.rejects(adapter.readScheduleSuggestions('booking-id'), /identity mismatch/)
  }
  const { adapter, clients } = scheduleSetup()
  clients.bookings.scheduleSuggestions = async () => { throw new Error('Scheduler unavailable') }
  await assert.rejects(adapter.readScheduleSuggestions('booking-id'), /Scheduler unavailable/)
  clients.auth.me = async () => { throw new Error('Session expired') }
  await assert.rejects(adapter.readScheduleSuggestions('booking-id'), /Session expired/)
})

test('schedule read rejects initial scope mismatch and auth drift before and after suggestions', async () => {
  for (const change of [{ id: 'other' }, { role: 'collector' }, { proxy_for: 'other' }]) {
    for (const stage of ['initial', 'preflight', 'response']) {
      const { adapter, clients, calls, response } = scheduleSetup()
      const changed = () => { clients.auth.me = async () => ({ id: 'real-user-id', role: 'artist', ...change }) }
      if (stage === 'initial') changed()
      if (stage === 'preflight') clients.bookings.get = async id => { changed(); return { id, artist_id: 'real-user-id' } }
      if (stage === 'response') clients.bookings.scheduleSuggestions = async () => { changed(); return response }
      await assert.rejects(adapter.readScheduleSuggestions('booking-id', { id: 'real-user-id', role: 'artist' }), /scope changed/)
      assert.deepEqual(calls, [])
    }
  }
})

const scheduleIntent = { kind: 'booking.schedule', bookingId: 'booking-id', slot: { date: '2026-09-20', time: '13:00', duration_minutes: 120 } }
function scheduleWriteSetup(role = 'artist') {
  const context = scheduleSetup(role)
  let stored = { id: 'booking-id', artist_id: 'real-user-id', status: 'pending' }
  context.clients.bookings.get = async () => ({ ...stored })
  context.clients.bookings.update = async (id, changes) => {
    context.calls.push(['update', id, changes])
    stored = { ...stored, ...changes }
    return { ...stored }
  }
  return context
}

test('pending scheduling sends only schedule fields and returns separately read persisted state', async () => {
  const { adapter, clients, calls } = scheduleWriteSetup()
  const originalGet = clients.bookings.get
  let reads = 0
  clients.bookings.get = async () => ({ ...await originalGet(), readback_marker: ++reads })
  const receipt = await adapter.execute({ ...scheduleIntent, slot: { ...scheduleIntent.slot, status: 'confirmed', allow_conflict: true, notify_client: true } })
  assert.equal(receipt.kind, 'booking.schedule')
  assert.equal(receipt.result.status, 'pending')
  assert.equal(receipt.result.readback_marker, 2)
  assert.deepEqual(calls, [['update', 'booking-id', { scheduled_date: '2026-09-20', scheduled_time: '13:00', duration_minutes: 120, expected_status: 'pending' }]])
})

test('pending scheduling rejects invalid input, denied role, foreign booking and non-pending status before writes', async () => {
  for (const slot of [{ date: '2026-02-30' }, { time: '24:00' }, { duration_minutes: 14 }, { duration_minutes: 721 }, { duration_minutes: 15.5 }]) {
    const { adapter, calls } = scheduleWriteSetup()
    await assert.rejects(adapter.execute({ ...scheduleIntent, slot: { ...scheduleIntent.slot, ...slot } }), /valid suggested/)
    assert.deepEqual(calls, [])
  }
  for (const variant of ['collector', 'foreign', 'wrong-id', 'confirmed', 'quoted', 'cancelled', 'expired', 'completed', 'no_show']) {
    const { adapter, clients, calls } = scheduleWriteSetup(variant === 'collector' ? variant : 'artist')
    if (variant !== 'collector') clients.bookings.get = async () => ({ id: variant === 'wrong-id' ? 'other' : 'booking-id', artist_id: variant === 'foreign' ? 'other' : 'real-user-id', status: ['foreign', 'wrong-id'].includes(variant) ? 'pending' : variant })
    await assert.rejects(adapter.execute(scheduleIntent))
    assert.deepEqual(calls, [])
  }
})

test('pending scheduling preserves backend conflict rejection and marks transport outcomes ambiguous without retrying', async () => {
  const { adapter, clients, calls } = scheduleWriteSetup()
  const conflict = Object.assign(new Error('Slot is no longer available'), { status: 409 })
  clients.bookings.update = async () => { calls.push('update'); throw conflict }
  await assert.rejects(adapter.execute(scheduleIntent), error => error === conflict)
  assert.equal(calls.length, 1)
  clients.bookings.update = async () => { calls.push('update'); throw new Error('Network interrupted') }
  await assert.rejects(adapter.execute(scheduleIntent), /Save outcome is unverified.*before retrying/)
  assert.equal(calls.length, 2)
})

test('pending scheduling rejects scope drift before dispatch and after acknowledged save', async () => {
  for (const change of [{ id: 'other' }, { role: 'collector' }, { proxy_for: 'other' }]) {
    for (const stage of ['preflight', 'response', 'readback']) {
      const { adapter, clients, calls } = scheduleWriteSetup()
      const changeAuth = () => { clients.auth.me = async () => ({ id: 'real-user-id', role: 'artist', ...change }) }
      const originalGet = clients.bookings.get
      const originalUpdate = clients.bookings.update
      let reads = 0
      clients.bookings.get = async () => { reads++; if (stage === 'preflight' || (stage === 'readback' && reads === 2)) changeAuth(); return originalGet() }
      clients.bookings.update = async (...args) => { if (stage === 'response') changeAuth(); return originalUpdate(...args) }
      await assert.rejects(adapter.execute(scheduleIntent, { id: 'real-user-id', role: 'artist' }), stage === 'preflight' ? /scope changed/ : /Save outcome is unverified/)
      assert.equal(calls.length, stage === 'preflight' ? 0 : 1)
    }
  }
})

test('pending scheduling cannot claim persistence on response mismatch or failed/mismatched readback', async () => {
  for (const stage of ['response', 'readback', 'failure']) {
    const { adapter, clients, calls } = scheduleWriteSetup()
    const originalGet = clients.bookings.get
    const originalUpdate = clients.bookings.update
    let reads = 0
    clients.bookings.get = async () => {
      reads++
      if (reads === 2 && stage === 'failure') throw new Error('Readback unavailable')
      return { ...await originalGet(), ...(reads === 2 && stage === 'readback' ? { status: 'confirmed' } : {}) }
    }
    clients.bookings.update = async (...args) => ({ ...await originalUpdate(...args), ...(stage === 'response' ? { artist_id: 'other' } : {}) })
    await assert.rejects(adapter.execute(scheduleIntent), /Save outcome is unverified/)
    assert.equal(calls.length, 1)
  }
})

test('booking creation reuses staff API and cannot request confirmation side effects', async () => {
  const { adapter, clients, calls } = setup('founder')
  clients.bookings.staffCreate = async input => { calls.push(input); return { id: 'saved', reference_code: 'BKG-TEST', ...input } }
  const result = await adapter.execute({ kind: 'booking.create', booking: {
    guest_name: ' Test ', guest_email: 'test@example.test', description: ' Inquiry ',
    artist_id: 'other', status: 'confirmed', confirm_calendar: true,
  } })
  assert.equal(result.result.id, 'saved')
  assert.equal(calls[0].artist_id, 'real-user-id')
  assert.equal(calls[0].status, 'pending')
  assert.equal(calls[0].confirm_calendar, false)
  assert.equal(calls[0].description, 'Inquiry')
})

test('booking creation rejects denied roles, missing contact, stale scope and API failures', async () => {
  const intent = { kind: 'booking.create', booking: { guest_name: 'Test', guest_phone: '5550100', description: 'Inquiry' } }
  for (const role of ['collector', 'assistant', 'artist']) {
    await assert.rejects(setup(role).adapter.execute(intent), /admin or founder/)
  }
  const { adapter, clients } = setup('admin')
  await assert.rejects(adapter.execute({ ...intent, booking: { ...intent.booking, guest_phone: '' } }), /email or phone/)
  clients.bookings.staffCreate = async () => { throw new Error('API unavailable') }
  await assert.rejects(adapter.execute(intent), /API unavailable/)
  clients.artists.list = async () => { clients.auth.me = async () => ({ id: 'other', role: 'admin' }); return [{ id: 'real-user-id' }] }
  await assert.rejects(adapter.execute(intent), /account|scope|changed/i)
})

test('reads preserve exact identities/status, pagination totals and independent failures', async () => {
  const { adapter, clients, calls } = setup()
  clients.club.listPosts = async () => { throw new Error('Feed unavailable') }
  const events = []
  const state = await adapter.load(value => events.push(value))
  assert.equal(events[0].user.status, 'loading')
  assert.equal(state.bookings.data.items[0].status, 'expired')
  assert.equal(state.bookings.data.items[0].id, 'booking-id')
  assert.equal(state.bookings.data.total, 101)
  assert.equal(state.club.status, 'error')
  assert.equal(state.club.data, null)
  assert.equal(state.inbox.status, 'empty')
  assert.equal(state.artist.data.slug, 'own-artist')
  assert.equal(calls[0][1].all, false)
})

test('auth failure prevents downstream reads and never becomes empty success', async () => {
  const { adapter, clients, calls } = setup()
  clients.auth.me = async () => { throw new Error('Session expired') }
  const result = await adapter.load()
  assert.equal(result.user.status, 'error')
  assert.equal(result.bookings.status, 'blocked')
  assert.deepEqual(calls, [])
})

test('only authenticated admin/founder request all bookings', async () => {
  for (const role of ['collector', 'assistant', 'artist', 'admin', 'founder']) {
    const { adapter, calls } = setup(role)
    await adapter.load()
    assert.equal(calls[0][1].all, ['admin', 'founder'].includes(role))
  }
})

test('agent and client messages use distinct owners and preserve receipts', async () => {
  const { adapter, calls } = setup()
  const chat = await adapter.execute({ kind: 'agent.chat', agentId: 'spyder', message: 'hello' })
  assert.equal(chat.result.tool_calls[0].status, 'blocked')
  assert.equal(calls[0][0], 'chat')
  assert.equal(calls[0][1].context.agent_id, 'spyder')
  const sent = await adapter.execute({ kind: 'client.send', conversationId: 'thread-id', message: 'hello client' })
  assert.deepEqual(calls[1], ['send', 'thread-id', 'hello client', 'text'])
  assert.equal(sent.result.id, 'message-id')
  assert.match(sent.result.metadata_json, /pending/)
})

test('current auth role gates writes; denied participant read prevents send', async () => {
  const { adapter, clients, calls } = setup('collector')
  await assert.rejects(adapter.execute({ kind: 'agent.chat', agentId: 'gala', message: 'hello' }), /unavailable/)
  await assert.rejects(adapter.execute({ kind: 'gallery.upload', file: { size: 1 } }), /unavailable/)
  clients.conversations.get = async () => { throw new Error('Not a participant') }
  await assert.rejects(adapter.execute({ kind: 'client.send', conversationId: 'private', message: 'hello' }), /participant/)
  assert.deepEqual(calls, [])
})

test('gallery upload forwards the original File and provider failure is not success', async () => {
  const { adapter, clients, calls } = setup()
  const file = new File(['image'], 'image.png', { type: 'image/png' })
  await adapter.execute({ kind: 'gallery.upload', file })
  assert.equal(calls[0][1], file)
  clients.artists.uploadGalleryImage = async () => { throw new Error('Storage unavailable') }
  await assert.rejects(adapter.execute({ kind: 'gallery.upload', file }), /Storage unavailable/)
})

test('cancelLoad suppresses late publications after unmount', async () => {
  const { adapter, clients } = setup()
  let resolveAuth
  clients.auth.me = () => new Promise(resolve => { resolveAuth = resolve })
  const states = []
  const pending = adapter.load(state => states.push(state))
  await Promise.resolve()
  adapter.cancelLoad()
  resolveAuth({ id: 'real-user-id', role: 'artist' })
  await pending
  assert.equal(states.length, 1)
})

test('conversation reads retain message artifacts and pagination', async () => {
  const { adapter } = setup()
  const result = await adapter.readConversation('thread-id')
  assert.equal(result.conversation.id, 'thread-id')
  assert.equal(result.messages[0].id, 'message-id')
  assert.equal(result.messages[0].artifact_payload.quote_id, 'quote-id')
  assert.equal(result.messages[0].artifact_state.status, 'awaiting_payment')
  assert.equal(result.total, 150)
  assert.equal(result.has_more, true)
})

test('conversation read refuses changed account or proxy scope', async () => {
  for (const changed of [{ id: 'other' }, { proxy_for: 'other' }, { role: 'collector' }]) {
    const { adapter, clients } = setup()
    let reads = 0
    clients.auth.me = async () => ({ id: 'real-user-id', role: 'artist', ...(reads++ ? changed : {}) })
    await assert.rejects(adapter.readConversation('thread-id'), /scope changed/)
  }
})

test('conversation scope denial stops message fetch, read failure never becomes empty', async () => {
  const { adapter, clients } = setup()
  let messageReads = 0
  clients.conversations.get = async () => { throw new Error('Not a participant') }
  clients.conversations.getMessages = async () => { messageReads++; throw new Error('Messages unavailable') }
  await assert.rejects(adapter.readConversation('private'), /participant/)
  assert.equal(messageReads, 0)
  clients.conversations.get = async id => ({ id })
  await assert.rejects(adapter.readConversation('thread-id'), /Messages unavailable/)
})

function artifactSetup(role = 'artist', messageType = 'slot_proposal') {
  const setupResult = setup(role)
  setupResult.clients.conversations.getMessages = async id => ({ messages: [{ id: 'artifact-id', conversation_id: id, message_type: messageType, artifact_state: { status: 'pending' }, artifact_payload: { booking_id: 'booking-id', slots: [{ date: '2026-09-15', time: '12:00' }] } }], total: 1, has_more: false })
  return setupResult
}
const selectSlot = { kind: 'artifact.action', conversationId: 'thread-id', messageId: 'artifact-id', messageType: 'slot_proposal', action: 'select_slot', slotIndex: 0 }

test('artifact uses exact handler verb/payload and preserves returned state', async () => {
  const { adapter, calls } = artifactSetup()
  const receipt = await adapter.execute(selectSlot)
  assert.deepEqual(calls, [['artifact', 'artifact-id', 'select_slot', { slot_index: 0 }]])
  assert.deepEqual(receipt.result.artifact_state, { status: 'confirmed', selected_index: 0 })
})

test('artifact rejects proxy-only participation, wrong message and registry denial before write', async () => {
  for (const variant of ['proxy', 'message', 'registry']) {
    const { adapter, clients, calls } = artifactSetup()
    if (variant === 'proxy') clients.conversations.getParticipants = async () => [{ user_id: 'proxy-target' }]
    if (variant === 'registry') clients.messages.listTypes = async () => ({ types: [] })
    await assert.rejects(adapter.execute({ ...selectSlot, messageId: variant === 'message' ? 'other' : 'artifact-id' }))
    assert.deepEqual(calls, [])
  }
})

test('collector cannot confirm another client quote; slot bounds and failed writes remain errors', async () => {
  const quote = artifactSetup('collector', 'quote_proposal')
  quote.clients.bookings.get = async id => ({ id, client_id: 'other-client' })
  await assert.rejects(quote.adapter.execute({ ...selectSlot, messageType: 'quote_proposal', action: 'confirm' }), /booking client/)
  assert.deepEqual(quote.calls, [])
  const slot = artifactSetup()
  await assert.rejects(slot.adapter.execute({ ...selectSlot, slotIndex: 2 }), /available proposed slot/)
  slot.clients.messages.artifactAction = async () => { throw new Error('Provider refused') }
  await assert.rejects(slot.adapter.execute(selectSlot), /Provider refused/)
})

test('Mercury context uses verified thread values and denied reads never call chat', async () => {
  const { adapter, clients, calls } = setup()
  clients.conversations.get = async id => ({ id, name: 'Verified thread' })
  await adapter.execute({ kind: 'agent.chat', agentId: 'mercury', conversationId: 'thread-id', message: 'Summarize' })
  assert.deepEqual(calls[0][1].context, { agent_id: 'mercury', surface: 'desktop', conversation_id: 'thread-id', conversation_name: 'Verified thread', portal_tab: 'courier' })
  clients.conversations.get = async () => { throw new Error('Not a participant') }
  await assert.rejects(adapter.execute({ kind: 'agent.chat', agentId: 'mercury', conversationId: 'other', message: 'Summarize' }), /participant/)
  assert.equal(calls.length, 1)
})

test('artifact rejects account scope change during preflight before dispatch', async () => {
  const { adapter, clients, calls } = artifactSetup()
  clients.conversations.getParticipants = async () => {
    clients.auth.me = async () => ({ id: 'other-user', role: 'artist' })
    return [{ user_id: 'real-user-id' }]
  }
  await assert.rejects(adapter.execute(selectSlot), /scope changed/)
  assert.deepEqual(calls, [])
})

test('thread resolves its own older booking independently of initial booking page', async () => {
  const { adapter, clients } = setup('collector')
  clients.conversations.get = async id => ({ id, booking_context: { id: 'older-booking' } })
  const thread = await adapter.readConversation('older-thread')
  assert.equal(thread.booking.id, 'older-booking')
  assert.equal(thread.booking.client_id, 'real-user-id')
  assert.equal(thread.bookingError, null)
  clients.bookings.get = async () => { throw new Error('Booking access denied') }
  const denied = await adapter.readConversation('older-thread')
  assert.equal(denied.booking, null)
  assert.equal(denied.bookingError, 'Booking access denied')
  assert.equal(denied.messages.length, 1)
})

test('client send and gallery upload reject scope drift immediately before write', async () => {
  const send = setup()
  send.clients.conversations.get = async id => {
    send.clients.auth.me = async () => ({ id: 'other-user', role: 'artist' })
    return { id }
  }
  await assert.rejects(send.adapter.execute({ kind: 'client.send', conversationId: 'thread-id', message: 'hello' }), /scope changed/)
  assert.deepEqual(send.calls, [])
  const upload = setup()
  let reads = 0
  upload.clients.auth.me = async () => ({ id: reads++ ? 'other-user' : 'real-user-id', role: 'artist' })
  await assert.rejects(upload.adapter.execute({ kind: 'gallery.upload', file: new File(['image'], 'image.png') }), /scope changed/)
  assert.deepEqual(upload.calls, [])
})

test('Studio edits preserve supported patch, original avatar file and exact gallery URL', async () => {
  const { adapter, calls } = setup()
  const changes = { bio: 'Artist biography', specialty: ['blackwork'], booking_buffer_minutes: 15 }
  const result = await adapter.execute({ kind: 'profile.update', changes })
  assert.deepEqual(calls[0], ['profile', changes])
  assert.equal(result.result.bio, changes.bio)
  const file = new File(['image'], 'portrait.png', { type: 'image/png' })
  const avatar = await adapter.execute({ kind: 'profile.avatar', file })
  assert.equal(calls[1][1], file)
  assert.equal(avatar.result.avatar_url, 'persisted-avatar-url')
  const removed = await adapter.execute({ kind: 'gallery.remove', url: 'https://assets.example.test/image.jpg' })
  assert.deepEqual(calls[2], ['remove', 'https://assets.example.test/image.jpg'])
  assert.deepEqual(removed.result.portfolio_images, [])
})

test('Booking settings reuse the profile patch and preserve all availability values', async () => {
  const { adapter, calls } = setup()
  const changes = {
    booking_slot_interval_minutes: 45,
    booking_buffer_minutes: 0,
    booking_min_notice_hours: 0,
    booking_max_advance_days: 365,
    booking_allow_same_day: false,
  }
  const receipt = await adapter.execute({ kind: 'profile.update', changes })
  assert.deepEqual(calls, [['profile', changes]])
  assert.equal(receipt.kind, 'profile.update')
  for (const [key, value] of Object.entries(changes)) assert.equal(receipt.result[key], value)
})

test('Studio rejects extra profile fields, fake files and foreign gallery URLs', async () => {
  const { adapter, calls } = setup()
  await assert.rejects(adapter.execute({ kind: 'profile.update', changes: { bio: 'ok', role: 'founder' } }), /Unsupported/)
  await assert.rejects(adapter.execute({ kind: 'profile.avatar', file: { name: 'fake.png', size: 5 } }), /nonempty image file/)
  await assert.rejects(adapter.execute({ kind: 'gallery.remove', url: 'https://other.example.test/image.jpg' }), /does not belong/)
  assert.deepEqual(calls, [])
})

test('Studio intents reject collector role and propagate provider failure', async () => {
  const restricted = setup('collector')
  for (const intent of [{ kind: 'profile.update', changes: { bio: 'test' } }, { kind: 'profile.avatar', file: new File(['x'], 'x.png') }, { kind: 'gallery.remove', url: 'url' }]) {
    await assert.rejects(restricted.adapter.execute(intent), /unavailable/)
  }
  assert.deepEqual(restricted.calls, [])
  const allowed = setup()
  allowed.clients.artists.updateProfile = async () => { throw new Error('Save failed') }
  await assert.rejects(allowed.adapter.execute({ kind: 'profile.update', changes: { bio: 'test' } }), /Save failed/)
})

test('Studio checks account scope immediately before profile writes', async () => {
  for (const intent of [{ kind: 'profile.update', changes: { bio: 'test' } }, { kind: 'profile.avatar', file: new File(['x'], 'x.png') }, { kind: 'gallery.remove', url: 'https://assets.example.test/image.jpg' }]) {
    const { adapter, clients, calls } = setup()
    let reads = 0
    clients.auth.me = async () => ({ id: reads++ ? 'other-user' : 'real-user-id', role: 'artist' })
    await assert.rejects(adapter.execute(intent), /scope changed/)
    assert.deepEqual(calls, [])
  }
})

test('newest-first backend page becomes oldest-first without losing the latest 40', async () => {
  const { adapter, clients } = setup()
  const newestFirst = Object.freeze(Array.from({ length: 60 }, (_, index) => ({
    id: `message-${60 - index}`,
    conversation_id: 'thread-id',
    created_at: new Date(Date.UTC(2026, 8, 9, 0, 60 - index)).toISOString(),
  })))
  clients.conversations.getMessages = async () => ({ messages: newestFirst, total: 120, has_more: true })
  const result = await adapter.readConversation('thread-id')
  assert.equal(result.messages[0].id, 'message-1')
  assert.equal(result.messages.at(-1).id, 'message-60')
  assert.equal(result.messages.slice(-40)[0].id, 'message-21')
  assert.equal(result.messages.slice(-40).at(-1).id, 'message-60')
  assert.ok(result.messages.every((message, index) => !index || message.created_at >= result.messages[index - 1].created_at))
  assert.equal(newestFirst[0].id, 'message-60')
  assert.notEqual(result.messages, newestFirst)
  assert.equal(result.total, 120)
  assert.equal(result.has_more, true)
})

test('upload batch initiating scope rejects second file after account changes', async () => {
  const { adapter, clients, calls } = setup()
  const expectedScope = { id: 'real-user-id', role: 'artist' }
  const intent = { kind: 'gallery.upload', file: new File(['image'], 'image.png') }
  await adapter.execute(intent, expectedScope)
  clients.auth.me = async () => ({ id: 'other-user', role: 'artist' })
  await assert.rejects(adapter.execute(intent, expectedScope), /scope changed/)
  assert.equal(calls.length, 1)
})

function confirmationSetup(role = 'artist') {
  const result = setup(role)
  const booking = { id: 'booking-id', artist_id: 'real-user-id', client_id: 'client-id' }
  const outcome = Object.freeze({
    booking_id: 'booking-id', truth_state: 'blocked', booking_status: 'confirmed',
    calendar_state: 'created', invitation_state: 'sent', meet_state: 'not_required',
    courier_state: 'failed', retry_eligible: true, updated_at: '2026-09-10T12:00:00Z',
    action_id: 'action-id', receipt_ids: ['receipt-id'], blocked_reasons: ['delivery_failed'],
    configured_mode: 'manual', effective_mode: 'manual', recommendation: 'Review delivery',
    explanation: 'Delivery failed', confidence: 'high', policy_version: 'test-policy',
    owner: 'booky', risk: 'low', required_approval: false, available_actions: [],
  })
  const history = Object.freeze([{ id: 'event-id', booking_id: 'booking-id', event_type: 'email_failed' }])
  result.clients.bookings.get = async id => { result.calls.push(['get', id]); return booking }
  result.clients.bookings.getBookyProjection = async id => { result.calls.push(['projection', id]); return outcome }
  result.clients.bookings.history = async id => { result.calls.push(['history', id]); return history }
  return { ...result, booking, outcome, history }
}

test('confirmation preserves exact staff projection and history without mutations', async () => {
  for (const role of ['artist', 'admin', 'founder']) {
    const { adapter, calls, booking, outcome, history } = confirmationSetup(role)
    const result = await adapter.readBookingConfirmation('booking-id', { id: 'real-user-id', role })
    assert.equal(result.booking, booking)
    assert.equal(result.outcome.status, 'ready')
    assert.equal(result.outcome.data, outcome)
    assert.equal(result.history.data, history)
    assert.deepEqual(calls, [['get', 'booking-id'], ['projection', 'booking-id'], ['history', 'booking-id']])
  }
})

test('assistant gets permitted history but an explicit unavailable projection', async () => {
  const { adapter, booking, calls } = confirmationSetup('assistant')
  booking.artist_id = 'other-artist'
  const result = await adapter.readBookingConfirmation('booking-id')
  assert.equal(result.outcome.status, 'blocked')
  assert.equal(result.outcome.data, null)
  assert.match(result.outcome.error, /unavailable/)
  assert.equal(result.history.status, 'ready')
  assert.deepEqual(calls, [['get', 'booking-id'], ['history', 'booking-id']])
})

test('collector gets own actual redacted outcome and never lifecycle history', async () => {
  const { adapter, clients, booking, calls } = confirmationSetup('collector')
  booking.client_id = 'real-user-id'
  booking.artist_id = 'other-artist'
  const outcome = Object.freeze({
    truth_state: 'completed', booking_status: 'confirmed', calendar_state: 'created',
    invitation_state: 'sent', meet_state: 'not_required', courier_state: 'sent',
    retry_eligible: false, updated_at: '2026-09-10T12:00:00Z',
  })
  clients.bookings.getBookyProjection = async id => { calls.push(['projection', id]); return outcome }
  const result = await adapter.readBookingConfirmation('booking-id')
  assert.equal(result.outcome.data, outcome)
  assert.equal(result.history.status, 'blocked')
  assert.deepEqual(calls, [['get', 'booking-id'], ['projection', 'booking-id']])
})

test('confirmation refuses foreign booking, mismatched identity, blank id and failed booking read', async () => {
  for (const role of ['artist', 'collector']) {
    const { adapter, booking, calls } = confirmationSetup(role)
    booking.artist_id = 'other-artist'
    await assert.rejects(adapter.readBookingConfirmation('booking-id'), /outside your account scope/)
    assert.deepEqual(calls, [['get', 'booking-id']])
  }
  const { adapter, clients, booking, calls } = confirmationSetup()
  await assert.rejects(adapter.readBookingConfirmation(' '), /existing booking/)
  assert.deepEqual(calls, [])
  booking.id = 'wrong-id'
  await assert.rejects(adapter.readBookingConfirmation('booking-id'), /identity mismatch/)
  clients.bookings.get = async () => { throw new Error('Booking unavailable') }
  await assert.rejects(adapter.readBookingConfirmation('booking-id'), /Booking unavailable/)
})

test('confirmation checks initiating scope before reads and changed scope before or after detail reads', async () => {
  for (const mismatch of [{ id: 'other' }, { role: 'admin' }, { proxy_for: 'other' }]) {
    const { adapter, calls } = confirmationSetup()
    await assert.rejects(adapter.readBookingConfirmation('booking-id', { id: 'real-user-id', role: 'artist', ...mismatch }), /scope changed/)
    assert.deepEqual(calls, [])
  }
  for (const changedAt of [1, 2]) {
    const { adapter, clients, calls } = confirmationSetup()
    let reads = 0
    clients.auth.me = async () => ({ id: 'real-user-id', role: 'artist', proxy_for: reads++ >= changedAt ? 'other' : undefined })
    await assert.rejects(adapter.readBookingConfirmation('booking-id'), /scope changed/)
    assert.equal(calls.length, changedAt === 1 ? 1 : 3)
  }
})

test('confirmation isolates failed detail reads and rejects wrong receipt identities', async () => {
  for (const method of ['getBookyProjection', 'history']) {
    const { adapter, clients } = confirmationSetup()
    clients.bookings[method] = async () => { throw new Error('Read denied') }
    const result = await adapter.readBookingConfirmation('booking-id')
    const failed = method === 'history' ? result.history : result.outcome
    const available = method === 'history' ? result.outcome : result.history
    assert.equal(failed.status, 'error')
    assert.equal(failed.data, null)
    assert.equal(failed.error, 'Read denied')
    assert.equal(available.status, 'ready')
  }
  const { adapter, clients } = confirmationSetup()
  clients.bookings.getBookyProjection = async () => ({ booking_id: 'wrong-id' })
  clients.bookings.history = async () => [{ booking_id: 'wrong-id' }]
  const result = await adapter.readBookingConfirmation('booking-id')
  assert.match(result.outcome.error, /identity mismatch/)
  assert.match(result.history.error, /identity mismatch/)
  clients.bookings.history = async () => []
  assert.equal((await adapter.readBookingConfirmation('booking-id')).history.status, 'empty')
})

test('late Studio responses are suppressed after a changed account scope', async () => {
  for (const [kind, method, extra] of [
    ['profile.update', 'updateProfile', { changes: { bio: 'Updated' } }],
    ['profile.avatar', 'uploadAvatar', { file: new File(['image'], 'image.png') }],
    ['gallery.upload', 'uploadGalleryImage', { file: new File(['image'], 'image.png') }],
    ['gallery.remove', 'removeGalleryImage', { url: 'https://assets.example.test/image.jpg' }],
  ]) {
    const { adapter, clients } = setup()
    clients.artists[method] = async () => {
      clients.auth.me = async () => ({ id: 'other-user', role: 'artist' })
      return { id: 'real-user-id' }
    }
    await assert.rejects(adapter.execute({ kind, ...extra }, { id: 'real-user-id', role: 'artist' }), /scope changed/)
  }
})
