/**
 * QuestsPage: Gamification hub for the Collector's Club.
 *
 * Fetches quest definitions + user progress from the API.
 * SVG icons mapped client-side by quest slug.
 */

import { useState, useEffect, useCallback, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useAuthStore } from '../../stores/auth'
import { quests as questsApi, type QuestWithProgress, type QuestSummary } from '../../lib/api'
import EmailConnectQuest from '../../components/EmailConnectQuest'
import InstallAppQuest from '../../components/InstallAppQuest'

/* ── Icon map (slug → SVG) ─────────────────────────── */

const QUEST_ICONS: Record<string, ReactNode> = {
  'first-booking': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>,
  'profile-pic': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>,
  'waiver-signed': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>,
  'connect-email': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>,
  'three-sessions': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>,
  'leave-review': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>,
  'refer-friend': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>,
  'enable-notifications': <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>,
}

const DEFAULT_ICON = <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>

const CATEGORIES = [
  { id: 'all', label: 'All Quests' },
  { id: 'onboarding', label: 'Getting Started' },
  { id: 'loyalty', label: 'Loyalty' },
  { id: 'social', label: 'Social' },
  { id: 'seasonal', label: 'Seasonal' },
] as const

/* ── Component ──────────────────────────────────────── */

export default function QuestsPage() {
  const user = useAuthStore((s) => s.user)
  const [filter, setFilter] = useState<string>('all')
  const [questList, setQuestList] = useState<QuestWithProgress[]>([])
  const [summary, setSummary] = useState<QuestSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [claiming, setClaiming] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const [listRes, summaryRes] = await Promise.all([
        questsApi.list(),
        questsApi.summary(),
      ])
      setQuestList(listRes.quests)
      setSummary(summaryRes)
    } catch {
      // Silently fail. Page still renders with empty state.
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  const handleClaim = async (questId: string) => {
    setClaiming(questId)
    try {
      const result = await questsApi.claim(questId)
      // Update local state
      setQuestList(prev => prev.map(q =>
        q.quest.id === questId ? { ...q, claimed: true, claimed_at: new Date().toISOString() } : q
      ))
      setSummary(prev => prev ? {
        ...prev,
        claimed_count: prev.claimed_count + 1,
        total_xp_earned: prev.total_xp_earned + result.xp_awarded,
        total_coin_earned: prev.total_coin_earned + result.coin_awarded,
      } : prev)
    } catch {
      // ignore
    } finally {
      setClaiming(null)
    }
  }

  const filteredQuests = filter === 'all'
    ? questList
    : questList.filter((q) => q.quest.category === filter)

  const completedCount = summary?.completed_count ?? 0
  const totalQuests = summary?.total_quests ?? questList.length

  return (
    <div className="dashboard-panel" style={{ minHeight: 'auto' }}>
      {/* Header */}
      <header className="panel-header">
        <h1 className="panel-title">Quests</h1>
        <span className="panel-badge">{completedCount}/{totalQuests} completed</span>
      </header>

      {/* Stats row */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
        gap: '0.6rem',
        padding: '1rem 1.25rem',
      }}>
        <StatBox
          label="RYO"
          value={user?.bttcoin_balance ?? 0}
          accent
          icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v12"/><path d="M15.5 9.5a3 3 0 0 0-3-1.5H11a3 3 0 0 0 0 4h2a3 3 0 0 1 0 4h-1.5a3 3 0 0 1-3-1.5"/></svg>}
        />
        <StatBox
          label="Quest XP"
          value={user?.quest_points ?? 0}
          accent
          icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>}
        />
        <StatBox
          label="Completed"
          value={completedCount}
          icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>}
        />
        <StatBox
          label="Available"
          value={totalQuests - completedCount}
          icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>}
        />
      </div>

      {/* Wallet CTA */}
      <div style={{ padding: '0 1.25rem 0.75rem' }}>
        <Link
          to="/dashboard/wallet"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0.75rem 1rem',
            background: 'rgba(250, 174, 21, 0.06)',
            border: '1px solid rgba(250, 174, 21, 0.2)',
            borderRadius: '10px',
            textDecoration: 'none',
            color: 'var(--gold)',
            fontSize: '0.82rem',
            fontWeight: 600,
            transition: 'border-color 0.2s',
          }}
        >
          <span>View RYO Wallet &mdash; {(user?.bttcoin_balance ?? 0).toLocaleString()} balance</span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>
          </svg>
        </Link>
      </div>

      {/* Category filter */}
      <div className="doc-filters">
        {CATEGORIES.map((cat) => (
          <button
            key={cat.id}
            className={`doc-filter-pill${filter === cat.id ? ' active' : ''}`}
            onClick={() => setFilter(cat.id)}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {/* Quest cards */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '2px',
        padding: '0 1.25rem 1.25rem',
      }}>
        {loading ? (
          <div className="doc-empty-state">
            <p className="doc-empty-message">Loading quests...</p>
          </div>
        ) : filteredQuests.length === 0 ? (
          <div className="doc-empty-state">
            <p className="doc-empty-message">No quests in this category yet.</p>
          </div>
        ) : (
          filteredQuests.map((qp) => (
            <QuestCard
              key={qp.quest.id}
              qp={qp}
              icon={QUEST_ICONS[qp.quest.slug] || DEFAULT_ICON}
              onClaim={handleClaim}
              claiming={claiming === qp.quest.id}
            />
          ))
        )}
      </div>

      {/* Rewards summary */}
      {summary && summary.claimed_count > 0 && (
        <div style={{
          padding: '1rem 1.25rem',
          borderTop: '1px solid var(--panel-border)',
          display: 'flex',
          justifyContent: 'center',
          gap: '2rem',
        }}>
          <span style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>
            Earned: <strong style={{ color: 'var(--gold)', fontFamily: "'JetBrains Mono', monospace" }}>+{summary.total_xp_earned} XP</strong>
          </span>
          <span style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>
            Earned: <strong style={{ color: 'var(--gold)', fontFamily: "'JetBrains Mono', monospace" }}>+{summary.total_coin_earned} RYO</strong>
          </span>
        </div>
      )}
    </div>
  )
}

