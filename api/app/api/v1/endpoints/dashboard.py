"""
Artist dashboard summary endpoint + daily debrief system.

Aggregates Stripe balance, payout schedule, today's bookings, messages,
and quest data into a single response for the artist dashboard home.

The debrief system provides AI-generated daily business summaries accessible
via REST endpoint and through Gala Chat.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.status import BookingStatus, DocStatus
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.booking import Booking
from app.models.document import DocumentInstance
from app.models.notification import Notification
from app.models.quest import Quest, UserQuest
from app.models.user import User

logger = logging.getLogger("ryozan.dashboard")
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def debrief_recipient_ids(db: AsyncSession, settings) -> list[UUID]:
    """Who receives the cron daily debrief.

    DEBRIEF_RECIPIENT_USER_ID names one account; blank sends it to every
    founder account, oldest first.
    """
    if settings.DEBRIEF_RECIPIENT_USER_ID:
        return [UUID(settings.DEBRIEF_RECIPIENT_USER_ID)]
    result = await db.execute(select(User.id).where(User.role == "founder").order_by(User.created_at.asc()))
    return list(result.scalars().all())


def _init_stripe():
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY


def _stripe_balance_projection(current_user: User) -> dict:
    """Project the platform balance for owners and connected balance for artists."""
    is_shop_owner = current_user.role in {"founder", "admin"}
    connected_account_id = None if is_shop_owner else current_user.stripe_account_id
    artist_ready = bool(connected_account_id and current_user.stripe_onboarding_complete)
    account_scope = "platform" if is_shop_owner else "artist"

    if not is_shop_owner and not artist_ready:
        return {
            "connected": False,
            "available_balance": None,
            "pending_balance": None,
            "currency": "usd",
            "next_payout_date": None,
            "account_scope": account_scope,
        }

    stripe_kwargs = {"stripe_account": connected_account_id} if connected_account_id else {}
    try:
        _init_stripe()
        balance = stripe.Balance.retrieve(**stripe_kwargs)
        available_entries = getattr(balance, "available", []) or []
        pending_entries = getattr(balance, "pending", []) or []
        available_balance = sum(
            int(getattr(entry, "amount", 0) or 0)
            for entry in available_entries
            if getattr(entry, "currency", "usd") == "usd"
        )
        pending_balance = sum(
            int(getattr(entry, "amount", 0) or 0)
            for entry in pending_entries
            if getattr(entry, "currency", "usd") == "usd"
        )

        next_payout_date = None
        payouts = stripe.Payout.list(limit=1, status="pending", **stripe_kwargs)
        payout_rows = getattr(payouts, "data", []) or []
        if payout_rows:
            arrival = getattr(payout_rows[0], "arrival_date", None)
            if arrival:
                next_payout_date = datetime.fromtimestamp(arrival, UTC).strftime("%Y-%m-%d")

        return {
            "connected": True,
            "available_balance": available_balance,
            "pending_balance": pending_balance,
            "currency": "usd",
            "next_payout_date": next_payout_date,
            "account_scope": account_scope,
        }
    except Exception as exc:
        logger.warning("Stripe balance fetch failed for %s: %s", current_user.id, exc)
        return {
            "connected": False,
            "available_balance": None,
            "pending_balance": None,
            "currency": "usd",
            "next_payout_date": None,
            "account_scope": account_scope,
        }


@router.get("/artist-summary")
async def get_artist_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Artist dashboard summary — single endpoint aggregating:
    - Stripe Connect balance & next payout date
    - RYO balance & quest points
    - Unread message count
    - Today's bookings with client details
    - Active quest progress
    """
    role_rank = ROLE_RANK.get(current_user.role, 0)
    is_artist_or_above = role_rank >= 2

    today = date.today()

    stripe_projection = _stripe_balance_projection(current_user)
    stripe_balance = stripe_projection["available_balance"]

    # --- Unread notifications ---
    unread_count = 0
    try:
        result = await db.execute(
            select(func.count(Notification.id)).where(
                and_(
                    Notification.user_id == current_user.id,
                    Notification.is_read.is_(False),
                )
            )
        )
        unread_count = result.scalar() or 0
    except Exception as e:
        logger.warning("Unread count failed: %s", e)

    # --- Today's bookings ---
    today_bookings = []
    try:
        if is_artist_or_above:
            # For founder/admin: show all; for artist: show only theirs
            query = select(Booking).where(Booking.scheduled_date == today)
            if current_user.role == "artist":
                query = query.where(Booking.artist_id == current_user.id)
            query = query.order_by(Booking.scheduled_time)
            result = await db.execute(query)
            bookings = result.scalars().all()
            for b in bookings:
                # Resolve client name from FK or guest_name fallback
                client_name = b.guest_name
                if b.client_id:
                    name_result = await db.execute(select(User.name).where(User.id == b.client_id))
                    client_name = name_result.scalar_one_or_none() or b.guest_name

                today_bookings.append(
                    {
                        "id": str(b.id),
                        "client_name": client_name or "Walk-in",
                        "client_email": b.guest_email,
                        "scheduled_date": str(b.scheduled_date) if b.scheduled_date else None,
                        "scheduled_time": b.scheduled_time,
                        "description": b.description,
                        "status": b.status,
                        "deposit_amount": float(b.deposit_amount) if b.deposit_amount else None,
                    }
                )
    except Exception as e:
        logger.warning("Today bookings fetch failed: %s", e)

    # --- Active quests ---
    active_quests = []
    try:
        result = await db.execute(
            select(Quest, UserQuest)
            .outerjoin(
                UserQuest,
                and_(
                    UserQuest.quest_id == Quest.id,
                    UserQuest.user_id == current_user.id,
                ),
            )
            .where(Quest.is_active)
        )
        for quest, progress in result.all():
            active_quests.append(
                {
                    "id": str(quest.id),
                    "title": quest.title,
                    "description": quest.description,
                    "xp_reward": quest.xp_reward,
                    "coin_reward": quest.coin_reward,
                    "target_count": quest.target_count,
                    "current_progress": progress.progress if progress else 0,
                    "completed": progress.completed if progress else False,
                }
            )
    except Exception as e:
        logger.warning("Quest fetch failed: %s", e)

    return {
        "user": {
            "name": current_user.name,
            "role": current_user.role,
            "bttcoin_balance": current_user.bttcoin_balance or 0,
            "quest_points": current_user.quest_points or 0,
            "avatar_url": current_user.avatar_url,
        },
        "stripe": {
            **stripe_projection,
        },
        "messages": {
            "unread_count": unread_count,
        },
        "today": {
            "bookings": today_bookings,
            "booking_count": len(today_bookings),
        },
        "quests": {
            "active": active_quests,
            "total_active": len(active_quests),
            "completed_count": sum(1 for q in active_quests if q["completed"]),
        },
        "briefing": {
            "generated_at": datetime.now().isoformat(),
            "summary": _generate_briefing(
                current_user.name,
                today_bookings,
                unread_count,
                active_quests,
                stripe_balance,
            ),
        },
    }


