import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <main className="ryozan-error-boundary ryozan-error-boundary--not-found">
      <section className="ryozan-error-boundary__panel">
        <h1>404</h1>
        <p>This page doesn't exist. Maybe it got tattooed over.</p>
        <div className="ryozan-error-boundary__actions">
        <Link
          to="/"
          className="ryozan-error-boundary__action ryozan-error-boundary__action--primary"
        >
          Back Home
        </Link>
        <Link
          to="/dashboard/book"
          className="ryozan-error-boundary__action ryozan-error-boundary__action--secondary"
        >
          Book a Session
        </Link>
        </div>
      </section>
    </main>
  )
}
