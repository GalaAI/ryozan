/**
 * API client for the Ryozan FastAPI backend (local dev port 18810).
 *
 * - Token stored in memory by default
 * - "Stay logged in" persists token to localStorage
 * - All methods return typed responses
 * - Vite proxy handles /api -> 127.0.0.1:18810 in dev (VITE_API_TARGET overrides)
 */

export const API_BASE = (import.meta.env.VITE_API_URL ?? '') + '/api/v1'
export const HEALTH_URL = (import.meta.env.VITE_API_URL ?? '') + '/health'   // root-level, not /api/v1/health
export const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''
const STORAGE_KEY = 'ryozan_token'
const DEFAULT_REQUEST_TIMEOUT_MS = 20000

let _token: string | null = null

/**
 * Set the auth token. Always persists to localStorage so sessions
 * survive page reloads.
 */
export function setToken(token: string | null) {
  _token = token
  if (token) {
    localStorage.setItem(STORAGE_KEY, token)
  } else {
    localStorage.removeItem(STORAGE_KEY)
  }
}

export function getToken(): string | null {
  if (!_token) {
    _token = localStorage.getItem(STORAGE_KEY)
  }
  return _token
}

const sessionExpiredListeners = new Set<() => void>()

/**
 * Called when the API rejects the stored token (revoked, expired or signed out
 * elsewhere). The auth store subscribes and clears the user, so ProtectedRoute
 * sends the person to /portal with returnTo instead of leaving cached data up.
 */
export function onSessionExpired(listener: () => void): () => void {
  sessionExpiredListeners.add(listener)
  return () => { sessionExpiredListeners.delete(listener) }
}

function expireSession(rejectedToken: string) {
  // A newer sign-in may have replaced the token while this request was in flight.
  if (getToken() !== rejectedToken) return
  setToken(null)
  sessionExpiredListeners.forEach(listener => listener())
}

/** Hydrate token from localStorage on app startup */
export function hydrateToken(): boolean {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored) {
    _token = stored
    return true
  }
  return false
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  }

  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const timeout = AbortSignal.timeout(DEFAULT_REQUEST_TIMEOUT_MS)
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      signal,
    })
    if (!res.ok) {
      let error: { detail?: unknown } = {}
      try {
        error = await res.json()
      } catch (err) {
        if (!(err instanceof SyntaxError)) throw err
      }
      // A wrong password on sign-in is a 401 too; only a rejected session token expires the session.
      if (res.status === 401 && token && !path.startsWith('/auth/login') && !path.startsWith('/auth/google')) expireSession(token)
      throw new ApiError(
        res.status,
        parseDetail(error?.detail, res.statusText || 'Request failed'),
        parseDetailCode(error?.detail),
        parseDetailRetryable(error?.detail),
      )
    }

    if (res.status === 204) return undefined as T
    // Await the body inside this boundary: fetch resolves before JSON arrives.
    return await res.json()
  } catch (err) {
    throw normalizeRequestError(err, signal, options.method)
  }
}

function normalizeRequestError(err: unknown, signal: AbortSignal, method = 'GET'): ApiError {
  if (err instanceof ApiError) return err
  const name = signal.aborted ? signal.reason?.name : err instanceof Error ? err.name : undefined
  const writeNotice = ['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())
    ? '' : ' The change may have reached the server. Check its status before submitting again.'
  if (name === 'TimeoutError') {
    return new ApiError(408, `Request timed out.${writeNotice || ' Check the connection and try again.'}`, 'request_timeout')
  }
  if (signal.aborted || name === 'AbortError') {
    return new ApiError(0, `Request was cancelled.${writeNotice}`, 'request_cancelled')
  }
  if (err instanceof SyntaxError) {
    return new ApiError(0, `The server returned an invalid response.${writeNotice}`, 'invalid_response')
  }
  return new ApiError(0, `Unable to complete the connection to the server.${writeNotice || ' Check the connection and try again.'}`, 'network_error')
}

export class ApiError extends Error {
  status: number
  code: string | null
  /** From a structured {code, message, retryable} detail; null when the server did not say. */
  retryable: boolean | null
  constructor(status: number, message: string, code: string | null = null, retryable: boolean | null = null) {
    super(message)
    this.status = status
    this.code = code
    this.retryable = retryable
    this.name = 'ApiError'
  }
}

/**
 * Normalize FastAPI `detail` field to a plain string.
 * - Plain string  → returned as-is
 * - Array (422 validation errors) → joined human-readable message
 * - {code, message} → returns message (machine code read separately via parseDetailCode)
 * - Object / other → JSON stringified
 */
function parseDetail(detail: unknown, fallback: string): string {
  if (!detail) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI 422: [{loc, msg, type}, ...]
    return detail.map((e: Record<string, unknown>) => (e?.msg as string) ?? JSON.stringify(e)).join('; ')
  }
  if (typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string') {
    // Structured {code, message} errors (e.g. flash upload validation)
    return (detail as { message: string }).message
  }
  return JSON.stringify(detail)
}

/** Extract the machine-readable code from a structured {code, message} detail, if present. */
function parseDetailCode(detail: unknown): string | null {
  if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).code === 'string') {
    return (detail as { code: string }).code
  }
  return null
}

function parseDetailRetryable(detail: unknown): boolean | null {
  if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).retryable === 'boolean') {
    return (detail as { retryable: boolean }).retryable
  }
  return null
}

// ── Auth ──────────────────────────────────────────────

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface User {
  id: string
  email: string
  name: string
  role: 'collector' | 'assistant' | 'artist' | 'admin' | 'founder'
  avatar_url: string | null
  bttcoin_balance: number
  quest_points: number
  marketing_opt_in: boolean
  must_reset_password: boolean
  reasons: string[] | null
  membership_tier: 'freemium' | 'pro' | 'premium' | 'business'
  membership_status: 'active' | 'trialing' | 'past_due' | 'canceled' | 'inactive'
  stripe_customer_id: string | null
  ai_monthly_message_cap: number
  early_access_galax3: boolean
  connected_apps_unlocked: boolean
  product_entitlements: Record<string, string> | null
  gala_personality: number
  nexus_profile: {
    response_templates?: Record<string, string>
  } | null
  proxy_for: string | null
  created_at: string
}

export interface UserMembership {
  configured_tier: 'freemium' | 'pro' | 'premium' | 'business'
  effective_tier: 'freemium' | 'pro' | 'premium' | 'business' | 'staff'
  membership_status: 'active' | 'trialing' | 'past_due' | 'canceled' | 'inactive'
  ai_monthly_message_cap: number | null
  ai_messages_used: number
  ai_messages_remaining: number | null
  primary_model: string
  fallback_model: string | null
  early_access_galax3: boolean
  connected_apps_unlocked: boolean
  product_entitlements: Record<string, string>
}

export const auth = {
  register: (data: {
    email: string
    password: string
    name: string
    reasons?: string[]
    marketing_opt_in?: boolean
    signup_code?: string
  }) => request<{ message: string; verification_required: boolean; access_token?: string | null }>('/auth/register', {
    method: 'POST',
    body: JSON.stringify(data),
  }),

  login: (email: string, password: string) =>
    request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  google: (credential: string, quoteToken?: string) =>
    request<TokenResponse>('/auth/google', {
      method: 'POST',
      body: JSON.stringify({ credential, quote_token: quoteToken }),
    }),

  me: () => request<User>('/auth/me'),

  logout: () => request<void>('/auth/logout', { method: 'POST' }),

  options: () => request<{ google: boolean; registration: boolean; instant_signup?: boolean; signup_code_required?: boolean }>('/auth/options'),

  /** Accept an invite token and set a password (creates or activates account). */
  registerInvite: (invite_token: string, password: string) =>
    request<TokenResponse>('/auth/register-invite', {
      method: 'POST',
      body: JSON.stringify({ invite_token, password }),
    }),

  /**
   * Request a password reset link.
   *
   * The API deliberately returns the same 200 body whether or not an account
   * exists, so the caller must never treat this response as confirmation that
   * the email is registered.
   */
  forgotPassword: (email: string) =>
    request<{ message: string }>('/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),

  /**
   * Ask whether a reset link is still redeemable, without spending it.
   *
   * Lets the reset page say "this expired" before the user invents a password
   * and types it twice. Answers a bare boolean and names no account, so it
   * reveals nothing to a caller who does not already hold the token.
   */
  validateResetToken: (token: string) =>
    request<{ usable: boolean }>(
      `/auth/reset-password/validate?token=${encodeURIComponent(token)}`,
    ),

  /** Redeem a reset token from the emailed link and set a new password. */
  resetPassword: (token: string, newPassword: string) =>
    request<void>('/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify({ token, new_password: newPassword }),
    }),

  /** Change the current user's password (requires current password). */
  changePassword: (currentPassword: string, newPassword: string) =>
    request<void>('/auth/change-password', {
      method: 'PATCH',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  /** Admin: generate an invite link for a new artist or staff member. */
  generateInvite: (data: {
    email: string
    name: string
    role: 'collector' | 'assistant' | 'artist' | 'admin'
    slug?: string
  }) => request<{ token: string; invite_url: string; expires_in_hours: number }>(
    '/auth/invite',
    { method: 'POST', body: JSON.stringify(data) }
  ),
}

export interface McpOAuthPreview {
  client_name: string
  requested_scopes: string[]
  role: User['role']
  can_approve: boolean
  blocked_scopes: string[]
}

export const mcpOAuth = {
  preview: (requestToken: string) =>
    request<McpOAuthPreview>(
      `/auth/mcp/authorize/preview?request_token=${encodeURIComponent(requestToken)}`
    ),
  decide: (requestToken: string, approve: boolean) =>
    request<{ redirect_url: string }>('/auth/mcp/authorize', {
      method: 'POST',
      body: JSON.stringify({ request_token: requestToken, approve }),
    }),
}

// ── Users ─────────────────────────────────────────────

export interface ArtistAccountStatus {
  id: string
  password_status: 'setup_required' | 'user_set' | 'historical_unknown'
  password_changed_at: string | null
  password_change_method: 'setup' | 'change' | 'reset' | 'invite' | null
  last_login_at: string | null
  last_login_method: 'password' | 'google' | 'invite' | null
}

export const users = {
  artistAccountStatus: () => request<ArtistAccountStatus[]>('/users/artist-account-status'),
  list: (params?: { role?: string; skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.role) query.set('role', params.role)
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    // Backend returns User[] directly (not paginated object)
    return request<User[]>(`/users/${qs ? `?${qs}` : ''}`)
  },

  get: (id: string) => request<User>(`/users/${id}`),

  /** Artist: list distinct clients from your bookings */
  myClients: () => request<User[]>('/users/my-clients'),

  membership: () => request<UserMembership>('/users/me/membership'),

  updateProfile: (data: Partial<Pick<User, 'name' | 'avatar_url' | 'marketing_opt_in' | 'reasons' | 'gala_personality' | 'nexus_profile'>>) =>
    request<User>('/users/me', {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  updateRole: (id: string, role: string) =>
    request<User>(`/users/${id}/role`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    }),

  remove: (id: string) =>
    request<void>(`/users/${id}`, { method: 'DELETE' }),

  /** Founder only: set or clear inbox delegation for a user. */
  setProxy: (userId: string, proxyFor: string | null) =>
    request<User>(`/users/${userId}/proxy`, {
      method: 'PATCH',
      body: JSON.stringify({ proxy_for: proxyFor }),
    }),

  updateMembership: (
    userId: string,
    data: Partial<Pick<User, 'membership_tier' | 'membership_status' | 'stripe_customer_id' | 'ai_monthly_message_cap' | 'early_access_galax3' | 'connected_apps_unlocked' | 'product_entitlements'>>
  ) =>
    request<User>(`/users/${userId}/membership`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  uploadAvatar: async (file: File): Promise<User> => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${API_BASE}/users/me/avatar`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${getToken()}` },
      body: fd,
    })
    if (!res.ok) throw new Error('Avatar upload failed')
    return res.json()
  },
}

// ── Contacts / Huddle private export ─────────────────

export interface ContactRecord {
  id: string
  name: string
  email: string | null
  phone: string | null
  source: string
  lifecycle_stage: 'lead' | 'client' | string
  booking_count: number
  last_booking_at: string | null
  last_booking_status: string | null
  marketing_opt_in: boolean
  tags: string[]
}

export interface ContactListResponse {
  contacts: ContactRecord[]
  total: number
}

export interface ContactIntakeRecord {
  source_system: string
  source_type: string
  source_ref: string
  contact_id: string
  occurred_at: string
  payload: Record<string, unknown>
}

export interface ContactExportResponse {
  contacts: ContactRecord[]
  intake_items: ContactIntakeRecord[]
  total_contacts: number
  total_intake_items: number
}

export const contacts = {
  list: (params?: { search?: string; skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.search) query.set('search', params.search)
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<ContactListResponse>(`/contacts${qs ? `?${qs}` : ''}`)
  },

  intakeExport: () => request<ContactExportResponse>('/contacts/intake-export'),
}

// ── Artists ───────────────────────────────────────────

export interface ArtistSummary {
  id: string
  slug: string
  name: string
  specialty: string[] | null
  avatar_url: string | null
  availability_source: 'google_calendar' | 'venue' | 'manual' | 'none'
  booking_mode: string | null
  booking_slot_interval_minutes?: number
  booking_buffer_minutes?: number
  booking_min_notice_hours?: number
  booking_max_advance_days?: number
  booking_allow_same_day?: boolean
}

export interface ArtistDetail extends ArtistSummary {
  title: string | null
  bio: string | null
  booking_mode: string | null
  booking_url: string | null
  availability_source: 'google_calendar' | 'venue' | 'manual' | 'none'
  instagram_url: string | null
  portfolio_images: string[] | null
  /** original_url -> 640px WebP thumb url; an original without an entry is served full size. */
  portfolio_thumbs?: Record<string, string> | null
  portfolio_focus?: Record<string, string> | null
  phone: string | null
  facebook_url: string | null
  tiktok_url: string | null
  website_url: string | null
  flash_deposit_amount?: number | null
  allow_prepay_full?: boolean
  working_hours?: Record<string, [number, number] | null> | null
}

export const artists = {
  /** Public: list all artists (crew page) */
  list: () => request<ArtistSummary[]>('/artists/'),

  /** Public: get a single artist by slug */
  get: (slug: string) => request<ArtistDetail>(`/artists/${slug}`),

  /** Artist+: update own profile */
  updateProfile: (data: {
    title?: string
    specialty?: string[]
    bio?: string
    booking_mode?: string
    booking_slot_interval_minutes?: number
    booking_buffer_minutes?: number
    booking_min_notice_hours?: number
    booking_max_advance_days?: number
    booking_allow_same_day?: boolean
    flash_deposit_amount?: number | null
    allow_prepay_full?: boolean
    working_hours?: Record<string, [number, number] | null> | null
    instagram_url?: string
    phone?: string
    facebook_url?: string
    tiktok_url?: string
    website_url?: string
  }) => request<ArtistDetail>('/artists/me', {
    method: 'PATCH',
    body: JSON.stringify(data),
  }),

  /** Artist+: upload an image to portfolio gallery (uses raw fetch. FormData needs no Content-Type) */
  uploadGalleryImage: async (file: File): Promise<ArtistDetail> => {
    const formData = new FormData()
    formData.append('file', file)
    const headers: Record<string, string> = {}
    if (_token) headers['Authorization'] = `Bearer ${_token}`
    const res = await fetch(`${API_BASE}/artists/me/gallery/upload`, {
      method: 'POST',
      headers,
      body: formData,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `Upload failed (${res.status})`)
    }
    return res.json()
  },

  /** Artist+: remove an image from portfolio gallery */
  removeGalleryImage: (url: string) => request<ArtistDetail>('/artists/me/gallery', {
    method: 'DELETE',
    body: JSON.stringify({ url }),
  }),

  /** Artist+: upload a profile picture */
  uploadAvatar: async (file: File): Promise<ArtistDetail> => {
    const formData = new FormData()
    formData.append('file', file)
    const headers: Record<string, string> = {}
    if (_token) headers['Authorization'] = `Bearer ${_token}`
    const res = await fetch(`${API_BASE}/artists/me/avatar`, {
      method: 'POST',
      headers,
      body: formData,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `Avatar upload failed (${res.status})`)
    }
    return res.json()
  },
}

export interface GiftCardCheckoutRequest {
  artist_id: string
  amount_cents: number
  purchaser_name: string
  purchaser_email: string
  recipient_name: string
  recipient_email?: string
  send_to_recipient: boolean
  message?: string
}

export interface GiftCardCheckoutResponse {
  gift_card_id: string
  session_id: string
  checkout_url: string
  face_value_cents: number
  charged_amount_cents: number
}

export interface GiftCardStatusResponse {
  status: 'pending' | 'active' | 'redeemed' | 'voided'
  face_value_cents: number
  recipient_email_hint: string
  delivered: boolean
}

export interface GiftCardRedeemResponse {
  gift_card_id: string
  artist_id: string
  redeemed_cents: number
  balance_cents: number
  status: string
}

