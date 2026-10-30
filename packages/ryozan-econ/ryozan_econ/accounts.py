"""Chart of accounts.

Tenant-keyed because Ryozan ships to more than one studio, and no studio is the
only shape of shop. Every account belongs to exactly one tenant and nothing in
this module reaches across them.

Amounts are whole cents as ``int`` everywhere in this package. No floats touch
money -- 0.1 + 0.2 has no place in a ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .tenancy import normalize_tenant_id


class AccountType(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


#: Types whose balance grows on the debit side. The rest grow on the credit
#: side. This is the whole of the sign convention; nothing else guesses.
DEBIT_NORMAL: frozenset[AccountType] = frozenset({AccountType.ASSET, AccountType.EXPENSE})


def is_debit_normal(account_type: AccountType) -> bool:
    return account_type in DEBIT_NORMAL


@dataclass(frozen=True, slots=True)
class Account:
    code: str
    name: str
    type: AccountType
    description: str = ""

    @property
    def debit_normal(self) -> bool:
        return is_debit_normal(self.type)


class UnknownAccountError(KeyError):
    """Raised when a journal line names an account the chart does not define."""


class ChartOfAccounts:
    """A tenant's accounts, keyed by code."""

    def __init__(self, tenant_id: str, accounts: list[Account] | None = None) -> None:
        self.tenant_id = normalize_tenant_id(tenant_id)
        self._by_code: dict[str, Account] = {}
        for account in accounts or []:
            self.add(account)

    def add(self, account: Account) -> Account:
        if account.code in self._by_code:
            raise ValueError(f"duplicate account code {account.code!r}")
        self._by_code[account.code] = account
        return account

    def get(self, code: str) -> Account:
        try:
            return self._by_code[code]
        except KeyError as exc:
            raise UnknownAccountError(f"{code!r} is not in the chart for tenant {self.tenant_id!r}") from exc

    def __contains__(self, code: object) -> bool:
        return code in self._by_code

    def __len__(self) -> int:
        return len(self._by_code)

    def codes(self) -> list[str]:
        return sorted(self._by_code)

    def of_type(self, account_type: AccountType) -> list[Account]:
        return [a for a in self._by_code.values() if a.type == account_type]


# ── Well-known codes ────────────────────────────────────────────────
# Named constants rather than bare strings, so a posting rule that references a
# nonexistent account fails at import rather than at month end.

UNDEPOSITED_FUNDS = "1000"
STRIPE_CLEARING = "1010"
OPERATING_BANK = "1020"
VENMO_CLEARING = "1030"
ACCOUNTS_RECEIVABLE = "1100"

CLIENT_DEPOSITS_HELD = "2000"
GIFT_CARD_LIABILITY = "2010"
ARTIST_PAYOUTS_PAYABLE = "2020"
SALES_TAX_PAYABLE = "2030"
TAX_RESERVE = "2040"

OWNER_EQUITY = "3000"
RETAINED_EARNINGS = "3900"

SERVICE_REVENUE = "4000"
BOOTH_RENT_INCOME = "4010"
MERCH_REVENUE = "4020"
GIFT_CARD_BREAKAGE = "4030"

ARTIST_COMMISSION_EXPENSE = "5000"
PROCESSING_FEES = "5010"
SUPPLIES_EXPENSE = "5020"
RENT_EXPENSE = "5030"
SOFTWARE_EXPENSE = "5040"


_STUDIO_TEMPLATE: tuple[Account, ...] = (
    Account(UNDEPOSITED_FUNDS, "Undeposited Funds", AccountType.ASSET, "Cash and checks taken but not yet banked"),
    Account(STRIPE_CLEARING, "Stripe Clearing", AccountType.ASSET, "Settled with Stripe, not yet paid out"),
    Account(OPERATING_BANK, "Operating Bank", AccountType.ASSET, "The business checking account"),
    Account(
        VENMO_CLEARING,
        "Venmo Balance",
        AccountType.ASSET,
        "Received in Venmo and not yet swept to the bank. Distinct from cash in the safe",
    ),
    Account(ACCOUNTS_RECEIVABLE, "Accounts Receivable", AccountType.ASSET, "Invoiced and unpaid"),
    Account(
        CLIENT_DEPOSITS_HELD,
        "Client Deposits Held",
        AccountType.LIABILITY,
        "A booking deposit is money owed as a session, not income, until the session happens",
    ),
    Account(
        GIFT_CARD_LIABILITY,
        "Gift Card Liability",
        AccountType.LIABILITY,
        "Sold gift cards are unearned until redeemed",
    ),
    Account(
        ARTIST_PAYOUTS_PAYABLE,
        "Artist Payouts Payable",
        AccountType.LIABILITY,
        "The artist's share of a charge the shop collected on their behalf",
    ),
    Account(SALES_TAX_PAYABLE, "Sales Tax Payable", AccountType.LIABILITY, "Collected on behalf of the state"),
    Account(TAX_RESERVE, "Tax Reserve", AccountType.LIABILITY, "Set aside against estimated tax"),
    Account(OWNER_EQUITY, "Owner Equity", AccountType.EQUITY),
    Account(RETAINED_EARNINGS, "Retained Earnings", AccountType.EQUITY),
    Account(SERVICE_REVENUE, "Tattoo Service Revenue", AccountType.REVENUE),
    Account(BOOTH_RENT_INCOME, "Booth Rent Income", AccountType.REVENUE, "Chair rent charged to contractors"),
    Account(MERCH_REVENUE, "Merch & Stash Revenue", AccountType.REVENUE),
    Account(GIFT_CARD_BREAKAGE, "Gift Card Breakage", AccountType.REVENUE, "Expired or abandoned gift card value"),
    Account(
        ARTIST_COMMISSION_EXPENSE,
        "Artist Commission",
        AccountType.EXPENSE,
        "Contractor cost of a collected charge",
    ),
    Account(PROCESSING_FEES, "Payment Processing Fees", AccountType.EXPENSE, "Stripe and card network fees"),
    Account(SUPPLIES_EXPENSE, "Supplies", AccountType.EXPENSE),
    Account(RENT_EXPENSE, "Rent", AccountType.EXPENSE),
    Account(SOFTWARE_EXPENSE, "Software & Subscriptions", AccountType.EXPENSE),
)


def studio_chart(tenant_id: str) -> ChartOfAccounts:
    """The default tattoo-studio chart, which onboarding starts every tenant on.

    A tenant can add accounts afterwards; the template is a starting point, not a
    cage. It is deliberately small -- a shop that cannot name an account will not
    use it, and an unused account is worse than a missing one.
    """
    return ChartOfAccounts(tenant_id, list(_STUDIO_TEMPLATE))


def studio_template_codes() -> list[str]:
    return [a.code for a in _STUDIO_TEMPLATE]
