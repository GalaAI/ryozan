"""Ryozan MCP tools — remote-work surface.

Design rules applied here:
  * High-leverage tools, not thin wrappers on every route.
  * search_* over list_all_*.
  * Human-readable context out, not raw UUID soup.
  * Paginated and truncated by default.
  * External sends use the existing Courier channel-send endpoint so consent,
    participant scope, provider delivery, and receipt logging remain structural.
"""

from __future__ import annotations

import json
from typing import Any

from app.constants.timezones import shop_timezone
from app.core.config import get_settings
from app.mcp.upstream import UpstreamError, get_health, get_json, write_json
from app.services.studio_config import describe_hours

MAX_ROWS = 25
DEFAULT_DEV_WORKSPACE_SLUG = "dev-tracker"

RYOZAN_DEV_SCOPES = [
    "ryozan.dev.read",
    "ryozan.dev.write",
    "ryozan.dev.execute",
    "ryozan.dev.build",
    "ryozan.dev.test",
    "ryozan.dev.git",
    "ryozan.dev.migrate",
    "ryozan.dev.deploy",
    "ryozan.cloud.read",
    "ryozan.cloud.write",
    "ryozan.scope.read",
    "ryozan.scope.write",
    "ryozan.admin",
]


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_cents(value: Any) -> str:
    """Stripe balances come back in cents."""
    try:
        return f"${int(value) / 100:,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _today_booking_line(booking: dict) -> str:
    """Format an entry from /dashboard/artist-summary today.bookings.

    That payload is a hand-built dict, NOT a BookingResponse, so it has a
    narrower field set than _booking_line expects.
    """
    time = booking.get("scheduled_time") or "time TBD"
    name = booking.get("client_name") or "Walk-in"
    line = f"  {time} · {name} · [{booking.get('status', '?')}]"
    desc = (booking.get("description") or "").strip()
    if desc:
        line += f"\n      {desc[:120]}"
    if booking.get("deposit_amount") is not None:
        line += f"\n      deposit {_fmt_money(booking['deposit_amount'])}"
    line += f"\n      booking_id: {booking.get('id')}"
    return line


def _fmt_when(booking: dict) -> str:
    date = booking.get("scheduled_date") or booking.get("preferred_date")
    time = booking.get("scheduled_time") or booking.get("preferred_time")
    if not date:
        return "unscheduled"
    label = str(date) + (f" {time}" if time else "")
    return label if booking.get("scheduled_date") else f"{label} (requested, not booked)"


def _who(booking: dict) -> str:
    name = booking.get("resolved_name") or booking.get("client_name") or "unknown client"
    contact = booking.get("resolved_phone") or booking.get("resolved_email") or ""
    return f"{name}{f' · {contact}' if contact else ''}"


def _booking_line(booking: dict) -> str:
    bits = [
        f"[{booking.get('status', '?')}] {_fmt_when(booking)} — {_who(booking)}",
        f"    {(booking.get('description') or 'no description').strip()[:120]}",
    ]
    meta = []
    if booking.get("placement"):
        meta.append(str(booking["placement"]))
    if booking.get("size_estimate"):
        meta.append(str(booking["size_estimate"]))
    if booking.get("estimated_total") is not None:
        meta.append(f"est {_fmt_money(booking['estimated_total'])}")
    meta.append("deposit paid" if booking.get("deposit_paid") else "no deposit")
    if booking.get("reference_code"):
        meta.append(f"ref {booking['reference_code']}")
    bits.append(f"    {' · '.join(meta)}")
    bits.append(f"    booking_id: {booking.get('id')}")
    return "\n".join(bits)


async def tool_today(token: str | None, args: dict) -> str:
    """Aggregate 'what needs me right now' view.

    Mirrors the nested shape of GET /dashboard/artist-summary exactly:
    user / stripe / messages / today / quests / briefing.
    """
    data = await get_json("/dashboard/artist-summary", token)
    user = data.get("user") or {}
    stripe = data.get("stripe") or {}
    today = data.get("today") or {}
    quests = data.get("quests") or {}

    who = user.get("name") or "artist"
    lines = [f"Ryozan — today at a glance for {who}", ""]

    lines.append(f"Unread messages: {(data.get('messages') or {}).get('unread_count', 0)}")

    if stripe.get("connected"):
        # available_balance and pending_balance are in cents.
        lines.append(f"Stripe available: {_fmt_cents(stripe.get('available_balance'))}")
        if stripe.get("pending_balance"):
            lines.append(f"Stripe pending: {_fmt_cents(stripe.get('pending_balance'))}")
        if stripe.get("next_payout_date"):
            lines.append(f"Next payout: {stripe['next_payout_date']}")
    else:
        lines.append("Stripe: not connected")

    lines.append(f"RYO: {user.get('bttcoin_balance', 0)} · quest points: {user.get('quest_points', 0)}")
    if quests.get("total_active"):
        lines.append(f"Active quests: {quests['total_active']} ({quests.get('completed_count', 0)} complete)")

    bookings = today.get("bookings") or []
    lines.append("")
    lines.append(f"Today's bookings ({today.get('booking_count', len(bookings))}):")
    if not bookings:
        lines.append("  nothing on the books today")
    for booking in bookings[:MAX_ROWS]:
        lines.append(_today_booking_line(booking))

    summary = (data.get("briefing") or {}).get("summary")
    if summary:
        lines.extend(["", "Briefing:", f"  {summary}"])
    return "\n".join(lines)


async def tool_search_bookings(token: str | None, args: dict) -> str:
    """Search the booking pipeline with role-correct scoping."""
    limit = min(int(args.get("limit") or 20), 100)
    params: dict[str, Any] = {"limit": limit, "skip": int(args.get("skip") or 0)}
    if args.get("status"):
        params["status"] = args["status"]
    if args.get("whole_shop"):
        params["all"] = "true"

    data = await get_json("/bookings/", token, params)
    bookings = data.get("bookings", [])
    total = data.get("total", len(bookings))

    query = (args.get("query") or "").strip().lower()
    if query:
        bookings = [
            b
            for b in bookings
            if query in str(b.get("resolved_name") or "").lower()
            or query in str(b.get("client_name") or "").lower()
            or query in str(b.get("description") or "").lower()
            or query in str(b.get("reference_code") or "").lower()
        ]

    header = f"{len(bookings)} booking(s) shown of {total} matching in scope."
    if query:
        header += f" Text filter: '{query}'."
    if not bookings:
        return header + "\n\nNothing matched. Try a different status or drop the query."
    body = "\n\n".join(_booking_line(b) for b in bookings[:MAX_ROWS])
    return f"{header}\n\n{body}"


async def tool_get_booking(token: str | None, args: dict) -> str:
    """Full detail plus lifecycle history for one booking."""
    booking_id = args.get("booking_id")
    if not booking_id:
        return "booking_id is required."

    booking = await get_json(f"/bookings/{booking_id}", token)
    lines = [_booking_line(booking), ""]

    for label, key in (("Style", "style_preference"), ("Budget", "budget"), ("Timeline", "timeline")):
        if booking.get(key):
            lines.append(f"{label}: {booking[key]}")
    if booking.get("artist_notes"):
        lines.append(f"Artist notes: {booking['artist_notes']}")
    if booking.get("client_notes"):
        lines.append(f"Client notes: {booking['client_notes']}")
    lines.append(f"SMS consent on file: {'yes' if booking.get('sms_opt_in') else 'no'}")
    if booking.get("conversation_id"):
        lines.append(f"Thread: conversation_id {booking['conversation_id']}")

    try:
        history = await get_json(f"/bookings/{booking_id}/history", token)
    except UpstreamError:
        history = []
    if history:
        lines.append("")
        lines.append("Lifecycle:")
        for event in history[:15]:
            lines.append(f"  {event.get('created_at', '?')} · {event.get('event_type')} · {event.get('summary')}")
    return "\n".join(lines)


