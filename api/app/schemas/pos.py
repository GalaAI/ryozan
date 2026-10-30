"""
Pydantic schemas for POS transaction recording.

POSTransactionCreate: staff records a cash or card payment.
POSTransactionResponse: full transaction data returned to the frontend.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class POSTransactionCreate(BaseModel):
    """Record a POS transaction (cash or card)."""

    amount_cents: int = Field(..., gt=0, description="Amount in cents (e.g. 15000 = $150.00)")
    commission_base_cents: int | None = Field(
        None,
        ge=0,
        description="Configured service price used for artist commission; defaults to amount_cents",
    )
    payment_method: str = Field(..., pattern="^(cash|card)$", description="'cash' or 'card'")
    processor: str | None = Field(None, description="'stripe', 'manual', or null")
    booking_id: UUID | None = None
    client_name: str | None = None
    description: str | None = None
    notes: str | None = None
    stripe_session_id: str | None = None


class POSTransactionResponse(BaseModel):
    id: UUID
    artist_id: UUID
    booking_id: UUID | None = None
    amount_cents: int
    commission_base_cents: int
    price_adjustment_cents: int
    payment_method: str
    processor: str | None = None
    client_name: str | None = None
    description: str | None = None
    stripe_session_id: str | None = None
    status: str
    notes: str | None = None
    created_at: datetime
    created_by: UUID

    model_config = {"from_attributes": True}


# The existing manual-record and response schemas stay unchanged. Attempts have
# a separate input contract: clients select intent, never an account or split.
Cents = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
POSAttemptState = Literal[
    "prepared",
    "awaiting_payment",
    "processing",
    "cancel_requested",
    "reconciliation_required",
    "succeeded",
    "canceled",
    "failed",
]


class POSAttemptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
    artist_id: UUID
    booking_id: UUID | None = None
    collection_method: Literal["qr", "reader"]
    # An explicit final service price, never an estimate or a gross payment.
    service_total_cents: Cents
    tip_cents: Cents = 0
    terminal_reader_id: str | None = Field(default=None, pattern=r"^tmr_[A-Za-z0-9]+$")

    @model_validator(mode="after")
    def reader_matches_method(self):
        if (self.collection_method == "reader") != (self.terminal_reader_id is not None):
            raise ValueError("A reader is required only for reader collection")
        return self


class POSBalanceSnapshot(BaseModel):
    """Exact service-only projection stored before creating a provider object."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    currency: Literal["USD"] = "USD"
    livemode: bool = Field(strict=True)
    service_total_cents: Cents
    paid_service_cents: Cents
    refunded_service_cents: Cents
    remaining_service_cents: Cents
    evidence_keys: list[str]

    @model_validator(mode="after")
    def exact_remaining_balance(self):
        if self.refunded_service_cents > self.paid_service_cents:
            raise ValueError("Service refunds exceed service payments")
        expected = max(0, self.service_total_cents - self.paid_service_cents + self.refunded_service_cents)
        if self.remaining_service_cents != expected:
            raise ValueError("Remaining service balance does not match its evidence")
        if len(self.evidence_keys) != len(set(self.evidence_keys)):
            raise ValueError("Balance evidence must not be counted twice")
        return self


class POSReceiptContext(BaseModel):
    """Server-authored receipt facts, independent of browser or booking edits."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    description: str = Field(min_length=1, max_length=1000)
    reference_code: str | None = Field(default=None, max_length=64)
    customer_email: str | None = Field(default=None, max_length=320)
    refund_policy: Literal["all_payments_final"] = "all_payments_final"
    processing_fee_policy: Literal["included_in_displayed_amount"] = "included_in_displayed_amount"


class POSAttemptResponse(POSTransactionResponse):
    """Staff attempt view; legacy endpoints still use POSTransactionResponse."""

    attempt_version: Literal[1]
    request_key: str
    collection_method: Literal["qr", "reader"]
    attempt_state: POSAttemptState
    attempt_revision: int
    currency: Literal["USD"]
    livemode: bool
    tip_cents: Cents
    stripe_payment_intent_id: str | None = None
    stripe_account_id: str | None = None
    routing_destination: Literal["artist", "shop"]
    routing_reason: str
    commission_rate: int
    artist_cut_cents: Cents
    artist_tip_cents: Cents
    shop_cut_cents: Cents
    payout_owed_cents: Cents
    balance_snapshot: POSBalanceSnapshot
    receipt_context: POSReceiptContext
    terminal_reader_id: str | None = None
    reader_account_id: str | None = None
    reader_ownership: Literal["connected_account", "platform", "platform_preview"] | None = None
    reader_eligibility_ref: str | None = None
    checkout_url: str | None = None
    updated_at: datetime
    settled_at: datetime | None = None
