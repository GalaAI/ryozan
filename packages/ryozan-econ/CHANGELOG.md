# Changelog

Notable changes to Ryozan E-con. This project follows [Semantic
Versioning](https://semver.org/) once it reaches 1.0; before then, minor versions
may contain breaking changes and will say so here.

## [Unreleased]

### Added

- Double-entry journal with the balanced-entry invariant enforced in the
  `JournalEntry` constructor, and an append-only `Journal`.
- Tenant-keyed chart of accounts with a default studio template.
- Posting rules for card payments, the four deposit kinds, deposit recognition,
  gift card sale and redemption, booth rent by cash or payment app, and contractor
  payouts.
- Trial balance, income statement, balance sheet and contractor payables owed,
  all derived from the journal with no stored totals.
- Tenancy helpers for the Ark `entity:<slug>` convention, refusing a `vessel:` ref
  as a tenant.
- Idempotent posting keyed on `(provider, external_id)` so replayed provider
  webhooks cannot double-count.