export const giftCards = {
  checkout: (data: GiftCardCheckoutRequest) => request<GiftCardCheckoutResponse>('/gift-cards/checkout', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  status: (sessionId: string) => request<GiftCardStatusResponse>(`/gift-cards/status/${encodeURIComponent(sessionId)}`),
  redeem: (data: { code: string; amount_cents: number; booking_id?: string; note?: string }) =>
    request<GiftCardRedeemResponse>('/gift-cards/redeem', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}

// ── Flash designs ─────────────────────────────────────

export type FlashStatus = 'available' | 'claimed' | 'sold'

export interface FlashDesign {
  id: string
  artist_id: string
  artist_slug?: string | null
  artist_name?: string | null
  image_url: string
  preview_url?: string | null
  source_image_url?: string | null
  crop_x?: number
  crop_y?: number
  crop_w?: number
  crop_h?: number
  crop_units?: 'percent' | 'pixel'
  asset_version?: number
  context_regions?: Array<Record<string, unknown>>
  title: string | null
  name?: string | null
  ai_suggested_name?: string | null
  description: string | null
  price: number | null
  estimated_minutes?: number | null
  size_estimate: string | null
  size_label?: string | null
  placement?: string | null
  style: string | null
  ai_metadata?: Record<string, unknown> | null
  status: FlashStatus
  created_at: string
}

export const flash = {
  /** Public: list flash designs for an artist by slug */
  listByArtist: (slug: string) =>
    request<{ designs: FlashDesign[]; total: number }>(`/flash/store/public?artist_slug=${slug}&limit=50`)
      .then(r => r.designs.map(d => ({ ...d, status: (d.status as string) === 'published' ? ('available' as FlashStatus) : d.status }))),

  /** Artist+: list own flash designs */
  listOwn: () => request<FlashDesign[]>('/artists/me/flash'),

  /** Artist+: upload a new flash design image + metadata */
  create: async (file: File, meta: {
    title?: string
    description?: string
    price?: number
    size_estimate?: string
    style?: string
  }): Promise<FlashDesign> => {
    const formData = new FormData()
    formData.append('file', file)
    if (meta.title)         formData.append('title', meta.title)
    if (meta.description)   formData.append('description', meta.description)
    if (meta.price != null) formData.append('price', String(meta.price))
    if (meta.size_estimate) formData.append('size_estimate', meta.size_estimate)
    if (meta.style)         formData.append('style', meta.style)
    const headers: Record<string, string> = {}
    if (_token) headers['Authorization'] = `Bearer ${_token}`
    const res = await fetch(`${API_BASE}/artists/me/flash`, {
      method: 'POST', headers, body: formData,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `Flash upload failed (${res.status})`)
    }
    return res.json()
  },

  /** Artist+: update metadata or status of a flash design */
  update: (id: string, data: Partial<Pick<FlashDesign, 'title' | 'description' | 'price' | 'size_estimate' | 'style' | 'status'>>) =>
    request<FlashDesign>(`/artists/me/flash/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  /** Artist+: delete a flash design */
  remove: (id: string) =>
    request<void>(`/artists/me/flash/${id}`, { method: 'DELETE' }),

  /** Collector: claim a flash design (creates a booking inquiry) */
  claim: (id: string, data: { name: string; email: string; phone?: string; notes?: string }) =>
    request<{ booking_id: string }>(`/flash/${id}/claim`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}

// ── Bookings ──────────────────────────────────────────

export type BookingKind = 'tattoo_session' | 'flash' | 'consultation'
export type ConsultationMethod = 'whatsapp' | 'meet' | 'in_person'
export type BookyMode = 'manual' | 'shadow' | 'approval' | 'consult_auto' | 'session_auto'
export type BookyTruthState = 'loading' | 'ready' | 'empty' | 'stale' | 'blocked' | 'approval_needed' | 'failed' | 'completed'

export interface BookingOutcomeEnvelope {
  truth_state: BookyTruthState
  booking_status: string
  calendar_state: string
  invitation_state: string
  meet_state: string
  courier_state: string
  retry_eligible: boolean
  updated_at: string
}

export interface BookyDecisionEnvelope extends BookingOutcomeEnvelope {
  action_id: string
  booking_id: string | null
  receipt_ids: string[]
  configured_mode: BookyMode
  effective_mode: BookyMode
  recommendation: string
  explanation: string
  confidence: string
  policy_version: string
  owner: 'booky'
  risk: string
  required_approval: boolean
  blocked_reasons: string[]
  available_actions: string[]
}

export interface BookySettings {
  artist_id: string
  artist_slug: string
  mode: BookyMode
  opted_in_at: string | null
  automation_enabled: boolean
  policy_version: string
  artist_canary_allowed: boolean
  collector_canary_configured: boolean
  session_auto_available: false
}

export interface BookyCapability {
  capability: string
  inquiry_id: string
  collector_id: string
  expires_at: string
  policy_version: string
}

export interface Booking {
  id: string
  client_id: string | null
  artist_id: string
  status: string
  description: string
  booking_kind?: BookingKind | null
  consultation_method?: ConsultationMethod | null
  placement: string | null
  size_estimate: string | null
  style_preference: string | null
  reference_urls: string[] | null
  preferred_date: string | null
  preferred_time: string | null
  preferred_slots?: Array<{ date: string; time: string }> | null
  scheduled_date: string | null
  scheduled_time: string | null
  duration_minutes: number | null
  deposit_amount: number | null
  estimated_total: number | null
  deposit_paid: boolean
  is_bring_your_own_flash?: boolean
  deposit_authorization_status?: 'pending_checkout' | 'authorized' | 'captured' | 'released' | 'expired' | 'failed' | 'reauthorization_required' | null
  deposit_authorized_at?: string | null
  deposit_review_deadline_at?: string | null
  deposit_review_reminder_sent_at?: string | null
  artist_notes: string | null
  client_notes: string | null
  conversation_id: string | null
  reference_code: string | null
  guest_name: string | null
  guest_email: string | null
  guest_phone: string | null
  sms_opt_in: boolean
  sms_opt_in_at: string | null
  sms_consent_text: string | null
  budget: string | null
  timeline: string | null
  stripe_checkout_session_id: string | null
  stripe_payment_intent_id: string | null
  aftercare_ack: string | null
  completed_date: string | null
  created_at: string
  updated_at: string
  client_name: string | null
  artist_name: string | null
  // Resolved identity: always populated, use these instead of guest_name
  resolved_name: string | null
  resolved_email: string | null
  resolved_phone: string | null
}

export interface BookingConversationRepairResult {
  booking_id: string
  reference_code: string
  conversation_id: string
  repairs?: string
}

export interface BookingConversationRepairResponse {
  fixed: BookingConversationRepairResult[]
  skipped: Array<{ booking_id: string; reason: string }>
  total: number
}

export interface BookingLifecycleEvent {
  id: string
  booking_id: string | null
  artist_id: string | null
  actor_id: string | null
  event_type: string
  source: string
  reference_code: string | null
  hold_token: string | null
  guest_email: string | null
  summary: string
  details: Record<string, unknown> | null
  created_at: string
}

export interface BookingScheduleSuggestion {
  date: string
  time: string
  label: string
  duration_minutes: number
  source: string
  confidence: string
  rationale: string
}

export interface BookingScheduleSuggestionResponse {
  booking_id: string
  artist_id: string
  timezone: string
  duration_minutes: number
  calendar_connected: boolean
  suggestions: BookingScheduleSuggestion[]
}

export interface PublicBookingSubmit {
  inquiry_id?: string
  artist_slug: string
  first_name: string
  last_name: string
  email: string
  phone?: string
  sms_opt_in?: boolean
  description: string
  booking_kind?: BookingKind
  consultation_method?: ConsultationMethod
  booky_consent?: boolean
  booky_consent_policy_version?: string
  booky_capability?: string
  style_preference?: string
  session_type_id?: string
  placement?: string
  size_estimate?: string
  budget?: string
  timeline?: string
  preferred_date?: string
  preferred_time?: string
  hold_token?: string
  booking_add_on_ids?: string[]
  save_payment_method?: boolean
  reference_image_urls?: string[]
  client_notes?: string
}

export interface PublicBookingResponse {
  reference_code: string | null
  artist_name: string | null
  booking_id: string | null
  checkout_url: string | null
  checkout_required: boolean
  deposit_amount: number | null
  message: string
  booking_status: string | null
  scheduled_date: string | null
  scheduled_time: string | null
  conference_url: string | null
  booky_outcome: BookingOutcomeEnvelope | null
  idempotent_acknowledgement: boolean
  account_setup_token: string | null
}

export interface PublicBookingDraft {
  inquiry_id: string
  artist_slug?: string
  first_name?: string
  last_name?: string
  email?: string
  phone?: string | null
  sms_opt_in?: boolean
  description?: string
  booking_kind?: BookingKind
  consultation_method?: ConsultationMethod
  style_preference?: string | null
  placement?: string | null
  size_estimate?: string | null
  budget?: string | null
  timeline?: string | null
  preferred_date?: string | null
  preferred_time?: string | null
  hold_token?: string | null
  client_notes?: string | null
  image_urls?: string[]
}

export interface BookingDraftRecord {
  id: string
  inquiry_id: string
  status: string
  artist_slug: string | null
  guest_name: string | null
  guest_email: string | null
  guest_phone: string | null
  form_data: Partial<PublicBookingDraft> | null
  image_urls: string[] | null
  reference_code: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export const bookings = {
  /** Public: save a recoverable snapshot before final submit */
  saveDraft: (data: PublicBookingDraft): Promise<{ inquiry_id: string; status: string }> =>
    request('/bookings/draft', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Staff: list recoverable partial public booking submissions */
  listDrafts: (params?: { status?: string; skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.status) query.set('status', params.status)
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<{ drafts: BookingDraftRecord[]; total: number }>(`/bookings/drafts${qs ? `?${qs}` : ''}`)
  },

  /** Public: upload a reference/placement image (no auth) */
  uploadImage: async (file: File, draft?: PublicBookingDraft): Promise<{ url: string }> => {
    const fd = new FormData()
    fd.append('file', file)
    if (draft) fd.append('draft_json', JSON.stringify(draft))
    const res = await fetch(`${API_BASE}/bookings/upload-image`, {
      method: 'POST',
      body: fd,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, parseDetail(err.detail, 'Image upload failed'))
    }
    return res.json()
  },

  /** Public: submit a booking request without an account */
  submitPublic: async (data: PublicBookingSubmit): Promise<PublicBookingResponse> => {
    const res = await fetch(`${API_BASE}/bookings/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, parseDetail(err.detail, 'Booking submission failed'))
    }
    return res.json()
  },

  /** Auth: link a guest booking to a newly created account */
  linkAccount: (referenceCode: string) =>
    request<Booking>(`/bookings/${referenceCode}/link-account`, { method: 'POST' }),

  create: (data: {
    artist_id: string
    description: string
    placement?: string
    size_estimate?: string
    style_preference?: string
    reference_urls?: string[]
    preferred_date?: string
    preferred_time?: string
    client_notes?: string
  }) => request<Booking>('/bookings/', {
    method: 'POST',
    body: JSON.stringify(data),
  }),

  /**
   * Admin/founder: create a booking on behalf of an artist (a front desk booking
   * an artist's clients; needs ADMIN_BOOK_ON_BEHALF_ENABLED on the API). Mirrors
   * backend StaffBookingCreate. When status is
   * 'confirmed' with a date and confirm_calendar is true, the API fans out to
   * the artist's Google Calendar + client email + SMS.
   */
  staffCreate: (data: {
    artist_id: string
    client_id?: string
    guest_name?: string
    guest_email?: string
    guest_phone?: string
    description: string
    placement?: string
    size_estimate?: string
    style_preference?: string
    scheduled_date?: string
    scheduled_time?: string
    duration_minutes?: number
    deposit_amount?: number
    estimated_total?: number
    artist_notes?: string
    status?: string
    confirm_calendar?: boolean
  }) => request<Booking>('/bookings/staff', {
    method: 'POST',
    body: JSON.stringify(data),
  }),

  list: (params?: { status?: string; skip?: number; limit?: number; all?: boolean; for_artist?: string }) => {
    const query = new URLSearchParams()
    if (params?.status) query.set('status', params.status)
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.all) query.set('all', 'true')
    if (params?.for_artist) query.set('for_artist', params.for_artist)
    const qs = query.toString()
    return request<{ bookings: Booking[]; total: number }>(`/bookings/${qs ? `?${qs}` : ''}`)
  },

  get: (id: string) => request<Booking>(`/bookings/${id}`),

  getBookySettings: (artistId: string) =>
    request<BookySettings>(`/bookings/booky/settings/${artistId}`),

  updateBookySettings: (artistId: string, mode: BookyMode) =>
    request<BookySettings>(`/bookings/booky/settings/${artistId}`, {
      method: 'PATCH',
      body: JSON.stringify({ mode }),
    }),

  createBookyCapability: (inquiryId: string) =>
    request<BookyCapability>('/bookings/booky/capability', {
      method: 'POST',
      body: JSON.stringify({ inquiry_id: inquiryId }),
    }),

  getBookyProjection: (bookingId: string) =>
    request<BookingOutcomeEnvelope | BookyDecisionEnvelope>(`/bookings/${bookingId}/booky`),

  retryBookyConfirmation: (bookingId: string) =>
    request<BookyDecisionEnvelope>(`/bookings/${bookingId}/booky/retry`, { method: 'POST' }),

  history: (id: string) =>
    request<BookingLifecycleEvent[]>(`/bookings/${id}/history`),

  scheduleSuggestions: (id: string, params?: { days?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.days) query.set('days', String(params.days))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<BookingScheduleSuggestionResponse>(`/bookings/${id}/schedule-suggestions${qs ? `?${qs}` : ''}`)
  },

  update: (
    id: string,
    data: Partial<Booking> & {
      expected_status?: Booking['status']
      notify_client?: boolean
      allow_conflict?: boolean
      allow_out_of_bounds?: boolean
      override_reason?: string
    },
  ) =>
    request<Booking>(`/bookings/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  reschedule: (id: string, data: {
    scheduled_date: string
    scheduled_time: string
    duration_minutes?: number
    reason?: string
    notify_client?: boolean
    allow_conflict?: boolean
    allow_out_of_bounds?: boolean
    override_reason?: string
  }) =>
    request<Booking>(`/bookings/${id}/reschedule`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Staff: what the approval card prefills, and whether a deposit link can go out. */
  approvalDefaults: (id: string) =>
    request<BookingApprovalDefaults>(`/bookings/${id}/approval-defaults`),

  /** Staff: approve a request with one time and a deposit. Sends the "request approved" email. */
  approve: (id: string, data: BookingApproveInput) =>
    request<BookingApprovalResult>(`/bookings/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  cancel: (id: string, data?: {
    reason?: string
    notify_client?: boolean
    delete_calendar_event?: boolean
  }) =>
    request<Booking>(`/bookings/${id}/cancel`, {
      method: 'POST',
      body: JSON.stringify(data || {}),
    }),

  aftercareAck: (id: string) =>
    request<{ status: string; acknowledged: boolean }>(`/bookings/${id}/aftercare-ack`, { method: 'POST' }),

  /** Gala AI: draft a booking response */
  galaAssist: (id: string) =>
    request<{ draft: string; booking_id: string }>(`/bookings/${id}/gala-assist`, { method: 'POST' }),

  /** Ensure a conversation thread exists for this booking. Creates one on demand for guest bookings. */
  ensureConversation: (id: string) =>
    request<{ conversation_id: string }>(`/bookings/${id}/conversation`, { method: 'POST' }),

  /** Staff: repair missing or degraded booking conversations and intake messages. */
  backfillConversations: (params?: { limit?: number; repairExisting?: boolean }) => {
    const query = new URLSearchParams()
    if (params?.limit !== undefined) query.set('limit', String(params.limit))
    if (params?.repairExisting) query.set('repair_existing', 'true')
    const qs = query.toString()
    return request<BookingConversationRepairResponse>(`/bookings/backfill-conversations${qs ? `?${qs}` : ''}`, {
      method: 'POST',
    })
  },
}

// ── CRM (native Booking CRM Mod — pipeline v1) ─────────────────

export interface CrmPipelineTaskRecord {
  id: string
  title: string
  body: string | null
  status: string
  priority: string | null
  booking_id: string | null
  assignee_id: string | null
  created_at: string
  updated_at: string
  booking_reference_code: string | null
  booking_client_name: string | null
  booking_artist_name: string | null
  conversation_id: string | null
  booking_url: string | null
  courier_url: string | null
}

