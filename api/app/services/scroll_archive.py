"""
Owner archive for submitted Artist Scrolls.

When an artist submits their scroll the shop owner (founder) gets a durable copy filed
into their own account: a generated PDF, an in-app notification, and an email that links
to the Artist Scrolls page in the app. The email never carries the PDF or a storage link:
the PDF can embed the artist's government ID, and an ID must never land in an inbox.

Two decisions here are load-bearing.

**The archive uses its own document category, never 'scroll'.**
`scrolls.py` maps a category slug to a document category as `scroll_{slug}`, with the
builtin `artist` slug mapping to bare `scroll`. Filing the owner's copy under `scroll`
would break two things at once: `GET /documents/scroll/my` returns the caller's own
`category='scroll'` row, so the founder would see an artist's scroll as their own; and
`submit_scroll` upserts by deleting every `category='scroll'` row for the user first, so
the founder's whole archive would be deleted the moment they submitted their own scroll.
The archive therefore lives under slug `owner-archive` -> category `scroll_owner-archive`.

**Archived objects are immutable.**
Submissions previously wrote fixed R2 keys (`artist-sig-{user_id}.png`), so re-submitting
overwrote the signature and certificate objects of every earlier submission in place,
which would silently rewrite the owner's archived evidence and defeat the point of
archiving it. Every submission now gets a unique key prefix from `build_submission_prefix`,
mirroring `storage.build_object_key`, so an earlier archive's objects are never touched.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select, text

from app.constants.timezones import shop_timezone
from app.models.document import DocumentInstance
from app.models.notification import Notification
from app.models.scroll_owner_email_outbox import ScrollOwnerEmailOutbox
from app.models.user import User
from app.services import storage
from app.services.email import FROM_ADMIN, send_email

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

    from app.services.scroll_pdf import ScrollAsset

logger = logging.getLogger("ryozan.scroll_archive")

# Scroll category slug for the owner's archive, and the document category it maps to
# under the scrolls.py convention (`scroll_{slug}`, builtin `artist` -> `scroll`).
OWNER_ARCHIVE_SLUG = "owner-archive"
OWNER_ARCHIVE_LABEL = "Artist Scrolls"
OWNER_ARCHIVE_CATEGORY = f"scroll_{OWNER_ARCHIVE_SLUG}"
OWNER_ARCHIVE_DESCRIPTION = "Durable copies of Artist Scrolls submitted by artists, filed to the owner."
OWNER_ARCHIVE_ICON = "archive"

# Notification type, alongside the existing scroll_request / scroll_remind types.
SCROLL_SUBMITTED_NOTIF_TYPE = "scroll_submitted"

FOUNDER_ROLE = "founder"
MAX_OUTBOX_ATTEMPTS = 5
OUTBOX_STALE_MINUTES = 10


def build_submission_prefix(artist_user_id: str) -> str:
    """Unique, path-safe R2 prefix for one scroll submission.

    Mirrors `storage.build_object_key`'s datestamp+token scheme, but at the folder level
    so every object of a single submission is grouped and no later submission can collide
    with, or overwrite, an earlier one.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    token = secrets.token_hex(4)
    return f"scrolls/{artist_user_id}/{stamp}-{token}"


async def resolve_founders(db: AsyncSession) -> list[User]:
    """Every user with role='founder'. Empty list is a valid, non-fatal outcome."""
    result = await db.execute(select(User).where(User.role == FOUNDER_ROLE))
    return list(result.scalars().all())


def _archive_data(
    artist: User,
    fields: dict,
    assets: list[ScrollAsset],
    submission_id: str,
    storage_prefix: str,
    submitted_at: datetime,
    pdf_url: str | None,
) -> dict:
    """The founder copy's data_json: enough to identify the source artist and submission,
    plus a per-document record of what was actually stored so the archive can never
    silently claim a document that is not there."""
    return {
        "source": "artist_scroll_submission",
        "artist_user_id": str(artist.id),
        "artist_name": artist.name,
        "artist_email": artist.email,
        "submission_id": submission_id,
        "submitted_at": submitted_at.isoformat(),
        "storage_prefix": storage_prefix,
        "scroll_pdf_url": pdf_url,
        "certs_stored": {
            asset.key: {
                "label": asset.label,
                "provided": asset.provided,
                "stored": asset.stored,
                "url": asset.url,
                "error": asset.error,
            }
            for asset in assets
        },
        "fields": fields,
    }


