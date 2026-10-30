/**
 * GuildPage: Coming soon landing for the Guild mod.
 *
 * Five planned charms: Quests, Leaderboard, Class, Inspire, Gauntlet.
 * Uses the same HUD chrome as other mods.
 */

import { Link } from 'react-router-dom'

const charms = [
  {
    name: 'Quests',
    desc: 'Achievement-based tasks that earn RYO and XP.',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
      </svg>
    ),
  },
  {
    name: 'Leaderboard',
    desc: 'Rankings, streaks, and friendly competition.',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>
      </svg>
    ),
  },
  {
    name: 'Class',
    desc: 'Group learning modules and skill workshops.',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
      </svg>
    ),
  },
  {
    name: 'Inspire',
    desc: 'Shared reference library and mood boards.',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
      </svg>
    ),
  },
  {
    name: 'Gauntlet',
    desc: 'Mini games, drills, challenges, and trials.',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="m9 12 2 2 4-4"/><path d="M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9c1.46 0 2.84.35 4.06.96"/><path d="M21 3v4h-4"/>
      </svg>
    ),
  },
]

export default function GuildPage() {
  return (
    <div className="dashboard-panel home-panel nexus">
      <div className="guild-coming-soon">
        <div className="guild-hero">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>
          </svg>
          <h2 className="guild-hero-title">The Guild is Coming</h2>
          <p className="guild-hero-desc">
            Level up your craft. Compete with your crew. Earn rewards.
          </p>
        </div>

        <div className="guild-charms-grid">
          {charms.map((charm) => (
            <div key={charm.name} className="guild-charm-card">
              <div className="guild-charm-icon">{charm.icon}</div>
              <div>
                <h3 className="guild-charm-name">{charm.name}</h3>
                <p className="guild-charm-desc">{charm.desc}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="guild-cta">
          <Link to="/dashboard" className="btn btn-outline btn-sm">
            Back to Nexus
          </Link>
        </div>
      </div>
    </div>
  )
}
