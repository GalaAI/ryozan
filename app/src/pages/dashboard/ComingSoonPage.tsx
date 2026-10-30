/**
 * ComingSoonPage: Reusable coming-soon landing for dashboard pages.
 * Uses the same .shop-coming-soon CSS pattern for consistency.
 */

interface ComingSoonFeature {
  icon: React.ReactNode
  label: string
  desc: string
}

interface ComingSoonPageProps {
  title: string
  description: string
  icon: React.ReactNode
  features?: ComingSoonFeature[]
}

export default function ComingSoonPage({ title, description, icon, features }: ComingSoonPageProps) {
  return (
    <div className="shop-page shop-coming-soon">
      <div className="shop-coming-soon-content">
        <div className="shop-coming-soon-icon">
          {icon}
        </div>
        <h2 className="shop-coming-soon-title">{title}</h2>
        <p className="shop-coming-soon-subtitle">{description}</p>

        {features && features.length > 0 && (
          <div className="shop-coming-soon-items">
            {features.map((f, i) => (
              <div key={i} className="shop-coming-soon-item">
                {f.icon}
                <div>
                  <span className="shop-coming-soon-item-label">{f.label}</span>
                  <span className="shop-coming-soon-item-desc">{f.desc}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        <p className="shop-coming-soon-cta">Coming soon</p>
      </div>
    </div>
  )
}
