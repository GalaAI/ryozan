# AGENTS.md

## Repository overview

Monorepo for **Ryozan Studio** ([ryozan.app](https://ryozan.app)): FastAPI backend, React (Vite) PWA, PostgreSQL on Railway, static frontend served from the API container in production.

| Area | Path | Stack |
|------|------|--------|
| API | `api/` | Python, FastAPI, SQLAlchemy, Alembic, Uvicorn |
| Frontend | `app/` | React 19, TypeScript, Vite, Tailwind v4 |
| Deploy | repo root | `Dockerfile` + `railway.toml` → single Railway service |

**Infrastructure (typical):** Railway (API + DB + unified image), Cloudflare (DNS, R2 assets, Email Worker for inbound), Resend (outbound), Stripe Connect, Clover, Web Push (VAPID). See root `README.md` for the full table.

## Local development

**API**

```bash
cd api
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 18810
```

**App**

```bash
cd app
npm install
npm run dev
```

Default Vite dev server: **http://localhost:15310**. The app proxies `/api` to the local API on **http://localhost:18810** by default (override with `VITE_API_TARGET`). Use `npm run dev:prod` only when intentionally testing the local UI against Railway production. Ryozan's dev ports (web 15310, API 18810) stay clear of ArkStation's dev ports (15300, 18788), so both can run on one machine.

**CRM pipeline v1:** staff endpoints `GET/POST /crm/pipeline-tasks`, `PATCH /crm/pipeline-tasks/{id}/status` (requires assistant+). Alembic revision `20260515_crm_pipeline_tasks`. Frontend: `crm` client in `app/src/lib/api.ts`.

## Environment and secrets

- Copy variable names from `api/.env.example`; **never commit real secrets** or paste production URLs/keys into chat.
- Prefer local DB (`DATABASE_URL` in `.env.example` shows sqlite for dev) unless you intentionally need shared data.

## CI / validation

- `.github/workflows/api.yml` — API lint/import checks; deploy on `main` as configured in repo.
- `.github/workflows/app.yml` — TypeScript + ESLint + build validation for `app/`.

Before claiming a change is safe: run the same checks locally (`cd app && npm run lint && npm run build`; Python checks per `api.yml`).

## Deployment mental model

- **Production:** one Railway deployment builds the root `Dockerfile` (frontend build + API image); FastAPI serves the built SPA as static files.
- Compare branch `HEAD`, `origin/main`, and the live production commit with `node scripts/deploy-state.mjs`.
- **Cloudflare** here is mainly R2, email routing/worker, and DNS — not a substitute for the Railway app container unless you explicitly add Workers/Pages for this product.

## Design system

- The only UI source is `packages/ui` (published internally as `@ryozan/ui`). Consumers import from `@ryozan/ui`; nobody authors new UI components or inline styled elements. Read `packages/ui/README.md` before touching any UI.
- No hex, no px literals, no fonts outside the allowlist in app code. `npm run lint:adherence:src` in `app/` enforces it and runs in CI.
- Editing `packages/ui/src` changes nothing in the app until you repack and relock (`cd packages/ui && npm run pack:app`, then in `app/` run `npm install --package-lock-only "@ryozan/ui@file:vendor/ryozan-ui-<version>.tgz"`); `npm run check:ui-tarball` fails otherwise.
- Source of record is the Ryozan Studio Design System artifact (claude.ai artifact id 35f92e5b-ae2d-4c67-a4e1-ec118dfe929e). A need the package cannot express is a design request upstream, never an improvised screen.

## Agent behavior

- Prefer **existing patterns** in `api/` and `app/` over new frameworks.
- After API or schema changes, consider Alembic migrations in `api/` if the project uses them for the change.
- For frontend, respect React 19 + Vite 7 + Tailwind v4 conventions already in the tree.

## Claude Code skill: `ryozan-design-canon`

`.claude/skills/ryozan-design-canon/SKILL.md` is the design canon as a skill: read order, build rules, gates and the never list. Claude Code loads it in this repo; other agents read the file directly before any UI work. It restates the Design system section above; when the two disagree, the artifact wins and both get corrected.

## Cursor project rules

Shorter behavioral context lives in `.cursor/rules/` (always-on repo map plus scoped rules for `api/**/*.py` and `app/**/*.{ts,tsx}`). Prefer expanding `AGENTS.md` for long how-tos; keep rules concise.

## Cursor Cloud specific instructions

Full local stack needs **PostgreSQL 16+**, **Python 3.12** (`python3.12-venv` on Debian/Ubuntu), and **Node 20+**. Docker is optional; `docker-compose.yml` dev profile still expects a host `api/.env` and does not bundle Postgres.

### PostgreSQL (one-time per VM)

The API defaults in `api/app/core/config.py` expect `postgresql+asyncpg://postgres:galax2025@localhost:5432/nexus`. On a fresh Linux VM:

```bash
sudo service postgresql start
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'galax2025';"
sudo -u postgres createdb nexus 2>/dev/null || true
```

Copy `api/.env.example` to `api/.env` and set at least `LOCAL_DEV_MODE=true`, `ENABLE_API_DOCS=true`, and update `DATABASE_URL` to `postgresql+asyncpg://postgres:galax2025@localhost:5432/nexus` (matching the PostgreSQL setup above).

### Start dev servers (tmux recommended)

From repo root, in separate sessions:

```bash
cd api && source .venv/bin/activate && uvicorn main:app --reload --host 127.0.0.1 --port 18810
cd app && npm run dev
```

- App: http://localhost:15310 (proxies `/api` and `/health` to :18810)
- API health: http://localhost:18810/health

### Local staff login

After the API has created tables on first boot:

```bash
cd api && .venv/bin/python scripts/bootstrap_dev_login.py
```

Password is written to `.dev/state/dev-login-password.txt` (founder `founder@ryozan.app`). Collector/staff UI entry is `/portal`.

### Lint / build (CI parity)

See **CI / validation** above: `cd app && npm run lint && npm run build`; `cd api && ruff check app/ main.py` (venv with `requirements-dev.txt`).

### UI-only against production API

`cd app && npm run dev:prod` needs only Node; no local Postgres or API.

<!-- solatree:lanes begin -->
## Lanes

This repo has four lanes: backend, aios, middleware, frontend. Every edit, code change and feature happens in one, one task per lane.

1. Pick the lane: `node "$(git rev-parse --git-common-dir)/solatree/solatree.mjs" lane which <paths>`. Work crossing layers uses one lead lane, and the PR takes the `solatree:cross-layer` label.
2. Open it: `node "$(git rev-parse --git-common-dir)/solatree/solatree.mjs" lane open <layer> --task <slug> --holder <your session id>`. It prints the path. Work only there. If the lane is busy, add `--wait 1800`, or ask.
3. Finish: commit, push, open the PR with `--label solatree:graft`.
4. Close it: `node "$(git rev-parse --git-common-dir)/solatree/solatree.mjs" lane close <layer>`. To fix that PR later, reopen with `--branch <its branch>`.

Do not run `git worktree add`, create branches by hand, or commit anywhere else. Hooks refuse them. See who holds what with `node "$(git rev-parse --git-common-dir)/solatree/solatree.mjs" lane status`.
<!-- solatree:lanes end -->
