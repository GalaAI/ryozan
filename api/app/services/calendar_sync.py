"""
Google Calendar sync service for Ryozan bookings.

Creates, updates, and deletes calendar events when bookings change.
Also provides availability and upcoming-event queries for the dashboard
and booking form.

Uses google-auth + google-api-python-client. Production can use a delegated
Google Workspace service account while legacy per-user OAuth refresh tokens
remain available during migration.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from google.auth.credentials import Credentials as GoogleCredentials
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.constants.timezones import shop_timezone
from app.core.config import get_settings

logger = logging.getLogger("ryozan.calendar_sync")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
WORKSPACE_DELEGATED_MODE = "workspace_delegated"


# ── Credentials / Client ────────────────────────


def workspace_delegation_enabled() -> bool:
    settings = get_settings()
    return getattr(settings, "GOOGLE_CALENDAR_AUTH_MODE", "oauth") == WORKSPACE_DELEGATED_MODE


def calendar_connection_configured(refresh_token: str | None = None) -> bool:
    """Return whether the active Calendar authority has enough configuration."""
    settings = get_settings()
    if workspace_delegation_enabled():
        return bool(
            getattr(settings, "GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON", "").strip()
            and getattr(settings, "GOOGLE_CALENDAR_DELEGATED_USER", "").strip()
        )
    # Preserve the legacy gate semantics during cutover. The credential builder
    # still validates the OAuth client pair before making a Google request.
    return bool(refresh_token or settings.GOOGLE_REFRESH_TOKEN)


def artist_calendar_id(artist: Any) -> str:
    """Resolve the calendar operated for one artist without exposing it publicly."""
    if not workspace_delegation_enabled():
        return "primary"
    explicit_id = (getattr(artist, "google_calendar_id", None) or "").strip()
    if explicit_id:
        return explicit_id
    return (getattr(artist, "email", None) or "").strip()


def artist_calendar_connected(artist: Any) -> bool:
    """Return whether Ryozan can attempt Calendar operations for an artist."""
    if workspace_delegation_enabled():
        return bool(artist_calendar_id(artist) and calendar_connection_configured())
    return calendar_connection_configured(getattr(artist, "google_refresh_token", None))


def _default_calendar_id() -> str:
    settings = get_settings()
    configured = getattr(settings, "GOOGLE_CALENDAR_DEFAULT_ID", "").strip()
    if configured:
        return configured
    if workspace_delegation_enabled():
        return getattr(settings, "GOOGLE_CALENDAR_DELEGATED_USER", "").strip() or "primary"
    return "primary"


def _get_credentials(refresh_token: str | None = None) -> GoogleCredentials:
    """Build delegated Workspace credentials or legacy OAuth2 credentials."""
    settings = get_settings()
    if workspace_delegation_enabled():
        raw_info = getattr(settings, "GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON", "").strip()
        delegated_user = getattr(settings, "GOOGLE_CALENDAR_DELEGATED_USER", "").strip()
        if not raw_info or not delegated_user:
            raise RuntimeError(
                "Google Calendar not configured: missing delegated service account JSON or Workspace user"
            )
        try:
            service_account_info = json.loads(raw_info)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Google Calendar service account JSON is invalid") from exc
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=SCOPES,
        )
        return credentials.with_subject(delegated_user)

    token = refresh_token or settings.GOOGLE_REFRESH_TOKEN
    if not settings.GOOGLE_CLIENT_ID or not token:
        raise RuntimeError("Google Calendar not configured: missing GOOGLE_CLIENT_ID or refresh token")

    return Credentials(
        token=None,
        refresh_token=token,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def _get_calendar_service(refresh_token: str | None = None):
    """Return an authorized Google Calendar API v3 service."""
    creds = _get_credentials(refresh_token)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def check_calendar_connection(
    refresh_token: str | None = None,
    calendar_id: str | None = None,
) -> dict:
    """
    Validate that a stored refresh token can still access Calendar.

    Returns a small status dict instead of raising so dashboards can show a
    reconnect state before booking availability silently falls back.
    """
    settings = get_settings()
    resolved_calendar_id = calendar_id or _default_calendar_id()
    if settings.LOCAL_DEV_MODE:
        token_present = calendar_connection_configured(refresh_token)
        return {
            "healthy": token_present,
            "calendar_accessible": token_present,
            "calendar_id": resolved_calendar_id if token_present else None,
            "summary": "Local dev calendar" if token_present else None,
            "reason": None if token_present else "not_connected",
            "reconnect_required": False,
        }

    try:
        creds = _get_credentials(refresh_token)
        creds.refresh(GoogleAuthRequest())
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        calendar = service.calendars().get(calendarId=resolved_calendar_id).execute()
        return {
            "healthy": True,
            "calendar_accessible": True,
            "calendar_id": calendar.get("id", "primary"),
            "summary": calendar.get("summary"),
            "reason": None,
            "reconnect_required": False,
        }
    except RefreshError as exc:
        logger.warning("Google Calendar refresh failed: %s", exc)
        return {
            "healthy": False,
            "calendar_accessible": False,
            "calendar_id": None,
            "summary": None,
            "reason": "refresh_failed",
            "reconnect_required": True,
        }
    except HttpError as exc:
        logger.warning("Google Calendar health check failed: %s", exc)
        return {
            "healthy": False,
            "calendar_accessible": False,
            "calendar_id": None,
            "summary": None,
            "reason": f"google_http_{exc.resp.status}",
            "reconnect_required": exc.resp.status in {400, 401, 403},
        }
    except Exception as exc:
        logger.warning("Google Calendar health check unavailable: %s", exc)
        return {
            "healthy": False,
            "calendar_accessible": False,
            "calendar_id": None,
            "summary": None,
            "reason": "unavailable",
            "reconnect_required": False,
        }


# ── Helpers ──────────────────────────────────────


def _build_event_body(booking_data: dict) -> dict:
    """
    Convert booking data into a Google Calendar event resource.

    Expected keys in booking_data:
        client_name, description, placement, reference_code,
        scheduled_date (str YYYY-MM-DD), scheduled_time (str HH:MM),
        duration_minutes (int, default 120)
    """
    client_name = booking_data.get("client_name", "Client")
    description = booking_data.get("description", "")
    placement = booking_data.get("placement", "")
    reference_code = booking_data.get("reference_code", "")
    duration = booking_data.get("duration_minutes", 120)
    booking_kind = booking_data.get("booking_kind", "tattoo_session")
    consultation_method = booking_data.get("consultation_method")
    client_email = (booking_data.get("client_email") or "").strip()

    # Build description block
    desc_lines = []
    if description:
        label = "Consultation" if booking_kind == "consultation" else "Tattoo"
        desc_lines.append(f"{label}: {description}")
    if placement:
        desc_lines.append(f"Placement: {placement}")
    if reference_code:
        desc_lines.append(f"Ref: {reference_code}")
    event_description = "\n".join(desc_lines)

    # Parse date/time into RFC3339
    date_str = booking_data.get("scheduled_date")
    time_str = booking_data.get("scheduled_time", "12:00")

    if date_str:
        # Handle both date objects and strings
        if hasattr(date_str, "isoformat"):
            date_str = date_str.isoformat()
        if hasattr(time_str, "isoformat"):
            time_str = time_str.isoformat()[:5]

        start_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
        end_dt = start_dt + timedelta(minutes=duration)

        start = {"dateTime": start_dt.isoformat(), "timeZone": shop_timezone().key}
        end = {"dateTime": end_dt.isoformat(), "timeZone": shop_timezone().key}
    else:
        # No date yet, use an all-day placeholder for today
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        start = {"date": today}
        end = {"date": today}

    method_labels = {
        "whatsapp": "WhatsApp",
        "meet": "Google Meet",
        "in_person": "In person",
    }
    if booking_kind == "consultation" and consultation_method:
        desc_lines.append(f"Method: {method_labels.get(consultation_method, consultation_method)}")
        event_description = "\n".join(desc_lines)

    body = {
        "summary": (
            f"{client_name} - Consultation" if booking_kind == "consultation" else f"{client_name} - Tattoo Session"
        ),
        "description": event_description,
        "location": (
            "Google Meet"
            if consultation_method == "meet"
            else "WhatsApp"
            if consultation_method == "whatsapp"
            else (get_settings().STUDIO_ADDRESS.strip() or get_settings().STUDIO_NAME)
        ),
        "start": start,
        "end": end,
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60},
                {"method": "popup", "minutes": 1440},  # 24 hours
            ],
        },
    }
    event_id = (booking_data.get("event_id") or "").strip()
    if event_id:
        body["id"] = event_id
    if client_email:
        body["attendees"] = [{"email": client_email}]
    if booking_kind == "consultation" and consultation_method == "meet":
        request_id = (booking_data.get("conference_request_id") or event_id or reference_code).strip()
        if request_id:
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": request_id,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
    return body


# ── Public API ───────────────────────────────────


async def create_booking_event(
    booking_data: dict,
    calendar_id: str | None = None,
    refresh_token: str | None = None,
) -> dict:
    """
    Create a Google Calendar event from booking data.

    Returns dict with 'event_id' and 'html_link' on success.
    Raises on failure (caller should handle).
    """
    if get_settings().LOCAL_DEV_MODE:
        reference = booking_data.get("reference_code", "unknown")
        logger.info("[LOCAL_DEV_MODE] calendar create skipped for booking %s", reference)
        return {"event_id": f"dev-{reference}", "html_link": "", "conference_url": ""}

    try:
        service = _get_calendar_service(refresh_token)
        body = _build_event_body(booking_data)
        resolved_calendar_id = calendar_id or _default_calendar_id()

        insert_kwargs: dict[str, object] = {"calendarId": resolved_calendar_id, "body": body}
        if body.get("attendees"):
            insert_kwargs["sendUpdates"] = "all"
        if body.get("conferenceData"):
            insert_kwargs["conferenceDataVersion"] = 1
        event = service.events().insert(**insert_kwargs).execute()

        logger.info(
            "Created calendar event %s for booking %s",
            event["id"],
            booking_data.get("reference_code", "?"),
        )
        return {
            "event_id": event["id"],
            "html_link": event.get("htmlLink", ""),
            "conference_url": event.get("hangoutLink", ""),
        }

    except HttpError as exc:
        event_id = (booking_data.get("event_id") or "").strip()
        if exc.resp.status == 409 and event_id:
            event = service.events().get(calendarId=resolved_calendar_id, eventId=event_id).execute()
            logger.info("Reused existing calendar event %s after duplicate insert", event_id)
            return {
                "event_id": event["id"],
                "html_link": event.get("htmlLink", ""),
                "conference_url": event.get("hangoutLink", ""),
            }
        logger.error("Google Calendar create failed: %s", exc)
        raise
    except Exception as exc:
        logger.error("Calendar event creation failed: %s", exc)
        raise


async def update_booking_event(
    event_id: str,
    updates: dict,
    calendar_id: str | None = None,
    refresh_token: str | None = None,
) -> dict:
    """
    Update an existing calendar event.

    `updates` can contain the same keys as booking_data (for a full rebuild)
    or a subset of Google Calendar event fields for a partial patch.
    """
    if get_settings().LOCAL_DEV_MODE:
        logger.info("[LOCAL_DEV_MODE] calendar update skipped for event %s", event_id)
        return {"event_id": event_id, "html_link": ""}

    try:
        service = _get_calendar_service(refresh_token)
        resolved_calendar_id = calendar_id or _default_calendar_id()

        # If updates look like booking data, rebuild the event body
        if any(k in updates for k in ("client_name", "scheduled_date", "description")):
            body = _build_event_body(updates)
        else:
            body = updates

        patch_kwargs: dict[str, object] = {
            "calendarId": resolved_calendar_id,
            "eventId": event_id,
            "body": body,
        }
        if body.get("attendees"):
            patch_kwargs["sendUpdates"] = "all"
        if body.get("conferenceData"):
            patch_kwargs["conferenceDataVersion"] = 1
        event = service.events().patch(**patch_kwargs).execute()

        logger.info("Updated calendar event %s", event_id)
        return {
            "event_id": event["id"],
            "html_link": event.get("htmlLink", ""),
            "conference_url": event.get("hangoutLink", ""),
        }

    except HttpError as exc:
        logger.error("Google Calendar update failed for %s: %s", event_id, exc)
        raise
    except Exception as exc:
        logger.error("Calendar event update failed for %s: %s", event_id, exc)
        raise


async def get_booking_event_busy_block(
    event_id: str,
    calendar_id: str | None = None,
    refresh_token: str | None = None,
) -> dict | None:
    """Return the current event interval so retry checks can exclude it."""
    if get_settings().LOCAL_DEV_MODE:
        return None
    service = _get_calendar_service(refresh_token)
    resolved_calendar_id = calendar_id or _default_calendar_id()
    event = service.events().get(calendarId=resolved_calendar_id, eventId=event_id).execute()
    start = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date")
    end = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date")
    return {"start": start, "end": end} if start and end else None


async def get_booking_event_state(
    event_id: str,
    calendar_id: str | None = None,
    refresh_token: str | None = None,
) -> dict | None:
    """Return the provider state needed to resume a durable booking saga."""
    if get_settings().LOCAL_DEV_MODE:
        return {
            "event_id": event_id,
            "conference_url": "",
            "attendee_emails": [],
        }
    service = _get_calendar_service(refresh_token)
    resolved_calendar_id = calendar_id or _default_calendar_id()
    try:
        event = service.events().get(calendarId=resolved_calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp.status in {404, 410}:
            return None
        raise
    return {
        "event_id": event.get("id") or event_id,
        "conference_url": event.get("hangoutLink", ""),
        "attendee_emails": [
            attendee["email"]
            for attendee in event.get("attendees", [])
            if isinstance(attendee, dict) and attendee.get("email")
        ],
    }


async def delete_booking_event(
    event_id: str,
    calendar_id: str | None = None,
    refresh_token: str | None = None,
) -> bool:
    """
    Delete a calendar event (cancelled booking).

    Returns True on success, False on failure. Never raises.
    """
    if get_settings().LOCAL_DEV_MODE:
        logger.info("[LOCAL_DEV_MODE] calendar delete skipped for event %s", event_id)
        return True

    try:
        service = _get_calendar_service(refresh_token)
        resolved_calendar_id = calendar_id or _default_calendar_id()
        service.events().delete(
            calendarId=resolved_calendar_id,
            eventId=event_id,
            sendUpdates="all",
        ).execute()

        logger.info("Deleted calendar event %s", event_id)
        return True

    except HttpError as exc:
        if exc.resp.status == 410:
            # Already deleted
            logger.info("Calendar event %s already gone (410)", event_id)
            return True
        logger.error("Google Calendar delete failed for %s: %s", event_id, exc)
        return False
    except Exception as exc:
        logger.error("Calendar event delete failed for %s: %s", event_id, exc)
        return False


async def get_availability(
    start_date: str,
    end_date: str,
    calendar_id: str | None = None,
    refresh_token: str | None = None,
) -> list[dict]:
    """
    Return free/busy blocks for a date range.

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        refresh_token: Per-user Google OAuth token (falls back to env)

    Returns list of busy blocks: [{"start": iso, "end": iso}, ...]
    Used by the booking form to show available slots.
    """
    try:
        service = _get_calendar_service(refresh_token)
        resolved_calendar_id = calendar_id or _default_calendar_id()

        body = {
            "timeMin": f"{start_date}T00:00:00-05:00",
            "timeMax": f"{end_date}T23:59:59-05:00",
            "timeZone": shop_timezone().key,
            "items": [{"id": resolved_calendar_id}],
        }

        result = service.freebusy().query(body=body).execute()
        calendars = result.get("calendars", {})
        calendar_result = calendars.get(resolved_calendar_id)
        if not calendar_result:
            raise RuntimeError("Google Calendar freebusy response omitted the requested calendar")
        errors = calendar_result.get("errors") or []
        if errors:
            reasons = ", ".join(str(error.get("reason") or "unknown") for error in errors)
            raise RuntimeError(f"Google Calendar freebusy returned calendar errors: {reasons}")
        busy_blocks = calendar_result.get("busy", [])

        logger.info(
            "Availability query %s to %s: %d busy blocks",
            start_date,
            end_date,
            len(busy_blocks),
        )
        return busy_blocks

    except HttpError as exc:
        logger.error("Google Calendar freebusy failed: %s", exc)
        raise
    except Exception as exc:
        logger.error("Calendar availability query failed: %s", exc)
        raise


async def get_upcoming_events(
    days: int = 7,
    calendar_id: str | None = None,
    refresh_token: str | None = None,
) -> list[dict]:
    """
    Return upcoming events for the dashboard.

    Returns list of simplified event dicts:
        [{"id", "summary", "start", "end", "location", "html_link"}, ...]
    """
    try:
        service = _get_calendar_service(refresh_token)
        resolved_calendar_id = calendar_id or _default_calendar_id()

        now = datetime.now(UTC)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days)).isoformat()

        result = (
            service.events()
            .list(
                calendarId=resolved_calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )

        events = []
        for item in result.get("items", []):
            events.append(
                {
                    "id": item["id"],
                    "summary": item.get("summary", ""),
                    "start": item.get("start", {}).get("dateTime", item.get("start", {}).get("date", "")),
                    "end": item.get("end", {}).get("dateTime", item.get("end", {}).get("date", "")),
                    "location": item.get("location", ""),
                    "html_link": item.get("htmlLink", ""),
                }
            )

        logger.info("Upcoming events (%d days): %d found", days, len(events))
        return events

    except HttpError as exc:
        logger.error("Google Calendar list failed: %s", exc)
        raise
    except Exception as exc:
        logger.error("Calendar upcoming query failed: %s", exc)
        raise
