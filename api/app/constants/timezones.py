"""Shared wall-clock timezone for the studio.

The studio trades in STUDIO_TIMEZONE while the API runs with TZ=Etc/UTC on
Railway. Any "which day did this happen on" question has to be asked in studio
time, or an 8pm payment is filed under tomorrow.

This is the single definition, read on every call because the owner can change
the zone from the studio settings page while the API is running.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def shop_timezone() -> ZoneInfo:
    """The studio's zone. ZoneInfo caches by key, so this is cheap."""
    return ZoneInfo(get_settings().STUDIO_TIMEZONE)


def shop_today() -> date:
    """Today in shop time, not in the server's UTC."""
    return datetime.now(shop_timezone()).date()


def shop_day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive shop-time bounds for a date range, as tz-aware datetimes.

    Ledger timestamps are timestamptz and the API runs with TZ=Etc/UTC, so naive
    bounds are read as UTC: a payment taken in the evening can land on the wrong
    business day. Aware bounds in shop time put every entry on the day the shop
    actually made it.

    Lives here rather than in an endpoint module because POS and E-con both ask
    the same question and must answer it identically.
    """
    zone = shop_timezone()
    return (
        datetime.combine(start, time.min, tzinfo=zone),
        datetime.combine(end, time.max, tzinfo=zone),
    )
