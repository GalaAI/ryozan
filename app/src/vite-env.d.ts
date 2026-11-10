/// <reference types="vite/client" />

declare const __APP_VERSION__: string

interface ImportMetaEnv {
  /** API origin override. Empty in dev (Vite proxy handles routing) and in Railway production (the API serves the SPA same-origin). */
  readonly VITE_API_URL: string
  readonly VITE_MAPBOX_TOKEN: string
  readonly VITE_APP_ENV: string
  /** Google Identity Services browser client ID. Must equal the API's GOOGLE_SIGNIN_CLIENT_ID, or /auth/google rejects every ID token with a 401. */
  readonly VITE_GOOGLE_CLIENT_ID?: string
  /** This install's Jam workspace id. Unset: Jam metadata is not attached. */
  readonly VITE_JAM_WORKSPACE_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
