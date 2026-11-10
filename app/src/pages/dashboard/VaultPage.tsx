/**
 * VaultPage: Artist financial command center + Archive.
 *
 * Two tabs:
 *   1. Financial Hub: Stripe balances, payouts, payment evidence, and artist pay structures
 *   2. Archive: Searchable table of completed waivers, bookings, receipts
 *
 * Artists see this instead of the collector Wallet page.
 */

import type { JSX } from 'react'
import { lazy, Suspense, useEffect, useState, useCallback } from 'react'
import { useAuthStore } from '../../stores/auth'
import {
  ApiError,
  stripeConnect,
  archive as archiveApi,
  dashboard as dashboardApi,
  econ as econApi,
  type StripeConnectStatus,
  type BoothRentStatus,
  type BoothRentInterval,
  type BoothRentPaymentMethod,
  type ArtistStripeInfo,
  type ArchiveRecord,
  type ArchiveDetail,
  type DashboardSummary,
  type EconIntegrationsStatus,
  type EconLedgerEntry,
  type EconLedgerSummary,
} from '../../lib/api'
import ArtistDepositActivity from '../../components/ArtistDepositActivity'
import { archivePhotoIdUrl } from '../../lib/archive-photo-id'

const STRIPE_CONNECT_PLATFORM_PROFILE_URL = 'https://dashboard.stripe.com/connect/set-up/profile'

const ReferenceLibrary = lazy(() => import('../../components/ReferenceLibrary'))

const EMPTY_BOOTH_RENT: BoothRentStatus = {
  configured: false,
  amount_cents: null,
  installment_amount_cents: null,
  interval: null,
  payment_method: 'cash',
  status: 'not_configured',
  subscription_id: null,
  current_period_end: null,
  next_due_date: null,
  cancel_at_period_end: false,
  commission_rate: null,
}

type PayStructureMode = 'commission' | 'booth'

interface PayStructureDraft {
  mode: PayStructureMode
  commissionRate: string
  amount: string
  interval: BoothRentInterval
  paymentMethod: BoothRentPaymentMethod
  isGuestArtist: boolean
  nextDueDate: string
}

/* ── Icons ────────────────────────────────── */

const Icons = {
  stripe: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/>
    </svg>
  ),
  coin: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><path d="M12 6v12"/><path d="M15.5 9.5a3 3 0 0 0-3-1.5H11a3 3 0 0 0 0 4h2a3 3 0 0 1 0 4h-1.5a3 3 0 0 1-3-1.5"/>
    </svg>
  ),
  calendar: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
    </svg>
  ),
  trendUp: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>
    </svg>
  ),
  externalLink: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
    </svg>
  ),
  checkCircle: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
    </svg>
  ),
  alertCircle: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
    </svg>
  ),
  archive: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>
    </svg>
  ),
  search: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
    </svg>
  ),
  waiver: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
  ),
  booking: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><path d="m9 16 2 2 4-4"/>
    </svg>
  ),
  receipt: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M8 10h8"/><path d="M8 14h4"/>
    </svg>
  ),
  sortAsc: (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m7 15 5 5 5-5"/><path d="m7 9 5-5 5 5"/>
    </svg>
  ),
  chevronDown: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9"/>
    </svg>
  ),
  chevronUp: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="18 15 12 9 6 15"/>
    </svg>
  ),
}

const TYPE_ICONS: Record<string, JSX.Element> = {
  waiver: Icons.waiver,
  booking: Icons.booking,
  receipt: Icons.receipt,
}

/* ── Helpers ────────────────────────────────── */

