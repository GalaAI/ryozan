# Ryozan quickstart (local, about 10 minutes)

Ryozan runs on your machine: a FastAPI API on port 18788, a Vite web app on
port 15300, and PostgreSQL on 5433. Every agent calls NVIDIA Nemotron on Nebius
Token Factory, so the only credential you need is a Token Factory API key.

## 1. Configure

```bash
cp .env.example .env              # sets POSTGRES_PASSWORD for docker compose
cp api/.env.example api/.env      # then set NEBIUS_API_KEY in api/.env
```

In `api/.env`, keep `POSTGRES_PASSWORD` and the password inside `DATABASE_URL`
equal to the value in the root `.env`. Signing keys are generated on first run
and kept in `api/.dev-secrets.json` (gitignored).

## 2. Start

With Docker:

```bash
docker compose up -d db api app
```

Or natively (Python 3.12, Node 20+), with only the database in Docker:

```bash
docker compose up -d db
cd api && python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 18788
cd ../app && npm ci && npm run dev
```

On Windows (PowerShell) the virtual environment keeps its programs in `Scripts`:

```powershell
docker compose up -d db
cd api; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn main:app --host 127.0.0.1 --port 18788
cd ..\app; npm ci; npm run dev
```

`requirements.txt` skips `uvloop` on Windows (it has no Windows build) and
installs `tzdata`, which Python's `zoneinfo` needs there.

The API creates its tables on first boot. Every port binds to 127.0.0.1. The
web dev server proxies `/api` to `http://127.0.0.1:18788` by default; set
`VITE_API_TARGET` only to point it somewhere else.

## 3. Create the first account

```bash
cd api && .venv/bin/python scripts/bootstrap_dev_login.py
```

On Windows use `.venv\Scripts\python scripts\bootstrap_dev_login.py`. With
Docker only, run `docker compose exec api python scripts/bootstrap_dev_login.py --allow-nonlocal-db`
(inside compose the database host is `db`).

This creates `founder@ryozan.app` (display name "Studio Founder") and
prints a generated password, also saved to `.dev/state/dev-login-password.txt`.
Add `--with-admin` for a second staff account. The script refuses non-local
databases.

## 4. Use it

1. Open http://localhost:15300 and choose **Open the studio portal**.
2. Sign in with the founder account.
3. On the Booking tab, use **New booking** to record a real inquiry through the
   booking API; it appears in the calendar, the Hub counts and Gala's tools.
4. Ask Gala "What is on my schedule?" in the Hub. Gala answers through the
   `get_schedule` tool; the `/api/v1/gala/chat` response carries the id of the
   Nemotron model that served it.
5. Sign out from the account menu. The session token is revoked on the server
   (`POST /api/v1/auth/logout`), so a copied token stops working at once.

## Models

| Lane | Default model (Token Factory id) |
| --- | --- |
| Gala | `nvidia/Nemotron-3-Ultra-550b-a55b` (per agent profile) |
| Most Bandits and the tier default | `nvidia/nemotron-3-super-120b-a12b` |
| Fast lanes and utility calls | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` |
| Vision critique (Rochishin) | `nvidia/Nemotron-3.5-Super-VL` |

Each agent tries its own model first and falls back to the tier lane. Set
`AI_*_PRIMARY_MODEL` in `api/.env` to change the tier default. All four ids are
served by Token Factory (checked 2026-10-29 through the Ark gateway, which
forwards these exact upstream names).

## Studio identity

Client email, SMS, calendar invites and PDF exports print `STUDIO_NAME`
(default `Ryozan Studio`). Set `STUDIO_ADDRESS` in `api/.env` to add a postal
address to email footers and PDFs; nothing prints when it is blank.

## Optional services

Google sign-in, Stripe, email, SMS and Pinterest stay hidden until their keys
are set in `api/.env`. Self-registration appears once an email provider is
configured. An Ark Model Gateway can replace Token Factory by setting
`ARK_MODEL_GATEWAY_BASE_URL`, `ARK_MODEL_GATEWAY_KEY` and `ark/*` model slugs.
