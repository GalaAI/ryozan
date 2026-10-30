import { Component, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

interface Props { children: ReactNode }
interface State { hasError: boolean; error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <main className="bttco-error-boundary" role="alert">
        <section className="bttco-error-boundary__panel">
          <h1>Something went wrong</h1>
          <p>We hit an unexpected error. Try refreshing the page.</p>
          <div className="bttco-error-boundary__actions">
          <button
            type="button"
            className="bttco-error-boundary__action bttco-error-boundary__action--primary"
            onClick={() => window.location.reload()}
          >
            Refresh Page
          </button>
          <Link
            to="/"
            className="bttco-error-boundary__action bttco-error-boundary__action--secondary"
            onClick={() => this.setState({ hasError: false, error: null })}
          >
            Back Home
          </Link>
          </div>
        </section>
      </main>
    )
  }
}