export const crm = {
  listPipelineTasks: (params?: { status?: string; skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.status) query.set('status', params.status)
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<{ tasks: CrmPipelineTaskRecord[]; total: number }>(
      `/crm/pipeline-tasks${qs ? `?${qs}` : ''}`,
    )
  },

  createPipelineTask: (data: {
    title: string
    body?: string | null
    status?: string
    priority?: string | null
    booking_id?: string | null
    assignee_id?: string | null
  }) =>
    request<CrmPipelineTaskRecord>('/crm/pipeline-tasks', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  setPipelineTaskStatus: (taskId: string, status: string) =>
    request<CrmPipelineTaskRecord>(`/crm/pipeline-tasks/${taskId}/status?status=${encodeURIComponent(status)}`, {
      method: 'PATCH',
    }),
}

// ── Conversations ─────────────────────────────────────

/**
 * A single message in a conversation. Messages are either plain text
 * (`message_type === 'text'`, payload null) or typed artifacts with
 * a structured `artifact_payload` and mutable `artifact_state`.
 *
 * Artifact types are validated + dispatched by the backend's
 * app.services.artifacts registry. The frontend renders different
 * card components based on `message_type` and mutates state via
 * `messagesApi.artifactAction()`.
 */
export interface ConversationMessage {
  id: string
  conversation_id: string
  sender_id: string
  sender_name: string | null
  content: string
  message_type: string
  metadata_json: string | null
  artifact_payload: Record<string, unknown> | null
  artifact_state: Record<string, unknown> | null
  created_at: string
  updated_at: string | null
}

/** Response shape for POST /messages/{id}/artifact-action */
export interface ArtifactActionResponse {
  message_id: string
  message_type: string
  artifact_state: Record<string, unknown>
  updated_at: string
}

/** Response shape for GET /messages/types */
export interface ArtifactTypeInfo {
  message_type: string
  allowed_actions: string[]
}

export interface Conversation {
  id: string
  name: string | null
  is_group: boolean
  created_by: string
  created_at: string
  updated_at: string
  participant_count: number
  last_message: ConversationMessage | null
  unread_count: number
  is_unread: boolean
  last_read_at: string | null
  booking_context: {
    id: string
    reference_code: string | null
    status: string
    client_name: string | null
    client_email: string | null
    client_phone: string | null
    artist_name: string | null
    description: string
    reference_urls: string[]
    booking_kind: BookingKind
    consultation_method: ConsultationMethod | null
    placement: string | null
    style_preference: string | null
    preferred_date: string | null
    preferred_time?: string | null
    preferred_slots?: Array<{ date: string; time: string }> | null
    scheduled_date: string | null
    scheduled_time: string | null
    booking_url: string | null
  } | null
  channel_context: {
    channel: string
    provider: string | null
    direction: string | null
    lane_id: string | null
    lane_label: string | null
    assigned_agent_id: string | null
    assigned_agent_label: string | null
    assignment_status: 'assigned' | 'unverified' | string
    reply_mode: 'gated_auto_reply' | 'human_operated' | string | null
    voice_status: 'provider_configured' | 'not_configured' | 'unverified' | string
    business_number_masked: string | null
    routing_status: 'verified' | 'blocked' | string
    customer_number: string | null
    status: string | null
    last_log_id: string | null
    last_seen_at: string | null
  } | null
}

export interface CourierChannel {
  id: string
  label: string
  kind: 'email' | 'social' | 'messaging' | 'voice' | 'content' | string
  connected: boolean
  configured: boolean
  route: string | null
  forward_to: string | null
  status: string
  direction: string
  provider?: string
  lane?: string
}

export interface CourierTool {
  id: string
  label: string
  provider: string
  lane: string
  status: string
  webhook_url?: string
  endpoint?: string
  capabilities: string[]
}

export interface CourierChannelStatus {
  channels: CourierChannel[]
  tools?: CourierTool[]
  sms_lanes?: Array<{
    id: 'bella' | 'gala' | 'ryozan-collector'
    label: string
    configured: boolean
    masked_number: string | null
    status: 'ready' | 'blocked' | string
    configuration_error: string | null
    assigned_agent_id?: 'bella' | 'gala' | 'sigma'
    assigned_agent_label?: string
    assignment_status?: 'assigned' | string
    reply_mode?: 'gated_auto_reply' | 'human_operated' | string
    auto_reply_enabled?: boolean
    voice_status?: 'provider_configured' | 'not_configured' | string
  }>
  policy: {
    archive_by_default: boolean
    deletes_remove_participant_only: boolean
    audit_required: boolean
    business_channels_use_twilio?: boolean
    personal_channels_prefer_open_source_sip?: boolean
    content_agents_never_contact_clients_without_approval?: boolean
  }
}

export interface Participant {
  id: string
  user_id: string
  user_name: string | null
  role: string
  joined_at: string
  last_read_at: string | null
}

export const conversations = {
  list: () => request<{ conversations: Conversation[]; total: number }>('/conversations/'),

  channelStatus: () => request<CourierChannelStatus>('/conversations/channels/status'),

  get: (convId: string) => request<Conversation>(`/conversations/${convId}`),

  create: (data: { participant_ids: string[]; name?: string; is_group?: boolean }) =>
    request<Conversation>('/conversations/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getParticipants: (convId: string) =>
    request<Participant[]>(`/conversations/${convId}/participants`),

  getMessages: (convId: string, params?: { skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<{ messages: ConversationMessage[]; total: number; has_more: boolean }>(
      `/conversations/${convId}/messages${qs ? `?${qs}` : ''}`
    )
  },

  sendMessage: (
    convId: string,
    content: string,
    messageType: string = 'text',
    metadataJson?: string,
    artifactPayload?: Record<string, unknown>,
  ) =>
    request<ConversationMessage>(`/conversations/${convId}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        content,
        message_type: messageType,
        metadata_json: metadataJson,
        artifact_payload: artifactPayload,
      }),
    }),

  sendChannelMessage: (
    convId: string,
    data: { channel: 'courier' | 'sms' | 'whatsapp'; content: string; to?: string; is_transactional?: boolean },
  ) =>
    request<ConversationMessage>(`/conversations/${convId}/channel-send`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  markRead: (convId: string) =>
    request<void>(`/conversations/${convId}/mark-read`, { method: 'POST' }),

  /** Gala AI: generate a context-aware reply draft */
  galaAssist: (convId: string) =>
    request<{ draft: string; conversation_id: string }>(`/conversations/${convId}/gala-assist`, {
      method: 'POST',
    }),

  /** Signal typing */
  setTyping: (convId: string) =>
    request<void>(`/conversations/${convId}/typing`, { method: 'POST' }),

  /** Check who is typing */
  getTyping: (convId: string) =>
    request<{ typing: string[] }>(`/conversations/${convId}/typing`),

  /** Delete a message (sender or admin) */
  deleteMessage: (convId: string, messageId: string) =>
    request<void>(`/conversations/${convId}/messages/${messageId}`, { method: 'DELETE' }),

  /** Archive a conversation (removes you from participant list) */
  archive: (convId: string) =>
    request<void>(`/conversations/${convId}/archive`, { method: 'POST' }),

  /** Archive the conversation for the signed-in participant. */
  delete: (convId: string) =>
    request<void>(`/conversations/${convId}`, { method: 'DELETE' }),
}

// ── Messages (artifact dispatcher) ────────────────────
//
// Per-message endpoints for interactive message artifacts. Conversation
// CRUD lives on `conversations` above; this namespace is just the
// artifact state-machine surface (confirm/cancel a quote proposal,
// select a slot from a slot proposal, sign a waiver request, etc.).
//
// `artifactAction` dispatches an action to the backend handler for
// the message's type and returns the new artifact_state. The frontend
// card component can update in place without a full message refetch.

export const messages = {
  /**
   * Dispatch an action against an artifact message.
   * Example: confirm a quote_proposal, pick a slot from a slot_proposal.
   */
  artifactAction: (
    messageId: string,
    action: string,
    data?: Record<string, unknown>,
  ) =>
    request<ArtifactActionResponse>(
      `/messages/${messageId}/artifact-action`,
      {
        method: 'POST',
        body: JSON.stringify({ action, data }),
      },
    ),

  /** List every registered artifact type + allowed actions. */
  listTypes: () =>
    request<{ types: ArtifactTypeInfo[] }>('/messages/types'),
}

// ── Session Types (per-artist size/duration/pricing) ──

export interface SessionType {
  id: string
  artist_id: string
  label: string
  size: string
  placements: string[] | null
  duration_minutes: number
  buffer_minutes: number | null
  deposit_amount: number
  hourly_rate: number | null
  price_min: number | null
  price_max: number | null
  is_active: boolean
  sort_order: number
}

export interface BookingAddOn {
  id: string
  artist_id: string
  label: string
  description: string | null
  price_amount: number | null
  duration_minutes: number | null
  is_active: boolean
  sort_order: number
}

export const sessionTypes = {
  /** Public: list session types for an artist */
  forArtist: (slug: string, activeOnly = true) =>
    request<SessionType[]>(`/session-types/artist/${slug}?active_only=${activeOnly}`),

  /** Artist+: list your own session types */
  mine: () =>
    request<SessionType[]>('/session-types/mine'),

  /** Artist+: create a session type */
  create: (data: Omit<SessionType, 'id' | 'artist_id' | 'is_active' | 'sort_order'> & { sort_order?: number }) =>
    request<SessionType>('/session-types/', { method: 'POST', body: JSON.stringify(data) }),

  /** Artist+: update a session type */
  update: (id: string, data: Partial<SessionType>) =>
    request<SessionType>(`/session-types/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  /** Artist+: delete a session type */
  remove: (id: string) =>
    request<void>(`/session-types/${id}`, { method: 'DELETE' }),

  /** Public: lookup best match for an artist + size + optional placement */
  lookup: (artistSlug: string, size: string, placement?: string) => {
    const params = new URLSearchParams({ artist_slug: artistSlug, size })
    if (placement) params.set('placement', placement)
    return request<SessionType | null>(`/session-types/lookup?${params}`)
  },
}

export const bookingAddOns = {
  /** Public: list active booking add-ons for an artist */
  forArtist: (slug: string, activeOnly = true) =>
    request<BookingAddOn[]>(`/booking-addons/artist/${slug}?active_only=${activeOnly}`),

  /** Artist+: list your booking add-ons */
  mine: (artistId?: string) => {
    const qs = artistId ? `?artist_id=${encodeURIComponent(artistId)}` : ''
    return request<BookingAddOn[]>(`/booking-addons/mine${qs}`)
  },

  /** Artist+: create a booking add-on */
  create: (data: Omit<BookingAddOn, 'id' | 'artist_id' | 'is_active' | 'sort_order'> & { sort_order?: number; artist_id?: string }) =>
    request<BookingAddOn>('/booking-addons/', { method: 'POST', body: JSON.stringify(data) }),

  /** Artist+: update a booking add-on */
  update: (id: string, data: Partial<BookingAddOn>) =>
    request<BookingAddOn>(`/booking-addons/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  /** Artist+: delete a booking add-on */
  remove: (id: string) =>
    request<void>(`/booking-addons/${id}`, { method: 'DELETE' }),
}

// ── Documents ────────────────────────────────────────

export interface DocumentTemplate {
  id: string
  name: string
  category: string
  description: string | null
  fields_json: string
  is_active: boolean
  requires_signature: boolean
  created_at: string
}

export interface DocumentInstance {
  id: string
  template_id: string | null
  template_name: string | null
  user_id: string
  user_name: string | null
  category: string
  title: string
  status: string
  data_json: string | null
  signed_at: string | null
  file_url: string | null
  notes: string | null
  // Send / lifecycle fields
  send_token: string | null
  recipient_email: string | null
  recipient_name: string | null
  sent_at: string | null
  sent_by: string | null
  viewed_at: string | null
  reminder_at: string | null
  reminder_sent: boolean
  expires_at: string | null
  created_at: string
  updated_at: string
}

// ── Document send / public form types ──────────────

export interface DocumentSendRequest {
  template_id: string
  recipient_email: string
  recipient_name: string
  reminder_hours?: number
  expires_hours?: number
}

export interface DocumentSendResponse {
  instance_id: string
  send_token: string
  public_url: string
}

export interface DocumentPublicForm {
  title: string
  description: string | null
  fields: Record<string, unknown>[]
  requires_signature: boolean
  recipient_name: string
  expired: boolean
  category: string
  initial_values: Record<string, string>
  prefill_notice: string | null
}

export interface DocumentPublicSubmit {
  data: Record<string, unknown>
  signature_data?: string   // base64 PNG
}

export const documents = {
  /** List all document templates */
  listTemplates: (params?: { category?: string }) => {
    const query = new URLSearchParams()
    if (params?.category) query.set('category', params.category)
    const qs = query.toString()
    return request<{ templates: DocumentTemplate[]; total: number }>(
      `/documents/templates${qs ? `?${qs}` : ''}`
    )
  },

  /** Get a specific template */
  getTemplate: (id: string) => request<DocumentTemplate>(`/documents/templates/${id}`),

  /** List document instances (filled forms, uploaded docs) */
  listDocuments: (params?: { category?: string; user_id?: string; status?: string }) => {
    const query = new URLSearchParams()
    if (params?.category) query.set('category', params.category)
    if (params?.user_id) query.set('user_id', params.user_id)
    if (params?.status) query.set('status', params.status)
    const qs = query.toString()
    return request<{ documents: DocumentInstance[]; total: number }>(
      `/documents/${qs ? `?${qs}` : ''}`
    )
  },

  /** Submit a filled form */
  submit: (data: {
    template_id?: string
    category: string
    title: string
    data_json?: string
    file_url?: string
    notes?: string
  }) => request<DocumentInstance>('/documents/', {
    method: 'POST',
    body: JSON.stringify(data),
  }),

  /** Sign a document */
  sign: (id: string) =>
    request<DocumentInstance>(`/documents/${id}/sign`, { method: 'POST' }),

  /** Update document status (admin) */
  updateStatus: (id: string, status: string, notes?: string) =>
    request<DocumentInstance>(`/documents/${id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status, notes }),
    }),

  /** Send a document to a recipient via magic link */
  send: (data: DocumentSendRequest) =>
    request<DocumentSendResponse>('/documents/send', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Public: get form data for a magic link token (no auth) */
  getPublicForm: async (token: string): Promise<DocumentPublicForm> => {
    const res = await fetch(`${API_BASE}/documents/public/${token}`)
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, err.detail || 'Failed to load form')
    }
    return res.json()
  },

  /** Public: submit a completed form via magic link (no auth) */
  submitPublicForm: async (token: string, data: DocumentPublicSubmit): Promise<DocumentInstance> => {
    const res = await fetch(`${API_BASE}/documents/public/${token}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, err.detail || 'Submission failed')
    }
    return res.json()
  },

  /** Public: walk-in check-in, creates waiver token (no auth) */
  checkin: async (data: { name: string; email: string; artist_slug?: string }): Promise<{ token: string; form_url: string }> => {
    const res = await fetch(`${API_BASE}/documents/checkin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, err.detail || 'Check-in failed')
    }
    return res.json()
  },

  /** Manual resend reminder for a sent document */
  remind: (id: string) =>
    request<{ status: string }>(`/documents/${id}/remind`, { method: 'POST' }),

  /** Delete a document instance */
  remove: (id: string) =>
    request<{ status: string }>(`/documents/${id}`, { method: 'DELETE' }),

  /** Upload a file */
  upload: (file: File, category: string, title: string) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('category', category)
    formData.append('title', title)

    const headers: Record<string, string> = {}
    if (_token) {
      headers['Authorization'] = `Bearer ${_token}`
    }

    return fetch(`${API_BASE}/documents/upload`, {
      method: 'POST',
      headers,
      body: formData,
    }).then(async (res) => {
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: res.statusText }))
        throw new ApiError(res.status, error.detail || 'Upload failed')
      }
      return res.json() as Promise<DocumentInstance>
    })
  },
}

// ── Notifications ─────────────────────────────────────

export interface Notification {
  id: string
  user_id: string
  notification_type: string
  title: string
  body: string
  is_read: boolean
  action_url: string | null
  created_at: string
}

export const notifications = {
  list: (params?: { unread_only?: boolean; skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.unread_only) query.set('unread_only', 'true')
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<{ notifications: Notification[]; total: number; unread_count: number }>(
      `/notifications/${qs ? `?${qs}` : ''}`
    )
  },

  markRead: (id: string) =>
    request<Notification>(`/notifications/${id}/read`, { method: 'POST' }),

  markAllRead: () =>
    request<{ marked: number }>('/notifications/read-all', { method: 'POST' }),

  dismiss: (id: string) =>
    request<void>(`/notifications/${id}`, { method: 'DELETE' }),

  clearAll: () =>
    request<void>('/notifications/', { method: 'DELETE' }),
}

export interface NotificationPreferences {
  id: string
  user_id: string
  email_bookings: boolean
  email_messages: boolean
  email_quests: boolean
  email_marketing: boolean
  push_bookings: boolean
  push_messages: boolean
  push_quests: boolean
  updated_at: string
}

