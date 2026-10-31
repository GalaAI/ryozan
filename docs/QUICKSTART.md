# Ryozan quickstart (local, about 10 minutes)

Ryozan runs on your machine: a FastAPI API on port 18788, a Vite web app on
port 15300, and PostgreSQL on 5433. The agents call an OpenAI-compatible model
endpoint. The default is NVIDIA Nemotron on Nebius Token Factory, so the only
credential you need to start is a Token Factory API key. Without one the app
still runs; agent chat answers with a 503 that says to set `NEBIUS_API_KEY`.

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

Before the API starts, `alembic upgrade head` builds or updates the schema. Every port binds to 127.0.0.1. The
web dev server proxies `/api` to `http://127.0.0.1:18788` by default; set
`VITE_API_TARGET` only to point it somewhere else.

## 3. Create the first account

```bash
cd api && .venv/bin/python scripts/bootstrap_dev_login.py
```

On Windows use `.venv\Scripts\python scripts\bootstrap_dev_login.py`. With
Docker only, run `docker compose exec api python scripts/bootstrap_dev_login.py --allow-nonlocal-db`
(inside compose the database host is `db`, which the script does not treat as
local without that flag).

This creates `founder@ryozan.app` (display name "Studio Founder") and prints a
generated password. A native run also saves it to
`.dev/state/dev-login-password.txt` (gitignored); under Docker that file stays
inside the container, so copy the printed password. Use `--email` and `--name`
to pick your own founder login, and `--with-admin` to add a studio admin
account. Without `--allow-nonlocal-db` the script refuses any database it does
not recognize as local.

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
`AI_*_PRIMARY_MODEL` and `AI_*_FALLBACK_MODEL` in `api/.env` to change the tier
models. The per-agent ids live in `AGENT_PROFILES` in
`api/app/api/v1/endpoints/gala_chat.py`.

To use a different OpenAI-compatible provider, set `NEBIUS_BASE_URL` and
`NEBIUS_API_KEY` to that provider's base URL and key, and set the tier models to
ids it serves. Agents whose own model the provider does not serve fall back to
the tier lane.

## Studio identity

Client email, SMS, calendar invites and PDF exports print `STUDIO_NAME`
(default `Ryozan Studio`). Set `STUDIO_ADDRESS` in `api/.env` to add a postal
address to email footers and PDFs; nothing prints when it is blank.

## Optional services

Each integration (Google Calendar, Google sign-in linking, Pinterest, Stripe
Connect, Web Push, email forwarding) appears under Connections only when its
keys are set in `api/.env`. Plaid is configured the same way, but its bank link
opens from the Hustle page (`/dashboard/hustle`), not from Connections. The API reports which ones this install offers at
`GET /api/v1/integrations/options`. Client self-registration sends a
password-setting email, so it needs an email provider (Resend, SendGrid or
SMTP). The full list of variables is in the Configuration section of the root
`README.md`.