function formatUSD(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

function formatKnownUSD(cents: number | null): string {
  return cents == null ? 'Unavailable' : formatUSD(cents)
}

function formatPayoutDate(value: string | null, known: boolean): string {
  if (!known) return 'Unavailable'
  if (!value) return 'No pending payout'
  return new Date(`${value}T12:00:00`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function formatDueDate(value: string): string {
  return new Date(`${value}T12:00:00`).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function nextMonthFirst(): string {
  const now = new Date()
  return new Date(Date.UTC(now.getFullYear(), now.getMonth() + 1, 1)).toISOString().slice(0, 10)
}

function currentBoothPeriod(interval: BoothRentInterval | null, nextDueDate: string | null): string {
  const dueDate = nextDueDate || nextMonthFirst()
  return interval === 'biweekly' ? dueDate : dueDate.slice(0, 7)
}

function payStructureDraft(artist: ArtistStripeInfo): PayStructureDraft {
  return {
    mode: artist.booth_rent?.configured ? 'booth' : 'commission',
    commissionRate: String(artist.commission_rate ?? 70),
    amount: artist.booth_rent?.amount_cents ? (artist.booth_rent.amount_cents / 100).toFixed(2) : '',
    interval: artist.booth_rent?.interval || 'month',
    paymentMethod: artist.booth_rent?.configured ? artist.booth_rent.payment_method : 'cash',
    isGuestArtist: artist.is_guest_artist,
    nextDueDate: artist.booth_rent?.next_due_date || nextMonthFirst(),
  }
}

/* ── Archive Tab Component ────────────────────── */

export function ArchiveTab() {
  const [records, setRecords] = useState<ArchiveRecord[]>([])
  const [total, setTotal] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [typeFilter, setTypeFilter] = useState('all')
  const [searchTerm, setSearchTerm] = useState('')
  const [page, setPage] = useState(0)
  const [sortBy, setSortBy] = useState<'date' | 'type' | 'client'>('date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [archiveError, setArchiveError] = useState<string | null>(null)
  const pageSize = 25

  const loadArchive = useCallback(async () => {
    setIsLoading(true)
    setArchiveError(null)
    try {
      const data = await archiveApi.list({
        type: typeFilter,
        status: 'approved',
        search: searchTerm || undefined,
        skip: page * pageSize,
        limit: pageSize,
        sort_by: sortBy,
        sort_dir: sortDir,
      })
      setRecords(data.records)
      setTotal(data.total)
    } catch (error) {
      setRecords([])
      setTotal(0)
      setArchiveError(error instanceof Error ? error.message : 'Archive records could not be loaded')
    } finally {
      setIsLoading(false)
    }
  }, [typeFilter, searchTerm, page, sortBy, sortDir])

  useEffect(() => {
    loadArchive()
  }, [loadArchive])

  // Reset to page 0 when filters change
  useEffect(() => {
    setPage(0)
  }, [typeFilter, searchTerm, sortBy, sortDir])

  const totalPages = Math.ceil(total / pageSize)

  const toggleSort = (col: 'date' | 'type' | 'client') => {
    if (sortBy === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(col)
      setSortDir(col === 'date' ? 'desc' : 'asc')
    }
  }

  const SortIndicator = ({ col }: { col: 'date' | 'type' | 'client' }) => {
    if (sortBy !== col) return <span className="archive-sort-icon archive-sort-inactive">{Icons.sortAsc}</span>
    return <span className="archive-sort-icon">{sortDir === 'asc' ? Icons.chevronUp : Icons.chevronDown}</span>
  }

  return (
    <div className="archive-container">
      {/* Type subtabs */}
      <div className="archive-subtabs" role="tablist" aria-label="Archive record type">
        {[
          { key: 'all', label: 'All' },
          { key: 'doc', label: 'Docs' },
          { key: 'waiver', label: 'Waivers' },
          { key: 'booking', label: 'Bookings' },
          { key: 'receipt', label: 'Receipts' },
        ].map(t => (
          <button
            type="button"
            key={t.key}
            role="tab"
            aria-selected={typeFilter === t.key}
            className={`archive-subtab ${typeFilter === t.key ? 'active' : ''}`}
            onClick={() => setTypeFilter(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Search bar */}
      <div className="archive-filters">
        <div className="archive-search-wrap">
          <span className="archive-search-icon">{Icons.search}</span>
          <input
            type="text"
            className="archive-search-input"
            aria-label="Search archive by client name"
            placeholder="Search by client name..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
          />
        </div>
      </div>

      {/* Table */}
      <div className="archive-table-wrap">
        <table className="archive-table">
          <thead>
            <tr>
              <th
                className="archive-th archive-th-sortable"
                role="button"
                tabIndex={0}
                aria-sort={sortBy === 'client' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                onClick={() => toggleSort('client')}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') toggleSort('client') }}
              >
                Client <SortIndicator col="client" />
              </th>
              <th
                className="archive-th archive-th-sortable"
                role="button"
                tabIndex={0}
                aria-sort={sortBy === 'type' ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
                onClick={() => toggleSort('type')}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') toggleSort('type') }}
              >
                Type <SortIndicator col="type" />
              </th>
              <th className="archive-th">Artist</th>
              <th className="archive-th"></th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  <td colSpan={4}><div className="skeleton" style={{ height: '20px', margin: '4px 0' }} /></td>
                </tr>
              ))
            ) : archiveError ? (
              <tr>
                <td colSpan={4} className="archive-empty-cell">
                  <div className="archive-empty" role="alert">
                    <span>{Icons.alertCircle}</span>
                    <p>{archiveError}</p>
                    <button type="button" className="btn btn-outline btn-sm" onClick={() => void loadArchive()}>Try again</button>
                  </div>
                </td>
              </tr>
            ) : records.length === 0 ? (
              <tr>
                <td colSpan={4} className="archive-empty-cell">
                  <div className="archive-empty">
                    <span>{Icons.archive}</span>
                    <p>No records found</p>
                  </div>
                </td>
              </tr>
            ) : (
              records.map(rec => (
                <ArchiveRow
                  key={rec.id}
                  record={rec}
                  expanded={expandedId === rec.id}
                  onToggle={() => setExpandedId(expandedId === rec.id ? null : rec.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="archive-pagination">
          <button
            type="button"
            className="btn btn-outline btn-sm"
            disabled={page === 0}
            onClick={() => setPage(p => p - 1)}
          >
            Previous
          </button>
          <span className="archive-page-info">
            Page {page + 1} of {totalPages} ({total} records)
          </span>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            disabled={page >= totalPages - 1}
            onClick={() => setPage(p => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}

function ArchiveRow({ record, expanded, onToggle }: {
  record: ArchiveRecord
  expanded: boolean
  onToggle: () => void
}) {
  const typeIcon = TYPE_ICONS[record.record_type] || Icons.waiver
  const typeLabel = record.record_type.charAt(0).toUpperCase() + record.record_type.slice(1)
  const [detail, setDetail] = useState<ArchiveDetail | null>(null)
  const photoIdUrl = archivePhotoIdUrl(detail?.data)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  useEffect(() => {
    if (expanded && !detail) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDetailLoading(true)
      setDetailError(null)
      archiveApi.detail(String(record.original_id))
        .then(setDetail)
        .catch((error) => setDetailError(error instanceof Error ? error.message : 'Record details could not be loaded'))
        .finally(() => setDetailLoading(false))
    }
  }, [detail, expanded, record.original_id])

  return (
    <>
      <tr className="archive-row" onClick={onToggle} style={{ cursor: 'pointer' }}>
        <td className="archive-td archive-td-client">
          <span className="archive-ref">{record.original_id.slice(0, 8).toUpperCase()}</span>
          <span>{record.client_name}</span>
        </td>
        <td className="archive-td archive-td-type">
          <span className={`archive-type-badge archive-type-${record.record_type}`}>
            {typeIcon} {typeLabel}
          </span>
        </td>
        <td className="archive-td archive-td-artist">{record.artist_name || '--'}</td>
        <td className="archive-td archive-td-view">
          <button type="button" className="cs-review-btn" aria-expanded={expanded} onClick={(e) => { e.stopPropagation(); onToggle() }}>
            View
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="archive-detail-row">
          <td colSpan={4}>
            {detailLoading ? (
              <div style={{ padding: '1.5rem', color: 'var(--text-muted)', fontSize: '0.8rem' }}>Loading record...</div>
            ) : detailError ? (
              <div role="alert" style={{ padding: '1.5rem', color: 'var(--gold)', fontSize: '0.8rem' }}>{detailError}</div>
            ) : detail?.data ? (
              <div className="archive-detail archive-full-record">
                {/* Client Portion */}
                <div className="archive-record-section">
                  <h4 className="archive-record-heading">Client Information</h4>
                  <div className="archive-detail-grid">
                    {detail.data.full_name && <div className="archive-detail-item"><span className="archive-detail-label">Name</span><span className="archive-detail-value">{detail.data.full_name}</span></div>}
                    {detail.data.date_of_birth && <div className="archive-detail-item"><span className="archive-detail-label">DOB</span><span className="archive-detail-value">{detail.data.date_of_birth}</span></div>}
                    {detail.data.street_address && <div className="archive-detail-item"><span className="archive-detail-label">Address</span><span className="archive-detail-value">{detail.data.street_address}</span></div>}
                    {detail.data.phone && <div className="archive-detail-item"><span className="archive-detail-label">Phone</span><span className="archive-detail-value">{detail.data.phone}</span></div>}
                    {detail.data.email && <div className="archive-detail-item"><span className="archive-detail-label">Email</span><span className="archive-detail-value">{detail.data.email}</span></div>}
                    {detail.data.emergency_contact && <div className="archive-detail-item"><span className="archive-detail-label">Emergency</span><span className="archive-detail-value">{detail.data.emergency_contact} {detail.data.emergency_phone && `(${detail.data.emergency_phone})`}</span></div>}
                    {detail.data.emergency_contact_address && <div className="archive-detail-item"><span className="archive-detail-label">Emergency Address</span><span className="archive-detail-value">{detail.data.emergency_contact_address}</span></div>}
                  </div>
                </div>

                {/* Tattoo Details */}
                <div className="archive-record-section">
                  <h4 className="archive-record-heading">Tattoo Details</h4>
                  <div className="archive-detail-grid">
                    {detail.data.artist_name && <div className="archive-detail-item"><span className="archive-detail-label">Artist</span><span className="archive-detail-value">{detail.data.artist_name}</span></div>}
                    {detail.data.tattoo_description && <div className="archive-detail-item"><span className="archive-detail-label">Description</span><span className="archive-detail-value">{detail.data.tattoo_description}</span></div>}
                    {detail.data.quoted_price && <div className="archive-detail-item"><span className="archive-detail-label">Price</span><span className="archive-detail-value">${detail.data.quoted_price}</span></div>}
                  </div>
                </div>

                {/* Health Info */}
                {(detail.data.allergies || detail.data.medical_conditions || detail.data.medications) && (
                  <div className="archive-record-section">
                    <h4 className="archive-record-heading">Health Information</h4>
                    <div className="archive-detail-grid">
                      <div className="archive-detail-item"><span className="archive-detail-label">Pregnant</span><span className="archive-detail-value">{detail.data.pregnant ? 'Yes' : 'No'}</span></div>
                      <div className="archive-detail-item"><span className="archive-detail-label">Blood Disorder</span><span className="archive-detail-value">{detail.data.blood_disorder ? 'Yes' : 'No'}</span></div>
                      <div className="archive-detail-item"><span className="archive-detail-label">Skin Condition</span><span className="archive-detail-value">{detail.data.skin_condition ? 'Yes' : 'No'}</span></div>
                      {detail.data.allergies && detail.data.allergies !== 'None' && <div className="archive-detail-item"><span className="archive-detail-label">Allergies</span><span className="archive-detail-value">{detail.data.allergies}</span></div>}
                      {detail.data.medical_conditions && <div className="archive-detail-item"><span className="archive-detail-label">Conditions</span><span className="archive-detail-value">{detail.data.medical_conditions}</span></div>}
                      {detail.data.medications && <div className="archive-detail-item"><span className="archive-detail-label">Medications</span><span className="archive-detail-value">{detail.data.medications}</span></div>}
                    </div>
                  </div>
                )}

                {/* Photo ID */}
                {photoIdUrl && (
                  <div className="archive-record-section">
                    <h4 className="archive-record-heading">Photo ID</h4>
                    <img src={photoIdUrl} alt="Client ID" className="archive-id-photo" />
                  </div>
                )}

                {/* Client Signature */}
                {detail.data.signature_url && (
                  <div className="archive-record-section">
                    <h4 className="archive-record-heading">Client Signature</h4>
                    <img src={detail.data.signature_url} alt="Client signature" className="archive-signature" />
                    {detail.signed_at && <span className="archive-detail-label" style={{ marginTop: '0.25rem', display: 'block' }}>Signed {formatDate(detail.signed_at)}</span>}
                  </div>
                )}

                {/* Artist Portion */}
                {detail.data.countersigned_by && (
                  <div className="archive-record-section archive-artist-section">
                    <h4 className="archive-record-heading">Artist Sign-Off</h4>
                    <div className="archive-detail-grid">
                      <div className="archive-detail-item"><span className="archive-detail-label">Artist</span><span className="archive-detail-value">{detail.data.countersigned_by}</span></div>
                      <div className="archive-detail-item"><span className="archive-detail-label">Condition</span><span className="archive-detail-value" style={{ textTransform: 'capitalize' }}>{detail.data.client_condition_notes || 'Good'}</span></div>
                      {detail.data.countersigned_at && <div className="archive-detail-item"><span className="archive-detail-label">Countersigned</span><span className="archive-detail-value">{formatDate(detail.data.countersigned_at)}</span></div>}
                    </div>
                    {detail.data.artist_countersignature && (
                      <>
                        <span className="archive-detail-label" style={{ marginTop: '0.5rem', display: 'block' }}>Artist Signature</span>
                        <img src={detail.data.artist_countersignature} alt="Artist signature" className="archive-signature" />
                      </>
                    )}
                  </div>
                )}

                {/* Metadata */}
                <div className="archive-record-meta">
                  <span>Ref: {detail.data.reference_code || record.original_id.toString().slice(0, 8).toUpperCase()}</span>
                  <span>Date: {formatDate(record.date)}</span>
                </div>
              </div>
            ) : (
              <div className="archive-detail">
                <div className="archive-detail-grid">
                  <div className="archive-detail-item"><span className="archive-detail-label">Email</span><span className="archive-detail-value">{record.client_email || '--'}</span></div>
                  <div className="archive-detail-item"><span className="archive-detail-label">Date</span><span className="archive-detail-value">{formatDate(record.date)}</span></div>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

/* ── Main Component ────────────────────────────────── */

export default function VaultPage() {
  const user = useAuthStore((s) => s.user)
  const [activeTab, setActiveTab] = useState<'financial' | 'archive' | 'library'>('financial')
  const [stripeStatus, setStripeStatus] = useState<StripeConnectStatus | null>(null)
  const [dashboardSummary, setDashboardSummary] = useState<DashboardSummary | null>(null)
  const [econStatus, setEconStatus] = useState<EconIntegrationsStatus | null>(null)
  const [ledger, setLedger] = useState<EconLedgerEntry[]>([])
  const [ledgerSummary, setLedgerSummary] = useState<EconLedgerSummary | null>(null)
  const [artistPayouts, setArtistPayouts] = useState<ArtistStripeInfo[]>([])
  const [artistOnboardingId, setArtistOnboardingId] = useState<string | null>(null)
  const [artistOnboardingMessage, setArtistOnboardingMessage] = useState<string | null>(null)
  const [artistOnboardingNeedsPlatformSetup, setArtistOnboardingNeedsPlatformSetup] = useState(false)
  const [boothRent, setBoothRent] = useState<BoothRentStatus | null>(null)
  const [boothRentAction, setBoothRentAction] = useState<string | null>(null)
  const [boothRentMessage, setBoothRentMessage] = useState<string | null>(null)
  const [payDrafts, setPayDrafts] = useState<Record<string, PayStructureDraft>>({})
  const [financeUnavailable, setFinanceUnavailable] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const isShopTreasury = user?.role === 'founder' || user?.role === 'admin'
  const isArtist = user?.role === 'artist'
  useEffect(() => {
    const load = async () => {
      try {
        const financeRequests = isShopTreasury
          ? [econApi.status(), econApi.ledger({ limit: 100 }), econApi.ledgerSummary(), stripeConnect.listArtists()]
          : []
        const [stripe, artistRent, summary, ...finance] = await Promise.allSettled([
          isShopTreasury ? Promise.resolve(null) : stripeConnect.status().catch(() => null),
          isShopTreasury ? Promise.resolve(null) : stripeConnect.boothRentStatus().catch(() => null),
          dashboardApi.artistSummary(),
          ...financeRequests,
        ])
        if (stripe.status === 'fulfilled' && stripe.value) {
          setStripeStatus(stripe.value as StripeConnectStatus)
        }
        if (artistRent.status === 'fulfilled' && artistRent.value) {
          setBoothRent(artistRent.value as BoothRentStatus)
        }
        if (summary.status === 'fulfilled') {
          setDashboardSummary(summary.value as DashboardSummary)
        } else {
          setFinanceUnavailable(true)
        }
        if (isShopTreasury) {
          const [statusResult, ledgerResult, ledgerSummaryResult, artistsResult] = finance
          if (statusResult?.status === 'fulfilled') setEconStatus(statusResult.value as EconIntegrationsStatus)
          if (ledgerResult?.status === 'fulfilled') setLedger(ledgerResult.value as EconLedgerEntry[])
          if (ledgerSummaryResult?.status === 'fulfilled') setLedgerSummary(ledgerSummaryResult.value as EconLedgerSummary)
          if (artistsResult?.status === 'fulfilled') {
            const artists = artistsResult.value as ArtistStripeInfo[]
            setArtistPayouts(artists)
            setPayDrafts(Object.fromEntries(artists
              .filter((artist) => artist.role === 'artist')
              .map((artist) => [artist.id, payStructureDraft(artist)])))
          }
          if (finance.some((result) => result?.status === 'rejected')) setFinanceUnavailable(true)
        }
      } catch {
        setFinanceUnavailable(true)
      }
      finally { setIsLoading(false) }
    }
    load()
  }, [isShopTreasury])

  const isConnected = dashboardSummary?.stripe.connected ?? stripeStatus?.connected ?? false
  const stripeBalance = dashboardSummary?.stripe.connected ? dashboardSummary.stripe.available_balance : null
  const pendingBalance = dashboardSummary?.stripe.connected ? dashboardSummary.stripe.pending_balance : null
  const nextPayoutDate = dashboardSummary?.stripe.next_payout_date ?? null
  const stripeStateKnown = dashboardSummary !== null
  const postedPayments = ledger.filter((entry) => entry.entry_type === 'payment' && entry.status !== 'removed')
  const grossCollected = ledgerSummary?.buckets
    .filter((bucket) => bucket.bucket === 'income')
    .reduce((total, bucket) => total + bucket.amount_cents, 0) ?? null
  const stripeLedgerCount = postedPayments.filter((entry) => entry.provider === 'stripe').length
  const payoutReadyArtists = artistPayouts.filter((artist) => artist.routing_ready).length
  const payoutSetupArtists = artistPayouts.filter((artist) => artist.stripe_account_id && !artist.routing_ready).length

  const handleArtistOnboard = async (artistId: string) => {
    setArtistOnboardingId(artistId)
    setArtistOnboardingMessage(null)
    setArtistOnboardingNeedsPlatformSetup(false)
    try {
      const result = await stripeConnect.adminOnboard(artistId)
      if (!result.url) throw new Error('Stripe Connect did not return a setup link')
      window.location.href = result.url
    } catch (error) {
      setFinanceUnavailable(true)
      setArtistOnboardingNeedsPlatformSetup(
        error instanceof ApiError && error.code === 'stripe_connect_platform_profile_incomplete',
      )
      setArtistOnboardingMessage(error instanceof Error ? error.message : 'Stripe Connect could not be opened')
    } finally {
      setArtistOnboardingId(null)
    }
  }

  const handleBoothRentRedirect = async (mode: 'checkout' | 'portal') => {
    setBoothRentAction(mode)
    setBoothRentMessage(null)
    try {
      const result = mode === 'checkout'
        ? await stripeConnect.startBoothRent()
        : await stripeConnect.manageBoothRent()
      if (!result.url) throw new Error('Stripe billing did not return a secure link')
      window.location.href = result.url
    } catch (error) {
      setBoothRentMessage(error instanceof Error ? error.message : 'Stripe billing could not be opened')
      setBoothRentAction(null)
    }
  }

  const handlePayStructureSave = async (artist: ArtistStripeInfo) => {
    const draft = payDrafts[artist.id]
    if (!draft) return
    const amount = Number(draft.amount)
    const commissionRate = Number(draft.commissionRate)
    if (draft.mode === 'booth' && (!Number.isFinite(amount) || amount < 1)) {
      setBoothRentMessage('Enter booth rent of at least $1.00')
      return
    }
    if (draft.mode === 'booth' && !/^\d{4}-\d{2}-\d{2}$/.test(draft.nextDueDate)) {
      setBoothRentMessage('Choose the next booth-rent due date')
      return
    }
    if (draft.mode === 'commission' && (!Number.isInteger(commissionRate) || commissionRate < 0 || commissionRate > 99)) {
      setBoothRentMessage('Enter an artist commission rate from 0 to 99 percent')
      return
    }
    setBoothRentAction(artist.id)
    setBoothRentMessage(null)
    try {
      const updated = await stripeConnect.configureBoothRent(artist.id, {
        enabled: draft.mode === 'booth',
        ...(draft.mode === 'booth' ? {
          amount_cents: Math.round(amount * 100),
          interval: draft.interval,
          payment_method: draft.paymentMethod,
          next_due_date: draft.nextDueDate,
        } : {
          commission_rate: commissionRate,
        }),
        is_guest_artist: draft.isGuestArtist,
      })
      setArtistPayouts((artists) => artists.map((item) => item.id === updated.id ? updated : item))
      setPayDrafts((current) => ({ ...current, [updated.id]: payStructureDraft(updated) }))
      setBoothRentMessage(draft.mode === 'booth'
        ? `${artist.name} is set to 100% artist share with ${draft.paymentMethod.replace('_', ' ')} booth rent.`
        : `${artist.name} is set to a ${commissionRate}% artist commission.`)
    } catch (error) {
      setBoothRentMessage(error instanceof Error ? error.message : 'Artist pay structure could not be saved')
    } finally {
      setBoothRentAction(null)
    }
  }

  const handleBoothRentCash = async (artist: ArtistStripeInfo) => {
    const period = currentBoothPeriod(
      artist.booth_rent?.interval ?? null,
      artist.booth_rent?.next_due_date ?? null,
    )
    setBoothRentAction(`cash:${artist.id}`)
    setBoothRentMessage(null)
    try {
      const result = await stripeConnect.recordBoothRentCash(artist.id, period)
      setArtistPayouts((artists) => artists.map((item) => item.id === result.artist.id ? result.artist : item))
      setLedgerSummary((summary) => summary ? {
        ...summary,
        contractor_contributions_cents: summary.contractor_contributions_cents
          + (result.already_recorded ? 0 : result.payment.amount_cents),
        contractor_contribution_count: summary.contractor_contribution_count
          + (result.already_recorded ? 0 : 1),
      } : summary)
      setBoothRentMessage(result.already_recorded
        ? `${artist.name}'s ${period} cash booth rent was already recorded.`
        : `${artist.name}'s ${period} cash booth rent is recorded as a contractor contribution.`)
    } catch (error) {
      setBoothRentMessage(error instanceof Error ? error.message : 'Cash booth rent could not be recorded')
    } finally {
      setBoothRentAction(null)
    }
  }

  if (isLoading && activeTab === 'financial') {
    return (
      <div className="dashboard-panel home-panel nexus">
        <div style={{ padding: '2rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {[1,2,3,4].map(i => <div key={i} className="skeleton skeleton-card" style={{ height: '80px' }} />)}
        </div>
      </div>
    )
  }

  return (
    <div className="dashboard-panel home-panel nexus">
      <header className="nexus-header">
        <div className="nexus-greeting">
          <p className="home-greeting">Vault</p>
          <p className="treasury-scope">
            {isShopTreasury ? 'Shop treasury' : 'Artist earnings'}
            <span>{isShopTreasury ? 'Stripe + Eco Mod' : 'Your Stripe Connect account'}</span>
          </p>
        </div>
        <span className={`treasury-source-state ${financeUnavailable ? 'treasury-source-state--warning' : ''}`}>
          {financeUnavailable ? 'Partial data' : 'Source checked'}
        </span>
      </header>

      {/* Tab bar */}
      <div className="vault-tabs" role="tablist" aria-label="Vault sections">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'financial'}
          className={`vault-tab ${activeTab === 'financial' ? 'vault-tab-active' : ''}`}
          onClick={() => setActiveTab('financial')}
        >
          {Icons.coin} Financial Hub
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'archive'}
          className={`vault-tab ${activeTab === 'archive' ? 'vault-tab-active' : ''}`}
          onClick={() => setActiveTab('archive')}
        >
          {Icons.archive} Archive
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'library'}
          className={`vault-tab ${activeTab === 'library' ? 'vault-tab-active' : ''}`}
          onClick={() => setActiveTab('library')}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
          </svg>
          {' '}Library
        </button>
      </div>

      {activeTab === 'library' ? (
        <Suspense fallback={<div style={{ padding: '2rem', display: 'flex', justifyContent: 'center' }}><div className="skeleton skeleton-card" style={{ height: '200px', width: '100%' }} /></div>}>
          <ReferenceLibrary />
        </Suspense>
      ) : activeTab === 'financial' ? (
        <>
          {/* ── Balance Cards ── */}
          <div className="vault-balance-row">
            <div className="vault-balance-card">
              <div className="vault-balance-icon vault-icon-gold">{Icons.stripe}</div>
              <div className="vault-balance-info">
                <span className="vault-balance-value vault-value-gold">
                  {isShopTreasury ? formatKnownUSD(grossCollected) : formatKnownUSD(stripeBalance)}
                </span>
                <span className="vault-balance-label">{isShopTreasury ? 'Gross collected' : 'Available'}</span>
              </div>
            </div>

            <div className="vault-balance-card">
              <div className="vault-balance-icon vault-icon-green">{Icons.trendUp}</div>
              <div className="vault-balance-info">
                <span className="vault-balance-value">
                  {isShopTreasury ? formatKnownUSD(stripeBalance) : formatKnownUSD(pendingBalance)}
                </span>
                <span className="vault-balance-label">{isShopTreasury ? 'Stripe available' : 'Pending'}</span>
              </div>
            </div>

            <div className="vault-balance-card">
              <div className="vault-balance-icon">{Icons.calendar}</div>
              <div className="vault-balance-info">
                <span className="vault-balance-value">
                  {isShopTreasury ? formatKnownUSD(pendingBalance) : formatPayoutDate(nextPayoutDate, stripeStateKnown)}
                </span>
                <span className="vault-balance-label">{isShopTreasury ? 'Stripe pending' : 'Next payout'}</span>
              </div>
            </div>
          </div>

          {/* ── Main Grid ── */}
          <div className="vault-grid">

            {/* Stripe payment and payout truth */}
            <div className="vault-card card">
              <div className="nexus-panel-header">
                <span>{Icons.stripe} {isShopTreasury ? 'Stripe payment rail' : 'Stripe Connect'}</span>
              </div>
              <div className="vault-card-body">
                {isConnected ? (
                  <div className="vault-stripe-connected">
                    <div className="vault-stripe-status">
                      <span className="vault-status-dot vault-status-live" />
                      <span>{isShopTreasury ? 'Platform active' : 'Connected'}</span>
                      {Icons.checkCircle}
                    </div>
                    <div className="vault-stripe-details">
                      <div className="vault-detail-row">
                        <span>Available Balance</span>
                        <span className="vault-detail-value">{formatKnownUSD(stripeBalance)}</span>
                      </div>
                      <div className="vault-detail-row">
                        <span>Pending</span>
                        <span className="vault-detail-value">{formatKnownUSD(pendingBalance)}</span>
                      </div>
                      <div className="vault-detail-row">
                        <span>Next Payout</span>
                        <span className="vault-detail-value">{formatPayoutDate(nextPayoutDate, stripeStateKnown)}</span>
                      </div>
                    </div>
                    <a
                      href="https://dashboard.stripe.com"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn btn-outline btn-sm vault-stripe-link"
                    >
                      Open Stripe Dashboard {Icons.externalLink}
                    </a>
                  </div>
                ) : isShopTreasury ? (
                  <div className="vault-stripe-disconnected">
                    <div className="vault-stripe-disconnected-icon">{Icons.alertCircle}</div>
                    <p className="vault-stripe-desc">
                      Stripe platform balance could not be verified. Complete the Connect platform activation in Stripe, then refresh this page.
                    </p>
                    <a
                      href="https://dashboard.stripe.com/acct_1RsxnVRrvzLnK6i2/connect/accounts/overview"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn btn-primary btn-sm"
                      style={{ width: '100%' }}
                    >
                      Open Connect Setup {Icons.externalLink}
                    </a>
                  </div>
                ) : (
                  <div className="vault-stripe-disconnected">
                    <div className="vault-stripe-status">
                      <span className="vault-status-dot vault-status-inactive" />
                      <span>Not Connected</span>
                      {Icons.alertCircle}
                    </div>
                    <p className="vault-stripe-desc">
                      {financeUnavailable
                        ? 'Stripe data could not be verified. No zero-dollar balance is being substituted.'
                        : 'Connect your Stripe account to accept payments, track earnings, and receive automatic payouts.'}
                    </p>
                    <a
                      className="btn btn-primary btn-sm"
                      style={{ width: '100%' }}
                      href="/dashboard?portal=hub&workflow=connections"
                    >
                      Manage in Hub Connections
                    </a>
                  </div>
                )}
              </div>
            </div>

            {isArtist && <ArtistDepositActivity />}

            {!isShopTreasury && boothRent?.configured && (
              <div className="vault-card card booth-rent-card">
                <div className="nexus-panel-header">
                  <span>{Icons.calendar} Booth rent</span>
                  <small className={`booth-rent-state booth-rent-state--${boothRent.status}`}>
                    {boothRent.status.replaceAll('_', ' ')}
                  </small>
                </div>
                <div className="vault-card-body booth-rent-body">
                  <div className="booth-rent-amount">
                    <strong>{formatKnownUSD(boothRent.installment_amount_cents)}</strong>
                    <span>{boothRent.interval === 'biweekly' ? 'every 2 weeks' : 'monthly'}</span>
                  </div>
                  {boothRent.interval === 'biweekly' && (
                    <p className="vault-stripe-desc">
                      Based on a {formatKnownUSD(boothRent.amount_cents)} monthly booth-rent obligation.
                    </p>
                  )}
                  {boothRent.next_due_date && (
                    <p className="vault-stripe-desc">
                      Next payment due {formatDueDate(boothRent.next_due_date)}.
                    </p>
                  )}
                  <div className="booth-rent-flow" aria-label="Booth rent payment model">
                    <span>Client revenue</span>
                    <strong>100% to you</strong>
                    <span>Rent billed separately</span>
                  </div>
                  {boothRent.payment_method === 'cash' && (
                    <p className="vault-stripe-desc">
                      Pay the shop directly. Staff records each cash payment in E-con as a contractor contribution.
                    </p>
                  )}
                  {boothRent.payment_method === 'one_time' && (
                    <p className="vault-stripe-desc">
                      Pay each booth-rent period through a one-time Stripe checkout. No automatic draft is created.
                    </p>
                  )}
                  {boothRent.payment_method === 'autodraft' && (
                    <p className="vault-stripe-desc">
                      Stripe automatically drafts booth rent each period after you subscribe.
                    </p>
                  )}
                  {boothRent.current_period_end && (
                    <p className="vault-stripe-desc">
                      Current period ends {formatDate(boothRent.current_period_end)}
                      {boothRent.cancel_at_period_end && boothRent.payment_method === 'autodraft'
                        ? '. Stripe will cancel after this period.'
                        : '.'}
                    </p>
                  )}
                  {boothRent.payment_method !== 'cash' && (
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={boothRentAction !== null}
                      onClick={() => void handleBoothRentRedirect(
                        boothRent.payment_method === 'autodraft'
                          && boothRent.subscription_id
                          && ['active', 'trialing', 'past_due', 'unpaid', 'paused'].includes(boothRent.status)
                          ? 'portal'
                          : 'checkout',
                      )}
                    >
                      {boothRentAction
                        ? 'Opening Stripe...'
                        : boothRent.payment_method === 'one_time'
                          ? 'Pay this period'
                          : boothRent.subscription_id
                          && ['active', 'trialing', 'past_due', 'unpaid', 'paused'].includes(boothRent.status)
                          ? 'Manage payment'
                          : 'Start auto draft'}
                    </button>
                  )}
                  {boothRentMessage && <p className="booth-rent-message" role="status">{boothRentMessage}</p>}
                </div>
              </div>
            )}

            <div className="vault-card card">
              <div className="nexus-panel-header">
                <span>{Icons.receipt} Payment evidence</span>
              </div>
              <div className="vault-card-body treasury-evidence-list">
                <div className="treasury-evidence-row">
                  <span>E-con shop ledger</span>
                  <strong>{isShopTreasury ? `${postedPayments.length} posted payment${postedPayments.length === 1 ? '' : 's'}` : 'Shop-scoped'}</strong>
                  <small>{isShopTreasury ? `${stripeLedgerCount} from Stripe` : 'Artist accounts do not receive the shop ledger'}</small>
                </div>
                <div className="treasury-evidence-row">
                  <span>Processor fees</span>
                  <strong>Unavailable</strong>
                  <small>E-con does not yet publish Stripe fee totals, so the UI does not infer them.</small>
                </div>
                <div className="treasury-evidence-row">
                  <span>Payout schedule</span>
                  <strong>{formatPayoutDate(nextPayoutDate, stripeStateKnown)}</strong>
                  <small>Stripe owns the expected payout date. Bank arrival can occur later, and the destination account remains masked in Stripe.</small>
                </div>
              </div>
            </div>

            {isShopTreasury && (
              <div className="vault-card card treasury-payout-card">
                <div className="nexus-panel-header">
                  <span>{Icons.stripe} Settlement accounts</span>
                  <small>Founder sales settle to Ryozan · artists sell through their merchant accounts · {payoutReadyArtists} ready · {payoutSetupArtists} incomplete · {artistPayouts.length - payoutReadyArtists - payoutSetupArtists} not connected</small>
                </div>
                <div className="vault-card-body treasury-payout-roster">
                  {artistPayouts.length === 0 ? (
                    <div className="treasury-payout-empty">
                      No artist connected accounts exist yet. Complete Ryozan's Stripe Connect platform setup before inviting artists.
                    </div>
                  ) : artistPayouts.map((artist) => {
                    const state = artist.role === 'founder'
                      ? 'Shop settlement configured'
                      : artist.routing_ready
                      ? 'Direct payments and payouts ready'
                      : artist.provider_state === 'unavailable'
                        ? 'Stripe status unavailable'
                      : artist.stripe_account_id
                        ? `Setup incomplete${artist.requirements_due ? ` · ${artist.requirements_due} requirement${artist.requirements_due === 1 ? '' : 's'} due` : ''}`
                        : 'Not connected'
                    return (
                      <div className="treasury-payout-row" key={artist.id}>
                        <span className={`vault-status-dot ${artist.routing_ready ? 'vault-status-live' : 'vault-status-inactive'}`} />
                        <div>
                          <strong>{artist.name}</strong>
                          <small>
                            {state} · {artist.role === 'founder'
                              ? '100% to shop · weekly owner draw'
                              : `${artist.commission_rate ?? 70}% artist share`}
                          </small>
                        </div>
                        {artist.routing_ready ? (
                          <em>{artist.role === 'founder' ? 'Shop ready' : 'Merchant ready'}</em>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            disabled={artistOnboardingId === artist.id}
                            onClick={() => void handleArtistOnboard(artist.id)}
                          >
                            {artistOnboardingId === artist.id ? 'Opening...' : artist.stripe_account_id ? 'Resume setup' : 'Connect Stripe'}
                          </button>
                        )}
                      </div>
                    )
                  })}
                  {artistOnboardingMessage && <p className="booth-rent-message" role="alert">{artistOnboardingMessage}</p>}
                  {artistOnboardingNeedsPlatformSetup && (
                    <a
                      className="btn btn-primary btn-sm"
                      href={STRIPE_CONNECT_PLATFORM_PROFILE_URL}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Complete Stripe platform profile
                    </a>
                  )}
                </div>
              </div>
            )}

            {isShopTreasury && (
              <div className="vault-card card booth-rent-admin-card">
                <div className="nexus-panel-header">
                  <span>{Icons.calendar} Artist payroll & pay structures</span>
                  <small>Individual commission, booth, and guest-artist settings</small>
                </div>
                <div className="treasury-evidence-row">
                  <span>Contractor contributions</span>
                  <strong>{formatKnownUSD(ledgerSummary?.contractor_contributions_cents ?? null)}</strong>
                  <small>{ledgerSummary?.contractor_contribution_count ?? 0} recorded shop shares and booth-rent receipts</small>
                </div>
                <div className="vault-card-body booth-rent-roster">
                  {artistPayouts.filter((artist) => artist.role === 'artist').map((artist) => {
                    const rent = artist.booth_rent ?? EMPTY_BOOTH_RENT
                    const draft = payDrafts[artist.id] || {
                      mode: rent.configured ? 'booth' as const : 'commission' as const,
                      commissionRate: String(artist.commission_rate ?? 70),
                      amount: '',
                      interval: 'month' as const,
                      paymentMethod: 'cash' as const,
                      isGuestArtist: artist.is_guest_artist,
                      nextDueDate: nextMonthFirst(),
                    }
                    const subscriptionLocked = Boolean(
                      rent.subscription_id
                      && ['active', 'trialing', 'past_due', 'unpaid', 'incomplete', 'paused'].includes(rent.status)
                    )
                    return (
                      <div className="booth-rent-row pay-structure-row" key={artist.id}>
                        <div className="booth-rent-artist pay-structure-artist">
                          <div>
                            <strong>{artist.name}</strong>
                            {artist.is_guest_artist && <span className="guest-artist-badge">Guest</span>}
                          </div>
                          <small>
                            {rent.configured
                              ? `${rent.status.replaceAll('_', ' ')} · 100% artist share · ${rent.payment_method.replace('_', ' ')} · ${rent.interval === 'biweekly' ? 'every 2 weeks' : 'monthly'}${rent.next_due_date ? ` · due ${formatDueDate(rent.next_due_date)}` : ''}`
                              : `${artist.commission_rate ?? 70}% artist commission`}
                          </small>
                        </div>
                        <label className="pay-structure-field">
                          <span>Artist type</span>
                          <select
                            value={draft.isGuestArtist ? 'guest' : 'resident'}
                            aria-label={`${artist.name} artist type`}
                            onChange={(event) => setPayDrafts((current) => ({
                              ...current,
                              [artist.id]: { ...draft, isGuestArtist: event.target.value === 'guest' },
                            }))}
                          >
                            <option value="resident">Resident</option>
                            <option value="guest">Guest artist</option>
                          </select>
                        </label>
                        <label className="pay-structure-field">
                          <span>Pay structure</span>
                          <select
                            value={draft.mode}
                            disabled={subscriptionLocked}
                            aria-label={`${artist.name} pay structure`}
                            onChange={(event) => setPayDrafts((current) => ({
                              ...current,
                              [artist.id]: { ...draft, mode: event.target.value as PayStructureMode },
                            }))}
                          >
                            <option value="commission">Commission</option>
                            <option value="booth">Booth rent</option>
                          </select>
                        </label>
                        {draft.mode === 'commission' ? (
                          <label className="pay-structure-field pay-structure-rate">
                            <span>Artist keeps</span>
                            <div><input
                              type="number"
                              min="0"
                              max="99"
                              step="1"
                              value={draft.commissionRate}
                              disabled={subscriptionLocked}
                              aria-label={`${artist.name} artist commission rate`}
                              onChange={(event) => setPayDrafts((current) => ({
                                ...current,
                                [artist.id]: { ...draft, commissionRate: event.target.value },
                              }))}
                            /><b>%</b></div>
                          </label>
                        ) : (
                          <div className="pay-structure-booth-fields">
                            <label className="pay-structure-field booth-rent-money-field">
                              <span>Monthly rent</span>
                              <div className="booth-rent-money"><span>$</span><input
                                  type="number"
                                  min="1"
                                  step="0.01"
                                  inputMode="decimal"
                                  value={draft.amount}
                                  disabled={subscriptionLocked}
                                  aria-label={`${artist.name} monthly booth rent amount`}
                                  onChange={(event) => setPayDrafts((current) => ({
                                    ...current,
                                    [artist.id]: { ...draft, amount: event.target.value },
                                  }))}
                                /></div>
                            </label>
                            <label className="pay-structure-field">
                              <span>Schedule</span>
                              <select
                                value={draft.interval}
                                disabled={subscriptionLocked}
                                aria-label={`${artist.name} booth rent schedule`}
                                onChange={(event) => setPayDrafts((current) => ({
                                  ...current,
                                  [artist.id]: { ...draft, interval: event.target.value as BoothRentInterval },
                                }))}
                              >
                                <option value="month">Monthly</option>
                                <option value="biweekly">Biweekly</option>
                              </select>
                            </label>
                            <label className="pay-structure-field">
                              <span>Next due</span>
                              <input
                                type="date"
                                value={draft.nextDueDate}
                                disabled={subscriptionLocked}
                                aria-label={`${artist.name} next booth rent due date`}
                                onChange={(event) => setPayDrafts((current) => ({
                                  ...current,
                                  [artist.id]: { ...draft, nextDueDate: event.target.value },
                                }))}
                              />
                            </label>
                            <label className="pay-structure-field">
                              <span>Payment</span>
                              <select
                                value={draft.paymentMethod}
                                disabled={subscriptionLocked}
                                aria-label={`${artist.name} booth rent payment method`}
                                onChange={(event) => setPayDrafts((current) => ({
                                  ...current,
                                  [artist.id]: { ...draft, paymentMethod: event.target.value as BoothRentPaymentMethod },
                                }))}
                              >
                                <option value="cash">Cash</option>
                                <option value="one_time">One-time online</option>
                                <option value="autodraft">Auto draft</option>
                              </select>
                            </label>
                          </div>
                        )}
                        <div className="booth-rent-actions pay-structure-actions">
                          {subscriptionLocked && <em>Billing terms managed in Stripe</em>}
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            disabled={boothRentAction === artist.id}
                            onClick={() => void handlePayStructureSave(artist)}
                          >
                            {boothRentAction === artist.id ? 'Saving...' : 'Save settings'}
                          </button>
                          {rent.configured && rent.payment_method === 'cash' && (
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              disabled={boothRentAction === `cash:${artist.id}`}
                              onClick={() => void handleBoothRentCash(artist)}
                            >
                              {boothRentAction === `cash:${artist.id}` ? 'Recording...' : 'Record cash'}
                            </button>
                          )}
                        </div>
                      </div>
                    )
                  })}
                  {boothRentMessage && <p className="booth-rent-message" role="status">{boothRentMessage}</p>}
                </div>
              </div>
            )}

            {isShopTreasury && (
              <div className="vault-card card treasury-rails-card">
                <div className="nexus-panel-header">
                  <span>{Icons.trendUp} Financial rails</span>
                </div>
                <div className="vault-card-body treasury-rail-list">
                  <div className="treasury-rail-row">
                    <span className={`vault-status-dot ${isConnected ? 'vault-status-live' : 'vault-status-inactive'}`} />
                    <div><strong>Stripe</strong><small>Live online checkout, receipts, webhooks, balances, and payouts</small></div>
                    <em>{isConnected ? 'Live' : 'Check'}</em>
                  </div>
                  <div className="treasury-rail-row">
                    <span className={`vault-status-dot ${econStatus?.mercury.ready_to_sync ? 'vault-status-live' : 'vault-status-inactive'}`} />
                    <div><strong>Mercury</strong><small>Bank reads and owner-approved money movement only</small></div>
                    <em>{econStatus?.mercury.ready_to_sync ? 'Ready' : 'Not wired'}</em>
                  </div>
                  <div className="treasury-rail-row">
                    <span className={`vault-status-dot ${econStatus?.plaid.ready_to_sync ? 'vault-status-live' : 'vault-status-inactive'}`} />
                    <div><strong>Plaid</strong><small>Read-only account verification and transaction sync</small></div>
                    <em>{econStatus?.plaid.ready_to_sync ? 'Ready' : 'Not wired'}</em>
                  </div>
                  <div className="treasury-rail-row">
                    <span className="vault-status-dot vault-status-inactive" />
                    <div><strong>Clover</strong><small>In-store only when a live POS contract is proven</small></div>
                    <em>Planned</em>
                  </div>
                </div>
              </div>
            )}

          </div>
        </>
      ) : (
        <ArchiveTab />
      )}
    </div>
  )
}