export const notificationPreferences = {
  get: () =>
    request<NotificationPreferences>('/notification-preferences/'),

  update: (data: Partial<Pick<
    NotificationPreferences,
    'email_bookings' | 'email_messages' | 'email_quests' | 'email_marketing' | 'push_bookings' | 'push_messages' | 'push_quests'
  >>) =>
    request<NotificationPreferences>('/notification-preferences/', {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
}

// ── Studio settings (public) ──────────────────────────

/** Weekday names as the API sends them, Sunday first like Date.getDay(). */
export type StudioWeekday = 'sun' | 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat'

/** GET /studio/settings: one install's non-secret display and booking settings. */
/** Weekly hours keyed "0" (Monday) to "6" (Sunday). A closed day is null. */
export type WeeklyHours = Record<string, [number, number] | null>

export interface StudioSettings {
  studio_name: string
  owner_name: string
  contact_email: string
  contact_phone: string
  address: string
  /** IANA zone the booking engine trades in; empty means the browser's own. */
  timezone: string
  booking_days: {
    closed_weekdays: StudioWeekday[]
    request_only_weekdays: StudioWeekday[]
    /** Artists the request-only days apply to; empty means every artist. */
    request_only_artist_slugs: string[]
  }
  /** When the studio books. An artist's own working hours replace `work` for that artist. */
  booking_hours: {
    work: WeeklyHours
    consultation: WeeklyHours
  }
  default_artist_slug: string
  waiver_template_id: string
  sample_flash_urls: string[]
  /** Domain studio email handles live on; empty means handles are off. */
  email_domain: string
  /** The studio's public website; blank values hide their section. */
  site: StudioSiteSettings
}

/** "product": the Ryozan landing at /. "studio": this studio's own website. */
export type PublicSiteMode = 'product' | 'studio'

export interface StudioSiteSettings {
  mode: PublicSiteMode
  /** Hostnames that get the studio site even when mode is "product". */
  studio_hosts: string[]
  tagline: string
  story_paragraphs: string[]
  hours: string[]
  hero_image_url: string
  gallery_image_urls: string[]
  social_links: { label: string; url: string }[]
}

export const studio = {
  getSettings: (init?: RequestInit) =>
    request<StudioSettings>('/studio/settings', init),

  getEditableSettings: () =>
    request<StudioSettingsEdit>('/studio/settings/edit'),

  saveEditableSettings: (values: StudioSettingsEdit) =>
    request<StudioSettingsEdit>('/studio/settings/edit', {
      method: 'PUT',
      body: JSON.stringify(values),
    }),

  getSetupStatus: () =>
    request<{ needs_setup: boolean; has_accounts: boolean }>('/studio/setup'),

  claimInstall: (data: {
    setup_code: string
    studio_name: string
    timezone: string
    owner_name: string
    email: string
    password: string
  }) => request<TokenResponse>('/studio/setup', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
}

/** What the owner can change in the app, by the server's setting names. */
export interface StudioSettingsEdit {
  STUDIO_NAME?: string
  STUDIO_TIMEZONE?: string
  STUDIO_OWNER_NAME?: string
  SHOP_EMAIL?: string
  STUDIO_PHONE?: string
  STUDIO_ADDRESS?: string
  STUDIO_ARTIST_ROSTER?: string
  BOOKING_CLOSED_WEEKDAYS?: string
  BOOKING_REQUEST_ONLY_WEEKDAYS?: string
  BOOKING_WORK_HOURS?: string
  BOOKING_CONSULTATION_HOURS?: string
  PUBLIC_SITE_MODE?: string
  STUDIO_TAGLINE?: string
  STUDIO_STORY?: string
  STUDIO_HOURS?: string
  STUDIO_HERO_IMAGE_URL?: string
  STUDIO_GALLERY_IMAGE_URLS?: string
  STUDIO_SOCIAL_LINKS?: string
}

// ── Push Notifications ───────────────────────────────

export const push = {
  getVapidKey: () =>
    request<{ public_key: string }>('/push/vapid-key'),

  subscribe: (subscription: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    request<{ status: string }>('/push/subscribe', {
      method: 'POST',
      body: JSON.stringify(subscription),
    }),

  unsubscribe: () =>
    request<void>('/push/subscribe', { method: 'DELETE' }),
}

// ── Waiver (public, no auth header) ─────────────────

export interface WaiverSubmitPayload {
  full_name: string
  date_of_birth: string
  phone: string
  email: string
  emergency_contact: string
  emergency_contact_address: string
  social_handle?: string
  artist_slug: string
  tattoo_description: string
  tattoo_placement: string
  pregnant: string
  blood_disorder: string
  skin_condition: string
  allergies?: string
  medical_conditions?: string
  photo_id_url?: string
  health_and_eligibility: boolean
  risks_and_consent: boolean
  accept_terms: boolean
  signature_data: string
}

export interface WaiverResponse {
  id: string
  reference_code: string
  client_name: string
  artist_name: string
  tattoo_description: string
  signed_at: string
}

// ── Waiver types (dashboard retrieval) ───────

export interface WaiverListItem {
  id: string
  guest_name: string | null
  guest_email: string | null
  artist_name: string | null
  reference_code: string | null
  status: string
  signed_at: string | null
  created_at: string
}

export interface WaiverListResponse {
  waivers: WaiverListItem[]
  total: number
}

export interface WaiverDetail {
  id: string
  reference_code: string
  status: string
  signed_at: string | null
  full_name: string
  date_of_birth: string
  phone: string
  email: string
  emergency_contact: string
  emergency_contact_address: string | null
  social_handle: string | null
  artist_name: string
  tattoo_description: string
  tattoo_placement: string
  pregnant: string
  blood_disorder: string
  skin_condition: string
  allergies: string | null
  medical_conditions: string | null
  quoted_price: string | null
  // Check-in intake (3 step) additions; absent on older waivers.
  street_address?: string | null
  session_date?: string | null
  intoxicated?: string | null
  blood_thinners?: string | null
  health_same_as_last_visit?: boolean | null
  checkin_source?: string | null
  photo_id_reused_from?: string | null
  signature_adopted_from?: string | null
  photo_id_url: string | null
  signature_url: string | null
  artist_signed_at: string | null
  artist_reviewed_by: string | null
  notes: string | null
  created_at: string
}

export interface CountersignResponse {
  status: string
  waiver_id: string
  new_status: string
  artist_name: string
  artist_signed_at: string
  reference_code: string | null
}

// ── Mailchimp / Email Marketing ──────────────────────

export interface MailchimpAudience {
  id: string
  name: string
  member_count: number
  list_id: string
}

export interface EmailCampaign {
  id: string
  audience_id: string
  subject: string
  preview_text: string
  from_name: string
  template_html: string
  status: 'draft' | 'scheduled' | 'sent'
  scheduled_at: string | null
  sent_at: string | null
  stats: {
    opens: number
    clicks: number
    unsubscribes: number
  } | null
  created_at: string
}

export interface EmailBlastRequest {
  audience: 'bttco' | 'ryozan-founder'
  subject: string
  preview_text?: string
  from_name: string
  content_html: string
  schedule_at?: string
}

export const mailchimp = {
  /** List configured Mailchimp audiences */
  getAudiences: () =>
    request<{ audiences: MailchimpAudience[] }>('/mailchimp/audiences'),

  /** Subscribe a user to an audience */
  subscribe: (data: { audience: string; email: string; name?: string; tags?: string[] }) =>
    request<{ status: string }>('/mailchimp/subscribe', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Unsubscribe a user */
  unsubscribe: (data: { audience: string; email: string }) =>
    request<{ status: string }>('/mailchimp/unsubscribe', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Send an email blast/campaign */
  sendBlast: (data: EmailBlastRequest) =>
    request<EmailCampaign>('/mailchimp/campaigns', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** List campaigns */
  listCampaigns: (params?: { audience?: string; status?: string }) => {
    const query = new URLSearchParams()
    if (params?.audience) query.set('audience', params.audience)
    if (params?.status) query.set('status', params.status)
    const qs = query.toString()
    return request<{ campaigns: EmailCampaign[]; total: number }>(
      `/mailchimp/campaigns${qs ? `?${qs}` : ''}`
    )
  },

  /** Get campaign stats */
  getCampaignStats: (id: string) =>
    request<EmailCampaign>(`/mailchimp/campaigns/${id}`),
}

// ── RYO / Economy ────────────────────────────────

export interface RyoCoinTransaction {
  id: string
  user_id: string
  amount: number
  type: 'earn' | 'spend' | 'bonus' | 'refund'
  source: string
  description: string
  balance_after: number
  created_at: string
}

export interface RyoCoinWallet {
  balance: number
  total_earned: number
  total_spent: number
  transactions: RyoCoinTransaction[]
  conversion_rate: number // RYO per $1 USD
}

export const ryoCoin = {
  /** Get wallet details for current user */
  getWallet: () =>
    request<RyoCoinWallet>('/bttcoin/wallet'),

  /** List transaction history */
  getTransactions: (params?: { type?: string; skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.type) query.set('type', params.type)
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<{ transactions: RyoCoinTransaction[]; total: number }>(
      `/bttcoin/transactions${qs ? `?${qs}` : ''}`
    )
  },

  /** Admin: award RYO to a user */
  award: (userId: string, amount: number, description: string) =>
    request<RyoCoinTransaction>('/bttcoin/award', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, amount, description }),
    }),

  /** Spend RYO (e.g., redeem for reward) */
  spend: (amount: number, description: string) =>
    request<RyoCoinTransaction>('/bttcoin/spend', {
      method: 'POST',
      body: JSON.stringify({ amount, description }),
    }),
}

export interface RewardGrant {
  id: string
  user_id: string
  event_type: string
  source_id: string
  xp_delta: number
  coin_delta: number
  description: string
  unlock_kind: 'art' | 'digital_merch' | 'loot_pack' | null
  unlock_slug: string | null
  metadata_json: Record<string, unknown>
  created_at: string
}

export interface RewardOffer {
  id: string
  offer_type: 'mini_game'
  game_slug: string
  reward_kind: 'art' | 'digital_merch' | 'loot_pack'
  reward_slug: string
  status: 'offered' | 'completed' | 'expired'
  expires_at: string | null
  completed_at: string | null
  created_at: string
}

export interface RewardInventory {
  grants: RewardGrant[]
  offers: RewardOffer[]
  total_unlocks: number
}

export const rewards = {
  recordPortalActivity: () =>
    request<{ awarded: boolean; grant: RewardGrant; new_quest_points: number; new_bttcoin_balance: number }>(
      '/rewards/portal-activity',
      { method: 'POST' },
    ),

  inventory: () => request<RewardInventory>('/rewards/inventory'),

  completeOffer: (offerId: string) =>
    request<{ offer: RewardOffer; grant: RewardGrant; new_quest_points: number; new_bttcoin_balance: number }>(
      `/rewards/offers/${offerId}/complete`,
      { method: 'POST' },
    ),
}

/* ── Quests ────────────────────────────────── */

export interface QuestDef {
  id: string
  slug: string
  title: string
  description: string
  category: string
  xp_reward: number
  coin_reward: number
  requirement: string
  target_count: number
  is_active: boolean
  sort_order: number
  created_at: string
}

export interface QuestWithProgress {
  quest: QuestDef
  progress: number
  completed: boolean
  claimed: boolean
  completed_at: string | null
  claimed_at: string | null
}

export interface QuestSummary {
  total_quests: number
  completed_count: number
  claimed_count: number
  total_xp_earned: number
  total_coin_earned: number
}

export interface QuestClaimResult {
  quest: QuestDef
  xp_awarded: number
  coin_awarded: number
  new_quest_points: number
  new_bttcoin_balance: number
}

export const quests = {
  list: (category?: string) => {
    const qs = category ? `?category=${category}` : ''
    return request<{ quests: QuestWithProgress[]; total: number }>(`/quests/${qs}`)
  },

  summary: () =>
    request<QuestSummary>('/quests/summary'),

  claim: (questId: string) =>
    request<QuestClaimResult>(`/quests/${questId}/claim`, { method: 'POST' }),

  updateProgress: (questId: string, progress: number) =>
    request<QuestWithProgress>(`/quests/${questId}/progress`, {
      method: 'POST',
      body: JSON.stringify({ progress }),
    }),
}

export const waiver = {
  /** Public: submit a signed tattoo consent waiver (no auth required) */
  submit: async (data: WaiverSubmitPayload): Promise<WaiverResponse> => {
    const res = await fetch(`${API_BASE}/waiver/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, err.detail || 'Waiver submission failed')
    }
    return res.json()
  },

  /** Public: upload photo ID to Drive before waiver submit */
  uploadPhotoId: async (file: File): Promise<{ photo_id_url: string }> => {
    const formData = new FormData()
    formData.append('file', file)
    const res = await fetch(`${API_BASE}/waiver/upload-id`, {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, err.detail || 'Photo ID upload failed')
    }
    return res.json()
  },

  /** Any auth user: list waivers I submitted (matched by email) */
  listMine: (params?: { skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<WaiverListResponse>(`/waiver/mine${qs ? `?${qs}` : ''}`)
  },

  /** Artist+: list submitted waivers */
  list: (params?: { skip?: number; limit?: number; status?: string; search?: string }) => {
    const query = new URLSearchParams()
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.status) query.set('status', params.status)
    if (params?.search) query.set('search', params.search)
    const qs = query.toString()
    return request<WaiverListResponse>(`/waiver/list${qs ? `?${qs}` : ''}`)
  },

  /** Artist+: get full waiver detail */
  get: (id: string) => request<WaiverDetail>(`/waiver/${id}`),

  /** Artist+: update waiver status */
  updateStatus: (id: string, status: string, notes?: string) =>
    request<{ status: string; new_status: string }>(`/waiver/${id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status, notes }),
    }),

  /** Artist+: counter-sign a waiver with post-session data */
  countersign: (id: string, data: {
    signature_data: string
    session_duration_minutes?: number
    client_condition_notes?: string
    notes?: string
  }) =>
    request<CountersignResponse>(`/waiver/${id}/countersign`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Artist+: export waiver as PDF */
  exportPdf: (id: string) =>
    request<{ pdf_url: string; reference_code: string }>(`/waiver/${id}/export-pdf`, {
      method: 'POST',
    }),
}

// ── Saved Artist Signature ──────────────────────────────────────────

export const artistSignature = {
  get: () => request<{ signature_url: string | null }>('/users/me/signature'),
  save: (signatureData: string) =>
    request<{ signature_url: string }>('/users/me/signature', {
      method: 'PUT',
      body: JSON.stringify({ signature_data: signatureData }),
    }),
}

// ── Gala Smart Upload ────────────────────────────────────

export const galaUpload = async (params: {
  file: File
  category: string
  note?: string
}): Promise<{ status: string; message: string; url: string; category: string }> => {
  const fd = new FormData()
  fd.append('file', params.file)
  fd.append('category', params.category)
  if (params.note) fd.append('note', params.note)
  const token = getToken()
  const res = await fetch(`${API_BASE}/gala/upload`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, err.detail || 'Upload failed')
  }
  return res.json()
}

export interface GalaChatHistoryMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface GalaChatComponent {
  type: 'booking-list' | 'client-card' | 'schedule-view' | 'stat-card' | 'waiver-status' | 'action-buttons' | 'web-results'
  data: Record<string, unknown>
}

export type GalaActionIntent =
  | 'schedule.read'
  | 'bookings.read'
  | 'bookings.pending.read'
  | 'bookings.stale.read'
  | 'business_stats.read'

export interface GalaChatResponse {
  response: string
  tool_calls: Array<Record<string, unknown>>
  memories_used: number
  components: GalaChatComponent[]
  model?: string | null
  model_provider?: string | null
}

export interface GalaSessionCloseResponse {
  agent_id: string
  compacted: boolean
  memories_written: number
}

export type ApexAgentId = 'bella' | 'gala'
export interface ApexAgentProfile {
  id: string
  agentId: ApexAgentId
  displayName: string
  version: number
  revision: number
  status: 'candidate' | 'gauntlet_passed' | 'evaluation_passed' | 'promoted' | 'retired'
  traits: {
    curiosity: number
    warmth: number
    initiative: number
    precision: number
    brevity: number
    questionBudget: number
    followUpDepth: number
    oneQuestionAtATime: boolean
  }
  directives: string[]
  boundaries: string[]
  inputRefs: string[]
  providerBinding: {
    provider: 'elevenlabs'
    agentRef: string
    channels: Array<'voice' | 'sms' | 'chat' | 'email'>
    syncStatus: 'not_configured' | 'pending' | 'in_sync' | 'drifted' | 'failed'
    syncedAt?: string
  }
}

export interface ApexAgentEvolutionResponse {
  ok: boolean
  profile?: ApexAgentProfile
  promotedProfile?: ApexAgentProfile
  profiles?: ApexAgentProfile[]
  response?: string
  changes?: string[]
  artifact?: { id: string; jobId: string; kind: string }
  result?: { passed: boolean; summary: string }
  provider_receipt?: string
  trials?: Array<{ trialId: string; kind: string; state: string; outcomeSummary: string }>
}

export interface AgentAccess {
  agent_id: string
  account_id: string
  environment: 'local_dev' | 'standard'
  /** False outside local development: saved policies are not enforced there, so the card is read-only. */
  editable: boolean
  revision: number
  tools: string[]
  scopes: string[]
  updated_at: string | null
  catalog: { id: string; description: string; scope: string }[]
}

export interface BenchPreviewRequest {
  scenario: string
  access?: Pick<AgentAccess, 'revision' | 'tools' | 'scopes'>
  profile_revision?: number
  traits?: ApexAgentProfile['traits']
  directives?: string[]
  lesson?: string
}

export interface OrchestraRouting {
  inventory?: {observedAt:string;sections:{id:string;label:string;runtimeId:string;runtimeClass:string;available:boolean;authState:string;endpoint:string|null;models:{id:string;label:string;provider:string;upstreamModel?:string;source:string;assignable:boolean;assignmentModelId?:string;assignmentReason:string;endpointProvider:string;modelMaker?:string}[];warnings:string[]}[];notes:string[]}
  orchestraId: 'orchestra:bttco' | 'orchestra:ryozan'
  scope: 'bttco_chat' | 'ryozan_chat'
  members: {actorRef:string;displayName:string;responsibility:string;assignment:{defaultModel:string|null;fallbacks:string[];revision:number;configuredBy:string;configuredAt:string}|null}[]
  models: {id:string;label:string;provider:string}[]
}
export interface BenchPreviewResponse {
  tool_context?: {current:{name:string;description:string;scope:string}[];draft:{name:string;description:string;scope:string}[];added:string[];removed:string[];source:string;executed:false}
  context_provenance?: {scenario:string;configuration:string;live_records:'not_provided';limitations:string[]}
  agent_id: string
  response: string
  scenario: string
  access_revision: number
  profile_revision: number | null
  model: string
  generated_at: string
  mode: 'illustrative_preview'
}

export const gala = {
  capabilities: () => request<{ai_configured:boolean;model_provider:string;agent_evolution:boolean;shared_model_routing:boolean;access_editing:boolean}>('/gala/capabilities'),
  getModelRouting: () => request<{ok:boolean;readOnly?:boolean;routing:OrchestraRouting}>('/gala/orchestra/model-routing'),
  saveModelRouting: (data:{actorRef:string;expectedRevision:number;defaultModel:string|null;fallbacks:string[]}) => request<{ok:boolean;actorRef:string;assignment:NonNullable<OrchestraRouting['members'][number]['assignment']>}>('/gala/orchestra/model-routing',{method:'POST',body:JSON.stringify(data)}),
  previewAgent: (agentId: string, data: BenchPreviewRequest) => request<BenchPreviewResponse>(`/gala/agents/${encodeURIComponent(agentId)}/preview`, { method: 'POST', body: JSON.stringify(data) }),
  getAccess: (agentId: string) => request<AgentAccess>(`/gala/agents/${encodeURIComponent(agentId)}/access`),
  saveAccess: (agentId: string, data: Pick<AgentAccess, 'revision' | 'tools' | 'scopes'>) => request<AgentAccess>(`/gala/agents/${encodeURIComponent(agentId)}/access`, { method: 'PUT', body: JSON.stringify(data) }),
  chat: (data: {
    message: string
    action_intent?: GalaActionIntent
    context?: Record<string, unknown>
    history?: GalaChatHistoryMessage[]
  }) =>
    request<GalaChatResponse>('/gala/chat', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  closeSession: (data: {
    agent_id: string
    turns: GalaChatHistoryMessage[]
    context?: Record<string, unknown>
    memory_consent: boolean
  }) =>
    request<GalaSessionCloseResponse>('/gala/session/close', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  evolution: {
    list: () => request<ApexAgentEvolutionResponse>('/gala/evolution'),
    get: (agentId: ApexAgentId) => request<ApexAgentEvolutionResponse>(`/gala/evolution/${agentId}`),
    dialogue: (agentId: ApexAgentId, data: { message: string; history?: GalaChatHistoryMessage[]; input_refs?: string[] }) =>
      request<ApexAgentEvolutionResponse>(`/gala/evolution/${agentId}/dialogue`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    saveProfile: (agentId: ApexAgentId, profile: Pick<ApexAgentProfile, 'traits' | 'directives' | 'boundaries' | 'inputRefs'>) =>
      request<ApexAgentEvolutionResponse>(`/gala/evolution/${agentId}/profile`, {
        method: 'POST',
        body: JSON.stringify({
          traits: profile.traits,
          directives: profile.directives,
          boundaries: profile.boundaries,
          input_refs: profile.inputRefs,
        }),
      }),
    runGauntlet: (agentId: ApexAgentId) =>
      request<ApexAgentEvolutionResponse>(`/gala/evolution/${agentId}/gauntlet`, { method: 'POST' }),
    recordEvaluation: (agentId: ApexAgentId, evaluationArtifactId: string) =>
      request<ApexAgentEvolutionResponse>(`/gala/evolution/${agentId}/evaluation`, {
        method: 'POST',
        body: JSON.stringify({ evaluation_artifact_id: evaluationArtifactId }),
      }),
    promote: (agentId: ApexAgentId) =>
      request<ApexAgentEvolutionResponse>(`/gala/evolution/${agentId}/promote`, { method: 'POST' }),
    promoteAndApply: (agentId: ApexAgentId) =>
      request<ApexAgentEvolutionResponse>(`/gala/evolution/${agentId}/promote-and-apply`, { method: 'POST' }),
    recoverLearningCaptures: () =>
      request<{ ok: boolean; requeued: number; outbox_ids: string[] }>(
        '/gala/evolution/recover-learning-captures',
        { method: 'POST' },
      ),
  },
}

// ── Quotes ───────────────────────────────────────────

export interface TimeSlot {
  date: string      // YYYY-MM-DD
  time: string      // HH:MM
  duration_minutes: number
}

export interface QuoteCreate {
  booking_id: string
  estimated_total_min?: number
  estimated_total_max?: number
  deposit_amount?: number
  available_slots?: TimeSlot[]
  artist_message?: string
  expires_in_days?: number
}

export interface QuoteResponse {
  id: string
  booking_id: string
  token: string
  estimated_total_min: number | null
  estimated_total_max: number | null
  deposit_amount: number
  available_slots: TimeSlot[]
  artist_message: string | null
  status: string
  sent_at: string
  viewed_at: string | null
  accepted_at: string | null
  expires_at: string | null
  selected_date: string | null
  selected_time: string | null
  selected_duration: number | null
}

export interface QuotePublicView {
  artist_name: string
  artist_avatar: string | null
  client_name: string
  description: string
  placement: string | null
  size_estimate: string | null
  estimated_total_min: number | null
  estimated_total_max: number | null
  deposit_amount: number
  available_slots: TimeSlot[]
  artist_message: string | null
  status: string
  expired: boolean
  reference_code: string | null
  selected_date: string | null
  selected_time: string | null
  selected_duration: number | null
}

export const quotes = {
  /** Artist: create a quote for a booking */
  create: (data: QuoteCreate) =>
    request<QuoteResponse>('/quotes/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Artist: get all quotes for a booking */
  forBooking: (bookingId: string) =>
    request<QuoteResponse[]>(`/quotes/booking/${bookingId}`),

  /** Public: view a quote by token (no auth) */
  getPublic: async (token: string): Promise<QuotePublicView> => {
    const res = await fetch(`${API_BASE}/quotes/public/${token}`)
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, err.detail || 'Quote not found')
    }
    return res.json()
  },

  /** Public: accept a quote and get Stripe Checkout URL (no auth) */
  accept: async (token: string, selectedSlotIndex: number): Promise<{ checkout_url: string }> => {
    const res = await fetch(`${API_BASE}/quotes/public/${token}/accept`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selected_slot_index: selectedSlotIndex }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new ApiError(res.status, err.detail || 'Failed to accept quote')
    }
    return res.json()
  },

  /** Public: send a reply message to the artist (no auth) */
  reply: async (token: string, message: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/quotes/public/${token}/reply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new ApiError(res.status, err.detail || 'Failed to send reply')
    }
  },
}

// ── Stripe Connect ───────────────────────────────────

export interface BookingApprovalDefaults {
  deposit_amount: number
  deposit_source: 'booking' | 'session_type' | 'duration_rule' | 'consultation'
  duration_minutes: number
  stripe_ready: boolean
  can_self_onboard: boolean
  artist_name: string
}

export interface BookingApproveInput {
  scheduled_date: string
  scheduled_time: string
  duration_minutes?: number
  deposit_amount: number
  artist_message?: string
  allow_conflict?: boolean
  allow_out_of_bounds?: boolean
  override_reason?: string
}

export interface BookingApprovalResult {
  booking: Booking
  status: string
  deposit_amount: number
  deposit_link_sent: boolean
  stripe_ready: boolean
  email_queued: boolean
  deposit_url?: string | null
  scheduled_date: string
  scheduled_time: string
}

export interface StripeConnectStatus {
  connected: boolean
  onboarding_complete: boolean
  routing_ready: boolean
  charges_enabled: boolean
  payouts_enabled: boolean
  provider_state: 'live' | 'not_connected' | 'unavailable'
  requirements_due: number
  account_id?: string
}

export type BoothRentPaymentMethod = 'cash' | 'one_time' | 'autodraft'
export type BoothRentInterval = 'biweekly' | 'month'

export interface BoothRentStatus {
  configured: boolean
  amount_cents: number | null
  installment_amount_cents: number | null
  interval: BoothRentInterval | null
  payment_method: BoothRentPaymentMethod
  status: 'not_configured' | 'pending' | 'checkout_pending' | 'active' | 'past_due' | 'canceled' | string
  subscription_id: string | null
  current_period_end: string | null
  next_due_date: string | null
  cancel_at_period_end: boolean
  commission_rate: number | null
}

export interface ArtistStripeInfo {
  id: string
  name: string
  role: 'artist' | 'founder'
  slug: string | null
  email: string
  commission_rate: number | null
  stripe_account_id: string | null
  stripe_onboarding_complete: boolean
  routing_ready: boolean
  charges_enabled: boolean
  payouts_enabled: boolean
  provider_state: 'live' | 'not_connected' | 'unavailable'
  requirements_due: number
  is_guest_artist: boolean
  booth_rent?: BoothRentStatus
}

export const stripeConnect = {
  /** Artist: start or resume Stripe Connect onboarding */
  onboard: () =>
    request<{ url: string; account_id: string }>('/stripe/connect/onboard', {
      method: 'POST',
    }),

  /** Artist: check own Stripe Connect status */
  status: () =>
    request<StripeConnectStatus>('/stripe/connect/status'),

  /** Admin: onboard a specific artist */
  adminOnboard: (userId: string) =>
    request<{ url: string; account_id: string }>(`/stripe/connect/onboard/${userId}`, {
      method: 'POST',
    }),

  /** Admin: list all artists with Stripe status */
  listArtists: () =>
    request<ArtistStripeInfo[]>('/stripe/connect/artists'),

  /** Artist: inspect the separately billed booth-rent obligation */
  boothRentStatus: () =>
    request<BoothRentStatus>('/stripe/booth-rent/status'),

  /** Founder/admin: configure an artist for booth rent or commission */
  configureBoothRent: (
    userId: string,
    data: {
      enabled: boolean
      amount_cents?: number
      interval?: BoothRentInterval
      payment_method?: BoothRentPaymentMethod
      commission_rate?: number
      is_guest_artist?: boolean
      next_due_date?: string
    },
  ) =>
    request<ArtistStripeInfo>(`/stripe/booth-rent/artists/${userId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  /** Founder/admin: record one cash booth-rent contribution */
  recordBoothRentCash: (userId: string, period: string) =>
    request<{
      already_recorded: boolean
      payment: { id: string; amount_cents: number; period: string; category: string; payment_method: 'cash' }
      artist: ArtistStripeInfo
    }>(`/stripe/booth-rent/artists/${userId}/cash-payment`, {
      method: 'POST',
      body: JSON.stringify({ period }),
    }),

  /** Artist: start Stripe-hosted one-time or automatic booth-rent checkout */
  startBoothRent: () =>
    request<{ url: string; session_id: string; payment_method: BoothRentPaymentMethod }>('/stripe/booth-rent/checkout', {
      method: 'POST',
    }),

  /** Artist: manage payment method and cancellation in Stripe */
  manageBoothRent: () =>
    request<{ url: string }>('/stripe/booth-rent/portal', {
      method: 'POST',
    }),
}

// ── POS (Point of Sale) ──────────────────────────────

export interface POSChargeRequest {
  amount_cents: number
  booking_id?: string
  client_name?: string
  client_email?: string
  description?: string
}

export interface POSChargeResponse {
  session_id: string
  checkout_url: string
  amount_cents: number
  description: string
}

export interface POSInvoiceRequest extends POSChargeRequest {
  client_email: string
}

export interface POSInvoiceResponse extends POSChargeResponse {
  recipient_email: string
  email_sent: boolean
}

export interface POSStatusResponse {
  session_id: string
  status: 'open' | 'complete' | 'expired'
  payment_status: 'unpaid' | 'paid' | 'no_payment_required'
  amount_cents: number
}

export interface POSRecordRequest {
  amount_cents: number
  commission_base_cents?: number | null
  payment_method: 'cash' | 'card'
  processor?: string | null
  booking_id?: string | null
  client_name?: string | null
  description?: string | null
  notes?: string | null
  stripe_session_id?: string | null
}

export interface POSTransaction {
  id: string
  artist_id: string
  booking_id: string | null
  amount_cents: number
  commission_base_cents: number
  price_adjustment_cents: number
  payment_method: string
  processor: string | null
  client_name: string | null
  description: string | null
  stripe_session_id: string | null
  status: string
  notes: string | null
  created_at: string
  created_by: string
}

export interface POSArtistSummary {
  artist_id: string
  artist_name: string
  commission_rate: number | null
  transaction_count: number
  gross_cents: number
  cash_cents: number
  card_cents: number
  estimated_artist_share_cents: number
  estimated_shop_share_cents: number
  estimated_tax_reserve_cents: number
}

export interface POSIntegrationSummary {
  period_start: string
  period_end: string
  transaction_count: number
  gross_cents: number
  cash_cents: number
  card_cents: number
  stripe_cents: number
  manual_cents: number
  estimated_tax_reserve_cents: number
  estimated_artist_share_cents: number
  estimated_shop_share_cents: number
  by_artist: POSArtistSummary[]
  recent_transactions: POSTransaction[]
}

export interface PlaidTransactionsSyncResponse {
  added: unknown[]
  modified: unknown[]
  removed: unknown[]
  next_cursor: string
  has_more: boolean
  persisted: boolean
}

export interface EconIntegrationsStatus {
  plaid: {
    configured: boolean
    connected: boolean
    ready_to_sync: boolean
    missing: string[]
    setup: EconProviderSetup
    last_synced_at?: string | null
    latest_sync?: EconSyncRun | null
    detail: string
  }
  clover: {
    configured: boolean
    oauth_ready: boolean
    environment: string
    merchant_configured: boolean
    ready_to_sync: boolean
    missing: string[]
    oauth_missing: string[]
    setup: EconProviderSetup
    last_synced_at?: string | null
    latest_sync?: EconSyncRun | null
    detail: string
  }
  mercury: {
    configured: boolean
    api_base: string
    ready_to_sync: boolean
    missing: string[]
    setup: EconProviderSetup
    accounts: MercuryAccountSummary[]
    last_synced_at?: string | null
    latest_sync?: EconSyncRun | null
    detail: string
  }
  payroll_tax: {
    configured: boolean
    detail: string
  }
}

export interface EconProviderSetup {
  required_env: string[]
  optional_env?: string[]
  runtime_requirements?: string[]
}

export interface MercuryAccountSummary {
  id: string | null
  name: string | null
  type: string | null
  status: string | null
}

export interface EconSyncRun {
  id: string
  provider: string
  status: string
  started_at: string | null
  finished_at: string | null
  added_count: number
  modified_count: number
  removed_count: number
  error: string | null
  metadata: Record<string, unknown> | null
}

export interface EconLedgerEntry {
  id: string
  provider: string
  external_id: string
  entry_type: string
  status: string
  amount_cents: number
  currency: string
  occurred_at: string | null
  description: string | null
  counterparty: string | null
  category: string | null
  payroll_tax_bucket: string | null
}

export interface EconLedgerBucket {
  provider: string
  bucket: string
  count: number
  amount_cents: number
}

export interface EconLedgerSummary {
  buckets: EconLedgerBucket[]
  contractor_contributions_cents: number
  contractor_contribution_count: number
}

export interface EconPayrollTaxSummary {
  income_cents: number
  expense_cents: number
  deductible_expense_cents: number
  net_taxable_cents: number
  estimated_tax_reserve_cents: number
  uncategorized_cents: number
  prep_status: 'ready' | 'needs_review'
  buckets: EconLedgerBucket[]
}

export interface EconSyncProviderResult {
  provider: string
  status: 'success' | 'skipped' | 'error'
  detail: string
  updates: number
  metadata: Record<string, unknown>
}

export interface EconSyncResponse {
  status: 'success' | 'partial' | 'skipped' | 'error'
  results: EconSyncProviderResult[]
}

export interface EconDiagnosticResult {
  provider: string
  status: 'pass' | 'partial' | 'blocked' | 'fail' | 'not_run'
  detail: string
  missing: string[]
  metadata: Record<string, unknown>
  checked_at: string
}

export interface EconDiagnosticsResponse {
  status: 'pass' | 'partial' | 'blocked' | 'fail' | 'not_run'
  live: boolean
  results: EconDiagnosticResult[]
}

export const pos = {
  /** Create a POS charge. Returns checkout URL for QR code. */
  charge: (data: POSChargeRequest) =>
    request<POSChargeResponse>('/pos/charge', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Email the same scoped Stripe Checkout link to a selected collector. */
  invoice: (data: POSInvoiceRequest) =>
    request<POSInvoiceResponse>('/pos/invoice', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** Poll payment status of a POS session */
  status: (sessionId: string) =>
    request<POSStatusResponse>(`/pos/status/${sessionId}`),

  /** Record a POS transaction (cash or card) */
  record: (data: POSRecordRequest) =>
    request<POSTransaction>('/pos/record', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /** List POS transactions, optionally filtered by date */
  transactions: (params?: { date?: string }) => {
    const qs = new URLSearchParams()
    if (params?.date) qs.set('date', params.date)
    return request<POSTransaction[]>(`/pos/transactions${qs.toString() ? `?${qs}` : ''}`)
  },

  /** Hustle E-con finance summary from recorded POS transactions */
  summary: (params?: { period_start?: string; period_end?: string }) => {
    const qs = new URLSearchParams()
    if (params?.period_start) qs.set('period_start', params.period_start)
    if (params?.period_end) qs.set('period_end', params.period_end)
    return request<POSIntegrationSummary>(`/pos/summary${qs.toString() ? `?${qs}` : ''}`)
  },
}

// ── Hustle E-con integrations ───────────────────────

export const econ = {
  status: () =>
    request<EconIntegrationsStatus>('/econ/integrations/status'),

  diagnostics: (params?: { providers?: string[]; live?: boolean }) => {
    const qs = new URLSearchParams()
    params?.providers?.forEach((provider) => qs.append('providers', provider))
    if (typeof params?.live === 'boolean') qs.set('live', String(params.live))
    return request<EconDiagnosticsResponse>(`/econ/integrations/diagnostics${qs.toString() ? `?${qs}` : ''}`)
  },

  sync: (data?: { providers?: string[]; limit?: number; persist?: boolean; mercury_account_id?: string }) =>
    request<EconSyncResponse>('/econ/sync', {
      method: 'POST',
      body: JSON.stringify({
        providers: data?.providers,
        limit: data?.limit ?? 25,
        persist: data?.persist ?? true,
        mercury_account_id: data?.mercury_account_id,
      }),
    }),

  syncRuns: (params?: { provider?: string; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params?.provider) qs.set('provider', params.provider)
    if (params?.limit) qs.set('limit', String(params.limit))
    return request<EconSyncRun[]>(`/econ/sync-runs${qs.toString() ? `?${qs}` : ''}`)
  },

  cloverOAuthUrl: () =>
    request<{ authorization_url: string }>('/econ/clover/oauth-url'),

  cloverOrders: (params?: { limit?: number }) => {
    const qs = new URLSearchParams()
    if (params?.limit) qs.set('limit', String(params.limit))
    return request<unknown>(`/econ/clover/orders${qs.toString() ? `?${qs}` : ''}`)
  },

  mercuryAccounts: () =>
    request<unknown>('/econ/mercury/accounts'),

  mercuryTransactions: (params?: {
    limit?: number
    start?: string
    end?: string
    account_id?: string
    search?: string
    status?: string
    order?: 'asc' | 'desc'
  }) => {
    const qs = new URLSearchParams()
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.start) qs.set('start', params.start)
    if (params?.end) qs.set('end', params.end)
    if (params?.account_id) qs.set('account_id', params.account_id)
    if (params?.search) qs.set('search', params.search)
    if (params?.status) qs.set('status', params.status)
    if (params?.order) qs.set('order', params.order)
    return request<unknown>(`/econ/mercury/transactions${qs.toString() ? `?${qs}` : ''}`)
  },

  ledger: (params?: { provider?: string; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params?.provider) qs.set('provider', params.provider)
    if (params?.limit) qs.set('limit', String(params.limit))
    return request<EconLedgerEntry[]>(`/econ/ledger${qs.toString() ? `?${qs}` : ''}`)
  },

  /** Omit the period for all time; Hustl passes the same window it gives posApi.summary. */
  ledgerSummary: (params?: { period_start?: string; period_end?: string }) => {
    const qs = new URLSearchParams()
    if (params?.period_start) qs.set('period_start', params.period_start)
    if (params?.period_end) qs.set('period_end', params.period_end)
    return request<EconLedgerSummary>(`/econ/ledger/summary${qs.toString() ? `?${qs}` : ''}`)
  },

  payrollTaxSummary: (params?: { period_start?: string; period_end?: string }) => {
    const qs = new URLSearchParams()
    if (params?.period_start) qs.set('period_start', params.period_start)
    if (params?.period_end) qs.set('period_end', params.period_end)
    return request<EconPayrollTaxSummary>(`/econ/payroll-tax/summary${qs.toString() ? `?${qs}` : ''}`)
  },
}

// ── Dashboard Summary ────────────────────────────────

export interface DashboardSummary {
  user: {
    name: string
    role: string
    bttcoin_balance: number
    quest_points: number
    avatar_url: string | null
  }
  stripe: {
    connected: boolean
    available_balance: number | null  // cents; null when the provider read is unavailable
    pending_balance: number | null
    currency: string
    next_payout_date: string | null
    account_scope: 'platform' | 'artist'
  }
  messages: { unread_count: number }
  today: {
    bookings: Array<{
      id: string
      client_name: string
      client_email: string | null
      scheduled_date: string | null
      scheduled_time: string | null
      description: string
      status: string
      deposit_amount: number | null
    }>
    booking_count: number
  }
  quests: {
    active: Array<{
      id: string
      title: string
      description: string
      xp_reward: number
      coin_reward: number
      target_count: number
      current_progress: number
      completed: boolean
    }>
    total_active: number
    completed_count: number
  }
  briefing: {
    generated_at: string
    summary: string
  }
}

export interface DashboardDebrief {
  generated_at: string
  period_start: string
  period_end: string
  summary: string
  stats: {
    new_bookings: number
    completed_sessions: number
    new_waivers: number
    new_accounts: number
    pending_actions: number
    revenue_today: number | null
  }
  components: unknown[]
  action_items: unknown[]
}

export const dashboard = {
  /** Artist/founder dashboard summary */
  artistSummary: () =>
    request<DashboardSummary>('/dashboard/artist-summary'),

  /** AI-generated daily business debrief */
  debrief: () =>
    request<DashboardDebrief>('/dashboard/debrief'),

  /** Founder daily digest */
  dailyDigest: () =>
    request<{
      date: string
      summary: string
      bookings: { new_today: number; by_status: Record<string, number> }
      sessions: { reference_code: string; client_name: string; artist_name: string; scheduled_time: string; description: string; status: string }[]
      waivers_submitted: number
      messages_today: number
      unread_notifications: number
      total_clients: number
      revenue_today: number
    }>('/dashboard/daily-digest'),
}

// ── Broadcasts (Herald Feed) ────────────────────────

export interface BroadcastItem {
  id: string
  author_id: string
  author_name: string | null
  title: string
  body: string
  broadcast_type: string
  pinned: boolean
  audience: string
  created_at: string
}

export interface BroadcastPublishResult {
  destination: string
  ok: boolean
  post_id: string | null
  error: string | null
}

export interface BroadcastPublishResponse {
  broadcast_id: string
  results: BroadcastPublishResult[]
}

export interface FeedbackItem {
  id: string
  user_id: string
  user_name: string | null
  user_email: string | null
  message: string
  status: string
  priority: number
  created_at: string
}

export const broadcasts = {
  list: (limit = 20) =>
    request<{ broadcasts: BroadcastItem[] }>(`/broadcasts/?limit=${limit}`),

  create: (data: { title: string; body: string; broadcast_type?: string; pinned?: boolean; audience?: string }) =>
    request<BroadcastItem>('/broadcasts/', { method: 'POST', body: JSON.stringify(data) }),

  publish: (id: string, data: { destinations: string[]; image_url?: string }) =>
    request<BroadcastPublishResponse>(`/broadcasts/${id}/publish`, { method: 'POST', body: JSON.stringify(data) }),

  submitFeedback: (message: string) =>
    request<FeedbackItem>('/broadcasts/feedback', { method: 'POST', body: JSON.stringify({ message }) }),

  listFeedback: (status?: string, limit = 50) =>
    request<{ feedback: FeedbackItem[] }>(`/broadcasts/feedback?limit=${limit}${status ? `&status=${status}` : ''}`),

  updateFeedback: (id: string, data: { status?: string; priority?: number }) =>
    request<{ ok: boolean }>(`/broadcasts/feedback/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
}

// ── Herald artist reposts (Ark Social Modz over the Pack bridge) ──

export type RepostStatus = 'pending' | 'approved' | 'rejected' | 'published' | 'failed'

export interface RepostDraft {
  id: string
  status: RepostStatus
  artist: { name: string; handle: string }
  source: {
    mediaId: string
    mediaType: string
    permalink?: string
    caption?: string
    postedAt?: string
    thumbnailUrl?: string
  }
  media: Array<{ url: string; type: 'IMAGE' | 'VIDEO' }>
  draftCaption: string
  caption: string
  hashtags: string[]
  scheduledFor?: string
  review?: { decision: 'approve' | 'reject'; by: string; at: string; note?: string }
  publish?: { attemptedAt: string; by: string; commandId?: string; mediaId?: string; error?: string }
}

export interface RepostScanResult {
  created: RepostDraft[]
  skipped: Array<{ handle: string; mediaId: string; reason: string }>
  errors: Array<{ handle: string; error: string }>
}

export const socialReposts = {
  list: (status?: RepostStatus) =>
    request<{ drafts: RepostDraft[]; roster: Array<{ name: string; handle: string }> }>(
      `/herald/reposts${status ? `?status=${status}` : ''}`,
    ),

  location: () =>
    request<{ location: { locationId?: string; name?: string; singleLineAddress?: string } }>(
      '/herald/reposts/location',
    ),

  scan: (handles?: string[]) =>
    request<RepostScanResult>('/herald/reposts/scan', {
      method: 'POST',
      body: JSON.stringify(handles?.length ? { handles } : {}),
    }),

  edit: (id: string, data: { caption?: string; hashtags?: string[]; scheduled_for?: string; clear_schedule?: boolean }) =>
    request<{ draft: RepostDraft }>(`/herald/reposts/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  review: (id: string, decision: 'approve' | 'reject', note?: string) =>
    request<{ draft: RepostDraft }>(`/herald/reposts/${id}/review`, {
      method: 'POST',
      body: JSON.stringify({ decision, ...(note ? { note } : {}) }),
    }),

  publish: (id: string) =>
    request<{ draft: RepostDraft }>(`/herald/reposts/${id}/publish`, { method: 'POST', body: JSON.stringify({}) }),
}

// ── Email Connect ────────────────────────────────────

export interface EmailStatus {
  handle: string | null
  bttco_email: string | null
  forward_to: string | null
  verified: boolean
  connected: boolean
}

export interface HandleCheck {
  handle: string
  available: boolean
  bttco_email: string
}

export const emailConnect = {
  status: () =>
    request<EmailStatus>('/email/status'),

  checkHandle: (handle: string) =>
    request<HandleCheck>(`/email/check/${encodeURIComponent(handle)}`),

  connect: (handle: string, forward_to: string) =>
    request<EmailStatus>('/email/connect', {
      method: 'POST',
      body: JSON.stringify({ handle, forward_to }),
    }),

  /** Resend verification by re-submitting the same handle + forward_to */
  resend: (handle: string, forward_to: string) =>
    request<EmailStatus>('/email/connect', {
      method: 'POST',
      body: JSON.stringify({ handle, forward_to }),
    }),

  /** Release the current email handle (clears route on backend) */
  release: () =>
    request<void>('/email/release', { method: 'DELETE' }),
}

// ── Archive (Vault) ──────────────────────────────────

export interface ArchiveRecord {
  id: string
  record_type: 'waiver' | 'booking' | 'receipt'
  client_name: string
  client_email: string | null
  artist_name: string | null
  date: string
  status: string
  summary: string
  original_id: string
}

export interface ArchiveListResponse {
  records: ArchiveRecord[]
  total: number
}

export const archive = {
  list: (params?: {
    type?: string
    status?: string
    search?: string
    skip?: number
    limit?: number
    sort_by?: string
    sort_dir?: string
  }) => {
    const query = new URLSearchParams()
    if (params?.type) query.set('type', params.type)
    if (params?.status) query.set('status', params.status)
    if (params?.search) query.set('search', params.search)
    if (params?.skip) query.set('skip', String(params.skip))
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.sort_by) query.set('sort_by', params.sort_by)
    if (params?.sort_dir) query.set('sort_dir', params.sort_dir)
    const qs = query.toString()
    return request<ArchiveListResponse>(`/archive/${qs ? `?${qs}` : ''}`)
  },

  detail: (id: string) =>
    request<ArchiveDetail>(`/archive/${id}/detail`),
}

export interface ArchiveDetail {
  id: string
  type: string
  title: string
  status: string
  client_name: string | null
  client_email: string | null
  artist_name: string | null
  signed_at: string | null
  created_at: string | null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>
}

// ── Google Auth / Calendar ───────────────────────────

export interface CalendarEvent {
  id: string
  summary: string
  description: string | null
  start: string   // ISO datetime
  end: string     // ISO datetime
  location: string | null
  status: string
  html_link: string | null
}

export interface BusyBlock {
  start: string   // ISO datetime
  end: string     // ISO datetime
}

export interface SchedulerContractAction {
  name: string
  method: string
  path: string
  auth: string
  purpose: string
  schema_ref: string
}

export interface SchedulerContractAppointmentType {
  id: string
  label: string
  size: string
  placements: string[] | null
  duration_minutes: number
  buffer_minutes: number | null
  deposit_amount: number
  hourly_rate: number | null
  deposit_required: boolean
  sms_reminder_required: boolean
  minimum_notice_hours: number
  max_advance_days: number
  allow_same_day: boolean
  price_min: number | null
  price_max: number | null
}

export interface SchedulerContract {
  version: string
  artist_slug: string
  timezone: string
  rules: Record<string, number | boolean>
  blocked_reason_labels: Record<string, string>
  booky: {
    consultation_automation_available: boolean
    authenticated_collector_canary_required: true
    consent_required: boolean
    consent_policy_version: string
    supported_booking_kind: 'consultation'
    outcome_schema_ref: 'BookingOutcomeEnvelope'
  }
  appointment_types: SchedulerContractAppointmentType[]
  actions: SchedulerContractAction[]
}

export const googleAuth = {
  /** Get the OAuth connect URL to redirect the user to */
  getConnectUrl: () =>
    request<{ authorization_url: string }>('/auth/google/connect'),

  /** Check whether the current user has a connected Google account */
  getStatus: () =>
    request<{
      connected: boolean
      managed: boolean
      effective_user_id?: string | null
      email?: string | null
      source?: 'self' | 'founder_fallback' | 'none' | string | null
      healthy: boolean
      calendar_accessible: boolean
      calendar_id?: string | null
      calendar_summary?: string | null
      reason?: string | null
      reconnect_required: boolean
    }>('/auth/google/status'),

  /** Disconnect the linked Google account */
  disconnect: () =>
    request<void>('/auth/google/disconnect', { method: 'DELETE' }),

  /** Google URL that links a Google login to the signed-in account */
  getSigninLinkUrl: () =>
    request<{ authorization_url: string }>('/auth/google/link'),

  /** Whether a Google login is linked to the signed-in account */
  getSigninLinkStatus: () =>
    request<{ linked: boolean }>('/auth/google/link/status'),
}

export type IntegrationId = 'google_calendar' | 'google_signin_link' | 'pinterest' | 'plaid' | 'stripe_connect' | 'push' | 'email_forwarding' | 'shop_email' | 'shop_texts'

export interface IntegrationOption {
  id: IntegrationId
  /** The server has this provider's credentials set. */
  configured: boolean
  /** The caller's role may start the connect flow; null when signed out. */
  allowed: boolean | null
}

export interface IntegrationOptionsResponse {
  integrations: IntegrationOption[]
}

export interface ShopEmailRecord {
  record?: string
  type?: string
  name?: string
  value?: string
  priority?: number
  ttl?: string
  status?: string
}

/** GET /connections/email: where the shop's mail goes out from. Never carries a key. */
export interface ShopEmailConnection {
  platform_ready: boolean
  platform_sender: string | null
  sender_name: string | null
  domain: string | null
  from_address: string | null
  status: string | null
  records: ShopEmailRecord[]
  checked_at: string | null
  sending_from: string | null
  demo: boolean
}

/** GET /connections/texts: the shop's texting line. Never carries a token. */
export interface ShopTextsConnection {
  platform_ready: boolean
  mode: 'platform' | 'own' | null
  account_sid: string | null
  phone_number: string | null
  verification_status: string | null
  verification_submitted_at: string | null
  verification_reason: string | null
  sending_ready: boolean
  checked_at: string | null
  demo: boolean
  use_cases: string[]
  opt_in_types: string[]
  volumes: string[]
}

export interface ShopTextsVerification {
  business_name: string
  business_type: 'PRIVATE_PROFIT' | 'SOLE_PROPRIETOR' | 'NON_PROFIT'
  ein?: string
  website: string
  notification_email: string
  contact_first_name: string
  contact_last_name: string
  contact_phone: string
  street: string
  city: string
  state: string
  postal_code: string
  use_case: string
  use_case_summary: string
  message_sample: string
  opt_in_type: string
  opt_in_image_url: string
  volume: string
}

export const shopConnections = {
  email: () => request<ShopEmailConnection>('/connections/email'),
  addEmailDomain: (domain: string, fromLocal: string) =>
    request<ShopEmailConnection>('/connections/email/domain', {
      method: 'POST',
      body: JSON.stringify({ domain, from_local: fromLocal }),
    }),
  checkEmailDomain: () =>
    request<ShopEmailConnection>('/connections/email/domain/check', { method: 'POST' }),
  removeEmailDomain: () =>
    request<ShopEmailConnection>('/connections/email/domain', { method: 'DELETE' }),
  texts: () => request<ShopTextsConnection>('/connections/texts'),
  startPlatformTexts: () => request<ShopTextsConnection>('/connections/texts/platform', { method: 'POST' }),
  findTextsNumbers: (areaCode?: string) =>
    request<{ numbers: { phone_number: string; friendly_name?: string }[] }>(
      `/connections/texts/numbers${areaCode ? `?area_code=${encodeURIComponent(areaCode)}` : ''}`,
    ),
  buyTextsNumber: (phoneNumber: string) =>
    request<ShopTextsConnection>('/connections/texts/number', {
      method: 'POST',
      body: JSON.stringify({ phone_number: phoneNumber }),
    }),
  submitTextsVerification: (form: ShopTextsVerification) =>
    request<ShopTextsConnection>('/connections/texts/verification', { method: 'POST', body: JSON.stringify(form) }),
  checkTexts: () => request<ShopTextsConnection>('/connections/texts/check', { method: 'POST' }),
  connectOwnTexts: (accountSid: string, authToken: string, phoneNumber: string) =>
    request<ShopTextsConnection>('/connections/texts/own', {
      method: 'POST',
      body: JSON.stringify({ account_sid: accountSid, auth_token: authToken, phone_number: phoneNumber }),
    }),
  disconnectTexts: () => request<ShopTextsConnection>('/connections/texts', { method: 'DELETE' }),
}

export const integrations = {
  /** Which third-party connections this server offers the current user (optional auth) */
  options: () =>
    request<IntegrationOptionsResponse>('/integrations/options'),
}

/* ── Mods: the Mods inventory (Ark Mold contract) and the Ark Matrix Mod ── */

export type ModTier = 'unknown' | 'planned' | 'scaffolded' | 'implemented' | 'validated' | 'integrated' | 'runtime-proven'

export interface ModMoldTab {
  id: string
  label: string
  prefix: string
}

export interface ModEntry {
  code: string
  kind: 'mod'
  group: string
  tab: string
  name: string
  alt: string
  status: ModTier
  tier: ModTier
  source: string
  /** null when the Mod has no setting to check. */
  connected: boolean | null
  route: string | null
  gaps: [string, string][]
}

export interface ModsInventory {
  mold: { id: string; itemType: string; title: string; eyebrow: string; headline: string; agent: string; tabs: ModMoldTab[]; source: string }
  feed: { status: 'live' | 'unavailable'; entries: ModEntry[]; warnings: string[] }
}

export interface MatrixModTool {
  capabilityId: string
  name: string
  risk: 'read' | 'write'
  description: string
}

export interface MatrixModMatrix {
  slug: string
  matrixRef: string
  label: string
  covers: 'one' | 'kind'
  engine: { name: string; starts: string; server: 'own' | 'shared' | 'none'; serverDetail: string }
  scopes: { read: string; write: string | null }
  writeRefusal: string | null
  tools: MatrixModTool[]
}

export interface MatrixModRun {
  runId: string
  state: string
  scenario?: { id: string; title: string }
  change?: string
  startedAt?: string
}

export interface MatrixModView {
  connected: boolean
  reason?: string
  matrices: MatrixModMatrix[]
  runs: MatrixModRun[] | null
  runsNote?: string | null
}

export const mods = {
  /** The shop's Mods inventory (admin and founder) */
  inventory: () => request<ModsInventory>('/mods'),
  /** Ark's Matrix Mod: Eden Matrices with their engines, scopes and tools, plus recent runs */
  matrix: () => request<MatrixModView>('/mods/matrix'),
}

export interface PinterestBoard {
  id: string
  name: string
  pin_count?: number
  privacy?: string
}

export const pinterestAuth = {
  /** Get the OAuth connect URL to redirect the user to (collectors + staff) */
  getConnectUrl: () =>
    request<{ authorize_url: string }>('/auth/pinterest/connect'),

  /** Whether the current user has a connected Pinterest account */
  getStatus: () =>
    request<{ connected: boolean; username?: string | null }>('/auth/pinterest/status'),

  /** Disconnect the linked Pinterest account */
  disconnect: () =>
    request<void>('/auth/pinterest/disconnect', { method: 'DELETE' }),

  /** List the connected account's own boards */
  listBoards: () =>
    request<{ items: PinterestBoard[]; bookmark?: string | null }>('/auth/pinterest/boards'),

  /** Pull a collector board's pins into a booking's reference set */
  addBoardToBooking: (bookingId: string, boardId: string) =>
    request<{ booking_id: string; board_id: string; pins_pulled: number; total_references: number }>(
      `/auth/pinterest/booking-references/${bookingId}/${boardId}`,
      { method: 'POST' },
    ),
}

export interface PlaidBalanceAccount {
  account_id: string
  name: string
  official_name: string | null
  type: string
  subtype: string | null
  mask: string | null
  balances: {
    available: number | null
    current: number | null
    limit: number | null
    iso_currency_code: string | null
  }
}

export interface PlaidBalanceResponse {
  accounts: PlaidBalanceAccount[]
}

export interface PlaidStatus {
  connected: boolean
}

export interface PlaidLinkTokenResponse {
  link_token: string
  expiration: string
}

export interface PlaidExchangeResponse {
  connected: boolean
  item_id: string
}

export const plaid = {
  status: () =>
    request<PlaidStatus>('/plaid/status'),

  balance: () =>
    request<PlaidBalanceResponse>('/plaid/balance'),

  createLinkToken: () =>
    request<PlaidLinkTokenResponse>('/plaid/link-token', { method: 'POST' }),

  exchangePublicToken: (publicToken: string) =>
    request<PlaidExchangeResponse>('/plaid/exchange-token', {
      method: 'POST',
      body: JSON.stringify({ public_token: publicToken }),
    }),

  syncTransactions: (data?: { cursor?: string | null; count?: number }) =>
    request<PlaidTransactionsSyncResponse>('/plaid/transactions/sync', {
      method: 'POST',
      body: JSON.stringify({ cursor: data?.cursor ?? null, count: data?.count ?? 100 }),
    }),

  disconnect: () =>
    request<void>('/plaid/disconnect', { method: 'DELETE' }),
}

export const calendar = {
  /** Get upcoming calendar events for the next N days (current user's calendar). */
  getUpcoming: (days?: number) =>
    request<{ events: CalendarEvent[] }>(`/calendar/upcoming?days=${days || 7}`),

  /**
   * Public: machine-readable scheduling contract for appointment type,
   * slot hold, blocked reason, and booking submission integration.
   */
  getSchedulerContract: (artistSlug: string) =>
    request<SchedulerContract>(`/calendar/scheduler-contract/${encodeURIComponent(artistSlug)}`),

  /**
   * Get busy blocks for the CURRENT user's calendar between two ISO timestamps.
   * Use getArtistAvailability() instead when you need another artist's schedule
   * (e.g. an assistant responding to a booking assigned to a different artist).
   */
  getAvailability: (start: string, end: string) =>
    request<{ busy: BusyBlock[] }>(`/calendar/availability?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),

  /**
   * Public: get busy blocks for a specific artist by slug. Returns
   * `calendar_connected: false` with an empty busy list if the artist has
   * never linked their Google account.
   */
  getArtistAvailability: (
    artistSlug: string,
    start: string,
    end: string,
  ) =>
    request<{ start: string; end: string; busy: BusyBlock[]; calendar_connected: boolean }>(
      `/calendar/availability/${encodeURIComponent(artistSlug)}?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    ),

  getArtistSlots: (
    artistSlug: string,
    params: {
      start: string
      end: string
      sessionTypeId?: string
      size?: string
      placement?: string
      durationMinutes?: number
      slotIntervalMinutes?: number
      bufferMinutes?: number
      minimumNoticeHours?: number
      maxAdvanceDays?: number
      allowSameDay?: boolean
      limit?: number
      includeBlocked?: boolean
      blockedLimit?: number
      consultation?: boolean
    },
  ) => {
    const query = new URLSearchParams({
      start: params.start,
      end: params.end,
    })
    if (params.sessionTypeId) query.set('session_type_id', params.sessionTypeId)
    if (params.size) query.set('size', params.size)
    if (params.placement) query.set('placement', params.placement)
    if (params.durationMinutes) query.set('duration_minutes', String(params.durationMinutes))
    if (params.slotIntervalMinutes) query.set('slot_interval_minutes', String(params.slotIntervalMinutes))
    if (params.bufferMinutes !== undefined) query.set('buffer_minutes', String(params.bufferMinutes))
    if (params.minimumNoticeHours !== undefined) query.set('minimum_notice_hours', String(params.minimumNoticeHours))
    if (params.maxAdvanceDays) query.set('max_advance_days', String(params.maxAdvanceDays))
    if (params.allowSameDay) query.set('allow_same_day', 'true')
    if (params.limit) query.set('limit', String(params.limit))
    if (params.includeBlocked) query.set('include_blocked', 'true')
    if (params.blockedLimit) query.set('blocked_limit', String(params.blockedLimit))
    if (params.consultation) query.set('consultation', 'true')
    return request<{
      artist_slug: string
      timezone: string
      calendar_connected: boolean
      duration_minutes: number
      slot_interval_minutes: number
      buffer_minutes: number
      minimum_notice_hours: number
      max_advance_days: number
      slots: Array<{
        date: string
        time: string
        start: string
        end: string
        label: string
        duration_minutes: number
        source: string
        confidence: string
      }>
      blocked_slots?: Array<{
        date: string
        time: string
        start: string
        end: string
        label: string
        duration_minutes: number
        reasons: string[]
      }> | null
    }>(`/calendar/slots/${encodeURIComponent(artistSlug)}?${query.toString()}`)
  },

  holdArtistSlot: (data: {
    artist_slug: string
    date: string
    time: string
    session_type_id?: string
    size?: string
    placement?: string
    duration_minutes?: number
    buffer_minutes?: number
    minimum_notice_hours?: number
    max_advance_days?: number
    allow_same_day?: boolean
    inquiry_id?: string
    guest_email?: string
    booking_kind?: BookingKind
    consultation?: boolean
  }) =>
    request<{
      hold_token: string
      artist_slug: string
      date: string
      time: string
      duration_minutes: number
      expires_at: string
    }>('/calendar/slot-holds', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  releaseArtistSlotHold: (holdToken: string) =>
    request<{ hold_token: string; status: string; released: boolean }>(
      `/calendar/slot-holds/${encodeURIComponent(holdToken)}`,
      { method: 'DELETE' },
    ),

  /**
   * Import upcoming Google Calendar events (including Acuity-synced) as Ryozan bookings.
   * Idempotent: events already imported are skipped.
   */
  importFromCalendar: (days?: number) =>
    request<{ imported: number; skipped: number; total: number }>(
      `/calendar/import${days ? `?days=${days}` : ''}`,
      { method: 'POST' },
    ),

  /**
   * Personal time blocks. Mirrors backend TimeBlock endpoints in
   * calendar.py. Blocks make a window un-bookable (busy source in slot
   * generation, blocked reason "personal_block"). Artists manage their own;
   * admins+ may target another artist via artist_id.
   */
  timeBlocks: {
    list: (params: { start: string; end: string; artist_id?: string }) => {
      const query = new URLSearchParams({ start: params.start, end: params.end })
      if (params.artist_id) query.set('artist_id', params.artist_id)
      return request<TimeBlock[]>(`/calendar/time-blocks?${query.toString()}`)
    },
    create: (data: {
      artist_id?: string
      block_date: string
      all_day?: boolean
      start_time?: string
      end_time?: string
      reason?: string
    }) =>
      request<TimeBlock>('/calendar/time-blocks', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    remove: (blockId: string) =>
      request<{ id: string; deleted: boolean }>(
        `/calendar/time-blocks/${encodeURIComponent(blockId)}`,
        { method: 'DELETE' },
      ),
  },
}

/** Personal time block on an artist's calendar (mirrors backend TimeBlockResponse). */
export interface TimeBlock {
  id: string
  artist_id: string
  block_date: string
  all_day: boolean
  start_time: string | null
  end_time: string | null
  reason: string | null
  google_calendar_event_id: string | null
}

// ── Health Check ─────────────────────────────────────

export interface HealthStatus {
  ok: boolean
  db: 'connected' | 'unreachable' | 'unknown'
  version?: string
  failure?: 'timeout' | 'cancelled' | 'network' | 'server' | 'invalid_response'
}

// Club (Community Feed)

export interface ClubPost {
  id: string
  author_id: string
  author_name: string | null
  author_avatar: string | null
  body: string
  media_url: string | null
  post_type: string
  likes_count: number
  is_liked: boolean
  created_at: string
}

export const club = {
  listPosts: (params?: { skip?: number; limit?: number; post_type?: string }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set('skip', String(params.skip))
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.post_type) qs.set('post_type', params.post_type)
    return request<{ posts: ClubPost[]; total: number }>(`/club/posts${qs.toString() ? `?${qs}` : ''}`)
  },
  createPost: (data: { body: string; post_type?: string; media_url?: string }) =>
    request<ClubPost>('/club/posts', { method: 'POST', body: JSON.stringify(data) }),
  toggleLike: (postId: string) =>
    request<{ likes_count: number; is_liked: boolean }>(`/club/posts/${postId}/like`, { method: 'POST' }),
  deletePost: (postId: string) =>
    request<void>(`/club/posts/${postId}`, { method: 'DELETE' }),
}


export const ai = {
  generateTemplate: (data: { template_type: string; tone?: string; context?: string }) =>
    request<{ text: string }>('/ai/generate-template', { method: 'POST', body: JSON.stringify(data) }),
  generateMessage: (data: { purpose: string; client_name?: string; booking_desc?: string; tone?: string; context?: string }) =>
    request<{ text: string }>('/ai/generate-message', { method: 'POST', body: JSON.stringify(data) }),
}


/**
 * Ping the API health endpoint. Returns status without throwing.
 * Used by the app shell to detect API outages and show a banner
 * instead of letting every component fail independently.
 */
export async function checkApiHealth(cancellation?: AbortSignal): Promise<HealthStatus> {
  const timeout = AbortSignal.timeout(5000)
  const signal = cancellation ? AbortSignal.any([cancellation, timeout]) : timeout
  try {
    const res = await fetch(HEALTH_URL, { signal, cache: 'no-store' })
    let data: { db?: unknown; version?: string; status?: string } = {}
    try {
      data = await res.json()
    } catch (err) {
      if (res.ok || !(err instanceof SyntaxError)) throw err
    }
    const db = data?.db === 'connected' || data?.db === 'unreachable' ? data.db : 'unknown'
    const ok = res.ok && db !== 'unreachable' && data?.status !== 'degraded' && data?.status !== 'error'
    return { ok, db, version: data?.version, ...(ok ? {} : { failure: 'server' as const }) }
  } catch (err) {
    const error = normalizeRequestError(err, signal)
    const failure = error.code === 'request_timeout' ? 'timeout'
      : error.code === 'request_cancelled' ? 'cancelled'
        : error.code === 'invalid_response' ? 'invalid_response' : 'network'
    return { ok: false, db: 'unknown', failure }
  }
}

// ════════════════════════════════════════════════════════════════
// Flash Sheet + Design Storefront
// ════════════════════════════════════════════════════════════════

export type FlashSheetStatus = 'uploaded' | 'identified' | 'segmented' | 'published' | 'archived'
export type FlashDesignStatus = 'draft' | 'published' | 'claimed' | 'archived'

export interface FlashSheet {
  id: string
  artist_id: string
  image_url: string
  source_image_url: string
  crop_applied: boolean
  crop_x: number
  crop_y: number
  crop_w: number
  crop_h: number
  asset_version: number
  title: string | null
  status: FlashSheetStatus
  design_count: number
  created_at: string
}

export interface FlashManagerDesign {
  id: string
  sheet_id: string
  artist_id: string
  artist_slug?: string | null
  artist_name?: string | null
  crop_x: number
  crop_y: number
  crop_w: number
  crop_h: number
  crop_units: 'percent' | 'pixel'
  image_url: string | null
  preview_url: string | null
  source_image_url: string | null
  asset_version: number
  context_regions: Array<Record<string, unknown>>
  name: string | null
  ai_suggested_name: string | null
  price: number | null
  estimated_minutes: number | null
  deposit_amount: number | null
  reclaimable: boolean
  size_label: string | null
  placement: string | null
  style: string | null
  mask_json: Record<string, unknown> | null
  canvas_state: Record<string, unknown> | null
  ai_metadata: Record<string, unknown> | null
  gala_sync_enabled: boolean
  status: FlashDesignStatus
  claimed_by: string | null
  claimed_at: string | null
  created_at: string
}

export interface SegmentResult {
  label: string
  score: number
  box: { xmin: number; ymin: number; xmax: number; ymax: number }
  role: 'design' | 'context'
  context_type: string | null
}

export interface SegmentResponse {
  sheet_id: string
  image_url: string
  designs: SegmentResult[]
  context_regions: SegmentResult[]
  design_count: number
}

export interface FlashSegmentationCropInput {
  crop_x: number
  crop_y: number
  crop_w: number
  crop_h: number
}

/** Persist a reversible overall sheet crop as the active preview and storefront asset. */
export async function saveFlashSheetCrop(
  sheetId: string,
  crop: FlashSegmentationCropInput,
): Promise<FlashSheet> {
  return request<FlashSheet>(`/flash/${sheetId}/crop`, {
    method: 'PUT',
    body: JSON.stringify(crop),
  })
}

/** Upload a flash sheet image (artist/admin scoped) */
export async function uploadFlashSheet(file: File, title?: string, artistId?: string): Promise<FlashSheet> {
  const formData = new FormData()
  formData.append('file', file)
  if (title) formData.append('title', title)
  if (artistId) formData.append('artist_id', artistId)

  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  let res: Response
  try {
    res = await fetch(`${API_BASE}/flash/upload`, {
      method: 'POST',
      headers,
      body: formData,
    })
  } catch {
    // Network-level failure (offline, DNS, CORS preflight): the API is unreachable.
    throw new ApiError(0, 'Could not reach the server. Check your connection and try again.', 'FLASH_API_UNAVAILABLE')
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, parseDetail(error.detail, 'Upload failed'), parseDetailCode(error.detail))
  }

  return res.json()
}

/** AI-segment designs on a flash sheet (artist only) */
export async function segmentFlashSheet(sheetId: string, crop?: FlashSegmentationCropInput): Promise<SegmentResponse> {
  return request<SegmentResponse>(`/flash/${sheetId}/segment`, {
    method: 'POST',
    ...(crop ? { body: JSON.stringify(crop) } : {}),
  })
}

export interface ApexDatasetHandoffReceipt {
  status: 'snapshot_created'
  sample_count: number
  training_started: false
  apex_receipt: {
    id: string
    owner: string
    modelId: string
    sampleCount: number
    contentSha256: string
    artifactUri: string
    createdAt: string
  } | null
}

/** Founder/admin: snapshot reviewed flash masks in Ark Apex without starting training. */
export async function pushFlashTrainingManifestToApex(): Promise<ApexDatasetHandoffReceipt> {
  return request<ApexDatasetHandoffReceipt>('/flash/feedback/push-to-apex', {
    method: 'POST',
  })
}

export interface FlashSheetInventoryResponse {
  sheet: FlashSheet
  designs: FlashManagerDesign[]
}

/** Ask AI for a draft named inventory without publishing the sheet. */
export async function identifyFlashSheet(sheetId: string): Promise<FlashSheetInventoryResponse> {
  return request<FlashSheetInventoryResponse>(`/flash/sheets/${sheetId}/identify`, {
    method: 'POST',
  })
}

/** Publish a complete whole-sheet catalog after artist review. */
export async function publishFlashSheetCatalog(sheetId: string): Promise<FlashSheetInventoryResponse> {
  return request<FlashSheetInventoryResponse>(`/flash/sheets/${sheetId}/publish-catalog`, {
    method: 'POST',
  })
}

export interface FlashDesignCropInput {
  crop_x: number
  crop_y: number
  crop_w: number
  crop_h: number
  name?: string
}

/** Create a browser-safe design preview from an artist-selected sheet region. */
export async function createFlashDesignCrop(
  sheetId: string,
  data: FlashDesignCropInput
): Promise<FlashManagerDesign> {
  return request(`/flash/${sheetId}/designs`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

/** List artist's flash sheets */
export async function listFlashSheets(
  skip = 0,
  limit = 20,
  artistId?: string
): Promise<{ sheets: FlashSheet[]; total: number }> {
  const params = new URLSearchParams({ skip: String(skip), limit: String(limit) })
  if (artistId) params.set('artist_id', artistId)
  return request(`/flash/sheets?${params.toString()}`)
}

/** Delete a flash sheet and its segmented designs */
export async function deleteFlashSheet(sheetId: string): Promise<void> {
  try {
    return await request(`/flash/sheets/${sheetId}`, { method: 'DELETE' })
  } catch (error) {
    if (error instanceof ApiError && error.status === 405) {
      return request(`/flash/sheets/${sheetId}/delete`, { method: 'POST' })
    }
    throw error
  }
}

// ── Hustle / Stash inventory ─────────────────────────

export type StashLane = 'flash' | 'crafts' | 'digital'
export type StashItemStatus = 'draft' | 'published' | 'archived' | 'sold'

export interface StashItem {
  id: string
  artist_id: string
  created_by_id: string | null
  source_flash_design_id: string | null
  lane: StashLane
  title: string
  description: string | null
  status: StashItemStatus
  sku: string | null
  asset_url: string | null
  price: number | null
  inventory_count: number | null
  size_label: string | null
  estimated_minutes: number | null
  delivery_kind: string | null
  storage_ref: string | null
  tags: string[] | null
  metadata: Record<string, unknown> | null
  ai_metadata: Record<string, unknown> | null
  gala_sync_enabled: boolean
  published_at: string | null
  created_at: string
  updated_at: string
}

export type StashItemInput = Partial<Omit<StashItem, 'id' | 'artist_id' | 'created_by_id' | 'created_at' | 'updated_at' | 'published_at'>> & {
  title?: string
  artist_id?: string
}

export interface StashCanvasSession {
  id: string
  artist_id: string
  sheet_id: string | null
  created_by_id: string | null
  lane: StashLane
  status: string
  canvas_state: Record<string, unknown> | null
  ai_suggestions: Record<string, unknown> | null
  source_context: Record<string, unknown> | null
  gala_sync_enabled: boolean
  created_at: string
  updated_at: string
}

export const stash = {
  listCreators: () => request<User[]>('/stash/creators'),

  listItems: (params?: { lane?: StashLane; status?: string; artistId?: string; skip?: number; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.lane) query.set('lane', params.lane)
    if (params?.status) query.set('status', params.status)
    if (params?.artistId) query.set('artist_id', params.artistId)
    if (params?.skip !== undefined) query.set('skip', String(params.skip))
    if (params?.limit !== undefined) query.set('limit', String(params.limit))
    const qs = query.toString()
    return request<{ items: StashItem[]; total: number }>(`/stash/items${qs ? `?${qs}` : ''}`)
  },

  createItem: (data: StashItemInput & { title: string; lane: StashLane }) =>
    request<StashItem>('/stash/items', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateItem: (itemId: string, data: StashItemInput) =>
    request<StashItem>(`/stash/items/${itemId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  createItemFromFlash: (designId: string) =>
    request<StashItem>(`/stash/items/from-flash/${designId}`, { method: 'POST' }),

  createCanvasSession: (data: {
    artist_id?: string
    sheet_id?: string
    lane?: StashLane
    status?: string
    canvas_state?: Record<string, unknown>
    ai_suggestions?: Record<string, unknown>
    source_context?: Record<string, unknown>
    gala_sync_enabled?: boolean
  }) =>
    request<StashCanvasSession>('/stash/canvas-sessions', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateCanvasSession: (sessionId: string, data: {
    status?: string
    canvas_state?: Record<string, unknown>
    ai_suggestions?: Record<string, unknown>
    source_context?: Record<string, unknown>
    gala_sync_enabled?: boolean
  }) =>
    request<StashCanvasSession>(`/stash/canvas-sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
}

/** List designs on a flash sheet */
export async function listSheetDesigns(sheetId: string): Promise<{ designs: FlashManagerDesign[]; total: number }> {
  return request(`/flash/${sheetId}/designs`)
}

/** Update a flash design (name, price, time, status) */
export interface FlashDesignUpdate {
  name?: string
  price?: number
  estimated_minutes?: number
  deposit_amount?: number
  reclaimable?: boolean
  size_label?: string
  placement?: string
  style?: string
  mask_json?: Record<string, unknown>
  canvas_state?: Record<string, unknown>
  ai_metadata?: Record<string, unknown>
  gala_sync_enabled?: boolean
  status?: FlashDesignStatus
}

export async function updateFlashDesign(
  designId: string,
  data: FlashDesignUpdate
): Promise<FlashManagerDesign> {
  return request(`/flash/designs/${designId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

/** Delete an incorrect unclaimed design while preserving its source sheet. */
export async function deleteFlashDesign(designId: string): Promise<void> {
  return request(`/flash/designs/${designId}`, { method: 'DELETE' })
}

/** Browse published flash designs (collector storefront) */
export async function browseFlashStore(
  opts: { artist_id?: string; style?: string; skip?: number; limit?: number } = {}
): Promise<{ designs: FlashManagerDesign[]; total: number }> {
  const params = new URLSearchParams()
  if (opts.artist_id) params.set('artist_id', opts.artist_id)
  if (opts.style) params.set('style', opts.style)
  if (opts.skip) params.set('skip', String(opts.skip))
  if (opts.limit) params.set('limit', String(opts.limit))
  return request(`/flash/store?${params.toString()}`)
}

/** Public: browse the collector storefront without authentication. */
export async function browsePublicFlashStore(
  opts: { artist_slug?: string; style?: string; skip?: number; limit?: number } = {}
): Promise<{ designs: FlashManagerDesign[]; total: number }> {
  const params = new URLSearchParams()
  if (opts.artist_slug) params.set('artist_slug', opts.artist_slug)
  if (opts.style) params.set('style', opts.style)
  if (opts.skip) params.set('skip', String(opts.skip))
  if (opts.limit) params.set('limit', String(opts.limit))
  return request(`/flash/store/public?${params.toString()}`)
}

// ── Public Flash Sheet Wall ──────────────────────────────────────────────────

export interface PublicFlashSheet {
  id: string
  image_url: string
  title: string | null
  design_count: number
  designs: FlashManagerDesign[]
}

export interface PublicSheetWall {
  sheets: PublicFlashSheet[]
  artist_slug: string
  artist_name: string
  flash_deposit_amount: number
  allow_prepay_full: boolean
}

/** Public: get flash sheet wall for an artist (sheets + designs with crop coords) */
export async function getPublicSheetWall(artistSlug: string): Promise<PublicSheetWall> {
  const res = await fetch(`${API_BASE}/flash/public/sheets/${encodeURIComponent(artistSlug)}`)
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, parseDetail(error.detail, 'Failed to load flash designs'))
  }
  return res.json()
}

export interface FlashBookRequest {
  design_id: string
  scheduled_date: string   // YYYY-MM-DD
  scheduled_time: string   // HH:MM
  guest_name: string
  guest_email: string
  guest_phone?: string
  pay_full?: boolean
}

export interface FlashBookResponse {
  booking_reference: string
  checkout_url: string | null
  checkout_required: boolean
  amount_cents: number
  is_deposit: boolean
  bttcoin_reward: number
  artist_name: string
  design_name: string
  scheduled_date: string
  scheduled_time: string
}

/** Public: book a flash design. Creates Checkout only when payment is configured. */
export async function bookFlashDesign(data: FlashBookRequest): Promise<FlashBookResponse> {
  const res = await fetch(`${API_BASE}/flash/book`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, parseDetail(error.detail, 'Booking failed'))
  }
  return res.json()
}

// ── Courier: Email Log ────────────────────────────────────────────────────

export interface EmailLogRow {
  id: string
  to_email: string
  subject: string
  template: string
  from_alias: string | null
  status: 'queued' | 'sent' | 'failed' | 'delivered' | 'bounced' | 'opened' | 'clicked'
  resend_id: string | null
  error: string | null
  retry_count: number
  created_at: string
  updated_at: string
}

export interface EmailLogListResponse {
  items: EmailLogRow[]
  total: number
  skip: number
  limit: number
}

export interface EmailStatRow {
  template: string
  status: string
  count: number
}

export interface EmailStatsResponse {
  by_template_status: EmailStatRow[]
  total_sent: number
  total_failed: number
  window_days: number
}

/** Paginated email log (founder only) */
export async function listEmailLog(opts: {
  status?: string
  template?: string
  search?: string
  skip?: number
  limit?: number
} = {}): Promise<EmailLogListResponse> {
  const params = new URLSearchParams()
  if (opts.status)   params.set('status',   opts.status)
  if (opts.template) params.set('template', opts.template)
  if (opts.search)   params.set('search',   opts.search)
  if (opts.skip)     params.set('skip',     String(opts.skip))
  if (opts.limit)    params.set('limit',    String(opts.limit))
  const qs = params.toString()
  return request(`/email/log${qs ? `?${qs}` : ''}`)
}

/** Email send stats by template + status (last N days) */
export async function getEmailStats(days = 30): Promise<EmailStatsResponse> {
  return request(`/email/log/stats?days=${days}`)
}

/** Retry a failed email send */
export async function retryEmailLog(logId: string): Promise<{ success: boolean; message: string }> {
  return request(`/email/log/${logId}/retry`, { method: 'POST' })
}

// ── Artist Scroll ──────────────────────────────────────

export interface ArtistScrollData {
  id: string
  user_id?: string
  user_name?: string
  status: string
  signed_at: string | null
  artist_signature: string | null
  bbp_cert_url: string | null
  first_aid_cert_url: string | null
  cpr_cert_url: string | null
  govt_id_url: string | null
  owner_sig_url: string | null
  tax_opt_in: boolean | null
  fields: Record<string, string>
  created_at: string
}

export async function submitArtistScroll(params: {
  artistSignature: string
  fieldsJson: string
  bbpCert?: File | null
  firstAidCert?: File | null
  cprCert?: File | null
  govtId?: File | null
}): Promise<{ id: string; status: string; signed_at: string; bbp_cert_url: string | null }> {
  const fd = new FormData()
  fd.append('artist_signature', params.artistSignature)
  fd.append('fields_json', params.fieldsJson)
  if (params.bbpCert) fd.append('bbp_cert', params.bbpCert)
  if (params.firstAidCert) fd.append('first_aid_cert', params.firstAidCert)
  if (params.cprCert) fd.append('cpr_cert', params.cprCert)
  if (params.govtId) fd.append('govt_id', params.govtId)
  const res = await fetch(`${API_BASE}/documents/scroll/submit`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}` },
    body: fd,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Scroll submission failed')
  }
  return res.json()
}

export async function getMyScroll(): Promise<{ scroll: ArtistScrollData | null }> {
  return request('/documents/scroll/my')
}

export async function getUserScroll(userId: string): Promise<{ scroll: ArtistScrollData | null }> {
  return request(`/documents/scroll/user/${userId}`)
}

// ── Scroll Categories & Tab System ───────────────────────────────────────────

export interface ScrollCategory {
  id: string
  slug: string
  label: string
  icon: string | null
  description: string | null
  is_builtin: boolean
  ai_digest_enabled: boolean
  ai_digest_prompt: string | null
  sort_order: number
}

export interface ScrollDoc {
  id: string
  user_id: string | null
  user_name: string | null
  title: string
  status: string
  file_url: string | null
  signed_at: string | null
  created_at: string
  ai_digest: Record<string, unknown> | null
  notes: string | null
}

export interface ScrollCategoryDocsResponse {
  category: { slug: string; label: string; icon: string | null; ai_digest_enabled: boolean }
  docs: ScrollDoc[]
  total: number
}

export async function listScrollCategories(): Promise<ScrollCategory[]> {
  return request('/scrolls/categories')
}

export async function createScrollCategory(params: {
  label: string
  description?: string
  icon?: string
  ai_digest_enabled?: boolean
  ai_digest_prompt?: string
}): Promise<ScrollCategory> {
  const fd = new FormData()
  fd.append('label', params.label)
  fd.append('description', params.description ?? '')
  fd.append('icon', params.icon ?? '📄')
  fd.append('ai_digest_enabled', params.ai_digest_enabled ? 'true' : 'false')
  fd.append('ai_digest_prompt', params.ai_digest_prompt ?? '')
  const res = await fetch(`${API_BASE}/scrolls/categories`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}` },
    body: fd,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as Record<string, string>).detail || 'Failed to create category')
  }
  return res.json()
}

export async function deleteScrollCategory(slug: string): Promise<void> {
  await request(`/scrolls/categories/${slug}`, { method: 'DELETE' })
}

export async function interpretScrollCategory(intent: string): Promise<ScrollCategory> {
  const res = await fetch(`${API_BASE}/scrolls/categories/interpret`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ intent }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as Record<string, string>).detail || 'Gala failed to interpret intent')
  }
  return res.json()
}

