import { useCallback, useEffect, useMemo, useState } from 'react'
import { usePlaidLink } from 'react-plaid-link'
import { Link } from 'react-router-dom'
import { useToast } from '../../components/Toast'
import {
  econ as econApi,
  googleAuth,
  plaid as plaidApi,
  pos as posApi,
  stash as stashApi,
  stripeConnect,
  users as usersApi,
  type PlaidBalanceResponse,
  type EconIntegrationsStatus,
  type EconDiagnosticsResponse,
  type EconLedgerEntry,
  type EconLedgerSummary,
  type EconPayrollTaxSummary,
  type EconProviderSetup,
  type EconSyncRun,
  type POSIntegrationSummary,
  type User,
} from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { useStudioSettings } from '../../hooks/useStudioSettings'
import FlashManager, { type FlashStage } from './FlashManager'

type ConnectorStatus = 'active' | 'pending' | 'planned'
type StashTab = 'flash' | 'crafts' | 'digital' | 'econ' | 'integrations'
type EconPage = 'shopfront' | 'command' | 'rails'

interface HustleConnector {
  id: string
  label: string
  value: string
  desc: string
  status: ConnectorStatus
  actionLabel: string
}

const STATUS_ITEMS: HustleConnector[] = [
  {
    id: 'stash',
    label: 'Inventory',
    value: 'Stash',
    desc: 'AI assisted inventory, claims, provenance, reorder signals, and connector sync.',
    status: 'pending',
    actionLabel: 'Configure',
  },
  {
    id: 'payments',
    label: 'Payments',
    value: 'Stripe online / Clover in-store',
    desc: 'Stripe owns online checkout. Clover stays in-store and is monitored through QuickBooks and Mercury until POS import is worth revisiting.',
    status: 'pending',
    actionLabel: 'Connect',
  },
  {
    id: 'google-business',
    label: 'Google Business',
    value: 'Workspace/GBP/GA4',
    desc: 'Workspace identity, Business Profile discovery, Search Console visibility, GA4 conversion tracking, and Ads readiness.',
    status: 'pending',
    actionLabel: 'Configure',
  },
  {
    id: 'storage',
    label: 'Storage',
    value: 'Vault/R2/Drive',
    desc: 'Production assets stay on the Ryozan storage path. Google Drive and Dropbox can become import/export connectors.',
    status: 'pending',
    actionLabel: 'Authorize',
  },
  {
    id: 'embed',
    label: 'Embed',
    value: 'Contract planned',
    desc: 'Future embeddable Hustle storefront and Stash inventory widgets.',
    status: 'planned',
    actionLabel: 'Plan',
  },
]

const PUBLISH_CHECKLIST = [
  ['Profile', 'Avatar, bio, contact'],
  ['Inventory', 'Priced published work'],
  ['Booking', 'Huddl path active'],
  ['Payment', 'Stripe connected'],
]

const workspaceIdentityItem = (contactEmail: string) => ({
  label: 'Workspace identity',
  value: contactEmail || 'Not set',
  note: contactEmail
    ? 'Primary operator account for Business Profile, Search Console, GA4, Ads, Drive, Gmail, and Calendar ownership.'
    : 'Set SHOP_EMAIL to the account that owns Business Profile, Search Console, GA4, Ads, Drive, Gmail, and Calendar.',
})

const GOOGLE_STACK_ITEMS = [
  {
    label: 'Business Profile',
    value: 'Discovery',
    note: 'Track Search and Maps views, calls, website clicks, directions, bookings, and message intent against real booking outcomes.',
  },
  {
    label: 'GA4 web stream',
    value: 'Property mapping required',
    note: 'Google Analytics is awaiting property and privacy verification. Public site reporting uses Vercel.',
  },
  {
    label: 'Search Console',
    value: 'SEO/AISO',
    note: 'Connect domain performance to booking pages, artist pages, flash drops, local intent queries, and rich-result health.',
  },
  {
    label: 'Google Ads',
    value: 'Ready next',
    note: 'Import GA4 key events before spend so campaigns optimize toward bookings and deposits instead of raw traffic.',
  },
]

const GOOGLE_CONVERSION_ITEMS = [
  ['generate_lead', 'Public booking submit, quote request, consultation request'],
  ['begin_checkout', 'Deposit or flash checkout started'],
  ['purchase', 'Deposit, flash sale, merch, or paid product completed'],
  ['sign_up', 'Collector account or club join'],
  ['search', 'Site, shop, flash, artist, and reference searches'],
]

const GOOGLE_OPERATIONS_ITEMS = [
  ['Discoverability', 'Search Console queries plus Business Profile interactions show what people want before they book.'],
  ['Booking conversion', 'GA4 funnels connect artist pages, booking steps, lead submits, deposits, and abandoned paths.'],
  ['Revenue attribution', 'Stripe checkout, QuickBooks, Mercury, and purchase events tie traffic sources to deposit and product revenue.'],
  ['Workspace leverage', 'Gmail, Calendar, Drive, Docs, Sheets, and Looker Studio become the shared operating layer for the owner, artists, and staff.'],
]

