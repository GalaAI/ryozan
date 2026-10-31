# Web Push (VAPID)

Web Push sends notifications to a user's browser or installed app for new bookings, messages, scroll updates and quest events. Each device subscribes once. Ryozan stores the subscription and sends to every device a user has subscribed.

There is no third-party account to create. The studio generates its own VAPID key pair, and the browser's push service (Google, Mozilla, Apple and others) accepts messages signed with it.

Code: `api/app/api/v1/endpoints/push.py`, `api/app/services/push.py`, the service worker at `app/public/sw.js`, and `app/src/components/PushOptIn.tsx`.

## Who can use it

| Action | Endpoint | Roles |
| --- | --- | --- |
| Read the public key | `GET /api/v1/push/vapid-key` | Anyone, no sign-in |
| Subscribe this device, unsubscribe all devices | `POST /api/v1/push/subscribe`, `DELETE /api/v1/push/subscribe` | Any signed-in user |

The Connections card "Push notifications" is offered to every signed-in role when push is configured (both keys and a subject, the same test `/push/vapid-key` applies) and the browser supports it.

## What to register

Nothing with a third party. There are no callback or webhook URLs. The browser's push service gives each subscription its own endpoint URL, which Ryozan stores.

Generate a key pair once. This is the command from `api/app/core/config.py`, run from `api/` with its virtual environment active:

```bash
python -c "from pywebpush import webpush; from py_vapid import Vapid; v=Vapid(); v.generate_keys(); from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat; import base64; b=lambda d: base64.urlsafe_b64encode(d).rstrip(b'=').decode(); print('PRIVATE:', b(v.private_key.private_numbers().private_value.to_bytes(32,'big'))); print('PUBLIC:', b(v.private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)))"
```

It prints a 43 character private key and an 87 character public key, both base64url without padding. Keep the private key secret.

Do not rotate the pair casually. Every existing subscription is bound to the public key it was made with, so new keys mean every device has to subscribe again.

## Environment variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `VAPID_PUBLIC_KEY` | Yes | blank | Served to browsers. |
| `VAPID_PRIVATE_KEY` | Yes | blank | Signs each push. |
| `VAPID_SUBJECT` | Yes, unless `SHOP_EMAIL` is set | blank | A contact URI you own: `mailto:you@studio.example.com` or `https://studio.example.com`. Anything else fails validation at startup. |
| `SHOP_EMAIL` | No | blank | When `VAPID_SUBJECT` is blank, Ryozan uses `mailto:` + `SHOP_EMAIL`. |

With keys but no subject, push stays off: `GET /push/vapid-key` answers 503 and names the missing setting, and sends are skipped with a log line. With `LOCAL_DEV_MODE=true`, sends are skipped.

## How to check it works

1. `GET /api/v1/push/vapid-key` returns `{"public_key": "..."}`. A 503 names what is missing.
2. Sign in, open Connections, and use the Push notifications card. The browser asks for permission.
3. The device appears as a row in `ryozan_push_subscriptions`, and the "enable-notifications" quest completes.
4. Trigger an event for that user, such as a new message. A notification arrives. Expired subscriptions (HTTP 410 from the push service) are deleted automatically.

The site must be served over HTTPS for a service worker and push to work. `localhost` counts as secure for development.

## Third-party review before strangers can connect

None. There is no app review for Web Push. One platform rule matters:

- **iPhone and iPad.** Web Push works on iOS and iPadOS 16.4 or later, only for a web app the user has added to the Home Screen, and the permission request must come from a user action such as tapping a button. ([WebKit: Web Push for Web Apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/), checked 2026-10-31.) On an iPhone or iPad outside an installed app, the card says push is not supported.

## Known limits

- The notification icon path is fixed to `/assets/images/logo-icon.png` on your own site.
