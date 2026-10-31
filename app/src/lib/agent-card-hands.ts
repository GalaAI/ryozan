import type { AgentActionCard } from '@ryozan/ui'

export type NemotronManaPip = '⚡ Nano' | '👁️ Super-VL' | '🔥 Super' | '👑 Ultra'

export interface BanditOrchestraMember {
  id: string
  legacyId: string
  name: string
  moniker: string
  fullTitle: string
  star: string
  archetype: string
  domain: string
  role: string
  nemotronModel: string
  manaPip: NemotronManaPip
  accent: string
  rgb: string
  npcGreeting: string
}

export const RYOZAN_BANDIT_ORCHESTRA: readonly BanditOrchestraMember[] = [
  {
    id: 'gala',
    legacyId: 'gala',
    name: 'Gala',
    moniker: 'The Heavenly Sovereign',
    fullTitle: 'Gala · The Heavenly Sovereign',
    star: 'Tenkū · Sovereign Star',
    archetype: 'Mythic Sovereign Command',
    domain: 'Cross-Domain Maestro & Session Arbiter',
    role: 'Maestro · Sovereign Star',
    nemotronModel: 'nvidia/Nemotron-3-Ultra-550b-a55b',
    manaPip: '👑 Ultra',
    accent: 'var(--ukiyoe-kin-gold, #cba15b)',
    rgb: '203,161,91',
    npcGreeting: 'The 108 stars of Ryōzanpaku stand ready at the stronghold gates. Name the shop ruling you need sealed.',
  },
  {
    id: 'soko',
    legacyId: 'spyder',
    name: 'Sōkō',
    moniker: 'The Timely Rain',
    fullTitle: 'Sōkō (Song Jiang) · The Timely Rain',
    star: 'Tenkai · Chief Star',
    archetype: 'Legendary Creature: Bandit Conductor',
    domain: 'Ryozan Conductor & Back-of-House Shop Boss',
    role: 'Conductor · Chief Star',
    nemotronModel: 'nvidia/nemotron-3-super-120b-a12b',
    manaPip: '🔥 Super',
    accent: '#D9BF95',
    rgb: '217,191,149',
    npcGreeting: 'Every chair, needle, and ledger passes through the Hall of Loyalty. Which station needs the Timely Rain?',
  },
  {
    id: 'shishin',
    legacyId: 'xar',
    name: 'Shishin',
    moniker: 'Nine-Tattooed Dragon',
    fullTitle: 'Shishin (Shi Jin) · Nine-Tattooed Dragon',
    star: 'Tenfu · Tattooed Dragon Star',
    archetype: 'Artifact: Ledger & Flash Receipt',
    domain: 'Commerce, Flash Drops, Deposits & Ledger',
    role: 'Commerce · Tattooed Dragon Star',
    nemotronModel: 'nvidia/nemotron-3-super-120b-a12b',
    manaPip: '🔥 Super',
    accent: 'var(--ukiyoe-beni, #c24b2a)',
    rgb: '194,75,42',
    npcGreeting: 'Nine dragons coiled in sumi ink, and not a single cent unbalanced in ryozan-econ. Tap a ledger card to collect or audit.',
  },
  {
    id: 'ensei',
    legacyId: 'bella',
    name: 'Ensei',
    moniker: 'The Prodigy',
    fullTitle: 'Ensei (Yan Qing) · The Prodigy',
    star: 'Tenkō · Skilful Star',
    archetype: 'Instant: Client Intake Docket',
    domain: 'Front-Desk Ingress NPC & Client Triage',
    role: 'Front Desk · Skilful Star',
    nemotronModel: 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B',
    manaPip: '⚡ Nano',
    accent: 'var(--accent-coral, #d38a68)',
    rgb: '211,138,104',
    npcGreeting: 'Walk-ins, DMs, and first-timers greeted at the curtain before the ink dries. Who are we triaging next?',
  },
  {
    id: 'saishin',
    legacyId: 'booky',
    name: 'Saishin',
    moniker: 'Little Whirlwind',
    fullTitle: 'Saishin (Chai Jin) · Little Whirlwind',
    star: 'Tenki · Noble Star',
    archetype: 'Sorcery: Chair & Slot Ticket',
    domain: 'Scheduling, Chair Allocation & Calendar Sync',
    role: 'Scheduling · Noble Star',
    nemotronModel: 'nvidia/nemotron-3-super-120b-a12b',
    manaPip: '🔥 Super',
    accent: '#DD664E',
    rgb: '221,102,78',
    npcGreeting: 'My noble pass opens every chair in the studio. Pick a card to estimate hours, hold a 24h slot, or reflow the day.',
  },
  {
    id: 'taiso',
    legacyId: 'mercury',
    name: 'Taisō',
    moniker: 'The Magic Traveller',
    fullTitle: 'Taisō (Dai Zong) · The Magic Traveller',
    star: 'Tensoku · 800-Li Express Star',
    archetype: 'Instant: Express Courier Manifest',
    domain: 'Courier Multi-Channel Inbox (SMS, WhatsApp, IG, Email)',
    role: 'Courier · 800-Li Express Star',
    nemotronModel: 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B',
    manaPip: '⚡ Nano',
    accent: '#B795E8',
    rgb: '183,149,232',
    npcGreeting: 'Eight hundred li a day with talismans strapped to my ankles—SMS, WhatsApp, Instagram, and Email unified in one scroll.',
  },
  {
    id: 'rochishin',
    legacyId: 'smoku',
    name: 'Rochishin',
    moniker: 'The Tattooed Monk',
    fullTitle: 'Rochishin (Lu Zhishen) · The Tattooed Monk',
    star: 'Tenkō · Sagacious Star',
    archetype: 'Enchantment: Lore & Style Scroll',
    domain: 'Apprentice Guild, Ukiyo-e Style Guide & Visual Critique',
    role: 'Guild · Sagacious Star',
    nemotronModel: 'nvidia/Nemotron-3.5-Super-VL',
    manaPip: '👁️ Super-VL',
    accent: 'var(--ukiyoe-kin-gold, #cba15b)',
    rgb: '203,161,91',
    npcGreeting: 'Cherry blossoms tattooed across my shoulders and an iron staff in hand! Show me the placement or apprentice drill to inspect.',
  },
  {
    id: 'andozen',
    legacyId: 'docbot',
    name: 'Andōzen',
    moniker: 'The Divine Physician',
    fullTitle: 'Andōzen (An Daoquan) · The Divine Physician',
    star: 'Tenrei · Healer Star',
    archetype: 'Sorcery: Divine Heal Chart',
    domain: 'Aftercare, Waiver Compliance & Healing Check-Ins',
    role: 'Aftercare · Healer Star',
    nemotronModel: 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B',
    manaPip: '⚡ Nano',
    accent: '#7FC3B1',
    rgb: '127,195,177',
    npcGreeting: 'No needle touches skin without a sealed waiver, and no collector heals alone. Which chart shall we examine?',
  },
  {
    id: 'riki',
    legacyId: 'herald',
    name: 'Riki',
    moniker: 'The Black Whirlwind',
    fullTitle: 'Riki (Li Kui) · The Black Whirlwind',
    star: 'Tensatsu · Town Crier Star',
    archetype: 'Sorcery: Herald Proclamation',
    domain: 'Herald Social Publishing (Pinterest Ryozan, TikTok, IG)',
    role: 'Herald · Town Crier Star',
    nemotronModel: 'nvidia/nemotron-3-super-120b-a12b',
    manaPip: '🔥 Super',
    accent: '#8FB89A',
    rgb: '143,184,154',
    npcGreeting: 'Twin axes raised! Give the word and I will sound the studio proclamation across Pinterest, Instagram, and TikTok!',
  },
] as const

