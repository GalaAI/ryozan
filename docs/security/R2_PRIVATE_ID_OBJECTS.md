# Private government ID objects in R2

Status: code on branch `fix/waiver-id-photos-private`. The per-install infra step below is not done
and needs the shop owner's approval for each install.

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

Every read point turns the stored reference into a short-lived SigV4 signed GET link on
`https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com/<R2_BUCKET_NAME>/<key>` (`storage.signed_id_url`):

- `POST /waiver/upload-id` (preview, 10 minutes)
- `GET /waiver/{id}` photo ID, and the `pdf_url` returned by `POST /waiver/{id}/export-pdf` (15 minutes)
- `POST /documents/checkin/verify` returning client "ID on file" (15 minutes)
- `GET /documents/scroll/my`, `GET /documents/scroll/user/{id}` (`govt_id_url`, `fields`)
- Document responses that carry raw `data_json` (`GET /documents/`, and the sign, status, upload and remind responses)
- `GET /archive/{id}/detail`

Only references on this install's own hosts resolve (the configured assets domain, this account's
`r2.dev` host, or this account's S3 endpoint with the configured bucket). Anything else passes through
unchanged and is never fetched by the server.

Write points normalize an echoed signed link back to the stored reference (`storage.canonical_id_reference`),
so a browser that submits the preview link it was shown never persists an expiring URL.

Because read points sign what is stored, every write point that stores client data runs it through
`storage.authorize_client_references` (dicts, lists and strings, recursively). A value naming an object on
our bucket (any own host form, or a bare key under `waivers/`, `signatures/`, `scrolls/`, `documents/`) is
kept only when it is an unexpired upload link this server signed, sits in the caller's own folder
(`<root>/<user id>/...`, `scrolls/<category>/<user id>/...`), or is a reference the server copied itself in
that request (ID reuse, adopted or saved signature). Anything else is refused with 422 and nothing is stored.
Founder/admin skip the check only on `POST /documents/`. Artist Scroll submit drops every server-owned key
(ID, certificate, signature, PDF, prefix) from `fields_json` and sets them only from that request's uploads.

Waiver PDFs embed the ID image bytes at generation time, fetched through a signed link, and never print
the URL. Every export rebuilds the packet from the stored reference, so re-exporting an older waiver
embeds its ID the same way. When the reference is not one of our own ID objects, or the fetch fails,
the Photo ID section still prints with a note that the ID is on file.

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
   ))
   ```

   Keep URL normalization on (Rules, Settings, Normalize incoming URLs) so encoded paths cannot slip
   past the match. WAF rules run before cache, so cached copies are blocked too.
2. R2, bucket `<R2_BUCKET_NAME>`, Settings, Public access: if the `r2.dev` subdomain is enabled, disable
   it. The custom domain stays connected for public assets.
3. Verify: a legacy `https://<assets-domain>/waivers/pending-ids/<id>.jpg` link returns 403, while staff
   can still open a waiver in Countersign, export the PDF with the ID on it, and a returning client still
   sees "ID on file" after the check-in code.

After the rule, legacy public links stop working; the app keeps working through signed links.

## Known residual exposure (not covered by this change)

- Artist Scroll PDFs (`scrolls/**/*.pdf`, `scroll_pdf_url` and the owner archive `file_url`) embed the
  artist government ID and are still public. Blocking them needs those read points signed first.
