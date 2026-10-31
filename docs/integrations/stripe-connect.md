# Stripe Connect

The studio's Stripe account is the **platform**. Each artist onboards their own **connected account**. Client payments (deposits, quotes, flash, gift cards, POS) then become direct charges on the artist's connected account, and the platform keeps its share as an application fee. Until an artist's account can take cards and pay out, the full amount lands in the platform account and the artist's share is recorded as owed.

The platform account also runs booth rent: Stripe Checkout and subscriptions that artists pay to the studio.

Code: `api/app/api/v1/endpoints/stripe_connect.py` (onboarding, status, booth rent), `api/app/api/v1/endpoints/stripe_webhooks.py` (events), `api/app/services/payments.py` (charge routing), `api/app/services/stripe_client.py`.

## What kind of connected account the code creates

Ryozan uses the **Accounts v2** API (`stripe.v2.core.accounts.create`), not the v1 Express or Standard types. The create call sets:

```python
"identity": {"country": settings.STRIPE_CONNECT_COUNTRY.lower()},  # default "us"
"dashboard": "full",
"defaults": {
    "responsibilities": {
        "fees_collector": "stripe",
        "losses_collector": "stripe",
    }
},
"configuration": {
    "customer": {},
    "merchant": {"capabilities": {"card_payments": {"requested": True}}},
},
```

In Stripe's terms:

- `dashboard: "full"` gives the artist the full Stripe Dashboard. In the v1 vocabulary this is closest to a **Standard** account, not Express.
- `fees_collector: "stripe"` means Stripe takes its processing fee from the artist's account directly. The application fee Ryozan sets is only the studio's share.
- `losses_collector: "stripe"` means Stripe, not the studio, is liable for the artist's negative balance.
- The `customer` configuration lets the platform bill the same account for booth rent.

Onboarding is Stripe-hosted (`v2.core.account_links`, use case `account_onboarding`). Stripe collects identity and bank details. Ryozan never sees them.

## Who can use it

| Action | Endpoint | Roles |
| --- | --- | --- |
| Start or resume own onboarding | `POST /api/v1/stripe/connect/onboard` | `artist`, `founder` |
| Own status | `GET /api/v1/stripe/connect/status` | `artist`, `founder` |
| Onboard on behalf of an artist, list artists | `POST /api/v1/stripe/connect/onboard/{user_id}`, `GET /stripe/connect/artists` | `admin` and higher |

`STRIPE_CONNECT_ROLES = {"artist", "founder"}` in `integrations.py` is the single source. The Connections card is offered only to those two roles.

## What to register

