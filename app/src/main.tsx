import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { HelmetProvider } from 'react-helmet-async'
import { jam } from '@jam.dev/sdk'
import ErrorBoundary from './components/ErrorBoundary'
import { ToastProvider } from './components/Toast'
import { useAuthStore } from './stores/auth'
import './index.css'
import '@bttco/ui/fonts.css'
import '@bttco/ui/tokens.css'
import '@bttco/ui/scene-compat.css'
import App from './App'

function getJamSurface(pathname: string): 'dashboard' | 'portal' | 'public' {
  if (pathname === '/dashboard' || pathname.startsWith('/dashboard/')) return 'dashboard'
  if (pathname === '/portal' || pathname.startsWith('/portal/')) return 'portal'
  return 'public'
}

jam.metadata(() => {
  const { user, isAuthenticated, effectiveRole } = useAuthStore.getState()

  return {
    workspaceId: 'blind-tiger-tattoo-co',
    userId: user?.id ?? null,
    authenticated: isAuthenticated,
    role: user?.role ?? 'anonymous',
    effectiveRole: isAuthenticated ? effectiveRole : 'anonymous',
    surface: getJamSurface(window.location.pathname),
  }
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HelmetProvider>
      <ErrorBoundary>
        <ToastProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </ToastProvider>
      </ErrorBoundary>
    </HelmetProvider>
  </StrictMode>,
)
