/**
 * ClubPage — Coming soon landing page for the Ryozan collector community.
 *
 * The full community feed, discussions, and social features are in
 * development. This placeholder uses Ryozan design tokens.
 */

export default function ClubPage() {
  return (
    <div className="shop-page shop-coming-soon">
      <div className="shop-coming-soon-content">
        <div className="shop-coming-soon-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--gold-vibrant)" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/>
            <circle cx="9" cy="7" r="4"/>
            <path d="M23 21v-2a4 4 0 00-3-3.87"/>
            <path d="M16 3.13a4 4 0 010 7.75"/>
          </svg>
        </div>

        <h2 className="shop-coming-soon-title">The Club is Coming</h2>

        <p className="shop-coming-soon-subtitle">
          A private space for Ryozan collectors to connect, share their ink, and earn rewards.
        </p>

        <div className="shop-coming-soon-items">
          <div className="shop-coming-soon-item">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
            </svg>
            <div>
              <span className="shop-coming-soon-item-label">Community Feed</span>
              <span className="shop-coming-soon-item-desc">Share your pieces, ask questions, connect with other collectors</span>
            </div>
          </div>
          <div className="shop-coming-soon-item">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="8" r="7"/>
              <polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>
            </svg>
            <div>
              <span className="shop-coming-soon-item-label">RYO Rewards</span>
              <span className="shop-coming-soon-item-desc">Earn coins on every session, climb the ranks from Prospect to Prestige</span>
            </div>
          </div>
          <div className="shop-coming-soon-item">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M12 2L2 7l10 5 10-5-10-5z"/>
              <path d="M2 17l10 5 10-5"/>
              <path d="M2 12l10 5 10-5"/>
            </svg>
            <div>
              <span className="shop-coming-soon-item-label">Exclusive Drops</span>
              <span className="shop-coming-soon-item-desc">Early access to flash releases and member-only events</span>
            </div>
          </div>
        </div>

        <p className="shop-coming-soon-cta">Coming soon</p>
      </div>
    </div>
  )
}