async def tool_list_threads(token: str | None, args: dict) -> str:
    """Recent Courier threads, unread first in the caller's scope."""
    limit = min(int(args.get("limit") or 20), 100)
    data = await get_json("/conversations/", token, {"limit": limit})
    convs = data.get("conversations", [])
    if args.get("unread_only"):
        convs = [c for c in convs if c.get("is_unread") or c.get("unread_count")]

    if not convs:
        return "No threads in scope."

    lines = [f"{len(convs)} thread(s) of {data.get('total', len(convs))} total.", ""]
    for conv in convs[:MAX_ROWS]:
        flag = "UNREAD" if (conv.get("is_unread") or conv.get("unread_count")) else "read"
        name = conv.get("name") or "1:1 thread"
        last = (conv.get("last_message") or {}).get("content") or ""
        channel = (conv.get("channel_context") or {}).get("channel") or "app"
        lines.append(f"[{flag}] {name} · via {channel} · updated {conv.get('updated_at', '?')}")
        if last:
            lines.append(f"    last: {last.strip()[:140]}")
        lines.append(f"    conversation_id: {conv.get('id')}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def tool_read_thread(token: str | None, args: dict) -> str:
    """Read one thread oldest-last so the newest message is at the bottom."""
    conversation_id = args.get("conversation_id")
    if not conversation_id:
        return "conversation_id is required."

    limit = min(int(args.get("limit") or 30), 200)
    data = await get_json(f"/conversations/{conversation_id}/messages", token, {"limit": limit})
    messages = list(reversed(data.get("messages", [])))
    if not messages:
        return "That thread has no messages."

    lines = [f"Thread {conversation_id} — {data.get('total', len(messages))} message(s), showing {len(messages)}.", ""]
    for msg in messages:
        who = msg.get("sender_name") or "client"
        lines.append(f"{msg.get('created_at', '?')} · {who}:")
        lines.append(f"  {(msg.get('content') or '').strip()}")
    lines.append("")
    lines.append("Before replying: confirm the latest message is genuinely unanswered and check current booked state.")
    return "\n".join(lines)


async def tool_draft_reply(token: str | None, args: dict) -> str:
    """Assemble thread context for a reply. Drafts only — never sends."""
    conversation_id = args.get("conversation_id")
    if not conversation_id:
        return "conversation_id is required."

    intent = (args.get("intent") or "").strip()
    transcript = await tool_read_thread(token, {"conversation_id": conversation_id, "limit": 20})
    return (
        f"{transcript}\n\n"
        f"--- DRAFTING CONTEXT ---\n"
        f"Reply intent: {intent or '(not specified — ask before drafting)'}\n\n"
        "House rules for this reply:\n"
        "  - Casual and direct. Not corporate.\n"
        "  - No em dashes or en dashes anywhere in the copy.\n"
        "  - Do not promise a date or price that is not already confirmed in the thread.\n"
        "  - Check the last inbound message is actually unanswered before drafting.\n\n"
        "This tool cannot send. Produce the draft, show it to the owner, and let them send it "
        "from Courier so consent and thread state are checked at delivery."
    )


async def tool_send_channel_message(token: str | None, args: dict) -> str:
    """Send an approved Courier reply through the canonical channel boundary."""
    conversation_id = str(args.get("conversation_id") or "").strip()
    content = str(args.get("content") or "").strip()
    channel = str(args.get("channel") or "courier").strip().lower()
    if not conversation_id:
        return "conversation_id is required."
    if not content:
        return "content is required."
    if channel not in {"courier", "sms", "whatsapp"}:
        return "channel must be courier, sms, or whatsapp."

    payload: dict[str, Any] = {
        "channel": channel,
        "content": content,
        "is_transactional": bool(args.get("is_transactional", True)),
    }
    if args.get("to"):
        payload["to"] = str(args["to"])
    receipt = await write_json(
        f"/conversations/{conversation_id}/channel-send",
        token,
        "POST",
        payload,
    )
    return json.dumps(
        {
            "status": "sent",
            "conversation_id": conversation_id,
            "channel": channel,
            "message_id": receipt.get("id"),
            "provider_receipt": json.loads(receipt.get("metadata_json") or "{}"),
        },
        ensure_ascii=False,
        indent=2,
    )


async def _booking_write(token: str | None, args: dict, action: str, payload: dict[str, Any]) -> str:
    booking_id = str(args.get("booking_id") or "").strip()
    if not booking_id:
        return "booking_id is required."
    # Client notices are a separate, explicit choice; an agent change defaults to silent.
    payload["notify_client"] = args.get("notify_client") is True
    booking = await write_json(f"/bookings/{booking_id}/{action}", token, "POST", payload)
    return json.dumps(
        {
            "status": booking.get("status"),
            "action": action,
            "booking_id": booking.get("id") or booking_id,
            "scheduled_date": booking.get("scheduled_date"),
            "scheduled_time": booking.get("scheduled_time"),
            "client_notified": payload["notify_client"],
        },
        ensure_ascii=False,
        indent=2,
    )


async def tool_reschedule_booking(token: str | None, args: dict) -> str:
    """Move a booking to a validated slot through the staff REST boundary."""
    payload: dict[str, Any] = {
        "scheduled_date": str(args.get("scheduled_date") or ""),
        "scheduled_time": str(args.get("scheduled_time") or ""),
    }
    for key in ("duration_minutes", "reason"):
        if args.get(key) is not None:
            payload[key] = args[key]
    return await _booking_write(token, args, "reschedule", payload)


async def tool_cancel_booking(token: str | None, args: dict) -> str:
    """Cancel a booking through the staff REST boundary, keeping its audit trail."""
    payload: dict[str, Any] = {"delete_calendar_event": args.get("delete_calendar_event") is not False}
    if args.get("reason") is not None:
        payload["reason"] = args["reason"]
    return await _booking_write(token, args, "cancel", payload)


async def tool_search_contacts(token: str | None, args: dict) -> str:
    """Find a client by name, email or phone. Requires assistant role or higher."""
    query = (args.get("query") or "").strip()
    if not query:
        return "query is required (name, email or phone fragment)."

    limit = min(int(args.get("limit") or 20), 100)
    data = await get_json("/contacts", token, {"search": query, "limit": limit})
    contacts = data.get("contacts", [])
    if not contacts:
        return f"No contacts matched '{query}'."

    lines = [f"{len(contacts)} of {data.get('total', len(contacts))} contact(s) matching '{query}':", ""]
    for contact in contacts[:MAX_ROWS]:
        detail = " · ".join(x for x in (contact.get("email"), contact.get("phone")) if x)
        lines.append(f"  {contact.get('name', 'unknown')}{f' — {detail}' if detail else ''}")
    return "\n".join(lines)


# Shop actions a person takes as themselves, so test agents (the matrix NPCs)
# and a signed-in client's own assistant use the web app's routes, not a copy.


def _receipt(**fields: Any) -> str:
    return json.dumps({k: v for k, v in fields.items() if v is not None}, ensure_ascii=False, indent=2)


async def tool_list_artists(token: str | None, args: dict) -> str:
    """The shop's artists with the ids booking and messaging need."""
    artists = await get_json("/artists/", token)
    if not artists:
        return "No artists are listed."
    lines = [f"{len(artists)} artist(s):", ""]
    for artist in artists[:MAX_ROWS]:
        lines.append(f"  {artist.get('name')} · slug {artist.get('slug')} · id {artist.get('id')}")
    return "\n".join(lines)


async def tool_list_flash(token: str | None, args: dict) -> str:
    """Published flash designs a client can book."""
    params = {"artist_id": args.get("artist_id"), "limit": min(int(args.get("limit") or 20), 50)}
    data = await get_json("/flash/store", token, params)
    designs = data.get("designs", [])
    if not designs:
        return "No published flash designs."
    lines = [f"{len(designs)} of {data.get('total', len(designs))} published flash design(s):", ""]
    for design in designs[:MAX_ROWS]:
        price = _fmt_money(design.get("price")) if design.get("price") is not None else "price on request"
        claimed = " · claimed" if design.get("claimed_by") else ""
        lines.append(f"  {design.get('name') or 'Untitled'} · {price} · id {design.get('id')}{claimed}")
    return "\n".join(lines)


async def tool_find_slots(token: str | None, args: dict) -> str:
    """Free booking slots for one artist, from the same route the public booking form uses."""
    slug = str(args.get("artist_slug") or "").strip()
    if not slug:
        return "artist_slug is required (see ryozan_list_artists)."
    params = {"start": args.get("start"), "end": args.get("end"), "duration_minutes": args.get("duration_minutes")}
    data = await get_json(f"/calendar/slots/{slug}", token, params)
    slots = data.get("slots", [])
    if not slots:
        return f"No free slots for {slug} between {args.get('start')} and {args.get('end')}."
    lines = [f"{len(slots)} free slot(s) for {slug}, {data.get('duration_minutes')} min sessions:", ""]
    for slot in slots[:MAX_ROWS]:
        lines.append(f"  {slot.get('label')} · start {slot.get('start')}")
    return "\n".join(lines)


async def tool_request_booking(token: str | None, args: dict) -> str:
    """Send a booking request to an artist as the signed-in client."""
    fields = (
        "artist_id",
        "description",
        "placement",
        "size_estimate",
        "style_preference",
        "preferred_date",
        "preferred_time",
        "client_notes",
    )
    payload = {key: args[key] for key in fields if args.get(key) is not None}
    booking = await write_json("/bookings/", token, "POST", payload)
    return _receipt(
        status=booking.get("status"),
        booking_id=booking.get("id"),
        reference_code=booking.get("reference_code"),
        preferred_date=booking.get("preferred_date"),
    )


async def tool_book_flash(token: str | None, args: dict) -> str:
    """Book a flash design for the signed-in client, never on someone else's name."""
    me = await get_json("/auth/me", token)
    payload = {
        "design_id": str(args.get("design_id") or ""),
        "scheduled_date": str(args.get("scheduled_date") or ""),
        "scheduled_time": str(args.get("scheduled_time") or ""),
        "guest_name": me.get("name") or me.get("email"),
        "guest_email": me.get("email"),
        "pay_full": False,
    }
    booked = await write_json("/flash/book", token, "POST", payload)
    return _receipt(
        booking_reference=booked.get("booking_reference"),
        design_name=booked.get("design_name"),
        scheduled_date=booked.get("scheduled_date"),
        scheduled_time=booked.get("scheduled_time"),
        checkout_required=booked.get("checkout_required"),
        checkout_url=booked.get("checkout_url"),
        amount_cents=booked.get("amount_cents"),
    )


async def tool_start_conversation(token: str | None, args: dict) -> str:
    """Open an in-app thread with other shop members and post its first message."""
    participant_ids = [str(value) for value in (args.get("participant_ids") or []) if value]
    content = str(args.get("content") or "").strip()
    if not participant_ids:
        return "participant_ids is required."
    if not content:
        return "content is required."
    body: dict[str, Any] = {"participant_ids": participant_ids, "is_group": len(participant_ids) > 1}
    if args.get("name"):
        body["name"] = str(args["name"])
    conversation = await write_json("/conversations/", token, "POST", body)
    conversation_id = conversation.get("id")
    message = await write_json(f"/conversations/{conversation_id}/messages", token, "POST", {"content": content})
    return _receipt(conversation_id=conversation_id, message_id=message.get("id"))


async def tool_post_message(token: str | None, args: dict) -> str:
    """Post an in-app message in a thread the caller belongs to. Sends no email or SMS."""
    conversation_id = str(args.get("conversation_id") or "").strip()
    content = str(args.get("content") or "").strip()
    if not conversation_id:
        return "conversation_id is required."
    if not content:
        return "content is required."
    message = await write_json(f"/conversations/{conversation_id}/messages", token, "POST", {"content": content})
    return _receipt(conversation_id=conversation_id, message_id=message.get("id"))


async def tool_block_time(token: str | None, args: dict) -> str:
    """Block personal time on an artist's calendar, such as approved time off."""
    fields = ("artist_id", "block_date", "all_day", "start_time", "end_time", "reason")
    payload = {key: args[key] for key in fields if args.get(key) is not None}
    block = await write_json("/calendar/time-blocks", token, "POST", payload)
    return _receipt(
        time_block_id=block.get("id"),
        artist_id=block.get("artist_id"),
        block_date=block.get("block_date"),
        all_day=block.get("all_day"),
        start_time=block.get("start_time"),
        end_time=block.get("end_time"),
        reason=block.get("reason"),
    )


async def tool_approve_booking(token: str | None, args: dict) -> str:
    """Approve a booking request with one time and a deposit, through the staff approval route."""
    booking_id = str(args.get("booking_id") or "").strip()
    if not booking_id:
        return "booking_id is required."
    fields = ("scheduled_date", "scheduled_time", "duration_minutes", "deposit_amount", "artist_message")
    payload = {key: args[key] for key in fields if args.get(key) is not None}
    result = await write_json(f"/bookings/{booking_id}/approve", token, "POST", payload)
    return _receipt(
        booking_id=(result.get("booking") or {}).get("id") or booking_id,
        status=result.get("status"),
        scheduled_date=result.get("scheduled_date"),
        scheduled_time=result.get("scheduled_time"),
        deposit_amount=result.get("deposit_amount"),
        deposit_link_sent=result.get("deposit_link_sent"),
    )


async def tool_update_flash(token: str | None, args: dict) -> str:
    """Change a flash design's name, price, deposit or status, for example to run a promo."""
    design_id = str(args.get("design_id") or "").strip()
    if not design_id:
        return "design_id is required."
    payload = {key: args[key] for key in ("name", "price", "deposit_amount", "status") if args.get(key) is not None}
    if not payload:
        return "Nothing to change: pass name, price, deposit_amount or status."
    design = await write_json(f"/flash/designs/{design_id}", token, "PATCH", payload)
    return _receipt(
        design_id=design.get("id") or design_id,
        name=design.get("name"),
        price=design.get("price"),
        deposit_amount=design.get("deposit_amount"),
        status=design.get("status"),
    )


async def _resolve_workspace_slug(token: str | None, args: dict) -> str | None:
    requested = (args.get("slug") or "").strip()
    if requested:
        return requested
    workspaces = await get_json("/workspaces", token)
    if any(row.get("slug") == DEFAULT_DEV_WORKSPACE_SLUG for row in workspaces):
        return DEFAULT_DEV_WORKSPACE_SLUG
    if workspaces:
        return str(workspaces[0].get("slug") or DEFAULT_DEV_WORKSPACE_SLUG)
    return None


async def tool_dev_workspaces(token: str | None, args: dict) -> str:
    """List available Ryozan dev tracker workspaces."""
    rows = await get_json("/workspaces", token)
    if not rows:
        return (
            "No Ryozan dev workspaces exist in the connected database. The MCP is healthy, "
            "but the workspace tracker needs a workspace row before status, todos, log or "
            "feature-matrix tools have anything to read."
        )
    lines = [f"{len(rows)} Ryozan dev workspace(s):", ""]
    for row in rows[:MAX_ROWS]:
        state = "archived" if row.get("archived") else "active"
        lines.append(f"{row.get('slug')} · {row.get('name')} · {state}")
        if row.get("description"):
            lines.append(f"  {str(row['description'])[:180]}")
    return "\n".join(lines)


async def tool_dev_workspace_status(token: str | None, args: dict) -> str:
    """Read the Ryozan dev tracker workspace status."""
    slug = await _resolve_workspace_slug(token, args)
    if not slug:
        return await tool_dev_workspaces(token, args)
    data = await get_json(f"/workspaces/{slug}/status", token)
    workspace = data.get("workspace") or {}
    lines = [
        f"Ryozan dev workspace: {workspace.get('name') or slug}",
        "",
        f"Open todos: {data.get('open_todos', 0)}",
        f"Done todos: {data.get('done_todos', 0)}",
        f"Notes: {data.get('note_count', 0)}",
        f"Log entries: {data.get('log_count', 0)}",
    ]
    if data.get("feature_counts"):
        counts = ", ".join(f"{key}={value}" for key, value in sorted(data["feature_counts"].items()))
        lines.append(f"Feature states: {counts}")
    if data.get("verdict_counts"):
        counts = ", ".join(f"{key}={value}" for key, value in sorted(data["verdict_counts"].items()))
        lines.append(f"Verdicts: {counts}")
    recent = data.get("recent_log") or []
    if recent:
        lines.extend(["", "Recent log:"])
        for entry in recent[:5]:
            lines.append(
                f"  {entry.get('created_at', '?')} · {entry.get('author', 'unknown')}: {entry.get('entry', '')[:180]}"
            )
    return "\n".join(lines)


async def tool_dev_feature_matrix(token: str | None, args: dict) -> str:
    """Read the Ryozan dev feature matrix."""
    slug = await _resolve_workspace_slug(token, args)
    if not slug:
        return await tool_dev_workspaces(token, args)
    query = (args.get("query") or "").strip().lower()
    verdict = (args.get("verdict") or "").strip().lower()
    status = (args.get("status") or "").strip().lower()
    limit = min(int(args.get("limit") or 30), 100)
    rows = await get_json(f"/workspaces/{slug}/features", token)

    if query:
        rows = [
            row
            for row in rows
            if query in str(row.get("domain") or "").lower()
            or query in str(row.get("notes") or "").lower()
            or query in str(row.get("blockers") or "").lower()
        ]
    if verdict:
        rows = [row for row in rows if str(row.get("verdict") or "").lower() == verdict]
    if status:
        rows = [row for row in rows if str(row.get("status") or "").lower() == status]

    lines = [f"{len(rows)} Ryozan feature row(s) matched in workspace '{slug}'.", ""]
    if not rows:
        return "\n".join(lines).rstrip()
    for row in rows[:limit]:
        label = row.get("domain") or "unnamed feature"
        state = row.get("status") or "unknown"
        row_verdict = row.get("verdict") or "no verdict"
        lines.append(f"[{row_verdict}] {label} · {state}")
        if row.get("db"):
            lines.append(f"  db: {row['db']}")
        if row.get("api"):
            lines.append(f"  api: {row['api']}")
        if row.get("ui"):
            lines.append(f"  ui: {row['ui']}")
        if row.get("blockers"):
            lines.append(f"  blockers: {str(row['blockers'])[:220]}")
        if row.get("notes"):
            lines.append(f"  notes: {str(row['notes'])[:220]}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def tool_dev_todos(token: str | None, args: dict) -> str:
    """Read Ryozan dev tracker todos."""
    slug = await _resolve_workspace_slug(token, args)
    if not slug:
        return await tool_dev_workspaces(token, args)
    status = (args.get("status") or "open").strip()
    limit = min(int(args.get("limit") or 30), 100)
    rows = await get_json(f"/workspaces/{slug}/todos", token, {"status": status} if status else None)
    lines = [
        f"{len(rows)} Ryozan dev todo(s) in workspace '{slug}'" + (f" with status {status}." if status else "."),
        "",
    ]
    if not rows:
        return "\n".join(lines).rstrip()
    for todo in rows[:limit]:
        assignee = todo.get("assignee") or "unassigned"
        lines.append(f"[{todo.get('status', '?')}] {todo.get('title', 'untitled')} · {assignee}")
        lines.append(
            f"  id: {todo.get('id')} · author: {todo.get('author', 'unknown')} · created: {todo.get('created_at', '?')}"
        )
    return "\n".join(lines)


async def tool_dev_recent_log(token: str | None, args: dict) -> str:
    """Read recent Ryozan dev tracker log entries."""
    slug = await _resolve_workspace_slug(token, args)
    if not slug:
        return await tool_dev_workspaces(token, args)
    limit = min(int(args.get("limit") or 25), 100)
    rows = await get_json(f"/workspaces/{slug}/log", token, {"limit": limit})
    lines = [f"{len(rows)} recent Ryozan dev log entries from workspace '{slug}'.", ""]
    if not rows:
        return "\n".join(lines).rstrip()
    for entry in rows[:limit]:
        lines.append(f"{entry.get('created_at', '?')} · {entry.get('author', 'unknown')}:")
        lines.append(f"  {str(entry.get('entry') or '').strip()[:260]}")
    return "\n".join(lines)


async def tool_dev_scope_manifest(token: str | None, args: dict) -> str:
    """Return the complete owner development scope and Axiom target contract."""
    actor = await get_json("/auth/me", token)
    if actor.get("role") not in {"admin", "founder"}:
        raise UpstreamError("Ryozan development scopes require admin or founder access.")
    return "\n".join(
        [
            "Ryozan owner development scopes:",
            *[f"  {scope}" for scope in RYOZAN_DEV_SCOPES],
            "",
            "Source repository: GalaAI/ryozan",
            "Google OAuth: one web client, GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in api/.env, "
            "handles Calendar authorization and browser sign-in. VITE_GOOGLE_CLIENT_ID must match "
            "the API audience.",
            "",
            "This MCP server owns Ryozan coordination reads and writes. Connected Codex, Claude and "
            "Gemini clients use these scopes with their own source, shell, build, test and Git tools.",
        ]
    )


async def tool_dev_create_todo(token: str | None, args: dict) -> str:
    slug = await _resolve_workspace_slug(token, args)
    if not slug:
        raise UpstreamError("No Ryozan dev workspace exists.")
    title = str(args.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    row = await write_json(
        f"/workspaces/{slug}/todos",
        token,
        "POST",
        {
            "title": title,
            "assignee": str(args["assignee"]).strip() if args.get("assignee") else None,
            "author": str(args.get("author") or "unknown"),
            "source": "mcp",
        },
    )
    return f"Created Ryozan dev todo {row.get('id')} in '{slug}': {row.get('title')}"


async def tool_dev_update_todo(token: str | None, args: dict) -> str:
    slug = await _resolve_workspace_slug(token, args)
    if not slug:
        raise UpstreamError("No Ryozan dev workspace exists.")
    todo_id = str(args.get("todo_id") or "").strip()
    if not todo_id:
        raise ValueError("todo_id is required")
    payload = {key: args[key] for key in ("title", "assignee", "status") if key in args}
    if not payload:
        raise ValueError("one of title, assignee or status is required")
    row = await write_json(f"/workspaces/{slug}/todos/{todo_id}", token, "PATCH", payload)
    return f"Updated Ryozan dev todo {row.get('id')} in '{slug}' to {row.get('status')}."


async def tool_dev_append_log(token: str | None, args: dict) -> str:
    slug = await _resolve_workspace_slug(token, args)
    if not slug:
        raise UpstreamError("No Ryozan dev workspace exists.")
    entry = str(args.get("entry") or "").strip()
    if not entry:
        raise ValueError("entry is required")
    row = await write_json(
        f"/workspaces/{slug}/log",
        token,
        "POST",
        {
            "entry": entry,
            "author": str(args.get("author") or "unknown"),
            "source": "mcp",
        },
    )
    return f"Appended Ryozan dev log entry {row.get('id')} in '{slug}'."


async def tool_dev_upsert_feature(token: str | None, args: dict) -> str:
    slug = await _resolve_workspace_slug(token, args)
    if not slug:
        raise UpstreamError("No Ryozan dev workspace exists.")
    domain = str(args.get("domain") or "").strip()
    if not domain:
        raise ValueError("domain is required")
    payload = {
        key: args[key]
        for key in ("domain", "live_rows", "db_tables", "api", "ui", "status", "verdict", "notes")
        if key in args
    }
    payload["domain"] = domain
    row = await write_json(f"/workspaces/{slug}/features", token, "PUT", payload)
    return (
        f"Upserted Ryozan feature '{row.get('domain')}' in '{slug}': "
        f"status={row.get('status')}, verdict={row.get('verdict') or 'none'}."
    )


async def tool_triage_booking_with_calendar(token: str | None, args: dict) -> str:
    """Combine one booking intake, conversation state, and live consultation slots."""
    booking_id = str(args.get("booking_id") or "").strip()
    artist_slug = str(args.get("artist_slug") or "").strip()
    start = str(args.get("start") or "").strip()
    end = str(args.get("end") or "").strip()
    if not booking_id or not artist_slug or not start or not end:
        raise ValueError("booking_id, artist_slug, start and end are required")

    booking = await get_json(f"/bookings/{booking_id}", token)
    slot_data = await get_json(
        f"/calendar/slots/{artist_slug}",
        token,
        {
            "start": start,
            "end": end,
            "consultation": "true",
            "include_blocked": "false",
            "limit": min(int(args.get("limit") or 8), 20),
        },
    )

    lines = [
        f"Booking triage for {booking.get('resolved_name') or booking.get('client_name') or 'unknown client'}",
        f"status: {booking.get('status') or 'unknown'}",
        f"request: {(booking.get('description') or 'not provided').strip()}",
        f"placement: {booking.get('placement') or 'not provided'}",
        f"reference: {booking.get('reference_code') or 'none'}",
        f"calendar connected: {'yes' if slot_data.get('calendar_connected') else 'no'}",
    ]
    if booking.get("resolved_email"):
        lines.append(f"email: {booking['resolved_email']}")
    if booking.get("resolved_phone"):
        lines.append(f"phone: {booking['resolved_phone']}")

    conversation_id = booking.get("conversation_id")
    if conversation_id:
        try:
            transcript = await tool_read_thread(token, {"conversation_id": conversation_id, "limit": 10})
            lines.extend(["", "Recent Courier context:", transcript])
        except UpstreamError as exc:
            lines.extend(["", f"Courier context unavailable: {exc}"])

    slots = (slot_data.get("slots") or []) if slot_data.get("calendar_connected") else []
    lines.extend(["", f"Next verified consultation options ({len(slots)}):"])
    for slot in slots:
        lines.append(f"  {slot.get('label')} · {slot.get('start')}")
    if not slot_data.get("calendar_connected"):
        lines.append("  unavailable because Google Calendar could not be verified")
    elif not slots:
        lines.append("  no conflict-free slots in this range")

    lines.extend(
        [
            "",
            "This is review-only triage. Do not message or create an event until the owner approves.",
            "Booking creation and client invitations remain separate operator-approved actions.",
        ]
    )
    return "\n".join(lines)


async def tool_consultation_availability(token: str | None, args: dict) -> str:
    """Return live 30-minute consultation slots after Google and Ryozan conflict checks."""
    artist_slug = str(args.get("artist_slug") or "").strip()
    start = str(args.get("start") or "").strip()
    end = str(args.get("end") or "").strip()
    if not artist_slug or not start or not end:
        raise ValueError("artist_slug, start and end are required")
    data = await get_json(
        f"/calendar/slots/{artist_slug}",
        token,
        {
            "start": start,
            "end": end,
            "consultation": "true",
            "include_blocked": "true",
            "limit": min(int(args.get("limit") or 60), 240),
        },
    )
    if not data.get("calendar_connected"):
        return (
            f"Live consultation availability is unavailable for {artist_slug}: "
            "Google Calendar is not connected or could not be checked. Do not offer fallback times."
        )
    slots = data.get("slots") or []
    lines = [
        f"{len(slots)} live consultation slot(s) for {artist_slug}.",
        f"Hours: {describe_hours(get_settings().booking_consultation_schedule)}, {shop_timezone().key} time.",
        f"Google Calendar connected: {'yes' if data.get('calendar_connected') else 'no'}",
        "",
    ]
    for slot in slots:
        lines.append(f"{slot.get('label')} · {slot.get('start')} · {slot.get('confidence')}")
    if not slots:
        lines.append("No conflict-free slots were found in that range.")
    return "\n".join(lines)


async def tool_messaging_manifest(token: str | None, args: dict) -> str:
    """Return the structured Email Foundry and legacy-send reconciliation."""
    days = min(max(int(args.get("days") or 90), 1), 365)
    data = await get_json("/email/manifest", token, {"days": days})
    return json.dumps(data, ensure_ascii=False, indent=2)


async def tool_foundry_state(token: str | None, args: dict) -> str:
    """Hydrate the locked Foundry adapter through the signed-in founder."""
    data = await get_json("/email-templates/foundry/state", token)
    return json.dumps(data, ensure_ascii=False)


async def tool_foundry_put_state(token: str | None, args: dict) -> str:
    """Persist one locked store key and return any resulting send receipt."""
    key = str(args.get("key") or "").strip()
    if not key:
        raise ValueError("key is required")
    if "value" not in args:
        raise ValueError("value is required")
    data = await write_json(f"/email-templates/foundry/state/{key}", token, "PUT", args["value"])
    return json.dumps(data, ensure_ascii=False)


async def tool_foundry_approve_outbox(token: str | None, args: dict) -> str:
    """Approve exactly one held list send through the founder REST boundary."""
    entry_id = str(args.get("entry_id") or "").strip()
    if not entry_id:
        raise ValueError("entry_id is required")
    data = await write_json(f"/email-templates/foundry/outbox/{entry_id}/approve", token, "POST", {})
    return json.dumps(data, ensure_ascii=False)


async def tool_foundry_assist(token: str | None, args: dict) -> str:
    """Run the locked Herald editor control through Ryozan's approved model gateway."""
    data = await write_json("/email-templates/foundry/assist", token, "POST", args)
    return json.dumps(data, ensure_ascii=False)


async def tool_foundry_connectors(token: str | None, args: dict) -> str:
    """Project Ryozan-owned provider bindings without returning credential values."""
    data = await get_json("/email-templates/foundry/connectors", token)
    return json.dumps(data, ensure_ascii=False)


async def tool_foundry_connector_action(token: str | None, args: dict) -> str:
    """Apply one founder-scoped Courier binding action and return its live probe receipt."""
    connector_id = str(args.get("connector_id") or "").strip()
    action = str(args.get("action") or "").strip()
    if not connector_id or not action:
        raise ValueError("connector_id and action are required")
    data = await write_json(f"/email-templates/foundry/connectors/{connector_id}/{action}", token, "POST", {})
    return json.dumps(data, ensure_ascii=False)


async def tool_service_health(token: str | None, args: dict) -> str:
    """Deploy readiness and the commit currently serving. No auth needed."""
    lines = ["Ryozan service health", ""]
    for label, path in (("readiness", "/health/ready"), ("deploy", "/health/deploy")):
        try:
            payload = await get_health(path)
        except UpstreamError as exc:
            lines.append(f"{label}: UNREACHABLE — {exc}")
            continue
        if label == "readiness":
            lines.append(f"readiness: {payload.get('status')} (version {payload.get('version')})")
            failures = payload.get("failures") or []
            if failures:
                lines.append(f"  failing checks: {', '.join(failures)}")
        else:
            lines.append(f"deploy: branch {payload.get('branch', '?')} commit {payload.get('commit', '?')}")
    return "\n".join(lines)


_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
_EXTERNAL_SEND = {**_WRITE, "openWorldHint": True}
_UPSERT = {**_WRITE, "idempotentHint": True}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "ryozan_today",
        "title": "Ryozan today at a glance",
        "description": (
            "Returns the Ryozan shop's current-day picture for the signed-in artist: today's booked "
            "sessions with client names, unread message count, Stripe balance and next payout. Use "
            "this first when the owner asks what is happening today, what needs attention, or for a "
            "morning catch-up on the shop."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_today,
    },
    {
        "name": "ryozan_search_bookings",
        "title": "Search Ryozan bookings",
        "description": (
            "Searches the Ryozan booking pipeline. Filter by status (pending, confirmed, completed, "
            "cancelled) and optionally by a free-text query matched against client name, tattoo "
            "description and reference code. Results are scoped to the caller's role automatically. "
            "Use when asked about upcoming sessions, deposits owed, pending requests, or a specific "
            "client's booking."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Booking status filter, e.g. pending, confirmed, completed, cancelled.",
                },
                "query": {"type": "string", "description": "Free-text filter on client name, description or ref code."},
                "whole_shop": {
                    "type": "boolean",
                    "description": "Admin and founder only: widen scope from own bookings to the entire shop.",
                },
                "limit": {"type": "integer", "description": "Max rows to fetch, 1-100. Default 20."},
                "skip": {"type": "integer", "description": "Offset for paging. Default 0."},
            },
            "additionalProperties": False,
        },
        "handler": tool_search_bookings,
    },
]

