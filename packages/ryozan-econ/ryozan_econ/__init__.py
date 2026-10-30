"""Ryozan E-con: portable double-entry bookkeeping for a studio.

Tenant-keyed by construction so one deployment serves many studios. No studio is
the only shape of shop, so nothing here assumes one.

Deliberately free of any database, web framework or host-application import. It is
pure domain logic over whole cents, so the same package can be embedded in an API,
shipped as an ArkStation Pack, or run standalone, without carrying a persistence
choice along with it.

    from ryozan_econ import studio_chart, Journal, SourceTransaction, post
    from ryozan_econ import income_statement, balance_sheet

    chart = studio_chart("entity:example-studio")
    journal = Journal("entity:example-studio", chart)
    journal.post_if_new(post(SourceTransaction(...)))
    pnl = income_statement(journal, chart)
"""

from .accounts import (
    Account,
    AccountType,
    ChartOfAccounts,
    UnknownAccountError,
    is_debit_normal,
    studio_chart,
    studio_template_codes,
)
from .journal import (
    DuplicateSourceError,
    Journal,
    JournalEntry,
    JournalLine,
    UnbalancedEntryError,
    credit,
    debit,
)
from .posting import DEPOSIT_KINDS, SourceTransaction, UnsupportedKindError, post, supported_kinds
from .reports import (
    BalanceSheet,
    IncomeStatement,
    TrialBalance,
    artist_payouts_owed,
    balance_sheet,
    income_statement,
    trial_balance,
)
from .tenancy import (
    ARK_ENTITY_REF,
    ARK_VESSEL_REF,
    RYOZAN_VESSEL,
    TenantIdError,
    ark_entity_tenant,
    is_ark_entity,
    normalize_tenant_id,
)

__all__ = [
    "ARK_ENTITY_REF",
    "ARK_VESSEL_REF",
    "Account",
    "AccountType",
    "BalanceSheet",
    "ChartOfAccounts",
    "DEPOSIT_KINDS",
    "DuplicateSourceError",
    "IncomeStatement",
    "Journal",
    "JournalEntry",
    "JournalLine",
    "RYOZAN_VESSEL",
    "SourceTransaction",
    "TenantIdError",
    "TrialBalance",
    "UnbalancedEntryError",
    "UnknownAccountError",
    "UnsupportedKindError",
    "ark_entity_tenant",
    "artist_payouts_owed",
    "balance_sheet",
    "credit",
    "debit",
    "income_statement",
    "is_ark_entity",
    "is_debit_normal",
    "normalize_tenant_id",
    "post",
    "studio_chart",
    "studio_template_codes",
    "supported_kinds",
    "trial_balance",
]

__version__ = "0.1.0"