const RYOZAN_ALIAS_TO_CANONICAL: Record<string, string> = {
  gala: 'gala',
  soko: 'soko',
  spyder: 'soko',
  shishin: 'shishin',
  xar: 'shishin',
  ensei: 'ensei',
  bella: 'ensei',
  saishin: 'saishin',
  booky: 'saishin',
  taiso: 'taiso',
  mercury: 'taiso',
  rochishin: 'rochishin',
  smoku: 'rochishin',
  andozen: 'andozen',
  doc: 'andozen',
  docbot: 'andozen',
  riki: 'riki',
  herald: 'riki',
}

export function normalizeBanditId(agentId: string): string {
  return RYOZAN_ALIAS_TO_CANONICAL[agentId.trim().toLowerCase()] ?? agentId
}

export function getBanditMember(agentId: string): BanditOrchestraMember {
  const canonical = normalizeBanditId(agentId)
  return RYOZAN_BANDIT_ORCHESTRA.find(item => item.id === canonical || item.legacyId === agentId) ?? RYOZAN_BANDIT_ORCHESTRA[1]
}

/**
 * Every card names a tool the Ryozan API really runs (GALA_TOOLS in
 * api/app/api/v1/endpoints/gala_chat.py) and that this agent may call. A card
 * is sealed only by a succeeded receipt for that tool.
 */
function banditCard(agentId: string, id: string, name: string, description: string, shell: AgentActionCard['shell'], mcpTool: string | undefined, tag: string, oracleText: string): AgentActionCard {
  const member = getBanditMember(agentId)
  return { id, name, description, shell, mcpTool, oracleText, manaPip: member.manaPip, nemotronModel: member.nemotronModel, typeLine: `Bandit Action: [${member.fullTitle}] · ${tag}` }
}