TOOL_SPECS += [
    {
        "name": "ryozan_get_booking",
        "title": "Get one Ryozan booking",
        "description": (
            "Returns full detail for a single booking including placement, size, budget, timeline, "
            "artist and client notes, deposit state, SMS consent status and the append-only "
            "lifecycle history. Requires a booking_id from ryozan_search_bookings or ryozan_today."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {"booking_id": {"type": "string", "description": "Booking UUID."}},
            "required": ["booking_id"],
            "additionalProperties": False,
        },
        "handler": tool_get_booking,
    },
    {
        "name": "ryozan_list_threads",
        "title": "List Ryozan Courier threads",
        "description": (
            "Lists recent client message threads from Courier with unread flags, channel (SMS, "
            "email, app), last message preview and conversation_id. Use when asked who is waiting "
            "on a reply, what came in, or to find a thread before reading it."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "unread_only": {"type": "boolean", "description": "Only return threads with unread messages."},
                "limit": {"type": "integer", "description": "Max threads to fetch, 1-100. Default 20."},
            },
            "additionalProperties": False,
        },
        "handler": tool_list_threads,
    },
    {
        "name": "ryozan_read_thread",
        "title": "Read a Ryozan thread",
        "description": (
            "Reads the message history of one Courier conversation in chronological order, newest "
            "last. Use before answering any question about what a client said or asked, and always "
            "before drafting a reply."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Conversation UUID."},
                "limit": {"type": "integer", "description": "Max messages, 1-200. Default 30."},
            },
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
        "handler": tool_read_thread,
    },
]

