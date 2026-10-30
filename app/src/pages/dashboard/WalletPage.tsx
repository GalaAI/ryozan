/**
 * WalletPage: RYO wallet for Collectors Club members.
 *
 * Displays balance, transaction history, earn/spend mechanics,
 * and USD conversion. Members-only economy.
 */

import { useState, useEffect } from 'react'
import { useAuthStore } from '../../stores/auth'
import { bttcoin, type BTTCoinTransaction, type BTTCoinWallet } from '../../lib/api'

const TX_TYPE_LABELS: Record<string, { label: string; color: string; sign: string }> = {
  earn: { label: 'Earned', color: 'var(--ok)', sign: '+' },
  spend: { label: 'Spent', color: 'var(--danger)', sign: '-' },
  bonus: { label: 'Bonus', color: 'var(--gold)', sign: '+' },
  refund: { label: 'Refund', color: 'var(--muted)', sign: '+' },
}

export default function WalletPage() {
  const user = useAuthStore((s) => s.user)
  const [wallet, setWallet] = useState<BTTCoinWallet | null>(null)
  const [transactions, setTransactions] = useState<BTTCoinTransaction[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [txFilter, setTxFilter] = useState<string>('all')
  const [apiAvailable, setApiAvailable] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        const w = await bttcoin.getWallet()
        setWallet(w)
        setTransactions(w.transactions || [])
      } catch {
        // Keep the authenticated account balance, but never invent wallet totals.
        setApiAvailable(false)
      } finally {
        setIsLoading(false)
      }
    }
    load()
  }, [])

  const balance = wallet?.balance ?? user?.bttcoin_balance ?? null
  const totalEarned = wallet?.total_earned ?? null
  const totalSpent = wallet?.total_spent ?? null
  const rate = wallet?.conversion_rate ?? null
  const usdValue = balance != null && rate ? (balance / rate).toFixed(2) : null
  const formatKnown = (value: number | null) => value == null ? 'Unavailable' : value.toLocaleString()

  const filteredTx = txFilter === 'all'
    ? transactions
    : transactions.filter((t) => t.type === txFilter)

  const getTimeLabel = (dateStr: string): string => {
    const date = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffHours = diffMs / (1000 * 60 * 60)
    if (diffHours < 1) return 'Just now'
    if (diffHours < 24) return `${Math.floor(diffHours)}h ago`
    if (diffHours < 48) return 'Yesterday'
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }

  if (isLoading) {
    return <div className="dashboard-panel"><p className="text-muted" style={{ padding: '2rem' }}>Loading wallet...</p></div>
  }

  return (
    <div className="dashboard-panel" style={{ minHeight: 'auto' }}>
      <header className="panel-header">
        <h1 className="panel-title">RYO Wallet</h1>
        <span className="panel-badge">{apiAvailable ? 'Wallet live' : 'Partial data'}</span>
      </header>

      {/* ── Balance Hero ── */}
      <div style={{
        padding: '2rem 1.5rem',
        textAlign: 'center',
        borderBottom: '1px solid var(--panel-border)',
      }}>
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.6rem',
          marginBottom: '0.4rem',
        }}>
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="1.8">
            <circle cx="12" cy="12" r="10"/><path d="M12 6v12"/><path d="M15.5 9.5a3 3 0 0 0-3-1.5H11a3 3 0 0 0 0 4h2a3 3 0 0 1 0 4h-1.5a3 3 0 0 1-3-1.5"/>
          </svg>
          <span style={{
            fontSize: '2.8rem',
            fontWeight: 700,
            color: 'var(--gold)',
            fontFamily: "'JetBrains Mono', monospace",
            lineHeight: 1,
          }}>
            {formatKnown(balance)}
          </span>
        </div>
        <div style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>
          RYO Balance
        </div>
        <div style={{
          fontSize: '0.78rem',
          color: 'var(--muted)',
          marginTop: '0.5rem',
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {usdValue != null && rate != null
            ? <>~ ${usdValue} USD <span style={{ opacity: 0.6 }}>({rate} RYO = $1)</span></>
            : 'USD estimate unavailable'}
        </div>
      </div>

      {/* ── Earn / Spend Summary ── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: '0.75rem',
        padding: '1.25rem 1.5rem',
      }}>
        <div style={{
          padding: '1rem',
          background: 'rgba(76, 175, 80, 0.06)',
          border: '1px solid rgba(76, 175, 80, 0.2)',
          borderRadius: '10px',
          textAlign: 'center',
        }}>
          <div style={{ fontSize: '1.4rem', fontWeight: 700, color: 'var(--ok)', fontFamily: "'JetBrains Mono', monospace" }}>
            {totalEarned == null ? 'Unavailable' : `+${totalEarned.toLocaleString()}`}
          </div>
          <div style={{ fontSize: '0.7rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: '0.2rem' }}>
            Total Earned
          </div>
        </div>
        <div style={{
          padding: '1rem',
          background: 'rgba(217, 83, 79, 0.06)',
          border: '1px solid rgba(217, 83, 79, 0.2)',
          borderRadius: '10px',
          textAlign: 'center',
        }}>
          <div style={{ fontSize: '1.4rem', fontWeight: 700, color: 'var(--danger)', fontFamily: "'JetBrains Mono', monospace" }}>
            {totalSpent == null ? 'Unavailable' : `-${totalSpent.toLocaleString()}`}
          </div>
          <div style={{ fontSize: '0.7rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: '0.2rem' }}>
            Total Spent
          </div>
        </div>
      </div>

      {/* ── Program boundary ── */}
      <div style={{ padding: '0 1.5rem 1.25rem' }}>
        <div style={{
          padding: '1rem',
          background: 'rgba(255, 248, 236, 0.03)',
          border: '1px solid var(--panel-border)',
          borderRadius: '10px',
        }}>
          <div style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.6rem' }}>
            Reward balance, not cash
          </div>
          <p style={{ color: 'var(--muted)', fontSize: '0.78rem', lineHeight: 1.5, margin: 0 }}>
            RYO is a shop reward balance. It is separate from deposits, receipts, refunds, and card payments. Current earn and redemption offers appear in Quests and Loot when published.
          </p>
        </div>
      </div>

      {/* ── Transaction History ── */}
      <div style={{ padding: '0 1.5rem 1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Transaction History
          </span>
          <div style={{ display: 'flex', gap: '0.3rem' }}>
            {['all', 'earn', 'spend', 'bonus'].map((f) => (
              <button
                key={f}
                className={`doc-filter-pill${txFilter === f ? ' active' : ''}`}
                onClick={() => setTxFilter(f)}
                style={{ fontSize: '0.7rem', padding: '0.25rem 0.6rem' }}
              >
                {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {!apiAvailable && transactions.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '2rem 1rem',
            color: 'var(--muted)',
            fontSize: '0.85rem',
          }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="1.5" style={{ marginBottom: '0.75rem', opacity: 0.5 }}>
              <circle cx="12" cy="12" r="10"/><path d="M12 6v12"/><path d="M15.5 9.5a3 3 0 0 0-3-1.5H11a3 3 0 0 0 0 4h2a3 3 0 0 1 0 4h-1.5a3 3 0 0 1-3-1.5"/>
            </svg>
            <p>Transaction history is unavailable.</p>
            <p style={{ fontSize: '0.78rem', marginTop: '0.5rem' }}>
              Your account balance is shown above when available. No zero-dollar history or totals have been substituted.
            </p>
          </div>
        ) : filteredTx.length === 0 ? (
          <p style={{ textAlign: 'center', color: 'var(--muted)', fontSize: '0.85rem', padding: '1.5rem' }}>
            No {txFilter === 'all' ? '' : txFilter} transactions yet.
          </p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {filteredTx.map((tx) => {
              const meta = TX_TYPE_LABELS[tx.type] || TX_TYPE_LABELS.earn
              return (
                <div key={tx.id} style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                  padding: '0.7rem 0.25rem',
                  borderBottom: '1px solid rgba(250, 174, 21, 0.06)',
                }}>
                  <div style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '8px',
                    background: `${meta.color}15`,
                    color: meta.color,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '0.85rem',
                    fontWeight: 700,
                    flexShrink: 0,
                  }}>
                    {meta.sign}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: '0.85rem', color: 'var(--text)', fontWeight: 500 }}>
                      {tx.description}
                    </div>
                    <div style={{ fontSize: '0.72rem', color: 'var(--muted)', marginTop: '0.1rem' }}>
                      {tx.source} &middot; {getTimeLabel(tx.created_at)}
                    </div>
                  </div>
                  <div style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontWeight: 600,
                    fontSize: '0.88rem',
                    color: meta.color,
                    flexShrink: 0,
                  }}>
                    {meta.sign}{tx.amount}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
