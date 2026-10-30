"""Posting rules: a payment becomes a pair of journal sides.

Every ``kind`` here is an ordinary studio payment shape, and every field on
``SourceTransaction`` is something a payment processor already reports: the split
fields a platform charge carries in its metadata, and the processor fee from the
balance transaction. Nothing here needs data a shop is not already given.

The accounting decisions worth arguing about, stated once:

* **A deposit is not income.** It is money owed as a session. It credits a
  liability and only becomes revenue when the session happens. Booking a deposit
  straight to revenue overstates income and understates what the shop owes, which
  is the single most common way a small-business ledger goes wrong.
* **A gift card is not income either**, for the same reason, until it is redeemed.
* **Processing fees are an expense, not a smaller sale.** Recording net revenue
  hides the cost of card acceptance, which is a real and negotiable line item.
* **The artist's share is a cost when the shop collected the money**, and a
  liability until it is paid out. When the artist takes the charge directly on
  their own connected account, the shop never held that money and only its own
  cut is income.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

from dataclasses import dataclass

from . import accounts as a
from .journal import JournalEntry, JournalLine, credit, debit

#: Deposit kinds all post identically, so they share one rule rather than four
#: copies of it.
DEPOSIT_KINDS: frozenset[str] = frozenset(
    {"public_booking_deposit", "flash_deposit", "quote_deposit", "quote_proposal_deposit"}
)


class UnsupportedKindError(ValueError):
    """No posting rule is defined for this payment kind."""


@dataclass(frozen=True, slots=True)
class SourceTransaction:
    """A settled payment, in the terms the payment provider reports it."""

    tenant_id: str
    provider: str
    external_id: str
    kind: str
    occurred_at: datetime
    #: What the customer paid, in cents. Always the gross.
    gross_cents: int
    #: Provider fee borne by this tenant. Zero when the fee is not theirs.
    fee_cents: int = 0
    artist_id: str | None = None
    #: The artist's share of the charge, when the shop collected it for them.
    artist_cut_cents: int = 0
    #: True when the charge landed on the artist's own connected account, so the
    #: shop only ever held its application fee.
    direct_to_artist: bool = False
    #: The shop's cut on a direct-to-artist charge (the Stripe application fee).
    shop_cut_cents: int = 0
    description: str = ""

    def __post_init__(self) -> None:
        if self.gross_cents <= 0:
            raise ValueError("gross_cents must be positive")
        if self.fee_cents < 0 or self.artist_cut_cents < 0 or self.shop_cut_cents < 0:
            raise ValueError("amounts are whole positive cents")
        if self.fee_cents > self.gross_cents:
            raise ValueError("fee cannot exceed the gross")
        if self.artist_cut_cents > self.gross_cents:
            raise ValueError("the artist's cut cannot exceed the gross")


def _settlement_lines(txn: SourceTransaction, revenue_or_liability_code: str) -> list[JournalLine]:
    """The common shape: cash in, fee out, the full gross credited somewhere.

    Debits net-of-fee to clearing plus the fee to expense, so the two sides equal
    the gross without ever recording revenue net of fees.
    """
    lines: list[JournalLine] = [
        debit(a.STRIPE_CLEARING, txn.gross_cents - txn.fee_cents, "settled, awaiting payout"),
    ]
    if txn.fee_cents:
        lines.append(debit(a.PROCESSING_FEES, txn.fee_cents, "provider fee"))
    lines.append(credit(revenue_or_liability_code, txn.gross_cents, txn.description or txn.kind))
    return lines


def _artist_share_lines(txn: SourceTransaction) -> list[JournalLine]:
    """Cost of the contractor's share, and the liability to pay it."""
    if not txn.artist_cut_cents:
        return []
    return [
        debit(a.ARTIST_COMMISSION_EXPENSE, txn.artist_cut_cents, "artist share", artist_id=txn.artist_id),
        credit(a.ARTIST_PAYOUTS_PAYABLE, txn.artist_cut_cents, "owed to artist", artist_id=txn.artist_id),
    ]


def _service_payment(txn: SourceTransaction) -> list[JournalLine]:
    if txn.direct_to_artist:
        # The shop never held the gross. Only its application fee is income, and
        # it settles through the platform balance.
        if not txn.shop_cut_cents:
            raise ValueError("a direct-to-artist charge with no shop cut posts nothing for the shop")
        return [
            debit(a.STRIPE_CLEARING, txn.shop_cut_cents, "application fee settled"),
            credit(a.SERVICE_REVENUE, txn.shop_cut_cents, txn.description or "shop cut on artist charge"),
        ]
    return _settlement_lines(txn, a.SERVICE_REVENUE) + _artist_share_lines(txn)


