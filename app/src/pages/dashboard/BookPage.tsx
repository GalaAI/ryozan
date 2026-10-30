/**
 * BookPage: where a client starts a booking.
 *
 * Lists the studio's artists as the API returns them. A booking request is made
 * with Saishin in the portal's Booking tab, which reads and writes the client's
 * own booking records; this page invents no artists, slots or confirmations.
 */

import { useEffect, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/auth'
import { artists as artistsApi, type ArtistSummary } from '../../lib/api'

type Roster = { status: 'loading' } | { status: 'ready'; artists: ArtistSummary[] } | { status: 'failed' }

export default function BookPage() {
  const viewAs = useAuthStore((s) => s.viewAs)
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()
  const isCollector = (user?.role || 'collector') === 'collector' || viewAs === 'collector'
  const [roster, setRoster] = useState<Roster>({ status: 'loading' })

  useEffect(() => {
    let active = true
    artistsApi.list()
      .then((artists) => { if (active) setRoster({ status: 'ready', artists }) })
      .catch(() => { if (active) setRoster({ status: 'failed' }) })
    return () => { active = false }
  }, [])

  // Founders/artists should not see the booking flow
  if (!isCollector) return <Navigate to="/dashboard" replace />

  const askSaishin = () => navigate('/dashboard?portal=books')

  return (
    <div className="book-page">
      <div className="book-flow">
        <h2>Book a session</h2>
        {roster.status === 'loading' && <div className="book-state">Loading artists...</div>}
        {roster.status === 'failed' && <div className="book-state book-state--error">The artist list could not be loaded. Try again in a moment.</div>}
        {roster.status === 'ready' && roster.artists.length === 0 && (
          <div className="book-state">
            This studio has no artists on Ryozan yet, so there is no one to book. When a studio adds its artists, clients choose one here and send a request the artist confirms.
          </div>
        )}
        {roster.status === 'ready' && roster.artists.length > 0 && (
          <ul>
            {roster.artists.map((artist) => (
              <li key={artist.id}>
                <strong>{artist.name}</strong>
                {artist.specialty?.length ? <span> · {artist.specialty.join(', ')}</span> : null}
              </li>
            ))}
          </ul>
        )}
        <p>Saishin handles booking requests and scheduling questions in the Booking tab, using only your own booking records.</p>
        <button type="button" className="book-flow-next" onClick={askSaishin}>Ask Saishin</button>
      </div>
    </div>
  )
}
