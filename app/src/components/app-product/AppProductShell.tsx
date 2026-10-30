import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { BRAND } from '../../lib/brand'
import '../../styles/app-product.css'

type AppProductShellProps = {
  children: ReactNode
  policyPage?: boolean
}

export default function AppProductShell({ children, policyPage = false }: AppProductShellProps) {
  return (
    <div className={`app-product${policyPage ? ' app-product--policy' : ''}`}>
      <header className="app-product__header">
        <a className="app-product__brand" href="/" aria-label={`${BRAND.product} home`}>
          <img src={BRAND.mark} alt="" />
          <span>
            <strong>{BRAND.product}</strong>
            <small>Studio operating system</small>
          </span>
        </a>
        <nav aria-label="App navigation">
          <a href="/#how-it-works">How it works</a>
          <Link to="/privacy">Privacy</Link>
          <Link className="app-product__nav-cta" to="/portal">Open portal</Link>
        </nav>
      </header>

      <main>{children}</main>

      <footer className="app-product__footer">
        <div>
          <img src={BRAND.mark} alt="" />
          <p><strong>{BRAND.product}</strong><br />{BRAND.tagline}.</p>
        </div>
        <nav aria-label="Legal">
          <Link to="/privacy">Privacy Policy</Link>
          <Link to="/terms">Terms of Service</Link>
        </nav>
        <small>© {new Date().getFullYear()} {BRAND.studio}. All rights reserved.</small>
      </footer>
    </div>
  )
}
