# Contributing to Ryozan E-con

This is a ledger. A bug here does not crash a page, it quietly reports the wrong
number and someone files a tax return on it. So the bar is a little different from
most libraries, and it is short enough to state in full.

## Non-negotiables

1. **Money is `int` cents.** No `float`, no `Decimal` in stored values, ever. A
   ledger that can represent 0.30000000000000004 is not a ledger.
2. **No dependencies.** This package imports only the standard library. It gets
   embedded in other people's applications and must never drag a database, an ORM
   or a web framework in with it.
3. **No framework, no I/O.** Pure domain logic. Persistence is the caller's
   problem, deliberately, so the same code runs in a service, a script or a test.
4. **Every entry balances.** The check lives in the `JournalEntry` constructor. Do
   not move it, do not add a path that bypasses it.
5. **The journal is append-only.** Corrections are reversing entries. Do not add
   update or delete.
6. **Tenant isolation.** Nothing reads across tenants. Every new API takes or
   derives a tenant id.

## Changing a posting rule

A posting rule encodes an accounting opinion, so a passing test is not enough.
Say in the PR, in a sentence or two:

- what event the rule describes
- which account each side hits, and why that account
- what would be wrong if it went to the obvious alternative

"Deposits credit a liability because the shop owes a session it has not yet
performed; crediting revenue would overstate income and hide the obligation" is the
level of explanation expected. If you cannot write that sentence, the rule is not
ready.

## Tests

```bash
PYTHONPATH=. python -m unittest discover -s tests -p "*_tests.py" -v
```

Assert the invariant, not the arithmetic. `assertTrue(sheet.balanced)` is worth
more than a hardcoded total, because it keeps holding when the numbers change.

New behaviour needs a test that **fails without the change**. Check it: break your
own fix and watch the test go red. A test that passes either way is documentation,
not a test.

Never use a real payment identifier, customer name, or account id in a fixture,
even a historical one. Synthetic values only — `pi_example_*`, `entity:example-studio`,
all-ones UUIDs.

## Style

```bash
ruff check .
ruff format --check .
```

Line length 120, Python 3.12 target. Comments explain *why*; the code already says
what.

## Adding an account or a kind

- an account is one `Account(...)` line in `accounts.py`, plus a named constant
- a payment kind is one function in `posting.py` plus one entry in `_RULES`

Prefer extending the chart over overloading an existing account. An account nobody
can name is worse than one more account.