In the [Stripe Dashboard](https://dashboard.stripe.com/):

1. Create or use the studio's Stripe account and activate it for live payments.
2. Open **Connect** and set up the platform. Complete the **platform profile** (the platform questionnaire). Until it is done, Stripe refuses to create connected accounts. Ryozan maps that refusal to HTTP 409 with code `stripe_connect_platform_profile_incomplete`.
3. Copy the secret key from **Developers, API keys**. Use a test key (`sk_test_...`) or a sandbox first.
4. Create the three event destinations below and copy each signing secret.

### Webhook URLs and event destinations

Stripe sends events for direct charges to a **Connected accounts** destination, and Accounts v2 thin events to a **Your account** destination. Ryozan expects three destinations:

| Destination | Listens to | Payload | URL | Signing secret variable |
| --- | --- | --- | --- | --- |
| Platform events | Your account | Snapshot | `https://studio.example.com/api/v1/stripe/webhook` | `STRIPE_WEBHOOK_SECRET` |
| Connected account events | Connected accounts | Snapshot | `https://studio.example.com/api/v1/stripe/webhook` (the same URL) | `STRIPE_CONNECT_WEBHOOK_SECRET` |
| Account readiness | Your account | Thin | `https://studio.example.com/api/v1/stripe/connect/webhook` | `STRIPE_CONNECT_THIN_WEBHOOK_SECRET` |

`/stripe/webhook` tries both snapshot secrets, so the two snapshot destinations can share one URL. Both URLs are built from the studio's own domain; there is no variable for the path.

Snapshot events the handler acts on (subscribe both snapshot destinations to the ones that apply):

- `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`
- `payment_intent.succeeded`, `payment_intent.payment_failed`, `payment_intent.canceled`, `payment_intent.amount_capturable_updated`
- `charge.succeeded`, `charge.failed`, `charge.refunded`, `charge.dispute.created`, `charge.dispute.closed`
- `refund.created`, `refund.updated`, `refund.failed`
- `customer.created`, `customer.updated`, `customer.deleted`
- `invoice.finalized`, `invoice.paid`, `invoice.payment_failed` (booth rent)
- `transfer.created`, `transfer.updated`, `transfer.reversed`, `transfer.canceled`
- `payout.paid`, `payout.failed`, `account.updated`

Thin events the readiness endpoint acts on:

- `v2.core.account[configuration.customer].capability_status_updated`
- `v2.core.account[configuration.merchant].capability_status_updated`
- `v2.core.account[requirements].updated`

### Onboarding return URL

| Production | Set by | Local default |
| --- | --- | --- |
| `https://studio.example.com/dashboard?portal=hub&workflow=connections` | `STRIPE_CONNECT_RETURN_URL` | `http://localhost:15300/dashboard?portal=hub&workflow=connections` |

Ryozan adds `stripe=return` or `stripe=refresh` to this URL and gives both to Stripe as the Account Link `return_url` and `refresh_url`. These are not registered in the Dashboard. They only need to point at your own site.

Checkout success and cancel pages are built from `FRONTEND_URL`.

## Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `STRIPE_SECRET_KEY` | Yes | blank | Turns the integration on. |
| `STRIPE_WEBHOOK_SECRET` | Yes outside development | blank | Platform snapshot destination. |
| `STRIPE_CONNECT_WEBHOOK_SECRET` | Yes once artists take payments | blank | Connected accounts snapshot destination. Without it, direct charge events from artist accounts fail signature checks. |
| `STRIPE_CONNECT_THIN_WEBHOOK_SECRET` | Yes for live readiness updates | blank | Without it, `/stripe/connect/webhook` answers 503. Readiness is still refreshed when an artist opens their status. |
| `STRIPE_CONNECT_RETURN_URL` | Yes on a hosted install | localhost | See above. |
| `STRIPE_CONNECT_COUNTRY` | Outside the US | `US` | Country of every artist's connected account, as an ISO 3166-1 alpha-2 code (`GB`, `CA`, `AU` ...). Validated at startup; an unassigned code stops the API. Stripe must support Connect in that country for your platform. |
| `FRONTEND_URL` | Yes on a hosted install | `http://localhost:15300` | Checkout return pages. |

If no webhook secret is set, `/stripe/webhook` accepts unsigned events only when `APP_ENV` is a development value or `DEBUG`/`LOCAL_DEV_MODE` is on. Otherwise it answers 503.

For local testing, the Stripe CLI can forward both scopes:

```bash
stripe listen --forward-to localhost:18788/api/v1/stripe/webhook
stripe listen --forward-connect-to localhost:18788/api/v1/stripe/webhook
stripe listen --forward-thin-to localhost:18788/api/v1/stripe/connect/webhook --thin-events "*"
```

Each command prints its own `whsec_` secret.

## How to check it works

1. `GET /api/v1/integrations/options` shows `stripe_connect` with `configured: true`, and `allowed: true` for an artist.
2. As an artist, open Connections, choose Stripe, finish Stripe's hosted onboarding. You return with `stripe=return`.
3. `GET /api/v1/stripe/connect/status` reports `charges_enabled`, `payouts_enabled` and `routing_ready`. `routing_ready` is true only when both the `card_payments` and `payouts` capabilities are `active`.
4. Pay a test deposit with card `4242 4242 4242 4242`. In the Stripe Dashboard, the event delivery for each destination shows `200`.

## Third-party review before strangers can connect

Checked against Stripe's documentation on 2026-10-31.

- **Platform profile.** A platform must complete its platform profile before creating connected accounts. Stripe's Accounts v2 errors include "Platform has not completed platform questionnaire and cannot create connected accounts", and related errors for a platform that is not activated or not verified. ([Accounts v2 API reference](https://docs.stripe.com/api/v2/core/accounts), checked 2026-10-31.)
- **Account settings.** `dashboard: "full"` gives the connected account the full Stripe Dashboard. With `fees_collector: "stripe"`, "Stripe collects payment fees directly from the connected account". With `losses_collector: "stripe"`, "Stripe is liable for the connected account's negative balances". These cannot be changed after the merchant configuration is added. Stripe collects KYC in this setup. ([Configure connected accounts](https://docs.stripe.com/connect/accounts-v2/connected-account-configuration), checked 2026-10-31.)
- **Direct charges.** Stripe recommends direct charges for connected accounts with the full Dashboard, and sends `checkout.session.completed` for them. ([Direct charges](https://docs.stripe.com/connect/direct-charges), checked 2026-10-31.)
- **Event scopes.** Direct charges arrive on a **Connected accounts** destination. Accounts v2 thin events need a **Your account** destination. Live endpoints must be public HTTPS with TLS 1.2 or higher. ([Webhooks](https://docs.stripe.com/webhooks), checked 2026-10-31.)
- **Accounts v2 limits.** Stripe says you must use Accounts v1 for OAuth-connected accounts and a few capabilities such as Treasury and Issuing. Ryozan uses none of those. ([Connect and Accounts v2](https://docs.stripe.com/connect/accounts-v2), checked 2026-10-31.)

## Known limits

- Every connected account uses the one `STRIPE_CONNECT_COUNTRY`. Artists in different countries on one install are not supported.
- Accounts v2 needs the `stripe` Python SDK pinned in `api/requirements.txt` (`stripe==15.6.1`). An older SDK lacks `client.v2.core.accounts`.