TOOL_SPECS += [
    {
        "name": "ryozan_dev_scope_manifest",
        "title": "Ryozan owner development scopes",
        "description": (
            "Returns the complete Ryozan owner development scope manifest plus the Axiom entity, "
            "vessel and Google Cloud project binding. Use before source, build, test, Git, migration, "
            "deployment or cloud work to identify the exact execution and receipt owners."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_dev_scope_manifest,
    },
    {
        "name": "ryozan_dev_create_todo",
        "title": "Create a Ryozan dev todo",
        "description": (
            "Creates a development todo in the canonical Ryozan workspace through the existing "
            "admin or founder REST write boundary. Use to claim or hand off any source, build, "
            "test, Git, migration, deployment, cloud or architecture task."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "title": {"type": "string", "minLength": 1, "maxLength": 280},
                "assignee": {"type": "string"},
                "author": {"type": "string"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        "handler": tool_dev_create_todo,
    },
    {
        "name": "ryozan_dev_update_todo",
        "title": "Update a Ryozan dev todo",
        "description": (
            "Updates the title, assignee or open/done state of an existing Ryozan development todo "
            "through the canonical workspace API. Use when a connected development client claims, "
            "completes, reopens or hands off work."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "todo_id": {"type": "string"},
                "title": {"type": "string", "minLength": 1, "maxLength": 280},
                "assignee": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "done"]},
            },
            "required": ["todo_id"],
            "additionalProperties": False,
        },
        "handler": tool_dev_update_todo,
    },
    {
        "name": "ryozan_dev_append_log",
        "title": "Append Ryozan development evidence",
        "description": (
            "Appends a source, command, test, build, migration, deployment, cloud or handoff evidence "
            "entry to the canonical Ryozan development log. This is the MCP write path clients use "
            "to leave durable coordination evidence after native development work."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "entry": {"type": "string", "minLength": 1},
                "author": {"type": "string"},
            },
            "required": ["entry"],
            "additionalProperties": False,
        },
        "handler": tool_dev_append_log,
    },
    {
        "name": "ryozan_dev_upsert_feature",
        "title": "Upsert a Ryozan feature row",
        "description": (
            "Creates or updates one canonical Ryozan feature-matrix row including database, API, UI, "
            "status, verdict, blocker and evidence notes. Use after an audit or implementation pass "
            "so every connected client sees the same current development truth."
        ),
        "annotations": {**_UPSERT, "openWorldHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "domain": {"type": "string", "minLength": 1, "maxLength": 120},
                "live_rows": {"type": "integer", "minimum": 0},
                "db_tables": {"type": "array", "items": {"type": "string"}},
                "api": {"type": "string"},
                "ui": {"type": "string"},
                "status": {"type": "string"},
                "verdict": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["domain"],
            "additionalProperties": False,
        },
        "handler": tool_dev_upsert_feature,
    },
]

