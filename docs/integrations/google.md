# Google sign-in and Google Calendar

One Google OAuth client powers two features. Sign-in may use a second client of its own (see `GOOGLE_SIGNIN_CLIENT_ID` below):

- **Google sign-in.** Anyone can sign in to the portal with a Google account. A new Google user becomes a `collector`. Staff whose Google address differs from their portal address can link their Google login from Connections.
- **Google Calendar.** An artist connects their own Google Calendar. Ryozan then reads free/busy time for booking slots, writes booking events, and can import upcoming events as bookings.

Code: `api/app/api/v1/endpoints/auth.py` (sign-in), `api/app/api/v1/endpoints/google_auth.py` (Calendar connect), `api/app/api/v1/endpoints/calendar.py` and `api/app/services/calendar_sync.py` (Calendar use).

## Who can use it

| Action | Endpoint | Roles |
| --- | --- | --- |
| Sign in with Google | `GET /api/v1/auth/google/start`, `POST /api/v1/auth/google` | Anyone |
| Link a Google login to an existing account | `GET /api/v1/auth/google/link` | `assistant` and higher |
| Connect Google Calendar | `GET /api/v1/auth/google/connect` | Any signed-in user |
| Sync, import, create or edit Calendar events | `/api/v1/calendar/sync/...`, `/calendar/import`, `/calendar/events` | `artist` and higher |

The Google Calendar card is offered to every signed-in role (`GOOGLE_CALENDAR_ROLES = ALL_SIGNED_IN`), but only `artist` and higher can do anything with a connected calendar. The Google sign-in link card has its own options entry, `google_signin_link`, offered to `assistant` and higher (`GOOGLE_SIGNIN_LINK_ROLES`), the same gate as `GET /auth/google/link`.

## What to register