def file_owner_archive(
    db: AsyncSession,
    artist: User,
    founders: list[User],
    fields: dict,
    assets: list[ScrollAsset],
    submission_id: str,
    storage_prefix: str,
    submitted_at: datetime,
    pdf_url: str | None,
) -> list[DocumentInstance]:
    """Create one archive DocumentInstance per founder and return them.

    Rows are added to the caller's session but not committed. The caller owns the
    transaction so the archive lands atomically with the artist's own record.
    """
    if not founders:
        logger.warning(
            "Artist scroll %s submitted by %s but no user has role='founder'; no owner archive copy was filed.",
            submission_id,
            artist.id,
        )
        return []

    created: list[DocumentInstance] = []
    payload = _archive_data(artist, fields, assets, submission_id, storage_prefix, submitted_at, pdf_url)
    for founder in founders:
        instance = DocumentInstance(
            user_id=founder.id,
            category=OWNER_ARCHIVE_CATEGORY,
            title=f"Artist Scroll: {artist.name}",
            status="signed",
            data_json=json.dumps(payload),
            file_url=pdf_url,
            signed_at=submitted_at,
            notes=f"Owner archive copy of the Artist Scroll submitted by {artist.name}.",
        )
        db.add(instance)
        created.append(instance)

    return created


def notify_owners(
    db: AsyncSession,
    artist: User,
    founders: list[User],
    submitted_at: datetime,
    pdf_url: str | None,
) -> int:
    """In-app notification for each founder. Best-effort: never raises."""
    count = 0
    for founder in founders:
        try:
            db.add(
                Notification(
                    user_id=founder.id,
                    title="Artist Scroll Submitted",
                    body=(
                        f"{artist.name} submitted their Artist Scroll. "
                        + (
                            "A signed PDF copy has been filed to your Artist Scrolls archive."
                            if pdf_url
                            else "The archive entry was filed, but its PDF is unavailable."
                        )
                    ),
                    notification_type=SCROLL_SUBMITTED_NOTIF_TYPE,
                    action_url=f"/dashboard?portal=hub&panel=codex&category={OWNER_ARCHIVE_SLUG}",
                    is_read=False,
                )
            )
            count += 1
        except Exception:
            # Logged at error, not warning: a missing owner notification means the shop
            # owner never learns a compliance document arrived.
            logger.exception(
                "Failed to queue scroll_submitted notification for founder %s (artist=%s, submitted_at=%s)",
                founder.id,
                artist.id,
                submitted_at.isoformat(),
            )
    return count