/* ── Stat Box ────────────────────────────────────────── */

function StatBox({ label, value, icon, accent }: {
  label: string
  value: number
  icon: ReactNode
  accent?: boolean
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '0.75rem',
      padding: '0.85rem 1rem',
      background: accent ? 'rgba(250, 174, 21, 0.06)' : 'rgba(255, 248, 236, 0.03)',
      border: `1px solid ${accent ? 'rgba(250, 174, 21, 0.35)' : 'var(--panel-border)'}`,
      borderRadius: '10px',
    }}>
      <div style={{
        width: '36px',
        height: '36px',
        borderRadius: '10px',
        background: accent ? 'rgba(250, 174, 21, 0.18)' : 'rgba(250, 174, 21, 0.1)',
        color: 'var(--gold)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}>
        {icon}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.05rem' }}>
        <span style={{
          fontSize: '1.3rem',
          fontWeight: 700,
          color: accent ? 'var(--gold)' : 'var(--text)',
          lineHeight: 1.15,
        }}>{value.toLocaleString()}</span>
        <span style={{
          fontSize: '0.68rem',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
          color: 'var(--muted)',
        }}>{label}</span>
      </div>
    </div>
  )
}

/* ── Quest Card ──────────────────────────────────────── */

function QuestCard({ qp, icon, onClaim, claiming }: {
  qp: QuestWithProgress
  icon: ReactNode
  onClaim: (id: string) => void
  claiming: boolean
}) {
  const { quest, progress, completed, claimed } = qp
  const done = completed || claimed
  const canClaim = completed && !claimed

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '1rem',
      padding: '0.85rem 0.5rem',
      borderBottom: '1px solid rgba(250, 174, 21, 0.08)',
      opacity: claimed ? 0.55 : 1,
      transition: 'background 0.15s',
      borderRadius: '6px',
      cursor: 'default',
    }}>
      {/* Icon */}
      <div style={{
        width: '40px',
        height: '40px',
        borderRadius: '10px',
        background: done ? 'rgba(76, 175, 80, 0.12)' : 'rgba(250, 174, 21, 0.08)',
        color: done ? 'var(--ok)' : 'var(--gold)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}>
        {done ? (
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        ) : icon}
      </div>

      {/* Info */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontWeight: 600,
          color: 'var(--text)',
          fontSize: '0.9rem',
          textDecoration: claimed ? 'line-through' : 'none',
        }}>
          {quest.title}
        </div>
        <div style={{
          fontSize: '0.78rem',
          color: 'var(--muted)',
          lineHeight: 1.4,
          marginTop: '0.1rem',
        }}>
          {quest.description}
        </div>
        {/* Progress bar */}
        {quest.target_count > 1 && !done && (
          <div style={{ marginTop: '0.4rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <div style={{
              flex: 1,
              height: '4px',
              background: 'rgba(250, 174, 21, 0.1)',
              borderRadius: '2px',
              overflow: 'hidden',
              maxWidth: '120px',
            }}>
              <div style={{
                width: `${(progress / quest.target_count) * 100}%`,
                height: '100%',
                background: 'var(--gold)',
                borderRadius: '2px',
                transition: 'width 0.3s ease',
              }} />
            </div>
            <span style={{
              fontSize: '0.65rem',
              color: 'var(--muted)',
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              {progress}/{quest.target_count}
            </span>
          </div>
        )}
        {/* Inline email connect form for the connect-email quest */}
        {quest.slug === 'connect-email' && !done && <EmailConnectQuest />}
        {quest.slug === 'enable-notifications' && !done && <InstallAppQuest />}
      </div>

      {/* Rewards + Claim */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: '0.2rem',
        flexShrink: 0,
      }}>
        {canClaim ? (
          <button
            onClick={() => onClaim(quest.id)}
            disabled={claiming}
            style={{
              padding: '4px 14px',
              background: 'var(--gold)',
              color: 'var(--bg)',
              border: 'none',
              borderRadius: '6px',
              fontSize: '0.72rem',
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
              cursor: claiming ? 'not-allowed' : 'pointer',
              opacity: claiming ? 0.6 : 1,
            }}
          >
            {claiming ? '...' : 'Claim'}
          </button>
        ) : (
          <>
            <span style={{
              fontSize: '0.72rem',
              fontWeight: 600,
              color: 'var(--gold)',
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              +{quest.xp_reward} XP
            </span>
            <span style={{
              fontSize: '0.68rem',
              color: 'var(--muted)',
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              +{quest.coin_reward} coin
            </span>
          </>
        )}
      </div>
    </div>
  )
}
