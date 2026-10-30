"""Statements derived from the journal.

Nothing here stores anything. Every number is computed from journal entries, so
there is no second copy of the truth to drift -- which is exactly the failure
a stored summary invites, where two reports answer the same question from two
places and are free to disagree.

Sign convention lives in one place: ``Journal.balances`` returns net debits, and
each report flips that to the account's natural side using ``debit_normal``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from .journal import Journal

from dataclasses import dataclass, field

from .accounts import AccountType, ChartOfAccounts


@dataclass(frozen=True, slots=True)
class TrialBalance:
    """Every account's debit/credit totals. The first thing an accountant asks for."""

    rows: dict[str, tuple[int, int]]  # code -> (debit_cents, credit_cents)

    @property
    def total_debits(self) -> int:
        return sum(d for d, _ in self.rows.values())

    @property
    def total_credits(self) -> int:
        return sum(c for _, c in self.rows.values())

    @property
    def balanced(self) -> bool:
        return self.total_debits == self.total_credits


@dataclass(frozen=True, slots=True)
class IncomeStatement:
    revenue_cents: int
    expense_cents: int
    by_account: dict[str, int] = field(default_factory=dict)

    @property
    def net_income_cents(self) -> int:
        return self.revenue_cents - self.expense_cents


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    asset_cents: int
    liability_cents: int
    equity_cents: int
    net_income_cents: int
    by_account: dict[str, int] = field(default_factory=dict)

    @property
    def liabilities_and_equity_cents(self) -> int:
        """Equity includes the period's result, which is what makes it tie."""
        return self.liability_cents + self.equity_cents + self.net_income_cents

    @property
    def balanced(self) -> bool:
        return self.asset_cents == self.liabilities_and_equity_cents


def trial_balance(journal: Journal, start: datetime | None = None, end: datetime | None = None) -> TrialBalance:
    rows: dict[str, tuple[int, int]] = {}
    entries = journal.entries if start is None or end is None else journal.between(start, end)
    for entry in entries:
        for line in entry.lines:
            d, c = rows.get(line.account_code, (0, 0))
            rows[line.account_code] = (d + line.debit_cents, c + line.credit_cents)
    return TrialBalance(dict(sorted(rows.items())))


def _natural_balances(
    journal: Journal,
    chart: ChartOfAccounts,
    account_type: AccountType,
    start: datetime | None,
    end: datetime | None,
) -> dict[str, int]:
    """Balances for one account type, positive in that type's normal direction."""
    net_debits = journal.balances(start, end)
    out: dict[str, int] = {}
    for account in chart.of_type(account_type):
        raw = net_debits.get(account.code, 0)
        if raw == 0:
            continue
        out[account.code] = raw if account.debit_normal else -raw
    return out


def income_statement(
    journal: Journal,
    chart: ChartOfAccounts,
    start: datetime | None = None,
    end: datetime | None = None,
) -> IncomeStatement:
    """Revenue less expenses for a period. Profit and loss."""
    revenue = _natural_balances(journal, chart, AccountType.REVENUE, start, end)
    expense = _natural_balances(journal, chart, AccountType.EXPENSE, start, end)
    return IncomeStatement(
        revenue_cents=sum(revenue.values()),
        expense_cents=sum(expense.values()),
        by_account={**revenue, **expense},
    )


def balance_sheet(
    journal: Journal,
    chart: ChartOfAccounts,
    start: datetime | None = None,
    end: datetime | None = None,
) -> BalanceSheet:
    """Assets against liabilities plus equity, as at the end of the window.

    The period's net income is carried separately rather than folded into equity,
    because until the books are closed it has not been moved to retained
    earnings. Including it is what makes the sheet tie.
    """
    assets = _natural_balances(journal, chart, AccountType.ASSET, start, end)
    liabilities = _natural_balances(journal, chart, AccountType.LIABILITY, start, end)
    equity = _natural_balances(journal, chart, AccountType.EQUITY, start, end)
    pnl = income_statement(journal, chart, start, end)
    return BalanceSheet(
        asset_cents=sum(assets.values()),
        liability_cents=sum(liabilities.values()),
        equity_cents=sum(equity.values()),
        net_income_cents=pnl.net_income_cents,
        by_account={**assets, **liabilities, **equity},
    )


def artist_payouts_owed(journal: Journal, start: datetime | None = None, end: datetime | None = None) -> dict[str, int]:
    """What the shop still owes each artist, by artist id.

    Walks the payable account's lines rather than a separate tally, so it cannot
    disagree with the balance sheet.
    """
    from .accounts import ARTIST_PAYOUTS_PAYABLE

    entries = journal.entries if start is None or end is None else journal.between(start, end)
    owed: dict[str, int] = {}
    for entry in entries:
        for line in entry.lines:
            if line.account_code != ARTIST_PAYOUTS_PAYABLE or line.artist_id is None:
                continue
            # A credit increases what is owed; a debit is a payout against it.
            owed[line.artist_id] = owed.get(line.artist_id, 0) + line.credit_cents - line.debit_cents
    return {artist: cents for artist, cents in sorted(owed.items()) if cents}