const canonicalHands: Record<string, AgentActionCard[]> = {
  gala: [
    banditCard('gala', 'report-up', 'Report up', 'Pull the last 24 hours into one debrief.', 'heal-chart', 'get_daily_debrief', 'Sovereign Command', 'Read the daily debrief: new bookings, action items and the performance summary for the last 24 hours.'),
    banditCard('gala', 'read-books', 'Read the books', 'Total bookings, pending count and active clients.', 'receipt-tape', 'get_business_stats', 'Ledger Read', 'Read the studio statistics from live booking records: totals, pending requests and active clients.'),
    banditCard('gala', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
  soko: [
    banditCard('soko', 'read-board', 'Read the board', 'List the latest booking requests and their status.', 'intake-docket', 'lookup_bookings', 'Conductor Read', 'List the most recent bookings with status, client and dates.'),
    banditCard('soko', 'chase-stale', 'Chase stale requests', 'Find requests waiting more than 48 hours.', 'chair-ticket', 'get_stale_bookings', 'Conductor Follow-up', 'Find quotes and requests older than 48 hours, or past their proposed date, that still need an answer.'),
    banditCard('soko', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
  shishin: [
    banditCard('shishin', 'client-file', 'Client file', 'Open one client’s history and waiver state.', 'intake-docket', 'lookup_client', 'Client Read', 'Look up a client by name or email: booking history, waiver status and account details.'),
    banditCard('shishin', 'read-week', 'Read the week', 'Upcoming sessions for the next seven days.', 'chair-ticket', 'get_schedule', 'Schedule Read', 'Read confirmed, quoted and pending sessions from today forward.'),
    banditCard('shishin', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
  ensei: [
    banditCard('ensei', 'read-requests', 'Read requests', 'The newest booking requests in one list.', 'intake-docket', 'lookup_bookings', 'Intake Read', 'List the most recent booking requests with their status.'),
    banditCard('ensei', 'waiver-check', 'Waiver check', 'Is a client’s waiver pending, signed or approved?', 'heal-chart', 'get_waiver_status', 'Consent Read', 'Check a client’s waiver and document status.'),
    banditCard('ensei', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
  saishin: [
    // A local hours-by-rate calculator: no API tool, so it is never sealed.
    banditCard('saishin', 'estimate', 'Estimate', 'Price a session from hours and a rate.', 'receipt-tape', undefined, 'Local Estimate', 'Multiply estimated hours by your hourly rate. Nothing is saved, sent or quoted.'),
    banditCard('saishin', 'hold-slot', 'Hold a slot', 'Read the calendar, then open the next booking.', 'chair-ticket', 'get_schedule', 'Calendar Read', 'Read the sessions booked from today forward before preparing a slot hold. Nothing is confirmed from this card.'),
    banditCard('saishin', 'chase-stale', 'Chase stale requests', 'Bookings that need a follow-up.', 'intake-docket', 'get_stale_bookings', 'Calendar Follow-up', 'Find requests and quotes that have waited too long or passed their date.'),
    banditCard('saishin', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
  taiso: [
    banditCard('taiso', 'find-stalled', 'Find stalled bookings', 'Requests that went quiet.', 'intake-docket', 'get_stale_bookings', 'Courier Follow-up', 'Find requests and quotes that still need a reply.'),
    banditCard('taiso', 'send-notice', 'Send a notice', 'Post an in-app notification to a staff member.', 'courier-manifest', 'send_notification', 'Courier Notice', 'Send an in-app notification with a title, message and optional link.'),
    banditCard('taiso', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
  rochishin: [
    banditCard('rochishin', 'read-requests', 'Read requests', 'Booking requests with their descriptions.', 'intake-docket', 'lookup_bookings', 'Studio Read', 'List recent booking requests and their descriptions for review.'),
    banditCard('rochishin', 'read-week', 'Read the week', 'Sessions coming up this week.', 'chair-ticket', 'get_schedule', 'Schedule Read', 'Read the sessions booked from today forward.'),
    banditCard('rochishin', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
  andozen: [
    banditCard('andozen', 'waiver-check', 'Waiver check', 'Is a client’s waiver pending, signed or approved?', 'heal-chart', 'get_waiver_status', 'Care Read', 'Check a client’s waiver and document status.'),
    banditCard('andozen', 'read-week', 'Read the week', 'Sessions coming up, for aftercare timing.', 'chair-ticket', 'get_schedule', 'Schedule Read', 'Read the sessions booked from today forward.'),
    banditCard('andozen', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
  riki: [
    banditCard('riki', 'read-books', 'Read the numbers', 'Studio totals for an announcement.', 'receipt-tape', 'get_business_stats', 'Herald Read', 'Read the studio statistics from live booking records.'),
    banditCard('riki', 'send-notice', 'Send a notice', 'Post an in-app notification to a staff member.', 'courier-manifest', 'send_notification', 'Herald Notice', 'Send an in-app notification with a title, message and optional link.'),
    banditCard('riki', 'memento', 'Memento', 'Write something into this agent’s memory by hand.', 'memory-shard', 'save_memory', 'Memory Shard', 'Sealed when this agent saves a memory in chat. The card itself writes nothing.'),
  ],
}

export const agentCardHands: Record<string, AgentActionCard[]> = {
  ...canonicalHands,
  // Legacy Ryozan slot aliases resolve to the same Ryōzanpaku Bandit hands
  spyder: canonicalHands.soko,
  xar: canonicalHands.shishin,
  bella: canonicalHands.ensei,
  booky: canonicalHands.saishin,
  mercury: canonicalHands.taiso,
  smoku: canonicalHands.rochishin,
  docbot: canonicalHands.andozen,
  doc: canonicalHands.andozen,
  herald: canonicalHands.riki,
}
