"""Durable artist-gallery object cleanup."""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models.gallery_deletion_outbox import GalleryDeletionOutbox
from app.services.storage import delete_file

logger = logging.getLogger("ryozan.gallery_storage")
GALLERY_DELETE_MAX_ATTEMPTS = 8
GALLERY_DELETE_STALE_MINUTES = 10


async def queue_gallery_deletion(
    db: AsyncSession,
    *,
    artist_id: UUID,
    url: str,
    allowed_prefix: str,
) -> GalleryDeletionOutbox:
    idempotency_key = hashlib.sha256(f"{artist_id}:{url}".encode()).hexdigest()
    row_id = uuid.uuid4()
    now = datetime.now(UTC)
    inserted_id = await db.scalar(
        insert(GalleryDeletionOutbox)
        .values(
            id=row_id,
            artist_id=artist_id,
            idempotency_key=idempotency_key,
            url=url,
            allowed_prefix=allowed_prefix,
            status="queued",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[GalleryDeletionOutbox.idempotency_key])
        .returning(GalleryDeletionOutbox.id)
    )
    if inserted_id is not None:
        row = await db.get(GalleryDeletionOutbox, inserted_id)
    else:
        row = await db.scalar(
            select(GalleryDeletionOutbox).where(GalleryDeletionOutbox.idempotency_key == idempotency_key)
        )
    if row is None:
        raise RuntimeError("Gallery deletion intent was not visible after conflict")
    return row


async def process_gallery_deletion_outbox_once(
    limit: int = 20,
    *,
    outbox_id: UUID | None = None,
) -> int:
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=GALLERY_DELETE_STALE_MINUTES)
    async with async_session() as session:
        query = select(GalleryDeletionOutbox).where(
            GalleryDeletionOutbox.attempts < GALLERY_DELETE_MAX_ATTEMPTS,
            or_(
                and_(
                    GalleryDeletionOutbox.status.in_(("queued", "failed")),
                    GalleryDeletionOutbox.next_attempt_at <= now,
                ),
                and_(
                    GalleryDeletionOutbox.status == "deleting",
                    GalleryDeletionOutbox.updated_at <= stale_before,
                ),
            ),
        )
        if outbox_id is not None:
            query = query.where(GalleryDeletionOutbox.id == outbox_id)
        rows = list(
            (
                await session.execute(
                    query.order_by(GalleryDeletionOutbox.created_at, GalleryDeletionOutbox.id)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[tuple[UUID, str, str, int]] = []
        for row in rows:
            row.status = "deleting"
            row.attempts += 1
            row.updated_at = now
            row.last_error = None
            claimed.append((row.id, row.url, row.allowed_prefix, row.attempts))
        await session.commit()

        deleted_count = 0
        for row_id, url, allowed_prefix, attempt in claimed:
            deleted = False
            error: str | None = None
            try:
                deleted = await delete_file(url, allowed_prefix=allowed_prefix)
                if not deleted:
                    error = "R2 did not confirm object deletion"
            except Exception as exc:  # noqa: BLE001 - durable row owns retry state
                logger.exception("Gallery object deletion failed for outbox %s", row_id)
                error = str(exc)[:1000]
            row = await session.get(
                GalleryDeletionOutbox,
                row_id,
                with_for_update=True,
                populate_existing=True,
            )
            if row is None or row.status != "deleting" or row.attempts != attempt:
                continue
            finished_at = datetime.now(UTC)
            row.status = "deleted" if deleted else "failed"
            row.updated_at = finished_at
            row.last_error = error
            if not deleted:
                row.next_attempt_at = finished_at + timedelta(minutes=min(2**attempt, 120))
            await session.commit()
            if deleted:
                deleted_count += 1
        return deleted_count