TOOL_SPECS += [
    {
        "name": "ryozan_draft_reply",
        "title": "Prepare a Ryozan client reply draft",
        "description": (
            "Pulls a thread's recent history plus Ryozan house copy rules so a reply can be drafted "
            "for review. This tool never sends anything. Use when the owner asks for help answering a "
            "client. The finished draft must be shown to him and sent from Courier."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Conversation UUID to reply within."},
                "intent": {"type": "string", "description": "What the reply should accomplish, in plain words."},
            },
            "required": ["conversation_id"],
            "additionalProperties": False,
        },
        "handler": tool_draft_reply,
    },
    {
        "name": "ryozan_send_channel_message",
        "title": "Send an approved Ryozan Courier reply",
        "description": (
            "Sends one approved reply through an existing Ryozan Courier thread using Courier, "
            "Twilio SMS, or Twilio WhatsApp. This is a real external action. It reuses the REST "
            "participant, staff-role, consent, opt-out, provider delivery, and receipt gates. "
            "Use only after reading the latest thread and when the operator has authorized sending."
        ),
        "annotations": _EXTERNAL_SEND,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "Existing Courier conversation UUID."},
                "channel": {"type": "string", "enum": ["courier", "sms", "whatsapp"]},
                "content": {"type": "string", "minLength": 1, "maxLength": 1600},
                "to": {"type": "string", "description": "Optional recipient override validated by Courier."},
                "is_transactional": {"type": "boolean", "default": True},
            },
            "required": ["conversation_id", "channel", "content"],
            "additionalProperties": False,
        },
        "handler": tool_send_channel_message,
    },
    {
        "name": "ryozan_reschedule_booking",
        "title": "Reschedule a Ryozan booking",
        "description": (
            "Moves one booking to a new slot through the staff reschedule route, which checks shop "
            "hours, buffers, holds, other bookings and Google Calendar busy blocks. Only the assigned "
            "artist or shop management may change it. The client is not notified unless "
            "notify_client is true. Cannot move money."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Booking UUID from booking search."},
                "scheduled_date": {"type": "string", "description": "YYYY-MM-DD."},
                "scheduled_time": {"type": "string", "description": "HH:MM, shop local time."},
                "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 720},
                "reason": {"type": "string", "maxLength": 500},
                "notify_client": {"type": "boolean", "default": False},
            },
            "required": ["booking_id", "scheduled_date", "scheduled_time"],
            "additionalProperties": False,
        },
        "handler": tool_reschedule_booking,
    },
    {
        "name": "ryozan_cancel_booking",
        "title": "Cancel a Ryozan booking",
        "description": (
            "Cancels one booking through the staff cancel route with an auditable reason, releases "
            "any held card authorization the route already releases, and removes its calendar event "
            "unless delete_calendar_event is false. Only the assigned artist or shop management may "
            "cancel. The client is not notified unless notify_client is true. Issues no refund."
        ),
        "annotations": {**_WRITE, "destructiveHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Booking UUID from booking search."},
                "reason": {"type": "string", "maxLength": 500},
                "notify_client": {"type": "boolean", "default": False},
                "delete_calendar_event": {"type": "boolean", "default": True},
            },
            "required": ["booking_id"],
            "additionalProperties": False,
        },
        "handler": tool_cancel_booking,
    },
    {
        "name": "ryozan_search_contacts",
        "title": "Search Ryozan contacts",
        "description": (
            "Finds a Ryozan client by partial name, email or phone. Requires assistant role or "
            "higher. Use to resolve who someone is before looking up their bookings or threads."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name, email or phone fragment."},
                "limit": {"type": "integer", "description": "Max contacts, 1-100. Default 20."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "handler": tool_search_contacts,
    },
    {
        "name": "ryozan_list_artists",
        "title": "List Ryozan artists",
        "description": (
            "Lists the shop's artists with the slug and id that ryozan_request_booking, "
            "ryozan_start_conversation and ryozan_block_time take."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_list_artists,
    },
    {
        "name": "ryozan_list_flash",
        "title": "List bookable flash",
        "description": (
            "Lists published flash designs with price, id and whether one is already claimed. Use the id "
            "with ryozan_book_flash or ryozan_update_flash."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "artist_id": {"type": "string", "description": "Only this artist's designs."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
        "handler": tool_list_flash,
    },
    {
        "name": "ryozan_find_slots",
        "title": "Find free booking slots",
        "description": (
            "Lists one artist's free booking slots between two dates, using the shop's hours, buffers, "
            "minimum notice, time blocks and calendar busy times. Book one with ryozan_book_flash."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "artist_slug": {"type": "string", "description": "Artist slug from ryozan_list_artists."},
                "start": {"type": "string", "description": "YYYY-MM-DD."},
                "end": {"type": "string", "description": "YYYY-MM-DD."},
                "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 720},
            },
            "required": ["artist_slug", "start", "end"],
            "additionalProperties": False,
        },
        "handler": tool_find_slots,
    },
    {
        "name": "ryozan_request_booking",
        "title": "Request a booking as yourself",
        "description": (
            "Sends a booking request to one artist as the signed-in client, through the same route as "
            "the client portal. The artist approves a time and deposit later. Takes no payment."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "artist_id": {"type": "string", "description": "Artist id from ryozan_list_artists."},
                "description": {"type": "string", "minLength": 10, "maxLength": 5000},
                "placement": {"type": "string"},
                "size_estimate": {"type": "string"},
                "style_preference": {"type": "string"},
                "preferred_date": {"type": "string", "description": "YYYY-MM-DD."},
                "preferred_time": {"type": "string", "description": "HH:MM, shop local time."},
                "client_notes": {"type": "string"},
            },
            "required": ["artist_id", "description"],
            "additionalProperties": False,
        },
        "handler": tool_request_booking,
    },
    {
        "name": "ryozan_book_flash",
        "title": "Book a flash design as yourself",
        "description": (
            "Books one published flash design for the signed-in client at a free slot, always in the "
            "caller's own name and email. When the design needs a deposit the shop returns a Stripe "
            "checkout link; this tool never pays it."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "design_id": {"type": "string", "description": "Design id from ryozan_list_flash."},
                "scheduled_date": {"type": "string", "description": "YYYY-MM-DD."},
                "scheduled_time": {"type": "string", "description": "HH:MM, shop local time."},
            },
            "required": ["design_id", "scheduled_date", "scheduled_time"],
            "additionalProperties": False,
        },
        "handler": tool_book_flash,
    },
    {
        "name": "ryozan_start_conversation",
        "title": "Start an in-app thread",
        "description": (
            "Opens an in-app Courier thread with other shop members and posts its first message. "
            "In-app only: sends no email, SMS or WhatsApp."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "participant_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "content": {"type": "string", "minLength": 1, "maxLength": 10000},
                "name": {"type": "string", "description": "Thread name; set it for a group thread."},
            },
            "required": ["participant_ids", "content"],
            "additionalProperties": False,
        },
        "handler": tool_start_conversation,
    },
    {
        "name": "ryozan_post_message",
        "title": "Post in an in-app thread",
        "description": "Posts one in-app message in a thread the caller belongs to. Sends no email, SMS or WhatsApp.",
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "content": {"type": "string", "minLength": 1, "maxLength": 10000},
            },
            "required": ["conversation_id", "content"],
            "additionalProperties": False,
        },
        "handler": tool_post_message,
    },
    {
        "name": "ryozan_block_time",
        "title": "Block time on an artist's calendar",
        "description": (
            "Blocks personal time, such as approved time off, on the caller's calendar or, for shop "
            "management, on another artist's. Booking availability excludes it at once."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "artist_id": {"type": "string", "description": "Shop management only; defaults to the caller."},
                "block_date": {"type": "string", "description": "YYYY-MM-DD."},
                "all_day": {"type": "boolean", "default": False},
                "start_time": {"type": "string", "description": "HH:MM; required unless all_day."},
                "end_time": {"type": "string", "description": "HH:MM; required unless all_day."},
                "reason": {"type": "string", "maxLength": 255},
            },
            "required": ["block_date"],
            "additionalProperties": False,
        },
        "handler": tool_block_time,
    },
    {
        "name": "ryozan_approve_booking",
        "title": "Approve a booking request",
        "description": (
            "Approves one booking request with a time and deposit through the staff approval route, "
            "which checks hours, buffers and other bookings. A zero deposit confirms it; a positive one "
            "leaves it waiting on the deposit. Moves no money."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string"},
                "scheduled_date": {"type": "string", "description": "YYYY-MM-DD."},
                "scheduled_time": {"type": "string", "description": "HH:MM, shop local time."},
                "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 720},
                "deposit_amount": {"type": "number", "minimum": 0, "maximum": 5000},
                "artist_message": {"type": "string", "maxLength": 1000},
            },
            "required": ["booking_id", "scheduled_date", "scheduled_time", "deposit_amount"],
            "additionalProperties": False,
        },
        "handler": tool_approve_booking,
    },
    {
        "name": "ryozan_update_flash",
        "title": "Change a flash design",
        "description": (
            "Changes one flash design's name, price, deposit or status (draft, published, archived), "
            "for example to run a promo price. Only its artist or shop management may change it."
        ),
        "annotations": _UPSERT,
        "inputSchema": {
            "type": "object",
            "properties": {
                "design_id": {"type": "string"},
                "name": {"type": "string"},
                "price": {"type": "number", "minimum": 0},
                "deposit_amount": {"type": "number", "minimum": 0},
                "status": {"type": "string", "enum": ["draft", "published", "archived"]},
            },
            "required": ["design_id"],
            "additionalProperties": False,
        },
        "handler": tool_update_flash,
    },
    {
        "name": "ryozan_triage_booking_with_calendar",
        "title": "Triage a booking with live consultation times",
        "description": (
            "Combines one tattoo booking intake, resolved client contact, recent Courier context, "
            "and the next conflict-free consultation slots from Google Calendar and Ryozan. This is "
            "review-only and cannot message the client or create an event."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Booking UUID from booking search."},
                "artist_slug": {"type": "string"},
                "start": {"type": "string", "description": "Start date YYYY-MM-DD."},
                "end": {"type": "string", "description": "End date YYYY-MM-DD."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["booking_id", "artist_slug", "start", "end"],
            "additionalProperties": False,
        },
        "handler": tool_triage_booking_with_calendar,
    },
    {
        "name": "ryozan_consultation_availability",
        "title": "Find live Ryozan consultation times",
        "description": (
            "Returns conflict-free 30-minute consultation slots within the studio's consultation hours. "
            "Reads Google Calendar, native Ryozan bookings, active holds "
            "and personal blocks. Use immediately before offering times."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "artist_slug": {"type": "string"},
                "start": {"type": "string", "description": "Start date YYYY-MM-DD."},
                "end": {"type": "string", "description": "End date YYYY-MM-DD."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 240},
            },
            "required": ["artist_slug", "start", "end"],
            "additionalProperties": False,
        },
        "handler": tool_consultation_availability,
    },
    {
        "name": "ryozan_messaging_manifest",
        "title": "Inspect Ryozan email and messaging runtime",
        "description": (
            "Returns the founder-scoped Ryozan Email Foundry inventory, legacy renderer inventory, "
            "stable public brand assets, provider capability states, and receipt-backed live, "
            "legacy-live, available, not-live, or unknown classification. Use to reconcile which "
            "emails are actually sending before replacing or retiring any renderer."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 365,
                    "description": "Receipt evidence window in days. Default 90.",
                },
            },
            "additionalProperties": False,
        },
        "handler": tool_messaging_manifest,
    },
    {
        "name": "ryozan_foundry_state",
        "title": "Read Ryozan Email Foundry state",
        "description": (
            "Returns the founder-owned fields, schedules, custom templates, outbox rows, and "
            "authoritative delivery receipts used to hydrate the locked Email Foundry adapter."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_foundry_state,
    },
    # The site MCP previously exposed Foundry content and sends but had no provider-binding
    # contract. These two tools reuse the same founder OAuth boundary so Courier never receives
    # raw SendGrid, Resend, SMTP, Twilio, or R2 credentials.
    {
        "name": "ryozan_foundry_connectors",
        "title": "Read Ryozan Email Foundry connectors",
        "description": (
            "Returns configured, connected, and last-tested booleans for Ryozan-owned provider "
            "bindings without exposing credential values."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_foundry_connectors,
    },
    {
        "name": "ryozan_foundry_connector_action",
        "title": "Control a Ryozan Email Foundry connector",
        "description": (
            "Connects, reconnects, disconnects, or live-tests one Ryozan-owned provider binding "
            "and returns an attributed probe receipt. Disconnecting retains the tenant credential."
        ),
        "annotations": _UPSERT,
        "inputSchema": {
            "type": "object",
            "properties": {
                "connector_id": {
                    "type": "string",
                    "enum": ["sendgrid", "resend", "smtp", "twilio", "cloudflare-r2"],
                },
                "action": {
                    "type": "string",
                    "enum": ["connect", "reconnect", "disconnect", "test"],
                },
            },
            "required": ["connector_id", "action"],
            "additionalProperties": False,
        },
        "handler": tool_foundry_connector_action,
    },
    {
        "name": "ryozan_foundry_put_state",
        "title": "Persist Ryozan Email Foundry state",
        "description": (
            "Persists one exact locked Foundry store key. Outbox writes execute validated single-address "
            "tests through Ryozan's provider while manual list sends remain held for founder sign-off."
        ),
        "annotations": _EXTERNAL_SEND,
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": [
                        "bttco-foundry-fields-v1",
                        "bttco-foundry-schedule-v1",
                        "bttco-foundry-custom-v1",
                        "bttco-foundry-outbox-v1",
                    ],
                },
                "value": {},
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        "handler": tool_foundry_put_state,
    },
    {
        "name": "ryozan_foundry_approve_outbox",
        "title": "Approve a held Ryozan Foundry list send",
        "description": (
            "Applies the founder ruling to one held manual list send, limits recipients to Ryozan users "
            "with marketing consent, executes through Ryozan's provider, and returns the receipt."
        ),
        "annotations": _EXTERNAL_SEND,
        "inputSchema": {
            "type": "object",
            "properties": {"entry_id": {"type": "string", "minLength": 1}},
            "required": ["entry_id"],
            "additionalProperties": False,
        },
        "handler": tool_foundry_approve_outbox,
    },
    {
        "name": "ryozan_foundry_assist",
        "title": "Draft inside Ryozan Email Foundry",
        "description": (
            "Runs the locked Herald drafting control through Ryozan's approved Ark Model Gateway, "
            "returning draft-only JSON text to the founder-owned editor without sending an email."
        ),
        "annotations": _WRITE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "system": {"type": "string", "minLength": 1, "maxLength": 12000},
                "messages": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["user", "assistant"]},
                            "content": {"type": "string", "minLength": 1, "maxLength": 12000},
                        },
                        "required": ["role", "content"],
                        "additionalProperties": False,
                    },
                },
                "max_tokens": {"type": "integer", "minimum": 64, "maximum": 2500},
            },
            "required": ["system", "messages"],
            "additionalProperties": False,
        },
        "handler": tool_foundry_assist,
    },
    {
        "name": "ryozan_service_health",
        "title": "Ryozan service health",
        "description": (
            "Reports Ryozan API deploy readiness, failing startup checks, and the branch and commit "
            "currently serving production. Use when the site looks broken or to confirm what is "
            "actually deployed. Needs no authentication."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_service_health,
    },
]

