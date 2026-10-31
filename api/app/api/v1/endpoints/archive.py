"""
Archive endpoints — unified search across completed records.

Part of the Vault module. Merges data from:
  - ryozan_document_instances (completed waivers)
  - ryozan_bookings (completed / cancelled bookings)
  - Stripe payment records (via ryozan_bookings with stripe_payment_intent_id)

Staff+ access only.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.status import BookingStatus, DocStatus
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking import Booking
from app.models.document import DocumentInstance
from app.models.user import BTTCOUser
from app.schemas.archive import ArchiveListResponse, ArchiveRecord

logger = logging.getLogger("bttco.archive")

router = APIRouter(prefix="/archive", tags=["archive"])


def _require_staff(user: BTTCOUser):
    """Raise 403 if below artist role (staff floor)."""
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get("artist", 99):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires artist role or higher",
        )


@router.get("/", response_model=ArchiveListResponse)
async def list_archive(
    type: str = Query("all", pattern="^(waiver|booking|receipt|doc|all)$", alias="type"),  # noqa: A002
    search: str = Query("", max_length=200),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    sort_by: str = Query("date", pattern="^(date|type|client)$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return paginated archive records across waivers, bookings, and receipts.

    Merges completed documents (waivers), completed/cancelled bookings,
    and bookings with Stripe payment intents (receipts) into a single
    sortable, searchable list.
    """
    _require_staff(current_user)

    records: list[ArchiveRecord] = []

    # ── Waivers (only explicitly approved — Archive button is the gate) ──
    if type in ("all", "waiver"):
        waiver_q = select(DocumentInstance).where(
            DocumentInstance.category == "waiver",
            DocumentInstance.status == DocStatus.APPROVED,
        )
        if search:
            waiver_q = waiver_q.where(
                or_(
                    DocumentInstance.guest_name.ilike(f"%{search}%"),
                    DocumentInstance.recipient_name.ilike(f"%{search}%"),
                    DocumentInstance.guest_email.ilike(f"%{search}%"),
                    DocumentInstance.recipient_email.ilike(f"%{search}%"),
                )
            )
        result = await db.execute(waiver_q)
        waivers = result.scalars().all()

        # Batch-fetch user names for user_id lookups
        user_ids = {w.user_id for w in waivers if w.user_id}
        artist_ids = {w.artist_id_reviewed for w in waivers if w.artist_id_reviewed}
        all_ids = user_ids | artist_ids
        user_map: dict[UUID, str] = {}
        if all_ids:
            name_result = await db.execute(select(BTTCOUser.id, BTTCOUser.name).where(BTTCOUser.id.in_(all_ids)))
            user_map = {row.id: row.name for row in name_result}

        for w in waivers:
            client_name = w.guest_name or w.recipient_name or user_map.get(w.user_id, "Unknown")
            client_email = w.guest_email or w.recipient_email
            artist_name = user_map.get(w.artist_id_reviewed) if w.artist_id_reviewed else None
            records.append(
                ArchiveRecord(
                    id=w.id,
                    record_type="waiver",
                    client_name=client_name,
                    client_email=client_email,
                    artist_name=artist_name,
                    date=w.signed_at or w.created_at,
                    status=w.status,
                    summary=w.title,
                    original_id=w.id,
                )
            )

    # ── Docs (archived from Assets — any non-waiver document) ──
    if type in ("all", "doc"):
        doc_q = select(DocumentInstance).where(
            DocumentInstance.status == DocStatus.ARCHIVED,
            DocumentInstance.category != "waiver",
        )
        if search:
            doc_q = doc_q.where(
                or_(
                    DocumentInstance.title.ilike(f"%{search}%"),
                    DocumentInstance.recipient_name.ilike(f"%{search}%"),
                    DocumentInstance.recipient_email.ilike(f"%{search}%"),
                )
            )
        result = await db.execute(doc_q)
        docs = result.scalars().all()

        d_ids = {d.user_id for d in docs if d.user_id}
        d_user_map: dict[UUID, str] = {}
        if d_ids:
            name_result = await db.execute(select(BTTCOUser.id, BTTCOUser.name).where(BTTCOUser.id.in_(d_ids)))
            d_user_map = {row.id: row.name for row in name_result}

        for d in docs:
            client_name = d.recipient_name or d_user_map.get(d.user_id, "Unknown")
            records.append(
                ArchiveRecord(
                    id=d.id,
                    record_type=d.category or "doc",
                    client_name=client_name,
                    client_email=d.recipient_email,
                    artist_name=d_user_map.get(d.user_id),
                    date=d.updated_at or d.created_at,
                    status=DocStatus.ARCHIVED,
                    summary=d.title,
                    original_id=d.id,
                )
            )

    # ── Bookings (completed / cancelled) ──────────────────────
    if type in ("all", "booking"):
        booking_q = select(Booking).where(
            Booking.status.in_([BookingStatus.COMPLETED, BookingStatus.CANCELLED, "COMPLETED", "CANCELLED"])
        )
        if search:
            booking_q = booking_q.where(
                or_(
                    Booking.guest_name.ilike(f"%{search}%"),
                    Booking.guest_email.ilike(f"%{search}%"),
                )
            )
        result = await db.execute(booking_q)
        bookings_list = result.scalars().all()

        # Gather user IDs for name resolution
        b_client_ids = {b.client_id for b in bookings_list if b.client_id}
        b_artist_ids = {b.artist_id for b in bookings_list}
        b_all_ids = b_client_ids | b_artist_ids
        b_user_map: dict[UUID, tuple[str, str]] = {}  # id -> (name, email)
        if b_all_ids:
            name_result = await db.execute(
                select(BTTCOUser.id, BTTCOUser.name, BTTCOUser.email).where(BTTCOUser.id.in_(b_all_ids))
            )
            b_user_map = {row.id: (row.name, row.email) for row in name_result}

        # Also search by resolved client name if search is active
        filtered_bookings = bookings_list
        if search:
            search_lower = search.lower()
            filtered_bookings = [
                b
                for b in bookings_list
                if (b.guest_name and search_lower in b.guest_name.lower())
                or (b.guest_email and search_lower in b.guest_email.lower())
                or (b.client_id and b.client_id in b_user_map and search_lower in b_user_map[b.client_id][0].lower())
            ]

        for b in filtered_bookings:
            if b.client_id and b.client_id in b_user_map:
                client_name = b_user_map[b.client_id][0]
                client_email = b_user_map[b.client_id][1]
            else:
                client_name = b.guest_name or "Walk-in"
                client_email = b.guest_email

            artist_name = b_user_map.get(b.artist_id, ("Unknown",))[0] if b.artist_id in b_user_map else "Unknown"
            date = b.updated_at or b.created_at

            records.append(
                ArchiveRecord(
                    id=b.id,
                    record_type="booking",
                    client_name=client_name,
                    client_email=client_email,
                    artist_name=artist_name,
                    date=date,
                    status=b.status.lower(),
                    summary=b.description[:120] if b.description else "--",
                    original_id=b.id,
                )
            )

    # ── Receipts (bookings with Stripe payment) ───────────────
    if type in ("all", "receipt"):
        receipt_q = select(Booking).where(
            Booking.stripe_payment_intent_id.isnot(None),
            Booking.stripe_payment_intent_id != "",
        )
        if search:
            receipt_q = receipt_q.where(
                or_(
                    Booking.guest_name.ilike(f"%{search}%"),
                    Booking.guest_email.ilike(f"%{search}%"),
                )
            )
        result = await db.execute(receipt_q)
        receipts = result.scalars().all()

        # Reuse b_user_map or build a new one
        r_ids = {r.client_id for r in receipts if r.client_id} | {r.artist_id for r in receipts}
        r_user_map: dict[UUID, tuple[str, str]] = {}
        if r_ids:
            name_result = await db.execute(
                select(BTTCOUser.id, BTTCOUser.name, BTTCOUser.email).where(BTTCOUser.id.in_(r_ids))
            )
            r_user_map = {row.id: (row.name, row.email) for row in name_result}

        # Deduplicate: skip receipts whose booking ID is already in records
        existing_booking_ids = {r.original_id for r in records if r.record_type == "booking"}

        filtered_receipts = receipts
        if search:
            search_lower = search.lower()
            filtered_receipts = [
                r
                for r in receipts
                if (r.guest_name and search_lower in r.guest_name.lower())
                or (r.guest_email and search_lower in r.guest_email.lower())
                or (r.client_id and r.client_id in r_user_map and search_lower in r_user_map[r.client_id][0].lower())
            ]

        for r in filtered_receipts:
            if r.id in existing_booking_ids:
                continue

            if r.client_id and r.client_id in r_user_map:
                client_name = r_user_map[r.client_id][0]
                client_email = r_user_map[r.client_id][1]
            else:
                client_name = r.guest_name or "Walk-in"
                client_email = r.guest_email

            artist_name = r_user_map.get(r.artist_id, ("Unknown",))[0] if r.artist_id in r_user_map else "Unknown"

            amount_str = f"${float(r.estimated_total):.2f}" if r.estimated_total else "Payment"
            summary = f"{amount_str} - {r.description[:80]}" if r.description else amount_str

            records.append(
                ArchiveRecord(
                    id=r.id,
                    record_type="receipt",
                    client_name=client_name,
                    client_email=client_email,
                    artist_name=artist_name,
                    date=r.updated_at or r.created_at,
                    status="paid",
                    summary=summary,
                    original_id=r.id,
                )
            )

    # ── Sort ──────────────────────────────────────────────────
    reverse = sort_dir == "desc"
    if sort_by == "date":
        records.sort(key=lambda r: r.date, reverse=reverse)
    elif sort_by == "type":
        records.sort(key=lambda r: r.record_type, reverse=reverse)
    elif sort_by == "client":
        records.sort(key=lambda r: r.client_name.lower(), reverse=reverse)

    total = len(records)
    page = records[skip : skip + limit]

    return ArchiveListResponse(records=page, total=total)


