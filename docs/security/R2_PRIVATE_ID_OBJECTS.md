# Private government ID objects in R2

Status: code on branches `fix/waiver-id-photos-private` (ID photos, waiver PDFs, scroll IDs) and
`fix/scroll-pdf-private` (Artist Scroll PDFs). The per-install infra step below is not done and needs
the shop owner's approval for each install.

## Why

Each Ryozan install stores its media in its own R2 bucket (`R2_BUCKET_NAME`), usually served publicly
through the shop's assets domain (`R2_PUBLIC_DOMAIN`). Client and artist government ID images were
stored there and handed out as permanent public URLs, so anyone holding a link could open a person's ID.

## What the app does now

Stored references are unchanged (no migration, nothing moved, nothing deleted, no file opened to
decide). The app treats these R2 keys as private:

| Key pattern | What it holds | Written by |
| --- | --- | --- |
| `waivers/**` (everything) | Walk-in waiver ID photos (`waivers/pending-ids/`), printable waiver packets that embed the client ID (`waivers/<month>/<name>_<ref>/<name>_<ref>.pdf`) and the client's signature image (`.../signature.png`) | `POST /waiver/upload-id`, `POST /waiver/{id}/export-pdf`, the waiver and check-in signature uploads |
| `signatures/**` (everything) | Artists' saved signatures, used to countersign waivers | `POST /users/me/signature` |
| `scrolls/**` (everything) | Every Artist Scroll and scroll category object written before this change (government IDs, generated scroll PDFs, certificates, signatures, category uploads), plus every new file that is or embeds an ID | `POST /documents/scroll/submit`, `POST /scrolls/{slug}/upload` |

Older scroll files were never inspected, so any of them may hold an ID; all of them are private by key
and served through signed links. New files are routed by content:

| New file | Stored under |
| --- | --- |
| Government ID upload on the scroll form | `scrolls/<user>/<submission>/govt-id.<ext>` (private) |
| Generated scroll PDF that embeds the ID image | `scrolls/<user>/<submission>/artist-scroll.id.pdf` (private) |
| Scroll category upload marked "This file is an ID" | `scrolls/<slug>/<user>/<random>.id.<ext>` (private, never sent to the Gala AI digest) |
| Generated scroll PDF without an embedded ID (no ID supplied, or an ID uploaded as a PDF, which cannot be embedded) | `scrolls/<user>/<submission>/artist-scroll.noid.pdf` (private: it still prints the artist signature and the artist's details) |
| Signature PNGs from the scroll form (artist and owner) | `scrolls/<user>/<submission>/` (private) |
| Certificates from the scroll form | `scrolls-public/<user>/<submission>/` (public) |
| Unmarked scroll category upload | `scrolls-public/<slug>/<user>/<random>.<ext>` (public) |

The server knows at generation time whether it embedded the government ID image
(`scroll_pdf.build_scroll_pdf_report`, `embeds_government_id`). The `.id.<ext>` marker is kept on new
private files so they read as IDs wherever they are listed. The `.id`/`.noid` PDF names record only
whether the ID image is embedded; privacy comes from the prefix, and every generated scroll PDF is
private.

Generated `artist-scroll*.pdf` files written under `scrolls-public/` before that rule are moved at boot
by a one-shot background task (`scroll_archive.move_public_scroll_pdfs`, started from `main.py`'s
lifespan, so R2 never holds up startup): read the public object, write the same bytes to the private
twin key (same folder and file name), read the copy back and compare SHA-256, repoint every referencing
artist scroll and owner archive record, commit, and only then delete the public object, and only when no
document or Gala memory still mentions it. Any storage failure keeps the original reference and the
public object ("failed"); the next boot tries again. The reference rewrite and a pending row in
`ryozan_scroll_public_pdf_deletions` (Alembic revision `20261110_scroll_public_pdf_del`) land in one
commit, before any delete. The row is marked `deleted` only once R2 confirms; otherwise it stays
`pending` and backs off (up to an hour, never giving up), retried by the next boot and by the outbox
loop (`scroll_archive.retry_public_scroll_pdf_deletions_once`). An object another record still mentions
is marked `kept`, not retried. So a delete R2 did not confirm, or one a stopped process never ran, never
leaves a signature-bearing PDF public with nothing able to find it again. It logs one line of counts (pdfs, moved, failed,
public deleted, public pending delete, public kept, unproven records skipped) and nothing else. Idempotent: a moved record no longer references a
public PDF. A boot step
(`scroll_archive.count_legacy_scroll_references`) counts document rows that still reference
`scrolls/` objects and logs only that number; it changes nothing.

Every read point turns the stored reference into a short-lived SigV4 signed GET link on
`https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com/<R2_BUCKET_NAME>/<key>` (`storage.signed_id_url`):

- `POST /waiver/upload-id` (preview, 10 minutes)
- `GET /waiver/{id}` photo ID and client signature, and the `pdf_url` returned by
  `POST /waiver/{id}/export-pdf` (15 minutes)
- `POST /documents/checkin/verify` returning client "ID on file" and the signature on file (15 minutes)
- `GET /users/me/signature` and `POST /users/me/signature` (the artist's saved signature, 1 hour, for
  the countersign screen); `POST /waiver/{id}/countersign` stores the stable reference even when the
  app sends that signed link back
- `GET /documents/scroll/my`, `GET /documents/scroll/user/{id}` (`govt_id_url`, `artist_signature`,
  the certificate and signature URLs, and every private reference in `fields`)
- `POST /documents/scroll/submit` response `scroll_pdf_url` (15 minutes)
- Document responses (`GET /documents/`, and the sign, status, upload and remind responses): `file_url`
  and raw `data_json`, including the owner archive copy's top-level `scroll_pdf_url`, `certs_stored`
  and nested `fields`
- `GET /scrolls/{slug}` list and `POST /scrolls/{slug}/upload` response `file_url` (15 minutes), which
  is where the owner opens the Artist Scrolls archive PDF (category uploads come back unchanged)
- `GET /archive/{id}/detail` (`data`: the photo ID, `signature_url`, `artist_countersignature` and
  every other private reference, including nested `fields` of an owner archive copy)

Only references on this install's own hosts resolve (the configured assets domain, this account's
`r2.dev` host, or this account's S3 endpoint with the configured bucket). Anything else passes through
unchanged and is never fetched by the server.

Write points normalize an echoed signed link back to the stored reference (`storage.canonical_id_reference`),
so a browser that submits the preview link it was shown never persists an expiring URL.

`POST /documents/` holds every caller below admin (collectors, assistants and artists) to ownership: a private key in
`file_url` or anywhere in `data_json` is stored only when it lies under the caller's own upload folders
(`signatures/<user>/`, `scrolls/<user>/`, `scrolls/<category>/<user>/`); anything else is refused with
"Upload the file again" and nothing is stored. This install has no server-verified upload link check, so
below admin every `waivers/` key, a pending ID upload included, is refused on this route (no app screen
sends one there). Admin and founder writes are unchanged. Below admin, `POST /documents/` and
`POST /documents/upload` also refuse category `scroll` (the artist scroll, created only by
`submit_scroll`) and any `data_json` carrying a key only `submit_scroll` writes (`submission_id`,
`scroll_pdf_url`, `acceptance_record`, `storage_prefix`, `public_storage_prefix`, `certs_stored`,
`artist_user_id`) at any depth. No caller in the app uses those routes for either.

Access to records does not change. A founder or admin (and an assigned artist, as before) opens,
downloads and prints any waiver from the app. The waiver PDF prints the client's filled form values as
text and embeds the ID image, the client's signature and the artist's countersignature as images. The
images are fetched only from this install's own storage, through a short-lived signed link
(`is_own_storage_url` guards every fetch, so a client-supplied foreign URL is never requested); a
signature still held inline as a data URI is decoded instead. No URL is printed. Every export rebuilds
the packet from the stored references, so re-exporting an older waiver embeds them the same way. When
the ID reference is not one of our own objects, or the fetch fails, the Photo ID section still prints
with a note that the ID is on file. `tests/waiver_record_access_tests.py` proves this for a founder
and an admin.

Artist Scroll PDFs embed the government ID image bytes at generation time (the upload itself, not a
re-download). The PDF never prints a government ID link, whatever its key, and never contains its own
URL: the ID page reads "Private (staff open it through the app)". The server alone sets
`scroll_pdf_url`. The submitted fields never carry a storage reference: every server-owned key
(certificate, ID, signature and PDF URLs, `certs_stored`, `submission_id`, `storage_prefix`,
`acceptance_record`) is dropped, and so is any string anywhere in the payload that resolves to this
install's bucket. Only files uploaded in the same request end up referenced.

The scroll PDF's bytes are kept in the database only when storing it fails. The owner email row then
holds them (never attached), and a retry (`scroll_archive.retry_unstored_scroll_pdfs`, at boot and on
every outbox loop) uploads them under the submission's private `scrolls/` prefix
(`artist-scroll.id.pdf`, or `artist-scroll.noid.pdf`; a legacy row of unknown content gets the `.id`
name; never under `scrolls-public/`), records the URL on the records the server created for that
submission, and clears the bytes. A failed retry keeps them. It logs only counts (waiting, stored,
failed, unproven records skipped).

**Scroll PDF recovery trusts no client-writable field.** `data_json` could once be written by any
caller, so a submission id inside it proves nothing. `submit_scroll` records the exact ids of the
records it created (the artist scroll and every owner archive copy) and the artist id on the outbox
rows that hold the bytes (`ryozan_scroll_owner_email_outbox.document_ids`, `artist_user_id`, Alembic
revision `20261110_scroll_outbox_targets`), and the retry writes the URL onto those records only. For
rows queued before that, a record qualifies only through a trusted relationship: an owner archive
record (created only by `submit_scroll` or a founder), or the artist scroll owned by the artist the
archive record names. The storage folder comes from those trusted records only and must be that
artist's `scrolls/<artist>/...` folder. Anything else is skipped and counted, never guessed; with no
trusted record at all the bytes stay for a later run. The boot move repoints only the same trusted
records (an owner archive record, or the artist scroll owned by the artist whose
`scrolls-public/<artist>/<submission>` folder holds the PDF), and a skipped record never keeps a
public PDF alive.

Owner emails queued before this change kept the PDF bytes in the outbox table. A boot step
(`scroll_archive.clear_sent_owner_email_attachments`, run from `main.py`'s startup seeds) clears
`attachment_content` on finished rows (`sent`, or `exhausted` past the retry limit) only when the
submission's archive PDF is confirmed stored per the DB record: an owner archive copy for the same
`submission_id` with a non-empty http(s) `file_url` or `scroll_pdf_url`. Only the owner archive
category counts as proof; an artist scroll record never does.
Nothing is fetched from R2. When generating the PDF succeeded but storing it failed, the email's bytes
may be the only copy, so those rows are left untouched. Pending rows are never touched by the step. It
logs only three counts (found, cleared, kept) and is idempotent: the next boot clears 0.

The owner email for a new scroll is link only: no attachment and no storage link (public or signed).
Its one link is the authenticated Artist Scrolls page in the app, so an ID never lands in an inbox. A
signed link in an email would stop working after the SigV4 seven-day cap and would open the ID for
anyone the mail was forwarded to until then. Outbox rows queued before this change are sent without
their attachment (the stored bytes stay until the boot step above may clear them), and a stored body
that still says the PDF is attached is rewritten to the link-only wording before it is sent. The in-app
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
     starts_with(http.request.uri.path, "/waivers/")
     or starts_with(http.request.uri.path, "/signatures/")
     or starts_with(http.request.uri.path, "/scrolls/")
   ))
   ```

   Exactly what it covers: every object under `/waivers/` (ID photos, waiver packets, client signature
   images), every object under `/signatures/` (artists' saved signatures) and every object under
   `/scrolls/` (scroll IDs, every generated and legacy scroll PDF, scroll signatures, and every older
   scroll certificate or category upload). Nothing else is blocked: `/scrolls-public/` stays open (the
   prefix test includes the slash), as do avatars, portfolios, flash, references and bookings uploads. Keep URL normalization on (Rules, Settings, Normalize incoming URLs) so encoded paths cannot slip
   past the match. WAF rules run before cache, so cached copies are blocked too.
2. R2, bucket `<R2_BUCKET_NAME>`, Settings, Public access: if the `r2.dev` subdomain is enabled, disable
   it. The custom domain stays connected for public assets.
3. Verify: legacy `https://<assets-domain>/waivers/pending-ids/<id>.jpg`,
   `https://<assets-domain>/waivers/<month>/<name>_<ref>/signature.png`,
   `https://<assets-domain>/signatures/<user>/<file>.png` and
   `https://<assets-domain>/scrolls/<user>/<submission>/artist-scroll.pdf` links return 403, while staff
   can still open a waiver in Countersign (client signature shown), countersign with a saved signature,
   export the PDF with the ID and both signatures on it, a returning client still sees "ID on file"
   after the check-in code, and the owner can open a scroll PDF from Artist Scrolls.
   Any older `/scrolls/...` certificate or category upload link also returns 403, while new
   `/scrolls-public/...` links (a certificate, an unmarked category upload) still open publicly. A
   legacy `/scrolls-public/.../artist-scroll.noid.pdf` link stops opening once the boot move has
   deleted it; the app opens the private copy through a signed link.

After the rule, legacy public links stop working; the app keeps working through signed links.

## Known residual exposure

- Until the WAF rule above is live on an install, any legacy public link to these objects still opens.
- Owner emails sent before this change carried the scroll PDF as an attachment, so those inboxes may
  already hold an artist's ID. Sent mail cannot be recalled by the app; the owner decides whether to
  delete those messages.
- New scroll category uploads are public unless the uploader ticks "This file is an ID". An ID uploaded
  without the mark is not detected and stays public under `scrolls-public/`. Older uploads are all private.
- Finished owner emails whose archive PDF is not confirmed stored keep their bytes (the boot step logs
  how many), because they may be the only copy. The owner decides what to do with them.
- Once the WAF rule is live, links to older `/scrolls/` objects that were copied outside the app (for
  example pasted into a message, or printed as "Storage URL" for a certificate in an already generated
  scroll PDF) stop opening. In the app every such file still opens through a signed link.
