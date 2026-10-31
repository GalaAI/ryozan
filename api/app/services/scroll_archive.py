"""
Owner archive for submitted Artist Scrolls.

When an artist submits their scroll the shop owner (founder) gets a durable copy filed
into their own account: a generated PDF, an in-app notification, and an email with the
PDF attached.

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

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select

from app.constants.timezones import SHOP_TIMEZONE
from app.models.document import DocumentInstance
from app.models.notification import Notification
from app.models.scroll_owner_email_outbox import ScrollOwnerEmailOutbox
from app.models.user import User
from app.services.email import FROM_ADMIN, EmailAttachment, send_email

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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
    pdf_attached: bool,
) -> str:
    """Owner notice that a scroll arrived, in Email Foundry's language, listing exactly what is attached.

    Reuses the waiver renderers' Foundry shell (starseed ground, tiger mark, spec well, coral
    pill) so the owner copy reads like every other shop email. Every value is escaped.
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

    local = submitted_at.astimezone(SHOP_TIMEZONE)
    ts = f"{local.strftime('%B')} {local.day}, {local.year} at {local.strftime('%I:%M %p').lstrip('0')} ET"
    archive_message = (
        "A complete PDF copy is attached and has been filed to your Artist Scrolls archive."
        if pdf_attached
        else "The archive entry was filed, but PDF generation or storage failed, so no PDF is attached."
    )
    try:
        frontend = get_settings().FRONTEND_URL.rstrip("/")
    except Exception:
        frontend = "https://app.ryozan.app"
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


def queue_owner_emails(
    db: AsyncSession,
    artist: User,
    founders: list[User],
    submitted_at: datetime,
    assets: list[ScrollAsset],
    pdf_bytes: bytes | None,
    submission_id: str,
) -> int:
    """Persist owner email obligations in the archive transaction."""
    html = build_owner_archive_email_html(
        artist.name or "An artist",
        submitted_at,
        assets,
        pdf_attached=bool(pdf_bytes),
    )
    safe = "".join(c if c.isalnum() else "_" for c in (artist.name or "artist"))
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
                attachment_filename=f"artist-scroll-{safe}.pdf" if pdf_bytes else None,
                attachment_content=pdf_bytes,
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
            attachments = None
            if row.attachment_content and row.attachment_filename:
                attachments = [
                    EmailAttachment(
                        filename=row.attachment_filename,
                        content=row.attachment_content,
                        content_type="application/pdf",
                    )
                ]
            try:
                ok = await send_email(
                    to=row.to_email,
                    subject=row.subject,
                    html_body=row.html_body,
                    tags=["scroll_submitted"],
                    from_alias=FROM_ADMIN,
                    attachments=attachments,
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
