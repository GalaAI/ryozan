/**
 * HeraldsPage — Guides & reference hub.
 *
 * Tabs (Publisher and Reposts are founder and admin only):
 *   Reposts   — Artist posts drafted for the shop account, reviewed before publish
 *   Aftercare — Tattoo aftercare instructions for clients
 *   Herald    — AI-powered area research guide for business owners
 *
 * Accessible from the dashboard sidebar (hamburger on mobile).
 * Route: /dashboard/herald
 */

import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import HeraldRepostsTab from './HeraldRepostsTab'
import { broadcasts, type BroadcastItem, type BroadcastPublishResult } from '../../lib/api'
import { useAuthStore } from '../../stores/auth'

type HeraldTab = 'publisher' | 'reposts' | 'aftercare' | 'herald'

/* ── Sub-views ────────────────────────────── */

function PublisherTab() {
  const [items, setItems] = useState<BroadcastItem[]>([])
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [audience, setAudience] = useState('all')
  const [imageUrl, setImageUrl] = useState('')
  const [facebook, setFacebook] = useState(true)
  const [instagram, setInstagram] = useState(false)
  const [loading, setLoading] = useState(false)
  const [publishingId, setPublishingId] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [lastResults, setLastResults] = useState<BroadcastPublishResult[]>([])

  const canCreate = title.trim().length > 2 && body.trim().length > 2

  useEffect(() => {
    let alive = true
    broadcasts.list(12)
      .then((data) => {
        if (alive) setItems(data.broadcasts)
      })
      .catch((err) => {
        if (alive) setStatus(err instanceof Error ? err.message : 'Failed to load broadcasts')
      })
    return () => { alive = false }
  }, [])

  const destinations = useMemo(() => {
    const selected: string[] = []
    if (facebook) selected.push('facebook')
    if (instagram) selected.push('instagram')
    return selected
  }, [facebook, instagram])

  const handleCreate = async () => {
    if (!canCreate) return
    setLoading(true)
    setStatus(null)
    try {
      const created = await broadcasts.create({
        title: title.trim(),
        body: body.trim(),
        broadcast_type: 'announcement',
        audience,
      })
      setItems((current) => [created, ...current])
      setTitle('')
      setBody('')
      setStatus('Saved to Herald feed.')
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Failed to save broadcast')
    } finally {
      setLoading(false)
    }
  }

  const handlePublish = async (item: BroadcastItem) => {
    if (destinations.length === 0) {
      setStatus('Choose at least one destination.')
      return
    }
    setPublishingId(item.id)
    setStatus(null)
    setLastResults([])
    try {
      const result = await broadcasts.publish(item.id, {
        destinations,
        image_url: imageUrl.trim() || undefined,
      })
      setLastResults(result.results)
      const failed = result.results.filter((r) => !r.ok)
      setStatus(failed.length ? 'Published with errors.' : 'Published.')
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Failed to publish broadcast')
    } finally {
      setPublishingId(null)
    }
  }

  return (
    <div className="herald-tab-body herald-publisher">
      <div className="herald-section-intro">
        <h2>Herald Publisher</h2>
        <p>Create shop announcements, save them to the portal feed, then release them to connected public channels.</p>
      </div>

      <div className="herald-publisher-grid">
        <section className="herald-publisher-panel">
          <h3>Draft Broadcast</h3>
          <label>
            <span>Title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Books are open this week" />
          </label>
          <label>
            <span>Body</span>
            <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write the announcement clients should see." rows={7} />
          </label>
          <label>
            <span>Audience</span>
            <select value={audience} onChange={(e) => setAudience(e.target.value)}>
              <option value="all">Everyone</option>
              <option value="collectors">Collectors</option>
              <option value="artists">Artists</option>
              <option value="admins">Admins</option>
            </select>
          </label>
          <button type="button" className="herald-primary-btn" disabled={!canCreate || loading} onClick={handleCreate}>
            {loading ? 'Saving...' : 'Save Broadcast'}
          </button>
        </section>

        <section className="herald-publisher-panel">
          <h3>Publish Targets</h3>
          <div className="herald-channel-list">
            <label><input type="checkbox" checked={facebook} onChange={(e) => setFacebook(e.target.checked)} /> Facebook Page</label>
            <label><input type="checkbox" checked={instagram} onChange={(e) => setInstagram(e.target.checked)} /> Instagram Feed</label>
          </div>
          <label>
            <span>Image URL for Instagram</span>
            <input value={imageUrl} onChange={(e) => setImageUrl(e.target.value)} placeholder="https://assets.ryozan.app/..." />
          </label>
          <p className="herald-publisher-note">Facebook can publish text only. Instagram requires a public image URL.</p>
          {status && <div className="herald-publish-status">{status}</div>}
          {lastResults.length > 0 && (
            <div className="herald-result-list">
              {lastResults.map((result) => (
                <div key={result.destination} className={`herald-result ${result.ok ? 'ok' : 'fail'}`}>
                  <strong>{result.destination}</strong>
                  <span>{result.ok ? `Posted ${result.post_id || ''}` : result.error}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="herald-broadcast-list">
        <div className="herald-list-head">
          <h3>Recent Broadcasts</h3>
          <span>{items.length} loaded</span>
        </div>
        {items.length === 0 ? (
          <p className="herald-publisher-note">No broadcasts yet.</p>
        ) : (
          items.map((item) => (
            <article key={item.id} className="herald-broadcast-card">
              <div>
                <strong>{item.title}</strong>
                <p>{item.body}</p>
                <small>{item.broadcast_type} | {item.audience} | {new Date(item.created_at).toLocaleString()}</small>
              </div>
              <button type="button" className="herald-secondary-btn" disabled={publishingId === item.id} onClick={() => handlePublish(item)}>
                {publishingId === item.id ? 'Publishing...' : 'Publish'}
              </button>
            </article>
          ))
        )}
      </section>
    </div>
  )
}

function AftercareTab() {
  return (
    <div className="herald-tab-body">
      <div className="herald-section-intro">
        <h2>Tattoo Aftercare</h2>
        <p>Proper aftercare is the difference between a healed tattoo that pops and one that fades. Follow these steps exactly.</p>
      </div>

      <div className="herald-aftercare-grid">
        <div className="herald-aftercare-card">
          <div className="herald-ac-num">01</div>
          <div className="herald-ac-content">
            <h4>First 2 Hours</h4>
            <p>Leave the bandage on. Your artist applied it for a reason. Don't peek — it traps plasma and keeps bacteria out.</p>
          </div>
        </div>
        <div className="herald-aftercare-card">
          <div className="herald-ac-num">02</div>
          <div className="herald-ac-content">
            <h4>Remove &amp; Wash</h4>
            <p>Gently peel off the bandage. Wash with lukewarm water and unscented antibacterial soap using your fingertips only. No washcloths or loofahs.</p>
          </div>
        </div>
        <div className="herald-aftercare-card">
          <div className="herald-ac-num">03</div>
          <div className="herald-ac-content">
            <h4>Pat Dry &amp; Moisturize</h4>
            <p>Pat dry with a clean paper towel. Apply a thin layer of unscented lotion (Lubriderm, Hustle Butter, or Aquaphor). Thin — not a thick glob.</p>
          </div>
        </div>
        <div className="herald-aftercare-card">
          <div className="herald-ac-num">04</div>
          <div className="herald-ac-content">
            <h4>Days 1–3</h4>
            <p>Wash 2–3 times daily. Moisturize lightly after each wash. It will ooze, and that is normal. Keep it clean.</p>
          </div>
        </div>
        <div className="herald-aftercare-card">
          <div className="herald-ac-num">05</div>
          <div className="herald-ac-content">
            <h4>Days 4–14</h4>
            <p>It will peel and itch. Do not pick or scratch. Let the flakes fall naturally. Moisturize when it feels dry or tight.</p>
          </div>
        </div>
        <div className="herald-aftercare-card">
          <div className="herald-ac-num">06</div>
          <div className="herald-ac-content">
            <h4>Weeks 2–4</h4>
            <p>The top layer is healed. Keep moisturizing. Avoid direct sun, soaking in pools/oceans, and tight clothing over the area for the full month.</p>
          </div>
        </div>
      </div>

      <div className="herald-avoid-box">
        <h4>Avoid These</h4>
        <div className="herald-avoid-list">
          <span>Sunlight / tanning beds</span>
          <span>Swimming / soaking</span>
          <span>Picking or scratching</span>
          <span>Tight clothing over tattoo</span>
          <span>Petroleum jelly (Vaseline)</span>
          <span>Scented lotions or soaps</span>
        </div>
      </div>

      <p className="herald-footnote">
        Questions? <Link to="/dashboard/courier" className="herald-link">Message us in Courier</Link> or reach out directly.
        Full heals take 4–6 weeks — be patient with your skin.
      </p>
    </div>
  )
}

function HeraldTab() {
  return (
    <div className="herald-tab-body">
      <div className="herald-section-intro">
        <h2>AI-Powered Area Research for Business Owners</h2>
        <p className="herald-kicker">How to use AI tools to understand your neighborhood, competitors, and customer base</p>
      </div>

      <div className="herald-blog">
        <section className="herald-blog-section">
          <h3>Why Local Research Matters More Than Ever</h3>
          <p>
            Before AI, researching your local market meant hours of manually browsing Google Maps, reading Yelp reviews one by one,
            and guessing at competitor pricing. AI tools have compressed that process from days into minutes. Here is how to actually use them.
          </p>
        </section>

        <section className="herald-blog-section">
          <h3>Step 1: Map Your Competitive Landscape</h3>
          <p>Start with a prompt like:</p>
          <div className="herald-prompt-block">
            <span className="herald-prompt-label">Prompt</span>
            <p>"List all tattoo studios within 5 miles of [your zip code]. For each, include: average price range, specialties, Google rating, and number of reviews."</p>
          </div>
          <p>
            Claude and ChatGPT can synthesize public data about local businesses. Follow up by asking it to identify gaps —
            studios that are not offering a style you specialize in, or neighborhoods that are underserved.
          </p>
        </section>

        <section className="herald-blog-section">
          <h3>Step 2: Analyze Customer Reviews at Scale</h3>
          <p>Copy 20–30 Google or Yelp reviews from your top competitors and paste them into an AI prompt like:</p>
          <div className="herald-prompt-block">
            <span className="herald-prompt-label">Prompt</span>
            <p>"Analyze these reviews. What do customers love most? What complaints come up repeatedly? What do they wish was different? What language do they use when describing their experience?"</p>
          </div>
          <p>
            This reveals the exact words your target customers use, what drives their decisions, and where your competitors are leaving satisfaction on the table.
          </p>
        </section>

        <section className="herald-blog-section">
          <h3>Step 3: Understand Your Neighborhood Demographics</h3>
          <p>
            Use AI to interpret publicly available census data and neighborhood reports. Paste in demographic stats from the U.S. Census Bureau or Data USA and ask:
          </p>
          <div className="herald-prompt-block">
            <span className="herald-prompt-label">Prompt</span>
            <p>"Based on these demographics for my neighborhood (median age 28, median household income $52k, 60% renters, large arts district presence), what services and price points would resonate most with this community?"</p>
          </div>
        </section>

        <section className="herald-blog-section">
          <h3>Step 4: Build Your ICP (Ideal Client Profile)</h3>
          <p>After gathering the research above, ask AI to synthesize it into a client profile:</p>
          <div className="herald-prompt-block">
            <span className="herald-prompt-label">Prompt</span>
            <p>"Using the competitive analysis, review patterns, and demographics I've given you, build 3 detailed client personas for my tattoo studio. Include name, age, income, what they value in a tattoo artist, and what would make them choose us over a competitor."</p>
          </div>
          <p>
            These personas inform your marketing copy, social content, booking flow, and even which artists to hire next.
          </p>
        </section>

        <section className="herald-blog-section">
          <h3>Step 5: Generate Content From the Research</h3>
          <p>
            The real leverage comes when you feed your research back into content creation. Your AI-built ICP becomes the audience for every Instagram caption, email campaign, and booking page you write. Instead of guessing what resonates — you know.
          </p>
          <p>
            The business owners winning with AI are not the ones using it for one-off tasks. They are the ones who have built a research loop: gather data → analyze with AI → create content → measure → repeat.
          </p>
        </section>

        <div className="herald-takeaway">
          <span className="herald-takeaway-label">Key Takeaway</span>
          <p>AI does not replace local intuition — it amplifies it. A 30-minute research session with Claude or ChatGPT gives you more actionable intelligence than a week of manual research used to. The studio owner who uses it wins the neighborhood before anyone else realizes the game changed.</p>
        </div>
      </div>
    </div>
  )
}

/* ── Main Component ───────────────────────── */

const TAB_CONFIG: { id: HeraldTab; label: string; icon: React.ReactNode }[] = [
  {
    id: 'publisher',
    label: 'Publisher',
    icon: (
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 11l18-8-8 18-2-8-8-2z"/><path d="M11 13l10-10"/>
      </svg>
    ),
  },
  {
    id: 'reposts',
    label: 'Reposts',
    icon: (
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>
      </svg>
    ),
  },
  {
    id: 'aftercare',
    label: 'Aftercare',
    icon: (
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
      </svg>
    ),
  },
  {
    id: 'herald',
    label: 'Herald',
    icon: (
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
      </svg>
    ),
  },
]

const operatorOnly = (id: HeraldTab) => id === 'publisher' || id === 'reposts'

export default function HeraldsPage() {
  const effectiveRole = useAuthStore((s) => s.effectiveRole)
  const canPublish = ['admin', 'founder'].includes(effectiveRole)
  const fallbackTab: HeraldTab = 'aftercare'
  const [tab, setTab] = useState<HeraldTab>(canPublish ? 'publisher' : fallbackTab)
  const activeTab: HeraldTab = canPublish || !operatorOnly(tab) ? tab : fallbackTab

  const tabs = useMemo(
    () => TAB_CONFIG.filter((item) => (!operatorOnly(item.id) || canPublish)),
    [canPublish],
  )

  return (
    <div className="herald-page">
      <div className="herald-header">
        <div className="herald-title-row">
          <h1 className="herald-title">Herald &amp; Guides</h1>
          <span className="herald-subtitle">References, instructions &amp; resources</span>
        </div>
        <div className="herald-tab-bar">
          {tabs.map(({ id, label, icon }) => (
            <button
              key={id}
              className={`herald-tab-btn${activeTab === id ? ' active' : ''}`}
              onClick={() => setTab(id)}
            >
              {icon}
              <span>{label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="herald-body">
        {activeTab === 'publisher' && <PublisherTab />}
        {activeTab === 'reposts'   && <HeraldRepostsTab />}
        {activeTab === 'aftercare' && <AftercareTab />}
        {activeTab === 'herald'    && <HeraldTab />}
      </div>
    </div>
  )
}
