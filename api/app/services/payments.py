"""
Payment routing — single source of truth for the Ryozan revenue split.

Every checkout path calls resolve_charge_routing() instead of inlining its own
split, then apply_customer_receipt() to attach exact customer-facing receipt
context to the resulting PaymentIntent.

Rules:
  * Artist connected AND payment-ready -> direct charge on the artist's
    connected account. The artist is the settlement merchant and Ryozan takes
    its configured share as application_fee_amount.
  * Artist not connected, or onboarding incomplete -> the full amount lands
    in the Ryozan platform account. The artist's share is recorded as
    payout_owed_cents so it can be routed manually.
  * Booth-rent artists carry commission_rate = 100, so the shop cut is zero
    in both branches.

Never raises. Callers always get a usable ChargeRouting.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.econ import EconLedgerEntry

logger = logging.getLogger("ryozan.payments")

# Applied when an artist has no explicit commission_rate. 70 means the artist
# keeps 70% and the shop keeps 30%. Booth-rent artists are set to 100.
DEFAULT_ARTIST_COMMISSION_RATE = 70

# Customer prices are four percent above the configured service price. This is
# one all-in price for every supported Checkout payment method, not a card
# surcharge. Artist commission remains based on the configured service price,
# so the price adjustment stays with Ryozan to offset payment operating costs.
CUSTOMER_PRICE_INCREASE_BPS = 400

DESTINATION_ARTIST = "artist"
DESTINATION_SHOP = "shop"


@dataclass(frozen=True)
class ServiceBalanceProjection:
    """Read-only service balance. Uncertainty never becomes a chargeable amount."""

    status: str
    livemode: bool
    service_total_cents: int | None
    paid_service_cents: int
    refunded_service_cents: int
    remaining_service_cents: int | None
    evidence_keys: tuple[str, ...]
    reconciliation_reasons: tuple[str, ...]

    def snapshot(self) -> dict[str, Any]:
        if self.status != "ready":
            raise ValueError("Service balance requires reconciliation")
        return {
            "version": 1,
            "currency": "USD",
            "livemode": self.livemode,
            "service_total_cents": self.service_total_cents,
            "paid_service_cents": self.paid_service_cents,
            "refunded_service_cents": self.refunded_service_cents,
            "remaining_service_cents": self.remaining_service_cents,
            "evidence_keys": list(self.evidence_keys),
        }


def _record_field(record: Any, name: str, default: Any = None) -> Any:
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def _exact_cents(value: Any) -> int | None:
    # bool, float, numeric strings and implicit rounding are not persisted cents.
    return value if type(value) is int and 0 <= value <= 2_147_483_647 else None


def _service_allocation(value: Any, gross: int | None) -> tuple[int, int, int] | None:
    """The v1 ledger receipt is explicit; gross alone never supplies a base."""
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        return None
    parts = tuple(_exact_cents(value.get(key)) for key in ("service_cents", "price_adjustment_cents", "tip_cents"))
    if gross is None or any(part is None for part in parts) or sum(parts) != gross:
        return None
    return parts


def _manual_cash_allocation(
    row: Any, raw: dict, pos: Any, *, booking_id: str, artist_id: str
) -> tuple[int, int, int] | None:
    """Validate a new recorder-owned cash receipt against its persisted POS mirror.

    Cash already has POS and manual E-con owners; it is not a Stripe attempt.
    Only the authorized recorder may atomically author this versioned receipt
    with its POS row. A marker added to old gross-derived data is not a backfill
    contract, and this pure projection does not authenticate arbitrary DB writers.
    """
    identities = {
        "booking_id": booking_id,
        "artist_id": artist_id,
        "pos_transaction_id": str(_record_field(pos, "id") or ""),
        "recorded_by": str(_record_field(pos, "created_by") or ""),
    }
    for key, expected in identities.items():
        value = raw.get(key)
        try:
            if not isinstance(value, str) or str(UUID(value)) != value or value != expected:
                return None
        except ValueError:
            return None
    try:
        receipt_id = str(_record_field(row, "id") or "")
        if str(UUID(receipt_id)) != receipt_id:
            return None
    except (TypeError, ValueError):
        return None
    gross = _exact_cents(_record_field(row, "amount_cents"))
    allocation = _service_allocation(raw.get("service_allocation"), gross)
    if (
        type(raw.get("pos_cash_receipt_version")) is not int
        or raw["pos_cash_receipt_version"] != 1
        or raw.get("payment_method") != "cash"
        or type(raw.get("livemode")) is not bool
        or any(key in raw for key in ("payment_intent", "stripe_session_id", "stripe_account_id"))
        or _record_field(row, "provider") != "manual"
        or _record_field(row, "entry_type") != "payment"
        or _record_field(row, "status") != "posted"
        or _record_field(row, "category") != "pos_payment"
        or _record_field(row, "external_id") != f"pos-cash:{identities['pos_transaction_id']}"
        or str(_record_field(row, "artist_id")) != artist_id
        or _record_field(row, "currency") != "USD"
        or str(_record_field(pos, "booking_id")) != booking_id
        or str(_record_field(pos, "artist_id")) != artist_id
        or _record_field(pos, "payment_method") != "cash"
        or _record_field(pos, "processor") != "manual"
        or _record_field(pos, "status") != "completed"
        or any(
            _record_field(pos, key) is not None
            for key in (
                "attempt_version",
                "attempt_state",
                "tip_cents",
                "livemode",
                "stripe_session_id",
                "stripe_payment_intent_id",
                "stripe_account_id",
            )
        )
        or not gross
        or _exact_cents(_record_field(pos, "amount_cents")) != gross
        or allocation is None
    ):
        return None
    # The legacy POS adjustment means gross minus base, including any tip.
    # The receipt alone separates that remainder into adjustment and tip.
    if (
        _exact_cents(_record_field(pos, "commission_base_cents")) != allocation[0]
        or _exact_cents(_record_field(pos, "price_adjustment_cents")) != allocation[1] + allocation[2]
    ):
        return None
    return allocation


def project_booking_service_balance(
    booking: Any | None,
    *,
    service_total_cents: int | None,
    livemode: bool,
    pos_transactions: Iterable[Any] = (),
    ledger_entries: Iterable[Any] = (),
    gift_card_redemptions: Iterable[Any] = (),
    gift_cards: Iterable[Any] = (),
) -> ServiceBalanceProjection:
    """Project exact persisted payment evidence without reads, writes or providers.

    The caller supplies the staff-confirmed final service price and a complete
    booking-scoped evidence set, including linked gift purchases, in one consistent
    DB snapshot. Booking estimates,
    deposit_amount and quote prices are not proof of payment or service credit.
    Legacy POS bases were backfilled from gross, so only a v1 attempt allocation
    or matching ledger ``raw.service_allocation`` v1 can prove their service part.
    A Stripe ledger allocation contains integer service_cents,
    price_adjustment_cents and tip_cents, with raw.stripe_account_id present
    (null means platform scope) and raw.livemode matching the server environment.
    Manual cash requires a pos_cash_receipt_version=1 receipt, payment_method=cash,
    canonical booking/artist/POS/operator UUIDs, explicit livemode, and an exact
    allocation paired with one completed cash/manual POS row. It has no Stripe
    account or provider references. Legacy cash without this receipt stays unknown.

    Gift-card redemption rows are the existing committed service-value ledger;
    their linked cards must still match the booking's service artist and currency,
    and their purchase receipts must prove the same test/live environment.
    gift-card purchases are not service payments. Exact full refunds can reverse
    the original allocation; partial refunds require their own allocation.
    """
    issues: set[str] = set()
    evidence_keys: set[str] = set()
    total = _exact_cents(service_total_cents)
    if total is None:
        issues.add("confirmed_service_total_required")
    if type(livemode) is not bool:
        issues.add("payment_mode_required")
    booking_id = str(_record_field(booking, "id") or "")
    artist_id = str(_record_field(booking, "artist_id") or "")
    if booking is not None and (not booking_id or not artist_id):
        issues.add("booking_identity_required")
    deposit_aliases = {
        str(value)
        for value in (
            _record_field(booking, "stripe_payment_intent_id"),
            _record_field(booking, "stripe_checkout_session_id"),
        )
        if value
    }
    groups: list[dict[str, Any]] = []
    pos_aliases: set[str] = set()
    if _record_field(booking, "deposit_authorization_status") in {"pending_checkout", "authorized"}:
        issues.add("unsettled_deposit_authorization")

    def belongs(row_booking_id: Any) -> bool:
        return bool(booking_id and str(row_booking_id or "") == booking_id)

    def add_payment(
        *,
        aliases: set[str],
        gross: Any,
        allocation: Any,
        account: Any,
        account_known: bool,
        mode: Any,
        provider: str = "stripe",
    ):
        # Join the POS and E-con mirrors by provider identity, including rows
        # that bridge a Checkout Session and its PaymentIntent. Order-independent.
        matched = [group for group in groups if aliases & group["aliases"]]
        group = {
            "aliases": set(aliases),
            "gross": set(),
            "allocations": set(),
            "accounts": set(),
            "modes": set(),
            "providers": {provider},
        }
        for existing in matched:
            for name in group:
                group[name].update(existing[name])
            groups.remove(existing)
        exact_gross = _exact_cents(gross)
        if exact_gross is None:
            issues.add("invalid_payment_amount")
        else:
            group["gross"].add(exact_gross)
        if allocation is not None:
            group["allocations"].add(allocation)
        if account_known:
            if account is not None and (not isinstance(account, str) or not account.startswith("acct_")):
                issues.add("invalid_payment_account")
            else:
                group["accounts"].add(account)
        if type(mode) is bool:
            group["modes"].add(mode)
            if mode != livemode:
                issues.add("payment_mode_mismatch")
        groups.append(group)

    pos_rows = tuple(pos_transactions)
    pos_by_id: dict[str, list[Any]] = {}
    for row in pos_rows:
        pos_by_id.setdefault(str(_record_field(row, "id") or ""), []).append(row)
    for row in pos_rows:
        if not belongs(_record_field(row, "booking_id")):
            continue
        row_id = str(_record_field(row, "id") or "")
        if not row_id or str(_record_field(row, "artist_id")) != artist_id:
            issues.add("pos_identity_mismatch")
            continue
        aliases = {
            str(value)
            for value in (
                _record_field(row, "stripe_payment_intent_id"),
                _record_field(row, "stripe_session_id"),
            )
            if value
        }
        aliases.add(f"pos:{row_id}")
        pos_aliases.update(aliases)
        state = _record_field(row, "attempt_state")
        status = _record_field(row, "status")
        if state in {"prepared", "awaiting_payment", "processing", "cancel_requested", "reconciliation_required"}:
            issues.add(f"unsettled_pos_attempt:{row_id}")
            continue
        if status == "voided" and state in {None, "canceled", "failed"}:
            continue
        if status != "completed" or state not in {None, "succeeded"}:
            issues.add(f"unsettled_pos_payment:{row_id}")
            continue
        versioned = _record_field(row, "attempt_version") == 1
        if versioned and _record_field(row, "currency") != "USD":
            issues.add("payment_currency_mismatch")
        gross = _record_field(row, "amount_cents")
        allocation = (
            _service_allocation(
                {
                    "version": 1,
                    "service_cents": _record_field(row, "commission_base_cents"),
                    "price_adjustment_cents": _record_field(row, "price_adjustment_cents"),
                    "tip_cents": _record_field(row, "tip_cents"),
                },
                _exact_cents(gross),
            )
            if versioned
            else None
        )
        add_payment(
            aliases=aliases,
            gross=gross,
            allocation=allocation,
            account=_record_field(row, "stripe_account_id"),
            account_known=versioned,
            mode=_record_field(row, "livemode") if versioned else None,
            provider=(
                "manual"
                if _record_field(row, "payment_method") == "cash" or _record_field(row, "processor") == "manual"
                else "stripe"
            ),
        )

    refunds: list[Any] = []
    ledger_rows = tuple(ledger_entries)
    related_aliases = deposit_aliases | pos_aliases
    for row in ledger_rows:
        raw = _record_field(row, "raw") or {}
        if (
            _record_field(row, "provider") == "stripe"
            and _record_field(row, "entry_type") == "payment"
            and isinstance(raw, dict)
            and belongs(raw.get("booking_id"))
            and raw.get("checkout_type") not in {"gift_card", "booth_rent"}
        ):
            related_aliases.update(
                str(value)
                for value in (
                    raw.get("payment_intent"),
                    raw.get("stripe_session_id"),
                    _record_field(row, "external_id"),
                )
                if value
            )
    seen_cash_receipts: set[str] = set()
    seen_cash_receipt_ids: set[str] = set()
    for row in ledger_rows:
        raw = _record_field(row, "raw") or {}
        external_id = str(_record_field(row, "external_id") or "")
        manual_claim = (
            external_id.startswith("pos-cash:")
            or (isinstance(raw, dict) and "pos_cash_receipt_version" in raw)
            or (
                _record_field(row, "provider") == "manual"
                and (
                    _record_field(row, "category") == "pos_payment"
                    or (
                        isinstance(raw, dict)
                        and (
                            "pos_transaction_id" in raw
                            or (raw.get("payment_method") == "cash" and raw.get("booking_id"))
                        )
                    )
                )
            )
        )
        if manual_claim:
            pos_id = str(raw.get("pos_transaction_id") or "") if isinstance(raw, dict) else ""
            related = (
                (isinstance(raw, dict) and belongs(raw.get("booking_id")))
                or f"pos:{pos_id}" in pos_aliases
                or (external_id.startswith("pos-cash:") and f"pos:{external_id[9:]}" in pos_aliases)
            )
            if not related:
                continue
            mirrors = pos_by_id.get(pos_id, ())
            allocation = (
                _manual_cash_allocation(row, raw, mirrors[0], booking_id=booking_id, artist_id=artist_id)
                if isinstance(raw, dict) and len(mirrors) == 1
                else None
            )
            if allocation is None:
                issues.add("manual_cash_receipt_requires_reconciliation")
                continue
            receipt_id = str(_record_field(row, "id"))
            if pos_id in seen_cash_receipts or receipt_id in seen_cash_receipt_ids:
                issues.add("duplicate_manual_cash_receipt")
                continue
            seen_cash_receipts.add(pos_id)
            seen_cash_receipt_ids.add(receipt_id)
            add_payment(
                aliases={f"pos:{pos_id}"},
                gross=_record_field(row, "amount_cents"),
                allocation=allocation,
                account=None,
                account_known=False,
                mode=raw["livemode"],
                provider="manual",
            )
            continue
        if _record_field(row, "provider") != "stripe":
            continue
        raw = _record_field(row, "raw") or {}
        if not isinstance(raw, dict):
            issues.add("invalid_ledger_context")
            continue
        aliases = {str(value) for value in (raw.get("payment_intent"), raw.get("stripe_session_id")) if value}
        external_id = str(_record_field(row, "external_id") or "")
        if _record_field(row, "entry_type") == "payment" and external_id:
            aliases.add(external_id)
        related = bool(aliases & related_aliases)
        if raw.get("booking_id") and not belongs(raw["booking_id"]):
            if related:
                issues.add("payment_booking_mismatch")
            continue
        if not belongs(raw.get("booking_id")) and not related:
            continue
        if str(_record_field(row, "artist_id") or "") != artist_id:
            issues.add("payment_artist_mismatch")
            continue
        if str(_record_field(row, "currency") or "").upper() != "USD":
            issues.add("payment_currency_mismatch")
        if _record_field(row, "entry_type") == "refund":
            refunds.append(row)
            continue
        if _record_field(row, "entry_type") != "payment" or raw.get("checkout_type") in {"gift_card", "booth_rent"}:
            continue
        status = _record_field(row, "status")
        if status in {"failed", "canceled"}:
            continue
        if status not in {"posted", "succeeded", "paid", "completed"}:
            issues.add(f"unsettled_ledger_payment:{external_id}")
            continue
        if not aliases:
            issues.add("payment_identity_missing")
            continue
        gross = _record_field(row, "amount_cents")
        add_payment(
            aliases=aliases,
            gross=gross,
            allocation=_service_allocation(raw.get("service_allocation"), _exact_cents(gross)),
            account=raw.get("stripe_account_id"),
            account_known="stripe_account_id" in raw,
            mode=raw.get("livemode"),
        )

    paid = 0
    known_payments: list[dict[str, Any]] = []
    for group in groups:
        if any(len(group[name]) != 1 for name in ("gross", "allocations", "providers", "modes")):
            issues.add("payment_allocation_or_account_requires_reconciliation")
            continue
        group["provider"] = next(iter(group["providers"]))
        if len(group["accounts"]) != (1 if group["provider"] == "stripe" else 0):
            issues.add("payment_allocation_or_account_requires_reconciliation")
            continue
        allocation = next(iter(group["allocations"]))
        if sum(allocation) != next(iter(group["gross"])):
            issues.add("payment_mirrors_disagree")
            continue
        group["allocation"] = allocation
        group["account"] = next(iter(group["accounts"])) if group["provider"] == "stripe" else None
        group["refunded"] = [0, 0, 0]
        known_payments.append(group)
        paid += allocation[0]
        identity = min(group["aliases"], key=lambda key: (not key.startswith("pi_"), not key.startswith("cs_"), key))
        scope = (group["account"] or "platform") if group["provider"] == "stripe" else "manual"
        evidence_keys.add(f"payment:{scope}:{identity}")

    if _record_field(booking, "deposit_paid", False) and not any(
        group["aliases"] & deposit_aliases for group in known_payments
    ):
        issues.add("paid_deposit_missing_exact_evidence")

    refunded = 0
    seen_refunds: dict[str, tuple[Any, ...]] = {}
    for row in refunds:
        raw = _record_field(row, "raw") or {}
        refund_id = str(_record_field(row, "external_id") or "")
        status = _record_field(row, "status")
        if status in {"failed", "canceled"}:
            continue
        if status != "succeeded":
            issues.add(f"unsettled_refund:{refund_id}")
            continue
        amount = _record_field(row, "amount_cents")
        gross = _exact_cents(-amount) if type(amount) is int and amount < 0 else None
        parent = [group for group in known_payments if raw.get("payment_intent") in group["aliases"]]
        if not refund_id or gross is None or len(parent) != 1 or parent[0]["provider"] != "stripe":
            issues.add("refund_missing_exact_payment")
            continue
        payment = parent[0]
        if "stripe_account_id" not in raw or raw["stripe_account_id"] != payment["account"]:
            issues.add("refund_account_requires_reconciliation")
            continue
        if type(raw.get("livemode")) is not bool or raw["livemode"] != livemode:
            issues.add("refund_mode_requires_reconciliation")
            continue
        allocation = _service_allocation(raw.get("service_allocation"), gross)
        if allocation is None and gross == sum(payment["allocation"]):
            # A full refund of an already exact payment reverses those exact
            # components; no division by 1.04 or deduction of a guessed tip.
            allocation = payment["allocation"]
        if allocation is None:
            issues.add("partial_refund_allocation_required")
            continue
        identity = (raw.get("payment_intent"), payment["account"], gross, allocation)
        if refund_id in seen_refunds:
            if seen_refunds[refund_id] != identity:
                issues.add("refund_mirrors_disagree")
            continue
        seen_refunds[refund_id] = identity
        for index, part in enumerate(allocation):
            payment["refunded"][index] += part
        if any(part > original for part, original in zip(payment["refunded"], payment["allocation"], strict=True)):
            issues.add("refund_exceeds_original_allocation")
        refunded += allocation[0]
        evidence_keys.add(f"refund:{payment['account'] or 'platform'}:{refund_id}")

    cards: dict[str, Any] = {}
    for card in gift_cards:
        card_id = str(_record_field(card, "id") or "")
        if card_id:
            if card_id in cards and any(
                _record_field(cards[card_id], name) != _record_field(card, name)
                for name in ("artist_id", "currency", "status", "original_amount_cents")
            ):
                issues.add("gift_card_provenance_mirrors_disagree")
            cards[card_id] = card
    seen_redemptions: dict[str, tuple[str, int]] = {}
    redeemed_per_card: dict[str, int] = {}
    for row in gift_card_redemptions:
        if not belongs(_record_field(row, "booking_id")):
            continue
        redemption_id = str(_record_field(row, "id") or "")
        card_id = str(_record_field(row, "gift_card_id") or "")
        card = cards.get(card_id)
        amount = _exact_cents(_record_field(row, "amount_cents"))
        if not redemption_id or not amount:
            issues.add("invalid_gift_card_redemption")
            continue
        if card is None:
            issues.add("gift_card_provenance_required")
            continue
        if str(_record_field(card, "artist_id")) != artist_id:
            issues.add("gift_card_artist_mismatch")
            continue
        if str(_record_field(card, "currency") or "").upper() != "USD":
            issues.add("gift_card_currency_mismatch")
            continue
        original = _exact_cents(_record_field(card, "original_amount_cents"))
        if _record_field(card, "status") not in {"active", "redeemed"} or original is None:
            issues.add("gift_card_status_requires_reconciliation")
            continue
        purchase_ids = {
            str(value)
            for value in (
                _record_field(card, "stripe_payment_intent_id"),
                _record_field(card, "stripe_session_id"),
            )
            if value
        }
        purchase_modes: set[bool] = set()
        for entry in ledger_rows:
            raw = _record_field(entry, "raw") or {}
            if (
                _record_field(entry, "provider") != "stripe"
                or _record_field(entry, "entry_type") != "payment"
                or _record_field(entry, "status") not in {"posted", "succeeded", "paid", "completed"}
                or str(_record_field(entry, "artist_id") or "") != artist_id
                or str(_record_field(entry, "currency") or "").upper() != "USD"
                or not isinstance(raw, dict)
            ):
                continue
            aliases = {
                str(value)
                for value in (
                    _record_field(entry, "external_id"),
                    raw.get("payment_intent"),
                    raw.get("stripe_session_id"),
                )
                if value
            }
            if aliases & purchase_ids and type(raw.get("livemode")) is bool:
                purchase_modes.add(raw["livemode"])
        if purchase_modes != {livemode}:
            issues.add("gift_card_purchase_mode_requires_reconciliation")
            continue
        if redemption_id in seen_redemptions:
            if seen_redemptions[redemption_id] != (card_id, amount):
                issues.add("gift_card_redemption_mirrors_disagree")
            continue
        seen_redemptions[redemption_id] = (card_id, amount)
        redeemed_per_card[card_id] = redeemed_per_card.get(card_id, 0) + amount
        if redeemed_per_card[card_id] > original:
            issues.add("gift_card_redemptions_exceed_original_value")
        paid += amount
        evidence_keys.add(f"gift_card_redemption:{redemption_id}")

    return ServiceBalanceProjection(
        status="reconciliation_required" if issues else "ready",
        livemode=livemode,
        service_total_cents=total,
        paid_service_cents=paid,
        refunded_service_cents=refunded,
        remaining_service_cents=None if issues else max(0, total - paid + refunded),
        evidence_keys=tuple(sorted(evidence_keys)),
        reconciliation_reasons=tuple(sorted(issues)),
    )


NO_REFUND_CHECKOUT_NOTICE = (
    "All payments are final and non-refundable except where required by law or for duplicate or "
    "unauthorized charges. With artist approval, a tattoo deposit may be transferred to a "
    "rescheduled appointment. The amount shown is the full amount charged by Ryozan."
)


@dataclass
class ChargeRouting:
    """Resolved routing for one charge. All amounts are integer cents."""

    destination: str  # DESTINATION_ARTIST | DESTINATION_SHOP
    amount_cents: int
    commission_base_cents: int
    price_adjustment_cents: int
    commission_rate: int  # effective rate actually applied
    artist_cut_cents: int  # what the artist earns on this charge
    shop_cut_cents: int  # what the shop keeps
    payout_owed_cents: int  # still owed to the artist by hand (0 if Stripe settled)
    settled_by_stripe: bool
    artist_account_id: str | None
    reason: str  # audit string, stored on the ledger row
    payment_intent_data: dict[str, Any] = field(default_factory=dict)
    tip_cents: int = 0
    artist_tip_cents: int = 0

    def apply_to(self, checkout_params: dict[str, Any]) -> dict[str, Any]:
        """Merge payment_intent_data into a Stripe Checkout Session payload."""
        if self.payment_intent_data:
            existing = checkout_params.get("payment_intent_data") or {}
            checkout_params["payment_intent_data"] = {**existing, **self.payment_intent_data}
        return checkout_params

    def request_options(self, *, idempotency_key: str | None = None) -> dict[str, Any] | None:
        """Scope a direct charge to the connected merchant account."""
        from app.services.stripe_client import stripe_request_options

        return stripe_request_options(
            account_id=self.artist_account_id if self.settled_by_stripe else None,
            idempotency_key=idempotency_key,
        )

    def ledger_fields(self) -> dict[str, Any]:
        """Portable routing facts for a payment ledger record."""
        fields = {
            "amount_cents": self.amount_cents,
            "commission_base_cents": self.commission_base_cents,
            "price_adjustment_cents": self.price_adjustment_cents,
            "destination": self.destination,
            "shop_cut_cents": self.shop_cut_cents,
            "payout_owed_cents": self.payout_owed_cents,
            "stripe_account_id": self.artist_account_id,
            "settled": self.settled_by_stripe,
        }
        if self.tip_cents:
            fields.update(tip_cents=self.tip_cents, artist_tip_cents=self.artist_tip_cents)
        return fields

    def receipt_metadata(self) -> dict[str, str]:
        """Exact split fields copied from Checkout into E-con evidence."""
        metadata = {
            "commission_rate": str(self.commission_rate),
            "commission_base_cents": str(self.commission_base_cents),
            "customer_price_adjustment_cents": str(self.price_adjustment_cents),
            "customer_price_increase_bps": str(CUSTOMER_PRICE_INCREASE_BPS),
            "artist_cut_cents": str(self.artist_cut_cents),
            "shop_cut_cents": str(self.shop_cut_cents),
            "payout_owed_cents": str(self.payout_owed_cents),
            "shop_share_category": "contractor_contribution" if self.shop_cut_cents > 0 else "",
        }
        # Existing zero-tip checkout metadata remains byte-for-byte compatible.
        if self.tip_cents:
            metadata.update(tip_cents=str(self.tip_cents), artist_tip_cents=str(self.artist_tip_cents))
        return metadata


def apply_customer_receipt(
    checkout_params: dict[str, Any],
    *,
    customer_email: str | None,
    description: str,
    metadata: dict[str, str],
) -> dict[str, Any]:
    """Attach exact receipt and reconciliation facts to a Checkout payment."""
    policy_metadata = {
        "refund_policy": "all_payments_final",
        "processing_fee_policy": "included_in_displayed_amount",
    }
    checkout_metadata = dict(checkout_params.get("metadata") or {})
    checkout_params["metadata"] = {**checkout_metadata, **metadata, **policy_metadata}
    payment_intent_data = dict(checkout_params.get("payment_intent_data") or {})
    payment_intent_metadata = dict(payment_intent_data.get("metadata") or {})
    payment_intent_data["description"] = description
    payment_intent_data["metadata"] = {**payment_intent_metadata, **metadata, **policy_metadata}
    custom_text = dict(checkout_params.get("custom_text") or {})
    custom_text["submit"] = {"message": NO_REFUND_CHECKOUT_NOTICE}
    checkout_params["custom_text"] = custom_text
    if customer_email:
        checkout_params["customer_email"] = customer_email
        payment_intent_data["receipt_email"] = customer_email
    checkout_params["payment_intent_data"] = payment_intent_data
    return checkout_params


# Stripe documents this placeholder for the success redirect, where it becomes
# the Checkout Session id. The cancel return needs no session, so it is left off.
CHECKOUT_SESSION_ID_PLACEHOLDER = "{CHECKOUT_SESSION_ID}"
PUBLIC_CHECKOUT_RETURN_PATH = "/book/confirm"


def public_checkout_return_url(frontend_url: str, outcome: str, reference_code: str | None) -> str:
    """Where Stripe sends a public buyer back to: the public, no-login confirm page.

    ``outcome`` is "success" or "cancelled". The success URL carries the session
    id so the confirm page can read the booking's real state from
    GET /bookings/checkout-status/{session_id}; the redirect itself is never
    treated as a payment receipt.
    """
    if outcome not in {"success", "cancelled"}:
        raise ValueError(f"unknown checkout outcome: {outcome}")
    reference = quote(reference_code or "", safe="")
    url = f"{frontend_url.rstrip('/')}{PUBLIC_CHECKOUT_RETURN_PATH}?checkout={outcome}&ref={reference}"
    if outcome == "success":
        url += f"&session_id={CHECKOUT_SESSION_ID_PLACEHOLDER}"
    return url


def customer_price_cents(service_price_cents: int) -> int:
    """Return the single customer-facing price after the four-percent increase."""
    service_price_cents = max(0, int(service_price_cents or 0))
    return (service_price_cents * (10_000 + CUSTOMER_PRICE_INCREASE_BPS) + 5_000) // 10_000


async def upsert_stripe_ledger_entry(
    db: AsyncSession,
    *,
    external_id: str,
    entry_type: str,
    status: str,
    amount_cents: int,
    currency: str,
    occurred_at: datetime | None,
    description: str,
    category: str,
    artist_id: UUID | None,
    raw: dict[str, Any],
) -> EconLedgerEntry:
    """Persist one idempotent Stripe payment or refund in the E-con ledger."""
    result = await db.execute(
        select(EconLedgerEntry).where(
            EconLedgerEntry.provider == "stripe",
            EconLedgerEntry.external_id == external_id,
        )
    )
    entry = result.scalar_one_or_none()
    fields = {
        "entry_type": entry_type,
        "status": status,
        "amount_cents": amount_cents,
        "currency": currency.upper(),
        "occurred_at": occurred_at,
        "description": description,
        "counterparty": "Stripe",
        "category": category,
        "payroll_tax_bucket": "income",
        "artist_id": artist_id,
        "raw": raw,
    }
    if entry:
        for key, value in fields.items():
            setattr(entry, key, value)
        return entry

    entry = EconLedgerEntry(provider="stripe", external_id=external_id, **fields)
    db.add(entry)
    return entry


def effective_artist_rate(artist: Any) -> int:
    """
    Resolve the artist's commission rate.

    None means the rate was never configured. Founder sales default to full
    shop settlement; artists fall back to the house rate. Both cases remain
    explicit in the routing receipt.
    """
    rate = getattr(artist, "commission_rate", None)
    if rate is None and getattr(artist, "role", None) == "founder":
        logger.info(
            "founder %s has no commission_rate; routing 100 percent to the shop",
            getattr(artist, "slug", None) or getattr(artist, "id", "?"),
        )
        return 0
    if rate is None:
        logger.warning(
            "artist %s (%s) has no commission_rate; defaulting to %d",
            getattr(artist, "slug", None) or getattr(artist, "id", "?"),
            getattr(artist, "email", "?"),
            DEFAULT_ARTIST_COMMISSION_RATE,
        )
        return DEFAULT_ARTIST_COMMISSION_RATE
    return max(0, min(100, int(rate)))


def _stripe_field(value: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field_name, default)
    return getattr(value, field_name, default)


def connect_account_ready(account: Any) -> bool:
    """Return whether an Accounts v2 merchant can charge and pay out."""
    configuration = _stripe_field(account, "configuration", {}) or {}
    merchant = _stripe_field(configuration, "merchant", {}) or {}
    merchant_capabilities = _stripe_field(merchant, "capabilities", {}) or {}
    card_payments = _stripe_field(merchant_capabilities, "card_payments", {}) or {}
    stripe_balance = _stripe_field(merchant_capabilities, "stripe_balance", {}) or {}
    payouts = _stripe_field(stripe_balance, "payouts", {}) or {}
    if merchant:
        return bool(_stripe_field(card_payments, "status") == "active" and _stripe_field(payouts, "status") == "active")

    # Compatibility for already-onboarded v1 accounts while Stripe exposes
    # them through Accounts v2. New accounts never use this legacy shape.
    capabilities = _stripe_field(account, "capabilities", {}) or {}
    return bool(
        _stripe_field(account, "details_submitted", False)
        and _stripe_field(account, "charges_enabled", False)
        and _stripe_field(account, "payouts_enabled", False)
        and _stripe_field(capabilities, "transfers") == "active"
    )


def _artist_can_receive(artist: Any) -> bool:
    """
    True only when Stripe will actually accept a direct charge.

    Gating on stripe_account_id alone is not enough: an artist who started
    onboarding but never finished has an account id that cannot accept
    charges, and Stripe rejects the whole Checkout Session.
    """
    return bool(getattr(artist, "stripe_account_id", None) and getattr(artist, "stripe_onboarding_complete", False))


def resolve_charge_routing(
    artist: Any,
    amount_cents: int,
    *,
    artist_share_base_cents: int | None = None,
    tip_cents: int = 0,
) -> ChargeRouting:
    """
    Decide where a charge lands and how it splits.

    Args:
        artist: a User row (needs commission_rate, stripe_account_id,
            stripe_onboarding_complete)
        amount_cents: total charged to the collector, in cents
        artist_share_base_cents: configured service price used to calculate
            artist commission. Defaults to amount_cents for legacy callers.
        tip_cents: included in amount_cents, separate from service and the
            price adjustment. A tip belongs to the service artist, except the
            existing zero-rate founder policy settles the entire sale to shop.

    Returns:
        ChargeRouting. Never raises.
    """
    amount_cents = max(0, int(amount_cents or 0))
    tip_cents = max(0, min(amount_cents, int(tip_cents or 0)))
    service_and_adjustment = amount_cents - tip_cents
    commission_base_cents = (
        service_and_adjustment
        if artist_share_base_cents is None
        else max(0, min(service_and_adjustment, int(artist_share_base_cents or 0)))
    )
    price_adjustment_cents = service_and_adjustment - commission_base_cents
    rate = effective_artist_rate(artist)
    artist_tip = 0 if getattr(artist, "role", None) == "founder" and rate == 0 else tip_cents

    # Artist earns `rate` percent. Shop keeps the remainder. Integer division
    # floors the artist cut, so any rounding remainder falls to the shop.
    artist_cut = commission_base_cents * rate // 100 + artist_tip
    shop_cut = amount_cents - artist_cut

    account_id = getattr(artist, "stripe_account_id", None)

    if artist_cut == 0:
        return ChargeRouting(
            destination=DESTINATION_SHOP,
            amount_cents=amount_cents,
            commission_base_cents=commission_base_cents,
            price_adjustment_cents=price_adjustment_cents,
            commission_rate=rate,
            artist_cut_cents=0,
            shop_cut_cents=shop_cut,
            payout_owed_cents=0,
            settled_by_stripe=False,
            artist_account_id=None,
            reason="zero_artist_share",
            payment_intent_data={},
            tip_cents=tip_cents,
            artist_tip_cents=artist_tip,
        )

    if _artist_can_receive(artist):
        pi_data: dict[str, Any] = {}
        # Stripe rejects application_fee_amount of 0, so a merchant with no
        # shop share gets an unmodified direct charge.
        if shop_cut > 0:
            pi_data["application_fee_amount"] = shop_cut

        return ChargeRouting(
            destination=DESTINATION_ARTIST,
            amount_cents=amount_cents,
            commission_base_cents=commission_base_cents,
            price_adjustment_cents=price_adjustment_cents,
            commission_rate=rate,
            artist_cut_cents=artist_cut,
            shop_cut_cents=shop_cut,
            payout_owed_cents=0,
            settled_by_stripe=True,
            artist_account_id=account_id,
            reason=(
                "connected_price_adjustment"
                if rate == 100 and price_adjustment_cents > 0
                else "connected"
                if shop_cut > 0
                else "connected_booth_rent"
            ),
            payment_intent_data=pi_data,
            tip_cents=tip_cents,
            artist_tip_cents=artist_tip,
        )

    # Fallback: no usable connected account. The whole charge lands in the
    # Ryozan platform account and the artist's share is owed manually.
    reason = "onboarding_incomplete" if account_id else "not_connected"
    logger.info(
        "charge of %d cents for artist %s routed to shop (%s); %d cents owed on payout",
        amount_cents,
        getattr(artist, "slug", None) or getattr(artist, "id", "?"),
        reason,
        artist_cut,
    )

    return ChargeRouting(
        destination=DESTINATION_SHOP,
        amount_cents=amount_cents,
        commission_base_cents=commission_base_cents,
        price_adjustment_cents=price_adjustment_cents,
        commission_rate=rate,
        artist_cut_cents=artist_cut,
        shop_cut_cents=shop_cut,
        payout_owed_cents=artist_cut,
        settled_by_stripe=False,
        artist_account_id=None,
        reason=reason,
        payment_intent_data={},
        tip_cents=tip_cents,
        artist_tip_cents=artist_tip,
    )
