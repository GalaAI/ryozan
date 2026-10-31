"""
Status constants for Ryozan entities.

Single source of truth for all status values across the codebase.
Import these instead of using raw strings.
"""


class DocStatus:
    """Document/waiver statuses."""

    SENT = "sent"
    SIGNED = "signed"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    ARCHIVED = "archived"
    REJECTED = "rejected"
    FLAGGED = "flagged"
    EXPIRED = "expired"


class BookingStatus:
    """Booking statuses."""

    PENDING = "pending"
    QUOTED = "quoted"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    EXPIRED = "expired"


class QuoteStatus:
    """Quote statuses."""

    SENT = "sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
