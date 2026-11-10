/**
 * StudioHomePage: `/` when PUBLIC_SITE_MODE=studio.
 *
 * Adapted from the original studio home page: hero, story teaser, the artist
 * roster, studio gallery and the booking call to action, on the same
 * v2-redesign.css classes. The original's hardcoded copy, photos, reviews,
 * FAQ and structured data are gone; every word and image here is a studio
 * setting or a row from GET /artists, and a section without data is not drawn.
 */
import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { Helmet } from 'react-helmet-async'
import { Link } from 'react-router-dom'
import StudioSiteShell from '../../components/studio-site/StudioSiteShell'
import { useStudioSettings } from '../../hooks/useStudioSettings'
import { artists as artistsApi, type ArtistSummary, type StudioSettings } from '../../lib/api'
import { artistAvatar, artistSpecialties, heroImageStyle, listedArtists, studioContact, studioName } from '../../lib/studio-site'

export function StudioHomeView({ settings, artists }: { settings: StudioSettings; artists: ArtistSummary[] }) {
  const name = studioName(settings)
  const { tagline, story_paragraphs: story, hero_image_url: hero, gallery_image_urls: gallery } = settings.site
  const roster = listedArtists(artists)
  const { email } = studioContact(settings)

  return (
    <StudioSiteShell settings={settings} onHome showArtists={roster.length > 0}>
      <Helmet>
        <title>{name}</title>
        {tagline && <meta name="description" content={tagline} />}
        <meta property="og:title" content={name} />
        {tagline && <meta property="og:description" content={tagline} />}
        {hero && <meta property="og:image" content={hero} />}
      </Helmet>

      <section className="hero" style={heroImageStyle(hero) as CSSProperties | undefined}>
        <div className="container hero-content">
          <h1 className="booking-cta-heading serif">{name}</h1>
          {tagline && <p className="subheading">{tagline}</p>}
          <Link to="/book" className="btn btn-primary">Book a session</Link>
        </div>
      </section>

      {story.length > 0 && (
        <section id="about" className="calendar-purpose" aria-labelledby="studio-story-title">
          <div className="container calendar-purpose-grid">
            <div>
              <span className="calendar-purpose-eyebrow">Our story</span>
              <h2 id="studio-story-title">{name}</h2>
            </div>
            <div className="calendar-purpose-copy">
              <p>{story[0]}</p>
              <Link to="/our-story" className="calendar-purpose-link">Read our story <span aria-hidden="true">&rarr;</span></Link>
            </div>
          </div>
        </section>
      )}

      {roster.length > 0 && (
        <section id="artists" aria-labelledby="studio-artists-title">
          <div className="container">
            <div className="section-header">
              <h2 id="studio-artists-title" className="serif">The artists</h2>
            </div>
            <div className="portfolio-grid">
              {roster.map((artist) => {
                const avatar = artistAvatar(artist)
                const styles = artistSpecialties(artist)
                return (
                  <Link key={artist.id} to={`/artist/${encodeURIComponent(artist.slug)}`} aria-label={`${artist.name}, artist profile`}>
                    <div className="portfolio-box">
                      {avatar && <img src={avatar} alt={artist.name} loading="lazy" />}
                    </div>
                    <h3>{artist.name}</h3>
                    {styles && <p className="subheading">{styles}</p>}
                  </Link>
                )
              })}
            </div>
          </div>
        </section>
      )}

      {gallery.length > 0 && (
        <section id="gallery" aria-labelledby="studio-gallery-title">
          <div className="container shop-gallery">
            <div className="section-header">
              <h2 id="studio-gallery-title" className="serif">The studio</h2>
            </div>
            <div className="shop-masonry-grid">
              {gallery.map((url, index) => (
                <div key={url} className="shop-masonry-item">
                  <img src={url} alt={`${name}, photo ${index + 1}`} loading="lazy" />
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      <section id="booking" aria-labelledby="studio-booking-title">
        <div className="container">
          <div className="booking-cta">
            <span className="booking-cta-eyebrow">Bookings</span>
            <h2 id="studio-booking-title" className="booking-cta-heading serif">Book a session</h2>
            <p className="booking-cta-body">Choose an artist, send your idea and manage the appointment from one account.</p>
            <Link to="/book" className="btn btn-primary booking-cta-btn">Start a booking</Link>
            <p className="booking-cta-aside">
              A question first? <Link to="/ask">Ask the shop</Link>
              {email && <> or write to <a href={`mailto:${email}`}>{email}</a></>}
            </p>
          </div>
        </div>
      </section>
    </StudioSiteShell>
  )
}

export default function StudioHomePage() {
  const { settings } = useStudioSettings()
  const [artists, setArtists] = useState<ArtistSummary[]>([])

  useEffect(() => {
    let active = true
    artistsApi.list()
      .then((list) => { if (active) setArtists(list) })
      .catch(() => { if (active) setArtists([]) })
    return () => { active = false }
  }, [])

  return <StudioHomeView settings={settings} artists={artists} />
}
