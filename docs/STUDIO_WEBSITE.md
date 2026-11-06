# Studio website mode

By default `/` shows the Ryozan product landing, which is what the hosted demo serves. A studio running its own install can show its own public website there instead: a home page, an Our story page, a page per artist and the booking entry the app already has. Nothing on those pages is hardcoded. The words and images come from environment settings, and the artists come from the database.

## Turn it on

Set these on the API service (`api/.env` locally, the Railway service variables in production) and redeploy:

```bash
PUBLIC_SITE_MODE=studio
STUDIO_NAME=Lantern Tattoo
STUDIO_TAGLINE=Fine line and color, by appointment.
STUDIO_STORY="We opened in a small room with one chair.

Now there are three artists and a waiting list."
STUDIO_ADDRESS=1 Example Way, Springfield
STUDIO_PHONE=+1 555 0100
SHOP_EMAIL=hello@lantern.example
STUDIO_HOURS=Tue-Sat 11am-7pm\nSun-Mon closed
STUDIO_HERO_IMAGE_URL=https://media.lantern.example/front.jpg
STUDIO_GALLERY_IMAGE_URLS=https://media.lantern.example/1.jpg,https://media.lantern.example/2.jpg
STUDIO_SOCIAL_LINKS=Instagram=https://instagram.com/lantern
```

The browser reads them from `GET /api/v1/studio/settings` (public, cached for five minutes), under `site`. Check the result with:

```bash
curl -s http://localhost:18810/api/v1/studio/settings
```

Only `PUBLIC_SITE_MODE=studio` is required. Every other value is optional, and a blank one hides its section: no gallery without gallery photos, no story teaser without a story, no hours or social links in the footer without them. A blank `STUDIO_NAME` shows "Tattoo studio".

## What the visitor gets

| Path | Page | Content |
| --- | --- | --- |
| `/` | Home | Studio name and tagline over the hero photo, the first story paragraph, the artists, the gallery and a booking call to action. |
| `/our-story` | Our story | Every `STUDIO_STORY` paragraph. |
| `/artist/<slug>` | Artist | Name, title, styles, bio, links and portfolio from the artist's own profile (`GET /api/v1/artists/<slug>`), edited by the artist in the portal. |
| footer, every page | Visit | Address, hours, phone, email and social links. |

Booking buttons open `/dashboard/book`, the app's booking entry; a visitor who is not signed in goes through `/portal` first. `/portal`, `/privacy` and `/terms` work the same in both modes. In product mode `/our-story` and `/artist/<slug>` are not found.

Artists appear when they have an artist, admin or founder account with a slug. Their photo is the account avatar; their portfolio is the gallery they upload in the portal.

## Images

Image settings take an `https://` URL or a site path starting with `/`. Plain `http://`, protocol-relative `//host/...` and anything with quotes, spaces or brackets are refused when the API starts, so a typo shows up in the deploy log rather than as a broken page. The same rule applies to social links, which must be `Label=https://...` pairs.

## One install, two domains

`STUDIO_SITE_HOSTS` is only for an install that answers on a product domain and a studio domain at the same time. With `PUBLIC_SITE_MODE=product` and `STUDIO_SITE_HOSTS=lantern.example,www.lantern.example`, those two hostnames get the studio site and every other hostname gets the product landing. The match is exact, so list each hostname, without scheme or port. A studio-only install leaves it blank and sets `PUBLIC_SITE_MODE=studio`.

## Limits

- The site is chosen in the browser once the settings arrive, so nothing renders at `/` for that moment, and the product landing never flashes on a studio site. Before the script runs, and for crawlers that do not run JavaScript, the page is the static fallback in `app/index.html`, which names Ryozan rather than the studio.
- The privacy and terms pages are the app's own policies in both modes.
- Changing a setting needs a redeploy (or restart) of the API, and browsers may keep the previous values for up to five minutes.
