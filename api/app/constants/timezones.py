"""Shared wall-clock timezone for the shop.

The shop trades in America/New_York while the API runs with TZ=Etc/UTC on
Railway. Any "which day did this happen on" question has to be asked in shop
time, or an 8pm Eastern payment is filed under tomorrow.

This is the single definition. Seven modules previously each declared their own
ZoneInfo("America/New_York") under three different names -- SHOP_TIMEZONE,
_SHOP_TIMEZONE and _EASTERN -- in endpoints/bookings.py, endpoints/calendar.py,
endpoints/waiver.py, endpoints/stripe_connect.py, endpoints/stripe_webhooks.py,
services/email.py and services/email_foundry.py. All of them import from here
now, so the shop's trading timezone changes in one place.

The IANA string literals in endpoints/calendar.py are deliberately untouched:
those are Google Calendar API payload fields that want a name, not a tzinfo.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SHOP_TIMEZONE = ZoneInfo("America/New_York")


def shop_today() -> date:
    """Today in shop time, not in the server's UTC."""
    return datetime.now(SHOP_TIMEZONE).date()


def shop_day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive shop-time bounds for a date range, as tz-aware datetimes.

    Ledger timestamps are timestamptz and the API runs with TZ=Etc/UTC, so naive
    bounds are read as UTC: a payment taken at 8pm Eastern is 00:00 UTC the next
    day and lands on the wrong business day. Aware bounds in shop time put every
    entry on the day the shop actually made it.

    Lives here rather than in an endpoint module because POS and E-con both ask
    the same question and must answer it identically.
    """
    return (
        datetime.combine(start, time.min, tzinfo=SHOP_TIMEZONE),
        datetime.combine(end, time.max, tzinfo=SHOP_TIMEZONE),
    )