TOOL_SPECS += [
    {
        "name": "ryozan_dev_workspaces",
        "title": "List Ryozan dev workspaces",
        "description": (
            "Lists Ryozan dev tracker workspaces available to the signed-in admin or founder. Use "
            "this first when the agent does not know which workspace slug to use, or when status, "
            "todo, log or feature matrix reads report that no workspace exists."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_dev_workspaces,
    },
    {
        "name": "ryozan_dev_workspace_status",
        "title": "Ryozan dev workspace status",
        "description": (
            "Reads the Ryozan dev tracker workspace status, including open todo count, done todo "
            "count, feature state counts, verdict counts and recent collaboration log entries. "
            "Use this before Ryozan development work so the agent sees the current coordination "
            "state instead of guessing from stale chat context."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Workspace slug. Defaults to dev-tracker when present, otherwise the first workspace.",
                },
            },
            "additionalProperties": False,
        },
        "handler": tool_dev_workspace_status,
    },
    {
        "name": "ryozan_dev_feature_matrix",
        "title": "Ryozan dev feature matrix",
        "description": (
            "Reads the Ryozan dev tracker feature matrix with DB, API, UI, blocker, note, status "
            "and verdict fields. Filter by query, verdict or status. Use when deciding what to "
            "build, wire, defer, or audit next in Ryozan development."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Workspace slug. Defaults to dev-tracker when present, otherwise the first workspace.",
                },
                "query": {"type": "string", "description": "Text filter across domain, notes and blockers."},
                "verdict": {"type": "string", "description": "Optional verdict filter, e.g. BUILD, WIRE, SEED, DEFER."},
                "status": {"type": "string", "description": "Optional implementation status filter."},
                "limit": {"type": "integer", "description": "Max rows to show, 1-100. Default 30."},
            },
            "additionalProperties": False,
        },
        "handler": tool_dev_feature_matrix,
    },
    {
        "name": "ryozan_dev_todos",
        "title": "Ryozan dev todos",
        "description": (
            "Reads Ryozan dev tracker todos for a workspace, open by default. Use when an agent "
            "needs the current implementation queue, blockers, assignees or handoff items before "
            "starting Ryozan source work."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Workspace slug. Defaults to dev-tracker when present, otherwise the first workspace.",
                },
                "status": {"type": "string", "description": "Todo status filter, usually open or done. Defaults open."},
                "limit": {"type": "integer", "description": "Max todos to show, 1-100. Default 30."},
            },
            "additionalProperties": False,
        },
        "handler": tool_dev_todos,
    },
    {
        "name": "ryozan_dev_recent_log",
        "title": "Ryozan dev recent log",
        "description": (
            "Reads the recent Ryozan dev tracker collaboration log for source-work handoff context. "
            "Use this to see what other agents or operators recently changed, decided, blocked or "
            "verified before editing the Ryozan repo."
        ),
        "annotations": _READ_ONLY,
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Workspace slug. Defaults to dev-tracker when present, otherwise the first workspace.",
                },
                "limit": {"type": "integer", "description": "Max log entries to show, 1-100. Default 25."},
            },
            "additionalProperties": False,
        },
        "handler": tool_dev_recent_log,
    },
]

