# Plaid

Plaid links a user's bank account. Ryozan then reads account balances and syncs bank transactions into the Econ ledger, where the shop tracks money in and out.

Each user links their own bank. The Plaid access token is stored on that user's row (`plaid_access_token`). There is no shop-wide bank link.

Code: `api/app/api/v1/endpoints/plaid.py`, and the Econ status and sync in `api/app/api/v1/endpoints/econ.py`.

## Who can use it

| Action | Endpoint | Roles |
| --- | --- | --- |
| Create a Link token, exchange the public token, status, disconnect, balances | `POST /api/v1/plaid/link-token`, `POST /plaid/exchange-token`, `GET /plaid/status`, `DELETE /plaid/disconnect`, `GET /plaid/balance` | Any signed-in user |
| Sync transactions into the ledger | `POST /api/v1/plaid/transactions/sync` | `assistant` and higher |

`GET /api/v1/integrations/options` offers `plaid` to every signed-in role. The Connections screen does not draw a Plaid card. Plaid Link opens from the Hustle page at `/dashboard/hustle`, which the web app shows to `artist`, `admin` and `founder`.

## What to register

1. Create a team at [dashboard.plaid.com](https://dashboard.plaid.com/).
2. Under **Developers, Keys**, copy the `client_id` and the secret for the environment you will use. Sandbox and Production have different secrets.
3. Plaid Link runs in the browser through `react-plaid-link`. Ryozan does not send a `redirect_uri` or a `webhook` when it creates a Link token, so there is no callback URL or webhook URL to register for this integration.

## What the code asks Plaid for

From `create_link_token`:

```python
products=[Products("transactions")],
country_codes=[CountryCode("US")],
language="en",
client_name=settings.APP_NAME,
```

So Link offers US institutions only, with the Transactions product. `client_name` is `APP_NAME`, which defaults to `Ryozan API`; Plaid shows it to the user inside Link.

## Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `PLAID_CLIENT_ID` | Yes | blank | |
| `PLAID_SECRET` | Yes | blank | Must match `PLAID_ENVIRONMENT`. |
| `PLAID_ENVIRONMENT` | No | `sandbox` | `sandbox` or `production`, the two environments `plaid-python` 42.0.0 defines. Any other value (including `development`, which Plaid shut down) makes every Plaid call answer 503 naming `PLAID_ENVIRONMENT`, and the `plaid` option reports `configured: false`. |
| `APP_NAME` | No | `Ryozan API` | Shown in Plaid Link as the app name. Set it to your studio name. |

## How to check it works

1. `GET /api/v1/integrations/options` shows `plaid` with `configured: true`.
2. As an artist, open `/dashboard/hustle` and start the bank link. In Sandbox, pick any institution and sign in with `user_good` / `pass_good`.
3. `GET /api/v1/plaid/status` returns `connected: true`.
4. `GET /api/v1/plaid/balance` lists accounts.
5. `POST /api/v1/plaid/transactions/sync` returns transactions and writes them to the ledger.

## Third-party review before strangers can connect

Checked against Plaid's documentation on 2026-10-31.

- **Sandbox** is free and fully featured, with test institutions only. The default Sandbox login for every institution is `user_good` / `pass_good`. ([Sandbox overview](https://plaid.com/docs/sandbox/), checked 2026-10-31.)
- **Development** no longer exists. Plaid decommissioned it on June 20, 2024. ([Changelog](https://plaid.com/docs/changelog/), checked 2026-10-31.)
- **Live banks.** Teams created on or after April 15, 2026 get free Trial plans (US and Canada only) instead of Limited Production. Older teams may still have Limited Production. Full Production access is requested from the Plaid Dashboard. ([Sandbox overview](https://plaid.com/docs/sandbox/), checked 2026-10-31.) The Dashboard's Launch Center gives the per-team list of steps ([Launch checklist](https://plaid.com/docs/launch-checklist/), checked 2026-10-31).
- **Transactions webhooks.** Plaid says webhooks are "required or strongly recommended" for Transactions when you read transactions made after the user linked. Ryozan has no Plaid webhook; it syncs when someone asks. ([Launch checklist](https://plaid.com/docs/launch-checklist/), checked 2026-10-31.)

## Known limits

- US institutions only (`CountryCode("US")`).
- No Plaid webhook, so new transactions arrive only when a sync runs.
- One bank link per user. Linking again replaces the stored access token.
