# Email: outbound and inbound

Email has two halves that use different code and different providers.

- **Outbound.** Booking confirmations, deposit receipts, waivers, password resets, sign-up links and staff alerts. One function sends all of it: `send_email` in `api/app/services/email.py`. It can use SendGrid, Resend or SMTP.
- **Inbound.** Mail sent to the studio's domain: replies to Courier conversations, mail to an artist's studio handle, and mail to reserved addresses such as `info@`. Only Resend can receive. Code: `api/app/api/v1/endpoints/email_inbound.py`, with artist handles claimed through `api/app/api/v1/endpoints/email_connect.py`.

Every address Ryozan sends from, forwards to, or receives on comes from your settings. Nothing in the code names a mail domain.

## Who can use it

| Action | Endpoint | Roles |
| --- | --- | --- |
| Receive Ryozan's outbound mail | n/a | Everyone |
| Claim a studio email handle that forwards to a personal inbox ("Ryozan Email" card) | `POST /api/v1/email/connect`, `GET /email/status`, `DELETE /email/release` | Any signed-in user |
| Read the send log and stats | `GET /api/v1/email/log`, `GET /email/log/stats` | `founder` |

The `email_forwarding` option (the "Ryozan Email" handle card) is configured when a handle can work end to end: `RESEND_API_KEY` and `COURIER_REPLY_DOMAIN` are set, because Resend receives and forwards handle mail. Outbound mail alone counts SMTP only when both `SMTP_USER` and `SMTP_PASSWORD` are set.

## Outbound

### Which provider sends

`send_email` picks in this order:

1. `SENDGRID_API_KEY` set: send with SendGrid. On failure, try Resend if its key is set, then SMTP if it is set.
2. Otherwise `RESEND_API_KEY` set: send with Resend. On failure, try SMTP.
3. Otherwise `SMTP_USER` and `SMTP_PASSWORD` set: send with SMTP.
4. Otherwise nothing is sent and the send is logged as failed.

Every attempt is written to the email log. With `LOCAL_DEV_MODE=true`, nothing is sent and the log says `dev_skipped`. Outside production, `EMAIL_TEST_REDIRECT` sends every message to one address instead.

### The From address each provider uses

`sender_identity()` in `services/email.py` is the one source for every provider:

| Provider | From address | Name |
| --- | --- | --- |
| SendGrid | `SENDGRID_FROM_EMAIL`, else `SHOP_EMAIL` | `SENDGRID_FROM_NAME`, else `STUDIO_NAME` |
| SendGrid with `SENDGRID_USE_ALIAS_FROM=true` | `noreply@`, `admin@` or `service@` (by message type) on the domain of the address above | as above |
| Resend | `RESEND_FROM_EMAIL`, else `SHOP_EMAIL` | `RESEND_FROM_NAME`, else `STUDIO_NAME` |
| SMTP | `SMTP_FROM_EMAIL`, else `SMTP_USER`, else `SHOP_EMAIL` | `SMTP_FROM_NAME`, else `STUDIO_NAME` |

With no From address at all, SendGrid and Resend sends fail with an error naming the variable to set, and the email log records it. The From domain must be one the provider has verified for your account.

Replies go to `SHOP_EMAIL` when it is set, unless the caller sets its own Reply-To.

### Option A: SendGrid (recommended today)

1. Create a SendGrid account (now part of Twilio).
2. Under **Settings, Sender Authentication**, authenticate your domain. SendGrid gives you CNAME records to add at your DNS host; with automated security on, those CNAMEs carry SPF and DKIM. A sending subdomain such as `mail.studio.example.com` keeps this separate from your normal mailbox.
3. Under **Settings, API Keys**, create a key with Mail Send permission.
4. Set:

   ```
   SENDGRID_API_KEY=SG.your-key
   SENDGRID_FROM_EMAIL=noreply@studio.example.com
   SENDGRID_FROM_NAME=Your Studio
   SHOP_EMAIL=hello@studio.example.com
   ```

   `SENDGRID_FROM_EMAIL` must be on your authenticated domain. Blank falls back to `SHOP_EMAIL`.

Ryozan has no SendGrid event webhook, so delivered, bounced and opened states are not tracked for SendGrid sends. Ryozan also does not use SendGrid Inbound Parse.

### Option B: SMTP

