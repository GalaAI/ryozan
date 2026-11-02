# Roadmap

This file lists what is done, what is next and what comes later. Every item points at code or a pull request in this repository. There are no dates. Items move when the work lands, not before.

Core and optional extras are defined in [What's in v1](README.md#whats-in-v1).

## Now: v1, one studio, self-hosted

Done and on `main`:

- One install runs one studio. Docker Compose, the production image and Railway all build from the same [`Dockerfile`](Dockerfile) ([`docker-compose.yml`](docker-compose.yml), [`railway.toml`](railway.toml)). Self-hosting docs: README and [`docs/QUICKSTART.md`](docs/QUICKSTART.md) (#12).
- The core areas run: bookings and schedule, client inbox, waivers and documents, flash, Stripe deposits and checkout, the agent crew and the MCP server ([README](README.md#whats-in-v1)).
- Agents run on any OpenAI-compatible endpoint, with per-agent models and tier fallbacks ([`ai_routing.py`](api/app/services/ai_routing.py)).
- The MCP server signs clients in with OAuth 2.1 and filters tools by role and scope ([`api/app/mcp`](api/app/mcp)).
- The schema has one path: Alembic. The containers run `alembic upgrade head` before the API starts, and the API issues no DDL (#14, [`api/alembic/README.md`](api/alembic/README.md)).
- Models match the schema, checked in CI (#16).
- One studio's rules moved behind settings that are off for new installs (#13). The web app reads studio details from a public settings endpoint instead of hardcoding them (#15).
- The Connections screen offers only the integrations this server has keys for (#10).
- The BTTCO name is gone from tables, indexes and constraints (#17), from the web app and the UI package, now `@ryozan/ui` (#18), and from backend code and env vars, now `RYOZAN_*` (#20).
- Backend and frontend CI run on GitHub-hosted runners for every push and pull request (#11).

## Next

Grounded in the current code. Not yet started unless noted.

- **Finish the rename in the API contract and stored data.** These still carry the old name and need a versioned change, because clients, Stripe objects and existing rows depend on them. Named as later work in #17.
  - JSON fields and routes: `bttcoin_balance`, `bttcoin_reward`, `bttcoin_awarded` and the `/api/v1/bttcoin` wallet routes ([`rewards.py`](api/app/api/v1/endpoints/rewards.py), [`flash.py`](api/app/api/v1/endpoints/flash.py), [`documents.py`](api/app/api/v1/endpoints/documents.py), [`dashboard.py`](api/app/api/v1/endpoints/dashboard.py)); `bttco_email` ([`email_connect.py`](api/app/api/v1/endpoints/email_connect.py)).
  - Stripe metadata keys: `bttco_user_id`, `bttco_billing_kind` ([`stripe_webhooks.py`](api/app/api/v1/endpoints/stripe_webhooks.py)).
  - Stored values: `tenant_id` `"bttco"` ([`email_log.py`](api/app/api/v1/endpoints/email_log.py)), `bttco_fastapi` source labels ([`contacts.py`](api/app/api/v1/endpoints/contacts.py)), `bttco<hex>` Google Calendar event ids ([`bookings.py`](api/app/api/v1/endpoints/bookings.py)), the `X-BTTCO-Courier-Forward` header ([`email_inbound.py`](api/app/api/v1/endpoints/email_inbound.py)).
  - The `bttcoin_balance` column and the `bttco_*` keys of `/health/ready` ([`main.py`](api/main.py), [`api/alembic/README.md`](api/alembic/README.md)).
  - Later still: drop the old `BTTCO_*` env names that [`legacy_env.py`](api/app/core/legacy_env.py) still accepts with a warning (#20).
- **Integration reviews.** For each optional integration (Stripe Connect, Google, Twilio, Resend or SendGrid or SMTP, Meta, Pinterest, Plaid, Clover, Mercury, ElevenLabs, Tavily): write down setup and test steps and check them on a clean install. Notes go in [`docs/integrations/`](docs/integrations/README.md). [`integrations.py`](api/app/api/v1/endpoints/integrations.py) already reports which ones a server is configured for.
- **Feature flags for the optional extras.** Quests, RYO coin and rewards, the club feed, Viber, dev workspaces and the Guild and Loot placeholders have no off switch today ([README](README.md#optional-extras)). Add one setting per extra so a studio can run the core alone.
- **Unreferenced code.** Decide, module by module, whether to wire in or remove code that nothing imports or routes to, such as [`services/whatsapp.py`](api/app/services/whatsapp.py), [`Hotbar.tsx`](app/src/components/Hotbar.tsx) and [`DocumentHub.tsx`](app/src/pages/dashboard/DocumentHub.tsx). The full list is in #21.
- **The `ryozan-econ` ledger.** [`packages/ryozan-econ`](packages/ryozan-econ) is tested in CI but the API does not import it. Either wire it into the payment paths or document it as a separate library.

## Later

Larger changes. Direction only.

- **Hosted multi-studio service, with a tenant model.** Today one process serves one studio: studio identity and rules come from one set of settings ([`config.py`](api/app/core/config.py)), the timezone is a module constant, and the only tenant field is the fixed `"bttco"` in [`email_log.py`](api/app/api/v1/endpoints/email_log.py). Hosting many studios needs a tenant id on studio-owned rows, per-tenant settings and per-tenant keys for each integration.
- **Write actions for the finance connectors.** Mercury payments, Clover order changes, payroll and tax filing stay read-only until there is an approval table for them ([`econ.py`](api/app/api/v1/endpoints/econ.py)).
- **Guild features.** Leaderboards, class and gauntlets exist only as names on the coming-soon [`GuildPage.tsx`](app/src/pages/dashboard/GuildPage.tsx). They stay optional extras if they are built at all.
