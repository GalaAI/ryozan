# Pinterest

Each user connects their own Pinterest account. Ryozan stores a Pinterest refresh token on that user's row and mints short-lived access tokens when it needs them.

What it is used for:

- **Collectors** pull pins from one of their own boards into a booking as reference images (`POST /api/v1/auth/pinterest/booking-references/{booking_id}/{board_id}`).
- **Admins and founders** ingest a board into the References library, read account and pin analytics, and pin a published flash design to a "Flash" board.

Code: `api/app/api/v1/endpoints/pinterest.py`, `api/app/services/pinterest.py`.

## Who can use it

| Action | Endpoint | Roles |
| --- | --- | --- |
| Connect, status, disconnect, list own boards | `/api/v1/auth/pinterest/connect`, `/status`, `/disconnect`, `/boards` | Any signed-in user |
| Pull a board into a booking's references | `POST /api/v1/auth/pinterest/booking-references/{booking_id}/{board_id}` | The booking's client, or `admin` and higher |
| Analytics, board ingest, pin a flash design | `/analytics`, `/pins/{pin_id}/analytics`, `/ingest/{board_id}`, `/pin-flash/{design_id}` | `admin` and higher |

## What to register

1. Sign in at [developers.pinterest.com](https://developers.pinterest.com/) with a Pinterest business account and create an app under **My apps**.
2. Pinterest reviews the app. When approved it has **Trial** access.
3. In the app settings, add the redirect URI below.
4. Copy the App id and App secret key.

### Redirect URI

| Production | Set by | Local default |
| --- | --- | --- |
| `https://studio.example.com/api/v1/auth/pinterest/callback` | `PINTEREST_REDIRECT_URI` | `http://localhost:18788/api/v1/auth/pinterest/callback` |

The same value is sent on the authorize request and on the token exchange, so it must match the registered URI exactly. After the callback, the API sends the browser to `FRONTEND_URL` + `/dashboard?portal=hub&workflow=connections&pinterest=connected`.

## Scopes the code requests

```python
PINTEREST_SCOPES = ["boards:read", "boards:write", "pins:read", "pins:write", "user_accounts:read"]
```

The authorize URL is `https://www.pinterest.com/oauth/`. The token exchange asks for `continuous_refresh`. The write scopes are only needed by the staff "pin a flash design" action. The module docstring lists fewer scopes; the list above is what the code sends.

## Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `PINTEREST_CLIENT_ID` | Yes | blank | App id. |
| `PINTEREST_CLIENT_SECRET` | Yes | blank | App secret key. Sent as HTTP Basic auth on token calls. |
| `PINTEREST_REDIRECT_URI` | Yes on a hosted install | `http://localhost:18788/api/v1/auth/pinterest/callback` | Must match the app settings. |
| `PINTEREST_USE_SANDBOX` | No | `true` | `true` sends every API call, including the token exchange, to the sandbox host. |
| `PINTEREST_SANDBOX_API_BASE` | No | `https://api-sandbox.pinterest.com/v5` | |
| `PINTEREST_API_BASE` | No | `https://api.pinterest.com/v5` | Used when `PINTEREST_USE_SANDBOX=false`. |

### Sandbox or production host

With `PINTEREST_USE_SANDBOX=true` (the default), Ryozan talks to the sandbox host only. Sandbox boards and pins are separate from a user's real ones, so a collector will not see their real boards. This setting is for testing the connect flow.

For real boards, set `PINTEREST_USE_SANDBOX=false`. Pinterest lists reading boards and Pins as allowed on Trial access, at Trial rate limits. Creating boards and Pins (the flash pin action) needs Standard access to be visible to anyone but the creator.

## How to check it works

1. `GET /api/v1/integrations/options` shows `pinterest` with `configured: true`.
2. Open Connections, choose Pinterest, approve. You land back with `pinterest=connected`, and the card shows `@username`.
3. `GET /api/v1/auth/pinterest/status` returns `connected: true`.
4. `GET /api/v1/auth/pinterest/boards` lists boards. This is the real proof the token works.

## Third-party review before strangers can connect

Checked against Pinterest's developer documentation on 2026-10-31.

- **Trial access.** An approved app starts here. Trial is rate limited per day per app, and "all Pins and Boards created with Trial access are only visible to their creator as Sandbox entities". ([Access tiers](https://developers.pinterest.com/docs/key-concepts/access-tiers/), checked 2026-10-31.)
- **Standard access.** Request it from My apps with **Upgrade**. You need Trial approval, a privacy policy link, and a video of your app "completing an action using the Pinterest API" that shows the OAuth flow. Pinterest asks for the OAuth video even if you are the only user. ([Access tiers](https://developers.pinterest.com/docs/key-concepts/access-tiers/), checked 2026-10-31.)
- **New apps and personal accounts.** Apps registered on or after September 14, 2026 may get HTTP 403 `PINNER_DATA_ACCESS_DENIED` when reading a board or Pin of a non-business user without Pinterest's authorization. Most collectors have personal accounts, so a newly registered app may not be able to read their boards. ([Access tiers](https://developers.pinterest.com/docs/key-concepts/access-tiers/), checked 2026-10-31.)
- **Sandbox tokens.** Pinterest supports getting a sandbox token through OAuth by calling `oauth/token` on the `api-sandbox` host, which is what Ryozan does in sandbox mode. Moving to production means dropping `-sandbox` from the host and using a production token. ([Sandbox](https://developers.pinterest.com/docs/developer-tools/sandbox/), checked 2026-10-31.)

## Known limits

- Switching `PINTEREST_USE_SANDBOX` does not convert stored tokens. Users who connected in sandbox mode must disconnect and connect again.
- The staff analytics, ingest and flash pin actions use the staff member's own Pinterest connection, not a shop-wide account.