Any SMTP server that supports STARTTLS on the configured port works. Ryozan always uses STARTTLS, except for a local capture server on `localhost:1025`.

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@studio.example.com
SMTP_PASSWORD=your-app-password
SMTP_FROM_EMAIL=you@studio.example.com
SMTP_FROM_NAME=Your Studio
```

For Gmail or Google Workspace, `SMTP_PASSWORD` is an App Password, and Google requires 2-Step Verification to create one. Some work or school accounts cannot create them.

### Option C: Resend

1. Add your domain in Resend and add the SPF and DKIM DNS records it shows. Wait for it to verify.
2. Create an API key.
3. Set:

   ```
   RESEND_API_KEY=re_your-key
   RESEND_FROM_EMAIL=noreply@studio.example.com
   RESEND_FROM_NAME=Your Studio
   SHOP_EMAIL=hello@studio.example.com
   ```

Resend is also the inbound provider (below), and inbound forwards are sent from the same `RESEND_FROM_EMAIL` identity.

### Delivery webhook (Resend only)

| URL | Signing secret | Events Ryozan records |
| --- | --- | --- |
| `https://studio.example.com/api/v1/webhooks/resend` | `RESEND_WEBHOOK_SECRET` | `email.sent`, `email.delivered`, `email.bounced`, `email.complained`, `email.delivery_delayed`, `email.opened`, `email.clicked` |

In Resend, add a webhook with that URL and copy its signing secret into `RESEND_WEBHOOK_SECRET`. Outside development (`APP_ENV` other than `development`, `dev`, `local` or `test`, and `DEBUG` off) the endpoint answers 503 until the secret is set, 400 without signature headers, and 401 for a bad signature. The inbound webhook below follows the same rule.

## Inbound

### How routing works

Resend receives mail for your domain and posts an `email.received` event to Ryozan. Ryozan fetches the message body from Resend's Receiving API with `RESEND_API_KEY`, then routes it in this order:

1. **Conversation replies.** `convo-<conversation id>@<COURIER_REPLY_DOMAIN>` is appended to that Courier conversation. The receiving server's DMARC result must be `pass`, or the message is refused with 403. Courier sets this address as the Reply-To on conversation notifications.
2. **Artist handles.** A verified `<handle>@<COURIER_REPLY_DOMAIN>` claimed from the "Ryozan Email" card forwards to that artist's personal address.
3. **Reserved addresses.** `info`, `bookings`, `admin`, `noreply`, `support`, plus any local parts in `INBOUND_OWNER_ALIASES`, go to the owner inbox.
4. **Everything else** goes to the owner inbox.

The owner inbox is `SHOP_OWNER_EMAIL`, else `SHOP_EMAIL`. The Courier copy goes to the account with that address, else the oldest `founder` account. With neither address set, owner mail is archived in the app and never forwarded anywhere; the webhook answers `{"status": "archived", "reason": "no_owner_email_configured"}` and logs a warning.

Every message is also archived to Courier. Forwarding is sent through Resend from the `RESEND_FROM_EMAIL` identity (else `SHOP_EMAIL`); with no sender address, mail is archived without forwarding (`"reason": "no_sender_configured"`). Automatic and already-forwarded mail is ignored to prevent loops.

### What to register

1. In Resend, enable receiving for a domain. Resend's guidance is to use a subdomain (for example `in.studio.example.com`) so its MX record does not compete with your normal mailbox's MX records. The Resend MX record must be the lowest priority value for that domain.
2. Add a webhook for the `email.received` event:

   | URL | Signing secret |
   | --- | --- |
   | `https://studio.example.com/api/v1/webhooks/email-inbound` | `RESEND_INBOUND_WEBHOOK_SECRET` |

3. Set `COURIER_REPLY_DOMAIN` to the receiving domain, for example `in.studio.example.com`. Conversation reply addresses and artist handles are both on it, and only that domain is accepted for conversation replies. Blank (the default) turns off conversation reply addresses and handle claims (`/email/check` and `/email/connect` answer 503 naming the variable).
4. Set `SHOP_OWNER_EMAIL` (or `SHOP_EMAIL`) to the inbox that should receive `info@`, `bookings@` and the catch-all.

Outside development, the inbound endpoint answers 503 until `RESEND_INBOUND_WEBHOOK_SECRET` is set. It answers 500 if `RESEND_API_KEY` is missing.

## Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SENDGRID_API_KEY` | One provider is required | blank | |
| `SENDGRID_FROM_EMAIL` | With SendGrid, unless `SHOP_EMAIL` is on the authenticated domain | blank | Must be on your authenticated domain. |
| `SENDGRID_FROM_NAME` | No | blank (`STUDIO_NAME`) | |
| `SENDGRID_USE_ALIAS_FROM` | No | `false` | `true` sends from `noreply@`, `admin@` or `service@` on the `SENDGRID_FROM_EMAIL` domain. |
| `RESEND_API_KEY` | For Resend sending or any inbound | blank | |
| `RESEND_FROM_EMAIL` | With Resend, unless `SHOP_EMAIL` is on the verified domain | blank | Also the sender of inbound forwards. |
| `RESEND_FROM_NAME` | No | blank (`STUDIO_NAME`) | |
| `RESEND_WEBHOOK_SECRET` | For the delivery webhook outside development | blank | Without it the endpoint answers 503. |
| `RESEND_INBOUND_WEBHOOK_SECRET` | For inbound outside development | blank | Without it the endpoint answers 503. |
| `COURIER_REPLY_DOMAIN` | For conversation replies and artist handles | blank | The receiving domain. Must be a DNS hostname. Blank turns both off. |
| `SHOP_OWNER_EMAIL` | Recommended with inbound | blank | Owner inbox for reserved addresses, the catch-all and booking alerts. Blank falls back to `SHOP_EMAIL`. |
| `INBOUND_OWNER_ALIASES` | No | blank | Extra local parts for the owner inbox. |
| `SMTP_HOST`, `SMTP_PORT` | With SMTP | `smtp.gmail.com`, `587` | |
| `SMTP_USER`, `SMTP_PASSWORD` | With SMTP | blank | Both are needed to send. |
| `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME` | No | blank | Blank sends from `SMTP_USER` with `STUDIO_NAME`. |
| `SHOP_EMAIL` | Recommended | blank | Reply-To on outbound mail, fallback From address, fallback owner inbox, and the shop copy on booking alerts. |
| `EMAIL_TEST_REDIRECT` | Development only | blank | Ignored when `APP_ENV=production`. |
| `LOCAL_DEV_MODE` | Development only | `false` | `true` skips every send. |

## How to check it works

1. `GET /api/v1/auth/options` returns `"registration": true` once a provider is set. The portal then offers self sign-up.
2. Request a password reset for your own account (`POST /api/v1/auth/forgot-password`) and check the inbox.
3. As a founder, `GET /api/v1/email/log` shows the attempt, its status, and any provider error.
4. Inbound: send a message to `info@` on your receiving domain. Resend's dashboard shows the webhook delivery, and the message appears in Courier.

## Third-party review before strangers can connect

No app review is needed. What the providers require is proof you own the sending domain. Checked on 2026-10-31.

- **SendGrid.** "Twilio requires that customers authenticate or verify their sender identities", either by Single Sender verification or by Domain Authentication. ([Sender verification](https://www.twilio.com/docs/sendgrid/ui/sending-email/sender-verification), checked 2026-10-31.) Domain authentication uses CNAME records, and with automated security "Twilio SendGrid creates and maintains SPF, DKIM, and DMARC records on your behalf". You can only send from the authenticated domain, and subdomains do not inherit it. ([Domain authentication](https://www.twilio.com/docs/sendgrid/ui/account-and-settings/how-to-set-up-domain-authentication), checked 2026-10-31.)
- **Resend sending.** "You must add and verify at least one domain to send emails with Resend", and it must be a domain you own. ([Domains](https://resend.com/docs/dashboard/domains/introduction), checked 2026-10-31.)
- **Resend receiving.** Add the MX record Resend shows to a custom domain, or use the `<id>.resend.app` address Resend gives you. Resend recommends a subdomain when the domain already has MX records, because the lowest priority MX wins. ([Receiving](https://resend.com/docs/dashboard/receiving/introduction) and [custom receiving domains](https://resend.com/docs/dashboard/receiving/custom-domains), checked 2026-10-31.)
- **Resend webhooks** are signed per endpoint and verified with the `svix-id`, `svix-timestamp` and `svix-signature` headers. The secret is on the webhook details page. ([Verify webhooks](https://resend.com/docs/dashboard/webhooks/verify-webhooks-requests), checked 2026-10-31.)
- **Gmail SMTP.** "To create an app password, you need 2-Step Verification on your Google Account." ([App passwords](https://support.google.com/accounts/answer/185833), checked 2026-10-31.)

## Known limits

- Conversation reply addresses and artist handles share one receiving domain, `COURIER_REPLY_DOMAIN`. Changing it retires reply addresses already sent on the old domain.
- Ryozan has no SendGrid event webhook and does not use SendGrid Inbound Parse, so inbound mail always needs Resend.
