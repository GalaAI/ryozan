"""Private contacts and intake export bridge for Huddle/Nexus."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking import Booking
from app.models.document import DocumentInstance
from app.models.user import BTTCOUser
from app.schemas.contact import ContactExportResponse, ContactIntakeRecord, ContactListResponse, ContactRecord

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _require_contact_access(current_user: BTTCOUser) -> None:
    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK["assistant"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires assistant role or higher",
        )


def _normalize_email(email: str | None) -> str | None:
    value = (email or "").strip().lower()
    return value or None


def _normalize_phone(phone: str | None) -> str | None:
    digits = re.sub(r"\D+", "", phone or "")
    return digits or None


def _contact_key(name: str, email: str | None, phone: str | None) -> str:
    if email:
        return f"email:{email}"
    if phone:
        return f"phone:{phone}"
    return f"name:{name.strip().lower() or 'unknown'}"


def _contact_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _display_name(name: str | None, email: str | None) -> str:
    clean = (name or "").strip()
    if clean:
        return clean
    if email:
        return email.split("@")[0]
    return "Unknown"


def _touch_contact(
    contacts: dict[str, ContactRecord],
    *,
    name: str | None,
    email: str | None,
    phone: str | None,
    source: str,
    booking: Booking | None = None,
    marketing_opt_in: bool = False,
) -> ContactRecord:
    normalized_email = _normalize_email(email)
    normalized_phone = _normalize_phone(phone)
    display_name = _display_name(name, normalized_email)
    key = _contact_key(display_name, normalized_email, normalized_phone)
    existing = contacts.get(key)
    if existing is None:
        existing = ContactRecord(
            id=_contact_id(key),
            name=display_name,
            email=normalized_email,
            phone=phone or normalized_phone,
            source=source,
            lifecycle_stage="lead",
            marketing_opt_in=marketing_opt_in,
            tags=[source],
        )
        contacts[key] = existing

    if source not in existing.tags:
        existing.tags.append(source)
    if marketing_opt_in:
        existing.marketing_opt_in = True
    if booking:
        existing.booking_count += 1
        existing.lifecycle_stage = "client"
        existing.last_booking_status = booking.status
        if booking.status not in existing.tags:
            existing.tags.append(booking.status)
        if booking.source and booking.source not in existing.tags:
            existing.tags.append(booking.source)
        if booking.created_at and (existing.last_booking_at is None or booking.created_at > existing.last_booking_at):
            existing.last_booking_at = booking.created_at
            existing.last_booking_status = booking.status
    return existing


async def _load_contact_export(db: AsyncSession, current_user: BTTCOUser) -> ContactExportResponse:
    _require_contact_access(current_user)

    user_result = await db.execute(select(BTTCOUser))
    users = user_result.scalars().all()
    users_by_id: dict[UUID, BTTCOUser] = {user.id: user for user in users}

    booking_stmt = select(Booking)
    if current_user.role == "artist":
        booking_stmt = booking_stmt.where(Booking.artist_id == current_user.id)
    booking_result = await db.execute(booking_stmt)
    bookings = booking_result.scalars().all()
    visible_client_ids = (
        {booking.client_id for booking in bookings if booking.client_id} if current_user.role == "artist" else None
    )
    waiver_result = await db.execute(select(DocumentInstance).where(DocumentInstance.category == "waiver"))
    waivers = waiver_result.scalars().all()

    contacts: dict[str, ContactRecord] = {}
    intake_items: list[ContactIntakeRecord] = []

    for user in users:
        if user.role != "collector":
            continue
        if visible_client_ids is not None and user.id not in visible_client_ids:
            continue
        _touch_contact(
            contacts,
            name=user.name,
            email=user.email,
            phone=user.phone,
            source="bttco_user",
            marketing_opt_in=user.marketing_opt_in,
        )

    for booking in bookings:
        client = users_by_id.get(booking.client_id) if booking.client_id else None
        contact = _touch_contact(
            contacts,
            name=booking.guest_name or (client.name if client else None),
            email=booking.guest_email or (client.email if client else None),
            phone=booking.guest_phone or (client.phone if client else None),
            source="booking",
            booking=booking,
            marketing_opt_in=client.marketing_opt_in if client else False,
        )
        intake_items.append(
            ContactIntakeRecord(
                source_system="bttco_fastapi",
                source_type="booking",
                source_ref=booking.reference_code or str(booking.id),
                contact_id=contact.id,
                occurred_at=booking.created_at or datetime.now(UTC),
                payload={
                    "booking_id": str(booking.id),
                    "reference_code": booking.reference_code,
                    "status": booking.status,
                    "artist_id": str(booking.artist_id),
                    "client_id": str(booking.client_id) if booking.client_id else None,
                    "conversation_id": str(booking.conversation_id) if booking.conversation_id else None,
                    "placement": booking.placement,
                    "style_preference": booking.style_preference,
                    "preferred_date": booking.preferred_date.isoformat() if booking.preferred_date else None,
                    "scheduled_date": booking.scheduled_date.isoformat() if booking.scheduled_date else None,
                    "source": booking.source,
                },
            )
        )

    for waiver in waivers:
        try:
            waiver_data = json.loads(waiver.data_json or "{}")
        except json.JSONDecodeError:
            waiver_data = {}
        if not isinstance(waiver_data, dict):
            waiver_data = {}
        if current_user.role == "artist":
            waiver_artist_id = waiver_data.get("artist_id")
            if waiver_artist_id != str(current_user.id) and waiver.artist_id_reviewed != current_user.id:
                continue
        client = users_by_id.get(waiver.user_id) if waiver.user_id else None
        contact = _touch_contact(
            contacts,
            name=waiver.guest_name or waiver.recipient_name or (client.name if client else None),
            email=waiver.guest_email or waiver.recipient_email or (client.email if client else None),
            phone=waiver_data.get("phone") or (client.phone if client else None),
            source="waiver",
            marketing_opt_in=client.marketing_opt_in if client else False,
        )
        if waiver.status not in contact.tags:
            contact.tags.append(waiver.status)
        intake_items.append(
            ContactIntakeRecord(
                source_system="bttco_fastapi",
                source_type="waiver",
                source_ref=str(waiver.id),
                contact_id=contact.id,
                occurred_at=waiver.created_at or datetime.now(UTC),
                payload={
                    "waiver_id": str(waiver.id),
                    "status": waiver.status,
                    "user_id": str(waiver.user_id) if waiver.user_id else None,
                    "guest_social": waiver.guest_social,
                    "signed_at": waiver.signed_at.isoformat() if waiver.signed_at else None,
                    "source": "waiver",
                },
            )
        )

    sorted_contacts = sorted(
        contacts.values(),
        key=lambda contact: (
            contact.last_booking_at is None,
            -(contact.last_booking_at.timestamp() if contact.last_booking_at else 0),
            contact.name.lower(),
        ),
    )
    return ContactExportResponse(
        contacts=sorted_contacts,
        intake_items=sorted(intake_items, key=lambda item: item.occurred_at, reverse=True),
        total_contacts=len(sorted_contacts),
        total_intake_items=len(intake_items),
    )


@router.get("", response_model=ContactListResponse)
async def list_contacts(
    search: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    export = await _load_contact_export(db, current_user)
    contacts = export.contacts
    if search:
        needle = search.strip().lower()
        contacts = [
            contact
            for contact in contacts
            if needle in contact.name.lower()
            or (contact.email and needle in contact.email.lower())
            or (contact.phone and needle in contact.phone.lower())
        ]
    return ContactListResponse(contacts=contacts[skip : skip + limit], total=len(contacts))


@router.get("/intake-export", response_model=ContactExportResponse)
async def export_contacts_for_nexus(
    db: AsyncSession = Depends(get_db),
    current_user: BTTCOUser = Depends(get_current_user),
):
    return await _load_contact_export(db, current_user)