export async function listScrollCategoryDocs(
  categorySlug: string,
  userId?: string,
): Promise<ScrollCategoryDocsResponse> {
  const qs = userId ? `?user_id=${userId}` : ''
  return request(`/scrolls/${categorySlug}${qs}`)
}

export async function uploadScrollDoc(params: {
  categorySlug: string
  file: File
  title?: string
  notes?: string
  aiDigestEnabled?: boolean
  /** The file is a government ID: stored privately, served only through short-lived signed links. */
  isId?: boolean
}): Promise<{ id: string; title: string; file_url: string; status: string; ai_digest: unknown; created_at: string }> {
  const fd = new FormData()
  fd.append('file', params.file)
  fd.append('title', params.title ?? params.file.name)
  fd.append('notes', params.notes ?? '')
  fd.append('ai_digest_enabled', params.aiDigestEnabled && !params.isId ? 'true' : 'false')
  fd.append('is_id', params.isId ? 'true' : 'false')
  const res = await fetch(`${API_BASE}/scrolls/${params.categorySlug}/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${getToken()}` },
    body: fd,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as Record<string, string>).detail || 'Upload failed')
  }
  return res.json()
}

export async function deleteScrollDoc(docId: string): Promise<void> {
  await request(`/scrolls/docs/${docId}`, { method: 'DELETE' })
}

