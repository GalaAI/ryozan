/**
 * AdminUsers: User management panel (admin/founder only).
 *
 * Lists all users with role badges, search, pagination,
 * and inline role changes. Uses GET /api/v1/users/ and PATCH /api/v1/users/:id/role
 */

import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button, ReceiptBlock } from '@ryozan/ui'
import { users as usersApi, auth as authApi, type User, type ArtistAccountStatus } from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { CustomSelect } from '../../components/CustomSelect'
import { useToast } from '../../components/Toast'

const ROLES = ['collector', 'assistant', 'artist', 'admin', 'founder'] as const
const PAGE_SIZE = 20

const ROLE_COLORS: Record<string, string> = {
  founder: 'rgba(250, 174, 21, 0.9)',
  admin: 'rgba(217, 83, 79, 0.85)',
  artist: 'rgba(76, 175, 80, 0.85)',
  assistant: 'rgba(100, 181, 246, 0.85)',
  collector: 'rgba(185, 173, 150, 0.7)',
}

type SortColumn = 'name' | 'email' | 'role' | 'created_at' | 'bttcoin_balance'

function SortIcon({ col, sortCol, sortAsc }: { col: SortColumn; sortCol: SortColumn; sortAsc: boolean }) {
  return (
    <span style={{ marginLeft: '0.3rem', opacity: sortCol === col ? 1 : 0.3, fontSize: '0.65rem' }}>
      {sortCol === col ? (sortAsc ? '\u25B2' : '\u25BC') : '\u25BC'}
    </span>
  )
}

