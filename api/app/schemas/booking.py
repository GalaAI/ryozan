"""
Pydantic schemas for booking operations.

Clients create requests; artists confirm/update; both can view.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

BookingKind = Literal["tattoo_session", "flash", "consultation"]
ConsultationMethod = Literal["whatsapp", "meet", "in_person"]
BookyMode = Literal["manual", "shadow", "approval", "consult_auto", "session_auto"]
BookyTruthState = Literal[
    "loading",
    "ready",
    "empty",
    "stale",
    "blocked",
    "approval_needed",
    "failed",
    "completed",
]


class PreferredSlot(BaseModel):
    """One collector-proposed date and time. No hold and no deposit are attached."""

    date: date
    time: str = Field(pattern=r"^\d{2}:\d{2}$")


def _validate_preferred_slots(slots: list[PreferredSlot] | None) -> None:
    if slots is None:
        return
    if not slots:
        raise ValueError("preferred_slots must contain at least one slot")
    if len(slots) > 3:
        raise ValueError("preferred_slots accepts at most 3 slots")
    seen: set[tuple[date, str]] = set()
    for slot in slots:
        key = (slot.date, slot.time)
        if key in seen:
            raise ValueError("preferred_slots must not repeat the same date and time")
        seen.add(key)


class BookySettingsUpdate(BaseModel):
    mode: BookyMode


class BookySettingsResponse(BaseModel):
    artist_id: UUID
    artist_slug: str
    mode: BookyMode
    opted_in_at: datetime | None = None
    automation_enabled: bool
    policy_version: str
    artist_canary_allowed: bool
    collector_canary_configured: bool
    session_auto_available: bool = False


class BookyCapabilityRequest(BaseModel):
    inquiry_id: str = Field(min_length=8, max_length=100)


class BookyCapabilityResponse(BaseModel):
    capability: str
    inquiry_id: str
    collector_id: UUID
    expires_at: datetime
    policy_version: str


class BookingOutcomeEnvelope(BaseModel):
    """Collector-safe Booky outcome. No policy internals are exposed."""

    truth_state: BookyTruthState
    booking_status: str
    calendar_state: str
    invitation_state: str
    meet_state: str
    courier_state: str
    retry_eligible: bool = False
    updated_at: datetime


class BookyDecisionEnvelope(BookingOutcomeEnvelope):
    """Staff-only policy and decision projection."""

    action_id: str
    booking_id: UUID | None = None
    receipt_ids: list[UUID] = Field(default_factory=list)
    configured_mode: BookyMode
    effective_mode: BookyMode
    recommendation: str
    explanation: str
    confidence: str
    policy_version: str
    owner: str = "booky"
    risk: str
    required_approval: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)


class BookingCreate(BaseModel):
    """Client submits a booking request to an artist."""

    artist_id: UUID
    description: str = Field(min_length=10, max_length=5000)
    placement: str | None = None
    size_estimate: str | None = None
    style_preference: str | None = None
    reference_urls: list[str] | None = None
    preferred_date: date | None = None
    preferred_time: str | None = None
    client_notes: str | None = None


class StaffBookingCreate(BaseModel):
    """Admin/founder creates a booking on behalf of an artist (and optional client).

    Provide an existing client_id OR guest_name + (guest_email or guest_phone)
    for a walk-in / new client.
    """

    artist_id: UUID
    client_id: UUID | None = None
    guest_name: str | None = None
    guest_email: str | None = None
    guest_phone: str | None = None
    description: str = Field(min_length=1, max_length=5000)
    placement: str | None = None
    size_estimate: str | None = None
    style_preference: str | None = None
    scheduled_date: date | None = None
    scheduled_time: str | None = Field(None, max_length=5)
    duration_minutes: int | None = Field(None, ge=15, le=720)
    deposit_amount: float | None = Field(None, ge=0)
    estimated_total: float | None = Field(None, ge=0)
    artist_notes: str | None = None
    status: str = Field("confirmed", pattern="^(pending|quoted|confirmed)$")
    confirm_calendar: bool = True


class BookingUpdate(BaseModel):
    """Artist or admin updates a booking."""

    status: str | None = Field(None, pattern="^(pending|quoted|confirmed|completed|cancelled|no_show|expired)$")
    scheduled_date: date | None = None
    scheduled_time: str | None = None
    duration_minutes: int | None = Field(None, ge=15, le=720)
    deposit_amount: float | None = Field(None, ge=0)
    estimated_total: float | None = Field(None, ge=0)
    deposit_paid: bool | None = None
    artist_notes: str | None = None
    artist_id: UUID | None = None
    allow_conflict: bool = False
    allow_out_of_bounds: bool = False
    override_reason: str | None = Field(None, max_length=500)
    notify_client: bool = True


class BookingReschedule(BaseModel):
    """Artist/admin moves a booking to a validated available slot."""

    scheduled_date: date
    scheduled_time: str = Field(max_length=5)
    duration_minutes: int | None = Field(None, ge=15, le=720)
    reason: str | None = Field(None, max_length=500)
    notify_client: bool = True
    allow_conflict: bool = False
    allow_out_of_bounds: bool = False
    override_reason: str | None = Field(None, max_length=500)


class BookingApprove(BaseModel):
    """Staff approves a request: one confirmed time plus the deposit the collector owes.

    A positive deposit leaves the booking quoted (approved, deposit pending) and
    emails a deposit link only when the artist's Stripe Connect account is ready.
    A zero deposit confirms the booking.
    """

    scheduled_date: date
    scheduled_time: str = Field(max_length=5)
    duration_minutes: int | None = Field(None, ge=15, le=720)
    deposit_amount: float = Field(ge=0, le=5000)
    artist_message: str | None = Field(None, max_length=1000)
    allow_conflict: bool = False
    allow_out_of_bounds: bool = False
    override_reason: str | None = Field(None, max_length=500)


class BookingApprovalDefaults(BaseModel):
    """What the approval card prefills, and whether a deposit link can be sent."""

    deposit_amount: float
    deposit_source: Literal["booking", "session_type", "duration_rule", "consultation"]
    duration_minutes: int
    stripe_ready: bool
    can_self_onboard: bool
    artist_name: str


class BookingApprovalResult(BaseModel):
    booking: "BookingResponse"
    status: str
    deposit_amount: float
    deposit_link_sent: bool
    stripe_ready: bool
    email_queued: bool
    # Returned to the approving staff member so a phone-only collector can still
    # be sent the link by text or in Courier.
    deposit_url: str | None = None
    scheduled_date: date
    scheduled_time: str


class BookingCancel(BaseModel):
    """Artist/admin cancels a booking with an auditable reason."""

    reason: str | None = Field(None, max_length=500)
    notify_client: bool = True
    delete_calendar_event: bool = True


class BookingResponse(BaseModel):
    id: UUID
    client_id: UUID | None = None
    artist_id: UUID
    status: str
    description: str
    placement: str | None = None
    size_estimate: str | None = None
    style_preference: str | None = None
    reference_urls: list[str] | None = None
    preferred_date: date | None = None
    preferred_time: str | None = None
    preferred_slots: list[PreferredSlot] | None = None
    scheduled_date: date | None = None
    scheduled_time: str | None = None
    duration_minutes: int | None = None
    deposit_amount: float | None = None
    estimated_total: float | None = None
    deposit_paid: bool = False
    is_bring_your_own_flash: bool = False
    deposit_authorization_status: str | None = None
    deposit_authorized_at: datetime | None = None
    deposit_review_deadline_at: datetime | None = None
    deposit_review_reminder_sent_at: datetime | None = None
    checkout_url: str | None = None
    artist_notes: str | None = None
    client_notes: str | None = None
    conversation_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    # Guest contact (public bookings without an account)
    guest_email: str | None = None
    guest_phone: str | None = None
    sms_opt_in: bool = False
    sms_opt_in_at: datetime | None = None
    sms_consent_text: str | None = None
    reference_code: str | None = None
    booking_kind: BookingKind = "tattoo_session"
    consultation_method: ConsultationMethod | None = None
    # Joined fields (populated by endpoint)
    client_name: str | None = None
    artist_name: str | None = None

    # Resolved identity — always populated (view coalesces guest vs user)
    resolved_name: str | None = None
    resolved_email: str | None = None
    resolved_phone: str | None = None

    model_config = {"from_attributes": True}


class BookingListResponse(BaseModel):
    bookings: list[BookingResponse]
    total: int


class BookingLifecycleEventResponse(BaseModel):
    id: UUID
    booking_id: UUID | None = None
    artist_id: UUID | None = None
    actor_id: UUID | None = None
    event_type: str
    source: str
    reference_code: str | None = None
    hold_token: str | None = None
    guest_email: str | None = None
    summary: str
    details: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BookingScheduleSuggestion(BaseModel):
    date: str
    time: str
    label: str
    duration_minutes: int
    source: str
    confidence: str
    rationale: str


class BookingScheduleSuggestionResponse(BaseModel):
    booking_id: UUID
    artist_id: UUID
    timezone: str
    duration_minutes: int
    calendar_connected: bool
    suggestions: list[BookingScheduleSuggestion]


# ── Public booking (no auth required) ──────────────────


class PublicBookingSubmit(BaseModel):
    """Public booking request — no account needed."""

    inquiry_id: str | None = Field(None, max_length=100)
    artist_slug: str  # an artist slug or 'any' — resolved server-side
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=255)
    phone: str | None = None
    sms_opt_in: bool = False
    description: str = Field(min_length=10, max_length=5000)
    booking_kind: BookingKind = "tattoo_session"
    is_bring_your_own_flash: bool = False
    consultation_method: ConsultationMethod | None = None
    booky_consent: bool = False
    booky_consent_policy_version: str | None = Field(None, max_length=64)
    booky_capability: str | None = Field(None, max_length=2048)
    style_preference: str | None = None
    session_type_id: UUID | None = None
    placement: str | None = None
    size_estimate: str | None = None
    budget: str | None = None
    timeline: str | None = None
    preferred_date: date | None = None
    preferred_time: str | None = Field(None, max_length=50)
    hold_token: str | None = Field(None, max_length=100)
    # Custom pieces, consultations and bring-your-own-flash: the collector
    # proposes 1 to 3 date/times with no hold and no deposit; the artist picks
    # one later through PATCH /bookings/{id}. Mutually exclusive with the
    # single held slot above.
    preferred_slots: list[PreferredSlot] | None = Field(default=None, max_length=3)
    booking_add_on_ids: list[UUID] | None = Field(default=None, max_length=12)
    save_payment_method: bool = False
    reference_image_urls: list[str] | None = None  # Drive URLs from upload step
    client_notes: str | None = None

    @model_validator(mode="after")
    def validate_booking_kind(self):
        if self.preferred_slots is not None:
            _validate_preferred_slots(self.preferred_slots)
            if self.preferred_date is not None or self.preferred_time or self.hold_token:
                raise ValueError("preferred_slots cannot be combined with preferred_date, preferred_time or hold_token")
        if self.booking_kind == "consultation" and self.consultation_method is None:
            raise ValueError("consultation_method is required when booking_kind is consultation")
        if (
            self.booking_kind == "consultation"
            and self.preferred_slots is None
            and (self.preferred_date is None or not self.preferred_time or not self.hold_token)
        ):
            raise ValueError("consultations require a held date and time or preferred_slots")
        if self.booking_kind != "consultation" and self.consultation_method is not None:
            raise ValueError("consultation_method is only valid when booking_kind is consultation")
        if self.is_bring_your_own_flash and self.booking_kind != "flash":
            raise ValueError("is_bring_your_own_flash requires booking_kind flash")
        if self.booky_consent and not self.booky_consent_policy_version:
            raise ValueError("booky_consent_policy_version is required when Booky consent is granted")
        if not self.booky_consent and self.booky_consent_policy_version is not None:
            raise ValueError("booky_consent_policy_version requires Booky consent")
        if self.booky_capability and not self.inquiry_id:
            raise ValueError("inquiry_id is required with a Booky capability")
        return self


class PublicBookingResponse(BaseModel):
    reference_code: str | None = None
    artist_name: str | None = None
    booking_id: UUID | None = None
    checkout_url: str | None = None
    checkout_required: bool = False
    deposit_amount: float | None = None
    message: str = "Your booking request has been submitted!"
    booking_status: str | None = "pending"
    scheduled_date: date | None = None
    scheduled_time: str | None = None
    preferred_slots: list[PreferredSlot] | None = None
    conference_url: str | None = None
    booky_outcome: BookingOutcomeEnvelope | None = None
    idempotent_acknowledgement: bool = False
    account_setup_token: str | None = None


class PublicCheckoutStatusResponse(BaseModel):
    """What the public confirm page may show after a Stripe redirect.

    Keyed by the Checkout Session id the buyer was handed back. It carries no
    client name, email or phone: only the reference, the artist and where the
    booking and its deposit stand on the server.
    """

    reference_code: str | None = None
    artist_name: str | None = None
    booking_status: str
    # "paid": captured; "authorized": held for artist review; "pending":
    # Stripe has not reported the payment yet; "none": no deposit on record.
    deposit_state: Literal["paid", "authorized", "pending", "none"]
    deposit_amount: float | None = None
    scheduled_date: date | None = None
    scheduled_time: str | None = None


class PublicInquirySubmit(BaseModel):
    """Public general inquiry. Opens a Courier thread and never a booking row.

    artist_slug None or "any" lands the thread in the shop queue (founder and
    admin accounts); a real slug ties the thread to that artist.
    """

    inquiry_id: str | None = Field(None, min_length=8, max_length=100)
    artist_slug: str | None = Field(None, max_length=100)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=255)
    phone: str | None = Field(None, max_length=50)
    sms_opt_in: bool = False
    message: str = Field(min_length=10, max_length=5000)


class PublicInquiryResponse(BaseModel):
    reference_code: str
    conversation_id: UUID | None = None
    message: str = "Got it. We will reply by email."
    idempotent_acknowledgement: bool = False


class PublicBookingDraft(BaseModel):
    """Recoverable snapshot of an in-progress public booking form."""

    inquiry_id: str = Field(min_length=8, max_length=100)
    artist_slug: str | None = Field(None, max_length=100)
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=50)
    sms_opt_in: bool = False
    description: str | None = Field(None, max_length=5000)
    booking_kind: BookingKind | None = None
    is_bring_your_own_flash: bool = False
    consultation_method: ConsultationMethod | None = None
    style_preference: str | None = None
    placement: str | None = None
    size_estimate: str | None = None
    budget: str | None = None
    timeline: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = Field(None, max_length=50)
    hold_token: str | None = Field(None, max_length=100)
    # Plain dicts ({"date": "YYYY-MM-DD", "time": "HH:MM"}) so the draft
    # form_data JSON round-trips without date objects.
    preferred_slots: list[dict[str, str]] | None = Field(default=None, max_length=3)
    client_notes: str | None = None
    image_urls: list[str] | None = None


class PublicBookingDraftResponse(BaseModel):
    inquiry_id: str
    status: str


class BookingDraftRecord(BaseModel):
    id: UUID
    inquiry_id: str
    status: str
    artist_slug: str | None = None
    guest_name: str | None = None
    guest_email: str | None = None
    guest_phone: str | None = None
    form_data: dict | None = None
    image_urls: list[str] | None = None
    reference_code: str | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BookingDraftListResponse(BaseModel):
    drafts: list[BookingDraftRecord]
    total: int


BookingApprovalResult.model_rebuild()