@router.get("/{record_id}/detail")
async def get_archive_detail(
    record_id: UUID,
    current_user: BTTCOUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the full detail of an archived record, including data_json
    for waivers (client info, signatures, photo ID, countersignature).
    """
    _require_staff(current_user)
    import json

    # Try document first
    result = await db.execute(select(DocumentInstance).where(DocumentInstance.id == record_id))
    doc = result.scalar_one_or_none()
    if doc:
        data = json.loads(doc.data_json) if doc.data_json else {}
        artist_name = None
        if doc.artist_id_reviewed:
            ar = await db.execute(select(BTTCOUser.name).where(BTTCOUser.id == doc.artist_id_reviewed))
            artist_name = ar.scalar_one_or_none()

        return {
            "id": str(doc.id),
            "type": doc.category,
            "title": doc.title,
            "status": doc.status,
            "client_name": doc.guest_name or doc.recipient_name,
            "client_email": doc.guest_email or doc.recipient_email,
            "artist_name": artist_name or data.get("artist_name"),
            "signed_at": doc.signed_at.isoformat() if doc.signed_at else None,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "data": data,
        }

    # Try booking
    result = await db.execute(select(Booking).where(Booking.id == record_id))
    booking = result.scalar_one_or_none()
    if booking:
        return {
            "id": str(booking.id),
            "type": "booking",
            "title": f"Booking {booking.reference_code}",
            "status": booking.status,
            "client_name": booking.guest_name,
            "client_email": booking.guest_email,
            "artist_name": None,
            "signed_at": None,
            "created_at": booking.created_at.isoformat() if booking.created_at else None,
            "data": {
                "description": booking.description,
                "placement": booking.placement,
                "style": booking.style,
                "reference_code": booking.reference_code,
                "scheduled_date": str(booking.scheduled_date) if booking.scheduled_date else None,
                "scheduled_time": str(booking.scheduled_time) if booking.scheduled_time else None,
                "estimated_total": str(booking.estimated_total) if booking.estimated_total else None,
                "reference_images": booking.reference_images or [],
            },
        }

    raise HTTPException(status_code=404, detail="Record not found")
