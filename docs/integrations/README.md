# Integration setup guides

These guides are for a studio running its own copy of Ryozan. Each one says what to register with the third party, which URLs to give it, which environment variables to set, and how to check that it works.

Every guide was written from the code on `main` as of 2026-10-31. Where the code limits what a new install can do, the guide says so under "Known limits".

## The examples use one domain

The examples assume the production layout from the root `Dockerfile`: one container serves both the API and the built web app, on one domain. The guides write that domain as `https://studio.example.com`. Replace it with your own.

Set these two first, because several callback URLs are built from them:

| Variable | Production example | Local default |
| --- | --- | --- |
| `API_PUBLIC_BASE_URL` | `https://studio.example.com` | `http://localhost:18788` |
| `FRONTEND_URL` | `https://studio.example.com` | `http://localhost:15300` |

Some callback URLs have their own variable with a localhost default. Those defaults are wrong for any hosted install, so each guide lists the variable to set.

## Where integrations show up

The Connections screen is at `/dashboard?portal=hub&workflow=connections`. A card appears only when two things are true:

1. The server has the keys for it.
2. The signed-in role may start it.

`GET /api/v1/integrations/options` reports both facts for each integration as booleans. It never returns a key. The role lists live in `api/app/api/v1/endpoints/integrations.py`.

Roles, lowest to highest: `collector`, `assistant`, `artist`, `admin`, `founder`.

## Status

| Integration | Guide | Options id | "Configured" means | Who may start it | Third-party review before strangers connect |
| --- | --- | --- | --- | --- | --- |
| Google Calendar | [google.md](google.md) | `google_calendar` | `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` set, and Calendar mode is not `workspace_delegated` | Any signed-in user (Calendar use itself needs `artist` or higher) | Yes. Calendar scope is sensitive: Google verification, or Testing mode with up to 100 test users |
| Google sign-in linking | [google.md](google.md) | `google_signin_link` | A complete sign-in client: `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, or `GOOGLE_SIGNIN_CLIENT_ID` with its own `GOOGLE_SIGNIN_CLIENT_SECRET` | `assistant` and higher | No for `openid email profile` alone |
| Pinterest | [pinterest.md](pinterest.md) | `pinterest` | `PINTEREST_CLIENT_ID` and `PINTEREST_CLIENT_SECRET` set | Any signed-in user | Yes. Trial access is sandbox only; Standard access needs a video review |
| Plaid | [plaid.md](plaid.md) | `plaid` | `PLAID_CLIENT_ID` and `PLAID_SECRET` set, and `PLAID_ENVIRONMENT` is `sandbox` or `production` | Any signed-in user | Yes. Sandbox is free; live banks need a Plaid Trial plan or Production approval |
| Stripe Connect | [stripe-connect.md](stripe-connect.md) | `stripe_connect` | `STRIPE_SECRET_KEY` set | `artist` and `founder` only | Yes. Platform profile, live account activation |
| Email (outbound and inbound) | [email.md](email.md) | `email_forwarding` | Studio email handles can run: `RESEND_API_KEY` and `COURIER_REPLY_DOMAIN` set (handles are received and forwarded by Resend) | Any signed-in user | Domain verification (SPF and DKIM) at the provider |
| Web Push (VAPID) | [push.md](push.md) | `push` | `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` set, and a subject (`VAPID_SUBJECT`, or `SHOP_EMAIL`) | Any signed-in user | None. You generate your own keys |

Plaid has an options entry, but the Connections screen does not draw a Plaid card. Plaid Link opens from the Hustle page (`/dashboard/hustle`, for `artist`, `admin` and `founder`).

## Quick check after any change

```bash
curl -s https://studio.example.com/api/v1/integrations/options
```

Signed out, every `allowed` is `null`. Signed in (send `Authorization: Bearer <token>`), `allowed` is `true` or `false` for your role. A card is drawn only when both `configured` and `allowed` are `true`.

`GET /api/v1/auth/options` is the public view the sign-in page uses. Its `google` flag turns on the Google button.
