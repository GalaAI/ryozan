# Local E2E Testing — Collector Portal and Flows

State of the tooling for end-to-end testing of messaging, email, booking, and
purchasing against local infrastructure, with local data mirroring production.

## The pieces

| Piece | What | How |
|---|---|---|
| Test account | `e2e-collector@ryozan.app` (role `collector`, "Jon Doe") | `api/scripts/bootstrap_dev_login.py` (local-db-only guard). Password: `.dev/state/dev-login-password.txt` (gitignored, shared with the other dev logins) |
| Local DB (docker) | postgres:16 at `localhost:5433/nexus`, volume `bttco_pgdata` | `docker compose up -d db` |
| Prod mirror | Scoped, PII-sanitized production data in the local db | `./scripts/mirror-prod-db.ps1 -Docker` (or no flag for the host db on 5432) |
| Email capture | Mailhog, UI at http://localhost:8025 | `docker compose up -d mailhog` + run API via `./scripts/dev-api-e2e.ps1` |
| API (E2E mode) | Providers blanked, SMTP -> mailhog, sends never leave the machine | `./scripts/dev-api-e2e.ps1 [-Docker]` |
| Frontend | Vite proxying to the local API | `cd app; npm run dev` (proxies to http://127.0.0.1:18788) |

## Mirroring production data

`scripts/mirror-prod-db.ps1` wraps the repo's existing scoped sync pipeline:

1. Resolves the prod read URL through the linked Railway CLI (public proxy
   host, never the `.railway.internal` one).
2. `api/scripts/dump_prod_test_data.py` exports the last 90 days of bookings,
   conversations, messages, notifications, and referenced users to the
   gitignored `.dev/state/prod-test-dump.json`. **Client PII is pseudonymized
   by default**; `--no-sanitize` exists but leave it off unless you have a
   specific need.
3. `api/scripts/_ensure_schema.py` creates the ORM schema idempotently
   (fresh docker volumes start empty).
4. `api/scripts/sync_public_state.py` + `sync_test_data.py` import into the
   local target. Both refuse to run against a non-local database.

Nothing in this path writes to production. Re-run any time you want local
state re-aligned with true state.

## Flow-by-flow readiness

| Flow | Ready? | Notes |
|---|---|---|
| Login / portal | YES | E2E collector account in both host and docker dbs |
| Messaging (Courier) | YES | Mirrored conversations/messages give real threads; log in as staff (bootstrap default) on one side and the collector on the other |
| Email | YES | Run API through `dev-api-e2e.ps1`; every send lands in Mailhog with true recipients. Plain `LOCAL_DEV_MODE=true` skips sends entirely, and `EMAIL_TEST_REDIRECT` (set in `api/.env`) really sends via live providers to the redirect inbox: do not use that path for E2E |
| Booking (inquiry) | YES | `/book` wizard against local API + mirrored artists |
| Booking (deposit checkout) + purchasing | **BLOCKED on keys** | `api/.env` carries **live** Stripe keys (`sk_live`/`pk_live`). Purchase E2E against local would create real charges. Put test-mode keys in `api/.env` (`sk_test...`/`pk_test...`) before exercising checkout. Note only the public-booking path has a code guard against `sk_live` under `LOCAL_DEV_MODE`; flash/quotes/POS do not |
| SMS | NO (by design) | Twilio env vars are empty locally; SMS flows no-op |

## Known caveats

- The `db` service is intentionally on **5433** so the host Postgres on 5432
  (the historical dev db) is untouched. Scripts accept `-Docker` to target it;
  default remains the host db until you decide to switch fully.
- `sync_public_state.py` skips artists missing locally unless
  `--create-missing` is passed.
- Getting Stripe **test** keys wired (and webhook forwarding via
  `stripe listen`) is the one open item for full purchase E2E.
