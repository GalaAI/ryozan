"""Move collector replies stranded in "SMS - Bella" threads into booking threads.

WHY
---
Before the inbound routing fix, every collector text to the shop (Bella) line
landed in a founder-only "SMS - Bella: <phone>" Courier thread instead of the
booking thread the collector was answering. Artists other than the founder
never saw those replies. New inbound texts now route correctly; this script
repairs the history.

WHAT IT DOES
------------
For each "SMS - Bella: <phone>" thread:

  1. Finds the collector's open (or recently completed) bookings. Only a phone
     with exactly ONE such booking is moved: an old Bella thread mixes every
     booking the collector ever texted about, so with two or more bookings the
     history cannot be split safely and is left for a person to sort.
  2. Ensures the booking thread exists and has the artist and collector.
  3. Moves the messages and SMS log rows created at or after the booking was
     made, keeping original timestamps. Older rows stay in the Bella thread.

Threads with no matching booking are left untouched. Bella threads are kept,
never deleted.

SAFETY
------
  * Dry run is the DEFAULT. Nothing is written without ``--apply``.
  * One transaction: everything lands or nothing does.
  * Idempotent: moved rows are gone from the Bella thread, so a second run skips them.
  * Output masks phone numbers to the last four digits.

USAGE
-----
    python scripts/move_bella_replies_to_booking_threads.py
    python scripts/move_bella_replies_to_booking_threads.py --exclude-name "Jared Norton"
    python scripts/move_bella_replies_to_booking_threads.py --apply --exclude-name "Jared Norton"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typing import TYPE_CHECKING

from sqlalchemy import select, update  # noqa: E402

from app.api.v1.endpoints.sms import (  # noqa: E402
    BOOKING_THREAD_LANE_ID,
    _booking_thread,
    _candidate_bookings_for_sms,
    _ensure_participant,
)
from app.models.message import Conversation, Message  # noqa: E402
from app.models.sms_message import SmsMessage  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.sms import normalise_phone  # noqa: E402

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

BELLA_PREFIX = "SMS - Bella: "


@dataclass
class Move:
    source_id: UUID
    phone_masked: str
    collector_name: str
    booking_ref: str | None
    target_id: UUID | None
    message_count: int
    skipped_reason: str | None = None
    left_behind: int = 0


def _mask(phone: str) -> str:
    return f"***-***-{phone[-4:]}" if phone else "(none)"


async def plan_and_apply(db: AsyncSession, *, apply: bool, exclude_names: set[str]) -> list[Move]:
    assert BOOKING_THREAD_LANE_ID == "bella"
    excluded = {name.strip().lower() for name in exclude_names if name.strip()}
    threads = list(
        (await db.execute(select(Conversation).where(Conversation.name.like(f"{BELLA_PREFIX}%")))).scalars().all()
    )
    moves: list[Move] = []
    for thread in threads:
        phone = normalise_phone((thread.name or "").removeprefix(BELLA_PREFIX))
        messages = list(
            (await db.execute(select(Message).where(Message.conversation_id == thread.id).order_by(Message.created_at)))
            .scalars()
            .all()
        )
        if not phone or not messages:
            continue
        inbound_sender_ids = [m.sender_id for m in messages]
        sender = await db.scalar(select(User).where(User.id.in_(inbound_sender_ids), User.role == "collector").limit(1))
        move = Move(thread.id, _mask(phone), (sender.name if sender else "") or "(unknown)", None, None, len(messages))
        moves.append(move)
        if sender is None:
            move.skipped_reason = "no collector sender"
            continue
        candidates = await _candidate_bookings_for_sms(db, sender, phone)
        if not candidates:
            move.skipped_reason = "no open booking"
            continue
        if len(candidates) > 1:
            move.skipped_reason = f"ambiguous: {len(candidates)} open bookings, sort by hand"
            continue
        booking = candidates[0]
        move.booking_ref = booking.reference_code
        movable = [m for m in messages if m.created_at >= booking.created_at]
        move.message_count = len(movable)
        move.left_behind = len(messages) - len(movable)
        if not movable:
            move.skipped_reason = "all messages predate the booking"
            continue
        # Resolve the linked account first: once a guest booking is linked, the
        # SMS sender's name is the phone and guest_name is cleared.
        collector = sender
        if booking.client_id and booking.client_id != sender.id:
            collector = await db.get(User, booking.client_id) or sender
        names = {(n or "").strip().lower() for n in (sender.name, collector.name, booking.guest_name)}
        if names & excluded:
            move.skipped_reason = "excluded"
            continue
        target = await _booking_thread(db, booking, collector)
        move.target_id = target.id
        if target.id == thread.id:
            move.skipped_reason = "already the booking thread"
            continue
        if not apply:
            continue
        await _ensure_participant(db, target.id, booking.artist_id, "owner")
        await _ensure_participant(db, target.id, collector.id, "member")
        await db.execute(
            update(Message)
            .where(Message.conversation_id == thread.id, Message.created_at >= booking.created_at)
            .values(conversation_id=target.id)
        )
        await db.execute(
            update(SmsMessage)
            .where(SmsMessage.conversation_id == thread.id, SmsMessage.created_at >= booking.created_at)
            .values(conversation_id=target.id)
        )
        latest = max(m.created_at for m in movable)
        if target.updated_at is None or latest > target.updated_at:
            target.updated_at = latest
    if apply:
        await db.commit()
    else:
        await db.rollback()
    return moves


async def run(args: argparse.Namespace) -> int:
    from app.core.database import async_session

    async with async_session() as db:
        moves = await plan_and_apply(db, apply=args.apply, exclude_names=set(args.exclude_name or []))
    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"{mode}: {len(moves)} Bella thread(s) with messages")
    for move in moves:
        status = move.skipped_reason or ("moved" if args.apply else "would move")
        print(
            f"  {move.phone_masked}  {move.collector_name:<24} booking={move.booking_ref or '-':<14}"
            f" messages={move.message_count:<3} {status}"
            + (f" ({move.left_behind} older left in Bella thread)" if move.left_behind else "")
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the moves (default is a dry run)")
    parser.add_argument("--exclude-name", action="append", help="collector or guest name to leave untouched")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
