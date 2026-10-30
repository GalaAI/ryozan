/**
 * Auth store: Zustand-powered global auth state.
 *
 * Manages: user object, token, login/logout/register flows.
 * Token always persists to localStorage so sessions survive page reloads.
 *
 * "Remember me" saves user identity (name, email, avatar) to localStorage
 * so the login page can show a personalized welcome-back screen even after
 * the JWT expires. Cleared only by explicit "Not you?" action.
 *
 * "View As" lets the founder preview the app as any role (collector, artist,
 * admin, etc.) without changing auth. API calls still use the real token.
 * Only the UI rendering context changes.
 */

import { create } from 'zustand'
import { auth as authApi, getToken, onSessionExpired, setToken, type User, type ApiError } from '../lib/api'

// ── View As types ──

export type ViewAsMode =
  | null              // Normal: render as real role
  | 'collector'       // Preview collector portal
  | 'assistant'       // Preview assistant dashboard
  | 'artist'          // Preview artist dashboard
  | 'admin'           // Preview admin view

// ── Remembered user (persists across sessions) ──

const REMEMBERED_KEY = 'ryozan_remembered'

export interface RememberedUser {
  name: string
  email: string
  avatar_url: string | null
}

export function getRememberedUser(): RememberedUser | null {
  try {
    const raw = localStorage.getItem(REMEMBERED_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function saveRememberedUser(user: User) {
  localStorage.setItem(REMEMBERED_KEY, JSON.stringify({
    name: user.name,
    email: user.email,
    avatar_url: user.avatar_url,
  }))
}

export function clearRememberedUser() {
  localStorage.removeItem(REMEMBERED_KEY)
}

// ── Store ──

interface AuthState {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  portalIntermissionActive: boolean
  /**
   * True while the loader's dark field is fading away over a freshly mounted
   * portal. Lives in the store, not in a route, because the overlay has to
   * outlive the page that started it: LoginGate unmounts the instant we
   * navigate, and the reveal has to keep painting on top of the dashboard.
   */
  portalRevealing: boolean
  error: string | null

  // View As: founder-only UI perspective switch
  viewAs: ViewAsMode
  /** The role the UI should render for. Uses viewAs override if active, else real role. */
  effectiveRole: User['role']

  // Dev Mode: founder-only persistent dev toolbar
  devMode: boolean

  // GDT tab: which panel is open in the dev toolbar (null = collapsed)
  gdtTab: 'role' | 'api' | 'build' | 'design' | null
  setGdtTab: (tab: 'role' | 'api' | 'build' | 'design' | null) => void

  // Actions
  login: (email: string, password: string) => Promise<void>
  register: (data: {
    email: string
    password: string
    name: string
    reasons?: string[]
    marketing_opt_in?: boolean
    signup_code?: string
  }) => Promise<string>
  logout: () => void
  fetchMe: () => Promise<void>
  loginWithToken: (token: string) => Promise<void>
  clearError: () => void
  completePortalIntermission: () => void
  beginPortalReveal: () => void
  endPortalReveal: () => void
  setViewAs: (mode: ViewAsMode) => void
  toggleDevMode: () => void
}

function deriveEffectiveRole(user: User | null, viewAs: ViewAsMode): User['role'] {
  if (!user) return 'collector'
  // Only founder can use View As
  if (viewAs && user.role === 'founder') return viewAs
  return user.role
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isLoading: !!localStorage.getItem('ryozan_token'),
  isAuthenticated: false,
  portalIntermissionActive: false,
  portalRevealing: false,
  error: null,
  viewAs: null,
  effectiveRole: 'collector',
  devMode: false,
  gdtTab: null,

  login: async (email, password) => {
    set({ isLoading: true, error: null })
    try {
      const { access_token } = await authApi.login(email, password)
      setToken(access_token)
      const user = await authApi.me()
      saveRememberedUser(user)
      set({
        user,
        isAuthenticated: true,
        // NOT portalIntermissionActive. LoginGate owns the login-entry
        // ceremony and mounts its own loader; raising this here rendered
        // PortalIntermission on top of it, so two loaders played at once.
        // Google (loginWithToken) and register still use it: they have no gate.
        isLoading: false,
        effectiveRole: deriveEffectiveRole(user, get().viewAs),
      })
    } catch (err) {
      const message = (err as ApiError).message || 'Login failed'
      set({ error: message, isLoading: false })
      throw err
    }
  },

  register: async (data) => {
    set({ isLoading: true, error: null })
    try {
      const { message, access_token } = await authApi.register(data)
      set({ isLoading: false })
      // Instant sign-up (a demo install) returns a session for the new account.
      if (access_token) await get().loginWithToken(access_token)
      return message
    } catch (err) {
      const message = (err as ApiError).message || 'Registration failed'
      set({ error: message, isLoading: false })
      throw err
    }
  },

  logout: () => {
    // Retire the session server side too. The local token is cleared at once
    // so sign-out never waits on the network; a failed call only means the
    // token expires on its own schedule.
    if (getToken()) void authApi.logout().catch(() => undefined)
    setToken(null)
    set({
      user: null,
      isAuthenticated: false,
      portalIntermissionActive: false,
      portalRevealing: false,
      error: null,
      viewAs: null,
      effectiveRole: 'collector',
    })
  },

  fetchMe: async () => {
    set({ isLoading: true })
    try {
      const user = await authApi.me()
      saveRememberedUser(user)
      set({
        user,
        isAuthenticated: true,
        isLoading: false,
        effectiveRole: deriveEffectiveRole(user, get().viewAs),
      })
    } catch (error) {
      // Only a rejected token ends the session. A rate limit, network drop or
      // server error keeps the token and the signed-in user for the next check.
      const status = (error as ApiError | undefined)?.status
      if (status === 401 || status === 403) {
        setToken(null)
        set({ user: null, isAuthenticated: false, isLoading: false })
      } else {
        set({ isLoading: false })
      }
    }
  },

  loginWithToken: async (token: string) => {
    setToken(token)
    set({ isLoading: true })
    try {
      const user = await authApi.me()
      saveRememberedUser(user)
      set({
        user,
        isAuthenticated: true,
        portalIntermissionActive: true,
        isLoading: false,
        effectiveRole: deriveEffectiveRole(user, get().viewAs),
      })
    } catch {
      setToken(null)
      set({ user: null, isAuthenticated: false, isLoading: false })
    }
  },

  clearError: () => set({ error: null }),

  completePortalIntermission: () => set({ portalIntermissionActive: false }),

  beginPortalReveal: () => set({ portalRevealing: true }),
  endPortalReveal: () => set({ portalRevealing: false }),

  setViewAs: (mode) => {
    const { user } = get()
    // Only founder can use View As
    if (user?.role !== 'founder') return
    set({
      viewAs: mode,
      effectiveRole: deriveEffectiveRole(user, mode),
    })
  },

  toggleDevMode: () => {
    const { user, devMode } = get()
    if (user?.role !== 'founder' && user?.role !== 'admin') return
    // Turning off also clears viewAs and gdtTab
    set({ devMode: !devMode, ...(!devMode ? {} : { viewAs: null, gdtTab: null, effectiveRole: deriveEffectiveRole(user, null) }) })
  },

  setGdtTab: (tab) => set({ gdtTab: tab }),
}))

// A 401 on any authenticated request means the server no longer accepts this
// session. Drop the user so ProtectedRoute redirects to /portal with returnTo.
onSessionExpired(() => {
  useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: false, viewAs: null, effectiveRole: 'collector' })
})

// Dev-only: expose the store on window for local preview/inspection.
// Never present in production builds (tree-shaken out via import.meta.env.DEV).
if (import.meta.env.DEV) {
  ;(window as unknown as { useAuthStore?: typeof useAuthStore }).useAuthStore = useAuthStore
}
