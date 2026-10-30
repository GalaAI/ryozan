# Security Policy

## Reporting

Report a vulnerability privately through GitHub's **Report a vulnerability** button
on the Security tab of this repository. That opens a private advisory visible only
to the maintainers.

Please do not open a public issue for a security problem first.

Include what you did, what happened, and what you expected. A failing test is the
fastest possible report.

## Scope

This package holds no credentials, opens no sockets and touches no database. The
realistic security surface is therefore **correctness of money handling**, and the
following are treated as security issues, not ordinary bugs:

- any way to construct or persist an entry whose debits do not equal its credits
- any way for one tenant's data to appear in another tenant's books or reports
- any path that bypasses the append-only rule, letting posted history be altered
- an integer overflow, sign error or rounding path that changes a recorded amount
- an idempotency failure that lets the same source transaction post twice

## Out of scope

- how a downstream application stores what this package produces
- a posting rule you disagree with on accounting grounds — that is a normal issue
  or pull request, and a welcome one

## Supported versions

Pre-1.0. Only the latest released version receives fixes.