export default function AdminUsers() {
  const toast = useToast()
  const isOwner = useAuthStore((state) => state.user?.role === 'founder')
  const [accountStatus, setAccountStatus] = useState<Record<string, ArtistAccountStatus>>({})
  const [statusFailed, setStatusFailed] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const [userList, setUserList] = useState<User[]>([])
  const [total, setTotal] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [sortCol, setSortCol] = useState<SortColumn>('created_at')
  const [sortAsc, setSortAsc] = useState(false)

  // Fetch only: state is written once the requests answer.
  const requestUsers = useCallback(() => usersApi.list({ limit: 200 })
    .then(async (data) => {
      setUserList(data)
      setTotal(data.length)
      setAccountStatus({})
      setStatusFailed(false)
      if (isOwner) {
        try {
          const statuses = await usersApi.artistAccountStatus()
          setAccountStatus(Object.fromEntries(statuses.map((status) => [status.id, status])))
        } catch {
          setStatusFailed(true)
        }
      }
    })
    .catch(() => {
      toast.error('Failed to load users')
    })
    .finally(() => setIsLoading(false)), [toast, isOwner])

  const loadUsers = () => {
    setIsLoading(true)
    return requestUsers()
  }

  // A changed owner flag reloads: the loading state resets during render.
  const [usersKey, setUsersKey] = useState(() => [toast, isOwner] as const)
  if (usersKey[0] !== toast || usersKey[1] !== isOwner) {
    setUsersKey([toast, isOwner])
    setIsLoading(true)
  }

  useEffect(() => { requestUsers() }, [requestUsers])

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      const updated = await usersApi.updateRole(userId, newRole)
      setUserList((prev) =>
        prev.map((u) => (u.id === userId ? updated : u))
      )
      toast.success(`Role updated to ${newRole}`)
    } catch {
      toast.error('Failed to update role')
    }
  }

  const [proxyPickerFor, setProxyPickerFor] = useState<string | null>(null)

  const handleProxyChange = async (userId: string, proxyFor: string | null) => {
    try {
      const updated = await usersApi.setProxy(userId, proxyFor)
      setUserList((prev) => prev.map((u) => (u.id === userId ? updated : u)))
      setProxyPickerFor(null)
      toast.success(proxyFor ? 'Inbox relay enabled' : 'Inbox relay cleared')
    } catch {
      toast.error('Failed to update inbox relay')
    }
  }

  const handleSort = (col: typeof sortCol) => {
    if (sortCol === col) {
      setSortAsc(!sortAsc)
    } else {
      setSortCol(col)
      setSortAsc(true)
    }
    setPage(0)
  }

  // ── Invite modal state ──
  // Opened straight away when navigated with ?invite=1 (the form starts blank).
  const [inviteOpen, setInviteOpen] = useState(() => searchParams.get('invite') === '1')
  const [inviteName, setInviteName] = useState('')
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<'collector' | 'assistant' | 'artist' | 'admin'>('artist')
  const [inviteSlug, setInviteSlug] = useState('')
  const [inviteSending, setInviteSending] = useState(false)
  const [inviteResult, setInviteResult] = useState<{ url: string; hours: number } | null>(null)
  const [inviteError, setInviteError] = useState('')
  const [inviteCopied, setInviteCopied] = useState(false)

  // The ?invite=1 that opened the modal is removed from the URL.
  useEffect(() => {
    if (searchParams.get('invite') === '1') {
      setSearchParams({}, { replace: true }) // clean URL
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const resetInvite = () => {
    setInviteName(''); setInviteEmail(''); setInviteRole('artist')
    setInviteSlug(''); setInviteResult(null); setInviteError(''); setInviteCopied(false)
  }

  const handleInviteSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setInviteError('')
    setInviteSending(true)
    try {
      const res = await authApi.generateInvite({
        email: inviteEmail,
        name: inviteName,
        role: inviteRole,
        ...(inviteRole === 'artist' && inviteSlug ? { slug: inviteSlug } : {}),
      })
      setInviteResult({ url: res.invite_url, hours: res.expires_in_hours })
      toast.success(`Invite sent to ${inviteEmail}`)
      loadUsers() // refresh list in background
    } catch (err: unknown) {
      setInviteError(err instanceof Error ? err.message : 'Failed to generate invite')
    } finally {
      setInviteSending(false)
    }
  }

  const copyInviteUrl = () => {
    if (inviteResult) {
      navigator.clipboard.writeText(inviteResult.url)
      setInviteCopied(true)
      setTimeout(() => setInviteCopied(false), 2000)
    }
  }

  // Filter
  const filtered = search.trim()
    ? userList.filter((u) =>
        u.name.toLowerCase().includes(search.toLowerCase()) ||
        u.email.toLowerCase().includes(search.toLowerCase()) ||
        u.role.toLowerCase().includes(search.toLowerCase())
      )
    : userList

  // Sort
  const sorted = [...filtered].sort((a, b) => {
    let cmp = 0
    switch (sortCol) {
      case 'name': cmp = (a.name || '').localeCompare(b.name || ''); break
      case 'email': cmp = a.email.localeCompare(b.email); break
      case 'role': cmp = a.role.localeCompare(b.role); break
      case 'created_at': cmp = a.created_at.localeCompare(b.created_at); break
      case 'bttcoin_balance': cmp = (a.bttcoin_balance ?? 0) - (b.bttcoin_balance ?? 0); break
    }
    return sortAsc ? cmp : -cmp
  })

  // Paginate
  const totalPages = Math.ceil(sorted.length / PAGE_SIZE)
  const pageUsers = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  if (isLoading) {
    return <div className="dashboard-panel"><p className="text-muted" style={{ padding: '2rem' }}>Loading users...</p></div>
  }

  return (
    <div className="dashboard-panel" style={isOwner ? { paddingBottom: 'calc(var(--space-20) + env(safe-area-inset-bottom, 0))' } : undefined}>
      <header className="panel-header" style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <h1 className="panel-title">User Management</h1>
          <span className="panel-count">{total} users</span>
        </div>
        {isOwner && <Button onClick={() => void loadUsers()}>Refresh status</Button>}
        <button
          className="action-btn action-btn--gold"
          onClick={() => { resetInvite(); setInviteOpen(true) }}
          style={{ fontSize: '0.8rem', padding: '0.45rem 1rem' }}
        >
          + Invite
        </button>
      </header>

      {/* ── Invite Modal ── */}
      {inviteOpen && (
        <div className="pw-modal-overlay" onClick={() => setInviteOpen(false)}>
          <div className="pw-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '420px' }}>
            {inviteResult ? (
              /* ── Success state ── */
              <>
                <h3>Invite Sent!</h3>
                <p style={{ color: 'var(--muted)', fontSize: '0.85rem', margin: '0.5rem 0 1rem' }}>
                  A branded email has been sent to <strong style={{ color: 'var(--gold-vibrant)' }}>{inviteEmail}</strong>.
                  <br />The link expires in {inviteResult.hours} hours.
                </p>
                <label style={{ display: 'block', marginBottom: '0.75rem' }}>
                  Invite Link (backup)
                  <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.35rem' }}>
                    <input
                      type="text"
                      readOnly
                      value={inviteResult.url}
                      className="form-input"
                      style={{ flex: 1, fontSize: '0.75rem' }}
                      onClick={(e) => (e.target as HTMLInputElement).select()}
                    />
                    <button
                      type="button"
                      className="action-btn action-btn--gold"
                      onClick={copyInviteUrl}
                      style={{ fontSize: '0.75rem', padding: '0.4rem 0.75rem', whiteSpace: 'nowrap' }}
                    >
                      {inviteCopied ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                </label>
                <div className="pw-modal-actions">
                  <button type="button" onClick={() => setInviteOpen(false)} className="action-btn">
                    Done
                  </button>
                  <button
                    type="button"
                    className="action-btn action-btn--gold"
                    onClick={resetInvite}
                  >
                    Invite Another
                  </button>
                </div>
              </>
            ) : (
              /* ── Form state ── */
              <form onSubmit={handleInviteSubmit}>
                <h3>Invite Team Member</h3>
                <p style={{ color: 'var(--muted)', fontSize: '0.85rem', margin: '0.5rem 0 1rem' }}>
                  You get a link to send them. If email is connected, they get it by email too.
                </p>

                {inviteError && <div className="pw-modal-error">{inviteError}</div>}

                <label>
                  Full Name
                  <input
                    type="text"
                    value={inviteName}
                    onChange={(e) => setInviteName(e.target.value)}
                    required
                    autoFocus
                    placeholder="e.g. Alex Rivera"
                  />
                </label>

                <label>
                  Email
                  <input
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    required
                    placeholder="e.g. cailee@example.com"
                  />
                </label>

                <label>
                  Role
                  <select
                    value={inviteRole}
                    onChange={(e) => setInviteRole(e.target.value as typeof inviteRole)}
                    className="form-input"
                  >
                    <option value="artist">Artist</option>
                    <option value="assistant">Assistant</option>
                    <option value="admin">Admin</option>
                    <option value="collector">Collector</option>
                  </select>
                </label>

                {inviteRole === 'artist' && (
                  <label>
                    Artist Slug (URL path)
                    <input
                      type="text"
                      value={inviteSlug}
                      onChange={(e) => setInviteSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                      placeholder="e.g. cailee"
                    />
                    <span style={{ fontSize: '0.72rem', color: 'var(--muted)', display: 'block', marginTop: '0.2rem' }}>
                      {window.location.host}/artist/{inviteSlug || '...'}
                    </span>
                  </label>
                )}

                <div className="pw-modal-actions">
                  <button type="button" onClick={() => setInviteOpen(false)} disabled={inviteSending}>
                    Cancel
                  </button>
                  <button type="submit" className="pw-modal-submit" disabled={inviteSending}>
                    {inviteSending ? 'Sending...' : 'Send Invite'}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* Search bar */}
      <div style={{ padding: '1rem 1.25rem 0' }}>
        <input
          type="text"
          placeholder="Search by name, email, or role..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(0) }}
          className="form-input"
          style={{ width: '100%', maxWidth: '400px' }}
        />
        {search && (
          <span style={{ marginLeft: '0.75rem', fontSize: '0.8rem', color: 'var(--muted)' }}>
            {filtered.length} result{filtered.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      <div className="users-table-wrap" style={{ padding: '1rem', overflowX: 'auto' }}>
        <table className="users-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ cursor: 'pointer' }} onClick={() => handleSort('name')}>
                Name <SortIcon col="name" sortCol={sortCol} sortAsc={sortAsc} />
              </th>
              <th style={{ cursor: 'pointer' }} onClick={() => handleSort('email')}>
                Email <SortIcon col="email" sortCol={sortCol} sortAsc={sortAsc} />
              </th>
              <th style={{ cursor: 'pointer' }} onClick={() => handleSort('role')}>
                Role <SortIcon col="role" sortCol={sortCol} sortAsc={sortAsc} />
              </th>
              <th style={{ cursor: 'pointer' }} onClick={() => handleSort('created_at')}>
                Joined <SortIcon col="created_at" sortCol={sortCol} sortAsc={sortAsc} />
              </th>
              <th style={{ cursor: 'pointer' }} onClick={() => handleSort('bttcoin_balance')}>
                RYO <SortIcon col="bttcoin_balance" sortCol={sortCol} sortAsc={sortAsc} />
              </th>
              <th>Inbox Relay</th>
              {isOwner && <th>Artist account</th>}
            </tr>
          </thead>
          <tbody>
            {pageUsers.length === 0 ? (
              <tr>
                <td colSpan={isOwner ? 7 : 6} style={{ textAlign: 'center', padding: '2rem', color: 'var(--muted)' }}>
                  {search ? 'No users match your search.' : 'No users found.'}
                </td>
              </tr>
            ) : (
              pageUsers.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                      <div style={{
                        width: '28px',
                        height: '28px',
                        borderRadius: '50%',
                        background: u.avatar_url ? `url(${u.avatar_url}) center/cover` : 'rgba(250, 174, 21, 0.12)',
                        color: 'var(--gold-vibrant)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '0.7rem',
                        fontWeight: 700,
                        flexShrink: 0,
                      }}>
                        {!u.avatar_url && (u.name || '?').charAt(0).toUpperCase()}
                      </div>
                      {u.name}
                    </div>
                  </td>
                  <td style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>{u.email}</td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <span style={{
                        display: 'inline-block',
                        width: '8px',
                        height: '8px',
                        borderRadius: '50%',
                        background: ROLE_COLORS[u.role] || 'var(--muted)',
                        flexShrink: 0,
                      }} />
                      <CustomSelect
                        value={u.role}
                        onChange={(val) => handleRoleChange(u.id, val)}
                        options={ROLES.map((r) => ({ value: r, label: r }))}
                        compact
                      />
                    </div>
                  </td>
                  <td>{new Date(u.created_at).toLocaleDateString()}</td>
                  <td>
                    <span style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: '0.82rem',
                      color: u.bttcoin_balance > 0 ? 'var(--gold-vibrant)' : 'var(--muted)',
                    }}>
                      {u.bttcoin_balance}
                    </span>
                  </td>
                  <td style={{ minWidth: '140px' }}>
                    {u.proxy_for ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                        <span style={{ fontSize: '0.75rem', color: 'var(--gold-vibrant)' }}>
                          {userList.find((x) => x.id === u.proxy_for)?.name?.split(' ')[0] ?? 'Unknown'}
                        </span>
                        <button
                          onClick={() => handleProxyChange(u.id, null)}
                          title="Clear relay"
                          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '0 0.2rem', color: 'var(--muted)', fontSize: '0.8rem', lineHeight: 1 }}
                        >
                          {/* X icon */}
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                      </div>
                    ) : proxyPickerFor === u.id ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                        <select
                          autoFocus
                          className="form-input"
                          style={{ fontSize: '0.75rem', padding: '0.2rem 0.4rem', height: '26px' }}
                          defaultValue=""
                          onChange={(e) => e.target.value && handleProxyChange(u.id, e.target.value)}
                          onBlur={() => setProxyPickerFor(null)}
                        >
                          <option value="" disabled>Pick user…</option>
                          {userList
                            .filter((x) => x.id !== u.id && ['artist', 'admin', 'founder'].includes(x.role))
                            .map((x) => (
                              <option key={x.id} value={x.id}>{x.name}</option>
                            ))}
                        </select>
                        <button
                          onClick={() => setProxyPickerFor(null)}
                          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', fontSize: '0.8rem', lineHeight: 1, padding: 0 }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setProxyPickerFor(u.id)}
                        style={{
                          background: 'none',
                          border: '1px dashed var(--border-subtle)',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          color: 'var(--muted)',
                          fontSize: '0.72rem',
                          padding: '0.2rem 0.5rem',
                        }}
                      >
                        + Relay
                      </button>
                    )}
                  </td>
                  {isOwner && (
                    <td>
                      {u.role === 'artist' ? (
                        accountStatus[u.id] ? (
                          <ReceiptBlock eyebrow="Account status" rows={[
                            { label: 'Password', value: accountStatus[u.id].password_status === 'setup_required'
                              ? 'Setup/change required'
                              : accountStatus[u.id].password_status === 'user_set' ? 'User-set password' : 'Historical unknown' },
                            { label: 'Last password change', value: accountStatus[u.id].password_changed_at
                              ? `${new Date(accountStatus[u.id].password_changed_at!).toLocaleString()} (${accountStatus[u.id].password_change_method})` : 'Not recorded' },
                            { label: 'Last successful login', value: accountStatus[u.id].last_login_at
                              ? `${new Date(accountStatus[u.id].last_login_at!).toLocaleString()} (${accountStatus[u.id].last_login_method})` : 'Not recorded' },
                          ]} />
                        ) : statusFailed ? 'Status unavailable. Refresh to retry.' : 'Status not available. Refresh to retry.'
                      ) : null}
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          gap: '0.75rem',
          padding: '0.75rem 1.25rem 1.25rem',
        }}>
          <button
            className="action-btn"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
            style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem', opacity: page === 0 ? 0.4 : 1 }}
          >
            Prev
          </button>
          <span style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>
            Page {page + 1} of {totalPages}
          </span>
          <button
            className="action-btn"
            disabled={page >= totalPages - 1}
            onClick={() => setPage(page + 1)}
            style={{ padding: '0.4rem 0.8rem', fontSize: '0.8rem', opacity: page >= totalPages - 1 ? 0.4 : 1 }}
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
