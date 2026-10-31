---
applyTo: "app/**"
---

# app/ — React frontend rules

These rules supplement `.github/copilot-instructions.md` and override defaults
when editing anything under `app/`.

## Stack
* React 19, TypeScript, Vite, React Router v6, Zustand for state, Recharts
  for graphs. **No** Tailwind utility classes in dashboard components —
  use CSS tokens (see Rule #4 below).

## Routing
* `app/src/App.tsx` is the route table. Public routes use the `<Layout>`
  wrapper. Dashboard routes are inside `<ProtectedRoute>` →
  `<DashboardLayout>`.
* When adding a URL that external services might guess (legal pages, SMS
  terms, etc.), add redirect routes for the common variants. Example: PR
  #30 added `/terms`, `/privacy-policy`, `/sms-terms`, `/legal/terms`, etc.

## State (Zustand)
* Auth state lives in `useAuthStore`. The `viewAs` field is **frontend-only
  preview** — never use it to alter API calls.

## Design system
* The only UI source is `packages/ui` (`@ryozan/ui`). Every colour, space,
  radius, shadow and font is a token from `@ryozan/ui/tokens.css`, which is
  GENERATED from `packages/ui/design-system/tokens.json` (`npm run tokens:gen`
  in `packages/ui`). Never edit `tokens.css` by hand; never add a token
  outside `tokens.json`.
* `tokens.json` is a verbatim mirror of the Ryozan Studio Design
  System artifact, the source of record. A need the system cannot express is
  a design request upstream (change the artifact, mirror it here), never an
  improvised value or an interpreted screen.
* No raw hex, no px literals, no fonts outside the allowlist in `app/src`.
  ESLint (`no-restricted-syntax`) and `npm run lint:adherence:src` enforce it;
  `DS_LEGACY_DEBT` in `app/eslint.config.js` lists the files still being
  cleaned, and a file leaves that list when it is token-clean. New files are
  gated immediately.
* Components use semantic class names (`.cp-`, `.nexus-`, `.uwc-`), not
  utility classes. App-shell dimensions, z-index and tooltip infrastructure
  live in `app/src/styles/app-layout.css`; brand values do not.
* Full rules: `AGENTS.md` § Design system, and `packages/ui/README.md`.

## Icons
* Custom SVG icons follow Lucide stroke convention: `viewBox="0 0 24 24"`,
  `fill="none"`, `stroke="currentColor"`, `strokeWidth="1.5-1.8"`,
  `strokeLinecap="round"`, `strokeLinejoin="round"`. **Never emojis.**

## API calls
* Base path `/api/v1`. Use the shared `api()` helper pattern from existing
  components — don't roll your own fetch wrappers.
* Cache invalidation: when mutating, refetch the affected query. Don't
  optimistically update without rollback.

## Lazy loading
* Role-specific dashboard panels MUST be `lazy()` + `<Suspense>` so
  collectors don't download artist code and vice versa.

## TypeScript
* `tsc --noEmit -p tsconfig.json` must pass. Don't use `any` to silence
  errors — fix the type.
* Strict mode is on. Don't disable it.
