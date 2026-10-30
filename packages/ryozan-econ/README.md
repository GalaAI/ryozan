# Ryozan E-con

Double-entry bookkeeping for a studio. Pure Python, no dependencies, MIT licensed.

Ryozan is an open-source, distributed product for studio operations. Blind Tiger
Tattoo Co is the brand entity that inspired it and its first set of books — not
its owner. Nothing in this package is specific to any one shop, and every default
here is a starting point another studio can replace.

## Why this exists

Most studio software keeps a normalized single-entry transaction log: one row per
thing that arrived from the payment processor. That answers *"what came in"* well.

It cannot answer *"what do we owe, what did we actually earn, and do the books
close"*, because those need two sides per fact. Profit and loss, a balance sheet,
what you still owe each contractor, a defensible tax number — all of it derives
from a journal that balances. Build those on a log that doesn't and they inherit
the drift.

This package is that journal and nothing else. It stores no second copy of any
total, so there is no summary that can disagree with the ledger.

## Install

```bash
pip install ryozan-econ
```

Python 3.12+. Depends on nothing.

## Use

```python
from datetime import UTC, datetime

from ryozan_econ import Journal, SourceTransaction, post, studio_chart
from ryozan_econ import artist_payouts_owed, balance_sheet, income_statement

TENANT = "entity:your-studio"

chart = studio_chart(TENANT)  # the default studio chart of accounts
journal = Journal(TENANT, chart)

journal.post_if_new(
    post(
        SourceTransaction(
            tenant_id=TENANT,
            provider="stripe",
            external_id="pi_example_card_payment",
            kind="pos_payment",
            occurred_at=datetime(2026, 9, 25, 21, 2, 32, tzinfo=UTC),
            gross_cents=52000,  # what the client paid
            fee_cents=1538,  # the processor's cut
            artist_id="11111111-1111-1111-1111-111111111111",
            artist_cut_cents=26000,  # the artist's share, if the shop collected it
        )
    )
)

pnl = income_statement(journal, chart)
print(pnl.revenue_cents, pnl.expense_cents, pnl.net_income_cents)
print(artist_payouts_owed(journal))
```

`post_if_new` returns `None` when that `(provider, external_id)` is already
recorded, so replaying a provider webhook is a no-op rather than a double count.

## The accounting decisions

These are the choices that separate books from a spreadsheet. If you disagree with
one, it lives in a posting rule and it is meant to be replaceable.

- **A deposit is not income.** It is money owed as a session. It credits a
  liability and becomes revenue when the session happens. Booking deposits
  straight to revenue overstates income and hides what the shop owes — the most
  common way a small-business ledger goes wrong.
- **A gift card is not income either**, until it is redeemed.
- **Processing fees are an expense, not a smaller sale.** Recording revenue net of
  card fees hides a real and negotiable cost.
- **A contractor's share is a cost when the shop collected the money**, and a
  liability until paid out. When the contractor takes the charge on their own
  connected account the shop never held that money, so only its platform fee is
  income.
- **Cash and a payment-app balance are different assets.** Both are income, but the
  money sits in different places, and keeping them apart is what makes either one
  reconcilable.
- **The journal is append-only.** Books are corrected by posting a reversing entry,
  never by editing history. There is no update or delete here.

## Tenancy

Tenant-keyed on every account, entry and report, so one deployment serves many
studios.

Inside the Ark ontology a tenant is named with an entity ref, `entity:<slug>` — the
*business* whose money is counted. A vessel ref, `vessel:<slug>`, names software,
and `tenancy.py` **refuses one as a tenant**: software does not owe taxes, and a
ledger pointed at a vessel reads as fine right until two deployments disagree about
whose money they hold.

A plain identifier is accepted too, so a studio running this standalone and outside
Ark can keep books without adopting anything.

## Payment kinds

| Kind | Posts as |
|---|---|
| `pos_payment`, `service_payment` | revenue, fee expense, contractor payable |
| `public_booking_deposit`, `flash_deposit`, `quote_deposit`, `quote_proposal_deposit` | **liability** until earned |
| `deposit_earned` | liability → revenue, no cash movement |
| `gift_card` | **liability** until redeemed |
| `gift_card_redemption` | liability → revenue, no cash movement |
| `booth_rent_cash` | undeposited cash → booth rent income |
| `booth_rent_venmo` | payment-app balance → booth rent income |
| `artist_payout` | pays down the payable, not an expense twice |

Adding a kind is a function plus a dict entry in `posting.py`. Adding an account is
one line in `accounts.py`.

## Invariants

Asserted directly in the tests, not inferred from happy-path totals:

- an unbalanced entry **cannot be constructed** — the check is in the constructor
- the trial balance always ties
- the balance sheet always ties, including the period result
- deposits and gift cards stay liabilities until earned
- revenue is never recorded net of fees
- replaying a source posts nothing twice
- one tenant's books never see another's, and a vessel ref is refused as a tenant

```bash
PYTHONPATH=. python -m unittest discover -s tests -p "*_tests.py" -v
```

The suite is mutation-checked: removing the balanced-entry check, booking deposits
straight to revenue, or dropping the vessel-ref guard each fail it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: this package stays free
of dependencies and of any framework, money is always whole `int` cents, and a
change to a posting rule needs a sentence explaining the accounting, not only a
passing test.

Security reports: [SECURITY.md](SECURITY.md).

## Not yet built

Persistence, an onboarding flow for customizing the chart and tax profile, an
adapter that feeds an existing transaction log through `post()`, and an
auto-categorization layer. Each depends on the journal being right first.

## License

MIT. See [LICENSE](LICENSE).