def _generate_briefing(
    name: str,
    today_bookings: list,
    unread_count: int,
    quests: list,
    stripe_balance: int | None,
) -> str:
    """
    Generate a plain-text daily briefing for the artist.
    This is the GalaBot v0 — deterministic template.
    Future: replace with AI-generated summary via Claude subagent.
    """
    parts = []
    name.split()[0] if name else "Artist"

    # Appointments
    if today_bookings:
        count = len(today_bookings)
        parts.append(f"You have {count} session{'s' if count != 1 else ''} today.")
        next_b = today_bookings[0]
        time_str = next_b.get("scheduled_time", "")
        if time_str:
            h, m = int(time_str.split(":")[0]), time_str.split(":")[1]
            ampm = "PM" if h >= 12 else "AM"
            h12 = h % 12 or 12
            parts.append(f"First up: {next_b['client_name'] or 'a client'} at {h12}:{m} {ampm}.")
    else:
        parts.append("No sessions scheduled today — open day.")

    # Messages
    if unread_count > 0:
        parts.append(f"{unread_count} unread message{'s' if unread_count != 1 else ''} waiting.")

    # Quests
    incomplete = [q for q in quests if not q["completed"]]
    if incomplete:
        parts.append(f"{len(incomplete)} active quest{'s' if len(incomplete) != 1 else ''} in progress.")

    # Earnings
    if stripe_balance is not None and stripe_balance > 0:
        dollars = stripe_balance / 100
        parts.append(f"${dollars:,.2f} available in your Stripe balance.")

    return " ".join(parts)