def _deposit(txn: SourceTransaction) -> list[JournalLine]:
    # Unearned: the shop owes a session, not a service it has performed.
    return _settlement_lines(txn, a.CLIENT_DEPOSITS_HELD)


def _gift_card_sale(txn: SourceTransaction) -> list[JournalLine]:
    if txn.direct_to_artist:
        if not txn.shop_cut_cents:
            raise ValueError("a direct gift card sale with no shop cut posts nothing for the shop")
        return [
            debit(a.STRIPE_CLEARING, txn.shop_cut_cents, "application fee settled"),
            credit(a.GIFT_CARD_LIABILITY, txn.shop_cut_cents, txn.description or "shop cut on gift card sale"),
        ]
    return _settlement_lines(txn, a.GIFT_CARD_LIABILITY)


def _gift_card_redemption(txn: SourceTransaction) -> list[JournalLine]:
    # No money moves. A liability is discharged by delivering the service.
    return [
        debit(a.GIFT_CARD_LIABILITY, txn.gross_cents, "gift card redeemed"),
        credit(a.SERVICE_REVENUE, txn.gross_cents, txn.description or "gift card redemption"),
    ]


def _deposit_earned(txn: SourceTransaction) -> list[JournalLine]:
    # The session happened: the held deposit becomes revenue. Still no cash move.
    return [
        debit(a.CLIENT_DEPOSITS_HELD, txn.gross_cents, "deposit earned"),
        credit(a.SERVICE_REVENUE, txn.gross_cents, txn.description or "session completed"),
    ]


def _booth_rent(txn: SourceTransaction, asset_code: str, how: str) -> list[JournalLine]:
    """Booth rent received off the card rails.

    Income either way, but the money sits somewhere different depending on how it
    arrived, and that distinction is what makes a later reconciliation possible:
    cash is in the safe until it is banked, a Venmo balance is in Venmo until it
    is swept. Collapsing both into cash would make the safe look fuller than it is.

    Booth rent is easy to leave unrecorded precisely because no provider reports
    it: nothing arrives to reconcile against, so it exists only once somebody
    enters it.
    """
    return [
        debit(asset_code, txn.gross_cents, f"booth rent via {how}", artist_id=txn.artist_id),
        credit(a.BOOTH_RENT_INCOME, txn.gross_cents, txn.description or "booth rent", artist_id=txn.artist_id),
    ]


def _booth_rent_cash(txn: SourceTransaction) -> list[JournalLine]:
    return _booth_rent(txn, a.UNDEPOSITED_FUNDS, "cash")


def _booth_rent_venmo(txn: SourceTransaction) -> list[JournalLine]:
    return _booth_rent(txn, a.VENMO_CLEARING, "Venmo")


def _artist_payout(txn: SourceTransaction) -> list[JournalLine]:
    # Paying down what the shop owed the artist. Not an expense again.
    return [
        debit(a.ARTIST_PAYOUTS_PAYABLE, txn.gross_cents, "artist paid", artist_id=txn.artist_id),
        credit(a.OPERATING_BANK, txn.gross_cents, txn.description or "payout", artist_id=txn.artist_id),
    ]


_RULES = {
    "pos_payment": _service_payment,
    "service_payment": _service_payment,
    "gift_card": _gift_card_sale,
    "gift_card_redemption": _gift_card_redemption,
    "deposit_earned": _deposit_earned,
    "booth_rent_cash": _booth_rent_cash,
    "booth_rent_venmo": _booth_rent_venmo,
    "artist_payout": _artist_payout,
}


def supported_kinds() -> list[str]:
    return sorted(set(_RULES) | DEPOSIT_KINDS)


def post(txn: SourceTransaction) -> JournalEntry:
    """Turn a settled payment into one balanced journal entry.

    The entry carries the provider and external id, so ``Journal.post_if_new``
    makes replaying a provider's webhook a no-op rather than a double count.
    """
    if txn.kind in DEPOSIT_KINDS:
        lines = _deposit(txn)
    else:
        rule = _RULES.get(txn.kind)
        if rule is None:
            raise UnsupportedKindError(f"no posting rule for {txn.kind!r}; known: {', '.join(supported_kinds())}")
        lines = rule(txn)

    return JournalEntry(
        tenant_id=txn.tenant_id,
        occurred_at=txn.occurred_at,
        description=txn.description or txn.kind,
        lines=lines,
        source_provider=txn.provider,
        source_external_id=txn.external_id,
    )