In the [Google Cloud console](https://console.cloud.google.com/):

1. Create a project, or pick one.
2. Enable the **Google Calendar API** (APIs and Services, Library). Skip this if you only want sign-in.
3. Open **Google Auth Platform** and fill in **Branding**: app name, support email, home page, privacy policy, and your authorized domain (`studio.example.com`).
4. Under **Audience**, choose **External**. Choose **Internal** only if every user is in your own Google Workspace organization.
5. Under **Data Access**, add the scopes listed below.
6. Under **Clients**, create an OAuth client of type **Web application**.

### Authorized JavaScript origins

```
https://studio.example.com
```

This is your `FRONTEND_URL`. The quote confirmation page loads Google Identity Services in the browser, and Google only accepts that from a listed origin. For local work also add `http://localhost:15300`.

### Authorized redirect URIs

Add both. They must match byte for byte.

| Purpose | Production URI | How Ryozan builds it | Local default |
| --- | --- | --- | --- |
| Sign-in and login linking | `https://studio.example.com/api/v1/auth/google/callback-signin` | `API_PUBLIC_BASE_URL` + `/api/v1/auth/google/callback-signin`. No separate variable. | `http://localhost:18788/api/v1/auth/google/callback-signin` |
| Calendar connect | `https://studio.example.com/api/v1/auth/google/callback` | The value of `GOOGLE_REDIRECT_URI` | `http://localhost:18788/api/v1/auth/google/callback` |

`GOOGLE_REDIRECT_URI` defaults to the localhost value. Set it on every hosted install, or Calendar connect sends Google a localhost callback.

After Google returns, the API redirects the browser to `FRONTEND_URL`: `/portal` for sign-in, and `/dashboard?portal=hub&workflow=connections` for Calendar and linking.

## Scopes the code requests

Sign-in and linking (`auth.py`):

```
"scope": "openid email profile"
```

Calendar connect (`google_auth.py`):

```python
IDENTITY_SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]
LEGACY_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
```

Calendar connect asks for `access_type="offline"` and `prompt="consent"` so Google returns a refresh token. Ryozan stores that refresh token on the user row.

### Would a narrower Calendar scope do?

Probably. The code only calls these Calendar methods: `calendars.get`, `events.insert`, `events.patch`, `events.get`, `events.delete`, `events.list` and `freebusy.query`. In OAuth mode it only touches the user's `primary` calendar. It never shares, creates or deletes whole calendars, which is what the full `calendar` scope adds.

A narrower set that looks sufficient is `calendar.events` (or `calendar.events.owned`, since only the user's own primary calendar is used), plus `calendar.freebusy` and `calendar.calendars.readonly`. Google describes the full scope as "See, edit, share, and permanently delete all the calendars you can access", which is more than Ryozan needs. Check each method's accepted scopes in the Calendar API reference before switching. The code has not been changed; the scope lists live in `google_auth.py` and `calendar_sync.py`.

A narrower scope does not skip review. Calendar scopes are still sensitive. It does make the consent screen ask for less and makes the verification justification easier.

## Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `GOOGLE_CLIENT_ID` | Yes | blank | Web application client id. |
| `GOOGLE_CLIENT_SECRET` | Yes | blank | Must be the secret of the same client. |
| `GOOGLE_REDIRECT_URI` | Yes for Calendar on a hosted install | `http://localhost:18788/api/v1/auth/google/callback` | Calendar connect callback. |
| `API_PUBLIC_BASE_URL` | Yes on a hosted install | `http://localhost:18788` | Builds the sign-in callback. |
| `FRONTEND_URL` | Yes on a hosted install | `http://localhost:15300` | Where the API sends the browser afterwards. |
| `VITE_GOOGLE_CLIENT_ID` | For the in-browser Google button on the quote confirmation page | blank | Build-time variable for the web app. Pass it as a Docker build arg. Use the same id as `GOOGLE_CLIENT_ID`. |
| `GOOGLE_SIGNIN_CLIENT_ID` | No | blank | A separate OAuth client for sign-in and login linking. Blank: sign-in uses `GOOGLE_CLIENT_ID`. Build the web app with `VITE_GOOGLE_CLIENT_ID` set to this id. |
| `GOOGLE_SIGNIN_CLIENT_SECRET` | With a separate `GOOGLE_SIGNIN_CLIENT_ID` | blank | The secret of that sign-in client, sent in the sign-in token exchange. Without it, sign-in reports itself unconfigured (`/auth/options` `google: false`, `/auth/google/link` answers 503 naming this variable). |
| `GOOGLE_REFRESH_TOKEN` | No | blank | Shop-wide fallback refresh token used when a user has none. |
| `GOOGLE_CALENDAR_AUTH_MODE` | No | `oauth` | `oauth` (each artist connects) or `workspace_delegated`. |
| `GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON` | Only for `workspace_delegated` | blank | The full service account key JSON. |
| `GOOGLE_CALENDAR_DELEGATED_USER` | Only for `workspace_delegated` | blank | The Workspace user the service account acts as. |
| `GOOGLE_CALENDAR_DEFAULT_ID` | No | blank | Calendar id used when no artist calendar is named. |

### Workspace delegated mode

If your studio uses Google Workspace, you can skip per-artist consent. Set `GOOGLE_CALENDAR_AUTH_MODE=workspace_delegated`, create a service account, give it domain-wide delegation for `https://www.googleapis.com/auth/calendar` in the Workspace admin console, and set the two variables above. Each artist's calendar id is their `google_calendar_id`, or their email when that is blank. Share each artist calendar with the delegated user.

In this mode `GET /auth/google/connect` answers 409, and the Google Calendar card is not offered.

## How to check it works

1. `GET /api/v1/auth/options` returns `"google": true`. The portal sign-in page then shows the Google button.
2. Sign in with Google at `/portal`. A failure comes back as `?gsi_error=` with `unconfigured`, `cancelled`, `state`, `exchange` or `invalid`.
3. As an artist, open Connections, choose Google Calendar, and approve. You land back on Connections with `google=connected`. `google=error&reason=no_refresh_token` means Google sent no refresh token; remove the app at [myaccount.google.com/permissions](https://myaccount.google.com/permissions) and connect again.
4. `GET /api/v1/auth/google/status` returns `connected`, `healthy` and `calendar_accessible`.
5. `GET /api/v1/calendar/upcoming` returns your next events.

## Third-party review before strangers can connect

Checked against Google's documentation on 2026-10-31.

- **Testing mode.** A new External app starts in Testing. Only listed test users can authorize it, up to 100 test users. Google: "Authorizations by a test user will expire seven days from the time of consent." ([Manage App Audience](https://support.google.com/cloud/answer/15549945), checked 2026-10-31.)
- **Seven-day refresh tokens.** In Testing, refresh tokens expire after 7 days "unless the only OAuth scopes requested are a subset of name, email address, and user profile". So sign-in keeps working, but every artist's Calendar connection breaks after a week until they reconnect. ([Using OAuth 2.0](https://developers.google.com/identity/protocols/oauth2), checked 2026-10-31.)
- **In production without verification.** Users see the unverified app screen, and an app that shows it is capped at 100 new users in total. ([Manage App Audience](https://support.google.com/cloud/answer/15549945), checked 2026-10-31.)
- **Sensitive, not restricted.** Google uses "reading events stored in Google Calendar" as its example of a sensitive scope. Sensitive scope verification needs verified domain ownership in Search Console, a privacy policy on your domain, a demo video of the consent flow, and a justification for each scope, including why a narrower scope will not work. Google says it "can take up to 10 days". ([Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification), checked 2026-10-31.) Restricted scopes add an annual third-party security assessment ([Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification), checked 2026-10-31). Google's restricted list covers Gmail, Drive, Chat and Meet, not Calendar ([Workspace API user data policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy), checked 2026-10-31).
- **Sign-in only.** `openid email profile` are not sensitive, so a sign-in only app can publish without that review. Ryozan asks for Calendar only when a user starts Calendar connect, but the scopes are configured on one client.
- **Internal apps.** With Audience set to Internal, only your Workspace users can sign in, and sensitive scopes need no Google review.

## Known limits

- The sign-in callback has no variable of its own. It always follows `API_PUBLIC_BASE_URL`.
- Any signed-in user, including a collector, can grant the full Calendar scope from the Connections card even though only staff can use it.