const money = (cents: number) => `$${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const syncRunNote = (run?: EconSyncRun | null) => {
  if (!run) return null
  const date = run.finished_at || run.started_at
  const when = date ? new Date(date).toLocaleDateString() : 'recently'
  const changed = run.added_count + run.modified_count + run.removed_count
  if (run.status === 'error') {
    return `Last sync failed ${when}${run.error ? `: ${run.error.slice(0, 80)}` : ''}`
  }
  return `Last ${run.status} ${when} | ${changed} changes`
}

const missingSetupNote = (missing?: string[], setup?: EconProviderSetup) => {
  if (!missing?.length) return null
  const expected = setup?.required_env?.length ? ` | expected Railway vars: ${setup.required_env.join(', ')}` : ''
  return `Missing ${missing.join(', ')}${expected}`
}

const openExternal = (url: string) => {
  window.open(url, '_blank', 'noopener,noreferrer')
}

// Hustl's finance surfaces must all report the same window. posApi.summary
// already defaulted to year-to-date server-side while the E-con summaries
// defaulted to all time, so the page showed a YTD POS figure beside an all-time
// E-con figure. Pass one explicit period to all three instead of relying on
// three different defaults.
const hustlPeriod = () => {
  const now = new Date()
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  return { period_start: iso(new Date(now.getFullYear(), 0, 1)), period_end: iso(now) }
}

export default function HustlePage() {
  const currentUser = useAuthStore((s) => s.user)
  const { settings: studioSettings } = useStudioSettings()
  const toast = useToast()
  const urlParams = useMemo(() => new URLSearchParams(window.location.search), [])
  const initialStashTab = useMemo<StashTab>(() => {
    const stash = (urlParams.get('stash') || '').toLowerCase()
    const canOpenShopRails = currentUser?.role === 'founder' || currentUser?.role === 'admin'
    const allowed: StashTab[] = canOpenShopRails
      ? ['flash', 'crafts', 'digital', 'econ', 'integrations']
      : ['flash', 'crafts', 'digital']
    if (allowed.includes(stash as StashTab)) return stash as StashTab
    if ((urlParams.get('focus') || '').toLowerCase() === 'deposits' && canOpenShopRails) return 'econ'
    return 'flash'
  }, [currentUser?.role, urlParams])
  const initialEconPage = useMemo<EconPage>(() => {
    const page = (urlParams.get('page') || '').toLowerCase()
    const allowed: EconPage[] = ['shopfront', 'command', 'rails']
    if (allowed.includes(page as EconPage)) return page as EconPage
    if ((urlParams.get('focus') || '').toLowerCase() === 'deposits') return 'command'
    return 'shopfront'
  }, [urlParams])
  const [activeStashTab, setActiveStashTab] = useState<StashTab>(initialStashTab)
  const [econPage, setEconPage] = useState<EconPage>(initialEconPage)
  const [creatorRoster, setCreatorRoster] = useState<User[]>([])
  const [selectedCreatorId, setSelectedCreatorId] = useState<string>('')
  const [connectorPanel, setConnectorPanel] = useState<string>('stash')
  const [plaidBusy, setPlaidBusy] = useState(false)
  const [plaidLinkToken, setPlaidLinkToken] = useState<string | null>(null)
  const [stripeConnected, setStripeConnected] = useState(false)
  const [googleConnected, setGoogleConnected] = useState(false)
  const [plaidConnected, setPlaidConnected] = useState(false)
  const [plaidBalance, setPlaidBalance] = useState<PlaidBalanceResponse | null>(null)
  const [econSummary, setEconSummary] = useState<POSIntegrationSummary | null>(null)
  const [econStatus, setEconStatus] = useState<EconIntegrationsStatus | null>(null)
  const [econDiagnostics, setEconDiagnostics] = useState<EconDiagnosticsResponse | null>(null)
  const [econSyncRuns, setEconSyncRuns] = useState<EconSyncRun[]>([])
  const [econLedger, setEconLedger] = useState<EconLedgerEntry[]>([])
  const [econLedgerSummary, setEconLedgerSummary] = useState<EconLedgerSummary | null>(null)
  const [econPayrollTaxSummary, setEconPayrollTaxSummary] = useState<EconPayrollTaxSummary | null>(null)
  const [econLoading, setEconLoading] = useState(false)
  const [plaidSyncCount, setPlaidSyncCount] = useState<number | null>(null)
  const [mercuryAccountCount, setMercuryAccountCount] = useState<number | null>(null)
  const [mercuryTxnCount, setMercuryTxnCount] = useState<number | null>(null)
  const [selectedMercuryAccountId, setSelectedMercuryAccountId] = useState('')
  const [flashStage, setFlashStage] = useState<FlashStage>('upload')

  const canSwitchCreator = currentUser?.role === 'founder' || currentUser?.role === 'admin'

  // Econ and Integrations are founder/admin tabs; anyone else falls back to Flash.
  if (!canSwitchCreator && (activeStashTab === 'econ' || activeStashTab === 'integrations')) {
    setActiveStashTab('flash')
  }

  const onPlaidSuccess = useCallback(async (publicToken: string) => {
    try {
      await plaidApi.exchangePublicToken(publicToken)
      const balance = await plaidApi.balance().catch(() => null)
      if (balance) setPlaidBalance(balance)
      setPlaidConnected(true)
      toast.success('Plaid bank connection linked')
    } catch {
      toast.error('Failed to connect Plaid')
    } finally {
      setPlaidBusy(false)
      setPlaidLinkToken(null)
    }
  }, [toast])

  const { open: openPlaidLink, ready: plaidReady } = usePlaidLink({
    token: plaidLinkToken,
    onSuccess: onPlaidSuccess,
    onExit: () => {
      setPlaidBusy(false)
      setPlaidLinkToken(null)
    },
  })

  useEffect(() => {
    if (plaidLinkToken && plaidReady) openPlaidLink()
  }, [plaidLinkToken, plaidReady, openPlaidLink])

  useEffect(() => {
    if (!currentUser || !canSwitchCreator) return

    let cancelled = false
    const loadCreators = async () => {
      try {
        return await stashApi.listCreators()
      } catch {
        const users = await usersApi.list({ limit: 200 })
        return users.filter((user) => ['artist', 'admin', 'founder'].includes(user.role))
      }
    }

    loadCreators()
      .then((roster) => {
        if (cancelled) return
        const creators = roster
          .sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email))
        const defaultCreatorId = creators.find((creator) => creator.id === currentUser.proxy_for)?.id
          ?? creators.find((creator) => creator.id === currentUser.id)?.id
          ?? creators[0]?.id
          ?? currentUser.id
        setCreatorRoster(creators)
        setSelectedCreatorId((current) => creators.some((creator) => creator.id === current) ? current : defaultCreatorId)
      })
      .catch(() => {
        if (cancelled) return
        setCreatorRoster([currentUser])
        setSelectedCreatorId(currentUser.id)
      })

    return () => {
      cancelled = true
    }
  }, [canSwitchCreator, currentUser])

  useEffect(() => {
    if (!currentUser) return
    let cancelled = false
    Promise.allSettled([
      stripeConnect.status(),
      googleAuth.getStatus(),
      plaidApi.status(),
      plaidApi.balance().catch(() => null),
      posApi.summary(hustlPeriod()).catch(() => null),
      econApi.status().catch(() => null),
      econApi.diagnostics({ live: false }).catch(() => null),
      econApi.syncRuns({ limit: 6 }).catch(() => []),
      econApi.ledger({ limit: 8 }).catch(() => []),
      econApi.ledgerSummary(hustlPeriod()).catch(() => null),
      econApi.payrollTaxSummary(hustlPeriod()).catch(() => null),
    ]).then(([stripe, google, plaid, balance, summary, econ, diagnostics, syncRuns, ledger, ledgerSummary, payrollTaxSummary]) => {
      if (cancelled) return
      if (stripe.status === 'fulfilled') setStripeConnected(Boolean(stripe.value.charges_enabled || stripe.value.connected))
      if (google.status === 'fulfilled') setGoogleConnected(Boolean(google.value.connected))
      if (plaid.status === 'fulfilled') setPlaidConnected(Boolean(plaid.value.connected))
      if (balance.status === 'fulfilled' && balance.value) setPlaidBalance(balance.value)
      if (summary.status === 'fulfilled' && summary.value) setEconSummary(summary.value)
      if (econ.status === 'fulfilled' && econ.value) setEconStatus(econ.value)
      if (diagnostics.status === 'fulfilled' && diagnostics.value) setEconDiagnostics(diagnostics.value)
      if (syncRuns.status === 'fulfilled') setEconSyncRuns(syncRuns.value)
      if (ledger.status === 'fulfilled') setEconLedger(ledger.value)
      if (ledgerSummary.status === 'fulfilled' && ledgerSummary.value) setEconLedgerSummary(ledgerSummary.value)
      if (payrollTaxSummary.status === 'fulfilled' && payrollTaxSummary.value) setEconPayrollTaxSummary(payrollTaxSummary.value)
    }).catch(() => {})
    return () => {
      cancelled = true
    }
  }, [currentUser])

  // Opening Econ (or a new signed-in user on it) starts a load; the flag turns
  // on during render and the effect below fetches.
  const [econRequestSeen, setEconRequestSeen] = useState<readonly unknown[] | null>(null)
  if (!econRequestSeen || econRequestSeen[0] !== activeStashTab || econRequestSeen[1] !== currentUser) {
    setEconRequestSeen([activeStashTab, currentUser])
    if (currentUser && activeStashTab === 'econ') setEconLoading(true)
  }

  useEffect(() => {
    if (!currentUser || activeStashTab !== 'econ') return
    let cancelled = false
    Promise.allSettled([
      posApi.summary(hustlPeriod()),
      plaidApi.balance().catch(() => null),
      econApi.status().catch(() => null),
      econApi.diagnostics({ live: false }).catch(() => null),
      econApi.syncRuns({ limit: 6 }).catch(() => []),
      econApi.ledger({ limit: 8 }).catch(() => []),
      econApi.ledgerSummary(hustlPeriod()).catch(() => null),
      econApi.payrollTaxSummary(hustlPeriod()).catch(() => null),
    ]).then(([summary, balance, econ, diagnostics, syncRuns, ledger, ledgerSummary, payrollTaxSummary]) => {
      if (cancelled) return
      if (summary.status === 'fulfilled') setEconSummary(summary.value)
      if (balance.status === 'fulfilled' && balance.value) setPlaidBalance(balance.value)
      if (econ.status === 'fulfilled' && econ.value) setEconStatus(econ.value)
      if (diagnostics.status === 'fulfilled' && diagnostics.value) setEconDiagnostics(diagnostics.value)
      if (syncRuns.status === 'fulfilled') setEconSyncRuns(syncRuns.value)
      if (ledger.status === 'fulfilled') setEconLedger(ledger.value)
      if (ledgerSummary.status === 'fulfilled' && ledgerSummary.value) setEconLedgerSummary(ledgerSummary.value)
      if (payrollTaxSummary.status === 'fulfilled' && payrollTaxSummary.value) setEconPayrollTaxSummary(payrollTaxSummary.value)
    }).catch(() => {})
      .finally(() => {
        if (!cancelled) setEconLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [activeStashTab, currentUser])

  // Until an account is chosen, the first Mercury account is selected.
  if (!selectedMercuryAccountId) {
    const firstAccountId = (econStatus?.mercury.accounts ?? []).find((account) => account.id)?.id
    if (firstAccountId) setSelectedMercuryAccountId(firstAccountId)
  }

  const effectiveCreatorRoster = useMemo(
    () => canSwitchCreator ? creatorRoster : currentUser ? [currentUser] : [],
    [canSwitchCreator, creatorRoster, currentUser]
  )

  const effectiveSelectedCreatorId = canSwitchCreator
    ? selectedCreatorId
    : currentUser?.id ?? ''

  const selectedCreator = useMemo(
    () => effectiveCreatorRoster.find((creator) => creator.id === effectiveSelectedCreatorId) ?? currentUser ?? null,
    [currentUser, effectiveCreatorRoster, effectiveSelectedCreatorId]
  )

  const selectedCreatorName = selectedCreator?.name || selectedCreator?.email || ''
  const selectedHandle = selectedCreatorName ? selectedCreatorName.split(/\s+/)[0].toLowerCase() : 'creator'
  const mercuryAccounts = econStatus?.mercury.accounts ?? []

  const stashTabs: Array<{ id: StashTab; label: string }> = [
    { id: 'flash', label: 'Flash' },
    { id: 'crafts', label: 'Crafts' },
    { id: 'digital', label: 'Digital' },
    ...(canSwitchCreator ? [
      { id: 'econ' as const, label: 'Econ' },
      { id: 'integrations' as const, label: 'Integrations' },
    ] : []),
  ]

  const econPages: Array<{ id: EconPage; label: string }> = [
    { id: 'shopfront', label: 'Shopfront' },
    { id: 'command', label: 'Command' },
    { id: 'rails', label: 'Rails' },
  ]

  const openArtistConnections = () => {
    window.location.assign('/dashboard?portal=hub&workflow=connections')
  }

  const handleGoogleConnect = async () => {
    try {
      const { authorization_url } = await googleAuth.getConnectUrl()
      window.location.assign(authorization_url)
    } catch {
      toast.error('Failed to start Google connection')
    }
  }

  const handlePlaidConnect = async () => {
    setPlaidBusy(true)
    try {
      const data = await plaidApi.createLinkToken()
      setPlaidLinkToken(data.link_token)
    } catch {
      toast.error('Failed to start Plaid connection')
      setPlaidBusy(false)
    }
  }

  const handlePlaidSync = async () => {
    setPlaidBusy(true)
    try {
      const data = await plaidApi.syncTransactions({ count: 25 })
      setPlaidSyncCount(data.added.length + data.modified.length)
      const [ledger, ledgerSummary, payrollTaxSummary, status] = await Promise.all([
        econApi.ledger({ limit: 8 }).catch(() => []),
        econApi.ledgerSummary(hustlPeriod()).catch(() => null),
        econApi.payrollTaxSummary(hustlPeriod()).catch(() => null),
        econApi.status().catch(() => null),
      ])
      setEconLedger(ledger)
      if (ledgerSummary) setEconLedgerSummary(ledgerSummary)
      if (payrollTaxSummary) setEconPayrollTaxSummary(payrollTaxSummary)
      if (status) setEconStatus(status)
      setEconSyncRuns(await econApi.syncRuns({ limit: 6 }).catch(() => []))
      toast.success(`Plaid sync returned ${data.added.length + data.modified.length} updates`)
    } catch {
      toast.error('Plaid transaction sync failed')
    } finally {
      setPlaidBusy(false)
    }
  }

  const handleMercuryTransactions = async () => {
    try {
      const data = await econApi.mercuryTransactions({
        limit: 25,
        account_id: selectedMercuryAccountId || undefined,
      })
      const transactions = typeof data === 'object' && data && 'transactions' in data
        ? (data as { transactions?: unknown[] }).transactions
        : typeof data === 'object' && data && 'data' in data
          ? (data as { data?: unknown[] }).data
          : null
      setMercuryTxnCount(Array.isArray(transactions) ? transactions.length : 0)
      const [ledger, ledgerSummary, payrollTaxSummary, status] = await Promise.all([
        econApi.ledger({ limit: 8 }).catch(() => []),
        econApi.ledgerSummary(hustlPeriod()).catch(() => null),
        econApi.payrollTaxSummary(hustlPeriod()).catch(() => null),
        econApi.status().catch(() => null),
      ])
      setEconLedger(ledger)
      if (ledgerSummary) setEconLedgerSummary(ledgerSummary)
      if (payrollTaxSummary) setEconPayrollTaxSummary(payrollTaxSummary)
      if (status) setEconStatus(status)
      setEconSyncRuns(await econApi.syncRuns({ limit: 6 }).catch(() => []))
      toast.success('Mercury transaction read completed')
    } catch {
      toast.error('Mercury transaction read failed')
    }
  }

  const handleMercuryAccounts = async () => {
    try {
      const data = await econApi.mercuryAccounts()
      const accounts = typeof data === 'object' && data && 'accounts' in data
        ? (data as { accounts?: unknown[] }).accounts
        : typeof data === 'object' && data && 'data' in data
          ? (data as { data?: unknown[] }).data
          : null
      setMercuryAccountCount(Array.isArray(accounts) ? accounts.length : 0)
      const status = await econApi.status().catch(() => null)
      if (status) setEconStatus(status)
      setEconSyncRuns(await econApi.syncRuns({ limit: 6 }).catch(() => []))
      toast.success('Mercury account read completed')
    } catch {
      toast.error('Mercury account read failed')
    }
  }

  const handleEconSyncAll = async () => {
    setEconLoading(true)
    try {
      const sync = await econApi.sync({
        providers: ['plaid', 'mercury'],
        limit: 25,
        persist: true,
        mercury_account_id: selectedMercuryAccountId || undefined,
      })
      setPlaidSyncCount(sync.results.find((result) => result.provider === 'plaid')?.updates ?? null)
      const mercuryResult = sync.results.find((result) => result.provider === 'mercury')
      const mercuryAccounts = mercuryResult?.metadata.accounts
      const mercuryTransactions = mercuryResult?.metadata.transactions
      setMercuryAccountCount(typeof mercuryAccounts === 'number' ? mercuryAccounts : null)
      setMercuryTxnCount(typeof mercuryTransactions === 'number' ? mercuryTransactions : null)

      const [ledger, ledgerSummary, payrollTaxSummary, status] = await Promise.all([
        econApi.ledger({ limit: 8 }).catch(() => []),
        econApi.ledgerSummary(hustlPeriod()).catch(() => null),
        econApi.payrollTaxSummary(hustlPeriod()).catch(() => null),
        econApi.status().catch(() => null),
      ])
      setEconLedger(ledger)
      if (ledgerSummary) setEconLedgerSummary(ledgerSummary)
      if (payrollTaxSummary) setEconPayrollTaxSummary(payrollTaxSummary)
      if (status) setEconStatus(status)
      setEconSyncRuns(await econApi.syncRuns({ limit: 6 }).catch(() => []))
      const failed = sync.results
        .filter((result) => result.status === 'error')
        .map((result) => result.provider)
      const skipped = sync.results
        .filter((result) => result.status === 'skipped')
        .map((result) => result.provider)
      if (failed.length) {
        toast.error(`E-con sync finished with failures: ${failed.join(', ')}`)
      } else if (skipped.length === sync.results.length) {
        toast.info('No configured E-con rails are ready to sync')
      } else {
        toast.success('E-con rails synced')
      }
    } catch {
      toast.error('E-con sync failed')
    } finally {
      setEconLoading(false)
    }
  }

  const handleEconDiagnostics = async () => {
    setEconLoading(true)
    try {
      const diagnostics = await econApi.diagnostics({ live: true })
      setEconDiagnostics(diagnostics)
      if (diagnostics.status === 'pass') {
        toast.success('Payment rail diagnostics passed')
      } else if (diagnostics.status === 'fail') {
        toast.error('Payment rail diagnostics failed')
      } else {
        toast.info('Payment rail diagnostics need setup')
      }
    } catch {
      toast.error('Payment rail diagnostics failed')
    } finally {
      setEconLoading(false)
    }
  }

  const queueConnectorSpec = (connector: string) => {
    window.dispatchEvent(new CustomEvent('hotbar-action', {
      detail: {
        action: 'agent-assist',
        agentId: 'xar',
        message: `Xar, draft the implementation plan for the Hustle ${connector} connector. Include OAuth scopes, data model, API endpoints, security boundaries, Ryozan production constraints, Stash/Vault/Nexus relationships, staff approval flow, and the checks needed before deploy.`,
      },
    }))
    toast.info(`${connector} connector plan queued for Xar`)
  }

  const queueStashSpec = (lane: string) => {
    window.dispatchEvent(new CustomEvent('hotbar-action', {
      detail: {
        action: 'agent-assist',
        agentId: 'xar',
        message: `Xar, draft the ${lane} Stash implementation plan for Hustle. Include creator-scoped data models, upload and asset storage, AI assisted metadata, pricing guidance, publish approval, Gala Sync opt-in memory boundaries, Nexus profile context, AIvatar profile signals, and Ryozan API checks.`,
      },
    }))
    toast.info(`${lane} Stash plan queued for Xar`)
  }

  const connectorDetails = {
    stash: {
      title: 'Stash inventory layer',
      copy: 'Stash should become the Gala inventory product under Hustle: stock, flash sheets, claims, SKUs, supplies, provenance, reorder cues, cost signals, creator inventory, and AI-assisted listing readiness.',
      actions: [
        { label: 'Draft Stash spec', onClick: () => queueConnectorSpec('Stash inventory') },
      ],
    },
    payments: {
      title: 'Payment connectors',
      copy: 'Stripe is the default online checkout rail for bookings, flash deposits, merch, and digital products. If a creator has not finished Stripe onboarding, Hustle keeps them on a payroll fallback path and pushes Stripe setup while Mercury and QuickBooks cover banking, planning, bookkeeping, contractor records, and approval batches.',
      actions: [
        { label: 'Open artist Connections', onClick: openArtistConnections },
        { label: plaidBusy ? 'Opening Plaid...' : plaidConnected ? 'Reconnect Plaid' : 'Connect Plaid', onClick: handlePlaidConnect },
        { label: 'Read Mercury', onClick: handleMercuryAccounts },
      ],
    },
    'google-business': {
      title: 'Google business management suite',
      copy: 'Run Ryozan through the Workspace account, then connect Business Profile, GA4, Search Console, Ads, and booking revenue signals into one measurable operating loop.',
      actions: [
        { label: googleConnected ? 'Google Connected' : 'Connect Google', onClick: handleGoogleConnect },
        { label: 'Open Analytics', onClick: () => openExternal('https://analytics.google.com/analytics/web/') },
        { label: 'Open Business Profile', onClick: () => openExternal('https://business.google.com/') },
        { label: 'Open Search Console', onClick: () => openExternal('https://search.google.com/search-console') },
        { label: 'Open Workspace Admin', onClick: () => openExternal('https://admin.google.com/') },
      ],
    },
    storage: {
      title: 'Storage connectors',
      copy: 'Ryozan production assets should continue using the existing cloud asset path. Google Drive and Dropbox fit best as import/export connectors into Vault and Stash, not replacements for production storage.',
      actions: [
        { label: googleConnected ? 'Google Connected' : 'Connect Google', onClick: handleGoogleConnect },
        { label: 'Plan Dropbox OAuth', onClick: () => queueConnectorSpec('Dropbox storage') },
      ],
    },
    embed: {
      title: 'Embeddable surfaces',
      copy: 'Hustle can expose storefront and Stash inventory widgets later, with Ryozan auth, checkout, storage, legal, and analytics staying behind the approved API contracts.',
      actions: [
        { label: 'Plan embed contract', onClick: () => queueConnectorSpec('Hustle embed') },
      ],
    },
  }[connectorPanel] ?? null

  const plaidCurrent = plaidBalance?.accounts?.reduce((sum, account) => sum + (account.balances.current ?? 0), 0) ?? null
  const ledgerIncomeCents = econLedgerSummary?.buckets
    .filter((bucket) => bucket.bucket === 'income')
    .reduce((sum, bucket) => sum + bucket.amount_cents, 0) ?? 0
  const ledgerExpenseCents = econLedgerSummary?.buckets
    .filter((bucket) => bucket.bucket === 'expense')
    .reduce((sum, bucket) => sum + bucket.amount_cents, 0) ?? 0
  const projectedTaxReserveCents = econPayrollTaxSummary?.estimated_tax_reserve_cents
    ?? econSummary?.estimated_tax_reserve_cents
    ?? 0
  const projectedNetTaxableCents = econPayrollTaxSummary?.net_taxable_cents ?? 0
  const uncategorizedTaxCents = econPayrollTaxSummary?.uncategorized_cents ?? 0
  const econAlerts = [
    !stripeConnected ? 'Stripe Connect is not active for this creator.' : null,
    econStatus?.plaid.ready_to_sync === false ? `Plaid setup incomplete: ${econStatus.plaid.missing.join(', ')}` : null,
    econStatus?.mercury.ready_to_sync === false ? `Mercury setup incomplete: ${econStatus.mercury.missing.join(', ')}` : null,
    uncategorizedTaxCents ? `${money(Math.abs(uncategorizedTaxCents))} needs payroll/tax categorization.` : null,
    !econSummary?.transaction_count ? 'No POS transactions recorded for the selected period.' : null,
    !econLedger.length ? 'No normalized E-con ledger entries stored yet.' : null,
  ].filter(Boolean) as string[]

  return (
    <div className="dashboard-panel home-panel nexus hustle-page hustle-page--cockpit">
      <div className="hustle-cockpit">
        <main className="hustle-tab-panel" role="tabpanel">
          <section className="hustle-workspace-header NAV_BAR" aria-label="Hustle workspace controls">
            <div className="hustle-workspace-nav NAV_BAR__toolbar" role="tablist" aria-label="Stash inventory lanes">
                {stashTabs.map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      role="tab"
                      aria-selected={activeStashTab === tab.id}
                      className={`NAV_BAR__item hustle-workspace-tab ${activeStashTab === tab.id ? 'active' : ''}`}
                      onClick={() => setActiveStashTab(tab.id)}
                    >
                      <span className="NAV_BAR__label">{tab.label}</span>
                    </button>
                ))}
            </div>
          </section>

          <div className="hustle-stash-workspace">
            {activeStashTab === 'flash' && (
              <div className="hustle-flash-lab">
                <section id="flash-inventory" className="hustle-inventory hustle-inventory--workbench">
                  <FlashManager
                    artistId={canSwitchCreator ? effectiveSelectedCreatorId : undefined}
                    compact
                    stage={flashStage}
                    onStageChange={setFlashStage}
                  />
                </section>
              </div>
            )}

              {activeStashTab === 'crafts' && (
                <section className="hustle-lane-empty">
                  <p className="hustle-kicker">Physical goods</p>
                  <h3>Crafts inventory</h3>
                  <span className="hustle-muted">
                    This lane should handle merch, prints, originals, packed goods, supplies, stock counts, shipping status, and storefront availability.
                  </span>
                  <div className="hustle-lane-grid">
                    <div><strong>Catalog</strong><span>Products, variants, images, dimensions, and SKU rules.</span></div>
                    <div><strong>Fulfillment</strong><span>Inventory counts, pickup, shipping, and claim handoff.</span></div>
                    <div><strong>Intelligence</strong><span>Xar can suggest pricing, bundles, restock cues, and campaign timing.</span></div>
                  </div>
                  <button className="hustle-plan-button" onClick={() => queueStashSpec('Crafts')}>
                    Draft Crafts data model
                  </button>
                </section>
              )}

              {activeStashTab === 'digital' && (
                <section className="hustle-lane-empty">
                  <p className="hustle-kicker">Digital products</p>
                  <h3>Digital drops</h3>
                  <span className="hustle-muted">
                    This lane should manage downloads, digital reference packs, aftercare content, flash claim files, gated access, license terms, and Vault delivery.
                  </span>
                  <div className="hustle-lane-grid">
                    <div><strong>Library</strong><span>Files, previews, watermarks, versions, and access control.</span></div>
                    <div><strong>Delivery</strong><span>Vault-backed fulfillment, purchase receipts, and collector Loot sync.</span></div>
                    <div><strong>Signals</strong><span>Xar can track demand, AISO copy, SEO metadata, and conversion tests.</span></div>
                  </div>
                  <button className="hustle-plan-button" onClick={() => queueStashSpec('Digital')}>
                    Draft Digital data model
                  </button>
                </section>
              )}

              {activeStashTab === 'econ' && (
                <div className="hustle-econ-workspace">
                  <div className="hustle-econ-subnav" role="tablist" aria-label="Econ subpages">
                    {econPages.map((page) => (
                      <button
                        key={page.id}
                        type="button"
                        role="tab"
                        aria-selected={econPage === page.id}
                        className={`hustle-tab ${econPage === page.id ? 'active' : ''}`}
                        onClick={() => setEconPage(page.id)}
                      >
                        <strong>{page.label}</strong>
                      </button>
                    ))}
                  </div>

                  {econPage === 'shopfront' && (
                    <div className="hustle-shopfront-grid">
                      <section className="hustle-phone-preview">
                        <div className="hustle-phone-top">
                          <span>@{selectedHandle}</span>
                          <span>Live preview</span>
                        </div>
                        <div className="hustle-phone-avatar">{selectedCreatorName.charAt(0).toUpperCase() || 'H'}</div>
                        <h3>{selectedCreatorName || 'Creator Page'}</h3>
                        <p>Portfolio proof, flash drops, booking handoff, and social links.</p>
                        <div className="hustle-phone-buttons">
                          <span>Book</span>
                          <span>Flash</span>
                          <span>Shop</span>
                        </div>
                      </section>

                      <section className="hustle-rail-card">
                        <p className="hustle-kicker">Shopfront actions</p>
                        <Link to="/dashboard/shop" className="hustle-rail-action">Open storefront</Link>
                        <Link to="/dashboard/huddle?charm=bookings" className="hustle-rail-action">Booking paths</Link>
                        <Link to="/dashboard/artist-profile" className="hustle-rail-action">Creator profile</Link>
                      </section>

                      <section className="hustle-rail-card">
                        <p className="hustle-kicker">Publish checklist</p>
                        <div className="hustle-checklist">
                          {PUBLISH_CHECKLIST.map(([label, value]) => (
                            <div key={label}>
                              <span />
                              <strong>{label}</strong>
                              <small>{value}</small>
                            </div>
                          ))}
                        </div>
                      </section>

                      <section className="hustle-rail-card">
                        <p className="hustle-kicker">Branding guardrail</p>
                        <span className="hustle-muted">
                          Creator pages can theme content. Ryozan auth, checkout, storage, and legal surfaces stay fixed.
                        </span>
                      </section>
                    </div>
                  )}

                  {econPage === 'command' && (
                    <div className="hustle-integrations-grid">
                      <section className="hustle-rail-card">
                        <p className="hustle-kicker">Econ command</p>
                        <div className="hustle-lane-grid">
                          <div>
                            <strong>{econSummary ? money(econSummary.gross_cents) : econLoading ? 'Loading' : '$0.00'}</strong>
                            <span>Recorded POS gross</span>
                          </div>
                          <div>
                            <strong>{econSummary ? money(econSummary.cash_cents) : '$0.00'}</strong>
                            <span>Cash recorded</span>
                          </div>
                          <div>
                            <strong>{econSummary ? money(econSummary.card_cents) : '$0.00'}</strong>
                            <span>Card through Stripe</span>
                          </div>
                          <div>
                            <strong>{money(projectedTaxReserveCents)}</strong>
                            <span>Projected tax reserve</span>
                          </div>
                          <div>
                            <strong>{money(ledgerIncomeCents)}</strong>
                            <span>Synced ledger income</span>
                          </div>
                          <div>
                            <strong>{money(Math.abs(ledgerExpenseCents))}</strong>
                            <span>Synced ledger expenses</span>
                          </div>
                        </div>
                      </section>

                      <section className="hustle-rail-card">
                        <p className="hustle-kicker">Payroll and tax prep</p>
                        {econPayrollTaxSummary && (
                          <div className="hustle-lane-grid">
                            <div>
                              <strong>{money(projectedNetTaxableCents)}</strong>
                              <span>Projected taxable net</span>
                            </div>
                            <div>
                              <strong>{money(econPayrollTaxSummary.deductible_expense_cents)}</strong>
                              <span>Deductible expenses</span>
                            </div>
                            <div>
                              <strong>{money(Math.abs(uncategorizedTaxCents))}</strong>
                              <span>Needs categorization</span>
                            </div>
                            <div>
                              <strong>{econPayrollTaxSummary.prep_status === 'ready' ? 'Ready' : 'Review'}</strong>
                              <span>Prep status</span>
                            </div>
                          </div>
                        )}
                        {econSummary?.by_artist.length ? (
                          <div className="hustle-status-list">
                            {econSummary.by_artist.map((artist) => (
                              <div key={artist.artist_id} className="hustle-status-row hustle-status-row--active">
                                <span>
                                  <strong>{artist.artist_name}</strong>
                                  <small>
                                    {artist.transaction_count} records | artist share {money(artist.estimated_artist_share_cents)}
                                  </small>
                                </span>
                                <em>{money(artist.estimated_tax_reserve_cents)} reserve</em>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <span className="hustle-muted">No artist payout records yet for this period.</span>
                        )}
                      </section>

                      <section className="hustle-rail-card">
                        <p className="hustle-kicker">Recent payment records</p>
                        {econSummary?.recent_transactions.length ? (
                          <div className="hustle-status-list">
                            {econSummary.recent_transactions.slice(0, 5).map((txn) => (
                              <div key={txn.id} className="hustle-status-row hustle-status-row--active">
                                <span>
                                  <strong>{money(txn.amount_cents)} {txn.payment_method}</strong>
                                  <small>{txn.client_name || txn.description || 'POS transaction'}</small>
                                </span>
                                <em>{txn.processor || 'manual'}</em>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <span className="hustle-muted">POS records will appear here after payment capture.</span>
                        )}
                      </section>

                      <section className="hustle-rail-card">
                        <p className="hustle-kicker">Normalized ledger</p>
                        {econLedger.length ? (
                          <div className="hustle-status-list">
                            {econLedger.map((entry) => (
                              <div key={entry.id} className="hustle-status-row hustle-status-row--active">
                                <span>
                                  <strong>{money(entry.amount_cents)} {entry.provider}</strong>
                                  <small>{entry.description || entry.counterparty || entry.entry_type}</small>
                                </span>
                                <em>{entry.payroll_tax_bucket || entry.status}</em>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <span className="hustle-muted">Stripe checkout, QuickBooks, Plaid, and Mercury entries will appear here as they are normalized.</span>
                        )}
                      </section>
                    </div>
                  )}

                  {econPage === 'rails' && (
                    <div className="hustle-integrations-grid">
                      <section className="hustle-connector-config hustle-connector-config--panel">
                        <span className="hustle-kicker">Payment rails</span>
                        <div className="hustle-status-list">
                      {[
                        { label: 'Online checkout', value: 'Stripe', status: 'active', note: 'Default rail for deposits, booking payments, merch, and eCommerce' },
                        { label: 'In-store POS', value: 'Clover', status: 'pending', note: 'Use Clover devices for shop payments and cash. Monitor through QuickBooks and Mercury instead of direct POS import.' },
                        { label: 'Stripe', value: stripeConnected ? 'Connected' : 'Needs setup', status: stripeConnected ? 'active' : 'pending', note: 'Checkout, Connect, webhooks, online payment records' },
                        { label: 'Artist fallback', value: stripeConnected ? 'Stripe payout' : 'Payroll setup', status: stripeConnected ? 'active' : 'pending', note: stripeConnected ? 'Checkout can route destination payouts to the connected creator account' : 'Use shop-managed payout records while prompting this creator to finish Stripe onboarding' },
                        { label: 'Plaid', value: econStatus?.plaid.ready_to_sync ? 'Ready' : plaidConnected ? 'Linked' : 'Needs setup', status: econStatus?.plaid.ready_to_sync ? 'active' : plaidConnected ? 'pending' : 'planned', note: syncRunNote(econStatus?.plaid.latest_sync) ?? missingSetupNote(econStatus?.plaid.missing, econStatus?.plaid.setup) ?? (plaidCurrent == null ? 'Balance and transaction sync' : `Balance view ${plaidCurrent.toLocaleString(undefined, { style: 'currency', currency: 'USD' })}`) },
                        { label: 'QuickBooks', value: 'Accounting source', status: 'pending', note: 'Use for bookkeeping, contractor records, 1099/payroll reporting, and Clover cash/card reconciliation' },
                        { label: 'Mercury', value: econStatus?.mercury.ready_to_sync ? 'Approval-only' : 'Needs token', status: econStatus?.mercury.ready_to_sync ? 'active' : 'planned', note: syncRunNote(econStatus?.mercury.latest_sync) ?? missingSetupNote(econStatus?.mercury.missing, econStatus?.mercury.setup) ?? (mercuryAccountCount == null && mercuryTxnCount == null ? 'Bank reads and owner-approved payout queue' : `${mercuryAccountCount ?? 0} accounts, ${mercuryTxnCount ?? 0} transactions returned`) },
                      ].map((rail) => (
                            <div key={rail.label} className={`hustle-status-row hustle-status-row--${rail.status}`}>
                              <span>
                                <strong>{rail.label}</strong>
                                <small>{rail.note}</small>
                              </span>
                              <em>{rail.value}</em>
                            </div>
                          ))}
                        </div>
                      </section>

                      <section className="hustle-connector-config hustle-connector-config--panel">
                        <span className="hustle-kicker">Payment alerts</span>
                        <div className="hustle-status-list">
                          {econAlerts.map((alert) => (
                            <div key={alert} className="hustle-status-row hustle-status-row--pending">
                              <span>
                                <strong>{alert}</strong>
                                <small>Review before month-end close.</small>
                              </span>
                            </div>
                          ))}
                          {econDiagnostics?.results.map((result) => (
                            <div
                              key={`${result.provider}-${result.status}`}
                              className={`hustle-status-row hustle-status-row--${
                                result.status === 'pass' ? 'active' : result.status === 'fail' ? 'planned' : 'pending'
                              }`}
                            >
                              <span>
                                <strong>{result.provider} diagnostic: {result.status}</strong>
                                <small>
                                  {result.missing.length ? `${result.detail}: ${result.missing.join(', ')}` : result.detail}
                                </small>
                              </span>
                              <em>{econDiagnostics.live ? 'live' : 'setup'}</em>
                            </div>
                          ))}
                        </div>
                        <div className="hustle-connector-actions">
                      {mercuryAccounts.length > 0 && (
                        <label className="hustle-creator-select">
                          <span>Mercury account</span>
                          <select
                            value={selectedMercuryAccountId}
                            onChange={(event) => setSelectedMercuryAccountId(event.target.value)}
                          >
                            <option value="">All Mercury accounts</option>
                            {mercuryAccounts.map((account) => (
                              <option key={account.id ?? account.name ?? 'mercury-account'} value={account.id ?? ''}>
                                {[account.name || account.id || 'Mercury account', account.type, account.status]
                                  .filter(Boolean)
                                  .join(' | ')}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                      <button onClick={() => window.dispatchEvent(new CustomEvent('ryozan:open-pos'))}>Open POS</button>
                      <button onClick={handleEconDiagnostics}>{econLoading ? 'Checking rails' : 'Check rails live'}</button>
                      <button onClick={handleEconSyncAll}>{econLoading ? 'Syncing rails' : 'Sync ready rails'}</button>
                      <button onClick={openArtistConnections}>Open artist Connections</button>
                      <button onClick={handlePlaidConnect}>{plaidConnected ? 'Reconnect Plaid' : 'Connect Plaid'}</button>
                      <button onClick={handlePlaidSync}>{plaidSyncCount == null ? 'Sync Plaid' : `${plaidSyncCount} Plaid updates`}</button>
                      <button onClick={handleMercuryAccounts}>Read Mercury accounts</button>
                      <button onClick={handleMercuryTransactions}>Read Mercury txns</button>
                    </div>
                  </section>

                      <section className="hustle-rail-card">
                        <p className="hustle-kicker">Sync history</p>
                        {econSyncRuns.length ? (
                          <div className="hustle-status-list">
                            {econSyncRuns.map((run) => (
                              <div
                                key={run.id}
                                className={`hustle-status-row hustle-status-row--${
                                  run.status === 'success' ? 'active' : run.status === 'error' ? 'planned' : 'pending'
                                }`}
                              >
                                <span>
                                  <strong>{run.provider} {run.status}</strong>
                                  <small>
                                    {run.finished_at || run.started_at
                                      ? new Date(run.finished_at || run.started_at || '').toLocaleString()
                                      : 'No timestamp'} | {run.added_count + run.modified_count + run.removed_count} changes
                                  </small>
                                </span>
                                <em>{run.error ? 'error' : run.metadata?.kind ? String(run.metadata.kind) : 'sync'}</em>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <span className="hustle-muted">Plaid and Mercury sync attempts will appear here. Clover in-store activity is monitored through QuickBooks and bank deposits.</span>
                        )}
                      </section>
                    </div>
                  )}
                </div>
              )}

              {activeStashTab === 'integrations' && (
                <div className="hustle-integrations-grid">
                  <section className="hustle-rail-card">
                    <p className="hustle-kicker">Connector status</p>
                    <div className="hustle-status-list">
                      {STATUS_ITEMS.map((item) => (
                        <button
                          key={item.id}
                          className={`hustle-status-row hustle-status-row--${item.status}${connectorPanel === item.id ? ' active' : ''}`}
                          onClick={() => setConnectorPanel(item.id)}
                        >
                          <span>
                            <strong>{item.label}</strong>
                            <small>{item.desc}</small>
                          </span>
                          <em>{item.value}</em>
                          <b>{item.actionLabel}</b>
                        </button>
                      ))}
                    </div>
                  </section>

                  {connectorDetails && (
                    <section className="hustle-connector-config hustle-connector-config--panel">
                      <span className="hustle-kicker">{connectorDetails.title}</span>
                      <p>{connectorDetails.copy}</p>
                      <div className="hustle-connector-actions">
                        {connectorDetails.actions.map((action) => (
                          <button key={action.label} onClick={action.onClick}>
                            {action.label}
                          </button>
                        ))}
                      </div>
                      {connectorPanel === 'google-business' && (
                        <>
                          <div className="hustle-lane-grid">
                            {[workspaceIdentityItem(studioSettings.contact_email), ...GOOGLE_STACK_ITEMS].map((item) => (
                              <div key={item.label}>
                                <strong>{item.value}</strong>
                                <span>{item.label}: {item.note}</span>
                              </div>
                            ))}
                          </div>
                          <div className="hustle-status-list">
                            {GOOGLE_CONVERSION_ITEMS.map(([eventName, purpose]) => (
                              <div key={eventName} className="hustle-status-row hustle-status-row--pending">
                                <span>
                                  <strong>{eventName}</strong>
                                  <small>{purpose}</small>
                                </span>
                                <em>GA4 key event</em>
                              </div>
                            ))}
                            {GOOGLE_OPERATIONS_ITEMS.map(([label, note]) => (
                              <div key={label} className="hustle-status-row hustle-status-row--active">
                                <span>
                                  <strong>{label}</strong>
                                  <small>{note}</small>
                                </span>
                                <em>Ops loop</em>
                              </div>
                            ))}
                          </div>
                        </>
                      )}
                    </section>
                  )}
                </div>
              )}
            </div>
        </main>
      </div>
    </div>
  )
}