TOOLS_BY_NAME = {spec["name"]: spec for spec in TOOL_SPECS}


_ROLE_RANK = {role: rank for rank, role in enumerate(("collector", "assistant", "artist", "admin", "founder"))}

# Discovery and execution use the same policy table. Ryozan shop tools remain
# distinct from development coordination tools routed through Axiom/Hivemind.
TOOL_ACCESS_POLICY: dict[str, tuple[str, str]] = {
    "ryozan_today": ("collector", "ryozan.shop.read"),
    "ryozan_search_bookings": ("collector", "ryozan.shop.read"),
    "ryozan_get_booking": ("collector", "ryozan.shop.read"),
    "ryozan_list_threads": ("collector", "ryozan.shop.read"),
    "ryozan_read_thread": ("collector", "ryozan.shop.read"),
    "ryozan_draft_reply": ("assistant", "ryozan.shop.read"),
    "ryozan_send_channel_message": ("admin", "ryozan.shop.send"),
    "ryozan_search_contacts": ("assistant", "ryozan.shop.read"),
    "ryozan_list_artists": ("collector", "ryozan.shop.read"),
    "ryozan_list_flash": ("collector", "ryozan.shop.read"),
    "ryozan_find_slots": ("collector", "ryozan.shop.read"),
    "ryozan_request_booking": ("collector", "ryozan.client.write"),
    "ryozan_book_flash": ("collector", "ryozan.client.write"),
    "ryozan_start_conversation": ("collector", "ryozan.client.write"),
    "ryozan_post_message": ("collector", "ryozan.client.write"),
    "ryozan_block_time": ("artist", "ryozan.shop.write"),
    "ryozan_approve_booking": ("assistant", "ryozan.shop.write"),
    "ryozan_update_flash": ("artist", "ryozan.shop.write"),
    "ryozan_reschedule_booking": ("assistant", "ryozan.shop.write"),
    "ryozan_cancel_booking": ("assistant", "ryozan.shop.write"),
    "ryozan_triage_booking_with_calendar": ("assistant", "ryozan.shop.read"),
    "ryozan_consultation_availability": ("assistant", "ryozan.shop.read"),
    "ryozan_messaging_manifest": ("founder", "ryozan.shop.read"),
    "ryozan_foundry_state": ("founder", "ryozan.shop.read"),
    "ryozan_foundry_connectors": ("founder", "ryozan.shop.read"),
    "ryozan_foundry_connector_action": ("founder", "ryozan.shop.send"),
    "ryozan_foundry_put_state": ("founder", "ryozan.shop.send"),
    "ryozan_foundry_approve_outbox": ("founder", "ryozan.shop.send"),
    "ryozan_foundry_assist": ("founder", "ryozan.shop.send"),
    "ryozan_service_health": ("collector", "ryozan.service.read"),
    "ryozan_dev_scope_manifest": ("admin", "ryozan.dev.read"),
    "ryozan_dev_workspaces": ("admin", "ryozan.dev.read"),
    "ryozan_dev_workspace_status": ("admin", "ryozan.dev.read"),
    "ryozan_dev_feature_matrix": ("admin", "ryozan.dev.read"),
    "ryozan_dev_todos": ("admin", "ryozan.dev.read"),
    "ryozan_dev_recent_log": ("admin", "ryozan.dev.read"),
    "ryozan_dev_create_todo": ("admin", "ryozan.dev.write"),
    "ryozan_dev_update_todo": ("admin", "ryozan.dev.write"),
    "ryozan_dev_append_log": ("admin", "ryozan.dev.write"),
    "ryozan_dev_upsert_feature": ("admin", "ryozan.dev.write"),
}


def tool_allowed(name: str, role: str, scopes: set[str]) -> bool:
    minimum_role, required_scope = TOOL_ACCESS_POLICY[name]
    return _ROLE_RANK.get(role, -1) >= _ROLE_RANK[minimum_role] and required_scope in scopes


def public_tool_list(role: str, scopes: set[str]) -> list[dict[str, Any]]:
    """Return only tools the authenticated role and OAuth grant can call."""
    return [
        {k: v for k, v in spec.items() if k != "handler"}
        for spec in TOOL_SPECS
        if tool_allowed(spec["name"], role, scopes)
    ]
