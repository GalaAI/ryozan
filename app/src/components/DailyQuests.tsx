/**
 * DailyQuests - Native daily quest panel.
 *
 * Extracted from GalaCanvas iframe. Renders as standard React
 * with CSS classes instead of sandboxed iframe HTML.
 *
 * 3 daily quests, seeded by date. Each slot can be rerolled once per day.
 * State persisted to localStorage.
 */

import { useState, useCallback } from 'react'

/* ── Types ── */

interface DailyQuest {
  id: string
  title: string
  xp: number
  category: 'craft' | 'business' | 'community' | 'growth'
}

/* ── Quest Pool ── */

const QUEST_POOL: DailyQuest[] = [
  { id: 'q-portfolio', title: 'Upload a new portfolio piece', xp: 25, category: 'craft' },
  { id: 'q-sketch', title: 'Sketch something outside your comfort zone', xp: 20, category: 'craft' },
  { id: 'q-technique', title: 'Practice a technique you rarely use', xp: 30, category: 'craft' },
  { id: 'q-study', title: "Study a style you don't normally do", xp: 15, category: 'craft' },
  { id: 'q-flash', title: 'Design a flash sheet piece', xp: 35, category: 'craft' },
  { id: 'q-reference', title: 'Collect 5 reference images for inspiration', xp: 10, category: 'craft' },
  { id: 'q-aftercare', title: 'Review and update your aftercare templates', xp: 15, category: 'business' },
  { id: 'q-station', title: 'Deep clean and organize your station', xp: 20, category: 'business' },
  { id: 'q-document', title: 'Document your setup process', xp: 25, category: 'business' },
  { id: 'q-pricing', title: 'Review your pricing for one service', xp: 15, category: 'business' },
  { id: 'q-inventory', title: 'Check supply inventory and reorder if needed', xp: 10, category: 'business' },
  { id: 'q-reach', title: 'Reach out to a past client', xp: 20, category: 'community' },
  { id: 'q-feedback', title: 'Ask a client for a review or testimonial', xp: 25, category: 'community' },
  { id: 'q-collab', title: 'Message a fellow artist about a collab idea', xp: 30, category: 'community' },
  { id: 'q-share', title: 'Share a tip or technique with a crew member', xp: 15, category: 'community' },
  { id: 'q-bio', title: 'Update your artist bio or profile', xp: 15, category: 'growth' },
  { id: 'q-content', title: 'Post a work-in-progress to socials', xp: 20, category: 'growth' },
  { id: 'q-learn', title: 'Watch a tutorial on a new technique', xp: 15, category: 'growth' },
  { id: 'q-goal', title: 'Write down one professional goal for this week', xp: 10, category: 'growth' },
  { id: 'q-network', title: 'Follow or engage with 3 artists you admire', xp: 10, category: 'growth' },
]

const CATEGORY_COLORS: Record<string, string> = {
  craft: '#c9a84c',
  business: '#4a9c8f',
  community: '#7b68c4',
  growth: '#c46848',
}

/* ── Helpers ── */

function seededShuffle<T>(arr: T[], seed: number): T[] {
  const copy = [...arr]
  let s = seed
  for (let i = copy.length - 1; i > 0; i--) {
    s = (s * 16807 + 0) % 2147483647
    const j = s % (i + 1);
    [copy[i], copy[j]] = [copy[j], copy[i]]
  }
  return copy
}

function getTodaySeed(): number {
  const d = new Date()
  return d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate()
}

function pickDailyQuests(salts: [number, number, number]): DailyQuest[] {
  const results: DailyQuest[] = []
  const usedIds = new Set<string>()
  for (const salt of salts) {
    const seed = getTodaySeed() + salt
    const shuffled = seededShuffle(QUEST_POOL, seed)
    for (const q of shuffled) {
      if (!usedIds.has(q.id)) {
        results.push(q)
        usedIds.add(q.id)
        break
      }
    }
  }
  return results
}

const RefreshIcon = (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 4 23 10 17 10" />
    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
  </svg>
)

/* ── Component ── */

export default function DailyQuests() {
  const todayKey = getTodaySeed().toString()

  const [questSalts, setQuestSalts] = useState<[number, number, number]>(() => {
    const saved = localStorage.getItem(`bttco_quest_salts_${todayKey}`)
    if (saved) {
      try { return JSON.parse(saved) } catch { /* fall through */ }
    }
    return [0, 100, 200]
  })

  const [refreshedSlots, setRefreshedSlots] = useState<Set<number>>(() => {
    const saved = localStorage.getItem(`bttco_quest_refreshed_${todayKey}`)
    if (saved) {
      try { return new Set(JSON.parse(saved)) } catch { /* fall through */ }
    }
    return new Set()
  })

  const quests = pickDailyQuests(questSalts)

  const refreshQuest = useCallback((slotIndex: number) => {
    if (refreshedSlots.has(slotIndex)) return
    setQuestSalts((prev) => {
      const next = [...prev] as [number, number, number]
      next[slotIndex] = prev[slotIndex] + 1000 + Math.floor(Math.random() * 500)
      localStorage.setItem(`bttco_quest_salts_${todayKey}`, JSON.stringify(next))
      return next
    })
    setRefreshedSlots((prev) => {
      const next = new Set(prev)
      next.add(slotIndex)
      localStorage.setItem(`bttco_quest_refreshed_${todayKey}`, JSON.stringify([...next]))
      return next
    })
  }, [refreshedSlots, todayKey])

  return (
    <div className="daily-quests">
      <div className="daily-quests-header">
        <span className="daily-quests-label">Daily Quests</span>
      </div>
      <div className="daily-quests-list">
        {quests.map((q, i) => (
          <div key={q.id} className="daily-quest-row">
            <span className="daily-quest-dot" style={{ background: CATEGORY_COLORS[q.category] }} />
            <div className="daily-quest-info">
              <span className="daily-quest-title">{q.title}</span>
              <span className="daily-quest-xp">+{q.xp} XP</span>
            </div>
            <button
              className={`daily-quest-reroll${refreshedSlots.has(i) ? ' used' : ''}`}
              onClick={() => refreshQuest(i)}
              disabled={refreshedSlots.has(i)}
              title={refreshedSlots.has(i) ? 'Already rerolled today' : 'Reroll quest'}
            >
              {RefreshIcon}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