def build_owner_archive_email_html(
    artist_name: str,
    submitted_at: datetime,
    assets: list[ScrollAsset],
    *,
    pdf_filed: bool,
) -> str:
    """Owner notice that a scroll arrived, in Email Foundry's language, listing what was stored.

    Reuses the waiver renderers' Foundry shell (starseed ground, tiger mark, spec well, coral
    pill) so the owner copy reads like every other shop email. Every value is escaped.

    The scroll PDF can embed the artist's government ID, so this email neither attaches nor links
    it. The only link is the authenticated Artist Scrolls page in the app.
    """
    from app.core.config import get_settings
    from app.services.email_foundry_waiver import _MONO_FONT, _copy, _cta, _label, _shell, _tiger_header

    status_cells = []
    for index, asset in enumerate(assets):
        if not asset.provided:
            state, color = "Not provided", "rgba(226,218,194,.5)"
        elif asset.stored:
            state, color = "Stored", "#7fc3b1"
        else:
            state, color = f"UPLOAD FAILED: {asset.error or 'not stored'}", "#e0826f"
        rule = "" if index == 0 else "border-top:1px dashed rgba(226,218,194,.14);"
        status_cells.append(
            f'<tr><td style="padding:10px 0;{rule}font-family:{_MONO_FONT};font-size:9.5px;font-weight:700;'
            f"letter-spacing:.16em;color:rgba(226,218,194,.5);text-transform:uppercase;vertical-align:top;"
            f'">{escape(asset.label)}</td>'
            f'<td align="right" style="padding:10px 0 10px 12px;{rule}font-family:{_MONO_FONT};font-size:10px;'
            f'font-weight:700;letter-spacing:.12em;color:{color};text-transform:uppercase;">{escape(state)}</td></tr>'
        )
    documents = (
        '<tr><td style="padding:16px 26px 0;"><table class="bttco-email-well" role="presentation" width="100%" '
        'cellpadding="0" cellspacing="0" border="0" bgcolor="#101110" style="background-color:#101110;'
        'background-image:linear-gradient(#101110,#101110);border:1px solid rgba(226,218,194,.14);border-radius:12px;">'
        '<tr><td style="padding:6px 18px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0">'
        + (
            "".join(status_cells)
            or f'<tr><td style="padding:10px 0;font-family:{_MONO_FONT};font-size:10px;letter-spacing:.12em;'
            'color:rgba(226,218,194,.5);text-transform:uppercase;">No documents uploaded</td></tr>'
        )
        + "</table></td></tr></table></td></tr>"
    )

    local = submitted_at.astimezone(shop_timezone())
    ts = f"{local.strftime('%B')} {local.day}, {local.year} at {local.strftime('%I:%M %p').lstrip('0')} {local.strftime('%Z')}"
    archive_message = _LINK_ONLY_FILED if pdf_filed else _LINK_ONLY_NOT_FILED
    frontend = get_settings().public_site_url
    archive_url = f"{frontend}/dashboard?portal=hub&panel=codex&category={OWNER_ARCHIVE_SLUG}"

    artist = escape(artist_name)
    rows = "".join(
        [
            _copy(
                f'<strong style="color:#c8a15e;">{artist}</strong> signed and submitted their Artist Scroll on '
                f"{escape(ts)}. {escape(archive_message)}"
            ),
            _label("Documents"),
            documents,
            _cta("Open Artist Scrolls", archive_url),
        ]
    )
    return _shell(
        title="Artist Scroll filed",
        preheader=f"{artist_name} submitted their Artist Scroll.",
        header=_tiger_header("Artist Scroll // Filed"),
        rows=rows,
    )


# Owner email copy: link only. The scroll PDF can embed the artist's government ID.
_LINK_ONLY_FILED = (
    "A complete PDF copy has been filed to your Artist Scrolls archive. For privacy it is not "
    "attached to this email; open it in the app."
)
_LINK_ONLY_NOT_FILED = "The archive entry was filed, but PDF generation or storage failed, so the archive has no PDF."
# Wording stored in rows queued before the link-only rule, mapped to the link-only wording.
_ATTACHED_WORDING = {
    "A complete PDF copy is attached and has been filed to your Artist Scrolls archive.": _LINK_ONLY_FILED,
    "The archive entry was filed, but PDF generation or storage failed, so no PDF is attached.": (_LINK_ONLY_NOT_FILED),
}


def link_only_html(html_body: str) -> str:
    """A stored owner email body with any old "PDF is attached" wording rewritten to link-only."""
    for old, new in _ATTACHED_WORDING.items():
        html_body = html_body.replace(escape(old), escape(new))
    return html_body


def queue_owner_emails(
    db: AsyncSession,
    artist: User,
    founders: list[User],
    submitted_at: datetime,
    assets: list[ScrollAsset],
    pdf_filed: bool,
    submission_id: str,
    *,
    unstored_pdf: tuple[bytes, str] | None = None,
) -> int:
    """Persist owner email obligations in the archive transaction. Link only, never an attachment.

    `unstored_pdf` is (bytes, target filename) when the scroll PDF was generated but storing it
    failed. Those bytes are kept on the row only so retry_unstored_scroll_pdfs can store them later;
    they are never attached to the email.
    """
    html = build_owner_archive_email_html(
        artist.name or "An artist",
        submitted_at,
        assets,
        pdf_filed=pdf_filed,
    )
    queued = 0
    for founder in founders:
        if not founder.email:
            logger.error(
                "Founder %s has no email address; scroll submission by %s could not be queued.",
                founder.id,
                artist.id,
            )
            continue
        db.add(
            ScrollOwnerEmailOutbox(
                founder_id=founder.id,
                submission_id=submission_id,
                to_email=founder.email,
                subject=f"Artist Scroll Submitted: {artist.name}",
                html_body=html,
                attachment_filename=unstored_pdf[1] if unstored_pdf else None,
                attachment_content=unstored_pdf[0] if unstored_pdf else None,
                status="queued",
                attempts=0,
                next_attempt_at=submitted_at,
                created_at=submitted_at,
                updated_at=submitted_at,
            )
        )
        queued += 1
    return queued


