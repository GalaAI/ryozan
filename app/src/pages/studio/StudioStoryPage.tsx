/**
 * StudioStoryPage: /our-story when PUBLIC_SITE_MODE=studio.
 *
 * The original "Our Story" page told one studio's history in hardcoded copy
 * with its photos, timeline and press. This one prints STUDIO_STORY, one
 * paragraph per blank-line block, and the studio's contact settings.
 */
import { Helmet } from 'react-helmet-async'
import { Link } from 'react-router-dom'
import StudioSiteShell from '../../components/studio-site/StudioSiteShell'
import { useStudioSettings } from '../../hooks/useStudioSettings'
import type { StudioSettings } from '../../lib/api'
import { studioName } from '../../lib/studio-site'

export function StudioStoryView({ settings }: { settings: StudioSettings }) {
  const name = studioName(settings)
  const story = settings.site.story_paragraphs

  return (
    <StudioSiteShell settings={settings}>
      <Helmet>
        <title>{`Our story | ${name}`}</title>
        {story[0] && <meta name="description" content={story[0]} />}
      </Helmet>

      <section className="calendar-purpose" aria-labelledby="studio-story-title">
        <div className="container calendar-purpose-grid">
          <div>
            <span className="calendar-purpose-eyebrow">Our story</span>
            <h1 id="studio-story-title" className="booking-cta-heading serif">{name}</h1>
          </div>
          <div className="calendar-purpose-copy">
            {story.length > 0
              ? story.map((paragraph, index) => <p key={index}>{paragraph}</p>)
              : <p>This studio has not published its story yet.</p>}
            <div className="calendar-purpose-actions">
              <Link to="/book" className="btn btn-primary">Book a session</Link>
              <Link to="/" className="calendar-purpose-link">Back to the studio <span aria-hidden="true">&rarr;</span></Link>
            </div>
          </div>
        </div>
      </section>
    </StudioSiteShell>
  )
}

export default function StudioStoryPage() {
  const { settings } = useStudioSettings()
  return <StudioStoryView settings={settings} />
}
