"""
POS Transaction model — records every point-of-sale payment (cash or card).

Cash transactions are recorded manually by staff.
Card transactions may link to a Stripe Checkout Session.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Nullable, with no defaults: absence means an unversioned legacy record, never
# an inferred allocation. Keep v1 names stable for its additive migration.
POS_ATTEMPT_V1_COLUMNS = (
    "attempt_version",
    "request_key",
    "request_fingerprint",
    "collection_method",
    "attempt_state",
    "attempt_revision",
    "currency",
    "livemode",
    "tip_cents",
    "stripe_payment_intent_id",
    "stripe_account_id",
    "routing_destination",
    "routing_reason",
    "commission_rate",
    "artist_cut_cents",
    "artist_tip_cents",
    "shop_cut_cents",
    "payout_owed_cents",
    "balance_snapshot",
    "receipt_context",
    "terminal_reader_id",
    "reader_account_id",
    "reader_ownership",
    "reader_eligibility_ref",
    "checkout_url",
    "updated_at",
    "settled_at",
)
POS_ATTEMPT_ACTIVE_STATES = (
    "prepared",
    "awaiting_payment",
    "processing",
    "cancel_requested",
    "reconciliation_required",
)
_ACTIVE_SQL = ", ".join(f"'{state}'" for state in POS_ATTEMPT_ACTIVE_STATES)


class POSTransaction(Base):
    """Legacy payment records and versioned, durable QR/reader attempts."""

    __tablename__ = "ryozan_pos_transactions"
    __table_args__ = (
        CheckConstraint(
            "(" + " AND ".join(f"{name} IS NULL" for name in POS_ATTEMPT_V1_COLUMNS) + ") OR ("
            "attempt_version IS NOT NULL AND attempt_version = 1 AND "
            "request_key IS NOT NULL AND request_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$' AND "
            "request_fingerprint IS NOT NULL AND request_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "collection_method IS NOT NULL AND attempt_state IS NOT NULL AND "
            "attempt_revision IS NOT NULL AND attempt_revision >= 0 AND "
            "currency IS NOT NULL AND currency = 'USD' AND livemode IS NOT NULL AND tip_cents IS NOT NULL AND "
            "routing_destination IS NOT NULL AND routing_reason IS NOT NULL AND length(routing_reason) > 0 AND "
            "commission_rate IS NOT NULL AND artist_cut_cents IS NOT NULL AND artist_tip_cents IS NOT NULL AND "
            "shop_cut_cents IS NOT NULL AND "
            "payout_owed_cents IS NOT NULL AND balance_snapshot IS NOT NULL AND receipt_context IS NOT NULL AND "
            "updated_at IS NOT NULL)",
            name="ck_pos_attempt_v1_presence",
        ),
        CheckConstraint(
            "attempt_version IS NULL OR (amount_cents > 0 AND commission_base_cents >= 0 AND "
            "price_adjustment_cents >= 0 AND tip_cents >= 0 AND "
            "amount_cents::bigint = commission_base_cents::bigint + price_adjustment_cents + tip_cents AND "
            "commission_rate BETWEEN 0 AND 100 AND artist_cut_cents >= 0 AND shop_cut_cents >= 0 AND "
            "artist_tip_cents IN (0, tip_cents) AND "
            "artist_cut_cents::bigint = commission_base_cents::bigint * commission_rate / 100 + artist_tip_cents AND "
            "payout_owed_cents >= 0 AND amount_cents::bigint = artist_cut_cents::bigint + shop_cut_cents)",
            name="ck_pos_attempt_v1_amounts",
        ),
        CheckConstraint(
            "attempt_version IS NULL OR ((routing_destination = 'artist' AND stripe_account_id IS NOT NULL AND "
            "stripe_account_id ~ '^acct_[A-Za-z0-9]+$' AND payout_owed_cents = 0) OR "
            "(routing_destination = 'shop' AND stripe_account_id IS NULL AND payout_owed_cents = artist_cut_cents))",
            name="ck_pos_attempt_v1_routing",
        ),
        CheckConstraint(
            "attempt_version IS NULL OR (payment_method = 'card' AND processor IS NOT NULL AND processor = 'stripe' AND "
            "((collection_method = 'qr' AND terminal_reader_id IS NULL AND reader_account_id IS NULL AND "
            "reader_ownership IS NULL AND reader_eligibility_ref IS NULL) OR (collection_method = 'reader' AND "
            "terminal_reader_id IS NOT NULL AND terminal_reader_id ~ '^tmr_[A-Za-z0-9]+$' AND "
            "reader_ownership IS NOT NULL AND ((reader_ownership = 'connected_account' AND "
            "routing_destination = 'artist' AND reader_account_id IS NOT NULL AND "
            "reader_account_id = stripe_account_id AND reader_eligibility_ref IS NULL) OR "
            "(reader_ownership IN ('platform', 'platform_preview') AND reader_account_id IS NULL AND "
            "((reader_ownership = 'platform' AND routing_destination = 'shop' AND reader_eligibility_ref IS NULL) OR "
            "(reader_ownership = 'platform_preview' AND routing_destination = 'artist' AND "
            "reader_eligibility_ref IS NOT NULL AND length(trim(reader_eligibility_ref)) > 0)))))))",
            name="ck_pos_attempt_v1_collection",
        ),
        CheckConstraint(
            f"attempt_version IS NULL OR ((attempt_state IN ({_ACTIVE_SQL}) AND status = 'pending' AND "
            "settled_at IS NULL) OR (attempt_state = 'succeeded' AND status = 'completed' AND "
            "settled_at IS NOT NULL AND stripe_payment_intent_id IS NOT NULL) OR "
            "(attempt_state IN ('canceled', 'failed') AND status = 'voided' AND settled_at IS NULL))",
            name="ck_pos_attempt_v1_state",
        ),
        CheckConstraint(
            "attempt_version IS NULL OR ((stripe_payment_intent_id IS NULL OR "
            "stripe_payment_intent_id ~ '^pi_[A-Za-z0-9]+$') AND "
            "(stripe_session_id IS NULL OR stripe_session_id ~ '^cs_[A-Za-z0-9_]+$') AND "
            "(collection_method = 'qr' OR (stripe_session_id IS NULL AND checkout_url IS NULL)))",
            name="ck_pos_attempt_v1_provider_refs",
        ),
        CheckConstraint(
            "attempt_version IS NULL OR (jsonb_typeof(balance_snapshot) = 'object' AND "
            'balance_snapshot @> \'{"version": 1,"currency":"USD"}\'::jsonb AND '
            "balance_snapshot ?& ARRAY['service_total_cents','paid_service_cents','refunded_service_cents',"
            "'remaining_service_cents','evidence_keys','livemode'] AND "
            "balance_snapshot->'livemode' = to_jsonb(livemode) AND "
            "jsonb_typeof(balance_snapshot->'evidence_keys') = 'array' AND "
            "jsonb_typeof(balance_snapshot->'service_total_cents') = 'number' AND "
            "jsonb_typeof(balance_snapshot->'paid_service_cents') = 'number' AND "
            "jsonb_typeof(balance_snapshot->'refunded_service_cents') = 'number' AND "
            "jsonb_typeof(balance_snapshot->'remaining_service_cents') = 'number' AND "
            "(balance_snapshot->>'service_total_cents')::bigint >= 0 AND "
            "(balance_snapshot->>'paid_service_cents')::bigint >= 0 AND "
            "(balance_snapshot->>'refunded_service_cents')::bigint BETWEEN 0 AND "
            "(balance_snapshot->>'paid_service_cents')::bigint AND "
            "(balance_snapshot->>'remaining_service_cents')::bigint = greatest(0, "
            "(balance_snapshot->>'service_total_cents')::bigint - (balance_snapshot->>'paid_service_cents')::bigint + "
            "(balance_snapshot->>'refunded_service_cents')::bigint) AND "
            "commission_base_cents <= (balance_snapshot->>'remaining_service_cents')::bigint AND "
            "jsonb_typeof(receipt_context) = 'object' AND receipt_context ? 'description' AND "
            "jsonb_typeof(receipt_context->'description') = 'string' AND "
            "length(trim(receipt_context->>'description')) > 0 AND "
            'receipt_context @> \'{"version": 1,"refund_policy":"all_payments_final",'
            '"processing_fee_policy":"included_in_displayed_amount"}\'::jsonb)',
            name="ck_pos_attempt_v1_context",
        ),
        Index(
            "ux_pos_attempt_v1_request",
            "created_by",
            "request_key",
            unique=True,
            postgresql_where=text("attempt_version = 1"),
        ),
        Index(
            "ux_pos_attempt_v1_payment_intent",
            "livemode",
            text("coalesce(stripe_account_id, '')"),
            "stripe_payment_intent_id",
            unique=True,
            postgresql_where=text("attempt_version = 1 AND stripe_payment_intent_id IS NOT NULL"),
        ),
        Index(
            "ux_pos_attempt_v1_active_booking",
            "booking_id",
            unique=True,
            postgresql_where=text(
                f"attempt_version = 1 AND booking_id IS NOT NULL AND attempt_state IN ({_ACTIVE_SQL})"
            ),
        ),
        Index(
            "ux_pos_attempt_v1_active_reader",
            text("coalesce(reader_account_id, '')"),
            "terminal_reader_id",
            unique=True,
            postgresql_where=text(
                f"attempt_version = 1 AND terminal_reader_id IS NOT NULL AND attempt_state IN ({_ACTIVE_SQL})"
            ),
        ),
        Index(
            "ux_ryozan_pos_transactions_stripe_session_id",
            "stripe_session_id",
            unique=True,
            postgresql_where=text("stripe_session_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The artist who performed the service
    artist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ryozan_users.id"), nullable=False)

    # Optional link to a booking
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ryozan_bookings.id"), nullable=True
    )

    # Payment details
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_base_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    price_adjustment_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)  # 'cash' or 'card'
    processor: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 'stripe', 'manual', etc.

    # Client info (for walk-ins without a booking)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stripe reference (if card payment via Stripe)
    stripe_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Server-authored attempt contract. Artist is the service provider above;
    # created_by remains the authenticated operator, never the routing artist.
    attempt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    collection_method: Mapped[str | None] = mapped_column(String(12), nullable=True)
    attempt_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    livemode: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tip_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Null means the platform charge scope. Never resolve this again from a
    # mutable artist profile when reconciling, canceling, or refunding.
    stripe_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_destination: Mapped[str | None] = mapped_column(String(20), nullable=True)
    routing_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commission_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artist_cut_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artist_tip_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shop_cut_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payout_owed_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    balance_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    receipt_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    terminal_reader_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Reader resources and the PaymentIntent can belong to different accounts
    # only under Stripe's platform-owned-reader private preview. Persist both.
    reader_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reader_ownership: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Required only for private-preview routing; supplied by trusted server
    # capability configuration after enrollment is verified, never by a client.
    reader_eligibility_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Status tracking
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")  # completed, pending, voided

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # The staff member who recorded this transaction (may differ from artist)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ryozan_users.id"), nullable=False)