async def process_owner_email_outbox_once(limit: int = 20) -> int:
    """Claim and deliver one bounded outbox batch with retry evidence."""
    from app.core.database import async_session

    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=OUTBOX_STALE_MINUTES)
    async with async_session() as db:
        result = await db.execute(
            select(ScrollOwnerEmailOutbox)
            .where(
                ScrollOwnerEmailOutbox.attempts < MAX_OUTBOX_ATTEMPTS,
                or_(
                    and_(
                        ScrollOwnerEmailOutbox.status.in_(("queued", "failed")),
                        ScrollOwnerEmailOutbox.next_attempt_at <= now,
                    ),
                    and_(
                        ScrollOwnerEmailOutbox.status == "processing",
                        ScrollOwnerEmailOutbox.updated_at <= stale_before,
                    ),
                ),
            )
            .order_by(ScrollOwnerEmailOutbox.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = "processing"
            row.attempts += 1
            row.updated_at = now
        await db.commit()

        delivered = 0
        for row in rows:
            # Rows queued before the link-only rule may still hold the PDF bytes. They are never
            # attached, so a scroll PDF (and the ID it may embed) never reaches an inbox. The bytes
            # stay stored: if storing the archive PDF failed they may be its only copy. The boot step
            # (clear_sent_owner_email_attachments) clears them once the row is sent or exhausted and
            # the archive PDF is confirmed stored.
            # Their stored body may still say the PDF is attached; it is sent with the link-only wording.
            row.html_body = link_only_html(row.html_body)
            try:
                ok = await send_email(
                    to=row.to_email,
                    subject=row.subject,
                    html_body=row.html_body,
                    tags=["scroll_submitted"],
                    from_alias=FROM_ADMIN,
                )
                if ok:
                    row.status = "sent"
                    row.last_error = None
                    delivered += 1
                else:
                    row.status = "exhausted" if row.attempts >= MAX_OUTBOX_ATTEMPTS else "failed"
                    row.last_error = "send_email returned False"
            except Exception as exc:
                row.status = "exhausted" if row.attempts >= MAX_OUTBOX_ATTEMPTS else "failed"
                row.last_error = str(exc)[:1000]
                logger.exception("Owner email outbox row %s failed", row.id)
            row.next_attempt_at = datetime.now(UTC) + timedelta(minutes=min(2**row.attempts, 60))
            row.updated_at = datetime.now(UTC)
            await db.commit()
        return delivered


def _archive_pdf_confirmed_sql(outbox_alias: str) -> str:
    """SQL condition: the outbox row's submission has a stored archive PDF per the DB record.

    Confirmed means an owner archive copy or the artist's scroll record for the same submission_id
    carries a non-empty http(s) file_url or scroll_pdf_url. Nothing is fetched from R2. When PDF
    generation succeeded but storing it failed, the queued email's bytes may be the only copy, so
    such rows are never confirmed.
    """
    documents = DocumentInstance.__tablename__
    return (
        f"EXISTS (SELECT 1 FROM {documents} d "
        f"WHERE d.category IN ('scroll', '{OWNER_ARCHIVE_CATEGORY}') "
        f"AND position(('\"submission_id\": \"' || {outbox_alias}.submission_id || '\"') IN coalesce(d.data_json, '')) > 0 "
        "AND (d.file_url ~* '^https?://[^[:space:]]+' "
        "OR coalesce(d.data_json, '') ~* '\"scroll_pdf_url\": \"https?://[^\"[:space:]]+'))"
    )


async def clear_sent_owner_email_attachments(conn: AsyncConnection) -> tuple[int, int, int]:
    """Drop stored PDF bytes from finished owner emails whose archive PDF is confirmed stored. Idempotent.

    Rows queued before the link-only rule kept the scroll PDF (which can embed the artist's
    government ID). Finished rows are those sent, or exhausted (never delivered, past the retry
    limit, so never picked up again). One is cleared only when its submission's archive PDF is
    confirmed stored per the DB record (_archive_pdf_confirmed_sql); otherwise its bytes may be the
    only copy and the row is left untouched. Pending rows are never touched here.
    Returns (found, cleared, kept). Logs counts only, never row data.
    """
    table = ScrollOwnerEmailOutbox.__tablename__
    holding = f"{table}.status IN ('sent', 'exhausted') AND {table}.attachment_content IS NOT NULL"
    confirmed = _archive_pdf_confirmed_sql(table)
    found = (await conn.execute(text(f"SELECT count(*) FROM {table} WHERE {holding}"))).scalar_one()
    logger.info("Finished owner emails still holding PDF bytes: %d", found)
    result = await conn.execute(
        text(
            f"UPDATE {table} SET attachment_content = NULL, attachment_filename = NULL WHERE {holding} AND {confirmed}"
        )
    )
    cleared = int(result.rowcount or 0)
    logger.info("Cleared PDF bytes from finished owner emails with a stored archive PDF: %d", cleared)
    kept = (await conn.execute(text(f"SELECT count(*) FROM {table} WHERE {holding}"))).scalar_one()
    logger.info("Kept PDF bytes on finished owner emails with no confirmed archive PDF: %d", kept)
    return int(found), cleared, int(kept)


async def count_legacy_scroll_references(conn: AsyncConnection) -> int:
    """Count document rows that reference a legacy "scrolls/" object. Read only; logs the count only.

    Every object under "scrolls/" is private by key and served through signed links; new public
    files go under "scrolls-public/". This only measures how many stored references still point at
    the private prefix (file_url, artist_signature or data_json).
    """
    documents = DocumentInstance.__tablename__
    count = (
        await conn.execute(
            text(
                f"SELECT count(*) FROM {documents} "
                "WHERE coalesce(file_url, '') LIKE '%/scrolls/%' "
                "OR coalesce(artist_signature, '') LIKE '%/scrolls/%' "
                "OR coalesce(data_json, '') LIKE '%/scrolls/%'"
            )
        )
    ).scalar_one()
    logger.info("Document rows referencing legacy scrolls/ objects (served signed): %d", count)
    return int(count)


def _set_scroll_pdf_reference(doc: DocumentInstance, url: str) -> None:
    """Record a newly stored scroll PDF on a document row, filling only empty references."""
    if not doc.file_url:
        doc.file_url = url
    try:
        data = json.loads(doc.data_json or "{}")
    except (TypeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    if not data.get("scroll_pdf_url"):
        data["scroll_pdf_url"] = url
    nested = data.get("fields")
    if isinstance(nested, dict) and not nested.get("scroll_pdf_url"):
        nested["scroll_pdf_url"] = url
    doc.data_json = json.dumps(data)


async def retry_unstored_scroll_pdfs(db: AsyncSession, limit: int = 20) -> tuple[int, int, int]:
    """Store scroll PDFs whose upload failed, from the bytes kept for that purpose. Idempotent.

    A submission's bytes are kept on its outbox rows only while its archive PDF is not stored
    (never attached to an email). For each such submission this uploads the bytes under the
    submission's private "scrolls/" storage prefix (every generated PDF prints the artist signature,
    so none is ever stored under "scrolls-public/"; the ".noid" name is kept, a legacy row of unknown
    content gets the ".id" name), records the URL on the archive copies and the artist's scroll
    record, then clears the bytes. A failed upload keeps the bytes for the next run.
    Returns (pending, stored, failed). Logs counts only.
    """
    table = ScrollOwnerEmailOutbox.__tablename__
    rows = (
        (
            await db.execute(
                select(ScrollOwnerEmailOutbox)
                .where(
                    ScrollOwnerEmailOutbox.attachment_content.is_not(None),
                    text(f"NOT {_archive_pdf_confirmed_sql(table)}"),
                )
                .order_by(ScrollOwnerEmailOutbox.created_at)
            )
        )
        .scalars()
        .all()
    )
    by_submission: dict[str, list[ScrollOwnerEmailOutbox]] = {}
    for row in rows:
        by_submission.setdefault(row.submission_id, []).append(row)
    if not by_submission:
        return 0, 0, 0

    stored = failed = 0
    for submission_id, group in list(by_submission.items())[:limit]:
        docs = (
            (
                await db.execute(
                    select(DocumentInstance).where(
                        DocumentInstance.category.in_(("scroll", OWNER_ARCHIVE_CATEGORY)),
                        DocumentInstance.data_json.contains(f'"submission_id": "{submission_id}"', autoescape=True),
                    )
                )
            )
            .scalars()
            .all()
        )
        prefix = None
        for doc in docs:
            try:
                candidate = json.loads(doc.data_json or "{}").get("storage_prefix")
            except (TypeError, ValueError, AttributeError):
                candidate = None
            if isinstance(candidate, str) and candidate.startswith("scrolls/") and ".." not in candidate.split("/"):
                prefix = candidate
                break
        if not prefix:
            failed += 1
            continue
        no_id = group[0].attachment_filename == storage.SCROLL_NOID_PDF_FILENAME
        filename = storage.SCROLL_NOID_PDF_FILENAME if no_id else storage.SCROLL_ID_PDF_FILENAME
        try:
            url = await storage.upload_file(
                file_bytes=group[0].attachment_content,
                filename=filename,
                mime_type="application/pdf",
                # Always the private folder: the PDF prints the signatures whether or not it embeds the ID.
                folder_path=prefix,
            )
        except Exception:
            url = None
        if not url:
            failed += 1
            continue
        for doc in docs:
            _set_scroll_pdf_reference(doc, url)
        for row in group:
            row.attachment_content = None
            row.attachment_filename = None
            row.updated_at = datetime.now(UTC)
        await db.commit()
        stored += 1

    logger.info("Scroll PDFs waiting to be stored: %d", len(by_submission))
    logger.info("Scroll PDFs stored on retry: %d", stored)
    logger.info("Scroll PDF store retries that failed (bytes kept): %d", failed)
    return len(by_submission), stored, failed


# ── Generated scroll PDFs stored under the public prefix ────────────────

_SCROLL_PDF_STEM = "artist-scroll"


def _public_scroll_pdf_key(reference: object) -> str | None:
    """The object key when `reference` is a generated scroll PDF on our bucket under scrolls-public/."""
    key = storage.storage_key(reference) if isinstance(reference, str) else None
    if not key or not key.startswith(storage.SCROLL_PUBLIC_ROOT):
        return None
    name = key.rsplit("/", 1)[-1]
    return key if name.startswith(_SCROLL_PDF_STEM) and name.endswith(".pdf") else None


def _collect_public_pdf_refs(value: object, found: dict[str, str]) -> None:
    """Every public scroll PDF reference inside a JSON-like value, as key -> one stored reference."""
    if isinstance(value, str):
        key = _public_scroll_pdf_key(value)
        if key:
            found.setdefault(key, value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_public_pdf_refs(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_public_pdf_refs(item, found)


def _replace_pdf_ref(value: object, old_key: str, new_url: str) -> object:
    """Copy of a JSON-like value with every reference to `old_key` replaced by `new_url`."""
    if isinstance(value, str):
        return new_url if _public_scroll_pdf_key(value) == old_key else value
    if isinstance(value, dict):
        return {k: _replace_pdf_ref(v, old_key, new_url) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_pdf_ref(v, old_key, new_url) for v in value]
    return value


async def _remaining_references(db: AsyncSession, object_key: str) -> int:
    """How many stored records still mention `object_key`, in any document or Gala memory."""
    from app.models.ai_memory import AIMemory

    documents = await db.execute(
        select(func.count())
        .select_from(DocumentInstance)
        .where(
            or_(
                DocumentInstance.file_url.contains(object_key, autoescape=True),
                DocumentInstance.data_json.contains(object_key, autoescape=True),
                DocumentInstance.artist_signature.contains(object_key, autoescape=True),
            )
        )
    )
    memories = await db.execute(
        select(func.count()).select_from(AIMemory).where(AIMemory.value.contains(object_key, autoescape=True))
    )
    return int(documents.scalar_one() or 0) + int(memories.scalar_one() or 0)


async def move_public_scroll_pdfs(db: AsyncSession) -> dict[str, int]:
    """
    Boot step: move generated scroll PDFs written under scrolls-public/ to the private scrolls/ prefix.

    Every generated scroll PDF prints the artist signature, any owner countersignature and the artist's
    details, so none may stay public. For each such PDF referenced by an artist scroll or owner archive
    record: read it, write the same bytes to the private twin key (same folder name and file name, so
    the .id/.noid marker is kept), read the copy back and compare, then point every record at the private
    copy and commit. Only after that commit, and only when no stored record still mentions the old key,
    is the public object deleted; otherwise it is kept and counted. On any storage failure the record
    keeps its original reference. Idempotent: a moved record no longer references a public PDF.
    Logs counts only.
    """
    counts = {"pdfs": 0, "moved": 0, "failed": 0, "public deleted": 0, "public kept": 0}
    result = await db.execute(
        select(DocumentInstance)
        .where(
            DocumentInstance.category.in_((OWNER_ARCHIVE_CATEGORY, "scroll")),
            or_(
                DocumentInstance.file_url.contains(f"/{storage.SCROLL_PUBLIC_ROOT}"),
                DocumentInstance.data_json.contains(f"/{storage.SCROLL_PUBLIC_ROOT}"),
            ),
            or_(
                DocumentInstance.file_url.contains(_SCROLL_PDF_STEM),
                DocumentInstance.data_json.contains(_SCROLL_PDF_STEM),
            ),
        )
        .with_for_update(skip_locked=True)
    )
    by_key: dict[str, list[DocumentInstance]] = {}
    old_refs: dict[str, str] = {}
    for inst in result.scalars().all():
        found: dict[str, str] = {}
        _collect_public_pdf_refs(inst.file_url, found)
        with contextlib.suppress(TypeError, ValueError):
            _collect_public_pdf_refs(json.loads(inst.data_json or "null"), found)
        for key, ref in found.items():
            by_key.setdefault(key, []).append(inst)
            old_refs.setdefault(key, ref)
    counts["pdfs"] = len(by_key)

    moved: list[str] = []
    for old_key, records in by_key.items():
        folder, name = old_key.rsplit("/", 1)
        new_folder = storage.private_scroll_prefix(folder)
        content = await storage.fetch_object(old_key)
        new_url = None
        if content:
            try:
                new_url = await storage.upload_file(
                    file_bytes=content, filename=name, mime_type="application/pdf", folder_path=new_folder
                )
            except Exception:  # noqa: BLE001 - counted, original reference kept
                new_url = None
        readback = await storage.fetch_object(f"{new_folder}/{name}") if new_url else None
        if not (content and readback and hashlib.sha256(readback).digest() == hashlib.sha256(content).digest()):
            counts["failed"] += 1
            continue
        for inst in records:
            inst.file_url = _replace_pdf_ref(inst.file_url, old_key, new_url)
            try:
                data = json.loads(inst.data_json or "null")
            except (TypeError, ValueError):
                data = None
            if data is not None:
                inst.data_json = json.dumps(_replace_pdf_ref(data, old_key, new_url))
        moved.append(old_key)
    counts["moved"] = len(moved)
    await db.commit()

    for old_key in moved:
        deleted = False
        if await _remaining_references(db, old_key) == 0:
            deleted = await storage.delete_file(old_refs[old_key], allowed_prefix=storage.SCROLL_PUBLIC_ROOT)
        counts["public deleted" if deleted else "public kept"] += 1

    logger.info("Public scroll PDFs moved to private storage: %s", ", ".join(f"{n} {k}" for k, n in counts.items()))
    return counts


async def move_public_scroll_pdfs_once() -> dict[str, int]:
    """One boot pass in its own session (run in the background: it reads and writes R2)."""
    from app.core.database import async_session

    async with async_session() as db:
        return await move_public_scroll_pdfs(db)


async def process_unstored_scroll_pdfs_once(limit: int = 20) -> int:
    """One retry pass in its own session, for the boot step and the outbox loop."""
    from app.core.database import async_session

    async with async_session() as db:
        _pending, stored, _failed = await retry_unstored_scroll_pdfs(db, limit)
        await db.rollback()
    return stored
