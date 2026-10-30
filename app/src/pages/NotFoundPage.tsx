import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <main className="bttco-error-boundary bttco-error-boundary--not-found">
      <section className="bttco-error-boundary__panel">
        <h1>404</h1>
        <p>This page doesn't exist. Maybe it got tattooed over.</p>
        <div className="bttco-error-boundary__actions">
        <Link
          to="/"
          className="bttco-error-boundary__action bttco-error-boundary__action--primary"
        >
          Back Home
        </Link>
        <Link
          to="/dashboard/book"
          className="bttco-error-boundary__action bttco-error-boundary__action--secondary"
        >
          Book a Session
        </Link>
        </div>
      </section>
    </main>
  )
}
