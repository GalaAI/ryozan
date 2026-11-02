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

## 3. Set up your studio

The API prints a setup code at startup while the install has no owner:

```bash
docker compose logs api | grep "New install"   # or read the terminal running uvicorn
```

Open http://localhost:15300/setup, enter the code, and name your studio, its
time zone and your owner account. That account can change all the studio
details later, under Account, then Studio settings.

The code comes from `SECRET_KEY` and stops working once the install has an
owner, so a stranger who finds the page first cannot claim your install.

For a throwaway development login instead,
`cd api && .venv/bin/python scripts/bootstrap_dev_login.py` creates
`founder@ryozan.app` and prints a generated password (`--email` and `--name` pick
your own, and `--with-admin` adds a studio admin). It refuses any database it
does not recognize as local without `--allow-nonlocal-db`.

## 4. Use it

1. After setup you land in the portal. Later, open http://localhost:15300,
   choose **Open the studio portal** and sign in.
2. Under Account, open **Studio settings** and fill in your contact details.
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
