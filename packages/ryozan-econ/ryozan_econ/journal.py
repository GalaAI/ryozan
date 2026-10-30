"""Double-entry journal.

The one invariant that makes a ledger a ledger: every entry's debits equal its
credits. It is enforced in the constructor, so an unbalanced entry cannot exist
long enough to be written anywhere.

This is what a normalized single-entry transaction log is not. Such a log answers
"what arrived from the payment processor" well and cannot answer "what do we owe,
what did we earn, and do the books close", because those need two sides per fact.
Most studio software has the first and stops there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from .accounts import ChartOfAccounts

import uuid
from dataclasses import dataclass, field

from .tenancy import normalize_tenant_id


class UnbalancedEntryError(ValueError):
    """Debits did not equal credits."""


class DuplicateSourceError(ValueError):
    """The same source transaction was posted twice."""


@dataclass(frozen=True, slots=True)
class JournalLine:
    """One side of one fact. Exactly one of debit/credit carries the amount."""

    account_code: str
    debit_cents: int = 0
    credit_cents: int = 0
    memo: str = ""
    #: Who the line is attributable to, for per-artist reporting. Optional.
    artist_id: str | None = None

    def __post_init__(self) -> None:
        if self.debit_cents < 0 or self.credit_cents < 0:
            raise ValueError("a journal line carries no negative amounts; use the other side instead")
        if bool(self.debit_cents) == bool(self.credit_cents):
            raise ValueError("a journal line is exactly one of a debit or a credit")

    @property
    def amount_cents(self) -> int:
        return self.debit_cents or self.credit_cents

    @property
    def is_debit(self) -> bool:
        return bool(self.debit_cents)


def debit(account_code: str, cents: int, memo: str = "", artist_id: str | None = None) -> JournalLine:
    return JournalLine(account_code, debit_cents=cents, memo=memo, artist_id=artist_id)


def credit(account_code: str, cents: int, memo: str = "", artist_id: str | None = None) -> JournalLine:
    return JournalLine(account_code, credit_cents=cents, memo=memo, artist_id=artist_id)


@dataclass(slots=True)
class JournalEntry:
    tenant_id: str
    occurred_at: datetime
    description: str
    lines: list[JournalLine]
    #: Where the fact came from, so a source can be posted at most once.
    source_provider: str | None = None
    source_external_id: str | None = None
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        self.tenant_id = normalize_tenant_id(self.tenant_id)
        if not self.lines:
            raise UnbalancedEntryError("an entry with no lines states nothing")
        if self.total_debits != self.total_credits:
            raise UnbalancedEntryError(
                f"debits {self.total_debits} != credits {self.total_credits} for {self.description!r}"
            )

    @property
    def total_debits(self) -> int:
        return sum(line.debit_cents for line in self.lines)

    @property
    def total_credits(self) -> int:
        return sum(line.credit_cents for line in self.lines)

    @property
    def amount_cents(self) -> int:
        """The size of the entry -- one side of it, since both sides are equal."""
        return self.total_debits

    @property
    def source_key(self) -> tuple[str, str] | None:
        if self.source_provider and self.source_external_id:
            return (self.source_provider, self.source_external_id)
        return None

    def validate_against(self, chart: ChartOfAccounts) -> None:
        """Every line must name an account this tenant actually has."""
        if chart.tenant_id != self.tenant_id:
            raise ValueError(f"chart belongs to tenant {chart.tenant_id!r}, entry to {self.tenant_id!r}")
        for line in self.lines:
            chart.get(line.account_code)


class Journal:
    """A tenant's entries, append-only.

    Append-only on purpose: books are corrected by posting a reversing entry, not
    by editing history. There is no update or delete here and that is the point.
    """

    def __init__(self, tenant_id: str, chart: ChartOfAccounts | None = None) -> None:
        tenant_id = normalize_tenant_id(tenant_id)
        if chart is not None and chart.tenant_id != tenant_id:
            raise ValueError("chart belongs to a different tenant")
        self.tenant_id = tenant_id
        self.chart = chart
        self._entries: list[JournalEntry] = []
        self._source_keys: set[tuple[str, str]] = set()

    def post(self, entry: JournalEntry) -> JournalEntry:
        if entry.tenant_id != self.tenant_id:
            raise ValueError(f"entry belongs to tenant {entry.tenant_id!r}, journal to {self.tenant_id!r}")
        if self.chart is not None:
            entry.validate_against(self.chart)
        key = entry.source_key
        if key is not None:
            if key in self._source_keys:
                raise DuplicateSourceError(f"{key[0]}:{key[1]} is already posted")
            self._source_keys.add(key)
        self._entries.append(entry)
        return entry

    def post_if_new(self, entry: JournalEntry) -> JournalEntry | None:
        """Idempotent post. Returns None when the source was already recorded.

        Payment providers retry webhooks, so the common case is not an error.
        """
        key = entry.source_key
        if key is not None and key in self._source_keys:
            return None
        return self.post(entry)

    def has_source(self, provider: str, external_id: str) -> bool:
        return (provider, external_id) in self._source_keys

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def between(self, start: datetime, end: datetime) -> list[JournalEntry]:
        """Entries in an inclusive window. Callers pass tz-aware bounds."""
        return [e for e in self._entries if start <= e.occurred_at <= end]

    def balances(self, start: datetime | None = None, end: datetime | None = None) -> dict[str, int]:
        """Net debit-minus-credit per account code, for a window or all time.

        Positive means a net debit. Reports turn that into a natural balance using
        each account's normal side, so the sign convention lives in one place.
        """
        rows = self._entries if start is None or end is None else self.between(start, end)
        out: dict[str, int] = {}
        for entry in rows:
            for line in entry.lines:
                out[line.account_code] = out.get(line.account_code, 0) + line.debit_cents - line.credit_cents
        return out
