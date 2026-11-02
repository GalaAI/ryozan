/**
 * StudioSiteShell: navigation and footer of the studio website
 * (PUBLIC_SITE_MODE=studio), the counterpart of AppProductShell.
 *
 * Markup and class names are the original studio site's (v2-redesign.css,
 * loaded by PublicLayout): #navbar, the hamburger and mobile menu, and the
 * calendar-purpose band for the footer. Every name, contact line and link
 * comes from the studio settings; blank values leave their line out.
 */
import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { StudioSettings } from '../../lib/api'
import { hasContact, studioContact, studioName } from '../../lib/studio-site'

type StudioSiteShellProps = {
  settings: StudioSettings
  children: ReactNode
  /** The home page links its own sections with #anchors; other pages go through /. */
  onHome?: boolean
  /** Show the Artists link; the home page knows whether it lists any. */
  showArtists?: boolean
}

const BOOK_PATH = '/dashboard/book'

export default function StudioSiteShell({ settings, children, onHome = false, showArtists = false }: StudioSiteShellProps) {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const name = studioName(settings)
  const contact = studioContact(settings)
  const anchor = (id: string) => (onHome ? `#${id}` : `/#${id}`)

  // The app scrolls #root, not the window, so listen in the capture phase and
  // read whichever element scrolled.
  useEffect(() => {
    const onScroll = (event: Event) => {
      const target = event.target
      setScrolled((target instanceof Element ? target.scrollTop : window.scrollY) > 50)
    }
    document.addEventListener('scroll', onScroll, { capture: true, passive: true })
    return () => document.removeEventListener('scroll', onScroll, { capture: true })
  }, [])

  // The mobile menu locks page scroll while open, and always releases it.
  useEffect(() => {
    document.body.style.overflow = menuOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [menuOpen])

  const closeMenu = useCallback(() => setMenuOpen(false), [])

  const links: { label: string; href: string; route?: boolean }[] = [
    ...(settings.site.story_paragraphs.length ? [{ label: 'Our story', href: '/our-story', route: true }] : []),
    ...(showArtists ? [{ label: 'Artists', href: anchor('artists') }] : []),
    ...(settings.site.gallery_image_urls.length ? [{ label: 'The studio', href: anchor('gallery') }] : []),
    ...(hasContact(contact) ? [{ label: 'Visit', href: '#visit' }] : []),
  ]

  const renderLink = (link: (typeof links)[number], onClick?: () => void) => (
    link.route
      ? <Link to={link.href} onClick={onClick}>{link.label}</Link>
      : <a href={link.href} onClick={onClick}>{link.label}</a>
  )

  return (
    <>
      <nav id="navbar" className={scrolled ? 'scrolled' : ''} aria-label={`${name} navigation`}>
        <div className="container nav-inner">
          <Link to="/" className="serif" aria-label={`${name} home`}>{name}</Link>

          <ul className="nav-links">
            {links.map((link) => <li key={link.label}>{renderLink(link)}</li>)}
          </ul>

          <div className="nav-cta-group">
            <Link to="/portal" className="nav-utility-link">Sign in</Link>
            <Link to={BOOK_PATH} className="btn btn-outline">Book</Link>
            <button
              type="button"
              className={`hamburger ${menuOpen ? 'active' : ''}`}
              onClick={() => setMenuOpen((open) => !open)}
              aria-label="Open menu"
              aria-expanded={menuOpen}
              aria-controls="studio-mobile-menu"
            >
              <span className="hamburger-line" />
              <span className="hamburger-line" />
              <span className="hamburger-line" />
            </button>
          </div>
        </div>
      </nav>

      <div className={`mobile-menu-overlay ${menuOpen ? 'open' : ''}`} onClick={closeMenu} aria-hidden="true" />
      <div id="studio-mobile-menu" className={`mobile-menu ${menuOpen ? 'open' : ''}`} aria-hidden={!menuOpen}>
        <div className="mobile-menu-header">
          <span className="mono-text">Menu</span>
          <button type="button" className="mobile-menu-close" onClick={closeMenu} aria-label="Close menu">&times;</button>
        </div>
        <ul className="mobile-menu-links">
          {links.map((link) => <li key={link.label}>{renderLink(link, closeMenu)}</li>)}
        </ul>
        <div className="mobile-menu-cta">
          <Link to={BOOK_PATH} className="btn btn-primary" onClick={closeMenu}>Book a session</Link>
          <Link to="/portal" className="mobile-menu-account" onClick={closeMenu}>Sign in</Link>
        </div>
      </div>

      <main>{children}</main>

      <footer id="visit" className="calendar-purpose">
        <div className="container calendar-purpose-grid">
          <div>
            <span className="calendar-purpose-eyebrow">{hasContact(contact) ? 'Visit' : name}</span>
            {hasContact(contact) && (
              <div className="calendar-purpose-copy">
                <p>{name}</p>
                {contact.address && <p>{contact.address}</p>}
                {contact.hours.map((line) => <p key={line}>{line}</p>)}
              </div>
            )}
          </div>
          <div className="calendar-purpose-copy">
            {(contact.phone || contact.email) && (
              <p>
                {contact.phone && (contact.phoneHref ? <a href={contact.phoneHref}>{contact.phone}</a> : contact.phone)}
                {contact.phone && contact.email && <br />}
                {contact.email && <a href={`mailto:${contact.email}`}>{contact.email}</a>}
              </p>
            )}
            <div className="calendar-purpose-actions">
              {settings.site.social_links.map((link) => (
                <a key={link.url} className="calendar-purpose-link" href={link.url} target="_blank" rel="noopener noreferrer">{link.label}</a>
              ))}
              <Link className="calendar-purpose-link" to={BOOK_PATH}>Book a session</Link>
              <Link className="calendar-purpose-link" to="/privacy">Privacy</Link>
              <Link className="calendar-purpose-link" to="/terms">Terms</Link>
            </div>
            <p>&copy; {new Date().getFullYear()} {name}</p>
          </div>
        </div>
      </footer>
    </>
  )
}
