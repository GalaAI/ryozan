# Email: outbound and inbound

Email has two halves that use different code and different providers.

- **Outbound.** Booking confirmations, deposit receipts, waivers, password resets, sign-up links and staff alerts. One function sends all of it: `send_email` in `api/app/services/email.py`. It can use SendGrid, Resend or SMTP.
- **Inbound.** Mail sent to the studio's domain: replies to Courier conversations, mail to an artist's studio handle, and mail to reserved addresses such as `info@`. Only Resend can receive. Code: `api/app/api/v1/endpoints/email_inbound.py`, with artist handles claimed through `api/app/api/v1/endpoints/email_connect.py`.

> **Read Known limits first.** Today the Resend paths and the artist handle feature are tied to the original product domain in code. A new studio can send reliably through **SendGrid** or **SMTP**. Inbound mail and Resend sending need code changes before they work on another domain.

## Who can use it

| Action | Endpoint | Roles |
| --- | --- | --- |
| Receive Ryozan's outbound mail | n/a | Everyone |
| Claim a studio email handle that forwards to a personal inbox ("Ryozan Email" card) | `POST /api/v1/email/connect`, `GET /email/status`, `DELETE /email/release` | Any signed-in user |
| Read the send log and stats | `GET /api/v1/email/log`, `GET /email/log/stats` | `founder` |

The `email_forwarding` option is configured when `RESEND_API_KEY`, `SENDGRID_API_KEY` or `SMTP_PASSWORD` is set.

## Outbound

### Which provider sends

`send_email` picks in this order:

1. `SENDGRID_API_KEY` set: send with SendGrid. On failure, try Resend if its key is set, then SMTP if it is set.
2. Otherwise `RESEND_API_KEY` set: send with Resend. On failure, try SMTP.
3. Otherwise `SMTP_USER` and `SMTP_PASSWORD` set: send with SMTP.
4. Otherwise nothing is sent and the send is logged as failed.

Every attempt is written to the email log. With `LOCAL_DEV_MODE=true`, nothing is sent and the log says `dev_skipped`. Outside production, `EMAIL_TEST_REDIRECT` sends every message to one address instead.

### The From address each provider uses

| Provider | From address | Name |
| --- | --- | --- |
| SendGrid | `SENDGRID_FROM_EMAIL` | `SENDGRID_FROM_NAME` |
| SendGrid with `SENDGRID_USE_ALIAS_FROM=true` | A fixed `noreply@`, `admin@` or `service@` address on the original product domain. Leave this off. | `Ryozan Studio` |
| Resend | Always the fixed `noreply@`, `admin@` or `service@` address on the original product domain. `RESEND_FROM_EMAIL` is not used by `send_email`. | `Ryozan Studio` |
| SMTP | `SMTP_FROM_EMAIL`, or `SMTP_USER` when blank | `SMTP_FROM_NAME` |

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

   `SENDGRID_FROM_EMAIL` defaults to an address on the original product domain. You must set it.

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

Resend is wired up, but see Known limits: `send_email` sends Resend mail from a fixed address on the original product domain, which your Resend account cannot verify, so Resend rejects it. If SendGrid or SMTP is also set, the fallback chain still delivers.

When this is fixed, the setup is: add your domain in Resend, add the SPF and DKIM DNS records Resend shows, wait for it to verify, create an API key, and set `RESEND_API_KEY`, `RESEND_FROM_EMAIL` and `RESEND_FROM_NAME`.

### Delivery webhook (Resend only)

| URL | Signing secret | Events Ryozan records |
| --- | --- | --- |
| `https://studio.example.com/api/v1/webhooks/resend` | `RESEND_WEBHOOK_SECRET` | `email.sent`, `email.delivered`, `email.bounced`, `email.complained`, `email.delivery_delayed`, `email.opened`, `email.clicked` |

In Resend, add a webhook with that URL and copy its signing secret. If `RESEND_WEBHOOK_SECRET` is blank, this endpoint accepts unsigned requests in every environment. Set it.

## Inbound

### How routing works

Resend receives mail for your domain and posts an `email.received` event to Ryozan. Ryozan fetches the message body from Resend's Receiving API with `RESEND_API_KEY`, then routes it in this order:

1. **Conversation replies.** `convo-<conversation id>@<COURIER_REPLY_DOMAIN>` is appended to that Courier conversation. The receiving server's DMARC result must be `pass`, or the message is refused with 403. Courier sets this address as the Reply-To on conversation notifications.
2. **Artist handles.** A verified handle claimed from the "Ryozan Email" card forwards to that artist's personal address.
3. **Reserved addresses.** `info`, `bookings`, `admin`, `noreply`, `support`, plus any local parts in `INBOUND_OWNER_ALIASES`, go to the owner inbox.
4. **Everything else** goes to the owner inbox.

Every message is also archived to Courier. Forwarding is sent through Resend. Automatic and already-forwarded mail is ignored to prevent loops.

### What to register

1. In Resend, enable receiving for a domain. Resend's guidance is to use a subdomain (for example `in.studio.example.com`) so its MX record does not compete with your normal mailbox's MX records. The Resend MX record must be the lowest priority value for that domain.
2. Add a webhook for the `email.received` event:

   | URL | Signing secret |
   | --- | --- |
   | `https://studio.example.com/api/v1/webhooks/email-inbound` | `RESEND_INBOUND_WEBHOOK_SECRET` |

3. Set `COURIER_REPLY_DOMAIN` to the receiving domain, for example `in.studio.example.com`. The default is the original product domain.

Outside development, the inbound endpoint answers 503 until `RESEND_INBOUND_WEBHOOK_SECRET` is set. It answers 500 if `RESEND_API_KEY` is missing.

## Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SENDGRID_API_KEY` | One provider is required | blank | |
| `SENDGRID_FROM_EMAIL` | With SendGrid | original product address | Must be on your authenticated domain. |
| `SENDGRID_FROM_NAME` | No | `Ryozan Studio` | |
| `SENDGRID_USE_ALIAS_FROM` | No | `false` | Keep `false`. |
| `RESEND_API_KEY` | For Resend sending or any inbound | blank | |
| `RESEND_FROM_EMAIL`, `RESEND_FROM_NAME` | No | original product address | Not used by `send_email` today. |
| `RESEND_WEBHOOK_SECRET` | Recommended | blank | Delivery webhook. |
| `RESEND_INBOUND_WEBHOOK_SECRET` | For inbound outside development | blank | Inbound webhook. |
| `COURIER_REPLY_DOMAIN` | For conversation replies | original product domain | Must be a DNS hostname. |
| `INBOUND_OWNER_ALIASES` | No | blank | Extra local parts for the owner inbox. |
| `SMTP_HOST`, `SMTP_PORT` | With SMTP | `smtp.gmail.com`, `587` | |
| `SMTP_USER`, `SMTP_PASSWORD` | With SMTP | blank | Both are needed to send. |
| `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME` | No | blank, `Ryozan Studio` | |
| `SHOP_EMAIL` | Recommended | blank | Reply-To on outbound mail and the shop copy on booking alerts. |
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

These are code problems that block a new studio. The code was not changed.

- `send_email` always passes a fixed sender from `_FROM_MAP` in `services/email.py` (`noreply@`, `admin@` or `service@` on the original product domain) to Resend. Resend rejects a From domain the account has not verified, so Resend sending fails for any other studio. SendGrid (with `SENDGRID_USE_ALIAS_FROM=false`) and SMTP use your configured address and are not affected.
- The sender display name in `_FROM_MAP` is fixed to `Ryozan Studio`; it does not use `STUDIO_NAME`.
- Inbound forwarding in `email_inbound.py` sends from a fixed `noreply@` address on the original product domain, so Resend refuses the forward and the webhook answers 503.
- Reserved addresses and the catch-all forward to a fixed `founder@` address on the original product domain (`FALLBACK_ROUTES` and `FOUNDER_EMAIL`), not to `SHOP_OWNER_EMAIL` or `SHOP_EMAIL`. Do not point a production domain's MX at Resend until this is fixed, or the studio's mail goes to a third party's address.
- Conversation replies accept the original product domain as well as `COURIER_REPLY_DOMAIN`.
- Artist handles in `email_connect.py` are always shown and confirmed as `handle@` the original product domain, whatever domain the studio receives on.
- The `email_forwarding` option counts `SMTP_PASSWORD` alone as configured, but sending needs `SMTP_USER` too.
- The Resend delivery webhook accepts unsigned requests when `RESEND_WEBHOOK_SECRET` is blank, in every environment.
