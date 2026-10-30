/**
 * LootPage — Coming soon landing for the rewards vault.
 */
import ComingSoonPage from './ComingSoonPage'

const icon = (
  <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--gold, #a67d43)" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a4 4 0 0 0-8 0v2"/><circle cx="12" cy="15" r="2"/>
  </svg>
)

const features = [
  {
    icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/></svg>,
    label: 'RYO Wallet',
    desc: 'Track your balance, earn on every session, climb from Prospect to Prestige',
  },
  {
    icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M14.5 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>,
    label: 'Quests & Badges',
    desc: 'Complete challenges, unlock achievements, earn bonus rewards',
  },
  {
    icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 01-8 0"/></svg>,
    label: 'Rewards Store',
    desc: 'Redeem RYO for merch, discounts, and exclusive prizes',
  },
]

export default function LootPage() {
  return (
    <ComingSoonPage
      title="Loot"
      description="Your rewards vault. RYO wallet, quests, badges, and a marketplace for collectibles."
      icon={icon}
      features={features}
    />
  )
}
