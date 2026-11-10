# Private government ID objects in R2

Status: code on branches `fix/waiver-id-photos-private` (ID photos, waiver PDFs, scroll IDs) and
`fix/scroll-pdf-private` (Artist Scroll PDFs). The per-install infra step below is not done and needs
the shop owner's approval for each install.

## Why

Each Ryozan install stores its media in its own R2 bucket (`R2_BUCKET_NAME`), usually served publicly
through the shop's assets domain (`R2_PUBLIC_DOMAIN`). Client and artist government ID images were
stored there and handed out as permanent public URLs, so anyone holding a link could open a person's ID.

## What the app does now

Stored references are unchanged (no migration). The app treats these R2 keys as private:

| Key pattern | What it holds | Written by |
| --- | --- | --- |
| `waivers/pending-ids/*` | Walk-in waiver ID photo | `POST /waiver/upload-id` |
| `waivers/**/*.pdf` | Printable waiver packet, embeds the client ID image | `POST /waiver/{id}/export-pdf` |
| `scrolls/**/govt-id*` | Artist Scroll government ID | `POST /documents/scroll/submit` |
| `scrolls/**/*.pdf` | Artist Scroll PDF, embeds the artist government ID image; also any PDF uploaded to a scroll category | `POST /documents/scroll/submit`, `POST /scrolls/{slug}/upload` |

Other scroll assets (certificate images, signature PNGs, non-PDF category uploads) stay public.

Every read point turns the stored reference into a short-lived SigV4 signed GET link on
`https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com/<R2_BUCKET_NAME>/<key>` (`storage.signed_id_url`):

- `POST /waiver/upload-id` (preview, 10 minutes)
- `GET /waiver/{id}` photo ID, and the `pdf_url` returned by `POST /waiver/{id}/export-pdf` (15 minutes)
- `POST /documents/checkin/verify` returning client "ID on file" (15 minutes)
- `GET /documents/scroll/my`, `GET /documents/scroll/user/{id}` (`govt_id_url`, `fields`, including
  `fields.scroll_pdf_url`)
- `POST /documents/scroll/submit` response `scroll_pdf_url` (15 minutes)
- Document responses (`GET /documents/`, and the sign, status, upload and remind responses): `file_url`
  and raw `data_json`, including the owner archive copy's top-level `scroll_pdf_url`, `certs_stored`
  and nested `fields`
- `GET /scrolls/{slug}` list and `POST /scrolls/{slug}/upload` response `file_url` (15 minutes), which
  is where the owner opens the Artist Scrolls archive PDF
- `GET /archive/{id}/detail` (`data`, including nested `fields` of an owner archive copy)

Only references on this install's own hosts resolve (the configured assets domain, this account's
`r2.dev` host, or this account's S3 endpoint with the configured bucket). Anything else passes through
unchanged and is never fetched by the server.

Write points normalize an echoed signed link back to the stored reference (`storage.canonical_id_reference`),
so a browser that submits the preview link it was shown never persists an expiring URL.

Waiver PDFs embed the ID image bytes at generation time, fetched through a signed link, and never print
the URL. Every export rebuilds the packet from the stored reference, so re-exporting an older waiver
embeds its ID the same way. When the reference is not one of our own ID objects, or the fetch fails,
the Photo ID section still prints with a note that the ID is on file.

Artist Scroll PDFs embed the government ID image bytes at generation time (the upload itself, not a
re-download) and print "Private (staff open it through the app)" instead of the ID URL. The PDF never
contains its own URL or the ID URL. The server alone sets `scroll_pdf_url`; a value sent in the
submitted fields is dropped.

The owner email for a new scroll attaches the PDF bytes and links only to the authenticated Artist
Scrolls page in the app, so no storage URL (public or signed) travels by email. A signed link in an
email would have to outlive the SigV4 seven-day cap to stay useful in an inbox, and anyone it was
forwarded to could open the ID until it expired; the attachment plus app link avoids both. The in-app
notification links to the same app page.

Signed links keep working whether or not the assets domain serves the object, so the app keeps working
once the public path is blocked.

## Remaining infra step (owner approval required, not performed)

Do this per install. Replace `<assets-domain>` with that install's `R2_PUBLIC_DOMAIN` and `<zone>` with
its Cloudflare zone.

1. Cloudflare dashboard, zone `<zone>`, Security, WAF, Custom rules. Add a rule, action **Block**,
   expression:

   ```
   (http.host eq "<assets-domain>" and (
     starts_with(http.request.uri.path, "/waivers/pending-ids/")
     or (starts_with(http.request.uri.path, "/waivers/") and ends_with(lower(http.request.uri.path), ".pdf"))
     or (starts_with(http.request.uri.path, "/scrolls/") and http.request.uri.path contains "/govt-id")
     or (starts_with(http.request.uri.path, "/scrolls/") and ends_with(lower(http.request.uri.path), ".pdf"))
   ))
   ```

   Keep URL normalization on (Rules, Settings, Normalize incoming URLs) so encoded paths cannot slip
   past the match. WAF rules run before cache, so cached copies are blocked too.
2. R2, bucket `<R2_BUCKET_NAME>`, Settings, Public access: if the `r2.dev` subdomain is enabled, disable
   it. The custom domain stays connected for public assets.
3. Verify: legacy `https://<assets-domain>/waivers/pending-ids/<id>.jpg` and
   `https://<assets-domain>/scrolls/<user>/<submission>/artist-scroll.pdf` links return 403, while staff
   can still open a waiver in Countersign, export the PDF with the ID on it, a returning client still
   sees "ID on file" after the check-in code, and the owner can open a scroll PDF from Artist Scrolls.

After the rule, legacy public links stop working; the app keeps working through signed links.

## Known residual exposure

- Until the WAF rule above is live on an install, any legacy public link to these objects still opens.
- In this repository's history the owner email builder never received the PDF URL (it attaches the
  bytes), so past owner mail should hold no scroll PDF link. Mail sent by older forks is not covered.