/** Send an artist a first-time scroll completion request (founder only) */
export async function requestArtistScroll(artistId: string): Promise<void> {
  await request(`/scrolls/request/${artistId}`, { method: 'POST' })
}

/** Send an artist a scroll reminder. Debounced 24h server-side (founder only). */
export async function remindArtistScroll(artistId: string): Promise<void> {
  await request(`/scrolls/remind/${artistId}`, { method: 'POST' })
}

export interface ArtistScrollRequestStatus {
  artist_id: string
  has_completed_scroll: boolean
  request_sent: boolean
  last_request_at: string | null
  last_reminder_at: string | null
  next_reminder_at: string | null
  daily_reminders_active: boolean
}

export async function getArtistScrollRequestStatus(artistId: string): Promise<ArtistScrollRequestStatus> {
  return request(`/scrolls/status/${artistId}`)
}

// ── Response Templates ────────────────────────────────────────────────────────

export interface ResponseTemplate {
  id: string
  title: string
  body: string
  sort_order: number
  created_by: string | null
  created_at: string
  updated_at: string
}

export const responseTemplates = {
  list: (): Promise<ResponseTemplate[]> =>
    request('/response-templates/'),

  create: (data: { title: string; body: string; sort_order?: number }): Promise<ResponseTemplate> =>
    request('/response-templates/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (id: string, data: { title?: string; body?: string; sort_order?: number }): Promise<ResponseTemplate> =>
    request(`/response-templates/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  remove: (id: string): Promise<void> =>
    request(`/response-templates/${id}`, { method: 'DELETE' }),
}