@router.get("/daily-digest")
async def get_daily_digest(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Founder/Admin daily digest — aggregates today's full shop activity.

    Returns: bookings (new, confirmed, completed), revenue, waivers,
    notifications, conversations, and a narrative summary.
    """
    from app.models.message import Message

    role_rank = ROLE_RANK.get(current_user.role, 0)
    if role_rank < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Admin+ only")

    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    today - timedelta(days=1)

    # ── New bookings today ──
    new_bookings_result = await db.execute(
        select(func.count()).select_from(Booking).where(Booking.created_at >= today_start)
    )
    new_bookings = new_bookings_result.scalar() or 0

    # ── Bookings by status ──
    status_counts = {}
    for status in [
        BookingStatus.PENDING,
        BookingStatus.QUOTED,
        BookingStatus.CONFIRMED,
        BookingStatus.COMPLETED,
        BookingStatus.CANCELLED,
    ]:
        r = await db.execute(
            select(func.count())
            .select_from(Booking)
            .where(and_(Booking.status == status, Booking.created_at >= today_start))
        )
        count = r.scalar() or 0
        if count > 0:
            status_counts[status] = count

    # ── Today's sessions (confirmed with today's date) ──
    sessions_result = await db.execute(
        select(Booking)
        .where(
            and_(
                Booking.scheduled_date == today, Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.COMPLETED])
            )
        )
        .order_by(Booking.scheduled_time)
    )
    todays_sessions = []
    for b in sessions_result.scalars().all():
        client_name = b.guest_name
        if b.client_id:
            name_r = await db.execute(select(User.name).where(User.id == b.client_id))
            client_name = name_r.scalar_one_or_none() or b.guest_name

        artist_r = await db.execute(select(User.name).where(User.id == b.artist_id))
        artist_name = artist_r.scalar_one_or_none() or "Unknown"

        todays_sessions.append(
            {
                "reference_code": b.reference_code,
                "client_name": client_name,
                "artist_name": artist_name,
                "scheduled_time": b.scheduled_time,
                "description": b.description,
                "status": b.status,
            }
        )

    # ── Waivers submitted today ──
    waivers_result = await db.execute(
        select(func.count())
        .select_from(DocumentInstance)
        .where(
            and_(
                DocumentInstance.category == "waiver",
                DocumentInstance.created_at >= today_start,
            )
        )
    )
    waivers_today = waivers_result.scalar() or 0

    # ── Unread notifications ──
    notif_result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            and_(
                Notification.user_id == current_user.id,
                Notification.is_read.is_(False),
            )
        )
    )
    unread_notifs = notif_result.scalar() or 0

    # ── Messages today ──
    messages_result = await db.execute(
        select(func.count()).select_from(Message).where(Message.created_at >= today_start)
    )
    messages_today = messages_result.scalar() or 0

    # ── Total active clients (users with collector role) ──
    clients_result = await db.execute(select(func.count()).select_from(User).where(User.role == "collector"))
    total_clients = clients_result.scalar() or 0

    # ── Revenue (Stripe, if connected) ──
    revenue_today = 0
    try:
        settings = get_settings()
        if settings.STRIPE_SECRET_KEY:
            _init_stripe()
            # Get today's charges
            charges = stripe.Charge.list(
                created={"gte": int(today_start.timestamp())},
                limit=100,
            )
            revenue_today = sum(c.amount for c in charges.data if c.paid) / 100
    except Exception:
        pass

    # ── Build narrative summary ──
    parts = []
    if new_bookings:
        parts.append(f"{new_bookings} new booking{'s' if new_bookings != 1 else ''} came in today.")
    if todays_sessions:
        parts.append(f"{len(todays_sessions)} session{'s' if len(todays_sessions) != 1 else ''} on the schedule.")
    if waivers_today:
        parts.append(f"{waivers_today} waiver{'s' if waivers_today != 1 else ''} submitted.")
    if messages_today:
        parts.append(f"{messages_today} message{'s' if messages_today != 1 else ''} exchanged.")
    if revenue_today > 0:
        parts.append(f"${revenue_today:,.2f} in revenue.")
    if not parts:
        parts.append("Quiet day. No activity recorded yet.")

    return {
        "date": today.isoformat(),
        "summary": " ".join(parts),
        "bookings": {
            "new_today": new_bookings,
            "by_status": status_counts,
        },
        "sessions": todays_sessions,
        "waivers_submitted": waivers_today,
        "messages_today": messages_today,
        "unread_notifications": unread_notifs,
        "total_clients": total_clients,
        "revenue_today": revenue_today,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DAILY DEBRIEF SYSTEM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class DebriefResponse(BaseModel):
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    summary: str  # AI-generated natural language debrief
    stats: dict  # Raw stats for component rendering
    components: list[dict]  # Rich UI components
    action_items: list[dict]  # Things that need attention


async def gather_debrief_data(db: AsyncSession) -> dict:
    """
    Gather all debrief data for the last 24 hours.

    Returns a raw dict with stats, booking details, action items, and
    comparison data. Reusable from both the REST endpoint and Gala Chat tool.
    """
    now = datetime.now(UTC)
    period_start = now - timedelta(hours=24)
    yesterday_start = period_start - timedelta(hours=24)

    # ── New bookings in the last 24h ──
    new_bookings_result = await db.execute(
        select(Booking).where(Booking.created_at >= period_start).order_by(desc(Booking.created_at))
    )
    new_bookings = new_bookings_result.scalars().all()

    new_booking_details = []
    for b in new_bookings:
        client_name = b.guest_name
        if b.client_id:
            name_r = await db.execute(select(User.name).where(User.id == b.client_id))
            client_name = name_r.scalar_one_or_none() or b.guest_name

        new_booking_details.append(
            {
                "id": str(b.id),
                "client_name": client_name or "Walk-in",
                "client_email": b.guest_email,
                "description": (b.description or "")[:120],
                "status": b.status,
                "reference_code": b.reference_code,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "placement": b.placement,
                "style_preference": b.style_preference,
            }
        )

    # ── Completed sessions in the last 24h ──
    completed_result = await db.execute(
        select(func.count())
        .select_from(Booking)
        .where(
            and_(
                Booking.status == BookingStatus.COMPLETED,
                Booking.updated_at >= period_start,
            )
        )
    )
    completed_sessions = completed_result.scalar() or 0

    # ── New waiver submissions in the last 24h ──
    waivers_result = await db.execute(
        select(func.count())
        .select_from(DocumentInstance)
        .where(
            and_(
                DocumentInstance.category == "waiver",
                DocumentInstance.created_at >= period_start,
            )
        )
    )
    new_waivers = waivers_result.scalar() or 0

    # ── New user registrations in the last 24h ──
    new_users_result = await db.execute(select(func.count()).select_from(User).where(User.created_at >= period_start))
    new_accounts = new_users_result.scalar() or 0

    # ── Revenue (Stripe, if connected) ──
    revenue_today = None
    try:
        settings = get_settings()
        if settings.STRIPE_SECRET_KEY:
            _init_stripe()
            charges = stripe.Charge.list(
                created={"gte": int(period_start.timestamp())},
                limit=100,
            )
            revenue_today = sum(c.amount for c in charges.data if c.paid) / 100
    except Exception as e:
        logger.warning("Debrief Stripe revenue fetch failed: %s", e)

    # ── Pending action items ──
    action_items = []

    # Unreviewed waivers (submitted/signed but not approved)
    pending_waivers_result = await db.execute(
        select(DocumentInstance)
        .where(
            and_(
                DocumentInstance.category == "waiver",
                DocumentInstance.status.in_([DocStatus.SIGNED, "submitted", "pending_review"]),
            )
        )
        .order_by(desc(DocumentInstance.created_at))
        .limit(20)
    )
    pending_waivers = pending_waivers_result.scalars().all()
    for w in pending_waivers:
        action_items.append(
            {
                "type": "waiver_review",
                "priority": "medium",
                "title": f"Review waiver: {w.guest_name or w.recipient_name or 'Unknown'}",
                "detail": f"{w.title} ({w.status})",
                "entity_id": str(w.id),
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
        )

    # Unresponded bookings (pending for more than a few hours)
    stale_cutoff = now - timedelta(hours=4)
    pending_bookings_result = await db.execute(
        select(Booking)
        .where(
            and_(
                Booking.status == BookingStatus.PENDING,
                Booking.created_at <= stale_cutoff,
            )
        )
        .order_by(Booking.created_at)
        .limit(20)
    )
    pending_bookings = pending_bookings_result.scalars().all()
    for b in pending_bookings:
        hours_waiting = (now - b.created_at).total_seconds() / 3600 if b.created_at else 0
        action_items.append(
            {
                "type": "booking_response",
                "priority": "high" if hours_waiting > 24 else "medium",
                "title": f"Respond to booking: {b.guest_name or 'Unknown'}",
                "detail": f"Waiting {int(hours_waiting)}h ({b.reference_code})",
                "entity_id": str(b.id),
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
        )

    pending_actions_count = len(action_items)

    # ── Previous day comparison ──
    prev_bookings_result = await db.execute(
        select(func.count())
        .select_from(Booking)
        .where(
            and_(
                Booking.created_at >= yesterday_start,
                Booking.created_at < period_start,
            )
        )
    )
    prev_new_bookings = prev_bookings_result.scalar() or 0

    # ── Build stats dict ──
    stats = {
        "new_bookings": len(new_bookings),
        "completed_sessions": completed_sessions,
        "new_waivers": new_waivers,
        "new_accounts": new_accounts,
        "pending_actions": pending_actions_count,
        "revenue_today": revenue_today,
    }

    # ── Build components ──
    components = [
        {
            "type": "stat-card",
            "data": {
                "new_bookings": stats["new_bookings"],
                "completed_sessions": completed_sessions,
                "new_waivers": new_waivers,
                "new_accounts": new_accounts,
                "pending_actions": pending_actions_count,
                "revenue_today": revenue_today,
            },
        },
    ]

    if new_booking_details:
        components.append(
            {
                "type": "booking-list",
                "data": {
                    "title": "New Booking Requests (24h)",
                    "bookings": new_booking_details,
                    "selectable": True,
                },
            }
        )

    return {
        "period_start": period_start,
        "period_end": now,
        "stats": stats,
        "components": components,
        "action_items": action_items,
        "new_booking_details": new_booking_details,
        "prev_new_bookings": prev_new_bookings,
        "completed_sessions": completed_sessions,
    }


def debrief_owner_phrase(settings) -> str:
    name = settings.STUDIO_OWNER_NAME.strip()
    return f"the shop owner, {name}" if name else "the shop owner"


async def generate_ai_summary(data: dict, db=None, user=None) -> str:
    """
    Use the configured AI lane to generate a natural language debrief.

    Falls back to a template summary if the gateway is unavailable, the call
    fails, or the limited-release gate excludes `user`. The debrief itself still
    renders in every case; only the natural-language pass is skipped.
    """
    settings = get_settings()
    stats = data["stats"]

    # Build a context block for the AI
    context_lines = [
        f"Period: last 24 hours ending {data['period_end'].strftime('%B %d, %Y at %I:%M %p')}",
        f"New booking requests: {stats['new_bookings']}",
        f"Completed sessions: {stats['completed_sessions']}",
        f"New waiver submissions: {stats['new_waivers']}",
        f"New account registrations: {stats['new_accounts']}",
        f"Pending action items: {stats['pending_actions']}",
    ]

    if stats["revenue_today"] is not None:
        context_lines.append(f"Revenue: ${stats['revenue_today']:,.2f}")
    else:
        context_lines.append("Revenue: data unavailable")

    prev = data.get("prev_new_bookings", 0)
    if prev > 0:
        context_lines.append(f"Previous day new bookings: {prev}")

    if data["new_booking_details"]:
        context_lines.append("\nNew bookings:")
        for b in data["new_booking_details"][:10]:
            context_lines.append(
                f"  - {b['client_name']}: {b['description'][:80]} (status: {b['status']}, ref: {b['reference_code']})"
            )

    if data["action_items"]:
        context_lines.append(f"\nAction items ({len(data['action_items'])}):")
        for item in data["action_items"][:10]:
            context_lines.append(f"  - [{item['priority'].upper()}] {item['title']}: {item['detail']}")

    context_block = "\n".join(context_lines)

    from app.services.ai_routing import ai_access_allowed

    if not ai_access_allowed(settings, user):
        logger.info("Debrief AI summary skipped: AI is in limited release; using template summary")
        return _fallback_summary(stats, data)

    if not settings.ARK_MODEL_GATEWAY_KEY:
        return _fallback_summary(stats, data)

    try:
        from app.services.ai_routing import call_ark_gateway_chat_completion

        response = await call_ark_gateway_chat_completion(
            settings=settings,
            primary_model=settings.AI_PREMIUM_PRIMARY_MODEL,
            fallback_models=[settings.AI_PREMIUM_FALLBACK_MODEL, settings.AI_PAID_PRIMARY_MODEL],
            max_tokens=1200,
            tools=[],
            system_prompt=(
                f"You are Gala, the AI assistant for {settings.STUDIO_NAME}. "
                f"Write a full daily operations debrief for {debrief_owner_phrase(settings)}. "
                "Use the available calendar, booking, waiver, account, quest, lesson, and inbox signals. "
                "If a signal is not present in the data, say what is missing instead of inventing it. "
                "Structure the response like an outline but write it like a report. "
                "Use these plain-text sections: Overview, Priority Readout, Calendar and Sessions, "
                "Inbox and Waivers, Quests and Guild Lessons, Tips and Next Moves. "
                "Each section should be useful enough to act on, with specific counts, names, statuses, "
                "and risks when available. Include tutorial-style guidance, tips, and suggested navigation "
                "targets such as Huddl, Courier, Guild, Herald, Hustl, Bookings, or Quests. "
                "Be direct, no fluff, no emojis. Keep it under 650 words."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Generate the daily debrief from this data:\n\n{context_block}",
                }
            ],
        )

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        result = content.strip() if isinstance(content, str) and content.strip() else _fallback_summary(stats, data)

        if db is not None:
            from app.services.api_budget import log_usage

            usage = response.get("usage") or {}
            await log_usage(
                db,
                "dashboard/debrief",
                int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            )

        return result

    except Exception as e:
        logger.warning("Debrief AI summary generation failed: %s", e)
        return _fallback_summary(stats, data)


def _fallback_summary(stats: dict, data: dict) -> str:
    """Deterministic report fallback when AI is unavailable."""
    nb = stats["new_bookings"]
    cs = stats["completed_sessions"]
    nw = stats["new_waivers"]
    na = stats["new_accounts"]
    pa = stats["pending_actions"]
    prev = data.get("prev_new_bookings", 0)
    revenue = stats.get("revenue_today")
    booking_details = data.get("new_booking_details") or []
    action_items = data.get("action_items") or []

    trend = "steady"
    if prev > 0 and nb != prev:
        trend = "up from yesterday" if nb > prev else "down from yesterday"

    revenue_line = "Revenue data is unavailable."
    if revenue is not None:
        revenue_line = f"Revenue captured in this window is ${revenue:,.2f}."

    lines = [
        "Overview",
        (
            f"Last 24 hours: {nb} new booking{'s' if nb != 1 else ''}, "
            f"{cs} completed session{'s' if cs != 1 else ''}, "
            f"{nw} waiver{'s' if nw != 1 else ''}, and {na} new account{'s' if na != 1 else ''}. "
            f"Booking pace is {trend}. {revenue_line}"
        ),
        "",
        "Priority Readout",
        (
            f"{pa} item{'s' if pa != 1 else ''} need{'s' if pa == 1 else ''} attention. "
            "Handle stale bookings first, then waiver review, then client follow-up."
            if pa > 0
            else "No urgent action items were detected. Use the clear window for prep, outreach, and lesson progress."
        ),
        "",
        "Calendar and Sessions",
        (
            f"{cs} session{'s' if cs != 1 else ''} were completed in the reporting window. "
            "Open Huddl to compare today's schedule against booking readiness, deposits, references, and placement details."
        ),
        "",
        "Inbox and Waivers",
        (
            f"{nw} waiver{'s' if nw != 1 else ''} arrived and {nb} booking request{'s' if nb != 1 else ''} entered the queue. "
            "Open Courier for message context and Bookings for intake decisions."
        ),
        "",
        "Quests and Guild Lessons",
        "Quest and lesson telemetry is not attached to this endpoint yet. Use Guild and Quests as the fallback readout until those signals are wired into the debrief context.",
        "",
        "Tips and Next Moves",
    ]

    if booking_details:
        first = booking_details[0]
        lines.append(
            f"Start with {first.get('client_name') or 'the newest client'} "
            f"({first.get('status') or 'status pending'}, {first.get('reference_code') or 'no reference code'})."
        )
    elif action_items:
        first_action = action_items[0]
        lines.append(f"Start with {first_action.get('title')}: {first_action.get('detail')}.")
    else:
        lines.append(
            "Use the next admin block to prep references, confirm consent requirements, and refresh Herald updates."
        )

    return "\n".join(lines)


@router.get("/debrief", response_model=DebriefResponse)
async def get_daily_debrief(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Daily debrief endpoint. Returns AI-generated summary + raw data.

    Founder/Admin only. Aggregates last 24 hours of business activity
    and uses Claude to generate a natural language debrief.
    """
    role_rank = ROLE_RANK.get(current_user.role, 0)
    if role_rank < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Admin+ only")

    data = await gather_debrief_data(db)
    summary = await generate_ai_summary(data, db=db, user=current_user)

    return DebriefResponse(
        generated_at=datetime.now(UTC),
        period_start=data["period_start"],
        period_end=data["period_end"],
        summary=summary,
        stats=data["stats"],
        components=data["components"],
        action_items=data["action_items"],
    )


@router.post("/debrief/generate")
async def generate_and_store_debrief(
    db: AsyncSession = Depends(get_db),
    x_cron_secret: str = Header(alias="X-Cron-Secret"),
):
    """
    Cron-triggered debrief generation.

    Generates the daily debrief and stores it as a notification for the
    founder user. Called by an external cron job (n8n, Railway cron, etc.).

    Requires X-Cron-Secret header for authentication.
    """
    settings = get_settings()
    if x_cron_secret != settings.CRON_SECRET:
        raise HTTPException(status_code=403, detail="Invalid cron secret")

    data = await gather_debrief_data(db)
    summary = await generate_ai_summary(data, db=db)

    recipient_ids = await debrief_recipient_ids(db, settings)
    notifications = [
        Notification(
            user_id=recipient_id,
            title="Daily Debrief",
            body=summary,
            notification_type="system",
            action_url="/dashboard",
        )
        for recipient_id in recipient_ids
    ]
    db.add_all(notifications)
    await db.commit()

    if notifications:
        logger.info("Daily debrief generated and stored for %d recipient(s)", len(notifications))
    else:
        logger.warning("Daily debrief generated but no recipient: set DEBRIEF_RECIPIENT_USER_ID or add a founder")

    return {
        "status": "ok" if notifications else "no_recipient",
        "notification_id": str(notifications[0].id) if notifications else None,
        "recipient_count": len(notifications),
        "summary_length": len(summary),
        "stats": data["stats"],
    }
