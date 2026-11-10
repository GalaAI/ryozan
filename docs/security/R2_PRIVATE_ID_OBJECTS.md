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
| `scrolls/**/govt-id*` | Artist Scroll government ID (image, or an ID uploaded as a PDF) | `POST /documents/scroll/submit` |
| `scrolls/**/*.id.<ext>` | Generated Artist Scroll PDF that embeds the artist government ID image (`artist-scroll.id.pdf`), and any scroll category upload marked "This file is an ID" (`<random>.id.<ext>`) | `POST /documents/scroll/submit`, `POST /scrolls/{slug}/upload` with `is_id=true` |
| `scrolls/**/artist-scroll.pdf` | Legacy generated Artist Scroll PDF, stored before the marker names | earlier `POST /documents/scroll/submit` |

Generated scroll PDFs are named by content. The server knows at generation time whether it embedded the
government ID image (`scroll_pdf.build_scroll_pdf_report`, `embeds_government_id`): with the ID the PDF
is stored as `artist-scroll.id.pdf` (private), without it (no ID supplied, or an ID uploaded as a PDF,
which cannot be embedded) as `artist-scroll.noid.pdf` (public). Legacy `artist-scroll.pdf` objects stay
private by key: the app form requires a government ID, so a legacy generated PDF is taken to carry it,
and the plain name is never written again.

Public, unchanged: `artist-scroll.noid.pdf`, files uploaded to scroll categories without the ID mark
(`scrolls/<slug>/<user>/<random>.<ext>`), certificate images and signature PNGs. An upload marked as an
ID is also never sent to the Gala AI digest. Scroll certificate uploads need no mark: the one ID upload
on the scroll form is already stored as `govt-id.<ext>`.

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
  is where the owner opens the Artist Scrolls archive PDF (category uploads come back unchanged)
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
re-download). The PDF never prints a government ID link, whatever its key, and never contains its own
URL: the ID page reads "Private (staff open it through the app)". The server alone sets
`scroll_pdf_url`; a value sent in the submitted fields is dropped.

Owner emails that were already sent before this change kept the PDF bytes in the outbox table. A boot
step (`scroll_archive.clear_sent_owner_email_attachments`, run from `main.py`'s startup seeds) clears
`attachment_content` on every `sent` row that still holds it, logging only the count found and the
count cleared. It is idempotent: the next boot clears 0.

The owner email for a new scroll is link only: no attachment and no storage link (public or signed).
Its one link is the authenticated Artist Scrolls page in the app, so an ID never lands in an inbox. A
signed link in an email would stop working after the SigV4 seven-day cap and would open the ID for
anyone the mail was forwarded to until then. Outbox rows queued before this change are sent without
their attachment, their stored PDF bytes are cleared when the row is claimed, and a stored body that
still says the PDF is attached is rewritten to the link-only wording before it is sent. The in-app
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
     or (starts_with(http.request.uri.path, "/scrolls/") and lower(http.request.uri.path) contains ".id.")
     or (starts_with(http.request.uri.path, "/scrolls/") and ends_with(lower(http.request.uri.path), "/artist-scroll.pdf"))
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
   An `artist-scroll.noid.pdf` link and a scroll category PDF link still open publicly.

After the rule, legacy public links stop working; the app keeps working through signed links.

## Known residual exposure

- Until the WAF rule above is live on an install, any legacy public link to these objects still opens.
- Owner emails sent before this change carried the scroll PDF as an attachment, so those inboxes may
  already hold an artist's ID. Sent mail cannot be recalled by the app; the owner decides whether to
  delete those messages.
- Scroll category uploads are public unless the uploader ticks "This file is an ID". An ID uploaded
  without the mark is not detected and stays public. Earlier category uploads have no mark at all.
- Outbox rows that reached `exhausted` (never delivered) keep their stored bytes: they are neither sent
  nor claimed again, and the boot step clears only `sent` rows.
