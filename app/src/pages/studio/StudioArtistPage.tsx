/**
 * StudioArtistPage: /artist/:slug when PUBLIC_SITE_MODE=studio.
 *
 * Reads the artist from GET /artists/{slug}, as the original artist page did,
 * and shows the profile the artist keeps in the portal: name, title, styles,
 * bio, links and portfolio. The original's fallback roster, live calendar,
 * flash shop and placement preview are not part of this page; booking goes
 * through the app's booking entry.
 */
import { useEffect, useState } from 'react'
import { Helmet } from 'react-helmet-async'
import { Link, useParams } from 'react-router-dom'
import StudioSiteShell from '../../components/studio-site/StudioSiteShell'
import { useStudioSettings } from '../../hooks/useStudioSettings'
import { artists as artistsApi, type ArtistDetail, type StudioSettings } from '../../lib/api'
import { artistLinks, artistPortfolio, artistSpecialties, paragraphs, studioName } from '../../lib/studio-site'

export type ArtistLoad = { status: 'loading' } | { status: 'ready'; artist: ArtistDetail } | { status: 'missing' }

export function StudioArtistView({ settings, load }: { settings: StudioSettings; load: ArtistLoad }) {
  const name = studioName(settings)

  if (load.status !== 'ready') {
    return (
      <StudioSiteShell settings={settings} showArtists>
        <section className="calendar-purpose" aria-live="polite">
          <div className="container calendar-purpose-grid">
            <div>
              <span className="calendar-purpose-eyebrow">Artist</span>
              <h1 className="booking-cta-heading serif">{load.status === 'loading' ? 'Loading' : 'Artist not found'}</h1>
            </div>
            {load.status === 'missing' && (
              <div className="calendar-purpose-copy">
                <p>There is no artist at this address.</p>
                <Link to="/#artists" className="calendar-purpose-link">See the artists <span aria-hidden="true">&rarr;</span></Link>
              </div>
            )}
          </div>
        </section>
      </StudioSiteShell>
    )
  }

  const { artist } = load
  const styles = artistSpecialties(artist)
  const bio = paragraphs(artist.bio)
  const links = artistLinks(artist)
  const portfolio = artistPortfolio(artist)

  return (
    <StudioSiteShell settings={settings} showArtists>
      <Helmet>
        <title>{`${artist.name} | ${name}`}</title>
        {bio[0] && <meta name="description" content={bio[0]} />}
      </Helmet>

      <section className="calendar-purpose" aria-labelledby="artist-name">
        <div className="container calendar-purpose-grid">
          <div>
            <span className="calendar-purpose-eyebrow">{artist.title || 'Artist'}</span>
            <h1 id="artist-name" className="booking-cta-heading serif">{artist.name}</h1>
            {styles && <p className="subheading">{styles}</p>}
          </div>
          <div className="calendar-purpose-copy">
            {bio.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
            <div className="calendar-purpose-actions">
              <Link to="/dashboard/book" className="btn btn-primary">Book with {artist.name}</Link>
              {links.map((link) => (
                <a key={link.url} className="calendar-purpose-link" href={link.url} target="_blank" rel="noopener noreferrer">{link.label}</a>
              ))}
            </div>
          </div>
        </div>
      </section>

      {portfolio.length > 0 && (
        <section aria-labelledby="artist-portfolio-title">
          <div className="container">
            <div className="section-header">
              <h2 id="artist-portfolio-title" className="serif">Portfolio</h2>
            </div>
            <div className="portfolio-grid">
              {portfolio.map((url, index) => (
                <div key={url} className="portfolio-box">
                  <img src={url} alt={`${artist.name}, portfolio piece ${index + 1}`} loading="lazy" />
                </div>
              ))}
            </div>
          </div>
        </section>
      )}
    </StudioSiteShell>
  )
}

export default function StudioArtistPage() {
  const { slug = '' } = useParams()
  const { settings } = useStudioSettings()
  // The answer is kept with the slug it is for, so a new slug reads as loading.
  const [result, setResult] = useState<{ slug: string; load: ArtistLoad } | null>(null)

  useEffect(() => {
    let active = true
    artistsApi.get(encodeURIComponent(slug))
      .then((artist) => { if (active) setResult({ slug, load: { status: 'ready', artist } }) })
      .catch(() => { if (active) setResult({ slug, load: { status: 'missing' } }) })
    return () => { active = false }
  }, [slug])

  const load: ArtistLoad = result?.slug === slug ? result.load : { status: 'loading' }
  return <StudioArtistView settings={settings} load={load} />
}